import re

with open('phase4.py', 'r') as f:
    content = f.read()

content = content.replace('\n', '\n')
content = content.replace('f.write("\n== Phase 4 verification ==\n")', 'f.write("\n== Phase 4 verification ==\n")')

with open('phase4.py', 'w') as f:
    f.write(content)
