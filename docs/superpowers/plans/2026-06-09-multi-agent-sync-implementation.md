# Multi-Agent Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Git-backed repository skeleton that centralizes shared skills and MCP source content for Codex, Claude Code, and Cursor across macOS and Windows, using symbolic links first and copy fallback when needed.

**Architecture:** Keep shared source content inside repo-managed `skills/` and `mcp/` directories, then use thin shell/PowerShell entry points plus shared Node scripts to validate device manifests, render agent config templates, and synchronize managed targets. Device-specific paths live in JSON manifests so link targets and generated configs remain editable without code changes.

**Tech Stack:** Git, Node.js, Bash, PowerShell, JSON, plain-text templates

---

### Task 1: Create repository skeleton and shared placeholder content

**Files:**
- Create: `README.md`
- Create: `.gitignore`
- Create: `skills/shared/README.md`
- Create: `skills/codex/README.md`
- Create: `skills/claude-code/README.md`
- Create: `skills/cursor/README.md`
- Create: `mcp/servers/README.md`
- Create: `mcp/templates/codex/mcp.json.tmpl`
- Create: `mcp/templates/claude-code/settings.json.tmpl`
- Create: `mcp/templates/cursor/mcp.json.tmpl`
- Create: `env/.env.example`

- [ ] **Step 1: Write the failing test**

Create a file inventory expectation for the initial repo scaffold:

```text
README.md
.gitignore
skills/shared/README.md
skills/codex/README.md
skills/claude-code/README.md
skills/cursor/README.md
mcp/servers/README.md
mcp/templates/codex/mcp.json.tmpl
mcp/templates/claude-code/settings.json.tmpl
mcp/templates/cursor/mcp.json.tmpl
env/.env.example
```

- [ ] **Step 2: Run test to verify it fails**

Run: `find . -maxdepth 4 -type f | sort`
Expected: FAIL because the files above do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create the scaffold files with concise placeholder content:

```md
# SkillsShare

Central source of truth for shared AI agent skills and MCP configuration templates.
```

```gitignore
node_modules/
dist/
tmp/
devices/*.json
!devices/*.example.json
.DS_Store
Thumbs.db
```

```md
# Shared Skills

Place skills here that should be available to more than one agent.
```

```json
{
  "managedBy": "SkillsShare",
  "agent": "codex",
  "mcpServers": {}
}
```

```env
# Copy needed variables into your local shell profile or device-specific environment.
NODE_PATH=
PYTHON_PATH=
```

- [ ] **Step 4: Run test to verify it passes**

Run: `find . -maxdepth 4 -type f | sort`
Expected: PASS with the scaffold files listed.

- [ ] **Step 5: Commit**

```bash
git add README.md .gitignore skills mcp env
git commit -m "chore: add repository scaffold"
```

### Task 2: Add device manifest examples for macOS and Windows

**Files:**
- Create: `devices/macos.example.json`
- Create: `devices/windows.example.json`

- [ ] **Step 1: Write the failing test**

Define the required manifest keys that both examples must include:

```json
[
  "deviceName",
  "os",
  "repoRoot",
  "homeDir",
  "agents"
]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `test -f devices/macos.example.json && test -f devices/windows.example.json`
Expected: FAIL because neither example manifest exists.

- [ ] **Step 3: Write minimal implementation**

Create example manifests with the shared schema:

```json
{
  "deviceName": "macbook-example",
  "os": "macos",
  "repoRoot": "/Users/yourname/Documents/SkillsShare",
  "homeDir": "/Users/yourname",
  "executables": {
    "nodePath": "/opt/homebrew/bin/node",
    "pythonPath": "/usr/bin/python3"
  },
  "agents": {
    "codex": {
      "enabled": true,
      "skillsTargetDir": "/Users/yourname/.codex/skills/shared",
      "configTargetFile": "/Users/yourname/.codex/mcp.generated.json"
    },
    "claudeCode": {
      "enabled": true,
      "skillsTargetDir": "/Users/yourname/.claude/skills/shared",
      "configTargetFile": "/Users/yourname/.claude/settings.generated.json"
    },
    "cursor": {
      "enabled": true,
      "skillsTargetDir": "/Users/yourname/.cursor/skills/shared",
      "configTargetFile": "/Users/yourname/.cursor/mcp.generated.json"
    }
  }
}
```

```json
{
  "deviceName": "windows-example",
  "os": "windows",
  "repoRoot": "C:\\\\Users\\\\yourname\\\\Documents\\\\SkillsShare",
  "homeDir": "C:\\\\Users\\\\yourname",
  "executables": {
    "nodePath": "C:\\\\Program Files\\\\nodejs\\\\node.exe",
    "pythonPath": "C:\\\\Python311\\\\python.exe"
  },
  "agents": {
    "codex": {
      "enabled": true,
      "skillsTargetDir": "C:\\\\Users\\\\yourname\\\\.codex\\\\skills\\\\shared",
      "configTargetFile": "C:\\\\Users\\\\yourname\\\\.codex\\\\mcp.generated.json"
    },
    "claudeCode": {
      "enabled": true,
      "skillsTargetDir": "C:\\\\Users\\\\yourname\\\\.claude\\\\skills\\\\shared",
      "configTargetFile": "C:\\\\Users\\\\yourname\\\\.claude\\\\settings.generated.json"
    },
    "cursor": {
      "enabled": true,
      "skillsTargetDir": "C:\\\\Users\\\\yourname\\\\.cursor\\\\skills\\\\shared",
      "configTargetFile": "C:\\\\Users\\\\yourname\\\\.cursor\\\\mcp.generated.json"
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node -e "for (const f of ['devices/macos.example.json','devices/windows.example.json']) { const data=require('./'+f); for (const key of ['deviceName','os','repoRoot','homeDir','agents']) { if (!(key in data)) throw new Error(f + ' missing ' + key); } } console.log('ok')"`
Expected: PASS with `ok`.

- [ ] **Step 5: Commit**

```bash
git add devices
git commit -m "chore: add device manifest examples"
```

### Task 3: Implement shared manifest validation and template rendering logic

**Files:**
- Create: `scripts/validate-config.js`
- Create: `scripts/render-config.js`

- [ ] **Step 1: Write the failing test**

Define the first expected CLI behavior:

```bash
node scripts/validate-config.js --device devices/macos.example.json
node scripts/render-config.js --device devices/macos.example.json --dry-run
```

Expected behaviors:

- validator exits successfully for a valid example manifest
- renderer prints planned outputs without writing files in dry-run mode

- [ ] **Step 2: Run test to verify it fails**

Run: `node scripts/validate-config.js --device devices/macos.example.json`
Expected: FAIL because the script does not exist.

- [ ] **Step 3: Write minimal implementation**

Create a validator that:

- parses `--device <path>`
- loads JSON
- validates `deviceName`, `os`, `repoRoot`, `homeDir`, and `agents`
- validates agent objects contain `enabled`, `skillsTargetDir`, and `configTargetFile`
- exits non-zero with clear messages on invalid input

Create a renderer that:

- parses `--device <path>` and optional `--dry-run`
- loads template files for each enabled agent
- substitutes `{{REPO_ROOT}}`, `{{HOME_DIR}}`, `{{NODE_PATH}}`, and `{{PYTHON_PATH}}`
- prints target file mapping in dry-run mode
- writes rendered content when not in dry-run mode

Use this base helper structure:

```js
function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === '--dry-run') {
      args.dryRun = true;
      continue;
    }
    args[key.replace(/^--/, '')] = argv[i + 1];
    i += 1;
  }
  return args;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node scripts/validate-config.js --device devices/macos.example.json`
Expected: PASS with a concise success message.

Run: `node scripts/render-config.js --device devices/macos.example.json --dry-run`
Expected: PASS with a list of rendered target files and no writes.

- [ ] **Step 5: Commit**

```bash
git add scripts/validate-config.js scripts/render-config.js
git commit -m "feat: add manifest validation and template rendering"
```

### Task 4: Implement sync entry points for macOS and Windows

**Files:**
- Create: `scripts/sync.sh`
- Create: `scripts/sync.ps1`

- [ ] **Step 1: Write the failing test**

Define the expected sync entry behavior:

```bash
./scripts/sync.sh --device devices/macos.example.json --dry-run
pwsh -File scripts/sync.ps1 -Device devices/windows.example.json -DryRun
```

Expected behaviors:

- validate the manifest first
- render config targets in dry-run mode
- print link actions for shared skills
- skip writes in dry-run mode

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/sync.sh --device devices/macos.example.json --dry-run`
Expected: FAIL because the sync script does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/sync.sh` that:

- parses `--device` and optional `--dry-run`
- calls `node scripts/validate-config.js`
- calls `node scripts/render-config.js`
- invokes link-or-copy logic for `skills/shared`, `skills/codex`, `skills/claude-code`, and `skills/cursor`
- reports whether each target uses `link`, `copy-fallback`, or `dry-run`

Create `scripts/sync.ps1` with equivalent behavior for Windows:

- validate arguments
- call the same Node scripts
- try symlink creation first
- fall back to directory copy when symlink creation throws

Use a managed marker file for copied directories:

```text
.skills-share-managed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/sync.sh --device devices/macos.example.json --dry-run`
Expected: PASS with dry-run summary output.

Run: `pwsh -File scripts/sync.ps1 -Device devices/windows.example.json -DryRun`
Expected: PASS with dry-run summary output on a Windows-capable host.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync.sh scripts/sync.ps1
git commit -m "feat: add cross-platform sync entry points"
```

### Task 5: Document setup and verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the failing test**

List the documentation topics that must be covered:

```text
Overview
Repository layout
First-time setup
Device manifest setup
Dry-run sync
Real sync
Windows symlink fallback
Managed file safety
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rg -n "First-time setup|Dry-run sync|Windows symlink fallback" README.md`
Expected: FAIL because the current README does not document these sections.

- [ ] **Step 3: Write minimal implementation**

Expand `README.md` to include:

- what the repo manages
- what it intentionally does not manage
- how to copy a device example into a real device file
- how to run validation
- how to run dry-run sync
- how to run real sync on macOS and Windows
- how link-first and copy fallback behave

- [ ] **Step 4: Run test to verify it passes**

Run: `rg -n "First-time setup|Dry-run sync|Windows symlink fallback" README.md`
Expected: PASS with matching section headings.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add setup and verification guide"
```

### Task 6: Verify the repository end-to-end

**Files:**
- Modify: `README.md` if verification commands differ from actual behavior

- [ ] **Step 1: Write the failing test**

Define the verification command set:

```bash
node scripts/validate-config.js --device devices/macos.example.json
node scripts/validate-config.js --device devices/windows.example.json
node scripts/render-config.js --device devices/macos.example.json --dry-run
node scripts/render-config.js --device devices/windows.example.json --dry-run
bash scripts/sync.sh --device devices/macos.example.json --dry-run
```

- [ ] **Step 2: Run test to verify it fails**

Run each command before final fixes.
Expected: at least one failure until the implementation is complete and aligned.

- [ ] **Step 3: Write minimal implementation**

Fix any mismatches found during verification, keeping changes as small as possible.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
node scripts/validate-config.js --device devices/macos.example.json
node scripts/validate-config.js --device devices/windows.example.json
node scripts/render-config.js --device devices/macos.example.json --dry-run
node scripts/render-config.js --device devices/windows.example.json --dry-run
bash scripts/sync.sh --device devices/macos.example.json --dry-run
```

Expected: PASS for all commands on the current machine, except Windows-only execution commands that may require a Windows host.

- [ ] **Step 5: Commit**

```bash
git add .
git commit -m "test: verify initial multi-agent sync skeleton"
```

## Self-Review

- Spec coverage: repository layout, device manifests, shared skills strategy, MCP template rendering, macOS and Windows sync entry points, and verification docs are all mapped to Tasks 1-6.
- Placeholder scan: no `TODO`, `TBD`, or implicit "handle later" language remains in task instructions.
- Type consistency: manifest keys, script names, and template variable names are consistent across tasks.
