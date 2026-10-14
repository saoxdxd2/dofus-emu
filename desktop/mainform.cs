using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using DofusFarm.Engine;
using DofusFarm.Hardware;
using DofusFarm.Native;

namespace DofusFarm.UI
{
    public class MainForm : Form
    {
        private FarmEngine _engine;
        private FarmWatchdog _watchdog;
        private PriorityGovernor _governor;
        private HardwareSpecs _specs;

        private Panel _headerPanel;
        private Label _titleLabel;
        private Label _specsLabel;
        private Label _lblActiveBadge;

        private Panel _toolbarPanel;
        private Button _btnLaunchFarm;
        private Button _btnStopAll;
        private Button _btnAddSlot;
        private Button _btnEditSelected;
        private Button _btnDeleteSelected;
        private Button _btnRelayout;
        private Button _btnCascade;

        private Label _lblGpu;
        private ComboBox _cmbGpu;
        private Label _lblRam;
        private ComboBox _cmbRam;
        private Label _lblCores;
        private ComboBox _cmbCores;
        private Button _btnApplyAllHw;
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

        private System.Windows.Forms.Timer _syncTimer;
        private string _lastStateSignature = "";
        private int _isSyncing = 0;

        private volatile bool _isClosing = false;
        private volatile bool _isFarmLaunching = false;
        private readonly HashSet<string> _inFlightOps = new HashSet<string>();
        private readonly object _opsLock = new object();
        private readonly object _logFileLock = new object();

        private bool TryBeginOp(string key)
        {
            lock (_opsLock)
            {
                if (_inFlightOps.Contains(key)) return false;
                _inFlightOps.Add(key);
                return true;
            }
        }

        private void EndOp(string key)
        {
            lock (_opsLock)
            {
                _inFlightOps.Remove(key);
            }
        }

        private void SafeInvoke(Action action)
        {
            if (this.IsDisposed) return;
            try
            {
                if (this.IsHandleCreated && !this.IsDisposed)
                {
                    this.Invoke(action);
                }
            }
            catch (ObjectDisposedException) { }
            catch (InvalidOperationException) { }
        }

        private void SafeBeginInvoke(Action action)
        {
            if (this.IsDisposed) return;
            try
            {
                if (this.IsHandleCreated && !this.IsDisposed)
                {
                    this.BeginInvoke(action);
                }
            }
            catch (ObjectDisposedException) { }
            catch (InvalidOperationException) { }
        }

        public MainForm()
        {
            InitializeComponent();
            InitializeEngine();
        }

        private void InitializeComponent()
        {
            this.Text = "Dofus Touch Farm Manager v2.3";
            this.Size = new Size(1220, 680);
            this.MinimumSize = new Size(1060, 520);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(18, 19, 24);
            this.ForeColor = Color.FromArgb(235, 235, 240);
            this.Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);
            this.DoubleBuffered = true;

            _cellBadgeFont = new Font("Segoe UI", 8.5f, FontStyle.Bold);
            _customNameFont = new Font("Segoe UI", 9.5f, FontStyle.Bold);

            string iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_icon.ico");
            if (File.Exists(iconPath))
            {
                try { this.Icon = new Icon(iconPath); } catch { }
            }

            // Top Header Panel
            _headerPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 68,
                BackColor = Color.FromArgb(24, 26, 34),
                Padding = new Padding(18, 8, 18, 8)
            };

            _titleLabel = new Label
            {
                Text = "DOFUS TOUCH FARM MANAGER",
                Font = new Font("Segoe UI", 13f, FontStyle.Bold),
                ForeColor = Color.White,
                AutoSize = true,
                Location = new Point(18, 10)
            };

            _specsLabel = new Label
            {
                Text = "Checking PC hardware configuration...",
                Font = new Font("Segoe UI", 9f),
                ForeColor = Color.FromArgb(148, 163, 184),
                AutoSize = true,
                Location = new Point(19, 38)
            };

            _lblActiveBadge = new Label
            {
                Text = "● 0 / 4 Running",
                Font = new Font("Segoe UI", 10.5f, FontStyle.Bold),
                ForeColor = Color.FromArgb(148, 163, 184),
                AutoSize = true,
                Anchor = AnchorStyles.Top | AnchorStyles.Right,
                Location = new Point(_headerPanel.Width - 175, 22)
            };

            _headerPanel.Controls.Add(_titleLabel);
            _headerPanel.Controls.Add(_specsLabel);
            _headerPanel.Controls.Add(_lblActiveBadge);

            // Clean Single-Row Toolbar Panel
            _toolbarPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 54,
                BackColor = Color.FromArgb(21, 22, 29),
                Padding = new Padding(18, 8, 18, 8)
            };

            int btnY = 10;
            int btnH = 34;

            _btnLaunchFarm = CreateButton("Launch All", Color.FromArgb(37, 99, 235), Color.FromArgb(59, 130, 246), Color.White, 112, btnH, true);
            _btnLaunchFarm.Location = new Point(16, btnY);
            _btnLaunchFarm.Click += OnLaunchFarmClick;

            _btnStopAll = CreateButton("Stop All", Color.FromArgb(185, 28, 28), Color.FromArgb(220, 38, 38), Color.White, 80, btnH, true);
            _btnStopAll.Location = new Point(134, btnY);
            _btnStopAll.Click += OnStopAllClick;

            _btnAddSlot = CreateButton("+ Add", Color.FromArgb(16, 150, 108), Color.FromArgb(16, 185, 129), Color.White, 76, btnH, true);
            _btnAddSlot.Location = new Point(220, btnY);
            _btnAddSlot.Click += OnAddSlotClick;

            _btnEditSelected = CreateButton("Edit", Color.FromArgb(40, 44, 58), Color.FromArgb(55, 62, 80), Color.White, 62, btnH, false);
            _btnEditSelected.Location = new Point(302, btnY);
            _btnEditSelected.Click += OnEditSelectedClick;

            _btnDeleteSelected = CreateButton("Delete", Color.FromArgb(153, 27, 27), Color.FromArgb(220, 38, 38), Color.White, 72, btnH, true);
            _btnDeleteSelected.Location = new Point(370, btnY);
            _btnDeleteSelected.Click += OnDeleteSelectedClick;

            _btnRelayout = CreateButton("Tile", Color.FromArgb(40, 44, 58), Color.FromArgb(55, 62, 80), Color.White, 56, btnH, false);
            _btnRelayout.Location = new Point(448, btnY);
            _btnRelayout.Click += OnRelayoutClick;

            _btnCascade = CreateButton("Cascade", Color.FromArgb(40, 44, 58), Color.FromArgb(55, 62, 80), Color.White, 68, btnH, false);
            _btnCascade.Location = new Point(510, btnY);
            _btnCascade.Click += OnCascadeClick;

            // Global GPU, RAM & CPU Allocation Controls on Right
            _lblGpu = new Label { Text = "GPU:", ForeColor = Color.FromArgb(180, 185, 195), AutoSize = true, Location = new Point(716, 17) };
            _cmbGpu = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 40),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 108,
                Location = new Point(750, 14)
            };
            _cmbGpu.Items.AddRange(new object[] {
                "Host (OpenGL)",
                "ANGLE (D3D11)",
                "SwiftShader"
            });
            _cmbGpu.SelectedIndex = 0;
            _cmbGpu.SelectedIndexChanged += OnGpuModeChanged;

            _lblRam = new Label { Text = "RAM:", ForeColor = Color.FromArgb(180, 185, 195), AutoSize = true, Location = new Point(866, 17) };
            _cmbRam = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 40),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 90,
                Location = new Point(904, 14)
            };
            _cmbRam.Items.AddRange(new object[] {
                "512 MB", "768 MB", "1024 MB", "1536 MB", "2048 MB",
                "3072 MB", "4096 MB", "6144 MB", "8192 MB", "12288 MB", "16384 MB"
            });
            _cmbRam.SelectedIndex = 2; // 1024 MB

            _lblCores = new Label { Text = "CPU:", ForeColor = Color.FromArgb(180, 185, 195), AutoSize = true, Location = new Point(1000, 17) };
            _cmbCores = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 40),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 76,
                Location = new Point(1034, 14)
            };
            _cmbCores.Items.AddRange(new object[] { "1 Core", "2 Cores", "3 Cores", "4 Cores", "6 Cores", "8 Cores" });
            _cmbCores.SelectedIndex = 0; // 1 Core

            _btnApplyAllHw = CreateButton("Apply All", Color.FromArgb(79, 70, 229), Color.FromArgb(99, 102, 241), Color.White, 84, btnH, true);
            _btnApplyAllHw.Location = new Point(1116, btnY);
            _btnApplyAllHw.Click += OnApplyAllHardwareClick;

            _toolbarPanel.Controls.Add(_btnLaunchFarm);
            _toolbarPanel.Controls.Add(_btnStopAll);
            _toolbarPanel.Controls.Add(_btnAddSlot);
            _toolbarPanel.Controls.Add(_btnEditSelected);
            _toolbarPanel.Controls.Add(_btnDeleteSelected);
            _toolbarPanel.Controls.Add(_btnRelayout);
            _toolbarPanel.Controls.Add(_btnCascade);
            _toolbarPanel.Controls.Add(_lblGpu);
            _toolbarPanel.Controls.Add(_cmbGpu);
            _toolbarPanel.Controls.Add(_lblRam);
            _toolbarPanel.Controls.Add(_cmbRam);
            _toolbarPanel.Controls.Add(_lblCores);
            _toolbarPanel.Controls.Add(_cmbCores);
            _toolbarPanel.Controls.Add(_btnApplyAllHw);

            // DataGridView for Instances
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
            _grid.ColumnHeadersDefaultCellStyle.ForeColor = Color.FromArgb(148, 163, 184);
            _grid.ColumnHeadersDefaultCellStyle.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            _grid.ColumnHeadersDefaultCellStyle.Padding = new Padding(8, 4, 8, 4);
            _grid.DefaultCellStyle.BackColor = Color.FromArgb(18, 19, 25);
            _grid.DefaultCellStyle.ForeColor = Color.FromArgb(240, 240, 245);
            _grid.DefaultCellStyle.SelectionBackColor = Color.FromArgb(30, 41, 59);
            _grid.DefaultCellStyle.SelectionForeColor = Color.White;
            _grid.DefaultCellStyle.Padding = new Padding(8, 4, 8, 4);
            _grid.AlternatingRowsDefaultCellStyle.BackColor = Color.FromArgb(22, 24, 32);

            _grid.Columns.Add("Status", "STATUS");
            _grid.Columns.Add("CustomName", "INSTANCE NAME");
            _grid.Columns.Add("Name", "SLOT");
            _grid.Columns.Add("Ram", "RAM");
            _grid.Columns.Add("Cores", "CPU CORES");
            _grid.Columns.Add("Device", "DEVICE PROFILE");
            _grid.Columns.Add("Proxy", "NETWORK");

            DataGridViewButtonColumn btnAction = new DataGridViewButtonColumn
            {
                Name = "Action",
                HeaderText = "ACTION",
                Text = "Launch",
                UseColumnTextForButtonValue = false,
                FlatStyle = FlatStyle.Flat
            };
            _grid.Columns.Add(btnAction);

            DataGridViewButtonColumn btnEdit = new DataGridViewButtonColumn
            {
                Name = "Edit",
                HeaderText = "EDIT",
                Text = "Edit",
                UseColumnTextForButtonValue = true,
                FlatStyle = FlatStyle.Flat
            };
            _grid.Columns.Add(btnEdit);

            DataGridViewButtonColumn btnClone = new DataGridViewButtonColumn
            {
                Name = "Clone",
                HeaderText = "CLONE",
                Text = "Clone",
                UseColumnTextForButtonValue = true,
                FlatStyle = FlatStyle.Flat
            };
            _grid.Columns.Add(btnClone);

            DataGridViewButtonColumn btnDelete = new DataGridViewButtonColumn
            {
                Name = "Delete",
                HeaderText = "DELETE",
                Text = "Delete",
                UseColumnTextForButtonValue = true,
                FlatStyle = FlatStyle.Flat
            };
            _grid.Columns.Add(btnDelete);

            _grid.Columns["Status"].Width = 100;
            _grid.Columns["CustomName"].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
            _grid.Columns["CustomName"].FillWeight = 115;
            _grid.Columns["CustomName"].MinimumWidth = 110;
            _grid.Columns["Name"].Width = 78;
            _grid.Columns["Ram"].Width = 76;
            _grid.Columns["Cores"].Width = 80;
            _grid.Columns["Device"].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
            _grid.Columns["Device"].FillWeight = 135;
            _grid.Columns["Device"].MinimumWidth = 125;
            _grid.Columns["Proxy"].Width = 138;
            _grid.Columns["Action"].Width = 78;
            _grid.Columns["Edit"].Width = 66;
            _grid.Columns["Clone"].Width = 66;
            _grid.Columns["Delete"].Width = 72;

            _rowMenu = new ContextMenuStrip();
            _rowMenu.BackColor = Color.FromArgb(26, 28, 37);
            _rowMenu.ForeColor = Color.White;
            _rowMenu.Items.Add("Launch / Stop Instance", null, delegate { TriggerSelectedRowAction("Action"); });
            _rowMenu.Items.Add("Edit Instance...", null, delegate { OnEditSelectedClick(null, EventArgs.Empty); });
            _rowMenu.Items.Add("Clone Instance...", null, delegate { TriggerSelectedRowAction("Clone"); });
            _rowMenu.Items.Add(new ToolStripSeparator());
            ToolStripItem delItem = _rowMenu.Items.Add("Delete Selected Instance", null, delegate { OnDeleteSelectedClick(null, EventArgs.Empty); });
            delItem.ForeColor = Color.FromArgb(248, 113, 113);
            ToolStripItem delAllItem = _rowMenu.Items.Add("Delete ALL Instances...", null, delegate { PromptAndDeleteAllInstances(); });
            delAllItem.ForeColor = Color.FromArgb(248, 113, 113);

            _grid.CellPainting += OnGridCellPainting;
            _grid.CellMouseClick += OnGridCellMouseClick;
            _grid.CellDoubleClick += OnGridCellDoubleClick;
            _grid.KeyDown += OnGridKeyDown;
            _grid.CellMouseEnter += OnGridCellMouseEnter;
            _grid.CellMouseLeave += delegate { _grid.Cursor = Cursors.Default; };

            // Clean Bottom Status Bar
            _statusPanel = new Panel
            {
                Dock = DockStyle.Bottom,
                Height = 36,
                BackColor = Color.FromArgb(24, 26, 34),
                Padding = new Padding(18, 6, 18, 6)
            };

            _lblStatusText = new Label
            {
                Text = "Ready — Double-click any instance or click 'Edit' to customize its Name, RAM, or CPU Cores.",
                Font = new Font("Segoe UI", 9f),
                ForeColor = Color.FromArgb(148, 163, 184),
                AutoSize = true,
                Location = new Point(18, 9)
            };

            Label lblFocus = new Label
            {
                Text = "Quick Focus:",
                ForeColor = Color.FromArgb(148, 163, 184),
                AutoSize = true,
                Anchor = AnchorStyles.Top | AnchorStyles.Right,
                Location = new Point(_statusPanel.Width - 260, 9)
            };

            _btnFocus1 = CreateButton("#1", Color.FromArgb(34, 38, 50), Color.FromArgb(51, 65, 85), Color.FromArgb(147, 197, 253), 38, 24, false);
            _btnFocus1.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnFocus1.Location = new Point(_statusPanel.Width - 175, 6);
            _btnFocus1.Click += delegate { FocusSlotByIndex(0); };

            _btnFocus2 = CreateButton("#2", Color.FromArgb(34, 38, 50), Color.FromArgb(51, 65, 85), Color.FromArgb(147, 197, 253), 38, 24, false);
            _btnFocus2.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnFocus2.Location = new Point(_statusPanel.Width - 133, 6);
            _btnFocus2.Click += delegate { FocusSlotByIndex(1); };

            _btnFocus3 = CreateButton("#3", Color.FromArgb(34, 38, 50), Color.FromArgb(51, 65, 85), Color.FromArgb(147, 197, 253), 38, 24, false);
            _btnFocus3.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnFocus3.Location = new Point(_statusPanel.Width - 91, 6);
            _btnFocus3.Click += delegate { FocusSlotByIndex(2); };

            _btnFocus4 = CreateButton("#4", Color.FromArgb(34, 38, 50), Color.FromArgb(51, 65, 85), Color.FromArgb(147, 197, 253), 38, 24, false);
            _btnFocus4.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnFocus4.Location = new Point(_statusPanel.Width - 49, 6);
            _btnFocus4.Click += delegate { FocusSlotByIndex(3); };

            _statusPanel.Controls.Add(_lblStatusText);
            _statusPanel.Controls.Add(lblFocus);
            _statusPanel.Controls.Add(_btnFocus1);
            _statusPanel.Controls.Add(_btnFocus2);
            _statusPanel.Controls.Add(_btnFocus3);
            _statusPanel.Controls.Add(_btnFocus4);

            this.Controls.Add(_grid);
            this.Controls.Add(_statusPanel);
            this.Controls.Add(_toolbarPanel);
            this.Controls.Add(_headerPanel);

            // Real-time background status sync timer (4s interval, only redraws when state changes)
            _syncTimer = new System.Windows.Forms.Timer { Interval = 4000 };
            _syncTimer.Tick += OnSyncTimerTick;
            _syncTimer.Start();
        }

        private void FocusSlotByIndex(int rowIndex)
        {
            if (rowIndex >= 0 && rowIndex < _grid.Rows.Count)
            {
                InstanceModel inst = _grid.Rows[rowIndex].Tag as InstanceModel;
                if (inst != null)
                {
                    _engine.FocusInstance(inst.Port);
                    return;
                }
            }
            _engine.FocusInstance(5554 + (rowIndex * 2));
        }

        private void OnGridCellMouseEnter(object sender, DataGridViewCellEventArgs e)
        {
            if (e.RowIndex < 0 || e.ColumnIndex < 0)
            {
                _grid.Cursor = Cursors.Default;
                return;
            }
            string colName = _grid.Columns[e.ColumnIndex].Name;
            if (colName == "Action" || colName == "Edit" || colName == "Clone" || colName == "Delete")
            {
                _grid.Cursor = Cursors.Hand;
                return;
            }
            if (colName == "Status")
            {
                InstanceModel inst = _grid.Rows[e.RowIndex].Tag as InstanceModel;
                _grid.Cursor = (inst != null && inst.IsRunning) ? Cursors.Hand : Cursors.Default;
                return;
            }
            _grid.Cursor = Cursors.Default;
        }

        private Button CreateButton(string text, Color baseColor, Color hoverColor, Color foreColor, int width, int height, bool bold)
        {
            Button btn = new Button
            {
                Text = text,
                BackColor = baseColor,
                ForeColor = foreColor,
                FlatStyle = FlatStyle.Flat,
                Width = width,
                Height = height,
                Font = new Font("Segoe UI", 9f, bold ? FontStyle.Bold : FontStyle.Regular),
                Cursor = Cursors.Hand
            };
            btn.FlatAppearance.BorderSize = 0;
            btn.MouseEnter += delegate { if (btn.Enabled) btn.BackColor = hoverColor; };
            btn.MouseLeave += delegate { if (btn.Enabled) btn.BackColor = baseColor; };
            return btn;
        }

        /// <summary>
        /// Custom GDI+ renderer for commercial-grade pill status badges and action buttons in the DataGridView.
        /// Uses cached Font instances (_cellBadgeFont) to guarantee zero GDI handle leaks.
        /// </summary>
        private void OnGridCellPainting(object sender, DataGridViewCellPaintingEventArgs e)
        {
            if (e.RowIndex < 0 || e.ColumnIndex < 0) return;

            string colName = _grid.Columns[e.ColumnIndex].Name;
            if (colName != "Status" && colName != "Action" && colName != "Edit" && colName != "Clone" && colName != "Delete")
                return;

            e.PaintBackground(e.ClipBounds, true);
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            DataGridViewRow row = _grid.Rows[e.RowIndex];
            InstanceModel inst = row.Tag as InstanceModel;
            bool isRunning = inst != null && inst.IsRunning;

            if (colName == "Status")
            {
                Rectangle pillRect = new Rectangle(e.CellBounds.X + 8, e.CellBounds.Y + 9, e.CellBounds.Width - 16, e.CellBounds.Height - 18);
                Color pillBg = isRunning ? Color.FromArgb(16, 52, 42) : Color.FromArgb(30, 33, 44);
                Color pillText = isRunning ? Color.FromArgb(52, 211, 153) : Color.FromArgb(148, 163, 184);
                string label = isRunning ? "● Running" : "● Stopped";

                using (SolidBrush bg = new SolidBrush(pillBg))
                {
                    g.FillRectangle(bg, pillRect);
                }
                TextRenderer.DrawText(g, label, _cellBadgeFont, pillRect, pillText,
                    TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
                e.Handled = true;
                return;
            }

            // Action / Edit / Clone / Delete buttons
            Rectangle btnRect = new Rectangle(e.CellBounds.X + 6, e.CellBounds.Y + 8, e.CellBounds.Width - 12, e.CellBounds.Height - 16);
            Color btnBg = Color.FromArgb(38, 42, 56);
            Color btnBorder = Color.FromArgb(55, 62, 82);
            Color btnFg = Color.White;
            string btnText = (e.Value ?? "").ToString();

            if (colName == "Action")
            {
                if (isRunning)
                {
                    btnBg = Color.FromArgb(153, 27, 27);
                    btnBorder = Color.FromArgb(220, 38, 38);
                    btnText = "Stop";
                }
                else
                {
                    btnBg = Color.FromArgb(29, 78, 216);
                    btnBorder = Color.FromArgb(59, 130, 246);
                    btnText = "Launch";
                }
            }
            else if (colName == "Edit")
            {
                btnBg = Color.FromArgb(30, 41, 59);
                btnBorder = Color.FromArgb(71, 85, 105);
                btnFg = Color.FromArgb(147, 197, 253);
                btnText = "Edit";
            }
            else if (colName == "Clone")
            {
                btnBg = Color.FromArgb(30, 41, 59);
                btnBorder = Color.FromArgb(71, 85, 105);
                btnFg = Color.FromArgb(251, 191, 36);
                btnText = "Clone";
            }
            else if (colName == "Delete")
            {
                btnBg = Color.FromArgb(45, 26, 34);
                btnBorder = Color.FromArgb(127, 29, 29);
                btnFg = Color.FromArgb(248, 113, 113);
                btnText = "Delete";
            }

            using (SolidBrush bg = new SolidBrush(btnBg))
            using (Pen pen = new Pen(btnBorder, 1f))
            {
                g.FillRectangle(bg, btnRect);
                g.DrawRectangle(pen, btnRect);
            }
            TextRenderer.DrawText(g, btnText, _cellBadgeFont, btnRect, btnFg,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
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
                if (gpu == "angle_indirect") _cmbGpu.SelectedIndex = 1;
                else if (gpu == "swiftshader_indirect") _cmbGpu.SelectedIndex = 2;
                else _cmbGpu.SelectedIndex = 0;
            }
            catch { }
            finally { _suppressGpuEvent = false; }

            _watchdog = new FarmWatchdog(_engine);
            _watchdog.OnLog += Log;
            _watchdog.Start();

            _governor = new PriorityGovernor();
            _governor.OnLog += Log;
            _governor.Start();

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
                            if (_specs.HostRamGB >= 15.5)
                            {
                                _cmbRam.SelectedIndex = 3; // 1536 MB
                                _cmbCores.SelectedIndex = 1; // 2 Cores
                            }
                        }
                        RefreshGrid(true);
                    });
                }
                catch { }
            });
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            RefreshGrid(true);
        }

        private void OnSyncTimerTick(object sender, EventArgs e)
        {
            if (_isClosing || _isFarmLaunching) return;
            RefreshGrid(false);
        }

        private void Log(string msg)
        {
            if (string.IsNullOrEmpty(msg)) return;
            try
            {
                lock (_logFileLock)
                {
                    string logFile = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "DofusFarm.log");
                    File.AppendAllText(logFile, msg + "\r\n");
                }
            }
            catch { }

            if (this.IsDisposed || _isClosing || !this.IsHandleCreated) return;
            if (!msg.Contains("[GOVERNOR]") && !msg.Contains("[SECURITY]"))
            {
                SafeBeginInvoke(delegate
                {
                    if (_lblStatusText != null && !this.IsDisposed)
                    {
                        string clean = Regex.Replace(msg, @"^\[\d{2}:\d{2}:\d{2}\]\s*", "");
                        _lblStatusText.Text = clean;
                    }
                });
            }
        }

        private int GetSelectedRam()
        {
            string s = _cmbRam.SelectedItem != null ? _cmbRam.SelectedItem.ToString() : "1024 MB";
            Match m = Regex.Match(s, @"(\d+)");
            return m.Success ? int.Parse(m.Groups[1].Value) : 1024;
        }

        private int GetSelectedCores()
        {
            string s = _cmbCores.SelectedItem != null ? _cmbCores.SelectedItem.ToString() : "1 Core";
            Match m = Regex.Match(s, @"(\d+)");
            return m.Success ? int.Parse(m.Groups[1].Value) : 1;
        }

        private void RefreshGrid(bool forceRedraw = true)
        {
            if (Interlocked.CompareExchange(ref _isSyncing, 1, 0) != 0 && !forceRedraw) return;

            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    if (_isClosing) return;
                    List<InstanceModel> list = _engine.GetInstances();

                    // Build lightweight state signature so periodic timer only redraws when state actually changes
                    System.Text.StringBuilder sigBuilder = new System.Text.StringBuilder();
                    foreach (InstanceModel im in list)
                    {
                        sigBuilder.Append(im.Name).Append(':')
                                  .Append(im.CustomName).Append(':')
                                  .Append(im.IsRunning ? '1' : '0').Append(':')
                                  .Append(im.RamMb).Append(':')
                                  .Append(im.Cores).Append(':')
                                  .Append(im.PhoneModelId).Append(':')
                                  .Append(im.Proxy).Append(';');
                    }
                    string newSig = sigBuilder.ToString();
                    if (!forceRedraw && string.Equals(newSig, _lastStateSignature, StringComparison.Ordinal))
                    {
                        return;
                    }
                    _lastStateSignature = newSig;

                    SafeInvoke(delegate
                    {
                        string selectedName = null;
                        if (_grid.SelectedRows.Count > 0 && _grid.SelectedRows[0].Tag is InstanceModel)
                        {
                            selectedName = ((InstanceModel)_grid.SelectedRows[0].Tag).Name;
                        }

                        _grid.Rows.Clear();
                        int runningCount = 0;
                        foreach (InstanceModel inst in list)
                        {
                            if (inst.IsRunning) runningCount++;
                            string proxyDisplay = inst.Proxy ?? "127.0.0.1:8880";
                            if (proxyDisplay.Contains("127.0.0.1") || proxyDisplay.Contains("Accelerator"))
                                proxyDisplay = "Built-in Accelerator";
                            else
                                proxyDisplay = proxyDisplay.Replace("[🌐] ", "");

                            int rowIdx = _grid.Rows.Add(
                                inst.IsRunning ? "Running" : "Stopped",
                                inst.CustomName ?? inst.DisplayName,
                                inst.Name,
                                inst.RamMb + " MB",
                                inst.Cores + (inst.Cores == 1 ? " Core" : " Cores"),
                                inst.PhoneModelName ?? "Galaxy Tab S6",
                                proxyDisplay,
                                inst.IsRunning ? "Stop" : "Launch",
                                "Edit",
                                "Clone",
                                "Delete"
                            );

                            DataGridViewRow row = _grid.Rows[rowIdx];
                            row.Tag = inst;
                            row.Cells["CustomName"].Style.Font = _customNameFont;
                            row.Cells["CustomName"].Style.ForeColor = Color.White;
                            row.Cells["Name"].Style.ForeColor = Color.FromArgb(148, 163, 184);
                            row.Cells["Device"].Style.ForeColor = Color.FromArgb(226, 232, 240);
                            row.Cells["Proxy"].Style.ForeColor = Color.FromArgb(148, 163, 184);
                            if (inst.IsRunning)
                            {
                                row.Cells["Status"].ToolTipText = "Click to bring this running instance window to front";
                            }

                            if (!string.IsNullOrEmpty(selectedName) && string.Equals(inst.Name, selectedName, StringComparison.OrdinalIgnoreCase))
                            {
                                row.Selected = true;
                            }
                        }

                        int count = list.Count > 0 ? list.Count : 4;
                        _btnLaunchFarm.Text = string.Format("Launch All ({0}x)", count);

                        if (runningCount > 0)
                        {
                            _lblActiveBadge.Text = string.Format("● {0} / {1} Running", runningCount, list.Count);
                            _lblActiveBadge.ForeColor = Color.FromArgb(52, 211, 153);
                        }
                        else
                        {
                            _lblActiveBadge.Text = string.Format("● 0 / {0} Running", list.Count);
                            _lblActiveBadge.ForeColor = Color.FromArgb(148, 163, 184);
                        }
                    });
                }
                catch { }
                finally
                {
                    Interlocked.Exchange(ref _isSyncing, 0);
                }
            });
        }

        private void OnApplyAllHardwareClick(object sender, EventArgs e)
        {
            int ram = GetSelectedRam();
            int cores = GetSelectedCores();

            _btnApplyAllHw.Enabled = false;
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    int updated = _engine.ApplyHardwareToAllInstances(ram, cores);
                    Log(string.Format("Applied {0} MB RAM and {1} CPU Core(s) across {2} stopped instance(s).", ram, cores, updated));
                    RefreshGrid(true);
                }
                catch (Exception ex)
                {
                    Log("Error applying hardware settings: " + ex.Message);
                }
                finally
                {
                    SafeBeginInvoke(delegate { _btnApplyAllHw.Enabled = true; });
                }
            });
        }

        private void OnLaunchFarmClick(object sender, EventArgs e)
        {
            if (_isFarmLaunching) return;
            _isFarmLaunching = true;
            _btnLaunchFarm.Enabled = false;

            int defaultRam = GetSelectedRam();
            int defaultCores = GetSelectedCores();

            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    List<InstanceModel> existingInsts = _engine.GetInstances();
                    if (existingInsts.Count == 0)
                    {
                        for (int i = 1; i <= 4; i++)
                        {
                            if (_isClosing || !_isFarmLaunching) return;
                            string slotName = string.Format("dofus-{0:D2}", i);
                            _engine.ProvisionInstance(slotName, defaultRam, defaultCores, null, "Instance #" + i, "");
                        }
                        existingInsts = _engine.GetInstances();
                    }

                    Log(string.Format("Launching {0} instance(s)...", existingInsts.Count));
                    try { _engine.Adb("", "start-server", 6000); } catch { }

                    foreach (InstanceModel im in existingInsts)
                    {
                        if (_isClosing || !_isFarmLaunching) return;
                        if (im.IsRunning) continue;
                        _engine.LaunchInstance(
                            im.Name,
                            im.Port,
                            im.RamMb > 0 ? im.RamMb : defaultRam,
                            im.Cores > 0 ? im.Cores : defaultCores,
                            im.Proxy);
                        Thread.Sleep(1500);
                    }

                    Thread.Sleep(2500);
                    if (_isClosing || !_isFarmLaunching) return;
                    SafeInvoke(delegate
                    {
                        RefreshGrid(true);
                        OnRelayoutClick(null, null);
                    });

                    Thread.Sleep(4000);
                    if (_isClosing || !_isFarmLaunching) return;
                    SafeInvoke(delegate
                    {
                        RefreshGrid(true);
                        OnRelayoutClick(null, null);
                    });
                }
                catch (Exception ex)
                {
                    Log("Launch error: " + ex.Message);
                }
                finally
                {
                    _isFarmLaunching = false;
                    SafeBeginInvoke(delegate
                    {
                        _btnLaunchFarm.Enabled = true;
                    });
                }
            });
        }

        private void OnStopAllClick(object sender, EventArgs e)
        {
            _isFarmLaunching = false;
            _btnStopAll.Enabled = false;
            Log("Stopping all running instances...");
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    _engine.StopAll();
                    Thread.Sleep(500);
                    SafeInvoke(delegate
                    {
                        RefreshGrid(true);
                    });
                }
                catch (Exception ex)
                {
                    Log("Error stopping instances: " + ex.Message);
                }
                finally
                {
                    SafeBeginInvoke(delegate
                    {
                        _btnStopAll.Enabled = true;
                    });
                }
            });
        }

        private void OnRelayoutClick(object sender, EventArgs e)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    List<int> pids = new List<int>();
                    foreach (InstanceModel inst in _engine.GetInstances())
                    {
                        if (inst.IsRunning && inst.Pid > 0) pids.Add(inst.Pid);
                    }

                    if (pids.Count == 0)
                    {
                        Process[] procs = Process.GetProcessesByName("qemu-system-x86_64");
                        try
                        {
                            foreach (Process p in procs) pids.Add(p.Id);
                        }
                        finally
                        {
                            foreach (Process p in procs) p.Dispose();
                        }
                    }

                    if (pids.Count > 0)
                    {
                        Win32.ApplyFarmLayout(pids, true);
                        Log(string.Format("Tiled {0} active window(s).", pids.Count));
                    }
                }
                catch { }
            });
        }

        private void OnCascadeClick(object sender, EventArgs e)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    List<int> pids = new List<int>();
                    foreach (InstanceModel inst in _engine.GetInstances())
                    {
                        if (inst.IsRunning && inst.Pid > 0) pids.Add(inst.Pid);
                    }

                    if (pids.Count == 0)
                    {
                        Process[] procs = Process.GetProcessesByName("qemu-system-x86_64");
                        try
                        {
                            foreach (Process p in procs) pids.Add(p.Id);
                        }
                        finally
                        {
                            foreach (Process p in procs) p.Dispose();
                        }
                    }

                    if (pids.Count > 0)
                    {
                        Win32.ApplyCascadeLayout(pids);
                        Log(string.Format("Cascaded {0} active window(s).", pids.Count));
                    }
                }
                catch { }
            });
        }

        private void OnAddSlotClick(object sender, EventArgs e)
        {
            string nextSlot = _engine.GetNextAvailableSlot();
            int slotNum = 1;
            Match m = Regex.Match(nextSlot, @"(\d+)$");
            if (m.Success) int.TryParse(m.Groups[1].Value, out slotNum);

            string defaultCustomName = "Instance #" + slotNum;
            int defaultRam = GetSelectedRam();
            int defaultCores = GetSelectedCores();
            DeviceProfile defProfile = DeviceCatalog.GetDefaultProfileForIndex(slotNum);

            using (InstanceEditorDialog dlg = new InstanceEditorDialog(true, nextSlot, defaultCustomName, defaultRam, defaultCores, defProfile.Id, ""))
            {
                if (dlg.ShowDialog(this) == DialogResult.OK)
                {
                    string customName = dlg.InstanceCustomName;
                    int ram = dlg.SelectedRamMb;
                    int cores = dlg.SelectedCores;
                    string profileId = dlg.SelectedProfileId;
                    string proxy = dlg.SelectedProxy;

                    ThreadPool.QueueUserWorkItem(delegate
                    {
                        try
                        {
                            _engine.ProvisionInstance(nextSlot, ram, cores, profileId, customName, proxy);
                            RefreshGrid(true);
                        }
                        catch (Exception ex)
                        {
                            Log("Error adding instance: " + ex.Message);
                        }
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
            InstanceModel inst = _grid.SelectedRows[0].Tag as InstanceModel;
            if (inst != null)
            {
                OpenEditDialogForInstance(inst);
            }
        }

        private static string NormalizeProxyForComparison(string raw)
        {
            string s = (raw ?? "").Trim();
            if (s.StartsWith("[🌐] ")) s = s.Substring(5).Trim();
            if (s.StartsWith("[⚡]") || s.Contains("127.0.0.1:8880") || s.Contains("Accelerator")) return "";
            return s;
        }

        private void OnGpuModeChanged(object sender, EventArgs e)
        {
            if (_suppressGpuEvent || _engine == null) return;
            int idx = _cmbGpu.SelectedIndex;
            string mode = (idx == 1) ? "angle_indirect" : (idx == 2 ? "swiftshader_indirect" : "host");
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    _engine.SetConfiguredGpuMode(mode);
                }
                catch { }
            });
        }

        private void OnDeleteSelectedClick(object sender, EventArgs e)
        {
            if (_grid.SelectedRows.Count == 0)
            {
                DialogResult drAll = MessageBox.Show(
                    "No single row is selected.\n\nWould you like to delete ALL instances and reset the farm?",
                    "Delete Instances",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning);
                if (drAll == DialogResult.Yes)
                {
                    PromptAndDeleteAllInstances();
                }
                return;
            }

            InstanceModel inst = _grid.SelectedRows[0].Tag as InstanceModel;
            if (inst != null)
            {
                PromptAndDeleteInstance(inst);
            }
        }

        private void PromptAndDeleteInstance(InstanceModel inst)
        {
            if (inst == null) return;
            DialogResult confirm = MessageBox.Show(
                string.Format("Delete instance '{0}' ({1}) and permanently remove its virtual disk?", inst.CustomName ?? inst.Name, inst.Name),
                "Confirm Delete Instance",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Warning);

            if (confirm == DialogResult.Yes)
            {
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
                    catch (Exception ex)
                    {
                        Log(string.Format("Error deleting {0}: {1}", nameToDelete, ex.Message));
                    }
                    finally
                    {
                        EndOp(nameToDelete);
                    }
                });
            }
        }

        private void PromptAndDeleteAllInstances()
        {
            DialogResult confirm = MessageBox.Show(
                "Are you sure you want to stop and DELETE ALL instances (dofus-01..N)?\n\nYour Golden Master template is preserved so you can re-add clean instances anytime.",
                "Confirm Delete All Instances",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Warning);

            if (confirm == DialogResult.Yes)
            {
                _btnDeleteSelected.Enabled = false;
                ThreadPool.QueueUserWorkItem(delegate
                {
                    try
                    {
                        int count = _engine.DeleteAllInstances();
                        Log(string.Format("Deleted {0} instance(s).", count));
                        Thread.Sleep(400);
                        RefreshGrid(true);
                    }
                    catch (Exception ex)
                    {
                        Log("Error deleting all instances: " + ex.Message);
                    }
                    finally
                    {
                        SafeBeginInvoke(delegate { _btnDeleteSelected.Enabled = true; });
                    }
                });
            }
        }

        private void TriggerSelectedRowAction(string colName)
        {
            if (_grid.SelectedRows.Count == 0) return;
            int rowIdx = _grid.SelectedRows[0].Index;
            int colIdx = _grid.Columns[colName].Index;
            HandleGridAction(rowIdx, colIdx);
        }

        private void OnGridKeyDown(object sender, KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Delete && _grid.SelectedRows.Count > 0)
            {
                e.Handled = true;
                InstanceModel inst = _grid.SelectedRows[0].Tag as InstanceModel;
                if (inst != null)
                {
                    PromptAndDeleteInstance(inst);
                }
            }
        }

        private void OpenEditDialogForInstance(InstanceModel inst)
        {
            if (inst == null) return;
            using (InstanceEditorDialog dlg = new InstanceEditorDialog(
                false,
                inst.Name,
                inst.CustomName ?? inst.DisplayName,
                inst.RamMb,
                inst.Cores,
                inst.PhoneModelId,
                inst.Proxy))
            {
                DialogResult result = dlg.ShowDialog(this);
                if (dlg.DeleteRequested || result == DialogResult.Abort)
                {
                    PromptAndDeleteInstance(inst);
                    return;
                }
                if (result == DialogResult.OK)
                {
                    string slotName = inst.Name;
                    string slotSerial = inst.Serial;
                    int slotPort = inst.Port;
                    bool wasRunning = inst.IsRunning;

                    string customName = dlg.InstanceCustomName;
                    int ram = dlg.SelectedRamMb;
                    int cores = dlg.SelectedCores;
                    string profileId = dlg.SelectedProfileId;
                    string proxy = dlg.SelectedProxy;

                    bool hwOrNetChanged = (ram != inst.RamMb) ||
                                          (cores != inst.Cores) ||
                                          !string.Equals(profileId, inst.PhoneModelId, StringComparison.OrdinalIgnoreCase) ||
                                          !string.Equals(NormalizeProxyForComparison(proxy), NormalizeProxyForComparison(inst.Proxy), StringComparison.OrdinalIgnoreCase);

                    bool restartNow = false;
                    if (wasRunning && hwOrNetChanged)
                    {
                        DialogResult dr = MessageBox.Show(
                            string.Format("'{0}' is currently running.\n\nWould you like to restart it now so the new RAM / CPU / device / network settings take effect immediately?", customName),
                            "Apply Changes Now?",
                            MessageBoxButtons.YesNo,
                            MessageBoxIcon.Question);
                        restartNow = (dr == DialogResult.Yes);
                    }

                    ThreadPool.QueueUserWorkItem(delegate
                    {
                        try
                        {
                            if (restartNow)
                            {
                                _engine.StopInstance(slotSerial, slotName);
                                Thread.Sleep(800);
                            }
                            _engine.UpdateInstanceConfig(slotName, customName, ram, cores, profileId, proxy);
                            if (restartNow)
                            {
                                _engine.LaunchInstance(slotName, slotPort, ram, cores, proxy);
                                Thread.Sleep(1500);
                            }
                            RefreshGrid(true);
                        }
                        catch (Exception ex)
                        {
                            Log("Error updating instance: " + ex.Message);
                        }
                    });
                }
            }
        }

        private void OnGridCellMouseClick(object sender, DataGridViewCellMouseEventArgs e)
        {
            if (e.RowIndex < 0 || e.RowIndex >= _grid.Rows.Count) return;
            if (e.Button == MouseButtons.Right)
            {
                _grid.ClearSelection();
                _grid.Rows[e.RowIndex].Selected = true;
                if (_rowMenu != null)
                {
                    _rowMenu.Show(Cursor.Position);
                }
                return;
            }
            if (e.Button != MouseButtons.Left || e.ColumnIndex < 0) return;
            HandleGridAction(e.RowIndex, e.ColumnIndex);
        }

        private void HandleGridAction(int rowIndex, int columnIndex)
        {
            if (rowIndex < 0 || rowIndex >= _grid.Rows.Count || columnIndex < 0) return;
            DataGridViewRow row = _grid.Rows[rowIndex];
            if (row == null) return;
            InstanceModel inst = row.Tag as InstanceModel;
            if (inst == null) return;
            DataGridViewCellEventArgs e = new DataGridViewCellEventArgs(columnIndex, rowIndex);

            // 0. Click on Running Status pill -> Bring emulator window to front
            if (e.ColumnIndex == _grid.Columns["Status"].Index)
            {
                if (inst.IsRunning)
                {
                    _engine.FocusInstance(inst.Port);
                }
                return;
            }

            // 1. Launch / Stop
            if (e.ColumnIndex == _grid.Columns["Action"].Index)
            {
                if (!TryBeginOp(inst.Name)) return;
                ThreadPool.QueueUserWorkItem(delegate
                {
                    try
                    {
                        if (_isClosing) return;
                        if (inst.IsRunning)
                        {
                            _engine.StopInstance(inst.Serial, inst.Name);
                        }
                        else
                        {
                            _engine.LaunchInstance(inst.Name, inst.Port, inst.RamMb, inst.Cores, inst.Proxy);
                        }
                        Thread.Sleep(800);
                        RefreshGrid(true);
                    }
                    catch (Exception ex)
                    {
                        Log(string.Format("Error toggling {0}: {1}", inst.Name, ex.Message));
                    }
                    finally
                    {
                        EndOp(inst.Name);
                    }
                });
            }
            // 2. Edit Instance
            else if (e.ColumnIndex == _grid.Columns["Edit"].Index)
            {
                OpenEditDialogForInstance(inst);
            }
            // 3. Clone Instance
            else if (e.ColumnIndex == _grid.Columns["Clone"].Index)
            {
                string nextSlot = _engine.GetNextAvailableSlot();
                string suggestedCustom = (inst.CustomName ?? inst.Name) + " (Copy)";
                using (CloneDialog dlg = new CloneDialog(inst.CustomName ?? inst.Name, nextSlot, suggestedCustom))
                {
                    if (dlg.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(dlg.TargetSlotName))
                    {
                        string srcName = inst.Name;
                        string targetSlot = dlg.TargetSlotName;
                        string targetCustom = dlg.TargetCustomName;
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
                            catch (Exception ex)
                            {
                                Log(string.Format("Error cloning {0}: {1}", srcName, ex.Message));
                            }
                            finally
                            {
                                EndOp(targetSlot);
                            }
                        });
                    }
                }
            }
            // 4. Delete Instance
            else if (e.ColumnIndex == _grid.Columns["Delete"].Index)
            {
                PromptAndDeleteInstance(inst);
            }
        }

        private void OnGridCellDoubleClick(object sender, DataGridViewCellEventArgs e)
        {
            if (e.RowIndex < 0 || e.RowIndex >= _grid.Rows.Count) return;
            DataGridViewRow row = _grid.Rows[e.RowIndex];
            if (row == null) return;
            InstanceModel inst = row.Tag as InstanceModel;
            if (inst == null) return;

            OpenEditDialogForInstance(inst);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (e.CloseReason == CloseReason.UserClosing && !_isClosing)
            {
                int activeCount = 0;
                foreach (DataGridViewRow r in _grid.Rows)
                {
                    InstanceModel im = r.Tag as InstanceModel;
                    if (im != null && im.IsRunning) activeCount++;
                }

                if (activeCount > 0)
                {
                    DialogResult dr = MessageBox.Show(
                        string.Format("You have {0} game instance(s) running.\n\nYES - keep them running in the background (recommended): the next Launch attaches instantly, no 60-80s reboot.\nNO  - shut every instance down now.", activeCount),
                        "Keep Instances Running?",
                        MessageBoxButtons.YesNo,
                        MessageBoxIcon.Question);
                    _keepInstancesOnExit = (dr == DialogResult.Yes);
                }
            }

            _isClosing = true;
            _isFarmLaunching = false;

            if (_lblStatusText != null)
            {
                _lblStatusText.Text = _keepInstancesOnExit
                    ? "Detaching from running instances (they keep playing in the background)..."
                    : "Saving instance snapshots and shutting down cleanly...";
                Application.DoEvents();
            }

            if (_syncTimer != null)
            {
                try { _syncTimer.Stop(); _syncTimer.Dispose(); } catch { }
            }
            if (_engine != null) _engine.OnLog -= Log;
            if (_watchdog != null)
            {
                _watchdog.OnLog -= Log;
                _watchdog.Stop();
            }
            if (_governor != null)
            {
                _governor.OnLog -= Log;
                _governor.Stop();
            }
            if (_engine != null)
            {
                if (_keepInstancesOnExit)
                {
                    Log("Instances left running on purpose - next Launch will attach to them instantly.");
                }
                else
                {
                    _engine.StopAll();
                }
            }
            base.OnFormClosing(e);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _isClosing = true;
                if (_cellBadgeFont != null)
                {
                    try { _cellBadgeFont.Dispose(); } catch { }
                    _cellBadgeFont = null;
                }
                if (_customNameFont != null)
                {
                    try { _customNameFont.Dispose(); } catch { }
                    _customNameFont = null;
                }
                if (this.Icon != null)
                {
                    try { this.Icon.Dispose(); } catch { }
                }
            }
            base.Dispose(disposing);
        }
    }

    public class InstanceEditorDialog : Form
    {
        public string InstanceCustomName { get; private set; }
        public int SelectedRamMb { get; private set; }
        public int SelectedCores { get; private set; }
        public string SelectedProfileId { get; private set; }
        public string SelectedProxy { get; private set; }
        public bool DeleteRequested { get; private set; }

        private TextBox _txtCustomName;
        private ComboBox _cmbRam;
        private ComboBox _cmbCores;
        private ComboBox _cmbProfiles;
        private Button _btnRandomProfile;
        private TextBox _txtProxy;
        private Button _btnSave;
        private Button _btnCancel;

        private readonly List<DeviceProfile> _profiles;

        public InstanceEditorDialog(bool isNew, string slotName, string currentCustomName, int currentRamMb, int currentCores, string currentProfileId, string currentProxy)
        {
            this.Text = isNew ? ("Add New Instance (" + slotName + ")") : ("Edit Instance — " + currentCustomName + " (" + slotName + ")");
            this.Size = new Size(520, 385);
            this.StartPosition = FormStartPosition.CenterParent;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.BackColor = Color.FromArgb(22, 23, 30);
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f);

            _profiles = DeviceCatalog.GetAllProfiles();

            int y = 20;

            Label lblName = new Label { Text = "Instance Name:", Location = new Point(22, y + 3), AutoSize = true, ForeColor = Color.FromArgb(203, 213, 225) };
            _txtCustomName = new TextBox
            {
                Text = currentCustomName ?? slotName,
                Location = new Point(165, y),
                Width = 315,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 10f, FontStyle.Bold)
            };
            y += 45;

            Label lblRam = new Label { Text = "RAM Allocation:", Location = new Point(22, y + 3), AutoSize = true, ForeColor = Color.FromArgb(203, 213, 225) };
            _cmbRam = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                Location = new Point(165, y),
                Width = 140,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            int[] ramOptions = new[] { 512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192, 12288, 16384 };
            int ramIdx = 2;
            for (int i = 0; i < ramOptions.Length; i++)
            {
                _cmbRam.Items.Add(ramOptions[i] + " MB");
                if (ramOptions[i] == currentRamMb) ramIdx = i;
            }
            _cmbRam.SelectedIndex = ramIdx;

            Label lblCores = new Label { Text = "CPU Cores:", Location = new Point(320, y + 3), AutoSize = true, ForeColor = Color.FromArgb(203, 213, 225) };
            _cmbCores = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                Location = new Point(395, y),
                Width = 85,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            int[] coreOptions = new[] { 1, 2, 3, 4, 6, 8 };
            int coreIdx = 0;
            for (int i = 0; i < coreOptions.Length; i++)
            {
                _cmbCores.Items.Add(coreOptions[i] + (coreOptions[i] == 1 ? " Core" : " Cores"));
                if (coreOptions[i] == currentCores) coreIdx = i;
            }
            _cmbCores.SelectedIndex = coreIdx;
            y += 45;

            Label lblPhone = new Label { Text = "Device Profile:", Location = new Point(22, y + 3), AutoSize = true, ForeColor = Color.FromArgb(203, 213, 225) };
            _cmbProfiles = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                Location = new Point(165, y),
                Width = 235,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            int profIdx = 0;
            for (int i = 0; i < _profiles.Count; i++)
            {
                _cmbProfiles.Items.Add(_profiles[i].DisplayName);
                if (!string.IsNullOrEmpty(currentProfileId) && string.Equals(_profiles[i].Id, currentProfileId, StringComparison.OrdinalIgnoreCase))
                    profIdx = i;
            }
            if (_cmbProfiles.Items.Count > 0) _cmbProfiles.SelectedIndex = profIdx;

            _btnRandomProfile = new Button
            {
                Text = "Random",
                Location = new Point(406, y - 1),
                Size = new Size(74, 26),
                BackColor = Color.FromArgb(79, 70, 229),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnRandomProfile.FlatAppearance.BorderSize = 0;
            _btnRandomProfile.Click += delegate
            {
                if (_profiles.Count > 0)
                {
                    Random rng = new Random();
                    _cmbProfiles.SelectedIndex = rng.Next(_profiles.Count);
                }
            };
            y += 45;

            Label lblProxy = new Label { Text = "Proxy (Optional):", Location = new Point(22, y + 3), AutoSize = true, ForeColor = Color.FromArgb(203, 213, 225) };
            string cleanProxy = (currentProxy ?? "").Trim();
            if (cleanProxy.StartsWith("[🌐] ")) cleanProxy = cleanProxy.Substring(5).Trim();
            if (cleanProxy.StartsWith("[⚡]") || cleanProxy.Contains("127.0.0.1:8880") || cleanProxy.Contains("Accelerator")) cleanProxy = "";

            _txtProxy = new TextBox
            {
                Text = cleanProxy,
                Location = new Point(165, y),
                Width = 315,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Consolas", 9.5f)
            };

            Label lblProxyHint = new Label
            {
                Text = "Format: IP:Port (leave blank to use the built-in high-speed network accelerator).",
                Location = new Point(165, y + 28),
                Size = new Size(320, 32),
                ForeColor = Color.FromArgb(148, 163, 184),
                Font = new Font("Segoe UI", 8.2f)
            };
            y += 75;

            _btnSave = new Button
            {
                Text = isNew ? "Create Instance" : "Save Changes",
                DialogResult = DialogResult.OK,
                Location = new Point(235, y),
                Size = new Size(145, 36),
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnSave.FlatAppearance.BorderSize = 0;
            _btnSave.Click += delegate
            {
                InstanceCustomName = _txtCustomName.Text.Trim();
                if (string.IsNullOrEmpty(InstanceCustomName)) InstanceCustomName = slotName;

                Match mR = Regex.Match(_cmbRam.SelectedItem != null ? _cmbRam.SelectedItem.ToString() : "1024", @"(\d+)");
                SelectedRamMb = mR.Success ? int.Parse(mR.Groups[1].Value) : 1024;

                Match mC = Regex.Match(_cmbCores.SelectedItem != null ? _cmbCores.SelectedItem.ToString() : "1", @"(\d+)");
                SelectedCores = mC.Success ? int.Parse(mC.Groups[1].Value) : 1;

                int pIdx = _cmbProfiles.SelectedIndex;
                SelectedProfileId = (pIdx >= 0 && pIdx < _profiles.Count) ? _profiles[pIdx].Id : "galaxy-tab-s6";
                SelectedProxy = _txtProxy.Text.Trim();
            };

            _btnCancel = new Button
            {
                Text = "Cancel",
                DialogResult = DialogResult.Cancel,
                Location = new Point(390, y),
                Size = new Size(90, 36),
                BackColor = Color.FromArgb(45, 48, 60),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;

            if (!isNew)
            {
                Button btnDeleteInDlg = new Button
                {
                    Text = "Delete Instance",
                    Location = new Point(22, y),
                    Size = new Size(130, 36),
                    BackColor = Color.FromArgb(153, 27, 27),
                    ForeColor = Color.White,
                    FlatStyle = FlatStyle.Flat,
                    Font = new Font("Segoe UI", 9f, FontStyle.Bold),
                    Cursor = Cursors.Hand
                };
                btnDeleteInDlg.FlatAppearance.BorderSize = 0;
                btnDeleteInDlg.Click += delegate
                {
                    DeleteRequested = true;
                    this.DialogResult = DialogResult.Abort;
                    this.Close();
                };
                this.Controls.Add(btnDeleteInDlg);
            }

            this.Controls.Add(lblName);
            this.Controls.Add(_txtCustomName);
            this.Controls.Add(lblRam);
            this.Controls.Add(_cmbRam);
            this.Controls.Add(lblCores);
            this.Controls.Add(_cmbCores);
            this.Controls.Add(lblPhone);
            this.Controls.Add(_cmbProfiles);
            this.Controls.Add(_btnRandomProfile);
            this.Controls.Add(lblProxy);
            this.Controls.Add(_txtProxy);
            this.Controls.Add(lblProxyHint);
            this.Controls.Add(_btnSave);
            this.Controls.Add(_btnCancel);
            this.AcceptButton = _btnSave;
            this.CancelButton = _btnCancel;
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
            this.Text = "Clone Instance — " + sourceDisplayName;
            this.Size = new Size(440, 235);
            this.StartPosition = FormStartPosition.CenterParent;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.BackColor = Color.FromArgb(22, 23, 30);
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f);

            Label lbl = new Label
            {
                Text = string.Format("Clone '{0}' into slot {1} with a unique hardware identity:", sourceDisplayName, targetSlotName),
                Location = new Point(20, 16),
                Size = new Size(390, 28),
                ForeColor = Color.FromArgb(203, 213, 225)
            };

            Label lblName = new Label { Text = "New Instance Name:", Location = new Point(20, 56), AutoSize = true };
            _txtCustomName = new TextBox
            {
                Text = suggestedCustomName,
                Location = new Point(160, 53),
                Width = 240,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };

            _chkState = new CheckBox
            {
                Text = "Keep downloaded game files & map cache (Instant copy)",
                Checked = true,
                Location = new Point(20, 95),
                Size = new Size(390, 30),
                ForeColor = Color.FromArgb(52, 211, 153)
            };

            _btnOk = new Button
            {
                Text = "Clone Instance",
                DialogResult = DialogResult.OK,
                Location = new Point(195, 142),
                Size = new Size(115, 34),
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnOk.FlatAppearance.BorderSize = 0;
            _btnOk.Click += delegate
            {
                TargetCustomName = _txtCustomName.Text.Trim();
                if (string.IsNullOrEmpty(TargetCustomName)) TargetCustomName = targetSlotName;
                CloneState = _chkState.Checked;
            };

            _btnCancel = new Button
            {
                Text = "Cancel",
                DialogResult = DialogResult.Cancel,
                Location = new Point(320, 142),
                Size = new Size(80, 34),
                BackColor = Color.FromArgb(45, 48, 60),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;

            this.Controls.Add(lbl);
            this.Controls.Add(lblName);
            this.Controls.Add(_txtCustomName);
            this.Controls.Add(_chkState);
            this.Controls.Add(_btnOk);
            this.Controls.Add(_btnCancel);
            this.AcceptButton = _btnOk;
            this.CancelButton = _btnCancel;
        }
    }
}