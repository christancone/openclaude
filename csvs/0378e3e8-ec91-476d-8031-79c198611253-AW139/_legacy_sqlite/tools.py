import sqlite3
from pathlib import Path

def init_db(db_path: Path):
    with sqlite3.connect(db_path) as conn:
        with open('D:/work/openclaude/sparengine-export/phases/schema.sql', 'r') as f:
            schema = f.read()
        conn.executescript(schema)
        conn.commit()
