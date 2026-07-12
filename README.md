# Karst

Karst is an advanced MCP (Model Context Protocol) server designed to parse, index, and analyze code structures into a unified Knowledge Graph. By leveraging `tree-sitter`, a local SQLite database, and background workers, Karst allows LLMs and developers to rapidly traverse codebases, query definitions, and understand system architectures.

## Features & Capabilities

- **Multi-Language Parsing:** Supports AST-based parsing for Python, JavaScript, TypeScript, Dart, and Markdown files.
- **Knowledge Graph:** Stores projects, files, classes, functions, and relationships (edges) in a local, fast SQLite database (`knowledge_graph.db`).
- **MCP Tool Integration:** Exposes tools for standard AI agents to query the codebase efficiently:
  - `index_project`: Recursively index a directory.
  - `update_graph`: Update the graph for specific modified files.
  - `query_symbol`: Look up file and line definitions of symbols.
  - `get_file_outline`: Return an outline of classes and functions in a file.
  - `find_dependencies` & `find_dependents`: Explore code relationships.
- **Background Services:** Karst runs a robust suite of background scripts to enhance the graph without blocking the main MCP server:
  - **FileSystem Watchdog**: Listens for file changes and auto-indexes.
  - **Deep-Sweep Re-indexer**: Periodically re-evaluates parsed data.
  - **Git Auto-Poller**: Fetches remote changes dynamically.
  - **Database Optimizer**: Vacuums and analyzes the database to maintain performance.
  - **Vulnerability Scanner**: Automatically flags hardcoded shells or os executions.
  - **Semantic Embedder**: Uses `BAAI/bge-small-en-v1.5` (via `sentence-transformers`) to generate vector embeddings for nodes.
- **Web UI & Telemetry Dashboard:** A dark cyber-slate dashboard (available via `src/web.py`) provides:
  - A visual Force-Graph of the parsed code relationships.
  - Telemetry charts mapping latency and throughput (tokens/records processed) for both MCP tools and background services.
  - Process controls (Start/Stop) for each background service directly from the browser.
- **Automated Git Hook Integration**: A `pre-commit` hook setup automatically triggers incremental graph updates on commit.

## Usage

Start the server using `uv`:

```bash
uv run python -m src.main
```

Start the Web UI & Process Manager:

```bash
uv run python -m src.manage_ui start
```

Access the dashboard at `http://localhost:8080`.
