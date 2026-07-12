## Purpose
Provides a structural code graph representation for analysis using tree-sitter.

## When To Use
Use this server when you need to parse code, understand syntax trees, or query structural relationships in source code.

## When Not To Use
Do not use this for general text-based search (use ripgrep/grep instead).

## Main Capabilities
- Parsing code using tree-sitter.
- Extracting syntax trees and semantic relationships.
- Supporting Python, JavaScript, and TypeScript languages.

## Data Scope
Operates only on the source code files and directories requested by the client.

## Safety Notes
This server only performs static analysis and reads source code. It does not execute the code being analyzed.

## Typical Workflow
1. Client initializes the server.
2. Client requests structural analysis or queries a specific source file.
3. Server parses the file with tree-sitter and returns structural information.
