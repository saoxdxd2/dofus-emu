using System;
using System.Management;

namespace DofusFarm.Hardware
{
    public class HardwareSpecs
    {
        public string CpuName = "Unknown CPU";
        public int PhysicalCores = 1;
        public int LogicalThreads = 1;
        public double HostRamGB = 0;
        public double FreeRamGB = 0;
        public string GpuName = "Generic GPU";
        public string GpuDriver = "Unknown";
        public string Recommendation = "";

        public static HardwareSpecs Detect()
        {
            HardwareSpecs specs = new HardwareSpecs();
            try
            {
                using (ManagementObjectSearcher s = new ManagementObjectSearcher("SELECT Name, NumberOfCores, NumberOfLogicalProcessors FROM Win32_Processor"))
                using (ManagementObjectCollection coll = s.Get())
                {
                    foreach (ManagementObject mo in coll)
                    {
                        using (mo)
                        {
                            specs.CpuName = (mo["Name"] ?? "").ToString().Trim();
                            specs.PhysicalCores = Convert.ToInt32(mo["NumberOfCores"] ?? 1);
                            specs.LogicalThreads = Convert.ToInt32(mo["NumberOfLogicalProcessors"] ?? 1);
                            break;
                        }
                    }
                }
            }
            catch { }

            try
            {
                using (ManagementObjectSearcher s = new ManagementObjectSearcher("SELECT TotalPhysicalMemory FROM Win32_ComputerSystem"))
                using (ManagementObjectCollection coll = s.Get())
                {
                    foreach (ManagementObject mo in coll)
                    {
                        using (mo)
                        {
                            ulong bytes = Convert.ToUInt64(mo["TotalPhysicalMemory"] ?? 0);
                            specs.HostRamGB = Math.Round(bytes / (1024.0 * 1024.0 * 1024.0), 1);
                            break;
                        }
                    }
                }
            }
            catch { }

            try
            {
                using (ManagementObjectSearcher s = new ManagementObjectSearcher("SELECT FreePhysicalMemory FROM Win32_OperatingSystem"))
                using (ManagementObjectCollection coll = s.Get())
                {
                    foreach (ManagementObject mo in coll)
                    {
                        using (mo)
                        {
                            ulong kb = Convert.ToUInt64(mo["FreePhysicalMemory"] ?? 0);
                            specs.FreeRamGB = Math.Round(kb / (1024.0 * 1024.0), 1);
                            break;
                        }
                    }
                }
            }
            catch { }

            try
            {
                using (ManagementObjectSearcher s = new ManagementObjectSearcher("SELECT Name, DriverVersion FROM Win32_VideoController"))
                using (ManagementObjectCollection coll = s.Get())
                {
                    foreach (ManagementObject mo in coll)
                    {
                        using (mo)
                        {
                            string name = (mo["Name"] ?? "").ToString().Trim();
                            if (!name.ToLower().Contains("basic") && !name.ToLower().Contains("remote"))
                            {
                                specs.GpuName = name;
                                specs.GpuDriver = (mo["DriverVersion"] ?? "").ToString().Trim();
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
    }
}
