#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE_PATH=""
DRY_RUN="false"
MANAGED_MARKER=".skills-share-managed"

usage() {
  echo "Usage: bash scripts/sync.sh --device <path> [--dry-run]" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device)
      DEVICE_PATH="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift 1
      ;;
    *)
      usage
      ;;
  esac
done

if [[ -z "$DEVICE_PATH" ]]; then
  usage
fi

cd "$ROOT_DIR"
node scripts/validate-config.js --device "$DEVICE_PATH"

manifest_json="$(node -e "const fs=require('fs'); const p=require('path'); const data=JSON.parse(fs.readFileSync(p.resolve(process.cwd(), process.argv[1]), 'utf8')); process.stdout.write(JSON.stringify(data));" "$DEVICE_PATH")"

agent_value() {
  local agent="$1"
  local key="$2"
  printf '%s' "$manifest_json" | node -e "const fs=require('fs'); const data=JSON.parse(fs.readFileSync(0, 'utf8')); process.stdout.write(String(data.agents[process.argv[1]][process.argv[2]]));" "$agent" "$key"
}

ensure_parent_dir() {
  local target="$1"
  local parent
  parent="$(dirname "$target")"
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[dry-run] ensure parent: $parent"
  else
    mkdir -p "$parent"
  fi
}

copy_target() {
  local source_dir="$1"
  local target_dir="$2"

  ensure_parent_dir "$target_dir"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[dry-run] copy-fallback $source_dir -> $target_dir"
    return 0
  fi

  if [[ -d "$target_dir" && ! -f "$target_dir/$MANAGED_MARKER" ]]; then
    echo "Skip unmanaged directory: $target_dir"
    return 0
  fi

  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  cp -R "$source_dir"/. "$target_dir"/
  touch "$target_dir/$MANAGED_MARKER"
  echo "Copied $source_dir -> $target_dir"
}

link_target() {
  local source_dir="$1"
  local target_dir="$2"
  ensure_parent_dir "$target_dir"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[dry-run] link $source_dir -> $target_dir"
    return 0
  fi

  if [[ -L "$target_dir" || -f "$target_dir" ]]; then
    rm -f "$target_dir"
  elif [[ -d "$target_dir" ]]; then
    if [[ -f "$target_dir/$MANAGED_MARKER" ]]; then
      rm -rf "$target_dir"
    else
      echo "Skip unmanaged directory: $target_dir"
      return 0
    fi
  fi

  if ln -s "$source_dir" "$target_dir" 2>/dev/null; then
    echo "Linked $source_dir -> $target_dir"
  else
    copy_target "$source_dir" "$target_dir"
  fi
}

sync_agent_skills() {
  local agent="$1"
  local target_root
  target_root="$(agent_value "$agent" "skillsTargetDir")"

  case "$agent" in
    codex)
      link_target "$ROOT_DIR/skills/shared" "$target_root/shared"
      link_target "$ROOT_DIR/skills/codex" "$target_root/codex"
      ;;
    claudeCode)
      link_target "$ROOT_DIR/skills/shared" "$target_root/shared"
      link_target "$ROOT_DIR/skills/claude-code" "$target_root/claude-code"
      ;;
    cursor)
      link_target "$ROOT_DIR/skills/shared" "$target_root/shared"
      link_target "$ROOT_DIR/skills/cursor" "$target_root/cursor"
      ;;
  esac
}

for agent in codex claudeCode cursor; do
  enabled="$(agent_value "$agent" "enabled")"
  if [[ "$enabled" == "true" ]]; then
    sync_agent_skills "$agent"
  else
    echo "Skip disabled agent: $agent"
  fi
done

if [[ "$DRY_RUN" == "true" ]]; then
  node scripts/render-config.js --device "$DEVICE_PATH" --dry-run
else
  node scripts/render-config.js --device "$DEVICE_PATH"
fi

echo "Sync complete"
