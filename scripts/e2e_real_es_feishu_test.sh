#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="${ROOT_DIR}/venv/bin/python"

if [[ -x "${VENV_PY}" ]]; then
  PYTHON_BIN="${VENV_PY}"
else
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "[FAIL] Python executable not found (venv/python3/python)"
    exit 1
  fi
fi

ES_URL="${NEBULA_ES_URL:-http://localhost:9200}"
INDEX="${1:-nebula_metrics}"
WINDOW_MINUTES="${E2E_WINDOW_MINUTES:-30}"
TRACE_LIMIT="${E2E_TRACE_LIMIT:-5}"
RUN_ID_SUFFIX="$(date +%Y%m%d_%H%M%S)"
RUNS_PATH="${ROOT_DIR}/data/agent_runs_e2e_real_${RUN_ID_SUFFIX}.json"
DEDUPE_PATH="${ROOT_DIR}/data/notify_dedupe_e2e_real_${RUN_ID_SUFFIX}.json"
LOAD_SIM_DATA="${E2E_LOAD_SIM_DATA:-1}"
SIM_TRACES="${E2E_SIM_TRACES:-120}"

run() {
  echo "[RUN] $*"
  "$@"
}

pick_webhook() {
  if [[ -n "${NEBULA_FEISHU_WEBHOOK:-}" ]]; then
    printf '%s\n' "${NEBULA_FEISHU_WEBHOOK}"
    return 0
  fi

  "${PYTHON_BIN}" - <<'PY'
import json
from pathlib import Path

paths = [
    Path("data/notify_dedupe_feishu_test.json"),
    Path("data/notify_dedupe_feishu_test_dynamic.json"),
    Path("data/notify_dedupe_feishu_format_test.json"),
    Path("data/notify_dedupe_feishu_diverse_test.json"),
    Path("data/notify_dedupe.json"),
]

for p in paths:
    if not p.exists():
        continue
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(data, dict) or not data:
        continue
    key = next(iter(data.keys()))
    if ":" in key:
        print(key.split(":", 1)[1])
        raise SystemExit(0)

raise SystemExit(1)
PY
}

echo "[INFO] Root: ${ROOT_DIR}"
echo "[INFO] Python: ${PYTHON_BIN}"
echo "[INFO] ES_URL: ${ES_URL}"
echo "[INFO] INDEX: ${INDEX}"
echo "[INFO] RUNS_PATH: ${RUNS_PATH}"
echo "[INFO] DEDUPE_PATH: ${DEDUPE_PATH}"

WEBHOOK="$(pick_webhook || true)"
if [[ -z "${WEBHOOK}" ]]; then
  echo "[FAIL] Missing Feishu webhook. Set NEBULA_FEISHU_WEBHOOK or keep a valid notify_dedupe_feishu*.json"
  exit 2
fi

run "${PYTHON_BIN}" -V
run "${PYTHON_BIN}" -c "from elasticsearch import Elasticsearch; es=Elasticsearch('${ES_URL}'); assert es.ping(), 'ES ping failed'; print('ES ping ok')"

if [[ "${LOAD_SIM_DATA}" == "1" ]]; then
  run "${PYTHON_BIN}" "${ROOT_DIR}/scripts/load_simulated_es_data.py" \
    --es-url "${ES_URL}" \
    --index "${INDEX}" \
    --traces "${SIM_TRACES}" \
    --time-window-minutes "${WINDOW_MINUTES}" \
    --create-index \
    --refresh wait_for
fi

echo "[STEP] Running monitor-es one iteration with real ES + real Feishu webhook"
run "${PYTHON_BIN}" -m nebula_copilot.cli monitor-es \
  --index "${INDEX}" \
  --es-url "${ES_URL}" \
  --poll-interval-seconds 2 \
  --last-minutes "${WINDOW_MINUTES}" \
  --limit "${TRACE_LIMIT}" \
  --slow-threshold-ms 1 \
  --trigger-dedupe-seconds 60 \
  --max-iterations 1 \
  --push-webhook "${WEBHOOK}" \
  --runs-path "${RUNS_PATH}" \
  --notify-dedupe-path "${DEDUPE_PATH}" \
  --notify-dedupe-window-seconds 1 \
  --notify-max-retries 2

echo "[STEP] Validating run records"
VALIDATE_OUT="$("${PYTHON_BIN}" - <<PY
import json
from pathlib import Path

runs_path = Path(r"${RUNS_PATH}")
if not runs_path.exists():
    print("FAIL|runs_path_not_found")
    raise SystemExit(1)

rows = json.loads(runs_path.read_text(encoding="utf-8"))
if not isinstance(rows, list) or not rows:
    print("FAIL|runs_empty")
    raise SystemExit(1)

last = rows[-1]
status = str(last.get("status") or "")
notify = last.get("notify") if isinstance(last.get("notify"), dict) else {}
notify_status = str(notify.get("status") or "")
trace_id = str(last.get("trace_id") or "")
trigger_source = str(last.get("trigger_source") or "")
metrics = last.get("metrics") if isinstance(last.get("metrics"), dict) else {}
history_events = int(metrics.get("history_events") or 0)
bottleneck_duration_ms = int(metrics.get("bottleneck_duration_ms") or 0)

ok = (
    trigger_source == "monitor-es"
    and status in {"ok", "degraded"}
    and notify_status == "ok"
)

if not ok:
    print(f"FAIL|status={status}|notify={notify_status}|trigger={trigger_source}|trace={trace_id}")
    raise SystemExit(1)

print(
    "PASS|"
    f"trace_id={trace_id}|"
    f"status={status}|"
    f"notify_status={notify_status}|"
    f"history_events={history_events}|"
    f"bottleneck_duration_ms={bottleneck_duration_ms}"
)
PY
)"

echo "[RESULT] ${VALIDATE_OUT}"
echo "[PASS] End-to-end real ES -> monitor-es -> Feishu webhook test completed"
