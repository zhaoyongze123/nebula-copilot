# Nebula Copilot 项目规划（LangChain / LangGraph 版本）

## 1. 文档目标

本文档用于统一 `nebula-copilot` 的下一阶段建设方向，覆盖：

- 当前状态评估
- 业务目标与价值
- 技术架构演进路径（`AgentExecutor -> LangGraph`）
- Tool 契约与大数据摘要策略（降 token 成本）
- 分阶段任务拆解与验收标准
- 代码落地建议（文件级）

---

## 2. 当前阶段与基础能力盘点

### 2.1 已具备能力（现状）

项目当前已经具备较好的“可进化底座”：

1. **数据源抽象**
   - 本地 JSON：`LocalJsonRepository`
   - ES 查询：`ESRepository`

2. **确定性分析内核**
   - `analyzer.py` 可输出瓶颈 span、错误类型、建议动作

3. **结构化报告**
   - `NebulaReport` / `SpanReport` Pydantic 契约

4. **命令行入口**
   - `analyze` / `analyze-es` / `list-traces`

5. **Tool 基础契约（已初步统一）**
   - `status/tool/target/payload/error`

### 2.2 当前瓶颈

- Agent 编排仍偏“线性调用”，缺少显式状态管理
- 复杂场景下（循环、分支、回溯）难以稳定控制
- 大体量日志/trace 直接喂给 LLM 成本高、效果不稳

---

## 3. 业务分析

### 3.1 业务目标

构建一个面向排障值班的自动化 Agent：

- 输入：`trace_id`（或告警上下文）
- 自动调用：Trace / JVM / Logs 工具
- 输出：可落地排障结论 + 飞书通知
- 追求指标：**降低 MTTR、标准化分析路径、沉淀可复盘证据**

### 3.2 典型业务流

1. 告警系统触发（慢调用/错误率上升）
2. 传入 trace_id 或时间窗
3. Agent 自动完成多工具分析
4. 输出结构化摘要并发飞书
5. 结果归档（便于复盘与训练）

### 3.3 业务验收标准（建议）

- 关键链路自动化成功率 > 95%
- 相同 trace 不重复刷屏（去重策略）
- 飞书消息可读且可执行（必须含“建议动作”）
- 全链路具备可追踪 run_id（方便回查）

---

## 4. 技术路线：从 AgentExecutor 进化到 LangGraph

## 4.1 为什么要升级

`AgentExecutor` 适合快速起步，但在生产排障中会遇到：

- LLM 自由选工具导致路径不稳定
- 难以控制循环次数与回溯条件
- 状态观测不透明，调试成本高

`LangGraph` 更适合本项目：

- 将流程显式建模为状态机 / 有向图
- 每个节点职责单一、可测试
- 分支与重试策略可控
- 运行状态可观测、可回放

## 4.2 推荐图结构（第一版）

```text
START
  -> LoadTrace
  -> RuleAnalyze
  -> BranchByErrorType
      -> FetchJVM (可选)
      -> FetchLogs (可选)
  -> BuildReport
  -> PushFeishu
  -> PersistRun
END
```

### 节点说明

1. `LoadTrace`
   - 输入：trace_id/index/es_config
   - 输出：trace 摘要 + 原始引用

2. `RuleAnalyze`
   - 使用现有 `analyzer.py`（确定性逻辑）
   - 输出：bottleneck/error_type/action

3. `BranchByErrorType`
   - Timeout / DB / Downstream / Unknown 分流

4. `FetchJVM` / `FetchLogs`
   - 可并行（LangGraph 支持）
   - 输出必须是“摘要数据”，非大体量原文

5. `BuildReport`
   - 生成 `NebulaReport`
   - 可选调用 LLM 做“表达润色”，但不改事实

6. `PushFeishu`
   - 失败可重试（指数退避）
   - 保证发送状态可记录

7. `PersistRun`
   - 保存 `run_id`、耗时、状态、关键结论

---

## 5. Tool 契约深度优化（重点）

## 5.1 统一响应协议（升级版）

建议统一为：

```json
{
  "status": "ok|error",
  "tool": "tool_name",
  "target": "trace_id|service_name",
  "payload": {},
  "summary": {},
  "error": null,
  "meta": {
    "source": "es|logs|jvm|mock",
    "latency_ms": 123,
    "truncated": false,
    "sample_size": 200,
    "returned_size": 20,
    "run_id": "uuid"
  }
}
```

### 字段说明

- `payload`：结构化原始结果（可裁剪）
- `summary`：给 LLM 的“高密度情报摘要”（重点）
- `meta.truncated`：标识是否截断
- `meta.sample_size/returned_size`：帮助判断置信度

## 5.2 数据摘要（Summary）机制

核心原则：**LLM 只看情报，不看噪音**。

#### `search_logs` 示例

不要返回 1000 行日志全文，改为：

- 错误关键词 TopN（timeout / deadlock / 503）
- 时间分布（每分钟错误数）
- 代表性样本（最多 5~10 行）
- 相关 trace/span 命中统计

#### `get_trace` 示例

返回：

- 调用链深度、总 span 数
- 最慢 TopN spans
- ERROR spans 摘要
- 关键路径（critical path）

#### `get_jvm_metrics` 示例

返回：

- heap 使用率区间
- GC 次数与停顿分位数
- 线程池拒绝/阻塞指标
- 与历史基线偏差

---

## 6. 分阶段实施计划（含验收）

## 阶段 A：LangGraph MVP（1~2 天）

任务：

1. 引入 LangGraph 基础依赖与运行骨架
2. 定义 `AgentState`（TypedDict/Pydantic）
3. 实现核心节点：`LoadTrace -> RuleAnalyze -> BuildReport -> PushFeishu`
4. CLI 新增命令：`agent-analyze`

验收：

- 能输入 trace_id 跑通完整链路
- 能输出 `NebulaReport`
- 能成功推送飞书

## 阶段 B：分支与摘要增强（2~3 天）

任务：

1. 增加 `BranchByErrorType`
2. 引入 `FetchJVM` / `FetchLogs` 条件分支
3. 落地 Tool `summary` 机制与截断策略
4. 完善错误处理与重试策略

验收：

- Timeout/DB/Downstream 场景都能走对应分支
- 上下文 token 成本显著下降（>40%）
- 错误场景可观测且可追踪

## 阶段 C：生产化与治理（3~5 天）

任务：

1. 增加 run 持久化（sqlite/redis）
2. 增加去重（trace_id + 时间窗）
3. 增加审计日志与敏感信息脱敏
4. 增加监控指标（成功率/延迟/失败原因）

验收：

- 连续运行稳定
- 无重复刷屏
- 可回放问题 run

---

## 7. 代码落地建议（文件级）

建议新增/调整结构：

```text
nebula_copilot/
  agent/
    graph.py              # LangGraph 构建与编排入口
    state.py              # AgentState 定义
    nodes.py              # 各节点函数
    router.py             # 分支路由逻辑
    prompts.py            # LLM 提示词（可选）
  tools/
    trace_tools.py        # get_trace + trace summary
    log_tools.py          # search_logs + summary
    jvm_tools.py          # get_jvm_metrics + summary
    notify_tools.py       # push_feishu
  runtime/
    run_store.py          # run 记录持久化
  cli.py                  # 新增 agent-analyze 命令
```

### 建议复用

- 复用 `repository.py`：数据源读取
- 复用 `analyzer.py`：确定性诊断（事实来源）
- 复用 `report_schema.py`：最终输出契约

---

## 8. 核心状态定义建议（示意）

```python
from typing import Any, Dict, List, Optional, TypedDict

class AgentState(TypedDict, total=False):
    run_id: str
    trace_id: str
    index: str
    error_type: str
    bottleneck_service: str

    trace_result: Dict[str, Any]
    trace_summary: Dict[str, Any]

    jvm_result: Dict[str, Any]
    logs_result: Dict[str, Any]

    report: Dict[str, Any]
    feishu_sent: bool

    errors: List[str]
```

---

## 9. 测试策略

### 9.1 单测

- 每个节点入参/出参测试
- router 分支测试（4 类 error_type）
- tool summary 结果格式测试

### 9.2 集成测试

- 整图执行测试（mock tools）
- 飞书发送失败重试测试
- 大日志输入的截断/摘要测试

### 9.3 契约测试

- `NebulaReport` JSON 字段完整性
- Tool 响应协议字段完整性（含 summary/meta）

---

## 10. 风险与对策

1. **LLM 幻觉风险**
   - 对策：事实由工具提供，LLM只做编排与表述

2. **上下文成本过高**
   - 对策：强制 summary 机制 + 截断策略

3. **工具依赖不稳定（ES/日志平台波动）**
   - 对策：超时、重试、降级（只输出已知结论）

4. **重复告警干扰**
   - 对策：去重键 + 时间窗限流

---

## 11. 近期执行清单（下一迭代）

1. 建立 `agent/state.py` 与 `agent/graph.py` 最小骨架
2. 将现有 `tooling.py` 迁移到 `tools/*_tools.py` 并补 `summary/meta`
3. 新增 `agent-analyze` CLI 命令
4. 新增 1 组 LangGraph 集成测试（mock 工具）
5. 完成飞书失败重试与 run_id 落库（可先 sqlite）

---

## 12. 结论

本项目建议从“可运行的 AgentExecutor 原型”升级为“可控的 LangGraph 状态机架构”。

核心方向是：

- **流程可控**（图编排）
- **事实可信**（规则引擎 + 工具结果）
- **成本可控**（summary 抽象层）
- **运行可观测**（run_id + 持久化 + 指标）

按此路线推进，可在保持当前代码资产复用的前提下，较快落地生产级自动排障 Agent。






# 里程碑任务清单（优化版）

> 说明：以下状态基于当前代码仓库真实落地情况维护，避免“文档已完成、代码未实现”的偏差。

## M1：现有能力固化（已完成）
- [x] 规划文档与业务目标梳理（本文件）
- [x] 数据源抽象：`Repository`（本地 JSON + ES）
- [x] 确定性诊断：`analyzer.py`（瓶颈识别/异常分类/建议动作）
- [x] 结构化报告：`NebulaReport/SpanReport`
- [x] 基础 CLI：`analyze` / `analyze-es` / `list-traces`
- [x] 基础测试可用（当前 `pytest -q` 通过）

### M1 验收标准（已满足）
- 本地与 ES 两种输入均可完成诊断
- 报告模型字段固定且可序列化
- 关键测试通过（单测+CLI 基础路径）

---

## M2：Agent 基础化（已完成）
- [x] Tool 返回基础契约统一（`status/tool/target/payload/error`）
- [x] Tool 返回增强契约补齐（`meta/summary/truncation`）
- [x] `agent-analyze` 命令落地（支持 run_id 输出）
- [x] Tool 分层迁移到 `tools/*_tools.py`（兼容 `tooling.py` 入口）
- [x] 运行态记录 `run_id` 与最小持久化（`runs_path`）

### M2 验收标准（已满足）
- Agent 入口命令可一键执行“取数→诊断→通知”
- 每个 Tool 返回体包含统一契约与摘要信息
- 失败场景有可读错误码与降级输出

---

## M3：从 AgentExecutor 进化到 LangGraph（待开始）
- [ ] 引入 `agent/state.py`（统一状态定义）
- [ ] 引入 `agent/graph.py`（显式 DAG/状态机流程）
- [ ] 节点化执行：`get_trace -> analyze -> route -> enrich -> report -> notify`
- [ ] 条件路由：按 `error_type` 分发 JVM / Logs / 双查策略
- [ ] 失败重试与回退策略（超时、空数据、第三方故障）

### M3 验收标准（目标）
- 图中至少包含 6 个核心节点与 2 条条件分支
- 任一节点失败可在状态中追踪并给出降级结果
- 至少 1 组完整图集成测试（mock 工具链）通过

---

## M4：生产化与可观测（待开始）
- [ ] 飞书通知可靠化（重试、幂等、失败告警）
- [ ] 去重与限流（trace_id + 时间窗）
- [ ] 运行观测（成功率、耗时、失败原因分布）
- [ ] 部署方案（Docker/K8s）与运维手册

### M4 验收标准（目标）
- 自动任务连续稳定运行（可配置重试与超时）
- 重复 trace 不重复刷屏
- 有最小可观测仪表（日志/统计/run 记录）

---

## 当前阶段结论

当前项目已完成 **M2（Agent 基础化）**，进入 **M3（LangGraph 演进准备）**：
- 核心诊断链路、Agent CLI 入口与 Tool 契约已落地；
- Tool 已完成分层并保留向后兼容入口；
- 下一阶段将推进状态机编排、条件路由与失败回退策略。




