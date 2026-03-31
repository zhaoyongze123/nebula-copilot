# Nebula-Monitor 集成指南

本指南说明如何配置 nebula-monitor 以输出符合 nebula-copilot 要求的数据格式。

## 快速检查清单

在 nebula-monitor 中配置 Elasticsearch 导出器时，确保：

- [ ] 索引名称：`nebula_metrics`（或通过 `NEBULA_ES_INDEX` 自定义）
- [ ] 每条 span 记录必须包含以下字段：
  - `trace_id` / `traceId`
  - `span_id` / `spanId`
  - `parent_span_id` / `parentSpanId`
  - `service_name` / `serviceName`
  - `operation_name` / `operationName`
  - `status`：`OK | ERROR | SKIPPED | TIMEOUT`
  - `duration_ms` / `durationMs`：毫秒级整数
  - `timestamp`：**epoch_millis 格式（毫秒时间戳）**

## 数据字段映射

### 从 nebula-monitor 的数据模型到 ES

| nebula-monitor 字段 | → | ES 字段 | 类型 | 必需 |
|-----------------|---|---------|------|------|
| trace.id | → | trace_id | keyword | ✅ |
| span.id | → | span_id | keyword | ✅ |
| span.parentId | → | parent_span_id | keyword | ✅ |
| span.serviceName | → | service_name | keyword | ✅ |
| span.operationName | → | operation_name | keyword | ✅ |
| span.status | → | status | keyword | ✅ |
| span.durationMs | → | duration_ms | long | ✅ |
| span.timestamp | → | timestamp | date (epoch_millis) | ✅ |
| span.exceptionStack | → | exception_stack | text | ❌ |
| span.httpStatus | → | httpStatus | integer | ❌ |
| span.errorType | → | error_type | keyword | ❌ |
| metadata.environment | → | environment | keyword | ❌ |
| metadata.region | → | region | keyword | ❌ |
| metadata.cluster | → | cluster | keyword | ❌ |
| metadata.instanceId | → | instanceId | keyword | ❌ |
| metrics.jvm.heapUsedMb | → | heap_used_mb | float | ❌ |
| metrics.jvm.heapMaxMb | → | heap_max_mb | float | ❌ |

## 配置示例

### 环境变量配置

在 nebula-monitor 中设置以下变量：

```bash
# Elasticsearch 连接
ELASTICSEARCH_URL=http://localhost:9200
ELASTICSEARCH_INDEX=nebula_metrics
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_PASSWORD=your_password

# 导出器配置
EXPORTER_TYPE=elasticsearch
EXPORTER_ENABLED=true
EXPORTER_BATCH_SIZE=100
EXPORTER_TIMEOUT_MS=10000

# 字段映射（确保时间戳为 epoch_millis）
TIMESTAMP_FORMAT=epoch_millis
DURATION_UNIT=milliseconds
```

### Docker Compose 配置示例

如果 nebula-monitor 与 nebula-copilot 共享 Elasticsearch：

```yaml
version: '3.8'
services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.0.0
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
    ports:
      - "9200:9200"
    volumes:
      - es-data:/usr/share/elasticsearch/data

  nebula-monitor:
    image: your-org/nebula-monitor:latest
    environment:
      ELASTICSEARCH_URL: http://elasticsearch:9200
      ELASTICSEARCH_INDEX: nebula_metrics
      EXPORTER_TYPE: elasticsearch
    depends_on:
      - elasticsearch

  nebula-copilot:
    image: your-org/nebula-copilot:latest
    environment:
      NEBULA_ES_URL: http://elasticsearch:9200
      NEBULA_ES_INDEX: nebula_metrics
    depends_on:
      - elasticsearch
    ports:
      - "8080:8080"

volumes:
  es-data:
```

## 时间戳格式转换

**这是最常见的问题！**

### ❌ 常见错误

nebula-monitor 可能默认使用 ISO 8601 格式发送时间戳：

```json
{
  "timestamp": "2026-03-31T19:49:00Z"  // ❌ ISO 8601 格式
}
```

这会导致 copilot 查询时出现：
```
BadRequestError(400, 'search_phase_execution_exception',
  'failed to parse date field [2026-03-31T19:49:00Z] with format [epoch_millis]')
```

### ✅ 正确做法

将时间戳转换为毫秒级 Unix 时间戳：

```json
{
  "timestamp": 1711900800000  // ✅ epoch_millis 格式
}
```

### Python 转换示例

```python
from datetime import datetime

# 获取当前时间戳（毫秒）
timestamp_ms = int(datetime.now().timestamp() * 1000)

# 发送到 ES
document = {
    "trace_id": "abc123",
    "span_id": "def456",
    "timestamp": timestamp_ms,  # 毫秒时间戳
    # ... 其他字段
}
```

### Java 转换示例

```java
// 获取当前时间戳（毫秒）
long timestampMs = System.currentTimeMillis();

// 发送到 ES
Map<String, Object> document = new HashMap<>();
document.put("trace_id", "abc123");
document.put("span_id", "def456");
document.put("timestamp", timestampMs);  // 毫秒时间戳
// ... 其他字段
```

## 数据验证

### 1. 检查索引是否存在

```bash
curl -X GET "localhost:9200/nebula_metrics" \
  -u elastic:your_password
```

### 2. 检查索引映射

```bash
curl -X GET "localhost:9200/nebula_metrics/_mapping" \
  -u elastic:your_password
```

验证以下字段存在且类型正确：
- `timestamp`: `date` with `format: "epoch_millis"`
- `trace_id`, `span_id`: `keyword`
- `duration_ms`: `long`

### 3. 查询样本数据

```bash
curl -X GET "localhost:9200/nebula_metrics/_search?size=1" \
  -u elastic:your_password
```

检查返回的文档是否包含所有必需字段。

### 4. 使用 copilot 验证

在 copilot 启动后，尝试导入数据：

```bash
# 导入最近 1 小时的数据
curl -X POST "http://localhost:8080/api/import/start" \
  -d "from_date=2026-03-31T18:00:00&to_date=2026-03-31T19:00:00&limit=100"
```

查看响应：
- 成功：`{"data": {"task_id": "xxx", "status": "running"}}`
- 失败：检查错误信息中的时间戳或字段名称问题

## 调试常见问题

### 问题 1：导入失败 - "failed to parse date field"

**原因**：时间戳格式不是 epoch_millis

**解决**：
1. 检查 ES 索引映射中 `timestamp` 字段的格式
2. 确保 nebula-monitor 发送的时间戳是毫秒级
3. 如需修改，重建索引或使用 _reindex API

### 问题 2：导入成功但数据为空

**原因**：字段名称不匹配或日期范围有误

**解决**：
1. 检查 ES 中的实际字段名称（可能是 camelCase）
2. 确认日期范围内有数据（检查 timestamp 范围）
3. 检查 copilot 的 `es_client.py` 中是否支持该字段名称

### 问题 3：导入成功但 trace tree 为空

**原因**：缺少 parent_span_id 或树形结构不正确

**解决**：
1. 验证每个 span 都有正确的 `parent_span_id`（root 的父 ID 应为 null）
2. 确保没有孤立的 span（所有 parent_span_id 都指向现有的 span）
3. 检查 `trace_id` 一致性

### 问题 4：查询性能差

**原因**：缺少必要的索引

**解决**：
1. 在 `trace_id` 上建立关键字索引
2. 在 `timestamp` 上建立日期索引
3. 根据查询频率考虑在 `service_name` 上建立索引

示例：
```bash
curl -X PUT "localhost:9200/nebula_metrics/_settings" \
  -H "Content-Type: application/json" \
  -d '{
    "index.number_of_replicas": 1,
    "index.refresh_interval": "30s"
  }'
```

## 性能优化建议

### 1. 批量导入配置

```bash
# 设置合适的批量大小
EXPORTER_BATCH_SIZE=500

# 设置导入线程数
EXPORTER_WORKER_THREADS=4
```

### 2. 索引刷新策略

```bash
# 定期刷新而不是每次写入都刷新
PUT /nebula_metrics/_settings
{
  "index.refresh_interval": "30s"
}
```

### 3. 日志保留策略

```bash
# 使用 Index Lifecycle Management (ILM) 管理旧数据
PUT /_ilm/policy/nebula_metrics_policy
{
  "policy": "nebula_metrics_policy",
  "phases": {
    "hot": {
      "min_age": "0d",
      "actions": {"rollover": {"max_primary_store_size": "50GB"}}
    },
    "delete": {
      "min_age": "30d",
      "actions": {"delete": {}}
    }
  }
}
```

## 监控和告警

### 检查导出成功率

```bash
# 查询最近 1 小时的数据量
curl -X POST "localhost:9200/nebula_metrics/_search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "range": {
        "timestamp": {
          "gte": "now-1h",
          "lte": "now"
        }
      }
    },
    "size": 0
  }' | jq '.hits.total.value'
```

### 监控 copilot 导入进度

```bash
# 查询导入状态
curl -X GET "http://localhost:8080/api/import/xxx-task-id/status"
```

## 下一步

1. 在 nebula-monitor 中实现 ES 导出器
2. 按照本指南配置字段映射
3. 验证数据是否正确写入 ES
4. 在 nebula-copilot 中测试导入和同步功能
5. 监控数据质量和性能

## 参考资源

- [ES 数据格式标准](./ES_DATA_SCHEMA.md)
- [批量导入 API](./ES_IMPORT_SYNC_API.md)
- [导入/同步行为说明](./IMPORT_SYNC_BEHAVIOR.md)
- [Elasticsearch 官方文档](https://www.elastic.co/guide/en/elasticsearch/reference/current/index.html)
