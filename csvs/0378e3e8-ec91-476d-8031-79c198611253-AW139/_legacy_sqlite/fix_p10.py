with open('phase10.py', 'r') as f:
    text = f.read()

# Fix the dictionary conversion error for sqlite3.Row in fetchone
text = text.replace("export['asset'] = dict(cur.fetchone()) if cur.rowcount != 0 else {}", 
                    "row = cur.fetchone(); export['asset'] = dict(row) if row else {}")
text = text.replace("stats = dict(cur.fetchone())",
                    "row = cur.fetchone(); stats = dict(row) if row else {}")

with open('phase10.py', 'w') as f:
    f.write(text)
