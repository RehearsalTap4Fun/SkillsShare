param(
  [Parameter(Mandatory = $true)]
  [string]$Device,

  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$ManagedMarker = ".skills-share-managed"

function Get-Manifest {
  param([string]$ManifestPath)

  $resolved = [System.IO.Path]::GetFullPath((Join-Path $RootDir $ManifestPath))
  return Get-Content -Raw -Path $resolved | ConvertFrom-Json
}

function Ensure-ParentDir {
  param([string]$PathValue)

  $parent = Split-Path -Parent $PathValue
  if ($DryRun) {
    Write-Output "[dry-run] ensure parent: $parent"
    return
  }
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
}

function Copy-ManagedDir {
  param(
    [string]$SourceDir,
    [string]$TargetDir
  )

  if ((Test-Path $TargetDir) -and -not (Test-Path (Join-Path $TargetDir $ManagedMarker))) {
    Write-Output "Skip unmanaged directory: $TargetDir"
    return
  }

  if (Test-Path $TargetDir) {
    Remove-Item -Recurse -Force $TargetDir
  }

  Copy-Item -Recurse -Force $SourceDir $TargetDir
  New-Item -ItemType File -Force -Path (Join-Path $TargetDir $ManagedMarker) | Out-Null
  Write-Output "Copied $SourceDir -> $TargetDir"
}

function Link-OrCopy {
  param(
    [string]$SourceDir,
    [string]$TargetDir
  )

  Ensure-ParentDir -PathValue $TargetDir

  if ($DryRun) {
    Write-Output "[dry-run] link $SourceDir -> $TargetDir"
    return
  }

  if (Test-Path $TargetDir) {
    $item = Get-Item -Force $TargetDir
    if ($item.LinkType) {
      Remove-Item -Force $TargetDir
    } elseif ($item.PSIsContainer) {
      if (Test-Path (Join-Path $TargetDir $ManagedMarker)) {
        Remove-Item -Recurse -Force $TargetDir
      } else {
        Write-Output "Skip unmanaged directory: $TargetDir"
        return
      }
    } else {
      Remove-Item -Force $TargetDir
    }
  }

  try {
    New-Item -ItemType SymbolicLink -Path $TargetDir -Target $SourceDir | Out-Null
    Write-Output "Linked $SourceDir -> $TargetDir"
  } catch {
    Copy-ManagedDir -SourceDir $SourceDir -TargetDir $TargetDir
  }
}

Push-Location $RootDir
try {
  node scripts/validate-config.js --device $Device
  $manifest = Get-Manifest -ManifestPath $Device

  if ($manifest.agents.codex.enabled) {
    Link-OrCopy -SourceDir (Join-Path $RootDir "skills/shared") -TargetDir (Join-Path $manifest.agents.codex.skillsTargetDir "shared")
    Link-OrCopy -SourceDir (Join-Path $RootDir "skills/codex") -TargetDir (Join-Path $manifest.agents.codex.skillsTargetDir "codex")
  } else {
    Write-Output "Skip disabled agent: codex"
  }

  if ($manifest.agents.claudeCode.enabled) {
    Link-OrCopy -SourceDir (Join-Path $RootDir "skills/shared") -TargetDir (Join-Path $manifest.agents.claudeCode.skillsTargetDir "shared")
    Link-OrCopy -SourceDir (Join-Path $RootDir "skills/claude-code") -TargetDir (Join-Path $manifest.agents.claudeCode.skillsTargetDir "claude-code")
  } else {
    Write-Output "Skip disabled agent: claudeCode"
  }

  if ($manifest.agents.cursor.enabled) {
    Link-OrCopy -SourceDir (Join-Path $RootDir "skills/shared") -TargetDir (Join-Path $manifest.agents.cursor.skillsTargetDir "shared")
    Link-OrCopy -SourceDir (Join-Path $RootDir "skills/cursor") -TargetDir (Join-Path $manifest.agents.cursor.skillsTargetDir "cursor")
  } else {
    Write-Output "Skip disabled agent: cursor"
  }

  if ($DryRun) {
    node scripts/render-config.js --device $Device --dry-run
  } else {
    node scripts/render-config.js --device $Device
  }

  Write-Output "Sync complete"
} finally {
  Pop-Location
}
