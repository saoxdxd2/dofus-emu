fp = 'c:/Users/sao/Documents/dofus-emu/dofus-network/agent/planner.py'
with open(fp, 'r') as f:
    lines = f.readlines()
in_class = False
class_indent = 0
undelegated = []
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
            for j in range(i+1, min(i+60, len(lines))):
                nline = lines[j]
                nindent = len(nline) - len(nline.lstrip())
                if nline.strip() and (nline.strip().startswith('def ') or nline.strip().startswith('class ')):
                    if nindent <= indent:
                        break
                if 'self.' in nline:
                    uses_self = True
                    break
            if not uses_self:
                is_delegate = False
                for j in range(i+1, min(i+5, len(lines))):
                    nline = lines[j]
                    if 'autopilot_utils' not in nline and ('_utils' in nline):
                        if 'import' in nline:
                            is_delegate = True
                            break
                if not is_delegate:
                    undelegated.append((i+1, method_name))
print(f'Undelegated pure methods: {len(undelegated)}')
for ln, nm in undelegated:
    print(f'  L{ln:4d}: {nm}')