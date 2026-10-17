using System;
using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace DofusFarm.Hardware
{
    public class HardwareSpecs
    {
        public string CpuName = "Unknown CPU";
        public int PhysicalCores = Math.Max(1, Environment.ProcessorCount / 2);
        public int LogicalThreads = Math.Max(1, Environment.ProcessorCount);
        public double HostRamGB = 8.0;
        public double FreeRamGB = 4.0;
        public string GpuName = "DirectX / OpenGL Hardware GPU";
        public string GpuDriver = "Unknown";
        public string Recommendation = "";

        // ------------------------------------------------------------------
        // WMI-free detection (registry + kernel32 only).
        // The previous implementation fired 4 WMI queries, each spawning a
        // wmiprvse.exe host process with a 3s timeout (up to a 12s stall and
        // handle churn on every Detect() call). These native calls return in
        // ~1-5 ms total with zero child processes and zero WMI handles.
        // ------------------------------------------------------------------
        public static HardwareSpecs Detect()
        {
            HardwareSpecs specs = new HardwareSpecs();

            // --- CPU: logical threads + physical cores ---
            specs.LogicalThreads = Math.Max(1, Environment.ProcessorCount);
            specs.PhysicalCores = Math.Max(1, CountPhysicalCores());

            try
            {
                // HKLM\HARDWARE is populated by the kernel at boot: instant read.
                object cpuName = Registry.GetValue(
                    @"HKEY_LOCAL_MACHINE\HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                    "ProcessorNameString", null);
                string cName = cpuName == null ? "" : cpuName.ToString().Trim();
                if (cName.Length > 0) specs.CpuName = cName;
            }
            catch { }

            try
            {
                MEMORYSTATUSEX mem = new MEMORYSTATUSEX();
                mem.dwLength = (uint)Marshal.SizeOf(typeof(MEMORYSTATUSEX));
                if (GlobalMemoryStatusEx(mem))
                {
                    if (mem.ullTotalPhys > 0)
                        specs.HostRamGB = Math.Round(mem.ullTotalPhys / (1024.0 * 1024.0 * 1024.0), 1);
                    if (mem.ullAvailPhys > 0)
                        specs.FreeRamGB = Math.Round(mem.ullAvailPhys / (1024.0 * 1024.0 * 1024.0), 1);
                }
            }
            catch { }

            try
            {
                // The display-adapter class key is the exact registry source that
                // Win32_VideoController reads from; querying it directly skips the
                // WMI service hop entirely.
                using (RegistryKey root = Registry.LocalMachine.OpenSubKey(
                    @"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"))
                {
                    if (root != null)
                    {
                        for (int i = 0; i < 16; i++)
                        {
                            using (RegistryKey sub = root.OpenSubKey(i.ToString("0000")))
                            {
                                if (sub == null) continue;
                                object descObj = sub.GetValue("DriverDesc");
                                string desc = descObj == null ? "" : descObj.ToString().Trim();
                                if (desc.Length == 0) continue;
                                string lower = desc.ToLower();
                                if (lower.Contains("basic") || lower.Contains("remote")) continue;
                                specs.GpuName = desc;
                                object verObj = sub.GetValue("DriverVersion");
                                specs.GpuDriver = verObj == null ? "" : verObj.ToString().Trim();
                                break;
                            }
                        }
                    }
                }
            }
            catch { }

            if (specs.HostRamGB <= 8.5)
            {
                specs.Recommendation = string.Format(
                    "Host has {0} GB RAM & {1} threads. Recommended: 4x Farm @ 1024 MB RAM / 1 Core per instance.",
                    specs.HostRamGB, specs.LogicalThreads);
            }
            else if (specs.HostRamGB <= 16.5)
            {
                specs.Recommendation = string.Format(
                    "Host has {0} GB RAM & {1} threads. Capable of 4-8 instances @ 1024-2048 MB RAM, or 1 Single-Instance Power Mode @ 12 GB RAM / 6 Cores.",
                    specs.HostRamGB, specs.LogicalThreads);
            }
            else
            {
                specs.Recommendation = string.Format(
                    "High-end workstation: {0} GB RAM & {1} threads. Supports up to 16 GB RAM / 8 Cores per instance.",
                    specs.HostRamGB, specs.LogicalThreads);
            }

            return specs;
        }

        // Counts physical (HT-ignoring) cores via GetLogicalProcessorInformationEx first: the
        // legacy single-group API below fails outright on machines with >64 logical processors
        // (big farm servers), where ProcessorCount/2 was a wrong guess rather than a count.
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetLogicalProcessorInformationEx(int relationshipType, IntPtr buffer, ref int returnedLength);

        private static int CountPhysicalCoresEx()
        {
            int len = 0;
            GetLogicalProcessorInformationEx(0, IntPtr.Zero, ref len);
            if (len <= 0) return 0;

            IntPtr buf = Marshal.AllocHGlobal(len);
            try
            {
                if (!GetLogicalProcessorInformationEx(0, buf, ref len)) return 0;
                int cores = 0, offset = 0;
                while (offset < len)
                {
                    int relationship = Marshal.ReadInt32(buf, offset);
                    int size = Marshal.ReadInt32(buf, offset + 4);
                    if (size <= 0) break;
                    if (relationship == 0) cores++; // RelationProcessorCore
                    offset += size;
                }
                return cores;
            }
            finally { Marshal.FreeHGlobal(buf); }
        }

        private static int CountPhysicalCores()
        {
            try
            {
                int ex = CountPhysicalCoresEx();
                if (ex > 0) return ex;
            }
            catch { }

            IntPtr buffer = IntPtr.Zero;
            try
            {
                uint len = 0;
                GetLogicalProcessorInformation(IntPtr.Zero, ref len);
                if (len == 0) return Math.Max(1, Environment.ProcessorCount / 2);

                buffer = Marshal.AllocHGlobal((int)len);
                if (!GetLogicalProcessorInformation(buffer, ref len))
                    return Math.Max(1, Environment.ProcessorCount / 2);

                int stride = Marshal.SizeOf(typeof(SYSTEM_LOGICAL_PROCESSOR_INFORMATION));
                int cores = 0;
                long limit = (long)len - stride;
                for (long off = 0; off <= limit; off += stride)
                {
                    SYSTEM_LOGICAL_PROCESSOR_INFORMATION info =
                        (SYSTEM_LOGICAL_PROCESSOR_INFORMATION)Marshal.PtrToStructure(
                            new IntPtr((long)buffer + off),
                            typeof(SYSTEM_LOGICAL_PROCESSOR_INFORMATION));
                    if (info.Relationship == 0) cores++; // RelationProcessorCore
                }
                return Math.Max(1, cores);
            }
            catch
            {
                return Math.Max(1, Environment.ProcessorCount / 2);
            }
            finally
            {
                if (buffer != IntPtr.Zero) Marshal.FreeHGlobal(buffer);
            }
        }

        #region Native interop (replaces System.Management / WMI)

        [StructLayout(LayoutKind.Sequential)]
        private struct SYSTEM_LOGICAL_PROCESSOR_INFORMATION
        {
            public IntPtr ProcessorMask;
            public int Relationship;
            [MarshalAs(UnmanagedType.ByValArray, SizeConst = 16)]
            public byte[] Extra;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetLogicalProcessorInformation(IntPtr Buffer, ref uint ReturnLength);

        [StructLayout(LayoutKind.Sequential)]
        private class MEMORYSTATUSEX
        {
            public uint dwLength;
            public uint dwMemoryLoad;
            public ulong ullTotalPhys;
            public ulong ullAvailPhys;
            public ulong ullTotalPageFile;
            public ulong ullAvailPageFile;
            public ulong ullTotalVirtual;
            public ulong ullAvailVirtual;
            public ulong ullAvailExtendedVirtual;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GlobalMemoryStatusEx([In, Out] MEMORYSTATUSEX lpBuffer);

        #endregion
    }
}
