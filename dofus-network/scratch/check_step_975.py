import json
path = r'C:\Users\sao\.gemini\antigravity\brain\b7861677-f164-421d-b60c-ee08d719776e\.system_generated\logs\transcript.jsonl'
with open(path, 'r', encoding='utf-8') as f:
    for line in f:
        obj = json.loads(line)
        idx = obj.get('step_index', 0)
        if 975 <= idx <= 1030 and obj.get('tool_calls'):
            for tc in obj['tool_calls']:
                print(f"[{idx}] {tc.get('name')}: {str(tc.get('args'))[:120]}")
