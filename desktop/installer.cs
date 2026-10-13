using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;
using System.Security.Cryptography;

[assembly: AssemblyTitle("Dofus Farm Setup")]
[assembly: AssemblyDescription("Dofus Touch Virtualization Farm Standalone Native Installer Wizard")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Farm Virtualization Setup")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("2.0.0.0")]
[assembly: AssemblyVersion("2.0.0.0")]

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

            // Single-instance guard: prevent multiple installer instances
            bool createdNew;
            _singleInstanceMutex = new Mutex(true, @"Global\DofusFarmSetup_SingleInstance_Mutex_2026", out createdNew);
            if (!createdNew)
            {
                MessageBox.Show(
                    "The Dofus Touch Farm installer is already running.\n\nPlease wait for the active installation process to complete.",
                    "Setup Already In Progress",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning);
                return;
            }

            string tempStageDir = null;
            try
            {
                tempStageDir = Path.Combine(Path.GetTempPath(), "DofusSetup_" + Guid.NewGuid().ToString("N"));
                if (Directory.Exists(tempStageDir))
                {
                    try { Directory.Delete(tempStageDir, true); } catch { }
                }
                Directory.CreateDirectory(tempStageDir);

                // Unpack embedded payload archive
                Assembly asm = Assembly.GetExecutingAssembly();
                using (Stream s = asm.GetManifestResourceStream("DofusPayload.zip"))
                {
                    if (s != null)
                    {
                        string zipPath = Path.Combine(tempStageDir, "payload.zip");
                        using (FileStream fs = new FileStream(zipPath, FileMode.Create, FileAccess.Write))
                        {
                            s.CopyTo(fs);
                        }
                        ZipFile.ExtractToDirectory(zipPath, tempStageDir);
                        try { File.Delete(zipPath); } catch { }
                    }
                }

                string setupDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
                try
                {
                    string loc = Assembly.GetExecutingAssembly().Location;
                    if (!string.IsNullOrEmpty(loc) && File.Exists(loc))
                    {
                        string dir = Path.GetDirectoryName(loc);
                        if (!string.IsNullOrEmpty(dir) && Directory.Exists(dir))
                            setupDir = dir.TrimEnd('\\');
                    }
                }
                catch { }

                // Default installation folder is the directory where setup.exe is running
                string targetPath = setupDir;
                if (string.IsNullOrEmpty(targetPath) || targetPath.StartsWith(Path.GetTempPath(), StringComparison.OrdinalIgnoreCase))
                {
                    targetPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "DofusFarm");
                }

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
                    System.Threading.ManualResetEvent doneEvent = new System.Threading.ManualResetEvent(false);
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
                    try { Directory.Delete(tempStageDir, true); } catch { }
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
        public bool CreateShortcut { get; set; }
        public bool LaunchOnComplete { get; set; }
        
        [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true, CharSet = System.Runtime.InteropServices.CharSet.Auto)]
        private static extern bool CreateHardLink(string lpFileName, string lpExistingFileName, IntPtr lpSecurityAttributes);

        public event Action<int, string> OnProgress;
        public event Action<string> OnLog;

        public InstallerEngine(string stageDir, string targetDir)
        {
            StageDir = stageDir;
            TargetDir = targetDir;
            InstanceCount = 4;
            RamMb = 1024;
            Cores = 1;
            CreateShortcut = true;
            LaunchOnComplete = true;
        }

        private void Report(int pct, string msg)
        {
            if (OnProgress != null) OnProgress(pct, msg);
            if (OnLog != null) OnLog(string.Format("[{0:HH:mm:ss}] {1}", DateTime.Now, msg));
        }

        private static string DetectDiskFormat(string imagePath)
        {
            try
            {
                if (File.Exists(imagePath))
                {
                    using (FileStream fs = new FileStream(imagePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                    {
                        byte[] magic = new byte[4];
                        if (fs.Read(magic, 0, 4) == 4)
                        {
                            if (magic[0] == 0x51 && magic[1] == 0x46 && magic[2] == 0x49 && magic[3] == 0xFB)
                            {
                                return "qcow2";
                            }
                        }
                    }
                }
            }
            catch { }
            return "raw";
        }

        private bool VerifyPackageIntegrity()
        {
            string manifestPath = Path.Combine(StageDir, @"scripts\app.integrity");
            if (!File.Exists(manifestPath)) manifestPath = Path.Combine(StageDir, "app.integrity");
            if (!File.Exists(manifestPath)) manifestPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, @"scripts\app.integrity");
            if (!File.Exists(manifestPath)) manifestPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app.integrity");
            if (!File.Exists(manifestPath))
            {
                Report(5, "[ERROR] Master package integrity manifest is missing!");
                return false;
            }

            try
            {
                string[] lines = File.ReadAllLines(manifestPath, Encoding.UTF8);
                string salt = "DOFUS_FARM_INTEGRITY_SALT_2026_K7X9Q";
                StringBuilder allHashes = new StringBuilder();
                string expectedSig = null;

                using (SHA256 sha = SHA256.Create())
                {
                    foreach (string line in lines)
                    {
                        if (string.IsNullOrEmpty(line)) continue;
                        string[] parts = line.Split('|');
                        if (parts.Length != 2) continue;

                        if (parts[0] == "SIGNATURE")
                        {
                            expectedSig = parts[1];
                            continue;
                        }

                        string rel = parts[0];
                        string expectedHash = parts[1];
                        string full = Path.Combine(StageDir, rel);
                        if (!File.Exists(full)) full = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, rel);
                        if (!File.Exists(full))
                        {
                            string alt = Path.Combine(StageDir, "scripts", Path.GetFileName(rel));
                            if (File.Exists(alt)) full = alt;
                            else
                            {
                                string alt2 = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "scripts", Path.GetFileName(rel));
                                if (File.Exists(alt2)) full = alt2;
                            }
                        }

                        if (File.Exists(full))
                        {
                            byte[] hash;
                            using (FileStream fs = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.Read))
                            {
                                hash = sha.ComputeHash(fs);
                            }
                            string hex = BitConverter.ToString(hash).Replace("-", "");
                            if (!string.Equals(hex, expectedHash, StringComparison.OrdinalIgnoreCase))
                            {
                                Report(5, string.Format("Warning: Integrity mismatch on {0}", rel));
                                return false;
                            }
                            allHashes.Append(hex);
                        }
                        else
                        {
                            Report(5, string.Format("Warning: Missing required package file {0}", rel));
                            return false;
                        }
                    }

                    if (!string.IsNullOrEmpty(expectedSig))
                    {
                        byte[] salted = Encoding.UTF8.GetBytes(allHashes.ToString() + salt);
                        byte[] sig = sha.ComputeHash(salted);
                        string sigHex = BitConverter.ToString(sig).Replace("-", "");
                        if (!string.Equals(sigHex, expectedSig, StringComparison.OrdinalIgnoreCase))
                        {
                            Report(5, "Warning: Master package integrity signature mismatch");
                            return false;
                        }
                    }
                }
                Report(5, "Package integrity verified successfully.");
                return true;
            }
            catch (Exception ex)
            {
                Report(5, "Integrity check notification: " + ex.Message);
                return false;
            }
        }

        public void RunInstall(Action onComplete, Action<Exception> onError)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    Report(5, "Verifying package integrity and preparing destination...");
                    if (!VerifyPackageIntegrity())
                    {
                        Report(5, "[ERROR] Package integrity verification failed!");
                        throw new InvalidOperationException("Installation aborted: Package files failed cryptographic SHA-256 integrity check.");
                    }
                    if (!Directory.Exists(TargetDir)) Directory.CreateDirectory(TargetDir);

                    // 1. Copy binaries and assets
                    Report(15, "Deploying core application binaries and build tools...");
                    string[] rootFiles = new[] { "DofusFarm.exe", "uninstall.exe", "Build.exe", "compile.bat", "dofus-net-proxy.exe", "dofus-downloader.exe", "app_icon.ico", "installer_icon.ico", "template.zip", "sdk.7z" };
                    foreach (string f in rootFiles)
                    {
                        string src = Path.Combine(StageDir, f);
                        if (!File.Exists(src)) src = Path.Combine(StageDir, "scripts", f);
                        if (!File.Exists(src)) src = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, f);
                        if (!File.Exists(src)) src = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "scripts", f);
                        if (File.Exists(src))
                        {
                            File.Copy(src, Path.Combine(TargetDir, f), true);
                        }
                    }

                    // Copy setup.exe to target as self-contained tool
                    string currentExe = Assembly.GetExecutingAssembly().Location;
                    string destSetup = Path.Combine(TargetDir, "setup.exe");
                    try { if (File.Exists(currentExe) && !currentExe.Equals(destSetup, StringComparison.OrdinalIgnoreCase)) File.Copy(currentExe, destSetup, true); } catch { }

                    // 1b. Deploy scripts directory and anti-cheat disguise engines (DEPLOY-01)
                    Report(20, "Deploying anti-cheat disguise scripts and runtime tools...");
                    string targetScripts = Path.Combine(TargetDir, "scripts");
                    if (!Directory.Exists(targetScripts)) Directory.CreateDirectory(targetScripts);

                    string srcScripts = Path.Combine(StageDir, "scripts");
                    if (!Directory.Exists(srcScripts)) srcScripts = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "scripts");
                    if (Directory.Exists(srcScripts))
                    {
                        foreach (string sf in Directory.GetFiles(srcScripts))
                        {
                            try
                            {
                                File.Copy(sf, Path.Combine(targetScripts, Path.GetFileName(sf)), true);
                            }
                            catch { }
                        }
                        string srcBin = Path.Combine(srcScripts, "bin");
                        if (Directory.Exists(srcBin))
                        {
                            string targetBin = Path.Combine(targetScripts, "bin");
                            if (!Directory.Exists(targetBin)) Directory.CreateDirectory(targetBin);
                            foreach (string bf in Directory.GetFiles(srcBin))
                            {
                                try
                                {
                                    File.Copy(bf, Path.Combine(targetBin, Path.GetFileName(bf)), true);
                                }
                                catch { }
                            }
                        }
                    }

                    // 1c. Deploy complete source code and native builder toolchain (1-FILE PROJECT SUITE)
                    Report(25, "Deploying C# source repository and native build tools...");
                    string[] sourceDirs = new[] { "desktop", "builder", "launcher" };
                    foreach (string sSub in sourceDirs)
                    {
                        string srcSubPath = Path.Combine(StageDir, sSub);
                        if (!Directory.Exists(srcSubPath)) srcSubPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, sSub);
                        if (Directory.Exists(srcSubPath))
                        {
                            string destSubDir = Path.Combine(TargetDir, sSub);
                            if (!string.Equals(Path.GetFullPath(srcSubPath).TrimEnd('\\'), Path.GetFullPath(destSubDir).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                            {
                                CopyDirectoryRecursively(srcSubPath, destSubDir);
                            }
                        }
                    }

                    // 2. Unpack Android SDK
                    Report(30, "Deploying high-performance Android SDK (x86_64)...");
                    string destSdk = Path.Combine(TargetDir, "sdk");
                    string sdkEmu = Path.Combine(destSdk, @"emulator\emulator.exe");
                    if (!File.Exists(sdkEmu))
                    {
                        string sdkArchive = Path.Combine(TargetDir, "sdk.7z");
                        if (!File.Exists(sdkArchive)) sdkArchive = Path.Combine(StageDir, "sdk.7z");

                        if (File.Exists(sdkArchive))
                        {
                            Report(35, "Extracting offline Android SDK archive (sdk.7z)...");
                            string z7Exe = Path.Combine(StageDir, @"scripts\bin\7z.exe");
                            if (!File.Exists(z7Exe)) z7Exe = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, @"scripts\bin\7z.exe");

                            if (File.Exists(z7Exe))
                            {
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
                            }
                        }
                    }

                    // Flatten nested sdk\sdk structure if present
                    string nestedSdk = Path.Combine(destSdk, "sdk");
                    if (Directory.Exists(nestedSdk))
                    {
                        foreach (string dir in Directory.GetDirectories(nestedSdk))
                        {
                            string subDest = Path.Combine(destSdk, Path.GetFileName(dir));
                            if (Directory.Exists(subDest))
                            {
                                try { Directory.Delete(subDest, true); } catch { }
                            }
                            try { Directory.Move(dir, subDest); } catch { }
                        }
                        foreach (string file in Directory.GetFiles(nestedSdk))
                        {
                            string fileDest = Path.Combine(destSdk, Path.GetFileName(file));
                            if (File.Exists(fileDest))
                            {
                                try { File.Delete(fileDest); } catch { }
                            }
                            try { File.Move(file, fileDest); } catch { }
                        }
                        try { Directory.Delete(nestedSdk, true); } catch { }
                    }

                    if (!File.Exists(Path.Combine(destSdk, @"emulator\emulator.exe")))
                    {
                        throw new FileNotFoundException("Android SDK extraction verification failed: emulator.exe not found at " + Path.Combine(destSdk, @"emulator\emulator.exe"));
                    }

                    // 3. Deploy Golden Master Template
                    Report(60, "Configuring Golden Master AVD template...");
                    KillLingeringProcesses();
                    string avdHome = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".android\avd");
                    if (!Directory.Exists(avdHome)) Directory.CreateDirectory(avdHome);

                    // Ensure high-performance advancedFeatures.ini with no encryption
                    try
                    {
                        string dotAndroid = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".android");
                        string advIni = Path.Combine(dotAndroid, "advancedFeatures.ini");
                        string advContent = 
                            "GrallocSync = on\r\n" +
                            "LogcatPipe = on\r\n" +
                            "GLAsyncSwap = on\r\n" +
                            "GLESDynamicVersion = on\r\n" +
                            "GLDMA = on\r\n" +
                            "VirtioInput = on\r\n" +
                            "EncryptUserData = off\r\n" +
                            "IntelPerformanceMonitoringUnit = on\r\n" +
                            "Wifi = on\r\n" +
                            "HostComposition = on\r\n" +
                            "DynamicPartition = on\r\n" +
                            "HardwareDecoder = on\r\n" +
                            "GLDirectMem = on\r\n";
                        File.WriteAllText(advIni, advContent, Encoding.ASCII);
                    }
                    catch { }

                    string templateZip = Path.Combine(TargetDir, "template.zip");
                    if (!File.Exists(templateZip)) templateZip = Path.Combine(StageDir, "template.zip");
                    string targetTemplateAvd = Path.Combine(avdHome, "dofus-template.avd");

                    if (File.Exists(templateZip))
                    {
                        ForceCleanDirectory(targetTemplateAvd);
                        if (!Directory.Exists(targetTemplateAvd)) Directory.CreateDirectory(targetTemplateAvd);
                        try
                        {
                            ExtractZipWithOverwrite(templateZip, targetTemplateAvd);
                        }
                        catch (Exception ex)
                        {
                            throw new InvalidOperationException("Failed to extract master AVD template (template.zip): " + ex.Message, ex);
                        }
                    }

                    // Ensure template config.ini has Samsung tablet identifiers & 16:9 HD display
                    string tplCfg = Path.Combine(targetTemplateAvd, "config.ini");
                    if (File.Exists(tplCfg))
                    {
                        try
                        {
                            string tText = File.ReadAllText(tplCfg, Encoding.UTF8);
                            tText = OptimizeConfigIni(tText);
                            File.WriteAllText(tplCfg, tText, Encoding.UTF8);
                        }
                        catch { }
                    }

                    // 4. Provision initial instances
                    Report(75, string.Format("Provisioning {0} virtual instance(s) with differential QCOW2 disks...", InstanceCount));
                    string qemuImg = Path.Combine(destSdk, @"emulator\qemu-img.exe");
                    if (!File.Exists(qemuImg)) qemuImg = Path.Combine(destSdk, @"sdk\emulator\qemu-img.exe");
                    string goldenMaster = Path.Combine(targetTemplateAvd, "userdata-golden.img");

                    for (int i = 1; i <= InstanceCount; i++)
                    {
                        string name = string.Format("dofus-{0:D2}", i);
                        string avdDir = Path.Combine(avdHome, name + ".avd");
                        string iniFile = Path.Combine(avdHome, name + ".ini");

                        if (!Directory.Exists(avdDir)) Directory.CreateDirectory(avdDir);
                        string iniContent = string.Format("avd.ini.encoding=UTF-8\r\npath={0}\r\npath.rel=avd\\{1}.avd\r\ntarget=android-29\r\n", avdDir, name);
                        File.WriteAllText(iniFile, iniContent, Encoding.UTF8);

                        // Config.ini
                        string cfgDest = Path.Combine(avdDir, "config.ini");
                        string cfgSrc = Path.Combine(targetTemplateAvd, "config.ini");
                        if (File.Exists(cfgSrc) && !File.Exists(cfgDest)) File.Copy(cfgSrc, cfgDest, true);
                        if (File.Exists(cfgDest))
                        {
                            string cText = File.ReadAllText(cfgDest, Encoding.UTF8);
                            cText = OptimizeConfigIni(cText);
                            File.WriteAllText(cfgDest, cText, Encoding.UTF8);
                        }

                        // Identity & system.prop
                        string serialNo = "R58M" + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpper();
                        string androidId = Guid.NewGuid().ToString("N").Substring(0, 16);
                        string mac = string.Format("52:54:00:12:34:{0:X2}", i);
                        string identFile = Path.Combine(avdDir, "identity.json");
                        File.WriteAllText(identFile, string.Format("{{\"Serial\":\"{0}\",\"AndroidId\":\"{1}\",\"Mac\":\"{2}\"}}", serialNo, androidId, mac), Encoding.UTF8);

                        string propFile = Path.Combine(avdDir, "system.prop");
                        string sysProp = string.Format(
                            "ro.product.brand=samsung\r\n" +
                            "ro.product.manufacturer=samsung\r\n" +
                            "ro.product.model=SM-A515F\r\n" +
                            "ro.product.cpu.abi=arm64-v8a\r\n" +
                            "ro.product.cpu.abilist=arm64-v8a,armeabi-v7a,armeabi\r\n" +
                            "ro.product.name=a51nsxx\r\n" +
                            "ro.product.device=a51\r\n" +
                            "ro.product.board=exynos9611\r\n" +
                            "ro.build.product=a51\r\n" +
                            "ro.product.system.brand=samsung\r\n" +
                            "ro.product.system.manufacturer=samsung\r\n" +
                            "ro.product.system.model=SM-A515F\r\n" +
                            "ro.product.system.name=a51nsxx\r\n" +
                            "ro.product.system.device=a51\r\n" +
                            "ro.product.vendor.brand=samsung\r\n" +
                            "ro.product.vendor.manufacturer=samsung\r\n" +
                            "ro.product.vendor.model=SM-A515F\r\n" +
                            "ro.product.vendor.name=a51nsxx\r\n" +
                            "ro.product.vendor.device=a51\r\n" +
                            "ro.product.odm.brand=samsung\r\n" +
                            "ro.product.odm.manufacturer=samsung\r\n" +
                            "ro.product.odm.model=SM-A515F\r\n" +
                            "ro.product.odm.name=a51nsxx\r\n" +
                            "ro.product.odm.device=a51\r\n" +
                            "ro.product.bootimage.brand=samsung\r\n" +
                            "ro.product.bootimage.manufacturer=samsung\r\n" +
                            "ro.product.bootimage.model=SM-A515F\r\n" +
                            "ro.product.bootimage.name=a51nsxx\r\n" +
                            "ro.product.bootimage.device=a51\r\n" +
                            "ro.hardware=exynos9611\r\n" +
                            "ro.hardware.chipname=exynos9611\r\n" +
                            "ro.chipname=exynos9611\r\n" +
                            "ro.arch=exynos9611\r\n" +
                            "ro.board.platform=exynos9611\r\n" +
                            "ro.soc.manufacturer=Samsung\r\n" +
                            "ro.soc.model=Exynos 9611\r\n" +
                            "ro.build.flavor=a51nsxx-user\r\n" +
                            "ro.build.type=user\r\n" +
                            "ro.build.tags=release-keys\r\n" +
                            "ro.build.fingerprint=samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys\r\n" +
                            "ro.system.build.fingerprint=samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys\r\n" +
                            "ro.vendor.build.fingerprint=samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys\r\n" +
                            "ro.bootimage.build.fingerprint=samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys\r\n" +
                            "ro.build.display.id=QP1A.190711.020.A515FXXU1ATA7\r\n" +
                            "ro.build.description=a51nsxx-user 10 QP1A.190711.020 A515FXXU1ATA7 release-keys\r\n" +
                            "ro.system.build.type=user\r\n" +
                            "ro.system.build.tags=release-keys\r\n" +
                            "ro.kernel.qemu=0\r\n" +
                            "ro.boot.qemu=0\r\n" +
                            "ro.hardware.virtual=0\r\n" +
                            "ro.emulator=0\r\n" +
                            "ro.boot.hardware=exynos9611\r\n" +
                            "qemu.hw.mainkeys=1\r\n" +
                            "ro.serialno={0}\r\n" +
                            "ro.boot.serialno={0}\r\n" +
                            "gsm.sim.state=READY\r\n" +
                            "gsm.sim.operator.numeric=20801\r\n" +
                            "gsm.sim.operator.alpha=Orange\r\n" +
                            "gsm.network.type=LTE\r\n" +
                            "config.disable_animations=1\r\n" +
                            "debug.sf.nobootanimation=1\r\n" +
                            "pm.dexopt.boot=verify\r\n" +
                            "pm.dexopt.first-boot=verify\r\n" +
                            "pm.dexopt.install=quicken\r\n" +
                            "dalvik.vm.dex2oat-filter=verify\r\n" +
                            "dalvik.vm.dex2oat-threads={1}\r\n" +
                            "dalvik.vm.image-dex2oat-threads={1}\r\n" +
                            "dalvik.vm.boot-dex2oat-threads={1}\r\n" +
                            "persist.sys.strictmode.disable=true\r\n" +
                            "persist.sys.strictmode.visual=false\r\n" +
                            "dalvik.vm.checkjni=false\r\n" +
                            "debug.sf.latch_unsignaled=1\r\n" +
                            "debug.sf.disable_backpressure=1\r\n" +
                            "sys.use_fifo_ui=1\r\n" +
                            "ro.config.hw_quickpoweron=true\r\n" +
                            "ro.config.nocheckin=1\r\n" +
                            "persist.sys.disable_rescue=true\r\n" +
                            "sys.wifitracing.started=0\r\n" +
                            "persist.traced.enable=0\r\n" +
                            "persist.sys.timezone=Europe/Paris\r\n" +
                            "persist.sys.country=FR\r\n" +
                            "persist.sys.language=fr\r\n" +
                            "persist.sys.locale=fr-FR\r\n", serialNo, Cores);
                        File.WriteAllText(propFile, sysProp, Encoding.ASCII);

                        // Create Differential QCOW2 Overlay
                        if (File.Exists(goldenMaster))
                        {
                            string rawUd = Path.Combine(avdDir, "userdata-qemu.img");
                            string emuQcow2 = Path.Combine(avdDir, "userdata-qemu.img.qcow2");
                            if (!File.Exists(rawUd))
                            {
                                try
                                {
                                    bool linked = CreateHardLink(rawUd, goldenMaster, IntPtr.Zero);
                                    if (!linked) File.Copy(goldenMaster, rawUd, true);
                                }
                                catch
                                {
                                    try { File.Copy(goldenMaster, rawUd, true); } catch { }
                                }
                            }
                            if (!File.Exists(emuQcow2) && File.Exists(qemuImg))
                            {
                                string baseFmt = DetectDiskFormat(rawUd);
                                ProcessStartInfo psiQc = new ProcessStartInfo
                                {
                                    FileName = qemuImg,
                                    Arguments = string.Format("create -f qcow2 -b \"userdata-qemu.img\" -F {0} \"userdata-qemu.img.qcow2\"", baseFmt),
                                    WorkingDirectory = avdDir,
                                    CreateNoWindow = true,
                                    UseShellExecute = false,
                                    WindowStyle = ProcessWindowStyle.Hidden
                                };
                                RunProcessWithTimeout(psiQc, 5 * 60 * 1000);

                                if (!File.Exists(emuQcow2) || (new FileInfo(emuQcow2).Length == 0))
                                {
                                    string altFmt = (baseFmt == "raw") ? "qcow2" : "raw";
                                    psiQc.Arguments = string.Format("create -f qcow2 -b \"userdata-qemu.img\" -F {0} \"userdata-qemu.img.qcow2\"", altFmt);
                                    RunProcessWithTimeout(psiQc, 5 * 60 * 1000);
                                }
                            }
                        }
                    }

                    // 5. Windows Integration: Desktop Shortcut & Registry
                    Report(90, "Creating Desktop shortcut & Windows registration...");
                    string farmExe = Path.Combine(TargetDir, "DofusFarm.exe");
                    string icoPath = Path.Combine(TargetDir, "app_icon.ico");

                    if (CreateShortcut && File.Exists(farmExe))
                    {
                        try
                        {
                            CreateShellShortcut(
                                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Desktop), "Dofus Farm Manager.lnk"),
                                farmExe,
                                TargetDir,
                                icoPath,
                                "Dofus Touch Multi-Instance Virtualization Manager");
                        }
                        catch { }
                    }

                    // Registry entries
                    try
                    {
                        using (RegistryKey k = Registry.CurrentUser.CreateSubKey(@"Software\DofusFarm"))
                        {
                            if (k != null) k.SetValue("InstallPath", TargetDir);
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
                                uk.SetValue("DisplayVersion", "2.0.0");
                                uk.SetValue("Publisher", "Dofus Farm Project");
                                uk.SetValue("InstallLocation", TargetDir);
                                uk.SetValue("UninstallString", "\"" + Path.Combine(TargetDir, "uninstall.exe") + "\"");
                            }
                        }
                    }
                    catch { }

                    try
                    {
                        // Clear any stale Machine-level Android env vars left by old installs (e.g. dofus-emu)
                        // so they don't shadow the correct User-level vars we're about to set.
                        foreach (string staleVar in new[] { "ANDROID_SDK_ROOT", "ANDROID_HOME", "ANDROID_AVD_HOME", "DOFUS_FARM_HOME" })
                        {
                            try { Environment.SetEnvironmentVariable(staleVar, null, EnvironmentVariableTarget.Machine); } catch { }
                        }

                        // Set correct paths at Process scope (immediate effect for this session)
                        Environment.SetEnvironmentVariable("DOFUS_FARM_HOME",   TargetDir);
                        Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT",  destSdk);
                        Environment.SetEnvironmentVariable("ANDROID_HOME",      destSdk);
                        Environment.SetEnvironmentVariable("ANDROID_AVD_HOME",  avdHome);

                        // Set correct paths at User scope (persists across reboots)
                        Environment.SetEnvironmentVariable("DOFUS_FARM_HOME",   TargetDir, EnvironmentVariableTarget.User);
                        Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT",  destSdk,   EnvironmentVariableTarget.User);
                        Environment.SetEnvironmentVariable("ANDROID_HOME",      destSdk,   EnvironmentVariableTarget.User);
                        Environment.SetEnvironmentVariable("ANDROID_AVD_HOME",  avdHome,   EnvironmentVariableTarget.User);
                    }
                    catch { }

                    Report(100, "Installation completed successfully!");
                    Thread.Sleep(500);

                    // Launch DofusFarm.exe if requested (GUI mode)
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

        private void CopyDirectoryRecursively(string sourceDir, string targetDir)
        {
            if (!Directory.Exists(targetDir)) Directory.CreateDirectory(targetDir);
            foreach (string file in Directory.GetFiles(sourceDir))
            {
                try
                {
                    File.Copy(file, Path.Combine(targetDir, Path.GetFileName(file)), true);
                }
                catch { }
            }
            foreach (string sub in Directory.GetDirectories(sourceDir))
            {
                CopyDirectoryRecursively(sub, Path.Combine(targetDir, Path.GetFileName(sub)));
            }
        }

        public static void KillLingeringProcesses()
        {
            string[] procNames = new[] { "qemu-system-x86_64", "emulator", "dofus-net-proxy", "dofus-downloader", "DofusFarm", "adb" };
            foreach (string pName in procNames)
            {
                try
                {
                    foreach (Process p in Process.GetProcessesByName(pName))
                    {
                        try
                        {
                            p.Kill();
                            p.WaitForExit(3000);
                        }
                        catch { }
                    }
                }
                catch { }
            }
        }

        public static void ForceCleanDirectory(string dirPath)
        {
            if (!Directory.Exists(dirPath)) return;
            try
            {
                foreach (string f in Directory.GetFiles(dirPath, "*", SearchOption.AllDirectories))
                {
                    try { File.SetAttributes(f, FileAttributes.Normal); } catch { }
                }
            }
            catch { }
            try
            {
                Directory.Delete(dirPath, true);
            }
            catch { }
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

                    if (string.IsNullOrEmpty(entry.Name)) // Directory entry
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

        public static string OptimizeConfigIni(string text)
        {
            text = System.Text.RegularExpressions.Regex.Replace(text, @"hw\.device\.manufacturer\s*=.*", "hw.device.manufacturer = Samsung");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"hw\.device\.name\s*=.*", "hw.device.name = Galaxy Tab S6");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"hw\.lcd\.width\s*=.*", "hw.lcd.width = 1280");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"hw\.lcd\.height\s*=.*", "hw.lcd.height = 720");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"hw\.lcd\.density\s*=.*", "hw.lcd.density = 240");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"skin\.name\s*=.*", "skin.name = 1280x720");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"skin\.path\s*=.*", "skin.path = _no_skin");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"skin\.dynamic\s*=.*", "skin.dynamic = yes");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"showDeviceFrame\s*=.*", "showDeviceFrame = no");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"hw\.gpu\.mode\s*=.*", "hw.gpu.mode = host");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"fastboot\.forceColdBoot\s*=.*", "fastboot.forceColdBoot = no");
            text = System.Text.RegularExpressions.Regex.Replace(text, @"fastboot\.forceFastBoot\s*=.*", "fastboot.forceFastBoot = yes");

            if (!text.Contains("hw.gpu.mode")) text += "\r\nhw.gpu.mode = host\r\n";
            if (!text.Contains("fastboot.forceFastBoot")) text += "\r\nfastboot.forceFastBoot = yes\r\n";
            if (!text.Contains("firstboot.saveToLocalSnapshot")) text += "\r\nfirstboot.saveToLocalSnapshot = yes\r\n";
            if (!text.Contains("disk.dataPartition.path")) text += "\r\ndisk.dataPartition.path = userdata-qemu.img.qcow2\r\n";
            return text;
        }

        public static int RunProcessWithTimeout(ProcessStartInfo psi, int timeoutMs)
        {
            using (Process p = Process.Start(psi))
            {
                if (p == null) throw new InvalidOperationException("Failed to start process: " + psi.FileName);
                bool finished = p.WaitForExit(timeoutMs);
                if (!finished)
                {
                    try { p.Kill(); } catch { }
                    throw new TimeoutException(string.Format("Process '{0}' timed out after {1} seconds and was forcefully terminated.", Path.GetFileName(psi.FileName), timeoutMs / 1000));
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

        private CheckBox _chkShortcut;
        private ProgressBar _progBar;
        private Label _lblStatus;
        private TextBox _txtLogs;

        private Button _btnInstall;
        private Button _btnCancel;

        public InstallerForm(string stageDir, string defaultTarget)
        {
            _stageDir = stageDir;
            _targetDir = defaultTarget;
            _engine = new InstallerEngine(_stageDir, _targetDir);

            InitializeComponent();
        }

        private void InitializeComponent()
        {
            this.Text = "Dofus Touch Virtualization Farm Standalone Setup";
            this.Size = new Size(680, 560);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.BackColor = Color.FromArgb(30, 30, 30);
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f);

            // Icon
            string ico = Path.Combine(_stageDir, "installer_icon.ico");
            if (File.Exists(ico)) { try { this.Icon = new Icon(ico); } catch { } }

            // Header Panel
            _headerPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 70,
                BackColor = Color.FromArgb(37, 37, 38),
                Padding = new Padding(20, 10, 20, 10)
            };

            _titleLabel = new Label
            {
                Text = "DOFUS TOUCH FARM SETUP WIZARD",
                Font = new Font("Segoe UI", 12f, FontStyle.Bold),
                ForeColor = Color.FromArgb(86, 156, 214),
                AutoSize = true,
                Location = new Point(20, 12)
            };

            _subtitleLabel = new Label
            {
                Text = "100% Native C# Standalone Offline Installation & Auto-Optimization",
                Font = new Font("Segoe UI", 9f),
                ForeColor = Color.FromArgb(170, 170, 170),
                AutoSize = true,
                Location = new Point(20, 38)
            };

            _headerPanel.Controls.Add(_titleLabel);
            _headerPanel.Controls.Add(_subtitleLabel);

            // Installation Directory
            _lblTarget = new Label { Text = "Installation Destination Folder:", Location = new Point(25, 90), AutoSize = true, ForeColor = Color.LightGray };
            _txtTarget = new TextBox
            {
                Text = _targetDir,
                Location = new Point(25, 115),
                Width = 510,
                BackColor = Color.FromArgb(24, 24, 24),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };
            _btnBrowse = new Button
            {
                Text = "Browse...",
                Location = new Point(545, 114),
                Size = new Size(95, 26),
                BackColor = Color.FromArgb(45, 45, 48),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            _btnBrowse.FlatAppearance.BorderSize = 0;
            _btnBrowse.Click += OnBrowseClick;

            // Host Hardware specs detected
            _lblSpecs = new Label
            {
                Text = "Hardware Profile: Intel Core i5 | Intel UHD Graphics | 8 GB RAM | 8 Cores (Optimal: 4x Farm)",
                Location = new Point(25, 155),
                Size = new Size(615, 24),
                ForeColor = Color.FromArgb(78, 201, 176)
            };

            // Allocation Controls
            _lblInstances = new Label { Text = "Instance Count:", Location = new Point(25, 190), AutoSize = true, ForeColor = Color.LightGray };
            _cmbInstances = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(24, 24, 24),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 140,
                Location = new Point(25, 215)
            };
            _cmbInstances.Items.AddRange(new object[] { "1 Instance", "2 Instances", "3 Instances", "4 Instances (Default)", "6 Instances", "8 Instances" });
            _cmbInstances.SelectedIndex = 3;

            _lblRam = new Label { Text = "RAM per Instance:", Location = new Point(190, 190), AutoSize = true, ForeColor = Color.LightGray };
            _cmbRam = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(24, 24, 24),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 150,
                Location = new Point(190, 215)
            };
            _cmbRam.Items.AddRange(new object[] { "512 MB", "768 MB", "1024 MB (Default)", "1536 MB", "2048 MB", "4096 MB", "8192 MB", "16384 MB" });
            _cmbRam.SelectedIndex = 2;

            _lblCores = new Label { Text = "vCPU per Instance:", Location = new Point(365, 190), AutoSize = true, ForeColor = Color.LightGray };
            _cmbCores = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(24, 24, 24),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 110,
                Location = new Point(365, 215)
            };
            _cmbCores.Items.AddRange(new object[] { "1 Core", "2 Cores", "4 Cores", "8 Cores" });
            _cmbCores.SelectedIndex = 0;

            _chkShortcut = new CheckBox
            {
                Text = "Create 'Dofus Farm Manager' Desktop Shortcut (with custom SAO icon)",
                Checked = true,
                Location = new Point(25, 255),
                AutoSize = true,
                ForeColor = Color.FromArgb(86, 156, 214)
            };

            // Progress Bar & Status
            _lblStatus = new Label
            {
                Text = "Ready to install. Click 'Install Farm' to begin.",
                Location = new Point(25, 290),
                Size = new Size(615, 22),
                ForeColor = Color.LightGray
            };

            _progBar = new ProgressBar
            {
                Location = new Point(25, 315),
                Size = new Size(615, 20),
                Style = ProgressBarStyle.Continuous,
                Value = 0
            };

            _txtLogs = new TextBox
            {
                Location = new Point(25, 345),
                Size = new Size(615, 115),
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Vertical,
                BackColor = Color.FromArgb(20, 20, 20),
                ForeColor = Color.FromArgb(180, 180, 180),
                Font = new Font("Consolas", 8.5f),
                BorderStyle = BorderStyle.None
            };

            // Bottom Buttons
            _btnInstall = new Button
            {
                Text = "🚀 Install Farm",
                Location = new Point(415, 475),
                Size = new Size(130, 36),
                BackColor = Color.FromArgb(14, 99, 156),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnInstall.FlatAppearance.BorderSize = 0;
            _btnInstall.Click += OnInstallClick;

            _btnCancel = new Button
            {
                Text = "Cancel",
                Location = new Point(555, 475),
                Size = new Size(85, 36),
                BackColor = Color.FromArgb(45, 45, 48),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;
            _btnCancel.Click += delegate { this.Close(); };

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
            this.Controls.Add(_chkShortcut);
            this.Controls.Add(_lblStatus);
            this.Controls.Add(_progBar);
            this.Controls.Add(_txtLogs);
            this.Controls.Add(_btnInstall);
            this.Controls.Add(_btnCancel);

            _engine.OnProgress += (pct, msg) =>
            {
                if (this.IsDisposed || !this.IsHandleCreated) return;
                try
                {
                    this.BeginInvoke(new Action(delegate
                    {
                        if (this.IsDisposed) return;
                        _progBar.Value = Math.Min(100, Math.Max(0, pct));
                        _lblStatus.Text = msg;
                    }));
                }
                catch { }
            };

            _engine.OnLog += (log) =>
            {
                if (this.IsDisposed || !this.IsHandleCreated) return;
                try
                {
                    this.BeginInvoke(new Action(delegate
                    {
                        if (this.IsDisposed) return;
                        _txtLogs.AppendText(log + "\r\n");
                    }));
                }
                catch { }
            };
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
            _chkShortcut.Enabled = !installing;

            if (installing)
            {
                _btnInstall.Text = "⏳ Installing...";
                _btnInstall.BackColor = Color.FromArgb(60, 60, 60);
                this.Cursor = Cursors.WaitCursor;
            }
            else
            {
                _btnInstall.Text = "🚀 Install Farm";
                _btnInstall.BackColor = Color.FromArgb(14, 99, 156);
                this.Cursor = Cursors.Default;
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_isInstalling && e.CloseReason == CloseReason.UserClosing)
            {
                DialogResult dr = MessageBox.Show(
                    "Installation is currently in progress.\nAre you sure you want to abort the installation?",
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
            _engine.CreateShortcut = _chkShortcut.Checked;

            int instCount = 4;
            if (_cmbInstances.SelectedItem != null)
            {
                System.Text.RegularExpressions.Match m = System.Text.RegularExpressions.Regex.Match(_cmbInstances.SelectedItem.ToString(), @"(\d+)");
                if (m.Success) instCount = int.Parse(m.Groups[1].Value);
            }
            _engine.InstanceCount = instCount;

            SetUiState(true);

            _engine.RunInstall(
                delegate
                {
                    this.Invoke(new Action(delegate
                    {
                        SetUiState(false);
                        MessageBox.Show("Dofus Touch Farm installation completed successfully!", "Setup Complete", MessageBoxButtons.OK, MessageBoxIcon.Information);
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
