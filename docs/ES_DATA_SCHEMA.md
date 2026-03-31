# Elasticsearch 数据格式标准

本文档定义了 Nebula-Copilot 对 Elasticsearch 中 trace 数据的格式要求。

## 概述

Trace 数据在 Elasticsearch 中以两种方式存储：
1. **扁平格式**：每个 span 是一个独立的文档
2. **树形格式**（可选）：包含嵌套的 root 对象（用于聚合查询）

## 索引配置

### 索引名称
- 推荐：`nebula_metrics`
- 可配置，通过环境变量 `NEBULA_ES_INDEX` 设置

### 索引映射（Mapping）

```json
{
  "mappings": {
    "properties": {
      // 必需字段：Trace ID
      "trace_id": {"type": "keyword"},
      "traceId": {"type": "keyword"},  // 驼峰备选
      
      // 必需字段：Span ID
      "span_id": {"type": "keyword"},
      "spanId": {"type": "keyword"},   // 驼峰备选
      
      // 必需字段：Parent Span ID
      "parent_span_id": {"type": "keyword"},
      "parentSpanId": {"type": "keyword"},  // 驼峰备选
      
      // 必需字段：服务名
      "service_name": {"type": "keyword"},
      "serviceName": {"type": "keyword"},   // 驼峰备选
      
      // 必需字段：操作名
      "operation_name": {"type": "keyword"},
      "operationName": {"type": "keyword"}, // 驼峰备选
      "methodName": {"type": "keyword"},    // 备选名称
      
      // 必需字段：执行状态
      "status": {"type": "keyword"},        // OK, ERROR, SKIPPED, TIMEOUT 等
      
      // 必需字段：耗时（毫秒）
      "duration_ms": {"type": "long"},
      "durationMs": {"type": "long"},       // 驼峰备选
      "duration": {"type": "long"},         // 备选
      
      // 必需字段：时间戳
      "timestamp": {
        "type": "date",
        "format": "epoch_millis"  // 重要：使用毫秒时间戳
      },
      "@timestamp": {
        "type": "date"            // ISO 8601 格式备选
      },
      
      // 必需字段：异常栈
      "exception_stack": {"type": "text"},
      "exceptionStack": {"type": "text"},  // 驼峰备选
      
      // 可选字段：HTTP 信息
      "httpStatus": {"type": "integer"},
      "http_status": {"type": "integer"},
      
      // 可选字段：错误分类
      "errorType": {"type": "keyword"},
      "error_type": {"type": "keyword"},
      "errorCode": {"type": "keyword"},
      "error_code": {"type": "keyword"},
      
      // 可选字段：环境信息
      "environment": {"type": "keyword"},  // dev, staging, prod
      "region": {"type": "keyword"},       // 地域标识
      "cluster": {"type": "keyword"},      // 集群标识
      "instanceId": {"type": "keyword"},
      "instance_id": {"type": "keyword"},
      "podName": {"type": "keyword"},
      "pod_name": {"type": "keyword"},
      
      // 可选字段：JVM 指标
      "heap_used_mb": {"type": "float"},
      "heap_max_mb": {"type": "float"},
      "gc_count": {"type": "integer"},
      "thread_count": {"type": "integer"},
      "cpu_usage": {"type": "float"},
      "memory_rss_mb": {"type": "float"},
      
      // 可选字段：自定义对象
      "jvm": {"type": "object", "enabled": true},
      "tags": {"type": "object", "enabled": true},
      "message": {"type": "text"},
      "log": {"type": "text"},
      "logLevel": {"type": "keyword"}
    }
  }
}
```

## 文档格式

### Span 文档（扁平结构）

每个 span 是 ES 中的一个独立文档：

```json
{
  "_id": "span_id_value",
  "_source": {
    // 唯一标识
    "trace_id": "e6d31c87797e4e9c",
    "span_id": "30c1a9e5d20f4709",
    "parent_span_id": null,
    
    // 服务和操作信息
    "service_name": "gateway-service",
    "operation_name": "HTTP GET /api/v1/order/confirm",
    
    // 执行状态
    "status": "OK",  // OK | ERROR | SKIPPED | TIMEOUT
    "duration_ms": 29,
    
    // 时间戳（重要：epoch_millis 格式）
    "timestamp": 1711900800000,  // 毫秒级时间戳
    
    // 异常信息
    "exception_stack": "com.example.Exception: ...",
    
    // 可选：HTTP 信息
    "httpStatus": 200,
    
    // 可选：错误分类
    "error_type": "Timeout",
    "error_code": "TIMEOUT_001",
    
    // 可选：环境信息
    "environment": "prod",
    "region": "us-east-1",
    "cluster": "cluster-1",
    
    // 可选：指标
    "heap_used_mb": 512.5,
    "heap_max_mb": 2048,
    "cpu_usage": 45.2
  }
}
```

### 嵌套 Root 结构（可选）

某些场景下，可包含完整的 trace tree（嵌套 root 对象）：

```json
{
  "_id": "trace_id_value",
  "_source": {
    "trace_id": "e6d31c87797e4e9c",
    "root": {
      "span_id": "30c1a9e5d20f4709",
      "parent_span_id": null,
      "service_name": "trace-root",
      "operation_name": "trace:e6d31c87797e4e9c",
      "status": "ERROR",
      "duration_ms": 1128,
      "exception_stack": null,
      "children": [
        {
          "span_id": "a099e70456564698",
          "parent_span_id": "30c1a9e5d20f4709",
          "service_name": "gateway-service",
          "operation_name": "HTTP GET /api/v1/order/confirm",
          "status": "OK",
          "duration_ms": 29,
          "children": [
            // ... 更多嵌套 children
          ]
        }
      ]
    }
  }
}
```

## 字段说明

### 必需字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `trace_id` | keyword | 分布式追踪 ID，同一调用链的唯一标识 | `e6d31c87797e4e9c` |
| `span_id` | keyword | span 的唯一标识 | `30c1a9e5d20f4709` |
| `parent_span_id` | keyword | 父 span ID，root 为 null | `a099e70456564698` |
| `service_name` | keyword | 微服务名称 | `gateway-service` |
| `operation_name` | keyword | 操作/方法名 | `HTTP GET /api/v1/order/confirm` |
| `status` | keyword | 执行状态 | `OK` \| `ERROR` \| `SKIPPED` |
| `duration_ms` | long | 执行耗时（毫秒） | `29` |
| `timestamp` | date | 时间戳（**必须是 epoch_millis**） | `1711900800000` |

### 可选字段

| 字段 | 类型 | 说明 | 用途 |
|------|------|------|------|
| `exception_stack` | text | 异常堆栈跟踪 | 错误诊断 |
| `httpStatus` | integer | HTTP 状态码 | HTTP 操作诊断 |
| `error_type` | keyword | 错误类型分类 | 模式识别 |
| `environment` | keyword | 部署环境 | 环境隔离查询 |
| `region` | keyword | 地域信息 | 地域分析 |
| `cluster` | keyword | 集群标识 | 集群隔离 |
| `instanceId` | keyword | 实例 ID | 实例跟踪 |
| `heap_used_mb` | float | JVM 堆内存使用 | 性能分析 |
| `cpu_usage` | float | CPU 使用率 | 资源分析 |

## 重要约束

### 1. 时间戳格式

**❌ 错误（ISO 8601）：**
```
"timestamp": "2026-03-31T19:49:00"
```

**✅ 正确（epoch_millis）：**
```
"timestamp": 1711900800000
```

原因：ES 索引映射中 `timestamp` 字段的格式为 `epoch_millis`，必须发送毫秒级时间戳。

### 2. ID 字段

- `trace_id`、`span_id`、`parent_span_id` 必须是非空字符串
- 推荐使用 UUID 或 16 进制字符串
- 不支持数字 ID

### 3. status 字段

标准取值：
- `OK` - 成功执行
- `ERROR` - 执行错误
- `SKIPPED` - 跳过执行（熔断、限流等）
- `TIMEOUT` - 超时
- `RETRY` - 重试中

### 4. service_name 和 operation_name

- `service_name` 用于服务级别分析（聚合）
- `operation_name` 用于调用级别诊断（细节）
- 推荐格式：
  - RPC: `RPC methodName`
  - HTTP: `HTTP METHOD /path`
  - Database: `SQL SELECT ...`

### 5. 树形关系

两种方式都支持，但需要一致：

方式 A（推荐）：扁平 + parent_span_id 关系
```json
[
  {"span_id": "A", "parent_span_id": null},     // root
  {"span_id": "B", "parent_span_id": "A"},      // A 的子节点
  {"span_id": "C", "parent_span_id": "B"}       // B 的子节点
]
```

方式 B（可选）：嵌套树形
```json
{
  "root": {
    "span_id": "A",
    "children": [
      {
        "span_id": "B",
        "children": [
          {"span_id": "C", "children": []}
        ]
      }
    ]
  }
}
```

## 字段名称约定

Nebula-Copilot 支持多种字段名称格式（适配不同来源）：

| 概念 | snake_case | camelCase | 说明 |
|------|-----------|-----------|------|
| Trace ID | trace_id | traceId | 均支持，优先级相同 |
| Span ID | span_id | spanId | 均支持，优先级相同 |
| Parent | parent_span_id | parentSpanId | 均支持 |
| Service | service_name | serviceName | 均支持 |
| Operation | operation_name | operationName, methodName | 均支持 |
| Duration | duration_ms | durationMs, duration | 均支持 |
| Exception | exception_stack | exceptionStack | 均支持 |

优先级规则：
1. snake_case（示例中的标准形式）
2. camelCase（驼峰备选）

## 查询时间范围注意事项

当查询 timestamp 范围时，必须使用 epoch_millis 格式：

```python
# ✅ 正确
from datetime import datetime

to_date = datetime.now()
from_date = to_date - timedelta(hours=24)

from_ts = int(from_date.timestamp() * 1000)  # 转为毫秒
to_ts = int(to_date.timestamp() * 1000)

query = {
    "range": {
        "timestamp": {
            "gte": from_ts,
            "lte": to_ts
        }
    }
}
```

## 验证和对齐清单

当 nebula-monitor 集成时，使用此清单确保数据对齐：

- [ ] 所有 span 文档都包含必需的 5 个字段（trace_id, span_id, service_name, operation_name, status）
- [ ] duration_ms 字段为毫秒级整数
- [ ] timestamp 字段使用 epoch_millis 格式（毫秒时间戳）
- [ ] status 字段使用标准取值（OK, ERROR, SKIPPED, TIMEOUT）
- [ ] parent_span_id 在 root span 中为 null
- [ ] exception_stack 只在异常情况下填充
- [ ] 所有 ID 字段（trace_id, span_id 等）非空
- [ ] 索引映射包含所有必需字段

## 常见问题

### Q: 支持自定义字段吗？
A: 是的。通过 `tags` 对象或其他自定义字段都支持。但必需字段不能修改。

### Q: 支持嵌套结构吗？
A: 支持。`jvm`、`tags` 等字段可以是对象类型。但主 span 字段必须是扁平的。

### Q: 时间戳格式能用 ISO 8601 吗？
A: 不推荐。虽然 ES 支持多种日期格式，但 copilot 查询时使用 epoch_millis，为避免问题建议统一使用毫秒时间戳。

### Q: 如何升级已存在的索引？
A: 使用 ES 的 `_reindex` API 或重建索引。参考：[ES 重索引指南](https://www.elastic.co/guide/en/elasticsearch/reference/current/docs-reindex.html)

## 参考

- Elasticsearch 日期类型：https://www.elastic.co/guide/en/elasticsearch/reference/current/date.html
- OpenTelemetry Trace 规范：https://opentelemetry.io/docs/reference/specification/trace/api/
- Jaeger Trace 格式：https://www.jaegertracing.io/docs/architecture/#span-references
