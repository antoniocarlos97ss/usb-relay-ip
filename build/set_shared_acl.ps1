param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath
)

$ErrorActionPreference = 'Stop'
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
if ($currentSid.Value -eq 'S-1-5-18') {
    throw 'Run the client installer from the intended interactive user account; SYSTEM cannot identify the GUI owner safely.'
}

$systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$administratorsSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$allow = [System.Security.AccessControl.AccessControlType]::Allow
$none = [System.Security.AccessControl.PropagationFlags]::None

function New-UsbRelayAcl {
    param([bool]$IsDirectory)

    if ($IsDirectory) {
        $acl = [System.Security.AccessControl.DirectorySecurity]::new()
        $inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
                       [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    }
    else {
        $acl = [System.Security.AccessControl.FileSecurity]::new()
        $inheritance = [System.Security.AccessControl.InheritanceFlags]::None
    }

    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($currentSid)
    $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
        $systemSid, [System.Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance, $none, $allow
    ))
    $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
        $administratorsSid, [System.Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance, $none, $allow
    ))
    $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
        $currentSid, [System.Security.AccessControl.FileSystemRights]::Modify,
        $inheritance, $none, $allow
    ))
    return $acl
}

[System.IO.Directory]::CreateDirectory($TargetPath) | Out-Null
Set-Acl -LiteralPath $TargetPath -AclObject (New-UsbRelayAcl -IsDirectory $true)

# Replace explicit legacy DACLs as well; changing only the parent does not
# remove broad ACEs already stored on existing children during an upgrade.
Get-ChildItem -LiteralPath $TargetPath -Force -Recurse | ForEach-Object {
    Set-Acl -LiteralPath $_.FullName -AclObject (New-UsbRelayAcl -IsDirectory $_.PSIsContainer)
}
