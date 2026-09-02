#!/usr/bin/env bash
# Sparse-clone only the upstream repository's top-level folders into TARGET_DIR.
set -euo pipefail

REPOSITORY_URL="https://github.com/xiaoguiwucan/linux-wechat-agent.git"
TARGET_DIR="${1:-.}"
FOLDERS=(agent_console ai design_mockups docs memory scripts status tools web)
PROJECT_REPOSITORY_URL="https://github.com/flyswing/wechat-nas.git"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required." >&2
  exit 1
fi

# Update this project first. --ff-only protects all local changes from merge
# conflicts or replacement; users must commit/stash before running this script.
echo "Updating wechat-nas from GitHub..."
git -C "$PROJECT_DIR" pull --ff-only "$PROJECT_REPOSITORY_URL" main

if [[ ! -d "$TARGET_DIR" ]]; then

  echo "Target directory does not exist: $TARGET_DIR" >&2
  exit 1
fi

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
CHECKOUT_DIR="$TEMP_DIR/linux-wechat-agent"

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
  if [[ -e "$target_dir" ]]; then
    echo "Skipped existing directory: $target_dir" >&2
    continue
  fi
  mv "$source_dir" "$target_dir"
done

echo "Fetched upstream folders into: $TARGET_DIR"
echo "Root-level repository files were intentionally not copied."