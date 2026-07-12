import sqlite3
import os

DB_PATH = "data/knowledge_graph.db"

def verify_graph():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Check total projects and files
    cursor.execute("SELECT id, name, path FROM projects")
    projects = cursor.fetchall()
    print("--- Projects ---")
    for pid, pname, ppath in projects:
        cursor.execute("SELECT COUNT(*) FROM files WHERE project_id = ?", (pid,))
        file_count = cursor.fetchone()[0]
        print(f"Project: {pname} | Root: {ppath} | Indexed Files: {file_count}")
    
    print("\n--- Verifying Python Classes ---")
    # Verify that the 'Database' class was found
    cursor.execute('''
        SELECT n.name, n.type, f.path, n.start_line, n.end_line 
        FROM nodes n 
        JOIN files f ON n.file_id = f.id 
        WHERE n.name = 'Database' AND n.type = 'class'
    ''')
    for name, ntype, path, start, end in cursor.fetchall():
        print(f"Found {ntype} '{name}' in {os.path.basename(path)} (Lines {start}-{end})")

    print("\n--- Verifying Python Functions ---")
    # Verify that 'index_project' function was found
    cursor.execute('''
        SELECT n.name, n.type, f.path, n.start_line, n.end_line 
        FROM nodes n 
        JOIN files f ON n.file_id = f.id 
        WHERE n.name = 'index_project' AND n.type = 'function'
    ''')
    for name, ntype, path, start, end in cursor.fetchall():
        print(f"Found {ntype} '{name}' in {os.path.basename(path)} (Lines {start}-{end})")

    print("\n--- Verifying Markdown Headings ---")
    # Fetch a few markdown headings
    cursor.execute('''
        SELECT n.name, n.type, f.path, n.start_line
        FROM nodes n 
        JOIN files f ON n.file_id = f.id 
        WHERE n.type = 'heading'
        LIMIT 5
    ''')
    headings = cursor.fetchall()
    if not headings:
        print("No headings found (make sure markdown was correctly parsed).")
    else:
        for name, ntype, path, start in headings:
            print(f"Found {ntype}: '{name.strip()}' in {os.path.basename(path)} (Line {start})")

    conn.close()

if __name__ == "__main__":
    verify_graph()
