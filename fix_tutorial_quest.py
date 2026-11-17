fp = 'c:/Users/sao/Documents/dofus-emu/dofus-network/agent/planner.py'
with open(fp, 'r') as f:
    lines = f.readlines()

# Find _tutorial_quest method and remove leftover lines
start = None
end = None
for i, line in enumerate(lines):
    if 'def _tutorial_quest' in line:
        start = i
    if start is not None and i > start:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and not stripped.startswith('"""'):
            indent = len(line) - len(line.lstrip())
            if indent <= 4:  # same indent as def
                end = i
                break

if start is not None and end is None:
    end = len(lines)

if start is not None:
    # Remove lines from the def line to end
    new_lines = lines[:start] + [f'    @staticmethod\n    def _tutorial_quest(obs: Dict[str, Any],\n                        tutorial: Dict[str, Any]) -> Optional[int]:\n        """The quest the scenario is driving, without guessing from the list order."""\n        from agent.equipment_utils import _tutorial_quest as _tutorial_quest_impl\n        return _tutorial_quest_impl(obs, tutorial)\n'] + lines[end:]
    with open(fp, 'w') as f:
        f.writelines(new_lines)
    print(f"_tutorial_quest replaced (lines {start+1}-{end})")
else:
    print("_tutorial_quest not found")