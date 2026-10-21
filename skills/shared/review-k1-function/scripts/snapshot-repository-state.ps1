[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RequestPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'user-config.ps1')
try { $userProfile = Get-K1UserProfile } catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function ConvertTo-ProcessArgument {
    param([string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

    $builder = New-Object System.Text.StringBuilder
    $builder.Append([char]34) | Out-Null
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $slashes += 1
            continue
        }
        if ($character -eq [char]34) {
            if ($slashes -gt 0) {
                $builder.Append(([string][char]92) * ($slashes * 2)) | Out-Null
            }
            $builder.Append([char]92) | Out-Null
            $builder.Append([char]34) | Out-Null
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) {
            $builder.Append(([string][char]92) * $slashes) | Out-Null
            $slashes = 0
        }
        $builder.Append($character) | Out-Null
    }
    if ($slashes -gt 0) {
        $builder.Append(([string][char]92) * ($slashes * 2)) | Out-Null
    }
    $builder.Append([char]34) | Out-Null
    return $builder.ToString()
}

function Invoke-ReadOnlyGit {
    param(
        [string]$Repository,
        [string[]]$Arguments,
        [string]$Operation
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'git'
    $allArguments = @('--no-optional-locks', '-C', $Repository) + $Arguments
    $startInfo.Arguments = (@(
        $allArguments | ForEach-Object { ConvertTo-ProcessArgument $_ }
    ) -join ' ')
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $startInfo.StandardOutputEncoding = $utf8
    $startInfo.StandardErrorEncoding = $utf8

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        $process.Start() | Out-Null
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            StdOut = $stdoutTask.Result
            StdErr = $stderrTask.Result
            Operation = $Operation
        }
    } finally {
        $process.Dispose()
    }
}

function Get-Sha256Text {
    param([string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-Sha256File {
    param([string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Get-FullPath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Test-IsBelowBoundary {
    param([string]$Path, [string]$Boundary)
    $fullPath = (Get-FullPath $Path).TrimEnd([char]92, [char]47)
    $fullBoundary = (Get-FullPath $Boundary).TrimEnd([char]92, [char]47)
    return $fullPath.StartsWith(
        $fullBoundary + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-NoReparsePoint {
    param([string]$Path, [string]$Boundary, [string]$Code)
    $fullPath = Get-FullPath $Path
    $fullBoundary = (Get-FullPath $Boundary).TrimEnd([char]92, [char]47)
    $relative = $fullPath.Substring($fullBoundary.Length).TrimStart([char]92, [char]47)
    $current = $fullBoundary
    foreach ($segment in @($relative -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($segment)) { continue }
        $current = Join-Path $current $segment
        if (-not (Test-Path -LiteralPath $current)) { break }
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw $Code
        }
    }
}

function Assert-OwnedPath {
    param(
        [string]$Path,
        [string]$Boundary,
        [string]$OutsideCode,
        [string]$ReparseCode
    )
    if (-not (Test-IsBelowBoundary -Path $Path -Boundary $Boundary)) {
        throw $OutsideCode
    }
    Assert-NoReparsePoint -Path $Path -Boundary $Boundary -Code $ReparseCode
}

function Get-StatusSummary {
    param([string]$Porcelain)
    $counts = @{}
    $entryCount = 0
    $records = @($Porcelain -split "`0")
    $index = 0
    while ($index -lt $records.Count) {
        $record = $records[$index]
        if ($record.Length -lt 3) {
            $index += 1
            continue
        }
        $category = $record.Substring(0, 2)
        if (-not $counts.ContainsKey($category)) { $counts[$category] = 0 }
        $counts[$category] += 1
        $entryCount += 1
        if ($category.Contains('R') -or $category.Contains('C')) {
            $index += 1
        }
        $index += 1
    }

    $ordered = [ordered]@{}
    foreach ($key in @($counts.Keys | Sort-Object)) {
        $ordered[$key] = $counts[$key]
    }
    return [pscustomobject]@{
        EntryCount = $entryCount
        Categories = [pscustomobject]$ordered
    }
}

function Write-JsonExclusive {
    param([string]$Path, [object]$Value)
    if (Test-Path -LiteralPath $Path) { throw 'OUTPUT_ALREADY_EXISTS' }
    $parent = [System.IO.Path]::GetDirectoryName((Get-FullPath $Path))
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw 'OUTPUT_PARENT_MISSING'
    }
    $temporary = Join-Path $parent ('.state-' + [System.Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $json = $Value | ConvertTo-Json -Depth 12
        [System.IO.File]::WriteAllText(
            $temporary,
            $json + [Environment]::NewLine,
            (New-Object System.Text.UTF8Encoding($false))
        )
        [System.IO.File]::Move($temporary, (Get-FullPath $Path))
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

$projectRoot = $userProfile.workspaceRoot
$projectTmp = Get-FullPath (Join-Path $projectRoot '.tmp')

try {
    Assert-OwnedPath -Path $RequestPath -Boundary $projectTmp `
        -OutsideCode 'REQUEST_OUTSIDE_PROJECT_TMP' -ReparseCode 'REQUEST_REPARSE_POINT'
    Assert-OwnedPath -Path $OutputPath -Boundary $projectTmp `
        -OutsideCode 'OUTPUT_OUTSIDE_PROJECT_TMP' -ReparseCode 'OUTPUT_REPARSE_POINT'
    if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) {
        throw 'REQUEST_NOT_FOUND'
    }
    if (Test-Path -LiteralPath $OutputPath) { throw 'OUTPUT_ALREADY_EXISTS' }

    $request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $request.PSObject.Properties['repositories']) {
        throw 'INVALID_REQUEST'
    }

    $repositories = @()
    $errors = @()
    foreach ($entry in @($request.repositories)) {
        $role = if ($null -ne $entry.PSObject.Properties['role']) { [string]$entry.role } else { '' }
        $repository = if ($null -ne $entry.PSObject.Properties['path']) { [string]$entry.path } else { '' }
        if ([string]::IsNullOrWhiteSpace($role) -or [string]::IsNullOrWhiteSpace($repository)) {
            $errors += [pscustomobject]@{ role = $role; code = 'INVALID_REPOSITORY_ENTRY' }
            continue
        }
        if (-not (Test-Path -LiteralPath $repository -PathType Container)) {
            $errors += [pscustomobject]@{ role = $role; code = 'REPOSITORY_NOT_FOUND' }
            continue
        }

        $branchResult = Invoke-ReadOnlyGit -Repository $repository `
            -Arguments @('symbolic-ref', '--short', '-q', 'HEAD') -Operation 'branch'
        $headResult = Invoke-ReadOnlyGit -Repository $repository `
            -Arguments @('rev-parse', 'HEAD') -Operation 'head'
        $indexResult = Invoke-ReadOnlyGit -Repository $repository `
            -Arguments @('rev-parse', '--git-path', 'index') -Operation 'index-path'
        $statusResult = Invoke-ReadOnlyGit -Repository $repository `
            -Arguments @('status', '--porcelain=v1', '-z', '--untracked-files=all') -Operation 'status'

        $failed = @(
            @($headResult, $indexResult, $statusResult) |
                Where-Object { $_.ExitCode -ne 0 }
        )
        if ($failed.Count -gt 0) {
            $errors += [pscustomobject]@{
                role = $role
                code = 'GIT_STATE_READ_FAILED'
                operation = $failed[0].Operation
            }
            continue
        }

        $indexPath = $indexResult.StdOut.Trim()
        if (-not [System.IO.Path]::IsPathRooted($indexPath)) {
            $indexPath = Join-Path $repository $indexPath
        }
        $indexSha = if (Test-Path -LiteralPath $indexPath -PathType Leaf) {
            Get-Sha256File $indexPath
        } else {
            Get-Sha256Text ''
        }
        $statusSha = Get-Sha256Text $statusResult.StdOut
        $summary = Get-StatusSummary $statusResult.StdOut
        $branch = if ($branchResult.ExitCode -eq 0) { $branchResult.StdOut.Trim() } else { $null }
        $head = $headResult.StdOut.Trim()
        $digestInput = [ordered]@{
            branch = $branch
            head = $head
            indexSha256 = $indexSha
            statusSha256 = $statusSha
            entryCount = $summary.EntryCount
            categories = $summary.Categories
        }
        $digestJson = $digestInput | ConvertTo-Json -Compress -Depth 6
        $repositories += [pscustomobject]@{
            role = $role
            branch = $branch
            head = $head
            indexSha256 = $indexSha
            statusSha256 = $statusSha
            dirty = ($summary.EntryCount -gt 0)
            entryCount = $summary.EntryCount
            categories = $summary.Categories
            digest = Get-Sha256Text $digestJson
        }
    }

    $status = if ($errors.Count -eq 0) { 'ok' } else { 'error' }
    $result = [pscustomobject]@{
        schemaVersion = 1
        status = $status
        repositories = $repositories
        errors = $errors
    }
    Write-JsonExclusive -Path $OutputPath -Value $result
    if ($errors.Count -gt 0) { exit 1 }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
