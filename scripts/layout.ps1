<#
.SYNOPSIS
  Adaptive window placement for N emulator instances. Dot-source this file.

.DESCRIPTION
  Computes a layout for 1, 2, 3 or 4 windows and applies it with Win32
  SetWindowPos, so a farm actually fills the screen instead of stacking up
  behind each other:

    1 instance -> single maximised window
    2 instances-> two halves, side by side
    3 instances-> two on top, one full-width underneath
    4 instances-> clean 2x2 quad

  Works for any N: beyond 4 it falls back to a column-per-row grid so the
  farm degrades gracefully rather than throwing.

  The emulator exposes TWO visible windows and only one is the device:

    emulator.exe           -> ConsoleWindowClass   (log window - hidden)
    qemu-system-x86_64.exe -> Qt...QWindowIcon      (the real device window)

  So the device window is found by walking the launcher process tree and
  picking the Qt "Android Emulator" window, never by MainWindowHandle - that
  returns the console, which is how an earlier version ended up resizing the
  log window instead of the game.

.EXAMPLE
  . "$PSScriptRoot\layout.ps1"
  Get-FarmLayout -Count 3
  Set-FarmLayout -Procs $procs
#>

# Add-Type is a no-op when the type already exists in the session, which silently
# leaves a STALE definition behind after editing this file (a long-lived
# PowerShell console will keep the old shape and every new member will be
# "method not found"). Compile into a version-stamped namespace and reference
# that, so a reload always picks up the current member set.
$Script:FarmNs = 'FarmWin32v2'

if (-not ('{0}.Win32' -f $Script:FarmNs -as [type])) {
  Add-Type -Namespace $Script:FarmNs -Name Win32 -MemberDefinition @'
[DllImport("user32.dll", SetLastError=true)]
public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter,
    int X, int Y, int cx, int cy, uint uFlags);

[DllImport("user32.dll")]
public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

[DllImport("user32.dll")]
public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);

[DllImport("user32.dll")]
public static extern bool IsWindowVisible(IntPtr hWnd);

[DllImport("user32.dll")]
public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

[DllImport("user32.dll")]
public static extern IntPtr GetForegroundWindow();

[DllImport("user32.dll")]
public static extern bool SetForegroundWindow(IntPtr hWnd);

[DllImport("user32.dll")]
public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);

[DllImport("user32.dll")]
public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern int GetClassNameW(IntPtr hWnd, System.Text.StringBuilder s, int n);

[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern int GetWindowTextW(IntPtr hWnd, System.Text.StringBuilder s, int n);

[DllImport("user32.dll")]
public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

[StructLayout(LayoutKind.Sequential)]
public struct RECT { public int Left, Top, Right, Bottom; }
'@
}

$Script:W32 = ('{0}.Win32' -f $Script:FarmNs)

$Script:FarmShow = @{
  SW_HIDE       = 0
  SW_SHOWNORMAL = 1
  SW_MAXIMIZE   = 3
  SW_RESTORE    = 9
}

function Get-ScreenWorkArea {
  <#
    Primary monitor work area (screen minus taskbar). Falls back to a sane
    1920x1080 if the query fails, so a headless/CI box never divides by zero.
  #>
  try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    if ($wa.Width -gt 0 -and $wa.Height -gt 0) {
      return [pscustomobject]@{ X = $wa.X; Y = $wa.Y; W = $wa.Width; H = $wa.Height }
    }
    return [pscustomobject]@{ X = $b.X; Y = $b.Y; W = $b.Width; H = $b.Height }
  } catch {
    return [pscustomobject]@{ X = 0; Y = 0; W = 1920; H = 1080 }
  }
}

function Get-FarmLayout {
  <#
    Returns an array of placement rects (X, Y, W, H) for the given count.
    Pure geometry - no window handles - so it is trivially testable.
  #>
  param([Parameter(Mandatory)][int]$Count, [switch]$EdgeToEdge)

  if ($Count -lt 1) { throw "Count must be >= 1 (got $Count)" }

  $wa = Get-ScreenWorkArea
  $gap = if ($EdgeToEdge) { 0 } else { 4 }

  switch ($Count) {
    1 {
      return @([pscustomobject]@{ X = $wa.X; Y = $wa.Y; W = $wa.W; H = $wa.H })
    }
    2 {
      $w = [int](($wa.W - $gap) / 2)
      return @(
        [pscustomobject]@{ X = $wa.X;          Y = $wa.Y; W = $w;         H = $wa.H },
        [pscustomobject]@{ X = $wa.X + $w + $gap; Y = $wa.Y; W = $wa.W - $w - $gap; H = $wa.H }
      )
    }
    3 {
      $w = [int](($wa.W - $gap) / 2)
      $h = [int](($wa.H - $gap) / 2)
      return @(
        [pscustomobject]@{ X = $wa.X;            Y = $wa.Y;             W = $w;         H = $h },
        [pscustomobject]@{ X = $wa.X + $w + $gap; Y = $wa.Y;             W = $wa.W - $w - $gap; H = $h },
        [pscustomobject]@{ X = $wa.X;            Y = $wa.Y + $h + $gap; W = $wa.W;     H = $wa.H - $h - $gap }
      )
    }
    default {
      # 4 -> exact 2x2. N>4 -> rows x cols grid, cells as square as possible.
      if ($Count -eq 4) {
        $cols = 2; $rows = 2
      } else {
        $cols = [int][math]::Ceiling([math]::Sqrt($Count))
        $rows = [int][math]::Ceiling($Count / $cols)
      }
      $cw = [int](($wa.W - $gap * ($cols - 1)) / $cols)
      $ch = [int](($wa.H - $gap * ($rows - 1)) / $rows)
      $out = @()
      for ($i = 0; $i -lt $Count; $i++) {
        $c = $i % $cols
        $r = [int][math]::Floor($i / $cols)
        $x = $wa.X + $c * ($cw + $gap)
        $y = $wa.Y + $r * ($ch + $gap)
        $out += [pscustomobject]@{
          X = $x
          Y = $y
          W = $(if ($c -eq $cols - 1) { $wa.X + $wa.W - $x } else { $cw })
          H = $(if ($r -eq $rows - 1) { $wa.Y + $wa.H - $y } else { $ch })
        }
      }
      return $out
    }
  }
}

function Get-ProcessTree([int]$RootPid) {
  $tree = @($RootPid); $seen = @{}; $frontier = @($RootPid)
  while ($frontier.Count -gt 0) {
    $next = @()
    foreach ($parent in $frontier) {
      $kids = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$parent" -EA SilentlyContinue)
      foreach ($k in $kids) {
        if (-not $seen.ContainsKey($k.ProcessId)) {
          $seen[$k.ProcessId] = $true
          $tree += $k.ProcessId
          $next += $k.ProcessId
        }
      }
    }
    $frontier = $next
  }
  return $tree
}

function Get-EmulatorWindows {
  <#
    Return EVERY visible top-level window in the launcher process tree,
    annotated with its window class. Needed because a farm launcher exposes
    two visible windows and only one of them is the device:

      emulator.exe              -> ConsoleWindowClass   (the LOG window - hide it)
      qemu-system-x86_64.exe    -> Qt...QWindowIcon      (the real device - size this)

    MainWindowHandle is unreliable here: it happily returns the console, which
    is why an earlier version of this script resized the log window instead of
    the game.
  #>
  param([Parameter(Mandatory)][int[]]$LauncherPids)

  $targets = @()
  foreach ($lp in $LauncherPids) { $targets += Get-ProcessTree $lp }

  # Local alias so the type name interpolates inside the enum callback.
  $W32 = $Script:W32
  $found = New-Object System.Collections.ArrayList
  $cb = [FarmWin32v2.Win32+EnumWindowsProc]{
    param($h, $l)
    $wpid = 0
    [void][FarmWin32v2.Win32]::GetWindowThreadProcessId($h, [ref]$wpid)
    if ($targets -contains [int]$wpid) {
      $sb = New-Object System.Text.StringBuilder 256
      [void][FarmWin32v2.Win32]::GetClassNameW($h, $sb, 256)
      $tb = New-Object System.Text.StringBuilder 512
      [void][FarmWin32v2.Win32]::GetWindowTextW($h, $tb, 512)
      $r = New-Object FarmWin32v2.Win32+RECT
      [void][FarmWin32v2.Win32]::GetWindowRect($h, [ref]$r)
      [void]$found.Add([pscustomobject]@{
        Handle = $h
        Pid    = [int]$wpid
        Class  = $sb.ToString()
        Title  = $tb.ToString()
        Width  = ($r.Right - $r.Left)
        Height = ($r.Bottom - $r.Top)
      })
    }
    return $true
  }
  [void][FarmWin32v2.Win32]::EnumWindows($cb, [IntPtr]::Zero)
  return $found
}

function Get-RenderWindow {
  <#
    Pick the real device window out of the launcher tree.

    Selection rules, in order:
      1. class ConsoleWindowClass is the log window -> never choose it
      2. a Qt window whose title starts with "Android Emulator" is the device
      3. otherwise the largest non-console window wins
  #>
  param([Parameter(Mandatory)][int]$LauncherPid, [int]$TimeoutSec = 90)

  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $wins = Get-EmulatorWindows -LauncherPids @($LauncherPid)
    $cands = $wins | Where-Object {
      $_.Class -ne 'ConsoleWindowClass' -and
      $_.Class -notlike 'IME' -and $_.Class -notlike 'DummyWin' -and
      $_.Class -notlike '*ToolSaveBits' -and $_.Class -notlike 'MSCTFIME*'
    }
    $android = $cands | Where-Object { $_.Title -like 'Android Emulator*' } | Select-Object -First 1
    if ($android) { return $android }
    $big = $cands | Sort-Object @{e={$_.Width * $_.Height};Descending=$true} | Select-Object -First 1
    if ($big -and $big.Width -gt 100) { return $big }
    Start-Sleep -Milliseconds 750
  }
  return $null
}

function Hide-EmulatorConsole {
  <#
    Hide the emulator's console/log window so it does not sit on top of the
    farm. Uses ShowWindowAsync because ShowWindow would block on a window that
    belongs to another process.
  #>
  param([Parameter(Mandatory)][int[]]$LauncherPids)
  $W32 = $Script:W32
  $wins = Get-EmulatorWindows -LauncherPids $LauncherPids
  foreach ($w in ($wins | Where-Object { $_.Class -eq 'ConsoleWindowClass' })) {
    [void][FarmWin32v2.Win32]::ShowWindowAsync($w.Handle, $Script:FarmShow.SW_HIDE)
    Write-Host "[layout] hid emulator log window (pid $($w.Pid))" -ForegroundColor DarkGray
  }
}

function Set-FarmLayout {
  <#
    Place each emulator window according to Get-FarmLayout.
    -Procs is an array of launcher PIDs, index 0 -> first rect.
  #>
  param(
    [Parameter(Mandatory)][int[]]$Procs,
    [switch]$MaximizeSingle,
    [switch]$EdgeToEdge,
    [int]$WindowTimeoutSec = 90
  )

  if (-not $Procs -or $Procs.Count -eq 0) {
    Write-Warn 'layout: no processes to place.'
    return
  }

  $rects = Get-FarmLayout -Count $Procs.Count -EdgeToEdge:$EdgeToEdge
  $wa    = Get-ScreenWorkArea
  $W32   = $Script:W32
  Write-Host ("[layout] {0} instance(s) on {1}x{2} work area" -f $Procs.Count, $wa.W, $wa.H) -ForegroundColor Cyan

  # The log/console window would otherwise sit on top of the farm.
  Hide-EmulatorConsole -LauncherPids $Procs

  for ($i = 0; $i -lt $Procs.Count; $i++) {
    $r = $rects[$i]
    $win = Get-RenderWindow -LauncherPid $Procs[$i] -TimeoutSec $WindowTimeoutSec
    if (-not $win) {
      Write-Warn ("[layout] pid {0}: no device window within {1}s - skipping" -f $Procs[$i], $WindowTimeoutSec)
      continue
    }
    # The emulator is started -WindowStyle Minimized, so its window sits at the
    # magic minimized position (-32000,-32000). MoveWindow on a minimized window
    # succeeds but has no visible effect, so restore it FIRST or the farm
    # silently stacks up invisible behind everything else.
    [void][FarmWin32v2.Win32]::ShowWindow($win.Handle, $Script:FarmShow.SW_RESTORE)
    Start-Sleep -Milliseconds 250

    # Single instance: just maximise, which respects DPI/taskbar better than
    # a hand-computed rect.
    if ($Procs.Count -eq 1 -and $MaximizeSingle) {
      [void][FarmWin32v2.Win32]::ShowWindow($win.Handle, $Script:FarmShow.SW_MAXIMIZE)
      Write-Host ("[layout] pid {0} -> maximized" -f $Procs[$i]) -ForegroundColor Green
      continue
    }
    $ok = [FarmWin32v2.Win32]::MoveWindow($win.Handle, $r.X, $r.Y, $r.W, $r.H, $true)
    if ($ok) {
      Write-Host ("[layout] pid {0} -> {1},{2} {3}x{4}" -f $Procs[$i], $r.X, $r.Y, $r.W, $r.H) -ForegroundColor Green
    } else {
      Write-Warn ("[layout] pid {0}: MoveWindow failed" -f $Procs[$i])
    }
  }
}

function Show-FarmLayoutPreview {
  <#
    Human-readable table of what Get-FarmLayout would do. Useful for sanity
    checking geometry without launching anything.
  #>
  param([Parameter(Mandatory)][int]$Count)
  $rects = Get-FarmLayout -Count $Count
  $wa = Get-ScreenWorkArea
  Write-Host ("Screen work area: {0}x{1} at ({2},{3})" -f $wa.W, $wa.H, $wa.X, $wa.Y) -ForegroundColor DarkGray
  $i = 0
  foreach ($r in $rects) {
    Write-Host ("  instance {0}: X={1,-5} Y={2,-5} W={3,-5} H={4,-5}" -f $i, $r.X, $r.Y, $r.W, $r.H)
    $i++
  }
}
