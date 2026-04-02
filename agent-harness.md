# Nebula-Copilot 自我循环运行框架 - 提示词集合

基于 Anthropic 工程博客《Effective harnesses for long-running agents》(2025-11-26) 设计。

## 框架概述

本框架让 Claude Code 可以自主循环运行，通过增量开发方式完成项目优化任务。

---

## 核心文件

| 文件 | 用途 |
|------|------|
| task.json | 任务清单（AI 领取任务用） |
| progress.txt | 工作日志（记录完成/失败） |
| feature_list.json | 特性列表（所有待办功能） |
| init.sh | 环境初始化脚本 |
| run-agent.sh | 自我循环运行脚本 |

---

## 运行流程

### 1. 初始化

```bash
cd /Users/mac/项目/project/nebula-copilot
./init.sh
```

### 2. 启动自我循环

```bash
./run-agent.sh
```

框架会：
1. 读取 feature_list.json
2. 选择第一个 `passes: false` 的功能
3. 调用 Claude 完成开发
4. 提交 git，记录 progress
5. 循环直到所有功能完成

---

## 增量开发原则（来自 Anthropic 文章）

### 核心问题与解决方案

| 问题 | 方案 |
|------|------|
| 智能体一次性做太多 | **Feature list** + 每次只做一个 |
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
  "notes": "备注"
}
```

**重要**：
- 只改 `passes` 字段（false → true）
- 不删除或修改 steps（防止功能遗漏）
- 添加 notes 记录遇到的问题

---

## Coding Agent 会话流程

### 会话开始

1. **获取上下文**
   ```bash
   pwd
   cat progress.txt
   cat feature_list.json
   git log --oneline -10
   ```

2. **验证环境**
   ```bash
   ./init.sh
   python -m pytest -q  # 确保测试通过
   ```

3. **选择功能**
   - 读取 feature_list.json
   - 选择 `passes == false` 且优先级最高的功能
   - 不要选择已标记为完成的功能

### 开发过程

1. 仔细阅读 CLAUDE.md 了解项目规范
2. 阅读相关源代码
3. 按 steps 逐步实现
4. 编写/更新测试
5. 运行测试验证

### 会话结束

1. **更新 feature_list.json**
   ```json
   "passes": true
   ```

2. **Git 提交**
   ```bash
   git add .
   git commit -m "opt: 完成 opt-XXX"
   ```

3. **记录进度**
   ```
   [2026-04-02 10:30:00] [SUCCESS] opt-XXX - 完成向量模型升级
   ```

---

## Nebula-Copilot 当前特性列表

基于 README.md 中"下一阶段优化方向"：

| ID | 类别 | 描述 | 优先级 |
|----|------|------|--------|
| opt-001 | vector | 接入生产向量模型 (OpenAI Embeddings) | 1 |
| opt-002 | vector | 扩展向量库容量 (Milvus/Weaviate) | 2 |
| opt-003 | feedback | 人工反馈循环机制 | 2 |
| opt-004 | web | Dashboard 集成历史案例和源码定位 | 3 |
| opt-005 | monitoring | 诊断效果指标与告警联动 | 3 |
| opt-006 | testing | 完善 monitor-es 和 notifier 流程测试 | 1 |
| opt-007 | performance | 大 trace 文件处理性能优化 | 2 |
| opt-008 | docs | 运维故障排查手册 | 3 |

---

## 失败处理

如果某功能无法完成：

1. 记录失败原因到 progress.txt
2. 记录 notes 到 feature_list.json
3. 不要强行标记 passes 为 true
4. 继续下一个功能

格式：
```
[2026-04-02 10:30:00] [FAILED] opt-XXX - 需要先完成 opt-YYY 依赖
```

---

## 配置选项

```bash
MAX_ITERATIONS=50   # 最大迭代次数
SESSION_DELAY=3     # 会话间隔（秒）
PYTHON_BIN=./venv/bin/python  # Python 解释器路径
```

---

## 停止框架

```bash
# 方法1: 等待当前会话完成，然后按 Ctrl+C
# 方法2: 强制终止
pkill -f run-agent.sh
```

---

## 监控命令

```bash
# 查看进度日志
cat progress.txt

# 查看特性状态
cat feature_list.json | python3 -m json.tool

# 查看 git 历史
git log --oneline

# 查看测试覆盖率
python -m pytest --cov=nebula_copilot --cov-report=term-missing
```

---

## 参考

- 原文：https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- 发布日期：2025-11-26
- 项目文档：CLAUDE.md
