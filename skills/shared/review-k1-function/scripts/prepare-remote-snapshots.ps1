[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RequestPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [string]$CacheRoot,

    [ValidateRange(1, 180)]
    [int]$TimeoutSeconds = 180
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
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes += 1
            continue
        }
        if ($character -eq [char]34) {
            if ($backslashes -gt 0) {
                $builder.Append(([string][char]92) * ($backslashes * 2)) | Out-Null
            }
            $builder.Append([char]92) | Out-Null
            $builder.Append([char]34) | Out-Null
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            $builder.Append(([string][char]92) * $backslashes) | Out-Null
            $backslashes = 0
        }
        $builder.Append($character) | Out-Null
    }
    if ($backslashes -gt 0) {
        $builder.Append(([string][char]92) * ($backslashes * 2)) | Out-Null
    }
    $builder.Append([char]34) | Out-Null
    return $builder.ToString()
}

function Get-FullPath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Test-IsAtOrBelowBoundary {
    param([string]$Path, [string]$Boundary)
    $fullPath = (Get-FullPath $Path).TrimEnd([char]92, [char]47)
    $fullBoundary = (Get-FullPath $Boundary).TrimEnd([char]92, [char]47)
    return $fullPath.Equals(
        $fullBoundary,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or $fullPath.StartsWith(
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
        [string]$ReparseCode,
        [bool]$AllowBoundary = $false
    )
    $owned = if ($AllowBoundary) {
        Test-IsAtOrBelowBoundary -Path $Path -Boundary $Boundary
    } else {
        $fullPath = Get-FullPath $Path
        $fullBoundary = Get-FullPath $Boundary
        (Test-IsAtOrBelowBoundary -Path $fullPath -Boundary $fullBoundary) -and
            -not $fullPath.TrimEnd([char]92, [char]47).Equals(
                $fullBoundary.TrimEnd([char]92, [char]47),
                [System.StringComparison]::OrdinalIgnoreCase
            )
    }
    if (-not $owned) { throw $OutsideCode }
    Assert-NoReparsePoint -Path $Path -Boundary $Boundary -Code $ReparseCode
}

function Invoke-GitProcess {
    param(
        [string]$Repository,
        [string[]]$Arguments,
        [int]$Timeout,
        [string]$Operation
    )

    $allArguments = @('--no-optional-locks')
    if (-not [string]::IsNullOrWhiteSpace($Repository)) {
        $allArguments += @('-C', $Repository)
    }
    $allArguments += @($Arguments)

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'git'
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
    $startInfo.EnvironmentVariables['GIT_OPTIONAL_LOCKS'] = '0'
    $startInfo.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $startInfo.EnvironmentVariables['GIT_LFS_SKIP_SMUDGE'] = '1'
    $startInfo.EnvironmentVariables['GIT_SSH_COMMAND'] = 'ssh -o BatchMode=yes'

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        $process.Start() | Out-Null
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($Timeout * 1000)) {
            try { $process.Kill() } catch { }
            $process.WaitForExit()
            return [pscustomobject]@{
                ExitCode = -1
                TimedOut = $true
                StdOut = $stdoutTask.Result
                StdErr = $stderrTask.Result
                Operation = $Operation
            }
        }
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            TimedOut = $false
            StdOut = $stdoutTask.Result
            StdErr = $stderrTask.Result
            Operation = $Operation
        }
    } finally {
        $process.Dispose()
    }
}

function Test-SafeBranchName {
    param([string]$Branch)
    if (
        [string]::IsNullOrWhiteSpace($Branch) -or
        $Branch.Length -gt 200 -or
        $Branch -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or
        $Branch.Contains('..') -or
        $Branch.Contains('//') -or
        $Branch.Contains('@{') -or
        $Branch.EndsWith('.') -or
        $Branch.EndsWith('/')
    ) {
        return $false
    }
    $result = Invoke-GitProcess -Repository '' `
        -Arguments @('check-ref-format', '--branch', $Branch) `
        -Timeout 10 -Operation 'check-ref-format'
    return -not $result.TimedOut -and $result.ExitCode -eq 0
}

function Test-UnsafeRemoteUrl {
    param([string]$Url)
    if ([string]::IsNullOrWhiteSpace($Url)) { return $true }
    return $Url -match '^[A-Za-z][A-Za-z0-9+.-]*://[^/@\s]+@'
}

function Write-JsonExclusive {
    param([string]$Path, [object]$Value)
    if (Test-Path -LiteralPath $Path) { throw 'OUTPUT_ALREADY_EXISTS' }
    $parent = [System.IO.Path]::GetDirectoryName((Get-FullPath $Path))
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw 'OUTPUT_PARENT_MISSING'
    }
    $temporary = Join-Path $parent ('.remote-' + [System.Guid]::NewGuid().ToString('N') + '.tmp')
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

function Add-RoleError {
    param([System.Collections.ArrayList]$Errors, [string]$Role, [string]$Code)
    [void]$Errors.Add([pscustomobject]@{ role = $Role; code = $Code })
}

$projectRoot = $userProfile.workspaceRoot
$projectTmp = Get-FullPath (Join-Path $projectRoot '.tmp')
$remoteCacheBoundary = Get-FullPath (Join-Path $projectRoot '.cache\fast-precheck\remote-git')
if ([string]::IsNullOrWhiteSpace($CacheRoot)) {
    $CacheRoot = $remoteCacheBoundary
}

try {
    Assert-OwnedPath -Path $RequestPath -Boundary $projectTmp `
        -OutsideCode 'REQUEST_OUTSIDE_PROJECT_TMP' -ReparseCode 'REQUEST_REPARSE_POINT'
    Assert-OwnedPath -Path $OutputPath -Boundary $projectTmp `
        -OutsideCode 'OUTPUT_OUTSIDE_PROJECT_TMP' -ReparseCode 'OUTPUT_REPARSE_POINT'
    Assert-OwnedPath -Path $CacheRoot -Boundary $remoteCacheBoundary `
        -OutsideCode 'CACHE_OUTSIDE_PROJECT_CACHE' -ReparseCode 'CACHE_REPARSE_POINT' `
        -AllowBoundary $true
    if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) {
        throw 'REQUEST_NOT_FOUND'
    }
    if (Test-Path -LiteralPath $OutputPath) { throw 'OUTPUT_ALREADY_EXISTS' }

    $request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $request.PSObject.Properties['repositories']) {
        throw 'INVALID_REQUEST'
    }

    $requestEntries = @($request.repositories)
    if ($requestEntries.Count -gt 1) {
        $childProcesses = New-Object System.Collections.ArrayList
        $childFiles = New-Object System.Collections.ArrayList
        $requestParent = [System.IO.Path]::GetDirectoryName((Get-FullPath $RequestPath))
        try {
            foreach ($childEntry in $requestEntries) {
                $childId = [System.Guid]::NewGuid().ToString('N')
                $childRequestPath = Join-Path $requestParent ('.remote-child-' + $childId + '-request.json')
                $childOutputPath = Join-Path $requestParent ('.remote-child-' + $childId + '-output.json')
                $childRole = if ($null -ne $childEntry.PSObject.Properties['role']) {
                    [string]$childEntry.role
                } else {
                    ''
                }
                Write-JsonExclusive -Path $childRequestPath -Value ([pscustomobject]@{
                    repositories = @($childEntry)
                })
                [void]$childFiles.Add($childRequestPath)
                [void]$childFiles.Add($childOutputPath)

                $childStartInfo = New-Object System.Diagnostics.ProcessStartInfo
                $childStartInfo.FileName = Join-Path $PSHOME 'powershell.exe'
                $childArguments = @(
                    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
                    '-RequestPath', $childRequestPath,
                    '-OutputPath', $childOutputPath,
                    '-CacheRoot', $CacheRoot,
                    '-TimeoutSeconds', [string]$TimeoutSeconds
                )
                $childStartInfo.Arguments = (@(
                    $childArguments | ForEach-Object { ConvertTo-ProcessArgument $_ }
                ) -join ' ')
                $childStartInfo.UseShellExecute = $false
                $childStartInfo.RedirectStandardOutput = $true
                $childStartInfo.RedirectStandardError = $true
                $childStartInfo.CreateNoWindow = $true
                $utf8 = New-Object System.Text.UTF8Encoding($false)
                $childStartInfo.StandardOutputEncoding = $utf8
                $childStartInfo.StandardErrorEncoding = $utf8

                $childProcess = New-Object System.Diagnostics.Process
                $childProcess.StartInfo = $childStartInfo
                $childProcess.Start() | Out-Null
                [void]$childProcesses.Add([pscustomobject]@{
                    Role = $childRole
                    Process = $childProcess
                    StdOutTask = $childProcess.StandardOutput.ReadToEndAsync()
                    StdErrTask = $childProcess.StandardError.ReadToEndAsync()
                    OutputPath = $childOutputPath
                })
            }

            $mergedRepositories = New-Object System.Collections.ArrayList
            $mergedErrors = New-Object System.Collections.ArrayList
            foreach ($child in @($childProcesses)) {
                $finished = $child.Process.WaitForExit(($TimeoutSeconds + 60) * 1000)
                if (-not $finished) {
                    try { $child.Process.Kill() } catch { }
                    $child.Process.WaitForExit()
                    Add-RoleError -Errors $mergedErrors -Role $child.Role -Code 'REMOTE_FETCH_TIMEOUT'
                    continue
                }
                if (-not (Test-Path -LiteralPath $child.OutputPath -PathType Leaf)) {
                    Add-RoleError -Errors $mergedErrors -Role $child.Role -Code 'REMOTE_FETCH_FAILED'
                    continue
                }
                try {
                    $childPayload = Get-Content -LiteralPath $child.OutputPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    foreach ($childRepository in @($childPayload.repositories)) {
                        [void]$mergedRepositories.Add($childRepository)
                    }
                    foreach ($childError in @($childPayload.errors)) {
                        [void]$mergedErrors.Add($childError)
                    }
                } catch {
                    Add-RoleError -Errors $mergedErrors -Role $child.Role -Code 'REMOTE_FETCH_FAILED'
                }
            }

            $mergedStatus = if ($mergedRepositories.Count -gt 0 -and $mergedErrors.Count -eq 0) {
                'ok'
            } elseif ($mergedRepositories.Count -gt 0) {
                'partial'
            } else {
                'error'
            }
            Write-JsonExclusive -Path $OutputPath -Value ([pscustomobject][ordered]@{
                schemaVersion = 1
                status = $mergedStatus
                repositories = @($mergedRepositories)
                errors = @($mergedErrors)
            })
            if ($mergedStatus -eq 'ok') { exit 0 }
            if ($mergedStatus -eq 'partial') { exit 2 }
            exit 1
        } finally {
            foreach ($child in @($childProcesses)) {
                if ($null -ne $child.Process) { $child.Process.Dispose() }
            }
            foreach ($childFile in @($childFiles)) {
                if (Test-Path -LiteralPath $childFile -PathType Leaf) {
                    Remove-Item -LiteralPath $childFile -Force
                }
            }
        }
    }

    if (-not (Test-Path -LiteralPath $remoteCacheBoundary -PathType Container)) {
        New-Item -ItemType Directory -Path $remoteCacheBoundary -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $CacheRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $CacheRoot -Force | Out-Null
    }

    $officialPaths = $userProfile.repositories
    $repositories = New-Object System.Collections.ArrayList
    $errors = New-Object System.Collections.ArrayList

    foreach ($entry in @($request.repositories)) {
        $role = if ($null -ne $entry.PSObject.Properties['role']) { [string]$entry.role } else { '' }
        $localRepository = if ($null -ne $entry.PSObject.Properties['localRepository']) { [string]$entry.localRepository } else { '' }
        $remote = if ($null -ne $entry.PSObject.Properties['remote']) { [string]$entry.remote } else { '' }
        $baseBranch = if ($null -ne $entry.PSObject.Properties['baseBranch']) { [string]$entry.baseBranch } else { '' }
        $targetBranch = if ($null -ne $entry.PSObject.Properties['targetBranch']) { [string]$entry.targetBranch } else { '' }

        if (-not $officialPaths.ContainsKey($role)) {
            Add-RoleError -Errors $errors -Role $role -Code 'INVALID_REPOSITORY_ROLE'
            continue
        }
        if ([string]::IsNullOrWhiteSpace($localRepository) -or -not (Test-Path -LiteralPath $localRepository -PathType Container)) {
            Add-RoleError -Errors $errors -Role $role -Code 'REPOSITORY_NOT_FOUND'
            continue
        }
        $localRepository = Get-FullPath $localRepository
        $isOfficial = $localRepository.TrimEnd([char]92, [char]47).Equals(
            $officialPaths[$role].TrimEnd([char]92, [char]47),
            [System.StringComparison]::OrdinalIgnoreCase
        )
        $isFixture = Test-IsAtOrBelowBoundary -Path $localRepository -Boundary $projectTmp
        if (-not $isOfficial -and -not $isFixture) {
            Add-RoleError -Errors $errors -Role $role -Code 'REPOSITORY_NOT_ALLOWED'
            continue
        }
        if ($remote -cne 'origin') {
            Add-RoleError -Errors $errors -Role $role -Code 'INVALID_REMOTE'
            continue
        }
        if (-not (Test-SafeBranchName $baseBranch) -or -not (Test-SafeBranchName $targetBranch)) {
            Add-RoleError -Errors $errors -Role $role -Code 'INVALID_REMOTE_BRANCH'
            continue
        }

        $urlResult = Invoke-GitProcess -Repository $localRepository `
            -Arguments @('remote', 'get-url', 'origin') -Timeout 10 -Operation 'remote-url'
        $objectsResult = Invoke-GitProcess -Repository $localRepository `
            -Arguments @('rev-parse', '--git-path', 'objects') -Timeout 10 -Operation 'objects-path'
        if (
            $urlResult.TimedOut -or $objectsResult.TimedOut -or
            $urlResult.ExitCode -ne 0 -or $objectsResult.ExitCode -ne 0
        ) {
            Add-RoleError -Errors $errors -Role $role -Code 'REMOTE_FETCH_FAILED'
            continue
        }
        $remoteUrl = $urlResult.StdOut.Trim()
        if (Test-UnsafeRemoteUrl $remoteUrl) {
            Add-RoleError -Errors $errors -Role $role -Code 'REMOTE_URL_UNSAFE'
            continue
        }
        if ($isFixture) {
            $fixtureUri = $null
            if (-not [System.Uri]::TryCreate($remoteUrl, [System.UriKind]::Absolute, [ref]$fixtureUri) -or -not $fixtureUri.IsFile) {
                Add-RoleError -Errors $errors -Role $role -Code 'REMOTE_URL_UNSAFE'
                continue
            }
            $fixtureRemotePath = Get-FullPath $fixtureUri.LocalPath
            if (-not (Test-IsAtOrBelowBoundary -Path $fixtureRemotePath -Boundary $projectTmp)) {
                Add-RoleError -Errors $errors -Role $role -Code 'REMOTE_URL_UNSAFE'
                continue
            }
        }

        $roleCache = Get-FullPath (Join-Path $CacheRoot $role)
        Assert-OwnedPath -Path $roleCache -Boundary $remoteCacheBoundary `
            -OutsideCode 'CACHE_OUTSIDE_PROJECT_CACHE' -ReparseCode 'CACHE_REPARSE_POINT'
        $lockPath = Join-Path $CacheRoot ('.' + $role + '.lock')
        $lockStream = $null
        $lockOwned = $false
        try {
            try {
                $lockStream = New-Object System.IO.FileStream(
                    $lockPath,
                    [System.IO.FileMode]::CreateNew,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::None
                )
                $lockOwned = $true
            } catch [System.IO.IOException] {
                Add-RoleError -Errors $errors -Role $role -Code 'CACHE_LOCK_TIMEOUT'
                continue
            }

            if (-not (Test-Path -LiteralPath $roleCache -PathType Container)) {
                New-Item -ItemType Directory -Path $roleCache -Force | Out-Null
                $initResult = Invoke-GitProcess -Repository '' `
                    -Arguments @('init', '--bare', $roleCache) -Timeout 30 -Operation 'cache-init'
                if ($initResult.TimedOut -or $initResult.ExitCode -ne 0) {
                    Add-RoleError -Errors $errors -Role $role -Code 'REMOTE_FETCH_FAILED'
                    continue
                }
            }

            $objectsPath = $objectsResult.StdOut.Trim()
            if (-not [System.IO.Path]::IsPathRooted($objectsPath)) {
                $objectsPath = Join-Path $localRepository $objectsPath
            }
            $objectsPath = Get-FullPath $objectsPath
            $alternatesPath = Join-Path $roleCache 'objects\info\alternates'
            $alternatesParent = Split-Path -Parent $alternatesPath
            if (-not (Test-Path -LiteralPath $alternatesParent -PathType Container)) {
                New-Item -ItemType Directory -Path $alternatesParent -Force | Out-Null
            }
            [System.IO.File]::WriteAllText(
                $alternatesPath,
                $objectsPath.Replace([char]92, [char]47) + [Environment]::NewLine,
                (New-Object System.Text.UTF8Encoding($false))
            )

            $getCacheUrl = Invoke-GitProcess -Repository $roleCache `
                -Arguments @('remote', 'get-url', 'origin') -Timeout 10 -Operation 'cache-remote-read'
            $remoteArgs = if ($getCacheUrl.ExitCode -eq 0) {
                @('remote', 'set-url', 'origin', $remoteUrl)
            } else {
                @('remote', 'add', 'origin', $remoteUrl)
            }
            $setupCommands = @(
                $remoteArgs,
                @('config', 'remote.origin.promisor', 'true'),
                @('config', 'remote.origin.partialclonefilter', 'blob:none')
            )
            $setupFailed = $false
            foreach ($setupArgs in $setupCommands) {
                $setupResult = Invoke-GitProcess -Repository $roleCache `
                    -Arguments $setupArgs -Timeout 10 -Operation 'cache-config'
                if ($setupResult.TimedOut -or $setupResult.ExitCode -ne 0) {
                    $setupFailed = $true
                    break
                }
            }
            if ($setupFailed) {
                Add-RoleError -Errors $errors -Role $role -Code 'REMOTE_FETCH_FAILED'
                continue
            }

            $baseRef = "refs/remotes/origin/$baseBranch"
            $targetRef = "refs/remotes/origin/$targetBranch"
            $fetchResult = Invoke-GitProcess -Repository $roleCache `
                -Arguments @(
                    'fetch', '--force', '--no-tags', '--filter=blob:none', 'origin',
                    "+refs/heads/${baseBranch}:$baseRef",
                    "+refs/heads/${targetBranch}:$targetRef"
                ) -Timeout $TimeoutSeconds -Operation 'fetch'
            if ($fetchResult.TimedOut) {
                Add-RoleError -Errors $errors -Role $role -Code 'REMOTE_FETCH_TIMEOUT'
                continue
            }
            if ($fetchResult.ExitCode -ne 0) {
                $lsResult = Invoke-GitProcess -Repository $roleCache `
                    -Arguments @('ls-remote', '--heads', 'origin', "refs/heads/$baseBranch", "refs/heads/$targetBranch") `
                    -Timeout ([Math]::Min($TimeoutSeconds, 30)) -Operation 'ls-remote'
                $remoteRefs = if ($lsResult.ExitCode -eq 0) { $lsResult.StdOut } else { '' }
                $basePresent = $remoteRefs -match "(?m)\trefs/heads/$([regex]::Escape($baseBranch))$"
                $targetPresent = $remoteRefs -match "(?m)\trefs/heads/$([regex]::Escape($targetBranch))$"
                $code = if (-not $basePresent -or -not $targetPresent) {
                    'REMOTE_BRANCH_MISSING'
                } else {
                    'REMOTE_FETCH_FAILED'
                }
                Add-RoleError -Errors $errors -Role $role -Code $code
                continue
            }
            if ($fetchResult.StdErr -match '(?i)filtering not recognized|does not support filter') {
                Add-RoleError -Errors $errors -Role $role -Code 'REMOTE_FILTER_UNSUPPORTED'
                continue
            }

            $baseResult = Invoke-GitProcess -Repository $roleCache `
                -Arguments @('rev-parse', '--verify', "$baseRef^{commit}") -Timeout 10 -Operation 'base-sha'
            $targetResult = Invoke-GitProcess -Repository $roleCache `
                -Arguments @('rev-parse', '--verify', "$targetRef^{commit}") -Timeout 10 -Operation 'target-sha'
            if ($baseResult.ExitCode -ne 0 -or $targetResult.ExitCode -ne 0) {
                Add-RoleError -Errors $errors -Role $role -Code 'REMOTE_FETCH_FAILED'
                continue
            }
            [void]$repositories.Add([pscustomobject][ordered]@{
                role = $role
                source = 'remote'
                repository = $roleCache
                remote = 'origin'
                baseRef = $baseRef
                targetRef = $targetRef
                baseSha = $baseResult.StdOut.Trim()
                targetSha = $targetResult.StdOut.Trim()
            })
        } finally {
            if ($null -ne $lockStream) { $lockStream.Dispose() }
            if ($lockOwned -and (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
                Remove-Item -LiteralPath $lockPath -Force
            }
        }
    }

    $status = if ($repositories.Count -gt 0 -and $errors.Count -eq 0) {
        'ok'
    } elseif ($repositories.Count -gt 0) {
        'partial'
    } else {
        'error'
    }
    $payload = [pscustomobject][ordered]@{
        schemaVersion = 1
        status = $status
        repositories = @($repositories)
        errors = @($errors)
    }
    Write-JsonExclusive -Path $OutputPath -Value $payload
    if ($status -eq 'ok') { exit 0 }
    if ($status -eq 'partial') { exit 2 }
    exit 1
} catch {
    [Console]::Error.WriteLine([string]$_.Exception.Message)
    exit 1
}
