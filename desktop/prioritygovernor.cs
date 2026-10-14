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
            // Respect the requested core count even when it is 1 (was forced to 2 before, which
            // robbed single-core instances of a whole logical CPU each).
            int span = Math.Min(safeTotal, Math.Max(1, cores)); // already fixed (was Math.Max(2, cores))
            // Step by the instance's own span so slots pack without overlap: 1-core instances take
            // logical CPUs 0,1,2,... (Windows numbers the primary thread of each physical core first),
            // 2-core instances take {0,1},{2,3},... instead of a hardcoded stride of 2.
            int baseCore = (Math.Max(0, slot) * Math.Max(1, span)) % safeTotal;
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
                // Use CreateToolhelp32Snapshot + command-line cache to avoid WMI.
                string cmd = GetCachedCommandLine(pid);
                if (!string.IsNullOrEmpty(cmd))
                {
                    var mp = Regex.Match(cmd, @"-port\s+(\d+)");
                    if (mp.Success)
                    {
                        int port = int.Parse(mp.Groups[1].Value);
                        resolved = Math.Max(0, (port - 5554) / 2);
                    }
                    var mc = Regex.Match(cmd, @"-cores\s+(\d+)");
                    if (mc.Success)
                    {
                        int.TryParse(mc.Groups[1].Value, out parsedCores);
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

        private static Dictionary<int, string> _cmdLineCache = new Dictionary<int, string>();
        private static readonly object _cmdLineLock = new object();
        private static string GetCachedCommandLine(int pid)
        {
            lock (_cmdLineLock)
            {
                string cached;
                if (_cmdLineCache.TryGetValue(pid, out cached)) return cached;
            }
            try
            {
                // Use OpenProcess + NtQueryInformationProcess to read the command line without WMI.
                IntPtr hProcess = NativeMethods.OpenProcess(0x0400 | 0x0010, false, pid);
                if (hProcess == IntPtr.Zero) return null;
                try
                {
                    // Use PROCESS_BASIC_INFORMATION to get PEB address
                    NativeMethods.PROCESS_BASIC_INFORMATION_X pbi = new NativeMethods.PROCESS_BASIC_INFORMATION_X();
                    uint returnLen = 0;
                    uint err = NativeMethods.NtQueryInformationProcess(hProcess, 6, ref pbi, (uint)Marshal.SizeOf(typeof(NativeMethods.PROCESS_BASIC_INFORMATION_X)), ref returnLen);
                    if (err == 0 && pbi.PebBaseAddress != IntPtr.Zero)
                    {
                        // Read the PEB
                        NativeMethods.PEB_X peb = new NativeMethods.PEB_X();
                        uint bytesRead;
                        if (NativeMethods.ReadProcessMemory(hProcess, pbi.PebBaseAddress, ref peb, (uint)Marshal.SizeOf(typeof(NativeMethods.PEB_X)), out bytesRead))
                        {
                            if (peb.ProcessParameters != IntPtr.Zero)
                            {
                                // Read RTL_USER_PROCESS_PARAMETERS
                                NativeMethods.RTL_USER_PROCESS_PARAMETERS_X cmdParams = new NativeMethods.RTL_USER_PROCESS_PARAMETERS_X();
                                if (NativeMethods.ReadProcessMemory(hProcess, peb.ProcessParameters, ref cmdParams, (uint)Marshal.SizeOf(typeof(NativeMethods.RTL_USER_PROCESS_PARAMETERS_X)), out bytesRead))
                                {
                                    if (cmdParams.CommandLine.Length > 0 && cmdParams.CommandLine.Buffer != IntPtr.Zero)
                                    {
                                        byte[] buffer = new byte[(int)cmdParams.CommandLine.Length * sizeof(char)];
                                        if (NativeMethods.ReadProcessMemory(hProcess, cmdParams.CommandLine.Buffer, buffer, (uint)buffer.LongLength, out bytesRead))
                                        {
                                            string cmdLineRead = System.Text.Encoding.Unicode.GetString(buffer, 0, (int)(bytesRead / sizeof(char)));
                                            lock (_cmdLineLock)
                                            {
                                                if (!_cmdLineCache.ContainsKey(pid))
                                                    _cmdLineCache[pid] = cmdLineRead;
                                            }
                                            return cmdLineRead;
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                finally
                {
                    NativeMethods.CloseHandle(hProcess);
                }
            }
            catch { return null; }
            return null;
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
