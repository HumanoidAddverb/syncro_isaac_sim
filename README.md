# Syncro Digital Twin

Yifei Lyu (yifei.lyu@addverb.com)

Isaac Sim digital twin for the **Syncro 5** and **Syncro 10** cobot.  
Stream real robot joint positions into the simulation over WebSocket.

**Note: Isaac Sim need to be installed in host machine first**.

---

## Files

| File | Purpose |
|---|---|
| `syncro_sim_with_logger.py` | Isaac Sim server — simulation |
| `syncro_sim_client.py` | Client library — lib that contains client Websocket APIs|
| `test_joint_traj.py` | Send Websocket joint traj to sim to test |
| `test_joint_gui.py` | Send joint pos through GUI to sim to test |
| `test_example.py` | Easiest example |

---

## Quick Start

### 1. Launch the simulation

```bash
# Syncro 5 — warehouse scene (default)
python syncro_sim.py --robot syncro5

# Syncro 10 — warehouse scene
python syncro_sim.py --robot syncro10

# Plain scene: just a ground plane + Cobot
python syncro_sim.py --robot syncro5 --plain

# Custom host / port
python syncro_sim.py --robot syncro5 --host 0.0.0.0 --port 8765
```

### 2. Run the test scripts

In a second terminal:
```bash
# Test by sending a joint traj
python test_joint_traj.py                      # Syncro5, localhost:8765
python test_joint_traj.py syncro10             # Syncro10
python test_joint_traj.py syncro5 192.168.1.5  # remote sim machine
# Test by using a joint control GUI
python joint_gui.py                      # syncro5, localhost:8765
python joint_gui.py syncro10
python joint_gui.py syncro5 192.168.1.5 8765
```

---

## Using the Client in customized Code

```python
from syncro_sim_client import SyncroSimClient

client = SyncroSimClient(robot="syncro10")  # or "syncro5"
client.connect()
```

### Send joint positions (radians)

```python
client.set_joint_positions({
    "joint1": 0.0,
    "joint2": -0.785,   # -45°
    "joint3":  1.571,   # +90°
    "joint4":  0.0,
    "joint5":  0.0,
    "joint6":  0.0,
})
```

### Send joint positions (degrees)

```python
client.set_joint_positions_degrees({
    "joint1":   0.0,
    "joint2": -45.0,
    "joint3":  90.0,
})
```

### Send all six joints as an ordered list

```python
# Order: joint1 … joint6  (base → shoulder → elbow → wrist-pitch → wrist-roll → gripper)
client.set_all_joints([0.0, -0.785, 1.571, 0.0, 0.0, 0.0])              # radians
client.set_all_joints([0.0, -45.0,  90.0,  0.0, 0.0, 0.0], degrees=True)
```

### Read simulated state

```python
state = client.get_state()
print(state["joint_positions"])   # {"joint1": 0.0, ...}  radians
print(state["joint_velocities"])  # {"joint1": 0.0, ...}  rad/s
```

### High-frequency mirror loop

```python
import time

client = SyncroSimClient(robot="syncro10")
client.connect()
try:
    while True:
        obs = real_robot.get_observation()
        client.mirror_real_robot(obs)
        time.sleep(1 / 30)   # 30 Hz
finally:
    client.disconnect()
```

Or use it as a context manager:

```python
with SyncroSimClient(robot="syncro10") as client:
    while True:
        client.mirror_real_robot(real_robot.get_observation())
        time.sleep(1 / 30)
```

---

## WebSocket Protocol

The client talks to the sim over plain JSON WebSocket messages on port `8765`.

**Set joint targets (radians):**
```json
→ {"cmd": "set_joints", "positions": {"joint1": 0.0, "joint2": -0.785, ...}}
```

**Query current simulated state:**
```json
→ {"cmd": "get_state"}
← {"joint_positions": {"joint1": 0.0, ...}, "joint_velocities": {"joint1": 0.0, ...}}
```
