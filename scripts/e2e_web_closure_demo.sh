#!/usr/bin/env bash
set -euo pipefail

INDEX="${1:-nebula_metrics}"
LAST_MINUTES="${LAST_MINUTES:-10080}"
LIMIT="${LIMIT:-5}"
SLOW_THRESHOLD_MS="${SLOW_THRESHOLD_MS:-1}"
RUNS_PATH="${RUNS_PATH:-data/agent_runs.json}"
WEB_BASE_URL="${WEB_BASE_URL:-http://127.0.0.1:8080}"
PYTHON_BIN="${PYTHON_BIN:-/Users/mac/Documents/python/venv/bin/python}"
TMP_ENV_FILE="${TMP_ENV_FILE:-/tmp/nebula_web_demo.env}"

cat > "$TMP_ENV_FILE" <<'EOF'
LLM_ENABLED=false
EOF

printf "[1/3] monitor-es 单轮触发 (index=%s, last_minutes=%s, limit=%s)\n" "$INDEX" "$LAST_MINUTES" "$LIMIT"
"$PYTHON_BIN" -m nebula_copilot.cli monitor-es \
  --index "$INDEX" \
  --last-minutes "$LAST_MINUTES" \
  --limit "$LIMIT" \
  --slow-threshold-ms "$SLOW_THRESHOLD_MS" \
  --poll-interval-seconds 1 \
  --max-iterations 1 \
  --env-file "$TMP_ENV_FILE" \
  --runs-path "$RUNS_PATH" \
  --trigger-dedupe-seconds 1

printf "[2/3] 读取最新 run 记录并提取 trace_id\n"
LATEST_JSON=$("$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

runs_path = Path("data/agent_runs.json")
runs = json.loads(runs_path.read_text(encoding="utf-8")) if runs_path.exists() else []
if not runs:
    raise SystemExit("NO_RUNS")
latest = runs[-1]
print(json.dumps({
    "run_id": latest.get("run_id"),
    "trace_id": latest.get("trace_id"),
    "status": latest.get("status"),
    "started_at": latest.get("started_at"),
}, ensure_ascii=False))
PY
)

printf "latest_run=%s\n" "$LATEST_JSON"

TRACE_ID=$("$PYTHON_BIN" - <<'PY' "$LATEST_JSON"
import json
import sys
payload = json.loads(sys.argv[1])
print(payload.get("trace_id") or "")
PY
)

if [[ -z "$TRACE_ID" ]]; then
  echo "未提取到 trace_id，退出"
  exit 1
fi

printf "[3/3] 调用 Web Trace Inspect 验证闭环 (trace_id=%s)\n" "$TRACE_ID"
"$PYTHON_BIN" - <<'PY' "$WEB_BASE_URL" "$TRACE_ID"
import json
import sys
from urllib.parse import quote
from urllib.request import urlopen

base = sys.argv[1].rstrip("/")
trace_id = sys.argv[2]
url = f"{base}/api/traces/{quote(trace_id)}/inspect"
with urlopen(url, timeout=15) as resp:
    payload = json.loads(resp.read().decode("utf-8"))
print(json.dumps({
    "trace_id": trace_id,
    "ok": payload.get("ok"),
    "source": (payload.get("meta") or {}).get("source"),
    "error": payload.get("error"),
}, ensure_ascii=False))
PY

echo "闭环演示完成。可在 Dashboard 刷新查看最新 run。"
