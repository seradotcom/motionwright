param(
    [Parameter(Mandatory = $true)][long]$WindowHandle,
    [Parameter(Mandatory = $true)][string]$ProjectTitle
)
$ErrorActionPreference = 'Stop'
$Phase = 'PRECHECK'
# Emit a fixed, non-sensitive stage only. A failed Invoke is uncertain:
# neither the script nor the caller may replay it automatically.
trap {
    [Console]::Error.WriteLine("MOTIONWRIGHT_UI_STAGE:$Phase")
    exit 75
}
if ($env:GITHUB_ACTIONS -ne 'true') {
    throw 'Visible project creation is restricted to disposable CI'
}
if ($ProjectTitle -cne 'motionwright ci first use') {
    throw 'Unexpected original first-use title'
}
$Phase = 'UIA_LOAD'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$Phase = 'WINDOW_LOOKUP'
$window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
if ($null -eq $window) { throw 'Original installed window is unavailable' }
$Phase = 'ELEMENT_QUERY'
# Never select by Name alone: the HTML label and its input can share
# "Project title". UI Automation may return the non-focusable text label
# first; SetFocus on that element caused the original installed G2 failure.
$inputType = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Edit
)
$buttonType = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button
)
$scope = [System.Windows.Automation.TreeScope]::Descendants
$inputControl = $null
$submitControl = $null
$inputMatches = 0
$buttonMatches = 0
$Phase = 'FORM_FIND'
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $edits = $window.FindAll($scope, $inputType)
    $buttons = $window.FindAll($scope, $buttonType)
    $inputControl = $null
    $submitControl = $null
    $inputMatches = 0
    $buttonMatches = 0
    for ($index = 0; $index -lt $edits.Count; $index++) {
        $candidate = $edits.Item($index)
        if ($candidate.Current.Name -ceq 'Project title') {
            $inputMatches++
            $inputControl = $candidate
        }
    }
    for ($index = 0; $index -lt $buttons.Count; $index++) {
        $candidate = $buttons.Item($index)
        if ($candidate.Current.Name -ceq 'Create project') {
            $buttonMatches++
            $submitControl = $candidate
        }
    }
    if ($inputMatches -gt 1 -or $buttonMatches -gt 1) {
        $Phase = 'FORM_AMBIGUOUS'
        throw 'More than one matching original accessible form control'
    }
    if ($inputMatches -eq 1 -and $buttonMatches -eq 1) {
        break
    }
    Start-Sleep -Milliseconds 250
}
if ($inputMatches -ne 1 -or $buttonMatches -ne 1) {
    throw 'Original accessible edit and create button not observed'
}
$Phase = 'INPUT_NOT_FOCUSABLE'
if (-not $inputControl.Current.IsEnabled -or
    $inputControl.Current.IsOffscreen -or
    -not $inputControl.Current.IsKeyboardFocusable) {
    throw 'Original accessible edit is not focusable'
}
$Phase = 'INPUT_FOCUS'
$inputControl.SetFocus()
$Phase = 'TYPE_TITLE'
[System.Windows.Forms.SendKeys]::SendWait($ProjectTitle)
$Phase = 'BUTTON_ENABLE'
for ($attempt = 0; $attempt -lt 16; $attempt++) {
    $submitControl = $window.FindFirst($scope, $buttonCondition)
    if ($null -ne $submitControl -and $submitControl.Current.IsEnabled) {
        break
    }
    Start-Sleep -Milliseconds 250
}
if ($null -eq $submitControl -or -not $submitControl.Current.IsEnabled) {
    throw 'The real Create project action never became enabled'
}
# One action, no retry after this point. SQLite identity/readback is tested
# independently after the installed window returns.
$Phase = 'INVOKE_LOOKUP'
$invoke = $submitControl.GetCurrentPattern(
    [System.Windows.Automation.InvokePattern]::Pattern
)
if ($null -eq $invoke) { throw 'Original create button lacks an accessible Invoke action' }
$Phase = 'INVOKE_UNKNOWN'
$invoke.Invoke()
$Phase = 'FINISHED'
Write-Output 'VISIBLE_PROJECT_CREATE_ACTIVATED'
