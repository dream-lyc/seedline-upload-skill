param([ValidateSet('protect','check','owner')][string]$Action, [string]$Target)
$ErrorActionPreference = 'Stop'
try {
    $item = Get-Item -LiteralPath $Target -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'link' }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if ($Action -eq 'protect') {
        if ($item.PSIsContainer) {
            $acl = New-Object Security.AccessControl.DirectorySecurity
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        } else {
            $acl = New-Object Security.AccessControl.FileSecurity
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')
        }
        $acl.SetOwner($sid)
        $acl.SetAccessRuleProtection($true, $false)
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $Target -AclObject $acl
    }
    $acl = Get-Acl -LiteralPath $Target
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) { throw 'owner' }
    if ($Action -ne 'owner') {
        $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
        if ($rules.Count -eq 0) { throw 'empty acl' }
        foreach ($rule in $rules) {
            if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -ne $sid.Value) { throw 'shared acl' }
        }
    }
    exit 0
} catch { [Console]::Error.WriteLine('Private file permission check failed.'); exit 2 }
