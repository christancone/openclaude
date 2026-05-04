with open('phase5.py', 'r') as f:
    text = f.read()

text = text.replace("""        # We must add the foreign key target component_id if there is one
        if comp_ids_to_link:
            first_c = list(comp_ids_to_link)[0]
            conn.execute("UPDATE events SET component_id = ? WHERE id = ?", (first_c, ev_id))
        
        # Link event to page""", """        # Link event to page""")

text = text.replace("""        for c_id in comp_ids_to_link:
            # We don't verify component exists here, rely on foreign keys or just let it fail silently if not strict""", """        
        # We must add the foreign key target component_id if there is one
        if comp_ids_to_link:
            first_c = list(comp_ids_to_link)[0]
            conn.execute("UPDATE events SET component_id = ? WHERE id = ?", (first_c, ev_id))
            
        for c_id in comp_ids_to_link:
            # We don't verify component exists here, rely on foreign keys or just let it fail silently if not strict""")

with open('phase5.py', 'w') as f:
    f.write(text)
