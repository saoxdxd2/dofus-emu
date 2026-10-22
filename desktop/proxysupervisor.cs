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
        public ProxyCandidate EnsureActive(bool forceRefresh)
        {
            ProxyCandidate current = Active;
            if (!forceRefresh && current != null && current.Healthy)
            {
                // Cheap re-validation: only trust it for one refresh window.
                if ((DateTime.UtcNow - current.CheckedUtc).TotalSeconds < Math.Max(15, RefreshSeconds))
                    return current;
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
                ProxyCandidate wait = Active;
                for (int i = 0; i < 60 && wait == null; i++)
                {
                    Thread.Sleep(100);
                    wait = Active;
                }
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

                healthy.Sort(delegate (ProxyCandidate a, ProxyCandidate b)
                {
                    // The local tunnel first: no third party sees the traffic.
                    int sa = a.Source == "wireguard tunnel" ? 0 : 1;
                    int sb = b.Source == "wireguard tunnel" ? 0 : 1;
                    if (sa != sb) return sa - sb;
                    return a.LatencyMs.CompareTo(b.LatencyMs);
                });

                ProxyCandidate best = healthy.Count > 0 ? healthy[0] : null;
                bool changed;
                lock (_gate)
                {
                    _candidates.Clear();
                    _candidates.AddRange(results);
                    changed = best != null &&
                              (_active == null || !string.Equals(_active.Url, best.Url, StringComparison.OrdinalIgnoreCase));
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
        public string Describe()
        {
            ProxyCandidate a = Active;
            var sb = new StringBuilder();
            if (a == null) sb.Append("no healthy egress");
            else sb.Append("active: ").Append(a.Label);

            List<ProxyCandidate> all = Candidates;
            int up = 0;
            foreach (ProxyCandidate c in all) if (c.Healthy) up++;
            sb.Append("   |   ").Append(up).Append("/").Append(all.Count).Append(" candidates healthy");
            if (!string.IsNullOrEmpty(_directIp)) sb.Append("   |   this machine: ").Append(_directIp);
            return sb.ToString();
        }
    }
}
