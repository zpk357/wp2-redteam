[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$HostName,
    [Parameter(Mandatory)][ValidateRange(1, 65535)][int]$Port,
    [string]$User = "root",
    [string]$KitRoot = "",
    [string]$RemotePersistRoot = "/data/trace-g",
    [string]$IdentityFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $KitRoot) {
    $KitRoot = Join-Path ([IO.Path]::GetFullPath((Join-Path $repoRoot ".."))) "trace-g-server-kit"
}
$KitRoot = [IO.Path]::GetFullPath($KitRoot)

if ($HostName -notmatch '^[A-Za-z0-9.-]+$') {
    throw "HostName contains unsupported characters"
}
if ($User -notmatch '^[A-Za-z_][A-Za-z0-9_-]*$') {
    throw "User contains unsupported characters"
}
if ($RemotePersistRoot -notmatch '^/[A-Za-z0-9._/-]+$' -or $RemotePersistRoot.Contains("..")) {
    throw "RemotePersistRoot must be a simple absolute Linux path without '..'"
}
if (-not (Test-Path -LiteralPath $KitRoot -PathType Container)) {
    throw "Server kit directory is missing: $KitRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $KitRoot "SHA256SUMS") -PathType Leaf)) {
    throw "Server kit has no SHA256SUMS: $KitRoot"
}
if ($IdentityFile) {
    $IdentityFile = [IO.Path]::GetFullPath($IdentityFile)
    if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) {
        throw "SSH identity file is missing: $IdentityFile"
    }
}
foreach ($commandName in "ssh.exe", "sftp.exe") {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required Windows OpenSSH command is missing: $commandName"
    }
}

$target = "$User@$HostName"
$remoteKit = "$RemotePersistRoot/trace-g-server-kit"
$sshArgs = @("-p", [string]$Port)
$sftpArgs = @("-P", [string]$Port)
if ($IdentityFile) {
    $sshArgs += @("-i", $IdentityFile)
    $sftpArgs += @("-i", $IdentityFile)
}

& ssh.exe @sshArgs $target "mkdir -p '$RemotePersistRoot'"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to create the remote persistent directory"
}

$directoryBatchPath = Join-Path ([IO.Path]::GetTempPath()) "trace-g-sftp-directories-$PID.txt"
$manifestBatchPath = Join-Path ([IO.Path]::GetTempPath()) "trace-g-sftp-manifest-$PID.txt"
$uploadBatchPath = Join-Path ([IO.Path]::GetTempPath()) "trace-g-sftp-upload-$PID.txt"
$fileManifestPath = Join-Path ([IO.Path]::GetTempPath()) "trace-g-sftp-files-$PID.txt"
try {
    $kitPrefix = $KitRoot.TrimEnd("\") + "\"
    $directories = @((Get-Item -LiteralPath $KitRoot)) + @(
        Get-ChildItem -LiteralPath $KitRoot -Directory -Recurse |
            Sort-Object FullName
    )
    $files = @(
        Get-ChildItem -LiteralPath $KitRoot -File -Recurse |
            Sort-Object FullName
    )
    $directoryBatch = [Collections.Generic.List[string]]::new()
    foreach ($directory in $directories) {
        $relative = if ($directory.FullName -eq $KitRoot) { "." } else { $directory.FullName.Substring($kitPrefix.Length).Replace("\", "/") }
        $remoteDirectory = if ($relative -eq ".") { $remoteKit } else { "$remoteKit/$relative" }
        if ($remoteDirectory.Contains('"') -or $remoteDirectory.Contains("`r") -or
            $remoteDirectory.Contains("`n") -or $remoteDirectory.Contains("`t")) {
            throw "Unsupported directory name: $remoteDirectory"
        }
        $directoryBatch.Add("-mkdir `"$remoteDirectory`"")
    }
    [IO.File]::WriteAllLines(
        $directoryBatchPath,
        $directoryBatch,
        [Text.UTF8Encoding]::new($false)
    )
    & sftp.exe @sftpArgs -b $directoryBatchPath $target
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to create the remote server-kit directory tree"
    }

    $relativeFiles = [Collections.Generic.List[string]]::new()
    $uploadBatch = [Collections.Generic.List[string]]::new()
    foreach ($file in $files) {
        $localFile = $file.FullName.Replace("\", "/")
        $relative = $file.FullName.Substring($kitPrefix.Length).Replace("\", "/")
        $remoteFile = "$remoteKit/$relative"
        if ($localFile.Contains('"') -or $localFile.Contains("`r") -or
            $localFile.Contains("`n") -or $localFile.Contains("`t") -or
            $remoteFile.Contains('"') -or $remoteFile.Contains("`r") -or
            $remoteFile.Contains("`n") -or $remoteFile.Contains("`t")) {
            throw "Unsupported file name: $relative"
        }
        $relativeFiles.Add($relative)
        $uploadBatch.Add("put -a `"$localFile`" `"$remoteFile`"")
    }

    # Windows OpenSSH put -a resumes existing files but fails when the target does not exist.
    # Upload a small manifest first, then create only missing zero-byte targets on the server.
    [IO.File]::WriteAllText(
        $fileManifestPath,
        (($relativeFiles -join "`n") + "`n"),
        [Text.UTF8Encoding]::new($false)
    )
    $remoteManifest = "$RemotePersistRoot/.trace-g-upload-files-$PID.txt"
    $manifestLocalFile = $fileManifestPath.Replace("\", "/")
    $manifestBatch = @("put `"$manifestLocalFile`" `"$remoteManifest`"")
    [IO.File]::WriteAllLines(
        $manifestBatchPath,
        $manifestBatch,
        [Text.UTF8Encoding]::new($false)
    )
    & sftp.exe @sftpArgs -b $manifestBatchPath $target
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to upload the server-kit file manifest"
    }

    $prepareScript = 'set -eu; remote_kit="$1"; manifest="$2"; while IFS= read -r relative || [ -n "$relative" ]; do case "$relative" in ""|/*|../*|*/../*|*/..) exit 2 ;; esac; destination="$remote_kit/$relative"; if [ ! -e "$destination" ]; then : > "$destination"; fi; done < "$manifest"; rm -f "$manifest"'
    $prepareCommand = "sh -c '$prepareScript' sh '$remoteKit' '$remoteManifest'"
    & ssh.exe @sshArgs $target $prepareCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to prepare missing remote files for resumable upload"
    }

    [IO.File]::WriteAllLines(
        $uploadBatchPath,
        $uploadBatch,
        [Text.UTF8Encoding]::new($false)
    )
    & sftp.exe @sftpArgs -b $uploadBatchPath $target
    if ($LASTEXITCODE -ne 0) {
        throw "SFTP upload was interrupted. Run this script again to resume."
    }
}
finally {
    foreach ($temporaryPath in @(
        $directoryBatchPath,
        $manifestBatchPath,
        $uploadBatchPath,
        $fileManifestPath
    )) {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
    }
}

& ssh.exe @sshArgs $target "cd '$remoteKit' && sha256sum -c SHA256SUMS"
if ($LASTEXITCODE -ne 0) {
    throw "Remote SHA-256 verification failed"
}

Write-Host "Resumable upload and remote SHA-256 verification completed."
Write-Host "Remote kit: $target`:$remoteKit"

