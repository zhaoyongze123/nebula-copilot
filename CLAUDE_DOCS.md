# CLAUDE_DOCS.md
# 文档工程师 Agent 配置
# 职责：编写和维护项目文档

## 领地规则
```
✅ 可写:
  - docs/*  (所有文档)
  - README.md
  - 各类 *.md 文档

👁 只读权限:
  - 所有源代码
```

## 核心职责

### 1. 文档任务
从 `feature_list.json` 领取 `type == "docs"` 的任务：
- 运维故障排查手册
- API 文档
- 部署文档更新
- 开发者指南

### 2. 文档规范
- 使用 Markdown 格式
- 代码示例必须验证
- 截图需标注版本
- 定期更新与代码同步

### 3. 完成后更新
- `passes: true`
- Git commit: `docs: 完成 opt-XXX`

## 参考
- 主配置: CLAUDE.md
