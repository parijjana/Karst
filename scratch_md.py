import tree_sitter_markdown as ts_md
from tree_sitter import Language, Parser

def test_md():
    try:
        # Some versions expose LANGUAGE, others expose different things.
        print("Dir:", dir(ts_md))
        if hasattr(ts_md, 'LANGUAGE_MARKDOWN'):
            md_lang = Language(ts_md.LANGUAGE_MARKDOWN, 'markdown')
        elif hasattr(ts_md, 'language'):
            md_lang = Language(ts_md.language(), 'markdown')
        elif hasattr(ts_md, 'LANGUAGE'):
            md_lang = Language(ts_md.LANGUAGE, 'markdown')
        else:
            print("Could not find language object")
            return
            
        parser = Parser(md_lang)
        tree = parser.parse(b"# Heading 1\n## Heading 2\nSome text [link](file.md)")
        print(tree.root_node.sexp())
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test_md()
