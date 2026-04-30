with open('phase4.py', 'r') as f:
    text = f.read()

# remove the override logic to stop creating dummy components
text = text.replace("if valid_pns == ['UNKNOWN_PN'] and valid_sns == ['UNKNOWN_SN']:\n                # DONT SKIP! WE NEED COMPONENTS!\n                pass", 
                   "if valid_pns == ['UNKNOWN_PN'] and valid_sns == ['UNKNOWN_SN']:\n                continue")

with open('phase4.py', 'w') as f:
    f.write(text)
