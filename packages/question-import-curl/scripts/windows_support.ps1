# Windows PowerShell 5.1: no external language runtime or SDK.
Set-PSDebug -Off
$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
function Protect-Local([string]$Path) {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'link' }
    if ($item.PSIsContainer) {
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
    } else {
        $acl = New-Object Security.AccessControl.FileSecurity
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','Allow')
    }
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true,$false)
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Assert-Private([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'file' }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = Get-Acl -LiteralPath $Path
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) { throw 'owner' }
    $rules = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if (!$rules.Count) { throw 'acl' }
    foreach ($rule in $rules) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -ne $sid.Value) { throw 'shared' }
    }
}
function Assert-Origin([string]$Origin) {
    if ($Origin -cnotmatch '^(https|http)://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(:([0-9]+))?$') { throw 'origin' }
    $uri = [Uri]$Origin
    if ($uri.Scheme -eq 'http' -and $uri.Host -notin @('127.0.0.1','[::1]','::1')) { throw 'origin' }
    if ($uri.Port -lt 1 -or $uri.Port -gt 65535) { throw 'port' }
}
function New-PrivateDirectory {
    $path = Join-Path ([IO.Path]::GetTempPath()) ('seedline-http-' + [Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($path)
    Protect-Local $path
    return $path
}
function Write-NewText([string]$Path,[string]$Text) {
    $stream = New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $data=$utf8.GetBytes($Text); $stream.Write($data,0,$data.Length) } finally { $stream.Dispose() }
}
function Invoke-CurlOnce([string]$Method,[string]$Url,[string]$Headers,[string]$Body,[string]$Directory,[int]$Timeout) {
    foreach ($key in @('http_proxy','https_proxy','all_proxy','no_proxy','CURL_CA_BUNDLE','SSL_CERT_FILE','SSL_CERT_DIR')) {
        [Environment]::SetEnvironmentVariable($key,$null,'Process')
    }
    $arguments = @('-q','--silent','--path-as-is','--globoff','--noproxy','*','--proxy=',
        '--proto','=http,https','--connect-timeout','10','--max-time',"$Timeout",
        '--request',$Method,'--header',"@$Headers",'--output',(Join-Path $Directory 'response'),
        '--dump-header',(Join-Path $Directory 'received-headers'),'--write-out','%{http_code}')
    if ($Body) { $arguments += @('--upload-file',$Body) }
    $arguments += @('--url',$Url)
    $status = & curl.exe @arguments 2>$null
    $code = $LASTEXITCODE
    if ($status -notmatch '^\d{3}$') { $status = '000' }
    return @{status=[string]$status; code=$code}
}
function Output-Response([string]$Directory) {
    $path = Join-Path $Directory 'response'
    if (Test-Path -LiteralPath $path) {
        $stream=[IO.File]::OpenRead($path)
        try { $stream.CopyTo([Console]::OpenStandardOutput()) } finally { $stream.Dispose() }
    }
}
