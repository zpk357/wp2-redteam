param(
    [string]$Commit = "HEAD",
    [string]$OutputDirectory = "D:\hxjh\server-source-snapshots"
)

$ErrorActionPreference = "Stop"
$repository = Split-Path -Parent $PSScriptRoot
$resolvedCommit = (& git -C $repository rev-parse --verify "$Commit^{commit}").Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $resolvedCommit -notmatch "^[0-9a-f]{40}$") {
    throw "Unable to resolve a full source commit: $Commit"
}
$shortCommit = $resolvedCommit.Substring(0, 12)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$archive = Join-Path $OutputDirectory "wp2-redteam-source-$shortCommit.tar.gz"
$digestFile = "$archive.sha256"
$identityFile = "$archive.source.json"
if (Test-Path -LiteralPath $archive) {
    throw "Refusing to overwrite an existing source snapshot: $archive"
}

& git -C $repository archive --format=tar.gz --output=$archive $resolvedCommit
if ($LASTEXITCODE -ne 0) {
    throw "git archive failed"
}
$digest = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $digestFile -Value "sha256:$digest  $([IO.Path]::GetFileName($archive))" -Encoding ascii
$identity = [ordered]@{
    schema_version = "trace-g-source-snapshot-v1"
    source_commit = $resolvedCommit
    archive_name = [IO.Path]::GetFileName($archive)
    archive_sha256 = "sha256:$digest"
}
$identity | ConvertTo-Json | Set-Content -LiteralPath $identityFile -Encoding ascii
Write-Host "Source commit: $resolvedCommit"
Write-Host "Source snapshot: $archive"
Write-Host "SHA-256: sha256:$digest"
