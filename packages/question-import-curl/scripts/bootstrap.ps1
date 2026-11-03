# Dedicated website authorization protocol, no cookies, redirects or retries.
. "$PSScriptRoot/windows_support.ps1"
$privateDir=$null
try {
    if ($args.Count -ne 6) { throw 'arguments' }
    $operation,$origin,$requestId,$body,$statusPath,$retryPath=$args
    Assert-Origin $origin
    $method='POST'
    switch ($operation) {
        'init' { if ($requestId -ne '-') { throw 'id' }; $path='/api/agent-connections' }
        {$_ -in 'claims','ack','cancel'} {
            if ($requestId -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') { throw 'id' }
            $suffix='claims'
            if ($operation -eq 'ack') { $suffix='acknowledgement'; $method='PUT' }
            if ($operation -eq 'cancel') { $suffix='cancellation'; $method='PUT' }
            $path="/api/agent-connections/$requestId/$suffix"
        }
        default { throw 'operation' }
    }
    Assert-Private $body
    if ($statusPath -eq $retryPath -or (Test-Path -LiteralPath $statusPath) -or (Test-Path -LiteralPath $retryPath)) { throw 'output' }
    $privateDir=New-PrivateDirectory
    $headers=Join-Path $privateDir 'headers'
    Write-NewText $headers "Content-Type: application/json`nAccept: application/json`nExpect:`n"
    $result=Invoke-CurlOnce $method ($origin+$path) $headers $body $privateDir 40
    Write-NewText $statusPath $result.status
    $retry=''
    $received=Join-Path $privateDir 'received-headers'
    if (Test-Path -LiteralPath $received) {
        foreach ($line in [IO.File]::ReadAllLines($received)) { if ($line -match '^Retry-After:\s*(\d+)\s*$') { $retry=$Matches[1] } }
    }
    Write-NewText $retryPath $retry
    if ($result.code) { exit $result.code }
    Output-Response $privateDir
    exit 0
} catch { [Console]::Error.WriteLine(('Connection request stopped: ' + $_.Exception.GetType().Name + ' at line ' + $_.InvocationInfo.ScriptLineNumber)); exit 2 }
finally { if ($privateDir) { Remove-Item -LiteralPath $privateDir -Recurse -Force } }
