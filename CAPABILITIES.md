## Purpose
Provides a structural, graph-based, and semantic representation of codebases using `tree-sitter` and `sentence-transformers`. This server acts as the central knowledge brain for agents exploring large code repositories.

## When To Use
Use this server when you need to parse code, understand syntax trees, discover abstract code by semantic meaning (e.g. "auth middleware"), or query structural relationships (dependencies/dependents) in source code.

## When Not To Use
Do not use this for general text-based string search if you already know exactly what file you are looking at (use `grep` instead). 

## Main Capabilities
- **Deterministic Parsing:** High-accuracy AST parsing using `tree-sitter`.
- **Knowledge Graph:** Resolving functions and classes into a local SQLite DB for instant relational lookups.
- **Semantic Vector Search:** Searching for abstract concepts across code bases using `BAAI/bge-small-en-v1.5` embeddings.
- **Supported Languages:** Python, JavaScript, TypeScript, Dart, and Markdown.

## Data Scope
Operates on the source code files and directories requested by the client, storing relationships and semantic embeddings in a local `knowledge_graph.db`.

## Safety Notes
This server only performs static analysis and reads source code. It does not execute the code being analyzed.

## Typical Workflow (The Semantic to Lexical Pipeline)
When given a task in an unfamiliar codebase, use the following loop:
1. **The Broad Search (Semantic):** You do not know the exact symbol name. You call `semantic_search(project_name="my-project", query="JWT token validation logic")`.
2. **The Anchor Retrieval:** Karst embeds your query and returns the top matching functions/classes (e.g., `verify_token` at `src/jwt.py:10-45`).
3. **The Deep Dive (Lexical/Graph):** Now that you have exact symbol names, you use deterministic tools. You call `find_dependents(project_name="my-project", symbol_name="verify_token")` to see exactly which API routes rely on it.
4. **Conclusion:** You have successfully found the conceptual entry point and mapped its concrete execution path.
