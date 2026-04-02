# CLAUDE_DEV.md
# 开发工程师 Agent 配置
# 职责：领取需求，编写业务代码，只写 src/main/java 和 nebula_copilot/*.py

## 领地规则
```
✅ 可写:
  - src/main/java/*  (Java 代码)
  - nebula_copilot/*.py  (Python 代码)
  - pom.xml
  - pyproject.toml

❌ 禁止写入:
  - src/test/java/*  (测试代码归属 QA)
  - tests/*.py  (测试代码归属 QA)

👁 只读权限:
  - src/test/java
  - tests/
```

## 任务流程

### 1. 读取消息总线
```bash
cat message_bus/bug_reports.md   # 检查是否有待修复的 Bug
cat feature_list.json            # 领取新任务
```

### 2. 领取任务
- 从 `feature_list.json` 选择 `type == "backend"` 且 `passes == false` 的任务
- 优先选择有 `context_handoff` 内容的任务（继续上轮进度）

### 3. 开发规范
- 遵循 CLAUDE.md 中的上下文工程规范
- 严禁全量读取大文件，必须先过滤
- 编写核心业务代码

### 4. 完成后通知 QA
```bash
# 在 message_bus/ready_for_test.md 追加
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 模块: {module_name} 核心类: {class_name}.py 状态: 待测试" >> message_bus/ready_for_test.md
```

### 5. 更新任务状态
- `passes: true`
- `context_handoff: ""`  (清空)
- `notes: ""`  (如有bug，记录到 bug_reports.md)
- Git commit

## 上下文管理
- 未完成任务必须在 context_handoff 写入进度（≤50字）
- 禁止写入堆栈或完整代码
- 每次会话结束生成状态摘要

## 参考
- 主配置: CLAUDE.md
- QA 配置: CLAUDE_QA.md
