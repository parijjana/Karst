import tree_sitter
import tree_sitter_python
lang = tree_sitter.Language(tree_sitter_python.language())
parser = tree_sitter.Parser(lang)
src = b"class A:\n  def b():\n    x = 1"
tree = parser.parse(src)
query_str = """
(class_definition name: (identifier) @class.name) @class.def
(function_definition name: (identifier) @function.name) @function.def
(assignment left: (identifier) @variable.name) @variable.def
"""
query = tree_sitter.Query(lang, query_str)
cursor = tree_sitter.QueryCursor(query)
matches = cursor.matches(tree.root_node)
for m in matches:
    print(m)
