using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using DofusFarm.Native;

namespace DofusFarm.Engine
{
    public class FarmWatchdog
    {
        private readonly FarmEngine _engine;
        private readonly Dictionary<string, int> _consecutiveFailures = new Dictionary<string, int>();
        private readonly Dictionary<string, int> _gameDownCount = new Dictionary<string, int>();
        private readonly Dictionary<string, List<DateTime>> _restartHistory = new Dictionary<string, List<DateTime>>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, DateTime> _lastRestartTime = new Dictionary<string, DateTime>(StringComparer.OrdinalIgnoreCase);
        private const int MaxRestartsPerHour = 3;
        private const int RestartCooldownSeconds = 60;
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

        private void RestartInstanceAndTile(InstanceModel inst)
        {
            try
            {
                _engine.StopInstance(inst.Serial, inst.Name);
            }
            catch { }

            Thread.Sleep(1500);
            if (!Enabled) return;

            string healProxy = !string.IsNullOrEmpty(inst.Proxy) ? inst.Proxy : "127.0.0.1:8880";
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
            foreach (InstanceModel inst in instances)
            {
                if (!Enabled) break;
                if (!inst.IsRunning || inst.Pid <= 0) continue;

                // 1. Check process uptime and PID validation.
                // The grace period must sit ABOVE the launch flow's own boot budget (the engine gives
                // a cold boot up to 130s and its ADB wait up to 110s). At 90s the watchdog fired while
                // a healthy instance was still booting, killed it and started over - the log showed a
                // restart every ~2.5 minutes, so the guest never finished booting and the screen
                // stayed unresponsive. Keep this comfortably above that budget.
                const double BootGraceSec = 180.0;
                double procAgeSec = 999;
                try
                {
                    using (Process p = Process.GetProcessById(inst.Pid))
                    {
                        string pName = p.ProcessName.ToLower();
                        if (!pName.Contains("qemu") && !pName.Contains("emulator"))
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

                // 2. Verify ADB responsiveness
                string boot = _engine.AdbShell(inst.Serial, "getprop sys.boot_completed");
                bool isResponsive = !string.IsNullOrEmpty(boot) && boot.Contains("1");

                if (!isResponsive)
                {
                    int fails = 0;
                    lock (_syncLock)
                    {
                        _consecutiveFailures.TryGetValue(inst.Serial, out fails);
                        fails++;
                        _consecutiveFailures[inst.Serial] = fails;
                    }

                    if (fails < 3)
                    {
                        Log(string.Format("Instance {0} ({1}) unresponsive (check {2}/3)...", inst.Name, inst.Serial, fails));
                        continue;
                    }

                    lock (_syncLock)
                    {
                        _consecutiveFailures.Remove(inst.Serial);
                        _gameDownCount.Remove(inst.Serial);
                    }
                    if (!Enabled) break;

                    if (!CanRestart(inst.Serial))
                    {
                        Log(string.Format("RESTART THROTTLED: {0} ({1}) reached limit ({2} restarts/hr) or cooldown. Pausing auto-restart.", inst.Name, inst.Serial, MaxRestartsPerHour));
                        continue;
                    }

                    RecordRestart(inst.Serial);
                    Log(string.Format("HEAL TRIGGERED: {0} ({1}) is frozen. Restarting instance...", inst.Name, inst.Serial));
                    RestartInstanceAndTile(inst);
                    Log(string.Format("Self-heal completed for {0}!", inst.Name));
                }
                else
                {
                    lock (_syncLock) { _consecutiveFailures.Remove(inst.Serial); }

                    // Instance is healthy. Verify Dofus Touch process
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
                        int downs = 0;
                        lock (_syncLock)
                        {
                            _gameDownCount.TryGetValue(inst.Serial, out downs);
                            downs++;
                            _gameDownCount[inst.Serial] = downs;
                        }

                        if (downs <= 3 && Enabled)
                        {
                            Log(string.Format("Dofus Touch stopped in {0} (attempt {1}/3). Relaunching activity...", inst.Name, downs));
                            _engine.AdbShell(inst.Serial, "am start -n com.ankama.dofustouch/.MainActivity");
                            ThreadPool.QueueUserWorkItem(delegate
                            {
                                if (Enabled) _engine.InjectDisguiseViaCdp(inst.Serial, inst.Port, inst.Name);
                            });
                        }
                        else if (Enabled)
                        {
                            // Escalate to full instance restart after 3 failed activity relaunches
                            lock (_syncLock) { _gameDownCount.Remove(inst.Serial); }
                            if (!CanRestart(inst.Serial))
                            {
                                Log(string.Format("RESTART THROTTLED: {0} ({1}) reached limit ({2} restarts/hr) or cooldown.", inst.Name, inst.Serial, MaxRestartsPerHour));
                            }
                            else
                            {
                                RecordRestart(inst.Serial);
                                Log(string.Format("Activity failed to recover in {0} after 3 attempts; restarting instance...", inst.Name));
                                RestartInstanceAndTile(inst);
                            }
                        }
                    }
                    else
                    {
                        lock (_syncLock) { _gameDownCount.Remove(inst.Serial); }
                    }
                }
            }
        }
    }
}
