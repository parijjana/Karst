import asyncio
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def main():
    server_params = StdioServerParameters(
        command="uv",
        args=["--quiet", "run", "python", "-m", "src.main"],
        env=None
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            print("Connected to Code Graph Server!")
            
            result = await session.call_tool(
                "index_project",
                arguments={
                    "project_name": "code-graph-server",
                    "root_path": "D:/Programming/codex/mcp-servers/code-graph-server"
                }
            )
            print("Result of index_project:")
            print(result)

if __name__ == "__main__":
    asyncio.run(main())
