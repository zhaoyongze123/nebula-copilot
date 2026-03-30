# 每次执行前先看这篇rules

> 给 Copilot 的执行规则：每次开始任务前必须先阅读本文件，并严格按本文档执行。

# 项目协作规则rules

这些规则用于本仓库的日常协作与变更执行。

## 1. 诊断与结论必须证据驱动
- 不接受“看起来像”的结论，必须明确区分：数据问题、查询问题、分析逻辑问题。
- 输出结论时必须包含证据来源（trace/JVM/logs）与关键字段，避免纯主观判断。
- 若证据不足，直接写明“不足原因”和补数方案，不得伪装成已定位。

## 2. 优先真实链路，禁止仅用mock自证
- 涉及 `monitor-es` 与 `agent-analyze` 的能力，优先走真实 ES 查询并纳入最终结论。
- mock 数据仅用于补齐回归与复现场景，不能替代真实链路验收。
- 联调前需确认字段覆盖（trace、错误日志、JVM指标、时间窗）齐全。

## 3. 变更必须端到端闭环
- 代码改完必须执行：最小回归测试 + 目标命令实跑 + 运行记录核验。
- 只说“已修复”不算完成，必须给出可复核结果（状态、次数、关键输出）。
- 通知链路改动必须带一次真实 webhook 实测（含去重/重试语义）。

## 4. 告警内容要求“可行动且不重复”
- 禁止硬编码单一建议文案；建议必须来自当前故障上下文（规则或LLM结果）。
- 输出应包含：问题类型、根因解释、建议动作、置信度（若有）。
- 若不同问题链路触发，应生成差异化结论，不允许同质化模板刷屏。

## 5. LLM参与要求生产语义
- LLM开启时需明确是否为严格模式：
  - 非严格：允许回退，但必须记录回退原因。
  - 严格：LLM失败即任务失败，禁止“静默降级后仍报成功”。
- LLM调用异常要保留可排障信息（依赖缺失、配置错误、请求失败、解析失败）。

## 6. 过程透明与可追溯
- 关键节点写入运行历史（history events），包含状态与摘要。
- 对外汇报按“事实结果 -> 关键证据 -> 未完成项/风险”顺序。
- 参数或命令错误应直接承认并立即给出修正重跑结果。

## 7. 交付纪律
- 重大改动前先说明计划，执行中持续同步进展。
- 用户要求 push/main、联调、重建数据时，按要求直接执行并反馈结果。
- 若目标是“生产可用”，验收标准以真实环境结果为准，不以本地推断替代。

## 8. 项目开发与版本控制规范
项目开发与版本控制规范 (Development & Version Control Guidelines)
请严格遵守以下分支策略和提交信息规范，以确保项目历史的可读性与协作效率。
1. 分支管理策略 (Branching Strategy)
我们采用简化版的 Git Flow 策略：
main 分支：始终保持可部署状态。不允许直接在 main 上开发，必须通过 Pull Request 合并。
feature/* 分支：用于开发新功能。例如 feature/llm-analysis。
fix/* 分支：用于修复线上 Bug。例如 fix/llm-fallback-error。
refactor/* 分支：用于代码重构或性能优化。
test/* 分支：专门用于编写或优化测试用例。
2. 提交信息规范 (Commit Message Format)
所有 Commit Message 必须使用中文，并遵循以下格式：
格式： <type>(<scope>): <subject>
Type 类型：
feat: 新增功能（新特性、新逻辑）。
fix: 修补 Bug（解决运行错误、逻辑异常）。
docs: 文档变更（README、注释修改）。
style: 格式调整（不影响代码逻辑的空格、缩进等）。
refactor: 重构（既不是新增功能也不是修复 Bug 的代码变动）。
perf: 性能提升（优化响应速度、内存占用）。
test: 添加或修改测试代码（包括集成测试、烟雾测试）。
chore: 构建过程或辅助工具的变动（依赖更新、CI/CD 配置）。
Scope 作用域：
指定影响的模块（如：analyzer, graph, executor, notifier, cli, ci）。
Subject 简述：
使用动词开头（如：添加、修复、重构、更新）。
结尾不需要标点符号。
提交示例：
fix(executor): 修复 LLM 调用链中缺失依赖导致的 fallback 问题
feat(analyzer): 添加基于 LLM 的异常归纳逻辑
refactor(graph): 解耦 LLM 服务调用以提升系统稳定性
test(ci): 增加 llm-live-smoke 烟雾测试的错误断言
chore(deps): 更新 LangChain 相关依赖版本

每次修改完代码自动push到对应分支，并创建 Pull Request 以供代码审查和合并。请确保在 PR 描述中详细说明变更内容和测试结果，以便团队成员理解和验证。


## 9. 代码落地建议（文件级）
“我需要构建一个调用链可观测性系统。每次代码变动运行测试后，我希望能够明确看到：
决策路径：当前是走了规则链路（if/else）还是 LLM 链路？
LLM 状态：调用是否成功？如果失败，Fallback 到规则的原因是什么？
关键数据流：输入了什么日志/参数，LLM 输出的结果是什么（如果成功的话）。
请帮我按照以下规范重构代码中的 logger 输出：
统一前缀：所有决策相关的日志必须以 [TRACE-LLM] 或 [TRACE-RULE] 开头。
结构化日志：请使用 JSON 格式记录关键执行上下文。
示例格式：
成功时：[TRACE-LLM] { "status": "success", "module": "analyzer", "model": "gpt-4o", "input_len": 120, "output_token": 45 }
Fallback 时：[TRACE-RULE] { "status": "fallback", "module": "analyzer", "reason": "LangChain dependencies missing", "fallback_to": "keyword_match" }
请将这些标记插入到 analyzer.py 的 LLM 调用处、graph.py 的路由决策处，以及 executor.py 的执行入口处。”

## 10. 更新文档与计划同步流程
从现在起，为了保持项目的可维护性，请在我进行任何代码提交或逻辑变更时，强制执行以下“同步流程”：
1. 文档结构 (../docs/PROJECT_PLAN.md)
每当我完成一个功能或修复（feat/fix）时，请你在处理完代码后，主动更新 ../docs/PROJECT_PLAN.md 中的里程碑任务清单，确保它反映当前的项目状态和下一步计划。

## 11. PR 合并自动化流程

### 11.1 按 PR 号合并

- 运行：
  bash scripts/merge_pr.sh <pr_number> [labels] [merge_method]
- 示例：
  bash scripts/merge_pr.sh 1
  bash scripts/merge_pr.sh 1 "automerge,needs-release-note" rebase

脚本会自动执行：
- 检查最新 workflow run 状态（`gh run list`）
- 给 PR 添加标签
- 执行合并并删除分支（`gh pr merge`）

### 11.2 按当前分支自动定位 PR 并合并

- 运行：
  bash scripts/merge_current_pr.sh [labels] [merge_method]
- 示例：
  bash scripts/merge_current_pr.sh
  bash scripts/merge_current_pr.sh "automerge,ci-passed" squash

脚本会先读取当前 git 分支，再自动查找该分支对应的 OPEN PR，随后复用 `merge_pr.sh` 执行完整流程。

### 11.3 可选环境变量

- `RUN_LIMIT`：检查 workflow run 数量，默认 20
- `WAIT_SECONDS`：轮询间隔秒数，默认 20
- `MAX_WAIT_SECONDS`：CI 最长等待秒数，默认 1800
- `ALLOW_NO_RUNS`：设为 1 时，分支无 run 记录也允许继续