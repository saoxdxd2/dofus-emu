# Surgical edits to desktop/mainform.cs (CRLF, one blank line between statements).
# Span-based: locate by a unique marker, replace up to an end marker. Each edit
# asserts it found its markers, so a stale anchor fails loudly instead of
# silently mangling the file.
import io
import sys

PATH = "desktop/mainform.cs"
src = io.open(PATH, encoding="utf-8", newline="").read()


def d(*lines):
    return "\r\n\r\n".join(lines)


def insert_after(marker, text, label):
    global src
    i = src.find(marker)
    if i < 0 or src.find(marker, i + 1) >= 0:
        print("FAIL locate %s (%d matches)" % (label, src.count(marker)))
        sys.exit(1)
    i += len(marker)
    src = src[:i] + text + src[i:]
    print("ok   " + label)


def insert_before(marker, text, label):
    global src
    i = src.find(marker)
    if i < 0 or src.find(marker, i + 1) >= 0:
        print("FAIL locate %s (%d matches)" % (label, src.count(marker)))
        sys.exit(1)
    src = src[:i] + text + src[i:]
    print("ok   " + label)


def replace_span(start_marker, end_marker, text, label):
    global src
    i = src.find(start_marker)
    if i < 0 or src.find(start_marker, i + 1) >= 0:
        print("FAIL locate %s (%d matches)" % (label, src.count(start_marker)))
        sys.exit(1)
    j = src.find(end_marker, i + len(start_marker))
    if j < 0:
        print("FAIL end %s" % label)
        sys.exit(1)
    j += len(end_marker)
    src = src[:i] + text + src[j:]
    print("ok   " + label)


# --- 1. log queue fields -------------------------------------------------
insert_after(
    "        private long _logBytes = 0;",
    d(
        "",
        "        private readonly ConcurrentQueue<string> _logQueue = new ConcurrentQueue<string>();",
        "",
        "        private readonly AutoResetEvent _logSignal = new AutoResetEvent(false);",
        "",
        "        private Thread _logWorker;",
        "",
        "        private volatile bool _logWorkerStop = true;",
    ),
    "log-queue fields",
)

# --- 2. Log(): no disk work on the caller's thread -----------------------
replace_span(
    "            try\r\n\r\n            {\r\n\r\n                lock (_logFileLock)",
    "catch { try { if (_logWriter != null) { _logWriter.Dispose(); _logWriter = null; } } catch { }",
    d(
        "            // Disk work lives on the log writer thread. Callers are mostly the",
        "            // UI thread (every bridged JS message, every grid action), and a lock",
        "            // plus a stream write there landed inside the message pump.",
        "",
        "            EnqueueLogLine(msg);",
    ),
    "Log() off-thread",
)

# --- 3. FlushLog() + the writer itself -----------------------------------
replace_span(
    "        private void FlushLog()",
    "            }\r\n\r\n        }",
    d(
        "        private void FlushLog()",
        "",
        "        {",
        "",
        "            SignalLogWorker();",
        "",
        "        }",
        "",
        "        private void EnqueueLogLine(string msg)",
        "",
        "        {",
        "",
        "            if (_logWorkerStop) return;",
        "",
        "            _logQueue.Enqueue(msg);",
        "",
        "            SignalLogWorker();",
        "",
        "        }",
        "",
        "        private void SignalLogWorker()",
        "",
        "        {",
        "",
        "            try { _logSignal.Set(); } catch { }",
        "",
        "        }",
        "",
        "        private void StartLogWriter()",
        "",
        "        {",
        "",
        "            if (_logWorker != null) return;",
        "",
        "            _logWorkerStop = false;",
        "",
        "            var thread = new Thread(LogWriterLoop);",
        "",
        "            thread.IsBackground = true;",
        "",
        "            thread.Name = \"DofusFarm log writer\";",
        "",
        "            _logWorker = thread;",
        "",
        "            thread.Start();",
        "",
        "        }",
        "",
        "        /// <summary>Drain, flush, stop. Idempotent; call once the window is closed.</summary>",
        "",
        "        private void StopLogWriter()",
        "",
        "        {",
        "",
        "            Thread thread = _logWorker;",
        "",
        "            if (thread == null) return;",
        "",
        "            _logWorkerStop = true;",
        "",
        "            SignalLogWorker();",
        "",
        "            try { thread.Join(2000); } catch { }",
        "",
        "            lock (_logFileLock)",
        "",
        "            {",
        "",
        "                try { if (_logWriter != null) { _logWriter.Flush(); _logWriter.Dispose(); _logWriter = null; } } catch { }",
        "",
        "            }",
        "",
        "            _logWorker = null;",
        "",
        "        }",
        "",
        "        private void LogWriterLoop()",
        "",
        "        {",
        "",
        "            while (true)",
        "",
        "            {",
        "",
        "                try { _logSignal.WaitOne(1000); } catch { }",
        "",
        "                DrainLogQueue();",
        "",
        "                if (_logWorkerStop && _logQueue.IsEmpty) return;",
        "",
        "            }",
        "",
        "        }",
        "",
        "        private void DrainLogQueue()",
        "",
        "        {",
        "",
        "            string line;",
        "",
        "            bool wrote = false;",
        "",
        "            while (_logQueue.TryDequeue(out line))",
        "",
        "            {",
        "",
        "                try",
        "",
        "                {",
        "",
        "                    lock (_logFileLock)",
        "",
        "                    {",
        "",
        "                        if (_logWriter == null) OpenLogLocked(false);",
        "",
        "                        if (_logBytes >= LogMaxBytes)",
        "",
        "                        {",
        "",
        "                            RotateLogLocked();",
        "",
        "                            OpenLogLocked(true);",
        "",
        "                        }",
        "",
        "                        _logWriter.WriteLine(line);",
        "",
        "                        _logBytes += Encoding.UTF8.GetByteCount(line) + Environment.NewLine.Length;",
        "",
        "                        wrote = true;",
        "",
        "                        _logWritesSinceFlush++;",
        "",
        "                        if (_logWritesSinceFlush >= 200)",
        "",
        "                        {",
        "",
        "                            _logWriter.Flush();",
        "",
        "                            _logWritesSinceFlush = 0;",
        "",
        "                        }",
        "",
        "                    }",
        "",
        "                }",
        "",
        "                catch",
        "",
        "                {",
        "",
        "                    try { if (_logWriter != null) { _logWriter.Dispose(); _logWriter = null; } } catch { }",
        "",
        "                }",
        "",
        "            }",
        "",
        "            // Flush when the burst is over, not once per line.",
        "",
        "            if (wrote && _logQueue.IsEmpty)",
        "",
        "            {",
        "",
        "                lock (_logFileLock)",
        "",
        "                {",
        "",
        "                    try { if (_logWriter != null) { _logWriter.Flush(); _logWritesSinceFlush = 0; } } catch { }",
        "",
        "                }",
        "",
        "            }",
        "",
        "        }",
    ),
    "log writer thread",
)

# --- 4. start it before anything can log ---------------------------------
insert_after(
    "            InitializeComponent();",
    "\r\n\r\n            StartLogWriter();",
    "start writer",
)

# --- 5. grid double buffering -------------------------------------------
insert_before(
    "            _grid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(26, 28, 37);",
    d("            EnableGridDoubleBuffering();", ""),
    "grid double buffer call",
)

insert_before(
    "        private void RefreshGrid(bool forceRedraw = true)",
    d(
        "        // One rebuild per state change is unavoidable; a repaint per cell is not.",
        "",
        "        private void EnableGridDoubleBuffering()",
        "",
        "        {",
        "",
        "            try",
        "",
        "            {",
        "",
        "                typeof(DataGridView).GetProperty(\"DoubleBuffered\",",
        "",
        "                    System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)",
        "",
        "                    .SetValue(_grid, true, null);",
        "",
        "            }",
        "",
        "            catch { }",
        "",
        "        }",
        "",
        "        private static void SetCellValue(DataGridViewRow row, int column, string value)",
        "",
        "        {",
        "",
        "            if (row == null) return;",
        "",
        "            DataGridViewCell cell = row.Cells[column];",
        "",
        "            if (!string.Equals(cell.Value as string, value, StringComparison.Ordinal)) cell.Value = value;",
        "",
        "        }",
        "",
    ),
    "grid helpers",
)

# --- 6. update rows in place instead of clearing them --------------------
replace_span(
    "                            _grid.Rows.Clear();",
    "                                    row.Selected = true;",
    d(
        "                            // Reuse the existing rows when the count is unchanged:",
        "                            // Clear+Add reset the scroll position and repainted the",
        "                            // whole body every time one status flipped.",
        "",
        "                            bool reuseRows = _grid.Rows.Count == list.Count;",
        "",
        "                            if (!reuseRows) _grid.Rows.Clear();",
        "",
        "                            int runningCount = 0;",
        "",
        "                            for (int i = 0; i < list.Count; i++)",
        "",
        "                            {",
        "",
        "                                InstanceModel inst = list[i];",
        "",
        "                                bool isRunning = inst.IsRunning || (_webViewBackend != null && _webViewBackend.GetState(inst.Name) == ClientInstanceState.Running);",
        "",
        "                                if (isRunning)",
        "",
        "                                {",
        "",
        "                                    inst.IsRunning = true;",
        "",
        "                                    runningCount++;",
        "",
        "                                }",
        "",
        "                                string proxyDisplay = inst.Proxy ?? \"127.0.0.1:8880\";",
        "",
        "                                proxyDisplay = (proxyDisplay.Contains(\"127.0.0.1\") || proxyDisplay.Contains(\"Accelerator\"))",
        "",
        "                                    ? \"Built-in Accelerator\"",
        "",
        "                                    : proxyDisplay.Replace(\"[\U0001F310] \", \"\");",
        "",
        "                                string statusText = isRunning ? \"Running\" : \"Stopped\";",
        "",
        "                                string customName = inst.CustomName ?? inst.DisplayName;",
        "",
        "                                string ramText = hostShared ? \"Shared\" : (inst.RamMb > 0 ? (inst.RamMb + \" MB\") : \"Auto\");",
        "",
        "                                string coresText = hostShared ? \"Shared\" : (inst.Cores > 0 ? (inst.Cores + (inst.Cores == 1 ? \" Core\" : \" Cores\")) : \"Auto\");",
        "",
        "                                string deviceText = inst.PhoneModelName ?? \"Galaxy Tab S6\";",
        "",
        "                                string actionText = isRunning ? \"Stop\" : \"Launch\";",
        "",
        "                                DataGridViewRow row;",
        "",
        "                                if (reuseRows)",
        "",
        "                                {",
        "",
        "                                    row = _grid.Rows[i];",
        "",
        "                                    SetCellValue(row, 0, statusText);",
        "",
        "                                    SetCellValue(row, 1, customName);",
        "",
        "                                    SetCellValue(row, 2, inst.Name);",
        "",
        "                                    SetCellValue(row, 3, ramText);",
        "",
        "                                    SetCellValue(row, 4, coresText);",
        "",
        "                                    SetCellValue(row, 5, deviceText);",
        "",
        "                                    SetCellValue(row, 6, proxyDisplay);",
        "",
        "                                    SetCellValue(row, 7, actionText);",
        "",
        "                                }",
        "",
        "                                else",
        "",
        "                                {",
        "",
        "                                    int rowIdx = _grid.Rows.Add(statusText, customName, inst.Name,",
        "",
        "                                        ramText, coresText, deviceText, proxyDisplay, actionText,",
        "",
        "                                        \"Edit\", \"Clone\", \"Reset\", \"Delete\");",
        "",
        "                                    row = _grid.Rows[rowIdx];",
        "",
        "                                    row.Cells[\"CustomName\"].Style.Font = _customNameFont;",
        "",
        "                                    row.Cells[\"CustomName\"].Style.ForeColor = Color.White;",
        "",
        "                                    row.Cells[\"Name\"].Style.ForeColor = ColMuted;",
        "",
        "                                    row.Cells[\"Device\"].Style.ForeColor = Color.FromArgb(226, 232, 240);",
        "",
        "                                    row.Cells[\"Proxy\"].Style.ForeColor = ColMuted;",
        "",
        "                                }",
        "",
        "                                row.Tag = inst;",
        "",
        "                                row.Cells[\"Status\"].ToolTipText = isRunning ? \"Click to bring this running instance window to front\" : \"\";",
        "",
        "                                if (!string.IsNullOrEmpty(selectedName) && string.Equals(inst.Name, selectedName, StringComparison.OrdinalIgnoreCase))",
        "",
        "                                    row.Selected = true;",
    ),
    "grid in-place rows",
)

# --- 7. stop the writer once the window is gone --------------------------
if "protected override void OnFormClosed" in src:
    print("INFO OnFormClosed already exists (not touching it)")
else:
    insert_before(
        "        protected override void OnFormClosing(FormClosingEventArgs e)",
        d(
            "        protected override void OnFormClosed(FormClosedEventArgs e)",
            "",
            "        {",
            "",
            "            try { StopLogWriter(); } catch { }",
            "",
            "            base.OnFormClosed(e);",
            "",
            "        }",
            "",
        ),
        "OnFormClosed",
    )

io.open(PATH, "w", encoding="utf-8", newline="").write(src)
print("written %d chars" % len(src))
