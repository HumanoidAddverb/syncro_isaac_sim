import asyncio
import websockets
import json
import time
import sys
import tty
import termios
import threading

START_TIME = time.monotonic()
JOINT_NAMES = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"]
_gripper_is_open = True


def getch():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def keyboard_thread(loop, queue):
    while True:
        ch = getch()
        loop.call_soon_threadsafe(queue.put_nowait, ch)
        if ch == '\x03':
            break


async def drain_queue(queue):
    """Discard any buffered keystrokes before starting a new phase."""
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break


async def teleop(queue):
    global _gripper_is_open

    episode_num = 0

    async with websockets.connect("ws://localhost:8765") as ws:
        while True:
            episode_num += 1
            recording = []
            await drain_queue(queue)

            print(f"\n=== Episode {episode_num} started ===")
            print("Controls: qwerty/asdfgh = joints ±0.03 | p = gripper | SPACE = end episode | Ctrl+C = quit")

            # ── Teleop loop for one episode ──────────────────────────────────
            while True:
                await ws.send(json.dumps({"cmd": "get_state"}))
                state = json.loads(await ws.recv(
                    idx = 'asdfgh'.index(ch)
                    if idx < len(JOINT_NAMES):
                        jname = JOINT_NAMES[idx]
                        new_target = float(current_positions.get(jname, 0.0)) - 0.03
                        await ws.send(json.dumps({"cmd": "set_joints", "positions": {jname: new_target}}))

                elif ch == 'p':
                    _gripper_is_open = not _gripper_is_open
                    new_state = "open" if _gripper_is_open else "closed"
                    await ws.send(json.dumps({"cmd": "set_gripper_state", "gripper_state": new_state}))
                    print(f"\n[GRIPPER] → {new_state}\n")

                await asyncio.sleep(0.01)))
                current_positions = state.get("joint_positions", {})

                # Non-blocking key check
                ch = None
                try:
                    ch = queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

                if ch == '\x03':
                    print("\nCtrl+C — exiting.")
                    return

                if ch == ' ':
                    break  # end this episode

                # Record current frame
                recording.append({
                    "time": round(time.monotonic() - START_TIME, 4),
                    "joint_positions": {k: round(float(v), 6) for k, v in current_positions.items()},
                    "gripper_open": _gripper_is_open,
                })

                if ch and ch in 'qwerty':
                    idx = 'qwerty'.index(ch)
                    if idx < len(JOINT_NAMES):
                        jname = JOINT_NAMES[idx]
                        new_target = float(current_positions.get(jname, 0.0)) + 0.03
                        await ws.send(json.dumps({"cmd": "set_joints", "positions": {jname: new_target}}))

                elif ch and ch in 'asdfgh':
                    idx = 'asdfgh'.index(ch)
                    if idx < len(JOINT_NAMES):
                        jname = JOINT_NAMES[idx]
                        new_target = float(current_positions.get(jname, 0.0)) - 0.03
                        await ws.send(json.dumps({"cmd": "set_joints", "positions": {jname: new_target}}))

                elif ch == 'p':
                    _gripper_is_open = not _gripper_is_open
                    new_state = "open" if _gripper_is_open else "closed"
                    await ws.send(json.dumps({"cmd": "set_gripper_state", "gripper_state": new_state}))
                    print(f"\n[GRIPPER] → {new_state}\n")

                await asyncio.sleep(0.01)

            # ── End of episode: ask to save ──────────────────────────────────
            print(f"\n--- Episode {episode_num} ended  ({len(recording)} frames recorded) ---")
            print("Save this episode?  Press  y = save   n = discard")

            await drain_queue(queue)
            while True:
                ch = await queue.get()
                if ch == '\x03':
                    print("\nCtrl+C — exiting.")
                    return
                if ch == 'y':
                    filename = f"episode_{episode_num:04d}.json"
                    with open(filename, 'w') as f:
                        json.dump({"episode": episode_num, "frames": recording}, f, indent=2)
                    print(f"[SAVED] → {filename}")
                    break
                if ch == 'n':
                    print("[DISCARDED]")
                    break

            # ── Wait for user to start the next episode ──────────────────────
            print("\nPress any key to start the next episode  (Ctrl+C to quit)...")
            await drain_queue(queue)
            ch = await queue.get()
            if ch == '\x03':
                print("\nCtrl+C — exiting.")
                return

            # Reset scene: robot to home (all joints = 0) + blocks to initial poses
            print("[RESET] Resetting scene...")
            await ws.send(json.dumps({"cmd": "reset_scene"}))
            await ws.recv()  # wait for server ack before starting
            _gripper_is_open = True
            await asyncio.sleep(0.5)  # let physics settle
            print("[RESET] Done.")

async def main():
    queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    k_thread = threading.Thread(target=keyboard_thread, args=(loop, queue), daemon=True)
    k_thread.start()

    print("--- SYNCRO TELEOP CLIENT ---")
    print("qwerty  → ADD  0.03 to joint1-6")
    print("asdfgh  → SUB  0.03 from joint1-6")
    print("p       → toggle gripper open/closed")
    print("SPACE   → end current episode")
    print("Ctrl+C  → quit")
    print("----------------------------")

    await teleop(queue)


asyncio.run(main())