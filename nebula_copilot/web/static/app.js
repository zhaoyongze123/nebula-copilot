const state = {
  selectedRunId: null,
  selectedTraceId: null,
  selectedSpanId: null,
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

function setSource(id, source) {
  const el = qs(id);
  if (!el) return;
  el.textContent = `source:${source || '-'}`;
}

function eventClass(text) {
  const raw = String(text || '').toLowerCase();
  if (/error|failed|exception|timeout/.test(raw)) return 'error';
  if (/warn|degraded|retry|fallback/.test(raw)) return 'warn';
  return 'info';
}

async function getJson(url) {
  const res = await fetch(url);
  const payload = await res.json();
  if (!res.ok || payload.ok === false) {
    const message = payload.error || `HTTP ${res.status}`;
    throw new Error(message);
  }
  return payload;
}

function renderKpi(data) {
  const root = qs('kpiGrid');
  if (!root) return;
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
  const body = qs('runsAccordion');
  if (!body) {
    const legacyTable = qs('runsTable');
    const legacyBody = legacyTable ? legacyTable.querySelector('tbody') : null;
    if (!legacyBody) return;
    legacyBody.innerHTML = '';
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
      legacyBody.appendChild(tr);
    }
    return;
  }
  body.innerHTML = '';

  if (!items.length) {
    body.innerHTML = '<div class="card">暂无符合条件的 run 记录</div>';
    return;
  }

  for (const run of items) {
    const details = document.createElement('details');
    details.className = 'run-item';
    details.dataset.runId = run.run_id || '';
    details.innerHTML = `
      <summary>
        <span>${statusBadge(run.status)}</span>
        <span>${run.run_id || '-'}</span>
        <span class="run-meta">trace: ${run.trace_id || '-'}</span>
        <span class="run-meta">${fmtTime(run.started_at || run.timestamp)}</span>
      </summary>
      <div class="run-body">
        <div><strong>duration:</strong> ${run.duration_ms ?? '-'} ms</div>
        <div><strong>notify:</strong> ${(run.notify || {}).status || '-'}</div>
      </div>
    `;

    details.addEventListener('toggle', () => {
      if (details.open) {
        selectRun(run);
      }
    });

    if (state.selectedRunId && state.selectedRunId === run.run_id) {
      details.open = true;
      details.classList.add('active');
    }

    body.appendChild(details);
  }
}

function renderRunDetail(page) {
  const summary = qs('runSummary');
  if (!summary) return;
  const run = page.summary || {};
  summary.innerHTML = `
    <div><strong>run_id:</strong> ${run.run_id || '-'}</div>
    <div><strong>trace_id:</strong> ${run.trace_id || '-'}</div>
    <div><strong>status:</strong> ${statusBadge(run.status || 'ok')}</div>
    <div><strong>duration:</strong> ${run.duration_ms ?? '-'} ms</div>
    <div><strong>started:</strong> ${fmtTime(run.started_at || run.timestamp)}</div>
  `;

  const timeline = qs('timeline');
  if (!timeline) return;
  timeline.innerHTML = '';
  const events = page.timeline || [];
  for (const ev of events) {
    const li = document.createElement('li');
    const eventText = `${ev.phase || ''} ${ev.message || ''}`;
    li.className = eventClass(`${ev.phase || ''} ${ev.message || ''}`);
    li.textContent = `${fmtTime(ev.ts || ev.timestamp)} | ${ev.phase || ev.name || 'event'} | ${ev.message || ''}`;
    li.dataset.searchText = eventText.toLowerCase();
    timeline.appendChild(li);
  }

  const diagnosis = qs('diagnosis');
  if (diagnosis) {
    diagnosis.textContent = page.diagnosis?.summary || JSON.stringify(page.diagnosis || {}, null, 2);
  }

  const llmResult = qs('llmResult');
  if (!llmResult) return;
  const raw = page.raw || {};
  const history = Array.isArray(raw.history) ? raw.history : [];
  const llmEvents = history.filter((ev) => {
    const node = String(ev.node || '').toLowerCase();
    return node.includes('llm') || node.includes('report_polish');
  });

  const llmSummary = raw.summary || '';
  if (llmSummary || llmEvents.length) {
    const eventText = llmEvents.map((ev) => {
      return `[${ev.node || '-'}|${ev.status || '-'}] ${ev.message || ''}`;
    }).join('\n');
    llmResult.textContent = [
      llmSummary ? `摘要:\n${llmSummary}` : '',
      eventText ? `\nLLM事件:\n${eventText}` : ''
    ].filter(Boolean).join('\n');
  } else {
    llmResult.textContent = '本次运行无 LLM 分析结果（可能使用规则分析或未启用 LLM）。';
  }
}

function renderTraceInspect(payload) {
  const panel = qs('tracePanel');
  if (!panel) return;
  const tree = normalizeTraceTree(payload.tree || {});
  const diagnosis = payload.diagnosis || {};
  const bottleneck = diagnosis.bottleneck?.span || {};
  panel.innerHTML = `
    <div><strong>trace_id:</strong> ${payload.trace_id || '-'}</div>
    <div><strong>root service:</strong> ${tree.service_name || '-'}</div>
    <div><strong>bottleneck:</strong> ${bottleneck.service_name || '-'}</div>
    <div><strong>duration:</strong> ${bottleneck.duration_ms ?? tree.duration_ms ?? '-'} ms</div>
  `;

  const treeBox = qs('traceTree');
  if (!treeBox) return;
  treeBox.innerHTML = '';
  if (tree && tree.span_id) {
    treeBox.appendChild(renderSpanNode(tree));
    renderTopology(tree, bottleneck.span_id);
  } else {
    treeBox.textContent = '无可展示的 trace 树';
    const cyBox = qs('cy');
    if (cyBox) cyBox.innerHTML = '';
  }
}

let cyInstance = null;

function isSyntheticRootNode(node) {
  if (!node) return false;
  const service = String(node.service_name || '').toLowerCase();
  const operation = String(node.operation_name || '').toLowerCase();
  return service === 'trace-root' || operation.startsWith('trace:');
}

function normalizeTraceTree(root) {
  let current = root;
  while (
    current &&
    isSyntheticRootNode(current) &&
    Array.isArray(current.children) &&
    current.children.length === 1 &&
    isSyntheticRootNode(current.children[0])
  ) {
    current = current.children[0];
  }
  return current || root;
}

function renderTopology(treeRoot, bottleneckId) {
  const container = qs('cy');
  if (!container || typeof cytoscape === 'undefined') return;
  const elements = [];

  function traverse(node, parentId) {
    if (!node || !node.span_id) return;
    const currentNodeId = node.span_id;
    const skipNode = isSyntheticRootNode(node);

    if (!skipNode) {
      elements.push({
        data: {
          id: currentNodeId,
          label: `${node.service_name || 'unknown'}\n${node.operation_name || ''}\n${node.status || 'OK'}`,
          status: String(node.status || 'OK').toUpperCase(),
          isBottleneck: currentNodeId === bottleneckId
        }
      });

      if (parentId) {
        elements.push({
          data: {
            id: `${parentId}-${currentNodeId}`,
            source: parentId,
            target: currentNodeId
          }
        });
      }
    }

    const nextParentId = skipNode ? parentId : currentNodeId;

    if (Array.isArray(node.children)) {
      node.children.forEach(child => traverse(child, nextParentId));
    }
  }

  traverse(treeRoot, null);

  if (!elements.length) {
    container.innerHTML = '<div style="padding:8px;color:#5f6b73;">当前链路无可展示的服务拓扑节点</div>';
    return;
  }

  if (cyInstance) {
    cyInstance.destroy();
  }

  cyInstance = cytoscape({
    container,
    elements: elements,
    style: [
      {
        selector: 'node',
        style: {
          'background-color': function(ele) {
            if (ele.data('isBottleneck')) return '#bf2f2f';
            const status = String(ele.data('status') || 'OK').toUpperCase();
            if (status === 'ERROR') return '#d14949';
            if (status === 'SKIPPED') return '#c58a1b';
            return '#0b5ea8';
          },
          'label': 'data(label)',
          'color': '#1b2b34',
          'font-size': '10px',
          'text-wrap': 'wrap',
          'text-valign': 'bottom',
          'text-margin-y': 4,
          'font-weight': function(ele) {
            const status = String(ele.data('status') || 'OK').toUpperCase();
            return (ele.data('isBottleneck') || status === 'ERROR') ? 'bold' : 'normal';
          },
        }
      },
      {
        selector: 'edge',
        style: {
          'width': 2,
          'line-color': '#d7e0e6',
          'target-arrow-color': '#d7e0e6',
          'target-arrow-shape': 'triangle',
          'curve-style': 'taxi',
          'taxi-direction': 'downward'
        }
      },
      {
        selector: 'node.highlight',
        style: {
          'background-color': '#0f8f4f',
          'border-width': 2,
          'border-color': '#0f8f4f'
        }
      },
      {
        selector: 'edge.highlight',
        style: {
          'line-color': '#0f8f4f',
          'target-arrow-color': '#0f8f4f',
          'width': 3
        }
      }
    ],
    layout: {
      name: 'breadthfirst',
      directed: true,
      padding: 10
    }
  });

  cyInstance.on('mouseover', 'node', function(e){
    const sel = e.target;
    cyInstance.elements().removeClass('highlight');
    sel.addClass('highlight');
    sel.predecessors().addClass('highlight');
    sel.successors().addClass('highlight');
  });

  cyInstance.on('mouseout', 'node', function(e){
    cyInstance.elements().removeClass('highlight');
  });
}

function renderSpanNode(node) {
  const hasChildren = Array.isArray(node.children) && node.children.length > 0;
  const wrapper = document.createElement(hasChildren ? 'details' : 'div');
  if (hasChildren) {
    wrapper.open = true;
  }

  const line = document.createElement('div');
  line.className = 'span-line';
  line.dataset.spanId = node.span_id;
  line.innerHTML = `<strong>${node.service_name}</strong> / ${node.operation_name} / ${node.duration_ms}ms / ${node.status}`;

  line.addEventListener('click', () => selectSpanNode(node));

  const pickBtn = document.createElement('button');
  pickBtn.className = 'pick-btn';
  pickBtn.textContent = '查日志';
  pickBtn.addEventListener('click', async (ev) => {
    ev.preventDefault();
    selectSpanNode(node);
    qs('spanIdInput').value = node.span_id;
    await loadLogs();
  });
  line.appendChild(pickBtn);

  if (!hasChildren) {
    wrapper.appendChild(line);
    return wrapper;
  }

  const summary = document.createElement('summary');
  summary.appendChild(line);
  wrapper.appendChild(summary);

  for (const child of node.children) {
    wrapper.appendChild(renderSpanNode(child));
  }
  return wrapper;
}

function selectSpanNode(node) {
  state.selectedSpanId = node.span_id;
  qs('spanIdInput').value = node.span_id;
  if (!qs('keywordInput').value.trim()) {
    qs('keywordInput').value = node.service_name || node.operation_name || '';
  }

  document.querySelectorAll('.tree .span-line').forEach((el) => {
    el.classList.toggle('active', el.dataset.spanId === node.span_id);
  });

  const needle = `${node.service_name || ''} ${node.operation_name || ''} ${node.span_id || ''}`.toLowerCase();
  const timelineItems = Array.from(document.querySelectorAll('#timeline li'));
  let found = false;
  for (const item of timelineItems) {
    const hit = needle && (item.dataset.searchText || '').includes(needle);
    item.classList.toggle('active', hit);
    if (hit && !found) {
      item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      found = true;
    }
  }
}

async function loadOverview() {
  const data = await getJson('/api/overview');
  renderKpi(data.data || {});
  setSource('sourceKpi', data.meta?.source);
}

async function loadRuns() {
  const traceId = encodeURIComponent(qs('traceIdInput').value.trim());
  const status = encodeURIComponent(qs('statusSelect').value);
  const sort = encodeURIComponent(qs('sortSelect').value);
  const data = await getJson(`/api/runs?trace_id=${traceId}&status=${status}&sort=${sort}&size=50`);
  const items = data.data?.items || [];
  renderRuns(items);
  setSource('sourceRuns', data.meta?.source);
  if (!state.selectedRunId && items.length) {
    await selectRun(items[0]);
  }
}

async function selectRun(run) {
  state.selectedRunId = run.run_id;
  state.selectedTraceId = run.trace_id;

  document.querySelectorAll('.run-item').forEach((el) => {
    const runId = el.dataset.runId || '';
    const active = runId === run.run_id;
    el.classList.toggle('active', active);
    if (active) {
      el.open = true;
    }
  });

  const page = await getJson(`/api/runs/${encodeURIComponent(run.run_id)}/page`);
  renderRunDetail(page.data || {});
  setSource('sourceRunDetail', page.meta?.source);
  if (run.trace_id) {
    try {
      await loadTraceInspect(run.trace_id);
    } catch (err) {
      const panel = qs('tracePanel');
      if (panel) {
        panel.innerHTML = `<div><strong>trace_id:</strong> ${run.trace_id}</div><div><strong>提示:</strong> Trace 检查暂不可用：${err.message}</div>`;
      }
      setSource('sourceTrace', 'error');
    }
  }
}

async function loadTraceInspect(traceId) {
  const data = await getJson(`/api/traces/${encodeURIComponent(traceId)}/inspect`);
  renderTraceInspect(data.data || {});
  setSource('sourceTrace', data.meta?.source);
}

async function loadLogs() {
  if (!state.selectedTraceId) return;
  const spanId = encodeURIComponent(qs('spanIdInput').value.trim());
  const keyword = encodeURIComponent(qs('keywordInput').value.trim());
  const data = await getJson(`/api/logs/search?trace_id=${encodeURIComponent(state.selectedTraceId)}&span_id=${spanId}&keyword=${keyword}&limit=50`);
  qs('logsResult').textContent = JSON.stringify(data.data || {}, null, 2);
  setSource('sourceLogs', data.meta?.source);
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
