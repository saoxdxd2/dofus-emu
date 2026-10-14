using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Management;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace DofusFarm.Native
{
    public static class NativeMethods
    {
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern bool CreateProcessW(
            string lpApplicationName, string lpCommandLine,
            IntPtr lpProcessAttributes, IntPtr lpThreadAttributes,
            bool bInheritHandles, uint dwCreationFlags,
            IntPtr lpEnvironment, string lpCurrentDirectory,
            ref STARTUPINFOW lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr hObject);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool TerminateProcess(IntPtr hProcess, uint uExitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern uint WaitForSingleObject(IntPtr hHandle, uint dwMilliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr OpenProcess(uint dwDesiredAccess, bool bInheritHandle, int dwProcessId);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool ReadProcessMemory(IntPtr hProcess, IntPtr lpBaseAddress,
            byte[] lpBuffer, uint nSize, out uint lpNumberOfBytesRead);        [DllImport("ntdll.dll", SetLastError = true)]
        public static extern uint NtQueryInformationProcess(IntPtr hProcess, int processInfoClass,
            ref PROCESS_BASIC_INFORMATION_X processInformation, uint processInformationLength,
            ref uint returnLength);

        [StructLayout(LayoutKind.Sequential)]
        public struct PROCESS_BASIC_INFORMATION_X
        {
            public IntPtr ExitStatus;
            public IntPtr PebBaseAddress;
            public IntPtr AffinityMask;
            public int BasePriority;
            public IntPtr UniqueProcessId;
            public IntPtr InheritedFromUniqueProcessId;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct PEB_X
        {
            public byte byteReserved1; public byte BeingDebugged; public byte Reserved2; public byte Reserved3;
            public IntPtr Reserved4; public IntPtr Reserved5; public IntPtr Reserved6;
            public IntPtr ProcessParameters;
            public IntPtr Reserved7; public IntPtr Reserved8; public IntPtr Reserved9;
            public IntPtr Reserved10; public IntPtr CLSID; public IntPtr Reserved11;
            public IntPtr Reserved12; public IntPtr Reserved13; public IntPtr Reserved14;
            public IntPtr Reserved15; public IntPtr Reserved16; public IntPtr Reserved17;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct RTL_USER_PROCESS_PARAMETERS_X
        {
            public ushort Length; public ushort MaximumLength; public IntPtr DisposalParameters;
            public IntPtr ImagePathName; public IntPtr CommandLine;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CreatePipe(ref IntPtr hReadPipe, ref IntPtr hWritePipe,
            ref SECURITY_ATTRIBUTES lpPipeAttributes, uint nSize);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool ReadFile(IntPtr hFile, byte[] lpBuffer, uint nNumberOfBytesToRead,
            out uint lpNumberOfBytesRead, IntPtr lpOverlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool PeekNamedPipe(IntPtr hPipe, IntPtr lpBuffer, uint nBufferSize,
            out uint lpBytesRead, out uint lpTotalBytesAvail, IntPtr lpReserved);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern IntPtr CreateDirectoryW(string lpPathName, IntPtr lpSecurityAttributes);
    }

    public struct STARTUPINFOW
    {
        public uint cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public uint dwX;
        public uint dwY;
        public uint dwXSize;
        public uint dwYSize;
        public uint dwXCountChars;
        public uint dwYCountChars;
        public uint dwFillAttribute;
        public uint dwFlags;
        public ushort wShowWindow;
        public ushort cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    [StructLayout(LayoutKind.Sequential)]
    public struct SECURITY_ATTRIBUTES
    {
        public int nLength;
        public bool bInheritHandle;
        public IntPtr lpSecurityDescriptor;
    }

    public struct PROCESS_INFORMATION
    {
        public IntPtr hProcess;
        public IntPtr hThread;
        public uint dwProcessId;
        public uint dwThreadId;
    }


    public static class Win32
    {
        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter,
            int X, int Y, int cx, int cy, uint uFlags);

        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll")]
        public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll")]
        public static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        public static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool CreateHardLink(string lpFileName, string lpExistingFileName, IntPtr lpSecurityAttributes);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr CreateToolhelp32Snapshot(uint dwFlags, uint th32ProcessID);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr hObject);

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
        public struct PROCESSENTRY32
        {
            public uint dwSize;
            public uint cntUsage;
            public uint th32ProcessID;
            public IntPtr th32DefaultHeapID;
            public uint th32ModuleID;
            public uint cntThreads;
            public uint th32ParentProcessID;
            public int pcPriClassBase;
            public uint dwFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
            public string szExeFile;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        public static extern bool Process32First(IntPtr hSnapshot, ref PROCESSENTRY32 lppe);

        [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        public static extern bool Process32Next(IntPtr hSnapshot, ref PROCESSENTRY32 lppe);

        public const uint TH32CS_SNAPPROCESS = 0x00000002;

        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);

        [DllImport("user32.dll")]
        public static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

        [DllImport("user32.dll")]
        public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);

        [DllImport("user32.dll", SetLastError = true)]
        public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern int GetClassNameW(IntPtr hWnd, StringBuilder s, int n);

        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder s, int n);

        [DllImport("user32.dll", EntryPoint = "GetWindowLongW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern int GetWindowLong32(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern IntPtr GetWindowLongPtr64(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern int SetWindowLong32(IntPtr hWnd, int nIndex, int dwNewLong);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern IntPtr SetWindowLongPtr64(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

        public static IntPtr GetWindowLong(IntPtr hWnd, int nIndex)
        {
            if (IntPtr.Size == 8)
                return GetWindowLongPtr64(hWnd, nIndex);
            else
                return new IntPtr(GetWindowLong32(hWnd, nIndex));
        }

        public static IntPtr SetWindowLong(IntPtr hWnd, int nIndex, IntPtr dwNewLong)
        {
            if (IntPtr.Size == 8)
                return SetWindowLongPtr64(hWnd, nIndex, dwNewLong);
            else
                return new IntPtr(SetWindowLong32(hWnd, nIndex, unchecked((int)dwNewLong.ToInt64())));
        }

        [return: MarshalAs(UnmanagedType.Bool)]
        public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [StructLayout(LayoutKind.Sequential)]
        public struct RECT
        {
            public int Left;
            public int Top;
            public int Right;
            public int Bottom;
            public int Width { get { return Right - Left; } }
            public int Height { get { return Bottom - Top; } }
        }

        public const int SW_HIDE = 0;
        public const int SW_SHOWNORMAL = 1;
        public const int SW_SHOWMINIMIZED = 2;
        public const int SW_MAXIMIZE = 3;
        public const int SW_RESTORE = 9;

        public class WindowInfo
        {
            public IntPtr Handle;
            public int Pid;
            public string Class;
            public string Title;
            public int Width;
            public int Height;
        }

        public static HashSet<int> GetProcessTrees(IEnumerable<int> rootPids)
        {
            HashSet<int> targets = rootPids != null ? new HashSet<int>(rootPids) : new HashSet<int>();
            if (targets.Count == 0) return targets;
            Dictionary<int, List<int>> parentMap = new Dictionary<int, List<int>>();

            IntPtr snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if (snap != IntPtr.Zero && snap != (IntPtr)(-1))
            {
                try
                {
                    PROCESSENTRY32 pe = new PROCESSENTRY32();
                    pe.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
                    if (Process32First(snap, ref pe))
                    {
                        do
                        {
                            int ppid = (int)pe.th32ParentProcessID;
                            int pid = (int)pe.th32ProcessID;
                            if (!parentMap.ContainsKey(ppid))
                                parentMap[ppid] = new List<int>();
                            parentMap[ppid].Add(pid);
                        } while (Process32Next(snap, ref pe));
                    }
                }
                finally
                {
                    CloseHandle(snap);
                }
            }

            Queue<int> frontier = new Queue<int>(rootPids);
            while (frontier.Count > 0)
            {
                int curr = frontier.Dequeue();
                if (parentMap.ContainsKey(curr))
                {
                    foreach (int child in parentMap[curr])
                    {
                        if (targets.Add(child))
                        {
                            frontier.Enqueue(child);
                        }
                    }
                }
            }
            return targets;
        }

        public static List<int> GetProcessTree(int rootPid)
        {
            return new List<int>(GetProcessTrees(new[] { rootPid }));
        }

        public static List<WindowInfo> GetEmulatorWindows(IEnumerable<int> launcherPids)
        {
            HashSet<int> targets = GetProcessTrees(launcherPids);
            List<WindowInfo> found = new List<WindowInfo>();
            EnumWindows(delegate (IntPtr h, IntPtr l)
            {
                uint wpid = 0;
                GetWindowThreadProcessId(h, out wpid);
                if (targets.Contains((int)wpid))
                {
                    StringBuilder sb = new StringBuilder(256);
                    GetClassNameW(h, sb, 256);
                    StringBuilder tb = new StringBuilder(512);
                    GetWindowTextW(h, tb, 512);
                    RECT r;
                    GetWindowRect(h, out r);

                    found.Add(new WindowInfo
                    {
                        Handle = h,
                        Pid = (int)wpid,
                        Class = sb.ToString(),
                        Title = tb.ToString(),
                        Width = r.Width,
                        Height = r.Height
                    });
                }
                return true;
            }, IntPtr.Zero);

            return found;
        }

        public static void HideAllProcessWindows(int rootPid)
        {
            if (rootPid <= 0) return;
            foreach (WindowInfo w in GetEmulatorWindows(new[] { rootPid }))
            {
                ShowWindowAsync(w.Handle, SW_HIDE);
            }
        }

        public static void HideEmulatorConsole(IEnumerable<int> launcherPids)
        {
            foreach (WindowInfo w in GetEmulatorWindows(launcherPids))
            {
                if (w.Class == "ConsoleWindowClass")
                {
                    ShowWindowAsync(w.Handle, SW_HIDE);
                }
            }
        }

        public static void HideEmulatorToolbar(IEnumerable<int> launcherPids)
        {
            foreach (WindowInfo w in GetEmulatorWindows(launcherPids))
            {
                if (w.Class.IndexOf("ToolSaveBits", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    (w.Title == "Emulator" && w.Width < 120))
                {
                    ShowWindowAsync(w.Handle, SW_HIDE);
                }
            }
        }

        public static IntPtr FindDeviceWindow(int launcherPid)
        {
            List<WindowInfo> wins = GetEmulatorWindows(new[] { launcherPid });
            foreach (WindowInfo w in wins)
            {
                if (w.Class != "ConsoleWindowClass" &&
                    !w.Class.Contains("IME") &&
                    !w.Class.Contains("DummyWin") &&
                    !w.Class.Contains("ToolSaveBits") &&
                    !w.Class.Contains("PbufferClass") &&
                    w.Width > 200 && w.Height > 150)
                {
                    if (w.Title.StartsWith("Android Emulator", StringComparison.OrdinalIgnoreCase) ||
                        w.Class.Contains("QWindowIcon"))
                        return w.Handle;
                }
            }

            WindowInfo biggest = null;
            foreach (WindowInfo w in wins)
            {
                if (w.Class != "ConsoleWindowClass" &&
                    !w.Class.Contains("ToolSaveBits") &&
                    !w.Class.Contains("PbufferClass") &&
                    w.Width > 200 && w.Height > 150)
                {
                    if (biggest == null || (w.Width * w.Height > biggest.Width * biggest.Height))
                        biggest = w;
                }
            }
            return biggest != null ? biggest.Handle : IntPtr.Zero;
        }

        public static Rectangle[] ComputeLayout(int count, bool edgeToEdge)
        {
            Rectangle wa = Screen.PrimaryScreen.WorkingArea;
            int gap = edgeToEdge ? 0 : 4;

            if (count <= 1)
            {
                return new[] { new Rectangle(wa.X, wa.Y, wa.Width, wa.Height) };
            }
            if (count == 2)
            {
                int w = (wa.Width - gap) / 2;
                return new[]
                {
                    new Rectangle(wa.X, wa.Y, w, wa.Height),
                    new Rectangle(wa.X + w + gap, wa.Y, wa.Width - w - gap, wa.Height)
                };
            }
            if (count == 3)
            {
                int w = (wa.Width - gap) / 2;
                int h = (wa.Height - gap) / 2;
                return new[]
                {
                    new Rectangle(wa.X, wa.Y, w, h),
                    new Rectangle(wa.X + w + gap, wa.Y, wa.Width - w - gap, h),
                    new Rectangle(wa.X, wa.Y + h + gap, wa.Width, wa.Height - h - gap)
                };
            }

            // 4x grid or N-quad
            int cols = 2;
            int rows = (count + 1) / 2;
            int cellW = (wa.Width - gap * (cols - 1)) / cols;
            int cellH = (wa.Height - gap * (rows - 1)) / rows;

            Rectangle[] rects = new Rectangle[count];
            for (int i = 0; i < count; i++)
            {
                int c = i % cols;
                int r = i / cols;
                int x = wa.X + c * (cellW + gap);
                int y = wa.Y + r * (cellH + gap);
                int w = (c == cols - 1) ? (wa.X + wa.Width - x) : cellW;
                int h = (r == rows - 1) ? (wa.Y + wa.Height - y) : cellH;
                rects[i] = new Rectangle(x, y, w, h);
            }
            return rects;
        }

        public static void ApplyFarmLayout(IList<int> launcherPids, bool edgeToEdge)
        {
            if (launcherPids == null || launcherPids.Count == 0) return;

            HideEmulatorConsole(launcherPids);
            HideEmulatorToolbar(launcherPids);

            Rectangle[] rects = ComputeLayout(launcherPids.Count, edgeToEdge);

            for (int i = 0; i < launcherPids.Count && i < rects.Length; i++)
            {
                IntPtr hWnd = FindDeviceWindow(launcherPids[i]);
                if (hWnd != IntPtr.Zero)
                {
                    ShowWindow(hWnd, SW_RESTORE);
                    MoveWindow(hWnd, rects[i].X, rects[i].Y, rects[i].Width, rects[i].Height, true);
                    SetWindowPos(hWnd, IntPtr.Zero, rects[i].X, rects[i].Y, rects[i].Width, rects[i].Height, 0x0040);
                }
            }
        }

        public static void ApplyCascadeLayout(IList<int> launcherPids)
        {
            if (launcherPids == null || launcherPids.Count == 0) return;

            HideEmulatorConsole(launcherPids);
            HideEmulatorToolbar(launcherPids);

            Rectangle wa = Screen.PrimaryScreen.WorkingArea;
            int width = Math.Min(640, (int)(wa.Width * 0.5));
            int height = Math.Min(800, (int)(wa.Height * 0.75));
            int stepX = 45;
            int stepY = 45;

            for (int i = 0; i < launcherPids.Count; i++)
            {
                IntPtr hWnd = FindDeviceWindow(launcherPids[i]);
                if (hWnd != IntPtr.Zero)
                {
                    ShowWindow(hWnd, SW_RESTORE);
                    int x = wa.X + (i * stepX);
                    int y = wa.Y + (i * stepY);
                    MoveWindow(hWnd, x, y, width, height, true);
                    SetWindowPos(hWnd, IntPtr.Zero, x, y, width, height, 0x0040);
                }
            }
        }
    }
}
