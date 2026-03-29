# Nebula-Copilot CLI (MVP + 工程化增强)

Nebula-Copilot 是一个终端排障助手：
- 生成本地 mock trace 数据
- 使用 `nebula-cli analyze <trace_id>` 分析链路
- 自动输出瓶颈节点、耗时、异常分类和行动建议
- 支持 `rich/json` 双格式输出，方便机器人推送
- 支持接入真实 Elasticsearch，直接按 TraceID 查询并分析

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

参数说明：
- `--source/-s`：输入 trace JSON 文件
- `--format`：`rich` 或 `json`
- `--top-n`：输出最慢的前 N 个 span
- `--verbose`：开启调试日志

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
