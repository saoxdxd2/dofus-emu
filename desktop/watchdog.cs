using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using DofusFarm.Native;

namespace DofusFarm.Engine
{
    public enum ComponentHealthState
    {
        Healthy,
        Degraded,
        Recovering,
        Quarantined
    }

    public class ComponentStatus
    {
        public string Name { get; set; }
        public ComponentHealthState State { get; set; }
        public int ConsecutiveFailures { get; set; }
        public DateTime LastSuccess { get; set; }
        public DateTime LastFailure { get; set; }
        public DateTime LastRecovery { get; set; }
        public int RecoveryAttempts { get; set; }
        public string LastError { get; set; }

        public ComponentStatus(string name)
        {
            Name = name;
            State = ComponentHealthState.Healthy;
            ConsecutiveFailures = 0;
            LastSuccess = DateTime.UtcNow;
            LastFailure = DateTime.MinValue;
            LastRecovery = DateTime.MinValue;
            RecoveryAttempts = 0;
            LastError = "";
        }
    }

    public class InstanceHealthRecord
    {
        public string Serial { get; set; }
        public string Name { get; set; }
        public ComponentHealthState OverallState { get; set; }
        public Dictionary<string, ComponentStatus> Components { get; private set; }

        public InstanceHealthRecord(string serial, string name)
        {
            Serial = serial;
            Name = name;
            OverallState = ComponentHealthState.Healthy;
            Components = new Dictionary<string, ComponentStatus>(StringComparer.OrdinalIgnoreCase);
        }

        public ComponentStatus GetComponent(string compName)
        {
            ComponentStatus status;
            if (!Components.TryGetValue(compName, out status))
            {
                status = new ComponentStatus(compName);
                Components[compName] = status;
            }
            return status;
        }
    }

    public class FarmWatchdog
    {
        private readonly FarmEngine _engine;
        private readonly Dictionary<string, InstanceHealthRecord> _healthRecords = new Dictionary<string, InstanceHealthRecord>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, int> _consecutiveFailures = new Dictionary<string, int>();
        private readonly Dictionary<string, int> _gameDownCount = new Dictionary<string, int>();
        private readonly Dictionary<string, List<DateTime>> _restartHistory = new Dictionary<string, List<DateTime>>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, DateTime> _lastRestartTime = new Dictionary<string, DateTime>(StringComparer.OrdinalIgnoreCase);
        private static readonly int MaxRestartsPerHour = int.TryParse(Environment.GetEnvironmentVariable("DOFUS_MAX_RESTARTS_PER_HOUR") ?? "", out var _mrph) && _mrph > 0 ? _mrph : 3;
        private static readonly int RestartCooldownSeconds = int.TryParse(Environment.GetEnvironmentVariable("DOFUS_RESTART_COOLDOWN_SECONDS") ?? "", out var _rcs) && _rcs > 0 ? _rcs : 60;
        private readonly object _syncLock = new object();
        private Timer _timer;
        private int _runningState = 0;
        public volatile bool Enabled = true;

        public event Action<string> OnLog;

        public FarmWatchdog(FarmEngine engine)
        {
            _engine = engine;
            Enabled = true;
        }

        private bool CanRestart(string serial)
        {
            DateTime now = DateTime.UtcNow;
            lock (_syncLock)
            {
                DateTime last;
                if (_lastRestartTime.TryGetValue(serial, out last) && (now - last).TotalSeconds < RestartCooldownSeconds)
                    return false;

                List<DateTime> history;
                if (!_restartHistory.TryGetValue(serial, out history))
                {
                    history = new List<DateTime>();
                    _restartHistory[serial] = history;
                }
                history.RemoveAll(t => (now - t).TotalHours >= 1.0);
                return history.Count < MaxRestartsPerHour;
            }
        }

        private void RecordRestart(string serial)
        {
            DateTime now = DateTime.UtcNow;
            lock (_syncLock)
            {
                _lastRestartTime[serial] = now;
                List<DateTime> history;
                if (!_restartHistory.TryGetValue(serial, out history))
                {
                    history = new List<DateTime>();
                    _restartHistory[serial] = history;
                }
                history.Add(now);
            }
        }

        private void Log(string msg)
        {
            var handler = OnLog;
            if (handler != null && Enabled)
            {
                try
                {
                    handler(string.Format("[{0:HH:mm:ss}] [WATCHDOG] {1}", DateTime.Now, msg));
                }
                catch { }
            }
        }

        private void LogEvent(string component, string serial, ComponentHealthState oldState, ComponentHealthState newState, string reason, string action)
        {
            Log(string.Format("[EVENT] Component={0} Serial={1} Transition={2}->{3} Reason=\"{4}\" Action=\"{5}\"",
                component, serial, oldState, newState, reason, action));
        }

        public void Start()
        {
            Enabled = true;
            if (_timer == null)
            {
                _timer = new Timer(Tick, null, 15000, 25000);
            }
        }

        public void Stop()
        {
            Enabled = false;
            if (_timer != null)
            {
                try { _timer.Dispose(); } catch { }
                _timer = null;
            }
            lock (_syncLock)
            {
                _consecutiveFailures.Clear();
                _gameDownCount.Clear();
                _healthRecords.Clear();
            }
        }

        private void Tick(object state)
        {
            if (!Enabled || Interlocked.CompareExchange(ref _runningState, 1, 0) != 0) return;

            try
            {
                if (Enabled) CheckAndHeal();
            }
            catch (Exception ex)
            {
                Log("Error during watchdog cycle: " + ex.Message);
            }
            finally
            {
                Interlocked.Exchange(ref _runningState, 0);
            }
        }

        private InstanceHealthRecord GetRecord(string serial, string name)
        {
            lock (_syncLock)
            {
                InstanceHealthRecord rec;
                if (!_healthRecords.TryGetValue(serial, out rec))
                {
                    rec = new InstanceHealthRecord(serial, name);
                    _healthRecords[serial] = rec;
                }
                return rec;
            }
        }

        // --- Probes ---

        private static bool ProbeTcpPort(int port, int timeoutMs = 600)
        {
            if (port <= 0 || port > 65535) return false;
            try
            {
                using (var client = new TcpClient())
                {
                    var ar = client.BeginConnect("127.0.0.1", port, null, null);
                    if (ar.AsyncWaitHandle.WaitOne(timeoutMs))
                    {
                        client.EndConnect(ar);
                        return true;
                    }
                    return false;
                }
            }
            catch
            {
                return false;
            }
        }

        private static bool ProbeSentinelHeartbeat(int port = 8001, int timeoutMs = 600)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(string.Format("http://127.0.0.1:{0}/health", port));
                req.Timeout = timeoutMs;
                req.Method = "GET";
                using (var resp = (HttpWebResponse)req.GetResponse())
                {
                    return resp.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return ProbeTcpPort(port, timeoutMs);
            }
        }

        private void RestartInstanceAndTile(InstanceModel inst)
        {
            try
            {
                _engine.StopInstance(inst.Serial, inst.Name);
            }
            catch { }

            Thread.Sleep(1500);
            if (!Enabled) return;

            string healProxy = !string.IsNullOrEmpty(inst.Proxy) ? inst.Proxy : ("127.0.0.1:" + NetworkSettings.For().BasePort);
            _engine.LaunchInstance(inst.Name, inst.Port, inst.RamMb, inst.Cores, healProxy);

            Thread.Sleep(2500);
            if (!Enabled) return;

            List<int> pids = new List<int>();
            foreach (InstanceModel live in _engine.GetInstances())
            {
                if (live.IsRunning && live.Pid > 0) pids.Add(live.Pid);
            }
            if (pids.Count > 0) Win32.ApplyFarmLayout(pids, true);
        }

        public void CheckAndHeal()
        {
            if (!Enabled) return;
            List<InstanceModel> instances = _engine.GetInstances();
            DateTime now = DateTime.UtcNow;

            foreach (InstanceModel inst in instances)
            {
                if (!Enabled) break;
                if (!inst.IsRunning || inst.Pid <= 0) continue;

                InstanceHealthRecord record = GetRecord(inst.Serial, inst.Name);

                // 1. Process Validation & Uptime
                const double BootGraceSec = 180.0;
                double procAgeSec = 999;
                bool isEmulator = false;
                bool isWebView = false;
                try
                {
                    using (Process p = Process.GetProcessById(inst.Pid))
                    {
                        string pName = p.ProcessName.ToLower();
                        isEmulator = pName.Contains("qemu") || pName.Contains("emulator");
                        isWebView = pName.Contains("webview2") || pName.Contains("dofusfarm") || pName.Contains("msedgewebview2");

                        if (!isEmulator && !isWebView)
                        {
                            continue;
                        }
                        procAgeSec = (DateTime.Now - p.StartTime).TotalSeconds;
                    }
                }
                catch
                {
                    continue;
                }

                if (procAgeSec < BootGraceSec)
                {
                    continue;
                }

                // 2. Component Probe: Proxy Relay
                ComponentStatus relayStatus = record.GetComponent("ProxyRelay");
                bool relayAlive = ProbeTcpPort(inst.Port);
                if (!relayAlive)
                {
                    relayStatus.ConsecutiveFailures++;
                    relayStatus.LastFailure = now;
                    relayStatus.LastError = string.Format("Port {0} unresponsive", inst.Port);

                    if (relayStatus.ConsecutiveFailures == 1)
                    {
                        LogEvent("ProxyRelay", inst.Serial, relayStatus.State, ComponentHealthState.Degraded, relayStatus.LastError, "Observing");
                        relayStatus.State = ComponentHealthState.Degraded;
                    }
                    else if (relayStatus.ConsecutiveFailures >= 2 && relayStatus.State != ComponentHealthState.Recovering)
                    {
                        LogEvent("ProxyRelay", inst.Serial, relayStatus.State, ComponentHealthState.Recovering, relayStatus.LastError, "Surgical in-place restart of proxy relay");
                        relayStatus.State = ComponentHealthState.Recovering;
                        relayStatus.RecoveryAttempts++;
                        relayStatus.LastRecovery = now;

                        // Surgical in-place recovery: restart proxy relay child without killing game window
                        try
                        {
                            _engine.StopProxy(inst.Port);
                            Thread.Sleep(300);
                            string upstream = !string.IsNullOrEmpty(inst.Proxy) ? inst.Proxy : "";
                            _engine.EnsureProxyRunning(inst.Port, upstream, inst.Serial);
                            Log(string.Format("Surgical relay restart applied for {0} (port {1})", inst.Name, inst.Port));
                        }
                        catch (Exception ex)
                        {
                            Log(string.Format("Surgical relay restart failed for {0}: {1}", inst.Name, ex.Message));
                        }
                    }
                }
                else
                {
                    if (relayStatus.State != ComponentHealthState.Healthy)
                    {
                        LogEvent("ProxyRelay", inst.Serial, relayStatus.State, ComponentHealthState.Healthy, "Port responding", "Marked healthy");
                    }
                    relayStatus.State = ComponentHealthState.Healthy;
                    relayStatus.ConsecutiveFailures = 0;
                    relayStatus.LastSuccess = now;
                }

                // 3. Component Probe: Sentinel Sidecar
                ComponentStatus sentinelStatus = record.GetComponent("Sentinel");
                bool sentinelAlive = ProbeSentinelHeartbeat(8001);
                if (!sentinelAlive)
                {
                    sentinelStatus.ConsecutiveFailures++;
                    sentinelStatus.LastFailure = now;
                    sentinelStatus.LastError = "Sentinel heartbeat port 8001 timed out";

                    if (sentinelStatus.ConsecutiveFailures >= 2 && sentinelStatus.State != ComponentHealthState.Recovering)
                    {
                        LogEvent("Sentinel", inst.Serial, sentinelStatus.State, ComponentHealthState.Recovering, sentinelStatus.LastError, "Restarting sentinel sidecar");
                        sentinelStatus.State = ComponentHealthState.Recovering;
                        sentinelStatus.RecoveryAttempts++;
                        sentinelStatus.LastRecovery = now;
                    }
                }
                else
                {
                    if (sentinelStatus.State != ComponentHealthState.Healthy)
                    {
                        LogEvent("Sentinel", inst.Serial, sentinelStatus.State, ComponentHealthState.Healthy, "Heartbeat restored", "Marked healthy");
                    }
                    sentinelStatus.State = ComponentHealthState.Healthy;
                    sentinelStatus.ConsecutiveFailures = 0;
                    sentinelStatus.LastSuccess = now;
                }

                // 4. Component Probe: Game Process / Container
                ComponentStatus gameStatus = record.GetComponent("GameProcess");
                if (isEmulator)
                {
                    string boot = _engine.AdbShell(inst.Serial, "getprop sys.boot_completed");
                    bool isResponsive = !string.IsNullOrEmpty(boot) && boot.Contains("1");

                    if (!isResponsive)
                    {
                        gameStatus.ConsecutiveFailures++;
                        gameStatus.LastFailure = now;
                        gameStatus.LastError = "ADB getprop unresponsive";

                        if (gameStatus.ConsecutiveFailures < 3)
                        {
                            Log(string.Format("Instance {0} ({1}) unresponsive (check {2}/3)...", inst.Name, inst.Serial, gameStatus.ConsecutiveFailures));
                            continue;
                        }

                        if (!CanRestart(inst.Serial))
                        {
                            Log(string.Format("RESTART THROTTLED: {0} ({1}) reached limit ({2} restarts/hr) or cooldown.", inst.Name, inst.Serial, MaxRestartsPerHour));
                            continue;
                        }

                        RecordRestart(inst.Serial);
                        LogEvent("Instance", inst.Serial, gameStatus.State, ComponentHealthState.Quarantined, "ADB frozen after 3 checks", "Full instance restart");
                        RestartInstanceAndTile(inst);
                        gameStatus.ConsecutiveFailures = 0;
                        gameStatus.State = ComponentHealthState.Healthy;
                        continue;
                    }

                    // Verify Dofus Touch Activity
                    string gamePid = _engine.AdbShell(inst.Serial, "pidof com.ankama.dofustouch");
                    bool isGameRunning = false;
                    if (!string.IsNullOrEmpty(gamePid) && !gamePid.Contains("ERROR") && !gamePid.Contains("error"))
                    {
                        foreach (char c in gamePid.Trim())
                        {
                            if (char.IsDigit(c)) { isGameRunning = true; break; }
                        }
                    }

                    if (!isGameRunning)
                    {
                        gameStatus.ConsecutiveFailures++;
                        gameStatus.LastFailure = now;
                        gameStatus.LastError = "Activity com.ankama.dofustouch not in pidof";

                        if (gameStatus.ConsecutiveFailures <= 3 && Enabled)
                        {
                            LogEvent("GameProcess", inst.Serial, gameStatus.State, ComponentHealthState.Recovering, gameStatus.LastError, "In-place am start relaunch");
                            gameStatus.State = ComponentHealthState.Recovering;
                            _engine.AdbShell(inst.Serial, "am start -n com.ankama.dofustouch/.MainActivity");
                            ThreadPool.QueueUserWorkItem(delegate
                            {
                                if (Enabled) _engine.InjectDisguiseViaCdp(inst.Serial, inst.Port, inst.Name);
                            });
                        }
                        else if (Enabled)
                        {
                            if (!CanRestart(inst.Serial))
                            {
                                Log(string.Format("RESTART THROTTLED: {0} ({1}) reached limit ({2} restarts/hr).", inst.Name, inst.Serial, MaxRestartsPerHour));
                            }
                            else
                            {
                                RecordRestart(inst.Serial);
                                LogEvent("GameProcess", inst.Serial, gameStatus.State, ComponentHealthState.Quarantined, "Activity failed 3 in-place relaunches", "Full instance restart");
                                RestartInstanceAndTile(inst);
                                gameStatus.ConsecutiveFailures = 0;
                                gameStatus.State = ComponentHealthState.Healthy;
                            }
                        }
                    }
                    else
                    {
                        if (gameStatus.State != ComponentHealthState.Healthy)
                        {
                            LogEvent("GameProcess", inst.Serial, gameStatus.State, ComponentHealthState.Healthy, "Game process running", "Marked healthy");
                        }
                        gameStatus.State = ComponentHealthState.Healthy;
                        gameStatus.ConsecutiveFailures = 0;
                        gameStatus.LastSuccess = now;
                    }
                }
                else if (isWebView)
                {
                    // WebView2 renderer probe
                    try
                    {
                        using (Process p = Process.GetProcessById(inst.Pid))
                        {
                            if (!p.Responding)
                            {
                                gameStatus.ConsecutiveFailures++;
                                gameStatus.LastFailure = now;
                                gameStatus.LastError = "WebView2 main process not responding";
                                LogEvent("WebView2", inst.Serial, gameStatus.State, ComponentHealthState.Degraded, gameStatus.LastError, "Monitoring responsiveness");
                            }
                            else
                            {
                                gameStatus.State = ComponentHealthState.Healthy;
                                gameStatus.ConsecutiveFailures = 0;
                                gameStatus.LastSuccess = now;
                            }
                        }
                    }
                    catch
                    {
                        gameStatus.State = ComponentHealthState.Degraded;
                    }
                }
            }
        }
    }
}
