import json

path = r'C:\Users\sao\.gemini\antigravity\brain\b7861677-f164-421d-b60c-ee08d719776e\.system_generated\logs\transcript.jsonl'
lines = []
with open(path, 'r', encoding='utf-8') as f:
    for line in f:
        obj = json.loads(line)
        if obj.get('type') == 'USER_INPUT':
            lines.append(('USER', obj.get('content', ''), obj.get('step_index')))
        elif obj.get('type') == 'PLANNER_RESPONSE' and obj.get('content'):
            lines.append(('ASSISTANT_CONTENT', obj.get('content', '')[:300], obj.get('step_index')))

for t, c, step in lines:
    print(f"[{step}] === {t} ===")
    print(str(c)[:300])
