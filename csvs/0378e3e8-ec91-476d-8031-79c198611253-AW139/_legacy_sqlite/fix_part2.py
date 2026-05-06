with open('phase4.py', 'r') as f:
    text = f.read()

# Fix table serials columns (remove description)
text = text.replace("INSERT OR IGNORE INTO serials (id, part_type_id, serial_number, description, installed_status) VALUES (?, ?, ?, ?, ?)", "INSERT OR IGNORE INTO serials (id, part_type_id, serial_number, installed_status) VALUES (?, ?, ?, ?)")
text = text.replace("(f\"{pn}::{sn}\", pn, sn, 'Engine seed', 'ON')", "(f\"{pn}::{sn}\", pn, sn, 'ON')")
text = text.replace("(f\"{pn}::{sn}\", pn, sn, f'Discovered SN {sn}', 'ON')", "(f\"{pn}::{sn}\", pn, sn, 'ON')")

with open('phase4.py', 'w') as f:
    f.write(text)
