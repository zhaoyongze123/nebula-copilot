# MCP 工具配置指南

## 已配置的 MCP

### 1. Memory MCP ✅
```bash
claude mcp add memory -- npx -y @modelcontextprotocol/server-memory
```
**功能**：长期记忆存储
**用途**：
- 记录高频 Bug 和解决方案
- 存储架构决策记录
- 跨会话共享重要发现

### 2. Filesystem MCP ✅
```bash
claude mcp add filesystem -- npx -y @modelcontextprotocol/server-filesystem "/Users/mac/Documents/Obsidian Vault/"
```
**功能**：文件系统访问
**用途**：
- 查阅业务规则和设计文档
- 参考架构图和流程图
- 查询历史决策记录

## 待配置 MCP

### 3. Puppeteer MCP（浏览器控制）
```bash
claude mcp add puppeteer -- npx -y @modelcontextprotocol/server-puppeteer
```
**功能**：直接控制浏览器
**工具集**：
- `puppeteer_navigate`: 访问 URL
- `puppeteer_screenshot`: 截图
- `puppeteer_click`: 点击元素
- `puppeteer_fill`: 填写表单
- `puppeteer_evaluate`: 执行 JS

### 4. Fetch MCP（HTTP 请求）
⚠️ **注意**：官方 `@modelcontextprotocol/server-fetch` 包不存在，正在寻找替代方案。

### 5. GitHub MCP（版本控制）
```bash
claude mcp add github -- npx -y @modelcontextprotocol/server-github
```
**功能**：GitHub API 操作
**工具**：
- `create_pull_request`
- `search_repositories`
- `get_issue`

### 6. PostgreSQL MCP（数据库）
```bash
claude mcp add postgres -- npx -y @modelcontextprotocol/server-postgres
```
**功能**：直连数据库查询

## 查看 MCP 状态
```bash
claude mcp list
```

## 故障排查
```bash
# 移除失败的 MCP
claude mcp remove <name>

# 重新添加
claude mcp add <name> -- <command>
```

## MCP 工具在 CLAUDE.md 中的使用规则

### Memory 使用
```
遇到疑难 Bug 时：
1. 先查询 memory 是否有记录: memory_search("bug timeout")
2. 解决后写入: memory_create()
3. 格式: [时间] 类别: xxx 内容: xxx
```

### Filesystem 使用
```
遇到业务逻辑问题：
1. 查阅 Obsidian: filesystem_read("/Users/mac/Documents/Obsidian Vault/java基础/xxx.md")
2. 搜索关键词: filesystem_search("订单状态机")
```

### Puppeteer 使用（QA Agent）
```
验证 Web Dashboard：
1. puppeteer_navigate("http://localhost:8080/dashboard")
2. puppeteer_fill("#search", "trace_id")
3. puppeteer_click("#search-btn")
4. puppeteer_screenshot("results/verify.png")
```
