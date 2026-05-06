import sqlite3
c = sqlite3.connect('graph.db')
try:
    c.execute("INSERT INTO pages_fts (rowid, page_id, text_content, file_name, document_type) VALUES (1, 'abc', 'def', 'ghi', 'jkl')")
except Exception as e:
    print(e)
