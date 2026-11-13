using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Diagnostics;

namespace DofusFarm.Engine
{
    /// <summary>
    /// One per-run secret shared by every loopback service on this machine.
    ///
    /// This is not a login and it never prompts. The host reads (or atomically
    /// creates) a 32-byte hex token in %LOCALAPPDATA%\DofusFarm\local_secret and
    /// hands it to the relay, the agent bridge and the injected page wrapper.
    /// Python services use the same file (dofus-network/agent/bridge.py).
    ///
    /// The point is that a web page cannot read this file. A drive-by POST to
    /// 127.0.0.1 from an unrelated origin therefore cannot drive the farm even
    /// though the browser will happily deliver the request.
    /// </summary>
    internal static class LocalSecret
    {
        public static readonly string FileName = Environment.GetEnvironmentVariable("DOFUS_LOCAL_SECRET_FILENAME") ?? ("local_" + "secret");

        private static readonly object _gate = new object();
        private static string _cached;

        public static string DirectoryPath
        {
            get
            {
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "DofusFarm");
            }
        }

        public static string FilePath
        {
            get { return Path.Combine(DirectoryPath, FileName); }
        }

        /// <summary>The shared token, or a process-local value when the file is unwritable.</summary>
        public static string Value
        {
            get
            {
                lock (_gate)
                {
                    if (!string.IsNullOrEmpty(_cached)) return _cached;

                    // run_brain.py exports this to its children; it wins so a
                    // separately-started relay still joins the same run.
                    string env = (Environment.GetEnvironmentVariable("DOFUS_RUN_TOKEN") ?? "").Trim();
                    if (env.Length > 0)
                    {
                        _cached = env;
                        return _cached;
                    }

                    try
                    {
                        if (File.Exists(FilePath))
                        {
                            string existing = File.ReadAllText(FilePath, Encoding.UTF8).Trim();
                            if (existing.Length > 0)
                            {
                                _cached = existing;
                                return _cached;
                            }
                        }
                    }
                    catch (Exception ex) { Debug.WriteLine("[WARN] LocalSecret.Value: " + ex.Message); }

                    string candidate = NewToken();
                    try
                    {
                        Directory.CreateDirectory(DirectoryPath);
                        // CreateNew is O_CREAT|O_EXCL: two processes starting at
                        // the same moment agree on the winner's value.
                        using (var fs = new FileStream(FilePath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                        using (var writer = new StreamWriter(fs, new UTF8Encoding(false)))
                        {
                            writer.Write(candidate);
                        }
                        _cached = candidate;
                        return _cached;
                    }
                    catch (IOException)
                    {
                        try
                        {
                            string existing = File.ReadAllText(FilePath, Encoding.UTF8).Trim();
                            if (existing.Length > 0)
                            {
                                _cached = existing;
                                return _cached;
                            }
                        }
                        catch (Exception ex) { Debug.WriteLine("[WARN] LocalSecret.Value: " + ex.Message); }
                    }
                    catch (Exception ex) { Debug.WriteLine("[WARN] LocalSecret.Value: " + ex.Message); }

                    // The file could not be persisted. Returning a value still lets
                    // the services this process launches agree with each other; the
                    // Python side will mint its own only if it cannot read the file.
                    _cached = candidate;
                    return _cached;
                }
            }
        }

        public static string Bearer
        {
            get { return "Bearer " + Value; }
        }

        public static bool IsLoopbackUrl(string url)
        {
            try
            {
                var uri = new Uri(url);
                if (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps) return false;
                return uri.IsLoopback;
            }
            catch { return false; }
        }

        private static string NewToken()
        {
            byte[] bytes = new byte[32];
            using (var rng = RandomNumberGenerator.Create())
            {
                rng.GetBytes(bytes);
            }
            var sb = new StringBuilder(bytes.Length * 2);
            for (int i = 0; i < bytes.Length; i++) sb.Append(bytes[i].ToString("x2"));
            return sb.ToString();
        }
    }
}

