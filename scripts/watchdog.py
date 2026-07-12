import time
import os
import signal
import sys
from src.database import Database

def main():
    db_path = "data/knowledge_graph.db"
    timeout_seconds = 60 # 60s timeout
    check_interval = 10  # 10s polling
    
    print(f"Starting Watchdog Daemon (Timeout: {timeout_seconds}s)")
    
    while True:
        try:
            db = Database(db_path)
            stale_processes = db.get_stale_processes(timeout_seconds)
            
            for p in stale_processes:
                pid = p["pid"]
                script_name = p["script_name"]
                last_status = p["last_status"]
                elapsed = p["elapsed_seconds"]
                
                print(f"ZOMBIE DETECTED: {script_name} (PID: {pid}) hung for {elapsed:.1f}s. Last status: '{last_status}'")
                
                # Attempt to kill
                try:
                    if os.name == 'nt':
                        os.system(f"taskkill /F /PID {pid}")
                    else:
                        os.kill(pid, signal.SIGKILL)
                    kill_msg = f"Successfully killed PID {pid}."
                except Exception as e:
                    kill_msg = f"Failed to kill PID {pid}: {e}"
                
                print(kill_msg)
                
                # Log telemetry
                details = f"Last Status: '{last_status}'. Hung for {elapsed:.1f}s. {kill_msg}"
                db.log_telemetry(None, f"watchdog_kill:{script_name}", elapsed * 1000, 0, details)
                
                # Unregister from active_processes
                db.unregister_process(pid)
                
            db.close()
        except Exception as e:
            print(f"Watchdog error: {e}")
            
        time.sleep(check_interval)

if __name__ == "__main__":
    main()
