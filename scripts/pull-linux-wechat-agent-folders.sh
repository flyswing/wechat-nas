#!/usr/bin/env bash
# Force-update this project, then replace top-level upstream folders.
set -euo pipefail

REPOSITORY_URL="https://github.com/xiaoguiwucan/linux-wechat-agent.git"
TARGET_DIR="${1:-.}"
FOLDERS=(agent_console ai design_mockups docs memory scripts status tools web)
PROJECT_REPOSITORY_URL="https://github.com/flyswing/wechat-nas.git"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$PROJECT_DIR/scripts/$(basename "${BASH_SOURCE[0]}")"

# Force tracked project files to exactly match GitHub main. Ignored runtime
# data (such as .env and config/) remains untouched; tracked local work is
# intentionally discarded.
echo "Force-updating wechat-nas from GitHub..."
git -C "$PROJECT_DIR" fetch --depth 1 "$PROJECT_REPOSITORY_URL" main
git -C "$PROJECT_DIR" reset --hard FETCH_HEAD

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
CHECKOUT_DIR="$TEMP_DIR/linux-wechat-agent"
SCRIPT_BACKUP="$TEMP_DIR/$(basename "$SCRIPT_PATH")"
cp "$SCRIPT_PATH" "$SCRIPT_BACKUP"

git clone --depth 1 --filter=blob:none --no-checkout "$REPOSITORY_URL" "$CHECKOUT_DIR"

declare -a sparse_patterns=()
for folder in "${FOLDERS[@]}"; do
  sparse_patterns+=("/$folder/**")
done

git -C "$CHECKOUT_DIR" sparse-checkout init --no-cone
git -C "$CHECKOUT_DIR" sparse-checkout set --no-cone "${sparse_patterns[@]}"
git -C "$CHECKOUT_DIR" checkout main

for folder in "${FOLDERS[@]}"; do
  source_dir="$CHECKOUT_DIR/$folder"
  target_dir="$TARGET_DIR/$folder"
  rm -rf "$target_dir"
  mv "$source_dir" "$target_dir"
done

# The upstream scripts directory replaces this one, so restore the updater.
mkdir -p "$(dirname "$SCRIPT_PATH")"
cp "$SCRIPT_BACKUP" "$SCRIPT_PATH"
chmod +x "$SCRIPT_PATH"

echo "Force-updated project and replaced upstream folders in: $TARGET_DIR"
echo "Root-level upstream repository files were intentionally not copied."