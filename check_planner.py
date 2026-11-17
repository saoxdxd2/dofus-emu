fp = 'c:/Users/sao/Documents/dofus-emu/dofus-network/agent/planner.py'
with open(fp, 'r') as f:
    lines = f.readlines()
in_class = False
class_indent = 0
methods = []
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == 'class QuestPlanner:':
        in_class = True
        class_indent = len(line) - len(line.lstrip())
        continue
    if in_class:
        if stripped and (stripped.startswith('def ') or stripped.startswith('async def ')):
            indent = len(line) - len(line.lstrip())
            if indent <= class_indent:
                in_class = False
                continue
            method_name = stripped.split('(')[0].replace('def ', '').replace('async ', '')
            uses_self = False
            self_lines = []
            for j in range(i+1, min(i+60, len(lines))):
                nline = lines[j]
                nindent = len(nline) - len(nline.lstrip())
                if nline.strip() and (nline.strip().startswith('def ') or nline.strip().startswith('class ')):
                    if nindent <= indent:
                        break
                if 'self.' in nline:
                    uses_self = True
                    break
            methods.append((i+1, method_name, uses_self))

pure = [(ln, nm) for ln, nm, us in methods if not us]
print(f'planner.py Pure methods remaining: {len(pure)}')
for ln, nm in pure:
    print(f'  L{ln:4d}: {nm}')

# Check which are already delegates
delegated = 0
not_delegated = 0
for ln, nm in pure:
    for j in range(ln-1, min(ln+4, len(lines))):
        nline = lines[j]
        if 'planner_utils' in nline or 'map_utils' in nline or 'combat_utils' in nline or 'objective_utils' in nline or 'equipment_utils' in nline or 'navigation_utils' in nline or 'tracking_utils' in nline or 'market_utils' in nline:
            delegated += 1
            break
        if nline.strip() and not nline.strip().startswith('#') and not nline.strip().startswith('"""') and not nline.strip().startswith('return') and not nline.strip().startswith('pass'):
            not_delegated += 1
            break
print(f'Delegated: {delegated}, Not delegated: {not_delegated}')