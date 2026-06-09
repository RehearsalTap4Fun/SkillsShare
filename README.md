# SkillsShare

Central source of truth for shared AI agent skills and MCP configuration templates across devices.

## Overview

This repository manages shared content for:

- Codex
- Claude Code
- Cursor

The first version targets:

- macOS
- Windows

The repository uses a link-first model for shared skills and a template-rendering model for generated config files. When symbolic links are unavailable on Windows, the sync flow falls back to copying managed directories.

## What This Repository Manages

- Shared skill source directories
- MCP source placeholders and config templates
- Per-device manifest examples
- Cross-platform validation and sync scripts

## What This Repository Does Not Manage

- API keys or secrets
- Agent cache files
- Session files
- Logs
- Arbitrary user settings unrelated to managed outputs

## Repository Layout

```text
skills/
  shared/
  codex/
  claude-code/
  cursor/
mcp/
  servers/
  templates/
devices/
env/
scripts/
docs/
```

## First-time Setup

1. Copy the matching device example into a real device manifest:

```bash
cp devices/macos.example.json devices/my-macbook.json
```

PowerShell:

```powershell
Copy-Item devices/windows.example.json devices/my-windows-box.json
```

2. Edit the copied manifest and set your actual paths:

- `repoRoot`
- `homeDir`
- `executables.nodePath`
- `executables.pythonPath`
- each agent `skillsTargetDir`
- each agent `configTargetFile`

3. Make sure Node.js is installed on the machine that will run sync.

## Device Manifest Setup

Each device manifest is a JSON file with:

- `deviceName`
- `os`
- `repoRoot`
- `homeDir`
- `executables`
- `agents`

Agent settings currently include:

- `enabled`
- `skillsTargetDir`
- `configTargetFile`

The scripts read only your chosen device manifest. Real device manifests are ignored by Git through `.gitignore`.

## Validation

Validate a manifest before syncing:

```bash
node scripts/validate-config.js --device devices/macos.example.json
```

PowerShell:

```powershell
node scripts/validate-config.js --device devices/windows.example.json
```

## Dry-run Sync

Dry-run on macOS:

```bash
bash scripts/sync.sh --device devices/macos.example.json --dry-run
```

Dry-run on Windows:

```powershell
pwsh -File scripts/sync.ps1 -Device devices/windows.example.json -DryRun
```

Dry-run validates the manifest, prints planned link actions, and prints generated config targets without writing files.

## Real Sync

Real sync on macOS:

```bash
bash scripts/sync.sh --device devices/my-macbook.json
```

Real sync on Windows:

```powershell
pwsh -File scripts/sync.ps1 -Device devices/my-windows-box.json
```

The sync flow:

1. validates your device manifest
2. creates managed links for shared skill directories
3. falls back to managed directory copy on Windows if links fail
4. renders generated config files from templates

## Windows Symlink Fallback

Windows symlink creation may fail if Developer Mode is disabled or if the shell lacks permission to create links.

When that happens, the PowerShell sync script:

- creates a copied managed directory instead of a link
- writes a `.skills-share-managed` marker file into the copied directory
- refreshes only directories it previously managed

This keeps updates predictable without deleting unrelated user content.

## Managed File Safety

The sync scripts are intentionally conservative.

They may replace:

- managed symbolic links
- managed copied directories that contain `.skills-share-managed`
- generated config files listed in the selected manifest

They do not delete arbitrary directories or caches.

## Verification

Recommended checks after setup:

```bash
node scripts/validate-config.js --device devices/macos.example.json
node scripts/render-config.js --device devices/macos.example.json --dry-run
bash scripts/sync.sh --device devices/macos.example.json --dry-run
```

Windows:

```powershell
node scripts/validate-config.js --device devices/windows.example.json
node scripts/render-config.js --device devices/windows.example.json --dry-run
pwsh -File scripts/sync.ps1 -Device devices/windows.example.json -DryRun
```

After a real sync, change a file under `skills/shared/` and run sync again to verify the update flows through to the agent target directory.
