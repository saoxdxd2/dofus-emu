using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;

namespace DofusBuild
{
    class Program
    {
        static string Csc = @"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe";
        static string RepoRoot;

        static int Main(string[] args)
        {
            Console.Title = "Dofus Farm Native Builder";
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("==========================================================");
            Console.WriteLine("   DOFUS TOUCH FARM NATIVE C# COMPILER & PACKAGER        ");
            Console.WriteLine("==========================================================");
            Console.ResetColor();

            if (!File.Exists(Csc))
            {
                string altCsc = @"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe";
                if (File.Exists(altCsc)) Csc = altCsc;
            }

            RepoRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            if (!File.Exists(Path.Combine(RepoRoot, "builder", "Build.cs")) &&
                Directory.GetParent(RepoRoot) != null &&
                File.Exists(Path.Combine(Directory.GetParent(RepoRoot).FullName, "builder", "Build.cs")))
            {
                RepoRoot = Directory.GetParent(RepoRoot).FullName;
            }

            string desktopDir = Path.Combine(RepoRoot, "desktop");
            string ico = Path.Combine(RepoRoot, "app_icon.ico");
            string setupIco = Path.Combine(RepoRoot, "installer_icon.ico");
            string outFarm = Path.Combine(RepoRoot, "DofusFarm.exe");
            string outUninstall = Path.Combine(RepoRoot, "uninstall.exe");
            string outSetup = Path.Combine(RepoRoot, "setup.exe");

            // 0. Compile Go Network Proxy if go.exe is available
            TryBuildGoProxy();

            // 1. Compile DofusFarm.exe
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[1/3] Compiling native C# DofusFarm.exe...");
            Console.ResetColor();
            string[] farmSources = new[]
            {
                Path.Combine(desktopDir, "Win32.cs"),
                Path.Combine(desktopDir, "Hardware.cs"),
                Path.Combine(desktopDir, "FarmEngine.cs"),
                Path.Combine(desktopDir, "Watchdog.cs"),
                Path.Combine(desktopDir, "PriorityGovernor.cs"),
                Path.Combine(desktopDir, "MainForm.cs"),
                Path.Combine(desktopDir, "Program.cs")
            };
            string iconArg = File.Exists(ico) ? string.Format("\"/win32icon:{0}\" ", ico) : "";
            string farmArgs = string.Format("/nologo /target:winexe /optimize+ {0}\"/out:{1}\" /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Management.dll \"{2}\"",
                iconArg, outFarm, string.Join("\" \"", farmSources));

            if (Run(Csc, farmArgs) != 0)
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine("Failed to compile DofusFarm.exe");
                Console.ResetColor();
                return 1;
            }
            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("  [OK] DofusFarm.exe compiled ({0:N0} bytes)", new FileInfo(outFarm).Length);
            Console.ResetColor();

            // 2. Compile uninstall.exe
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[2/3] Compiling native C# uninstall.exe...");
            Console.ResetColor();
            string uninstallerCs = Path.Combine(desktopDir, "Uninstaller.cs");
            string setupIconArg = File.Exists(setupIco) ? string.Format("\"/win32icon:{0}\" ", setupIco) : "";
            string uninstArgs = string.Format("/nologo /target:winexe /optimize+ {0}\"/out:{1}\" /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll \"{2}\"",
                setupIconArg, outUninstall, uninstallerCs);

            if (Run(Csc, uninstArgs) != 0)
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine("Failed to compile uninstall.exe");
                Console.ResetColor();
                return 1;
            }
            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("  [OK] uninstall.exe compiled ({0:N0} bytes)", new FileInfo(outUninstall).Length);
            Console.ResetColor();

            // 3. Compile standalone setup.exe embedding sdk.7z & template.zip directly without temp copies
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[3/3] Packaging release payload and compiling standalone setup.exe...");
            Console.ResetColor();
            if (!BuildStandaloneSetup(outSetup, setupIco, desktopDir))
            {
                return 1;
            }

            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("\n==========================================================");
            Console.WriteLine("   BUILD COMPLETE: STANDALONE RELEASE SUITE READY!        ");
            Console.WriteLine("==========================================================");
            Console.ResetColor();
            return 0;
        }

        static void TryBuildGoProxy()
        {
            string proxySrc = Path.Combine(RepoRoot, @"scripts\dofus-net-proxy.go");
            if (!File.Exists(proxySrc)) return;

            string[] goCandidates = new[] { @"C:\Program Files\Go\bin\go.exe", @"C:\Go\bin\go.exe" };
            string goExe = null;
            foreach (string c in goCandidates)
            {
                if (File.Exists(c)) { goExe = c; break; }
            }
            if (goExe == null)
            {
                string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
                foreach (string dir in pathEnv.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    try
                    {
                        string p = Path.Combine(dir.Trim(), "go.exe");
                        if (File.Exists(p)) { goExe = p; break; }
                    }
                    catch { }
                }
            }

            if (!string.IsNullOrEmpty(goExe))
            {
                string proxyOut = Path.Combine(RepoRoot, "dofus-net-proxy.exe");
                Run(goExe, string.Format("build -ldflags=\"-s -w\" -o \"{0}\" \"{1}\"", proxyOut, proxySrc), RepoRoot);
            }
        }

        static bool BuildStandaloneSetup(string outSetup, string setupIco, string desktopDir)
        {
            string tempDir = Path.GetTempPath();
            string stageDir = Path.Combine(tempDir, "dofus_build_stage_" + Guid.NewGuid().ToString("N"));
            string payloadZip = Path.Combine(tempDir, "DofusPayload_" + Guid.NewGuid().ToString("N") + ".zip");

            try
            {
                if (Directory.Exists(stageDir)) Directory.Delete(stageDir, true);
                Directory.CreateDirectory(stageDir);

                string stageScripts = Path.Combine(stageDir, "scripts");
                Directory.CreateDirectory(stageScripts);
                foreach (string rs in new[] { "mobile-disguise.js", "apply-zram.sh" })
                {
                    string src = Path.Combine(RepoRoot, "scripts", rs);
                    if (File.Exists(src))
                        File.Copy(src, Path.Combine(stageScripts, rs), true);
                }

                string repoBin = Path.Combine(RepoRoot, @"scripts\bin");
                if (Directory.Exists(repoBin))
                {
                    string stageBin = Path.Combine(stageScripts, "bin");
                    Directory.CreateDirectory(stageBin);
                    foreach (string f in Directory.GetFiles(repoBin))
                        File.Copy(f, Path.Combine(stageBin, Path.GetFileName(f)), true);
                }

                foreach (string b in new[] { "DofusFarm.exe", "uninstall.exe", "dofus-net-proxy.exe", "app_icon.ico", "installer_icon.ico" })
                {
                    string p = Path.Combine(RepoRoot, b);
                    if (!File.Exists(p)) p = Path.Combine(RepoRoot, "scripts", b);
                    if (File.Exists(p)) File.Copy(p, Path.Combine(stageDir, b), true);
                }

                if (File.Exists(payloadZip)) File.Delete(payloadZip);
                ZipFile.CreateFromDirectory(stageDir, payloadZip, CompressionLevel.Fastest, false);

                // Embed template.zip and sdk.7z directly from RepoRoot without copying 896 MB into %TEMP%
                StringBuilder resourceArgs = new StringBuilder();
                resourceArgs.AppendFormat("\"/resource:{0},DofusPayload.zip\" ", payloadZip);

                long totalBytes = new FileInfo(payloadZip).Length;
                string tplZip = Path.Combine(RepoRoot, "template.zip");
                if (File.Exists(tplZip))
                {
                    resourceArgs.AppendFormat("\"/resource:{0},template.zip\" ", tplZip);
                    totalBytes += new FileInfo(tplZip).Length;
                }

                string sdk7z = Path.Combine(RepoRoot, "sdk.7z");
                if (File.Exists(sdk7z))
                {
                    resourceArgs.AppendFormat("\"/resource:{0},sdk.7z\" ", sdk7z);
                    totalBytes += new FileInfo(sdk7z).Length;
                }

                Console.WriteLine("  [OK] Payload streams ready: {0:F1} MB (zero temp archive duplication)", totalBytes / (1024.0 * 1024.0));

                string manifestFile = Path.Combine(tempDir, "StandaloneSetup_" + Guid.NewGuid().ToString("N") + ".manifest");
                string manifestSource =
                    "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" +
                    "<assembly manifestVersion=\"1.0\" xmlns=\"urn:schemas-microsoft-com:asm.v1\">\n" +
                    "  <assemblyIdentity version=\"2.2.0.0\" name=\"DofusFarmSetup\"/>\n" +
                    "  <trustInfo xmlns=\"urn:schemas-microsoft-com:asm.v3\">\n" +
                    "    <security>\n" +
                    "      <requestedPrivileges xmlns=\"urn:schemas-microsoft-com:asm.v3\">\n" +
                    "        <requestedExecutionLevel level=\"asInvoker\" uiAccess=\"false\" />\n" +
                    "      </requestedPrivileges>\n" +
                    "    </security>\n" +
                    "  </trustInfo>\n" +
                    "  <compatibility xmlns=\"urn:schemas-microsoft-com:compatibility.v1\">\n" +
                    "    <application>\n" +
                    "      <supportedOS Id=\"{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}\" />\n" +
                    "    </application>\n" +
                    "  </compatibility>\n" +
                    "</assembly>";
                File.WriteAllText(manifestFile, manifestSource, new UTF8Encoding(false));

                string[] setupSources = new[]
                {
                    Path.Combine(desktopDir, "Win32.cs"),
                    Path.Combine(desktopDir, "Hardware.cs"),
                    Path.Combine(desktopDir, "PriorityGovernor.cs"),
                    Path.Combine(desktopDir, "FarmEngine.cs"),
                    Path.Combine(desktopDir, "Installer.cs")
                };
                string setupIconArg = File.Exists(setupIco) ? string.Format("\"/win32icon:{0}\" ", setupIco) : "";
                string setupArgs = string.Format(
                    "/nologo /target:winexe /platform:anycpu /optimize+ {0}\"/win32manifest:{1}\" {2}" +
                    "/r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Management.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll /r:Microsoft.CSharp.dll \"/out:{3}\" \"{4}\"",
                    setupIconArg, manifestFile, resourceArgs.ToString(), outSetup, string.Join("\" \"", setupSources));

                int sec = Run(Csc, setupArgs);
                try { File.Delete(manifestFile); } catch { }

                if (sec == 0)
                {
                    Console.ForegroundColor = ConsoleColor.Green;
                    Console.WriteLine("  [OK] setup.exe compiled ({0:F1} MB)", new FileInfo(outSetup).Length / (1024.0 * 1024.0));
                    Console.ResetColor();
                    return true;
                }
                else
                {
                    Console.ForegroundColor = ConsoleColor.Red;
                    Console.WriteLine("  [FAIL] Failed to compile setup.exe");
                    Console.ResetColor();
                    return false;
                }
            }
            finally
            {
                try { if (Directory.Exists(stageDir)) Directory.Delete(stageDir, true); } catch { }
                try { if (File.Exists(payloadZip)) File.Delete(payloadZip); } catch { }
            }
        }

        static int Run(string exe, string args, string workingDir = null)
        {
            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = exe,
                Arguments = args,
                WorkingDirectory = string.IsNullOrEmpty(workingDir) ? RepoRoot : workingDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardError = true,
                RedirectStandardOutput = true
            };
            using (Process p = Process.Start(psi))
            {
                string outText = p.StandardOutput.ReadToEnd();
                string errText = p.StandardError.ReadToEnd();
                p.WaitForExit();
                if (p.ExitCode != 0)
                {
                    if (!string.IsNullOrEmpty(outText)) Console.WriteLine(outText);
                    if (!string.IsNullOrEmpty(errText)) Console.WriteLine(errText);
                }
                return p.ExitCode;
            }
        }
    }
}
