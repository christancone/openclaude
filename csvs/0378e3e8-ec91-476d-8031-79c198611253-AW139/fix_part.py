with open('phase4.py', 'r') as f:
    text = f.read()

# Fix table part_types columns
text = text.replace("INSERT OR IGNORE INTO part_types (canonical_pn, description, ata_chapter, is_llp, is_overhaul)", "INSERT OR IGNORE INTO part_types (id, description, ata_chapter, is_llp, is_overhaul)")

# Fix table serials columns
text = text.replace("INSERT OR IGNORE INTO serials (id, canonical_pn, serial_number, description, installed_status)", "INSERT OR IGNORE INTO serials (id, part_type_id, serial_number, description, installed_status)")

# Fix table components columns
text = text.replace("INSERT OR IGNORE INTO components (id, asset_id, canonical_pn, installed_sn, description, tier, status)", "INSERT OR IGNORE INTO components (id, asset_id, part_type_id, installed_sn, description, tier, status)")
text = text.replace("INSERT OR IGNORE INTO components (id, asset_id, canonical_pn, installed_sn, description, tier, status, is_llp, is_overhaul)", "INSERT OR IGNORE INTO components (id, asset_id, part_type_id, installed_sn, description, tier, status)")
text = text.replace("(comp_id, asset_id, pn, sn, f'Component {pn}/{sn}', tier, 'DISCOVERED', meta['is_llp'], meta['is_overhaul'])", "(comp_id, asset_id, pn, sn, f'Component {pn}/{sn}', tier, 'DISCOVERED')")

with open('phase4.py', 'w') as f:
    f.write(text)
