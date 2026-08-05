param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-True {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Get-ExplicitRulesForSid {
    param(
        [Parameter(Mandatory = $true)]$Acl,
        [Parameter(Mandatory = $true)][string]$Sid
    )

    $matching = @()
    foreach ($rule in $Acl.Access) {
        try {
            $ruleSid = $rule.IdentityReference.Translate(
                [System.Security.Principal.SecurityIdentifier]
            ).Value
        }
        catch {
            continue
        }
        if ($ruleSid -eq $Sid -and
            -not $rule.IsInherited -and
            $rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow) {
            $matching += $rule
        }
    }
    return $matching
}

function Assert-ExplicitRights {
    param(
        [Parameter(Mandatory = $true)]$Acl,
        [Parameter(Mandatory = $true)][string]$Sid,
        [Parameter(Mandatory = $true)][System.Security.AccessControl.FileSystemRights]$Rights,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $rules = @(Get-ExplicitRulesForSid -Acl $Acl -Sid $Sid)
    $hasRights = $false
    foreach ($rule in $rules) {
        if (($rule.FileSystemRights -band $Rights) -eq $Rights) {
            $hasRights = $true
        }
    }
    Assert-True $hasRights "Missing explicit $Rights access for $Label ($Sid)"
}

Assert-True ($env:OS -eq 'Windows_NT') 'This integration probe must run on Windows.'

$runId = $env:GITHUB_RUN_ID
if ([string]::IsNullOrWhiteSpace($runId)) { $runId = "local-$PID" }
$attempt = $env:GITHUB_RUN_ATTEMPT
if ([string]::IsNullOrWhiteSpace($attempt)) { $attempt = '1' }
$taskName = "USBRelayClientCI-$runId-$attempt"
$root = Join-Path $env:ProgramData ("USBRelay\" + $taskName)
$programDataRoot = $env:ProgramData
$sharedState = $root
$legacyChild = Join-Path $sharedState 'legacy\nested.json'
$markerPath = Join-Path $sharedState 'system-probe.json'
$xmlPath = Join-Path $sharedState 'system-task.xml'

try {
    New-Item -ItemType Directory -Path (Split-Path $legacyChild) -Force | Out-Null
    Set-Content -LiteralPath $legacyChild -Value '{"legacy":true}' -Encoding UTF8

    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File (Join-Path $PSScriptRoot '..\build\set_shared_acl.ps1') `
        -TargetPath $sharedState
    Assert-True ($LASTEXITCODE -eq 0) "ACL script failed with exit code $LASTEXITCODE"

    $systemSid = 'S-1-5-18'
    $administratorsSid = 'S-1-5-32-544'
    $usersSid = 'S-1-5-32-545'
    $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value

    $directoryAcl = Get-Acl -LiteralPath $sharedState
    Assert-ExplicitRights $directoryAcl $systemSid ([System.Security.AccessControl.FileSystemRights]::FullControl) 'SYSTEM'
    Assert-ExplicitRights $directoryAcl $administratorsSid ([System.Security.AccessControl.FileSystemRights]::FullControl) 'Administrators'
    Assert-ExplicitRights $directoryAcl $currentSid ([System.Security.AccessControl.FileSystemRights]::Modify) 'runner user'
    Assert-True (@(Get-ExplicitRulesForSid $directoryAcl $usersSid).Count -eq 0) 'Users must not receive an explicit allow ACE.'

    $fileAcl = Get-Acl -LiteralPath $legacyChild
    Assert-ExplicitRights $fileAcl $systemSid ([System.Security.AccessControl.FileSystemRights]::FullControl) 'SYSTEM on legacy child'
    Assert-ExplicitRights $fileAcl $currentSid ([System.Security.AccessControl.FileSystemRights]::Modify) 'runner user on legacy child'

    & python (Join-Path $PSScriptRoot 'render_system_task.py') $xmlPath $markerPath $programDataRoot
    Assert-True ($LASTEXITCODE -eq 0) 'Failed to render the production Task Scheduler XML.'

    & schtasks.exe /Create /TN $taskName /XML $xmlPath /F
    Assert-True ($LASTEXITCODE -eq 0) 'Failed to create the temporary SYSTEM task.'

    $registeredXml = (& schtasks.exe /Query /TN $taskName /XML | Out-String)
    Assert-True ($LASTEXITCODE -eq 0) 'Failed to query the temporary SYSTEM task.'
    Assert-True ($registeredXml -match '<UserId>S-1-5-18</UserId>') 'Registered task does not use the SYSTEM SID.'

    & schtasks.exe /Run /TN $taskName
    Assert-True ($LASTEXITCODE -eq 0) 'Failed to start the temporary SYSTEM task.'

    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while (-not (Test-Path -LiteralPath $markerPath) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    Assert-True (Test-Path -LiteralPath $markerPath) 'SYSTEM task did not write its marker within 45 seconds.'

    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    Assert-True ($marker.sid -eq $systemSid) "Probe ran as unexpected SID $($marker.sid)."
    Assert-True ([bool]$marker.lock_acquired) 'SYSTEM process could not acquire the real msvcrt-backed named lock.'
    Assert-True ([bool]$marker.headless_argument) 'Production task renderer did not append --headless.'

    & python -c "import os, msvcrt; assert os.name == 'nt'; from client.core.windows_pnp import list_usb_devices; result = list_usb_devices(include_properties=False); assert result is not None; print('PnP records:', len(result))"
    Assert-True ($LASTEXITCODE -eq 0) 'The production PowerShell/CIM PnP query failed on Windows.'

    Write-Host 'Windows ACL, SYSTEM Task Scheduler, msvcrt lock, and PnP integration checks passed.'
}
finally {
    & schtasks.exe /End /TN $taskName 2>$null | Out-Null
    & schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null
    if (Test-Path -LiteralPath $root) {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    }
}
