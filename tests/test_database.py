import pytest
from typing import Generator
from src.karst_core.database.database import Database
from src.settings import TRUSTED_LOCAL_OWNER


def add_test_project(db: Database, name: str, path: str) -> int:
    return db.add_project(name, path, TRUSTED_LOCAL_OWNER, f"test:{name}")

@pytest.fixture
def db() -> Generator[Database, None, None]:
    database = Database(":memory:")
    yield database
    database.close()

def test_add_project(db: Database) -> None:
    project_id = add_test_project(db, "test_project", "/path/to/project")
    assert project_id > 0


def test_add_project_rejects_fake_client_owner(db: Database) -> None:
    with pytest.raises(ValueError, match="trusted local stdio domain"):
        db.add_project("test_project", "/path/to/project", "client-a", "stable")

def test_add_file(db: Database) -> None:
    project_id = add_test_project(db, "test_project", "/path/to/project")
    file_id = db.add_file(project_id, "test_file.py", "hash123")
    assert file_id > 0

def test_add_node(db: Database) -> None:
    project_id = add_test_project(db, "test_project", "/path/to/project")
    file_id = db.add_file(project_id, "test_file.py", "hash123")
    node_id = db.add_node(project_id, file_id, "function", "test_func", 10, 20)
    assert node_id > 0
    
    node = db.get_node_by_name(project_id, "test_func")
    assert node is not None
    assert node["name"] == "test_func"
    assert node["type"] == "function"
    assert node["start_line"] == 10
    assert node["end_line"] == 20

def test_add_edge(db: Database) -> None:
    project_id = add_test_project(db, "test_project", "/path/to/project")
    file_id = db.add_file(project_id, "test_file.py", "hash123")
    node1_id = db.add_node(project_id, file_id, "function", "func1", 1, 10)
    node2_id = db.add_node(project_id, file_id, "function", "func2", 11, 20)
    
    edge_id = db.add_edge(project_id, node1_id, node2_id, "calls")
    assert edge_id > 0
    
    edges = db.get_edges_for_node(node1_id)
    assert len(edges) == 1
    assert edges[0]["source_id"] == node1_id
    assert edges[0]["target_id"] == node2_id
    assert edges[0]["type"] == "calls"

def test_clear_project_data(db: Database) -> None:
    project_id = add_test_project(db, "test_project", "/path/to/project")
    file_id = db.add_file(project_id, "test_file.py", "hash123")
    db.add_node(project_id, file_id, "function", "test_func", 10, 20)
    
    db.clear_project_data(project_id)
    
    node = db.get_node_by_name(project_id, "test_func")
    # Due to CASCADE delete, the node should be gone if projects is deleted
    assert node is None
