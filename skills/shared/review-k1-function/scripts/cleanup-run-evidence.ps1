param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Initialize', 'Cleanup')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$RunDirectory,

    [Parameter(Mandatory = $true)]
    [string]$RunId,

    [Parameter(Mandatory = $true)]
    [ValidateSet('initialization', 'success', 'error', 'clarification', 'exception')]
    [string]$Reason
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'user-config.ps1')
try { $userProfile = Get-K1UserProfile } catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}

$markerName = '.review-k1-run.json'
$markerOwner = 'review-k1-function'
$schemaVersion = 1

function Stop-Structured {
    param([string]$Code, [string]$Message)
    throw [System.InvalidOperationException]::new("$Code|$Message")
}

function Write-StructuredResult {
    param([string]$Status, [string]$Code, [string]$Message, [int]$ExitCode)
    $payload = [ordered]@{
        schemaVersion = $schemaVersion
        status = $Status
        action = $Action
        code = $Code
        reason = $Reason
        runId = $RunId
        runDirectory = $script:ResolvedRunDirectory
    }
    if (-not [string]::IsNullOrWhiteSpace($Message)) {
        $payload.message = $Message
    }
    $json = [pscustomobject]$payload | ConvertTo-Json -Depth 8 -Compress
    [Console]::Out.WriteLine($json)
    exit $ExitCode
}

function Test-IsReparsePoint {
    param([System.IO.FileSystemInfo]$Item)
    return ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
}

function Get-DirectChildEntry {
    param([string]$Parent, [string]$Leaf)
    try {
        $matches = @(
            Get-ChildItem -LiteralPath $Parent -Force -ErrorAction Stop |
                Where-Object { $_.Name.Equals($Leaf, [System.StringComparison]::OrdinalIgnoreCase) }
        )
    } catch {
        Stop-Structured 'PATH_INSPECTION_FAILED' $_.Exception.Message
    }
    if ($matches.Count -eq 0) { return $null }
    if ($matches.Count -ne 1) {
        Stop-Structured 'PATH_INSPECTION_FAILED' "Direct-child identity is ambiguous: $Leaf"
    }
    return $matches[0]
}

function Assert-NormalDirectory {
    param(
        [System.IO.FileSystemInfo]$Item,
        [string]$ReparseCode,
        [string]$InvalidCode
    )
    if ($null -eq $Item -or -not $Item.PSIsContainer) {
        Stop-Structured $InvalidCode "Expected directory is missing or not a directory"
    }
    if (Test-IsReparsePoint $Item) {
        Stop-Structured $ReparseCode "Reparse-point directory is not allowed: $($Item.FullName)"
    }
}

function Get-StrictJson {
    param([string]$Path)
    try {
        $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
        $text = [System.IO.File]::ReadAllText($Path, $strictUtf8)
        return $text | ConvertFrom-Json
    } catch {
        Stop-Structured 'OWNERSHIP_MARKER_INVALID' $_.Exception.Message
    }
}

function Get-TreeEntriesWithoutReparsePoints {
    param([string]$Root)
    $entries = @()
    $pending = New-Object System.Collections.Generic.Queue[string]
    $pending.Enqueue($Root)
    while ($pending.Count -gt 0) {
        $directory = $pending.Dequeue()
        try {
            $children = @(Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop)
        } catch {
            Stop-Structured 'PATH_INSPECTION_FAILED' $_.Exception.Message
        }
        foreach ($child in $children) {
            if (Test-IsReparsePoint $child) {
                Stop-Structured 'RUN_CONTENT_REPARSE_POINT' "Run content contains a reparse point: $($child.FullName)"
            }
            $entries += $child
            if ($child.PSIsContainer) {
                $pending.Enqueue($child.FullName)
            }
        }
    }
    return @($entries)
}

function Confirm-OwnedRun {
    param([string]$ExpectedRunDirectory)
    $candidate = Get-DirectChildEntry $script:TmpRoot $RunId
    if ($null -eq $candidate) { return $null }
    Assert-NormalDirectory $candidate 'RUN_DIRECTORY_REPARSE_POINT' 'RUN_DIRECTORY_INVALID'
    $candidateFull = [System.IO.Path]::GetFullPath($candidate.FullName)
    if (-not $candidateFull.Equals($ExpectedRunDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
        Stop-Structured 'RUN_IDENTITY_CHANGED' "Run directory identity changed: $candidateFull"
    }

    $entries = @(Get-TreeEntriesWithoutReparsePoints $candidateFull)
    $markerPath = Join-Path $candidateFull $markerName
    $markerEntry = @($entries | Where-Object {
        $_.FullName.Equals($markerPath, [System.StringComparison]::OrdinalIgnoreCase)
    })
    if ($markerEntry.Count -eq 0) {
        Stop-Structured 'OWNERSHIP_MARKER_MISSING' "Ownership marker is missing: $markerPath"
    }
    if ($markerEntry.Count -ne 1 -or $markerEntry[0].PSIsContainer) {
        Stop-Structured 'OWNERSHIP_MARKER_INVALID' "Ownership marker is not a unique file: $markerPath"
    }
    $marker = Get-StrictJson $markerPath
    $requiredProperties = @('schemaVersion', 'owner', 'runId', 'physicalProjectRoot')
    $hasRequiredProperties = @(
        $requiredProperties | Where-Object { $null -eq $marker.PSObject.Properties[$_] }
    ).Count -eq 0
    $valid = $hasRequiredProperties -and (
        $marker.schemaVersion -eq $schemaVersion -and
        $marker.owner -eq $markerOwner -and
        $marker.runId -eq $RunId -and
        ([string]$marker.physicalProjectRoot).Equals(
            $script:ProjectRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )
    if (-not $valid) {
        Stop-Structured 'OWNERSHIP_MARKER_INVALID' "Ownership marker fields do not match this run"
    }
    $markerStream = [System.IO.File]::OpenRead($markerPath)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = ([System.BitConverter]::ToString($sha256.ComputeHash($markerStream))).Replace('-', '')
    } finally {
        $sha256.Dispose()
        $markerStream.Dispose()
    }
    return [pscustomobject]@{
        Directory = $candidateFull
        CreationTicks = $candidate.CreationTimeUtc.Ticks
        MarkerHash = $hash
        MarkerPath = $markerPath
        Entries = @($entries)
    }
}

$script:ResolvedRunDirectory = $RunDirectory
try {
    if ($RunId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' -or $RunId -in @('.', '..')) {
        Stop-Structured 'INVALID_RUN_ID' "RunId is not a safe leaf name"
    }
    if (($Action -eq 'Initialize') -ne ($Reason -eq 'initialization')) {
        Stop-Structured 'INVALID_REASON' "Initialize requires initialization; Cleanup requires an exit reason"
    }

    $script:ProjectRoot = $userProfile.workspaceRoot
    $projectEntry = Get-Item -LiteralPath $script:ProjectRoot -Force -ErrorAction Stop
    Assert-NormalDirectory $projectEntry 'PROJECT_ROOT_REPARSE_POINT' 'PROJECT_ROOT_INVALID'

    $script:TmpRoot = [System.IO.Path]::GetFullPath((Join-Path $script:ProjectRoot '.tmp'))
    $tmpEntry = Get-Item -LiteralPath $script:TmpRoot -Force -ErrorAction Stop
    Assert-NormalDirectory $tmpEntry 'TMP_ROOT_REPARSE_POINT' 'TMP_ROOT_INVALID'
    if (-not ([System.IO.Path]::GetDirectoryName($tmpEntry.FullName)).Equals(
        $script:ProjectRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        Stop-Structured 'TMP_ROOT_INVALID' "The .tmp root is not a direct project child"
    }

    $expectedRunDirectory = [System.IO.Path]::GetFullPath((Join-Path $script:TmpRoot $RunId))
    $script:ResolvedRunDirectory = [System.IO.Path]::GetFullPath($RunDirectory)
    if (
        -not $script:ResolvedRunDirectory.Equals(
            $expectedRunDirectory,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not ([System.IO.Path]::GetDirectoryName($script:ResolvedRunDirectory)).Equals(
            $script:TmpRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        Stop-Structured 'RUN_DIRECTORY_OUTSIDE_TMP' "RunDirectory is not the requested direct child of .tmp"
    }

    if ($Action -eq 'Initialize') {
        if ($null -ne (Get-DirectChildEntry $script:TmpRoot $RunId)) {
            Stop-Structured 'RUN_DIRECTORY_ALREADY_EXISTS' "Run directory already exists"
        }
        New-Item -ItemType Directory -Path $script:ResolvedRunDirectory -ErrorAction Stop | Out-Null
        $createdEntry = Get-DirectChildEntry $script:TmpRoot $RunId
        Assert-NormalDirectory $createdEntry 'RUN_DIRECTORY_REPARSE_POINT' 'RUN_DIRECTORY_INVALID'
        $preMarkerEntries = @(Get-TreeEntriesWithoutReparsePoints $script:ResolvedRunDirectory)
        if ($preMarkerEntries.Count -ne 0) {
            Stop-Structured 'RUN_DIRECTORY_NOT_EMPTY' "New run directory was not empty before marker creation"
        }
        $marker = [pscustomobject][ordered]@{
            schemaVersion = $schemaVersion
            owner = $markerOwner
            runId = $RunId
            physicalProjectRoot = $script:ProjectRoot
        }
        $markerJson = $marker | ConvertTo-Json -Depth 4
        $markerPath = Join-Path $script:ResolvedRunDirectory $markerName
        $stream = New-Object System.IO.FileStream(
            $markerPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        try {
            $writer = New-Object System.IO.StreamWriter(
                $stream,
                (New-Object System.Text.UTF8Encoding($false))
            )
            try { $writer.Write($markerJson) } finally { $writer.Dispose() }
        } finally {
            if ($null -ne $stream) { $stream.Dispose() }
        }
        $identity = Confirm-OwnedRun $script:ResolvedRunDirectory
        if ($null -eq $identity) {
            Stop-Structured 'RUN_INITIALIZATION_FAILED' "Run disappeared after marker creation"
        }
        Write-StructuredResult 'ok' 'RUN_INITIALIZED' $null 0
    }

    $firstIdentity = Confirm-OwnedRun $script:ResolvedRunDirectory
    if ($null -eq $firstIdentity) {
        Write-StructuredResult 'ok' 'RUN_ALREADY_ABSENT' $null 0
    }
    $secondIdentity = Confirm-OwnedRun $script:ResolvedRunDirectory
    if (
        $null -eq $secondIdentity -or
        $firstIdentity.CreationTicks -ne $secondIdentity.CreationTicks -or
        $firstIdentity.MarkerHash -ne $secondIdentity.MarkerHash
    ) {
        Stop-Structured 'RUN_IDENTITY_CHANGED' "Run identity changed before deletion"
    }
    try {
        $files = @(
            $secondIdentity.Entries |
                Where-Object {
                    -not $_.PSIsContainer -and
                    -not $_.FullName.Equals(
                        $secondIdentity.MarkerPath,
                        [System.StringComparison]::OrdinalIgnoreCase
                    )
                }
        )
        foreach ($file in $files) {
            [System.IO.File]::Delete($file.FullName)
        }
        $directories = @(
            $secondIdentity.Entries |
                Where-Object { $_.PSIsContainer } |
                Sort-Object { $_.FullName.Length } -Descending
        )
        foreach ($directory in $directories) {
            [System.IO.Directory]::Delete($directory.FullName, $false)
        }
        $finalIdentity = Confirm-OwnedRun $script:ResolvedRunDirectory
        if (
            $null -eq $finalIdentity -or
            $secondIdentity.CreationTicks -ne $finalIdentity.CreationTicks -or
            $secondIdentity.MarkerHash -ne $finalIdentity.MarkerHash
        ) {
            Stop-Structured 'RUN_IDENTITY_CHANGED' "Run identity changed during deletion"
        }
        [System.IO.Directory]::Delete($script:ResolvedRunDirectory, $true)
    } catch {
        if ($_.Exception.Message.StartsWith('RUN_IDENTITY_CHANGED|')) { throw }
        Stop-Structured 'CLEANUP_FAILED' $_.Exception.Message
    }
    if ($null -ne (Get-DirectChildEntry $script:TmpRoot $RunId)) {
        Stop-Structured 'CLEANUP_INCOMPLETE' "Run directory still exists after deletion"
    }
    Write-StructuredResult 'ok' 'RUN_CLEANED' $null 0
} catch {
    $rawMessage = $_.Exception.Message
    $separator = $rawMessage.IndexOf('|')
    if ($separator -gt 0) {
        $code = $rawMessage.Substring(0, $separator)
        $message = $rawMessage.Substring($separator + 1)
    } else {
        $code = 'CLEANUP_HELPER_FAILED'
        $message = $rawMessage
    }
    Write-StructuredResult 'error' $code $message 1
}
