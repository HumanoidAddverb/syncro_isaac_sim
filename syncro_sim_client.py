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

# Shared state for keyboard input
last_pressed_key = None
key_lock = threading.Lock()
_gripper_is_open = True   # tracks local gripper toggle state

def getch():
    """Reads a single character from standard input (blocking)."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(sys.stdin.fileno())
        # Block indefinitely until a key is pressed (0 CPU usage)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def keyboard_thread():
    """Background thread to read keys and pass them to the main loop."""
    global last_pressed_key
    while True:
        ch = getch()
        with key_lock:
            last_pressed_key = ch
        if ch == '\x03': # Ctrl+C
            break

async def teleop():
    global last_pressed_key, _gripper_is_open
    async with websockets.connect("ws://localhost:8765") as ws:
        while True:
            # 1. Always get the latest state from the server
            await ws.send(json.dumps({"cmd": "get_state"}))
            state = json.loads(await ws.recv())
            current_positions = state.get("joint_positions", {})
            
            # 2. Check if a key was pressed in the background thread
            ch = None
            with key_lock:
                if last_pressed_key:
                    ch = last_pressed_key
                    last_pressed_key = None  # Consume the key press
            
            if ch == '\x03': # Ctrl+C
                break
                
            # 3. If a key was pressed, calculate the new target and send it
            if ch and ch in 'qwerty':
                idx = 'qwerty'.index(ch)
                if idx < len(JOINT_NAMES):
                    jname = JOINT_NAMES[idx]
                    new_target = float(current_positions.get(jname, 0.0)) + 0.03
                    
                    await ws.send(json.dumps({
                        "cmd": "set_joints",
                        "positions": {jname: new_target}
                    }))
                
            elif ch and ch in 'asdfgh':
                idx = 'asdfgh'.index(ch)
                if idx < len(JOINT_NAMES):
                    jname = JOINT_NAMES[idx]
                    new_target = float(current_positions.get(jname, 0.0)) - 0.03
                    
                    await ws.send(json.dumps({
                        "cmd": "set_joints",
                        "positions": {jname: new_target}
                    }))
            
            elif ch == 'p':
                # Toggle gripper open ↔ closed
                _gripper_is_open = not _gripper_is_open
                new_state = "open" if _gripper_is_open else "closed"
                await ws.send(json.dumps({
                    "cmd": "set_gripper_state",
                    "gripper_state": new_state,
                }))
                print(f"\n[GRIPPER] → {new_state}\n")

            # Print current state
            # joint_array = [round(float(current_positions.get(j, 0.0)), 2) for j in JOINT_NAMES]
            # t = time.monotonic() - START_TIME
            # print(f"[{t:.2f}] Current sim joints: {joint_array}      ", end='\r')

            await asyncio.sleep(0.01)

# Start keyboard thread
k_thread = threading.Thread(target=keyboard_thread, daemon=True)
k_thread.start()

print("--- KEYBOARD CONTROL ACTIVE ---")
print("Press 'qwerty' to ADD 0.03 to joint1-joint6 respectively")
print("Press 'asdfgh' to SUBTRACT 0.03 from joint1-joint6 respectively")
print("Press 'p'      to toggle gripper open/closed")
print("Press Ctrl+C to exit")
print("-------------------------------")

asyncio.run(teleop())