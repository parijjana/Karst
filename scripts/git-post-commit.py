import subprocess
import sys
import asyncio
from pathlib import Path

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def main():
    try:
        # Get commit hash and message
        log_output = subprocess.check_output(["git", "log", "-1", "--pretty=format:%H%n%s"], text=True)
        lines = log_output.strip().split("\n")
        commit_hash = lines[0]
        message = "\n".join(lines[1:])
        
        # Get files changed in this commit
        diff_output = subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD"], text=True)
    except subprocess.CalledProcessError as e:
        print(f"Error getting git info: {e}", file=sys.stderr)
        sys.exit(1)
        
    files_changed = []
    for line in diff_output.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0]
            filepath = parts[1]
            files_changed.append({"path": filepath, "status": status})
            
    project_name = Path.cwd().name
    print(f"Logging commit {commit_hash} for project: {project_name}")
    
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
                    "log_commit",
                    arguments={
                        "project_name": project_name,
                        "commit_hash": commit_hash,
                        "message": message,
                        "files_changed": files_changed
                    }
                )
                print(f"Log commit result: {result.content[0].text if hasattr(result, 'content') else result}")
    except Exception as e:
        print(f"Error communicating with MCP server: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
