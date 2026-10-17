using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
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

[assembly: AssemblyTitle("Dofus Touch Farm Setup")]
[assembly: AssemblyDescription("Dofus Touch Multi-Instance Virtualization Installer")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Touch Farm Setup")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("2.3.0.0")]
[assembly: AssemblyVersion("2.3.0.0")]

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
                    "Setup Already Running",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
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
                MessageBox.Show("Setup Error: " + ex.Message, "Dofus Touch Farm Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
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

        private string ResolveLocalFallbackFile(string relPath)
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

        /// <summary>
        /// Zero-copy stream unpacker: reads directly from the memory-mapped PE resource stream
        /// without writing an intermediate 896 MB payload.zip to %TEMP%.
        /// Runtime binaries go directly to TargetDir, template.zip streams directly into dofus-template.avd,
        /// and only sdk.7z + 7z.exe are staged in StageDir for native 7-Zip extraction.
        /// </summary>
        private void StreamUnpackPayload(string targetDir, string targetScriptsDir, string targetTemplateAvd)
        {
            if (Directory.Exists(StageDir))
            {
                try { FarmEngine.ForceDeleteDirectory(StageDir); } catch { }
            }
            Directory.CreateDirectory(StageDir);

            Assembly asm = Assembly.GetExecutingAssembly();

            // 1. Extract core runtime binaries & scripts from DofusPayload.zip
            using (Stream resStream = asm.GetManifestResourceStream("DofusPayload.zip"))
            {
                if (resStream != null)
                {
                    using (ZipArchive archive = new ZipArchive(resStream, ZipArchiveMode.Read))
                    {
                        int totalEntries = archive.Entries.Count;
                        int entryIndex = 0;
                        foreach (ZipArchiveEntry entry in archive.Entries)
                        {
                            entryIndex++;
                            if (string.IsNullOrEmpty(entry.Name)) continue;

                            if (entryIndex % 20 == 0 || entryIndex == totalEntries)
                            {
                                int pct = 4 + (int)((entryIndex * 48.0) / Math.Max(1, totalEntries));
                                Report(pct, string.Format("Extracting {0} ({1}/{2})...", entry.Name, entryIndex, totalEntries));
                            }

                            string rel = entry.FullName.Replace('/', '\\').TrimStart('\\');
                            string fileName = entry.Name;

                            if (fileName.Equals("template.zip", StringComparison.OrdinalIgnoreCase))
                            {
                                Report(12, "Unpacking Golden Master Android image...");
                                FarmEngine.ForceDeleteDirectory(targetTemplateAvd);
                                Directory.CreateDirectory(targetTemplateAvd);
                                using (Stream nestedZipStream = entry.Open())
                                using (ZipArchive tplArchive = new ZipArchive(nestedZipStream, ZipArchiveMode.Read))
                                {
                                    ExtractArchiveToDirectory(tplArchive, targetTemplateAvd);
                                }
                                continue;
                            }

                            if (fileName.Equals("sdk.7z", StringComparison.OrdinalIgnoreCase) ||
                                rel.StartsWith(@"scripts\bin\", StringComparison.OrdinalIgnoreCase))
                            {
                                string stageDest = Path.Combine(StageDir, rel);
                                string stageParent = Path.GetDirectoryName(stageDest);
                                if (!string.IsNullOrEmpty(stageParent) && !Directory.Exists(stageParent))
                                    Directory.CreateDirectory(stageParent);

                                if (fileName.Equals("sdk.7z", StringComparison.OrdinalIgnoreCase))
                                {
                                    using (Stream sIn = entry.Open())
                                    {
                                        CopyStreamWithProgress(sIn, entry.Length, stageDest, 4, 8, "Preparing Android virtualization archive...");
                                    }
                                }
                                else
                                {
                                    entry.ExtractToFile(stageDest, true);
                                }
                                continue;
                            }

                            string directDest;
                            if (rel.StartsWith(@"scripts\", StringComparison.OrdinalIgnoreCase))
                            {
                                directDest = Path.Combine(targetScriptsDir, rel.Substring(8));
                            }
                            else
                            {
                                directDest = Path.Combine(targetDir, rel);
                            }

                            string parentDir = Path.GetDirectoryName(directDest);
                            if (!string.IsNullOrEmpty(parentDir) && !Directory.Exists(parentDir))
                                Directory.CreateDirectory(parentDir);

                            if (File.Exists(directDest))
                            {
                                try { File.SetAttributes(directDest, FileAttributes.Normal); } catch { }
                            }
                            entry.ExtractToFile(directDest, true);
                        }
                    }
                }
            }

            // 2. Stream top-level seekable PE resource "template.zip" directly into dofus-template.avd (zero RAM buffering, zero temp writes!)
            using (Stream tplResStream = asm.GetManifestResourceStream("template.zip"))
            {
                if (tplResStream != null)
                {
                    Report(6, "Unpacking Golden Master Android image...");
                    FarmEngine.ForceDeleteDirectory(targetTemplateAvd);
                    Directory.CreateDirectory(targetTemplateAvd);
                    using (ZipArchive tplArchive = new ZipArchive(tplResStream, ZipArchiveMode.Read))
                    {
                        ExtractArchiveToDirectory(tplArchive, targetTemplateAvd);
                    }
                }
            }

            // 3. Stream top-level PE resource "sdk.7z" to StageDir for 7z.exe extraction
            using (Stream sdkResStream = asm.GetManifestResourceStream("sdk.7z"))
            {
                if (sdkResStream != null)
                {
                    string stageSdk = Path.Combine(StageDir, "sdk.7z");
                    CopyStreamWithProgress(sdkResStream, sdkResStream.Length, stageSdk, 10, 9, "Preparing Android virtualization archive...");
                }
            }
        }

        private void CopyStreamWithProgress(Stream inStream, long totalLen, string destPath, int startPct, int spanPct, string statusText)
        {
            using (FileStream outStream = new FileStream(destPath, FileMode.Create, FileAccess.Write, FileShare.None, 262144))
            {
                byte[] buffer = new byte[262144];
                long written = 0;
                int read;
                long lastReport = 0;
                while ((read = inStream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    outStream.Write(buffer, 0, read);
                    written += read;
                    if (totalLen > 0 && written - lastReport >= 16 * 1024 * 1024)
                    {
                        lastReport = written;
                        int pct = startPct + (int)((written * spanPct) / totalLen);
                        Report(pct, statusText);
                    }
                }
            }
        }

        private static void ExtractArchiveToDirectory(System.IO.Compression.ZipArchive archive, string destDir)
        {
            if (!Directory.Exists(destDir)) Directory.CreateDirectory(destDir);
            string fullDestDir = Path.GetFullPath(destDir);
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string destPath = Path.GetFullPath(Path.Combine(destDir, entry.FullName));
                if (!destPath.StartsWith(fullDestDir + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) &&
                    !destPath.Equals(fullDestDir, StringComparison.OrdinalIgnoreCase))
                    continue;

                if (string.IsNullOrEmpty(entry.Name))
                {
                    Directory.CreateDirectory(destPath);
                    continue;
                }

                string parent = Path.GetDirectoryName(destPath);
                if (!string.IsNullOrEmpty(parent) && !Directory.Exists(parent))
                    Directory.CreateDirectory(parent);

                if (File.Exists(destPath))
                {
                    try { File.SetAttributes(destPath, FileAttributes.Normal); } catch { }
                }
                entry.ExtractToFile(destPath, true);
            }
        }

        private static void CopyDirectory(string sourceDir, string destDir)
        {
            if (!Directory.Exists(sourceDir)) return;
            Directory.CreateDirectory(destDir);
            foreach (string file in Directory.GetFiles(sourceDir))
            {
                string destFile = Path.Combine(destDir, Path.GetFileName(file));
                try { File.Copy(file, destFile, true); } catch { }
            }
            foreach (string subDir in Directory.GetDirectories(sourceDir))
            {
                string destSub = Path.Combine(destDir, Path.GetFileName(subDir));
                CopyDirectory(subDir, destSub);
            }
        }

        public void RunInstall(Action onComplete, Action<Exception> onError)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    Report(2, "Preparing installation directory...");
                    KillLingeringProcesses();
                    if (!Directory.Exists(TargetDir)) Directory.CreateDirectory(TargetDir);

                    string targetScripts = Path.Combine(TargetDir, "scripts");
                    if (!Directory.Exists(targetScripts)) Directory.CreateDirectory(targetScripts);

                    string avdHome = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".android\avd");
                    if (!Directory.Exists(avdHome)) Directory.CreateDirectory(avdHome);
                    string targetTemplateAvd = Path.Combine(avdHome, "dofus-template.avd");

                    // 1. Stream-unpack embedded payload (zero duplicate temp zip writes)
                    Report(4, "Deploying application files & system image...");
                    StreamUnpackPayload(TargetDir, targetScripts, targetTemplateAvd);

                    // Fallback copy if running unbundled next to local files
                    foreach (string f in new[] { "DofusFarm.exe", "uninstall.exe", "dofus-net-proxy.exe", "app_icon.ico", "installer_icon.ico", "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll" })
                    {
                        string dest = Path.Combine(TargetDir, f);
                        if (!File.Exists(dest))
                        {
                            string src = ResolveLocalFallbackFile(f);
                            if (!string.IsNullOrEmpty(src) && File.Exists(src)) File.Copy(src, dest, true);
                        }
                    }
                    foreach (string sName in new[] { "mobile-disguise.js", "apply-zram.sh" })
                    {
                        string dest = Path.Combine(targetScripts, sName);
                        if (!File.Exists(dest))
                        {
                            string src = ResolveLocalFallbackFile(@"scripts\" + sName);
                            if (!string.IsNullOrEmpty(src) && File.Exists(src)) File.Copy(src, dest, true);
                        }
                    }

                    // Fallback copy for client bundle and runtimes if unbundled
                    string targetClient = Path.Combine(TargetDir, "client");
                    if (!Directory.Exists(targetClient))
                    {
                        string localClient = ResolveLocalFallbackFile(@"desktop\client");
                        if (string.IsNullOrEmpty(localClient)) localClient = ResolveLocalFallbackFile("client");
                        if (!string.IsNullOrEmpty(localClient) && Directory.Exists(localClient))
                        {
                            CopyDirectory(localClient, targetClient);
                        }
                    }
                    string targetRuntimes = Path.Combine(TargetDir, "runtimes");
                    if (!Directory.Exists(targetRuntimes))
                    {
                        string localRuntimes = ResolveLocalFallbackFile("runtimes");
                        if (!string.IsNullOrEmpty(localRuntimes) && Directory.Exists(localRuntimes))
                        {
                            CopyDirectory(localRuntimes, targetRuntimes);
                        }
                    }

                    string targetNetwork = Path.Combine(TargetDir, "dofus-network");
                    if (!Directory.Exists(targetNetwork))
                    {
                        string localNet = ResolveLocalFallbackFile("dofus-network");
                        if (!string.IsNullOrEmpty(localNet) && Directory.Exists(localNet))
                        {
                            CopyDirectory(localNet, targetNetwork);
                        }
                    }
                    foreach (string hFile in new[] { "requirements.txt", "start-sentinel.bat", "install-python-ai.bat" })
                    {
                        string dest = Path.Combine(TargetDir, hFile);
                        if (!File.Exists(dest))
                        {
                            string src = ResolveLocalFallbackFile(hFile);
                            if (!string.IsNullOrEmpty(src) && File.Exists(src)) File.Copy(src, dest, true);
                        }
                    }

                    string embedZip = Path.Combine(TargetDir, @"runtimes\python_embed.zip");
                    string targetPythonDir = Path.Combine(TargetDir, @"runtimes\python");
                    if (File.Exists(embedZip) && (!Directory.Exists(targetPythonDir) || !File.Exists(Path.Combine(targetPythonDir, "python.exe"))))
                    {
                        Report(18, "Unpacking clean embedded Python runtime...");
                        Directory.CreateDirectory(targetPythonDir);
                        using (ZipArchive pArch = ZipFile.OpenRead(embedZip))
                        {
                            ExtractArchiveToDirectory(pArch, targetPythonDir);
                        }
                    }

                    if (!File.Exists(Path.Combine(targetTemplateAvd, "userdata-golden.img")))
                    {
                        string localTpl = ResolveLocalFallbackFile("template.zip");
                        if (!string.IsNullOrEmpty(localTpl) && File.Exists(localTpl))
                        {
                            Report(15, "Unpacking Golden Master Android image...");
                            FarmEngine.ForceDeleteDirectory(targetTemplateAvd);
                            Directory.CreateDirectory(targetTemplateAvd);
                            using (ZipArchive tplArch = ZipFile.OpenRead(localTpl))
                            {
                                ExtractArchiveToDirectory(tplArch, targetTemplateAvd);
                            }
                        }
                    }

                    // 2. Unpack Android SDK (sdk.7z) ONLY if legacy emulator archive is present
                    string destSdk = Path.Combine(TargetDir, "sdk");
                    string sdkArchive = ResolveLocalFallbackFile("sdk.7z");
                    bool hasLegacyArchive = !string.IsNullOrEmpty(sdkArchive) && File.Exists(sdkArchive);

                    if (hasLegacyArchive && !File.Exists(Path.Combine(destSdk, @"emulator\emulator.exe")))
                    {
                        Report(20, "Unpacking legacy Android virtualization engine...");
                        string z7Exe = ResolveLocalFallbackFile(@"scripts\bin\7z.exe");
                        if (!string.IsNullOrEmpty(z7Exe) && File.Exists(z7Exe))
                        {
                            ProcessStartInfo psi7z = new ProcessStartInfo
                            {
                                FileName = z7Exe,
                                Arguments = string.Format("x \"{0}\" -o\"{1}\" -y", sdkArchive, TargetDir),
                                CreateNoWindow = true,
                                UseShellExecute = false,
                                WindowStyle = ProcessWindowStyle.Hidden
                            };

                            using (Process p7z = Process.Start(psi7z))
                            {
                                Stopwatch sw7z = Stopwatch.StartNew();
                                string[] sdkStages = new[]
                                {
                                    "Unpacking Android virtualization engine...",
                                    "Extracting OpenGL & Vulkan hardware renderers...",
                                    "Deploying Android 10 x86_64 system image...",
                                    "Finalizing SDK platform tools..."
                                };
                                while (!p7z.WaitForExit(450))
                                {
                                    if (sw7z.Elapsed.TotalMinutes > 30)
                                    {
                                        try { p7z.Kill(); } catch { }
                                        throw new TimeoutException("Android SDK extraction timed out.");
                                    }
                                    double ratio = Math.Min(0.96, sw7z.Elapsed.TotalSeconds / 32.0);
                                    int pct = 20 + (int)(ratio * 32.0);
                                    int msgIdx = Math.Min(sdkStages.Length - 1, (int)(ratio * sdkStages.Length));
                                    Report(pct, sdkStages[msgIdx]);
                                }
                                if (p7z.ExitCode != 0)
                                {
                                    throw new InvalidOperationException(string.Format("Android SDK extraction failed (exit code {0}).", p7z.ExitCode));
                                }
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
                                try { Directory.Move(file, fileDest); } catch { }
                            }
                            try { Directory.Delete(nestedSdk, true); } catch { }
                        }
                    }
                    else
                    {
                        Report(35, "Configuring Standalone WebView2 Engine (Zero-Emulator Mode)...");
                    }

                    // 3. Provision instances using shared FarmEngine (54% -> 62%)
                    Report(55, string.Format("Configuring {0} isolated game account slot(s)...", InstanceCount));
                    FarmEngine farm = new FarmEngine(TargetDir, true);
                    for (int i = 1; i <= InstanceCount; i++)
                    {
                        string slotName = string.Format("dofus-{0:D2}", i);
                        string customName = string.Format("Instance #{0}", i);
                        DeviceProfile prof = DeviceCatalog.GetDefaultProfileForIndex(i);
                        farm.ProvisionInstance(slotName, RamMb, Cores, prof.Id, customName, "");

                        // Also initialize isolated WebView2 profile directory
                        string wvProf = Path.Combine(
                            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                            "DofusFarm", "webview2_profiles", slotName);
                        try { Directory.CreateDirectory(wvProf); } catch { }
                    }

                    // 4. Smooth Cold-Run Pre-Warm if legacy emulator exists, otherwise finalize WebView2 setup
                    bool hasEmulatorBinary = File.Exists(Path.Combine(destSdk, @"emulator\emulator.exe"));
                    if (hasEmulatorBinary)
                    {
                        int batchSize = 2;
                        int totalBatches = (InstanceCount + batchSize - 1) / batchSize;
                        int currentBatch = 0;

                        for (int batchStart = 1; batchStart <= InstanceCount; batchStart += batchSize)
                        {
                            currentBatch++;
                            int batchEnd = Math.Min(InstanceCount, batchStart + batchSize - 1);
                            int countInBatch = batchEnd - batchStart + 1;
                            int batchBasePct = 62 + ((currentBatch - 1) * 32) / Math.Max(1, totalBatches);
                            int batchSpanPct = 32 / Math.Max(1, totalBatches);

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
                                string[] warmMessages = new[]
                                {
                                    string.Format("Starting background warmup for Instance #{0}..#{1}...", batchStart, batchEnd),
                                    string.Format("Optimizing graphics & memory cache for Instance #{0}..#{1}...", batchStart, batchEnd),
                                    string.Format("Pre-loading Dofus Touch for instant startup ({0}/{1})...", batchEnd, InstanceCount),
                                    string.Format("Saving instant-boot snapshot for Instance #{0}..#{1}...", batchStart, batchEnd)
                                };
                                while (!doneEvent.Wait(450))
                                {
                                    if (batchSw.Elapsed.TotalSeconds > 150) break;
                                    double ratio = Math.Min(0.95, batchSw.Elapsed.TotalSeconds / 38.0);
                                    int pct = batchBasePct + (int)(ratio * batchSpanPct);
                                    int mIdx = Math.Min(warmMessages.Length - 1, (int)(ratio * warmMessages.Length));
                                    Report(pct, warmMessages[mIdx]);
                                }
                            }
                        }
                    }
                    else
                    {
                        Report(70, "Setting up isolated browser storage and mobile disguise profiles...");
                        Thread.Sleep(150);
                        Report(82, "Configuring GPU WebGL acceleration and local virtual host mapping...");
                        Thread.Sleep(150);
                    }

                    // 4b. Configure Python AI Sentinel Environment & Dependencies
                    Report(88, "Checking Python environment for AI Network Health & Sentinel...");
                    SetupPythonEnvironment(TargetDir);

                    // 5. Desktop Shortcut, Registry & Environment Variables
                    Report(96, "Creating desktop shortcut & finalizing installation...");
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
                                k.SetValue("DefaultBackend", "WebView2");
                                k.SetValue("DefaultRamMb", RamMb);
                                k.SetValue("DefaultCores", Cores);
                                // Renderer check: pick the graphics backend that fits this machine once,
                                // at install time, instead of leaving the user to guess Host vs ANGLE.
                                if (k.GetValue("GpuMode") == null && farm != null)
                                {
                                    k.SetValue("GpuMode", farm.DetectBestGpuMode());
                                }
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
                                uk.SetValue("DisplayVersion", "2.3.0");
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
                        Environment.SetEnvironmentVariable("DOFUS_FARM_HOME",  TargetDir, EnvironmentVariableTarget.User);

                        if (hasEmulatorBinary)
                        {
                            Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT", destSdk);
                            Environment.SetEnvironmentVariable("ANDROID_HOME",     destSdk);
                            Environment.SetEnvironmentVariable("ANDROID_AVD_HOME", avdHome);

                            Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT", destSdk,   EnvironmentVariableTarget.User);
                            Environment.SetEnvironmentVariable("ANDROID_HOME",     destSdk,   EnvironmentVariableTarget.User);
                            Environment.SetEnvironmentVariable("ANDROID_AVD_HOME", avdHome,   EnvironmentVariableTarget.User);
                        }
                    }
                    catch { }

                    Report(100, "Installation complete! Launching Dofus Farm Manager...");
                    Thread.Sleep(350);

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

        private void SetupPythonEnvironment(string targetDir)
        {
            try
            {
                string pyExe = FindPythonExecutable(targetDir);
                string reqFile = Path.Combine(targetDir, "requirements.txt");

                if (!string.IsNullOrEmpty(pyExe) && File.Exists(reqFile))
                {
                    string getPip = Path.Combine(targetDir, @"runtimes\python\get-pip.py");
                    if (File.Exists(getPip))
                    {
                        try
                        {
                            var psiPip = new ProcessStartInfo
                            {
                                FileName = pyExe,
                                Arguments = string.Format("\"{0}\" --no-warn-script-location -q", getPip),
                                WorkingDirectory = targetDir,
                                CreateNoWindow = true,
                                UseShellExecute = false,
                                WindowStyle = ProcessWindowStyle.Hidden
                            };
                            using (var p = Process.Start(psiPip)) { if (p != null) p.WaitForExit(45000); }
                        }
                        catch { }
                    }

                    Report(88, "Configuring Python AI dependencies (torch, transformers, thx01)...");
                    ProcessStartInfo psi = new ProcessStartInfo
                    {
                        FileName = pyExe,
                        Arguments = string.Format("-m pip install -r \"{0}\" --no-warn-script-location -q", reqFile),
                        WorkingDirectory = targetDir,
                        CreateNoWindow = true,
                        UseShellExecute = false,
                        WindowStyle = ProcessWindowStyle.Hidden
                    };
                    using (Process proc = Process.Start(psi))
                    {
                        if (proc != null)
                        {
                            if (!proc.WaitForExit(90000))
                            {
                                try { proc.Kill(); } catch { }
                            }
                        }
                    }
                }
                else
                {
                    Report(88, "Python AI environment ready.");
                }
            }
            catch (Exception ex)
            {
                Report(88, "Python setup note: " + ex.Message);
            }
        }

        private static string FindPythonExecutable(string targetDir = null)
        {
            if (!string.IsNullOrEmpty(targetDir))
            {
                string localPort = Path.Combine(targetDir, @"runtimes\python\python.exe");
                if (File.Exists(localPort)) return localPort;
                localPort = Path.Combine(targetDir, @"python\python.exe");
                if (File.Exists(localPort)) return localPort;
            }

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

            foreach (string p in pathEnv.Split(';'))
            {
                if (string.IsNullOrEmpty(p)) continue;
                try
                {
                    string candidate = Path.Combine(p.Trim(), "py.exe");
                    if (File.Exists(candidate)) return candidate;
                }
                catch { }
            }

            return null;
        }

        public static void KillLingeringProcesses()
        {
            foreach (string pName in new[] { "qemu-system-x86_64", "emulator", "dofus-net-proxy", "DofusFarm", "adb" })
            {
                try
                {
                    foreach (Process p in Process.GetProcessesByName(pName))
                    {
                        using (p)
                        {
                            try { p.Kill(); p.WaitForExit(3000); } catch { }
                        }
                    }
                }
                catch { }
            }
        }
    }

    /// <summary>
    /// Custom-painted modern smooth progress bar with gradient fill (replaces chunky Windows Vista progress bar).
    /// </summary>
    public class SmoothProgressBar : Control
    {
        private int _value = 0;
        public int Value
        {
            get { return _value; }
            set
            {
                int clamped = Math.Min(100, Math.Max(0, value));
                if (_value != clamped)
                {
                    _value = clamped;
                    this.Invalidate();
                }
            }
        }

        public SmoothProgressBar()
        {
            this.SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
            this.Height = 18;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            if (this.Width <= 4 || this.Height <= 4) return;
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            Rectangle rect = new Rectangle(0, 0, this.Width - 1, this.Height - 1);
            using (SolidBrush bgBrush = new SolidBrush(Color.FromArgb(26, 28, 36)))
            using (Pen borderPen = new Pen(Color.FromArgb(45, 48, 62), 1f))
            {
                g.FillRectangle(bgBrush, rect);
                g.DrawRectangle(borderPen, rect);
            }

            if (_value > 0)
            {
                int fillW = Math.Min(this.Width - 2, Math.Max(4, (int)Math.Round(((this.Width - 2) * _value) / 100.0)));
                int fillH = Math.Max(2, this.Height - 2);
                Rectangle fillRect = new Rectangle(1, 1, fillW, fillH);
                using (LinearGradientBrush grad = new LinearGradientBrush(
                    fillRect,
                    Color.FromArgb(37, 99, 235),
                    Color.FromArgb(16, 185, 129),
                    LinearGradientMode.Horizontal))
                {
                    g.FillRectangle(grad, fillRect);
                }
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

        private Panel _hwBannerPanel;
        private Label _lblSpecs;

        private Label _lblInstances;
        private ComboBox _cmbInstances;
        private Label _lblRam;
        private ComboBox _cmbRam;
        private Label _lblCores;
        private ComboBox _cmbCores;

        private Label _lblTarget;
        private TextBox _txtTarget;
        private Button _btnBrowse;

        private SmoothProgressBar _progBar;
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
            this.Text = "Dofus Touch Farm — Setup";
            this.Size = new Size(640, 435);
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
                Height = 70,
                BackColor = Color.FromArgb(24, 26, 34),
                Padding = new Padding(22, 12, 22, 10)
            };

            _titleLabel = new Label
            {
                Text = "DOFUS TOUCH FARM",
                Font = new Font("Segoe UI", 13f, FontStyle.Bold),
                ForeColor = Color.White,
                AutoSize = true,
                Location = new Point(24, 12)
            };

            _subtitleLabel = new Label
            {
                Text = "Ultra-Fast Lightweight Standalone Multi-Account Client  •  Automatic Setup",
                Font = new Font("Segoe UI", 9f),
                ForeColor = Color.FromArgb(148, 163, 184),
                AutoSize = true,
                Location = new Point(25, 39)
            };

            _lblTimer = new Label
            {
                Text = "00:00",
                Font = new Font("Consolas", 10f, FontStyle.Bold),
                ForeColor = Color.FromArgb(52, 211, 153),
                AutoSize = true,
                Location = new Point(552, 16),
                Visible = false
            };

            _headerPanel.Controls.Add(_titleLabel);
            _headerPanel.Controls.Add(_subtitleLabel);
            _headerPanel.Controls.Add(_lblTimer);

            // Reassuring Hardware Verification Banner
            _hwBannerPanel = new Panel
            {
                Location = new Point(25, 84),
                Size = new Size(575, 34),
                BackColor = Color.FromArgb(22, 38, 36)
            };
            _lblSpecs = new Label
            {
                Text = "Checking PC hardware configuration...",
                Location = new Point(12, 7),
                Size = new Size(550, 20),
                ForeColor = Color.FromArgb(52, 211, 153),
                Font = new Font("Segoe UI", 9f, FontStyle.Bold)
            };
            _hwBannerPanel.Controls.Add(_lblSpecs);

            // Primary Allocation Controls (Instances, RAM, CPU Cores)
            _lblInstances = new Label { Text = "Accounts (Instances):", Location = new Point(25, 134), AutoSize = true, ForeColor = Color.FromArgb(203, 213, 225) };
            _cmbInstances = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 40),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 175,
                Location = new Point(25, 157)
            };
            _cmbInstances.Items.AddRange(new object[] { "1 Instance", "2 Instances", "3 Instances", "4 Instances (Recommended)", "6 Instances", "8 Instances" });
            _cmbInstances.SelectedIndex = 3;

            _lblRam = new Label { Text = "RAM per Instance:", Location = new Point(218, 134), AutoSize = true, ForeColor = Color.FromArgb(203, 213, 225) };
            _cmbRam = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 40),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 180,
                Location = new Point(218, 157)
            };
            _cmbRam.Items.AddRange(new object[] { "512 MB", "768 MB", "1024 MB (Recommended)", "1536 MB", "2048 MB", "3072 MB", "4096 MB", "8192 MB" });
            _cmbRam.SelectedIndex = 2;

            _lblCores = new Label { Text = "CPU Cores per Instance:", Location = new Point(416, 134), AutoSize = true, ForeColor = Color.FromArgb(203, 213, 225) };
            _cmbCores = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 40),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 184,
                Location = new Point(416, 157)
            };
            _cmbCores.Items.AddRange(new object[] { "1 Core (Recommended)", "2 Cores", "3 Cores", "4 Cores", "6 Cores", "8 Cores" });
            _cmbCores.SelectedIndex = 0;

            // Installation Directory
            _lblTarget = new Label { Text = "Installation Folder:", Location = new Point(25, 200), AutoSize = true, ForeColor = Color.FromArgb(203, 213, 225) };
            _txtTarget = new TextBox
            {
                Text = _targetDir,
                Location = new Point(25, 223),
                Width = 472,
                BackColor = Color.FromArgb(26, 28, 36),
                ForeColor = Color.FromArgb(226, 232, 240),
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 9.5f)
            };
            _btnBrowse = new Button
            {
                Text = "Change...",
                Location = new Point(505, 221),
                Size = new Size(95, 27),
                BackColor = Color.FromArgb(40, 43, 56),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnBrowse.FlatAppearance.BorderSize = 0;
            _btnBrowse.Click += OnBrowseClick;

            // Progress Bar & Status
            _lblStatus = new Label
            {
                Text = "Ready to install. Optimal settings have been selected for your PC.",
                Location = new Point(25, 272),
                Size = new Size(500, 22),
                ForeColor = Color.FromArgb(203, 213, 225)
            };

            _lblPctBadge = new Label
            {
                Text = "0%",
                Location = new Point(530, 272),
                Size = new Size(70, 22),
                TextAlign = ContentAlignment.MiddleRight,
                ForeColor = Color.FromArgb(56, 189, 248),
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold)
            };

            _progBar = new SmoothProgressBar
            {
                Location = new Point(25, 298),
                Size = new Size(575, 18),
                Value = 0
            };

            // Bottom Action Buttons
            _btnInstall = new Button
            {
                Text = "Install Now",
                Location = new Point(345, 336),
                Size = new Size(155, 40),
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 10f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnInstall.FlatAppearance.BorderSize = 0;
            _btnInstall.MouseEnter += delegate { if (_btnInstall.Enabled) _btnInstall.BackColor = Color.FromArgb(59, 130, 246); };
            _btnInstall.MouseLeave += delegate { if (_btnInstall.Enabled) _btnInstall.BackColor = Color.FromArgb(37, 99, 235); };
            _btnInstall.Click += OnInstallClick;

            _btnCancel = new Button
            {
                Text = "Cancel",
                Location = new Point(510, 336),
                Size = new Size(90, 40),
                BackColor = Color.FromArgb(40, 43, 56),
                ForeColor = Color.FromArgb(203, 213, 225),
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
            this.Controls.Add(_hwBannerPanel);
            this.Controls.Add(_lblInstances);
            this.Controls.Add(_cmbInstances);
            this.Controls.Add(_lblRam);
            this.Controls.Add(_cmbRam);
            this.Controls.Add(_lblCores);
            this.Controls.Add(_cmbCores);
            this.Controls.Add(_lblTarget);
            this.Controls.Add(_txtTarget);
            this.Controls.Add(_btnBrowse);
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
                        _lblSpecs.Text = string.Format("✓ PC Ready: {0}  •  {1} GB RAM ({2} Threads)",
                            specs.CpuName, specs.HostRamGB, specs.LogicalThreads);
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
                _btnInstall.Text = "Installing...";
                _btnInstall.BackColor = Color.FromArgb(51, 65, 85);
                this.Cursor = Cursors.WaitCursor;
            }
            else
            {
                _installStopwatch.Stop();
                _uiTimer.Stop();
                _btnInstall.Text = "Install Now";
                _btnInstall.BackColor = Color.FromArgb(37, 99, 235);
                this.Cursor = Cursors.Default;
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_isInstalling && e.CloseReason == CloseReason.UserClosing)
            {
                DialogResult dr = MessageBox.Show(
                    "Setup is currently configuring your game instances.\nAre you sure you want to cancel?",
                    "Cancel Setup?",
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
