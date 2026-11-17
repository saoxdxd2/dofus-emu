fp = 'c:/Users/sao/Documents/dofus-emu/dofus-network/agent/autopilot.py'
with open(fp, 'r') as f:
    lines = f.readlines()
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if stripped.startswith('def _warn_if_no_bridge') or stripped.startswith('def _screen_text') or stripped.startswith('def _login_banned') or stripped.startswith('def _login_lockout') or stripped.startswith('def _farm_allowed') or stripped.startswith('def _bridge_alive') or stripped.startswith('def _quest_data_pending') or stripped.startswith('def _note_schedule') or stripped.startswith('def _fight_summary') or stripped.startswith('def _fight_line') or stripped.startswith('def _record_observation') or stripped.startswith('def _obs_map') or stripped.startswith('def _obs_cell') or stripped.startswith('def _world_key') or stripped.startswith('def _dialog_signature') or stripped.startswith('def _action_complete'):
        print(f'L{i}: {stripped}')
        # Check if it's a delegate
        for j in range(i+1, min(i+5, len(lines))):
            nline = lines[j]
            if 'autopilot_utils' in nline:
                print(f'  L{j+1}: {nline.strip()} -> DELEGATED')
                break
            if nline.strip() and not nline.strip().startswith('#') and not nline.strip().startswith('\"\"\"') and not nline.strip().startswith('return') and not nline.strip().startswith('pass'):
                print(f'  L{j+1}: {nline.rstrip()} -> NOT DELEGATED')
                break