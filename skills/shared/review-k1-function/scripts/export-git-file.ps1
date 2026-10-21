[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Repository,

    [Parameter(Mandatory = $true)]
    [string]$Commit,

    [Parameter(Mandatory = $true)]
    [string]$GitPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [switch]$MaterializeLfs,

    [string]$Remote = 'origin',

    [string]$ExpectedLfsPath,

    [ValidateRange(1, 120)]
    [int]$LfsTimeoutSeconds = 120
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'user-config.ps1')
try { $userProfile = Get-K1UserProfile } catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}

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

function Test-IsValidGitPath {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    if ($Value.IndexOf([char]0) -ge 0) { return $false }
    if ($Value.Contains('\')) { return $false }
    if ($Value.StartsWith('/')) { return $false }
    if ($Value -match '^[A-Za-z]:') { return $false }
    if ($Value.Contains('//') -or $Value.EndsWith('/')) { return $false }
    foreach ($segment in @($Value -split '/')) {
        if (
            [string]::IsNullOrEmpty($segment) -or
            $segment -eq '.' -or
            $segment -eq '..'
        ) {
            return $false
        }
    }
    return $true
}

function Test-ContainsReparsePoint {
    param([string]$Path, [string]$Boundary)
    $current = [System.IO.Path]::GetDirectoryName($Path)
    while ($null -ne $current) {
        $insideBoundary = $current.Equals(
            $Boundary,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or $current.StartsWith(
            $Boundary.TrimEnd(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            ) + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
        if (-not $insideBoundary) { break }

        if ([System.IO.Directory]::Exists($current)) {
            $attributes = [System.IO.File]::GetAttributes($current)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                return $true
            }
        }
        if ($current.Equals($Boundary, [System.StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $current = [System.IO.Path]::GetDirectoryName($current)
    }
    return $false
}

function Test-IsBelowBoundary {
    param([string]$Path, [string]$Boundary)
    $prefix = $Boundary.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    return $Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-LfsPointer {
    param([string]$Path)
    $file = Get-Item -LiteralPath $Path -Force
    if ($file.Length -gt 1024) {
        return [pscustomobject]@{ State = 'ordinary'; Oid = $null; Size = $null }
    }
    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
        $text = $utf8.GetString([System.IO.File]::ReadAllBytes($Path))
    } catch {
        return [pscustomobject]@{ State = 'ordinary'; Oid = $null; Size = $null }
    }
    $header = 'version https://git-lfs.github.com/spec/v1'
    if (-not $text.StartsWith($header, [System.StringComparison]::Ordinal)) {
        return [pscustomobject]@{ State = 'ordinary'; Oid = $null; Size = $null }
    }
    $match = [regex]::Match(
        $text,
        '\Aversion https://git-lfs\.github\.com/spec/v1\r?\noid sha256:([0-9a-f]{64})\r?\nsize ([0-9]+)\r?\n?\z',
        [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    if (-not $match.Success) {
        return [pscustomobject]@{ State = 'invalid'; Oid = $null; Size = $null }
    }
    $size = 0L
    if (-not [long]::TryParse(
        $match.Groups[2].Value,
        [System.Globalization.NumberStyles]::None,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$size
    )) {
        return [pscustomobject]@{ State = 'invalid'; Oid = $null; Size = $null }
    }
    return [pscustomobject]@{
        State = 'pointer'
        Oid = $match.Groups[1].Value
        Size = $size
    }
}

function Get-Sha256Hex {
    param([string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha256.ComputeHash($stream)
        return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
    } finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function New-GitStartInfo {
    param(
        [string]$RepositoryPath,
        [string[]]$Arguments,
        [bool]$RedirectInput
    )
    $allArguments = @('--no-optional-locks', '-C', $RepositoryPath) + @($Arguments)
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'git'
    $startInfo.Arguments = (@(
        $allArguments | ForEach-Object { ConvertTo-ProcessArgument $_ }
    ) -join ' ')
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.RedirectStandardInput = $RedirectInput
    $startInfo.CreateNoWindow = $true
    $startInfo.EnvironmentVariables['GIT_OPTIONAL_LOCKS'] = '0'
    $startInfo.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $startInfo.EnvironmentVariables['GIT_LFS_SKIP_SMUDGE'] = '1'
    $startInfo.EnvironmentVariables['GIT_SSH_COMMAND'] = 'ssh -o BatchMode=yes'
    return $startInfo
}

function Stop-ProcessTree {
    param([System.Diagnostics.Process]$Process)
    try {
        if ($Process.HasExited) { return }
    } catch {
        return
    }

    $taskkillPath = Join-Path ([System.Environment]::SystemDirectory) 'taskkill.exe'
    if ([System.IO.File]::Exists($taskkillPath)) {
        $stopInfo = New-Object System.Diagnostics.ProcessStartInfo
        $stopInfo.FileName = $taskkillPath
        $stopInfo.Arguments = "/PID $($Process.Id) /T /F"
        $stopInfo.UseShellExecute = $false
        $stopInfo.RedirectStandardOutput = $true
        $stopInfo.RedirectStandardError = $true
        $stopInfo.CreateNoWindow = $true
        $stopProcess = New-Object System.Diagnostics.Process
        $stopProcess.StartInfo = $stopInfo
        try {
            $stopProcess.Start() | Out-Null
            $stdoutTask = $stopProcess.StandardOutput.ReadToEndAsync()
            $stderrTask = $stopProcess.StandardError.ReadToEndAsync()
            if (-not $stopProcess.WaitForExit(5000)) {
                try { $stopProcess.Kill() } catch { }
                $stopProcess.WaitForExit()
            }
            [void]$stdoutTask.Result
            [void]$stderrTask.Result
        } catch {
        } finally {
            $stopProcess.Dispose()
        }
    }

    try {
        if (-not $Process.HasExited) {
            $Process.Kill()
        }
        $Process.WaitForExit()
    } catch {
    }
}

function Invoke-LfsFetch {
    param(
        [string]$RepositoryPath,
        [string]$RemoteName,
        [string]$CommitSha,
        [string]$Path,
        [int]$TimeoutMilliseconds
    )
    $startInfo = New-GitStartInfo -RepositoryPath $RepositoryPath `
        -Arguments @(
            'lfs', 'fetch', $RemoteName, $CommitSha,
            "--include=$Path", '--exclude='
        ) -RedirectInput $false
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        $process.Start() | Out-Null
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            Stop-ProcessTree -Process $process
            return [pscustomobject]@{
                Succeeded = $false
                FailureCode = 'LFS_FETCH_TIMEOUT'
                Detail = 'remote LFS fetch exceeded the time budget'
            }
        }
        [void]$stdoutTask.Result
        $stderrText = $stderrTask.Result
        if ($process.ExitCode -eq 0) {
            return [pscustomobject]@{
                Succeeded = $true
                FailureCode = $null
                Detail = $null
            }
        }
        if ($stderrText -match '(?i)object does not exist|object not found|missing object|not available on the server|\b404\b') {
            return [pscustomobject]@{
                Succeeded = $false
                FailureCode = 'LFS_OBJECT_MISSING'
                Detail = 'requested LFS object is not available on the remote'
            }
        }
        if ($stderrText -match '(?i)authentication|authorization|credential|access denied|\b401\b|\b403\b') {
            return [pscustomobject]@{
                Succeeded = $false
                FailureCode = 'LFS_FETCH_AUTH_FAILED'
                Detail = 'authentication rejected by remote'
            }
        }
        if ($stderrText -match '(?i)connection|could not resolve|network|timed? out|tls|remote end hung up|unexpected eof') {
            return [pscustomobject]@{
                Succeeded = $false
                FailureCode = 'LFS_FETCH_NETWORK_FAILED'
                Detail = 'remote LFS network request failed'
            }
        }
        return [pscustomobject]@{
            Succeeded = $false
            FailureCode = 'LFS_FETCH_FAILED'
            Detail = 'remote LFS fetch failed'
        }
    } finally {
        $process.Dispose()
    }
}

function Invoke-LfsSmudge {
    param(
        [string]$RepositoryPath,
        [string]$Path,
        [string]$PointerPath,
        [string]$MaterializedPath,
        [int]$TimeoutMilliseconds
    )
    $startInfo = New-GitStartInfo -RepositoryPath $RepositoryPath `
        -Arguments @('lfs', 'smudge', '--', $Path) -RedirectInput $true
    $startInfo.EnvironmentVariables.Remove('GIT_LFS_SKIP_SMUDGE')
    $process = New-Object System.Diagnostics.Process
    $output = $null
    try {
        $output = [System.IO.FileStream]::new(
            $MaterializedPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $process.StartInfo = $startInfo
        $process.Start() | Out-Null
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $input = [System.IO.File]::OpenRead($PointerPath)
        try {
            $input.CopyTo($process.StandardInput.BaseStream)
            $process.StandardInput.Close()
        } finally {
            $input.Dispose()
        }
        $copyTask = $process.StandardOutput.BaseStream.CopyToAsync($output)
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            Stop-ProcessTree -Process $process
            [void]$copyTask.Result
            [void]$stderrTask.Result
            return [pscustomobject]@{
                Succeeded = $false
                FailureCode = 'LFS_SMUDGE_TIMEOUT'
                Detail = 'LFS materialization exceeded the time budget'
            }
        }
        [void]$copyTask.Result
        [void]$stderrTask.Result
        $output.Flush()
        if ($process.ExitCode -eq 0) {
            return [pscustomobject]@{
                Succeeded = $true
                FailureCode = $null
                Detail = $null
            }
        }
        return [pscustomobject]@{
            Succeeded = $false
            FailureCode = 'LFS_SMUDGE_FAILED'
            Detail = 'LFS materialization failed'
        }
    } finally {
        if ($null -ne $output) { $output.Dispose() }
        try {
            if (-not $process.HasExited) {
                Stop-ProcessTree -Process $process
            }
        } catch { }
        $process.Dispose()
    }
}

if ($Commit -notmatch '\A[0-9A-Fa-f]{40}\z') {
    [Console]::Error.WriteLine('INVALID_COMMIT')
    exit 1
}
if (-not (Test-IsValidGitPath $GitPath)) {
    [Console]::Error.WriteLine('INVALID_GIT_PATH')
    exit 1
}
if ($MaterializeLfs) {
    if ($Remote -cne 'origin') {
        [Console]::Error.WriteLine('INVALID_LFS_REMOTE')
        exit 1
    }
    if (
        [string]::IsNullOrWhiteSpace($ExpectedLfsPath) -or
        $ExpectedLfsPath -cne $GitPath
    ) {
        [Console]::Error.WriteLine('LFS_PATH_MISMATCH')
        exit 1
    }
}

try {
    $resolvedRepository = (Resolve-Path -LiteralPath $Repository -ErrorAction Stop).Path
} catch {
    [Console]::Error.WriteLine('REPOSITORY_NOT_FOUND')
    exit 1
}

$projectRoot = $userProfile.workspaceRoot
$tmpRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.tmp'))
$remoteCacheRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot '.cache\fast-precheck\remote-git')
)
$resolvedRepository = [System.IO.Path]::GetFullPath($resolvedRepository)
if ($MaterializeLfs) {
    $repositoryAttributes = [System.IO.File]::GetAttributes($resolvedRepository)
    if (
        -not (Test-IsBelowBoundary $resolvedRepository $remoteCacheRoot) -or
        ($repositoryAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        (Test-ContainsReparsePoint $resolvedRepository $remoteCacheRoot)
    ) {
        [Console]::Error.WriteLine('LFS_REPOSITORY_NOT_ALLOWED')
        exit 1
    }
}
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$tmpPrefix = $tmpRoot.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar

if (-not $resolvedOutput.StartsWith($tmpPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    [Console]::Error.WriteLine('OUTPUT_OUTSIDE_TMP')
    exit 1
}
if (
    [System.IO.File]::Exists($resolvedOutput) -or
    [System.IO.Directory]::Exists($resolvedOutput)
) {
    [Console]::Error.WriteLine('OUTPUT_ALREADY_EXISTS')
    exit 1
}
if (Test-ContainsReparsePoint $resolvedOutput $tmpRoot) {
    [Console]::Error.WriteLine('OUTPUT_REPARSE_POINT')
    exit 1
}

$outputParent = [System.IO.Path]::GetDirectoryName($resolvedOutput)
[System.IO.Directory]::CreateDirectory($outputParent) | Out-Null

$temporaryPath = Join-Path $outputParent (
    '.' +
    [System.IO.Path]::GetFileName($resolvedOutput) +
    '.' +
    [System.Guid]::NewGuid().ToString('N') +
    '.tmp'
)
$materializedPath = Join-Path $outputParent (
    '.' +
    [System.IO.Path]::GetFileName($resolvedOutput) +
    '.' +
    [System.Guid]::NewGuid().ToString('N') +
    '.lfs.tmp'
)
$gitObject = "$Commit`:$GitPath"
$arguments = @(
    '--no-optional-locks',
    '-C',
    $resolvedRepository,
    'cat-file',
    'blob',
    $gitObject
) |
    ForEach-Object { ConvertTo-ProcessArgument $_ }

$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = 'git'
$startInfo.Arguments = $arguments -join ' '
$startInfo.UseShellExecute = $false
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.CreateNoWindow = $true

$process = $null
$stream = $null
$completed = $false
$failureText = $null
try {
    $stream = [System.IO.FileStream]::new(
        $temporaryPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $process.Start() | Out-Null
    $errorTask = $process.StandardError.ReadToEndAsync()
    $process.StandardOutput.BaseStream.CopyTo($stream)
    $stream.Flush()
    $stream.Dispose()
    $stream = $null
    $process.WaitForExit()
    $errorText = $errorTask.Result

    if ($process.ExitCode -ne 0) {
        $failureText = $errorText.Trim()
        throw 'GIT_PROCESS_NONZERO'
    }

    if ($MaterializeLfs) {
        $pointer = Get-LfsPointer -Path $temporaryPath
        if ($pointer.State -eq 'invalid') {
            $failureText = 'INVALID_LFS_POINTER'
            throw $failureText
        }
        if ($pointer.State -eq 'pointer') {
            # Fetch, its single transient retry, and smudge share one bounded budget.
            $lfsStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
            $lfsBudgetMilliseconds = $LfsTimeoutSeconds * 1000
            $remainingMilliseconds = [Math]::Max(
                0,
                $lfsBudgetMilliseconds - [int]$lfsStopwatch.ElapsedMilliseconds
            )
            $lfsFetch = Invoke-LfsFetch -RepositoryPath $resolvedRepository `
                -RemoteName $Remote -CommitSha $Commit -Path $GitPath `
                -TimeoutMilliseconds $remainingMilliseconds
            if (
                -not $lfsFetch.Succeeded -and
                $lfsFetch.FailureCode -eq 'LFS_FETCH_NETWORK_FAILED'
            ) {
                $remainingMilliseconds = [Math]::Max(
                    0,
                    $lfsBudgetMilliseconds - [int]$lfsStopwatch.ElapsedMilliseconds
                )
                if ($remainingMilliseconds -eq 0) {
                    $lfsFetch = [pscustomobject]@{
                        Succeeded = $false
                        FailureCode = 'LFS_FETCH_TIMEOUT'
                        Detail = 'remote LFS fetch exceeded the time budget'
                    }
                } else {
                    $lfsFetch = Invoke-LfsFetch -RepositoryPath $resolvedRepository `
                        -RemoteName $Remote -CommitSha $Commit -Path $GitPath `
                        -TimeoutMilliseconds $remainingMilliseconds
                }
            }
            if (-not $lfsFetch.Succeeded) {
                $failureText = $lfsFetch.FailureCode
                if (-not [string]::IsNullOrWhiteSpace($lfsFetch.Detail)) {
                    $failureText += "`nLFS_FETCH_DETAIL: $($lfsFetch.Detail)"
                }
                throw $failureText
            }
            $remainingMilliseconds = [Math]::Max(
                0,
                $lfsBudgetMilliseconds - [int]$lfsStopwatch.ElapsedMilliseconds
            )
            if ($remainingMilliseconds -eq 0) {
                $lfsSmudge = [pscustomobject]@{
                    Succeeded = $false
                    FailureCode = 'LFS_SMUDGE_TIMEOUT'
                    Detail = 'LFS materialization exceeded the time budget'
                }
            } else {
                $lfsSmudge = Invoke-LfsSmudge -RepositoryPath $resolvedRepository `
                    -Path $GitPath -PointerPath $temporaryPath `
                    -MaterializedPath $materializedPath `
                    -TimeoutMilliseconds $remainingMilliseconds
            }
            if (-not $lfsSmudge.Succeeded) {
                $failureText = $lfsSmudge.FailureCode
                if (-not [string]::IsNullOrWhiteSpace($lfsSmudge.Detail)) {
                    $failureText += "`nLFS_SMUDGE_DETAIL: $($lfsSmudge.Detail)"
                }
                throw $failureText
            }
            $materializedFile = Get-Item -LiteralPath $materializedPath -Force
            $materializedHash = Get-Sha256Hex -Path $materializedPath
            if (
                $materializedFile.Length -ne $pointer.Size -or
                $materializedHash -cne $pointer.Oid
            ) {
                $failureText = 'LFS_CONTENT_MISMATCH'
                throw $failureText
            }
            [System.IO.File]::Delete($temporaryPath)
            [System.IO.File]::Move($materializedPath, $resolvedOutput)
        } else {
            [System.IO.File]::Move($temporaryPath, $resolvedOutput)
        }
    } else {
        [System.IO.File]::Move($temporaryPath, $resolvedOutput)
    }
    $completed = $true
} catch {
    if ([string]::IsNullOrWhiteSpace($failureText)) {
        $failureText = $_.Exception.Message
    }
} finally {
    if ($null -ne $stream) {
        $stream.Dispose()
    }
    if ($null -ne $process) {
        try {
            if (-not $process.HasExited) {
                $process.Kill()
                $process.WaitForExit()
            }
        } catch {
        } finally {
            $process.Dispose()
        }
    }
    if (-not $completed -and [System.IO.File]::Exists($temporaryPath)) {
        [System.IO.File]::Delete($temporaryPath)
    }
    if (-not $completed -and [System.IO.File]::Exists($materializedPath)) {
        [System.IO.File]::Delete($materializedPath)
    }
}

if (-not $completed) {
    [Console]::Error.WriteLine('GIT_OBJECT_EXPORT_FAILED')
    if (-not [string]::IsNullOrWhiteSpace($failureText)) {
        [Console]::Error.WriteLine($failureText)
    }
    exit 1
}

exit 0
