param(
    [Parameter(Mandatory)][int]$EditorPid,
    [Parameter(Mandatory)][long]$EditorCreationFiletime,
    [Parameter(Mandatory)][string]$Executable,
    [Parameter(Mandatory)][string]$OriginalOtio,
    [Parameter(Mandatory)][string]$OriginalSha256,
    [Parameter(Mandatory)][string]$Evidence
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -cne 'true' -or $env:GITHUB_JOB -cne 'kdenlive-native-editor' -or
    $env:GITHUB_RUN_ATTEMPT -cne '1' -or $env:RUNNER_OS -cne 'Windows') { throw 'Original Windows editor job required' }

# The observer runs in Windows PowerShell 5.1 from a PowerShell 7 job.
# Use the system modules for this engine instead of inheriting its caller's path.
Import-Module -Name "$PSHOME\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1"
Import-Module -Name "$PSHOME\Modules\Microsoft.PowerShell.Management\Microsoft.PowerShell.Management.psd1"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class KdenliveMenuInput
{
    [StructLayout(LayoutKind.Sequential)]
    private struct Point { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct Mouse
    {
        public int X, Y;
        public uint Wheel, Flags, Time;
        public UIntPtr Extra;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Input { public uint Type; public Mouse Mouse; }

    [DllImport("user32.dll")]
    private static extern IntPtr WindowFromPhysicalPoint(Point point);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")]
    private static extern bool SetPhysicalCursorPos(int x, int y);
    [DllImport("user32.dll")]
    private static extern bool GetPhysicalCursorPos(out Point point);
    [DllImport("user32.dll")]
    private static extern uint SendInput(uint count, Input[] events, int size);


    private delegate bool WindowCallback(IntPtr window, IntPtr data);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(WindowCallback callback, IntPtr data);
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr window);

    public static IntPtr[] OwnedWindows(uint owner)
    {
        var owned = new System.Collections.Generic.List<IntPtr>();
        EnumWindows((window, data) => {
            uint found;
            if (IsWindowVisible(window) && GetWindowThreadProcessId(window, out found) != 0 && found == owner)
                owned.Add(window);
            return owned.Count <= 16;
        }, IntPtr.Zero);
        return owned.ToArray();
    }

    public static bool OwnedPoint(int x, int y, uint owner)
    {
        uint found;
        IntPtr window = WindowFromPhysicalPoint(new Point { X = x, Y = y });
        return window != IntPtr.Zero && GetWindowThreadProcessId(window, out found) != 0
            && found == owner;
    }

    public static bool ClickOwnedPoint(int x, int y, uint owner)
    {
        Point observed;
        if (!OwnedPoint(x, y, owner) || !SetPhysicalCursorPos(x, y)
            || !GetPhysicalCursorPos(out observed) || observed.X != x || observed.Y != y
            || !OwnedPoint(x, y, owner))
            return false;

        var events = new [] {
            new Input { Type = 0, Mouse = new Mouse { Flags = 0x0002 } },
            new Input { Type = 0, Mouse = new Mouse { Flags = 0x0004 } }
        };
        return SendInput((uint)events.Length, events, Marshal.SizeOf(typeof(Input))) == events.Length;
    }
}
'@
$editor = [Diagnostics.Process]::GetProcessById($EditorPid)
$deadline = [Diagnostics.Stopwatch]::StartNew()
$scope = [System.Windows.Automation.TreeScope]::Descendants
$savePath = Join-Path $Evidence 'initial.kdenlive'

function Assert-Editor {
    $editor.Refresh()
    if ($editor.HasExited -or $editor.StartTime.ToUniversalTime().ToFileTimeUtc() -ne $EditorCreationFiletime -or
        ![string]::Equals($editor.MainModule.FileName, $Executable, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Kdenlive process or image changed'
    }
}

function Record([string]$Name, [object]$Value) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 5 -Compress) + [Environment]::NewLine)
    $stream = [IO.File]::Open((Join-Path $Evidence $Name), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally {
        $stream.Dispose()
    }
}

function Owned-Windows {
    Assert-Editor
    $windows = [KdenliveMenuInput]::OwnedWindows([uint32]$EditorPid)
    if ($windows.Count -gt 16) { throw 'Kdenlive top-level window limit exceeded' }
    foreach ($handle in $windows) {
        try {
            $window = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
            if ($window.Current.ProcessId -eq $EditorPid -and !$window.Current.IsOffscreen) { $window }
        } catch [System.Windows.Automation.ElementNotAvailableException] {
            # A transient Qt menu can close while the observer enumerates it.
            continue
        }
    }
}

function Observe-Windows {
    $rows = @(Owned-Windows | ForEach-Object {
        @{
            hwnd = $_.Current.NativeWindowHandle
            name = (Control-Name $_)
            role = $_.Current.ControlType.ProgrammaticName
            class_name = $_.Current.ClassName
        }
    })
    return $rows
}

function Control-Name($Item) {
    return (($Item.Current.Name -replace '&', '') -replace '\u2026$', '...').Trim()
}

function Find-Control($Windows, $Role, [string]$Name, [string]$AutomationId = '') {
    $seen = [Collections.Generic.HashSet[string]]::new()
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $Role
    )
    foreach ($window in $Windows) {
        $nodes = $window.FindAll($scope, $condition)
        if ($nodes.Count -gt 300) { throw 'Kdenlive control list limit exceeded' }
        foreach ($node in $nodes) {
            $matches = (Control-Name $node) -ceq $Name
            if ($AutomationId -eq 'fileNameEdit') {
                $fieldId = $node.Current.AutomationId
                $matches = $matches -or $fieldId -ceq '1148' -or $fieldId -cmatch '(^|\.)fileNameEdit$'
            }
            if ($matches -and $node.Current.ProcessId -eq $EditorPid -and
                !$node.Current.IsOffscreen -and $node.Current.IsEnabled -and
                $seen.Add(($node.GetRuntimeId() -join '.'))) { $node }
        }
    }
}

function Await-Unique([string]$Name, [ScriptBlock]$Select, [int]$Seconds = 20) {
    $limit = [Math]::Min(95, $deadline.Elapsed.TotalSeconds + $Seconds)
    while ($deadline.Elapsed.TotalSeconds -lt $limit) {
        Assert-Editor
        $rows = @(& $Select)
        if ($rows.Count -gt 1) { throw "Ambiguous Kdenlive selection for $Name" }
        if ($rows.Count -eq 1) { return $rows[0] }
        Start-Sleep -Milliseconds 120
    }
    throw "Kdenlive selection did not appear for $Name"
}

function Invoke-Control($Node, [string]$Stage, [switch]$Expand, [switch]$ConfirmDialogTransition) {
    Assert-Editor
    if ($Node.Current.ProcessId -ne $EditorPid -or !$Node.Current.IsEnabled -or $Node.Current.IsOffscreen) {
        throw 'Kdenlive control is not owned and usable'
    }
    $targetName = Control-Name $Node
    $pattern = $null
    if ($Expand -and $Node.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
        Record "$Stage-intent.json" @{ editor_pid = $EditorPid; control = $targetName; operation = 'expand'; automatic_retry = $false }
        $pattern.Expand()
    } elseif ($Node.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
        Record "$Stage-intent.json" @{ editor_pid = $EditorPid; control = $targetName; operation = 'invoke'; automatic_retry = $false }
        try {
            $pattern.Invoke()
        } catch {
            if (!$ConfirmDialogTransition -or
                ($_.Exception -isnot [System.Windows.Automation.ElementNotAvailableException] -and
                 $_.Exception.InnerException -isnot [System.Windows.Automation.ElementNotAvailableException])) { throw }
            Record "$Stage-unsettled.json" @{ editor_pid = $EditorPid; control = $targetName; automatic_retry = $false }
            return
        }
    } else {
        throw "Kdenlive supplied no supported action for $targetName"
    }
    Record "$Stage-acknowledged.json" @{ editor_pid = $EditorPid; control = $targetName }
}

function Select-MenuItem($Item, [string]$Name, [string]$Stage) {
    Assert-Editor
    $identity = $Item.GetRuntimeId() -join '.'
    if (!$identity -or $Item.Current.ProcessId -ne $EditorPid -or
        $Item.Current.ControlType -ne [System.Windows.Automation.ControlType]::MenuItem -or
        !$Item.Current.IsEnabled -or $Item.Current.IsOffscreen -or
        (Control-Name $Item) -cne $Name) { throw 'Kdenlive menu item changed before selection' }

    # Locate the visible Qt menu row without invoking its transient UIA provider.
    $point = $Item.GetClickablePoint()
    $x = [int][Math]::Floor($point.X)
    $y = [int][Math]::Floor($point.Y)
    if (![KdenliveMenuInput]::OwnedPoint($x, $y, [uint32]$EditorPid)) { throw 'Kdenlive menu point is obscured' }
    $hit = [System.Windows.Automation.AutomationElement]::FromPoint([Windows.Point]::new($x, $y))
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    for ($depth = 0; $null -ne $hit -and $depth -lt 3; $depth++) {
        if (($hit.GetRuntimeId() -join '.') -ceq $identity) { break }
        $hit = $walker.GetParent($hit)
    }
    if (!$hit -or ($hit.GetRuntimeId() -join '.') -cne $identity) { throw 'Kdenlive menu hit target differs' }
    Assert-Editor
    Record "$Stage-intent.json" @{ editor_pid = $EditorPid; control = $Name; operation = 'click'; automatic_retry = $false }
    if (![KdenliveMenuInput]::ClickOwnedPoint($x, $y, [uint32]$EditorPid)) { throw 'Kdenlive menu click unconfirmed' }
    Record "$Stage-acknowledged.json" @{ editor_pid = $EditorPid; control = $Name }
}

function File-Action([string]$Name, [string]$Stage) {
    $menu = Await-Unique 'File' {
        Find-Control @(Owned-Windows) ([System.Windows.Automation.ControlType]::MenuItem) 'File'
    }
    Invoke-Control $menu "$Stage-file-menu" -Expand
    $item = Await-Unique $Name {
        Find-Control @(Owned-Windows) ([System.Windows.Automation.ControlType]::MenuItem) $Name
    }
    Select-MenuItem $item $Name "$Stage-menu-item"
}

function Find-FileDialog([string[]]$Titles) {
    $matches = [Collections.Generic.HashSet[string]]::new()
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Window
    )
    foreach ($window in @(Owned-Windows)) {
        $candidates = @($window)
        if ($Titles -cnotcontains (Control-Name $window)) {
            $children = $window.FindAll($scope, $condition)
            if ($children.Count -gt 16) { throw 'Kdenlive nested window limit exceeded' }
            $candidates = @($children)
        }
        foreach ($candidate in $candidates) {
            if ($candidate.Current.ProcessId -eq $EditorPid -and !$candidate.Current.IsOffscreen -and
                $Titles -ccontains (Control-Name $candidate) -and $matches.Add(($candidate.GetRuntimeId() -join '.'))) {
                $candidate
            }
        }
    }
}

function Choose-File([string[]]$Titles, [string]$Path, [string]$Button, [string]$Stage) {
    $dialog = Await-Unique ($Titles -join ' / ') { Find-FileDialog $Titles }
    $entry = Await-Unique 'File name' {
        Find-Control @($dialog) ([System.Windows.Automation.ControlType]::Edit) 'File name:' 'fileNameEdit'
    }
    $value = $entry.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($value.Current.IsReadOnly) { throw 'Kdenlive filename control is read-only' }
    Assert-Editor
    Record "$Stage-path-intent.json" @{ editor_pid = $EditorPid; file = $Path; automatic_retry = $false }
    $value.SetValue($Path)
    if ($value.Current.Value -cne $Path) { throw 'Kdenlive filename readback differs' }
    $submit = Await-Unique $Button {
        Find-Control @($dialog) ([System.Windows.Automation.ControlType]::Button) $Button
    }
    if ($Stage -ceq 'import' -and
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $OriginalSha256) {
        throw 'Kdenlive import input changed before confirmation'
    }
    Invoke-Control $submit "$Stage-submit" -ConfirmDialogTransition
    while ($deadline.Elapsed.TotalSeconds -lt 95) {
        Assert-Editor
        if (!@(Find-FileDialog $Titles).Count) { return }
        Start-Sleep -Milliseconds 120
    }
    throw "Original Kdenlive file dialog did not close for $Stage"
}

if ($OriginalSha256 -cnotmatch '^[a-f0-9]{64}$' -or
    (Get-FileHash -LiteralPath $OriginalOtio -Algorithm SHA256).Hash.ToLowerInvariant() -cne $OriginalSha256 -or
    (Test-Path -LiteralPath $savePath)) { throw 'Kdenlive import input or save destination differs' }
try {
    $main = Await-Unique 'Kdenlive window' {
        Owned-Windows | Where-Object { $_.Current.NativeWindowHandle -gt 0 -and (Control-Name $_) -match 'Kdenlive$' }
    }
    Record 'initial-window.json' @{ editor_pid = $EditorPid; hwnd = $main.Current.NativeWindowHandle; name = (Control-Name $main) }
    File-Action 'OpenTimelineIO Import...' 'import'
    Choose-File @('OpenTimelineIO Import') $OriginalOtio 'Open' 'import'
    File-Action 'Save As...' 'initial-save'
    Choose-File @('Save As', 'Save Project') $savePath 'Save' 'initial-save'
    if (!(Test-Path -LiteralPath $savePath -PathType Leaf)) { throw 'Kdenlive did not create an initial saved project' }
    if ((Get-FileHash -LiteralPath $OriginalOtio -Algorithm SHA256).Hash.ToLowerInvariant() -cne $OriginalSha256) {
        throw 'Kdenlive import changed the source OTIO'
    }
    Record 'initial-save-observed.json' @{
        editor_pid = $EditorPid
        source_sha256 = $OriginalSha256
        saved_sha256 = (Get-FileHash -LiteralPath $savePath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
} catch {
    $originalError = $_
    try {
        Record 'editor-diagnostic.json' @{ message = $originalError.Exception.Message; windows = @(Observe-Windows) }
    } catch {
        Write-Verbose $_.Exception.Message
    }
    throw $originalError
}
