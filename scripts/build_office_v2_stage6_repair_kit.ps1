param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$BaseModelLock,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$Revision = "HEAD",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$repositoryPath = (Resolve-Path -LiteralPath $Repository).Path
$baseLockPath = (Resolve-Path -LiteralPath $BaseModelLock).Path
$pythonPaths = @(
    (Join-Path $repositoryPath "src"),
    (Join-Path $repositoryPath "agent_image")
)
if ($env:PYTHONPATH) {
    $pythonPaths += $env:PYTHONPATH
}
$env:PYTHONPATH = $pythonPaths -join [IO.Path]::PathSeparator
if (Test-Path -LiteralPath $OutputDirectory) {
    throw "Refusing to overwrite repair kit: $OutputDirectory"
}
$revisionId = (& git -C $repositoryPath rev-parse $Revision).Trim()
if ($LASTEXITCODE -ne 0 -or $revisionId -notmatch '^[0-9a-f]{40}$') {
    throw "Unable to resolve an exact Git revision"
}
$sourceDir = Join-Path $OutputDirectory "source"
$locksDir = Join-Path $OutputDirectory "locks"
New-Item -ItemType Directory -Path $sourceDir, $locksDir | Out-Null
$archive = Join-Path $sourceDir "wp2-redteam-source.tar"
$repairLock = Join-Path $locksDir "stage6-repair-plan.json"

& $Python "$repositoryPath/scripts/build_g5_source_archive.py" `
    --repository $repositoryPath --revision $revisionId --output $archive
if ($LASTEXITCODE -ne 0) { throw "Source archive build failed" }
$base = Get-Content -Raw -LiteralPath $baseLockPath | ConvertFrom-Json
Copy-Item -LiteralPath $baseLockPath `
    -Destination (Join-Path $locksDir "stage6-base-model-lock.json")
& $Python "$repositoryPath/scripts/build_office_v2_stage6_repair_lock.py" plan `
    --repository $repositoryPath --revision $revisionId --source-archive $archive `
    --model-digest $base.manifest_digest `
    --controller-image $base.controller_image_reference `
    --base-model-lock $baseLockPath --output $repairLock
if ($LASTEXITCODE -ne 0) { throw "Repair plan lock build failed" }

Copy-Item -LiteralPath "$repositoryPath/scripts/server_apply_office_v2_step6_repair.sh" `
    -Destination "$OutputDirectory/server_apply_office_v2_step6_repair.sh"
$relativeFiles = @(
    "source/wp2-redteam-source.tar",
    "locks/stage6-base-model-lock.json",
    "locks/stage6-repair-plan.json",
    "server_apply_office_v2_step6_repair.sh"
)
$sumLines = foreach ($relative in $relativeFiles) {
    $full = Join-Path $OutputDirectory $relative
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $full).Hash.ToLowerInvariant()
    "$hash  $($relative.Replace('\', '/'))"
}
$sumPayload = ($sumLines -join "`n") + "`n"
[IO.File]::WriteAllText(
    (Join-Path $OutputDirectory "SHA256SUMS"),
    $sumPayload,
    [Text.UTF8Encoding]::new($false)
)
Write-Output "repair-kit=$OutputDirectory"
Write-Output "source-revision=$revisionId"
