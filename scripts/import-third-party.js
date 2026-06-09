#!/usr/bin/env node

const fs = require("fs");
const os = require("os");
const path = require("path");

const REPO_ROOT = process.cwd();
const IMPORTS_DIR = path.join(REPO_ROOT, "imports");
const CURSOR_IMPORTS_DIR = path.join(IMPORTS_DIR, "cursor");
const CURSOR_SKILLS_IMPORTS_DIR = path.join(CURSOR_IMPORTS_DIR, "skills");
const CURSOR_MCPS_IMPORTS_DIR = path.join(CURSOR_IMPORTS_DIR, "mcps");
const CLAUDE_IMPORTS_DIR = path.join(IMPORTS_DIR, "claude");
const MANIFEST_PATH = path.join(IMPORTS_DIR, "manifest.json");
const CLAUDE_SCAN_RESULTS_PATH = path.join(CLAUDE_IMPORTS_DIR, "scan-results.json");

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function removeAndRecreate(dirPath) {
  fs.rmSync(dirPath, { recursive: true, force: true });
  ensureDir(dirPath);
}

function copyDirectory(sourcePath, destinationPath) {
  fs.cpSync(sourcePath, destinationPath, { recursive: true });
}

function exists(dirPath) {
  return fs.existsSync(dirPath);
}

function readDirNames(rootPath) {
  if (!exists(rootPath)) {
    return [];
  }
  return fs.readdirSync(rootPath, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name);
}

function latestMtimeMsInDir(dirPath) {
  let latest = 0;
  const entries = fs.readdirSync(dirPath, { withFileTypes: true });
  for (const entry of entries) {
    const entryPath = path.join(dirPath, entry.name);
    const stats = fs.statSync(entryPath);
    latest = Math.max(latest, stats.mtimeMs);
    if (entry.isDirectory()) {
      latest = Math.max(latest, latestMtimeMsInDir(entryPath));
    }
  }
  return latest;
}

function writeJson(filePath, value) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function normalizeProjectName(projectName) {
  return projectName.replace(/[^a-zA-Z0-9._-]+/g, "-");
}

function importCursorSkills(manifest) {
  const cursorSkillsRoot = path.join(os.homedir(), ".cursor", "skills-cursor");
  const imported = [];

  ensureDir(CURSOR_SKILLS_IMPORTS_DIR);
  for (const skillName of readDirNames(cursorSkillsRoot)) {
    const sourceDir = path.join(cursorSkillsRoot, skillName);
    const skillFile = path.join(sourceDir, "SKILL.md");
    if (!exists(skillFile)) {
      continue;
    }

    const importDir = path.join(CURSOR_SKILLS_IMPORTS_DIR, skillName);
    copyDirectory(sourceDir, importDir);
    imported.push({
      type: "skill",
      name: skillName,
      sourceAgent: "cursor",
      sourcePath: sourceDir,
      sourceMtimeMs: latestMtimeMsInDir(sourceDir),
      importPath: importDir
    });
  }

  manifest.imported.push(...imported);
  return imported.length;
}

function importCursorMcps(manifest) {
  const cursorProjectsRoot = path.join(os.homedir(), ".cursor", "projects");
  const newestByName = new Map();

  ensureDir(CURSOR_MCPS_IMPORTS_DIR);
  for (const projectName of readDirNames(cursorProjectsRoot)) {
    const mcpsRoot = path.join(cursorProjectsRoot, projectName, "mcps");
    if (!exists(mcpsRoot)) {
      continue;
    }

    for (const mcpName of readDirNames(mcpsRoot)) {
      const sourceDir = path.join(mcpsRoot, mcpName);
      const sourceMtimeMs = latestMtimeMsInDir(sourceDir);
      const previous = newestByName.get(mcpName);
      if (!previous || sourceMtimeMs > previous.sourceMtimeMs) {
        newestByName.set(mcpName, {
          type: "mcp",
          name: mcpName,
          sourceAgent: "cursor",
          sourcePath: sourceDir,
          sourceProject: projectName,
          sourceMtimeMs
        });
      }
    }
  }

  for (const item of newestByName.values()) {
    const importDir = path.join(CURSOR_MCPS_IMPORTS_DIR, item.name);
    copyDirectory(item.sourcePath, importDir);
    manifest.imported.push({
      ...item,
      importPath: importDir,
      normalizedSourceProject: normalizeProjectName(item.sourceProject)
    });
  }

  return newestByName.size;
}

function scanClaude(manifest) {
  const claudeRoot = path.join(os.homedir(), ".claude");
  const findings = {
    sourceAgent: "claudeCode",
    scannedPath: claudeRoot,
    importableSkills: [],
    importableMcps: [],
    skipped: []
  };

  if (exists(path.join(claudeRoot, "settings.json"))) {
    findings.skipped.push({
      path: path.join(claudeRoot, "settings.json"),
      reason: "contains environment configuration and may include secrets"
    });
  }

  manifest.scans.push({
    sourceAgent: "claudeCode",
    status: "no-safe-importable-assets-found",
    scannedPath: claudeRoot
  });
  writeJson(CLAUDE_SCAN_RESULTS_PATH, findings);
}

function main() {
  removeAndRecreate(CURSOR_IMPORTS_DIR);
  removeAndRecreate(CLAUDE_IMPORTS_DIR);

  const manifest = {
    generatedAt: new Date().toISOString(),
    imported: [],
    scans: [],
    merge: {
      winners: [],
      losers: []
    }
  };

  const cursorSkillCount = importCursorSkills(manifest);
  const cursorMcpCount = importCursorMcps(manifest);
  scanClaude(manifest);

  writeJson(MANIFEST_PATH, manifest);

  console.log(`Imported Cursor skills: ${cursorSkillCount}`);
  console.log(`Imported Cursor MCPs: ${cursorMcpCount}`);
  console.log(`Claude scan results: ${CLAUDE_SCAN_RESULTS_PATH}`);
  console.log(`Manifest written: ${MANIFEST_PATH}`);
}

main();
