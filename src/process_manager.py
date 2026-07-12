import asyncio
import os

ALLOWED_SCRIPTS = {
    "watchdog": ["uv", "run", "python", "-m", "scripts.watchdog"]
}

# In-memory registry of running tasks (script_name -> subprocess object)
active_processes: dict[str, asyncio.subprocess.Process] = {}

async def start_script(script_name: str) -> dict:
    if script_name not in ALLOWED_SCRIPTS:
        raise ValueError(f"Script '{script_name}' is not in the allowed registry.")
    
    if script_name in active_processes:
        proc = active_processes[script_name]
        if proc.returncode is None:
            return {"status": "already_running", "pid": proc.pid}
            
    cmd = ALLOWED_SCRIPTS[script_name]
    
    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)
    
    log_file = open(f"logs/{script_name}.log", "w")
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=log_file,
        stderr=log_file,
        preexec_fn=getattr(os, 'setsid', None) if os.name != 'nt' else None
    )
    
    active_processes[script_name] = proc
    return {"status": "started", "pid": proc.pid}

async def stop_script(script_name: str) -> dict:
    if script_name not in ALLOWED_SCRIPTS:
        raise ValueError(f"Script '{script_name}' is not in the allowed registry.")
        
    proc = active_processes.get(script_name)
    if not proc or proc.returncode is not None:
        return {"status": "not_running"}
        
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        
    return {"status": "stopped"}

def get_script_status(script_name: str) -> dict:
    if script_name not in ALLOWED_SCRIPTS:
        return {"status": "invalid_script"}
        
    proc = active_processes.get(script_name)
    if not proc:
        return {"status": "not_running"}
        
    if proc.returncode is not None:
        return {"status": "stopped", "exit_code": proc.returncode}
        
    return {"status": "running", "pid": proc.pid}
