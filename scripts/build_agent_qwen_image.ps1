param(
    [string]$KitRoot = "D:\hxjh\trace-g-server-kit",
    [string]$WheelhousePath = "",
    [string]$Image = "trace-redteam-agent-qwen:server",
    [string]$MutatorImage = "trace-redteam-mutator-qwen:server",
    [string]$ModelArchiveName = "ollama-models-qwen3-8b.tar",
    [string]$ModelLockName = "qwen3-8b-model-lock.json",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $WheelhousePath) {
    $WheelhousePath = Join-Path $KitRoot "python\agent-qwen-wheelhouse"
}

function Require-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file is missing: $Path"
    }
}

function Require-Directory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Required directory is missing: $Path"
    }
}

function Invoke-Checked([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

$modelArchive = Join-Path $KitRoot "models\$ModelArchiveName"
$modelLockPath = Join-Path $KitRoot "models\$ModelLockName"
$ollamaArchive = Join-Path $KitRoot "images\ollama-0.32.1.tar"
$imageLockPath = Join-Path $KitRoot "images\image-locks.json"
$pythonLock = Join-Path $repoRoot "agent_image\requirements.agent-qwen.lock"
$dockerfile = Join-Path $repoRoot "agent_image\Dockerfile.qwen"
$mutatorDockerfile = Join-Path $repoRoot "agent_image\Dockerfile.qwen-mutator"
$modelVerifier = Join-Path $repoRoot "scripts\verify_ollama_model_archive.py"
$promptIdentityScript = Join-Path $repoRoot "scripts\print_agent_prompt_identity.py"

foreach ($path in @(
    $modelArchive,
    $modelLockPath,
    $ollamaArchive,
    $imageLockPath,
    $pythonLock,
    $dockerfile,
    $mutatorDockerfile,
    $modelVerifier,
    $promptIdentityScript
)) {
    Require-File $path
}
Require-Directory $WheelhousePath

$modelLock = Get-Content -LiteralPath $modelLockPath -Raw | ConvertFrom-Json
$imageLocks = Get-Content -LiteralPath $imageLockPath -Raw | ConvertFrom-Json
if ($modelLock.schema_version -ne "1.0") {
    throw "Unsupported model lock schema"
}
if ($modelLock.ollama_image -ne $imageLocks.ollama.reference) {
    throw "Model lock and Ollama image lock reference different images"
}
$ollamaArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ollamaArchive).Hash.ToLowerInvariant()
if ($ollamaArchiveHash -ne $imageLocks.ollama.archive_sha256) {
    throw "Ollama image archive SHA-256 does not match image-locks.json"
}

Invoke-Checked $Python @(
    $modelVerifier,
    "--archive", $modelArchive,
    "--lock", $modelLockPath
)

$promptIdentityJson = (& $Python $promptIdentityScript)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve the shared Agent system prompt identity"
}
$promptIdentity = $promptIdentityJson | ConvertFrom-Json
$modelParts = ([string]$modelLock.model_name).Split(":", 2)
if ($modelParts.Count -ne 2 -or $modelParts[0] -notmatch "^[a-z0-9._-]+$" -or
    $modelParts[1] -notmatch "^[a-z0-9._-]+$") {
    throw "Model lock contains an unsupported Ollama model name"
}
$modelManifestPath = "registry.ollama.ai/library/$($modelParts[0])/$($modelParts[1])"

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("trace-g-agent-qwen-" + [guid]::NewGuid().ToString("N"))
$resolvedTempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
$resolvedTemporaryRoot = [IO.Path]::GetFullPath($temporaryRoot)
if (-not $resolvedTemporaryRoot.StartsWith($resolvedTempBase, [StringComparison]::OrdinalIgnoreCase) -or
    -not (Split-Path -Leaf $resolvedTemporaryRoot).StartsWith("trace-g-agent-qwen-")) {
    throw "Refusing unsafe temporary path: $resolvedTemporaryRoot"
}

New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    $wheelValidation = Join-Path $temporaryRoot "validated-wheels"
    $modelContext = Join-Path $temporaryRoot "model-context"
    New-Item -ItemType Directory -Path $wheelValidation | Out-Null
    New-Item -ItemType Directory -Path $modelContext | Out-Null

    Invoke-Checked $Python @(
        "-m", "pip", "download",
        "--no-index",
        "--find-links", $WheelhousePath,
        "--dest", $wheelValidation,
        "--platform", "manylinux_2_17_x86_64",
        "--python-version", "311",
        "--implementation", "cp",
        "--abi", "cp311",
        "--only-binary=:all:",
        "--require-hashes",
        "--no-deps",
        "--requirement", $pythonLock
    )

    Invoke-Checked "tar" @("-xf", $modelArchive, "-C", $modelContext)

    $ollamaReference = [string]$imageLocks.ollama.reference
    $localIdentity = (& docker image inspect --format "{{.Id}}" $ollamaReference 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Invoke-Checked "docker" @("load", "--input", $ollamaArchive)
        $localIdentity = (& docker image inspect --format "{{.Id}}" $ollamaReference)
        if ($LASTEXITCODE -ne 0) {
            throw "Loaded Ollama archive did not provide $ollamaReference"
        }
    }
    $localIdentity = ([string]$localIdentity).Trim().ToLowerInvariant()
    $allowedOllamaIdentities = @(
        ([string]$imageLocks.ollama.source_image_id).ToLowerInvariant(),
        ([string]$imageLocks.ollama.archive_config_digest).ToLowerInvariant()
    )
    if ($localIdentity -notin $allowedOllamaIdentities) {
        throw "Local Ollama image identity is not allowed by image-locks.json: $localIdentity"
    }

    Invoke-Checked "docker" @(
        "buildx", "build",
        "--load",
        "--file", $dockerfile,
        "--tag", $Image,
        "--build-context", "wheelhouse=$wheelValidation",
        "--build-context", "ollama-models=$modelContext",
        "--build-arg", "OLLAMA_IMAGE=$ollamaReference",
        "--build-arg", "MODEL_NAME=$($modelLock.model_name)",
        "--build-arg", "MODEL_DIGEST=$($modelLock.model_digest)",
        "--build-arg", "MODEL_MANIFEST_PATH=$modelManifestPath",
        "--build-arg", "SYSTEM_PROMPT_VERSION=$($promptIdentity.version)",
        "--build-arg", "SYSTEM_PROMPT_DIGEST=$($promptIdentity.digest)",
        "--build-arg", "MUTATOR_PROMPT_VERSION=$($promptIdentity.mutator_version)",
        "--build-arg", "MUTATOR_PROMPT_DIGEST=$($promptIdentity.mutator_digest)",
        $repoRoot
    )

    $config = (& docker image inspect --format "{{json .Config}}" $Image) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect built Agent-Qwen image"
    }
    if ($config.User -ne "10001:10001") {
        throw "Built Agent-Qwen image does not run as the locked non-root user"
    }
    if (($config.Entrypoint -join " ") -ne "python -m app.agent_qwen_bootstrap") {
        throw "Built Agent-Qwen image has an unexpected entrypoint"
    }
    $labels = $config.Labels
    if ($labels.'org.trace-g.runtime' -ne "self-contained-agent-qwen" -or
        $labels.'org.trace-g.role' -ne "agent" -or
        $labels.'org.trace-g.agent-framework' -ne "langgraph" -or
        $labels.'org.trace-g.model.digest' -ne $modelLock.model_digest -or
        $labels.'org.trace-g.system-prompt.version' -ne $promptIdentity.version -or
        $labels.'org.trace-g.system-prompt.digest' -ne $promptIdentity.digest -or
        $labels.'org.trace-g.mutator-prompt.version' -ne $promptIdentity.mutator_version -or
        $labels.'org.trace-g.mutator-prompt.digest' -ne $promptIdentity.mutator_digest) {
        throw "Built Agent-Qwen image identity labels do not match the locked contract"
    }

    $builtId = (& docker image inspect --format "{{.Id}}" $Image).Trim()
    Invoke-Checked "docker" @(
        "buildx", "build",
        "--load",
        "--file", $mutatorDockerfile,
        "--tag", $MutatorImage,
        "--build-arg", "AGENT_BASE_IMAGE=$Image",
        $repoRoot
    )
    $mutatorConfig = (& docker image inspect --format "{{json .Config}}" $MutatorImage) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect built Mutator-Qwen image"
    }
    if ($mutatorConfig.User -ne "10001:10001" -or
        ($mutatorConfig.Entrypoint -join " ") -ne "python -m app.office_v2_mutator_worker" -or
        $mutatorConfig.Labels.'org.trace-g.runtime' -ne "self-contained-mutator-qwen" -or
        $mutatorConfig.Labels.'org.trace-g.role' -ne "mutator" -or
        $mutatorConfig.Labels.'org.trace-g.model.digest' -ne $modelLock.model_digest) {
        throw "Built Mutator-Qwen image identity does not match the locked contract"
    }
    $mutatorBuiltId = (& docker image inspect --format "{{.Id}}" $MutatorImage).Trim()
    Write-Host "Agent-Qwen image built and inspected: $Image"
    Write-Host "Image ID: $builtId"
    Write-Host "Mutator-Qwen image built and inspected: $MutatorImage"
    Write-Host "Mutator image ID: $mutatorBuiltId"
    Write-Host "Model: $($modelLock.model_name)@$($modelLock.model_digest)"
    Write-Host "System prompt: $($promptIdentity.version)@$($promptIdentity.digest)"
    Write-Host "Mutator prompt: $($promptIdentity.mutator_version)@$($promptIdentity.mutator_digest)"
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}
