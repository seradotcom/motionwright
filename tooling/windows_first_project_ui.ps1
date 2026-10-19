param(
    [Parameter(Mandatory = $true)][long]$WindowHandle,
    [Parameter(Mandatory = $true)][string]$ProjectTitle
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') {
    throw 'Visible project creation is restricted to disposable CI'
}
if ($ProjectTitle -cne 'motionwright ci first use') {
    throw 'Unexpected original first-use title'
}
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
if ($null -eq $window) { throw 'Original installed window is unavailable' }
$inputCondition = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::NameProperty, 'Project title'
)
$buttonCondition = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::NameProperty, 'Create project'
)
$scope = [System.Windows.Automation.TreeScope]::Descendants
$inputControl = $null
$submitControl = $null
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
$inputControl.SetFocus()
[System.Windows.Forms.SendKeys]::SendWait($ProjectTitle)
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
$invoke = $submitControl.GetCurrentPattern(
    [System.Windows.Automation.InvokePattern]::Pattern
)
if ($null -eq $invoke) { throw 'Original create button lacks an accessible Invoke action' }
$invoke.Invoke()
Write-Output 'VISIBLE_PROJECT_CREATE_ACTIVATED'
