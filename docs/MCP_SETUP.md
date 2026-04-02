# MCP 工具配置指南

## 当前已配置并连接成功

### 1. Memory MCP ✅
```bash
claude mcp add memory -- npx -y @modelcontextprotocol/server-memory
```
**功能**：长期记忆存储（知识图谱）
**用途**：
- 记录高频 Bug 和解决方案
- 存储架构决策记录
- 跨会话共享重要发现

**工具**：
- `memory_create` - 创建记忆节点
- `memory_search` - 搜索记忆
- `memory_list` - 列出所有记忆

### 2. Filesystem MCP ✅
```bash
claude mcp add filesystem -- npx -y @modelcontextprotocol/server-filesystem "/Users/mac/Documents/Obsidian Vault/"
```
**功能**：文件系统访问
**用途**：
- 查阅业务规则和设计文档
- 参考架构图和流程图
- 查询历史决策记录

**工具**：
- `filesystem_read` - 读取文件
- `filesystem_search` - 搜索文件
- `filesystem_list_directory` - 列出目录

### 3. GitHub MCP ✅
```bash
claude mcp add github -- npx -y @modelcontextprotocol/server-github
```
**功能**：GitHub API 操作
**用途**：
- 创建 PR
- 搜索仓库
- 管理 Issue

**工具**：
- `github_create_pull_request`
- `github_search_repositories`
- `github_get_issue`
- `github_create_issue`

### 4. Sequential Thinking MCP ✅
```bash
claude mcp add thinking -- npx -y @modelcontextprotocol/server-sequential-thinking
```
**功能**：顺序思考和问题解决
**用途**：
- 复杂问题的分步分析
- 调试时的系统性思考
- 决策前的利弊分析

**工具**：
- `thinking_next` - 下一步思考
- `thinking_reset` - 重置思考

---

## 已添加但未连接（需要额外配置）

### 5. Puppeteer MCP ❌（需要 Chrome）
```bash
# 需要先安装 Chrome，然后：
claude mcp add puppeteer -- npx -y @modelcontextprotocol/server-puppeteer
```
**功能**：浏览器控制
**工具**：
- `puppeteer_navigate` - 访问 URL
- `puppeteer_screenshot` - 截图
- `puppeteer_click` - 点击元素
- `puppeteer_fill` - 填写表单
- `puppeteer_evaluate` - 执行 JS

### 6. Fetch MCP ❌（包不存在）
⚠️ 官方 `@modelcontextprotocol/server-fetch` 包不存在。

---

## 待配置（需要 API Key）

### 7. Brave Search MCP（需要 brave-api-key）
```bash
claude mcp add brave-search -- npx -y @modelcontextprotocol/server-brave-search
# 需要环境变量: BRAVE_API_KEY
```

### 8. Google Maps MCP（需要 google-api-key）
```bash
claude mcp add google-maps -- npx -y @modelcontextprotocol/server-google-maps
# 需要环境变量: GOOGLE_API_KEY
```

### 9. PostgreSQL MCP（需要数据库 URL）
```bash
claude mcp add postgres -- npx -y @modelcontextprotocol/server-postgres <database_url>
```

---

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

# 查看 MCP 配置文件
cat ~/.claude.json | jq '.mcpServers'
```

---

## MCP 工具使用指南

### Memory - 长期记忆
```
遇到疑难 Bug 时：
1. 先 memory_search("NullPointerException")
2. 如果有记录，直接使用已知方案
3. 如果没有，解决后 memory_create() 记录
```

### Filesystem - Obsidian 笔记
```
遇到业务逻辑问题：
1. filesystem_search("订单状态机")
2. filesystem_read("/path/to/note.md")
3. 理解业务背景后再写代码
```

### GitHub - 版本控制
```
功能完成后：
1. github_search_repositories("nebula-copilot")
2. github_create_pull_request(...)
3. github_create_issue(...)
```

### Thinking - 顺序思考
```
遇到复杂问题时：
1. thinking_next("问题是什么？")
2. thinking_next("可能的原因？")
3. thinking_next("验证方案？")
4. thinking_reset()  # 完成后重置
```
