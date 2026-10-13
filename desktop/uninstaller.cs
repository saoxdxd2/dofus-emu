using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Dofus Farm Uninstaller")]
[assembly: AssemblyDescription("Dofus Touch Virtualization Farm Native Uninstaller & Auto Cache Cleaner")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Farm Virtualization")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("2.0.0.0")]
[assembly: AssemblyVersion("2.0.0.0")]

namespace DofusUninstall
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
                string exePath = Assembly.GetExecutingAssembly().Location;
                string tempDir = Path.GetTempPath();

                bool noRespawn = false;
                int waitForPid = 0;
                string targetDir = baseDir;
                bool isQuiet = false;
                bool isForce = false;

                if (args != null)
                {
                    for (int i = 0; i < args.Length; i++)
                    {
                        if (args[i].Equals("-NoRespawn", StringComparison.OrdinalIgnoreCase) || args[i].Equals("/NoRespawn", StringComparison.OrdinalIgnoreCase))
                        {
                            noRespawn = true;
                        }
                        else if (args[i].Equals("-WaitForPid", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                        {
                            int.TryParse(args[i + 1], out waitForPid);
                        }
                        else if (args[i].Equals("-InstallDir", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                        {
                            targetDir = args[i + 1].Trim('"');
                        }
                        else if (args[i].Equals("/quiet", StringComparison.OrdinalIgnoreCase) || args[i].Equals("-quiet", StringComparison.OrdinalIgnoreCase))
                        {
                            isQuiet = true;
                        }
                        else if (args[i].Equals("/force", StringComparison.OrdinalIgnoreCase) || args[i].Equals("-force", StringComparison.OrdinalIgnoreCase))
                        {
                            isForce = true;
                        }
                    }
                }

                // If running inside install directory and not requested -NoRespawn, copy self to %TEMP% so targetDir has NO open file locks!
                if (!noRespawn && !exePath.StartsWith(tempDir, StringComparison.OrdinalIgnoreCase))
                {
                    int currentPid = Process.GetCurrentProcess().Id;
                    string tempExe = Path.Combine(tempDir, "dofus_uninstall_" + currentPid + ".exe");
                    try
                    {
                        File.Copy(exePath, tempExe, true);
                        string forwardArgs = string.Format("-WaitForPid {0} -InstallDir \"{1}\" {2} -NoRespawn",
                            currentPid,
                            baseDir,
                            args != null ? string.Join(" ", args) : "");
                        ProcessStartInfo psiRestart = new ProcessStartInfo
                        {
                            FileName = tempExe,
                            Arguments = forwardArgs,
                            UseShellExecute = false
                        };
                        Process.Start(psiRestart);
                        return;
                    }
                    catch { }
                }

                // If spawned from parent, wait for parent process to fully exit and release file locks
                if (waitForPid > 0)
                {
                    try
                    {
                        using (Process parent = Process.GetProcessById(waitForPid))
                        {
                            parent.WaitForExit(10000);
                        }
                    }
                    catch { }
                    System.Threading.Thread.Sleep(500);
                }

                if (string.IsNullOrEmpty(targetDir) || targetDir.StartsWith(tempDir, StringComparison.OrdinalIgnoreCase))
                {
                    try
                    {
                        using (RegistryKey k = Registry.CurrentUser.OpenSubKey(@"Software\DofusFarm"))
                        {
                            if (k != null)
                            {
                                object val = k.GetValue("InstallPath");
                                if (val != null) targetDir = val.ToString().TrimEnd('\\');
                            }
                        }
                    }
                    catch { }
                }

                // Safety guard: NEVER purge the source development repository
                if (!string.IsNullOrEmpty(targetDir) && (File.Exists(Path.Combine(targetDir, @"desktop\MainForm.cs")) || File.Exists(Path.Combine(targetDir, @"builder\Build.cs"))))
                {
                    targetDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "DofusFarm");
                }

                // User confirmation dialog
                if (!isQuiet && !isForce)
                {
                    string msg = "Are you sure you want to completely uninstall Dofus Touch Farm?\n\n" +
                                 "This will automatically:\n" +
                                 "  * Terminate all running emulators and background proxies\n" +
                                 "  * Clean all virtual disks, shader caches, and download archives\n" +
                                 "  * Remove desktop and start menu shortcuts\n" +
                                 "  * Remove all installed files and folders (preserving ONLY setup.exe)\n\n" +
                                 "Proceed with uninstallation?";

                    DialogResult res = MessageBox.Show(msg, "Uninstall Dofus Touch Farm", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                    if (res != DialogResult.Yes) return;
                }

                // 1. Terminate all active emulators, ADB, and proxy daemons
                KillProcesses(new[] { "qemu-system-x86_64", "emulator", "dofus-net-proxy", "dofus-downloader", "DofusFarm", "adb" }, targetDir);
                System.Threading.Thread.Sleep(2000);

                // 2. Wipe AVD virtual disks and emulator caches
                string userProfile = Environment.GetEnvironmentVariable("USERPROFILE");
                if (string.IsNullOrEmpty(userProfile)) userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                string avdHome = Path.Combine(userProfile, @".android\avd");
                if (Directory.Exists(avdHome))
                {
                    foreach (string d in Directory.GetDirectories(avdHome))
                    {
                        string dirName = Path.GetFileName(d);
                        if (dirName.EndsWith(".avd", StringComparison.OrdinalIgnoreCase))
                        {
                            string baseName = dirName.Substring(0, dirName.Length - 4);
                            if (baseName.StartsWith("dofus-", StringComparison.OrdinalIgnoreCase) && !baseName.Equals("dofus-template", StringComparison.OrdinalIgnoreCase))
                            {
                                SafeDeleteDirectory(d);
                            }
                        }
                    }
                    foreach (string f in Directory.GetFiles(avdHome))
                    {
                        string fileName = Path.GetFileName(f);
                        if (fileName.EndsWith(".ini", StringComparison.OrdinalIgnoreCase))
                        {
                            string baseName = fileName.Substring(0, fileName.Length - 4);
                            if (baseName.StartsWith("dofus-", StringComparison.OrdinalIgnoreCase) && !baseName.Equals("dofus-template", StringComparison.OrdinalIgnoreCase))
                            {
                                try { File.SetAttributes(f, FileAttributes.Normal); File.Delete(f); } catch { }
                            }
                        }
                    }
                }

                string androidCache = Path.Combine(userProfile, @".android\cache");
                if (Directory.Exists(androidCache))
                {
                    try { Directory.Delete(androidCache, true); } catch { }
                }

                // Purge temp caches
                try
                {
                    foreach (string d in Directory.GetDirectories(tempDir, "DofusSetup_*"))
                    {
                        try { Directory.Delete(d, true); } catch { }
                    }
                    foreach (string d in Directory.GetDirectories(tempDir, "dofus_stage_*"))
                    {
                        try { Directory.Delete(d, true); } catch { }
                    }
                    string zip = Path.Combine(tempDir, "DofusPayload.zip");
                    if (File.Exists(zip)) try { File.Delete(zip); } catch { }
                }
                catch { }

                // 3. Remove Desktop & Start Menu shortcuts
                string[] shortcutDirs = new[]
                {
                    Environment.GetFolderPath(Environment.SpecialFolder.Desktop),
                    Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory),
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs),
                    Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms)
                };

                foreach (string sDir in shortcutDirs)
                {
                    if (!string.IsNullOrEmpty(sDir) && Directory.Exists(sDir))
                    {
                        foreach (string sc in new[] { "Dofus Farm Manager.lnk", "Dofus Touch Farm.lnk", "DofusFarm.lnk" })
                        {
                            string p = Path.Combine(sDir, sc);
                            if (File.Exists(p)) try { File.Delete(p); } catch { }
                        }
                    }
                }

                // 4. Clean Registry & Environment variables
                try
                {
                    Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\DofusTouchFarm", false);
                }
                catch { }

                try
                {
                    Registry.CurrentUser.DeleteSubKeyTree(@"Software\DofusFarm", false);
                }
                catch { }

                try
                {
                    Environment.SetEnvironmentVariable("DOFUS_FARM_HOME", null, EnvironmentVariableTarget.User);
                    string sdkRoot = Environment.GetEnvironmentVariable("ANDROID_SDK_ROOT", EnvironmentVariableTarget.User);
                    if (!string.IsNullOrEmpty(sdkRoot) && (sdkRoot.IndexOf("DofusFarm", StringComparison.OrdinalIgnoreCase) >= 0 || (!string.IsNullOrEmpty(targetDir) && sdkRoot.StartsWith(targetDir, StringComparison.OrdinalIgnoreCase))))
                    {
                        Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT", null, EnvironmentVariableTarget.User);
                    }
                    string andHome = Environment.GetEnvironmentVariable("ANDROID_HOME", EnvironmentVariableTarget.User);
                    if (!string.IsNullOrEmpty(andHome) && (andHome.IndexOf("DofusFarm", StringComparison.OrdinalIgnoreCase) >= 0 || (!string.IsNullOrEmpty(targetDir) && andHome.StartsWith(targetDir, StringComparison.OrdinalIgnoreCase))))
                    {
                        Environment.SetEnvironmentVariable("ANDROID_HOME", null, EnvironmentVariableTarget.User);
                    }
                    string avdHomeEnv = Environment.GetEnvironmentVariable("ANDROID_AVD_HOME", EnvironmentVariableTarget.User);
                    if (!string.IsNullOrEmpty(avdHomeEnv) && (avdHomeEnv.IndexOf("DofusFarm", StringComparison.OrdinalIgnoreCase) >= 0 || (!string.IsNullOrEmpty(targetDir) && avdHomeEnv.StartsWith(targetDir, StringComparison.OrdinalIgnoreCase))))
                    {
                        Environment.SetEnvironmentVariable("ANDROID_AVD_HOME", null, EnvironmentVariableTarget.User);
                    }
                }
                catch { }

                // 5. Purge target installation directory (preserving ONLY setup.exe unless -force is specified)
                if (!string.IsNullOrEmpty(targetDir) && Directory.Exists(targetDir))
                {
                    DirectoryInfo di = new DirectoryInfo(targetDir);
                    foreach (FileInfo fi in di.GetFiles())
                    {
                        if (isForce || !fi.Name.Equals("setup.exe", StringComparison.OrdinalIgnoreCase))
                        {
                            try { fi.Attributes = FileAttributes.Normal; fi.Delete(); } catch { }
                        }
                    }
                    foreach (DirectoryInfo sub in di.GetDirectories())
                    {
                        SafeDeleteDirectory(sub.FullName);
                    }
                    if (isForce || di.GetFiles().Length == 0)
                    {
                        try { di.Delete(true); } catch { }
                    }
                }

                // If running in temp as spawned helper, schedule self deletion
                if (noRespawn && exePath.StartsWith(tempDir, StringComparison.OrdinalIgnoreCase))
                {
                    try
                    {
                        ProcessStartInfo psiDel = new ProcessStartInfo
                        {
                            FileName = "cmd.exe",
                            Arguments = string.Format("/c ping 127.0.0.1 -n 2 > nul & del /f /q \"{0}\"", exePath),
                            CreateNoWindow = true,
                            UseShellExecute = false,
                            WindowStyle = ProcessWindowStyle.Hidden
                        };
                        Process.Start(psiDel);
                    }
                    catch { }
                }

                if (!isQuiet)
                {
                    MessageBox.Show("Dofus Touch Farm has been completely uninstalled.\nAll caches and virtual disks have been cleaned.",
                        "Uninstall Complete", MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show("Uninstallation Error: " + ex.Message, "Dofus Farm Uninstall", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private static void KillProcesses(string[] names, string targetDir = null)
        {
            foreach (string name in names)
            {
                if (!string.IsNullOrEmpty(targetDir) && name.Equals("qemu-system-x86_64", StringComparison.OrdinalIgnoreCase))
                {
                    // Scope QEMU termination to DofusFarm directory
                    foreach (Process p in Process.GetProcessesByName(name))
                    {
                        try
                        {
                            using (p)
                            {
                                bool isOurs = true;
                                try
                                {
                                    if (p.MainModule != null && !p.MainModule.FileName.StartsWith(targetDir, StringComparison.OrdinalIgnoreCase))
                                        isOurs = false;
                                }
                                catch { }
                                if (isOurs)
                                {
                                    p.Kill();
                                    p.WaitForExit(1000);
                                }
                            }
                        }
                        catch { }
                    }
                    continue;
                }

                try
                {
                    ProcessStartInfo psi = new ProcessStartInfo
                    {
                        FileName = "taskkill.exe",
                        Arguments = "/F /T /IM " + name + ".exe",
                        CreateNoWindow = true,
                        UseShellExecute = false,
                        WindowStyle = ProcessWindowStyle.Hidden
                    };
                    using (Process p = Process.Start(psi)) { p.WaitForExit(1000); }
                }
                catch { }

                foreach (Process p in Process.GetProcessesByName(name))
                {
                    try
                    {
                        using (p)
                        {
                            p.Kill();
                            p.WaitForExit(1000);
                        }
                    }
                    catch { }
                }
            }
        }

        private static void SafeDeleteDirectory(string path)
        {
            if (!Directory.Exists(path)) return;
            string cleanPath = path.TrimEnd('\\');
            try
            {
                DirectoryInfo di = new DirectoryInfo(cleanPath);
                foreach (FileInfo fi in di.GetFiles("*", SearchOption.AllDirectories))
                {
                    try { fi.Attributes = FileAttributes.Normal; fi.Delete(); } catch { }
                }
                foreach (DirectoryInfo sub in di.GetDirectories("*", SearchOption.AllDirectories))
                {
                    try { sub.Attributes = FileAttributes.Normal; } catch { }
                }
                Directory.Delete(cleanPath, true);
            }
            catch { }

            if (Directory.Exists(cleanPath))
            {
                try
                {
                    ProcessStartInfo psi = new ProcessStartInfo
                    {
                        FileName = "cmd.exe",
                        Arguments = "/c attrib -r -s -h /s /d \"" + cleanPath + "\\*\" & rd /s /q \"" + cleanPath + "\"",
                        CreateNoWindow = true,
                        UseShellExecute = false,
                        WindowStyle = ProcessWindowStyle.Hidden
                    };
                    using (Process p = Process.Start(psi)) { p.WaitForExit(10000); }
                }
                catch { }
            }

            if (Directory.Exists(cleanPath))
            {
                try { Directory.Delete(cleanPath, true); } catch { }
            }
        }
    }
}
