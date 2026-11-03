# Signed single-attempt curl transport. Secrets remain in process memory.
. "$PSScriptRoot/windows_support.ps1"
$privateDir = $null
try {
    if ($args.Count -lt 4) { throw 'arguments' }
    $method=[string]$args[0]; $path=[string]$args[1]; $options=@{}
    for ($i=2; $i -lt $args.Count; $i+=2) {
        if ($i+1 -ge $args.Count -or $options.ContainsKey([string]$args[$i])) { throw 'arguments' }
        if ($args[$i] -notin @('--profile','--body','--content-type','--idempotency-key','--status-file')) { throw 'option' }
        $options[[string]$args[$i]]=[string]$args[$i+1]
    }
    if ($method -cnotmatch '^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$') { throw 'method' }
    if ($path -cnotmatch '^/api/integration/v1/' -or $path -match '[^\x21-\x7e]|[#\\]|%(?![a-fA-F0-9]{2})') { throw 'path' }
    $pathOnly=($path -split '\?',2)[0]
    if ($pathOnly -match '/\.{1,2}(/|$)|%2e|%2f|%5c|%0[0-9a-f]|%1[0-9a-f]|%20|%7f') { throw 'path' }
    $profilePath=$options['--profile']; Assert-Private $profilePath
    $profile=[IO.File]::ReadAllText($profilePath,$utf8) | ConvertFrom-Json
    if (@($profile.PSObject.Properties).Count -ne 3) { throw 'profile' }
    $origin=[string]$profile.origin; Assert-Origin $origin
    $ak=[string]$profile.access_key; $secret=[string]$profile.secret_key
    if ($ak -cnotmatch '^[A-Za-z0-9_-]{1,64}$' -or $secret -cnotmatch '^[A-Za-z0-9_-]{43}$') { throw 'key' }
    $key=[Convert]::FromBase64String($secret.Replace('-','+').Replace('_','/')+'=')
    if ([Convert]::ToBase64String($key).TrimEnd('=').Replace('+','-').Replace('/','_') -cne $secret) { throw 'key' }
    $body=$options['--body']; $mime=$options['--content-type']; $operation=$options['--idempotency-key']
    if ($options.ContainsKey('--body') -ne $options.ContainsKey('--content-type')) { throw 'body' }
    if ($body -and (!(Test-Path -LiteralPath $body -PathType Leaf) -or $mime -notmatch '^[\x20-\x7e]+$')) { throw 'body' }
    if ($options.ContainsKey('--idempotency-key') -and $operation -notmatch '^[\x21-\x7e]{1,128}$') { throw 'key' }
    $statusPath=$options['--status-file']
    if ($statusPath -and (Test-Path -LiteralPath $statusPath)) { throw 'status' }
    $created=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); $expires=$created+300
    $nonceBytes=New-Object byte[] 16
    $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($nonceBytes) } finally { $rng.Dispose() }
    $nonce=[Convert]::ToBase64String($nonceBytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    $url=$origin+$path
    $components='"@method" "@target-uri"'
    $canonical='"@method": '+$method+"`n"+'"@target-uri": '+$url
    $headers="Accept: application/json`nExpect:`n"
    if ($body) {
        $sha=[Security.Cryptography.SHA256]::Create(); $stream=[IO.File]::OpenRead($body)
        try { $digest='sha-256=:'+ [Convert]::ToBase64String($sha.ComputeHash($stream)) + ':' } finally { $stream.Dispose(); $sha.Dispose() }
        $components+=' "content-digest" "content-type"'
        $canonical+="`n"+'"content-digest": '+$digest+"`n"+'"content-type": '+$mime
        $headers+="Content-Digest: $digest`nContent-Type: $mime`n"
    }
    if ($operation) {
        $components+=' "idempotency-key"'; $canonical+="`n"+'"idempotency-key": '+$operation
        $headers+="Idempotency-Key: $operation`n"
    }
    $parameters='('+$components+');created='+$created+';expires='+$expires+';nonce="'+$nonce+'";keyid="'+$ak+'";alg="hmac-sha256"'
    $canonical+="`n"+'"@signature-params": '+$parameters
    $hmac=New-Object Security.Cryptography.HMACSHA256
    try { $hmac.Key=$key; $signature=[Convert]::ToBase64String($hmac.ComputeHash($utf8.GetBytes($canonical))) } finally { $hmac.Dispose() }
    $secret=$null; $key=$null; $profile=$null; $canonical=$null
    $headers+="Signature-Input: sig1=$parameters`nSignature: sig1=:$signature`:`n"
    $privateDir=New-PrivateDirectory
    $headerPath=Join-Path $privateDir 'headers'; Write-NewText $headerPath $headers
    $timeout=1900; if ($method -eq 'GET' -and $path -eq '/api/integration/v1/me') { $timeout=40 }
    $result=Invoke-CurlOnce $method $url $headerPath $body $privateDir $timeout
    if ($statusPath) { Write-NewText $statusPath $result.status }
    Output-Response $privateDir
    if ($result.code) { exit $result.code }
    if ($result.status -notmatch '^2\d\d$') { exit 22 }
    exit 0
} catch { [Console]::Error.WriteLine('Request stopped: check local configuration and tools.'); exit 2 }
finally { if ($privateDir) { Remove-Item -LiteralPath $privateDir -Recurse -Force } }
