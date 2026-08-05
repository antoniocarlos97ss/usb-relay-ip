[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,
    [string]$LegacyRoot = '',
    [switch]$RemoveTarget
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Test-ReparsePoint {
    param([Parameter(Mandatory = $true)][System.IO.FileSystemInfo]$Item)

    return (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Remove-Win32ExtendedPathPrefix {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.StartsWith('\\?\UNC\', [System.StringComparison]::OrdinalIgnoreCase)) {
        return '\\' + $Path.Substring(8)
    }
    if ($Path.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) {
        return $Path.Substring(4)
    }
    return $Path
}

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $fullPath = Remove-Win32ExtendedPathPrefix -Path $fullPath
    return $fullPath.TrimEnd([char[]]@(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ))
}

function Get-SpecialFolderPath {
    param(
        [Parameter(Mandatory = $true)]
        [System.Environment+SpecialFolder]$Folder
    )

    $path = [System.Environment]::GetFolderPath($Folder)
    if ([string]::IsNullOrWhiteSpace($path)) {
        throw "Windows did not return a path for special folder $Folder"
    }
    return Get-NormalizedPath -Path $path
}

function Test-PathWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $candidatePath = Get-NormalizedPath -Path $Candidate
    $rootPath = Get-NormalizedPath -Path $Root
    return [System.StringComparer]::OrdinalIgnoreCase.Equals($candidatePath, $rootPath) -or
        $candidatePath.StartsWith(
            ($rootPath + '\\'),
            [System.StringComparison]::OrdinalIgnoreCase
        )
}

function Assert-NoReparseAncestors {
    param([Parameter(Mandatory = $true)][System.IO.DirectoryInfo]$Directory)

    $cursor = $Directory
    while ($null -ne $cursor) {
        if (-not $cursor.Exists) {
            throw "Expected existing directory does not exist: $($cursor.FullName)"
        }
        if (Test-ReparsePoint -Item $cursor) {
            throw "Refusing reparse-point ancestor: $($cursor.FullName)"
        }
        $parent = $cursor.Parent
        if ($null -eq $parent -or $parent.FullName -eq $cursor.FullName) {
            break
        }
        $cursor = $parent
    }
}

function Get-SafeFileSystemNodes {
    param([Parameter(Mandatory = $true)][System.IO.DirectoryInfo]$Root)

    $pending = [System.Collections.Generic.Stack[System.IO.DirectoryInfo]]::new()
    $nodes = [System.Collections.Generic.List[System.IO.FileSystemInfo]]::new()
    $pending.Push($Root)

    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        if (Test-ReparsePoint -Item $directory) {
            throw "Refusing reparse-point directory: $($directory.FullName)"
        }
        $nodes.Add($directory)

        # Walk one level at a time.  A reparse point is rejected before a
        # directory is ever pushed, so this code never follows a junction.
        foreach ($entry in $directory.EnumerateFileSystemInfos('*')) {
            if (Test-ReparsePoint -Item $entry) {
                throw "Refusing reparse-point child: $($entry.FullName)"
            }
            $nodes.Add($entry)
            if ($entry -is [System.IO.DirectoryInfo]) {
                $pending.Push([System.IO.DirectoryInfo]$entry)
            }
        }
    }

    return $nodes.ToArray()
}

function Get-SharedStateDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$AllowMissing
    )

    $fullPath = Get-NormalizedPath -Path $Path
    $approvedRoot = Join-Path (
        Get-SpecialFolderPath -Folder ([System.Environment+SpecialFolder]::CommonApplicationData)
    ) 'USBRelay'
    if (-not (Test-PathWithinRoot -Candidate $fullPath -Root $approvedRoot)) {
        throw "Refusing a path outside the real ProgramData\\USBRelay root: $fullPath"
    }
    $directory = [System.IO.DirectoryInfo]::new($fullPath)
    if (-not $AllowMissing -and -not $directory.Exists) {
        throw "Shared state directory does not exist: $fullPath"
    }
    return $directory
}

function Get-LegacyStateDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$AllowMissing
    )

    $fullPath = Get-NormalizedPath -Path $Path
    $approvedRoot = Join-Path (
        Get-SpecialFolderPath -Folder ([System.Environment+SpecialFolder]::ApplicationData)
    ) 'USBRelay'
    if (-not (Test-PathWithinRoot -Candidate $fullPath -Root $approvedRoot)) {
        throw "Refusing a legacy path outside the real APPDATA\\USBRelay root: $fullPath"
    }
    $directory = [System.IO.DirectoryInfo]::new($fullPath)
    if ([System.IO.File]::Exists($fullPath)) {
        throw "Legacy shared state path is a file: $fullPath"
    }
    if (-not $AllowMissing -and -not $directory.Exists) {
        throw "Legacy shared state directory does not exist: $fullPath"
    }
    return $directory
}

function New-UsbRelayAcl {
    param([Parameter(Mandatory = $true)][bool]$IsDirectory)

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

function Get-SafeExistingNode {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fresh = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (Test-ReparsePoint -Item $fresh) {
        throw "Refusing reparse-point node: $($fresh.FullName)"
    }
    if ($fresh -is [System.IO.DirectoryInfo]) {
        Assert-NoReparseAncestors -Directory $fresh.Parent
    }
    else {
        Assert-NoReparseAncestors -Directory $fresh.Directory
    }
    return $fresh
}

function Set-SafeAcl {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$AclObject
    )

    $fresh = Get-SafeExistingNode -Path $Path
    Set-Acl -LiteralPath $fresh.FullName -AclObject $AclObject -ErrorAction Stop
    # Re-read after the mutation so a replacement/reparse race fails closed on
    # the next operation instead of being silently trusted.
    [void](Get-SafeExistingNode -Path $fresh.FullName)
}

function Remove-SafeNode {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fresh = Get-SafeExistingNode -Path $Path
    Remove-Item -LiteralPath $fresh.FullName -Force -ErrorAction Stop
}

function Remove-SafeSharedState {
    param([Parameter(Mandatory = $true)][System.IO.DirectoryInfo]$Root)

    if (-not $Root.Exists) {
        return
    }
    Assert-NoReparseAncestors -Directory $Root.Parent
    $nodes = @(Get-SafeFileSystemNodes -Root $Root)

    foreach ($node in @($nodes | Where-Object { $_ -isnot [System.IO.DirectoryInfo] })) {
        Remove-SafeNode -Path $node.FullName
    }
    foreach ($directory in @($nodes | Where-Object { $_ -is [System.IO.DirectoryInfo] } |
            Sort-Object @{ Expression = { $_.FullName.Length }; Descending = $true })) {
        Remove-SafeNode -Path $directory.FullName
    }
}

$target = Get-SharedStateDirectory -Path $TargetPath -AllowMissing
Assert-NoReparseAncestors -Directory $target.Parent

if ($RemoveTarget) {
    Remove-SafeSharedState -Root $target
    exit 0
}

$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
if ($currentSid.Value -eq 'S-1-5-18') {
    throw 'Run the client installer from the intended interactive user account; SYSTEM cannot identify the GUI owner safely.'
}
$systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$administratorsSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$allow = [System.Security.AccessControl.AccessControlType]::Allow
$none = [System.Security.AccessControl.PropagationFlags]::None

if ([System.IO.File]::Exists($target.FullName)) {
    throw "Shared state path is a file: $($target.FullName)"
}
if (-not $target.Exists) {
    [System.IO.Directory]::CreateDirectory($target.FullName) | Out-Null
    $target = [System.IO.DirectoryInfo]::new($target.FullName)
}

# Protect the root before walking children.  Once the root DACL is replaced,
# an ordinary user can no longer create or replace entries while this process
# applies the inherited rules below.
Set-SafeAcl -Path $target.FullName -AclObject (New-UsbRelayAcl -IsDirectory $true)

# Runtime code deliberately never creates a shared lock root.  Provision it
# here, after the parent DACL is protected, so SYSTEM and the installing GUI
# user serialize transport mutations through the same safe directory.
$operationLocks = [System.IO.DirectoryInfo]::new(
    [System.IO.Path]::Combine($target.FullName, 'operation-locks')
)
if ([System.IO.File]::Exists($operationLocks.FullName)) {
    throw "Operation lock path is a file: $($operationLocks.FullName)"
}
if (-not $operationLocks.Exists) {
    [System.IO.Directory]::CreateDirectory($operationLocks.FullName) | Out-Null
}
$operationLocks = Get-SafeExistingNode -Path $operationLocks.FullName
if ($operationLocks -isnot [System.IO.DirectoryInfo]) {
    throw "Operation lock path is not a directory: $($operationLocks.FullName)"
}

$nodes = @(Get-SafeFileSystemNodes -Root $target |
    Where-Object { $_.FullName -ine $target.FullName })
foreach ($node in $nodes) {
    Set-SafeAcl -Path $node.FullName -AclObject (
        New-UsbRelayAcl -IsDirectory ($node -is [System.IO.DirectoryInfo])
    )
}

if (-not [string]::IsNullOrWhiteSpace($LegacyRoot)) {
    $legacy = Get-LegacyStateDirectory -Path $LegacyRoot -AllowMissing
    if ($legacy.Exists) {
        Assert-NoReparseAncestors -Directory $legacy.Parent
        $legacyFilePath = [System.IO.Path]::Combine($legacy.FullName, 'pnp_sessions.json')
        if ([System.IO.File]::Exists($legacyFilePath)) {
            $safeLegacyFile = Get-SafeExistingNode -Path $legacyFilePath
            if ($safeLegacyFile -isnot [System.IO.FileInfo]) {
                throw "Legacy state is not a regular file: $legacyFilePath"
            }
            $destination = [System.IO.Path]::Combine($target.FullName, 'pnp_sessions.json')
            if (-not [System.IO.File]::Exists($destination) -and
                -not [System.IO.Directory]::Exists($destination)) {
                # The source is read immediately after the reparse/ancestor
                # check and the destination is inside the protected root.
                Copy-Item -LiteralPath $safeLegacyFile.FullName -Destination $destination -ErrorAction Stop
                Set-SafeAcl -Path $destination -AclObject (New-UsbRelayAcl -IsDirectory $false)
            }
            else {
                [void](Get-SafeExistingNode -Path $destination)
            }
        }
    }
}
