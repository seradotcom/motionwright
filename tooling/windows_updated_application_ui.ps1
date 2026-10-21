param(
    [Parameter(Mandatory = $true)][long]$WindowHandle,
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [Parameter(Mandatory = $true)][UInt64]$CreationFiletime,
    [Parameter(Mandatory = $true)][string]$OperationId,
    [Parameter(Mandatory = $true)][ValidateSet('edit', 'observe')][string]$Mode,
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][long]$ExpectedRevision,
    [Parameter(Mandatory = $true)][string]$OldTitle,
    [Parameter(Mandatory = $true)][string]$NewTitle
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$Result = [ordered]@{
    schema = 'motionwright.windows-updated-application-ui/1'
    operation_id = $OperationId
    mode = $Mode
    state = 'NOT_SENT'
    phase = 'PRECHECK'
    project_id = $ProjectId
    revision = $ExpectedRevision
    title = $OldTitle
    ack_marker = $null
    save_attempts = 0
    selection_confirmed = $false
    future_ack_absent = $false
    process = [ordered]@{
        pid = $ProcessId
        creation_filetime = $CreationFiletime
        window_handle = $WindowHandle
    }
}
function Write-Original {
    [Console]::Out.WriteLine(($Result | ConvertTo-Json -Depth 6 -Compress))
    [Console]::Out.Flush()
}
trap {
    if ($Result.save_attempts -gt 0 -and $Result.state -ne 'ACK') {
        $Result.state = 'UNKNOWN'
    }
    [Console]::Error.WriteLine("MOTIONWRIGHT_UPDATED_UI_STAGE:$($Result.phase)")
    Write-Original
    exit 75
}
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows') {
    throw 'Owned visible observation requires disposable Windows CI'
}
if ([guid]::Parse($ProjectId).ToString() -cne $ProjectId -or
    [guid]::Parse($OperationId).ToString() -cne $OperationId -or
    $ExpectedRevision -lt 0 -or $ExpectedRevision -ge 9007199254740991 -or
    $NewTitle -cnotmatch '^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,95}$' -or
    $NewTitle -cne $NewTitle.Trim() -or $NewTitle -ceq $OldTitle) {
    throw 'Closed visible-action input contract failed'
}
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic
$Scope = [System.Windows.Automation.TreeScope]::Descendants
$TextType = [System.Windows.Automation.ControlType]::Text
$ButtonType = [System.Windows.Automation.ControlType]::Button
$EditType = [System.Windows.Automation.ControlType]::Edit
$ListType = [System.Windows.Automation.ControlType]::List
$ItemType = [System.Windows.Automation.ControlType]::ListItem
$Window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)

function Assert-OriginalWindow {
    $live = [System.Diagnostics.Process]::GetProcessById($ProcessId)
    try {
        if ($live.HasExited -or [UInt64]$live.StartTime.ToUniversalTime().ToFileTimeUtc() -ne $CreationFiletime) {
            throw 'Original application lifetime changed'
        }
    } finally {
        $live.Dispose()
    }
    if ($null -eq $Window -or $Window.Current.ProcessId -ne $ProcessId -or
        $Window.Current.NativeWindowHandle -ne $WindowHandle -or
        $Window.Current.Name -cne 'Motionwright' -or $Window.Current.IsOffscreen) {
        throw 'Original owned Motionwright HWND changed'
    }
}
function Elements($Root, $Type) {
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $Type)
    $found = $Root.FindAll($Scope, $condition)
    for ($index = 0; $index -lt $found.Count; $index++) {
        $element = $found.Item($index)
        if (-not $element.Current.IsOffscreen) { $element }
    }
}
function Named($Root, $Type, [string]$Name) {
    foreach ($element in @(Elements $Root $Type)) {
        if ($element.Current.Name -ceq $Name) { $element }
    }
}
function One($Root, $Type, [string]$Name) {
    $found = @(Named $Root $Type $Name)
    if ($found.Count -ne 1) { throw 'Accessible identity absent or ambiguous' }
    return $found[0]
}
function Invoke-Once($Element) {
    Assert-OriginalWindow
    if (-not $Element.Current.IsEnabled) { throw 'Original control is disabled' }
    $action = $Element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    if ($null -eq $action) { throw 'Original control cannot be invoked' }
    $action.Invoke()
}
function Wait-Library([bool]$Visible) {
    for ($poll = 0; $poll -lt 100; $poll++) {
        Assert-OriginalWindow
        $found = @(Named $Window $ButtonType 'Close project library')
        if ($found.Count -gt 1) { throw 'Ambiguous project library' }
        if (($found.Count -eq 1) -eq $Visible) { return }
        Start-Sleep -Milliseconds 200
    }
    throw 'Original library operation did not finish'
}
function Open-Library {
    if (@(Named $Window $ButtonType 'Close project library').Count -eq 0) {
        Invoke-Once (One $Window $ButtonType 'Open project library')
    }
    Wait-Library $true
}
function Find-SavedRow {
    $previous = ''
    for ($page = 0; $page -lt 32; $page++) {
        $list = $null
        for ($poll = 0; $poll -lt 100; $poll++) {
            Assert-OriginalWindow
            $lists = @(Named $Window $ListType 'Saved projects')
            if ($lists.Count -gt 1) { throw 'Ambiguous saved-project list' }
            if ($lists.Count -eq 1) { $list = $lists[0]; break }
            Start-Sleep -Milliseconds 200
        }
        if ($null -eq $list) { throw 'Saved-project list did not load' }
        $matches = @()
        $identities = @()
        foreach ($row in @(Elements $list $ItemType)) {
            foreach ($text in @(Elements $row $TextType)) {
                if ($text.Current.Name -cmatch '^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$') {
                    $identities += $text.Current.Name
                }
            }
            if (@(Named $row $TextType $ProjectId).Count -eq 1) { $matches += $row }
        }
        if ($matches.Count -gt 1) { throw 'Saved project UUID is ambiguous' }
        if ($matches.Count -eq 1) { return $matches[0] }
        $observed = ($identities -join '|')
        if ($observed -ceq $previous) { throw 'Saved-project pagination did not advance' }
        $previous = $observed
        $more = @(Named $Window $ButtonType 'Load more projects')
        if ($more.Count -ne 1) { throw 'Exact saved project is absent' }
        Invoke-Once $more[0]
        $advanced = $false
        for ($poll = 0; $poll -lt 100; $poll++) {
            Assert-OriginalWindow
            $lists = @(Named $Window $ListType 'Saved projects')
            if ($lists.Count -gt 1) { throw 'Ambiguous saved-project list' }
            if ($lists.Count -eq 1) {
                $nextIdentities = @()
                foreach ($item in @(Elements $lists[0] $ItemType)) {
                    foreach ($text in @(Elements $item $TextType)) {
                        if ($text.Current.Name -cmatch '^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$') {
                            $nextIdentities += $text.Current.Name
                        }
                    }
                }
                if (($nextIdentities -join '|') -cne $observed) { $advanced = $true; break }
            }
            Start-Sleep -Milliseconds 200
        }
        if (-not $advanced) { throw 'Saved-project pagination did not produce a new page' }
    }
    throw 'Saved-project observation exceeds its bound'
}
function Assert-Row($Row, [string]$Title, [long]$Revision, [bool]$Current) {
    if (@(Named $Row $TextType $ProjectId).Count -ne 1 -or
        @(Named $Row $TextType $Title).Count -ne 1) {
        throw 'Saved project identity or title differs'
    }
    $revisionFound = $false
    foreach ($text in @(Elements $Row $TextType)) {
        if ($text.Current.Name -cmatch ('^Revision ' + [regex]::Escape([string]$Revision) + '(?:\s|$)')) {
            $revisionFound = $true
        }
    }
    if (-not $revisionFound -or
        ($Current -and @(Named $Row $TextType 'Current').Count -ne 1)) {
        throw 'Original accepted project or revision is not current'
    }
}
function Reject-BriefBlockers {
    foreach ($name in @('Unconfirmed brief submission', 'Brief change not sent',
                         'Brief draft backup warning', 'Brief conflicts')) {
        $condition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::NameProperty, $name)
        foreach ($element in $Window.FindAll($Scope, $condition)) {
            if (-not $element.Current.IsOffscreen) { throw 'Brief has an unresolved operation or conflict' }
        }
    }
}
function Title-Value {
    $inputControl = One $Window $EditType 'Project title'
    if ($inputControl.Current.AutomationId -and $inputControl.Current.AutomationId -cne 'project-title-rich') {
        throw 'Wrong title editor'
    }
    $value = $inputControl.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($null -eq $value -or $value.Current.IsReadOnly) { throw 'Title value is not observable' }
    return $value.Current.Value
}
Assert-OriginalWindow
$Result.phase = 'SELECT_SAVED_UUID'
$wantedTitle = $OldTitle
$wantedRevision = $ExpectedRevision
if ($Mode -eq 'observe') {
    $wantedTitle = $NewTitle
    $wantedRevision = $ExpectedRevision + 1
}
Open-Library
$row = Find-SavedRow
Assert-Row $row $wantedTitle $wantedRevision $false
$verb = 'Open '
if (@(Named $row $TextType 'Current').Count -eq 1) { $verb = 'Reopen ' }
Invoke-Once (One $row $ButtonType ($verb + $wantedTitle))
Wait-Library $false

# Reopen the library for readback. Its Current marker is derived from the
# Project actually accepted by App; a successful click alone is insufficient.
$Result.phase = 'VERIFY_ACCEPTED_UUID'
Open-Library
$row = Find-SavedRow
Assert-Row $row $wantedTitle $wantedRevision $true
$Result.selection_confirmed = $true
Invoke-Once (One $Window $ButtonType 'Close project library')
Wait-Library $false
$Result.phase = 'BRIEF_READBACK'
Invoke-Once (One $Window $ButtonType 'Brief')
for ($poll = 0; $poll -lt 50; $poll++) {
    if (@(Named $Window $EditType 'Project title').Count -eq 1) { break }
    Start-Sleep -Milliseconds 200
}
Reject-BriefBlockers
if ((Title-Value) -cne $wantedTitle -or
    @(Named $Window $TextType ('r' + $wantedRevision)).Count -lt 1) {
    throw 'Accepted project title or revision is not visible in Brief'
}
if ($Mode -eq 'observe') {
    $Result.state = 'OBSERVED'
    $Result.phase = 'READ_ONLY_REOPEN_OBSERVED'
    $Result.revision = $wantedRevision
    $Result.title = $wantedTitle
    Write-Original
    exit 0
}
$ack = 'The original save reply and saved revision r' + ($ExpectedRevision + 1) + ' agree.'
if (@(Named $Window $TextType $ack).Count -ne 0) { throw 'Future ACK was already visible' }
$Result.future_ack_absent = $true
if (@(Named $Window $TextType '0 fields with local input.').Count -ne 1) {
    throw 'Existing uncommitted Brief inputs must remain untouched'
}
$Result.phase = 'PREPARE_ONE_TITLE'
$inputControl = One $Window $EditType 'Project title'
if (-not $inputControl.Current.IsKeyboardFocusable -or -not $inputControl.Current.IsEnabled) {
    throw 'Original title editor is not focusable'
}
$null = [Microsoft.VisualBasic.Interaction]::AppActivate($ProcessId)
$inputControl.SetFocus()
for ($poll = 0; $poll -lt 8; $poll++) {
    if ($inputControl.Current.HasKeyboardFocus) { break }
    Start-Sleep -Milliseconds 125
}
if (-not $inputControl.Current.HasKeyboardFocus) { throw 'Original title editor lacks keyboard focus' }
[System.Windows.Forms.SendKeys]::SendWait('^a' + $NewTitle)
for ($poll = 0; $poll -lt 40; $poll++) {
    if ((Title-Value) -ceq $NewTitle) { break }
    Start-Sleep -Milliseconds 125
}
if ((Title-Value) -cne $NewTitle) { throw 'Single keyboard delivery was not confirmed' }
Reject-BriefBlockers
$save = $null
for ($poll = 0; $poll -lt 40; $poll++) {
    $save = One $Window $ButtonType 'Save title'
    if ($save.Current.IsEnabled) { break }
    Start-Sleep -Milliseconds 125
}
if (-not $save.Current.IsEnabled) { throw 'Original Save title is disabled' }
$invoke = $save.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
if ($null -eq $invoke) { throw 'Original Save title has no Invoke pattern' }
Assert-OriginalWindow
# The caller retained ui-intent before launching this controller. This is the
# only Save invocation; every later loop observes and cannot dispatch again.
$Result.phase = 'SAVE_ORIGINAL_REPLY_UNKNOWN'
$Result.save_attempts = 1
$Result.state = 'UNKNOWN'
$invoke.Invoke()
for ($poll = 0; $poll -lt 150; $poll++) {
    Assert-OriginalWindow
    $acknowledgments = @(Named $Window $TextType $ack)
    if ($acknowledgments.Count -gt 1) { throw 'Original ACK is ambiguous' }
    if ($acknowledgments.Count -eq 1) {
        Reject-BriefBlockers
        if ((Title-Value) -cne $NewTitle -or
            @(Named $Window $TextType ('r' + ($ExpectedRevision + 1))).Count -lt 1) {
            throw 'Original ACK and current visible project disagree'
        }
        $Result.state = 'ACK'
        $Result.phase = 'ORIGINAL_REPLY_AND_REVISION_OBSERVED'
        $Result.revision = $ExpectedRevision + 1
        $Result.title = $NewTitle
        $Result.ack_marker = $ack
        Write-Original
        exit 0
    }
    Start-Sleep -Milliseconds 200
}
throw 'Original save reply was not confirmed; no replay is permitted'
