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
        static readonly string Csc = @"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe";
        static string RepoRoot;

        static int Main(string[] args)
        {
            Console.Title = "Dofus Farm Native Builder";
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("==========================================================");
            Console.WriteLine("   DOFUS TOUCH FARM NATIVE C# COMPILER & PACKAGER        ");
            Console.WriteLine("==========================================================");
            Console.ResetColor();

            RepoRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            if (File.Exists(Path.Combine(RepoRoot, "builder", "Build.cs")))
            {
                // Running from repo root
            }
            else if (Directory.GetParent(RepoRoot) != null && File.Exists(Path.Combine(Directory.GetParent(RepoRoot).FullName, "DofusFarm.exe")))
            {
                RepoRoot = Directory.GetParent(RepoRoot).FullName;
            }

            string desktopDir = Path.Combine(RepoRoot, "desktop");
            string ico = Path.Combine(RepoRoot, "app_icon.ico");
            string setupIco = Path.Combine(RepoRoot, "installer_icon.ico");
            string outFarm = Path.Combine(RepoRoot, "DofusFarm.exe");
            string outUninstall = Path.Combine(RepoRoot, "uninstall.exe");
            string outSetup = Path.Combine(RepoRoot, "setup.exe");

            // 1. Compile DofusFarm.exe
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[1/4] Compiling native C# DofusFarm.exe...");
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
            string farmArgs = string.Format("/nologo /target:winexe \"/win32icon:{0}\" \"/out:{1}\" /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Management.dll \"{2}\"",
                ico, outFarm, string.Join("\" \"", farmSources));

            int ec = Run(Csc, farmArgs);
            if (ec != 0) { Console.ForegroundColor = ConsoleColor.Red; Console.WriteLine("Failed to compile DofusFarm.exe"); return 1; }
            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("  [OK] DofusFarm.exe compiled ({0} bytes)", new FileInfo(outFarm).Length);
            Console.ResetColor();

            // 2. Compile uninstall.exe
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[2/4] Compiling native C# uninstall.exe...");
            Console.ResetColor();
            string uninstallerCs = Path.Combine(desktopDir, "Uninstaller.cs");
            string uninstArgs = string.Format("/nologo /target:winexe \"/win32icon:{0}\" \"/out:{1}\" /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll \"{2}\"",
                setupIco, outUninstall, uninstallerCs);

            ec = Run(Csc, uninstArgs);
            if (ec != 0) { Console.ForegroundColor = ConsoleColor.Red; Console.WriteLine("Failed to compile uninstall.exe"); return 1; }
            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("  [OK] uninstall.exe compiled ({0} bytes)", new FileInfo(outUninstall).Length);
            Console.ResetColor();

            // 3. Seal Code Integrity Manifest
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[3/4] Sealing Code Integrity Manifest...");
            Console.ResetColor();
            GenerateIntegrityManifest();

            // 4. Compile standalone setup.exe with embedded payload
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[4/4] Packaging payload and compiling standalone setup.exe...");
            Console.ResetColor();
            BuildStandaloneSetup(outSetup, setupIco);

            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("\n==========================================================");
            Console.WriteLine("   BUILD COMPLETE: 100% NATIVE C# SUITE READY!            ");
            Console.WriteLine("==========================================================");
            Console.ResetColor();
            return 0;
        }

        static void GenerateIntegrityManifest()
        {
            string[] criticalFiles = new[]
            {
                "DofusFarm.exe",
                "uninstall.exe",
                "dofus-net-proxy.exe",
                "dofus-downloader.exe",
                @"scripts\mobile-disguise.js",
                @"scripts\human-behavior-model.js",
                "template.zip",
                "sdk.7z"
            };

            string manifestPath = Path.Combine(RepoRoot, @"scripts\app.integrity");
            string salt = "DOFUS_FARM_INTEGRITY_SALT_2026_K7X9Q";
            StringBuilder allHashes = new StringBuilder();
            List<string> lines = new List<string>();

            using (SHA256 sha = SHA256.Create())
            {
                foreach (string rel in criticalFiles)
                {
                    string full = Path.Combine(RepoRoot, rel);
                    if (File.Exists(full))
                    {
                        byte[] hash;
                        using (FileStream fs = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.Read))
                        {
                            hash = sha.ComputeHash(fs);
                        }
                        string hex = BitConverter.ToString(hash).Replace("-", "");
                        lines.Add(rel + "|" + hex);
                        allHashes.Append(hex);
                    }
                }

                byte[] salted = Encoding.UTF8.GetBytes(allHashes.ToString() + salt);
                byte[] sig = sha.ComputeHash(salted);
                string sigHex = BitConverter.ToString(sig).Replace("-", "");
                lines.Add("SIGNATURE|" + sigHex);
            }

            File.WriteAllLines(manifestPath, lines.ToArray(), Encoding.UTF8);
            Console.WriteLine("  [OK] Sealed app.integrity ({0} files)", lines.Count - 1);
        }

        static void BuildStandaloneSetup(string outSetup, string setupIco)
        {
            string tempDir = Path.GetTempPath();
            string stageDir = Path.Combine(tempDir, "dofus_build_stage_" + Guid.NewGuid().ToString("N"));
            string payloadZip = Path.Combine(tempDir, "DofusPayload.zip");

            if (Directory.Exists(stageDir)) Directory.Delete(stageDir, true);
            Directory.CreateDirectory(stageDir);

            // Copy scripts and tools
            string stageScripts = Path.Combine(stageDir, "scripts");
            Directory.CreateDirectory(stageScripts);
            string repoScripts = Path.Combine(RepoRoot, "scripts");
            if (Directory.Exists(repoScripts))
            {
                foreach (string f in Directory.GetFiles(repoScripts))
                    File.Copy(f, Path.Combine(stageScripts, Path.GetFileName(f)), true);
                string repoBin = Path.Combine(repoScripts, "bin");
                if (Directory.Exists(repoBin))
                {
                    string stageBin = Path.Combine(stageScripts, "bin");
                    Directory.CreateDirectory(stageBin);
                    foreach (string f in Directory.GetFiles(repoBin))
                        File.Copy(f, Path.Combine(stageBin, Path.GetFileName(f)), true);
                }
            }

            // Copy complete source code repository & tools for 1-file standalone project suite
            string[] sourceDirs = new[] { "desktop", "builder", "launcher" };
            foreach (string sDir in sourceDirs)
            {
                string srcPath = Path.Combine(RepoRoot, sDir);
                if (Directory.Exists(srcPath))
                {
                    CopyDirectoryRecursively(srcPath, Path.Combine(stageDir, sDir));
                }
            }

            // Copy binaries, build tools, and assets
            string[] binaries = new[] { "DofusFarm.exe", "uninstall.exe", "Build.exe", "compile.bat", "dofus-net-proxy.exe", "dofus-downloader.exe", "app_icon.ico", "installer_icon.ico", "LICENSE", "README.md" };
            foreach (string b in binaries)
            {
                string p = Path.Combine(RepoRoot, b);
                if (!File.Exists(p)) p = Path.Combine(RepoRoot, "scripts", b);
                if (File.Exists(p)) File.Copy(p, Path.Combine(stageDir, b), true);
            }

            // Ensure Build.exe inside setup payload is freshly compiled from latest builder\Build.cs
            string stageBuildExe = Path.Combine(stageDir, "Build.exe");
            string stageBuildCs = Path.Combine(stageDir, @"builder\Build.cs");
            if (File.Exists(stageBuildCs))
            {
                string bArgs = string.Format("/nologo /target:exe /r:System.IO.Compression.FileSystem.dll \"/out:{0}\" \"{1}\"", stageBuildExe, stageBuildCs);
                Run(Csc, bArgs);
            }

            // Copy template.zip
            string tplZip = Path.Combine(RepoRoot, "template.zip");
            if (File.Exists(tplZip)) File.Copy(tplZip, Path.Combine(stageDir, "template.zip"), true);

            // Copy sdk.7z
            string sdk7z = Path.Combine(RepoRoot, "sdk.7z");
            if (File.Exists(sdk7z)) File.Copy(sdk7z, Path.Combine(stageDir, "sdk.7z"), true);

            // Compress payload
            if (File.Exists(payloadZip)) File.Delete(payloadZip);
            string z7Exe = Path.Combine(RepoRoot, @"scripts\bin\7z.exe");
            if (File.Exists(z7Exe))
            {
                Run(z7Exe, string.Format("a -tzip -mx0 \"{0}\" \"{1}\\*\"", payloadZip, stageDir));
            }
            else
            {
                ZipFile.CreateFromDirectory(stageDir, payloadZip, CompressionLevel.Fastest, false);
            }
            try { Directory.Delete(stageDir, true); } catch { }

            Console.WriteLine("  [OK] Payload compressed: {0:F1} MB", new FileInfo(payloadZip).Length / (1024.0 * 1024.0));

            // Compile setup.exe
            string manifestFile = Path.Combine(tempDir, "StandaloneSetup.manifest");
            string manifestSource =
                "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" +
                "<assembly manifestVersion=\"1.0\" xmlns=\"urn:schemas-microsoft-com:asm.v1\">\n" +
                "  <assemblyIdentity version=\"1.0.0.0\" name=\"DofusFarmSetup\"/>\n" +
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
            File.WriteAllText(manifestFile, manifestSource, Encoding.UTF8);

            string installerCs = Path.Combine(RepoRoot, @"desktop\Installer.cs");
            string setupArgs = string.Format(
                "/nologo /target:winexe /platform:anycpu /optimize+ \"/win32icon:{0}\" \"/win32manifest:{1}\" \"/resource:{2},DofusPayload.zip\" " +
                "/r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll /r:Microsoft.CSharp.dll \"/out:{3}\" \"{4}\"",
                setupIco, manifestFile, payloadZip, outSetup, installerCs);

            int sec = Run(Csc, setupArgs);
            if (sec == 0)
            {
                Console.ForegroundColor = ConsoleColor.Green;
                Console.WriteLine("  [OK] setup.exe compiled ({0:F1} MB)", new FileInfo(outSetup).Length / (1024.0 * 1024.0));
                Console.ResetColor();
            }
            else
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine("  [FAIL] Failed to compile setup.exe");
                Console.ResetColor();
            }

            try { File.Delete(manifestFile); } catch { }
            try { File.Delete(payloadZip); } catch { }
        }

        static int Run(string exe, string args)
        {
            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = exe,
                Arguments = args,
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

        static void CopyDirectoryRecursively(string sourceDir, string targetDir)
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
    }
}
