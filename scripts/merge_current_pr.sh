#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/merge_current_pr.sh [labels] [merge_method]

Args:
  labels        Comma-separated labels, default: "automerge,ci-passed"
  merge_method  squash|merge|rebase, default: squash

Behavior:
  - Detect current git branch
  - Find OPEN PR where head branch equals current branch
  - Delegate to scripts/merge_pr.sh for CI gate, labeling, and merge

Examples:
  bash scripts/merge_current_pr.sh
  bash scripts/merge_current_pr.sh "automerge,needs-release-note" rebase
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

LABELS="${1:-automerge,ci-passed}"
MERGE_METHOD="${2:-squash}"

if ! command -v git >/dev/null 2>&1; then
  echo "[FAIL] git command not found."
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "[FAIL] gh command not found."
  exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ -z "${CURRENT_BRANCH}" || "${CURRENT_BRANCH}" == "HEAD" ]]; then
  echo "[FAIL] Unable to detect current branch (detached HEAD?)."
  exit 1
fi

PR_NUMBER="$(gh pr list --head "${CURRENT_BRANCH}" --state open --json number --jq '.[0].number // empty')"
if [[ -z "${PR_NUMBER}" ]]; then
  echo "[FAIL] No OPEN PR found for current branch: ${CURRENT_BRANCH}"
  echo "[INFO] You can create one via: gh pr create --fill"
  exit 1
fi

echo "[INFO] Current branch: ${CURRENT_BRANCH}"
echo "[INFO] Matched PR: #${PR_NUMBER}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "${SCRIPT_DIR}/merge_pr.sh" "${PR_NUMBER}" "${LABELS}" "${MERGE_METHOD}"
