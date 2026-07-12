import hashlib
from pathlib import Path


import tree_sitter
import tree_sitter_python
import tree_sitter_javascript
import tree_sitter_typescript

# Try to import tree_sitter_dart, fallback if it fails
try:
    import tree_sitter_dart
    DART_AVAILABLE = True
except ImportError:
    DART_AVAILABLE = False

from src.database import Database

class ParserError(Exception):
    pass

class CodeParser:
    def __init__(self) -> None:
        self.languages = {
            ".py": tree_sitter.Language(tree_sitter_python.language()),
            ".js": tree_sitter.Language(tree_sitter_javascript.language()),
            ".ts": tree_sitter.Language(tree_sitter_typescript.language_typescript()),
        }
        
        if DART_AVAILABLE:
            self.languages[".dart"] = tree_sitter.Language(tree_sitter_dart.language())
        
        self.parsers = {}
        for ext, lang in self.languages.items():
            self.parsers[ext] = tree_sitter.Parser(lang)

        # Define queries to extract classes, functions, and variables
        self.queries_str = {
            ".py": """
                (class_definition name: (identifier) @class.name) @class.def
                (function_definition name: (identifier) @function.name) @function.def
                (assignment left: (identifier) @variable.name) @variable.def
            """,
            ".js": """
                (class_declaration name: (identifier) @class.name) @class.def
                (function_declaration name: (identifier) @function.name) @function.def
                (lexical_declaration (variable_declarator name: (identifier) @variable.name) @variable.def)
                (variable_declaration (variable_declarator name: (identifier) @variable.name) @variable.def)
            """,
            ".ts": """
                (class_declaration name: (type_identifier) @class.name) @class.def
                (function_declaration name: (identifier) @function.name) @function.def
                (lexical_declaration (variable_declarator name: (identifier) @variable.name) @variable.def)
                (variable_declaration (variable_declarator name: (identifier) @variable.name) @variable.def)
            """,
            ".dart": """
                (class_definition name: (identifier) @class.name) @class.def
                (function_signature name: (identifier) @function.name) @function.def
                (declaration name: (identifier) @variable.name) @variable.def
            """
        }
        
        self.queries = {}
        for ext, lang in self.languages.items():
            if ext in self.queries_str:
                try:
                    self.queries[ext] = tree_sitter.Query(lang, self.queries_str[ext])
                except Exception as e:
                    print(f"Error compiling query for {ext}: {e}")

    def parse_file(self, db: Database, project_id: int, file_path: str) -> None:
        path = Path(file_path)
        ext = path.suffix
        
        if ext not in self.parsers or ext not in self.queries:
            return  # Skip unsupported files
            
        try:
            with open(path, "rb") as f:
                content = f.read()
        except Exception:
            return
            
        file_hash = hashlib.sha256(content).hexdigest()
        file_id = db.add_file(project_id, str(path), file_hash)
        
        parser = self.parsers[ext]
        tree = parser.parse(content)
        query = self.queries[ext]
        
        try:
            cursor = tree_sitter.QueryCursor(query)
            matches = cursor.matches(tree.root_node)
            
            for match_index, match_dict in matches:
                # We need to find the node type and definition
                node_type = None
                name_node = None
                def_node = None
                
                # Check for class, function, variable
                for k in ["class", "function", "variable"]:
                    if f"{k}.name" in match_dict and f"{k}.def" in match_dict:
                        node_type = k
                        # In matches, values are lists of nodes
                        name_node_list = match_dict[f"{k}.name"]
                        def_node_list = match_dict[f"{k}.def"]
                        if name_node_list and def_node_list:
                            name_node = name_node_list[0]
                            def_node = def_node_list[0]
                        break
                
                if node_type and name_node and def_node:
                    name_str = content[name_node.start_byte:name_node.end_byte].decode('utf8')
                    start_line = def_node.start_point[0] + 1
                    end_line = def_node.end_point[0] + 1
                    db.add_node(project_id, file_id, node_type, name_str, start_line, end_line)
                    
        except Exception as e:
            print(f"Error parsing tree for {file_path}: {e}")
