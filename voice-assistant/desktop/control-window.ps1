# General window control for VESPER: activate, minimize, maximize a specific
# app, or minimize/restore everything at once. Real Windows APIs, not AI
# guessing from a screenshot.
#
# For a specific app, matches are found in real Z-order (front-to-back
# window stacking) rather than process list order, so when multiple windows
# of the same app are open, the one closer to the front (i.e. more recently
# active) is preferred -- a much better proxy for "which one did they mean"
# than an arbitrary process list order.
#
# Outputs exactly one line:
#   DONE:<window title or description>
#   NOT_FOUND

param(
    [string]$TargetName,

    [Parameter(Mandatory=$true)]
    [ValidateSet("activate", "minimize", "maximize", "show_desktop", "restore_all")]
    [string]$Action
)

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public class VesperWin32 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    public static List<KeyValuePair<IntPtr, string>> GetWindowsInZOrder() {
        var result = new List<KeyValuePair<IntPtr, string>>();
        EnumWindows((hWnd, lParam) => {
            if (IsWindowVisible(hWnd)) {
                int length = GetWindowTextLength(hWnd);
                if (length > 0) {
                    var sb = new StringBuilder(length + 1);
                    GetWindowText(hWnd, sb, sb.Capacity);
                    result.Add(new KeyValuePair<IntPtr, string>(hWnd, sb.ToString()));
                }
            }
            return true;
        }, IntPtr.Zero);
        return result;
    }
}
"@

# Global actions that don't target a specific app.
if ($Action -eq "show_desktop") {
    (New-Object -ComObject Shell.Application).MinimizeAll()
    Write-Output "DONE:desktop"
    return
}
if ($Action -eq "restore_all") {
    (New-Object -ComObject Shell.Application).UndoMinimizeALL()
    Write-Output "DONE:restored"
    return
}

if (-not $TargetName) {
    Write-Output "NOT_FOUND"
    return
}

# EnumWindows returns windows front-to-back, so the first match is the
# most-recently-active one among any duplicates (e.g. multiple Chrome windows).
$windows = [VesperWin32]::GetWindowsInZOrder()
$match = $null
foreach ($w in $windows) {
    if ($w.Value -like "*$TargetName*") {
        $match = $w
        break
    }
}

if ($match) {
    switch ($Action) {
        "activate" {
            [VesperWin32]::ShowWindow($match.Key, 9) | Out-Null   # SW_RESTORE
            [VesperWin32]::SetForegroundWindow($match.Key) | Out-Null
        }
        "minimize" {
            [VesperWin32]::ShowWindow($match.Key, 6) | Out-Null   # SW_MINIMIZE
        }
        "maximize" {
            [VesperWin32]::ShowWindow($match.Key, 3) | Out-Null   # SW_MAXIMIZE
            [VesperWin32]::SetForegroundWindow($match.Key) | Out-Null
        }
    }
    Write-Output "DONE:$($match.Value)"
} else {
    Write-Output "NOT_FOUND"
}
