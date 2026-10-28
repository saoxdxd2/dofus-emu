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
// output. Every guard that matters (schema, no-progress, recovery) lives in
// agent/autopilot.py, where a test can see it.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using DofusFarm.Engine;

namespace DofusFarm.UI
{
    /// <summary>Persisted AI panel state. Plain key=value so no JSON dependency.</summary>
    public sealed class AiSettings
    {
        public string Mode = "off";          // off | order | auto
        public int TargetSlot = 0;           // 0 = all instances (kept for compatibility)
        // 1-based instance slots the panel may drive. Empty = every instance.
        // A single slot used to be the only choice, so one loop could only ever
        // watch one client while four were open.
        public List<int> TargetSlots = new List<int>();
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

        public static void FillShortcutList(ListView list, Dictionary<string, int> bindings)
        {
            list.Items.Clear();
            foreach (string action in Actions())
            {
                int code;
                string label = bindings.TryGetValue(action, out code) && code != 0
                    ? ((Keys)code).ToString() : "(unset)";
                var item = new ListViewItem(ActionLabel(action));
                item.SubItems.Add(label);
                item.Tag = action;
                list.Items.Add(item);
            }
        }

        public static bool BlockUnbindableKey(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Tab || e.KeyCode == Keys.Enter || e.KeyCode == Keys.Escape) return true;
            e.SuppressKeyPress = true;
            return false;
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
                    string key, val;
                    if (!Ini.TryKeyValue(raw, out key, out val, skipSemicolon: false)) continue;
                    switch (key)
                    {
                        case "mode": s.Mode = val; break;
                        case "target_slot": s.TargetSlot = ParseInt(val, 0); break;
                        case "target_slots":
                            s.TargetSlots = new List<int>();
                            foreach (string part in val.Split(','))
                            {
                                int slot = ParseInt(part.Trim(), 0);
                                if (slot >= 1 && !s.TargetSlots.Contains(slot)) s.TargetSlots.Add(slot);
                            }
                            break;
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
                // target_slot stays the first selection (0 = all) so an older build
                // reading this file still points at a real instance.
                TargetSlot = TargetSlots != null && TargetSlots.Count > 0 ? TargetSlots[0] : 0;
                sb.AppendLine("target_slot=" + TargetSlot.ToString());
                sb.AppendLine("target_slots=" + string.Join(",",
                    (TargetSlots ?? new List<int>()).ConvertAll(delegate(int v) { return v.ToString(); }).ToArray()));
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
            AiSettings.FillShortcutList(_list, _map);
        }

        private void OnKeyDown(object sender, KeyEventArgs e)
        {
            if (_list.SelectedItems.Count == 0) return;
            if (AiSettings.BlockUnbindableKey(e)) return;
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
        // Multi-select: a ComboBox can name exactly one instance, so "All instances"
        // was the only way to address more than one and it silently meant "whichever
        // window the bridge happens to pick first". A button that opens a checklist
        // gives every combination, and the label states what will actually be driven.
        private Button _btnTargets;
        private CheckBox _chkDry;
        private TextBox _txtGoals;
        private RichTextBox _output;
        private Button _btnStart;
        private Button _btnStop;
        private Button _btnSend;
        private Button _btnKeys;
        private Label _lblState;

        private readonly List<Process> _procs = new List<Process>();
        // Which instance each child drives. Without this the panel knows only "a
        // loop is running", so the in-game badge, the shell pill and Stop all
        // described the whole farm: pressing AI in one window turned it on (or off)
        // for every client, and one client's step count was shown on all of them.
        private readonly Dictionary<Process, string> _instanceByProc = new Dictionary<Process, string>();
        private readonly object _procLock = new object();

        // One brain daemon for every instance. The old path spawned one Python
        // child per selected instance, each with its own interpreter, numpy,
        // ONNX Runtime and knowledge cache. This is the single-process control
        // plane on 127.0.0.1:8895; the per-instance child path remains below as
        // a fallback if the daemon cannot start.
        private Process _brain;
        private readonly object _brainLock = new object();
        private readonly HashSet<string> _brainRunning = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private DateTime _brainStatusAt = DateTime.MinValue;
        private bool _brainHadInstances;
        private const int BrainPort = 8895;

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
            _btnTargets = MakeButton("Targets: All instances", Color.FromArgb(40, 44, 58),
                                     Color.FromArgb(55, 62, 80), 204, 0, 300);
            _btnTargets.Click += delegate { OpenTargetsDialog(); };
            row1.Controls.Add(_btnTargets);


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
            lblHint.Text = "All 25 tools enabled; schema, no-progress and recovery guards live in agent/autopilot.py.";
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
            return UiKit.Button(text, x, y, w, back, null, hover, 28);
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
        /// Rebuild the target label from the instances that actually exist. The old
        /// combo was a fixed "Slot #1..#4", so it offered slots with no instance
        /// behind them and hid the fifth one.
        /// </summary>
        public void RefreshTargets()
        {
            if (_btnTargets == null) return;
            UpdateTargetsLabel();
        }

        private int InstanceCount()
        {
            try { int n = _instanceCount != null ? _instanceCount() : 0; return n < 0 ? 0 : n; }
            catch { return 0; }
        }

        /// <summary>The checked slots, ascending. An empty list means every instance.</summary>
        private List<int> SelectedSlots()
        {
            var chosen = new List<int>();
            if (_settings.TargetSlots == null) return chosen;
            int count = InstanceCount();
            foreach (int slot in _settings.TargetSlots)
                if (slot >= 1 && (count == 0 || slot <= count) && !chosen.Contains(slot)) chosen.Add(slot);
            chosen.Sort();
            return chosen;
        }

        private string SlotName(int slot)
        {
            string name = null;
            try { name = _instanceBySlot != null ? _instanceBySlot(slot) : null; }
            catch { name = null; }
            return string.IsNullOrEmpty(name) ? ("Slot #" + slot) : name;
        }

        private void UpdateTargetsLabel()
        {
            List<int> chosen = SelectedSlots();
            var parts = new List<string>();
            foreach (int slot in chosen) parts.Add("#" + slot.ToString());
            _btnTargets.Text = "Targets: " + (chosen.Count == 0 ? "All instances" : string.Join(" ", parts.ToArray()))
                             + " (" + InstanceCount().ToString() + " configured)";
        }

        /// <summary>
        /// Comma-separated instance ids for a directive, or "all". "all" is resolved
        /// by the bridge against the windows that are actually open, which is the
        /// only honest answer: the panel cannot know which instances were launched.
        /// </summary>
        private string TargetsForDirective()
        {
            List<int> chosen = SelectedSlots();
            if (chosen.Count == 0) return "all";
            var ids = new List<string>();
            foreach (int slot in chosen)
            {
                string id = SlotName(slot);
                if (!string.IsNullOrEmpty(id) && !id.StartsWith("Slot #")) ids.Add(id);
            }
            return ids.Count == 0 ? "all" : string.Join(",", ids.ToArray());
        }

        /// <summary>Instance ids for the loop: one autopilot child each.</summary>
        private List<string> TargetsForLoop()
        {
            var ids = new List<string>();
            List<int> chosen = SelectedSlots();
            if (chosen.Count == 0)
            {
                int count = InstanceCount();
                for (int i = 1; i <= count; i++)
                {
                    string id = SlotName(i);
                    if (!string.IsNullOrEmpty(id) && !id.StartsWith("Slot #") && !ids.Contains(id)) ids.Add(id);
                }
                return ids;
            }
            foreach (int slot in chosen)
            {
                string id = SlotName(slot);
                if (!string.IsNullOrEmpty(id) && !id.StartsWith("Slot #")) ids.Add(id);
            }
            return ids;
        }

        private void OpenTargetsDialog()
        {
            int count = InstanceCount();
            var items = new List<string>();
            items.Add("All instances");
            for (int i = 1; i <= count; i++) items.Add("#" + i.ToString() + "  " + SlotName(i));

            var dlg = new Form();
            dlg.Text = "Which instances does the AI drive?";
            dlg.FormBorderStyle = FormBorderStyle.FixedDialog;
            dlg.StartPosition = FormStartPosition.CenterParent;
            dlg.MinimizeBox = false;
            dlg.MaximizeBox = false;
            dlg.ClientSize = new Size(320, 60 + 22 * Math.Max(1, items.Count));

            var list = new CheckedListBox();
            list.Location = new Point(10, 10);
            list.Size = new Size(300, 30 + 22 * Math.Max(1, items.Count));
            list.Items.AddRange(items.ToArray());

            List<int> chosen = SelectedSlots();
            if (chosen.Count == 0) list.SetItemChecked(0, true);
            else foreach (int slot in chosen) list.SetItemChecked(slot, true);

            var ok = new Button();
            ok.Text = "OK";
            ok.DialogResult = DialogResult.OK;
            ok.Location = new Point(150, list.Bottom + 8);
            var cancel = new Button();
            cancel.Text = "Cancel";
            cancel.DialogResult = DialogResult.Cancel;
            cancel.Location = new Point(236, list.Bottom + 8);
            dlg.Controls.Add(list);
            dlg.Controls.Add(ok);
            dlg.Controls.Add(cancel);
            dlg.AcceptButton = ok;
            dlg.CancelButton = cancel;

            // "All instances" is exclusive in BOTH directions. Only one direction was
            // wired, and the live picker then showed "All instances + #1" at once; the
            // read below honoured the "All" check and dropped the operator's selection
            // without a word.
            list.ItemCheck += delegate(object s, ItemCheckEventArgs e)
            {
                if (e.NewValue != CheckState.Checked) return;
                var box = (CheckedListBox)s;
                if (e.Index == 0)
                {
                    for (int i = 1; i < box.Items.Count; i++)
                        if (box.GetItemChecked(i)) box.SetItemChecked(i, false);
                    return;
                }
                if (box.GetItemChecked(0)) box.SetItemChecked(0, false);
            };

            if (dlg.ShowDialog(this) != DialogResult.OK) return;

            // Explicit rows win over a stale "All instances" check: the list is what
            // was asked for, and "all" is only the answer when nothing was picked.
            var next = new List<int>();
            for (int i = 1; i < list.Items.Count; i++)
                if (list.GetItemChecked(i)) next.Add(i);
            _settings.TargetSlots = next;
            UpdateTargetsLabel();
            SaveSettings();
            // A selection made while a loop runs changes what Start may do, and the
            // button's enable state is derived from exactly that.
            SetRunningUi(RunningNow);
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
            // An explicit selection names instances, not "the farm". The refusal below
            // used to run first, so with #1 already driving, Start said "press Stop
            // first" and the picker could never bring up a second instance -- the only
            // remaining path was that window's in-world badge. Selected instances now
            // start on their own; a running one is reported, never restarted under it.
            List<int> picked = SelectedSlots();
            if (picked.Count > 0)
            {
                int started = 0;
                foreach (int slot in picked)
                {
                    string id = SlotName(slot);
                    if (string.IsNullOrEmpty(id) || id.StartsWith("Slot #"))
                    {
                        Append("[ai] target #" + slot.ToString() + " has no client window to drive");
                        continue;
                    }
                    if (IsRunningFor(id)) { Append("[ai] " + id + " is already running"); continue; }
                    StartInstance(id, force);
                    started++;
                }
                if (started == 0) Append("[ai] nothing started - no selected instance is idle");
                return;
            }
            if (IsRunning) { Append("[ai] already running; press Stop first"); return; }

            string goals = _settings.Goals.Trim();
            if (goals.Length == 0)
            {
                // Full Auto: no prompt. The planner follows the active quest
                // curriculum and falls back to a standing farm order only when
                // no quest can be planned.
                Append("[ai] no goals: starting full auto (planner: quests first, then farm)");
                Launch("auto", _settings.Steps, 0, 0, "auto", "planner");
                return;
            }
            // steps 0 = unlimited; the loop stops on its own only when the goals
            // are done or the user presses Stop.
            Launch(goals, _settings.Steps, 0, 0, "auto");
        }

        /// <summary>
        /// Start the loop for ONE instance -- the in-game badge of that client.
        /// StartAuto honours the panel's selection, and a badge belongs to a single
        /// window, so using the shared selection there made one window's button
        /// start loops for instances nobody asked it to touch.
        /// </summary>
        public void StartInstance(string instanceId, bool force)
        {
            HarvestUiIntoSettings();
            if (string.IsNullOrEmpty(instanceId))
            {
                Append("[ai] a badge asked to start without saying which instance");
                return;
            }
            if (IsRunningFor(instanceId)) { Append("[ai] " + instanceId + " is already running"); return; }
            string goals = _settings.Goals.Trim();
            if (goals.Length == 0)
            {
                Append("[ai] " + instanceId + ": no goals, full auto (planner: quests first, then farm)");
                Launch("auto", _settings.Steps, 0, 0, "auto", "planner", instanceId);
                return;
            }
            Launch(goals, _settings.Steps, 0, 0, "auto", null, instanceId);
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

        private void Launch(string goalsText, int steps, double interval, double minutes, string mode,
                            string policyOverride = null, string onlyInstance = null)
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
            sb.Append(" --policy ").Append(policyOverride ?? _settings.Policy);
            if (minutes > 0) sb.Append(" --minutes ").Append(minutes.ToString(System.Globalization.CultureInfo.InvariantCulture));
            if (_settings.DryRun) sb.Append(" --dry-run");
            if (!RuntimeSettings.Current.FightBoards) sb.Append(" --no-fight-boards");

            // Use the verifier if one has been trained. It is optional by design:
            // without it the loop labels with rules, which is still a valid label.
            string verifier = null;
            try
            {
                // Prefer the neural head; fall back to the linear baseline. Both are
                // optional: without either, the loop labels with rules.
                string models = Path.Combine(_baseDir, "dofus-network", "models");
                string head = Path.Combine(models, "verifier_head.onnx");
                string baseline = Path.Combine(models, "verifier_baseline.json");
                string contractPath = Path.Combine(models, "verifier_head.validation.json");
                // G6: a head is not trustworthy because the file exists. The shipped
                // head's 1.0 validation accuracy is circular (rule labels on the rule's
                // own features), so it can override a correct rule on an action without
                // adding signal. Only a contract measured on live or human labels, on
                // enough examples and above the rule baseline, unlocks the auto-load.
                // Without it the loop labels with rules; a CLI --verifier-model still
                // overrides this choice for an experiment.
                try
                {
                    if (File.Exists(contractPath))
                    {
                        string contract = File.ReadAllText(contractPath);
                        bool validated = Regex.IsMatch(contract,
                            "\"labels\"\\s*:\\s*\"(live|human)\"");
                        Match n = Regex.Match(contract, "\"n\"\\s*:\\s*(\\d+)");
                        bool enough = n.Success && int.Parse(n.Groups[1].Value) >= 60;
                        bool above = Regex.IsMatch(contract,
                            "\"above_baseline\"\\s*:\\s*true");
                        if (validated && enough && above)
                            verifier = File.Exists(head) ? head
                                     : (File.Exists(baseline) ? baseline : null);
                    }
                }
                catch { }
                if (verifier != null) sb.Append(" --verifier-model \"").Append(verifier).Append("\"");
            }
            catch { }

            // One loop child per selected instance. They share the goals file and
            // the stop file, so Stop ends all of them at once, but each writes its
            // own log: two loops appending to one JSONL interleave rows from two
            // characters and the run becomes unreadable. With a single target the
            // filename stays as it was, because the report tools look for it.
            List<string> targets = string.IsNullOrEmpty(onlyInstance)
                ? TargetsForLoop()
                : new List<string> { onlyInstance };
            if (targets.Count == 0) targets.Add(null);
            bool shared = targets.Count == 1 && targets[0] == null;

            // Default path: one daemon, one model copy, one control plane. If it
            // cannot start (missing script, port held, import failure), the old
            // per-instance child loop below still runs.
            if (BrainStart(targets, goalsText, steps, interval, minutes, mode,
                           policyOverride, verifier))
            {
                if (_log != null) _log("[AI] " + mode + " loop started (brain daemon)");
                SetRunningUi(true);
                return;
            }

            try
            {
                foreach (string instance in targets)
                {
                    string childLog = shared || instance == null
                        ? logFile
                        : Path.Combine(stateDir, "autopilot." + instance + ".jsonl");
                    string childStop = StopFilePath(instance);
                    // A stop file left from a previous per-instance stop would make
                    // this child exit on its first check, looking like it started.
                    try { if (File.Exists(childStop)) File.Delete(childStop); } catch { }
                    string args = sb.ToString()
                        + " --stop-file \"" + childStop + "\""
                        + " --log \"" + childLog + "\""
                        + (string.IsNullOrEmpty(instance) ? "" : " --instance \"" + instance + "\"");

                    var psi = new ProcessStartInfo();
                    psi.FileName = python;
                    psi.Arguments = "\"" + script + "\" " + args;
                    psi.WorkingDirectory = work;
                    psi.UseShellExecute = false;
                    psi.CreateNoWindow = true;
                    psi.RedirectStandardOutput = true;
                    psi.RedirectStandardError = true;
                    psi.StandardOutputEncoding = Encoding.UTF8;
                    psi.StandardErrorEncoding = Encoding.UTF8;
                    psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
                    psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

                    var p = new Process();
                    p.StartInfo = psi;
                    p.EnableRaisingEvents = true;
                    p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
                    {
                        if (e.Data == null) return;
                        Append(string.IsNullOrEmpty(instance) ? e.Data
                              : ("[" + instance + "] " + e.Data));
                    };
                    p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
                    {
                        if (e.Data == null) return;
                        Append(string.IsNullOrEmpty(instance) ? "! " + e.Data
                              : ("[" + instance + "] ! " + e.Data));
                    };
                    p.Exited += delegate { OnChildExited(p); };
                    p.Start();
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                    lock (_procLock)
                    {
                        _procs.Add(p);
                        _instanceByProc[p] = instance;
                    }
                    Append("[ai] " + mode + " started (" + (instance ?? "default instance")
                          + ", pid " + p.Id.ToString() + ", " + steps.ToString() + " steps, policy "
                          + (policyOverride ?? _settings.Policy) + ")");
                }
                if (_log != null) _log("[AI] " + mode + " loop started");
                SetRunningUi(true);
            }
            catch (Exception ex)
            {
                Append("[ai] failed to start: " + ex.Message);
            }
        }

        /// <summary>
        /// The file one loop watches. The unaddressed child keeps the historical
        /// shared path (external tooling and the sentinel read it), and a named
        /// instance gets its own -- otherwise "stop this one" would have to write
        /// the file every other loop is watching.
        /// </summary>
        private static string StopFilePath(string instance)
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "DofusFarm");
            return string.IsNullOrEmpty(instance)
                ? Path.Combine(dir, "AUTOPILOT_STOP")
                : Path.Combine(dir, "AUTOPILOT_STOP." + instance);
        }

        /// <summary>The loop child that drives this instance, or null.</summary>
        public string RunningInstanceOf(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return null;
            if (BrainIsRunning(instanceId)) return instanceId;
            lock (_procLock)
            {
                foreach (KeyValuePair<Process, string> kv in _instanceByProc)
                {
                    if (!string.Equals(kv.Value, instanceId, StringComparison.OrdinalIgnoreCase)) continue;
                    try { if (!kv.Key.HasExited) return kv.Value; } catch { }
                }
            }
            return null;
        }

        /// <summary>True while the loop that drives THIS instance is alive.</summary>
        public bool IsRunningFor(string instanceId)
        {
            return RunningInstanceOf(instanceId) != null;
        }

        /// <summary>Every instance with a live loop, for the per-window badge.</summary>
        public List<string> RunningInstances()
        {
            var ids = new List<string>();
            RefreshBrainStatus();
            lock (_brainLock)
            {
                foreach (string id in _brainRunning)
                    if (!ids.Contains(id)) ids.Add(id);
            }
            lock (_procLock)
            {
                foreach (KeyValuePair<Process, string> kv in _instanceByProc)
                {
                    if (string.IsNullOrEmpty(kv.Value)) continue;
                    try { if (!kv.Key.HasExited && !ids.Contains(kv.Value)) ids.Add(kv.Value); } catch { }
                }
            }
            return ids;
        }

        /// <summary>Status of one instance, read from ITS OWN log.</summary>
        public string LiveStatusFor(string instanceId)
        {
            string line = ReadLastStepLine(StepLogFor(instanceId));
            return FormatStatus(IsRunningFor(instanceId), line);
        }

        /// <summary>The log one instance's loop writes: its own file when it has one.</summary>
        public static string StepLogFor(string instanceId)
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "DofusFarm");
            if (string.IsNullOrEmpty(instanceId)) return Path.Combine(dir, "autopilot.jsonl");
            string own = Path.Combine(dir, "autopilot." + instanceId + ".jsonl");
            return File.Exists(own) ? own : Path.Combine(dir, "autopilot.jsonl");
        }

        /// <summary>
        /// Stop one instance's loop. Nothing else is touched: no shared stop file is
        /// written, so the other clients keep playing.
        /// </summary>
        public void StopInstance(string instanceId, string why)
        {
            if (BrainIsRunning(instanceId) && BrainStopOne(instanceId, why))
            {
                SetRunningUi(IsRunning);
                return;
            }
            Process target = null;
            lock (_procLock)
            {
                foreach (KeyValuePair<Process, string> kv in _instanceByProc)
                {
                    if (string.Equals(kv.Value, instanceId, StringComparison.OrdinalIgnoreCase))
                    {
                        target = kv.Key;
                        break;
                    }
                }
                if (target != null)
                {
                    _instanceByProc.Remove(target);
                    _procs.Remove(target);
                }
            }
            if (target == null)
            {
                Append("[ai] " + instanceId + " has no loop to stop (" + why + ")");
                return;
            }
            try { File.WriteAllText(StopFilePath(instanceId), why + " at " + DateTime.Now.ToString("HH:mm:ss")); }
            catch { }
            Append("[ai] " + instanceId + ": stop requested (" + why + "); waiting for its current step");
            try
            {
                if (!target.WaitForExit(4000))
                {
                    Append("[ai] " + instanceId + ": child did not exit; killing it");
                    try { target.Kill(); } catch { }
                }
            }
            catch { }
        }


        public void StopAuto(string why)
        {
            bool hadBrain = BrainAnyRunning();
            if (hadBrain) BrainStopAll(why);
            List<Process> children;
            Dictionary<Process, string> attributed;
            lock (_procLock)
            {
                children = new List<Process>(_procs);
                attributed = new Dictionary<Process, string>(_instanceByProc);
                _procs.Clear();
                _instanceByProc.Clear();
            }
            string stopFile = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "DofusFarm", "AUTOPILOT_STOP");
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(stopFile));
                File.WriteAllText(stopFile, why + " at " + DateTime.Now.ToString("HH:mm:ss"));
                // Each named child watches its OWN file, so stopping the farm has to
                // ask each of them; the shared one stays written for the sentinel and
                // for any external tool that watches it.
                foreach (Process p in children)
                {
                    string instance;
                    if (!attributed.TryGetValue(p, out instance) || string.IsNullOrEmpty(instance)) continue;
                    try { File.WriteAllText(StopFilePath(instance), why + " at " + DateTime.Now.ToString("HH:mm:ss")); }
                    catch { }
                }
            }
            catch { }

            if (children.Count == 0)
            {
                Append("[ai] stop requested (" + why + "); no child process to stop");
                SetRunningUi(BrainAnyRunning());
                return;
            }
            Append("[ai] stop requested (" + why + "); waiting for "
                  + children.Count.ToString() + " loop(s) to finish the current step");
            foreach (Process p in children)
            {
                try
                {
                    if (!p.WaitForExit(4000))
                    {
                        Append("[ai] a child did not exit; killing it");
                        try { p.Kill(); } catch { }
                    }
                }
                catch { }
            }
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
            string target = TargetsForDirective();
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

        // -- brain daemon -----------------------------------------------------
        //
        // One process, one copy of the models and knowledge, one control plane.
        // The per-instance child path below is the fallback, not the default.

        private static string BrainUrl(string path)
        {
            return "http://127.0.0.1:" + BrainPort.ToString() + path;
        }

        private static string JsonEscape(string value)
        {
            if (value == null) return "";
            var sb = new StringBuilder(value.Length + 8);
            foreach (char ch in value)
            {
                switch (ch)
                {
                    case '\\': sb.Append("\\\\"); break;
                    case '"': sb.Append("\\\""); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (ch < 0x20) sb.Append("\\u").Append(((int)ch).ToString("x4"));
                        else sb.Append(ch);
                        break;
                }
            }
            return sb.ToString();
        }

        private static string JsonQuote(string value)
        {
            return "\"" + JsonEscape(value) + "\"";
        }

        private static string BrainRequest(string method, string path, string json, int timeoutMs)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(BrainUrl(path));
                req.Method = method;
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                // Loopback must not be routed through a system HTTP proxy.
                req.Proxy = null;
                req.KeepAlive = false;
                if (json != null)
                {
                    byte[] data = Encoding.UTF8.GetBytes(json);
                    req.ContentType = "application/json";
                    req.ContentLength = data.Length;
                    using (Stream stream = req.GetRequestStream())
                        stream.Write(data, 0, data.Length);
                }
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                    return reader.ReadToEnd();
            }
            catch
            {
                return null;
            }
        }

        private bool BrainProcessAlive()
        {
            lock (_brainLock)
            {
                if (_brain == null) return false;
                try { return !_brain.HasExited; } catch { return false; }
            }
        }

        private bool EnsureBrain()
        {
            if (BrainProcessAlive()) return true;
            string python = ResolvePython();
            if (python == null)
            {
                Append("[ai] brain daemon: python runtime not found");
                return false;
            }
            string script = Path.Combine(_baseDir, Path.Combine("dofus-network", "run_brain.py"));
            if (!File.Exists(script))
            {
                Append("[ai] brain daemon: missing " + script);
                return false;
            }
            string work = Path.Combine(_baseDir, "dofus-network");
            string stateDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "DofusFarm");
            try { Directory.CreateDirectory(stateDir); } catch { }

            var psi = new ProcessStartInfo();
            psi.FileName = python;
            psi.Arguments = "\"" + script + "\" --port " + BrainPort.ToString()
                + " --brain --brain-log-dir \"" + stateDir + "\""
                + " --recorder-dir \"" + Path.Combine(work, "trajectories") + "\"";
            psi.WorkingDirectory = work;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

            var p = new Process();
            p.StartInfo = psi;
            p.EnableRaisingEvents = true;
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
            { if (e.Data != null) Append("[brain] " + e.Data); };
            p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
            { if (e.Data != null) Append("[brain!] " + e.Data); };
            p.Exited += delegate { OnBrainExited(p); };
            try
            {
                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                lock (_brainLock) _brain = p;
                Append("[ai] brain daemon started (pid " + p.Id.ToString() + ", port "
                      + BrainPort.ToString() + ")");
            }
            catch (Exception ex)
            {
                Append("[ai] brain daemon failed to start: " + ex.Message);
                return false;
            }

            // Wait for the control plane, not just the process. A Python process
            // that is still importing is not a daemon yet.
            for (int i = 0; i < 30; i++)
            {
                if (BrainRequest("GET", "/brain/health", null, 500) != null) return true;
                Thread.Sleep(150);
            }
            Append("[ai] brain daemon did not answer /brain/health on port " + BrainPort.ToString());
            return false;
        }

        private bool BrainStart(List<string> targets, string goalsText, int steps,
                                double interval, double minutes, string mode,
                                string policy, string verifier)
        {
            var ids = new List<string>();
            foreach (string target in targets)
                if (!string.IsNullOrEmpty(target)) ids.Add(target);
            if (ids.Count == 0) return false;
            if (!EnsureBrain()) return false;

            var goals = new List<string>();
            foreach (string line in (goalsText ?? "").Replace("\r", "").Split('\n'))
            {
                string trimmed = line.Trim();
                if (trimmed.Length > 0) goals.Add(trimmed);
            }
            if (goals.Count == 0) goals.Add("do all the quests");

            var json = new StringBuilder();
            json.Append("{\"instances\":[");
            for (int i = 0; i < ids.Count; i++)
            {
                if (i > 0) json.Append(',');
                json.Append(JsonQuote(ids[i]));
            }
            json.Append("],\"goals\":[");
            for (int i = 0; i < goals.Count; i++)
            {
                if (i > 0) json.Append(',');
                json.Append(JsonQuote(goals[i]));
            }
            json.Append("],\"steps\":").Append(steps.ToString());
            json.Append(",\"interval\":").Append(interval.ToString(System.Globalization.CultureInfo.InvariantCulture));
            json.Append(",\"minutes\":").Append(minutes.ToString(System.Globalization.CultureInfo.InvariantCulture));
            json.Append(",\"policy\":").Append(JsonQuote(policy ?? _settings.Policy));
            json.Append(",\"mode\":").Append(JsonQuote(mode ?? "auto"));
            json.Append(",\"dry_run\":").Append(_settings.DryRun ? "true" : "false");
            json.Append(",\"fight_boards\":").Append(RuntimeSettings.Current.FightBoards ? "true" : "false");
            json.Append(",\"brain\":\"auto\"");
            json.Append(",\"command_shadow\":\"off\"");
            json.Append(",\"verifier_model\":").Append(
                string.IsNullOrEmpty(verifier) ? "null" : JsonQuote(verifier));
            json.Append('}');

            string body = BrainRequest("POST", "/brain/start", json.ToString(), 15000);
            if (body == null || body.IndexOf("\"ok\":true", StringComparison.Ordinal) < 0)
            {
                Append("[ai] brain daemon refused the start request; falling back to per-instance children"
                      + (body == null ? "" : ": " + Shorten(body, 240)));
                return false;
            }
            _brainStatusAt = DateTime.MinValue;
            _brainHadInstances = true;
            RefreshBrainStatus();
            Append("[ai] " + mode + " started on the brain daemon ("
                  + ids.Count.ToString() + " instance(s), " + steps.ToString()
                  + " steps, policy " + (policy ?? _settings.Policy) + ")");
            return true;
        }

        private bool BrainStopOne(string instance, string why)
        {
            if (string.IsNullOrEmpty(instance)) return false;
            if (!BrainProcessAlive()) return false;
            string json = "{\"instance\":" + JsonQuote(instance)
                + ",\"why\":" + JsonQuote(why) + "}";
            string body = BrainRequest("POST", "/brain/stop", json, 6000);
            bool ok = body != null && body.IndexOf("\"ok\":true", StringComparison.Ordinal) >= 0;
            if (ok)
            {
                lock (_brainLock) _brainRunning.Remove(instance);
                Append("[ai] " + instance + ": stop requested (" + why + ") on the brain daemon");
            }
            return ok;
        }

        private bool BrainStopAll(string why)
        {
            if (!BrainProcessAlive()) return false;
            string json = "{\"all\":true,\"why\":" + JsonQuote(why) + "}";
            string body = BrainRequest("POST", "/brain/stop", json, 6000);
            bool ok = body != null && body.IndexOf("\"ok\":true", StringComparison.Ordinal) >= 0;
            if (ok)
            {
                lock (_brainLock) _brainRunning.Clear();
                _brainHadInstances = false;
                Append("[ai] stop requested (" + why + ") on the brain daemon");
            }
            return ok;
        }

        private void RefreshBrainStatus()
        {
            Process process;
            lock (_brainLock) process = _brain;
            if (process == null)
            {
                lock (_brainLock) _brainRunning.Clear();
                return;
            }
            bool exited;
            try { exited = process.HasExited; } catch { exited = true; }
            if (exited)
            {
                lock (_brainLock) _brainRunning.Clear();
                return;
            }
            if ((DateTime.UtcNow - _brainStatusAt).TotalMilliseconds < 1500) return;
            _brainStatusAt = DateTime.UtcNow;
            string body = BrainRequest("GET", "/brain/status", null, 1200);
            if (body == null) return;

            var active = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            Match list = Regex.Match(body, "\"active\"\\s*:\\s*\\[([^\\]]*)\\]");
            if (list.Success)
            {
                foreach (Match item in Regex.Matches(list.Groups[1].Value, "\"([^\"]+)\""))
                    active.Add(item.Groups[1].Value);
            }
            bool wasRunning = _brainHadInstances;
            lock (_brainLock)
            {
                _brainRunning.Clear();
                foreach (string id in active) _brainRunning.Add(id);
                _brainHadInstances = active.Count > 0;
            }
            if (wasRunning && active.Count == 0)
            {
                // The last instance finished on its own: same idle-training window
                // the child path gets from OnChildExited.
                TryIdleTraining();
                try { BeginInvoke((MethodInvoker)delegate { SetRunningUi(false); }); } catch { }
            }
        }

        private bool BrainAnyRunning()
        {
            RefreshBrainStatus();
            lock (_brainLock) return _brainRunning.Count > 0;
        }

        private bool BrainIsRunning(string instance)
        {
            if (string.IsNullOrEmpty(instance)) return false;
            RefreshBrainStatus();
            lock (_brainLock) return _brainRunning.Contains(instance);
        }

        private void OnBrainExited(Process p)
        {
            bool had;
            lock (_brainLock)
            {
                had = _brainRunning.Count > 0;
                _brainRunning.Clear();
                _brainHadInstances = false;
                if (_brain == p) _brain = null;
            }
            try
            {
                int code = p.ExitCode;
                Append("[ai] brain daemon exited (code " + code.ToString() + ")");
            }
            catch { }
            if (had) TryIdleTraining();
            try { BeginInvoke((MethodInvoker)delegate { SetRunningUi(false); }); } catch { }
        }

        private void StopBrainProcess()
        {
            Process process;
            lock (_brainLock)
            {
                process = _brain;
                _brain = null;
                _brainRunning.Clear();
                _brainHadInstances = false;
            }
            if (process == null) return;
            try
            {
                if (!process.HasExited) process.Kill();
            }
            catch { }
        }

        // -- child process ----------------------------------------------------

        public bool IsRunning
        {
            get
            {
                if (BrainAnyRunning()) return true;
                List<Process> children;
                lock (_procLock) { children = new List<Process>(_procs); }
                foreach (Process p in children)
                    try { if (!p.HasExited) return true; } catch { }
                return false;
            }
        }

        private void OnChildExited(Process p)
        {
            bool anyLeft;
            lock (_procLock)
            {
                if (p != null) { _procs.Remove(p); _instanceByProc.Remove(p); }
                anyLeft = _procs.Count > 0;
            }
            try
            {
                if (p != null)
                {
                    int code = p.ExitCode;
                    Append("[ai] loop finished (exit " + code.ToString() + ")");
                }
            }
            catch { }
            // Idle training and the UI flip belong to the LAST child, not the first:
            // with several loops running, the quickest one finishing would train on
            // half a run and show "idle" while the others were still playing.
            if (!anyLeft)
            {
                // The machine is now idle and the run just produced data: this is
                // the only moment training is free. It writes a *candidate*; it
                // never replaces the model that was just used.
                TryIdleTraining();
                if (_log != null) _log("[AI] loop finished");
                try
                {
                    BeginInvoke((MethodInvoker)delegate { SetRunningUi(false); });
                }
                catch { }
            }
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
            return ReadLastStepLine(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "DofusFarm", "autopilot.jsonl"));
        }

        public static string ReadLastStepLine(string path)
        {
            try
            {
                if (!File.Exists(path)) return null;

                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    // Tail the last 64 KB: a step row is a few hundred bytes, but the
                    // observation rows carry a whole snapshot, so a smaller window can
                    // land inside one row. Read until the buffer is really full -- a
                    // single FileStream.Read may return less than asked, and on a log
                    // being written that cut the tail short and the pill then said
                    // "waiting for the first step" about a loop that had taken 44.
                    long len = fs.Length;
                    long start = Math.Max(0, len - 65536);
                    fs.Seek(start, SeekOrigin.Begin);
                    var buf = new byte[len - start];
                    int got = 0;
                    while (got < buf.Length)
                    {
                        int read = fs.Read(buf, got, buf.Length - got);
                        if (read <= 0) break;
                        got += read;
                    }
                    string text = Encoding.UTF8.GetString(buf, 0, got);
                    string[] lines = text.Split(new char[] { '\n' }, StringSplitOptions.RemoveEmptyEntries);
                    // Newest first, and only a row that really is a step: a window
                    // opening mid-row yields a fragment that starts with '{' and has
                    // no index in it, which is exactly what the pill showed as "idle".
                    for (int i = lines.Length - 1; i >= 0; i--)
                    {
                        string line = lines[i].Trim();
                        if (line.Length == 0 || !line.StartsWith("{")) continue;
                        if (line.IndexOf("\"index\"") < 0) continue;
                        if (line.IndexOf("\"tool\"") < 0) continue;
                        return line;
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
            // Start went dark the moment ANY loop was running, which made the picker a
            // lie: with #1-#4 selected and #1-#2 driving, adding the two idle
            // instances was not refused with a reason -- the button was unclickable.
            // It stays enabled while an explicit selection exists, because StartAuto
            // can act on that; "all instances" keeps the old rule.
            _btnStart.Enabled = !running || SelectedSlots().Count > 0;
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
            //
            // Passed through unchanged: these lines already carry their own "[ai] "
            // prefix, and adding a second one produced "[ai] [ai] ..." in the log.
            try
            {
                if (_log != null) _log(line);
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
                try { StopBrainProcess(); } catch { }
            }
            base.Dispose(disposing);
        }
    }
}
