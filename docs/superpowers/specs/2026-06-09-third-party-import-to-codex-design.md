# Third-Party Import to Codex Design

## Goal

Add a repeatable import pipeline that discovers user-installed third-party skills and MCP content from local Cursor and Claude Code installations, normalizes the results into this repository, and syncs the merged output into Codex.

The first version targets the current machine and must:

- import third-party skills from `~/.cursor/skills-cursor`
- import project-level MCP assets from `~/.cursor/projects/*/mcps/*`
- scan Claude Code safely without importing secrets from `~/.claude/settings.json`
- merge same-name items by most recent file modification time
- sync merged skill output into Codex without touching `.codex/skills/.system`

## Non-Goals

- importing built-in Codex system skills
- importing Claude environment variables or API keys
- rewriting Codex application-managed cache or session files
- translating Cursor MCP metadata into an active Codex runtime integration beyond storing normalized source artifacts

## Source Discovery

### Cursor

- Skills source: `~/.cursor/skills-cursor/<skill>/SKILL.md`
- MCP source: `~/.cursor/projects/*/mcps/<server>/`

The importer should copy only stable source artifacts:

- for skills: the full skill directory
- for MCPs: the full server directory that contains files like `SERVER_METADATA.json` and `INSTRUCTIONS.md`

Ephemeral project identifiers in Cursor project paths should not leak into normalized output names beyond provenance metadata.

### Claude Code

The importer should scan `~/.claude` for third-party skill-like or MCP-like structures, but current local evidence shows only `settings.json` with environment variables. The importer should record that no safe importable third-party assets were found instead of importing the file.

## Repository Layout Additions

```text
imports/
  cursor/
    skills/
    mcps/
  claude/
    scan-results.json
  manifest.json
skills/
  imported/
mcp/
  imported/
```

## Import Strategy

`scripts/import-third-party.js` should:

- scan known source directories
- copy discovered third-party assets into `imports/cursor/...`
- remove previously managed import outputs before rewriting them
- preserve source modification times in a manifest
- write a machine-readable `imports/manifest.json`

The manifest should include, per imported item:

- `type`: `skill` or `mcp`
- `name`
- `sourceAgent`
- `sourcePath`
- `sourceMtimeMs`
- `importPath`

## Merge Strategy

`scripts/merge-imports.js` should:

- read all imported assets from `imports/`
- normalize skills into `skills/imported/<name>/`
- normalize MCPs into `mcp/imported/<name>/`
- resolve name collisions by choosing the item with the latest `sourceMtimeMs`
- record both winner and loser metadata in `imports/manifest.json`

If two items have the same name and same timestamp, prefer a deterministic source order:

1. Cursor
2. Claude Code

This avoids unstable output across repeated runs.

## Codex Sync Strategy

The existing sync flow should gain Codex-aware imported content support:

- link `skills/imported` into `Codex` under `~/.codex/skills/imported`
- do not touch `.codex/skills/.system`
- generate or copy normalized MCP artifacts into a dedicated Codex-managed location, initially as source artifacts under the repository plus the existing generated config output

The first version only needs to guarantee real Codex skill availability. MCP normalization must be preserved in the repo and reflected in the generated Codex config file as metadata if possible, without breaking existing config generation.

## Conflict Policy

For same-name third-party assets across sources:

- compare `sourceMtimeMs`
- newest wins automatically
- loser remains recorded in the manifest
- no user prompt in the default path

## Safety Rules

- Never import `~/.claude/settings.json` content into repo-managed source files
- Never overwrite unmanaged directories inside `~/.codex/skills`
- Only rewrite repo-managed `imports/`, `skills/imported/`, and `mcp/imported/`
- Keep all generated state inspectable through `imports/manifest.json`

## Verification

The implementation must be verified with:

- importer dry-run or real run on current machine
- merger run on current machine
- Codex sync dry-run
- Codex real sync for imported skills
- spot-check that `~/.codex/skills/imported` contains the merged third-party skills
