
import tempfile
from pathlib import Path
from src.database import Database
from src.parser import CodeParser

def test_parser_python():
    db = Database(":memory:")
    project_id = db.add_project("test_project", "/tmp/test")
    
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.py"
        p.write_text("class MyClass:\n    def my_func():\n        x = 10\n")
        
        parser = CodeParser()
        parser.parse_file(db, project_id, str(p))
        
        # Verify
        node_class = db.get_node_by_name(project_id, "MyClass")
        assert node_class is not None
        assert node_class["type"] == "class"
        assert node_class["start_line"] == 1
        
        node_func = db.get_node_by_name(project_id, "my_func")
        assert node_func is not None
        assert node_func["type"] == "function"
        assert node_func["start_line"] == 2
        
        node_var = db.get_node_by_name(project_id, "x")
        assert node_var is not None
        assert node_var["type"] == "variable"
        assert node_var["start_line"] == 3

def test_parser_javascript():
    db = Database(":memory:")
    project_id = db.add_project("test_project_js", "/tmp/test_js")
    
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.js"
        p.write_text("class MyJSClass {}\nfunction myJSFunc() {}\nconst y = 20;\n")
        
        parser = CodeParser()
        parser.parse_file(db, project_id, str(p))
        
        node_class = db.get_node_by_name(project_id, "MyJSClass")
        assert node_class is not None
        assert node_class["type"] == "class"
        
        node_func = db.get_node_by_name(project_id, "myJSFunc")
        assert node_func is not None
        assert node_func["type"] == "function"
        
        node_var = db.get_node_by_name(project_id, "y")
        assert node_var is not None
        assert node_var["type"] == "variable"
