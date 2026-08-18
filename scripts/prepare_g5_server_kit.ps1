[CmdletBinding()]
param(
    [string]$KitRoot = "D:\hxjh\trace-g-server-kit-g5",
    [string]$AgentImage = "trace-redteam-agent-qwen:g4-local",
    [string]$ControllerImage = "trace-redteam-controller:server",
    [string]$G4Acceptance = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$KitRoot = [IO.Path]::GetFullPath($KitRoot)
if (-not $G4Acceptance) {
    $G4Acceptance = Join-Path $repoRoot "reports\local-acceptance\20260804-g4-rerun2\acceptance.json"
}
$G4Acceptance = [IO.Path]::GetFullPath($G4Acceptance)
if (Test-Path -LiteralPath $KitRoot) {
    throw "G5 kit root already exists; preserve it or choose a new path: $KitRoot"
}

function Invoke-Checked([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

function Get-Image([string]$Reference) {
    $raw = & docker image inspect $Reference
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect image: $Reference" }
    $items = @($raw | ConvertFrom-Json)
    if ($items.Count -ne 1) { throw "Expected one image for $Reference" }
    return $items[0]
}

if (-not (Test-Path -LiteralPath $G4Acceptance -PathType Leaf)) {
    throw "G4 acceptance evidence is missing: $G4Acceptance"
}
$agent = Get-Image $AgentImage
$controller = Get-Image $ControllerImage
$labels = $agent.Config.Labels
$modelName = [string]$labels.'org.trace-g.model.name'
$modelDigest = [string]$labels.'org.trace-g.model.digest'
if ($labels.'org.trace-g.runtime' -ne 'self-contained-agent-qwen' -or
    $labels.'org.trace-g.agent-framework' -ne 'langgraph' -or
    $modelName -ne 'qwen3:8b' -or
    $modelDigest -notmatch '^sha256:[0-9a-f]{64}$') {
    throw "Agent image labels do not satisfy the G5 contract"
}

$imagesDir = New-Item -ItemType Directory -Path (Join-Path $KitRoot "images")
$sourceDir = New-Item -ItemType Directory -Path (Join-Path $KitRoot "source")
$scriptsDir = New-Item -ItemType Directory -Path (Join-Path $KitRoot "scripts")
$docsDir = New-Item -ItemType Directory -Path (Join-Path $KitRoot "docs")
$evidenceDir = New-Item -ItemType Directory -Path (Join-Path $KitRoot "evidence")
$agentArchive = Join-Path $imagesDir "trace-redteam-agent-qwen-g5.tar"
$controllerArchive = Join-Path $imagesDir "trace-redteam-controller-g5.tar"
Invoke-Checked "docker" @("save", "--output", $agentArchive, $AgentImage)
Invoke-Checked "docker" @("save", "--output", $controllerArchive, $ControllerImage)

$sourceArchive = Join-Path $sourceDir "wp2-redteam-source.tar"
Invoke-Checked "python" @(
    (Join-Path $repoRoot "scripts\build_g5_source_archive.py"),
    "--repository", $repoRoot,
    "--output", $sourceArchive
)

$requiredScripts = @(
    "server_stage_g5.sh",
    "server_run_g5_gate.sh",
    "server_python.sh",
    "run_g4_local_acceptance.py",
    "verify_g5_server_kit.py",
    "collect_g5_host_evidence.py",
    "validate_g5_server_results.py"
)
foreach ($name in $requiredScripts) {
    Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\$name") -Destination $scriptsDir
}
Copy-Item -LiteralPath (Join-Path $repoRoot "docs\server-deployment.md") -Destination $docsDir
Copy-Item -LiteralPath (Join-Path $repoRoot "docs\setup\G5服务器阶段门指南.md") -Destination $docsDir
$packagedG4Acceptance = Join-Path $evidenceDir "g4-acceptance.json"
Copy-Item -LiteralPath $G4Acceptance -Destination $packagedG4Acceptance

$lock = [ordered]@{
    schema_version = "1.0"
    gate = "5.G5"
    generated_at = [DateTimeOffset]::UtcNow.ToString("O")
    agent_image = [ordered]@{
        reference = $AgentImage
        image_id = ([string]$agent.Id).ToLowerInvariant()
        archive = "images/trace-redteam-agent-qwen-g5.tar"
        archive_sha256 = (Get-FileHash $agentArchive -Algorithm SHA256).Hash.ToLowerInvariant()
        labels = [ordered]@{
            'org.trace-g.runtime' = [string]$labels.'org.trace-g.runtime'
            'org.trace-g.agent-framework' = [string]$labels.'org.trace-g.agent-framework'
            'org.trace-g.model.name' = $modelName
            'org.trace-g.model.digest' = $modelDigest
            'org.trace-g.ollama.version' = [string]$labels.'org.trace-g.ollama.version'
            'org.trace-g.langgraph.version' = [string]$labels.'org.trace-g.langgraph.version'
        }
    }
    controller_image = [ordered]@{
        reference = $ControllerImage
        image_id = ([string]$controller.Id).ToLowerInvariant()
        archive = "images/trace-redteam-controller-g5.tar"
        archive_sha256 = (Get-FileHash $controllerArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    model_name = $modelName
    model_digest = $modelDigest
    source = [ordered]@{
        archive = "source/wp2-redteam-source.tar"
        sha256 = (Get-FileHash $sourceArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    g4_acceptance = [ordered]@{
        path = "evidence/g4-acceptance.json"
        sha256 = (Get-FileHash $packagedG4Acceptance -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    forbidden_external_artifacts = @("ollama_image", "ollama_model_archive", "host_model_mount")
}
$lockPath = Join-Path $KitRoot "g5-server-kit-lock.json"
[IO.File]::WriteAllText(
    $lockPath,
    (($lock | ConvertTo-Json -Depth 20) + "`n"),
    [Text.UTF8Encoding]::new($false)
)
Invoke-Checked "python" @(
    (Join-Path $repoRoot "scripts\verify_g5_server_kit.py"),
    "--lock", $lockPath,
    "--kit-root", $KitRoot
)

$sumPath = Join-Path $KitRoot "SHA256SUMS"
$prefix = $KitRoot.TrimEnd("\") + "\"
$lines = Get-ChildItem -LiteralPath $KitRoot -Recurse -File |
    Where-Object FullName -ne $sumPath |
    Sort-Object FullName |
    ForEach-Object {
        $relative = $_.FullName.Substring($prefix.Length).Replace("\", "/")
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
[IO.File]::WriteAllText($sumPath, (($lines -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
Write-Host "G5 server kit ready: $KitRoot"
Write-Host "Agent image ID: $($lock.agent_image.image_id)"
Write-Host "Model: $modelName@$modelDigest"
