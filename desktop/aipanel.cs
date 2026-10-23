// aipanel.cs - the AI panel: settings, shortcuts, and the autonomous loop.
//
// Replaces the old "AI Commander" strip, which could not work by construction:
// it POSTed natural language to http://127.0.0.1:8890/sentinel/parse_command (a
// route the sentinel does not serve), swallowed the failure, then fell back to a
// keyword match whose default branch was AUTO_FARM -- so any text at all, typed
// or not, became "start farming". The result was then handed to a DOM-click hack
// that guesses at CSS selectors.
//
// What this panel does instead:
//   * Order  - one goal, a handful of steps, run by the real policy through the
//              real directive bridge, so it is validated and recorded.
//   * Auto   - the autonomous loop (agent/autopilot.py) as a child process, with
//              a step budget, a time budget and a stop file.
//   * Quick  - single tool calls (join, pass, follow, stop) posted straight to the
//              bridge at 127.0.0.1:8893, which is the same path Python uses.
//   * Settings and shortcuts persist to ai_settings.ini next to the exe.
//
// The panel never decides anything itself: it starts a process and shows its
// output. Every guard that matters (no buying, no invented prices) lives in
// agent/autopilot.py, where a test can see it.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

namespace DofusFarm.UI
{
    /// <summary>Persisted AI panel state. Plain key=value so no JSON dependency.</summary>
    public sealed class AiSettings
    {
        public string Mode = "off";          // off | order | auto
        public int TargetSlot = 0;           // 0 = all instances
        public string Policy = "planner";    // rule | encoder | planner
        public bool DryRun = false;
        // No wall-clock budget and no interval: the loop runs until the goals are
        // met or the user stops it. Steps 0 = unlimited for the same reason.
        public int Steps = 0;
        public string Goals = "";
        public string Order = "";

        // action -> virtual key code. Ints, because Keys.ToString() ordering for
        // modifier combinations ("F12, Control" vs "Control, F12") is not stable
        // enough to compare strings.
        public Dictionary<string, int> Shortcuts = DefaultShortcuts();

        /// <summary>
        /// Not user decisions. Only the planner can plan a quest — the rule maps a goal
        /// to a single tool and the encoder picks a tool for a step that no plan
        /// produced — and a step budget is a debugging aid rather than a setting. Both
        /// were exposed, so the panel could be put into a state that cannot work and
        /// the user had no way to know which of the three policies was the real one.
        /// </summary>
        public void PinDecisions()
        {
            Policy = "planner";
            Steps = 0;
        }

        public static Dictionary<string, int> DefaultShortcuts()
        {
            var map = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            map["start_auto"] = (int)Keys.F5;
            map["stop_all"] = (int)Keys.F6;
            map["send_order"] = (int)Keys.F7;
            map["emergency"] = (int)Keys.F12;
            map["toggle_dry"] = (int)Keys.F8;
            return map;
        }

        public static string[] Actions()
        {
            return new string[] { "start_auto", "stop_all", "send_order", "emergency", "toggle_dry" };
        }

        public static string ActionLabel(string action)
        {
            switch (action)
            {
                case "start_auto": return "Start / resume the autonomous loop";
                case "stop_all": return "Stop the loop";
                case "send_order": return "Send the order in the box";
                case "emergency": return "Emergency stop (all instances)";
                case "toggle_dry": return "Toggle dry run";
                default: return action;
            }
        }

        public static string PathFor(string baseDir)
        {
            return System.IO.Path.Combine(baseDir, "ai_settings.ini");
        }

        public static AiSettings Load(string baseDir)
        {
            var s = new AiSettings();
            string path = PathFor(baseDir);
            try
            {
                if (!File.Exists(path)) return s;
                foreach (string raw in File.ReadAllLines(path))
                {
                    string line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith("#")) continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string key = line.Substring(0, eq).Trim();
                    string val = line.Substring(eq + 1).Trim();
                    switch (key)
                    {
                        case "mode": s.Mode = val; break;
                        case "target_slot": s.TargetSlot = ParseInt(val, 0); break;
                        case "policy": s.Policy = val; break;
                        case "dry": s.DryRun = val == "1"; break;
                        case "steps": s.Steps = ParseInt(val, 200); break;
                        case "goals": s.Goals = Unescape(val); break;
                        case "order": s.Order = Unescape(val); break;
                        default:
                            if (key.StartsWith("shortcut."))
                                s.Shortcuts[key.Substring(9)] = ParseInt(val, (int)Keys.None);
                            break;
                    }
                }
            }
            catch { /* a corrupt settings file must not stop the app from starting */ }
            foreach (string a in Actions())
                if (!s.Shortcuts.ContainsKey(a)) s.Shortcuts[a] = DefaultShortcuts()[a];
            return s;
        }

        public void Save(string baseDir)
        {
            try
            {
                var sb = new StringBuilder();
                sb.AppendLine("# DofusFarm AI panel settings");
                sb.AppendLine("mode=" + Mode);
                sb.AppendLine("target_slot=" + TargetSlot.ToString());
                sb.AppendLine("policy=" + Policy);
                sb.AppendLine("dry=" + (DryRun ? "1" : "0"));
                sb.AppendLine("steps=" + Steps.ToString());
                sb.AppendLine("goals=" + Escape(Goals));
                sb.AppendLine("order=" + Escape(Order));
                foreach (KeyValuePair<string, int> kv in Shortcuts)
                    sb.AppendLine("shortcut." + kv.Key + "=" + kv.Value.ToString());
                File.WriteAllText(PathFor(baseDir), sb.ToString());
            }
            catch { }
        }

        private static int ParseInt(string v, int fallback)
        {
            int r;
            return int.TryParse(v, out r) ? r : fallback;
        }

        private static double ParseDouble(string v, double fallback)
        {
            double r;
            return double.TryParse(v, System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out r) ? r : fallback;
        }

        // Newlines are the one thing that cannot survive a line-based file.
        private static string Escape(string v)
        {
            if (v == null) return "";
            return v.Replace("\\", "\\\\").Replace("\r", "").Replace("\n", "\\n");
        }

        private static string Unescape(string v)
        {
            if (v == null) return "";
            var sb = new StringBuilder();
            for (int i = 0; i < v.Length; i++)
            {
                if (v[i] == '\\' && i + 1 < v.Length)
                {
                    char n = v[++i];
                    if (n == 'n') sb.Append('\n');
                    else if (n == '\\') sb.Append('\\');
                    else { sb.Append('\\'); sb.Append(n); }
                }
                else sb.Append(v[i]);
            }
            return sb.ToString();
        }
    }

    /// <summary>Rebind the five panel actions by pressing a key.</summary>
    public sealed class ShortcutEditorForm : Form
    {
        private readonly Dictionary<string, int> _map;
        private readonly ListView _list;

        public ShortcutEditorForm(Dictionary<string, int> shortcuts)
        {
            _map = new Dictionary<string, int>(shortcuts, StringComparer.OrdinalIgnoreCase);
            Text = "AI shortcuts";
            Width = 520;
            Height = 300;
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            BackColor = Color.FromArgb(20, 22, 28);
            ForeColor = Color.White;

            _list = new ListView();
            _list.View = View.Details;
            _list.FullRowSelect = true;
            _list.Columns.Add("Action", 330);
            _list.Columns.Add("Key", 120);
            _list.Location = new Point(14, 14);
            _list.Size = new Size(476, 190);
            _list.BackColor = Color.FromArgb(28, 30, 40);
            _list.ForeColor = Color.White;
            _list.KeyDown += OnKeyDown;
            Fill();

            var hint = new Label();
            hint.Text = "Select a row, then press the key you want.";
            hint.ForeColor = Color.FromArgb(148, 163, 184);
            hint.AutoSize = true;
            hint.Location = new Point(14, 210);
            Controls.Add(hint);

            var ok = new Button();
            ok.Text = "Save";
            ok.Location = new Point(320, 230);
            ok.Size = new Size(80, 28);
            ok.DialogResult = DialogResult.OK;
            Controls.Add(ok);

            var cancel = new Button();
            cancel.Text = "Cancel";
            cancel.Location = new Point(410, 230);
            cancel.Size = new Size(80, 28);
            cancel.DialogResult = DialogResult.Cancel;
            Controls.Add(cancel);

            AcceptButton = ok;
            CancelButton = cancel;
            Controls.Add(_list);
        }

        private void Fill()
        {
            _list.Items.Clear();
            foreach (string a in AiSettings.Actions())
            {
                int code;
                string label = _map.TryGetValue(a, out code) && code != 0
                    ? ((Keys)code).ToString() : "(unset)";
                var item = new ListViewItem(AiSettings.ActionLabel(a));
                item.SubItems.Add(label);
                item.Tag = a;
                _list.Items.Add(item);
            }
        }

        private void OnKeyDown(object sender, KeyEventArgs e)
        {
            if (_list.SelectedItems.Count == 0) return;
            if (e.KeyCode == Keys.Tab || e.KeyCode == Keys.Enter || e.KeyCode == Keys.Escape) return;
            e.SuppressKeyPress = true;
            string action = (string)_list.SelectedItems[0].Tag;
            _map[action] = (int)e.KeyData;
            Fill();
        }

        public Dictionary<string, int> Result
        {
            get { return _map; }
        }
    }

    /// <summary>The AI strip: mode, targets, budgets, log, and the child process.</summary>
    public sealed class AiPanel : Panel
    {
        private readonly string _baseDir;
        private readonly Action<string> _log;
        private readonly Func<int, string> _instanceBySlot;
        private readonly Func<int> _instanceCount;
        private readonly AiSettings _settings;

        private ComboBox _cmbMode;
        private ComboBox _cmbTarget;
        private CheckBox _chkDry;
        private TextBox _txtGoals;
        private RichTextBox _output;
        private Button _btnStart;
        private Button _btnStop;
        private Button _btnSend;
        private Button _btnKeys;
        private Label _lblState;

        private Process _proc;
        private readonly object _procLock = new object();

        public AiPanel(string baseDir, Func<int, string> instanceBySlot, Func<int> instanceCount, Action<string> log)
        {
            _baseDir = baseDir;
            _instanceBySlot = instanceBySlot;
            _instanceCount = instanceCount;
            _log = log;
            _settings = AiSettings.Load(baseDir);
            _settings.PinDecisions();   // not user decisions; see PinDecisions()

            Dock = DockStyle.Fill;
            BackColor = Color.FromArgb(20, 22, 28);
            Padding = new Padding(14, 6, 14, 6);

            BuildUi();
            ApplySettingsToUi();
        }

        /// <summary>The live settings object, so the Shortcuts page edits the same
        /// instance this panel reads on every keypress.</summary>
        public AiSettings Settings
        {
            get { return _settings; }
        }

        // -- construction -----------------------------------------------------

        private void BuildUi()
        {
            // Laid out with Dock rather than absolute coordinates: this panel is
            // now a page, so it has to look right at any window size instead of
            // only at the 1200 px the old bottom strip assumed.
            Padding = new Padding(18, 14, 18, 14);

            // ---- row 1: what to do ------------------------------------------
            var row1 = new Panel();
            row1.Dock = DockStyle.Top;
            row1.Height = 34;

            var lblMode = MakeLabel("Mode", 0, 4);
            row1.Controls.Add(lblMode);

            _cmbMode = MakeCombo(46, 0, 96, new object[] { "Off", "Order", "Auto" });
            _cmbMode.SelectedIndexChanged += delegate { OnModeChanged(); };
            row1.Controls.Add(_cmbMode);

            row1.Controls.Add(MakeLabel("Target", 154, 4));
            _cmbTarget = MakeCombo(204, 0, 170, new object[] { "All instances" });
            row1.Controls.Add(_cmbTarget);


            _chkDry = new CheckBox();
            _chkDry.Text = "Dry run (choose, never send)";
            _chkDry.ForeColor = Color.FromArgb(148, 163, 184);
            _chkDry.AutoSize = true;
            _chkDry.Location = new Point(528, 5);
            row1.Controls.Add(_chkDry);

            _btnStart = MakeButton("Start", Color.FromArgb(22, 101, 52), Color.FromArgb(21, 128, 61), 760, 0, 92);
            _btnStart.Click += delegate { StartAuto(false); };
            row1.Controls.Add(_btnStart);

            _btnStop = MakeButton("Stop", Color.FromArgb(127, 29, 29), Color.FromArgb(185, 28, 28), 860, 0, 80);
            _btnStop.Click += delegate { StopAuto("button"); };
            row1.Controls.Add(_btnStop);

            _btnSend = MakeButton("Send order", Color.FromArgb(37, 99, 235), Color.FromArgb(59, 130, 246), 948, 0, 108);
            _btnSend.Click += delegate { SendOrder(); };
            row1.Controls.Add(_btnSend);

            var quickJoin = MakeButton("Join", Color.FromArgb(30, 58, 138), Color.FromArgb(37, 99, 235), 1070, 0, 62);
            quickJoin.Click += delegate { Quick("fight.join", "{}"); };
            row1.Controls.Add(quickJoin);

            var quickPass = MakeButton("Pass", Color.FromArgb(146, 64, 14), Color.FromArgb(180, 83, 9), 1140, 0, 62);
            quickPass.Click += delegate { Quick("fight.pass", "{}"); };
            row1.Controls.Add(quickPass);

            var quickHalt = MakeButton("Halt", Color.FromArgb(51, 65, 85), Color.FromArgb(71, 85, 105), 1210, 0, 62);
            quickHalt.Click += delegate { EmergencyStop(); };
            row1.Controls.Add(quickHalt);

            // ---- row 2: goals -------------------------------------------------
            var row2 = new Panel();
            row2.Dock = DockStyle.Top;
            row2.Height = 150;
            row2.Padding = new Padding(0, 10, 0, 0);

            var lblGoals = new Label();
            lblGoals.Text = "Goals, one per line (Auto)  |  a single order (Order)";
            lblGoals.ForeColor = Color.FromArgb(148, 163, 184);
            lblGoals.AutoSize = true;
            lblGoals.Dock = DockStyle.Top;
            lblGoals.Height = 18;


            _txtGoals = new TextBox();
            _txtGoals.Multiline = true;
            _txtGoals.ScrollBars = ScrollBars.Vertical;
            _txtGoals.Dock = DockStyle.Fill;
            _txtGoals.BackColor = Color.FromArgb(28, 30, 40);
            _txtGoals.ForeColor = Color.White;
            _txtGoals.BorderStyle = BorderStyle.FixedSingle;
            _txtGoals.Font = new Font("Consolas", 9.5f);
            _txtGoals.TextChanged += delegate { _settings.Goals = _txtGoals.Text; };
            _txtGoals.KeyDown += OnGoalsKeyDown;
            row2.Controls.Add(_txtGoals);
            row2.Controls.Add(lblGoals);

            // ---- row 3: budgets ----------------------------------------------
            var row3 = new Panel();
            row3.Dock = DockStyle.Top;
            row3.Height = 62;
            row3.Padding = new Padding(0, 12, 0, 0);

            // No interval, no minutes: the loop is driven by the goals, not by a
            // clock. 0 steps = run until the goals are met or the user stops it.

            _btnKeys = MakeButton("Shortcuts...", Color.FromArgb(51, 65, 85), Color.FromArgb(71, 85, 105), 260, 22, 110);
            _btnKeys.Click += delegate { EditShortcuts(); };
            row3.Controls.Add(_btnKeys);

            // The agent script is injected on page load, so an edit to
            // mobile-disguise.js does nothing until the page reloads. Reloading the app
            // to pick up a script change is a heavy price for a one-line fix, and with
            // no auto-launch it also loses the session.
            var btnReload = MakeButton("Reload client", Color.FromArgb(30, 58, 138),
                                       Color.FromArgb(37, 99, 235), 380, 22, 118);
            btnReload.Click += delegate { ReloadClients(); };
            row3.Controls.Add(btnReload);

            _lblState = new Label();
            _lblState.Text = "idle";
            _lblState.ForeColor = Color.FromArgb(148, 163, 184);
            _lblState.AutoSize = true;
            _lblState.Location = new Point(556, 27);
            row3.Controls.Add(_lblState);

            var lblHint = new Label();
            lblHint.Text = "Guards live in agent/autopilot.py: no buying, no invented prices, no-progress stop.";
            lblHint.ForeColor = Color.FromArgb(100, 116, 139);
            lblHint.AutoSize = true;
            lblHint.Location = new Point(0, 27);
            lblHint.Visible = false;
            row3.Controls.Add(lblHint);

            // ---- row 4: live output (fills whatever is left) -------------------
            var row4 = new Panel();
            row4.Dock = DockStyle.Fill;
            row4.Padding = new Padding(0, 8, 0, 0);

            _output = new RichTextBox();
            _output.Dock = DockStyle.Fill;
            _output.BackColor = Color.FromArgb(16, 18, 24);
            _output.ForeColor = Color.FromArgb(203, 213, 225);
            _output.BorderStyle = BorderStyle.None;
            _output.Font = new Font("Consolas", 9f);
            _output.ReadOnly = true;
            _output.DetectUrls = false;
            row4.Controls.Add(_output);

            // Dock order is the reverse of the Controls collection order: the last
            // child added is laid out first. Add the filling box first and the
            // top rows after it, or every row overlaps the log.
            Controls.Add(row4);
            Controls.Add(row3);
            Controls.Add(row2);
            Controls.Add(row1);
        }

        private static Label MakeLabel(string text, int x, int y)
        {
            var l = new Label();
            l.Text = text;
            l.ForeColor = Color.FromArgb(148, 163, 184);
            l.AutoSize = true;
            l.Location = new Point(x, y);
            return l;
        }

        private void AddNumber(Control parent, string label, int x, int y, decimal min, decimal max, int decimals, out NumericUpDown box)
        {
            var lbl = new Label();
            lbl.Text = label;
            lbl.ForeColor = Color.FromArgb(148, 163, 184);
            lbl.AutoSize = true;
            lbl.Location = new Point(x, y - 18);
            parent.Controls.Add(lbl);

            box = new NumericUpDown();
            box.Location = new Point(x, y);
            box.Size = new Size(96, 24);
            box.Minimum = min;
            box.Maximum = max;
            box.DecimalPlaces = decimals;
            box.Increment = decimals > 0 ? 0.5m : 1m;
            box.BackColor = Color.FromArgb(28, 30, 40);
            box.ForeColor = Color.White;
            box.BorderStyle = BorderStyle.FixedSingle;
            parent.Controls.Add(box);
        }

        private static ComboBox MakeCombo(int x, int y, int w, object[] items)
        {
            var c = new ComboBox();
            c.Location = new Point(x, y);
            c.Size = new Size(w, 24);
            c.DropDownStyle = ComboBoxStyle.DropDownList;
            c.FlatStyle = FlatStyle.Flat;
            c.BackColor = Color.FromArgb(28, 30, 40);
            c.ForeColor = Color.White;
            c.Items.AddRange(items);
            c.SelectedIndex = 0;
            return c;
        }

        private static Button MakeButton(string text, Color back, Color hover, int x, int y, int w)
        {
            var b = new Button();
            b.Text = text;
            b.Location = new Point(x, y);
            b.Size = new Size(w, 28);
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.BackColor = back;
            b.ForeColor = Color.White;
            b.Font = new Font("Segoe UI", 8.5f, FontStyle.Bold);
            b.Cursor = Cursors.Hand;
            Color normal = back;
            b.MouseEnter += delegate { b.BackColor = hover; };
            b.MouseLeave += delegate { b.BackColor = normal; };
            return b;
        }

        // -- settings <-> ui --------------------------------------------------

        private void ApplySettingsToUi()
        {
            _cmbMode.SelectedIndex = _settings.Mode == "auto" ? 2 : (_settings.Mode == "order" ? 1 : 0);
            // The planner was reachable from Python (--policy planner) but not from
            // here, so the panel could only offer a one-tool rule and an encoder with
            // nothing to drive. "Nothing works" was, in part, that neither of these can
            // plan a quest: the rule maps a goal to a single tool, and the encoder picks
            // a tool for a step that no plan produced.
            _chkDry.Checked = _settings.DryRun;
            _txtGoals.Text = _settings.Goals;
            RefreshTargets();
        }

        /// <summary>
        /// Rebuild the target list from the instances that actually exist. The old
        /// combo was a fixed "Slot #1..#4", so it offered slots with no instance
        /// behind them and hid the fifth one.
        /// </summary>
        public void RefreshTargets()
        {
            if (_cmbTarget == null) return;
            int want = _settings.TargetSlot;
            int count = 0;
            try { count = _instanceCount != null ? _instanceCount() : 0; }
            catch { count = 0; }
            if (count < 0) count = 0;

            if (_cmbTarget.Items.Count != count + 1)
            {
                _cmbTarget.Items.Clear();
                _cmbTarget.Items.Add("All instances");
                for (int i = 1; i <= count; i++)
                {
                    string name = null;
                    try { name = _instanceBySlot != null ? _instanceBySlot(i) : null; }
                    catch { name = null; }
                    _cmbTarget.Items.Add(string.IsNullOrEmpty(name) ? ("Slot #" + i) : name);
                }
            }
            _cmbTarget.SelectedIndex = Math.Max(0, Math.Min(_cmbTarget.Items.Count - 1, want));
        }

        private static decimal Clamp(decimal v, decimal lo, decimal hi)
        {
            if (v < lo) return lo;
            if (v > hi) return hi;
            return v;
        }

        private void HarvestUiIntoSettings()
        {
            _settings.Mode = _cmbMode.SelectedIndex == 2 ? "auto" : (_cmbMode.SelectedIndex == 1 ? "order" : "off");
            _settings.TargetSlot = _cmbTarget.SelectedIndex;
            _settings.DryRun = _chkDry.Checked;
            _settings.Goals = _txtGoals.Text;
        }

        public void SaveSettings()
        {
            HarvestUiIntoSettings();
            _settings.Save(_baseDir);
        }

        private void OnModeChanged()
        {
            bool auto = _cmbMode.SelectedIndex == 2;
            _btnStart.Text = auto ? "Start" : "Run order";
            _lblState.Text = auto ? "auto" : (_cmbMode.SelectedIndex == 1 ? "order" : "idle");
        }

        // -- actions ----------------------------------------------------------

        public void StartAuto(bool force)
        {
            HarvestUiIntoSettings();
            if (IsRunning) { Append("[ai] already running; press Stop first"); return; }

            string goals = _settings.Goals.Trim();
            if (goals.Length == 0)
            {
                Append("[ai] no goals: type one per line first");
                return;
            }
            // steps 0 = unlimited; the loop stops on its own only when the goals
            // are done or the user presses Stop.
            Launch(goals, _settings.Steps, 0, 0, "auto");
        }

        public void SendOrder()
        {
            HarvestUiIntoSettings();
            if (IsRunning) { Append("[ai] loop is running; press Stop first"); return; }
            string order = _settings.Goals.Trim();
            if (order.Length == 0) { Append("[ai] type an order first"); return; }
            // A single order gets a short budget: it is one goal, not a session.
            Launch(order, 4, 0, 0, "order");
        }

        private void Launch(string goalsText, int steps, double interval, double minutes, string mode)
        {
            string python = ResolvePython();
            if (python == null)
            {
                Append("[ai] python runtime not found (looked for runtimes\\python\\python.exe)");
                return;
            }
            string script = Path.Combine(_baseDir, Path.Combine("dofus-network", "run_autopilot.py"));
            if (!File.Exists(script))
            {
                Append("[ai] missing " + script);
                return;
            }

            string work = Path.Combine(_baseDir, "dofus-network");
            string stateDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DofusFarm");
            try { Directory.CreateDirectory(stateDir); } catch { }
            // A stop file left over from a previous run makes the loop exit on its
            // first check -- it looks like "started" and does nothing. Clearing it
            // here is the difference between a no-op and a run.
            try
            {
                string staleStop = Path.Combine(stateDir, "AUTOPILOT_STOP");
                if (File.Exists(staleStop))
                {
                    File.Delete(staleStop);
                    Append("[ai] cleared the stop file left by the previous run");
                }
            }
            catch (Exception ex) { Append("[ai] could not clear the stop file: " + ex.Message); }

            string goalsFile = Path.Combine(stateDir, "ai_goals.txt");
            string stopFile = Path.Combine(stateDir, "AUTOPILOT_STOP");
            string logFile = Path.Combine(stateDir, "autopilot.jsonl");

            try
            {
                File.WriteAllText(goalsFile, goalsText);
                if (File.Exists(stopFile)) File.Delete(stopFile);
            }
            catch (Exception ex)
            {
                Append("[ai] cannot write " + goalsFile + ": " + ex.Message);
                return;
            }

            var sb = new StringBuilder();
            sb.Append("--goals-file \"").Append(goalsFile).Append("\"");
            sb.Append(" --steps ").Append(steps.ToString());
            sb.Append(" --interval ").Append(interval.ToString(System.Globalization.CultureInfo.InvariantCulture));
            sb.Append(" --policy ").Append(_settings.Policy);
            sb.Append(" --stop-file \"").Append(stopFile).Append("\"");
            sb.Append(" --log \"").Append(logFile).Append("\"");
            if (minutes > 0) sb.Append(" --minutes ").Append(minutes.ToString(System.Globalization.CultureInfo.InvariantCulture));
            if (_settings.DryRun) sb.Append(" --dry-run");

            // Use the verifier if one has been trained. It is optional by design:
            // without it the loop labels with rules, which is still a valid label.
            try
            {
                // Prefer the neural head; fall back to the linear baseline. Both are
                // optional: without either, the loop labels with rules.
                string models = Path.Combine(_baseDir, "dofus-network", "models");
                string head = Path.Combine(models, "verifier_head.onnx");
                string baseline = Path.Combine(models, "verifier_baseline.json");
                string verifier = File.Exists(head) ? head : (File.Exists(baseline) ? baseline : null);
                if (verifier != null) sb.Append(" --verifier-model \"").Append(verifier).Append("\"");
            }
            catch { }
            string instance = InstanceForTarget();
            if (!string.IsNullOrEmpty(instance)) sb.Append(" --instance \"").Append(instance).Append("\"");

            var psi = new ProcessStartInfo();
            psi.FileName = python;
            psi.Arguments = "\"" + script + "\" " + sb.ToString();
            psi.WorkingDirectory = work;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

            try
            {
                var p = new Process();
                p.StartInfo = psi;
                p.EnableRaisingEvents = true;
                p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) Append(e.Data); };
                p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) Append("! " + e.Data); };
                p.Exited += delegate { OnChildExited(); };
                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                lock (_procLock) { _proc = p; }
                Append("[ai] " + mode + " started (pid " + p.Id.ToString() + ", " + steps.ToString() + " steps, policy " + _settings.Policy + ")");
                if (_log != null) _log("[AI] " + mode + " loop started");
                SetRunningUi(true);
            }
            catch (Exception ex)
            {
                Append("[ai] failed to start: " + ex.Message);
            }
        }

        public void StopAuto(string why)
        {
            Process p;
            lock (_procLock) { p = _proc; }
            string stopFile = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "DofusFarm", "AUTOPILOT_STOP");
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(stopFile));
                File.WriteAllText(stopFile, why + " at " + DateTime.Now.ToString("HH:mm:ss"));
            }
            catch { }

            if (p == null)
            {
                Append("[ai] stop requested (" + why + "); no child process to stop");
                return;
            }
            Append("[ai] stop requested (" + why + "); waiting for the loop to finish the current step");
            try
            {
                if (!p.WaitForExit(4000))
                {
                    Append("[ai] child did not exit; killing it");
                    try { p.Kill(); } catch { }
                }
            }
            catch { }
        }

        public void EmergencyStop()
        {
            StopAuto("emergency");
            // Also tell every instance to stop acting, through the same bridge the
            // agent uses. This works even if the loop was never started.
            Quick("safe.stop", "{\"reason\":\"emergency stop from the AI panel\"}");
        }

        public void ToggleDryRun()
        {
            _chkDry.Checked = !_chkDry.Checked;
            Append("[ai] dry run " + (_chkDry.Checked ? "ON (choose and guard, never send)" : "off"));
        }

        /// <summary>Reload every open client page so it re-injects the agent script.</summary>
        public void ReloadClients()
        {
            try
            {
                _onReloadClients?.Invoke();
                Append("[ai] reloading the client page so the agent script is current");
            }
            catch (Exception ex)
            {
                Append("[ai] reload failed: " + ex.Message);
            }
        }

        /// <summary>Set by the host: reload every client page.</summary>
        public Action OnReloadClients
        {
            set { _onReloadClients = value; }
        }

        private Action _onReloadClients;

        public void EditShortcuts()
        {
            using (var dlg = new ShortcutEditorForm(_settings.Shortcuts))
            {
                if (dlg.ShowDialog(this) != DialogResult.OK) return;
                _settings.Shortcuts = dlg.Result;
                _settings.Save(_baseDir);
                Append("[ai] shortcuts saved");
            }
        }

        public bool HandleShortcut(Keys keyData)
        {
            foreach (KeyValuePair<string, int> kv in _settings.Shortcuts)
            {
                if (kv.Value == 0 || kv.Value != (int)keyData) continue;
                RunShortcut(kv.Key);
                return true;
            }
            return false;
        }

        /// <summary>The shortcuts, for the window that registers them system-wide.</summary>
        public Dictionary<string, int> ShortcutMap()
        {
            return new Dictionary<string, int>(_settings.Shortcuts);
        }

        /// <summary>Run one named shortcut. Shared by the focused key path and the
        /// global hotkey path so the two can never disagree about what F5 does.</summary>
        public void RunShortcut(string action)
        {
            switch (action)
            {
                case "start_auto": StartAuto(true); break;
                case "stop_all": StopAuto("shortcut"); break;
                case "send_order": SendOrder(); break;
                case "emergency": EmergencyStop(); break;
                case "toggle_dry": ToggleDryRun(); break;
            }
        }

        public string ShortcutSummary()
        {
            var sb = new StringBuilder();
            foreach (string a in AiSettings.Actions())
            {
                int code;
                if (!_settings.Shortcuts.TryGetValue(a, out code) || code == 0) continue;
                if (sb.Length > 0) sb.Append("  ");
                sb.Append(a.Replace("_", " ")).Append("=").Append(((Keys)code).ToString());
            }
            return sb.ToString();
        }

        // -- bridge ----------------------------------------------------------

        /// <summary>
        /// One tool call, straight to the directive bridge. Same path Python uses,
        /// so the call is schema-checked by the client and recorded like any other.
        /// </summary>
        /// <summary>The bridge's port, as the bridge published it.
        /// Hardcoding 8893 here meant that when the port was taken, every panel action
        /// failed with a connection error that looked like a client problem.</summary>
        private static string BridgeUrl()
        {
            int port = 8893;
            try
            {
                string path = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "DofusFarm", "bridge_port.txt");
                if (File.Exists(path))
                {
                    int parsed;
                    if (int.TryParse(File.ReadAllText(path).Trim(), out parsed) && parsed > 0)
                        port = parsed;
                }
            }
            catch { }
            return "http://127.0.0.1:" + port.ToString() + "/directive";
        }

        public void Quick(string tool, string argsJson)
        {
            string target = InstanceForTarget();
            string payload = "{\"id\":\"" + Guid.NewGuid().ToString("N") + "\",\"ts\":" +
                             ((long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds).ToString() +
                             ",\"directive\":\"" + tool.ToUpperInvariant().Replace('.', '_') + "\"" +
                             ",\"tool\":\"" + tool + "\",\"args\":" + argsJson + ",\"source\":\"panel\"";
            if (!string.IsNullOrEmpty(target)) payload += ",\"target_instance\":\"" + target + "\"";
            payload += "}";

            try
            {
                using (var wc = new WebClient())
                {
                    wc.Headers[HttpRequestHeader.ContentType] = "application/json";
                    wc.Encoding = Encoding.UTF8;
                    string res = wc.UploadString(BridgeUrl(), "POST", payload);
                    Append("[ai] " + tool + " -> " + Shorten(res, 220));
                }
            }
            catch (Exception ex)
            {
                Append("[ai] " + tool + " failed: " + ex.Message);
            }
        }

        private string InstanceForTarget()
        {
            int slot = _cmbTarget.SelectedIndex;
            if (slot <= 0 || _instanceBySlot == null) return null;
            try
            {
                string id = _instanceBySlot(slot);
                return id == "all" ? null : id;
            }
            catch { return null; }
        }

        // -- child process ----------------------------------------------------

        public bool IsRunning
        {
            get
            {
                Process p;
                lock (_procLock) { p = _proc; }
                if (p == null) return false;
                try { return !p.HasExited; } catch { return false; }
            }
        }

        private void OnChildExited()
        {
            Process p;
            lock (_procLock) { p = _proc; _proc = null; }
            try
            {
                if (p != null)
                {
                    int code = p.ExitCode;
                    Append("[ai] loop finished (exit " + code.ToString() + ")");
                    // The machine is now idle and the run just produced data: this is
                    // the only moment training is free. It writes a *candidate*; it
                    // never replaces the model that was just used.
                    TryIdleTraining();
                    if (_log != null) _log("[AI] loop finished (exit " + code.ToString() + ")");
                }
            }
            catch { }
            try
            {
                BeginInvoke((MethodInvoker)delegate { SetRunningUi(false); });
            }
            catch { }
        }

        /// <summary>
        /// One line the shell can show on every page: what the loop is doing right
        /// now. Reads the step log rather than the process, because the log is the
        /// thing that actually advances -- a live process with a stalled loop and
        /// a dead process look identical from the outside.
        /// </summary>
        public string LiveStatusText()
        {
            return FormatStatus(IsRunning, ReadLastStepLine());
        }

        /// <summary>Pure formatting, so it can be checked without a window.</summary>
        public static string FormatStatus(bool running, string lastLine)
        {
            int index = -1;
            string tool = null, verdict = null, source = null;
            if (!string.IsNullOrEmpty(lastLine))
            {
                Match mi = Regex.Match(lastLine, "\"index\"\\s*:\\s*(\\d+)");
                if (mi.Success) int.TryParse(mi.Groups[1].Value, out index);
                Match mt = Regex.Match(lastLine, "\"tool\"\\s*:\\s*\"([^\"]*)\"");
                if (mt.Success) tool = mt.Groups[1].Value;
                Match mv = Regex.Match(lastLine, "\"verdict\"\\s*:\\s*\"([^\"]*)\"");
                if (mv.Success) verdict = mv.Groups[1].Value;
                Match ms = Regex.Match(lastLine, "\"source\"\\s*:\\s*\"([^\"]*)\"");
                if (ms.Success) source = ms.Groups[1].Value;
            }

            if (!running && index < 0) return "AI idle";
            if (!running) return string.Format("AI stopped - last step {0}", index + 1);
            if (index < 0) return "AI running - waiting for the first step";
            return string.Format("AI running - step {0} - {1} {2}{3}",
                index + 1,
                string.IsNullOrEmpty(tool) ? "?" : tool,
                string.IsNullOrEmpty(verdict) ? "" : verdict,
                string.IsNullOrEmpty(source) ? "" : " (" + source + ")");
        }

        /// <summary>True while the child process is alive; the shell colours the pill with it.</summary>
        public bool RunningNow
        {
            get { return IsRunning; }
        }

        public static string ReadLastStepLine()
        {
            try
            {
                string path = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "DofusFarm", "autopilot.jsonl");
                if (!File.Exists(path)) return null;

                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    // Tail the last 8 KB: a step row is a few hundred bytes, so this
                    // is enough for the last line without reading a 100 MB log.
                    long len = fs.Length;
                    long start = Math.Max(0, len - 8192);
                    fs.Seek(start, SeekOrigin.Begin);
                    var buf = new byte[len - start];
                    int read = fs.Read(buf, 0, buf.Length);
                    string text = Encoding.UTF8.GetString(buf, 0, read);
                    string[] lines = text.Split(new char[] { '\n' }, StringSplitOptions.RemoveEmptyEntries);
                    for (int i = lines.Length - 1; i >= 0; i--)
                    {
                        string line = lines[i].Trim();
                        if (line.Length > 0 && line.StartsWith("{")) return line;
                    }
                }
            }
            catch { }
            return null;
        }

        /// <summary>
        /// Fire-and-forget the idle trainer. It refuses on its own if a loop holds
        /// the lock or a client bridge is answering, so this stays a hint.
        /// </summary>
        private void TryIdleTraining()
        {
            try
            {
                string python = ResolvePython();
                string script = Path.Combine(_baseDir, Path.Combine("dofus-network", "ai", "idle_trainer.py"));
                if (python == null || !File.Exists(script)) return;

                var psi = new ProcessStartInfo();
                psi.FileName = python;
                psi.Arguments = "\"" + script + "\" --once --max-seconds 90";
                psi.WorkingDirectory = Path.Combine(_baseDir, "dofus-network");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                var proc = new Process();
                proc.StartInfo = psi;
                proc.OutputDataReceived += delegate(object s2, DataReceivedEventArgs e)
                { if (e.Data != null) Append("[train] " + e.Data); };
                proc.ErrorDataReceived += delegate(object s2, DataReceivedEventArgs e)
                { if (e.Data != null) Append("[train!] " + e.Data); };
                proc.Start();
                proc.BeginOutputReadLine();
                proc.BeginErrorReadLine();
                Append("[ai] idle trainer started (candidate only)");
            }
            catch (Exception ex) { Append("[ai] idle trainer not started: " + ex.Message); }
        }

        private void SetRunningUi(bool running)
        {
            _btnStart.Enabled = !running;
            _btnSend.Enabled = !running;
            _lblState.Text = running ? "running" : (_cmbMode.SelectedIndex == 2 ? "auto" : "idle");
            _lblState.ForeColor = running ? Color.FromArgb(74, 222, 128) : Color.FromArgb(148, 163, 184);
        }

        private string ResolvePython()
        {
            string[] candidates = new string[]
            {
                Path.Combine(_baseDir, @"runtimes\python\python.exe"),
                Path.Combine(_baseDir, @"python\python.exe"),
                Path.Combine(_baseDir, @"runtimes\python\python3.exe")
            };
            foreach (string c in candidates) if (File.Exists(c)) return c;
            return null;
        }

        // -- output -----------------------------------------------------------

        private void OnGoalsKeyDown(object sender, KeyEventArgs e)
        {
            // Enter inserts a newline in Auto mode (one goal per line); in Order
            // mode it sends, because there is only ever one order.
            if (e.KeyCode == Keys.Enter && _cmbMode.SelectedIndex == 1 && !e.Shift)
            {
                e.SuppressKeyPress = true;
                SendOrder();
            }
        }

        public void Append(string line)
        {
            if (line == null) return;
            if (InvokeRequired)
            {
                try { BeginInvoke((MethodInvoker)delegate { Append(line); }); } catch { }
                return;
            }
            _output.AppendText(DateTime.Now.ToString("HH:mm:ss") + " " + line + Environment.NewLine);
            if (_output.TextLength > 24000) _output.Text = _output.Text.Substring(_output.TextLength - 16000);
            _output.SelectionStart = _output.TextLength;
            _output.ScrollToCaret();

            // Mirror to the host log as well.
            //
            // This text box is capped at 24,000 characters and disappears when the panel
            // closes, so anything written only here is gone exactly when it is most
            // needed. Two runs ended with "[AI] loop finished (exit -1)" and no
            // explanation, because the Python child's stdout — and any traceback on its
            // stderr, which arrives here prefixed with "! " — was written to this control
            // and nowhere else. A crash that leaves no trace is worse than the crash.
            try
            {
                if (_log != null) _log("[ai] " + line);
            }
            catch { }
        }

        private static string Shorten(string s, int max)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Length <= max ? s : s.Substring(0, max) + "...";
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                try { StopAuto("window closed"); } catch { }
            }
            base.Dispose(disposing);
        }
    }
}
