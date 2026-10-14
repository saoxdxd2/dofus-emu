using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;
using DofusFarm.Engine;
using DofusFarm.Hardware;
using DofusFarm.Native;

[assembly: AssemblyTitle("Dofus Farm Setup")]
[assembly: AssemblyDescription("Dofus Touch Virtualization Farm Standalone Installer")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Farm Virtualization Setup")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("2.1.0.0")]
[assembly: AssemblyVersion("2.1.0.0")]

namespace DofusStandaloneSetup
{
    static class Program
    {
        private static Mutex _singleInstanceMutex = null;

        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            bool createdNew;
            _singleInstanceMutex = new Mutex(true, @"Global\DofusFarmSetup_SingleInstance_Mutex_2026", out createdNew);
            if (!createdNew)
            {
                MessageBox.Show(
                    "The Dofus Touch Farm installer is already running.\n\nPlease wait for the active installation to complete.",
                    "Setup Already In Progress",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning);
                return;
            }

            string tempStageDir = Path.Combine(Path.GetTempPath(), "DofusSetup_" + Guid.NewGuid().ToString("N"));
            try
            {
                string targetPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "DofusFarm");
                bool isSilent = false;

                if (args != null)
                {
                    for (int i = 0; i < args.Length; i++)
                    {
                        string a = args[i];
                        if (a.Equals("/silent", StringComparison.OrdinalIgnoreCase) || a.Equals("-silent", StringComparison.OrdinalIgnoreCase) ||
                            a.Equals("/quiet", StringComparison.OrdinalIgnoreCase) || a.Equals("-quiet", StringComparison.OrdinalIgnoreCase))
                        {
                            isSilent = true;
                        }
                        else if ((a.Equals("/dir", StringComparison.OrdinalIgnoreCase) || a.Equals("-dir", StringComparison.OrdinalIgnoreCase) ||
                                  a.Equals("/path", StringComparison.OrdinalIgnoreCase) || a.Equals("-path", StringComparison.OrdinalIgnoreCase) ||
                                  a.Equals("/target", StringComparison.OrdinalIgnoreCase) || a.Equals("-target", StringComparison.OrdinalIgnoreCase) ||
                                  a.Equals("/installpath", StringComparison.OrdinalIgnoreCase) || a.Equals("-installpath", StringComparison.OrdinalIgnoreCase))
                                 && i + 1 < args.Length)
                        {
                            targetPath = args[++i].Trim('"');
                        }
                    }
                }

                if (isSilent)
                {
                    ManualResetEvent doneEvent = new ManualResetEvent(false);
                    Exception caughtEx = null;
                    InstallerEngine engine = new InstallerEngine(tempStageDir, targetPath);
                    engine.LaunchOnComplete = false;
                    engine.RunInstall(
                        delegate { doneEvent.Set(); },
                        delegate (Exception ex) { caughtEx = ex; doneEvent.Set(); }
                    );
                    doneEvent.WaitOne();
                    if (caughtEx != null) throw caughtEx;
                }
                else
                {
                    Application.Run(new InstallerForm(tempStageDir, targetPath));
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show("Fatal Setup Error: " + ex.Message, "Dofus Farm Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                if (!string.IsNullOrEmpty(tempStageDir) && Directory.Exists(tempStageDir))
                {
                    try { FarmEngine.ForceDeleteDirectory(tempStageDir); } catch { }
                }
                if (_singleInstanceMutex != null)
                {
                    try { _singleInstanceMutex.ReleaseMutex(); } catch { }
                    try { _singleInstanceMutex.Dispose(); } catch { }
                    _singleInstanceMutex = null;
                }
            }
        }
    }

    public class InstallerEngine
    {
        public string StageDir { get; private set; }
        public string TargetDir { get; set; }
        public int InstanceCount { get; set; }
        public int RamMb { get; set; }
        public int Cores { get; set; }
        public bool LaunchOnComplete { get; set; }

        public event Action<int, string> OnProgress;

        public InstallerEngine(string stageDir, string targetDir)
        {
            StageDir = stageDir;
            TargetDir = targetDir;
            InstanceCount = 4;
            RamMb = 1024;
            Cores = 1;
            LaunchOnComplete = true;
        }

        private void Report(int pct, string msg)
        {
            if (OnProgress != null) OnProgress(pct, msg);
        }

        private string ResolveSourceFile(string relPath)
        {
            string[] candidates = new[]
            {
                Path.Combine(StageDir, relPath),
                Path.Combine(StageDir, "scripts", Path.GetFileName(relPath)),
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory, relPath),
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "scripts", Path.GetFileName(relPath))
            };
            foreach (string c in candidates)
            {
                if (!string.IsNullOrEmpty(c) && File.Exists(c)) return c;
            }
            return null;
        }

        private void UnpackEmbeddedPayload()
        {
            if (Directory.Exists(StageDir))
            {
                try { FarmEngine.ForceDeleteDirectory(StageDir); } catch { }
            }
            Directory.CreateDirectory(StageDir);

            Assembly asm = Assembly.GetExecutingAssembly();
            using (Stream s = asm.GetManifestResourceStream("DofusPayload.zip"))
            {
                if (s != null)
                {
                    string zipPath = Path.Combine(StageDir, "payload.zip");
                    using (FileStream fs = new FileStream(zipPath, FileMode.Create, FileAccess.Write, FileShare.None, 262144))
                    {
                        byte[] buffer = new byte[262144];
                        int read;
                        long totalRead = 0;
                        long totalLen = 0;
                        try { totalLen = s.Length; } catch { }

                        while ((read = s.Read(buffer, 0, buffer.Length)) > 0)
                        {
                            fs.Write(buffer, 0, read);
                            totalRead += read;
                            if (totalLen > 0)
                            {
                                int pct = 2 + (int)((totalRead * 8) / totalLen);
                                Report(pct, "Unpacking installation package...");
                            }
                        }
                    }
                    Report(10, "Extracting package contents...");
                    ExtractZipWithOverwrite(zipPath, StageDir);
                    try { File.Delete(zipPath); } catch { }
                }
            }
        }

        public void RunInstall(Action onComplete, Action<Exception> onError)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    Report(2, "Unpacking installation files...");
                    UnpackEmbeddedPayload();

                    Report(14, "Verifying package integrity...");
                    if (!FarmEngine.VerifyIntegrity(StageDir) && !FarmEngine.VerifyIntegrity(AppDomain.CurrentDomain.BaseDirectory))
                    {
                        throw new InvalidOperationException("Installation aborted: Package files failed SHA-256 integrity verification.");
                    }

                    KillLingeringProcesses();
                    if (!Directory.Exists(TargetDir)) Directory.CreateDirectory(TargetDir);

                    // 1. Deploy clean runtime binaries and disguise scripts
                    Report(18, "Installing Dofus Farm Manager & network engine...");
                    foreach (string f in new[] { "DofusFarm.exe", "uninstall.exe", "dofus-net-proxy.exe", "app_icon.ico", "installer_icon.ico" })
                    {
                        string src = ResolveSourceFile(f);
                        if (!string.IsNullOrEmpty(src) && File.Exists(src))
                        {
                            string dest = Path.Combine(TargetDir, f);
                            if (!string.Equals(Path.GetFullPath(src), Path.GetFullPath(dest), StringComparison.OrdinalIgnoreCase))
                                File.Copy(src, dest, true);
                        }
                    }

                    string targetScripts = Path.Combine(TargetDir, "scripts");
                    if (!Directory.Exists(targetScripts)) Directory.CreateDirectory(targetScripts);
                    foreach (string sName in new[] { "mobile-disguise.js", "apply-zram.sh" })
                    {
                        string srcScript = ResolveSourceFile(@"scripts\" + sName);
                        if (!string.IsNullOrEmpty(srcScript) && File.Exists(srcScript))
                        {
                            File.Copy(srcScript, Path.Combine(targetScripts, sName), true);
                        }
                    }

                    FarmEngine.ResealLocalIntegrity(TargetDir);

                    // 2. Unpack Android SDK directly to TargetDir\sdk and remove temporary sdk.7z immediately
                    Report(25, "Unpacking Android virtualization engine...");
                    string destSdk = Path.Combine(TargetDir, "sdk");
                    if (!File.Exists(Path.Combine(destSdk, @"emulator\emulator.exe")))
                    {
                        string sdkArchive = ResolveSourceFile("sdk.7z");
                        string z7Exe = ResolveSourceFile(@"scripts\bin\7z.exe");
                        if (string.IsNullOrEmpty(sdkArchive) || !File.Exists(sdkArchive))
                            throw new FileNotFoundException("Required SDK archive (sdk.7z) not found in setup package.");
                        if (string.IsNullOrEmpty(z7Exe) || !File.Exists(z7Exe))
                            throw new FileNotFoundException("Required extractor (scripts\\bin\\7z.exe) not found in setup package.");

                        ProcessStartInfo psi7z = new ProcessStartInfo
                        {
                            FileName = z7Exe,
                            Arguments = string.Format("x \"{0}\" -o\"{1}\" -y", sdkArchive, TargetDir),
                            CreateNoWindow = true,
                            UseShellExecute = false,
                            WindowStyle = ProcessWindowStyle.Hidden
                        };
                        int exitCode = RunProcessWithTimeout(psi7z, 30 * 60 * 1000);
                        if (exitCode != 0)
                        {
                            throw new InvalidOperationException(string.Format("Android SDK extraction failed (7z exit code {0}).", exitCode));
                        }

                        if (sdkArchive.StartsWith(StageDir, StringComparison.OrdinalIgnoreCase))
                        {
                            try { File.Delete(sdkArchive); } catch { }
                        }
                    }

                    string nestedSdk = Path.Combine(destSdk, "sdk");
                    if (Directory.Exists(nestedSdk))
                    {
                        foreach (string dir in Directory.GetDirectories(nestedSdk))
                        {
                            string subDest = Path.Combine(destSdk, Path.GetFileName(dir));
                            if (Directory.Exists(subDest)) { try { Directory.Delete(subDest, true); } catch { } }
                            try { Directory.Move(dir, subDest); } catch { }
                        }
                        foreach (string file in Directory.GetFiles(nestedSdk))
                        {
                            string fileDest = Path.Combine(destSdk, Path.GetFileName(file));
                            if (File.Exists(fileDest)) { try { File.Delete(fileDest); } catch { } }
                            try { File.Move(file, fileDest); } catch { }
                        }
                        try { Directory.Delete(nestedSdk, true); } catch { }
                    }

                    if (!File.Exists(Path.Combine(destSdk, @"emulator\emulator.exe")))
                    {
                        throw new FileNotFoundException("Android SDK verification failed: emulator.exe not found.");
                    }

                    // 3. Unpack Golden Master Template (template.zip) and clean temporary archive
                    Report(50, "Unpacking Golden Master game image...");
                    string avdHome = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".android\avd");
                    if (!Directory.Exists(avdHome)) Directory.CreateDirectory(avdHome);

                    string templateZip = ResolveSourceFile("template.zip");
                    string targetTemplateAvd = Path.Combine(avdHome, "dofus-template.avd");
                    if (!string.IsNullOrEmpty(templateZip) && File.Exists(templateZip))
                    {
                        FarmEngine.ForceDeleteDirectory(targetTemplateAvd);
                        Directory.CreateDirectory(targetTemplateAvd);
                        ExtractZipWithOverwrite(templateZip, targetTemplateAvd);
                        if (templateZip.StartsWith(StageDir, StringComparison.OrdinalIgnoreCase))
                        {
                            try { File.Delete(templateZip); } catch { }
                        }
                    }

                    // 4. Provision instances & run parallel Cold-Run warmup using shared FarmEngine
                    Report(60, string.Format("Creating {0} isolated game instance(s) ({1} MB RAM / {2} Core)...", InstanceCount, RamMb, Cores));
                    FarmEngine farm = new FarmEngine(TargetDir, true);
                    for (int i = 1; i <= InstanceCount; i++)
                    {
                        string slotName = string.Format("dofus-{0:D2}", i);
                        string customName = string.Format("Instance #{0}", i);
                        DeviceProfile prof = DeviceCatalog.GetDefaultProfileForIndex(i);
                        farm.ProvisionInstance(slotName, RamMb, Cores, prof.Id, customName, "");
                    }

                    // 5. Parallel Cold-Run Pre-Warm in waves of up to 4 so every instance saves its native default_boot snapshot
                    Report(68, string.Format("Pre-warming {0} instance(s) for instant startup...", InstanceCount));
                    int batchSize = 4;
                    for (int batchStart = 1; batchStart <= InstanceCount; batchStart += batchSize)
                    {
                        int batchEnd = Math.Min(InstanceCount, batchStart + batchSize - 1);
                        int countInBatch = batchEnd - batchStart + 1;

                        using (CountdownEvent doneEvent = new CountdownEvent(countInBatch))
                        {
                            for (int idx = batchStart; idx <= batchEnd; idx++)
                            {
                                int slotIdx = idx;
                                ThreadPool.QueueUserWorkItem(delegate
                                {
                                    try
                                    {
                                        string sName = string.Format("dofus-{0:D2}", slotIdx);
                                        int sPort = 5554 + 2 * (slotIdx - 1);
                                        farm.WarmInstanceColdRunSync(sName, sPort, RamMb, Cores);
                                    }
                                    catch { }
                                    finally
                                    {
                                        try { doneEvent.Signal(); } catch { }
                                    }
                                });
                                Thread.Sleep(1200);
                            }

                            Stopwatch batchSw = Stopwatch.StartNew();
                            while (!doneEvent.Wait(600))
                            {
                                if (batchSw.Elapsed.TotalSeconds > 160) break;
                                int pct = 70 + (int)Math.Min(24, (batchSw.Elapsed.TotalSeconds / 45.0) * 24.0);
                                Report(pct, string.Format("Pre-warming game instances ({0}-{1} of {2}) for instant launch...", batchStart, batchEnd, InstanceCount));
                            }
                        }
                    }

                    // 6. Desktop Shortcut, Registry & Environment Variables
                    Report(96, "Finalizing shortcuts and Windows integration...");
                    string farmExe = Path.Combine(TargetDir, "DofusFarm.exe");
                    string icoPath = Path.Combine(TargetDir, "app_icon.ico");

                    if (File.Exists(farmExe))
                    {
                        try
                        {
                            CreateShellShortcut(
                                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Desktop), "Dofus Farm Manager.lnk"),
                                farmExe,
                                TargetDir,
                                icoPath,
                                "Dofus Touch Multi-Instance Manager");
                        }
                        catch { }
                    }

                    try
                    {
                        using (RegistryKey k = Registry.CurrentUser.CreateSubKey(@"Software\DofusFarm"))
                        {
                            if (k != null)
                            {
                                k.SetValue("InstallPath", TargetDir);
                                k.SetValue("DefaultRamMb", RamMb);
                                k.SetValue("DefaultCores", Cores);
                            }
                        }
                    }
                    catch { }

                    try
                    {
                        using (RegistryKey uk = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\DofusTouchFarm"))
                        {
                            if (uk != null)
                            {
                                uk.SetValue("DisplayName", "Dofus Touch Farm Manager");
                                uk.SetValue("DisplayIcon", icoPath);
                                uk.SetValue("DisplayVersion", "2.1.0");
                                uk.SetValue("Publisher", "Dofus Farm Project");
                                uk.SetValue("InstallLocation", TargetDir);
                                uk.SetValue("UninstallString", "\"" + Path.Combine(TargetDir, "uninstall.exe") + "\"");
                            }
                        }
                    }
                    catch { }

                    try
                    {
                        foreach (string staleVar in new[] { "ANDROID_SDK_ROOT", "ANDROID_HOME", "ANDROID_AVD_HOME", "DOFUS_FARM_HOME" })
                        {
                            try { Environment.SetEnvironmentVariable(staleVar, null, EnvironmentVariableTarget.Machine); } catch { }
                        }

                        Environment.SetEnvironmentVariable("DOFUS_FARM_HOME",  TargetDir);
                        Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT", destSdk);
                        Environment.SetEnvironmentVariable("ANDROID_HOME",     destSdk);
                        Environment.SetEnvironmentVariable("ANDROID_AVD_HOME", avdHome);

                        Environment.SetEnvironmentVariable("DOFUS_FARM_HOME",  TargetDir, EnvironmentVariableTarget.User);
                        Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT", destSdk,   EnvironmentVariableTarget.User);
                        Environment.SetEnvironmentVariable("ANDROID_HOME",     destSdk,   EnvironmentVariableTarget.User);
                        Environment.SetEnvironmentVariable("ANDROID_AVD_HOME", avdHome,   EnvironmentVariableTarget.User);
                    }
                    catch { }

                    Report(100, "Setup complete! Launching Dofus Farm Manager...");
                    Thread.Sleep(400);

                    if (LaunchOnComplete && File.Exists(farmExe))
                    {
                        ProcessStartInfo psiFarm = new ProcessStartInfo
                        {
                            FileName = farmExe,
                            WorkingDirectory = TargetDir,
                            UseShellExecute = false
                        };
                        psiFarm.EnvironmentVariables["DOFUS_FARM_HOME"] = TargetDir;
                        psiFarm.EnvironmentVariables["ANDROID_SDK_ROOT"] = destSdk;
                        psiFarm.EnvironmentVariables["ANDROID_HOME"] = destSdk;
                        psiFarm.EnvironmentVariables["ANDROID_AVD_HOME"] = avdHome;
                        Process.Start(psiFarm);
                    }

                    if (onComplete != null) onComplete();
                }
                catch (Exception ex)
                {
                    if (onError != null) onError(ex);
                }
            });
        }

        private void CreateShellShortcut(string shortcutPath, string targetPath, string workingDir, string iconPath, string desc)
        {
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null) return;
            dynamic shell = Activator.CreateInstance(shellType);
            dynamic sc = shell.CreateShortcut(shortcutPath);
            sc.TargetPath = targetPath;
            sc.WorkingDirectory = workingDir;
            sc.Description = desc;
            if (File.Exists(iconPath)) sc.IconLocation = iconPath + ",0";
            sc.Save();
        }

        public static void KillLingeringProcesses()
        {
            foreach (string pName in new[] { "qemu-system-x86_64", "emulator", "dofus-net-proxy", "DofusFarm", "adb" })
            {
                try
                {
                    foreach (Process p in Process.GetProcessesByName(pName))
                    {
                        try { p.Kill(); p.WaitForExit(3000); } catch { }
                    }
                }
                catch { }
            }
        }

        public static void ExtractZipWithOverwrite(string zipPath, string destDir)
        {
            if (!Directory.Exists(destDir)) Directory.CreateDirectory(destDir);
            using (ZipArchive archive = ZipFile.OpenRead(zipPath))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string destPath = Path.GetFullPath(Path.Combine(destDir, entry.FullName));
                    string fullDestDir = Path.GetFullPath(destDir);
                    if (!destPath.StartsWith(fullDestDir + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                        && !destPath.Equals(fullDestDir, StringComparison.OrdinalIgnoreCase))
                        continue;

                    if (string.IsNullOrEmpty(entry.Name))
                    {
                        Directory.CreateDirectory(destPath);
                        continue;
                    }

                    string parentDir = Path.GetDirectoryName(destPath);
                    if (!string.IsNullOrEmpty(parentDir) && !Directory.Exists(parentDir))
                        Directory.CreateDirectory(parentDir);

                    if (File.Exists(destPath))
                    {
                        try { File.SetAttributes(destPath, FileAttributes.Normal); } catch { }
                    }

                    entry.ExtractToFile(destPath, true);
                }
            }
        }

        public static int RunProcessWithTimeout(ProcessStartInfo psi, int timeoutMs)
        {
            using (Process p = Process.Start(psi))
            {
                if (p == null) throw new InvalidOperationException("Failed to start process: " + psi.FileName);
                if (!p.WaitForExit(timeoutMs))
                {
                    try { p.Kill(); } catch { }
                    throw new TimeoutException(string.Format("Process '{0}' timed out after {1} seconds.", Path.GetFileName(psi.FileName), timeoutMs / 1000));
                }
                return p.ExitCode;
            }
        }
    }

    public class InstallerForm : Form
    {
        private InstallerEngine _engine;
        private string _stageDir;
        private string _targetDir;

        private Panel _headerPanel;
        private Label _titleLabel;
        private Label _subtitleLabel;
        private Label _lblTimer;

        private Label _lblTarget;
        private TextBox _txtTarget;
        private Button _btnBrowse;

        private Label _lblSpecs;
        private Label _lblInstances;
        private ComboBox _cmbInstances;
        private Label _lblRam;
        private ComboBox _cmbRam;
        private Label _lblCores;
        private ComboBox _cmbCores;

        private ProgressBar _progBar;
        private Label _lblStatus;
        private Label _lblPctBadge;

        private Button _btnInstall;
        private Button _btnCancel;

        private System.Windows.Forms.Timer _uiTimer;
        private Stopwatch _installStopwatch;

        public InstallerForm(string stageDir, string defaultTarget)
        {
            _stageDir = stageDir;
            _targetDir = defaultTarget;
            _engine = new InstallerEngine(_stageDir, _targetDir);
            _installStopwatch = new Stopwatch();

            InitializeComponent();
            DetectHostHardwareAsync();
        }

        private void InitializeComponent()
        {
            this.Text = "Dofus Touch Farm Setup";
            this.Size = new Size(640, 415);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.BackColor = Color.FromArgb(18, 19, 24);
            this.ForeColor = Color.FromArgb(235, 235, 240);
            this.Font = new Font("Segoe UI", 9.5f);
            this.DoubleBuffered = true;

            try
            {
                string exeLoc = Assembly.GetExecutingAssembly().Location;
                if (!string.IsNullOrEmpty(exeLoc) && File.Exists(exeLoc))
                    this.Icon = Icon.ExtractAssociatedIcon(exeLoc);
            }
            catch { }

            _headerPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 68,
                BackColor = Color.FromArgb(25, 26, 34),
                Padding = new Padding(20, 10, 20, 10)
            };

            _titleLabel = new Label
            {
                Text = "⚡ DOFUS TOUCH FARM INSTALLER",
                Font = new Font("Segoe UI", 12f, FontStyle.Bold),
                ForeColor = Color.FromArgb(96, 165, 250),
                AutoSize = true,
                Location = new Point(22, 12)
            };

            _subtitleLabel = new Label
            {
                Text = "One-Click Setup • Pre-Warms Instances During Installation for Instant Launch",
                Font = new Font("Segoe UI", 9f),
                ForeColor = Color.FromArgb(156, 163, 175),
                AutoSize = true,
                Location = new Point(22, 37)
            };

            _lblTimer = new Label
            {
                Text = "00:00",
                Font = new Font("Consolas", 10f, FontStyle.Bold),
                ForeColor = Color.FromArgb(52, 211, 153),
                AutoSize = true,
                Location = new Point(555, 16),
                Visible = false
            };

            _headerPanel.Controls.Add(_titleLabel);
            _headerPanel.Controls.Add(_subtitleLabel);
            _headerPanel.Controls.Add(_lblTimer);

            _lblTarget = new Label { Text = "Installation Folder:", Location = new Point(25, 86), AutoSize = true, ForeColor = Color.FromArgb(200, 205, 215) };
            _txtTarget = new TextBox
            {
                Text = _targetDir,
                Location = new Point(25, 110),
                Width = 470,
                BackColor = Color.FromArgb(26, 28, 36),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 9.5f)
            };
            _btnBrowse = new Button
            {
                Text = "Browse...",
                Location = new Point(505, 108),
                Size = new Size(95, 27),
                BackColor = Color.FromArgb(42, 44, 56),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnBrowse.FlatAppearance.BorderSize = 0;
            _btnBrowse.Click += OnBrowseClick;

            _lblSpecs = new Label
            {
                Text = "Detecting your PC hardware...",
                Location = new Point(25, 148),
                Size = new Size(575, 22),
                ForeColor = Color.FromArgb(52, 211, 153),
                Font = new Font("Segoe UI", 9f, FontStyle.Bold)
            };

            _lblInstances = new Label { Text = "Instances:", Location = new Point(25, 182), AutoSize = true, ForeColor = Color.FromArgb(180, 185, 195) };
            _cmbInstances = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 38),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 170,
                Location = new Point(25, 205)
            };
            _cmbInstances.Items.AddRange(new object[] { "1 Instance", "2 Instances", "3 Instances", "4 Instances (Default)", "6 Instances", "8 Instances" });
            _cmbInstances.SelectedIndex = 3;

            _lblRam = new Label { Text = "RAM per Instance:", Location = new Point(215, 182), AutoSize = true, ForeColor = Color.FromArgb(180, 185, 195) };
            _cmbRam = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 38),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 180,
                Location = new Point(215, 205)
            };
            _cmbRam.Items.AddRange(new object[] { "512 MB", "768 MB", "1024 MB (Default)", "1536 MB", "2048 MB", "3072 MB", "4096 MB", "8192 MB" });
            _cmbRam.SelectedIndex = 2;

            _lblCores = new Label { Text = "CPU Cores per Instance:", Location = new Point(415, 182), AutoSize = true, ForeColor = Color.FromArgb(180, 185, 195) };
            _cmbCores = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 38),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 185,
                Location = new Point(415, 205)
            };
            _cmbCores.Items.AddRange(new object[] { "1 Core (Default)", "2 Cores", "3 Cores", "4 Cores", "6 Cores", "8 Cores" });
            _cmbCores.SelectedIndex = 0;

            _lblStatus = new Label
            {
                Text = "Ready to install.",
                Location = new Point(25, 255),
                Size = new Size(500, 22),
                ForeColor = Color.FromArgb(220, 225, 235)
            };

            _lblPctBadge = new Label
            {
                Text = "0%",
                Location = new Point(530, 255),
                Size = new Size(70, 22),
                TextAlign = ContentAlignment.MiddleRight,
                ForeColor = Color.FromArgb(96, 165, 250),
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold)
            };

            _progBar = new ProgressBar
            {
                Location = new Point(25, 280),
                Size = new Size(575, 22),
                Style = ProgressBarStyle.Continuous,
                Value = 0
            };

            _btnInstall = new Button
            {
                Text = "🚀 Install",
                Location = new Point(350, 322),
                Size = new Size(150, 38),
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 10f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnInstall.FlatAppearance.BorderSize = 0;
            _btnInstall.Click += OnInstallClick;

            _btnCancel = new Button
            {
                Text = "Cancel",
                Location = new Point(510, 322),
                Size = new Size(90, 38),
                BackColor = Color.FromArgb(42, 44, 56),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;
            _btnCancel.Click += delegate { this.Close(); };

            _uiTimer = new System.Windows.Forms.Timer { Interval = 500 };
            _uiTimer.Tick += delegate
            {
                if (_installStopwatch.IsRunning)
                {
                    TimeSpan ts = _installStopwatch.Elapsed;
                    _lblTimer.Text = string.Format("{0:D2}:{1:D2}", (int)ts.TotalMinutes, ts.Seconds);
                }
            };

            this.Controls.Add(_headerPanel);
            this.Controls.Add(_lblTarget);
            this.Controls.Add(_txtTarget);
            this.Controls.Add(_btnBrowse);
            this.Controls.Add(_lblSpecs);
            this.Controls.Add(_lblInstances);
            this.Controls.Add(_cmbInstances);
            this.Controls.Add(_lblRam);
            this.Controls.Add(_cmbRam);
            this.Controls.Add(_lblCores);
            this.Controls.Add(_cmbCores);
            this.Controls.Add(_lblStatus);
            this.Controls.Add(_lblPctBadge);
            this.Controls.Add(_progBar);
            this.Controls.Add(_btnInstall);
            this.Controls.Add(_btnCancel);

            _engine.OnProgress += delegate (int pct, string msg)
            {
                if (this.IsDisposed || !this.IsHandleCreated) return;
                try
                {
                    this.BeginInvoke(new Action(delegate
                    {
                        if (this.IsDisposed) return;
                        int clamped = Math.Min(100, Math.Max(0, pct));
                        _progBar.Value = clamped;
                        _lblPctBadge.Text = clamped + "%";
                        _lblStatus.Text = msg;
                    }));
                }
                catch { }
            };
        }

        private void DetectHostHardwareAsync()
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                HardwareSpecs specs = HardwareSpecs.Detect();
                if (this.IsDisposed || !this.IsHandleCreated) return;
                try
                {
                    this.BeginInvoke(new Action(delegate
                    {
                        if (this.IsDisposed) return;
                        _lblSpecs.Text = string.Format("PC: {0} • {1} • {2} GB RAM ({3} Threads)",
                            specs.CpuName, specs.GpuName, specs.HostRamGB, specs.LogicalThreads);
                        if (specs.HostRamGB >= 15.5 && !_isInstalling)
                        {
                            _cmbRam.SelectedIndex = 3; // 1536 MB
                            _cmbCores.SelectedIndex = 1; // 2 Cores
                        }
                    }));
                }
                catch { }
            });
        }

        private void OnBrowseClick(object sender, EventArgs e)
        {
            using (FolderBrowserDialog fbd = new FolderBrowserDialog())
            {
                fbd.SelectedPath = _txtTarget.Text;
                if (fbd.ShowDialog() == DialogResult.OK)
                {
                    _txtTarget.Text = fbd.SelectedPath;
                }
            }
        }

        private bool _isInstalling = false;

        private void SetUiState(bool installing)
        {
            _isInstalling = installing;
            _btnInstall.Enabled = !installing;
            _btnCancel.Enabled = !installing;
            _btnBrowse.Enabled = !installing;
            _txtTarget.ReadOnly = installing;
            _cmbInstances.Enabled = !installing;
            _cmbRam.Enabled = !installing;
            _cmbCores.Enabled = !installing;

            if (installing)
            {
                _lblTimer.Visible = true;
                _installStopwatch.Restart();
                _uiTimer.Start();
                _btnInstall.Text = "⏳ Installing...";
                _btnInstall.BackColor = Color.FromArgb(55, 60, 72);
                this.Cursor = Cursors.WaitCursor;
            }
            else
            {
                _installStopwatch.Stop();
                _uiTimer.Stop();
                _btnInstall.Text = "🚀 Install";
                _btnInstall.BackColor = Color.FromArgb(37, 99, 235);
                this.Cursor = Cursors.Default;
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_isInstalling && e.CloseReason == CloseReason.UserClosing)
            {
                DialogResult dr = MessageBox.Show(
                    "Installation is currently in progress.\nAre you sure you want to abort?",
                    "Abort Installation?",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning);
                if (dr == DialogResult.No)
                {
                    e.Cancel = true;
                    return;
                }
            }
            base.OnFormClosing(e);
        }

        private void OnInstallClick(object sender, EventArgs e)
        {
            if (_isInstalling) return;

            _targetDir = _txtTarget.Text.Trim();
            _engine.TargetDir = _targetDir;
            _engine.LaunchOnComplete = true;

            int instCount = 4;
            if (_cmbInstances.SelectedItem != null)
            {
                Match m = Regex.Match(_cmbInstances.SelectedItem.ToString(), @"(\d+)");
                if (m.Success) int.TryParse(m.Groups[1].Value, out instCount);
            }
            _engine.InstanceCount = instCount;

            int ramMb = 1024;
            if (_cmbRam.SelectedItem != null)
            {
                Match m = Regex.Match(_cmbRam.SelectedItem.ToString(), @"(\d+)");
                if (m.Success) int.TryParse(m.Groups[1].Value, out ramMb);
            }
            _engine.RamMb = ramMb;

            int cores = 1;
            if (_cmbCores.SelectedItem != null)
            {
                Match m = Regex.Match(_cmbCores.SelectedItem.ToString(), @"(\d+)");
                if (m.Success) int.TryParse(m.Groups[1].Value, out cores);
            }
            _engine.Cores = cores;

            SetUiState(true);

            _engine.RunInstall(
                delegate
                {
                    this.Invoke(new Action(delegate
                    {
                        SetUiState(false);
                        this.Close();
                    }));
                },
                delegate (Exception ex)
                {
                    this.Invoke(new Action(delegate
                    {
                        SetUiState(false);
                        MessageBox.Show("Installation Error: " + ex.Message, "Setup Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    }));
                }
            );
        }
    }
}
