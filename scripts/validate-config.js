#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const AGENT_KEYS = ["codex", "claudeCode", "cursor"];
const REQUIRED_TOP_LEVEL_KEYS = ["deviceName", "os", "repoRoot", "homeDir", "agents"];
const REQUIRED_AGENT_KEYS = ["enabled", "skillsTargetDir", "configTargetFile"];

function fail(message) {
  console.error(`Validation failed: ${message}`);
  process.exit(1);
}

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (token.startsWith("--")) {
      args[token.slice(2)] = argv[i + 1];
      i += 1;
    }
  }
  return args;
}

function loadJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    fail(`unable to parse JSON from ${filePath}: ${error.message}`);
  }
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function validateManifest(manifest, manifestPath) {
  for (const key of REQUIRED_TOP_LEVEL_KEYS) {
    if (!(key in manifest)) {
      fail(`${manifestPath} is missing top-level key "${key}"`);
    }
  }

  if (!["macos", "windows"].includes(manifest.os)) {
    fail(`${manifestPath} has unsupported os "${manifest.os}"`);
  }

  for (const key of ["deviceName", "repoRoot", "homeDir"]) {
    if (!isNonEmptyString(manifest[key])) {
      fail(`${manifestPath} key "${key}" must be a non-empty string`);
    }
  }

  if (typeof manifest.agents !== "object" || manifest.agents === null || Array.isArray(manifest.agents)) {
    fail(`${manifestPath} key "agents" must be an object`);
  }

  for (const agent of AGENT_KEYS) {
    const agentConfig = manifest.agents[agent];
    if (!agentConfig) {
      fail(`${manifestPath} is missing agents.${agent}`);
    }
    for (const key of REQUIRED_AGENT_KEYS) {
      if (!(key in agentConfig)) {
        fail(`${manifestPath} is missing agents.${agent}.${key}`);
      }
    }
    if (typeof agentConfig.enabled !== "boolean") {
      fail(`${manifestPath} agents.${agent}.enabled must be a boolean`);
    }
    for (const key of ["skillsTargetDir", "configTargetFile"]) {
      if (!isNonEmptyString(agentConfig[key])) {
        fail(`${manifestPath} agents.${agent}.${key} must be a non-empty string`);
      }
    }
  }

  if ("executables" in manifest) {
    if (typeof manifest.executables !== "object" || manifest.executables === null || Array.isArray(manifest.executables)) {
      fail(`${manifestPath} key "executables" must be an object when provided`);
    }
    for (const key of ["nodePath", "pythonPath"]) {
      if (key in manifest.executables && !isNonEmptyString(manifest.executables[key])) {
        fail(`${manifestPath} executables.${key} must be a non-empty string when provided`);
      }
    }
  }
}

function main() {
  const args = parseArgs(process.argv);
  const devicePath = args.device;
  if (!devicePath) {
    fail('missing required argument "--device <path>"');
  }

  const resolvedPath = path.resolve(process.cwd(), devicePath);
  if (!fs.existsSync(resolvedPath)) {
    fail(`device manifest does not exist: ${resolvedPath}`);
  }

  const manifest = loadJson(resolvedPath);
  validateManifest(manifest, resolvedPath);
  console.log(`Validation passed: ${resolvedPath}`);
}

main();
