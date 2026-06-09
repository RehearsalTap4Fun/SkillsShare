# Multi-Agent Skills and MCP Sync Design

## Goal

Create a repository in `/Users/tap4fun/Documents/SkillsShare` that acts as the single source of truth for shared AI agent assets across multiple devices and multiple agent applications.

The first version must support:

- Agent apps: Codex, Claude Code, Cursor
- Operating systems: macOS and Windows
- Distribution model: symbolic links first, copy fallback when links are unavailable

The repository should let the user update shared skills and MCP-related source content once, then apply the changes to each local agent installation through a consistent sync workflow.

## Non-Goals

- Linux support in v1
- Secret storage inside Git
- Syncing agent caches, sessions, logs, or generated local state
- Full agent autodiscovery for every possible install layout

## Design Summary

The repository will separate shared source content from generated per-agent local configuration.

- Shared `skills` content lives inside the repo and is linked into agent-specific local directories where possible.
- Shared `mcp` source content also lives inside the repo, but final agent config files are rendered from templates because each device and agent can require different absolute paths or command locations.
- Each device has a small device manifest that records OS, path roots, and other non-secret local differences.
- Sync scripts perform three tasks:
  - validate required local inputs
  - create or refresh links for shared content
  - render agent-specific configuration files from templates

When symbolic links are blocked on Windows, the sync flow falls back to copying only the affected shared content while keeping the same source-of-truth structure.

## Repository Layout

```text
SkillsShare/
  docs/
    superpowers/
      specs/
        2026-06-09-multi-agent-sync-design.md
  skills/
    shared/
      README.md
    codex/
      README.md
    claude-code/
      README.md
    cursor/
      README.md
  mcp/
    servers/
      README.md
    templates/
      codex/
        mcp.json.tmpl
      claude-code/
        settings.json.tmpl
      cursor/
        mcp.json.tmpl
  devices/
    macos.example.json
    windows.example.json
  env/
    .env.example
  scripts/
    sync.sh
    sync.ps1
    render-config.js
    validate-config.js
  .gitignore
  README.md
```

## Shared Skills Strategy

The repository contains shared skill source directories:

- `skills/shared`: common skills intended for more than one agent
- `skills/codex`: Codex-specific skill content
- `skills/claude-code`: Claude Code-specific skill content
- `skills/cursor`: Cursor-specific skill content

The sync script will map these source directories into each agent's local skill path.

Primary behavior:

- Create symbolic links from local agent directories to repo directories
- Replace stale links that point to old locations
- Create parent directories when missing

Fallback behavior:

- If link creation fails on Windows, copy the source directory into the target path
- Mark copied targets so later sync runs can refresh them cleanly

This keeps day-to-day maintenance link-first while remaining usable on Windows systems without symlink support or without Developer Mode enabled.

## MCP Strategy

MCP should not be synchronized as a single final config file because:

- agent config schemas differ
- command paths differ by OS
- repository locations differ by device
- some agent configs may need different nesting or wrapper keys

Instead:

- source MCP assets live in `mcp/servers/`
- per-agent config templates live in `mcp/templates/<agent>/`
- sync scripts render final files using repo paths and device manifest values

The first version will generate placeholder-safe, editable config stubs for:

- Codex
- Claude Code
- Cursor

The generated files are meant to be inspected and adjusted if the user has unusual local install paths, but the defaults should cover standard layouts documented in the repo examples.

## Device Configuration

Each machine gets a device manifest JSON file outside secrets management concerns.

Expected contents:

- `deviceName`
- `os`: `macos` or `windows`
- `repoRoot`
- `homeDir`
- per-agent config roots
- optional executable overrides such as `nodePath` or `pythonPath`

The repo will ship with examples only:

- `devices/macos.example.json`
- `devices/windows.example.json`

The user creates real device files from these examples and keeps device-specific values there. Secrets remain outside Git and may be provided via environment variables.

## Sync Flow

The sync flow is:

1. Load the selected device manifest
2. Validate required fields and target paths
3. Ensure shared source directories exist
4. Create or refresh links for skill directories
5. Fall back to copy mode for any target that cannot be linked
6. Render MCP config files from templates into each agent's config location
7. Print a concise summary of actions and any manual follow-up

Both macOS and Windows get first-class sync entry points:

- `scripts/sync.sh`
- `scripts/sync.ps1`

Shared rendering and validation logic lives in Node scripts so that behavior stays consistent across shells.

## Agent-Specific Boundaries

### Codex

- Manage shared skills path mapping only
- Render a generated MCP-related config artifact from template
- Avoid touching cache or app-managed runtime state

### Claude Code

- Manage shared skills path mapping only where the local installation model supports it
- Render generated config files from template
- Avoid overwriting unrelated user settings when possible by generating a dedicated managed block or dedicated file

### Cursor

- Focus on generated MCP/config artifacts and optional skill-like shared prompt directories if applicable
- Avoid assumptions about unsupported internal cache layout

Because local installation details can vary, the scripts should centralize path definitions in the device manifest instead of hardcoding them in business logic.

## Error Handling

The sync scripts should fail early for:

- missing device manifest
- unsupported OS value
- missing required path fields
- missing template files

The sync scripts should warn, not hard fail, for:

- optional agent not enabled on the current device
- target directory absent but creatable
- link creation failure when copy fallback succeeds

The scripts should never delete arbitrary directories. They may only replace:

- managed symbolic links
- managed copied directories previously created by the sync tool
- generated config files explicitly listed in the templates output mapping

## Testing Strategy

The repository should include lightweight validation checks rather than a heavy test harness in v1.

Minimum verification:

- device manifest schema validation
- template rendering dry-run
- path summary output per agent
- clear reporting of link mode versus copy fallback mode

Manual verification steps should be documented in `README.md` for:

- macOS first-time setup
- Windows first-time setup
- verifying generated config output
- verifying that updating a shared skill is reflected after sync

## Implementation Notes

- Use ASCII-only file content by default
- Use Node.js for config rendering and validation to keep cross-platform logic in one place
- Keep shell wrappers thin
- Make target agent path mapping explicit and editable
- Prefer idempotent sync behavior so repeated runs are safe

## Recommended Initial Deliverables

The first implementation should create:

- repository README
- `.gitignore`
- example device manifests for macOS and Windows
- placeholder `skills` directories
- placeholder `mcp` directories and templates
- `scripts/render-config.js`
- `scripts/validate-config.js`
- `scripts/sync.sh`
- `scripts/sync.ps1`

## Open Assumptions Chosen For v1

- The user is comfortable editing a per-device JSON file
- Node.js is available on machines that will run the sync scripts
- The user prefers link-first behavior even when Windows may require fallback copy mode
- The initial repository can ship placeholder templates for agent config outputs, with path customization delegated to device manifests
