using System;

using System.Collections.Concurrent;

using System.Collections.Generic;

using System.ComponentModel;

using System.Diagnostics;

using System.Drawing;

using System.Drawing.Drawing2D;

using System.IO;

using System.Linq;

using System.Runtime.CompilerServices;

using System.Runtime.InteropServices;

using System.Text;

using System.Text.RegularExpressions;

using System.Threading;

using System.Threading.Tasks;

using System.Windows.Forms;

using DofusFarm.Engine;

using DofusFarm.Hardware;

using DofusFarm.Native;



namespace DofusFarm.UI

{

    public class MainForm : Form

    {

        internal static readonly Regex RxDigits = new Regex(@"(\d+)", RegexOptions.Compiled);

        private static readonly Regex RxTrailingDigits = new Regex(@"(\d+)$", RegexOptions.Compiled);

        private static readonly Regex RxLogPrefix = new Regex(@"^\[\d{2}:\d{2}:\d{2}\]\s*", RegexOptions.Compiled);



        // Pre-cached GDI+ brushes and pens to eliminate per-frame allocations in OnGridCellPainting

        private static readonly SolidBrush BrushPillRunBg = new SolidBrush(Color.FromArgb(16, 52, 42));

        private static readonly SolidBrush BrushPillStopBg = new SolidBrush(Color.FromArgb(30, 33, 44));

        private static readonly SolidBrush BrushBtnDefaultBg = new SolidBrush(Color.FromArgb(38, 42, 56));

        private static readonly SolidBrush BrushBtnStopBg = new SolidBrush(Color.FromArgb(153, 27, 27));

        private static readonly SolidBrush BrushBtnLaunchBg = new SolidBrush(Color.FromArgb(29, 78, 216));

        private static readonly SolidBrush BrushBtnEditCloneBg = new SolidBrush(Color.FromArgb(30, 41, 59));

        private static readonly SolidBrush BrushBtnDeleteBg = new SolidBrush(Color.FromArgb(45, 26, 34));



        private static readonly Pen PenBtnDefault = new Pen(Color.FromArgb(55, 62, 82), 1f);

        private static readonly Pen PenBtnStop = new Pen(Color.FromArgb(220, 38, 38), 1f);

        private static readonly Pen PenBtnLaunch = new Pen(Color.FromArgb(59, 130, 246), 1f);

        private static readonly Pen PenBtnEditClone = new Pen(Color.FromArgb(71, 85, 105), 1f);

        private static readonly Pen PenBtnDelete = new Pen(Color.FromArgb(127, 29, 29), 1f);



        private static readonly Color ColGreen = Color.FromArgb(52, 211, 153);

        private static readonly Color ColMuted = Color.FromArgb(148, 163, 184);

        private static readonly Color ColBlueText = Color.FromArgb(147, 197, 253);

        private static readonly Color ColAmberText = Color.FromArgb(251, 191, 36);

        private static readonly Color ColRedText = Color.FromArgb(248, 113, 113);

        private static readonly Font FontBtnBold = new Font("Segoe UI", 9f, FontStyle.Bold);

        private static readonly Font FontBtnReg = new Font("Segoe UI", 9f, FontStyle.Regular);



        internal static readonly int[] RamChoices = { 512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192, 12288, 16384 };

        internal static readonly int[] CoreChoices = { 0, 1, 2, 3, 4, 6, 8 };



        private FarmEngine _engine;

        private FarmWatchdog _watchdog;

        private PriorityGovernor _governor;

        private HardwareSpecs _specs;



        private IClientBackend _activeBackend;

        private EmulatorBackend _emulatorBackend;

        private WebView2Backend _webViewBackend;

        private AgentDirectiveBridge _agentBridge;



        private Panel _headerPanel;

        private Label _titleLabel;

        private Label _specsLabel;

        private Label _lblActiveBadge;

        private Label _lblVersionBadge;



        private Panel _toolbarPanel;

        private Button _btnLaunchFarm;

        private Button _btnStopAll;

        private Button _btnAddSlot;

        private Button _btnEditSelected;

        private Button _btnDeleteSelected;

        private Button _btnRelayout;

        private Button _btnCascade;

        private Button _btnProxies;



        private Label _lblBackend;



        private Label _lblGpu;

        private ComboBox _cmbGpu;

        private Label _lblRamBadge;

        private Label _lblEngineNotice;

        private ContextMenuStrip _rowMenu;

        private bool _suppressGpuEvent = false;

        private bool _keepInstancesOnExit = true;



        private DataGridView _grid;

        private Font _cellBadgeFont;

        private Font _customNameFont;



        private Panel _statusPanel;

        private Label _lblStatusText;

        private Button _btnFocus1;

        private Button _btnFocus2;

        private Button _btnFocus3;

        private Button _btnFocus4;



        private AiPanel _aiPanel;

        private Panel _navPanel;

        private Panel _pageHost;

        private Panel _pageInstances;

        private SettingsPage _pageSettings;
        private Label _aiLive;
        private System.Windows.Forms.Timer _aiLiveTimer;

        private ShortcutsPage _pageShortcuts;

        private LogsPage _pageLogs;

        private readonly List<Button> _navButtons = new List<Button>();

        private int _navIndex;



        private System.Windows.Forms.Timer _syncTimer;

        private string _lastStateSignature = "";

        private int _isSyncing = 0;

        private int _refreshPendingForce = 0;

        private int _shutdownStarted = 0;

        private int _shutdownComplete = 0;

        private static readonly Random Rng = new Random();



        private volatile bool _isClosing = false;

        private volatile bool _isFarmLaunching = false;

        private readonly ConcurrentDictionary<string, byte> _inFlightOps = new ConcurrentDictionary<string, byte>(StringComparer.OrdinalIgnoreCase);

        private readonly object _opsLock = new object();

        private readonly object _logFileLock = new object();

        private static readonly string LogFilePath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "DofusFarm.log");

        private StreamWriter _logWriter;

        private int _logWritesSinceFlush = 0;

        private volatile string _pendingStatusText;

        private int _statusTextPending = 0;



        [MethodImpl(MethodImplOptions.AggressiveInlining)]

        internal static int ParseFirstInt(object val, int fallback)

        {

            if (val == null) return fallback;

            string s = val.ToString();

            if (s.StartsWith("Auto", StringComparison.OrdinalIgnoreCase) || s.Contains("Shared")) return 0;

            Match m = RxDigits.Match(s);

            int parsed;

            return (m.Success && int.TryParse(m.Groups[1].Value, out parsed)) ? parsed : fallback;

        }



        [MethodImpl(MethodImplOptions.AggressiveInlining)]

        private bool TryBeginOp(string key) { return !string.IsNullOrEmpty(key) && _inFlightOps.TryAdd(key, 0); }



        [MethodImpl(MethodImplOptions.AggressiveInlining)]

        private void EndOp(string key) { byte b; if (!string.IsNullOrEmpty(key)) _inFlightOps.TryRemove(key, out b); }



        private void SafeUi(Action action, bool async)

        {

            if (IsDisposed || !IsHandleCreated) return;

            try { if (async) BeginInvoke(action); else Invoke(action); }

            catch (ObjectDisposedException) { }

            catch (InvalidOperationException) { }

        }



        private void SafeInvoke(Action action) { SafeUi(action, false); }

        private void SafeBeginInvoke(Action action) { SafeUi(action, true); }



        private Process _sentinelProcess;



        public MainForm()

        {

            InitializeComponent();

            InitializeEngine();

            TryStartSingletonSentinel();

        }



        private void TryStartSingletonSentinel()

        {

            ThreadPool.QueueUserWorkItem(delegate

            {

                try

                {

                    string baseDir = AppDomain.CurrentDomain.BaseDirectory;

                    string sentinelScript = Path.Combine(baseDir, @"dofus-network\networkhealth\sentinel.py");

                    if (!File.Exists(sentinelScript)) return;



                    string pyExe = FindPythonExecutable();

                    if (string.IsNullOrEmpty(pyExe)) return;



                    ProcessStartInfo psi = new ProcessStartInfo

                    {

                        FileName = pyExe,

                        Arguments = string.Format("\"{0}\"", sentinelScript),

                        WorkingDirectory = baseDir,

                        CreateNoWindow = true,

                        UseShellExecute = false,

                        WindowStyle = ProcessWindowStyle.Hidden

                    };

                    _sentinelProcess = Process.Start(psi);

                    Thread.Sleep(900);

                    if (_sentinelProcess != null && _sentinelProcess.HasExited)

                    {

                        if (IsLocalPortListening(8890))

                        {

                            try { _sentinelProcess.Dispose(); } catch { }

                            _sentinelProcess = null;

                            Log("🧠 Singleton AI Sentinel already running on port 8890 - reusing it.");

                        }

                        else Log("⚠ Singleton AI Sentinel exited immediately - check the dofus-network logs.");

                    }

                    else Log("🧠 Singleton AI Sentinel daemon auto-started (Port 8890).");

                }

                catch { }

            });

        }



        private static bool IsLocalPortListening(int port)

        {

            try

            {

                using (var c = new System.Net.Sockets.TcpClient())

                {

                    IAsyncResult ar = c.BeginConnect("127.0.0.1", port, null, null);

                    if (!ar.AsyncWaitHandle.WaitOne(300)) return false;

                    try { c.EndConnect(ar); return true; } catch { return false; }

                }

            }

            catch { return false; }

        }



        private static string JsonEscape(string s)

        {

            if (string.IsNullOrEmpty(s)) return "";

            var sb = new StringBuilder(s.Length + 16);

            foreach (char c in s)

            {

                switch (c)

                {

                    case '\\': sb.Append("\\\\"); break;

                    case '"': sb.Append("\\\""); break;

                    case '\b': sb.Append("\\b"); break;

                    case '\f': sb.Append("\\f"); break;

                    case '\n': sb.Append("\\n"); break;

                    case '\r': sb.Append("\\r"); break;

                    case '\t': sb.Append("\\t"); break;

                    default: if (c < 32) sb.AppendFormat("\\u{0:X4}", (int)c); else sb.Append(c); break;

                }

            }

            return sb.ToString();

        }



        private static string FindPythonExecutable()

        {

            string baseDir = AppDomain.CurrentDomain.BaseDirectory;

            string appPortable = Path.Combine(baseDir, @"runtimes\python\python.exe");

            if (File.Exists(appPortable)) return appPortable;

            appPortable = Path.Combine(baseDir, @"python\python.exe");

            if (File.Exists(appPortable)) return appPortable;



            string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";

            foreach (string p in pathEnv.Split(';'))

            {

                if (string.IsNullOrEmpty(p)) continue;

                try

                {

                    string candidate = Path.Combine(p.Trim(), "python.exe");

                    if (File.Exists(candidate)) return candidate;

                }

                catch { }

            }

            string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

            string pyPrograms = Path.Combine(localApp, @"Programs\Python");

            if (Directory.Exists(pyPrograms))

            {

                try

                {

                    foreach (string dir in Directory.GetDirectories(pyPrograms, "Python3*"))

                    {

                        string candidate = Path.Combine(dir, "python.exe");

                        if (File.Exists(candidate)) return candidate;

                    }

                }

                catch { }

            }

            return null;

        }



        private static ComboBox CreateCombo(int x, int y, int width, object[] items, int selectedIndex)

        {

            var cmb = new ComboBox

            {

                DropDownStyle = ComboBoxStyle.DropDownList,

                BackColor = Color.FromArgb(28, 30, 40),

                ForeColor = Color.White,

                FlatStyle = FlatStyle.Flat,

                Width = width,

                Location = new Point(x, y)

            };

            cmb.Items.AddRange(items);

            if (selectedIndex >= 0 && selectedIndex < items.Length) cmb.SelectedIndex = selectedIndex;

            return cmb;

        }



        private static DataGridViewButtonColumn CreateBtnCol(string name, string header, string text, int width, bool useColText = true)

        {

            return new DataGridViewButtonColumn

            {

                Name = name, HeaderText = header, Text = text,

                UseColumnTextForButtonValue = useColText, FlatStyle = FlatStyle.Flat, Width = width

            };

        }



        private void InitializeComponent()

        {

            Text = "Dofus Touch Farm Manager v2.3";

            Size = new Size(1220, 680);

            MinimumSize = new Size(1060, 520);

            StartPosition = FormStartPosition.CenterScreen;

            BackColor = Color.FromArgb(18, 19, 24);

            ForeColor = Color.FromArgb(235, 235, 240);

            Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);

            DoubleBuffered = true;



            _cellBadgeFont = new Font("Segoe UI", 8.5f, FontStyle.Bold);

            _customNameFont = new Font("Segoe UI", 9.5f, FontStyle.Bold);



            string iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_icon.ico");

            if (File.Exists(iconPath)) { try { Icon = new Icon(iconPath); } catch { } }



            _headerPanel = new Panel { Dock = DockStyle.Top, Height = 68, BackColor = Color.FromArgb(24, 26, 34), Padding = new Padding(18, 8, 18, 8) };

            _titleLabel = new Label { Text = "DOFUS TOUCH FARM MANAGER", Font = new Font("Segoe UI", 13f, FontStyle.Bold), ForeColor = Color.White, AutoSize = true, Location = new Point(18, 10) };

            _specsLabel = new Label { Text = "Checking PC hardware configuration...", Font = new Font("Segoe UI", 9f), ForeColor = ColMuted, AutoSize = true, Location = new Point(19, 38) };

            _lblActiveBadge = new Label { Text = "● 0 / 4 Running", Font = new Font("Segoe UI", 10.5f, FontStyle.Bold), ForeColor = ColMuted, AutoSize = true, Anchor = AnchorStyles.Top | AnchorStyles.Right, Location = new Point(_headerPanel.Width - 175, 22) };

            _lblVersionBadge = new Label { Text = "⚡ Version: resolving...", Font = new Font("Segoe UI", 9.5f, FontStyle.Bold), ForeColor = ColBlueText, AutoSize = true, Anchor = AnchorStyles.Top | AnchorStyles.Right, Location = new Point(_headerPanel.Width - 365, 23), Cursor = Cursors.Hand };

            _lblVersionBadge.Click += delegate { RefreshDofusVersion(true); };

            _headerPanel.Controls.AddRange(new Control[] { _titleLabel, _specsLabel, _lblVersionBadge, _lblActiveBadge });



            _toolbarPanel = new Panel { Dock = DockStyle.Top, Height = 88, BackColor = Color.FromArgb(21, 22, 29), Padding = new Padding(18, 6, 18, 6) };

            const int btnY = 8, btnH = 32;



            _btnLaunchFarm = CreateButton("Launch All", Color.FromArgb(37, 99, 235), Color.FromArgb(59, 130, 246), Color.White, 112, btnH, true, 16, btnY, OnLaunchFarmClick);

            _btnStopAll = CreateButton("Stop All", Color.FromArgb(185, 28, 28), Color.FromArgb(220, 38, 38), Color.White, 80, btnH, true, 134, btnY, OnStopAllClick);

            _btnAddSlot = CreateButton("+ Add", Color.FromArgb(16, 150, 108), Color.FromArgb(16, 185, 129), Color.White, 76, btnH, true, 220, btnY, OnAddSlotClick);

            _btnEditSelected = CreateButton("Edit", Color.FromArgb(40, 44, 58), Color.FromArgb(55, 62, 80), Color.White, 62, btnH, false, 302, btnY, OnEditSelectedClick);

            _btnDeleteSelected = CreateButton("Delete", Color.FromArgb(153, 27, 27), Color.FromArgb(220, 38, 38), Color.White, 72, btnH, true, 370, btnY, OnDeleteSelectedClick);

            _btnRelayout = CreateButton("⊞ Tile 4x", Color.FromArgb(40, 44, 58), Color.FromArgb(55, 62, 80), Color.White, 78, btnH, false, 448, btnY, OnRelayoutClick);

            _btnCascade = CreateButton("Cascade", Color.FromArgb(40, 44, 58), Color.FromArgb(55, 62, 80), Color.White, 72, btnH, false, 532, btnY, OnCascadeClick);

            _btnProxies = CreateButton("🎲 Proxies", Color.FromArgb(79, 70, 229), Color.FromArgb(99, 102, 241), Color.White, 84, btnH, false, 610, btnY, delegate
            {
                string p = Path.Combine(Environment.CurrentDirectory, "proxies.txt");
                if (!File.Exists(p)) p = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "proxies.txt");
                try
                {
                    if (File.Exists(p)) Process.Start("notepad.exe", p);
                    else MessageBox.Show("proxies.txt not found.", "Proxies Pool", MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
                catch (Exception ex) { MessageBox.Show("Error opening proxies.txt: " + ex.Message); }
            });

            Color lblCol = Color.FromArgb(180, 185, 195);

            // There is no engine selector any more: WebView2 is the only backend.
            // The Android/QEMU path is still compiled (FarmEngine needs it for the
            // legacy launch helpers) but nothing in the UI can select it.
            _lblBackend = new Label { Text = "Engine: WebView2", ForeColor = lblCol, AutoSize = true, Location = new Point(704, 15) };



            const int row2Y = 47;

            _lblGpu = new Label { Text = "GPU:", ForeColor = lblCol, AutoSize = true, Location = new Point(16, row2Y + 3) };

            _cmbGpu = CreateCombo(54, row2Y, 118, new object[] { "Host (OpenGL)", "ANGLE (D3D11)", "SwiftShader" }, 0);

            _cmbGpu.SelectedIndexChanged += OnGpuModeChanged;



            _lblRamBadge = new Label

            {

                Text = "⚡ Resource Architecture: Dynamic Shared RAM & CPU Pool (Auto-Balanced)",

                ForeColor = Color.FromArgb(52, 211, 153),

                Font = new Font("Segoe UI", 9f, FontStyle.Bold),

                AutoSize = true,

                Location = new Point(190, row2Y + 4)

            };



            _lblEngineNotice = new Label

            {

                Text = "⚡ Standalone WebView2 enabled with hardware WebGL & per-slot user-data isolation.",

                ForeColor = Color.FromArgb(56, 189, 248),

                Font = new Font("Segoe UI", 8.5f, FontStyle.Italic),

                AutoSize = true,

                Location = new Point(660, row2Y + 4)

            };



            _toolbarPanel.Controls.AddRange(new Control[]

            {

                _btnLaunchFarm, _btnStopAll, _btnAddSlot, _btnEditSelected, _btnDeleteSelected,

                _btnRelayout, _btnCascade, _lblBackend,

                _lblGpu, _cmbGpu, _lblRamBadge, _lblEngineNotice

            });



            _grid = new DataGridView

            {

                Dock = DockStyle.Fill,

                BackgroundColor = Color.FromArgb(16, 17, 22),

                ForeColor = Color.FromArgb(240, 240, 245),

                GridColor = Color.FromArgb(32, 35, 45),

                BorderStyle = BorderStyle.None,

                CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal,

                ColumnHeadersBorderStyle = DataGridViewHeaderBorderStyle.None,

                EnableHeadersVisualStyles = false,

                RowHeadersVisible = false,

                AllowUserToAddRows = false,

                AllowUserToDeleteRows = false,

                SelectionMode = DataGridViewSelectionMode.FullRowSelect,

                MultiSelect = false,

                RowTemplate = { Height = 44 },

                ColumnHeadersHeight = 38,

                ShowCellToolTips = true,

                ReadOnly = true

            };



            _grid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(26, 28, 37);

            _grid.ColumnHeadersDefaultCellStyle.ForeColor = ColMuted;

            _grid.ColumnHeadersDefaultCellStyle.Font = new Font("Segoe UI", 9f, FontStyle.Bold);

            _grid.ColumnHeadersDefaultCellStyle.Padding = new Padding(8, 4, 8, 4);

            _grid.DefaultCellStyle.BackColor = Color.FromArgb(18, 19, 25);

            _grid.DefaultCellStyle.ForeColor = Color.FromArgb(240, 240, 245);

            _grid.DefaultCellStyle.SelectionBackColor = Color.FromArgb(30, 41, 59);

            _grid.DefaultCellStyle.SelectionForeColor = Color.White;

            _grid.DefaultCellStyle.Padding = new Padding(8, 4, 8, 4);

            _grid.AlternatingRowsDefaultCellStyle.BackColor = Color.FromArgb(22, 24, 32);



            _grid.Columns.AddRange(

                new DataGridViewTextBoxColumn { Name = "Status", HeaderText = "STATUS", Width = 100 },

                new DataGridViewTextBoxColumn { Name = "CustomName", HeaderText = "INSTANCE NAME", AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill, FillWeight = 115, MinimumWidth = 110 },

                new DataGridViewTextBoxColumn { Name = "Name", HeaderText = "SLOT", Width = 78 },

                new DataGridViewTextBoxColumn { Name = "Ram", HeaderText = "RAM", Width = 76 },

                new DataGridViewTextBoxColumn { Name = "Cores", HeaderText = "CPU CORES", Width = 80 },

                new DataGridViewTextBoxColumn { Name = "Device", HeaderText = "DEVICE PROFILE", AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill, FillWeight = 135, MinimumWidth = 125 },

                new DataGridViewTextBoxColumn { Name = "Proxy", HeaderText = "NETWORK", Width = 138 },

                CreateBtnCol("Action", "ACTION", "Launch", 78, false),

                CreateBtnCol("Edit", "EDIT", "Edit", 66),

                CreateBtnCol("Clone", "CLONE", "Clone", 66),

                CreateBtnCol("Delete", "DELETE", "Delete", 72)

            );



            _rowMenu = new ContextMenuStrip { BackColor = Color.FromArgb(26, 28, 37), ForeColor = Color.White };

            _rowMenu.Items.Add("Launch / Stop (Active Engine)", null, delegate { TriggerSelectedRowAction("Action"); });

            _rowMenu.Items.Add("⚡ Launch in Standalone WebView2", null, delegate { LaunchSelectedInWebView2(); });


            _rowMenu.Items.Add(new ToolStripSeparator());

            _rowMenu.Items.Add("Edit Instance...", null, delegate { OnEditSelectedClick(null, EventArgs.Empty); });

            _rowMenu.Items.Add("🎲 Randomize Proxy (Pin Once)", null, delegate { RandomizeSelectedInstanceProxy(); });

            _rowMenu.Items.Add("🔄 Reset to Auto-Random (Default)", null, delegate { ResetSelectedInstanceProxy(); });

            _rowMenu.Items.Add("Clone Instance...", null, delegate { TriggerSelectedRowAction("Clone"); });

            _rowMenu.Items.Add(new ToolStripSeparator());

            _rowMenu.Items.Add("Delete Selected Instance", null, delegate { OnDeleteSelectedClick(null, EventArgs.Empty); }).ForeColor = ColRedText;

            _rowMenu.Items.Add("Delete ALL Instances...", null, delegate { PromptAndDeleteAllInstances(); }).ForeColor = ColRedText;



            _grid.CellPainting += OnGridCellPainting;

            _grid.CellMouseClick += OnGridCellMouseClick;

            _grid.CellDoubleClick += OnGridCellDoubleClick;

            _grid.KeyDown += OnGridKeyDown;

            _grid.CellMouseEnter += OnGridCellMouseEnter;

            _grid.CellMouseLeave += delegate { _grid.Cursor = Cursors.Default; };






            _statusPanel = new Panel { Dock = DockStyle.Bottom, Height = 36, BackColor = Color.FromArgb(24, 26, 34), Padding = new Padding(18, 6, 18, 6) };

            _lblStatusText = new Label

            {

                Text = "Ready — Double-click any instance or click 'Edit' to customize its Name, RAM, or CPU Cores.",

                Font = new Font("Segoe UI", 9f), ForeColor = ColMuted, AutoSize = true, Location = new Point(18, 9)

            };

            var lblFocus = new Label { Text = "Quick Focus:", ForeColor = ColMuted, AutoSize = true, Anchor = AnchorStyles.Top | AnchorStyles.Right, Location = new Point(_statusPanel.Width - 260, 9) };



            _btnFocus1 = CreateFocusButton("#1", _statusPanel.Width - 175, 0);

            _btnFocus2 = CreateFocusButton("#2", _statusPanel.Width - 133, 1);

            _btnFocus3 = CreateFocusButton("#3", _statusPanel.Width - 91, 2);

            _btnFocus4 = CreateFocusButton("#4", _statusPanel.Width - 49, 3);



            _statusPanel.Controls.AddRange(new Control[] { _lblStatusText, lblFocus, _btnFocus1, _btnFocus2, _btnFocus3, _btnFocus4 });

            BuildPages();

            Controls.AddRange(new Control[] { _pageHost, _statusPanel, _navPanel, _headerPanel });



            _syncTimer = new System.Windows.Forms.Timer { Interval = 4000 };

            _syncTimer.Tick += OnSyncTimerTick;

            _syncTimer.Start();



            RefreshDofusVersion(false);

        }



        private void RefreshDofusVersion(bool forceRefresh)

        {

            if (_lblVersionBadge != null)

            {

                _lblVersionBadge.Text = "⚡ Checking version...";

                _lblVersionBadge.ForeColor = ColAmberText;

            }



            DynamicVersionResolver.ResolveVersionAsync(null, forceRefresh, delegate(string msg) { Log(msg); }).ContinueWith(delegate(Task<DofusVersionInfo> t)

            {

                if (t.IsCompleted && !t.IsFaulted && t.Result != null)

                {

                    SafeBeginInvoke(delegate

                    {

                        var v = t.Result;

                        if (_lblVersionBadge != null && !IsDisposed)

                        {

                            _lblVersionBadge.Text = string.Format("⚡ v{0} ({1})", v.AppVersion, v.Source == "GooglePlayStore" ? "Live" : "Cached");

                            _lblVersionBadge.ForeColor = ColGreen;

                        }

                    });

                }

                else

                {

                    SafeBeginInvoke(delegate

                    {

                        if (_lblVersionBadge != null && !IsDisposed)

                        {

                            _lblVersionBadge.Text = "⚡ v3.14.2 (Default)";

                            _lblVersionBadge.ForeColor = ColRedText;

                        }

                    });

                }

            });

        }



        private string GetInstanceIdFromSlot(int slotIndex)

        {

            if (slotIndex <= 0) return "all";

            int rowIndex = slotIndex - 1;

            if (rowIndex >= 0 && rowIndex < _grid.Rows.Count)

            {

                InstanceModel inst = _grid.Rows[rowIndex].Tag as InstanceModel;

                if (inst != null && !string.IsNullOrEmpty(inst.Name)) return inst.Name;

            }

            return "dofus-" + slotIndex;

        }



        /// <summary>
        /// Panel shortcuts. Handled before any control sees the key, so F5 works
        /// whether the focus is in the grid, the goal box or a button.
        /// </summary>
        protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
        {
            if (_aiPanel != null && _aiPanel.HandleShortcut(keyData)) return true;
            return base.ProcessCmdKey(ref msg, keyData);
        }

        // -- global shortcuts -------------------------------------------------
        //
        // ProcessCmdKey only fires when this window has focus, and the whole point of
        // a start/stop shortcut is to press it while playing — when the game window,
        // not this one, is focused. So the shortcuts were dead exactly when they were
        // needed, and the key handler looked fine in code review.
        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        private const int WM_HOTKEY = 0x0312;
        private readonly Dictionary<int, string> _hotkeys = new Dictionary<int, string>();

        /// <summary>Register the AI panel's shortcuts as system-wide hotkeys.</summary>
        public void RegisterGlobalShortcuts()
        {
            UnregisterGlobalShortcuts();
            if (_aiPanel == null) return;
            int id = 0xA1;
            foreach (KeyValuePair<string, int> kv in _aiPanel.ShortcutMap())
            {
                if (kv.Value == 0) continue;
                Keys key = (Keys)kv.Value;
                uint mods = 0;
                if ((key & Keys.Control) == Keys.Control) mods |= 0x2;
                if ((key & Keys.Shift) == Keys.Shift) mods |= 0x4;
                if ((key & Keys.Alt) == Keys.Alt) mods |= 0x1;
                uint vk = (uint)(key & Keys.KeyCode);
                if (RegisterHotKey(Handle, id, mods, vk))
                {
                    _hotkeys[id] = kv.Key;
                    id++;
                }
                else
                {
                    // Usually another application already owns it. Saying which one
                    // failed is the difference between "shortcuts are broken" and a
                    // two-second fix.
                    Log("[AI] shortcut " + kv.Key + " (" + key.ToString()
                        + ") could not be registered; another application may own it");
                }
            }
            if (_hotkeys.Count > 0)
                Log("[AI] " + _hotkeys.Count + " global shortcut(s) registered");
        }

        public void UnregisterGlobalShortcuts()
        {
            foreach (int id in _hotkeys.Keys)
            {
                try { UnregisterHotKey(Handle, id); } catch { }
            }
            _hotkeys.Clear();
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_HOTKEY && _aiPanel != null)
            {
                int id = m.WParam.ToInt32();
                string action;
                if (_hotkeys.TryGetValue(id, out action))
                {
                    _aiPanel.RunShortcut(action);
                    return;
                }
            }
            base.WndProc(ref m);
        }

        /// <summary>
        /// Build the page shell. The window used to be one long column -- header,
        /// toolbar, grid, a 184 px AI strip, status bar -- so the instance list,
        /// which is what you actually watch, kept losing rows to panels touched
        /// once a session. Each job gets a page now, and the main page is just the
        /// grid.
        /// </summary>
        private void BuildPages()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');

            _pageInstances = new Panel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(16, 17, 22) };
            _pageInstances.Controls.Add(_grid);
            _pageInstances.Controls.Add(_toolbarPanel);

            _aiPanel = new AiPanel(baseDir, GetInstanceIdFromSlot, delegate { return _grid.Rows.Count; }, Log);
            // Shortcuts must work while the game has focus, so they are registered
            // with the OS rather than handled as ordinary keys.
            try { RegisterGlobalShortcuts(); } catch (Exception ex) { Log("[AI] shortcut registration failed: " + ex.Message); }
            _pageSettings = new SettingsPage(baseDir, Log, delegate
            {
                // Changing the egress invalidates every relay that is already
                // running: they hold the old upstream in their command line.
                try { _engine.StopAllProxies(); } catch { }
            });
            _pageShortcuts = new ShortcutsPage(baseDir, _aiPanel.Settings, delegate { _aiPanel.SaveSettings(); });

            // One line at startup that says how traffic will leave: the first
            // question in every "nothing loads" report.
            try
            {
                NetworkSettings startupNet = NetworkSettings.For(baseDir);
                Log("[NET] egress: " + startupNet.Summary + "  |  relay " + startupNet.BasePort + "+slot  |  " +
                    (startupNet.FailClosed ? "fail closed" : "will fall back to direct"));
            }
            catch { }
            _pageLogs = new LogsPage(baseDir);

            _pageHost = new Panel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(20, 22, 28) };
            _pageHost.Controls.Add(_pageInstances);
            _pageHost.Controls.Add(_aiPanel);
            _pageHost.Controls.Add(_pageSettings);
            _pageHost.Controls.Add(_pageShortcuts);
            _pageHost.Controls.Add(_pageLogs);

            _navPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 46,
                BackColor = Color.FromArgb(18, 19, 25),
                Padding = new Padding(14, 6, 14, 6)
            };

            string[] names = new string[] { "Instances", "AI", "Settings", "Shortcuts", "Logs" };
            int x = 14;
            for (int i = 0; i < names.Length; i++)
            {
                int index = i;
                var b = new Button();
                b.Text = names[i];
                b.Location = new Point(x, 7);
                b.Size = new Size(112, 30);
                b.FlatStyle = FlatStyle.Flat;
                b.FlatAppearance.BorderSize = 0;
                b.BackColor = Color.FromArgb(26, 28, 36);
                b.ForeColor = Color.FromArgb(203, 213, 225);
                b.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
                b.Cursor = Cursors.Hand;
                b.Click += delegate { ShowPage(index); };
                _navPanel.Controls.Add(b);
                _navButtons.Add(b);
                x += 116;
            }

            // The AI state has to be visible from every page, not only from the AI
            // page: a loop running in the background with no indication is how you
            // end up with two loops and one character.
            _aiLive = new Label();
            _aiLive.Text = "AI idle";
            _aiLive.AutoSize = false;
            _aiLive.TextAlign = ContentAlignment.MiddleRight;
            // Dock.Right, not a computed X: at BuildPages time the nav panel has
            // not been laid out yet, so Width is 0 and the label lands off-screen.
            _aiLive.Width = 360;
            _aiLive.Dock = DockStyle.Right;
            _aiLive.ForeColor = Color.FromArgb(148, 163, 184);
            _aiLive.Font = new Font("Consolas", 8.5f);
            _navPanel.Controls.Add(_aiLive);

            _aiLiveTimer = new System.Windows.Forms.Timer();
            _aiLiveTimer.Interval = 1000;
            _aiLiveTimer.Tick += delegate { RefreshAiLive(); };
            _aiLiveTimer.Start();

            ShowPage(0);
        }

        /// <summary>One tick: read the loop's own state and paint it.</summary>
        private void RefreshAiLive()
        {
            try
            {
                if (_aiPanel == null || _aiLive == null || _aiLive.IsDisposed) return;
                bool running = _aiPanel.RunningNow;
                string text = _aiPanel.LiveStatusText();
                if (_aiLive.Text != text) _aiLive.Text = text;
                Color want = running ? Color.FromArgb(74, 222, 128) : Color.FromArgb(148, 163, 184);
                if (_aiLive.ForeColor != want) _aiLive.ForeColor = want;

                // The nav button carries the same signal, so the eye finds it even
                // when the pill is cropped on a narrow window.
                if (_navButtons.Count > 1 && _navIndex != 1)
                {
                    _navButtons[1].ForeColor = running ? Color.FromArgb(74, 222, 128)
                                                       : Color.FromArgb(203, 213, 225);
                }
            }
            catch { }
        }

        private void ShowPage(int index)
        {
            if (_pageHost == null || index < 0 || index >= _pageHost.Controls.Count) return;
            _navIndex = index;
            for (int i = 0; i < _pageHost.Controls.Count; i++)
                _pageHost.Controls[i].Visible = (i == index);
            for (int i = 0; i < _navButtons.Count; i++)
            {
                bool active = (i == index);
                _navButtons[i].BackColor = active ? Color.FromArgb(37, 99, 235) : Color.FromArgb(26, 28, 36);
                _navButtons[i].ForeColor = active ? Color.White : Color.FromArgb(203, 213, 225);
            }
            // Pages that show live state refresh when opened, not on a hidden timer.
            if (index == 1 && _aiPanel != null) _aiPanel.RefreshTargets();
            if (index == 2 && _pageSettings != null) _pageSettings.ReloadState();
            if (index == 4 && _pageLogs != null) _pageLogs.Reload();
        }

        private Button CreateFocusButton(string text, int x, int slotIdx)

        {

            Button b = CreateButton(text, Color.FromArgb(34, 38, 50), Color.FromArgb(51, 65, 85), ColBlueText, 38, 24, false, x, 6, delegate { FocusSlotByIndex(slotIdx); });

            b.Anchor = AnchorStyles.Top | AnchorStyles.Right;

            return b;

        }



        private void FocusSlotByIndex(int rowIndex)

        {

            InstanceModel inst = (rowIndex >= 0 && rowIndex < _grid.Rows.Count) ? _grid.Rows[rowIndex].Tag as InstanceModel : null;

            if (inst == null) return;

            if (_activeBackend is WebView2Backend || (_webViewBackend != null && _webViewBackend.GetState(inst.Name) == ClientInstanceState.Running))

            {

                _webViewBackend.Focus(inst.Name);

            }

            else

            {

                _engine.FocusInstance(inst.Port);

            }

        }



        private void OnGridCellMouseEnter(object sender, DataGridViewCellEventArgs e)

        {

            if (e.RowIndex < 0 || e.ColumnIndex < 0) { _grid.Cursor = Cursors.Default; return; }

            string colName = _grid.Columns[e.ColumnIndex].Name;

            if (colName == "Action" || colName == "Edit" || colName == "Clone" || colName == "Delete")

            {

                _grid.Cursor = Cursors.Hand;

                return;

            }

            if (colName == "Status")

            {

                var inst = _grid.Rows[e.RowIndex].Tag as InstanceModel;

                _grid.Cursor = (inst != null && inst.IsRunning) ? Cursors.Hand : Cursors.Default;

                return;

            }

            _grid.Cursor = Cursors.Default;

        }



        private Button CreateButton(string text, Color baseColor, Color hoverColor, Color foreColor, int width, int height, bool bold, int x = 0, int y = 0, EventHandler onClick = null)

        {

            var btn = new Button

            {

                Text = text, BackColor = baseColor, ForeColor = foreColor, FlatStyle = FlatStyle.Flat,

                Width = width, Height = height, Location = new Point(x, y),

                Font = bold ? FontBtnBold : FontBtnReg, Cursor = Cursors.Hand

            };

            btn.FlatAppearance.BorderSize = 0;

            btn.MouseEnter += delegate { if (btn.Enabled) btn.BackColor = hoverColor; };

            btn.MouseLeave += delegate { if (btn.Enabled) btn.BackColor = baseColor; };

            if (onClick != null) btn.Click += onClick;

            return btn;

        }



        private void OnGridCellPainting(object sender, DataGridViewCellPaintingEventArgs e)

        {

            if (e.RowIndex < 0 || e.ColumnIndex < 0) return;



            string colName = _grid.Columns[e.ColumnIndex].Name;

            if (colName != "Status" && colName != "Action" && colName != "Edit" && colName != "Clone" && colName != "Delete") return;



            e.PaintBackground(e.ClipBounds, true);

            Graphics g = e.Graphics;

            g.SmoothingMode = SmoothingMode.AntiAlias;



            var inst = _grid.Rows[e.RowIndex].Tag as InstanceModel;

            bool isRunning = inst != null && inst.IsRunning;



            if (colName == "Status")

            {

                var pillRect = new Rectangle(e.CellBounds.X + 8, e.CellBounds.Y + 9, e.CellBounds.Width - 16, e.CellBounds.Height - 18);

                g.FillRectangle(isRunning ? BrushPillRunBg : BrushPillStopBg, pillRect);

                TextRenderer.DrawText(g, isRunning ? "● Running" : "● Stopped", _cellBadgeFont, pillRect,

                    isRunning ? ColGreen : ColMuted, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);

                e.Handled = true;

                return;

            }



            var btnRect = new Rectangle(e.CellBounds.X + 6, e.CellBounds.Y + 8, e.CellBounds.Width - 12, e.CellBounds.Height - 16);

            SolidBrush bg = BrushBtnDefaultBg;

            Pen pen = PenBtnDefault;

            Color fg = Color.White;

            string btnText;



            switch (colName)

            {

                case "Action":

                    bg = isRunning ? BrushBtnStopBg : BrushBtnLaunchBg;

                    pen = isRunning ? PenBtnStop : PenBtnLaunch;

                    btnText = isRunning ? "Stop" : "Launch";

                    break;

                case "Edit":

                    bg = BrushBtnEditCloneBg; pen = PenBtnEditClone; fg = ColBlueText; btnText = "Edit";

                    break;

                case "Clone":

                    bg = BrushBtnEditCloneBg; pen = PenBtnEditClone; fg = ColAmberText; btnText = "Clone";

                    break;

                case "Delete":

                    bg = BrushBtnDeleteBg; pen = PenBtnDelete; fg = ColRedText; btnText = "Delete";

                    break;

                default:

                    btnText = (e.Value ?? "").ToString();

                    break;

            }



            g.FillRectangle(bg, btnRect);

            g.DrawRectangle(pen, btnRect);

            TextRenderer.DrawText(g, btnText, _cellBadgeFont, btnRect, fg, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);

            e.Handled = true;

        }



        private void InitializeEngine()

        {

            _engine = new FarmEngine();

            _engine.OnLog += Log;



            try

            {

                _suppressGpuEvent = true;

                string gpu = _engine.GetConfiguredGpuMode();

                _cmbGpu.SelectedIndex = gpu == "angle_indirect" ? 1 : (gpu == "swiftshader_indirect" ? 2 : 0);

            }

            catch { }

            finally { _suppressGpuEvent = false; }



            _watchdog = new FarmWatchdog(_engine);

            _watchdog.OnLog += Log;

            _watchdog.Start();



            _governor = new PriorityGovernor();

            _governor.Engine = _engine;

            _governor.OnLog += Log;

            _governor.EngineOwnsAffinity = true;

            _governor.Start();



            // Initialize backends

            try

            {

                _emulatorBackend = new EmulatorBackend(_engine);

                _emulatorBackend.OnLog += Log;

                _emulatorBackend.OnStateChanged += delegate { SafeBeginInvoke(delegate { RefreshGrid(true); }); };



                _webViewBackend = new WebView2Backend();

                _webViewBackend.OnLog += Log;

                _webViewBackend.OnStateChanged += delegate { SafeBeginInvoke(delegate { RefreshGrid(true); }); };

                // The in-game HUD's Auto button asks the host to start the loop:
                // the page has no business spawning python, and this keeps the
                // shortcut, the AI page and the badge on exactly one code path.
                _webViewBackend.OnAiRequest += delegate(string instId, string action)
                {
                    SafeBeginInvoke(delegate
                    {
                        Log("[AI] in-game badge requested: " + action + " (" + instId + ")");
                        if (string.Equals(action, "start_auto", StringComparison.OrdinalIgnoreCase))
                        {
                            ShowPage(1);
                            _aiPanel.StartAuto(true);
                        }
                    });
                };

                _webViewBackend.Initialize();



                // One owner for egress: it probes every candidate, keeps the best,
                // and rebuilds the running relays (same ports) when it has to switch.
                ProxySupervisor.Instance.Bind(_engine, Log);
                ProxySupervisor.Instance.OnActiveChanged += delegate(ProxyCandidate pick)
                {
                    SafeBeginInvoke(delegate
                    {
                        Log("[PROXY] switching egress to " + pick.Label);
                        try { _engine.RebuildAllRelays(pick.Url, Log); } catch { }
                    });
                };
                ProxySupervisor.Instance.Start();

                _agentBridge = new AgentDirectiveBridge(_webViewBackend, GetInstanceIdFromSlot, Log, 8893);

                _agentBridge.Start();



                _activeBackend = (IClientBackend)_webViewBackend;

                if (_governor != null) _governor.Backend = _activeBackend;

            }

            catch (Exception ex)

            {

                Log("[BACKEND INIT ERROR] " + ex.Message);

            }



            ThreadPool.QueueUserWorkItem(delegate

            {

                try

                {

                    _specs = HardwareSpecs.Detect();

                    SafeInvoke(delegate

                    {

                        if (_specs != null)

                        {

                            _specsLabel.Text = string.Format("PC: {0}  •  {1}  •  {2} GB RAM ({3} GB Free)  •  {4} Threads",

                                _specs.CpuName, _specs.GpuName, _specs.HostRamGB, _specs.FreeRamGB, _specs.LogicalThreads);

                            ApplyHardwareDefaults(_specs);

                        }

                        RefreshGrid(true);

                    });

                }

                catch { }

            });

        }



        private void LaunchSelectedInWebView2()

        {

            if (_grid.SelectedRows.Count == 0) return;

            var inst = _grid.SelectedRows[0].Tag as InstanceModel;

            if (inst == null) return;

            if (_webViewBackend.GetState(inst.Name) == ClientInstanceState.Running)

            {

                _webViewBackend.Stop(inst.Name);

            }

            else

            {

                var opts = new ClientLaunchOptions

                {

                    InstanceId = inst.Name,

                    DisplayName = inst.CustomName ?? inst.DisplayName,

                    SlotPort = inst.Port,

                    RamMb = inst.RamMb > 0 ? inst.RamMb : GetSelectedRam(),

                    Cores = inst.Cores > 0 ? inst.Cores : GetSelectedCores(),

                    Proxy = inst.Proxy

                };

                if (WireWebView2LocalProxy(opts, inst)) _webViewBackend.Start(opts);

            }

            RefreshGrid(true);

        }



        private void OnSyncTimerTick(object sender, EventArgs e)

        {

            FlushLog();

            if (!_isClosing && !_isFarmLaunching) RefreshGrid(false);

        }



        private void Log(string msg)

        {

            if (string.IsNullOrEmpty(msg)) return;

            try

            {

                lock (_logFileLock)

                {

                    if (_logWriter == null)

                        _logWriter = new StreamWriter(new FileStream(LogFilePath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite)) { AutoFlush = false };

                    _logWriter.WriteLine(msg);

                    // Batch flushes: a flush syscall per line dominated disk
                    // I/O during log floods. Flush every 200 lines here, plus
                    // the periodic sync-timer flush and the shutdown flush.
                    _logWritesSinceFlush++;

                    if (_logWritesSinceFlush >= 200)

                    {

                        _logWriter.Flush();

                        _logWritesSinceFlush = 0;

                    }

                }

            }

            catch { try { if (_logWriter != null) { _logWriter.Dispose(); _logWriter = null; } } catch { } }



            if (IsDisposed || _isClosing || !IsHandleCreated) return;

            if (!msg.Contains("[GOVERNOR]") && !msg.Contains("[SECURITY]"))

            {

                // Coalesce status-label updates: during log floods only the newest message is

                // rendered, and at most one UI callback is queued at any time.

                _pendingStatusText = msg;

                if (Interlocked.CompareExchange(ref _statusTextPending, 1, 0) == 0)

                {

                    SafeBeginInvoke(delegate

                    {

                        try

                        {

                            if (_lblStatusText != null && !IsDisposed)

                                _lblStatusText.Text = RxLogPrefix.Replace(_pendingStatusText ?? "", "");

                        }

                        finally { Interlocked.Exchange(ref _statusTextPending, 0); }

                    });

                }

            }

        }



        private void FlushLog()

        {

            lock (_logFileLock)

            {

                try { if (_logWriter != null) { _logWriter.Flush(); _logWritesSinceFlush = 0; } } catch { }

            }

        }



        private int GetSelectedRam() { return 0; } // 0 = dynamic shared memory

        private int GetSelectedCores() { return 0; } // 0 = dynamic shared all cores



        // Derives per-instance defaults from whatever machine the farm happens to run on, so the

        // same build behaves sensibly on a 4-thread laptop and on a 32-thread farm server.

        //  * RAM: never below 1024 MB (Android 29 + WebView + the game need it); the old 512 MB

        //    floor made the guest swap constantly, which is what made everything feel frozen.

        //  * CPU: each instance needs at least 2 logical CPUs. With only 1 the emulator's UI

        //    thread competes with the guest renderer and the screen stops reacting to clicks.

        private void ApplyHardwareDefaults(HardwareSpecs specs)

        {

            if (specs == null) return;

            int logical = Math.Max(1, specs.LogicalThreads);



            // Default to what each instance will actually get once the farm is up. On a 4-core host

            // that is 1 Core, so 4 instances fit exactly on 4 physical cores - the target config.



        }



        private void RefreshGrid(bool forceRedraw = true)

        {

            if (Interlocked.CompareExchange(ref _isSyncing, 1, 0) != 0)

            {

                if (forceRedraw) Interlocked.Exchange(ref _refreshPendingForce, 1);

                return;

            }



            ThreadPool.QueueUserWorkItem(delegate

            {

                try

                {

                    if (_isClosing) return;

                    List<InstanceModel> list = _engine.GetInstances();



                    var sigBuilder = new StringBuilder(list.Count * 48);

                    foreach (InstanceModel im in list)

                    {

                        bool isImRunning = im.IsRunning || (_webViewBackend != null && _webViewBackend.GetState(im.Name) == ClientInstanceState.Running);

                        sigBuilder.Append(im.Name).Append(':').Append(im.CustomName).Append(':')

                                  .Append(isImRunning ? '1' : '0').Append(':').Append(im.RamMb).Append(':')

                                  .Append(im.Cores).Append(':').Append(im.PhoneModelId).Append(':').Append(im.Proxy).Append(';');

                    }

                    string newSig = sigBuilder.ToString();

                    if (!forceRedraw && string.Equals(newSig, _lastStateSignature, StringComparison.Ordinal)) return;

                    _lastStateSignature = newSig;



                    SafeInvoke(delegate

                    {

                        string selectedName = (_grid.SelectedRows.Count > 0 && _grid.SelectedRows[0].Tag is InstanceModel)

                            ? ((InstanceModel)_grid.SelectedRows[0].Tag).Name : null;



                        _grid.SuspendLayout();

                        try

                        {

                            _grid.Rows.Clear();

                            int runningCount = 0;

                            foreach (InstanceModel inst in list)

                            {

                                bool isRunning = inst.IsRunning || (_webViewBackend != null && _webViewBackend.GetState(inst.Name) == ClientInstanceState.Running);

                                if (isRunning)

                                {

                                    inst.IsRunning = true;

                                    runningCount++;

                                }

                                string proxyDisplay = inst.Proxy ?? "127.0.0.1:8880";

                                proxyDisplay = (proxyDisplay.Contains("127.0.0.1") || proxyDisplay.Contains("Accelerator"))

                                    ? "Built-in Accelerator"

                                    : proxyDisplay.Replace("[🌐] ", "");



                                int rowIdx = _grid.Rows.Add(

                                    isRunning ? "Running" : "Stopped",

                                    inst.CustomName ?? inst.DisplayName,

                                    inst.Name,

                                    inst.RamMb > 0 ? (inst.RamMb + " MB") : "Shared",

                                    inst.Cores > 0 ? (inst.Cores + (inst.Cores == 1 ? " Core" : " Cores")) : "Shared",

                                    inst.PhoneModelName ?? "Galaxy Tab S6",

                                    proxyDisplay,

                                    isRunning ? "Stop" : "Launch",

                                    "Edit", "Clone", "Delete");



                                DataGridViewRow row = _grid.Rows[rowIdx];

                                row.Tag = inst;

                                row.Cells["CustomName"].Style.Font = _customNameFont;

                                row.Cells["CustomName"].Style.ForeColor = Color.White;

                                row.Cells["Name"].Style.ForeColor = ColMuted;

                                row.Cells["Device"].Style.ForeColor = Color.FromArgb(226, 232, 240);

                                row.Cells["Proxy"].Style.ForeColor = ColMuted;

                                if (isRunning) row.Cells["Status"].ToolTipText = "Click to bring this running instance window to front";

                                if (!string.IsNullOrEmpty(selectedName) && string.Equals(inst.Name, selectedName, StringComparison.OrdinalIgnoreCase))

                                    row.Selected = true;

                            }



                            _btnLaunchFarm.Text = string.Format("Launch All ({0}x)", list.Count > 0 ? list.Count : 4);

                            _lblActiveBadge.Text = string.Format("● {0} / {1} Running", runningCount, list.Count);

                            _lblActiveBadge.ForeColor = runningCount > 0 ? ColGreen : ColMuted;

                        }

                        finally { _grid.ResumeLayout(); }

                    });

                }

                catch { }

                finally

                {

                    Interlocked.Exchange(ref _isSyncing, 0);

                    if (Interlocked.Exchange(ref _refreshPendingForce, 0) != 0)

                        ThreadPool.QueueUserWorkItem(delegate { RefreshGrid(true); });

                }

            });

        }







        private void OnLaunchFarmClick(object sender, EventArgs e)

        {

            if (_isFarmLaunching) return;

            _isFarmLaunching = true;

            _btnLaunchFarm.Enabled = false;



            int defaultRam = GetSelectedRam(), defaultCores = GetSelectedCores();

            ThreadPool.QueueUserWorkItem(delegate

            {

                try

                {

                    List<InstanceModel> existingInsts = _engine.GetInstances();

                    if (existingInsts.Count == 0)

                    {

                        int planned = _engine.RecommendedInstanceCount();

                        int plannedCores = _engine.RecommendedCoresPerInstance(planned);

                        for (int i = 1; i <= planned; i++)

                        {

                            if (_isClosing || !_isFarmLaunching) return;

                            _engine.ProvisionInstance(string.Format("dofus-{0:D2}", i), defaultRam, plannedCores, null, "Instance #" + i, "");

                        }

                        existingInsts = _engine.GetInstances();

                    }



                    Log(string.Format("Launching {0} instance(s) using backend: {1}...", existingInsts.Count, _activeBackend != null ? _activeBackend.Name : "Default"));



                    if (_activeBackend is WebView2Backend)

                    {

                        foreach (InstanceModel im in existingInsts)

                        {

                            if (_isClosing || !_isFarmLaunching) return;

                            if (_webViewBackend.GetState(im.Name) != ClientInstanceState.Running)

                            {

                                var opts = new ClientLaunchOptions

                                {

                                    InstanceId = im.Name,

                                    DisplayName = im.CustomName ?? im.DisplayName,

                                    SlotPort = im.Port,

                                    RamMb = im.RamMb > 0 ? im.RamMb : defaultRam,

                                    Cores = im.Cores > 0 ? im.Cores : defaultCores,

                                    Proxy = im.Proxy

                                };

                                if (WireWebView2LocalProxy(opts, im)) _webViewBackend.Start(opts);

                                // Natural human stagger: 2.5s - 3.8s between client connection handshakes

                                int naturalStaggerMs = 2500 + Rng.Next(1300);

                                Thread.Sleep(naturalStaggerMs);

                            }

                        }

                    }

                    else

                    {

                        try { _engine.Adb("", "start-server", 6000); } catch { }



                        foreach (InstanceModel im in existingInsts.Where(x => !x.IsRunning))

                        {

                            if (_isClosing || !_isFarmLaunching) return;

                            _engine.LaunchInstance(im.Name, im.Port, im.RamMb > 0 ? im.RamMb : defaultRam, im.Cores > 0 ? im.Cores : defaultCores, im.Proxy);

                            Thread.Sleep(1500);

                        }



                        foreach (int delay in new[] { 2500, 4000 })

                        {

                            Thread.Sleep(delay);

                            if (_isClosing || !_isFarmLaunching) return;

                            SafeInvoke(delegate { RefreshGrid(true); OnRelayoutClick(null, null); });

                        }

                    }

                }

                catch (Exception ex) { Log("Launch error: " + ex.Message); }

                finally

                {

                    _isFarmLaunching = false;

                    SafeBeginInvoke(delegate { _btnLaunchFarm.Enabled = true; RefreshGrid(true); });

                }

            });

        }



        private void OnStopAllClick(object sender, EventArgs e)

        {

            _isFarmLaunching = false;

            _btnStopAll.Enabled = false;

            Log("Stopping all running instances across both backends...");

            ThreadPool.QueueUserWorkItem(delegate

            {

                try

                {

                    if (_webViewBackend != null)

                    {

                        List<InstanceModel> existingInsts = _engine.GetInstances();

                        foreach (var im in existingInsts)

                        {

                            _webViewBackend.Stop(im.Name);

                        }

                        if (_webViewBackend.GetState("probe-test") == ClientInstanceState.Running) _webViewBackend.Stop("probe-test");

                    }

                    _engine.StopAll();

                    Thread.Sleep(500);

                    SafeInvoke(delegate { RefreshGrid(true); });

                }

                catch (Exception ex) { Log("Error stopping instances: " + ex.Message); }

                finally { SafeBeginInvoke(delegate { _btnStopAll.Enabled = true; }); }

            });

        }



        private void ApplyWindowLayout(bool tile)

        {

            if (_activeBackend != null)

            {

                if (tile) _activeBackend.TileWindows(true);

                else _activeBackend.CascadeWindows();

                Log(string.Format("{0} layout applied via {1}.", tile ? "Tile" : "Cascade", _activeBackend.Name));

                return;

            }



            ThreadPool.QueueUserWorkItem(delegate

            {

                try

                {

                    List<int> pids = _engine.GetInstances().Where(i => i.IsRunning && i.Pid > 0).Select(i => i.Pid).ToList();

                    if (pids.Count == 0)

                    {

                        Process[] procs = Process.GetProcessesByName("qemu-system-x86_64");

                        try { foreach (Process p in procs) pids.Add(p.Id); }

                        finally { foreach (Process p in procs) p.Dispose(); }

                    }

                    if (pids.Count > 0)

                    {

                        if (tile) Win32.ApplyFarmLayout(pids, true);

                        else Win32.ApplyCascadeLayout(pids);

                        Log(string.Format("{0} {1} active window(s).", tile ? "Tiled" : "Cascaded", pids.Count));

                    }

                }

                catch { }

            });

        }



        private void OnRelayoutClick(object sender, EventArgs e) { ApplyWindowLayout(true); }

        private void OnCascadeClick(object sender, EventArgs e) { ApplyWindowLayout(false); }



        private void OnAddSlotClick(object sender, EventArgs e)

        {

            string nextSlot = _engine.GetNextAvailableSlot();

            int slotNum = 1;

            Match m = RxTrailingDigits.Match(nextSlot);

            if (m.Success) int.TryParse(m.Groups[1].Value, out slotNum);



            using (var dlg = new InstanceEditorDialog(true, nextSlot, "Instance #" + slotNum, GetSelectedRam(), GetSelectedCores(), DeviceCatalog.GetDefaultProfileForIndex(slotNum).Id, ""))

            {

                if (dlg.ShowDialog(this) == DialogResult.OK)

                {

                    string customName = dlg.InstanceCustomName, profileId = dlg.SelectedProfileId, proxy = dlg.SelectedProxy;

                    int ram = dlg.SelectedRamMb, cores = dlg.SelectedCores;

                    ThreadPool.QueueUserWorkItem(delegate

                    {

                        try { _engine.ProvisionInstance(nextSlot, ram, cores, profileId, customName, proxy); RefreshGrid(true); }

                        catch (Exception ex) { Log("Error adding instance: " + ex.Message); }

                    });

                }

            }

        }



        private void OnEditSelectedClick(object sender, EventArgs e)

        {

            if (_grid.SelectedRows.Count == 0)

            {

                MessageBox.Show("Select an instance in the table first to edit its settings.", "Edit Instance", MessageBoxButtons.OK, MessageBoxIcon.Information);

                return;

            }

            OpenEditDialogForInstance(_grid.SelectedRows[0].Tag as InstanceModel);

        }



        internal static string NormalizeProxyForComparison(string raw)

        {

            if (!FarmEngine.HasManualProxy(raw)) return "";

            string s = (raw ?? "").Trim();

            if (s.StartsWith("[🌐] ")) s = s.Substring(5).Trim();

            return s;

        }



        private void OnGpuModeChanged(object sender, EventArgs e)

        {

            if (_suppressGpuEvent || _engine == null) return;

            int idx = _cmbGpu.SelectedIndex;

            string mode = idx == 1 ? "angle_indirect" : (idx == 2 ? "swiftshader_indirect" : "host");

            ThreadPool.QueueUserWorkItem(delegate { try { _engine.SetConfiguredGpuMode(mode); } catch { } });

        }



        private void OnDeleteSelectedClick(object sender, EventArgs e)

        {

            if (_grid.SelectedRows.Count == 0)

            {

                if (MessageBox.Show("No single row is selected.\n\nWould you like to delete ALL instances and reset the farm?",

                    "Delete Instances", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)

                    PromptAndDeleteAllInstances();

                return;

            }

            PromptAndDeleteInstance(_grid.SelectedRows[0].Tag as InstanceModel);

        }



        private void PromptAndDeleteInstance(InstanceModel inst)

        {

            if (inst == null) return;

            if (MessageBox.Show(string.Format("Delete instance '{0}' ({1}) and permanently remove its virtual disk?", inst.CustomName ?? inst.Name, inst.Name),

                "Confirm Delete Instance", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) return;



            if (!TryBeginOp(inst.Name)) return;

            string nameToDelete = inst.Name;

            ThreadPool.QueueUserWorkItem(delegate

            {

                try

                {

                    if (_isClosing) return;

                    _engine.DeleteInstance(nameToDelete);

                    Thread.Sleep(400);

                    RefreshGrid(true);

                }

                catch (Exception ex) { Log(string.Format("Error deleting {0}: {1}", nameToDelete, ex.Message)); }

                finally { EndOp(nameToDelete); }

            });

        }



        private void PromptAndDeleteAllInstances()

        {

            if (MessageBox.Show("Are you sure you want to stop and DELETE ALL instances (dofus-01..N)?\n\nYour Golden Master template is preserved so you can re-add clean instances anytime.",

                "Confirm Delete All Instances", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) return;



            _btnDeleteSelected.Enabled = false;

            ThreadPool.QueueUserWorkItem(delegate

            {

                try

                {

                    Log(string.Format("Deleted {0} instance(s).", _engine.DeleteAllInstances()));

                    Thread.Sleep(400);

                    RefreshGrid(true);

                }

                catch (Exception ex) { Log("Error deleting all instances: " + ex.Message); }

                finally { SafeBeginInvoke(delegate { _btnDeleteSelected.Enabled = true; }); }

            });

        }



        private void TriggerSelectedRowAction(string colName)

        {

            if (_grid.SelectedRows.Count > 0)

                HandleGridAction(_grid.SelectedRows[0].Index, _grid.Columns[colName].Index);

        }



        private void OnGridKeyDown(object sender, KeyEventArgs e)

        {

            if (e.KeyCode == Keys.Delete && _grid.SelectedRows.Count > 0)

            {

                e.Handled = true;

                PromptAndDeleteInstance(_grid.SelectedRows[0].Tag as InstanceModel);

            }

        }



        private void RandomizeSelectedInstanceProxy()
        {
            if (_grid.SelectedRows.Count == 0) return;
            var inst = _grid.SelectedRows[0].Tag as InstanceModel;
            if (inst == null) return;

            string rnd = FarmEngine.GetRandomProxy();
            if (string.IsNullOrEmpty(rnd))
            {
                MessageBox.Show("No proxies found in proxies.txt.\r\nAdd your proxies to proxies.txt to use the randomizer.", "Proxy Pool Empty", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            try
            {
                _engine.UpdateInstanceConfig(inst.Name, inst.CustomName ?? inst.DisplayName, inst.RamMb, inst.Cores, inst.PhoneModelId, rnd);
                Log(string.Format("🎲 Random proxy assigned to {0}: {1}", inst.Name, rnd));
                RefreshGrid(true);
            }
            catch (Exception ex)
            {
                Log(string.Format("Failed to randomize proxy for {0}: {1}", inst.Name, ex.Message));
            }
        }

        private void ResetSelectedInstanceProxy()
        {
            if (_grid.SelectedRows.Count == 0) return;
            var inst = _grid.SelectedRows[0].Tag as InstanceModel;
            if (inst == null) return;

            try
            {
                _engine.UpdateInstanceConfig(inst.Name, inst.CustomName ?? inst.DisplayName, inst.RamMb, inst.Cores, inst.PhoneModelId, "");
                Log(string.Format("🔄 Reset {0} to default automatic proxy randomization.", inst.Name));
                RefreshGrid(true);
            }
            catch (Exception ex)
            {
                Log(string.Format("Failed to reset proxy for {0}: {1}", inst.Name, ex.Message));
            }
        }

        private void OpenEditDialogForInstance(InstanceModel inst)

        {

            if (inst == null) return;

            using (var dlg = new InstanceEditorDialog(false, inst.Name, inst.CustomName ?? inst.DisplayName, inst.RamMb, inst.Cores, inst.PhoneModelId, inst.Proxy))

            {

                DialogResult result = dlg.ShowDialog(this);

                if (dlg.DeleteRequested || result == DialogResult.Abort) { PromptAndDeleteInstance(inst); return; }

                if (result != DialogResult.OK) return;



                string slotName = inst.Name, slotSerial = inst.Serial, customName = dlg.InstanceCustomName, profileId = dlg.SelectedProfileId, proxy = dlg.SelectedProxy;

                int slotPort = inst.Port, ram = dlg.SelectedRamMb, cores = dlg.SelectedCores;

                bool hwOrNetChanged = ram != inst.RamMb || cores != inst.Cores ||

                                      !string.Equals(profileId, inst.PhoneModelId, StringComparison.OrdinalIgnoreCase) ||

                                      !string.Equals(NormalizeProxyForComparison(proxy), NormalizeProxyForComparison(inst.Proxy), StringComparison.OrdinalIgnoreCase);



                bool restartNow = inst.IsRunning && hwOrNetChanged && MessageBox.Show(

                    string.Format("'{0}' is currently running.\n\nWould you like to restart it now so the new RAM / CPU / device / network settings take effect immediately?", customName),

                    "Apply Changes Now?", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes;



                ThreadPool.QueueUserWorkItem(delegate

                {

                    try

                    {

                        if (restartNow) { _engine.StopInstance(slotSerial, slotName); Thread.Sleep(800); }

                        _engine.UpdateInstanceConfig(slotName, customName, ram, cores, profileId, proxy);

                        if (restartNow) { _engine.LaunchInstance(slotName, slotPort, ram, cores, proxy); Thread.Sleep(1500); }

                        RefreshGrid(true);

                    }

                    catch (Exception ex) { Log("Error updating instance: " + ex.Message); }

                });

            }

        }



        private void OnGridCellMouseClick(object sender, DataGridViewCellMouseEventArgs e)

        {

            if (e.RowIndex < 0 || e.RowIndex >= _grid.Rows.Count) return;

            if (e.Button == MouseButtons.Right)

            {

                _grid.ClearSelection();

                _grid.Rows[e.RowIndex].Selected = true;

                if (_rowMenu != null) _rowMenu.Show(Cursor.Position);

                return;

            }

            if (e.Button == MouseButtons.Left && e.ColumnIndex >= 0)

                HandleGridAction(e.RowIndex, e.ColumnIndex);

        }



        private void HandleGridAction(int rowIndex, int columnIndex)

        {

            if (rowIndex < 0 || rowIndex >= _grid.Rows.Count || columnIndex < 0) return;

            var inst = _grid.Rows[rowIndex].Tag as InstanceModel;

            if (inst == null) return;



            string colName = _grid.Columns[columnIndex].Name;

            switch (colName)

            {

                case "Status":

                    if (_activeBackend is WebView2Backend || (_webViewBackend != null && _webViewBackend.GetState(inst.Name) == ClientInstanceState.Running))

                    {

                        var form = _webViewBackend != null ? _webViewBackend.GetForm(inst.Name) : null;

                        if (form != null && !form.IsDisposed)

                        {

                            SafeInvoke(delegate { form.BringToFront(); form.Focus(); });

                        }

                    }

                    else if (inst.IsRunning)

                    {

                        _engine.FocusInstance(inst.Port);

                    }

                    break;

                case "Action":

                    if (!TryBeginOp(inst.Name)) return;

                    ThreadPool.QueueUserWorkItem(delegate

                    {

                        try

                        {

                            if (_isClosing) return;

                            bool wvRunning = _webViewBackend != null && _webViewBackend.GetState(inst.Name) == ClientInstanceState.Running;

                            if (_activeBackend is WebView2Backend)

                            {

                                if (wvRunning)

                                {

                                    _webViewBackend.Stop(inst.Name);

                                }

                                else

                                {

                                    var opts = new ClientLaunchOptions

                                    {

                                        InstanceId = inst.Name,

                                        DisplayName = inst.CustomName ?? inst.DisplayName,

                                        SlotPort = inst.Port,

                                        RamMb = inst.RamMb > 0 ? inst.RamMb : GetSelectedRam(),

                                        Cores = inst.Cores > 0 ? inst.Cores : GetSelectedCores(),

                                        Proxy = inst.Proxy

                                    };

                                    if (WireWebView2LocalProxy(opts, inst)) _webViewBackend.Start(opts);

                                }

                            }

                            else

                            {

                                if (inst.IsRunning) _engine.StopInstance(inst.Serial, inst.Name);

                                else _engine.LaunchInstance(inst.Name, inst.Port, inst.RamMb, inst.Cores, inst.Proxy);

                            }

                            Thread.Sleep(800);

                            RefreshGrid(true);

                        }

                        catch (Exception ex) { Log(string.Format("Error toggling {0}: {1}", inst.Name, ex.Message)); }

                        finally { EndOp(inst.Name); }

                    });

                    break;

                case "Edit":

                    OpenEditDialogForInstance(inst);

                    break;

                case "Clone":

                    string nextSlot = _engine.GetNextAvailableSlot();

                    using (var dlg = new CloneDialog(inst.CustomName ?? inst.Name, nextSlot, (inst.CustomName ?? inst.Name) + " (Copy)"))

                    {

                        if (dlg.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(dlg.TargetSlotName))

                        {

                            string srcName = inst.Name, targetSlot = dlg.TargetSlotName, targetCustom = dlg.TargetCustomName;

                            bool cloneState = dlg.CloneState;

                            if (!TryBeginOp(targetSlot)) return;

                            ThreadPool.QueueUserWorkItem(delegate

                            {

                                try

                                {

                                    if (_isClosing) return;

                                    _engine.CloneInstance(srcName, targetSlot, cloneState, targetCustom);

                                    Thread.Sleep(800);

                                    RefreshGrid(true);

                                }

                                catch (Exception ex) { Log(string.Format("Error cloning {0}: {1}", srcName, ex.Message)); }

                                finally { EndOp(targetSlot); }

                            });

                        }

                    }

                    break;

                case "Delete":

                    PromptAndDeleteInstance(inst);

                    break;

            }

        }



        // Routes the WebView2 client through the local Go proxy: it provides

        // the /__cdn/ asset relay (hotlink-safe headers + shared disk cache)

        // and optional upstream egress, mirroring what LaunchInstance does for

        // Android instances.

        /// <summary>
        /// Returns false when the instance must NOT be started. Fail-closed has to
        /// cover the launch too: if the egress could not be wired and the mode is
        /// not "direct", starting the client anyway would put the real IP on the
        /// wire, which is the one thing the proxy exists to prevent.
        /// </summary>
        private bool WireWebView2LocalProxy(ClientLaunchOptions opts, InstanceModel inst)

        {

            try

            {

                NetworkSettings net = NetworkSettings.For(_engine.AppBaseDir);
                int slot = Math.Max(0, (inst.Port - 5554) / 2), localProxyPort = net.BasePort + slot;
                DeviceProfile profile = _engine.GetInstancePhoneModel(inst.Name);
                string serialPrefix = (profile != null && !string.IsNullOrEmpty(profile.SerialPrefix)) ? profile.SerialPrefix : "R58M";
                string serialNo = serialPrefix + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpperInvariant();
                string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLowerInvariant();

                // network.ini decides the egress; a per-instance proxy still wins.
                FarmEngine.UpstreamChoice choice = FarmEngine.ResolveUpstream(inst.Proxy, slot, Log);
                if (!choice.Ok)
                {
                    Log(string.Format("WebView2 {0}: not started - {1}. Fix it in Settings > Network.", inst.Name, choice.Reason));
                    return false;
                }
                string upstream = choice.Url;
                Log(string.Format("WebView2 {0}: egress {1} ({2})", inst.Name,
                    choice.Direct ? "DIRECT" : upstream, choice.Reason));

                bool wired = _engine.EnsureProxyRunning(localProxyPort, upstream, serialNo, androidId, profile);

                if (!wired && !choice.Direct)
                {
                    Log(string.Format("WebView2 {0}: relay did not start on port {1}; refusing to launch with a direct " +
                                      "connection (fail closed).", inst.Name, localProxyPort));
                    return false;
                }

                if (wired)

                {

                    opts.ProxyPort = localProxyPort;

                    try { opts.CacheDir = System.IO.Path.Combine(_engine.AppBaseDir, "asset_cache"); } catch { }

                    Log(string.Format("WebView2 {0}: relay proxy on 127.0.0.1:{1} (upstream: {2})",

                        inst.Name, localProxyPort, string.IsNullOrEmpty(upstream) ? "DIRECT (proxies.txt empty)" : upstream));

                }

                // Manual-login mode: do not hand the instance an account from
                // accounts.txt. Otherwise a successful Google/OAuth login is
                // overwritten on the next reload by whatever guest this slot was
                // assigned -- the guest identity is persisted into the profile and
                // replayed, so the real session never survives the reload.
                bool manualLogin = false;
                try
                {
                    manualLogin = System.IO.File.Exists(
                        System.IO.Path.Combine(_engine.AppBaseDir, "manual-login.flag"));
                }
                catch { }

                System.Collections.Generic.KeyValuePair<string, string>? acc = null;
                if (!manualLogin) acc = FarmEngine.GetAccountForSlot(slot);
                if (manualLogin)
                {
                    Log(string.Format("WebView2 {0}: manual-login.flag present - no account assigned from accounts.txt (sign in by hand)",
                        inst.Name));
                    opts.AccountLogin = null;
                    opts.AccountPassword = null;
                }
                else if (acc.HasValue)
                {
                    opts.AccountLogin = acc.Value.Key;
                    opts.AccountPassword = acc.Value.Value;
                    Log(string.Format("WebView2 {0}: assigned visitor/account from accounts.txt: {1}", inst.Name, opts.AccountLogin));
                }
                else
                {
                    Log(string.Format("WebView2 {0}: no accounts configured in accounts.txt (guest registration fallback)", inst.Name));
                }

                return true;
            }

            catch (Exception ex)

            {

                Log(string.Format("WebView2 proxy wiring failed for {0}: {1}", inst.Name, ex.Message));
                return false;
            }

        }



        private void OnGridCellDoubleClick(object sender, DataGridViewCellEventArgs e)

        {

            if (e.RowIndex >= 0 && e.RowIndex < _grid.Rows.Count)

                OpenEditDialogForInstance(_grid.Rows[e.RowIndex].Tag as InstanceModel);

        }



        protected override void OnFormClosing(FormClosingEventArgs e)

        {
            try { UnregisterGlobalShortcuts(); } catch { }
            try { if (_aiLiveTimer != null) { _aiLiveTimer.Stop(); _aiLiveTimer.Dispose(); _aiLiveTimer = null; } } catch { }

            // Persist the AI panel's mode, budgets and shortcuts. Doing it here
            // rather than on every keystroke keeps the file from being rewritten
            // while the loop is starting up.
            try { if (_aiPanel != null) _aiPanel.SaveSettings(); } catch { }

            if (e.CloseReason == CloseReason.UserClosing && !_isClosing)

            {

                int activeCount = _grid.Rows.Cast<DataGridViewRow>().Select(r => r.Tag as InstanceModel).Count(im => im != null && im.IsRunning);

                if (activeCount > 0)

                {

                    _keepInstancesOnExit = MessageBox.Show(

                        string.Format("You have {0} game instance(s) running.\n\nYES - keep them running in the background (recommended): the next Launch attaches instantly, no 60-80s reboot.\nNO  - shut every instance down now.", activeCount),

                        "Keep Instances Running?", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes;

                }

            }



            _isClosing = true;

            _isFarmLaunching = false;



            if (_lblStatusText != null)

            {

                _lblStatusText.Text = _keepInstancesOnExit

                    ? "Detaching from running instances (they keep playing in the background)..."

                    : "Saving instance snapshots and shutting down cleanly...";

            }



            if (Interlocked.CompareExchange(ref _shutdownStarted, 1, 0) != 0)

            {

                if (Interlocked.CompareExchange(ref _shutdownComplete, 0, 0) == 0) { e.Cancel = true; return; }

                FlushLog();

                base.OnFormClosing(e);

                return;

            }



            if (_syncTimer != null) { try { _syncTimer.Stop(); _syncTimer.Dispose(); } catch { } _syncTimer = null; }

            if (_engine != null) _engine.OnLog -= Log;

            if (_watchdog != null) { _watchdog.OnLog -= Log; _watchdog.Stop(); }

            if (_governor != null) { _governor.OnLog -= Log; _governor.Stop(); }

            if (_agentBridge != null) { try { _agentBridge.Dispose(); } catch { } _agentBridge = null; }

            if (_webViewBackend != null) { try { _webViewBackend.Dispose(); } catch { } }



            // Instance/process teardown used to run synchronously on the UI thread while pumping

            // messages with DoEvents. It now runs on a worker; the window stays up with the status

            // message until teardown finishes, then closes for real.

            e.Cancel = true;

            ThreadPool.QueueUserWorkItem(delegate

            {

                try

                {

                    if (_engine != null)

                    {

                        if (_keepInstancesOnExit) Log("Instances left running on purpose - next Launch will attach to them instantly.");

                        else _engine.StopAll();

                    }



                    if (_sentinelProcess != null)

                    {

                        try

                        {

                            if (!_sentinelProcess.HasExited) _sentinelProcess.Kill();

                            _sentinelProcess.Dispose();

                        }

                        catch { }

                        finally { _sentinelProcess = null; }

                    }

                }
                catch { }
                try { FarmEngine.StopWarp(); } catch { }
                finally

                {

                    FlushLog();

                    Interlocked.Exchange(ref _shutdownComplete, 1);

                    try { BeginInvoke(new Action(Close)); } catch { }

                }

            });

            base.OnFormClosing(e);

        }



        protected override void Dispose(bool disposing)

        {

            if (disposing)

            {

                _isClosing = true;

                if (_agentBridge != null) { try { _agentBridge.Dispose(); } catch { } _agentBridge = null; }

                if (_webViewBackend != null) { try { _webViewBackend.Dispose(); } catch { } _webViewBackend = null; }

                if (_cellBadgeFont != null) { try { _cellBadgeFont.Dispose(); } catch { } _cellBadgeFont = null; }

                if (_customNameFont != null) { try { _customNameFont.Dispose(); } catch { } _customNameFont = null; }

                if (Icon != null) { try { Icon.Dispose(); } catch { } }

            }

            base.Dispose(disposing);

        }

    }



    public class InstanceEditorDialog : Form

    {

        private static readonly Random Rng = new Random();



        public string InstanceCustomName { get; private set; }

        public int SelectedRamMb { get; private set; }

        public int SelectedCores { get; private set; }

        public string SelectedProfileId { get; private set; }

        public string SelectedProxy { get; private set; }

        public bool DeleteRequested { get; private set; }



        private TextBox _txtCustomName;

        private ComboBox _cmbProfiles;

        private Button _btnRandomProfile;

        private TextBox _txtProxy;

        private Button _btnRandomProxy;

        private Button _btnSave;

        private Button _btnCancel;



        private readonly List<DeviceProfile> _profiles;



        public InstanceEditorDialog(bool isNew, string slotName, string currentCustomName, int currentRamMb, int currentCores, string currentProfileId, string currentProxy)

        {

            Text = isNew ? ("Add New Instance (" + slotName + ")") : ("Edit Instance — " + currentCustomName + " (" + slotName + ")");

            Size = new Size(520, 385);

            StartPosition = FormStartPosition.CenterParent;

            FormBorderStyle = FormBorderStyle.FixedDialog;

            MaximizeBox = MinimizeBox = false;

            BackColor = Color.FromArgb(22, 23, 30);

            ForeColor = Color.White;

            Font = new Font("Segoe UI", 9.5f);



            _profiles = DeviceCatalog.GetAllProfiles();

            Color lblColor = Color.FromArgb(203, 213, 225), inputBg = Color.FromArgb(32, 34, 44);

            int y = 20;



            var lblName = new Label { Text = "Instance Name:", Location = new Point(22, y + 3), AutoSize = true, ForeColor = lblColor };

            _txtCustomName = new TextBox

            {

                Text = currentCustomName ?? slotName, Location = new Point(165, y), Width = 315,

                BackColor = inputBg, ForeColor = Color.White, BorderStyle = BorderStyle.FixedSingle, Font = new Font("Segoe UI", 10f, FontStyle.Bold)

            };

            y += 45;



            var lblRes = new Label { Text = "System Resources:", Location = new Point(22, y + 3), AutoSize = true, ForeColor = lblColor };

            var lblResVal = new Label

            {

                Text = "⚡ Shared Dynamic RAM & CPU Pool (Auto)",

                Location = new Point(165, y + 3),

                AutoSize = true,

                ForeColor = Color.FromArgb(52, 211, 153),

                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold)

            };

            y += 45;



            var lblPhone = new Label { Text = "Device Profile:", Location = new Point(22, y + 3), AutoSize = true, ForeColor = lblColor };

            _cmbProfiles = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Location = new Point(165, y), Width = 235, BackColor = inputBg, ForeColor = Color.White, FlatStyle = FlatStyle.Flat };

            _cmbProfiles.Items.AddRange(_profiles.Select(p => (object)p.DisplayName).ToArray());

            int profIdx = _profiles.FindIndex(p => string.Equals(p.Id, currentProfileId, StringComparison.OrdinalIgnoreCase));

            if (_cmbProfiles.Items.Count > 0) _cmbProfiles.SelectedIndex = Math.Max(0, profIdx);



            _btnRandomProfile = new Button

            {

                Text = "Random", Location = new Point(406, y - 1), Size = new Size(74, 26),

                BackColor = Color.FromArgb(79, 70, 229), ForeColor = Color.White, FlatStyle = FlatStyle.Flat,

                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold), Cursor = Cursors.Hand

            };

            _btnRandomProfile.FlatAppearance.BorderSize = 0;

            _btnRandomProfile.Click += delegate { if (_profiles.Count > 0) lock (Rng) _cmbProfiles.SelectedIndex = Rng.Next(_profiles.Count); };

            y += 45;



            var lblProxy = new Label { Text = "Proxy (Optional):", Location = new Point(22, y + 3), AutoSize = true, ForeColor = lblColor };

            _txtProxy = new TextBox

            {

                Text = MainForm.NormalizeProxyForComparison(currentProxy), Location = new Point(165, y), Width = 235,

                BackColor = inputBg, ForeColor = Color.White, BorderStyle = BorderStyle.FixedSingle, Font = new Font("Consolas", 9.5f)

            };

            _btnRandomProxy = new Button

            {

                Text = "Random", Location = new Point(406, y - 1), Size = new Size(74, 26),

                BackColor = Color.FromArgb(79, 70, 229), ForeColor = Color.White, FlatStyle = FlatStyle.Flat,

                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold), Cursor = Cursors.Hand

            };

            _btnRandomProxy.FlatAppearance.BorderSize = 0;

            _btnRandomProxy.Click += delegate

            {

                string rnd = FarmEngine.GetRandomProxy();

                if (!string.IsNullOrEmpty(rnd)) _txtProxy.Text = rnd;

                else MessageBox.Show(this, "No proxies found in proxies.txt.\r\nAdd proxies (one per line) in proxies.txt first.", "Proxy Pool Empty", MessageBoxButtons.OK, MessageBoxIcon.Information);

            };

            var lblProxyHint = new Label

            {

                Text = "Format: IP:Port (leave blank to auto-assign from proxies.txt).",

                Location = new Point(165, y + 28), Size = new Size(320, 32), ForeColor = Color.FromArgb(148, 163, 184), Font = new Font("Segoe UI", 8.2f)

            };

            y += 75;



            _btnSave = new Button

            {

                Text = isNew ? "Create Instance" : "Save Changes", DialogResult = DialogResult.OK, Location = new Point(235, y),

                Size = new Size(145, 36), BackColor = Color.FromArgb(37, 99, 235), ForeColor = Color.White,

                FlatStyle = FlatStyle.Flat, Font = new Font("Segoe UI", 9.5f, FontStyle.Bold), Cursor = Cursors.Hand

            };

            _btnSave.FlatAppearance.BorderSize = 0;

            _btnSave.Click += delegate

            {

                string cn = _txtCustomName.Text.Trim();

                InstanceCustomName = string.IsNullOrEmpty(cn) ? slotName : cn;

                SelectedRamMb = 0; // 0 = dynamic shared memory

                SelectedCores = 0; // 0 = dynamic shared all cores

                int pIdx = _cmbProfiles.SelectedIndex;

                SelectedProfileId = (pIdx >= 0 && pIdx < _profiles.Count) ? _profiles[pIdx].Id : "galaxy-tab-s6";

                SelectedProxy = _txtProxy.Text.Trim();

            };



            _btnCancel = new Button

            {

                Text = "Cancel", DialogResult = DialogResult.Cancel, Location = new Point(390, y),

                Size = new Size(90, 36), BackColor = Color.FromArgb(45, 48, 60), ForeColor = Color.White,

                FlatStyle = FlatStyle.Flat, Cursor = Cursors.Hand

            };

            _btnCancel.FlatAppearance.BorderSize = 0;



            if (!isNew)

            {

                var btnDeleteInDlg = new Button

                {

                    Text = "Delete Instance", Location = new Point(22, y), Size = new Size(130, 36),

                    BackColor = Color.FromArgb(153, 27, 27), ForeColor = Color.White, FlatStyle = FlatStyle.Flat,

                    Font = new Font("Segoe UI", 9f, FontStyle.Bold), Cursor = Cursors.Hand

                };

                btnDeleteInDlg.FlatAppearance.BorderSize = 0;

                btnDeleteInDlg.Click += delegate { DeleteRequested = true; DialogResult = DialogResult.Abort; Close(); };

                Controls.Add(btnDeleteInDlg);

            }



            Controls.AddRange(new Control[]

            {

                lblName, _txtCustomName, lblRes, lblResVal,

                lblPhone, _cmbProfiles, _btnRandomProfile, lblProxy, _txtProxy, _btnRandomProxy, lblProxyHint, _btnSave, _btnCancel

            });

            AcceptButton = _btnSave;

            CancelButton = _btnCancel;

        }

    }



    public class CloneDialog : Form

    {

        public string TargetSlotName { get; private set; }

        public string TargetCustomName { get; private set; }

        public bool CloneState { get; private set; }



        private TextBox _txtCustomName;

        private CheckBox _chkState;

        private Button _btnOk;

        private Button _btnCancel;



        public CloneDialog(string sourceDisplayName, string targetSlotName, string suggestedCustomName)

        {

            TargetSlotName = targetSlotName;

            Text = "Clone Instance — " + sourceDisplayName;

            Size = new Size(440, 235);

            StartPosition = FormStartPosition.CenterParent;

            FormBorderStyle = FormBorderStyle.FixedDialog;

            MaximizeBox = MinimizeBox = false;

            BackColor = Color.FromArgb(22, 23, 30);

            ForeColor = Color.White;

            Font = new Font("Segoe UI", 9.5f);



            var lbl = new Label

            {

                Text = string.Format("Clone '{0}' into slot {1} with a unique hardware identity:", sourceDisplayName, targetSlotName),

                Location = new Point(20, 16), Size = new Size(390, 28), ForeColor = Color.FromArgb(203, 213, 225)

            };

            var lblName = new Label { Text = "New Instance Name:", Location = new Point(20, 56), AutoSize = true };

            _txtCustomName = new TextBox

            {

                Text = suggestedCustomName, Location = new Point(160, 53), Width = 240,

                BackColor = Color.FromArgb(32, 34, 44), ForeColor = Color.White, BorderStyle = BorderStyle.FixedSingle

            };

            _chkState = new CheckBox

            {

                Text = "Keep downloaded game files & map cache (Instant copy)", Checked = true,

                Location = new Point(20, 95), Size = new Size(390, 30), ForeColor = Color.FromArgb(52, 211, 153)

            };



            _btnOk = new Button

            {

                Text = "Clone Instance", DialogResult = DialogResult.OK, Location = new Point(195, 142),

                Size = new Size(115, 34), BackColor = Color.FromArgb(37, 99, 235), ForeColor = Color.White,

                FlatStyle = FlatStyle.Flat, Font = new Font("Segoe UI", 9f, FontStyle.Bold), Cursor = Cursors.Hand

            };

            _btnOk.FlatAppearance.BorderSize = 0;

            _btnOk.Click += delegate

            {

                string cn = _txtCustomName.Text.Trim();

                TargetCustomName = string.IsNullOrEmpty(cn) ? targetSlotName : cn;

                CloneState = _chkState.Checked;

            };



            _btnCancel = new Button

            {

                Text = "Cancel", DialogResult = DialogResult.Cancel, Location = new Point(320, 142),

                Size = new Size(80, 34), BackColor = Color.FromArgb(45, 48, 60), ForeColor = Color.White,

                FlatStyle = FlatStyle.Flat, Cursor = Cursors.Hand

            };

            _btnCancel.FlatAppearance.BorderSize = 0;



            Controls.AddRange(new Control[] { lbl, lblName, _txtCustomName, _chkState, _btnOk, _btnCancel });

            AcceptButton = _btnOk;

            CancelButton = _btnCancel;

        }

    }

}

