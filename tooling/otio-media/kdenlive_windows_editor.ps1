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
$pidCondition = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $EditorPid
)
$root = [System.Windows.Automation.AutomationElement]::RootElement
$savePath = Join-Path $Evidence 'initial.kdenlive'
$windowScope = [System.Windows.Automation.TreeScope]::Children

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
    $windows = $root.FindAll($windowScope, $pidCondition)
    if ($windows.Count -gt 16) { throw 'Kdenlive top-level window limit exceeded' }
    foreach ($window in $windows) {
        if ($window.Current.ProcessId -eq $EditorPid -and !$window.Current.IsOffscreen) { $window }
    }
}

function Control-Name($Item) {
    return (($Item.Current.Name -replace '&', '') -replace '\u2026$', '...').Trim()
}

function Find-Control($Windows, $Role, [string]$Name, [string]$AutomationId = '') {
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $Role
    )
    foreach ($window in $Windows) {
        $nodes = $window.FindAll($scope, $condition)
        if ($nodes.Count -gt 300) { throw 'Kdenlive control list limit exceeded' }
        foreach ($node in $nodes) {
            $matches = (Control-Name $node) -ceq $Name
            if ($AutomationId.Length) { $matches = $matches -or $node.Current.AutomationId -ceq $AutomationId }
            if ($matches -and $node.Current.ProcessId -eq $EditorPid -and
                !$node.Current.IsOffscreen -and $node.Current.IsEnabled) { $node }
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

function Invoke-Control($Node, [string]$Stage, [switch]$Expand) {
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
        $pattern.Invoke()
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
    for ($depth = 0; $hit -and $depth -lt 3; $depth++) {
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

function Choose-File([string[]]$Titles, [string]$Path, [string]$Button, [string]$Stage) {
    $dialog = Await-Unique ($Titles -join ' / ') { Owned-Windows | Where-Object { $Titles -ccontains (Control-Name $_) } }
    $entry = Await-Unique 'File name' {
        Find-Control @($dialog) ([System.Windows.Automation.ControlType]::Edit) 'File name:' '1148'
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
    Invoke-Control $submit "$Stage-submit"
    while ($deadline.Elapsed.TotalSeconds -lt 95) {
        Assert-Editor
        if (!@(Owned-Windows | Where-Object { $Titles -ccontains (Control-Name $_) }).Count) { return }
        Start-Sleep -Milliseconds 120
    }
    throw "Original Kdenlive file dialog did not close for $Stage"
}

if ($OriginalSha256 -cnotmatch '^[a-f0-9]{64}$' -or
    (Get-FileHash -LiteralPath $OriginalOtio -Algorithm SHA256).Hash.ToLowerInvariant() -cne $OriginalSha256 -or
    (Test-Path -LiteralPath $savePath)) { throw 'Kdenlive import input or save destination differs' }
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
