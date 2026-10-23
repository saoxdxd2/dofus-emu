// proxysupervisor.cs - one owner for "how does traffic leave this machine".
//
// The relay (dofus-net-proxy) has always been able to fail closed, but the
// *choice* of upstream was scattered: a hard-coded WARP check in the launcher, a
// proxies.txt round-robin in the engine, a mode in network.ini. Every one of
// those could disagree, and when the ISP blocked WARP's UDP the whole stack had
// no answer except "nothing starts".
//
// This class is the single answer. In `auto` mode it:
//
//   1. builds a candidate list -- userspace tunnel, configured upstream, every
//      entry in proxies.txt;
//   2. tests each one for real (fetch a tiny URL, record the exit IP);
//   3. throws away anything that fails, and anything whose exit IP equals this
//      machine's own address (a "proxy" that does not change the IP is not a
//      proxy, and trusting it would leak);
//   4. keeps the best healthy one, and re-tests it on a timer;
//   5. on failure, fails over and rebuilds the running relays on the same ports,
//      so instances keep working without a relaunch.
//
// Fail-closed stays the default: if no candidate is healthy, `EnsureActive`
// returns null and the launcher refuses to start a client rather than dialling
// out on the real IP.
//
// Kept C# 5 compatible.

using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;

namespace DofusFarm.Engine
{
    public sealed class ProxyCandidate
    {
        public string Url;          // http:// or socks5://
        public string Source;       // "wireguard tunnel" | "custom upstream" | "proxies.txt"
        public string ExitIp;       // what the far side sees, when we could measure it
        public int LatencyMs;
        public bool Healthy;
        public string Note;
        public DateTime CheckedUtc = DateTime.MinValue;
        // Consecutive passes. A candidate that answers twice in a row has demonstrated
        // something a candidate that answered once has not, and the ranking uses that.
        public int HealthyStreak;

        public string Label
        {
            get
            {
                string where = string.IsNullOrEmpty(ExitIp) ? "" : " -> " + ExitIp;
                return Source + ": " + Url + where +
                       (LatencyMs > 0 ? " (" + LatencyMs + " ms)" : "");
            }
        }
    }

    public sealed class ProxySupervisor
    {
        public static readonly ProxySupervisor Instance = new ProxySupervisor();

        private readonly object _gate = new object();
        private readonly List<ProxyCandidate> _candidates = new List<ProxyCandidate>();
        private ProxyCandidate _active;
        // The upstream the relays are actually built with. Compared against the new pick to
        // decide whether a switch is real.
        private string _lastAppliedUrl;

        // The egress this machine used last time, and the address it presented.
        //
        // Reconnecting from a different address is what the server treats as a hijacked session,
        // so the address has to survive a restart, not just a probe cycle. It also makes the
        // start fast: one probe of a known URL instead of waiting for a full round of thirteen.
        // The ranked pool produced by ai/rank_proxies.py, best first.
        //
        // A single latency probe cannot tell a proxy that is fine from one that is about to stop
        // answering; a short time series can. Until there is a trained model for it, that script
        // scores candidates on median, jitter, trend and loss and writes the order here.
        // How long sessions actually last on each egress, learned on this machine.
        //
        // A probe says whether a proxy answers; it says nothing about whether a session on it
        // survives. Users cannot be asked to configure an exit they control, so the app has to
        // discover which egress works here by watching its own sessions and preferring the ones
        // that last. No setup, and it adapts when a proxy starts getting kicked.
        private DateTime _sessionStartUtc = DateTime.MinValue;
        private string _sessionEgress;
        private Dictionary<string, double> _survival = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
        private Dictionary<string, int> _sessionCount = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        private List<string> _rankOrder;
        private DateTime _rankLoadedUtc = DateTime.MinValue;

        private string _pinnedUrl;
        private string _pinnedExitIp;
        private bool _pinLoaded;
        private DateTime _lastRefreshUtc = DateTime.MinValue;
        private string _directIp;
        private FarmEngine _engine;
        private Timer _timer;
        private int _refreshing;          // 0/1 guard, so concurrent launches test once

        public event Action<string> OnLog;
        public event Action<ProxyCandidate> OnActiveChanged;

        private ProxySupervisor() { }

        /// <summary>Refresh interval. A dead proxy is discovered within this window.</summary>
        public int RefreshSeconds = 45;
        /// <summary>Per-candidate probe timeout.</summary>
        public int ProbeTimeoutMs = 8000;

        public ProxyCandidate Active
        {
            get { lock (_gate) { return _active; } }
        }

        public string ActiveUrl
        {
            get { ProxyCandidate c = Active; return c != null ? c.Url : null; }
        }

        public List<ProxyCandidate> Candidates
        {
            get { lock (_gate) { return new List<ProxyCandidate>(_candidates); } }
        }

        public void Bind(FarmEngine engine, Action<string> log)
        {
            _engine = engine;
            if (log != null) OnLog += log;
        }

        private void Log(string msg)
        {
            var h = OnLog;
            if (h == null) return;
            try { h("[PROXY] " + msg); } catch { }
        }

        public void Start()
        {
            lock (_gate)
            {
                if (_timer != null) return;
                _timer = new Timer(delegate { Tick(); }, null, 4000, Math.Max(10, RefreshSeconds) * 1000);
            }
        }

        public void Stop()
        {
            lock (_gate)
            {
                if (_timer != null) { try { _timer.Dispose(); } catch { } _timer = null; }
            }
        }

        /// <summary>The egress to use right now. Never returns a dead candidate;
        /// may return null, which means "do not launch".</summary>
        private static string PinPath()
        {
            try { return Path.Combine(NetworkSettings.AppBase, "asset_cache", "egress_pin.txt"); }
            catch { return null; }
        }

        private void LoadPin()
        {
            if (_pinLoaded) return;
            _pinLoaded = true;
            try
            {
                string path = PinPath();
                if (path == null || !File.Exists(path)) return;
                string[] parts = File.ReadAllText(path).Split('|');
                if (parts.Length >= 1 && parts[0].Trim().StartsWith("http", StringComparison.OrdinalIgnoreCase))
                    _pinnedUrl = parts[0].Trim();
                if (parts.Length >= 2) _pinnedExitIp = parts[1].Trim();
            }
            catch { }
        }

        private void SavePin(string url, string exitIp)
        {
            try
            {
                string path = PinPath();
                if (path == null || string.IsNullOrEmpty(url)) return;
                string dir = Path.GetDirectoryName(path);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                File.WriteAllText(path, url + "|" + (exitIp ?? "") + "|" + DateTime.UtcNow.ToString("o"));
                _pinnedUrl = url;
                _pinnedExitIp = exitIp;
            }
            catch { }
        }

        private void LoadRankOrder()
        {
            // Re-read at most once a minute: the file is small, but this runs on a timer.
            if (_rankOrder != null && (DateTime.UtcNow - _rankLoadedUtc).TotalSeconds < 60) return;
            try
            {
                string path = Path.Combine(NetworkSettings.AppBase, "asset_cache", "proxies_ranked.txt");
                if (!File.Exists(path)) { _rankOrder = null; _rankLoadedUtc = DateTime.UtcNow; return; }
                var list = new List<string>();
                foreach (string line in File.ReadAllLines(path))
                {
                    string s = line.Trim();
                    if (s.Length == 0 || s.StartsWith("#")) continue;
                    list.Add(s);
                }
                _rankOrder = list;
            }
            catch { _rankOrder = null; }
            _rankLoadedUtc = DateTime.UtcNow;
        }

        private int RankIndex(ProxyCandidate c)
        {
            if (_rankOrder == null || c == null || string.IsNullOrEmpty(c.Url)) return int.MaxValue;
            for (int i = 0; i < _rankOrder.Count; i++)
                if (string.Equals(_rankOrder[i], c.Url, StringComparison.OrdinalIgnoreCase)) return i;
            return int.MaxValue;
        }

        /// <summary>Called when a client starts on this egress.</summary>
        public void NoteSessionStart(string egress)
        {
            _sessionEgress = egress;
            _sessionStartUtc = DateTime.UtcNow;
        }

        /// <summary>
        /// Called when a client closes. Records how long the session lasted, which is the only
        /// honest measure of whether an egress is usable for playing.
        /// </summary>
        public void NoteSessionEnd(string reason)
        {
            if (_sessionStartUtc == DateTime.MinValue || string.IsNullOrEmpty(_sessionEgress)) return;
            double seconds = (DateTime.UtcNow - _sessionStartUtc).TotalSeconds;
            _sessionStartUtc = DateTime.MinValue;

            // Under ten seconds is a failed launch, not a session; it says nothing about the
            // egress and would poison the average.
            if (seconds < 10) return;

            lock (_gate)
            {
                double prev;
                _survival.TryGetValue(_sessionEgress, out prev);
                int n;
                _sessionCount.TryGetValue(_sessionEgress, out n);
                _survival[_sessionEgress] = (prev * n + seconds) / (n + 1);
                _sessionCount[_sessionEgress] = n + 1;
            }

            try
            {
                string path = Path.Combine(NetworkSettings.AppBase, "asset_cache", "sessions.jsonl");
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.AppendAllText(path, string.Format(
                    "{{\"ts\":\"{0:o}\",\"egress\":\"{1}\",\"seconds\":{2:F1},\"reason\":\"{3}\"}}\n",
                    DateTime.UtcNow, _sessionEgress.Replace("\\", "/"), seconds, (reason ?? "").Replace("\"", "'")));
            }
            catch { }
        }

        /// <summary>Average session seconds on this egress; 0 when unknown.</summary>
        private double SurvivalOf(ProxyCandidate c)
        {
            if (c == null || string.IsNullOrEmpty(c.Url)) return 0;
            lock (_gate)
            {
                double v;
                return _survival.TryGetValue(c.Url, out v) ? v : 0;
            }
        }

        public ProxyCandidate EnsureActive(bool forceRefresh)
        {
            ProxyCandidate current = Active;
            if (!forceRefresh && current != null && current.Healthy)
            {
                // Cheap re-validation: only trust it for one refresh window.
                if ((DateTime.UtcNow - current.CheckedUtc).TotalSeconds < Math.Max(15, RefreshSeconds))
                    return current;
            }
            // A pin from the previous run, still answering: use it and start immediately.
            //
            // This is the fast path and the stable one. Waiting for a full probe round cost up to
            // ProbeTimeoutMs + 5s before the first client could connect, and the egress it landed
            // on was often a different address than the one the last session used — which is the
            // reconnect-from-a-new-IP pattern the server closes.
            if (!forceRefresh)
            {
                LoadPin();
                if (!string.IsNullOrEmpty(_pinnedUrl))
                {
                    ProxyCandidate pin = Probe(_pinnedUrl, "pinned", Math.Min(2500, ProbeTimeoutMs));
                    if (pin.Healthy)
                    {
                        lock (_gate)
                        {
                            _candidates.Clear();
                            _candidates.Add(pin);
                            _active = pin;
                            _lastAppliedUrl = pin.Url;
                            _lastRefreshUtc = DateTime.UtcNow;
                        }
                        Log("egress pinned to " + pin.Label + " (same address as last session)");
                        return pin;
                    }
                    Log("pinned egress " + _pinnedUrl + " no longer answers; probing the pool");
                    _pinnedUrl = null;
                }
            }

            return Refresh();
        }

        private void Tick()
        {
            try
            {
                NetworkSettings s = NetworkSettings.For(NetworkSettings.AppBase);
                if (NetworkSettings.NormalizeMode(s.Mode) != NetworkSettings.ModeAuto)
                {
                    // Explicit modes are the user's decision: health-check only,
                    // never switch underneath them.
                    ProxyCandidate a = Active;
                    if (a != null)
                    {
                        ProxyCandidate re = Probe(a.Url, a.Source);
                        if (re.Healthy)
                        {
                            lock (_gate) { a.Healthy = true; a.LatencyMs = re.LatencyMs; a.CheckedUtc = DateTime.UtcNow; }
                        }
                        else
                        {
                            lock (_gate) { a.Healthy = false; }
                            Log("upstream " + a.Url + " stopped answering (" + a.Note + ")");
                        }
                    }
                    return;
                }
                Refresh();
            }
            catch (Exception ex) { Log("refresh failed: " + ex.Message); }
        }

        public void ForceRefresh()
        {
            ThreadPool.QueueUserWorkItem(delegate { Refresh(); });
        }

        /// <summary>Test every candidate and pick the best healthy one.</summary>
        public ProxyCandidate Refresh()
        {
            // One refresh at a time: four instances launching together must not
            // start four probe storms.
            if (Interlocked.CompareExchange(ref _refreshing, 1, 0) != 0)
            {
                // Wait for the refresh that is already running, for as long as that refresh
                // can possibly take.
                //
                // This used to give up after 6 s while the refresh it was waiting on allows
                // itself ProbeTimeoutMs + 5 s. With a dozen slow candidates the first launch of
                // the session therefore always saw "no healthy egress" and refused, and then the
                // supervisor found a perfectly good egress 45 s later — which reads as "the pool
                // is dead" when it was only still being tested.
                ProxyCandidate wait = Active;
                int budgetMs = ProbeTimeoutMs + 6000;
                int waited = 0;
                while (wait == null && waited < budgetMs)
                {
                    Thread.Sleep(100);
                    waited += 100;
                    wait = Active;
                }
                if (wait == null)
                    Log("still no healthy egress after waiting " + (waited / 1000) + "s for the"
                        + " probe in flight; this launch will be refused");
                return wait;
            }

            try
            {
                var list = BuildCandidates();
                if (list.Count == 0)
                {
                    lock (_gate) { _candidates.Clear(); _active = null; }
                    Log("no egress candidates: bin\\warp is missing and proxies.txt is empty.");
                    return null;
                }

                var results = new ProxyCandidate[list.Count];
                var done = new CountdownEvent(list.Count);
                for (int i = 0; i < list.Count; i++)
                {
                    int idx = i;
                    ThreadPool.QueueUserWorkItem(delegate
                    {
                        try { results[idx] = Probe(list[idx].Url, list[idx].Source); }
                        catch (Exception ex)
                        {
                            results[idx] = new ProxyCandidate
                            {
                                Url = list[idx].Url, Source = list[idx].Source,
                                Healthy = false, Note = ex.Message
                            };
                        }
                        finally { try { done.Signal(); } catch { } }
                    });
                }
                done.Wait(ProbeTimeoutMs + 5000);

                // Carry the streak across refreshes. Probes produce fresh objects every cycle,
                // so without this every pass looks like a first pass and a tunnel that answers
                // once looks exactly as good as a proxy that has answered ten times.
                Dictionary<string, int> streakBefore =
                    new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                lock (_gate)
                {
                    foreach (ProxyCandidate pc in _candidates)
                        if (pc != null && pc.Url != null) streakBefore[pc.Url] = pc.HealthyStreak;
                }
                for (int i = 0; i < results.Length; i++)
                {
                    ProxyCandidate rc = results[i];
                    if (rc == null || rc.Url == null) continue;
                    int before;
                    streakBefore.TryGetValue(rc.Url, out before);
                    rc.HealthyStreak = rc.Healthy ? before + 1 : 0;
                }

                string direct = DirectExitIp();
                var healthy = new List<ProxyCandidate>();
                for (int i = 0; i < results.Length; i++)
                {
                    ProxyCandidate c = results[i];
                    if (c == null) continue;
                    if (c.Healthy && !string.IsNullOrEmpty(direct) &&
                        string.Equals(c.ExitIp, direct, StringComparison.OrdinalIgnoreCase))
                    {
                        // A proxy that reports our own address is transparent or
                        // broken. Treating it as healthy would leak silently.
                        c.Healthy = false;
                        c.Note = "exit IP is this machine's own address - not a real proxy";
                    }
                    if (c.Healthy) healthy.Add(c);
                }

                LoadRankOrder();
                healthy.Sort(delegate (ProxyCandidate a, ProxyCandidate b)
                {
                    // Measured stability first, then the source ranking, then latency. A candidate
                    // the ranker has never seen sorts last, which is right: it has no record.
                    // Sessions that lasted come before sessions that did not. Unknown egresses
                    // sit in the middle: no evidence either way, so they get a fair trial.
                    double sa = SurvivalOf(a), sb = SurvivalOf(b);
                    if (Math.Abs(sa - sb) > 1.0) return sb.CompareTo(sa);

                    int ia = RankIndex(a), ib = RankIndex(b);
                    if (ia != ib) return ia.CompareTo(ib);
                    int ra = RankOf(a), rb = RankOf(b);
                    if (ra != rb) return rb - ra;          // higher rank wins
                    return a.LatencyMs.CompareTo(b.LatencyMs);
                });

                // Sticky egress: if the upstream already in use is still healthy, keep it.
                //
                // Switching changes the exit IP, and the server treats a session whose IP moves
                // as a hijacked account. A marginally faster candidate is not worth that: the
                // relay only moves when the current one actually fails, which is the only time
                // an IP change is unavoidable.
                ProxyCandidate incumbent = null;
                if (!string.IsNullOrEmpty(_lastAppliedUrl))
                {
                    foreach (ProxyCandidate c in healthy)
                    {
                        if (string.Equals(c.Url, _lastAppliedUrl, StringComparison.OrdinalIgnoreCase))
                        {
                            incumbent = c;
                            break;
                        }
                    }
                }

                ProxyCandidate best = incumbent ?? (healthy.Count > 0 ? healthy[0] : null);
                bool changed;
                lock (_gate)
                {
                    // Drop null slots before storing them.
                    //
                    // `results` is filled by pool threads and `done.Wait` has a timeout: when a
                    // probe outlives it, its slot is still null. The healthy-list loop below
                    // skips nulls, but this array is what Describe() walks, and
                    // `foreach (c in all) if (c.Healthy)` on a null slot throws on a thread-pool
                    // thread — which is not catchable and takes the whole process down. That was
                    // the "Fatal Error: Object reference not set to an instance of an object"
                    // arriving from SettingsPage.RefreshNetState while the supervisor was still
                    // probing.
                    _candidates.Clear();
                    for (int i = 0; i < results.Length; i++)
                    {
                        if (results[i] != null) _candidates.Add(results[i]);
                    }
                    // A switch is real only when the relays would be built with a different
                    // upstream than they already have.
                    //
                    // This used to compare against `_active`, which `best == null` also clears.
                    // So one probe pass that missed every candidate set _active to null, the
                    // next pass picked the same tunnel again, `changed` fired, and
                    // RebuildAllRelays stopped and restarted the relay on the same port —
                    // dropping every live socket, including the game connection. From the
                    // client that is "it was not possible to connect to the server", right after
                    // a login that had just worked, and it repeated because the tunnel's latency
                    // oscillates around the threshold.
                    changed = best != null &&
                              !string.Equals(_lastAppliedUrl, best.Url, StringComparison.OrdinalIgnoreCase);
                    if (changed)
                    {
                        _lastAppliedUrl = best.Url;
                        SavePin(best.Url, best.ExitIp);
                    }
                    _active = best;
                    _lastRefreshUtc = DateTime.UtcNow;
                }

                if (best == null)
                {
                    Log("no healthy egress (" + list.Count + " tested). Instances will refuse to start.");
                    for (int i = 0; i < results.Length; i++)
                    {
                        ProxyCandidate c = results[i];
                        if (c == null) continue;
                        Log("  dead: " + c.Url + "  ->  " + c.Note);
                    }
                }
                else if (changed)
                {
                    Log("active egress -> " + best.Label + "   (" + healthy.Count + "/" + list.Count + " candidates healthy)");
                    var h = OnActiveChanged;
                    if (h != null) { try { h(best); } catch { } }
                }
                return best;
            }
            finally
            {
                Interlocked.Exchange(ref _refreshing, 0);
            }
        }

        private List<ProxyCandidate> BuildCandidates()
        {
            var list = new List<ProxyCandidate>();
            NetworkSettings s = NetworkSettings.For(NetworkSettings.AppBase);

            // 1. The userspace tunnel, when its binaries are present. In auto mode
            //    it is tried but never required: an ISP that drops WARP's UDP
            //    simply fails this probe and the pool takes over.
            if (FarmEngine.IsWarpInstalled())
            {
                try
                {
                    if (FarmEngine.EnsureTunnel(null) || FarmEngine.IsTunnelListening())
                        list.Add(new ProxyCandidate { Url = s.SocksUrl, Source = "wireguard tunnel" });
                }
                catch { }
            }

            // 2. An explicitly configured upstream.
            if (!string.IsNullOrEmpty(s.Upstream))
            {
                string u = FarmEngine.NormalizeUpstreamProxy(s.Upstream);
                if (!string.IsNullOrEmpty(u)) list.Add(new ProxyCandidate { Url = u, Source = "custom upstream" });
            }

            // 3. The pool. Every entry is a candidate; dead ones cost one probe.
            try
            {
                string[] pool = FarmEngine.GetShuffledProxyPool();
                if (pool != null)
                {
                    int n = 0;
                    foreach (string p in pool)
                    {
                        if (string.IsNullOrEmpty(p)) continue;
                        list.Add(new ProxyCandidate { Url = p, Source = "proxies.txt" });
                        if (++n >= 12) break;      // a probe storm helps nobody
                    }
                }
            }
            catch { }

            return list;
        }

        private ProxyCandidate Probe(string url, string source)
        {
            return Probe(url, source, ProbeTimeoutMs);
        }

        /// <summary>
        /// Probe with an explicit timeout. The pinned-egress check runs on the launch path, and
        /// paying the full 8 s there is 8 s of a window that has not appeared yet: measured 6.6 s
        /// before the client could even start. A pinned proxy that cannot answer in a couple of
        /// seconds is not worth waiting for — the pool is right behind it.
        /// </summary>
        private ProxyCandidate Probe(string url, string source, int timeoutMs)
        {
            var c = new ProxyCandidate { Url = url, Source = source, CheckedUtc = DateTime.UtcNow };
            DateTime t0 = DateTime.UtcNow;

            if (url != null && url.StartsWith("socks5", StringComparison.OrdinalIgnoreCase))
            {
                // WebProxy cannot speak SOCKS. For our own tunnel that is not a
                // reason to trust it: wireproxy binds its port BEFORE the
                // WireGuard handshake completes, so a blocked UDP path looks
                // perfectly healthy to a port check. Do a real SOCKS5 CONNECT
                // through it instead - a dead tunnel never answers.
                bool local = url.IndexOf("127.0.0.1", StringComparison.Ordinal) >= 0 ||
                             url.IndexOf("localhost", StringComparison.OrdinalIgnoreCase) >= 0;
                if (local)
                {
                    int port = 1080;
                    try { port = new Uri(url).Port; } catch { }
                    c.Healthy = FarmEngine.ProbeSocksEgress(port, "1.1.1.1", 443, ProbeTimeoutMs);
                    c.Note = c.Healthy ? "tunnel carries traffic" : "listening but no traffic (handshake blocked)";
                }
                else
                {
                    c.Healthy = FarmEngine.PortOpenPublic(url);
                    c.Note = c.Healthy ? "socks5 listener up (not end-to-end tested)" : "socks5 listener down";
                }
                c.LatencyMs = (int)(DateTime.UtcNow - t0).TotalMilliseconds;
                return c;
            }

            string why;
            string ip = FarmEngine.TestHttpProxy(url, ProbeTimeoutMs, out why);
            c.LatencyMs = (int)(DateTime.UtcNow - t0).TotalMilliseconds;
            if (ip == null)
            {
                c.Healthy = false;
                c.Note = string.IsNullOrEmpty(why) ? "no answer" : why;
                return c;
            }
            c.Healthy = true;
            c.ExitIp = ip;
            c.Note = "ok";
            return c;
        }

        private string DirectExitIp()
        {
            if (!string.IsNullOrEmpty(_directIp)) return _directIp;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("https://api.ipify.org");
                req.Proxy = null;
                req.Timeout = 6000;
                req.ReadWriteTimeout = 6000;
                req.KeepAlive = false;
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var sr = new System.IO.StreamReader(resp.GetResponseStream()))
                {
                    string body = sr.ReadToEnd().Trim();
                    if (body.Length > 0 && body.Length < 64) _directIp = body;
                }
            }
            catch { }
            return _directIp;
        }

        /// <summary>Human-readable state for the Settings page and the log.</summary>
        /// <summary>
        /// How much a candidate has earned.
        ///
        /// The local tunnel used to win outright whenever it was healthy, on the reasonable
        /// grounds that no third party sees the traffic. The catch is that "healthy" here means
        /// "answered the last probe", and on a line where WARP's UDP is blocked the tunnel
        /// answers one probe and dies before the next: it binds its port, carries a little
        /// traffic, then exits. Ranking it first meant the app kept choosing an egress that
        /// failed seconds later - the client logged in, then reported that it could not connect
        /// to the server.
        ///
        /// The tunnel now ranks below every healthy proxy, proven or not, and that is a
        /// deliberate reversal of the privacy preference.
        ///
        /// Three sessions in a row were closed by the server within seconds of the egress moving
        /// onto the WARP tunnel, and not one of them involved the AI. WARP's exit is a Cloudflare
        /// address (measured: 104.28.220.131, stable across probes), and a shared, well-known VPN
        /// range is exactly what a game server is entitled to refuse. A stable address that gets
        /// you disconnected is worse than a rotating one that does not.
        ///
        /// The tunnel is still the last resort: it beats having no egress at all.
        /// </summary>
        private static int RankOf(ProxyCandidate c)
        {
            bool proven = c.HealthyStreak >= 2;
            bool tunnel = c.Source == "wireguard tunnel";
            if (proven && !tunnel) return 3;     // steady proxy: the address Ankama accepts
            if (!proven && !tunnel) return 2;    // unproven proxy, still a better bet than WARP
            if (proven && tunnel) return 1;      // steady tunnel, but see below
            return 0;                            // unproven tunnel
        }

        /// <summary>
        /// The hops the relay may use to reach a fixed exit, comma separated.
        ///
        /// The pick is no longer one of them: it becomes the exit, the address Ankama is allowed
        /// to see. These are the disposable middle of the path — the relay races them and takes
        /// whichever can reach the exit, so any of them can die without the session's identity
        /// changing.
        /// </summary>
        /// <summary>
        /// The pick followed by the next-best healthy candidates, comma separated.
        ///
        /// This is the default: the relay dials the pick, and only races the rest when it fails.
        /// One exit at a time, because a session that changes source address mid-flight is what
        /// the server treats as hijacked.
        /// </summary>
        public string UpstreamChain(string primary, int max)
        {
            if (string.IsNullOrEmpty(primary)) return primary;
            if (max < 2) return primary;

            var parts = new List<string> { primary };
            foreach (ProxyCandidate c in Candidates)
            {
                if (parts.Count >= max) break;
                if (c == null || !c.Healthy || string.IsNullOrEmpty(c.Url)) continue;
                if (c.Url.IndexOf(',') >= 0) continue;
                if (parts.Contains(c.Url)) continue;
                parts.Add(c.Url);
            }
            return string.Join(",", parts.ToArray());
        }

        public string HopChain(string exitUrl, int max)
        {
            if (string.IsNullOrEmpty(exitUrl)) return "";
            if (max < 1) return "";

            // SOCKS5 hops first, and it is not a preference.
            //
            // A hop has to open a tunnel to the exit's host:port, and an exit is rarely on 443.
            // HTTP proxies in this pool allow CONNECT to 443 and little else, so they cannot
            // reach an exit listening on :7890 at all. Measured on the same pool: 5 of 6 SOCKS5
            // hops reached the exit, 2 of 7 HTTP ones did. Ordering by anything other than
            // scheme was why the chain looked like it could not work.
            var socks = new List<string>();
            var others = new List<string>();
            foreach (ProxyCandidate c in Candidates)
            {
                if (c == null || !c.Healthy || string.IsNullOrEmpty(c.Url)) continue;
                // No commas in a URL, but an entry that contained one would silently split into
                // two bogus hops.
                if (c.Url.IndexOf(',') >= 0) continue;
                if (string.Equals(c.Url, exitUrl, StringComparison.OrdinalIgnoreCase)) continue;
                if (socks.Contains(c.Url) || others.Contains(c.Url)) continue;
                if (c.Url.StartsWith("socks5", StringComparison.OrdinalIgnoreCase)) socks.Add(c.Url);
                else others.Add(c.Url);
            }

            var parts = new List<string>();
            foreach (string url in socks)
            {
                if (parts.Count >= max) break;
                parts.Add(url);
            }
            foreach (string url in others)
            {
                if (parts.Count >= max) break;
                parts.Add(url);
            }
            return string.Join(",", parts.ToArray());
        }

        /// <summary>True when at least one hop speaks SOCKS5, which is what makes a fixed exit
        /// reachable at all.</summary>
        public bool HasSocksHop(string exitUrl)
        {
            string hops = HopChain(exitUrl, 4);
            foreach (string h in hops.Split(','))
                if (h.StartsWith("socks5", StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        public string Describe()
        {
            ProxyCandidate a = Active;
            var sb = new StringBuilder();
            if (a == null) sb.Append("no healthy egress");
            else sb.Append("active: ").Append(a.Label);

            List<ProxyCandidate> all = Candidates;
            int up = 0;
            // Defensive on purpose: this is called from the settings page on a pool thread,
            // where an exception is fatal rather than a log line.
            foreach (ProxyCandidate c in all) if (c != null && c.Healthy) up++;
            sb.Append("   |   ").Append(up).Append("/").Append(all.Count).Append(" candidates healthy");
            if (!string.IsNullOrEmpty(_directIp)) sb.Append("   |   this machine: ").Append(_directIp);
            return sb.ToString();
        }
    }
}
