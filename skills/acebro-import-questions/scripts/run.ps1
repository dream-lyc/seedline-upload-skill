$ErrorActionPreference='Stop'
$skillRoot=Split-Path $PSScriptRoot -Parent
$target='windows-x64'
if (![Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This package requires Windows x64.' }
if (!(Get-Command curl.exe -ErrorAction SilentlyContinue)) { throw 'curl.exe is required.' }
$runtimeRoot=$skillRoot
$binary=Join-Path $runtimeRoot "assets/$target/acebro-local/acebro-local.exe"
if (!(Test-Path -LiteralPath $binary)) {
    $manifest=Get-Content -LiteralPath (Join-Path $skillRoot 'assets/releases.json') -Raw | ConvertFrom-Json
    $version=$manifest.version; $release=$manifest.platforms.$target
    if ($version -notmatch '^\d+\.\d+\.\d+$' -or $release.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Invalid release manifest.' }
    $expected="https://github.com/dream-lyc/seedline-upload-skill/releases/download/v$version/acebro-import-questions-$version-$target.zip"
    if ($release.url -cne $expected) { throw 'Invalid release address.' }
    $cache=Join-Path $env:LOCALAPPDATA "seedline-upload-skill/$version/$target"
    $runtimeRoot=Join-Path $cache 'acebro-import-questions'
    if (!(Test-Path -LiteralPath $runtimeRoot)) {
        [void][IO.Directory]::CreateDirectory($cache)
        $work=Join-Path $cache ('.download-'+[Guid]::NewGuid().ToString('N'))
        [void][IO.Directory]::CreateDirectory($work)
        try {
            $archive=Join-Path $work 'package.zip'
            & curl.exe -q --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --connect-timeout 20 --max-time 300 --output $archive --url $expected
            if ($LASTEXITCODE -ne 0) { throw 'Download failed.' }
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -cne $release.sha256) { throw 'Download checksum mismatch.' }
            Expand-Archive -LiteralPath $archive -DestinationPath $work
            Move-Item -LiteralPath (Join-Path $work 'acebro-import-questions') -Destination $runtimeRoot
        } finally { Remove-Item -LiteralPath $work -Recurse -Force }
    }
    $binary=Join-Path $runtimeRoot "assets/$target/acebro-local/acebro-local.exe"
}
foreach ($line in [IO.File]::ReadAllLines((Join-Path $runtimeRoot 'SHA256SUMS'))) {
    if ($line -notmatch '^([0-9a-f]{64})  (.+)$') { throw 'Invalid checksum manifest.' }
    $hash=$Matches[1]; $relative=$Matches[2]
    if ([IO.Path]::IsPathRooted($relative) -or $relative -match '(^|/)\.\.(/|$)') { throw 'Invalid package path.' }
    if ((Get-FileHash -LiteralPath (Join-Path $runtimeRoot $relative) -Algorithm SHA256).Hash.ToLower() -cne $hash) { throw 'Package incomplete. Please reinstall.' }
}
$env:PYTHONUTF8='1'
& $binary @args
exit $LASTEXITCODE
