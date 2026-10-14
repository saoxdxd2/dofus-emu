using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Management;
using System.Text.RegularExpressions;
using System.Threading;
using DofusFarm.Native;

namespace DofusFarm.Engine
{
    public class PriorityGovernor
    {
        private Timer _timer;
        private int _lastForegroundPid = -1;
        private readonly HashSet<int> _pinnedPids = new HashSet<int>();
        private int _runningState = 0;
        public volatile bool Enabled = true;

        public event Action<string> OnLog;

        public PriorityGovernor()
        {
            Enabled = true;
        }

        public void Start()
        {
            Enabled = true;
            if (_timer == null)
            {
                _timer = new Timer(Tick, null, 2000, 1500);
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
            lock (_pinnedPids) { _pinnedPids.Clear(); }
            lock (_slotCache)
            {
                _slotCache.Clear();
                _slotCoresCache.Clear();
            }
        }

        private void Log(string message)
        {
            var handler = OnLog;
            if (handler != null && Enabled)
            {
                try
                {
                    handler(string.Format("[{0:HH:mm:ss}] [GOVERNOR] {1}", DateTime.Now, message));
                }
                catch { }
            }
        }

        private static readonly Dictionary<int, int> _slotCache = new Dictionary<int, int>();
        private static readonly Dictionary<int, int> _slotCoresCache = new Dictionary<int, int>();

        public static void RegisterInstancePid(int pid, int slot, int cores = 2)
        {
            lock (_slotCache)
            {
                _slotCache[pid] = slot;
                if (cores > 0)
                {
                    _slotCoresCache[slot] = cores;
                }
            }
        }

        public static long ComputeAffinityMask(int slot, int cores, int totalCores)
        {
            int safeTotal = Math.Min(62, Math.Max(1, totalCores));
            int span = Math.Min(safeTotal, Math.Max(2, cores));
            int baseCore = (Math.Max(0, slot) * 2) % safeTotal;
            long mask = 0L;
            for (int c = 0; c < span; c++)
            {
                int bit = (baseCore + c) % safeTotal;
                mask |= (1L << bit);
            }
            return mask != 0L ? mask : 1L;
        }

        private static int GetInstanceSlot(int pid, out int assignedCores)
        {
            assignedCores = 2;
            lock (_slotCache)
            {
                int cached;
                if (_slotCache.TryGetValue(pid, out cached))
                {
                    int cCores;
                    if (_slotCoresCache.TryGetValue(cached, out cCores) && cCores > 0)
                        assignedCores = cCores;
                    return cached;
                }
            }

            int resolved = -1;
            int parsedCores = 2;
            try
            {
                using (var searcher = new ManagementObjectSearcher(
                    string.Format("SELECT CommandLine FROM Win32_Process WHERE ProcessId = {0}", pid)))
                {
                    searcher.Options.Timeout = TimeSpan.FromSeconds(2);
                    using (var coll = searcher.Get())
                    {
                        foreach (ManagementObject mo in coll)
                        {
                            using (mo)
                            {
                                string cl = (mo["CommandLine"] ?? "").ToString();
                                var mp = Regex.Match(cl, @"-port\s+(\d+)");
                                if (mp.Success)
                                {
                                    int port = int.Parse(mp.Groups[1].Value);
                                    resolved = Math.Max(0, (port - 5554) / 2);
                                }
                                var mc = Regex.Match(cl, @"-cores\s+(\d+)");
                                if (mc.Success)
                                {
                                    int.TryParse(mc.Groups[1].Value, out parsedCores);
                                }
                                break;
                            }
                        }
                    }
                }
            }
            catch { }

            lock (_slotCache)
            {
                _slotCache[pid] = resolved;
                if (resolved >= 0)
                {
                    int cCores;
                    if (_slotCoresCache.TryGetValue(resolved, out cCores) && cCores > 0)
                        assignedCores = cCores;
                    else
                    {
                        assignedCores = Math.Max(2, parsedCores);
                        _slotCoresCache[resolved] = assignedCores;
                    }
                }
            }
            return resolved;
        }

        private void Tick(object state)
        {
            if (!Enabled || Interlocked.CompareExchange(ref _runningState, 1, 0) != 0) return;

            Process[] qemuProcs = null;
            try
            {
                qemuProcs = Process.GetProcessesByName("qemu-system-x86_64");
                if (qemuProcs == null || qemuProcs.Length == 0)
                {
                    lock (_pinnedPids) { _pinnedPids.Clear(); }
                    lock (_slotCache) { _slotCache.Clear(); }
                    return;
                }

                // Dynamic CPU core affinity assignment respecting user's configured core count per instance
                int totalCores = Environment.ProcessorCount;
                if (totalCores >= 4)
                {
                    lock (_pinnedPids)
                    {
                        HashSet<int> livePids = new HashSet<int>();
                        for (int i = 0; i < qemuProcs.Length; i++)
                        {
                            Process p = qemuProcs[i];
                            livePids.Add(p.Id);
                            if (!_pinnedPids.Contains(p.Id))
                            {
                                try
                                {
                                    int instCores;
                                    int slot = GetInstanceSlot(p.Id, out instCores);
                                    if (slot < 0) slot = i;
                                    long mask = ComputeAffinityMask(slot, instCores, totalCores);
                                    if (IntPtr.Size == 8)
                                        p.ProcessorAffinity = new IntPtr(mask);
                                    else
                                        p.ProcessorAffinity = new IntPtr(unchecked((int)mask));

                                    _pinnedPids.Add(p.Id);
                                    Log(string.Format("Governor pinned PID {0} (Slot #{1}, {2} threads) to Affinity Mask 0x{3:X}.", p.Id, slot + 1, Math.Max(2, instCores), mask));
                                }
                                catch { }
                            }
                        }
                        _pinnedPids.RemoveWhere(pid => !livePids.Contains(pid));

                        lock (_slotCache)
                        {
                            List<int> dead = new List<int>();
                            foreach (int pId in _slotCache.Keys)
                            {
                                if (!livePids.Contains(pId)) dead.Add(pId);
                            }
                            foreach (int pId in dead) _slotCache.Remove(pId);
                        }
                    }
                }

                IntPtr hFg = Win32.GetForegroundWindow();
                if (hFg == IntPtr.Zero) return;

                uint fgPid = 0;
                Win32.GetWindowThreadProcessId(hFg, out fgPid);
                if (fgPid == 0) return;

                // Check if the foreground window is one of the QEMU instances or their launcher wrapper (emulator.exe)
                bool isQemuActive = false;
                int activeTargetPid = -1;
                foreach (Process p in qemuProcs)
                {
                    if (p.Id == (int)fgPid)
                    {
                        isQemuActive = true;
                        activeTargetPid = p.Id;
                        break;
                    }
                }

                if (!isQemuActive)
                {
                    try
                    {
                        HashSet<int> tree = Win32.GetProcessTrees(new[] { (int)fgPid });
                        foreach (Process p in qemuProcs)
                        {
                            if (tree.Contains(p.Id))
                            {
                                isQemuActive = true;
                                activeTargetPid = p.Id;
                                break;
                            }
                        }
                    }
                    catch { }
                }

                if (isQemuActive && activeTargetPid > 0)
                {
                    if (_lastForegroundPid != activeTargetPid)
                    {
                        _lastForegroundPid = activeTargetPid;
                        foreach (Process p in qemuProcs)
                        {
                            try
                            {
                                if (p.Id == activeTargetPid)
                                {
                                    if (p.PriorityClass != ProcessPriorityClass.AboveNormal)
                                        p.PriorityClass = ProcessPriorityClass.AboveNormal;
                                }
                                else
                                {
                                    if (p.PriorityClass != ProcessPriorityClass.Normal)
                                        p.PriorityClass = ProcessPriorityClass.Normal;
                                }
                            }
                            catch { }
                        }
                        Log(string.Format("Foreground instance prioritized (PID: {0} -> AboveNormal). Background instances balanced at Normal.", activeTargetPid));
                    }
                }
                else
                {
                    // If user is outside the game, balance all to Normal
                    if (_lastForegroundPid != 0)
                    {
                        _lastForegroundPid = 0;
                        foreach (Process p in qemuProcs)
                        {
                            try
                            {
                                if (p.PriorityClass != ProcessPriorityClass.Normal)
                                    p.PriorityClass = ProcessPriorityClass.Normal;
                            }
                            catch { }
                        }
                    }
                }
            }
            catch { }
            finally
            {
                if (qemuProcs != null)
                {
                    for (int i = 0; i < qemuProcs.Length; i++)
                    {
                        if (qemuProcs[i] != null)
                        {
                            try { qemuProcs[i].Dispose(); } catch { }
                        }
                    }
                }
                Interlocked.Exchange(ref _runningState, 0);
            }
        }
    }
}
