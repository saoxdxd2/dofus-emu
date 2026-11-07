using System;
using System.IO;

public static class PythonLocator
{
    public static string Find(string targetDir = null)
    {
        if (!string.IsNullOrEmpty(targetDir))
        {
            string local = Path.Combine(targetDir, @"runtimes\python\python.exe");
            if (File.Exists(local)) return local;
            local = Path.Combine(targetDir, @"python\python.exe");
            if (File.Exists(local)) return local;
        }

        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string portable = Path.Combine(baseDir, @"runtimes\python\python.exe");
        if (File.Exists(portable)) return portable;
        portable = Path.Combine(baseDir, @"python\python.exe");
        if (File.Exists(portable)) return portable;

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

        string localApp = Environment.GetFolderPath(
            Environment.SpecialFolder.LocalApplicationData);
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
}
