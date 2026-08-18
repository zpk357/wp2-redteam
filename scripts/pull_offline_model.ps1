[CmdletBinding()]
param(
    [string]$ModelName = "qwen3:8b",
    [string]$OllamaImage = "ollama/ollama:0.32.1",
    [string]$AgentImage = "trace-redteam-agent:server",
    [string]$ModelVolume = "trace-g-offline-models",
    [string]$ContainerName = "trace-g-offline-ollama",
    [string]$ArchiveStem = "ollama-models-qwen3-8b",
    [string]$LockFileName = "qwen3-8b-model-lock.json",
    [int]$Port = 11435,
    [string]$KitRoot = "",
    [switch]$SkipImagePull
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $KitRoot) {
    $KitRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot "..\trace-g-server-kit"))
}
$modelsDir = Join-Path $KitRoot "models"
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null
if ($ArchiveStem -notmatch "^[a-zA-Z0-9][a-zA-Z0-9._-]*$" -or
    $LockFileName -notmatch "^[a-zA-Z0-9][a-zA-Z0-9._-]*\.json$") {
    throw "ArchiveStem and LockFileName must be plain file names."
}

function Invoke-Docker {
    param([Parameter(Mandatory)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Test-DockerImage {
    param([Parameter(Mandatory)][string]$Reference)
    $imageIds = @(& docker image ls --quiet $Reference)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list Docker image $Reference."
    }
    return $imageIds.Count -gt 0
}

& docker version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not available."
}
if (-not (Test-DockerImage $OllamaImage)) {
    if ($SkipImagePull) {
        throw "Required image is missing: $OllamaImage"
    }
    Write-Host "Pulling pinned Ollama image $OllamaImage ..."
    Invoke-Docker @("pull", $OllamaImage)
}
if (-not (Test-DockerImage $AgentImage)) {
    throw "Required helper image is missing: $AgentImage. Build it before exporting the model."
}

$existingContainer = & docker ps -aq --filter "name=^/$ContainerName$"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker containers."
}
if ($existingContainer) {
    throw "Refusing to replace existing container: $ContainerName"
}

$matchingVolumes = @(& docker volume ls --quiet --filter "name=^$ModelVolume$")
if ($LASTEXITCODE -ne 0) {
    throw "Unable to list Docker volumes."
}
if ($ModelVolume -notin $matchingVolumes) {
    Invoke-Docker @("volume", "create", $ModelVolume)
}

$containerStarted = $false
try {
    $runArgs = @(
        "run", "-d",
        "--name", $ContainerName,
        "--label", "trace-g.component=offline-model-acquisition",
        "--label", "trace-g.model.name=$ModelName",
        "-p", "127.0.0.1:$($Port):11434",
        "-e", "OLLAMA_HOST=0.0.0.0:11434",
        "-e", "OLLAMA_MODELS=/models",
        "-v", "$($ModelVolume):/models",
        $OllamaImage
    )
    $containerId = & docker @runArgs
    if ($LASTEXITCODE -ne 0 -or -not $containerId) {
        throw "Failed to start temporary Ollama container."
    }
    $containerStarted = $true

    $tagsUri = "http://127.0.0.1:$Port/api/tags"
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(2)
    while ($true) {
        try {
            Invoke-RestMethod -Method Get -Uri $tagsUri -TimeoutSec 5 | Out-Null
            break
        }
        catch {
            if ([DateTimeOffset]::UtcNow -ge $deadline) {
                Invoke-Docker @("logs", $ContainerName)
                throw "Ollama did not become ready within two minutes."
            }
            Start-Sleep -Seconds 2
        }
    }

    Write-Host "Ensuring real model $ModelName is complete in the local Docker volume ..."
    $pullBody = @{ model = $ModelName; stream = $false } | ConvertTo-Json -Compress
    $pullResult = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/pull" -ContentType "application/json" -Body $pullBody -TimeoutSec 7200
    if ($pullResult.status -ne "success") {
        throw "Ollama pull did not return success."
    }

    $tags = Invoke-RestMethod -Method Get -Uri $tagsUri -TimeoutSec 15
    $matches = @($tags.models | Where-Object { $_.name -eq $ModelName })
    if ($matches.Count -ne 1) {
        throw "Expected exactly one Ollama tag named $ModelName, found $($matches.Count)."
    }
    $model = $matches[0]
    $rawDigest = [string]$model.digest
    if ($rawDigest -match "^[0-9a-fA-F]{64}$") {
        $canonicalDigest = "sha256:$($rawDigest.ToLowerInvariant())"
    }
    elseif ($rawDigest -match "^sha256:[0-9a-fA-F]{64}$") {
        $canonicalDigest = $rawDigest.ToLowerInvariant()
    }
    else {
        throw "Ollama returned an invalid model digest."
    }

    $metadata = [ordered]@{
        schema_version = "1.0"
        model_name = $ModelName
        model_digest = $canonicalDigest
        model_size = $model.size
        modified_at = $model.modified_at
        details = $model.details
        ollama_image = $OllamaImage
        exported_at = [DateTimeOffset]::UtcNow.ToString("O")
    }
    $metadataPath = Join-Path $modelsDir $LockFileName
    [IO.File]::WriteAllText(
        $metadataPath,
        (($metadata | ConvertTo-Json -Depth 20) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )

    Invoke-Docker @("stop", $ContainerName)
    $containerStarted = $false

    $archiveFileName = "$ArchiveStem.tar"
    $archivePath = Join-Path $modelsDir $archiveFileName
    if (Test-Path -LiteralPath $archivePath) {
        throw "Refusing to overwrite existing model archive: $archivePath"
    }
    Write-Host "Exporting Ollama model volume to $archivePath ..."
    Invoke-Docker @(
        "run", "--rm", "--user", "0",
        "--mount", "type=volume,source=$ModelVolume,target=/from,readonly",
        "--mount", "type=bind,source=$modelsDir,target=/backup",
        "--entrypoint", "tar",
        $AgentImage,
        "-C", "/from", "-cf", "/backup/$archiveFileName", "."
    )

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
    $metadata.archive_file = $archiveFileName
    $metadata.archive_sha256 = "sha256:$hash"
    $metadata.archive_bytes = (Get-Item -LiteralPath $archivePath).Length
    [IO.File]::WriteAllText(
        $metadataPath,
        (($metadata | ConvertTo-Json -Depth 20) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    $hashPath = "$archivePath.sha256"
    [IO.File]::WriteAllText(
        $hashPath,
        "$hash  $archiveFileName$([Environment]::NewLine)",
        [Text.UTF8Encoding]::new($false)
    )

    Write-Host "Model locked: $ModelName@$canonicalDigest"
    Write-Host "Archive: $archivePath"
    Write-Host "SHA256: $hash"
}
finally {
    $remainingContainer = & docker ps -aq --filter "name=^/$ContainerName$"
    if ($remainingContainer) {
        $containerInspect = @(& docker inspect $ContainerName | ConvertFrom-Json)
        if ($LASTEXITCODE -ne 0 -or $containerInspect.Count -ne 1) {
            Write-Warning "Unable to verify container identity; refusing cleanup: $ContainerName"
            return
        }
        $componentLabel = $containerInspect[0].Config.Labels.'trace-g.component'
        $modelLabel = $containerInspect[0].Config.Labels.'trace-g.model.name'
        if ($componentLabel -eq "offline-model-acquisition" -and $modelLabel -eq $ModelName) {
            & docker rm -f $ContainerName *> $null
        }
        else {
            Write-Warning "Container identity changed; refusing cleanup: $ContainerName"
        }
    }
}
