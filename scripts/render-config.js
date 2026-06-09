#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const TEMPLATE_PATHS = {
  codex: path.join("mcp", "templates", "codex", "mcp.json.tmpl"),
  claudeCode: path.join("mcp", "templates", "claude-code", "settings.json.tmpl"),
  cursor: path.join("mcp", "templates", "cursor", "mcp.json.tmpl")
};

function fail(message) {
  console.error(`Render failed: ${message}`);
  process.exit(1);
}

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === "--dry-run") {
      args.dryRun = true;
      continue;
    }
    args[key.replace(/^--/, "")] = argv[i + 1];
    i += 1;
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

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function nativePathForCurrentOs(manifestOs, targetPath) {
  const currentIsWindows = process.platform === "win32";
  if ((manifestOs === "windows" && currentIsWindows) || (manifestOs === "macos" && !currentIsWindows)) {
    return path.resolve(targetPath);
  }
  return targetPath;
}

function renderTemplate(template, variables) {
  return template.replace(/\{\{([A-Z_]+)\}\}/g, (_match, key) => {
    if (!(key in variables)) {
      fail(`missing template variable ${key}`);
    }
    return String(variables[key]);
  });
}

function main() {
  const args = parseArgs(process.argv);
  if (!args.device) {
    fail('missing required argument "--device <path>"');
  }

  const repoRoot = process.cwd();
  const manifestPath = path.resolve(repoRoot, args.device);
  if (!fs.existsSync(manifestPath)) {
    fail(`device manifest does not exist: ${manifestPath}`);
  }

  const manifest = loadJson(manifestPath);
  const variables = {
    REPO_ROOT: manifest.repoRoot,
    HOME_DIR: manifest.homeDir,
    NODE_PATH: manifest.executables && manifest.executables.nodePath ? manifest.executables.nodePath : "",
    PYTHON_PATH: manifest.executables && manifest.executables.pythonPath ? manifest.executables.pythonPath : ""
  };

  const requestedAgents = args.agents ? new Set(args.agents.split(",").map((value) => value.trim()).filter(Boolean)) : null;
  const enabledAgents = Object.entries(manifest.agents)
    .filter(([, config]) => config.enabled)
    .filter(([agent]) => !requestedAgents || requestedAgents.has(agent));
  if (enabledAgents.length === 0) {
    console.log(`No enabled agents in ${manifestPath}`);
    return;
  }

  for (const [agent, config] of enabledAgents) {
    const templateRelativePath = TEMPLATE_PATHS[agent];
    if (!templateRelativePath) {
      fail(`no template mapping found for agent "${agent}"`);
    }

    const templatePath = path.join(repoRoot, templateRelativePath);
    if (!fs.existsSync(templatePath)) {
      fail(`template file does not exist: ${templatePath}`);
    }

    const template = fs.readFileSync(templatePath, "utf8");
    const rendered = renderTemplate(template, variables);
    const targetPath = nativePathForCurrentOs(manifest.os, config.configTargetFile);

    if (args.dryRun) {
      console.log(`[dry-run] render ${agent}: ${templatePath} -> ${targetPath}`);
      continue;
    }

    ensureDir(path.dirname(targetPath));
    fs.writeFileSync(targetPath, rendered);
    console.log(`Rendered ${agent}: ${targetPath}`);
  }
}

main();
