from pyglet.window.key import HOME
import argparse
import os
import json
import time
import queue
import threading
import asyncio
from sim_data_logger import SimDataLogger
import signal

_GLOBAL_LOGGER = None  

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
# SYNCRO5_USD  = os.path.join(HERE, "assets", "syncro5_with_gripper",  "syncro5_with_gripper.usd")

SYNCRO5_USD  = os.path.join(HERE, "assets", "heal_with_two_finger_gripper",  "heal_with_two_finger_gripper_w.usd")
SYNCRO10_USD = os.path.join(HERE, "assets", "Syncro10", "Syncro10.usd")


JOINT_NAMES = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "right_link_1_joint","right_link_2_joint","right_link_3_joint", "left_link_1_joint","left_link_2_joint", "left_link_3_joint"]

# ── RealSense camera body dimensions (metres) — approximate D435 ─────────────
CAM_BODY_SIZE = (0.09, 0.025, 0.025)       # length × height × depth
CAM_MOUNT_LINK = "end_effector"             # link just before the gripper
# Offset from link origin: slightly back from gripper, facing forward
CAM_OFFSET_POS = (0.0, 0.05, -0.0)         # (x, y, z) in link-local frame
CAM_FOCAL_LENGTH = 1.88                     # mm  (RealSense D435 RGB)
CAM_RESOLUTION   = (640, 480)

# ── Top-down camera settings ─────────────────────────────────────────────────
TOP_CAM_HEIGHT   = 2.5                     # metres above the table surface
TOP_CAM_FOCAL    = 2.0                     # mm — wider FOV for overview
TOP_CAM_BODY_SIZE = (0.08, 0.08, 0.04)     # small box housing

# ── Table dimensions (metres) — edit here to resize ──────────────────────────
TABLE_HEIGHT = 0.8          # workbench height
TABLE_SIZE   = (2.4, 1.6, TABLE_HEIGHT)   # length × width × height

# ── Cube dimensions (metres) ─────────────────────────────────────────────────
CUBE_SIZE = 0.12            # side length of each cube

# ── Robot scale factor ───────────────────────────────────────────────────────
ROBOT_SCALE = (1.0, 1.0, 1.0)

# ── CLI ───────────────────────────────────────────────────────────────────────
# AppLauncher must parse args before any omniverse import.
from isaaclab.app import AppLauncher  # noqa: E402

parser = argparse.ArgumentParser(description="Syncro cobot Isaac Sim mirror")
parser.add_argument(
    "--robot", choices=["syncro5", "syncro10"], default="syncro5",
    help="Robot model to load (default: syncro5)",
)
parser.add_argument("--port", type=int, default=8765, help="WebSocket port (default: 8765)")
parser.add_argument("--host", default="0.0.0.0", help="WebSocket bind host (default: 0.0.0.0)")
AppLauncher.add_app_launcher_args(parser)
parser.set_defaults(enable_cameras=True)
args_cli = parser.parse_args()

app_launcher   = AppLauncher(args_cli)
simulation_app = app_launcher.app

import omni.kit.app

_LOGGER_FINALIZED = False
_STOP_REQUESTED = False

def _safe_finalize():
    global _LOGGER_FINALIZED, _GLOBAL_LOGGER
    if _LOGGER_FINALIZED:
        return
    _LOGGER_FINALIZED = True

    if _GLOBAL_LOGGER is not None:
        try:
            print("[LOGGER] Finalizing dataset...")
            _GLOBAL_LOGGER.finalize()
            print("[LOGGER] Done.")
        except Exception as e:
            print(f"[LOGGER ERROR during finalize] {e}")

def _on_shutdown(event):
    print("[SIM] Kit shutdown detected...")
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    _safe_finalize()

app = omni.kit.app.get_app()
_shutdown_sub = app.get_shutdown_event_stream().create_subscription_to_pop(_on_shutdown)

# ── Post-launch imports (omniverse must be running first) ─────────────────────
import torch  # noqa: E402
import numpy as np  # noqa: E402

import isaaclab.sim as sim_utils  # noqa: E402
from isaaclab.assets import Articulation, ArticulationCfg, AssetBaseCfg, RigidObject, RigidObjectCfg  # noqa: E402
from isaaclab.actuators import ImplicitActuatorCfg  # noqa: E402
from isaaclab.scene import InteractiveScene, InteractiveSceneCfg  # noqa: E402
from isaaclab.sensors import TiledCameraCfg  # noqa: E402
from isaaclab.utils import configclass  # noqa: E402
from isaacsim.core.utils.rotations import euler_angles_to_quat  # noqa: E402

# Omniverse USD imports for creating camera prims directly
import omni.usd  # noqa: E402
from pxr import Usd, UsdGeom, Gf, Sdf  # noqa: E402

# ── Thread-safe state bridge ──────────────────────────────────────────────────
# Sim loop writes joint state here; WebSocket handlers read on demand.
_cmd_queue: "queue.Queue[dict]" = queue.Queue(maxsize=1)
_state_lock = threading.Lock()
_latest_state: dict = {"joint_positions": {}, "joint_velocities": {}}
_gripper_state: str = "open"
_gripper_lock = threading.Lock()
START_TIME = time.monotonic()

# ── Two-finger gripper joint positions ───────────────────────────────────────
# Adjust these values to match the physical joint limits of your gripper.
GRIPPER_OPEN_POS   = 1.0   # radians — fully open
GRIPPER_CLOSED_POS = -1.0   # radians — fully closed

# ── WebSocket server (background thread) ──────────────────────────────────────
try:
    import websockets  # type: ignore
    import time

    _ws_msg_count = 0
    _ws_last_time = time.time()

    async def _ws_handler(websocket):
        global _gripper_state
        async for raw in websocket:
            # now = time.monotonic() - START_TIME
            # print(f"[_ws_handler] recv instantaneous time: ",now)

            try:
                msg = json.loads(raw)
                # print(f"[_ws_handler] parsed msg: ",msg)
            except json.JSONDecodeError:
                print("got invalid message")
                await websocket.send(json.dumps({"error": "invalid JSON"}))
                continue

            cmd = msg.get("cmd")

            if cmd == "set_joints":
                positions = msg.get("positions", {})
                # Keep only the latest command — drop stale one if queue is full.
                if _cmd_queue.full():
                    try:
                        _cmd_queue.get_nowait()
                    except queue.Empty:
                        pass
                _cmd_queue.put_nowait(positions)

            elif cmd == "get_state":
                with _state_lock:
                    state = dict(_latest_state)
                await websocket.send(json.dumps(state))

            elif cmd == "get_gripper_state":
                with _gripper_lock:
                    g_state = _gripper_state
                await websocket.send(json.dumps({"gripper_state": g_state}))

            elif cmd == "set_gripper_state":
                new_state = msg.get("gripper_state")
                if new_state in ["open", "closed"]:
                    with _gripper_lock:
                        _gripper_state = new_state
                    val = GRIPPER_CLOSED_POS if new_state == "closed" else GRIPPER_OPEN_POS
                    positions = {
                        "right_link_1_joint": val,
                        "left_link_1_joint":  -val,
                    }
                    if _cmd_queue.full():
                        try:
                            _cmd_queue.get_nowait()
                        except queue.Empty:
                            pass
                    _cmd_queue.put_nowait(positions)
                else:
                    await websocket.send(json.dumps({"error": f"invalid gripper_state: {new_state!r}. Use 'open' or 'closed'"}))

            else:
                await websocket.send(json.dumps({"error": f"unknown cmd: {cmd!r}"}))

    async def _ws_main(host: str, port: int):
        async with websockets.serve(_ws_handler, host, port):
            print(f"[SIM] WebSocket API → ws://{host}:{port}")
            await asyncio.Future()  # run forever

    def _start_ws_server(host: str, port: int):
        asyncio.run(_ws_main(host, port))

    _HAS_WEBSOCKETS = True

except ImportError:
    _HAS_WEBSOCKETS = False
    print("[WARN] `websockets` not installed. WebSocket API disabled.")
    print("       Install it with:  pip install websockets")

# stiffness_vals = [402.67, 402.67, 744.277, 364.8477, 14.15, 8.53]   # one per joint
# damping_vals   = [0.161,  0.161,  0.2977,  0.14594,  0.00566,  0.00342]


#                           j1    j2    j3    j4   j5   j6   r_l1  r_l2  r_l3  l_l1  l_l2  l_l3
stiffness_vals = [4000, 4000, 4000, 600, 600, 600,   50,  0,  0,   50,  0,  0]   # one per joint
damping_vals   = [800,  800,  800,  60,  60,  60,    10,  0,  0,   10,  0,  0]


# ── Robot ArticulationCfg ─────────────────────────────────────────────────────
def _make_robot_cfg(usd_path: str, joint_names: list, pos=(0.0, 0.0, 0.0), scale=ROBOT_SCALE) -> ArticulationCfg:
    j = joint_names  # shorthand
    return ArticulationCfg(
        spawn=sim_utils.UsdFileCfg(
            usd_path=usd_path,
            activate_contact_sensors=True,
            scale=scale,
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                disable_gravity=False,
                max_depenetration_velocity=5.0,
            ),
            articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                enabled_self_collisions=False,
                solver_position_iteration_count=32,   # higher = more stable contacts
                solver_velocity_iteration_count=8,
                fix_root_link=True,
            ),
        ),
        init_state=ArticulationCfg.InitialStateCfg(
            pos=pos,
            joint_pos={name: 0.0 for name in j},
        ),
        actuators = {
            name: ImplicitActuatorCfg(
                joint_names_expr=[name],
                effort_limit_sim=500,
                velocity_limit=3,
                stiffness=stiffness_vals[i],
                damping=damping_vals[i],
            )
            for i, name in enumerate(j)
        }
    )


# ── Scene configurations ──────────────────────────────────────────────────────

@configclass
class SyncroPlainSceneCfg(InteractiveSceneCfg):
    """Scene: ground plane + dome light + table + two cubes + robot + cameras."""
    ground = AssetBaseCfg(
        prim_path="/World/ground",
        spawn=sim_utils.GroundPlaneCfg(),
    )
    dome_light = AssetBaseCfg(
        prim_path="/World/DomeLight",
        spawn=sim_utils.DomeLightCfg(intensity=3000.0, color=(0.75, 0.75, 0.75)),
    )
    # ── Table (static/kinematic rigid body so objects rest on it) ────────
    table = RigidObjectCfg(
        prim_path="/World/Table",
        spawn=sim_utils.CuboidCfg(
            size=TABLE_SIZE,
            visual_material=sim_utils.PreviewSurfaceCfg(
                diffuse_color=(0.45, 0.30, 0.15),   # wood-ish brown
            ),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                kinematic_enabled=True,              # fixed in place — does not fall
                disable_gravity=True,
            ),
            physics_material=sim_utils.RigidBodyMaterialCfg(
                static_friction=0.8,
                dynamic_friction=0.6,
                restitution=0.0,
            ),
            collision_props=sim_utils.CollisionPropertiesCfg(
                collision_enabled=True,
            ),
        ),
        init_state=RigidObjectCfg.InitialStateCfg(
            pos=(0.0, 0.0, TABLE_HEIGHT / 2.0),      # centre of the cuboid
        ),
    )
    # ── Cube A (red) — rigid body, pickable ──────────────────────────────
    cube_a = AssetBaseCfg(
        prim_path="/World/CubeA",
        spawn=sim_utils.CuboidCfg(
            size=(CUBE_SIZE, CUBE_SIZE, CUBE_SIZE),
            visual_material=sim_utils.PreviewSurfaceCfg(
                diffuse_color=(0.85, 0.15, 0.15),   # red
            ),
        ),
        init_state=AssetBaseCfg.InitialStateCfg(
            pos=(0.0, 0.4, TABLE_HEIGHT + CUBE_SIZE / 2.0),
            rot=euler_angles_to_quat([0, 0, 45], degrees=True)
        ),
    )
    cube_a2 = AssetBaseCfg(
        prim_path="/World/CubeA2",
        spawn=sim_utils.CuboidCfg(
            size=(CUBE_SIZE, CUBE_SIZE, CUBE_SIZE),
            visual_material=sim_utils.PreviewSurfaceCfg(
                diffuse_color=(0.85, 0.15, 0.15),   # red
            ),
        ),
        init_state=AssetBaseCfg.InitialStateCfg(
            pos=(0.1, 0.5, TABLE_HEIGHT + CUBE_SIZE / 2.0),
            rot=euler_angles_to_quat([0, 0, 70], degrees=True)
        ),
    )
    # ── Cube B (blue) ─────────────────────────────────────────────────────
    cube_b = AssetBaseCfg(
        prim_path="/World/CubeB",
        spawn=sim_utils.CuboidCfg(
            size=(CUBE_SIZE, CUBE_SIZE, CUBE_SIZE),
            visual_material=sim_utils.PreviewSurfaceCfg(
                diffuse_color = (0.15, 0.85, 0.15)   # green
            ),
        ),
        init_state=AssetBaseCfg.InitialStateCfg(
            pos=(0.3, 0.6, TABLE_HEIGHT + CUBE_SIZE / 2.0),
            rot=euler_angles_to_quat([0, 0, 50], degrees=True)
        ),
    )
    cube_c = RigidObjectCfg(
        prim_path="/World/CubeC",
        spawn=sim_utils.CuboidCfg(
            size=(0.30, 0.30, 0.18),
            visual_material=sim_utils.PreviewSurfaceCfg(
                diffuse_color=(0.45, 0.30, 0.15),   # wood-ish brown
            ),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                kinematic_enabled=False,              # fixed in place — does not fall
                disable_gravity=False,
            ),
            physics_material=sim_utils.RigidBodyMaterialCfg(
                static_friction=0.8,
                dynamic_friction=0.6,
                restitution=0.0,
            ),
            collision_props=sim_utils.CollisionPropertiesCfg(
                collision_enabled=True,
            ),
        ),
        init_state=RigidObjectCfg.InitialStateCfg(
           pos=(-0.3, 0.3, TABLE_HEIGHT + 0.18 / 2.0),
            rot=euler_angles_to_quat([0, 0, 20], degrees=True)
        ),
    )
    
    cube_d = RigidObjectCfg(
        prim_path="/World/CubeD",
        spawn=sim_utils.CuboidCfg(
            size=(0.05, 0.12, 0.12),
            visual_material=sim_utils.PreviewSurfaceCfg(
                diffuse_color = (0.00, 0.00, 0.00)   # black
            ),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                kinematic_enabled=False,             
                disable_gravity=False,
            ),
            physics_material=sim_utils.RigidBodyMaterialCfg(
                static_friction=0.8,
                dynamic_friction=0.6,
                restitution=0.0,
            ),
            collision_props=sim_utils.CollisionPropertiesCfg(
                collision_enabled=True,
            ),
        ),
        init_state=RigidObjectCfg.InitialStateCfg(
           pos=(-0.3, 0.3, TABLE_HEIGHT + 0.18 + 0.120 / 2.0),
            rot=euler_angles_to_quat([0, 0, -10], degrees=True)
        ),
    )
    cube_e = RigidObjectCfg(
        prim_path="/World/CubeE",
        spawn=sim_utils.CuboidCfg(
             size=(0.30, 0.30, 0.18),
            visual_material=sim_utils.PreviewSurfaceCfg(
                diffuse_color = (0.00, 0.00, 0.00)   # black
            ),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                kinematic_enabled=False,              
                disable_gravity=False,
            ),
            physics_material=sim_utils.RigidBodyMaterialCfg(
                static_friction=0.8,
                dynamic_friction=0.6,
                restitution=0.0,
            ),
            collision_props=sim_utils.CollisionPropertiesCfg(
                collision_enabled=True,
            ),
        ),
        init_state=RigidObjectCfg.InitialStateCfg(
           pos=(0.5, 0.4, TABLE_HEIGHT + 0.18 / 2.0),
            rot=euler_angles_to_quat([0, 0, 20], degrees=True)
        ),
    )
   
    
    # ── Robot ─────────────────────────────────────────────────────────────
    robot: ArticulationCfg = ArticulationCfg()

    # ── Wrist RealSense D435 camera (mounted on end-effector) ────────────
    # Specs: 1920×1080 RGB, 1280×720 depth, FOV ~87°×58°, range 0.1–10 m
    # We use 640×480 for sim performance; intrinsics match D435 proportions.
    wrist_camera = TiledCameraCfg(
        prim_path="/World/Robot/robot/end_effector/RealSenseD435",
        update_period=1 / 30,                # 30 FPS (D435 max RGB rate)
        height=480,
        width=640,
        data_types=["rgb", "depth"],
        spawn=sim_utils.PinholeCameraCfg(
            focal_length=1.93,               # mm — matches D435 RGB module
            f_stop=100.0,                    # effectively no DoF blur
            focus_distance=400.0,            # mm
            horizontal_aperture=2.096,       # mm — gives ~87° HFOV
            clipping_range=(0.105, 10.0),    # D435 depth range: 0.105–10 m
        ),
        offset=TiledCameraCfg.OffsetCfg(
            pos=CAM_OFFSET_POS,
            rot=tuple(
                euler_angles_to_quat(
                    np.array([0.0, 0.0, 0.0]),   # camera looks along -Z of link
                    degrees=True,
                ).tolist()
            ),
            convention="opengl",
        ),
    )

    # ── Top-down overview camera (fixed above workspace) ─────────────────
    top_down_camera = TiledCameraCfg(
        prim_path="/World/TopDownCamera",
        update_period=1 / 30,
        height=480,
        width=640,
        data_types=["rgb", "depth"],
        spawn=sim_utils.PinholeCameraCfg(
            focal_length=TOP_CAM_FOCAL,
            f_stop=100.0,
            focus_distance=400.0,
            horizontal_aperture=6.0,         # wider aperture for overview
            clipping_range=(0.05, 20.0),
        ),
        offset=TiledCameraCfg.OffsetCfg(
            pos=(0.0, 0.5, TOP_CAM_HEIGHT),
            rot=tuple(
                euler_angles_to_quat(
                    np.array([0.0, 0.0, 0.0]),  # look straight down
                    degrees=True,
                ).tolist()
            ),
            convention="opengl",
        ),
    )


# ── Sim-loop helpers ──────────────────────────────────────────────────────────
def _build_joint_index(robot: Articulation) -> dict:
    """Map joint name → column index in robot.data.joint_pos."""
    return {name: i for i, name in enumerate(robot.data.joint_names)}


def _update_shared_state(robot: Articulation, idx: dict) -> None:
    pos = robot.data.joint_pos[0].cpu().tolist()
    vel = robot.data.joint_vel[0].cpu().tolist()
    jp  = {name: pos[i] for name, i in idx.items()}
    jv  = {name: vel[i] for name, i in idx.items()}
    with _state_lock:
        _latest_state["joint_positions"]  = jp
        _latest_state["joint_velocities"] = jv


def _apply_joint_command(target_pos: torch.Tensor, positions: dict, idx: dict) -> None:
    """Update the persistent target tensor in-place (radians). Thread-safe: called from main thread only."""
    for name, rad in positions.items():
        if name in idx:
            target_pos[0, idx[name]] = float(rad)


# ── Visual camera body helpers ────────────────────────────────────────────────
def _add_visual_camera_body(stage, parent_path, body_size, lens_offset_z=None):
    """Add a small dark cuboid + silver lens cylinder under *parent_path*.

    This is purely cosmetic — it makes the camera visible in the 3-D viewport
    so operators can see where the sensor is.
    """
    body_path = f"{parent_path}/camera_body"
    lens_path = f"{parent_path}/camera_lens"

    body = UsdGeom.Cube.Define(stage, body_path)
    body.GetSizeAttr().Set(1.0)
    sx, sy, sz = body_size
    UsdGeom.XformCommonAPI(body.GetPrim()).SetScale(Gf.Vec3f(sx, sy, sz))
    body.GetDisplayColorAttr().Set([Gf.Vec3f(0.12, 0.12, 0.14)])

    lens = UsdGeom.Cylinder.Define(stage, lens_path)
    lens.GetRadiusAttr().Set(0.006)
    lens.GetHeightAttr().Set(0.004)
    lens.GetAxisAttr().Set("Z")
    lz = lens_offset_z if lens_offset_z is not None else (-sz / 2 - 0.002)
    UsdGeom.XformCommonAPI(lens.GetPrim()).SetTranslate(Gf.Vec3d(0.02, 0.0, lz))
    lens.GetDisplayColorAttr().Set([Gf.Vec3f(0.6, 0.6, 0.65)])


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    usd_path = SYNCRO5_USD if args_cli.robot == "syncro5" else SYNCRO10_USD
    print(f"[SIM] Robot  : {args_cli.robot}  ({usd_path})")
    print(f"[SIM] Joints : {JOINT_NAMES}")

    # Start WebSocket server in a daemon thread so it dies with the process.
    if _HAS_WEBSOCKETS:
        threading.Thread(
            target=_start_ws_server,
            args=(args_cli.host, args_cli.port),
            daemon=True,
            name="ws-server",
        ).start()

    # --- Simulation context ---
    sim_cfg = sim_utils.SimulationCfg(dt=1 / 120, render_interval=2)
    sim = sim_utils.SimulationContext(sim_cfg)
    sim.set_camera_view(eye=[1.8, 1.4, 1.4], target=[0.0, 0.0, TABLE_HEIGHT])

    # --- Scene (table + cubes + robot) ---
    scene_cfg = SyncroPlainSceneCfg(num_envs=1, env_spacing=2.5)
    robot_pos = (0.0, 0.0, TABLE_HEIGHT)   # robot sits on top of the table
    print("[SIM] Scene   : ground + table + 2 cubes")

    scene_cfg.robot = _make_robot_cfg(usd_path, JOINT_NAMES, pos=robot_pos).replace(prim_path="/World/Robot")
    scene = InteractiveScene(scene_cfg)

    sim.reset()
    scene.reset()

    robot: Articulation = scene["robot"]
    joint_idx = _build_joint_index(robot)
    print(f"[SIM] Joints : {list(joint_idx.keys())}")
    print(f"[SIM] Bodies : {robot.data.body_names}")

    dataset_root = os.path.expanduser(f"~/vla_dataset/syncro_5/syncro_sim_{int(time.time())}")

    sim_logger = SimDataLogger(
        dataset_root=dataset_root,
        episode_num=0,
        cameras={
            "ego": {"width": 640, "height": 480},
            "external": {"width": 640, "height": 480},
        },
        fps=30.0,
    )

    sim_logger.init_episode()
    print(f"[LOGGER] Saving to: {dataset_root}")

    global _GLOBAL_LOGGER
    _GLOBAL_LOGGER = sim_logger

    wrist_cam = scene["wrist_camera"]
    top_cam   = scene["top_down_camera"]

    # --- Add cosmetic camera bodies so the sensors are visible in viewport ---
    stage = omni.usd.get_context().get_stage()
    robot_prim_path = robot.cfg.prim_path
    cloned_path = robot_prim_path.replace("{ENV_REGEX_NS}", "envs/env_0")
    if stage.GetPrimAtPath(cloned_path).IsValid():
        robot_prim_path = cloned_path

    # Wrist camera visual body (under the sensor prim created by TiledCameraCfg)
    # Use the resolved env path (e.g. /World/envs/env_0/Robot/end_effector/RealSenseD435)
    wrist_cam_prim = f"{robot_prim_path}/end_effector/RealSenseD435"
    if stage.GetPrimAtPath(wrist_cam_prim).IsValid():
        _add_visual_camera_body(stage, wrist_cam_prim, CAM_BODY_SIZE)
        print(f"[SIM] Wrist RealSense D435 visual body added at {wrist_cam_prim}")
    else:
        print(f"[WARN] Wrist cam prim not found at {wrist_cam_prim} — skipping visual body")

    # Top-down camera visual body
    top_cam_prim = "/World/TopDownCamera"
    if stage.GetPrimAtPath(top_cam_prim).IsValid():
        _add_visual_camera_body(stage, top_cam_prim, TOP_CAM_BODY_SIZE)
        print(f"[SIM] Top-down camera visual body added at {top_cam_prim}")

    # --- Create viewport windows for live camera feeds ---
    from omni.kit.viewport.utility import create_viewport_window  # noqa: E402

    # robot_prim_path is already resolved to the env-cloned path (e.g. /World/envs/env_0/Robot)
    wrist_cam_sensor_path = f"{robot_prim_path}/end_effector/RealSenseD435"
    vp_wrist = create_viewport_window(
        window_name="Wrist RealSense D435",
        camera_path=wrist_cam_sensor_path,
        width=480,
        height=320,
        position_x=20,
        position_y=60,
    )
    print(f"[SIM] Viewport 'Wrist RealSense D435' → {wrist_cam_sensor_path}")

    vp_topdown = create_viewport_window(
        window_name="Top-Down Overview",
        camera_path=top_cam_prim,
        width=480,
        height=320,
        position_x=520,
        position_y=60,
    )
    print(f"[SIM] Viewport 'Top-Down Overview' → {top_cam_prim}")

    print("[SIM] Running. Connect with SyncroSimClient.")

    # Persistent target tensor: updated from WebSocket commands, teleported
    # into PhysX every step via write_joint_state_to_sim().  This bypasses
    # the drive/actuator system and USD joint limits entirely.

    target_pos = robot.data.joint_pos.clone()           # [1, num_joints]
    zero_vel   = torch.zeros_like(target_pos)

    last_cam_frame = -1
    step = 0

    try:
        while simulation_app.is_running() and not _STOP_REQUESTED:

            if step != 0:
                # Pull latest command (drop stale if queue backed up).
                try:
                    cmd = _cmd_queue.get_nowait()
                    _apply_joint_command(target_pos, cmd, joint_idx)
                except queue.Empty:
                    pass

            robot.set_joint_position_target(target_pos)
            scene.write_data_to_sim()

            sim.step()
            scene.update(sim.get_physics_dt())

            # ─────────────────────────────────────────────
            # LOG ONLY WHEN CAMERA UPDATES (~30 Hz)
            # ─────────────────────────────────────────────
            try:

                if step % 4 == 0:
                    # ── Get images ────────────────────────
                    ego_rgb = wrist_cam.data.output["rgb"][0].cpu().numpy()
                    ext_rgb = top_cam.data.output["rgb"][0].cpu().numpy()

                    # ── Get joints ───────────────────────
                    joint_pos = robot.data.joint_pos[0].cpu().numpy()

                    # ── Get gripper state ────────────────
                    with _gripper_lock:
                        # Map open/closed to 0.0/1.0 for VLA dataset compatibility
                        g_val = 1.0 if _gripper_state == "closed" else 0.0

                    # 7-dim: first 6 joints + gripper state
                    obs_joints = np.concatenate([joint_pos[:6], [g_val]]).astype(np.float32)
                    action_joints = np.concatenate([target_pos[0, :6].cpu().numpy(), [g_val]]).astype(np.float32)

                    # ── Timestamp ────────────────────────
                    leader_ts = time.time()

                    visual_obs = {
                        "rgb_ego": ego_rgb,
                        "rgb_external": ext_rgb,
                    }

                    sim_logger.push(
                        action_joints,
                        obs_joints,
                        visual_obs,
                        leader_ts
                    )

            except Exception as e:
                print(f"[LOGGER ERROR] {e}")

            if step % 5 == 0:
                _update_shared_state(robot, joint_idx)

            step += 1

    except Exception as e:
        print(f"[SIM ERROR] {e}")

    finally:
        _safe_finalize()
        if not _STOP_REQUESTED:
            # Only call sim.close if it wasn't a shutdown event that broke us
            # to avoid double-close errors, or just call it unconditionally if safe.
            pass

        sim.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        simulation_app.close()
