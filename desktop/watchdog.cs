using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Management;
using System.Threading;
using DofusFarm.Native;

namespace DofusFarm.Engine
{
    public class FarmWatchdog
    {
        private readonly FarmEngine _engine;
        private readonly Dictionary<string, int> _consecutiveFailures = new Dictionary<string, int>();
        private readonly Dictionary<string, int> _gameDownCount = new Dictionary<string, int>();
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

        public void CheckAndHeal()
        {
            if (!Enabled) return;
            List<InstanceModel> instances = _engine.GetInstances();
            foreach (InstanceModel inst in instances)
            {
                if (!Enabled) break;
                if (!inst.IsRunning || inst.Pid <= 0) continue;

                // 1. Check process uptime (120-second initial boot grace period) and PID validation
                double procAgeSec = 999;
                try
                {
                    using (Process p = Process.GetProcessById(inst.Pid))
                    {
                        string pName = p.ProcessName.ToLower();
                        if (!pName.Contains("qemu") && !pName.Contains("emulator"))
                        {
                            // Stale PID reused by unrelated host process - do not touch
                            continue;
                        }
                        procAgeSec = (DateTime.Now - p.StartTime).TotalSeconds;
                    }
                }
                catch
                {
                    // Target process is already dead
                    continue;
                }

                if (procAgeSec < 120)
                {
                    // Instance is still in its boot window. Do NOT kill!
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
                        Log(string.Format("Instance {0} ({1}) unresponsive (check {2}/3). Waiting for next cycle...",
                            inst.Name, inst.Serial, fails));
                        continue;
                    }

                    lock (_syncLock) { _consecutiveFailures.Remove(inst.Serial); }
                    if (!Enabled) break;

                    Log(string.Format("HEAL TRIGGERED: {0} ({1}, PID: {2}, Age: {3:0}s) is UNRESPONSIVE / FROZEN (3 consecutive failed checks)!",
                        inst.Name, inst.Serial, inst.Pid, procAgeSec));

                    // Kill stale emulator process safely confirming process name
                    try
                    {
                        _engine.Adb(inst.Serial, "emu kill", 3000);
                        using (Process p = Process.GetProcessById(inst.Pid))
                        {
                            string pName = p.ProcessName.ToLower();
                            if (pName.Contains("qemu") || pName.Contains("emulator"))
                            {
                                p.Kill();
                            }
                        }
                    }
                    catch { }

                    Thread.Sleep(2000);
                    if (!Enabled) break;

                    // Restart only this single instance preserving proxy isolation
                    Log(string.Format("Relaunching slot {0}...", inst.Name));
                    string healProxy = !string.IsNullOrEmpty(inst.Proxy) ? inst.Proxy : "127.0.0.1:8880";
                    _engine.LaunchInstance(inst.Name, inst.Port, inst.RamMb, inst.Cores, healProxy);

                    Thread.Sleep(3000);
                    if (!Enabled) break;

                    // Re-tile windows
                    List<int> pids = new List<int>();
                    foreach (InstanceModel live in _engine.GetInstances())
                    {
                        if (live.IsRunning && live.Pid > 0) pids.Add(live.Pid);
                    }
                    if (pids.Count > 0) Win32.ApplyFarmLayout(pids, true);

                    Log(string.Format("Self-heal completed for {0}!", inst.Name));
                }
                else
                {
                    lock (_syncLock) { _consecutiveFailures.Remove(inst.Serial); }

                    // Instance is healthy. Verify game status
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
                            Log(string.Format("Dofus Touch stopped in {0} (attempt {1}/3). Relaunching game...", inst.Name, downs));
                            _engine.AdbShell(inst.Serial, "am start -n com.ankama.dofustouch/.MainActivity");
                            ThreadPool.QueueUserWorkItem(delegate
                            {
                                if (Enabled) _engine.InjectDisguiseViaCdp(inst.Serial, inst.Port, inst.Name);
                            });
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
