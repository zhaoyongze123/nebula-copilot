#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/merge_pr.sh <pr_number> [labels] [merge_method]

Args:
  pr_number     Pull Request number, e.g. 1
  labels        Comma-separated labels, default: "automerge,ci-passed"
  merge_method  squash|merge|rebase, default: squash

Env:
  RUN_LIMIT       Number of runs to inspect from gh run list (default: 20)
  WAIT_SECONDS    Poll interval when latest run is still in progress (default: 20)
  MAX_WAIT_SECONDS Max wait time for CI completion (default: 1800)
  ALLOW_NO_RUNS   Set to 1 to skip CI gate when no runs are found

Examples:
  scripts/merge_pr.sh 1
  scripts/merge_pr.sh 1 "automerge,needs-release-note" rebase
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || -z "${1:-}" ]]; then
  usage
  exit 0
fi

PR_NUMBER="$1"
LABELS="${2:-automerge,ci-passed}"
MERGE_METHOD="${3:-squash}"
RUN_LIMIT="${RUN_LIMIT:-20}"
WAIT_SECONDS="${WAIT_SECONDS:-20}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-1800}"
ALLOW_NO_RUNS="${ALLOW_NO_RUNS:-0}"

if ! command -v gh >/dev/null 2>&1; then
  echo "[FAIL] gh command not found. Please install GitHub CLI first."
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "[FAIL] gh is not authenticated. Run: gh auth login"
  exit 1
fi

REPO_FULL_NAME="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"

if [[ "${MERGE_METHOD}" != "squash" && "${MERGE_METHOD}" != "merge" && "${MERGE_METHOD}" != "rebase" ]]; then
  echo "[FAIL] merge_method must be one of: squash|merge|rebase"
  exit 1
fi

read_pr_field() {
  local field="$1"
  gh pr view "${PR_NUMBER}" --json "${field}" --jq ".${field}"
}

PR_STATE="$(read_pr_field state)"
PR_DRAFT="$(read_pr_field isDraft)"
PR_HEAD="$(read_pr_field headRefName)"
PR_URL="$(read_pr_field url)"
PR_TITLE="$(read_pr_field title)"

if [[ "${PR_STATE}" != "OPEN" ]]; then
  echo "[FAIL] PR #${PR_NUMBER} is not OPEN (state=${PR_STATE})."
  exit 1
fi

if [[ "${PR_DRAFT}" == "true" ]]; then
  echo "[FAIL] PR #${PR_NUMBER} is a draft PR. Please mark it ready first."
  exit 1
fi

echo "[INFO] PR: #${PR_NUMBER} ${PR_TITLE}"
echo "[INFO] URL: ${PR_URL}"
echo "[INFO] Head branch: ${PR_HEAD}"

ci_gate_once() {
  local run_json
  run_json="$(gh run list --branch "${PR_HEAD}" --limit "${RUN_LIMIT}" --json status,conclusion,workflowName,url 2>/dev/null || echo "[]")"

  python3 - <<'PY' "${run_json}"
import json
import sys

try:
    data = json.loads(sys.argv[1])
except Exception:
    print("FAIL|Unable to parse gh run list output")
    sys.exit(0)

if not data:
    print("NONE|No workflow runs found for this branch")
    sys.exit(0)

latest = data[0]
status = str(latest.get("status") or "").lower()
conclusion = str(latest.get("conclusion") or "").lower()
name = latest.get("workflowName") or "unknown-workflow"
url = latest.get("url") or ""

if status != "completed":
    print(f"WAIT|Latest run in progress|{name}|{url}")
elif conclusion in {"success", "neutral", "skipped"}:
    print(f"PASS|Latest run passed|{name}|{url}")
else:
    print(f"FAIL|Latest run not successful: {conclusion}|{name}|{url}")
PY
}

start_ts="$(date +%s)"
while true; do
  gate_line="$(ci_gate_once)"
  gate_code="${gate_line%%|*}"
  gate_msg="${gate_line#*|}"

  case "${gate_code}" in
    PASS)
      echo "[PASS] CI gate ok: ${gate_msg}"
      break
      ;;
    WAIT)
      now_ts="$(date +%s)"
      elapsed="$((now_ts - start_ts))"
      if (( elapsed >= MAX_WAIT_SECONDS )); then
        echo "[FAIL] CI gate timeout after ${elapsed}s: ${gate_msg}"
        exit 1
      fi
      echo "[WAIT] ${gate_msg} (elapsed=${elapsed}s)"
      sleep "${WAIT_SECONDS}"
      ;;
    NONE)
      if [[ "${ALLOW_NO_RUNS}" == "1" ]]; then
        echo "[WARN] ${gate_msg}; continue due to ALLOW_NO_RUNS=1"
        break
      fi
      echo "[FAIL] ${gate_msg}; set ALLOW_NO_RUNS=1 to bypass"
      exit 1
      ;;
    *)
      echo "[FAIL] CI gate failed: ${gate_msg}"
      exit 1
      ;;
  esac
done

IFS=',' read -r -a label_array <<< "${LABELS}"
for label in "${label_array[@]}"; do
  clean_label="$(echo "${label}" | xargs)"
  if [[ -n "${clean_label}" ]]; then
    if gh api "repos/${REPO_FULL_NAME}/labels/${clean_label}" >/dev/null 2>&1; then
      echo "[STEP] Add label: ${clean_label}"
      gh pr edit "${PR_NUMBER}" --add-label "${clean_label}" >/dev/null
    else
      echo "[WARN] Label not found, skipped: ${clean_label}"
    fi
  fi
done

echo "[STEP] Merge PR #${PR_NUMBER} with --${MERGE_METHOD}"
gh pr merge "${PR_NUMBER}" "--${MERGE_METHOD}" --delete-branch

echo "[DONE] PR merged: ${PR_URL}"
