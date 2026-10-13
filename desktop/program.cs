using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Forms;
using DofusFarm.UI;

[assembly: AssemblyTitle("Dofus Farm Manager")]
[assembly: AssemblyDescription("Dofus Touch High-Performance Native Virtualization Manager")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Farm Manager")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("2.0.0.0")]
[assembly: AssemblyVersion("2.0.0.0")]

namespace DofusFarm
{
    static class Program
    {
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

        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);

            AppDomain.CurrentDomain.UnhandledException += delegate (object sender, UnhandledExceptionEventArgs e)
            {
                Exception ex = e.ExceptionObject as Exception;
                string msg = ex != null ? ex.ToString() : "Unknown fatal error";
                SafeLogFatal("FATAL", msg);
                MessageBox.Show("Fatal Error: " + (ex != null ? ex.Message : "Unknown"), "Dofus Farm Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            };

            Application.ThreadException += delegate (object sender, System.Threading.ThreadExceptionEventArgs e)
            {
                string msg = e.Exception != null ? e.Exception.ToString() : "Unknown UI error";
                SafeLogFatal("UI_ERROR", msg);
                string exMsg = e.Exception != null ? e.Exception.Message : "Unknown error";
                MessageBox.Show("UI Error: " + exMsg, "Dofus Farm Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            };

            Application.Run(new MainForm());
        }
    }
}
