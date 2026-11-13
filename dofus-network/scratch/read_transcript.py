import json

path = r'C:\Users\sao\.gemini\antigravity\brain\b7861677-f164-421d-b60c-ee08d719776e\.system_generated\logs\transcript.jsonl'
lines = []
with open(path, 'r', encoding='utf-8') as f:
    for line in f:
        obj = json.loads(line)
        if obj.get('type') == 'USER_INPUT':
            lines.append(('USER', obj.get('content', '')))
        elif obj.get('type') == 'PLANNER_RESPONSE' and obj.get('content'):
            lines.append(('ASSISTANT_CONTENT', obj.get('content', '')))

for t, c in lines[-6:]:
    print(f"=== {t} ===")
    print(str(c))


