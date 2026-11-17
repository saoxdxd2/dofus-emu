fp = 'c:/Users/sao/Documents/dofus-emu/dofus-network/agent/planner.py'
with open(fp, 'r') as f:
    lines = f.readlines()

# Find and replace _map_interactives (lines 699-724, 0-indexed 698-723)
new_method = '''    @staticmethod
    def _map_interactives(obs: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The map's interactive elements, from the nested block or the flat key."""
        from agent.map_utils import map_interactives as _map_interactives_impl
        return _map_interactives_impl(obs)
'''

start = None
end = None
for i, line in enumerate(lines):
    if 'def _map_interactives' in line:
        start = i
    if start is not None and i > start:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and not stripped.startswith('"""'):
            indent = len(line) - len(line.lstrip())
            if indent <= 4:
                end = i
                break

if start is not None and end is None:
    end = len(lines)

if start is not None:
    new_lines = lines[:start] + [new_method] + lines[end:]
    with open(fp, 'w') as f:
        f.writelines(new_lines)
    print(f"_map_interactives replaced (lines {start+1}-{end})")
else:
    print("_map_interactives not found")