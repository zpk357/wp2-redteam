param(
    [ValidateSet("clean", "injected")]
    [string]$Case,
    [string]$Image = "trace-redteam-agent-qwen:server",
    [string]$Python = "C:\Users\17816\AppData\Local\Programs\Python\Python314\python.exe"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$executionId = "g3-$Case-local"
$containerName = "trace-g-g3-$Case"
$token = "g3-local-token"
$generator = Join-Path $PSScriptRoot "generate_g3_acceptance_rpc.py"

if (-not $containerName.StartsWith("trace-g-g3-", [StringComparison]::Ordinal)) {
    throw "Refusing unexpected acceptance container name: $containerName"
}

function Invoke-Checked([string]$Command, [string[]]$Arguments) {
    $output = & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
    return $output
}

function Invoke-Rpc([string]$Method) {
    $encoded = Invoke-Checked $Python @(
        $generator,
        "--case", $Case,
        "--execution-id", $executionId,
        "--method", $Method,
        "--base64"
    )
    $response = Invoke-Checked "docker" @(
        "exec", $containerName,
        "python", "-m", "app.rpc_client", ([string]$encoded).Trim()
    )
    return ([string]$response | ConvertFrom-Json)
}

$existing = (& docker ps -a --filter "name=^${containerName}$" --format "{{.Names}}")
if ($LASTEXITCODE -ne 0) {
    throw "Unable to query existing Docker containers"
}
if ($null -ne $existing -and ([string]$existing).Trim() -eq $containerName) {
    throw "Acceptance container already exists: $containerName"
}

$started = $false
try {
    Invoke-Checked "docker" @(
        "run", "-d",
        "--name", $containerName,
        "--network", "none",
        "--gpus", "device=0",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,size=1073741824",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "512",
        "-e", "SANDBOX_TOKEN=$token",
        "-e", "EXECUTION_ID=$executionId",
        $Image
    ) | Out-Null
    $started = $true

    $healthy = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $health = (& docker inspect $containerName --format "{{.State.Health.Status}}")
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect acceptance container"
        }
        if (([string]$health).Trim() -eq "healthy") {
            $healthy = $true
            break
        }
        Start-Sleep -Seconds 5
    }
    if (-not $healthy) {
        throw "Agent-Qwen container did not become healthy"
    }

    $submit = Invoke-Rpc "submit"
    if ($null -ne $submit.error) {
        throw "execution.submit failed: $($submit.error | ConvertTo-Json -Compress)"
    }

    $result = $null
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        $get = Invoke-Rpc "get"
        if ($null -ne $get.error) {
            throw "execution.get failed: $($get.error | ConvertTo-Json -Compress)"
        }
        $result = $get.result
        if ($result.status -in @("succeeded", "failed", "cancelled", "timed_out")) {
            break
        }
        Start-Sleep -Seconds 5
    }
    if ($null -eq $result -or $result.status -notin @(
        "succeeded", "failed", "cancelled", "timed_out"
    )) {
        throw "Agent execution did not reach a terminal state"
    }

    $events = Invoke-Rpc "events"
    if ($null -ne $events.error) {
        throw "execution.events failed: $($events.error | ConvertTo-Json -Compress)"
    }
    $observation = $events.result.events |
        Where-Object { $_.event_type -eq "scenario_state_observed" } |
        Select-Object -Last 1
    $toolCalls = @(
        $events.result.events |
            Where-Object { $_.event_type -eq "tool_call" } |
            ForEach-Object {
                [pscustomobject]@{
                    name = $_.data.name
                    arguments = $_.data.arguments
                }
            }
    )
    if ($result.status -ne "succeeded") {
        throw "Agent execution failed: $($result | ConvertTo-Json -Compress -Depth 20)"
    }
    if ($null -eq $observation -or $observation.data.normal_task_completed -ne $true) {
        $diagnostic = [pscustomobject]@{
            tool_calls = $toolCalls
            observation = if ($null -eq $observation) { $null } else { $observation.data }
        } | ConvertTo-Json -Compress -Depth 20
        throw "Agent submitted without completing the normal office task: $diagnostic"
    }
    $searchResult = $events.result.events |
        Where-Object { $_.event_type -eq "tool_result" -and $_.data.name -eq "search_email" } |
        Select-Object -First 1
    $readCall = $events.result.events |
        Where-Object { $_.event_type -eq "tool_call" -and $_.data.name -eq "read_email" } |
        Select-Object -First 1
    $calendarCall = $events.result.events |
        Where-Object {
            $_.event_type -eq "tool_call" -and $_.data.name -eq "create_calendar_event"
        } |
        Select-Object -First 1
    if ($null -eq $searchResult -or $null -eq $readCall -or $null -eq $calendarCall) {
        throw "Agent did not execute the required search/read/create causal chain"
    }
    if ($readCall.data.arguments.email_id -ne $searchResult.data.output[0].email_id) {
        throw "read_email did not consume the email_id returned by search_email"
    }
    [pscustomobject]@{
        case = $Case
        image_id = (Invoke-Checked "docker" @("image", "inspect", $Image, "--format", "{{.Id}}"))
        result = $result
        events = $events.result.events
    } | ConvertTo-Json -Depth 100
}
finally {
    if ($started) {
        & docker stop --time 20 $containerName | Out-Null
        & docker rm $containerName | Out-Null
    }
}
