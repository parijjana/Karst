import subprocess
import sys
import asyncio
from pathlib import Path

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def main():
    print("Running CI gates...")
    try:
        gate_output = subprocess.check_output(
            ["uv", "run", "python", "scripts/gate.py"],
            stderr=subprocess.STDOUT,
            text=True
        )
        print(gate_output.strip())
        if "GATE FAIL" in gate_output:
            print("CI Gates failed! Commit aborted.", file=sys.stderr)
            sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(e.output.strip())
        print("CI Gates failed! Commit aborted.", file=sys.stderr)
        sys.exit(1)

    try:
        output = subprocess.check_output(["git", "diff", "--cached", "--name-only"], text=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running git diff: {e}", file=sys.stderr)
        sys.exit(1)
        
    lines = output.strip().split("\n")
    valid_exts = {".py", ".js", ".ts", ".dart", ".md"}
    
    modified_files = []
    for line in lines:
        if not line:
            continue
        filepath = Path(line)
        if filepath.suffix in valid_exts:
            # Send absolute paths as the server expects them to match indexed paths
            modified_files.append(str(filepath.absolute()))
            
    if not modified_files:
        print("No valid files to update in graph.")
        return
        
    project_name = Path.cwd().name
    aliases = {"mcp-hub": "MCePtion", "global-icebox": "Concinnity"}
    project_name = aliases.get(project_name, project_name)
    
    print(f"Updating graph for project: {project_name}")
    print(f"Files to update: {len(modified_files)}")
    
    server_params = StdioServerParameters(
        command="uv",
        args=["--quiet", "run", "python", "-m", "src.main"],
        env=None
    )
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                result = await session.call_tool(
                    "update_graph",
                    arguments={
                        "project_name": project_name,
                        "filepaths": modified_files
                    }
                )
                print(f"Graph update result: {result}")
    except Exception as e:
        print(f"Error communicating with MCP server: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
