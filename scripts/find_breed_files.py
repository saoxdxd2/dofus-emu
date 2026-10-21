import os

for root, dirs, files in os.walk('.'):
    # Skip large directories
    if 'node_modules' in root or '.git' in root:
        continue
    for f in files:
        fname = f.lower()
        if ('breed' in fname or 'class' in fname) and (fname.endswith('.json') or fname.endswith('.py')):
            print(os.path.join(root, f))
