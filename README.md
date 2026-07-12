# Karst

Karst is an advanced **Model Context Protocol (MCP)** server designed to parse, index, and analyze code structures into a unified Knowledge Graph. 

The core mission of Karst is to give LLMs and autonomous agents **deterministic, graph-based access** to large codebases, bridging the gap between raw text processing and deep architectural understanding.

---

## 🧠 Why We Built It (Architecture & Rationale)

While basic `grep` or regex-based tools can find strings, they lack semantic awareness of code (e.g., distinguishing a function definition from a string literal). To solve this, Karst was built with the following architectural pillars:

### 1. Deterministic Parsing (`tree-sitter`)
Instead of regex heuristics, Karst uses `tree-sitter` bindings for Python, JavaScript, TypeScript, Dart, and Markdown. 
**Why?** It ensures 100% accuracy in identifying classes, functions, and variables, even in syntactically complex or messy code files.

### 2. Local Knowledge Graph (SQLite)
Instead of relying on heavy graph databases (like Neo4j) which complicate deployment, Karst uses a heavily normalized **SQLite** database (`knowledge_graph.db`).
**Why?** It provides immediate, portable access to relational data (`projects` -> `files` -> `nodes` -> `edges`) with zero setup. We can run complex SQL joins to find dependents and dependencies in milliseconds.

### 3. Asynchronous Background Services
An MCP server must remain highly responsive. If the server blocks to re-index 10,000 files, the LLM client times out. 
**Why?** We decoupled heavy lifting into dedicated background services (managed via `src/manage_ui.py`):
*   **FileSystem Watchdog**: Immediately catches local file modifications and updates the graph without a full re-index.
*   **Deep-Sweep Re-indexer**: Periodically crawls the codebase to heal any missed changes and prevent "graph rot".
*   **Git Auto-Poller**: Synchronizes tracked repositories from remote origins every 60 seconds.
*   **Database Optimizer**: Runs `VACUUM` and `ANALYZE` to reclaim space and optimize query paths.
*   **Vulnerability Scanner**: Proactively flags dangerous patterns (e.g., `shell=True`, `os.system`) directly in the graph.
*   **Semantic Embedder**: We integrated `sentence-transformers` running the `BAAI/bge-small-en-v1.5` model. This allows hybrid search (both lexical graph lookups and semantic vector queries).

### 4. Visual Telemetry Dashboard
Agents are often black boxes. We built a Dark "Cyber-Slate" Web UI (served via `src/web.py` on port 8080).
**Why?** To provide real-time observability. Developers can visually navigate the Knowledge Graph (via Force-Graph), monitor service health, start/stop background scripts, and view aggregate telemetry (latency, tokens saved, records processed).

---

## 🛡️ How We Validated It (Quality & CI Gates)

Reliability is paramount for an agentic tool. Karst enforces strict quality control through a custom **CI Gate system** (`scripts/gate.py`) and automated git hooks.

Before any code is committed, it must pass a rigorous 5-stage validation pipeline:
1.  **G1 (Static Analysis):** Enforces strict `ruff` linting and strict `mypy` type-checking. No dynamic or loose typing is permitted.
2.  **G2 (Size Ratchet):** Prevents monolithic code. We enforce strict line limits (files must be <300 lines). The ratchet ensures technical debt never increases.
3.  **G3 (Structural Rules):** Prohibits wildcard imports (`from module import *`) to ensure clear dependency graphs.
4.  **G4 (Hermetic Testing):** Requires `pytest` execution in isolated environments using in-memory SQLite instances to verify the graph schema and parser logic.
5.  **G5 (Coverage):** Extracts and enforces a minimum test coverage percentage.

**Automated Self-Updating Graph:**
We validated the graph's accuracy by hooking it into `git`. A pre-commit hook automatically intercepts modified files and triggers an `update_graph` MCP command to Karst, meaning the knowledge graph updates itself exactly when the code changes.

---

## 🤖 The Agent Workflow (Semantic to Lexical Pipeline)

When an autonomous LLM agent is given a complex task in an unfamiliar codebase, Karst enables a two-step pipeline that mimics senior developer behavior:

### Step 1: The Broad Search (Semantic)
The agent often doesn't know the exact symbol names (e.g. human developers named a function `do_the_thing()`).
Instead of failing a strict lexical search, the agent calls:
`semantic_search(project_name="api", query="JWT validation and token parsing")`

Karst uses local embeddings to return the top **Anchor Nodes**, pointing the agent to the right conceptual neighborhood:
> *Top match: [0.892] function 'verify_token' at src/auth/jwt_utils.py:12-45*

### Step 2: The Deep Dive (Lexical / Graph)
Now equipped with exact symbol names and file paths, the agent switches back to deterministic graph traversal.
It calls `find_dependents(project_name="api", symbol_name="verify_token")` to see exactly which API routes rely on that authentication function.

**Semantic Search acts as the compass**, while the **AST Knowledge Graph acts as the map**.

---

## 🚀 Usage & Commands

### Prerequisites
*   `uv` package manager installed.
*   Python 3.10+

### Starting the Infrastructure

1.  **Start the Web Dashboard & Process Manager:**
    ```bash
    uv run python -m scripts.manage_ui start
    ```
    *Access the UI at `http://localhost:8080`*

2.  **Start the MCP Server (for Claude/Agents):**
    ```bash
    uv run python -m src.main
    ```

3.  **Run the Strict CI Validation Gate:**
    ```bash
    uv run python scripts/gate.py
    ```

### Available MCP Tools
Agents connecting to Karst have access to the following tools:
*   `index_project(project_name, root_path)`
*   `update_graph(project_name, filepaths)`
*   `query_symbol(project_name, symbol_name)`
*   `get_file_outline(project_name, filepath)`
*   `find_dependencies(project_name, symbol_name)`
*   `find_dependents(project_name, symbol_name)`
*   `semantic_search(project_name, query, limit)` - Execute vector similarity search on the codebase leveraging `BAAI/bge-small-en-v1.5` embeddings.
