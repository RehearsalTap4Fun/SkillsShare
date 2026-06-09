#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const REPO_ROOT = process.cwd();
const MANIFEST_PATH = path.join(REPO_ROOT, "imports", "manifest.json");
const SKILLS_IMPORTED_DIR = path.join(REPO_ROOT, "skills", "imported");
const MCP_IMPORTED_DIR = path.join(REPO_ROOT, "mcp", "imported");

function fail(message) {
  console.error(`Merge failed: ${message}`);
  process.exit(1);
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function rewriteManagedDir(dirPath) {
  fs.rmSync(dirPath, { recursive: true, force: true });
  ensureDir(dirPath);
}

function loadManifest() {
  if (!fs.existsSync(MANIFEST_PATH)) {
    fail(`missing manifest: ${MANIFEST_PATH}`);
  }
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
}

function writeManifest(manifest) {
  fs.writeFileSync(MANIFEST_PATH, `${JSON.stringify(manifest, null, 2)}\n`);
}

function sourceOrder(sourceAgent) {
  if (sourceAgent === "cursor") {
    return 0;
  }
  if (sourceAgent === "claudeCode") {
    return 1;
  }
  return 99;
}

function chooseWinner(currentWinner, challenger) {
  if (!currentWinner) {
    return challenger;
  }
  if (challenger.sourceMtimeMs > currentWinner.sourceMtimeMs) {
    return challenger;
  }
  if (challenger.sourceMtimeMs < currentWinner.sourceMtimeMs) {
    return currentWinner;
  }
  return sourceOrder(challenger.sourceAgent) < sourceOrder(currentWinner.sourceAgent) ? challenger : currentWinner;
}

function copyWinner(winner, destinationRoot) {
  const destinationPath = path.join(destinationRoot, winner.name);
  fs.cpSync(winner.importPath, destinationPath, { recursive: true });
  return destinationPath;
}

function buildTypeMap(importedItems, type) {
  const items = importedItems.filter((item) => item.type === type);
  const grouped = new Map();

  for (const item of items) {
    const current = grouped.get(item.name);
    grouped.set(item.name, chooseWinner(current, item));
  }

  return grouped;
}

function collectLosers(importedItems, winnersByName, type) {
  const losers = [];
  for (const item of importedItems.filter((candidate) => candidate.type === type)) {
    const winner = winnersByName.get(item.name);
    if (!winner) {
      continue;
    }
    if (winner.importPath !== item.importPath || winner.sourcePath !== item.sourcePath) {
      losers.push({
        type,
        name: item.name,
        sourceAgent: item.sourceAgent,
        sourcePath: item.sourcePath,
        sourceMtimeMs: item.sourceMtimeMs,
        lostTo: {
          sourceAgent: winner.sourceAgent,
          sourcePath: winner.sourcePath,
          sourceMtimeMs: winner.sourceMtimeMs
        }
      });
    }
  }
  return losers;
}

function main() {
  const manifest = loadManifest();
  const importedItems = Array.isArray(manifest.imported) ? manifest.imported : [];

  rewriteManagedDir(SKILLS_IMPORTED_DIR);
  rewriteManagedDir(MCP_IMPORTED_DIR);

  const skillWinners = buildTypeMap(importedItems, "skill");
  const mcpWinners = buildTypeMap(importedItems, "mcp");

  const winners = [];
  for (const winner of skillWinners.values()) {
    winners.push({
      ...winner,
      mergedPath: copyWinner(winner, SKILLS_IMPORTED_DIR)
    });
  }
  for (const winner of mcpWinners.values()) {
    winners.push({
      ...winner,
      mergedPath: copyWinner(winner, MCP_IMPORTED_DIR)
    });
  }

  const losers = [
    ...collectLosers(importedItems, skillWinners, "skill"),
    ...collectLosers(importedItems, mcpWinners, "mcp")
  ];

  manifest.merge = {
    generatedAt: new Date().toISOString(),
    winners,
    losers
  };
  writeManifest(manifest);

  console.log(`Merged skills: ${skillWinners.size}`);
  console.log(`Merged MCPs: ${mcpWinners.size}`);
  console.log(`Conflicts resolved: ${losers.length}`);
}

main();
