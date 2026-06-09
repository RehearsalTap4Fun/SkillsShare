# Third-Party Import to Codex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repeatable importer and merger that pulls third-party skills and MCP source artifacts from Cursor and Claude Code into this repository, then syncs the merged skill output into Codex.

**Architecture:** Add one script to inventory and copy source assets from external agent directories into `imports/`, a second script to normalize and merge those assets into repo-managed shared directories, and extend the existing sync script so Codex receives `skills/imported` without disturbing system skills. Use a manifest file to preserve provenance and conflict decisions.

**Tech Stack:** Node.js, Bash, JSON, filesystem copy/link operations

---

### Task 1: Add import and merge directory structure

**Files:**
- Create: `imports/.gitkeep`
- Create: `skills/imported/.gitkeep`
- Create: `mcp/imported/.gitkeep`

- [ ] **Step 1: Write the failing test**

Expect these paths to exist:

```text
imports/
skills/imported/
mcp/imported/
```

- [ ] **Step 2: Run test to verify it fails**

Run: `test -d imports && test -d skills/imported && test -d mcp/imported`
Expected: FAIL because the directories do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create the directories with `.gitkeep` placeholders.

- [ ] **Step 4: Run test to verify it passes**

Run: `test -d imports && test -d skills/imported && test -d mcp/imported`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add imports skills/imported mcp/imported
git commit -m "chore: add import workspace directories"
```

### Task 2: Implement third-party source importer

**Files:**
- Create: `scripts/import-third-party.js`

- [ ] **Step 1: Write the failing test**

Run target command:

```bash
node scripts/import-third-party.js
```

Expected:

- `imports/cursor/skills/` populated from `~/.cursor/skills-cursor`
- `imports/cursor/mcps/` populated from `~/.cursor/projects/*/mcps/*`
- `imports/claude/scan-results.json` created
- `imports/manifest.json` created

- [ ] **Step 2: Run test to verify it fails**

Run: `node scripts/import-third-party.js`
Expected: FAIL because the script does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement a Node script that:

- removes previously managed `imports/cursor` and `imports/claude`
- scans Cursor skills and copies each skill directory
- scans Cursor MCP directories and copies one directory per MCP name, keeping the newest source on name collision
- scans Claude safely and records no importable assets when only `settings.json` env data exists
- writes `imports/manifest.json` and `imports/claude/scan-results.json`

- [ ] **Step 4: Run test to verify it passes**

Run: `node scripts/import-third-party.js`
Expected: PASS with imported asset counts and manifest output.

- [ ] **Step 5: Commit**

```bash
git add scripts/import-third-party.js imports
git commit -m "feat: add third-party source importer"
```

### Task 3: Implement merge pipeline

**Files:**
- Create: `scripts/merge-imports.js`

- [ ] **Step 1: Write the failing test**

Run target command:

```bash
node scripts/merge-imports.js
```

Expected:

- `skills/imported/<name>/` created from imported skill winners
- `mcp/imported/<name>/` created from imported MCP winners
- `imports/manifest.json` updated with merge winners and conflicts

- [ ] **Step 2: Run test to verify it fails**

Run: `node scripts/merge-imports.js`
Expected: FAIL because the script does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement a Node script that:

- reads `imports/manifest.json`
- rewrites `skills/imported/` and `mcp/imported/`
- copies the newest imported item per name and type
- appends merge resolution metadata back into the manifest

- [ ] **Step 4: Run test to verify it passes**

Run: `node scripts/merge-imports.js`
Expected: PASS with merged asset counts and conflict summary.

- [ ] **Step 5: Commit**

```bash
git add scripts/merge-imports.js imports/manifest.json skills/imported mcp/imported
git commit -m "feat: add import merge pipeline"
```

### Task 4: Extend Codex sync to include imported output

**Files:**
- Modify: `scripts/sync.sh`
- Modify: `README.md`

- [ ] **Step 1: Write the failing test**

Run target command:

```bash
bash scripts/sync.sh --device devices/local-macos.json --codex-only --dry-run
```

Expected:

- dry-run shows `skills/imported` syncing into Codex
- no non-Codex agents are touched in `--codex-only` mode

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/sync.sh --device devices/macos.example.json --codex-only --dry-run`
Expected: FAIL because `--codex-only` is not implemented.

- [ ] **Step 3: Write minimal implementation**

Extend the shell sync script to:

- accept `--codex-only`
- link `skills/imported` into `<codex skills root>/imported`
- skip Claude Code and Cursor when `--codex-only` is set
- document the new flow in `README.md`

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/sync.sh --device devices/macos.example.json --codex-only --dry-run`
Expected: PASS with Codex-only sync actions listed.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync.sh README.md
git commit -m "feat: sync imported content to codex"
```

### Task 5: Perform local import and Codex sync verification

**Files:**
- Create: `devices/local-macos.json`

- [ ] **Step 1: Write the failing test**

Verification command set:

```bash
node scripts/import-third-party.js
node scripts/merge-imports.js
bash scripts/sync.sh --device devices/local-macos.json --codex-only --dry-run
bash scripts/sync.sh --device devices/local-macos.json --codex-only
find ~/.codex/skills/imported -maxdepth 2 -type f -name 'SKILL.md'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `test -f devices/local-macos.json`
Expected: FAIL because the local device file does not exist.

- [ ] **Step 3: Write minimal implementation**

Create an untracked local device manifest that points Codex at:

- `skillsTargetDir`: `/Users/tap4fun/.codex/skills`
- `configTargetFile`: `/Users/tap4fun/.codex/mcp.generated.json`

Disable non-target agents in that manifest for this verification.

- [ ] **Step 4: Run test to verify it passes**

Run the verification command set above.
Expected: PASS, and `~/.codex/skills/imported` contains imported third-party skills.

- [ ] **Step 5: Commit**

```bash
git add scripts README.md docs/superpowers/specs/2026-06-09-third-party-import-to-codex-design.md docs/superpowers/plans/2026-06-09-third-party-import-to-codex-implementation.md
git commit -m "feat: import third-party content into codex"
```

## Self-Review

- Spec coverage: source discovery, repository layout additions, import strategy, merge strategy, Codex sync, conflict policy, and verification are all covered.
- Placeholder scan: no unresolved placeholders remain.
- Type consistency: manifest field names and script names are consistent across tasks.
