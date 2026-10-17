using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;

namespace DofusFarm.Engine
{
    public class EmulatorBackend : IClientBackend
    {
        private readonly FarmEngine _engine;
        private readonly ConcurrentDictionary<string, ClientInstanceState> _states = new ConcurrentDictionary<string, ClientInstanceState>(StringComparer.OrdinalIgnoreCase);

        public string Name { get { return "Android AOSP Ranchu Emulator"; } }
        public BackendType Type { get { return BackendType.AndroidEmulator; } }

        public event Action<string> OnLog;
        public event Action<string, ClientInstanceState> OnStateChanged;

        public EmulatorBackend(FarmEngine engine)
        {
            _engine = engine ?? new FarmEngine();
            _engine.OnLog += msg => OnLog?.Invoke(msg);
        }

        public bool IsSupported(out string failureReason)
        {
            failureReason = null;
            if (!File.Exists(_engine.EmulatorPath))
            {
                failureReason = "Android emulator executable not found at: " + _engine.EmulatorPath;
                return false;
            }
            return true;
        }

        public void Initialize()
        {
            try { _engine.Adb("", "start-server", 5000); } catch { }
        }

        public void Start(ClientLaunchOptions options)
        {
            if (options == null || string.IsNullOrEmpty(options.InstanceId)) return;
            string id = options.InstanceId;
            SetState(id, ClientInstanceState.Starting);

            try
            {
                _engine.LaunchInstance(id, options.SlotPort, options.RamMb, options.Cores, options.Proxy);
                SetState(id, ClientInstanceState.Running);
            }
            catch (Exception ex)
            {
                SetState(id, ClientInstanceState.Faulted);
                OnLog?.Invoke(string.Format("[EMULATOR-BACKEND] Failed to launch {0}: {1}", id, ex.Message));
            }
        }

        public void Stop(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return;
            SetState(instanceId, ClientInstanceState.Stopping);

            try
            {
                var instances = _engine.GetInstances();
                var target = instances.FirstOrDefault(i => string.Equals(i.Name, instanceId, StringComparison.OrdinalIgnoreCase));
                string serial = target != null ? target.Serial : "emulator-5554";
                _engine.StopInstance(serial, instanceId);
            }
            finally
            {
                SetState(instanceId, ClientInstanceState.Stopped);
            }
        }

        public void Focus(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return;
            var instances = _engine.GetInstances();
            var target = instances.FirstOrDefault(i => string.Equals(i.Name, instanceId, StringComparison.OrdinalIgnoreCase));
            if (target != null && target.Port > 0)
            {
                _engine.FocusInstance(target.Port);
            }
        }

        public void TileWindows(bool edgeToEdge)
        {
            var pids = _engine.GetInstances().Where(i => i.IsRunning && i.Pid > 0).Select(i => i.Pid).ToList();
            if (pids.Count == 0)
            {
                Process[] procs = Process.GetProcessesByName("qemu-system-x86_64");
                try { foreach (Process p in procs) pids.Add(p.Id); }
                finally { foreach (Process p in procs) p.Dispose(); }
            }
            if (pids.Count > 0)
            {
                DofusFarm.Native.Win32.ApplyFarmLayout(pids, edgeToEdge);
            }
        }

        public void CascadeWindows()
        {
            var pids = _engine.GetInstances().Where(i => i.IsRunning && i.Pid > 0).Select(i => i.Pid).ToList();
            if (pids.Count == 0)
            {
                Process[] procs = Process.GetProcessesByName("qemu-system-x86_64");
                try { foreach (Process p in procs) pids.Add(p.Id); }
                finally { foreach (Process p in procs) p.Dispose(); }
            }
            if (pids.Count > 0)
            {
                DofusFarm.Native.Win32.ApplyCascadeLayout(pids);
            }
        }

        public ClientInstanceState GetState(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return ClientInstanceState.Stopped;
            ClientInstanceState state;
            if (_states.TryGetValue(instanceId, out state)) return state;

            var instances = _engine.GetInstances();
            var target = instances.FirstOrDefault(i => string.Equals(i.Name, instanceId, StringComparison.OrdinalIgnoreCase));
            if (target != null && target.IsRunning) return ClientInstanceState.Running;
            return ClientInstanceState.Stopped;
        }

        public ClientDiagnostics GetDiagnostics(string instanceId)
        {
            var diag = new ClientDiagnostics
            {
                InstanceId = instanceId,
                Backend = BackendType.AndroidEmulator,
                State = GetState(instanceId)
            };

            try
            {
                var instances = _engine.GetInstances();
                var target = instances.FirstOrDefault(i => string.Equals(i.Name, instanceId, StringComparison.OrdinalIgnoreCase));
                if (target != null && target.Pid > 0)
                {
                    using (var proc = Process.GetProcessById(target.Pid))
                    {
                        diag.WorkingSetMb = proc.WorkingSet64 / (1024.0 * 1024.0);
                        diag.PrivateCommitMb = proc.PrivateMemorySize64 / (1024.0 * 1024.0);
                        diag.Details = string.Format("PID: {0} | Priority: {1}", target.Pid, proc.PriorityClass);
                    }
                }
            }
            catch { }

            return diag;
        }

        private void SetState(string instanceId, ClientInstanceState newState)
        {
            _states[instanceId] = newState;
            try { OnStateChanged?.Invoke(instanceId, newState); } catch { }
        }

        public ICollection<string> GetActiveInstanceIds()
        {
            var list = new List<string>();
            try
            {
                foreach (var inst in _engine.GetInstances())
                {
                    if (inst.IsRunning) list.Add(inst.Name);
                }
            }
            catch { }
            return list;
        }

        public List<int> GetAllBrowserProcessIds()
        {
            return new List<int>();
        }

        public void Dispose()
        {
            _states.Clear();
        }
    }
}
