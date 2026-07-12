import sqlite3
from typing import List, Optional, Dict, Any

class Database:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        # Enable foreign keys
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.init_db()

    def init_db(self) -> None:
        cursor = self.conn.cursor()
        
        # Create projects table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE,
                path TEXT
            )
        ''')
        
        # Create files table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                path TEXT,
                hash TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        ''')
        
        # Create nodes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                file_id INTEGER,
                type TEXT,
                name TEXT,
                start_line INTEGER,
                end_line INTEGER,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
            )
        ''')
        
        # Create edges table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                source_id INTEGER,
                target_id INTEGER,
                type TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(source_id) REFERENCES nodes(id) ON DELETE CASCADE,
                FOREIGN KEY(target_id) REFERENCES nodes(id) ON DELETE CASCADE
            )
        ''')
        
        self.conn.commit()

    def add_project(self, name: str, path: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute('INSERT INTO projects (name, path) VALUES (?, ?)', (name, path))
        self.conn.commit()
        return cursor.lastrowid or 0

    def add_file(self, project_id: int, path: str, file_hash: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute('INSERT INTO files (project_id, path, hash) VALUES (?, ?, ?)', (project_id, path, file_hash))
        self.conn.commit()
        return cursor.lastrowid or 0

    def add_node(self, project_id: int, file_id: int, node_type: str, name: str, start_line: int, end_line: int) -> int:
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO nodes (project_id, file_id, type, name, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (project_id, file_id, node_type, name, start_line, end_line))
        self.conn.commit()
        return cursor.lastrowid or 0

    def add_edge(self, project_id: int, source_id: int, target_id: int, edge_type: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO edges (project_id, source_id, target_id, type)
            VALUES (?, ?, ?, ?)
        ''', (project_id, source_id, target_id, edge_type))
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_node_by_name(self, project_id: int, name: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM nodes WHERE project_id = ? AND name = ?', (project_id, name))
        row = cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "project_id": row[1],
                "file_id": row[2],
                "type": row[3],
                "name": row[4],
                "start_line": row[5],
                "end_line": row[6]
            }
        return None

    def get_edges_for_node(self, node_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM edges WHERE source_id = ? OR target_id = ?', (node_id, node_id))
        rows = cursor.fetchall()
        edges = []
        for row in rows:
            edges.append({
                "id": row[0],
                "project_id": row[1],
                "source_id": row[2],
                "target_id": row[3],
                "type": row[4]
            })
        return edges

    def clear_project_data(self, project_id: int) -> None:
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM projects WHERE id = ?', (project_id,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
