const state = {
    currentTab: 'panel-dashboard',
    cyConfigured: false
};

document.addEventListener('DOMContentLoaded', () => {
    initECharts();

    window.addEventListener('resize', () => {
        if(window.apdexChart) window.apdexChart.resize();
        if(window.responseTimeChart) window.responseTimeChart.resize();
        if(window.cy) window.cy.resize();
    });

    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            fetchDashboardOverview();
        });
    }

    const searchTraceBtn = document.getElementById('searchTraceBtn');
    if (searchTraceBtn) {
        searchTraceBtn.addEventListener('click', () => {
            const traceId = document.getElementById('traceIdInput').value.trim();
            if (traceId) {
                fetchTraceDetails(traceId);
            }
        });
    }

    const loadRunsBtn = document.getElementById('loadRunsBtn');
    if (loadRunsBtn) {
        loadRunsBtn.addEventListener('click', fetchRuns);
    }
    
    // Check URL parameters for trace lookup
    const urlParams = new URLSearchParams(window.location.search);
    const initTrace = urlParams.get('trace_id');
    if (initTrace) {
        document.getElementById('traceIdInput').value = initTrace;
        // switch tab logic triggers chart repair too
        document.querySelector('[data-target="panel-trace"]').click();
        fetchTraceDetails(initTrace);
    }

    // Auto fetch initial data
    fetchDashboardOverview();
    fetchRuns();
});

// ===== API Calls & Renderings =====

async function fetchDashboardOverview() {
    try {
        const res = await fetch('/api/overview');
        const payload = await res.json();
        if (!payload.ok) throw new Error(payload.error);
        renderKPIs(payload.data.kpi);
    } catch (e) {
        console.error('Failed to fetch dashboard data:', e);
    }
}

function renderKPIs(kpiData) {
    const grid = document.getElementById('kpiGrid');
    if (!grid) return;
    if (!kpiData) {
        grid.innerHTML = '<div style="color:#aaa;">无法获取指标数据</div>';
        return;
    }
    const metrics = [
        { label: '总请求量 (Total)', value: kpiData.total },
        { label: '成功率 (Success Rate)', value: kpiData.success_rate + '%' },
        { label: '异常数 (Failed)', value: kpiData.failed, color: 'var(--sw-error)' },
        { label: '降级数 (Degraded)', value: kpiData.degraded, color: 'var(--sw-warn)' },
        { label: 'P95 响应 (P95)', value: kpiData.p95_duration_ms + ' ms' }
    ];
    grid.innerHTML = '';
    metrics.forEach(m => {
        let valStyle = m.color ? `color: ${m.color};` : 'color: var(--sw-text-main);';
        // Need specific Skywalking dark styles for KPI since we added them above dashboard grid
        grid.innerHTML += `
        <div style="flex: 1; min-width: 180px; background: #252a36; border: 1px solid #444; border-radius: 4px; padding: 16px; border-top: 3px solid var(--sw-primary);">
            <div style="color: #aaa; font-size: 13px; margin-bottom: 8px;">${m.label}</div>
            <div style="font-size: 24px; font-weight: bold; color: #fff;">${m.value}</div>
        </div>`;
    });
}

async function fetchRuns() {
    try {
        const res = await fetch('/api/runs?size=20');
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error);
        const tbody = document.getElementById('runsTableBody');
        if (!tbody) return;
        tbody.innerHTML = '';
        const items = payload.data.items || [];
        if (items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 16px;">无数据</td></tr>';
            return;
        }
        items.forEach(item => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid #444';
            tr.innerHTML = `
                <td style="padding: 8px 0;">
                   <span style="background: ${item.status === 'failed' ? '#e74c3c' : (item.status === 'degraded' ? '#e67e22' : '#2ecc71')}; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 12px;">${item.status}</span>
                </td>
                <td><a href="?trace_id=${item.trace_id}" style="color: var(--sw-primary); text-decoration: none;">${item.trace_id}</a></td>
                <td>${item.started_at ? new Date(item.started_at).toLocaleString() : '-'}</td>
                <td>
                   <button onclick="inspectTrace('${item.trace_id}')" style="background:transparent; border:1px solid #777; color:#ddd; padding: 4px 8px; border-radius: 4px; cursor: pointer;">查看异常/监控</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Fetch runs error', e);
    }
}

window.inspectTrace = function(traceId) {
    document.getElementById('traceIdInput').value = traceId;
    document.querySelector('[data-target="panel-trace"]').click();
    fetchTraceDetails(traceId);
};

async function fetchTraceDetails(traceId) {
    const statusText = document.getElementById('traceStatusText');
    statusText.innerText = '加载中...';
    try {
        const url = `/api/traces/${traceId}/inspect`;
        const res = await fetch(url);
        const payload = await res.json();
        
        if (!payload.ok) throw new Error(payload.error || 'Server error');
        statusText.innerText = `数据来源: ${payload.meta.source}`;
        
        const tree = payload.data.tree;
        const diagnosis = payload.data.diagnosis;
        
        renderTraceGantt(tree);
        // Also fetch topology from the tree directly
        renderTopology(tree);
        renderDiagnosisSummary(diagnosis);
        
    } catch (e) {
        statusText.innerText = `加载失败: ${e.message}`;
        document.getElementById('ganttBody').innerHTML = '';
        document.getElementById('diagnosisResultContent').innerHTML = `<div style="color:var(--sw-error)">${e.message}</div>`;
    }
}

// ===== Rendering Gantt =====
let totalDurationGlobal = 1;

function renderTraceGantt(rootSpan) {
    const container = document.getElementById('ganttBody');
    if (!container) return;
    container.innerHTML = '';
    
    // Calculate global duration from root
    totalDurationGlobal = rootSpan.duration_ms || 1;
    
    // Flatten tree and render rows
    let rowsHtml = '';
    function traverse(span, depth, cumStartObj) {
        let isVirtualRoot = (span.service_name === 'trace-root' || !span.parent_span_id && (span.operation_name || '').startsWith('trace:'));
        
        let childStartObj = { start: cumStartObj.start };
        if (!isVirtualRoot) {
            const dur = span.duration_ms || 0;
            const startOffsetMs = cumStartObj.start;
            const offsetPercent = Math.min((startOffsetMs / totalDurationGlobal) * 100, 100);
            const widthPercent = Math.max((dur / totalDurationGlobal) * 100, 0.5);
            
            const rowColorStr = span.status === 'ERROR' ? '#e74c3c' : 'var(--sw-primary)';
            
            const spanDataJson = JSON.stringify(span).replace(/"/g, '&quot;');

            let bgRowStr = '';
            if (span.status === 'ERROR') bgRowStr = 'background: rgba(231, 76, 60, 0.1); border-left: 3px solid #e74c3c;';

            rowsHtml += `
            <div class="gantt-row" style="padding-left: ${depth * 15}px; cursor: pointer; transition: background 0.2s; ${bgRowStr}" onclick="showSpanDetails(this, '${spanDataJson}')">
                <div class="g-col-name" style="flex: 2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${span.operation_name}">
                    <span style="font-size: 11px; padding: 2px 4px; background: #333; border-radius: 2px; margin-right: 6px;">${span.service_name}</span>
                    ${span.operation_name}
                </div>
                <div class="g-col-duration" style="flex: 1; padding: 0 10px;">${dur} ms</div>
                <div class="g-col-timeline" style="flex: 3; position: relative; background: rgba(255,255,255,0.05); height: 20px; border-radius: 2px;">
                    <div style="position: absolute; top: 4px; height: 12px; background: ${rowColorStr}; border-radius: 2px; min-width: 2px; left: ${offsetPercent}%; width: ${widthPercent}%;"></div>
                </div>
            </div>`;
            
            // Only increase depth if it's a real node
            depth++;
        }
        
        if (span.children && span.children.length > 0) {
            span.children.forEach(child => {
                traverse(child, depth, childStartObj);
                childStartObj.start += (child.duration_ms || 0); // Approximate sequential child offset
            });
        }
    }
    
    traverse(rootSpan, 0, { start: 0 });
    container.innerHTML = rowsHtml;
}

window.showSpanDetails = function(rowElement, jsonStr) {
    // Basic highlight toggle
    document.querySelectorAll('.gantt-row').forEach(el => el.style.borderLeft = '');
    rowElement.style.borderLeft = '3px solid var(--sw-primary)';

    const spanObj = JSON.parse(jsonStr);
    const dtPanel = document.getElementById('spanDetailContent');
    
    let errHtml = '';
    if (spanObj.status === 'ERROR' && spanObj.exception_stack) {
        errHtml = `
        <div style="margin-top: 10px; padding: 10px; background: rgba(231,76,60,0.1); border: 1px solid #e74c3c; border-radius: 4px; font-family: monospace; white-space: pre-wrap; font-size: 11px; overflow-x: auto; color: #ffcccc;">
${spanObj.exception_stack}
        </div>`;
    }

    dtPanel.innerHTML = `
        <table style="width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px;">
           <tr style="border-bottom: 1px solid #444;"><td style="padding: 8px 0; color: #888; width: 80px;">Service</td><td style="padding: 8px 0; color: #eee;">${spanObj.service_name}</td></tr>
           <tr style="border-bottom: 1px solid #444;"><td style="padding: 8px 0; color: #888;">Operation</td><td style="padding: 8px 0; color: #fff;">${spanObj.operation_name}</td></tr>
           <tr style="border-bottom: 1px solid #444;"><td style="padding: 8px 0; color: #888;">Span ID</td><td style="padding: 8px 0; color: #eee;">${spanObj.span_id}</td></tr>
           <tr style="border-bottom: 1px solid #444;"><td style="padding: 8px 0; color: #888;">Duration</td><td style="padding: 8px 0; font-weight: bold; color: #fff;">${spanObj.duration_ms} ms</td></tr>
           <tr style="border-bottom: 1px solid #444;"><td style="padding: 8px 0; color: #888;">Status</td><td style="padding: 8px 0;"><span style="font-weight: bold; color: ${spanObj.status === 'ERROR' ? '#e74c3c' : '#2ecc71'}">${spanObj.status}</span></td></tr>
        </table>
        ${errHtml}
    `;
};

// ===== Diagnosis Output =====
function renderDiagnosisSummary(diagnosisObj) {
    const parent = document.getElementById('diagnosisResultContent');
    if (!diagnosisObj || Object.keys(diagnosisObj).length === 0) {
        parent.innerHTML = '<div style="color: #aaa">暂无针对此链路的AI诊断报告。</div>';
        return;
    }
    
    // Parse the API data
    const bottleneckHtml = diagnosisObj.bottleneck ? `
       <div style="margin-bottom: 6px;"><b>发现瓶颈组件: </b> <span style="color: #fff">${diagnosisObj.bottleneck.service_name}::${diagnosisObj.bottleneck.operation_name}</span></div>
       <div style="margin-bottom: 6px; color: #e74c3c;"><b>慢调用详情: </b> ${diagnosisObj.bottleneck.duration_ms}ms 消耗 (占链路极大比重)</div>
    ` : '';

    let spansHtml = '';
    if (diagnosisObj.top_spans && diagnosisObj.top_spans.length > 0) {
        diagnosisObj.top_spans.forEach(s => {
            spansHtml += `<li style="color: #ddd;">${s.service_name} — 耗时: <b>${s.duration_ms}ms</b> ${s.status === 'ERROR' ? '<span style="color: #e74c3c">(错误)</span>' : ''}</li>`;
        });
    }

    parent.innerHTML = `
        <div style="background: rgba(68, 141, 254, 0.1); border-left: 3px solid var(--sw-primary); border-radius: 4px; padding: 12px; font-size: 13px; line-height: 1.6;">
            ${bottleneckHtml}
            ${spansHtml ? `<div style="margin-top: 8px;"><b>关键耗时微服务:</b><ul style="padding-left: 20px; margin-top: 4px;">${spansHtml}</ul></div>` : ''}
            
            <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(68,141,254,0.3);">
                <div style="color: #448dfe; font-weight: bold; margin-bottom: 4px;">智能建议 / 根因:</div>
                <div style="color: #fff;">${diagnosisObj.root_cause || "系统暂无确切定论 (未见明显报错或阈值突破)"}</div>
            </div>
        </div>
    `;
}

// ===== Rendering Topology (Cytoscape) =====
function renderTopology(rootSpan) {
    const cyDom = document.getElementById('cy');
    if (!cyDom) return;

    let elements = [];
    let edgesMap = new Map();
    let nodesMap = new Map();
    
    function collectEdges(span, parentNodeName) {
        let nodeName = span.service_name;
        if (!nodeName) nodeName = 'Unknown';
        
        let isVirtualRoot = (nodeName === 'trace-root' || !span.parent_span_id && span.operation_name.startsWith('trace:'));
        let isError = span.status === 'ERROR' || span.status === 'error' || span.status === 'failed' || span.status === 'degraded';

        // 真正的微服务节点才记录到 nodesMap和edgesMap
        if (!isVirtualRoot) {
            let existing = nodesMap.get(nodeName);
            if (!existing || isError) {
                nodesMap.set(nodeName, { id: nodeName, label: nodeName, isError: isError });
            }

            if (parentNodeName && parentNodeName !== nodeName) {
                const edgeKey = `${parentNodeName}->${nodeName}`;
                if (!edgesMap.has(edgeKey)) {
                    edgesMap.set(edgeKey, { source: parentNodeName, target: nodeName, calls: 1 });
                } else {
                    edgesMap.get(edgeKey).calls++;
                }
            }

            // 核心诉求："failed 终止不显示后面链路"，只对真实节点阻断
            if (isError) {
                return;
            }
        }

        // 递归子节点
        if (span.children && span.children.length > 0) {
            span.children.forEach(child => {
                collectEdges(child, isVirtualRoot ? null : nodeName);
            });
        }
    }
    
    collectEdges(rootSpan, null);

    // 基于 base64 SVG 构建 3D 棱柱模块，完美匹配 SkyWalking 的 3D 立体感 Cube
    const healthySvg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 115"><polygon points="50,0 100,25 100,85 50,110 0,85 0,25" fill="#4B4D57"/><polygon points="50,0 100,25 50,50 0,25" fill="#818590"/><polygon points="0,25 50,50 50,110 0,85" fill="#383A42"/></svg>';
    const errorSvg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 115"><polygon points="50,0 100,25 100,85 50,110 0,85 0,25" fill="#A83C4B"/><polygon points="50,0 100,25 50,50 0,25" fill="#E6586F"/><polygon points="0,25 50,50 50,110 0,85" fill="#842B38"/></svg>';
    const healthyCube = 'data:image/svg+xml;base64,' + btoa(healthySvg);
    const errorCube = 'data:image/svg+xml;base64,' + btoa(errorSvg);

    nodesMap.forEach((val) => {
        elements.push({ 
            data: { 
                id: val.id, 
                label: val.label,
                image: val.isError ? errorCube : healthyCube
            } 
        });
    });

    edgesMap.forEach((val, key) => {
        elements.push({ data: { id: key, source: val.source, target: val.target, label: '' } });
    });

    // Need to initialize visible
    document.getElementById('panel-topology').style.display = 'block';

    if (window.cy && typeof window.cy.destroy === 'function') {
        window.cy.destroy();
    }

    if (elements.length > 0) {
        window.cy = cytoscape({
            container: cyDom,
            elements: elements,
            style: [
                {
                    selector: 'node',
                    style: {
                        'background-color': 'transparent',
                        'background-image': 'data(image)',
                        'background-fit': 'contain',
                        'width': 44,
                        'height': 50,
                        'shape': 'rectangle',
                        'label': 'data(label)',
                        'color': '#fff',
                        'text-valign': 'bottom',
                        'text-halign': 'center',
                        'text-margin-y': 6,
                        'font-size': '12px'
                    }
                },
                {
                    selector: 'edge',
                    style: {
                        'width': 1.5,
                        'line-color': '#a3c2f6',
                        'line-style': 'dashed',
                        'target-arrow-color': '#a3c2f6',
                        'target-arrow-shape': 'triangle',
                        'curve-style': 'bezier'
                    }
                }
            ],
            layout: {
                name: 'breadthfirst',
                directed: true,
                spacingFactor: 1.5
            }
        });
    }

    // Hide it back again since we're currently viewing the trace panel
    document.getElementById('panel-topology').style.display = 'none';
}

// ===== ECharts Static Init =====
function initECharts() {
    if (typeof echarts === 'undefined') return;

    const apdexDom = document.getElementById('apdexChart');
    if (apdexDom) {
        window.apdexChart = echarts.init(apdexDom);
        window.apdexChart.setOption({
            backgroundColor: 'transparent',
            tooltip: { trigger: 'axis' },
            xAxis: { type: 'category', data: ['09:40', '09:45', '09:50', '09:55', '10:00', '10:05'], show: false },
            yAxis: { type: 'value', min: 0, max: 1, splitLine: { lineStyle: { type: 'dashed', color: '#444' } } },
            series: [{ data: [1, 0.95, 0.9, 0.97, 1, 0.9], type: 'line', smooth: false, lineStyle: { color: '#448dfe', width: 2 }, symbol: 'none' }],
            grid: { left: 40, right: 20, top: 20, bottom: 20 }
        });
    }

    const respDom = document.getElementById('responseTimeChart');
    if (respDom) {
        window.responseTimeChart = echarts.init(respDom);
        window.responseTimeChart.setOption({
            backgroundColor: 'transparent',
            tooltip: { trigger: 'axis' },
            xAxis: { type: 'category', data: ['09:40', '09:45', '09:50', '09:55', '10:00', '10:05'], show: false },
            yAxis: { type: 'value', splitLine: { lineStyle: { type: 'dashed', color: '#444' } } },
            series: [{ data: [50, 300, 40, 250, 60, 45], type: 'line', smooth: false, areaStyle: { color: 'rgba(68, 141, 254, 0.1)' }, lineStyle: { color: '#448dfe', width: 2 }, symbol: 'none' }],
            grid: { left: 40, right: 20, top: 20, bottom: 20 }
        });
    }
}
