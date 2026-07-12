import os
import sys
import time
import subprocess
import signal

PID_FILE = "data/ui.pid"

def start_ui():
    if os.path.exists(PID_FILE):
        print("UI might already be running (PID file exists). Try stopping it first.")
        return
        
    print("Starting UI...")
    # CREATE_NEW_PROCESS_GROUP is required to send CTRL_BREAK_EVENT on Windows
    proc = subprocess.Popen(
        ["uv", "run", "python", "src/web.py"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    print(f"UI started with PID {proc.pid}")

def stop_ui():
    if not os.path.exists(PID_FILE):
        print("No PID file found. UI is not running.")
        return
        
    with open(PID_FILE, "r") as f:
        pid_str = f.read().strip()
        if not pid_str:
            os.remove(PID_FILE)
            return
        pid = int(pid_str)
        
    print(f"Stopping UI gracefully (PID {pid})...")
    try:
        # Send CTRL_BREAK_EVENT to gracefully shutdown uvicorn
        os.kill(pid, signal.CTRL_BREAK_EVENT)
        print("Sent graceful termination signal. Waiting for shutdown to free ports...")
        time.sleep(3) # Give uvicorn time to close the socket
    except OSError:
        print("Process already dead or unable to kill.")
        
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    print("UI stopped.")
    
def restart_ui():
    stop_ui()
    time.sleep(2)
    start_ui()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/manage_ui.py [start|stop|restart]")
        sys.exit(1)
        
    action = sys.argv[1].lower()
    if action == "start":
        start_ui()
    elif action == "stop":
        stop_ui()
    elif action == "restart":
        restart_ui()
    else:
        print(f"Unknown action: {action}")
