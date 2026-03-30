# Nebula-Copilot CLI (MVP + 工程化增强)

Nebula-Copilot 是一个终端排障助手：
- 生成本地 mock trace 数据
- 使用 `nebula-cli analyze <trace_id>` 分析链路
- 自动输出瓶颈节点、耗时、异常分类和行动建议
- 支持 `rich/json` 双格式输出，方便机器人推送
- 支持接入真实 Elasticsearch，直接按 TraceID 查询并分析

## 业务价值与适用场景

面向微服务生产环境的值班与故障应急场景，目标是把“发现慢链路/报错”到“定位责任服务与处置建议”的时间压缩到分钟级。

典型业务痛点：
- 链路跨多个服务，人工排查成本高
- 报警只告诉你“慢了/错了”，但不给可执行处置建议
- 通知系统易抖动，重复告警或推送失败影响值班效率

Nebula-Copilot 的业务收益：
- 快速定位：自动给出瓶颈节点、异常类型、关键证据
- 降本增效：支持规则 + LLM 双路径，LLM 不可用时可降级
- 闭环落地：支持 Web 可观测面板、告警推送与运行记录追踪

- 演示
  <img width="1640" height="895" alt="image" src="https://github.com/user-attachments/assets/fe52b31c-bfcd-48f3-96ae-194e08d43f1c" />


## 技术架构概览

系统采用“数据采集 -> 诊断编排 -> 通知闭环 -> 可视化展示”的分层结构：

1. 数据层
- Elasticsearch 作为 trace/log/jvm 指标来源
- 支持本地 JSON 与 ES 双数据源（便于本地调试 + 线上接入）

2. 诊断层
- `analyzer` 负责瓶颈识别、错误分类、摘要生成
- `agent graph` 负责调用编排（trace/jvm/logs）与重试回退
- 支持 LLM 增强决策，并提供强制/非强制决策模式

3. 可靠性层
- 运行去重、限流、通知重试
- 将系统侧失败与链路侧失败区分为 `degraded`/`failed`

4. 展示层
- Typer CLI（rich/json）
- Web Dashboard（Runs/Run Detail/Trace Tree/Topology/Logs）

## 目录结构

- `nebula_copilot/models.py`：Pydantic Trace 数据模型
- `nebula_copilot/mock_data.py`：Mock 数据生成与加载（timeout/db/downstream）
- `nebula_copilot/analyzer.py`：Trace 遍历、瓶颈诊断、错误分级、摘要模板
- `nebula_copilot/cli.py`：Typer CLI 与 Rich 终端展示
- `nebula_copilot/tooling.py`：Phase 2 Tool Calling 接口预埋 + POC
- `nebula_copilot/report_schema.py`：统一报告 Schema（便于飞书/钉钉）
- `tests/`：单元测试（算法 + CLI）
- `data/mock_trace.json`：本地模拟数据文件（运行后自动生成）

## 安装

```bash
/Users/mac/Documents/python/venv/bin/python -m pip install -e .
/Users/mac/Documents/python/venv/bin/python -m pip install -e ".[dev]"
```

## CLI 用法

### 1) 生成样例数据

```bash
nebula-cli seed trace_mock_2026_0001 --scenario timeout
nebula-cli seed trace_mock_db_0001 --scenario db --output data/mock_db.json
nebula-cli seed trace_mock_ds_0001 --scenario downstream --output data/mock_ds.json
```

### 2) 分析本地链路

```bash
nebula-cli analyze trace_mock_2026_0001 --source data/mock_trace.json --top-n 3 --format rich
nebula-cli analyze trace_mock_2026_0001 --source data/mock_trace.json --top-n 3 --format json
```

### 3) 查询最近 TraceID（高频排障入口）

```bash
nebula-cli list-traces --index nebula_metrics --last-minutes 30 --limit 20 --format rich
nebula-cli list-traces --index nebula_metrics --last-minutes 30 --limit 20 --format json
```

### 4) 接入真实 ES 查询

先配置环境变量（推荐）：

```bash
export NEBULA_ES_URL="https://your-es-host:9200"
export NEBULA_ES_USERNAME="your_user"
export NEBULA_ES_PASSWORD="your_password"
```

然后按 TraceID 直接查询并分析：

```bash
nebula-cli analyze-es 1f9b2f0d9a6a --index "nebula-trace-*" --top-n 5 --format rich
nebula-cli analyze-es 1f9b2f0d9a6a --index "nebula-trace-*" --format json
```

支持自动推送摘要到飞书/钉钉 webhook：

```bash
nebula-cli analyze-es 1f9b2f0d9a6a --index "nebula-trace-*" --push-webhook "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
```

可选参数：
- `--es-url`：ES 地址（默认读 `NEBULA_ES_URL`）
- `--username` / `--password`：认证信息（默认读环境变量）
- `--verify-certs/--no-verify-certs`：是否校验 TLS 证书
- `--timeout-seconds`：查询超时秒数
- `--push-webhook`：将排障摘要推送到群机器人

### 5) 自动监控 ES 慢链路并触发诊断

`monitor-es` 会按固定间隔轮询 ES，发现慢链路后自动触发诊断并推送飞书/钉钉：

```bash
nebula-cli monitor-es \
  --index nebula_metrics \
  --poll-interval-seconds 5 \
  --slow-threshold-ms 1000 \
  --last-minutes 5 \
  --limit 20 \
  --push-webhook "https://open.feishu.cn/open-apis/bot/v2/hook/xxx" \
  --llm-enabled
```

关键参数：
- `--poll-interval-seconds`：轮询间隔（默认 5 秒）
- `--slow-threshold-ms`：慢链路阈值（默认 1000ms）
- `--trigger-dedupe-seconds`：同一 trace 触发去重窗口
- `--max-iterations`：最大轮询次数（0 表示持续运行）

状态说明（重要）：
- `failed`：链路诊断存在真实 `ERROR` 节点（链路级失败）
- `degraded`：系统侧失败（例如 LLM 429/通知失败）或降级回退，但链路本身未出现 `ERROR`
- `ok`：诊断与通知均正常

### 6) 向 ES 批量写入高仿真链路监控数据（正常/慢链路/报错）

脚本：`scripts/load_simulated_es_data.py`

示例：

```bash
python scripts/load_simulated_es_data.py \
  --es-url http://localhost:9200 \
  --index nebula_metrics \
  --traces 2000 \
  --time-window-minutes 120 \
  --normal-ratio 0.72 \
  --slow-ratio 0.22 \
  --error-ratio 0.06 \
  --create-index \
  --refresh wait_for
```

特点：
- 生成完整分布式调用链（gateway -> user -> cart -> inventory -> pricing -> order -> payment）
- 同时写入 `traceId/trace_id`、`spanId/span_id` 等兼容字段
- 覆盖真实字段：`@timestamp`、`httpStatus`、`instanceId`、`podName`、`errorType`、`exceptionStack`、`tags`
- 可控制正常/慢链路/报错比例，适合压测和自动诊断联调
- 报错链路符合真实传播：上游节点 `ERROR` 后，下游同步调用会标记为 `SKIPPED`（`UpstreamFailure`）

参数说明：
- `--source/-s`：输入 trace JSON 文件
- `--format`：`rich` 或 `json`
- `--top-n`：输出最慢的前 N 个 span
- `--verbose`：开启调试日志

### 7) 启动前端可观测排障台（Web）

安装依赖后可直接启动：

```bash
nebula-web --host 0.0.0.0 --port 8080
```

打开浏览器访问：

```text
http://127.0.0.1:8080/dashboard
```

核心接口：
- `GET /api/overview`：总体 KPI 与最近异常
- `GET /api/runs`：运行记录列表（支持状态/trace/sort 过滤）
- `GET /api/runs/<run_id>/page`：单次运行详情
- `GET /api/traces/<trace_id>/inspect`：trace 树 + 诊断结果
- `GET /api/logs/search`：按 trace/span 反查服务日志

页面已支持显式数据来源标签（`source=local|es`）：
- KPI 区块：显示 `/api/overview` 的来源
- Runs 区块：显示 `/api/runs` 的来源
- Run Detail 区块：显示 `/api/runs/<run_id>/page` 的来源
- Trace Inspect 区块：显示 `/api/traces/<trace_id>/inspect` 的来源
- Logs 区块：显示 `/api/logs/search` 的来源

页面交互增强：
- Runs 区块使用可展开下拉模块（Accordion），展开即联动加载 Run Detail
- Run Detail 新增 “LLM 分析结果” 面板，展示摘要与 LLM 事件（如 `llm_decision`）
- Trace Tree 自动折叠重复 `trace-root` 层级，减少噪音
- Topology 自动过滤 synthetic root，仅展示真实服务节点
- Topology 节点按状态着色：`ERROR=红`、`SKIPPED=橙`、`OK=蓝`

### 8) 一键闭环演示（真实 ES）

脚本：`scripts/e2e_web_closure_demo.sh`

功能：
- 触发 `monitor-es` 单轮扫描（真实 ES）
- 刷新 `data/agent_runs.json`
- 输出最新 `run_id/trace_id`
- 调用 Web `trace inspect` 接口并打印可验证结果（`ok/source/error`）

示例：

```bash
bash scripts/e2e_web_closure_demo.sh nebula_metrics
```

可选环境变量：
- `LAST_MINUTES`（默认 10080）
- `LIMIT`（默认 5）
- `SLOW_THRESHOLD_MS`（默认 1）
- `RUNS_PATH`（默认 `data/agent_runs.json`）
- `WEB_BASE_URL`（默认 `http://127.0.0.1:8080`）

## 错误分级规则

- `Timeout`：异常栈含 `timeout` / `timed out`
- `DB`：异常栈含 `deadlock` / `lock wait` / `sql`
- `Downstream`：异常栈含 `503` / `connection refused` / `downstream`
- `Unknown`：状态为 ERROR 但不满足以上

## 排障摘要模板（可贴群）

命令 `analyze --format rich` 会自动输出：
- 瓶颈服务
- 耗时
- 异常类型
- 异常摘要
- 建议动作（先查哪个服务和日志关键词）

## 测试

```bash
/Users/mac/Documents/python/venv/bin/python -m pytest -q
```

## 发布前冒烟检查

本地执行：

```bash
NEBULA_ES_URL="http://localhost:9200" ./scripts/smoke_test.sh nebula_metrics
```

CI 自动执行（GitHub Actions）：
- 工作流文件：`.github/workflows/ci-smoke.yml`
- 触发方式：`push` / `pull_request` / `workflow_dispatch`
- 流程：拉起 ES 8.10.2 -> 写入示例 trace -> 执行 `smoke_test.sh`

该脚本会依次检查：
- venv 是否可用
- ES 连通性（ping）
- 最近 traceId 是否可查询
- `analyze-es` 是否成功并返回 0 退出码

## 部署与运维

- 部署手册：`docs/DEPLOYMENT.md`
- 运维手册：`docs/OPERATIONS.md`

PR 自动合并脚本：
- 按 PR 号执行：`bash scripts/merge_pr.sh <pr_number>`
- 按当前分支自动定位 PR：`bash scripts/merge_current_pr.sh`

支持方式：
- Docker：`Dockerfile` + `docker-compose.yml`
- Kubernetes：`deploy/k8s/deployment.yaml` + `deploy/k8s/secret.example.yaml`

## Phase 2 预埋（Tool Calling / Agent）

已提供：
- `query_trace`
- `query_jvm`
- `query_logs`
- `tool_get_trace`
- `tool_analyze_trace`
- `tool_get_jvm_metrics`
- `tool_search_logs`

以及 `run_agent_poc` 演示链路：
`trace_id -> query_trace -> query_jvm -> query_logs -> agent_report`

后续接入线上 ES/JVM/日志系统时，只需替换 `ToolRegistry` 的具体实现。

另外新增了 `nebula_copilot/repository.py`：
- `TraceRepository`（协议）
- `LocalJsonRepository`（当前实现）
- `ESRepository` / `HTTPRepository`（占位实现）

可以在不改 CLI 主流程的前提下替换数据源。

## 下一阶段 TODO（Phase 2 / 3）

- Phase 2
  - 将 `LocalJsonRepository` 替换为 `ESRepository` 真实现
  - 统一 Tool 返回 JSON schema（trace/jvm/logs）
  - 接入大模型进行“工具调用编排 + 结论生成”
- Phase 3
  - 引入故障知识库（Runbook/SOP/复盘）
  - 增加相似案例检索与引用
  - 输出建议中附“参考案例”来源
