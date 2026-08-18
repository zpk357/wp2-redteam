[CmdletBinding()]
param(
    [string]$KitRoot = "",
    [string]$AgentImage = "trace-redteam-agent:server",
    [string]$ControllerImage = "trace-redteam-controller:server",
    [string]$OllamaImage = "ollama/ollama:0.32.1",
    [string]$Python = "python",
    [switch]$SkipImageExport
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot ".."))
if (-not $KitRoot) {
    $KitRoot = Join-Path $workspaceRoot "trace-g-server-kit"
}
$KitRoot = [IO.Path]::GetFullPath($KitRoot)

$imagesDir = Join-Path $KitRoot "images"
$modelsDir = Join-Path $KitRoot "models"
$runtimeDir = Join-Path $KitRoot "runtime"
$sourceDir = Join-Path $KitRoot "source"
$scriptsDir = Join-Path $KitRoot "scripts"
$docsDir = Join-Path $KitRoot "docs"
$resultsDir = Join-Path $KitRoot "results"
@($imagesDir, $modelsDir, $runtimeDir, $sourceDir, $scriptsDir, $docsDir, $resultsDir) |
    ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }

function Require-File {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required offline artifact is missing: $Path"
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$Command,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

$modelArchive = Join-Path $modelsDir "ollama-models-qwen3-8b.tar"
$modelLock = Join-Path $modelsDir "qwen3-8b-model-lock.json"
Require-File $modelArchive
Require-File $modelLock
Invoke-Checked $Python @(
    (Join-Path $repoRoot "scripts\verify_ollama_model_archive.py"),
    "--archive", $modelArchive,
    "--lock", $modelLock
)

$nvidiaBundle = Join-Path $runtimeDir "nvidia-container-toolkit_1.19.1_deb_amd64.tar.gz"
$nvidiaChecksums = Join-Path $runtimeDir "nvidia-container-toolkit_1.19.1_checksums.txt"
Require-File $nvidiaBundle
Require-File $nvidiaChecksums

function Get-DockerImageInspect {
    param([Parameter(Mandatory)][string]$Reference)

    $raw = & docker image inspect $Reference
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker image: $Reference"
    }
    $items = @($raw | ConvertFrom-Json)
    if ($items.Count -ne 1) {
        throw "Expected one Docker image for $Reference, observed $($items.Count)"
    }
    return $items[0]
}

function Get-SavedImageConfigDigest {
    param(
        [Parameter(Mandatory)][string]$ArchivePath,
        [Parameter(Mandatory)][string]$Reference
    )

    Require-File $ArchivePath
    $manifestLines = @(& tar -xOf $ArchivePath "manifest.json")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read manifest.json from Docker archive: $ArchivePath"
    }
    try {
        $entries = @(($manifestLines -join [Environment]::NewLine) | ConvertFrom-Json)
    }
    catch {
        throw "Invalid manifest.json in Docker archive $($ArchivePath): $($_.Exception.Message)"
    }
    $matches = @(
        $entries | Where-Object {
            @($_.RepoTags) -contains $Reference
        }
    )
    if ($matches.Count -ne 1) {
        throw "Docker archive $ArchivePath does not contain exactly one $Reference entry"
    }
    $configName = [IO.Path]::GetFileNameWithoutExtension([string]$matches[0].Config)
    if ($configName -notmatch '^[0-9a-f]{64}$') {
        throw "Docker archive $ArchivePath has an invalid config digest: $configName"
    }
    return "sha256:$configName"
}

function Assert-SavedImageLocks {
    param(
        [Parameter(Mandatory)][string]$LockPath,
        [Parameter(Mandatory)][array]$Artifacts
    )

    Require-File $LockPath
    $locks = Get-Content -Raw -LiteralPath $LockPath | ConvertFrom-Json
    if ($locks.schema_version -ne "2.0") {
        throw "Unsupported image lock schema: $($locks.schema_version)"
    }
    foreach ($artifact in $Artifacts) {
        $property = $locks.PSObject.Properties[$artifact.Key]
        if ($null -eq $property) {
            throw "Image lock is missing the '$($artifact.Key)' entry"
        }
        $entry = $property.Value
        if ($entry.reference -ne $artifact.Reference) {
            throw "Locked $($artifact.Key) reference does not match $($artifact.Reference)"
        }
        if ($entry.archive -ne $artifact.Archive) {
            throw "Locked $($artifact.Key) archive does not match $($artifact.Archive)"
        }
        $archiveSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact.ArchivePath).Hash.ToLowerInvariant()
        if ($archiveSha256 -ne $entry.archive_sha256) {
            throw "Locked $($artifact.Key) archive SHA-256 does not match the saved file"
        }
        $savedDigest = Get-SavedImageConfigDigest -ArchivePath $artifact.ArchivePath -Reference $artifact.Reference
        if ($savedDigest -ne $entry.archive_config_digest) {
            throw (
                "$($artifact.Key) archive config digest mismatch: " +
                "expected $($entry.archive_config_digest), observed $savedDigest"
            )
        }
        if ($entry.source_image_id -notmatch '^sha256:[0-9a-f]{64}$') {
            throw "Locked $($artifact.Key) source image ID is invalid"
        }
    }
}

$obsoleteRuntimeArtifacts = @(
    "uv-x86_64-unknown-linux-gnu.tar.gz",
    "uv-x86_64-unknown-linux-gnu.tar.gz.sha256",
    "ollama-linux-amd64-v0.32.1.tar.zst",
    "ollama-v0.32.1-sha256sum.txt"
)
foreach ($artifactName in $obsoleteRuntimeArtifacts) {
    $artifactPath = Join-Path $runtimeDir $artifactName
    if (Test-Path -LiteralPath $artifactPath -PathType Leaf) {
        Remove-Item -LiteralPath $artifactPath -Force
    }
}

$agentArchive = Join-Path $imagesDir "trace-redteam-agent-server.tar"
$controllerArchive = Join-Path $imagesDir "trace-redteam-controller-server.tar"
$ollamaArchive = Join-Path $imagesDir "ollama-0.32.1.tar"
$imageLockPath = Join-Path $imagesDir "image-locks.json"
$imageArtifacts = @(
    [pscustomobject]@{
        Key = "agent"
        Reference = $AgentImage
        ArchivePath = $agentArchive
        Archive = "images/trace-redteam-agent-server.tar"
    },
    [pscustomobject]@{
        Key = "controller"
        Reference = $ControllerImage
        ArchivePath = $controllerArchive
        Archive = "images/trace-redteam-controller-server.tar"
    },
    [pscustomobject]@{
        Key = "ollama"
        Reference = $OllamaImage
        ArchivePath = $ollamaArchive
        Archive = "images/ollama-0.32.1.tar"
    }
)

if ($SkipImageExport) {
    Assert-SavedImageLocks -LockPath $imageLockPath -Artifacts $imageArtifacts
    Write-Host "Existing image archives match image-locks.json."
}
else {
    $agentInspect = Get-DockerImageInspect $AgentImage
    $controllerInspect = Get-DockerImageInspect $ControllerImage
    $ollamaInspect = Get-DockerImageInspect $OllamaImage

    Invoke-Checked "docker" @("save", "--output", $agentArchive, $AgentImage)
    Invoke-Checked "docker" @("save", "--output", $controllerArchive, $ControllerImage)
    Invoke-Checked "docker" @("save", "--output", $ollamaArchive, $OllamaImage)

    $agentSavedDigest = Get-SavedImageConfigDigest -ArchivePath $agentArchive -Reference $AgentImage
    $controllerSavedDigest = Get-SavedImageConfigDigest -ArchivePath $controllerArchive -Reference $ControllerImage
    $ollamaSavedDigest = Get-SavedImageConfigDigest -ArchivePath $ollamaArchive -Reference $OllamaImage

    $imageLocks = [ordered]@{
        schema_version = "2.0"
        generated_at = [DateTimeOffset]::UtcNow.ToString("O")
        agent = [ordered]@{
            reference = $AgentImage
            archive = "images/trace-redteam-agent-server.tar"
            archive_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $agentArchive).Hash.ToLowerInvariant()
            archive_config_digest = $agentSavedDigest
            source_image_id = ([string]$agentInspect.Id).ToLowerInvariant()
            repo_digests = @($agentInspect.RepoDigests)
            repo_tags = @($agentInspect.RepoTags)
        }
        controller = [ordered]@{
            reference = $ControllerImage
            archive = "images/trace-redteam-controller-server.tar"
            archive_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $controllerArchive).Hash.ToLowerInvariant()
            archive_config_digest = $controllerSavedDigest
            source_image_id = ([string]$controllerInspect.Id).ToLowerInvariant()
            repo_digests = @($controllerInspect.RepoDigests)
            repo_tags = @($controllerInspect.RepoTags)
        }
        ollama = [ordered]@{
            reference = $OllamaImage
            archive = "images/ollama-0.32.1.tar"
            archive_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ollamaArchive).Hash.ToLowerInvariant()
            archive_config_digest = $ollamaSavedDigest
            source_image_id = ([string]$ollamaInspect.Id).ToLowerInvariant()
            repo_digests = @($ollamaInspect.RepoDigests)
            repo_tags = @($ollamaInspect.RepoTags)
        }
    }
    [IO.File]::WriteAllText(
        $imageLockPath,
        (($imageLocks | ConvertTo-Json -Depth 20) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    Assert-SavedImageLocks -LockPath $imageLockPath -Artifacts $imageArtifacts
}
$sourceArchive = Join-Path $sourceDir "wp2-redteam-source.tar"
if (Test-Path -LiteralPath $sourceArchive) {
    Remove-Item -LiteralPath $sourceArchive -Force
}
$tarArgs = @(
    "--exclude=.git",
    "--exclude=.deps",
    "--exclude=.trace-g",
    "--exclude=.venv",
    "--exclude=.venv-*",
    "--exclude=.mypy_cache",
    "--exclude=.tox",
    "--exclude=.coverage",
    "--exclude=htmlcov",
    "--exclude=dist",
    "--exclude=build",
    "--exclude=*.egg-info",
    "--exclude=.env",
    "--exclude=./deploy/.env.server",
    "--exclude=deploy/.env.server",
    "--exclude=.ssh",
    "--exclude=authorized_keys",
    "--exclude=id_rsa*",
    "--exclude=id_ed25519*",
    "--exclude=*.pem",
    "--exclude=*.key",
    "--exclude=*.p12",
    "--exclude=*.pfx",
    "--exclude=*.sqlite*",
    "--exclude=*.db",
    "--exclude=*.db-*",
    "--exclude=.pytest_cache",
    "--exclude=.ruff_cache",
    "--exclude=__pycache__",
    "--exclude=.pytest-tmp-*",
    "--exclude=data",
    "--exclude=reports",
    "-cf", $sourceArchive,
    "-C", $repoRoot,
    "."
)
Invoke-Checked "tar" $tarArgs

$sourceMembers = @(& tar -tf $sourceArchive)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to list source archive: $sourceArchive"
}
$unsafeMemberPatterns = @(
    '(?i)(^|/)[.]git(/|$)',
    '(?i)(^|/)[.]trace-g(/|$)',
    '(?i)(^|/)[.]ssh(/|$)',
    '(?i)(^|/)[.]env$',
    '(?i)(^|/)deploy/[.]env[.]server$',
    '(?i)(^|/)authorized_keys$',
    '(?i)(^|/)id_(rsa|ed25519)([.]pub)?$',
    '(?i)[.](pem|key|p12|pfx)$',
    '(?i)[.](sqlite|sqlite3|db)(-|$)'
)
foreach ($member in $sourceMembers) {
    $normalized = $member -replace '^[.]/', ''
    foreach ($pattern in $unsafeMemberPatterns) {
        if ($normalized -match $pattern) {
            throw "Unsafe member found in source archive: $member"
        }
    }
}
$serverScripts = @(
    "server_bootstrap_offline.sh",
    "server_stage_offline.sh",
    "server_activate_gpu.sh",
    "server_abort.sh",
    "server_python.sh",
    "server_env.sh",
    "verify_server_locks.py",
    "verify_ollama_model_archive.py",
    "warm_ollama_model.py",
    "server_real_model_smoke.sh",
    "server_validate_replay.sh",
    "server_validate_trace_workspace.sh",
    "validate_trace_workspace_results.py",
    "stage_trace_workspace_results.py",
    "server_export_trace_workspace.sh",
    "build_server_learning_dataset.py",
    "build_weeks_1_5_validation.py",
    "stage_server_results.py",
    "verify_server_results.py",
    "server_export_incomplete.sh",
    "server_export_results.sh"
)
foreach ($scriptName in $serverScripts) {
    $scriptPath = Join-Path $repoRoot "scripts\$scriptName"
    Require-File $scriptPath
    Copy-Item -LiteralPath $scriptPath -Destination $scriptsDir -Force
}
Copy-Item -LiteralPath (Join-Path $repoRoot "docs\server-deployment.md") -Destination $docsDir -Force
$setupDocsDir = Join-Path $repoRoot "docs/setup"
if (Test-Path -LiteralPath $setupDocsDir -PathType Container) {
    Get-ChildItem -LiteralPath $setupDocsDir -Filter "*.md" -File |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $docsDir -Force
        }
}
$sumPath = Join-Path $KitRoot "SHA256SUMS"
$relativeRoot = $KitRoot.TrimEnd("\") + "\"
$sumLines = Get-ChildItem -LiteralPath $KitRoot -Recurse -File |
    Where-Object { $_.FullName -ne $sumPath } |
    Sort-Object FullName |
    ForEach-Object {
        $relative = $_.FullName.Substring($relativeRoot.Length).Replace("\", "/")
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
[IO.File]::WriteAllText(
    $sumPath,
    (($sumLines -join [string][char]10) + [string][char]10),
    [Text.UTF8Encoding]::new($false)
)

$size = (Get-ChildItem -LiteralPath $KitRoot -Recurse -File |
    Measure-Object -Property Length -Sum).Sum
Write-Host "Server kit ready: $KitRoot"
Write-Host ("Total bytes: {0:N0}" -f $size)
Write-Host "Integrity manifest: $sumPath"
