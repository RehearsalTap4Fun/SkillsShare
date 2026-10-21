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
$script:CurrentGitWarnings = @()

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

function Invoke-Git {
    param(
        [string]$Repository,
        [string[]]$Arguments,
        [string]$Operation
    )

    $allArguments = @('--no-optional-locks', '-C', $Repository) + @($Arguments)
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

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        $process.Start() | Out-Null
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        $exitCode = $process.ExitCode
    } finally {
        $process.Dispose()
    }

    if ($exitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($stderr)) {
        $script:CurrentGitWarnings += [pscustomobject]@{
            code = 'GIT_STDERR_WARNING'
            operation = $Operation
            message = $stderr.Trim()
        }
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        StdOut = $stdout
        StdErr = $stderr
    }
}

function Test-IsBelowBoundary {
    param([string]$Path, [string]$Boundary)
    $prefix = $Boundary.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    return $Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-PathEntryStatus {
    param([string]$Path)
    $parent = [System.IO.Path]::GetDirectoryName($Path)
    if ([string]::IsNullOrEmpty($parent)) {
        return [pscustomobject]@{ State = 'missing'; Attributes = $null }
    }
    $name = [System.IO.Path]::GetFileName($Path)
    try {
        $entry = @(
            Get-ChildItem -LiteralPath $parent -Force -ErrorAction Stop |
                Where-Object {
                    $_.Name.Equals($name, [System.StringComparison]::OrdinalIgnoreCase)
                } |
                Select-Object -First 1
        )
    } catch {
        return [pscustomobject]@{ State = 'error'; Attributes = $null }
    }
    if ($entry.Count -eq 0) {
        return [pscustomobject]@{ State = 'missing'; Attributes = $null }
    }
    return [pscustomobject]@{ State = 'ok'; Attributes = $entry[0].Attributes }
}

function Test-PathForReparsePoint {
    param([string]$Path, [string]$Boundary)
    $current = $Path

    while ($null -ne $current) {
        $insideBoundary = (Test-IsBelowBoundary $current $Boundary) -or
            $current.Equals($Boundary, [System.StringComparison]::OrdinalIgnoreCase)
        if (-not $insideBoundary) { break }
        $entryStatus = Get-PathEntryStatus $current
        if ($entryStatus.State -eq 'error') {
            return [pscustomobject]@{ State = 'error'; ContainsReparsePoint = $false }
        }
        if (
            $entryStatus.State -eq 'ok' -and
            ($entryStatus.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            return [pscustomobject]@{ State = 'ok'; ContainsReparsePoint = $true }
        }
        if ($current.Equals($Boundary, [System.StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $current = [System.IO.Path]::GetDirectoryName($current)
    }
    return [pscustomobject]@{ State = 'ok'; ContainsReparsePoint = $false }
}

function Get-CommitSha {
    param([string]$Repository, [string]$Ref)
    $script:LastGitErrorText = $null
    $result = Invoke-Git `
        -Repository $Repository `
        -Arguments @('rev-parse', '--verify', '--quiet', "$Ref^{commit}") `
        -Operation 'rev-parse'
    $lines = @($result.StdOut -split '\r?\n')
    if ($result.ExitCode -ne 0 -or $lines.Count -eq 0 -or [string]::IsNullOrWhiteSpace($lines[0])) {
        if (-not [string]::IsNullOrWhiteSpace($result.StdErr)) {
            $script:LastGitErrorText = $result.StdErr.Trim()
        }
        return $null
    }
    return $lines[0].Trim()
}

function ConvertTo-ExactRef {
    param([string]$Requested)
    if ($Requested.StartsWith('origin/')) {
        return "refs/remotes/$Requested"
    }
    return $Requested
}

function Test-IsFullCommitSha {
    param([string]$Requested)
    return $null -ne $Requested -and $Requested -cmatch '^[0-9a-fA-F]{40}$'
}

function Resolve-CommitObject {
    param(
        [string]$Repository,
        [string]$Requested,
        [string]$MissingCode,
        [bool]$IncludeWarning
    )
    $sha = Get-CommitSha $Repository $Requested
    $errorText = $script:LastGitErrorText
    if ($null -eq $sha) {
        $code = if ($null -ne $errorText) { 'GIT_REV_PARSE_FAILED' } else { $MissingCode }
        return [pscustomobject]@{
            State = 'error'
            Code = $code
            Requested = $Requested
            ErrorText = $errorText
        }
    }

    $resolved = [ordered]@{
        State = 'ok'
        Requested = $Requested
        ResolvedRef = "object:$sha"
        Sha = $sha
    }
    if ($IncludeWarning) {
        $resolved.Warning = $null
    }
    $resolved.ErrorText = $null
    return [pscustomobject]$resolved
}

function Resolve-BaseRef {
    param([string]$Repository, [string]$Requested)
    if (Test-IsFullCommitSha $Requested) {
        return Resolve-CommitObject $Repository $Requested 'BASE_OBJECT_MISSING' $true
    }
    if ($Requested.StartsWith('refs/') -or $Requested.StartsWith('origin/')) {
        $exact = ConvertTo-ExactRef $Requested
        $sha = Get-CommitSha $Repository $exact
        $errorText = $script:LastGitErrorText
        if ($null -eq $sha) {
            $code = if ($null -ne $errorText) { 'GIT_REV_PARSE_FAILED' } else { 'BASE_REF_MISSING' }
            return [pscustomobject]@{
                State = 'error'
                Code = $code
                Requested = $Requested
                ErrorText = $errorText
            }
        }
        return [pscustomobject]@{
            State = 'ok'
            Requested = $Requested
            ResolvedRef = $exact
            Sha = $sha
            Warning = $null
            ErrorText = $null
        }
    }

    $localRef = "refs/heads/$Requested"
    $remoteRef = "refs/remotes/origin/$Requested"
    $localSha = Get-CommitSha $Repository $localRef
    $localError = $script:LastGitErrorText
    $remoteSha = Get-CommitSha $Repository $remoteRef
    $remoteError = $script:LastGitErrorText
    $errorText = @(
        @($localError, $remoteError) |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            Select-Object -Unique
    )
    if ($errorText.Count -gt 0) {
        return [pscustomobject]@{
            State = 'error'
            Code = 'GIT_REV_PARSE_FAILED'
            Requested = $Requested
            ErrorText = ($errorText -join [System.Environment]::NewLine)
        }
    }
    if ($null -ne $localSha) {
        $warning = if ($null -ne $remoteSha -and $remoteSha -ne $localSha) { 'BASE_REMOTE_DIVERGED' } else { $null }
        return [pscustomobject]@{
            State = 'ok'
            Requested = $Requested
            ResolvedRef = $localRef
            Sha = $localSha
            Warning = $warning
            ErrorText = $null
        }
    }
    if ($null -ne $remoteSha) {
        return [pscustomobject]@{
            State = 'ok'
            Requested = $Requested
            ResolvedRef = $remoteRef
            Sha = $remoteSha
            Warning = $null
            ErrorText = $null
        }
    }
    return [pscustomobject]@{
        State = 'error'
        Code = 'BASE_REF_MISSING'
        Requested = $Requested
        ErrorText = $null
    }
}

function Resolve-TargetRef {
    param([string]$Repository, [string]$Requested)
    if (Test-IsFullCommitSha $Requested) {
        return Resolve-CommitObject $Repository $Requested 'TARGET_OBJECT_MISSING' $false
    }
    if ($Requested.StartsWith('refs/') -or $Requested.StartsWith('origin/')) {
        $exact = ConvertTo-ExactRef $Requested
        $sha = Get-CommitSha $Repository $exact
        $errorText = $script:LastGitErrorText
        if ($null -eq $sha) {
            $code = if ($null -ne $errorText) { 'GIT_REV_PARSE_FAILED' } else { 'TARGET_REF_MISSING' }
            return [pscustomobject]@{
                State = if ($code -eq 'TARGET_REF_MISSING') { 'requires_input' } else { 'error' }
                Code = $code
                Requested = $Requested
                ErrorText = $errorText
            }
        }
        return [pscustomobject]@{
            State = 'ok'
            Requested = $Requested
            ResolvedRef = $exact
            Sha = $sha
            ErrorText = $null
        }
    }

    $localRef = "refs/heads/$Requested"
    $remoteRef = "refs/remotes/origin/$Requested"
    $localSha = Get-CommitSha $Repository $localRef
    $localError = $script:LastGitErrorText
    $remoteSha = Get-CommitSha $Repository $remoteRef
    $remoteError = $script:LastGitErrorText
    $errorText = @(
        @($localError, $remoteError) |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            Select-Object -Unique
    )
    if ($errorText.Count -gt 0) {
        return [pscustomobject]@{
            State = 'error'
            Code = 'GIT_REV_PARSE_FAILED'
            Requested = $Requested
            ErrorText = ($errorText -join [System.Environment]::NewLine)
        }
    }
    if ($null -ne $localSha -and $null -ne $remoteSha -and $localSha -ne $remoteSha) {
        return [pscustomobject]@{
            State = 'requires_input'
            Code = 'TARGET_REF_AMBIGUOUS'
            Requested = $Requested
            Candidates = @(
                [pscustomobject]@{ Ref = $localRef; Sha = $localSha },
                [pscustomobject]@{ Ref = $remoteRef; Sha = $remoteSha }
            )
            ErrorText = $null
        }
    }
    if ($null -ne $localSha) {
        return [pscustomobject]@{
            State = 'ok'
            Requested = $Requested
            ResolvedRef = $localRef
            Sha = $localSha
            ErrorText = $null
        }
    }
    if ($null -ne $remoteSha) {
        return [pscustomobject]@{
            State = 'ok'
            Requested = $Requested
            ResolvedRef = $remoteRef
            Sha = $remoteSha
            ErrorText = $null
        }
    }
    return [pscustomobject]@{
        State = 'requires_input'
        Code = 'TARGET_REF_MISSING'
        Requested = $Requested
        ErrorText = $null
    }
}

function Test-MissingTargetContext {
    param(
        [string]$Repository,
        [object]$Requested
    )
    if ($Requested -isnot [string]) {
        return [pscustomobject]@{
            State = 'invalid'
            Code = 'INVALID_REQUESTED_TARGET_CONTEXT'
            Requested = $null
            ErrorText = $null
        }
    }

    $requestedText = [string]$Requested
    if (
        [string]::IsNullOrWhiteSpace($requestedText) -or
        $requestedText -ne $requestedText.Trim() -or
        $requestedText.IndexOf([char]0) -ge 0 -or
        (Test-IsFullCommitSha $requestedText)
    ) {
        return [pscustomobject]@{
            State = 'invalid'
            Code = 'INVALID_REQUESTED_TARGET_CONTEXT'
            Requested = $requestedText
            ErrorText = $null
        }
    }

    $sharedName = $requestedText
    if ($requestedText.StartsWith('refs/heads/')) {
        $sharedName = $requestedText.Substring('refs/heads/'.Length)
    } elseif ($requestedText.StartsWith('refs/remotes/origin/')) {
        $sharedName = $requestedText.Substring('refs/remotes/origin/'.Length)
    } elseif ($requestedText.StartsWith('origin/')) {
        $sharedName = $requestedText.Substring('origin/'.Length)
    } elseif ($requestedText.StartsWith('refs/') -or $requestedText.StartsWith('-')) {
        return [pscustomobject]@{
            State = 'invalid'
            Code = 'INVALID_REQUESTED_TARGET_CONTEXT'
            Requested = $requestedText
            ErrorText = $null
        }
    }

    if ([string]::IsNullOrWhiteSpace($sharedName) -or $sharedName.StartsWith('-')) {
        return [pscustomobject]@{
            State = 'invalid'
            Code = 'INVALID_REQUESTED_TARGET_CONTEXT'
            Requested = $requestedText
            ErrorText = $null
        }
    }
    $formatResult = Invoke-Git `
        -Repository $Repository `
        -Arguments @('check-ref-format', "refs/heads/$sharedName") `
        -Operation 'check-ref-format'
    if ($formatResult.ExitCode -ne 0) {
        return [pscustomobject]@{
            State = 'invalid'
            Code = 'INVALID_REQUESTED_TARGET_CONTEXT'
            Requested = $requestedText
            ErrorText = $formatResult.StdErr.Trim()
        }
    }

    $resolved = Resolve-TargetRef $Repository $sharedName
    if ($resolved.State -eq 'error') {
        return [pscustomobject]@{
            State = 'error'
            Code = $resolved.Code
            Requested = $requestedText
            ErrorText = $resolved.ErrorText
        }
    }
    if ($resolved.State -eq 'requires_input' -and $resolved.Code -eq 'TARGET_REF_MISSING') {
        return [pscustomobject]@{
            State = 'verified_missing'
            Code = $null
            Requested = $requestedText
            SharedName = $sharedName
            ErrorText = $null
        }
    }
    return [pscustomobject]@{
        State = 'exists'
        Code = 'NO_INDEPENDENT_CHANGE_TARGET_EXISTS'
        Requested = $requestedText
        SharedName = $sharedName
        ErrorText = $null
    }
}

function ConvertTo-PublicRef {
    param([object]$Resolved)
    return [pscustomobject]@{
        requested = $Resolved.Requested
        resolvedRef = $Resolved.ResolvedRef
        sha = $Resolved.Sha
    }
}

function ConvertTo-RepositoryRef {
    param([object]$Resolved)
    if ($Resolved.State -eq 'ok') {
        return ConvertTo-PublicRef $Resolved
    }
    return [pscustomobject]@{
        requested = $Resolved.Requested
        resolvedRef = $null
        sha = $null
    }
}

function Get-Commits {
    param([string]$Repository, [string]$BaseSha, [string]$TargetSha)
    $result = Invoke-Git `
        -Repository $Repository `
        -Arguments @(
            'log',
            '--format=%H%x1f%aI%x1f%s',
            "$BaseSha..$TargetSha"
        ) `
        -Operation 'log'
    if ($result.ExitCode -ne 0) {
        return [pscustomobject]@{
            State = 'error'
            Code = 'GIT_LOG_FAILED'
            Value = @()
            ErrorText = $result.StdErr.Trim()
        }
    }

    $commits = @()
    foreach ($line in @($result.StdOut -split '\r?\n')) {
        if ([string]::IsNullOrEmpty($line)) { continue }
        $fields = $line -split ([char]0x1f), 3
        $commits += [pscustomobject]@{
            sha = $fields[0]
            authorDate = $fields[1]
            subject = $fields[2]
        }
    }
    return [pscustomobject]@{
        State = 'ok'
        Code = $null
        Value = @($commits)
        ErrorText = $null
    }
}

function Get-Changes {
    param([string]$Repository, [string]$BaseSha, [string]$TargetSha)
    $result = Invoke-Git `
        -Repository $Repository `
        -Arguments @(
            '-c',
            'core.quotePath=false',
            'diff',
            '--name-status',
            '-z',
            '-M',
            "$BaseSha...$TargetSha"
        ) `
        -Operation 'diff'
    if ($result.ExitCode -ne 0) {
        return [pscustomobject]@{
            State = 'error'
            Code = 'GIT_DIFF_FAILED'
            Value = @()
            ErrorText = $result.StdErr.Trim()
        }
    }

    $changes = @()
    $fields = @([System.Text.RegularExpressions.Regex]::Split(
        $result.StdOut,
        [string][char]0
    ))
    $index = 0
    while ($index -lt $fields.Count) {
        $status = $fields[$index]
        $index += 1
        if ([string]::IsNullOrEmpty($status)) { break }
        if ($index -ge $fields.Count) {
            return [pscustomobject]@{
                State = 'error'
                Code = 'GIT_DIFF_PARSE_FAILED'
                Value = @()
                ErrorText = 'Missing path in NUL-delimited name-status output.'
            }
        }
        if ($status.StartsWith('R') -or $status.StartsWith('C')) {
            if (($index + 1) -ge $fields.Count) {
                return [pscustomobject]@{
                    State = 'error'
                    Code = 'GIT_DIFF_PARSE_FAILED'
                    Value = @()
                    ErrorText = 'Missing rename/copy path in NUL-delimited name-status output.'
                }
            }
            $oldPath = $fields[$index]
            $path = $fields[$index + 1]
            $index += 2
            $changes += [pscustomobject]@{
                status = $status
                oldPath = $oldPath
                path = $path
            }
            continue
        }
        $path = $fields[$index]
        $index += 1
        $changes += [pscustomobject]@{
            status = $status
            path = $path
        }
    }
    return [pscustomobject]@{
        State = 'ok'
        Code = $null
        Value = @($changes)
        ErrorText = $null
    }
}

function Get-MergeBase {
    param([string]$Repository, [string]$BaseSha, [string]$TargetSha)
    $result = Invoke-Git `
        -Repository $Repository `
        -Arguments @('merge-base', $BaseSha, $TargetSha) `
        -Operation 'merge-base'
    $lines = @($result.StdOut -split '\r?\n')
    if ($result.ExitCode -ne 0 -or $lines.Count -eq 0 -or [string]::IsNullOrWhiteSpace($lines[0])) {
        return [pscustomobject]@{
            State = 'error'
            Code = 'MERGE_BASE_FAILED'
            Value = $null
            ErrorText = $result.StdErr.Trim()
        }
    }
    return [pscustomobject]@{
        State = 'ok'
        Code = $null
        Value = $lines[0].Trim()
        ErrorText = $null
    }
}

function Get-WorkingTree {
    param([string]$Repository)
    $branchResult = Invoke-Git `
        -Repository $Repository `
        -Arguments @('symbolic-ref', '--short', '--quiet', 'HEAD') `
        -Operation 'symbolic-ref'
    $branchLines = @($branchResult.StdOut -split '\r?\n')
    $branch = if (
        $branchResult.ExitCode -eq 0 -and
        $branchLines.Count -gt 0 -and
        -not [string]::IsNullOrWhiteSpace($branchLines[0])
    ) {
        $branchLines[0].Trim()
    } else {
        'HEAD'
    }
    $statusResult = Invoke-Git `
        -Repository $Repository `
        -Arguments @('status', '--porcelain=v1') `
        -Operation 'status'
    if ($statusResult.ExitCode -ne 0) {
        return [pscustomobject]@{
            State = 'error'
            Code = 'GIT_STATUS_FAILED'
            Value = $null
            ErrorText = $statusResult.StdErr.Trim()
        }
    }
    [string[]]$porcelainLines = @()
    $porcelainLines = @(
        $statusResult.StdOut -split '\r?\n' |
            Where-Object { -not [string]::IsNullOrEmpty($_) } |
            ForEach-Object { "$_" }
    )
    return [pscustomobject]@{
        State = 'ok'
        Code = $null
        ErrorText = $null
        Value = [pscustomobject]@{
            branch = $branch
            porcelain = @($porcelainLines)
        }
    }
}

$projectRoot = $userProfile.workspaceRoot
$tmpRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.tmp'))
$remoteCacheRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot '.cache\fast-precheck\remote-git')
)
$resolvedRequestPath = [System.IO.Path]::GetFullPath($RequestPath)
$resolvedOutputPath = [System.IO.Path]::GetFullPath($OutputPath)

if (-not (Test-IsBelowBoundary $resolvedRequestPath $tmpRoot)) {
    [Console]::Error.WriteLine('REQUEST_OUTSIDE_TMP')
    exit 1
}
if (-not (Test-IsBelowBoundary $resolvedOutputPath $tmpRoot)) {
    [Console]::Error.WriteLine('OUTPUT_OUTSIDE_TMP')
    exit 1
}
$requestInspection = Test-PathForReparsePoint $resolvedRequestPath $tmpRoot
if ($requestInspection.State -eq 'error') {
    [Console]::Error.WriteLine('REQUEST_PATH_INSPECTION_FAILED')
    exit 1
}
if ($requestInspection.ContainsReparsePoint) {
    [Console]::Error.WriteLine('REQUEST_REPARSE_POINT')
    exit 1
}
$outputInspection = Test-PathForReparsePoint $resolvedOutputPath $tmpRoot
if ($outputInspection.State -eq 'error') {
    [Console]::Error.WriteLine('OUTPUT_PATH_INSPECTION_FAILED')
    exit 1
}
if ($outputInspection.ContainsReparsePoint) {
    [Console]::Error.WriteLine('OUTPUT_REPARSE_POINT')
    exit 1
}

$request = Get-Content -LiteralPath $resolvedRequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$repositoryResults = @()
$requiresInput = @()
$errors = @()

foreach ($repository in @($request.repositories)) {
    $script:CurrentGitWarnings = @()
    $source = if ($null -ne $repository.PSObject.Properties['source']) {
        [string]$repository.source
    } else {
        'local'
    }
    $isRemote = $source -ceq 'remote'
    $remoteName = $null
    $remoteBaseRef = $null
    $remoteTargetRef = $null
    $remoteContractCode = $null
    if ($isRemote) {
        $repositoryPath = [System.IO.Path]::GetFullPath([string]$repository.path)
        $remotePathInspection = Test-PathForReparsePoint $repositoryPath $remoteCacheRoot
        if (
            -not (Test-IsBelowBoundary $repositoryPath $remoteCacheRoot) -or
            $remotePathInspection.State -ne 'ok' -or
            $remotePathInspection.ContainsReparsePoint
        ) {
            $remoteContractCode = 'REMOTE_REPOSITORY_NOT_ALLOWED'
        }
        $baseSha = if ($null -ne $repository.PSObject.Properties['baseSha']) {
            [string]$repository.baseSha
        } else {
            ''
        }
        $targetSha = if ($null -ne $repository.PSObject.Properties['targetSha']) {
            [string]$repository.targetSha
        } else {
            ''
        }
        $remoteName = if ($null -ne $repository.PSObject.Properties['remote']) {
            [string]$repository.remote
        } else {
            ''
        }
        $remoteBaseRef = if ($null -ne $repository.PSObject.Properties['baseRef']) {
            [string]$repository.baseRef
        } else {
            ''
        }
        $remoteTargetRef = if ($null -ne $repository.PSObject.Properties['targetRef']) {
            [string]$repository.targetRef
        } else {
            ''
        }
        if (
            $null -eq $remoteContractCode -and
            (-not (Test-IsFullCommitSha $baseSha) -or -not (Test-IsFullCommitSha $targetSha))
        ) {
            $remoteContractCode = 'REMOTE_SHA_REQUIRED'
        }
        if (
            $null -eq $remoteContractCode -and
            (
                $remoteName -cne 'origin' -or
                $remoteBaseRef -cnotmatch '^refs/remotes/origin/[A-Za-z0-9][A-Za-z0-9._/-]*$' -or
                $remoteTargetRef -cnotmatch '^refs/remotes/origin/[A-Za-z0-9][A-Za-z0-9._/-]*$'
            )
        ) {
            $remoteContractCode = 'INVALID_REMOTE_PROVENANCE'
        }

        if ($null -ne $remoteContractCode) {
            $errors += [pscustomobject]@{
                role = $repository.role
                path = $repository.path
                code = $remoteContractCode
            }
            $base = [pscustomobject]@{
                State = 'blocked'
                Requested = $baseSha
                ResolvedRef = $null
                Sha = $null
                ErrorText = $null
            }
            $target = [pscustomobject]@{
                State = 'blocked'
                Requested = $targetSha
                ResolvedRef = $null
                Sha = $null
                ErrorText = $null
            }
        } else {
            $base = Resolve-CommitObject $repository.path $baseSha 'BASE_OBJECT_MISSING' $true
            $target = Resolve-CommitObject $repository.path $targetSha 'TARGET_OBJECT_MISSING' $false
            if ($base.State -eq 'ok') { $base.ResolvedRef = $remoteBaseRef }
            if ($target.State -eq 'ok') { $target.ResolvedRef = $remoteTargetRef }
        }
    } else {
        $base = Resolve-BaseRef $repository.path $repository.base
    }
    $hasNoIndependentChange = (
        -not $isRemote -and
        $null -ne $repository.PSObject.Properties['noIndependentChange']
    )
    $noIndependentChangeRequested = $false
    $invalidNoIndependentChange = $false
    if ($hasNoIndependentChange) {
        if ($repository.noIndependentChange -is [bool]) {
            $noIndependentChangeRequested = $repository.noIndependentChange
        } else {
            $invalidNoIndependentChange = $true
        }
    }
    $hasExplicitTarget = (
        -not $isRemote -and
        $null -ne $repository.PSObject.Properties['target']
    )
    $hasRequestedMissingTarget = (
        -not $isRemote -and
        $null -ne $repository.PSObject.Properties['requestedMissingTarget']
    )
    $targetConflict = (
        $noIndependentChangeRequested -and
        $hasExplicitTarget
    )
    $missingTargetContext = (
        $noIndependentChangeRequested -and
        -not $hasRequestedMissingTarget
    )
    $unexpectedTargetContext = (
        -not $noIndependentChangeRequested -and
        $hasRequestedMissingTarget
    )
    $missingContextResult = $null
    if (
        $noIndependentChangeRequested -and
        -not $invalidNoIndependentChange -and
        -not $targetConflict -and
        -not $missingTargetContext -and
        $base.State -eq 'ok'
    ) {
        $missingContextResult = Test-MissingTargetContext `
            $repository.path `
            $repository.requestedMissingTarget
    }
    $acceptedNoIndependentChange = (
        $noIndependentChangeRequested -and
        -not $invalidNoIndependentChange -and
        -not $targetConflict -and
        -not $missingTargetContext -and
        -not $unexpectedTargetContext -and
        $base.State -eq 'ok' -and
        $null -ne $missingContextResult -and
        $missingContextResult.State -eq 'verified_missing'
    )
    if ($isRemote) {
        # Remote target was resolved exclusively from the frozen SHA above.
    } elseif ($acceptedNoIndependentChange) {
        $target = [pscustomobject]@{
            State = 'ok'
            Requested = $base.Requested
            ResolvedRef = $base.ResolvedRef
            Sha = $base.Sha
            ErrorText = $null
        }
    } elseif ($noIndependentChangeRequested -or $invalidNoIndependentChange) {
        $target = [pscustomobject]@{
            State = 'blocked'
            Requested = if ($hasExplicitTarget) { [string]$repository.target } else { $null }
            ResolvedRef = $null
            Sha = $null
            ErrorText = $null
        }
    } else {
        $targetRequested = if ($hasExplicitTarget) { [string]$repository.target } else { '' }
        $target = Resolve-TargetRef $repository.path $targetRequested
    }

    $warnings = @()
    if ($base.State -eq 'ok' -and $null -ne $base.Warning) {
        $warnings += [pscustomobject]@{ code = $base.Warning }
    }
    if ($base.State -eq 'error') {
        $errorRecord = [ordered]@{
            role = $repository.role
            path = $repository.path
            code = $base.Code
            requested = $base.Requested
        }
        if (-not [string]::IsNullOrWhiteSpace($base.ErrorText)) {
            $errorRecord.stderr = $base.ErrorText
        }
        $errors += [pscustomobject]$errorRecord
    }
    if ($invalidNoIndependentChange) {
        $errors += [pscustomobject]@{
            role = $repository.role
            path = $repository.path
            code = 'INVALID_NO_INDEPENDENT_CHANGE'
        }
    }
    if ($missingTargetContext) {
        $errors += [pscustomobject]@{
            role = $repository.role
            path = $repository.path
            code = 'MISSING_REQUESTED_TARGET_CONTEXT'
        }
    }
    if ($unexpectedTargetContext) {
        $errors += [pscustomobject]@{
            role = $repository.role
            path = $repository.path
            code = 'UNEXPECTED_REQUESTED_TARGET_CONTEXT'
        }
    }
    if ($targetConflict) {
        $errors += [pscustomobject]@{
            role = $repository.role
            path = $repository.path
            code = 'NO_INDEPENDENT_CHANGE_TARGET_CONFLICT'
            baseRequested = [string]$repository.base
            targetRequested = [string]$repository.target
        }
    }
    if ($null -ne $missingContextResult -and $missingContextResult.State -eq 'invalid') {
        $errorRecord = [ordered]@{
            role = $repository.role
            path = $repository.path
            code = $missingContextResult.Code
        }
        if (-not [string]::IsNullOrWhiteSpace($missingContextResult.ErrorText)) {
            $errorRecord.stderr = $missingContextResult.ErrorText
        }
        $errors += [pscustomobject]$errorRecord
    }
    if ($null -ne $missingContextResult -and $missingContextResult.State -eq 'error') {
        $errorRecord = [ordered]@{
            role = $repository.role
            path = $repository.path
            code = $missingContextResult.Code
            requested = $missingContextResult.Requested
        }
        if (-not [string]::IsNullOrWhiteSpace($missingContextResult.ErrorText)) {
            $errorRecord.stderr = $missingContextResult.ErrorText
        }
        $errors += [pscustomobject]$errorRecord
    }
    if ($null -ne $missingContextResult -and $missingContextResult.State -eq 'exists') {
        $errors += [pscustomobject]@{
            role = $repository.role
            path = $repository.path
            code = $missingContextResult.Code
            requestedMissingTarget = $missingContextResult.Requested
        }
    }
    if ($target.State -eq 'error') {
        $errorRecord = [ordered]@{
            role = $repository.role
            path = $repository.path
            code = $target.Code
            requested = $target.Requested
        }
        if (-not [string]::IsNullOrWhiteSpace($target.ErrorText)) {
            $errorRecord.stderr = $target.ErrorText
        }
        $errors += [pscustomobject]$errorRecord
    }
    if ($target.State -eq 'requires_input') {
        if ($target.Code -eq 'TARGET_REF_MISSING') {
            $requiresInput += [pscustomobject]@{
                role = $repository.role
                path = $repository.path
                code = $target.Code
                base = ConvertTo-RepositoryRef $base
                target = ConvertTo-RepositoryRef $target
                options = @(
                    [pscustomobject]@{
                        action = 'provide_alternative_ref'
                        field = 'target'
                    },
                    [pscustomobject]@{
                        action = 'confirm_no_independent_change'
                        requestPatch = [pscustomobject]@{
                            noIndependentChange = $true
                            requestedMissingTarget = $target.Requested
                        }
                        removeFields = @('target')
                    }
                )
            }
        } else {
            $requiresInput += [pscustomobject]@{
                role = $repository.role
                path = $repository.path
                code = $target.Code
                requested = $target.Requested
                candidates = $target.Candidates
            }
        }
    }

    $commits = @()
    $changes = @()
    $mergeBase = $null
    $workingTree = $null
    if ($base.State -eq 'ok' -and $target.State -eq 'ok') {
        if ($acceptedNoIndependentChange) {
            $workingTreeResult = Get-WorkingTree $repository.path
            $commandResults = @($workingTreeResult)
            $mergeBase = $base.Sha
        } elseif ($isRemote) {
            $mergeBaseResult = Get-MergeBase $repository.path $base.Sha $target.Sha
            if ($mergeBaseResult.State -eq 'error') {
                $commandResults = @($mergeBaseResult)
            } else {
                $commitResult = Get-Commits $repository.path $base.Sha $target.Sha
                $changeResult = Get-Changes $repository.path $base.Sha $target.Sha
                $commandResults = @(
                    $commitResult,
                    $changeResult,
                    $mergeBaseResult
                )
            }
        } else {
            $mergeBaseResult = Get-MergeBase $repository.path $base.Sha $target.Sha
            if ($mergeBaseResult.State -eq 'error') {
                $commandResults = @($mergeBaseResult)
            } else {
                $commitResult = Get-Commits $repository.path $base.Sha $target.Sha
                $changeResult = Get-Changes $repository.path $base.Sha $target.Sha
                $workingTreeResult = Get-WorkingTree $repository.path
                $commandResults = @(
                    $commitResult,
                    $changeResult,
                    $mergeBaseResult,
                    $workingTreeResult
                )
            }
        }
        $commandFailures = @($commandResults | Where-Object { $_.State -eq 'error' })
        if ($commandFailures.Count -gt 0) {
            foreach ($failure in $commandFailures) {
                $errorRecord = [ordered]@{
                    role = $repository.role
                    path = $repository.path
                    code = $failure.Code
                }
                if (-not [string]::IsNullOrWhiteSpace($failure.ErrorText)) {
                    $errorRecord.stderr = $failure.ErrorText
                }
                $errors += [pscustomobject]$errorRecord
            }
        } else {
            if (-not $acceptedNoIndependentChange) {
                $commits = @($commitResult.Value)
                $changes = @($changeResult.Value)
                $mergeBase = $mergeBaseResult.Value
            }
            if (-not $isRemote) {
                $workingTree = $workingTreeResult.Value
            }
        }
    }
    $warnings += @($script:CurrentGitWarnings)
    $decision = if ($acceptedNoIndependentChange) {
        [pscustomobject]@{
            value = 'user_confirmed_no_independent_change'
            requestedMissingTarget = $missingContextResult.Requested
            base = ConvertTo-PublicRef $base
        }
    } else {
        $null
    }
    $repositoryRecord = [ordered]@{
        role = $repository.role
        path = $repository.path
        base = ConvertTo-RepositoryRef $base
        target = ConvertTo-RepositoryRef $target
        mergeBase = $mergeBase
        workingTree = $workingTree
        commits = $commits
        changes = $changes
        warnings = $warnings
        noIndependentChange = $acceptedNoIndependentChange
        decision = $decision
    }
    if ($isRemote) {
        $repositoryRecord.source = 'remote'
        $repositoryRecord.remote = $remoteName
        $repositoryRecord.baseRef = $remoteBaseRef
        $repositoryRecord.targetRef = $remoteTargetRef
    }
    $repositoryResults += [pscustomobject]$repositoryRecord
}

$status = 'ok'
$exitCode = 0
if ($errors.Count -gt 0) {
    $status = 'error'
    $exitCode = 1
} elseif ($requiresInput.Count -gt 0) {
    $status = 'requires_input'
    $exitCode = 2
}
$payload = [pscustomobject]@{
    schemaVersion = 1
    status = $status
    generatedAtUtc = [System.DateTime]::UtcNow.ToString(
        'o',
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    repositories = $repositoryResults
    requiresInput = $requiresInput
    errors = $errors
}
$json = $payload | ConvertTo-Json -Depth 12
[System.IO.File]::WriteAllText(
    $resolvedOutputPath,
    $json,
    (New-Object System.Text.UTF8Encoding($false))
)
exit $exitCode
