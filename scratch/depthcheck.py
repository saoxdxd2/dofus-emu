import io
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "desktop/mainform.cs"
src = io.open(path, encoding="utf-8", newline="").read()
lines = src.split("\r\n")

depth = 0
rows = []  # (lineno, depth_before_line, text)

for n, line in enumerate(lines, 1):
    start_depth = depth
    i = 0
    instr = False
    esc = False
    while i < len(line):
        c = line[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
        i += 1
    rows.append((n, start_depth, line.rstrip()))

print("final depth:", depth)
print("--- members not at depth 2 (namespace + class) ---")
shown = 0
for n, d, text in rows:
    s = text.strip()
    if s.startswith(("private ", "public ", "protected ", "internal ")) and d != 2:
        print("line %d  depth %d  %s" % (n, d, s[:80]))
        shown += 1
        if shown >= 20:
            break
if shown == 0:
    print("(none)")

print("--- depth at the methods I added ---")
for n, d, text in rows:
    s = text.strip()
    if s.startswith(("private void EnqueueLogLine", "private void FlushLog",
                     "private void StartLogWriter", "private void StopLogWriter",
                     "private void LogWriterLoop", "private void DrainLogQueue",
                     "private void EnableGridDoubleBuffering", "private static void SetCellValue",
                     "protected override void OnFormClosed", "private void Log(string msg)")):
        print("line %d  depth %d  %s" % (n, d, s[:80]))
