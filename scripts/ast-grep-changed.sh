#!/usr/bin/env bash
# Check only newly introduced policy violations, not unrelated legacy code.
set -euo pipefail

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: ast-grep:changed must run inside a Git working tree" >&2
  exit 2
fi

files=()
add_file() {
  local file="$1"
  local existing

  [[ -f "$file" ]] || return 0
  for existing in "${files[@]}"; do
    [[ "$existing" == "$file" ]] && return 0
  done
  files+=("$file")
}

diff_paths=$(mktemp)
trap 'rm -f "$diff_paths"' EXIT

if git rev-parse --verify --quiet HEAD >/dev/null; then
  git diff --name-only --diff-filter=ACMR -z HEAD >"$diff_paths"
  while IFS= read -r -d '' file; do
    add_file "$file"
  done <"$diff_paths"
fi

while IFS= read -r -d '' file; do
  add_file "$file"
done < <(git ls-files --others --exclude-standard -z)

if (( ${#files[@]} == 0 )); then
  echo "ast-grep: no added, copied, modified, or renamed files to scan"
  exit 0
fi

scan_files=()
for file in "${files[@]}"; do
  # Git emits repository-relative paths; prefix them so option-like filenames stay operands.
  scan_files+=("./$file")
done

bunx --no-install ast-grep scan --config sgconfig.yml --error "${scan_files[@]}"
