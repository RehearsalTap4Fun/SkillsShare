# Shared user-owned environment profile. Loading this file performs no writes.
function Get-K1ConfiguredPath {
    param([object]$Value, [string]$Field)
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value) -or
        $Value -match '[<>]|\$\{|REPLACE_ME|YOUR[_-]|(?:^|[\\/])EXAMPLE(?:[\\/]|$)' -or
        $Value -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+)') {
        throw "USER_CONFIG_PATH_REQUIRED|Configure an absolute path for $Field"
    }
    $full = [System.IO.Path]::GetFullPath($Value).TrimEnd([char]92, [char]47)
    if ($full -eq [System.IO.Path]::GetPathRoot($full).TrimEnd([char]92, [char]47)) {
        throw 'USER_CONFIG_ROOT_NOT_ALLOWED|A filesystem root cannot be a configured workspace or repository'
    }
    $current = $full
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
            if (-not $item.PSIsContainer -or
                ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "USER_CONFIG_PATH_UNSAFE|Configured $Field must use normal directories without reparse ancestors"
            }
        }
        $current = [System.IO.Path]::GetDirectoryName($current)
    }
    return $full
}

function Test-K1PathOverlap {
    param([string]$Left, [string]$Right)
    return $Left.Equals($Right, [System.StringComparison]::OrdinalIgnoreCase) -or
        $Left.StartsWith($Right + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -or
        $Right.StartsWith($Left + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-K1UserProfile {
    $profilePath = [Environment]::GetEnvironmentVariable('K1_REVIEW_PROFILE')
    if ([string]::IsNullOrWhiteSpace($profilePath)) {
        throw 'USER_CONFIG_REQUIRED|Set K1_REVIEW_PROFILE to your private profile JSON'
    }
    if ($profilePath -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+)' -or
        $profilePath -match '[<>]|\$\{|REPLACE_ME|YOUR[_-]' -or
        -not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
        throw 'USER_CONFIG_INVALID|K1_REVIEW_PROFILE must identify an existing absolute JSON file'
    }
    try {
        $profile = Get-Content -LiteralPath $profilePath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw 'USER_CONFIG_INVALID|Unable to parse the private profile JSON'
    }
    if ($null -eq $profile -or $null -eq $profile.PSObject.Properties['schemaVersion'] -or
        $profile.schemaVersion -ne 1 -or $null -eq $profile.PSObject.Properties['workspaceRoot'] -or
        $null -eq $profile.PSObject.Properties['repositories']) {
        throw 'USER_CONFIG_INVALID|Profile requires schemaVersion 1, workspaceRoot and repositories'
    }
    $workspace = Get-K1ConfiguredPath $profile.workspaceRoot 'workspaceRoot'
    $repositories = @{}
    foreach ($role in @('client', 'server', 'config')) {
        if ($null -eq $profile.repositories -or $null -eq $profile.repositories.PSObject.Properties[$role]) {
            throw "USER_CONFIG_PATH_REQUIRED|Configure repositories.$role"
        }
        $repository = Get-K1ConfiguredPath $profile.repositories.$role "repositories.$role"
        if (Test-K1PathOverlap $workspace $repository) {
            throw 'USER_CONFIG_WORKSPACE_OVERLAP|Workspace and source repositories must not overlap'
        }
        if (@($repositories.Values | Where-Object {
            $_.Equals($repository, [System.StringComparison]::OrdinalIgnoreCase)
        }).Count -gt 0) {
            throw 'USER_CONFIG_REPOSITORIES_NOT_DISTINCT|Each role must identify a distinct source repository'
        }
        $repositories[$role] = $repository
    }
    return [pscustomobject]@{ workspaceRoot = $workspace; repositories = $repositories }
}
