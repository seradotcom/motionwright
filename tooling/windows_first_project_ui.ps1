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
$inputCondition = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::NameProperty, 'Project title'
)
$buttonCondition = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::NameProperty, 'Create project'
)
$scope = [System.Windows.Automation.TreeScope]::Descendants
$inputControl = $null
$submitControl = $null
$Phase = 'FORM_FIND'
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $inputControl = $window.FindFirst($scope, $inputCondition)
    $submitControl = $window.FindFirst($scope, $buttonCondition)
    if ($null -ne $inputControl -and $null -ne $submitControl -and $inputControl.Current.IsEnabled) {
        break
    }
    Start-Sleep -Milliseconds 250
}
if ($null -eq $inputControl -or $null -eq $submitControl) {
    throw 'Original accessible Project library form not observed'
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
