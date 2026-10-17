using System;
using System.Collections.Generic;

namespace DofusFarm.Engine
{
    public enum BackendType
    {
        AndroidEmulator,
        WebView2Standalone
    }

    public enum ClientInstanceState
    {
        Stopped,
        Starting,
        Running,
        Stopping,
        Faulted
    }

    public class ClientLaunchOptions
    {
        public string InstanceId { get; set; }
        public string DisplayName { get; set; }
        public int SlotPort { get; set; }
        public int RamMb { get; set; }
        public int Cores { get; set; }
        public string Proxy { get; set; }
        public string EntryUrl { get; set; }
        public string UserDataDirectory { get; set; }

        // Coherent mobile blackbox properties
        public DeviceProfile Profile { get; set; }
        public string DeviceSerial { get; set; }
        public string DeviceUuid { get; set; }
        public string DisguiseScript { get; set; }
        public int ProxyPort { get; set; }

        // CPU & rendering optimizations
        public bool EnableBackgroundThrottling { get; set; }
        public int TargetFps { get; set; }

        // Dynamic client & server protocol versioning
        public string AppVersion { get; set; }
        public string BuildVersion { get; set; }
        public string VersionCode { get; set; }

        public ClientLaunchOptions()
        {
            RamMb = 1024;
            Cores = 1;
            TargetFps = 60;
            EnableBackgroundThrottling = true;
        }
    }

    public class ClientDiagnostics
    {
        public string InstanceId { get; set; }
        public BackendType Backend { get; set; }
        public ClientInstanceState State { get; set; }
        public double WorkingSetMb { get; set; }
        public double PrivateCommitMb { get; set; }
        public double CpuPercent { get; set; }
        public string Details { get; set; }
        public DateTime Timestamp { get; set; }

        public ClientDiagnostics()
        {
            Timestamp = DateTime.UtcNow;
            Details = "";
        }
    }

    public interface IClientBackend : IDisposable
    {
        string Name { get; }
        BackendType Type { get; }

        bool IsSupported(out string failureReason);
        void Initialize();
        void Start(ClientLaunchOptions options);
        void Stop(string instanceId);
        void Focus(string instanceId);
        ClientInstanceState GetState(string instanceId);
        ClientDiagnostics GetDiagnostics(string instanceId);
        void TileWindows(bool edgeToEdge);
        void CascadeWindows();
        ICollection<string> GetActiveInstanceIds();
        List<int> GetAllBrowserProcessIds();

        event Action<string> OnLog;
        event Action<string, ClientInstanceState> OnStateChanged;
    }
}
