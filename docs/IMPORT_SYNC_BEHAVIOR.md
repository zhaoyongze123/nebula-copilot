# 批量导入和自动同步行为说明

## 概述

导入和同步功能从 Elasticsearch 获取历史 traces，并保存到本地 `agent_runs.json`。

## 数据流向

```
Elasticsearch (nebula_metrics)
         ↓
  [导入/同步模块]
         ↓
  agent_runs.json (本地)
         ↓
  Web Dashboard
```

## 导入行为

### 什么是"导入"
- **功能**：按时间范围从 ES 一次性批量拉取历史 traces
- **流程**：`/api/import/start` → 后台处理 → 保存到 `data/agent_runs.json`
- **UI**：显示进度条，直到完成

### 导入后的数据特征

每个从 ES 导入的 trace 会生成一个 run 记录：

```json
{
  "run_id": "imported_0ac6128c5f8d",
  "trace_id": "0b962989652d457e",
  "started_at": "2026-03-31T19:55:38",
  "finished_at": "2026-03-31T19:55:38.448",
  "status": "ok",
  "metrics": {
    "duration_ms": 448,
    "span_count": 2,
    "service_count": 2,
    "has_error": false
  },
  "_source": "es_import"
}
```

### 为什么看起来"毫无关联"

- **原因**：每个导入的 trace 都是独立的记录，trace_id 各不相同
- **现象**：runs 列表中看到很多不同的 imported_* 行
- **这是正常的**：这反映了 ES 中的实际数据（多个独立的微服务调用链）

### 如何查看完整的 trace tree

1. 在 Web Dashboard 中点击任意导入的 run
2. 右侧 "Trace Inspect" 面板会显示完整的 span tree
3. tree 数据来自 ES（通过 `/api/traces/<trace_id>/inspect`）

**关键点**：local JSON 中不存储完整的 span tree，只存储 run 元数据。实际的 span 数据在点击时从 ES 查询。

## 自动同步行为

### 什么是"同步"
- **功能**：定时自动拉取 ES 中的最新 traces（增量更新）
- **流程**：`/api/sync/start` → 后台线程 → 每 N 秒检查一次 → 增量更新本地文件
- **UI**：显示同步状态（运行中、已暂停、成功/失败数量）

### 同步工作原理

```
同步线程 (后台)
    ├─ 每 300 秒（可配置）
    ├─ 回溯查询最近 60 分钟的 ES 数据
    ├─ 对比已导入的 traces
    ├─ 只导入新增的 traces
    └─ 增量写入到 agent_runs.json
```

### 启动和停止

```javascript
// 启动同步
POST /api/sync/start?interval_seconds=300&lookback_minutes=60

// 停止同步
POST /api/sync/stop

// 查询状态
GET /api/sync/status
```

## 数据去重规则

- **主键**：`trace_id`
- **去重逻辑**：新导入的 traces 如果 trace_id 已存在，则覆盖旧数据
- **排序**：按 `started_at` 时间戳降序排列

## 常见问题

### Q: 为什么导入的数据只有 2-3 个 spans？
A: 这反映了 ES 中的实际数据结构。不同的 traces 可能有不同的深度。
   - 如果期望更深的树，检查 ES 中的原始数据
   - 通过 `/api/traces/<trace_id>/inspect` 查看完整的 span tree

### Q: 如何确认同步在运行？
A: 
   1. 在 Web Dashboard 中点击 "🔄 自动同步" 按钮
   2. 查看浮动面板中的状态和时间戳
   3. 也可以通过 API 查询：`GET /api/sync/status`

### Q: 导入和同步有什么区别？
A: 
   - **导入**：手动触发，按日期范围拉取（历史数据回溯）
   - **同步**：自动定时，增量拉取最新数据（持续监听）

### Q: 导入失败了怎么办？
A: 检查错误信息：
   1. ES 地址/端口是否正确
   2. 索引名称是否存在
   3. 认证信息（用户名/密码）是否正确
   4. 日期格式是否为 ISO 8601（例如：2026-03-24T00:00:00）

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `NEBULA_ES_URL` | `http://localhost:9200` | ES 地址 |
| `NEBULA_ES_INDEX` | `nebula_metrics` | 索引名 |
| `NEBULA_ES_USERNAME` | - | ES 用户名（可选） |
| `NEBULA_ES_PASSWORD` | - | ES 密码（可选） |

## 技术细节

### 导入处理流程

1. 接收 `/api/import/start` 请求（with 日期范围、limit 等参数）
2. ESImporter 从 ES 查询 traces（按时间范围）
3. 将每个 TraceDocument 转换为 agent_run dict
4. 提取 metrics（span 数量、服务列表、是否有错误）
5. 构建 diagnosis 和 history
6. 与现有数据去重并合并
7. 保存到 `data/agent_runs.json`

### 查询 trace 的完整树

点击 run 时的流程：
1. 获取 `run.trace_id`
2. `/api/traces/<trace_id>/inspect` 
3. 首先尝试从本地 JSON 加载
4. 如果失败，从 ES 查询
5. 返回完整的 span tree + diagnosis
