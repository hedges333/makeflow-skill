#!/usr/bin/env bash
set -euo pipefail

# Makefile Workflow Installer
# Usage:
#   ./install.sh                # Install locally
#   ./install.sh --dry-run      # Check files without installing
#   curl -fsSL https://raw.githubusercontent.com/hedges333/makeflow-skill/main/install.sh | bash

PLUGIN_NAME="makeflow"
REPO_URL="https://github.com/hedges333/makeflow-skill.git"
TARGET_DIR="${HOME}/.gemini/config/plugins/${PLUGIN_NAME}"

RUNTIME_ALLOWLIST=(
  plugin.json
  hooks.json
  mcp_config.json
  default_config.json
  LICENSE
  README.md
  assets
  agents
  lib
  mcp
  rules
  skills
  scripts
)

DRY_RUN=false
for arg in "$@"; do
  if [ "$arg" = "--dry-run" ]; then
    DRY_RUN=true
  fi
done

copy_plugin_tree() {
  local src_dir="$1"
  local dst_dir="$2"
  (
    local tmp_tar
    tmp_tar=$(mktemp)
    trap 'rm -f "${tmp_tar}"' EXIT
    tar -C "${src_dir}" \
      --exclude='__pycache__' \
      --exclude='*.py[cod]' \
      --exclude='*$py.class' \
      --exclude='.DS_Store' \
      -cf "${tmp_tar}" "${RUNTIME_ALLOWLIST[@]}" &&
      rm -rf "${dst_dir}" &&
      mkdir -p "${dst_dir}" &&
      tar -xf "${tmp_tar}" -C "${dst_dir}"
  )
}

install_with_agy_from_dir() {
  local src_dir="$1"
  (
    local stage_dir
    echo "Staging runtime plugin tree for validation..."
    stage_dir=$(mktemp -d)
    trap 'rm -rf "${stage_dir}"' EXIT
    copy_plugin_tree "${src_dir}" "${stage_dir}/${PLUGIN_NAME}"
    echo "Running plugin check on staged tree..."
    agy plugin validate "${stage_dir}/${PLUGIN_NAME}" --strict
    if [ "$DRY_RUN" = true ]; then
      echo "Dry run passed. Skipping install."
      exit 0
    fi
    echo "Installing plugin with agy plugin install..."
    agy plugin install "${stage_dir}/${PLUGIN_NAME}"
  )
  if [ "$DRY_RUN" = true ]; then
    exit 0
  fi
}

echo "=== Installing Makefile Workflow Plugin ==="

if command -v agy >/dev/null 2>&1; then
  echo "Found Antigravity CLI (agy)."

  if [ -f "plugin.json" ] && [ -d "skills/makeflow" ]; then
    install_with_agy_from_dir "."
  else
    if [ "$DRY_RUN" = true ]; then
      echo "Dry run: will clone ${REPO_URL}, stage runtime tree, validate, and install."
      exit 0
    fi
    echo "Installing plugin from Git repository..."
    TEMP_DIR=$(mktemp -d)
    trap 'rm -rf "${TEMP_DIR}"' EXIT
    git clone --depth 1 "${REPO_URL}" "${TEMP_DIR}"
    install_with_agy_from_dir "${TEMP_DIR}"
  fi
else
  echo "agy CLI not found in PATH. Copying plugin files directly."
  if [ "$DRY_RUN" = true ]; then
    echo "Dry run: will copy files to ${TARGET_DIR}."
    exit 0
  fi
  if [ -f "plugin.json" ] && [ -d "skills/makeflow" ]; then
    copy_plugin_tree "." "${TARGET_DIR}"
  else
    TEMP_DIR=$(mktemp -d)
    trap 'rm -rf "${TEMP_DIR}"' EXIT
    git clone --depth 1 "${REPO_URL}" "${TEMP_DIR}"
    copy_plugin_tree "${TEMP_DIR}" "${TARGET_DIR}"
  fi
  echo "Copied plugin files to ${TARGET_DIR}."
fi

# Built-in technical English specifications are installed in skills/makeflow/references/resources/language/english/

echo "=== Makefile Workflow Installation Complete ==="
echo "Restart your agent session to load the plugin."
