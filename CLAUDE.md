# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Nebula-Copilot 是一个面向微服务生产环境的终端排障助手，通过分析分布式链路（trace）数据自动识别瓶颈、分类错误并提供可执行建议。支持本地 JSON 文件和 Elasticsearch 双数据源，提供 CLI 和 Web Dashboard 两种交互方式。

**当前开发状态**：Phase 0-4 全部完成，生产就绪（115+ 测试用例，99% 覆盖率）

## 开发命令

### 环境安装与依赖
```bash
# 安装项目（首次安装或更新后必须执行以注册 entry point）
python -m pip install -e .

# 安装开发依赖（包含 pytest）
python -m pip install -e ".[dev]"
```

### 本地开发与测试
```bash
# 运行所有测试
pytest -q

# 运行特定模块测试
pytest tests/test_analyzer.py
pytest tests/test_agent_graph.py

# 启动 Web Dashboard（推荐）
nebula-web --host 0.0.0.0 --port 8080
# 访问 http://127.0.0.1:8080/dashboard

# 启动调试模式
nebula-web --host 0.0.0.0 --port 8080 --debug
```

### CLI 常用命令
```bash
# 生成 mock 数据
nebula-cli seed trace_mock_001 --scenario timeout

# 分析本地 trace
nebula-cli analyze trace_mock_001 --source data/mock_trace.json --format rich

# 查询最近 TraceID（需配置 ES）
nebula-cli list-traces --index nebula_metrics --last-minutes 30 --limit 20

# 分析 ES 中的 trace
nebula-cli analyze-es 1f9b2f0d9a6a --index "nebula-trace-*" --format rich

# 监控 ES 慢链路
nebula-cli monitor-es --index nebula_metrics --poll-interval-seconds 5 --slow-threshold-ms 1000
```

### Docker 部署
```bash
# 构建并启动
docker compose up -d

# 冒烟测试（需先启动 ES 8.10.2）
NEBULA_ES_URL="http://localhost:9200" ./scripts/smoke_test.sh nebula_metrics
```

### 向量索引构建（Phase 2-4）
```bash
# 构建历史诊断向量索引
python scripts/build_history_index.py --runs-path data/agent_runs.json --collection nebula_diagnosis_history --validate
```

## 核心架构

### 分层结构

**1. 数据层**
- `repository.py`：数据源抽象（`TraceRepository` Protocol）
  - `LocalJsonRepository`：本地 JSON 文件
  - `ESRepository`：Elasticsearch
  - `HTTPRepository`：HTTP 仓库（预留）
- `es_client.py`：ES 查询客户端（trace、logs、jvm_metrics）
- `es_importer.py`：ES 批量数据导入工具
- `es_sync.py`：ES 数据同步（导入/同步双模式）

**2. 诊断层**
- `analyzer.py`：核心诊断引擎
  - `analyze_trace()`：瓶颈识别（排除合成的 trace-root）
  - `classify_error()`：错误分类（Timeout/DB/Downstream/Unknown/None）
  - `build_alert_summary()`：生成排障摘要模板
- `agent/graph.py`：LangGraph 工作流编排
  - 节点：`get_trace` → `analyze` → `route` → `enrich_jvm/logs` → `report` → `notify`
  - 路由逻辑：按 `error_type` 分流（dual/jvm/logs）
  - 重试机制：内置 2 次重试 + 指数退避
- `agent/state.py`：`AgentState` 状态管理（含事件历史记录）

**3. 工具层（tools/）**
- `trace_tools.py`：trace 数据获取
- `analysis_tools.py`：trace 分析工具
- `jvm_tools.py`：JVM 指标查询
- `logs_tools.py`：日志搜索
- `types.py`：工具注册表 `ToolRegistry` 与统一响应格式

**4. 知识与向量检索层（Phase 2-4）**
- `knowledge_base.py`：故障模式知识库（FaultPattern + KnowledgeInsight）
- `vector_store.py`：向量存储抽象（支持 local/Milvus/Weaviate）
- `history_vector.py`：历史诊断向量库（HistoryVectorStore）
- `code_whitelist.py`：源码白名单检索（CodeWhitelistStore）
- `evaluation.py`：评估与数据治理（MetricsCollector + 敏感数据脱敏）

**5. LLM 集成层**
- `llm/executor.py`：LLM 执行器（`LLMExecutor`）
  - `diagnose_incident()`：结构化根因决策
  - `suggest_action()`：行动建议生成
  - `polish_summary()`：报告润色
- `config.py`：配置管理（LLM、向量、运行保护）

**6. 展示与通知层**
- `cli.py`：Typer CLI（rich/json 双格式输出）
- `web/app.py`：Flask Web Dashboard
  - `/api/overview`：KPI 概览
  - `/api/runs`：运行记录列表
  - `/api/runs/<run_id>/page`：单次运行详情
  - `/api/traces/<trace_id>/inspect`：trace 树 + 诊断
  - `/api/logs/search`：日志搜索
- `notifier.py`：飞书/钉钉推送（带重试与降级）

### 核心数据模型（models.py）
```python
class Span:          # span 节点（含 children 树形结构）
class TraceDocument:  # trace 文档（trace_id + root Span）
```

### 状态归一化规则（CLI/Agent）
- `failed`：链路存在真实 ERROR 节点（链路级失败）
- `degraded`：系统侧失败（LLM 429/通知失败）或降级回退，但链路本身无 ERROR
- `ok`：诊断与通知均正常

### 环境变量配置（.env 或环境变量）
```bash
# Elasticsearch
NEBULA_ES_URL                    # ES 地址
NEBULA_ES_USERNAME               # ES 用户名
NEBULA_ES_PASSWORD               # ES 密码

# LLM 配置
LLM_ENABLED=true                 # 启用 LLM
LLM_PROVIDER=github              # LLM 提供商
LLM_MODEL=gpt-4.1-mini           # 模型名称
GH_MODELS_API_KEY=xxx            # GitHub Models API Key
LLM_BASE_URL=https://models.inference.ai.azure.com
LLM_TIMEOUT_MS=8000
LLM_MAX_RETRY=2
LLM_REPORT_POLISH_ENABLED=true   # 启用报告润色

# 向量检索
VECTOR_ENABLED=true              # 启用向量检索
VECTOR_PROVIDER=local            # local/milvus/weaviate
VECTOR_TOP_K=3
VECTOR_MIN_SCORE=0.5
VECTOR_COLLECTION=nebula_kb_patterns
VECTOR_PERSIST_DIR=./data/vector

# 运行保护
RUN_DEDUPE_WINDOW_SECONDS=300    # 去重窗口
RUN_RATE_LIMIT_PER_MINUTE=0      # 速率限制（0 = 无限制）
METRICS_ENABLED=true             # 启用指标收集
```

## 重要设计决策

### 1. trace-root 节点排除
ES 按 span 文档拼接 trace 时会生成合成根节点 `trace-root`，诊断时必须排除以避免误判瓶颈：
```python
candidates = [s for s in spans if s.service_name != "trace-root"]
```

### 2. LLM 条件调用策略
仅在 trace 存在错误时才调用 LLM，正常链路使用规则逻辑，降低成本：
```python
has_error = _has_error_in_trace(trace_doc.root)
executor_for_analysis = llm_executor if has_error else None
```

### 3. 错误分类规则（analyzer.py）
- `Timeout`：异常栈含 `timeout` / `timed out`
- `DB`：异常栈含 `deadlock` / `lock wait` / `sql`
- `Downstream`：异常栈含 `503` / `connection refused` / `downstream`
- `Unknown`：状态为 ERROR 但不满足以上
- `None`：无错误

### 4. Tool 统一响应格式
```python
{
    "status": "ok" | "failed" | "no_data",
    "tool": "tool_name",
    "target": "查询目标",
    "payload": {...},
    "error": "错误信息（如有）"
}
```

### 5. 敏感信息脱敏（web/app.py）
自动 mask 包含 `password/secret/token/api_key/authorization/webhook/cookie` 等字段的响应数据。

### 6. 去重与限流
- `runtime_guard.py`：运行保护（去重 + 限流）
- `RUN_DEDUPE_WINDOW_SECONDS`：同一 trace 去重窗口
- `RUN_RATE_LIMIT_PER_MINUTE`：每分钟最大运行次数（0 = 无限制）

### 7. Agent 长时间运行框架（基于 Anthropic《Effective harnesses for long-running agents》）

**设计来源**：Anthropic 2025-11-26 工程博客，描述跨越多个上下文窗口的 Agent 编排框架。

**三层架构**：
- `agent/session.py` — Session 生命周期管理（创建/恢复/checkpoint/结束）
- `agent/harness.py` — Agent 编排层（会话启动协议 + 增量执行）
- `agent/graph.py` — 核心诊断节点（保持原有逻辑）

**关键概念**：
- `DiagnosticSession`：一个诊断会话 = Agent 的一个上下文窗口
- `DiagnosticManifest`：任务清单（对应 Anthropic 的 feature_list.json），所有任务初始为 `pending`，只通过修改 `passes` 字段标记完成
- `SessionManager`：会话管理器，支持中途失败恢复（从 checkpoint 恢复）
- `build_session_harness()`：会话启动协议（ES 健康检查 → 恢复或创建会话 → 读取 manifest → 获取上下文）
- `run_diagnostic_session()`：完整多步骤诊断，支持 checkpoint 续恢复
- `run_incremental_step()`：每次只执行一个诊断步骤（对应 Anthropic 的 feature-by-feature）

**会话目录结构**：
```
data/agent_sessions/{trace_id}/
  manifest.json         # 任务清单（passes 字段追踪）
  checkpoint.json       # 最近 checkpoint（AgentState 快照）
  session_summary.txt  # 会话进度摘要（Anthropic 的 claude-progress.txt）
```

**Anthropic 风格原则**：
1. 已完成任务不可删除，只修改 `passes` 字段（防止数据篡改）
2. 每个会话结束写 `clean_state_summary`，下会话从干净状态开始
3. `get_next_task()` 每次只返回一个 pending 任务，强制增量执行
4. ES 健康检查门卫：会话开始前验证环境可用性

## 代码规范

### 语言与输出
- **所有思考过程、解释、回复、文档、日志描述必须使用简体中文**
- 代码中的变量名、函数名、类名保持英文（符合编程规范）
- 代码注释、docstring、提交信息、PR 描述全部使用中文

### Git 提交信息格式
```
<type>: <中文描述>

类型示例：
- feat：新功能
- fix：修复 bug
- docs：文档更新
- style：代码格式
- refactor：重构
- perf：性能优化
- test：测试
- chore：构建/工具

示例：
feat: 增加最近 traceId 查询命令
fix: 修复 CI 冒烟脚本的 Python 路径兼容问题
```

### 分支策略
- `main`：始终保持可部署状态，禁止直接提交
- `feat/*`：新功能
- `fix/*`：bug 修复
- `refactor/*`：重构
- `test/*`：测试相关

### 操作规范
- **执行终端命令**：无需请示，直接执行
- **修改文件**：无需请示，直接执行
- **合并分支到 main**：需要向用户请示并获得确认

### 诊断与决策原则
- **任何结论必须基于真实证据**（日志、trace、数据、运行结果等）
- 禁止"看起来像""大概"等主观判断
- 代码修改后必须执行最小回归测试并提供可复核的真实执行证据

### 前端数据规范（可用性优先原则）
- **所有前端展示数据必须来自真实 API**，禁止硬编码演示数据（时间轴、数值、占位符等）
- Web Dashboard 的 KPI 指标、图表、列表数据**必须实时从 ES 查询**，不得使用本地 JSON 文件或内存硬编码
- `initECharts()` 只负责初始化图表实例，图表数据必须由 `fetchDashboardOverview()` 从 `/api/overview` 获取真实时间序列后通过 `updateCharts()` 渲染
- 若 ES 无数据，UI 应显示明确错误信息（如"无法获取指标数据，请确认 ES 连接正常"），而非展示假数据
- 刷新按钮必须触发真实 ES 重新查询；导入按钮必须执行真实的 ES→本地批量导入

## 测试策略

### 测试文件组织
- `tests/test_analyzer.py`：诊断引擎测试
- `tests/test_agent_graph.py`：LangGraph 工作流测试
- `tests/test_history_vector.py`：历史向量库测试
- `tests/test_code_whitelist.py`：源码白名单测试
- `tests/test_evaluation.py`：评估与治理测试
- `tests/test_cli.py`：CLI 命令测试
- `tests/test_web.py`：Web API 测试

### 冒烟测试
- CI 自动执行：`.github/workflows/ci-smoke.yml`
- 本地执行：`NEBULA_ES_URL="http://localhost:9200" ./scripts/smoke_test.sh nebula_metrics`

## 自我循环运行框架（长时间智能体）

基于 Anthropic《Effective harnesses for long-running agents》(2025-11-26) 设计。

### 核心文件

| 文件 | 用途 |
|------|------|
| task.json | 任务清单（AI 领取任务用） |
| progress.txt | 工作日志（记录完成/失败） |
| feature_list.json | 特性列表（所有待办功能，passes 严格控制） |
| init.sh | 环境初始化脚本 |
| run-agent.sh | 自我循环运行脚本 |

### 增量开发原则

| 问题 | 方案 |
|------|------|
| 智能体一次性做太多 | Feature list + **每次只做一个** |
| 智能体过早宣布完成 | Feature list 详细描述，**passes 严格控制** |
| 环境破坏无法恢复 | **每次 git 提交** + progress 记录 |
| 测试不完整 | **端到端自动化测试** |

### Feature List 条目格式

```json
{
  "id": "opt-001",
  "category": "vector",
  "description": "功能描述",
  "steps": ["步骤1", "步骤2"],
  "passes": false,
  "priority": 1,
  "notes": ""
}
```

**重要**：
- 只改 `passes` 字段（false → true）
- 不删除或修改 steps
- 添加 notes 记录遇到的问题

### 运行流程

```bash
# 初始化
./init.sh

# 启动自我循环（人不在时自动跑）
./run-agent.sh
```

### 当前优化任务

| ID | 描述 | 优先级 |
|----|------|--------|
| opt-001 | 接入 OpenAI Embeddings | 1 |
| opt-002 | Milvus/Weaviate 大规模存储 | 2 |
| opt-003 | 人工反馈循环机制 | 2 |
| opt-004 | Dashboard 集成历史案例 | 3 |
| opt-005 | 诊断效果与告警联动 | 3 |
| opt-006 | monitor-es/notifier 测试覆盖 | 1 |
| opt-007 | 大 trace 处理优化 | 2 |
| opt-008 | 运维故障排查手册 | 3 |

---

## 扩展开发

### 添加新的 Tool
1. 在 `tools/` 下创建新模块（如 `xxx_tools.py`）
2. 实现工具函数，返回统一响应格式
3. 在 `tools/types.py` 的 `ToolRegistry` 中注册
4. 在 `agent/graph.py` 的对应节点中调用

### 添加新的故障模式
1. 在 `knowledge_base.py` 的 `KnowledgeBase.__init__()` 中添加 `FaultPattern`
2. 定义 `signals`、`related_metric_checks`、`linkage_suggestion`
3. 运行测试验证模式匹配

### 添加新的向量存储后端
1. 在 `vector_store.py` 的 `VectorStore` 基类中实现新方法
2. 在 `build_vector_store()` 工厂函数中添加 `provider` 分支
3. 更新配置文档和测试用例

## 文档资源

- 部署手册：`docs/DEPLOYMENT.md`
- 运维手册：`docs/OPERATIONS.md`
- ES 数据格式标准：`docs/ES_DATA_SCHEMA.md`
- Nebula-Monitor 集成：`docs/MONITOR_INTEGRATION_GUIDE.md`
- Web Dashboard 功能：`docs/WEB_DASHBOARD_FEATURES.md`
- 项目规划：`docs/PROJECT_PLAN.md`
