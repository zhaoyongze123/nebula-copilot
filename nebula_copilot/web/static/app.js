const state = {
  selectedRunId: null,
  selectedTraceId: null,
  autoTimer: null,
};

function qs(id) {
  return document.getElementById(id);
}

function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
}

function statusBadge(status) {
  const s = status || 'ok';
  return `<span class="badge ${s}">${s}</span>`;
}

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return await res.json();
}

function renderKpi(data) {
  const root = qs('kpiGrid');
  const kpi = data.kpi || {};
  const metrics = [
    ['总 Run', kpi.total ?? 0],
    ['成功率(%)', kpi.success_rate ?? 0],
    ['失败数', kpi.failed ?? 0],
    ['退化数', kpi.degraded ?? 0],
    ['P95(ms)', kpi.p95_duration_ms ?? 0],
    ['异常事件', (data.recent_anomalies || []).length],
  ];
  root.innerHTML = metrics
    .map(([label, value]) => `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div></div>`)
    .join('');
}

function renderRuns(items) {
  const body = qs('runsTable').querySelector('tbody');
  body.innerHTML = '';
  for (const run of items) {
    const tr = document.createElement('tr');
    tr.className = 'clickable';
    tr.innerHTML = `
      <td>${statusBadge(run.status)}</td>
      <td>${run.run_id || '-'}</td>
      <td>${run.trace_id || '-'}</td>
      <td>${run.duration_ms ?? '-'}</td>
      <td>${fmtTime(run.started_at || run.timestamp)}</td>
    `;
    tr.addEventListener('click', () => selectRun(run));
    body.appendChild(tr);
  }
}

function renderRunDetail(page) {
  const summary = qs('runSummary');
  const run = page.summary || {};
  summary.innerHTML = `
    <div><strong>run_id:</strong> ${run.run_id || '-'}</div>
    <div><strong>trace_id:</strong> ${run.trace_id || '-'}</div>
    <div><strong>status:</strong> ${statusBadge(run.status || 'ok')}</div>
    <div><strong>duration:</strong> ${run.duration_ms ?? '-'} ms</div>
    <div><strong>started:</strong> ${fmtTime(run.started_at || run.timestamp)}</div>
  `;

  const timeline = qs('timeline');
  timeline.innerHTML = '';
  const events = page.timeline || [];
  for (const ev of events) {
    const li = document.createElement('li');
    li.textContent = `${fmtTime(ev.ts || ev.timestamp)} | ${ev.phase || ev.name || 'event'} | ${ev.message || ''}`;
    timeline.appendChild(li);
  }

  const diagnosis = qs('diagnosis');
  diagnosis.textContent = page.diagnosis?.summary || JSON.stringify(page.diagnosis || {}, null, 2);
}

function renderTraceInspect(payload) {
  const panel = qs('tracePanel');
  const tree = payload.tree || {};
  const diagnosis = payload.diagnosis || {};
  const bottleneck = diagnosis.bottleneck?.span || {};
  panel.innerHTML = `
    <div><strong>trace_id:</strong> ${payload.trace_id || '-'}</div>
    <div><strong>root service:</strong> ${tree.service_name || '-'}</div>
    <div><strong>bottleneck:</strong> ${bottleneck.service_name || '-'}</div>
    <div><strong>duration:</strong> ${bottleneck.duration_ms ?? tree.duration_ms ?? '-'} ms</div>
  `;
}

async function loadOverview() {
  const data = await getJson('/api/overview');
  renderKpi(data.data || {});
}

async function loadRuns() {
  const traceId = encodeURIComponent(qs('traceIdInput').value.trim());
  const status = encodeURIComponent(qs('statusSelect').value);
  const sort = encodeURIComponent(qs('sortSelect').value);
  const data = await getJson(`/api/runs?trace_id=${traceId}&status=${status}&sort=${sort}&size=50`);
  const items = data.data?.items || [];
  renderRuns(items);
  if (!state.selectedRunId && items.length) {
    await selectRun(items[0]);
  }
}

async function selectRun(run) {
  state.selectedRunId = run.run_id;
  state.selectedTraceId = run.trace_id;
  const page = await getJson(`/api/runs/${encodeURIComponent(run.run_id)}/page`);
  renderRunDetail(page.data || {});
  if (run.trace_id) {
    await loadTraceInspect(run.trace_id);
  }
}

async function loadTraceInspect(traceId) {
  const data = await getJson(`/api/traces/${encodeURIComponent(traceId)}/inspect`);
  renderTraceInspect(data.data || {});
}

async function loadLogs() {
  if (!state.selectedTraceId) return;
  const spanId = encodeURIComponent(qs('spanIdInput').value.trim());
  const keyword = encodeURIComponent(qs('keywordInput').value.trim());
  const data = await getJson(`/api/logs/search?trace_id=${encodeURIComponent(state.selectedTraceId)}&span_id=${spanId}&keyword=${keyword}&limit=50`);
  qs('logsResult').textContent = JSON.stringify(data.data || {}, null, 2);
}

async function refreshAll() {
  try {
    await loadOverview();
    await loadRuns();
    qs('lastRefresh').textContent = `刷新: ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    qs('lastRefresh').textContent = `刷新失败: ${err.message}`;
  }
}

function setupAutoRefresh() {
  const box = qs('autoRefresh');
  const reset = () => {
    if (state.autoTimer) clearInterval(state.autoTimer);
    if (box.checked) {
      state.autoTimer = setInterval(refreshAll, 10000);
    }
  };
  box.addEventListener('change', reset);
  reset();
}

function bootstrap() {
  qs('refreshBtn').addEventListener('click', refreshAll);
  qs('filterBtn').addEventListener('click', refreshAll);
  qs('logSearchBtn').addEventListener('click', loadLogs);
  setupAutoRefresh();
  refreshAll();
}

window.addEventListener('DOMContentLoaded', bootstrap);
