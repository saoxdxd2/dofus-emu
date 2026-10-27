// clientbundle.cs - updating the client without breaking it.
//
// The client bundle is not shipped in the APK: Ankama serves manifest.json and
// the app downloads script.js at runtime. The old updater did that correctly
// (download beside the target, swap on success), and still broke the game after
// every update, for a reason that had nothing to do with the download:
//
//   **script.js is patched at BUILD time.** tools/patch-engine.js rewrites the
//   bundle (touch input, map-transition watchdog, Adjust SDK removal, WebGL
//   fingerprint). The runtime updater replaced script.js with the server's raw
//   copy, so every patch vanished the first time the server published a new
//   build -- login failed, or the page came up black, and it looked like "the
//   update broke it again" with no error pointing at the real cause.
//
// So the updater is now a small transaction:
//
//   download to staging  ->  apply the same rules  ->  validate  ->  commit
//
// Rules live in tools\patch-rules.txt, generated from patch-engine.js by
// tools/make-patch-rules.py, so there is exactly one description of what a
// patch is. If the staged bundle cannot be patched or does not validate, it is
// discarded and the working bundle stays. Nothing is deleted before its
// replacement exists, and script.js.bak always holds the unpatched original so
// a rebuild can start from a clean file.
//
// Kept C# 5 compatible.

using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;

namespace DofusFarm.Engine
{
    public static class ClientBundle
    {
        /// <summary>
        /// WebClient with a timeout and a User-Agent that survives.
        ///
        /// The stock WebClient has no timeout, so a dead proxy made the manifest
        /// fetch block for ~100 s and then log "The operation has timed out".
        ///
        /// The Agent property exists because of something measured, not guessed:
        /// `Headers["User-Agent"]` is applied to the FIRST request and then the
        /// collection is emptied -- a second request from the same instance carries
        /// no User-Agent at all (proved by printing the two built requests). The
        /// login CDN answers 403 (server=awselb/2.0) to a request without the mobile
        /// UA and 200 to the same URL with it, so one client per update run could
        /// read the manifest and then never download a single file. Setting the UA
        /// on every request built here is the only form that cannot be lost.
        /// </summary>
        private sealed class TimedWebClient : WebClient
        {
            public int TimeoutMs = 15000;
            public string Agent = null;

            protected override WebRequest GetWebRequest(Uri address)
            {
                WebRequest request = base.GetWebRequest(address);
                if (request != null)
                {
                    request.Timeout = TimeoutMs;
                    var http = request as HttpWebRequest;
                    if (http != null)
                    {
                        http.ReadWriteTimeout = TimeoutMs;
                        http.KeepAlive = false;
                        if (!string.IsNullOrEmpty(Agent)) http.UserAgent = Agent;
                    }
                }
                return request;
            }
        }

        public sealed class Rule
        {
            public string Id;
            public string Kind;         // "replace" | "boundary"
            public bool Core;
            public string Old;
            public string New;
            public string Start;
            public string End;
            public string Replacement;
            public int MaxLen;
        }

        /// <summary>Below this, a script.js is truncated or an error page.</summary>
        public const long MinScriptBytes = 1024 * 1024;

        public static string BundleDir()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string p = Path.Combine(baseDir, @"desktop\client\www");
            if (Directory.Exists(p)) return p;
            p = Path.Combine(baseDir, @"client\www");
            return Directory.Exists(p) ? p : null;
        }

        private static string RulesFile()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string p = Path.Combine(baseDir, @"tools\patch-rules.txt");
            if (File.Exists(p)) return p;
            p = Path.Combine(baseDir, @"..\tools\patch-rules.txt");
            return File.Exists(p) ? p : null;
        }

        public static List<Rule> LoadRules(out string error)
        {
            error = null;
            var rules = new List<Rule>();
            string path = RulesFile();
            if (path == null) { error = "tools\\patch-rules.txt not found"; return rules; }

            Rule cur = null;
            try
            {
                foreach (string raw in File.ReadAllLines(path))
                {
                    string line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith("#")) continue;
                    if (line.StartsWith("[") && line.EndsWith("]"))
                    {
                        cur = new Rule { Id = line.Substring(1, line.Length - 2) };
                        rules.Add(cur);
                        continue;
                    }
                    if (cur == null) continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string key = line.Substring(0, eq);
                    string val = line.Substring(eq + 1);
                    switch (key)
                    {
                        case "kind": cur.Kind = val; break;
                        case "core": cur.Core = val == "1"; break;
                        case "old": cur.Old = FromB64(val); break;
                        case "new": cur.New = FromB64(val); break;
                        case "start": cur.Start = FromB64(val); break;
                        case "end": cur.End = FromB64(val); break;
                        case "replacement": cur.Replacement = FromB64(val); break;
                        case "maxlen": int.TryParse(val, out cur.MaxLen); break;
                    }
                }
            }
            catch (Exception ex) { error = ex.Message; }
            return rules;
        }

        private static string FromB64(string s)
        {
            try { return Encoding.UTF8.GetString(Convert.FromBase64String(s)); }
            catch { return ""; }
        }

        /// <summary>Apply every rule, reporting which matched and which did not.</summary>
        public static string ApplyPatches(string js, List<Rule> rules,
                                          out List<string> matched, out List<string> skipped)
        {
            matched = new List<string>();
            skipped = new List<string>();
            foreach (Rule r in rules)
            {
                if (r.Kind == "boundary")
                {
                    int start = js.IndexOf(r.Start, StringComparison.Ordinal);
                    if (start < 0) { skipped.Add(r.Id + ": start marker missing"); continue; }
                    int end = js.IndexOf(r.End, start, StringComparison.Ordinal);
                    if (end < 0) { skipped.Add(r.Id + ": end marker missing"); continue; }
                    end += r.End.Length;
                    int span = end - start;
                    if (r.MaxLen > 0 && span > r.MaxLen)
                    {
                        skipped.Add(r.Id + ": span " + span + " chars, refused");
                        continue;
                    }
                    js = js.Substring(0, start) + r.Replacement + js.Substring(end);
                    matched.Add(r.Id);
                }
                else
                {
                    int at = js.IndexOf(r.Old, StringComparison.Ordinal);
                    if (at < 0) { skipped.Add(r.Id + ": needle missing"); continue; }
                    js = js.Substring(0, at) + r.New + js.Substring(at + r.Old.Length);
                    matched.Add(r.Id);
                }
            }
            return js;
        }

        /// <summary>A bundle is usable if the files the page loads are present and
        /// look like code, not like an HTML error page saved under a .js name.</summary>
        public static bool Validate(string dir, out string why) { return Validate(dir, true, out why); }

        /// <summary>
        /// `requirePage` is false for a staging tree: a manifest update carries only
        /// the build/* files, so index.html is not there -- and demanding it made
        /// every download end in "staged bundle rejected (index.html missing)", so
        /// the updater could never adopt anything. The live tree is checked with the
        /// page after the swap, which is where that requirement belongs.
        /// </summary>
        public static bool Validate(string dir, bool requirePage, out string why)
        {
            why = null;
            string script = Path.Combine(dir, @"build\script.js");
            if (!File.Exists(script)) { why = "build/script.js missing"; return false; }
            long len = new FileInfo(script).Length;
            if (len < MinScriptBytes) { why = "build/script.js is only " + len + " bytes"; return false; }

            string head;
            using (var fs = new FileStream(script, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (var sr = new StreamReader(fs, Encoding.UTF8, true, 4096))
            {
                char[] buf = new char[512];
                int n = sr.Read(buf, 0, buf.Length);
                head = new string(buf, 0, Math.Max(0, n));
            }
            if (head.TrimStart().StartsWith("<")) { why = "build/script.js starts with HTML"; return false; }

            string css = Path.Combine(dir, @"build\styles-native.css");
            if (File.Exists(css) && new FileInfo(css).Length < 200) { why = "styles-native.css is empty"; return false; }

            if (requirePage && !File.Exists(Path.Combine(dir, "index.html"))) { why = "index.html missing"; return false; }
            return true;
        }

        /// <summary>One retry, because a 403 from the CDN was transient here.</summary>
        private static string DownloadString(WebClient wc, string url)
        {
            Exception last = null;
            for (int attempt = 1; attempt <= 2; attempt++)
            {
                try { return wc.DownloadString(url); }
                catch (Exception ex)
                {
                    last = ex;
                    if (attempt == 1) System.Threading.Thread.Sleep(400);
                }
            }
            throw last;
        }

        private static void DownloadFile(WebClient wc, string url, string dest)
        {
            Exception last = null;
            for (int attempt = 1; attempt <= 2; attempt++)
            {
                try { wc.DownloadFile(url, dest); return; }
                catch (Exception ex)
                {
                    last = ex;
                    try { if (File.Exists(dest)) File.Delete(dest); } catch { }
                    if (attempt == 1) System.Threading.Thread.Sleep(400);
                }
            }
            throw last;
        }

        /// <summary>Restore the last known-good bundle if the live one is unusable.</summary>
        public static bool SelfHeal(Action<string> log)
        {
            string dir = BundleDir();
            if (dir == null) return false;

            string why;
            if (Validate(dir, out why)) return true;

            log?.Invoke("[UPDATER] bundle is not usable (" + why + ") - trying the backup");
            string script = Path.Combine(dir, @"build\script.js");
            string bak = script + ".bak";
            if (File.Exists(bak) && new FileInfo(bak).Length >= MinScriptBytes)
            {
                try
                {
                    File.Copy(bak, script, true);
                    log?.Invoke("[UPDATER] restored build/script.js from script.js.bak");
                    return Validate(dir, out why);
                }
                catch (Exception ex) { log?.Invoke("[UPDATER] restore failed: " + ex.Message); }
            }

            // Nothing to restore from: a rebuild is the only way back, and saying
            // so is more useful than a black screen with no explanation.
            log?.Invoke("[UPDATER] no usable backup. Reinstall (or rebuild) to restore the client bundle.");
            return false;
        }

        /// <summary>
        /// Point the page's asset URLs at a stamp derived from the bundle itself.
        ///
        /// standalone.html ships with `?v=3.14.2_mapfix10` on every script and
        /// stylesheet. That string never changes, so after an update Chromium keeps
        /// serving the *cached* old bundle and the update is invisible -- the page
        /// looks broken in a way no log explains. Re-stamping on every launch makes
        /// the cache key follow the file.
        /// </summary>
        private static void RewriteCacheBusters(string dir, Action<string> log)
        {
            try
            {
                string html = Path.Combine(dir, "standalone.html");
                string script = Path.Combine(dir, @"build\script.js");
                if (!File.Exists(html) || !File.Exists(script)) return;

                string stamp;
                using (var sha = System.Security.Cryptography.SHA1.Create())
                using (var fs = new FileStream(script, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    byte[] hash = sha.ComputeHash(fs);
                    stamp = BitConverter.ToString(hash).Replace("-", "").Substring(0, 10).ToLowerInvariant();
                }

                string text = File.ReadAllText(html);
                string replaced = Regex.Replace(text, @"\?v=[0-9A-Za-z_.\-]+", "?v=" + stamp);
                if (replaced != text)
                {
                    File.WriteAllText(html, replaced, new UTF8Encoding(false));
                    log?.Invoke("[UPDATER] asset URLs re-stamped: ?v=" + stamp);
                }
            }
            catch (Exception ex)
            {
                log?.Invoke("[UPDATER] could not re-stamp asset URLs: " + ex.Message);
            }
        }

        /// <summary>
        /// Bring the local bundle up to date, patch it, and only then adopt it.
        /// Returns the number of files adopted (0 = nothing changed or refused).
        ///
        /// Serialized: "Launch All" starts every instance at once and each one calls
        /// this from its own pool thread. Four concurrent transactions over the same
        /// script.js means four staging trees, four copies onto the same live file
        /// and a page that can load a half-written bundle. The first caller does the
        /// work; the rest read the stamp file it wrote and return 0.
        /// </summary>
        private static readonly object _gate = new object();

        public static int EnsureUpToDate(int proxyPort, Action<string> log)
        {
            lock (_gate) { return UpdateLocked(proxyPort, log); }
        }

        private static int UpdateLocked(int proxyPort, Action<string> log)
        {
            try
            {
                string dir = BundleDir();
                if (dir == null) return 0;

                // Neither of these is an update: one repairs a broken bundle, the
                // other keeps the page pointing at whatever is on disk. Both have to
                // run even when auto-update is switched off.
                SelfHeal(log);
                RewriteCacheBusters(dir, log);

                if (File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "no-client-update.flag")))
                {
                    log?.Invoke("[UPDATER] disabled by no-client-update.flag; keeping the local bundle");
                    return 0;
                }

                string stampPath = Path.Combine(dir, "client_version.json");
                var known = new Dictionary<string, string>();
                if (File.Exists(stampPath))
                {
                    foreach (Match m in Regex.Matches(File.ReadAllText(stampPath),
                                 "\"([^\"]+)\"\\s*:\\s*\"([^\"]*)\""))
                        known[m.Groups[1].Value] = m.Groups[2].Value;
                }

                string error;
                List<Rule> rules = LoadRules(out error);
                if (rules.Count == 0)
                    log?.Invoke("[UPDATER] warning: no patch rules (" + error + "); updates will be adopted unpatched");

                using (var wc = new TimedWebClient())
                {
                    if (proxyPort > 0) wc.Proxy = new WebProxy("http://127.0.0.1:" + proxyPort);
                    wc.Agent = "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36";
                    wc.Encoding = Encoding.UTF8;

                    const string server = "https://dt-proxy-production-login.ankama-games.com";
                    string manifest = DownloadString(wc, server + "/manifest.json");
                    var files = new Dictionary<string, string>();
                    foreach (Match m in Regex.Matches(manifest,
                                 "\"filename\"\\s*:\\s*\"([^\"]+)\"\\s*,\\s*\"version\"\\s*:\\s*\"([^\"]+)\""))
                        files[m.Groups[1].Value] = m.Groups[2].Value;
                    if (files.Count == 0)
                    {
                        log?.Invoke("[UPDATER] manifest has no files; keeping the local bundle");
                        return 0;
                    }

                    var todo = new List<string>();
                    foreach (var pair in files)
                    {
                        string local = Path.Combine(dir, pair.Key.Replace('/', Path.DirectorySeparatorChar));
                        string have;
                        bool upToDate = known.TryGetValue(pair.Key, out have) && have == pair.Value && File.Exists(local);
                        if (!upToDate) todo.Add(pair.Key);
                    }
                    if (todo.Count == 0) return 0;

                    string staging = Path.Combine(dir, "staging-" + DateTime.UtcNow.Ticks);
                    Directory.CreateDirectory(staging);
                    var staged = new List<string>();
                    try
                    {
                        foreach (string rel in todo)
                        {
                            string target = Path.Combine(staging, rel.Replace('/', Path.DirectorySeparatorChar));
                            Directory.CreateDirectory(Path.GetDirectoryName(target));
                            try
                            {
                                DownloadFile(wc, server + "/" + rel, target);
                                if (new FileInfo(target).Length == 0)
                                    throw new IOException("empty response");
                                staged.Add(rel);
                            }
                            catch (Exception ex)
                            {
                                log?.Invoke("[UPDATER] " + rel + " failed: " + ex.Message);
                                try { if (File.Exists(target)) File.Delete(target); } catch { }
                            }
                        }
                        if (staged.Count == 0)
                        {
                            log?.Invoke("[UPDATER] nothing downloaded; keeping the local bundle");
                            return 0;
                        }

                        // Patch the staged script BEFORE it can become live. This is
                        // the whole point: an unpatched script.js is what used to
                        // break the game after every update.
                        string stagedScript = Path.Combine(staging, @"build\script.js");
                        int coreTotal = 0, coreHit = 0;
                        if (File.Exists(stagedScript))
                        {
                            // The unpatched copy is what a future build must patch from.
                            try { File.Copy(stagedScript, Path.Combine(dir, @"build\script.js.bak"), true); } catch { }

                            string js = File.ReadAllText(stagedScript, Encoding.UTF8);
                            List<string> matched, skipped;
                            js = ApplyPatches(js, rules, out matched, out skipped);
                            File.WriteAllText(stagedScript, js, new UTF8Encoding(false));

                            foreach (Rule r in rules) if (r.Core) coreTotal++;
                            foreach (string id in matched)
                                foreach (Rule r in rules) if (r.Core && r.Id == id) coreHit++;

                            log?.Invoke(string.Format("[UPDATER] patched staged bundle: {0}/{1} rules, {2}/{3} core",
                                matched.Count, rules.Count, coreHit, coreTotal));
                            foreach (string s in skipped) log?.Invoke("[UPDATER]   skipped rule " + s);
                        }

                        // Validate the staged files, not the installed tree: the
                        // manifest never ships index.html.
                        string stagedWhy;
                        if (!Validate(staging, false, out stagedWhy))
                        {
                            log?.Invoke("[UPDATER] staged bundle rejected (" + stagedWhy + "); keeping the working one");
                            return 0;
                        }

                        // A staged script that took none of the core patches is the
                        // unpatched bundle that used to break login after every update.
                        // Adopting it is worse than staying on the current bundle.
                        if (File.Exists(stagedScript) && coreTotal > 0 && coreHit == 0)
                        {
                            log?.Invoke("[UPDATER] staged bundle refused: no core patch matched");
                            return 0;
                        }

                        int adopted = 0;
                        foreach (string rel in staged)
                        {
                            string src = Path.Combine(staging, rel.Replace('/', Path.DirectorySeparatorChar));
                            string dst = Path.Combine(dir, rel.Replace('/', Path.DirectorySeparatorChar));
                            try
                            {
                                Directory.CreateDirectory(Path.GetDirectoryName(dst));
                                // Old copy to .prev, new copy in, then drop .prev.
                                // Never delete the live file before its replacement
                                // is on disk -- that is how a 403 used to leave the
                                // page with no bundle at all.
                                string prev = dst + ".prev";
                                if (File.Exists(dst))
                                {
                                    try { File.Copy(dst, prev, true); } catch { }
                                }
                                File.Copy(src, dst, true);
                                try { if (File.Exists(prev)) File.Delete(prev); } catch { }
                                adopted++;
                            }
                            catch (Exception ex)
                            {
                                log?.Invoke("[UPDATER] could not adopt " + rel + ": " + ex.Message);
                                // Put the previous copy back so the tree stays coherent.
                                string prev = dst + ".prev";
                                try { if (File.Exists(prev)) File.Copy(prev, dst, true); } catch { }
                            }
                        }

                        if (adopted > 0)
                        {
                            var stamp = new StringBuilder("{\n");
                            int i = 0;
                            foreach (var pair in files)
                            {
                                stamp.AppendFormat("  \"{0}\": \"{1}\"{2}\n", pair.Key, pair.Value,
                                    ++i < files.Count ? "," : "");
                            }
                            stamp.Append("}\n");
                            File.WriteAllText(stampPath, stamp.ToString());

                            string why;
                            if (!Validate(dir, out why))
                                log?.Invoke("[UPDATER] WARNING: live bundle failed validation after update: " + why);
                            else
                                log?.Invoke(string.Format("[UPDATER] {0} file(s) updated and patched ({1}/{2} core rules)",
                                    adopted, coreHit, coreTotal));

                            // The stamp written at the top of this run describes the bundle
                            // that is now replaced. Without re-stamping, the page keeps
                            // asking for the old ?v= and Chromium serves the cached old
                            // build -- an update that installs and stays invisible.
                            RewriteCacheBusters(dir, log);
                        }
                        return adopted;
                    }
                    finally
                    {
                        try { if (Directory.Exists(staging)) Directory.Delete(staging, true); } catch { }
                    }
                }
            }
            catch (Exception ex)
            {
                log?.Invoke("[UPDATER] skipped: " + ex.Message);
                return 0;
            }
        }
    }
}
