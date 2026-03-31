# ES 批量导入与自动同步 API 文档

## 概述

nebula-copilot 提供了两个新的功能来从 Elasticsearch 导入历史数据：
1. **批量导入**：一次性从 ES 导入指定时间范围内的所有 traces
2. **自动同步**：后台线程定时拉取最新的 traces 到本地

## 批量导入 API

### 启动导入任务

**请求**
```http
POST /api/import/start
```

**查询参数**
| 参数 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `from_date` | ✓ | - | 开始时间（ISO 8601，例如：2025-03-01T00:00:00） |
| `to_date` | ✓ | - | 结束时间（ISO 8601，例如：2025-03-31T23:59:59） |
| `limit` | × | 1000 | 最大导入数量 |
| `es_url` | × | localhost:9200 | Elasticsearch 地址（可使用环境变量 NEBULA_ES_URL） |
| `index` | × | nebula_metrics | 索引名（可使用环境变量 NEBULA_ES_INDEX） |
| `username` | × | - | ES 用户名（可使用环境变量 NEBULA_ES_USERNAME） |
| `password` | × | - | ES 密码（可使用环境变量 NEBULA_ES_PASSWORD） |
| `output_path` | × | data/agent_runs.json | 输出文件路径 |

**cURL 示例**
```bash
curl -X POST "http://localhost:8080/api/import/start" \
  -G \
  --data-urlencode "from_date=2025-03-01T00:00:00" \
  --data-urlencode "to_date=2025-03-31T23:59:59" \
  --data-urlencode "limit=1000" \
  --data-urlencode "es_url=http://elasticsearch:9200" \
  --data-urlencode "index=nebula-trace-*"
```

**响应**
```json
{
  "ok": true,
  "data": {
    "task_id": "a1b2c3d4",
    "status": "running",
    "created_at": "2025-03-31T11:00:00.000000"
  },
  "meta": {
    "source": "local",
    "degraded": false,
    "latency_ms": 125
  }
}
```

### 查询导入进度

**请求**
```http
GET /api/import/<task_id>/status
```

**Path 参数**
| 参数 | 说明 |
|------|------|
| `task_id` | 导入任务 ID（由启动导入任务返回） |

**cURL 示例**
```bash
curl "http://localhost:8080/api/import/a1b2c3d4/status"
```

**响应**
```json
{
  "ok": true,
  "data": {
    "task_id": "a1b2c3d4",
    "status": "done",
    "progress": 100,
    "error": null,
    "created_at": "2025-03-31T11:00:00.000000",
    "updated_at": "2025-03-31T11:02:15.000000",
    "result": {
      "imported_count": 850
    }
  },
  "meta": {
    "source": "local",
    "degraded": false,
    "latency_ms": 5
  }
}
```

**status 枚举值**
- `running`：正在进行导入
- `done`：导入完成
- `error`：导入出错

## 自动同步 API

### 启动自动同步

**请求**
```http
POST /api/sync/start
```

**查询参数**
| 参数 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `interval_seconds` | × | 300 | 同步间隔（秒） |
| `lookback_minutes` | × | 60 | 回溯窗口（分钟） |
| `es_url` | × | localhost:9200 | Elasticsearch 地址 |
| `index` | × | nebula_metrics | 索引名 |
| `username` | × | - | ES 用户名 |
| `password` | × | - | ES 密码 |
| `output_path` | × | data/agent_runs.json | 输出文件路径 |

**cURL 示例**
```bash
curl -X POST "http://localhost:8080/api/sync/start" \
  -G \
  --data-urlencode "interval_seconds=300" \
  --data-urlencode "lookback_minutes=60" \
  --data-urlencode "es_url=http://elasticsearch:9200"
```

**响应**
```json
{
  "ok": true,
  "data": {
    "status": "started"
  },
  "meta": {
    "source": "local",
    "degraded": false,
    "latency_ms": 50
  }
}
```

### 查询同步状态

**请求**
```http
GET /api/sync/status
```

**cURL 示例**
```bash
curl "http://localhost:8080/api/sync/status"
```

**响应**
```json
{
  "ok": true,
  "data": {
    "is_running": true,
    "last_sync_time": "2025-03-31T11:05:00.000000",
    "total_synced": 1250,
    "total_errors": 3
  },
  "meta": {
    "source": "local",
    "degraded": false,
    "latency_ms": 8
  }
}
```

### 停止自动同步

**请求**
```http
POST /api/sync/stop
```

**cURL 示例**
```bash
curl -X POST "http://localhost:8080/api/sync/stop"
```

**响应**
```json
{
  "ok": true,
  "data": {
    "status": "stopped"
  },
  "meta": {
    "source": "local",
    "degraded": false,
    "latency_ms": 100
  }
}
```

## 工作流示例

### 完整导入 + 同步工作流

```bash
# 1. 启动自动同步（后台运行）
curl -X POST "http://localhost:8080/api/sync/start" \
  -G \
  --data-urlencode "interval_seconds=300" \
  --data-urlencode "lookback_minutes=60"

# 2. 查询同步状态
curl "http://localhost:8080/api/sync/status"

# 3. 批量导入历史数据（最近 7 天）
IMPORT_TASK=$(curl -s -X POST "http://localhost:8080/api/import/start" \
  -G \
  --data-urlencode "from_date=$(date -u -d '7 days ago' +%Y-%m-%dT00:00:00)" \
  --data-urlencode "to_date=$(date -u +%Y-%m-%dT23:59:59)" \
  --data-urlencode "limit=2000" | jq -r '.data.task_id')

echo "导入任务 ID: $IMPORT_TASK"

# 4. 轮询导入进度
while true; do
  RESULT=$(curl -s "http://localhost:8080/api/import/$IMPORT_TASK/status")
  STATUS=$(echo $RESULT | jq -r '.data.status')
  PROGRESS=$(echo $RESULT | jq -r '.data.progress')
  
  echo "Status: $STATUS, Progress: $PROGRESS%"
  
  if [ "$STATUS" == "done" ]; then
    echo "导入完成！导入数量: $(echo $RESULT | jq -r '.data.result.imported_count')"
    break
  fi
  
  if [ "$STATUS" == "error" ]; then
    echo "导入失败: $(echo $RESULT | jq -r '.data.error')"
    break
  fi
  
  sleep 5
done

# 5. 停止自动同步
curl -X POST "http://localhost:8080/api/sync/stop"
```

## 错误处理

### 常见错误响应

**时间格式错误**
```json
{
  "ok": false,
  "error": "Invalid date format: ...",
  "data": {},
  "meta": {...}
}
```

**导入任务不存在**
```json
{
  "ok": false,
  "error": "task_not_found",
  "data": {},
  "meta": {...}
}
```

**同步已在运行**
```json
{
  "ok": false,
  "error": "Sync is already running",
  "data": {},
  "meta": {...}
}
```

**Elasticsearch 连接失败**
```json
{
  "ok": false,
  "error": "Failed to connect to Elasticsearch: ...",
  "data": {},
  "meta": {"degraded": true, ...}
}
```

## 性能考虑

### 批量导入
- **单次导入上限**：建议 1000-2000 traces
- **时间范围**：根据需要调整（每个 trace 包含多个 span 可能较大）
- **并发导入**：支持多个并发导入任务

### 自动同步
- **默认间隔**：300 秒（5 分钟）
- **回溯窗口**：默认 60 分钟（只拉取最近 1 小时的数据）
- **后台运行**：不影响其他 API 响应
- **资源开销**：低（每 5 分钟一次小查询）

### API 响应时间
- `GET /api/sync/status`：< 10ms
- `POST /api/import/start`：< 100ms（异步返回）
- `GET /api/import/<task_id>/status`：< 10ms
- `POST /api/sync/start`：< 50ms
- `POST /api/sync/stop`：< 100ms

## 数据完整性

### 去重机制
- 导入的 traces 按 `trace_id` 去重
- 新导入数据覆盖旧数据（latest wins）
- 去重仅基于 `trace_id`，不考虑时间戳

### 增量同步
- 自动同步仅拉取 `last_sync_time` 之后的数据
- 避免重复导入相同的 traces
- 可通过调整 `lookback_minutes` 扩大时间窗口

## 环境变量配置

为了避免每次请求都传递 ES 连接参数，可以设置以下环境变量：

```bash
export NEBULA_ES_URL="http://elasticsearch:9200"
export NEBULA_ES_INDEX="nebula-trace-*"
export NEBULA_ES_USERNAME="elastic"
export NEBULA_ES_PASSWORD="changeme"
```

设置后，API 调用可以省略这些参数：

```bash
curl -X POST "http://localhost:8080/api/import/start" \
  -G \
  --data-urlencode "from_date=2025-03-01T00:00:00" \
  --data-urlencode "to_date=2025-03-31T23:59:59"
```

## 故障排查

### 导入超时
- 增加 `limit` 参数的数值，分多次导入
- 检查 Elasticsearch 连接和性能

### 同步未启动
- 检查 Elasticsearch 连接
- 查看日志是否有错误信息

### 数据未出现在 Web Dashboard
- 确认导入/同步已完成
- 检查 `output_path` 指定的文件是否存在
- 刷新 Web Dashboard 页面

### 导入卡在 "running" 状态
- 检查后台日志
- 可能是 Elasticsearch 查询超时，尝试减小 `limit` 参数
