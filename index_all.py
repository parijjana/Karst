import asyncio
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def main():
    server_params = StdioServerParameters(
        command="uv",
        args=["--quiet", "run", "python", "-m", "src.main"],
        env=None
    )
    
    projects = [
        ("mcp-hub", "D:/Programming/codex/mcp-hub"),
        ("code-graph-server", "D:/Programming/codex/mcp-servers/code-graph-server"),
        ("contexthistory", "D:/Programming/codex/mcp-servers/contexthistory"),
        ("global-icebox", "D:/Programming/codex/mcp-servers/global-icebox"),
        ("lore", "D:/Programming/codex/mcp-servers/lore"),
        ("portfolio-server", "D:/Programming/codex/mcp-servers/portfolio-server")
    ]

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to Code Graph Server! Starting batch index...")
            
            for name, path in projects:
                print(f"Indexing {name} at {path}...")
                result = await session.call_tool(
                    "index_project",
                    arguments={
                        "project_name": name,
                        "root_path": path
                    }
                )
                print(f"  -> {result.content[0].text}")

if __name__ == "__main__":
    asyncio.run(main())
