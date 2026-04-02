# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 第一部分：任务工作流（每次对话开始时执行）

**重要**：每次对话开始时，必须按以下顺序执行，除非明确说明要跳过框架。

### 1. 记忆加载（Context Handoff）
```bash
cat task.json                  # 查看任务列表和 meta 信息
cat feature_list.json          # 查看待办任务（含 context_handoff）
grep -r "context_handoff" feature_list.json | head -5  # 读取未完成任务的上轮线索
```

**状态接力规则**：
- 如果任务的 `context_handoff` 有内容，必须先读取并理解
- 如果任务未完成，必须在 `context_handoff` 字段写入当前进度（≤50字）
- 严禁写入大段堆栈或完整代码

### 2. 任务领取
- 从 `feature_list.json` 中选择 `passes == false` 且 `priority` 最高的任务
- 同一优先级按 id 顺序选择
- **领取后立即更新 context_handoff** 为"已领取，正在分析..."

### 3. 开发前检查
```bash
./init.sh              # 确保环境正常
pytest -q              # 确保测试通过
```

### 4. 编程式数据处理（严禁全量读取）
处理日志和大文件时：
```bash
# ✅ 正确做法：先过滤
grep -C 5 "ERROR\|Exception" target/app.log
grep -A 20 "MemoryError" heapdump.hprof | head -50

# ❌ 错误做法：cat 全文
cat large_log_file.log
```

### 5. 完成后更新（强制门禁）
- ✅ 编写/更新测试：`pytest tests/test_xxx.py`
- ✅ 测试全部通过
- ✅ 更新 `feature_list.json`：标记 `passes: true`，更新 `context_handoff` 为空
- ✅ Git 提交：`git add . && git commit -m "feat: 完成 opt-XXX"`
- ✅ 记录到 `message_bus/ready_for_test.md`（如需 QA 验证）
- ✅ 记录到 `progress.txt`

### 6. 失败处理
- ❌ 不要强行标记 passes 为 true
- ✅ 记录失败原因到 notes 和 context_handoff
- ✅ 如发现 Bug，记录到 `message_bus/bug_reports.md`
- ✅ 继续下一个任务

---

## 第二部分：上下文工程规范（Context Engineering）

基于《Effective context engineering for AI agents》(2025-09)

### 核心原则
**上下文是有限的资源，必须像管理内存一样管理它。**

### 状态与上下文管理
- **记忆加载**：接手任务时必须读取 `context_handoff`，了解上一轮留下的线索
- **记忆保存**：未完成任务时，写入当前进度、核心结论（≤50字），禁止写入堆栈
- **精简输出**：每个会话结束时生成"状态摘要"（50字以内）

### 数据处理约束
- **严禁全量读取**：大型日志、SQL 导出、堆快照文件
- **编程式过滤**：必须先 `grep`/`awk`/`python` 过滤关键行
- **分步处理**：大文件必须分块处理，禁止一次性读入

---

## 第三部分：编程式工具调用规范（Programmatic Tool Calling）

基于《Introducing advanced tool use: Programmatic Tool Calling》(2025-11)

### 核心原则
**让模型写代码处理数据，而不是让模型"吞下"原始数据。**

### 数据与日志处理规范
| 场景 | 禁止做法 | 正确做法 |
|------|----------|----------|
| Spring Boot 日志 | `cat app.log` | `grep -C 5 "ERROR" app.log` |
| 堆内存快照 | `cat heap.hprof` | `grep -A 30 "OutOfMemoryError" heap.hprof \| head -50` |
| ES 查询结果 | 直接读取全文 | 先 `jq '.hits.hits[:10]'` 筛选 |
| 大型 JSON | `cat data.json` | `python -c "import json; ..."` 提取关键字段 |

### 强制规则
```
面对任何 >100KB 的文件时，你必须：
1. 先用 grep/awk/python 过滤出关键行
2. 只读取过滤后的结果
3. 在笔记中记录："已过滤，原始文件 xxx 行"
```

---

## 第四部分：防复合错误机制（Evals & Self-Correction）

基于《Demystifying evals for AI agents》(2026-01)

### 核心原则
**防止"它以为自己修好了，其实改坏了别的地方"。**

### 质量门禁与评估
- **无测试，不流转**：修改核心业务代码后，严禁直接标记为完成
- **强制验证**：必须编写或更新对应的单元测试
- **结果判断**：只有 `pytest` 全部通过，才允许修改 passes 状态

### TDD 强制流程
```
1. 编写/修改代码
2. 编写/更新测试用例
3. 运行: pytest tests/test_xxx.py -v
4. 失败？修复代码，直到测试通过
5. 测试通过后才能 git commit
```

### 关键模块必须测试
- 高并发逻辑（抢票、限流）
- 订单状态机
- 诊断决策逻辑
- 通知推送流程

---

## 第五部分：多智能体协同规范（Coordinated Teams）

基于《2026 Agentic Coding Trends Report》(2026)

### 角色定义

| 角色 | 配置 | 领地 | 职责 |
|------|------|------|------|
| DEV | CLAUDE_DEV.md | src/main/java, nebula_copilot/*.py | 领取需求，编写业务代码 |
| QA | CLAUDE_QA.md | src/test/java, tests/*.py | 编写测试，验证功能 |
| DOCS | CLAUDE_DOCS.md | docs/* | 文档编写 |

### 领地隔离规则
```
DEV Agent:
  - 可写: src/main/java, nebula_copilot/*.py, pom.xml
  - 只读: src/test/java

QA Agent:
  - 可写: src/test/java, tests/*.py
  - 只读: src/main/java
  - 禁止: 直接修改业务代码

消息总线: message_bus/
```

### 黑板模式（消息通信）
```bash
# DEV 完成模块后，在 ready_for_test.md 追加
echo "[$(date)] 模块: vector_store 核心类: VectorStore.py 状态: 待测试" >> message_bus/ready_for_test.md

# QA 监控此文件，发现待测试条目后执行测试
# 测试通过/失败后，更新 bug_reports.md 或 completed.md
```

### 单角色运行（默认）
如无特殊配置，默认以 DEV 角色运行，遵循本 CLAUDE.md。

---

## 第六部分：MCP 工具集成规范（2026 Agent 增强）

基于《2026 Agentic Coding Trends Report》，通过 MCP (Model Context Protocol) 赋予 Agent "眼、耳、手"。

### 已配置 MCP 服务器

| MCP | 用途 | 状态 |
|-----|------|------|
| `memory` | 长期记忆存储 | ✅ 已连接 |
| `filesystem` | Obsidian 笔记库访问 | ✅ 已连接 |
| `fetch` | HTTP 请求（接口测试/文档获取） | ⚠️ 待修复 |

### Memory MCP 使用规范
```
工具: memory_* 系列
用途:
  - 记录高频 Bug 和解决方案
  - 存储架构决策记录
  - 跨会话共享重要发现

规则:
  - 遇到疑难 Bug 时，先查询 memory 是否有记录
  - 解决后写入 memory 供后续使用
  - 格式: [时间] 类别: xxx 内容: xxx
```

### Filesystem MCP 使用规范
```
工具: filesystem_* 系列
访问路径: /Users/mac/Documents/Obsidian Vault/

用途:
  - 查阅业务规则和设计文档
  - 参考架构图和流程图
  - 查询历史决策记录

规则:
  - 遇到业务逻辑问题，先查阅 Obsidian 笔记
  - 笔记路径: /Users/mac/Documents/Obsidian Vault/{category}/xxx.md
  - 优先查阅: java基础、java面试题 等与项目相关的笔记
```

### Fetch MCP 使用规范（待修复）
```
工具: fetch_* 系列
用途:
  - 测试本地 HTTP 接口
  - 验证 Web Dashboard 功能
  - 获取在线文档

规则:
  - 开发完接口后，用 fetch 验证 localhost:8080
  - 遇到错误，用 fetch 查阅 StackOverflow
  - 请求格式: fetch(url, options)
```

### Puppeteer MCP（可选，浏览器控制）
如需开启，终端执行：
```bash
claude mcp add puppeteer -- npx -y @modelcontextprotocol/server-puppeteer
```

Puppeteer 工具集：
- `puppeteer_navigate`: 访问 URL
- `puppeteer_screenshot`: 截图保存
- `puppeteer_click`: 点击元素
- `puppeteer_fill`: 填写表单
- `puppeteer_evaluate`: 执行 JS 获取 DOM

### MCP 故障排查
```bash
# 查看 MCP 状态
claude mcp list

# 重新连接失败的 MCP
claude mcp remove <name>
claude mcp add <name> -- <command>
```

---

## 第七部分：项目概述

Nebula-Copilot 是一个面向微服务生产环境的终端排障助手，通过分析分布式链路（trace）数据自动识别瓶颈、分类错误并提供可执行建议。支持本地 JSON 文件和 Elasticsearch 双数据源，提供 CLI 和 Web Dashboard 两种交互方式。

**当前开发状态**：Phase 0-4 全部完成，生产就绪（115+ 测试用例，99% 覆盖率）

---

## 第七部分：开发命令

### 环境安装与依赖
```bash
# 安装项目
python -m pip install -e .
python -m pip install -e ".[dev]"
```

### 测试
```bash
pytest -q                    # 运行所有测试
pytest tests/test_xxx.py -v  # 运行特定模块测试
```

### CLI 常用命令
```bash
nebula-cli seed trace_mock_001 --scenario timeout
nebula-cli analyze trace_mock_001 --source data/mock_trace.json --format rich
nebula-cli monitor-es --index nebula_metrics --poll-interval-seconds 5
nebula-web --host 0.0.0.0 --port 8080
```

### Docker
```bash
docker compose up -d
NEBULA_ES_URL="http://localhost:9200" ./scripts/smoke_test.sh nebula_metrics
```

---

## 第八部分：核心架构

### 分层结构

**数据层**：repository.py、es_client.py、es_importer.py、es_sync.py

**诊断层**：analyzer.py（瓶颈识别、错误分类）、agent/graph.py（LangGraph 工作流）

**工具层**：tools/（trace_tools、analysis_tools、jvm_tools、logs_tools）

**知识层**：history_vector.py、code_whitelist.py、evaluation.py

**LLM 层**：llm/executor.py

**展示层**：cli.py（Typer CLI）、web/app.py（Flask Dashboard）、notifier.py

### 关键设计决策

1. **trace-root 节点排除**：`candidates = [s for s in spans if s.service_name != "trace-root"]`

2. **LLM 条件调用**：仅在 trace 存在错误时才调用 LLM

3. **错误分类**：Timeout / DB / Downstream / Unknown / None

4. **敏感信息脱敏**：自动 mask password/secret/token/api_key 等字段

---

## 第九部分：Git 规范

### 分支策略
- `main`：始终可部署，禁止直接提交
- `feat/*`：新功能
- `fix/*`：bug 修复
- `refactor/*`：重构
- `test/*`：测试相关

### Commit 信息格式
```
<type>: <中文描述>

类型：feat / fix / docs / style / refactor / perf / test / chore
示例：feat: 完成 opt-001 向量模型升级
```

---

## 第十部分：当前优化任务

| ID | 类型 | 描述 | 优先级 | 状态 |
|----|------|------|--------|------|
| opt-001 | backend | OpenAI Embeddings 向量模型 | 1 | pending |
| opt-002 | backend | Milvus/Weaviate 大规模存储 | 2 | pending |
| opt-003 | backend | 人工反馈循环机制 | 2 | pending |
| opt-004 | frontend | Dashboard 集成历史案例 | 3 | pending |
| opt-005 | backend | 诊断效果与告警联动 | 3 | pending |
| opt-006 | qa | monitor-es/notifier 测试覆盖 | 1 | pending |
| opt-007 | backend | 大 trace 处理优化 | 2 | pending |
| opt-008 | docs | 运维故障排查手册 | 3 | pending |

---

## 参考

- 《Effective harnesses for long-running agents》- Anthropic, 2025-11
- 《Effective context engineering for AI agents》- Anthropic, 2025-09
- 《Introducing advanced tool use: Programmatic Tool Calling》- Anthropic, 2025-11
- 《Demystifying evals for AI agents》- Anthropic, 2026-01
- 《2026 Agentic Coding Trends Report》- Anthropic, 2026
