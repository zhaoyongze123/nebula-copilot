#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="${ROOT_DIR}/venv/bin/python"

if [[ ! -x "${VENV_PY}" ]]; then
  echo "[FAIL] venv python not found: ${VENV_PY}"
  exit 1
fi

ES_URL="${NEBULA_ES_URL:-http://localhost:9200}"
INDEX="${1:-nebula_metrics}"

run() {
  echo "[RUN] $*"
  "$@"
}

echo "[INFO] Root: ${ROOT_DIR}"
echo "[INFO] ES_URL: ${ES_URL}"
echo "[INFO] INDEX: ${INDEX}"

run "${VENV_PY}" -V
run "${VENV_PY}" -c "from elasticsearch import Elasticsearch; es=Elasticsearch('${ES_URL}'); assert es.ping(), 'ES ping failed'; print('ES ping ok')"

LAST_MINUTES="${SMOKE_LAST_MINUTES:-500000}"

echo "[STEP] List recent trace IDs (last ${LAST_MINUTES} minutes)"
TRACE_JSON="$(${VENV_PY} -m nebula_copilot.cli list-traces --index "${INDEX}" --es-url "${ES_URL}" --last-minutes "${LAST_MINUTES}" --limit 1 --format json)"
echo "${TRACE_JSON}"
TRACE_ID="$(${VENV_PY} -c "import json,sys; data=json.loads(sys.stdin.read()); arr=data.get('trace_ids', []); print(arr[0] if arr else '')" <<< "${TRACE_JSON}")"

if [[ -z "${TRACE_ID}" ]]; then
  echo "[FAIL] No trace ID found in last ${LAST_MINUTES} minutes from index ${INDEX}"
  exit 2
fi

echo "[STEP] Analyze trace: ${TRACE_ID}"
run "${VENV_PY}" -m nebula_copilot.cli analyze-es "${TRACE_ID}" --index "${INDEX}" --es-url "${ES_URL}" --format table --top-n 3

echo "[PASS] smoke test passed"
