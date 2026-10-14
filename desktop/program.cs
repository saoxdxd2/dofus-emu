using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using DofusFarm.Native;
using DofusFarm.UI;

[assembly: AssemblyTitle("Dofus Farm Manager")]
[assembly: AssemblyDescription("Dofus Touch High-Performance Native Virtualization Manager")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Farm Manager")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("2.2.0.0")]
[assembly: AssemblyVersion("2.2.0.0")]

namespace DofusFarm
{
    static class Program
    {
        private static Mutex _singleInstanceMutex = null;

        private static void SafeLogFatal(string prefix, string msg)
        {
            string line = string.Format("[{0:yyyy-MM-dd HH:mm:ss}] {1}: {2}\r\n", DateTime.Now, prefix, msg);
            try
            {
                string baseLog = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "DofusFarm.log");
                File.AppendAllText(baseLog, line);
                return;
            }
            catch { }

            try
            {
                string fallbackDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DofusFarm");
                if (!Directory.Exists(fallbackDir)) Directory.CreateDirectory(fallbackDir);
                File.AppendAllText(Path.Combine(fallbackDir, "DofusFarm.log"), line);
            }
            catch { }
        }

        private static void FocusExistingInstance()
        {
            try
            {
                int currentId = Process.GetCurrentProcess().Id;
                Process[] procs = Process.GetProcessesByName("DofusFarm");
                for (int i = 0; i < procs.Length; i++)
                {
                    using (Process p = procs[i])
                    {
                        if (p.Id != currentId && p.MainWindowHandle != IntPtr.Zero)
                        {
                            Win32.ShowWindow(p.MainWindowHandle, Win32.SW_RESTORE);
                            Win32.SetForegroundWindow(p.MainWindowHandle);
                            break;
                        }
                    }
                }
            }
            catch { }
        }

        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);

            bool createdNew;
            _singleInstanceMutex = new Mutex(true, @"Global\DofusFarmManager_SingleInstance_Mutex_2026", out createdNew);
            if (!createdNew)
            {
                FocusExistingInstance();
                return;
            }

            try
            {
                AppDomain.CurrentDomain.UnhandledException += delegate (object sender, UnhandledExceptionEventArgs e)
                {
                    Exception ex = e.ExceptionObject as Exception;
                    string msg = ex != null ? ex.ToString() : "Unknown fatal error";
                    SafeLogFatal("FATAL", msg);
                    MessageBox.Show("Fatal Error: " + (ex != null ? ex.Message : "Unknown"), "Dofus Farm Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                };

                Application.ThreadException += delegate (object sender, ThreadExceptionEventArgs e)
                {
                    string msg = e.Exception != null ? e.Exception.ToString() : "Unknown UI error";
                    SafeLogFatal("UI_ERROR", msg);
                    string exMsg = e.Exception != null ? e.Exception.Message : "Unknown error";
                    MessageBox.Show("UI Error: " + exMsg, "Dofus Farm Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                };

                Application.Run(new MainForm());
            }
            finally
            {
                if (_singleInstanceMutex != null)
                {
                    try { _singleInstanceMutex.ReleaseMutex(); } catch { }
                    try { _singleInstanceMutex.Dispose(); } catch { }
                    _singleInstanceMutex = null;
                }
            }
        }
    }
}
