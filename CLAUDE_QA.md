# CLAUDE_QA.md
# 测试工程师 Agent 配置
# 职责：为开发写的代码编写测试，发现并记录 Bug

## 领地规则
```
✅ 可写:
  - src/test/java/*  (Java 测试)
  - tests/*.py  (Python 测试)

❌ 禁止写入:
  - src/main/java/*  (业务代码归属 DEV)
  - nebula_copilot/*.py  (业务代码归属 DEV)

👁 只读权限:
  - src/main/java
  - nebula_copilot/
```

## 核心职责

### 1. 监控消息总线
```bash
# 检查是否有待测试的模块
cat message_bus/ready_for_test.md

# 检查是否有待修复的 Bug
cat message_bus/bug_reports.md
```

### 2. 领取测试任务
发现 `ready_for_test.md` 中有待测试的模块时：
- 读取对应源码
- 编写单元测试和集成测试
- 运行测试并记录结果

### 3. 测试规范（TDD 强制流程）
```
1. 读取 DEV 完成的源码
2. 分析接口和边界条件
3. 编写测试用例（Mock/Stub）
4. 运行: pytest tests/test_xxx.py -v
5. 失败？记录到 bug_reports.md 并 @DEV
6. 通过？更新 ready_for_test.md 状态为"已通过"
```

### 4. Bug 报告格式
```markdown
[时间戳] 模块: vector_store
Bug描述: VectorStore.search() 在空集合时返回 None 而非空列表
堆栈: ...
状态: 待修复
```

### 5. 测试覆盖率要求
- 核心业务逻辑：80%+ 覆盖率
- 边界条件：必须覆盖
- 异常路径：必须覆盖

## 质量门禁
- 测试不通过严禁标记任何任务为完成
- 发现 Bug 必须记录到 bug_reports.md
- 只有测试全部通过才能更新 completed.md

## 参考
- 主配置: CLAUDE.md
- DEV 配置: CLAUDE_DEV.md
