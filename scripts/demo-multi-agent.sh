#!/bin/bash
#==============================================================================
# 多角色协同演示脚本
# 演示 DEV + QA 如何通过 message_bus 协作
#==============================================================================

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}=================================================="
echo "   Nebula-Copilot 多角色协同演示"
echo "==================================================${NC}"
echo ""

# 检查 Claude CLI
if ! command -v claude &> /dev/null; then
    echo -e "${RED}错误: Claude CLI 未安装${NC}"
    echo "请先安装: https://docs.anthropic.com/en/docs/claude-code"
    exit 1
fi

echo -e "${YELLOW}步骤1: 演示消息总线机制${NC}"
echo "============================================"
echo ""
echo "模拟 DEV Agent 完成一个模块后写入待测试队列..."
echo ""

# 模拟 DEV 完成 vector_store 模块
cat > /tmp/demo_ready.md << 'EOF'
# 待测试模块消息总线
## 待测试队列
[2026-04-02 10:30:00] 模块: vector_store 核心类: VectorStore.py 状态: 待测试
EOF

cp /tmp/demo_ready.md message_bus/ready_for_test.md

echo -e "${GREEN}✓ DEV 写入 ready_for_test.md${NC}"
echo ""
cat message_bus/ready_for_test.md
echo ""

echo -e "${YELLOW}步骤2: 模拟 QA 发现待测试模块${NC}"
echo "============================================"
echo ""
echo "QA Agent 检测到待测试队列，开始测试..."
echo ""

# 模拟 QA 发现并处理
cat >> message_bus/ready_for_test.md << 'EOF'
[2026-04-02 10:35:00] 模块: vector_store 核心类: VectorStore.py 状态: 已通过
EOF

echo -e "${GREEN}✓ QA 测试通过，更新状态为已通过${NC}"
echo ""
cat message_bus/ready_for_test.md
echo ""

echo -e "${YELLOW}步骤3: 模拟 QA 发现 Bug${NC}"
echo "============================================"
echo ""

# 模拟 QA 发现 bug
cat > /tmp/demo_bug.md << 'EOF'
# Bug 报告消息总线
## 待处理 Bug
[2026-04-02 10:40:00] 模块: analyzer Bug描述: analyze_trace() 在空集合时抛出 IndexError 堆栈: IndexError: list index out of range 状态: 待修复
EOF

cp /tmp/demo_bug.md message_bus/bug_reports.md

echo -e "${RED}✓ QA 发现 Bug，记录到 bug_reports.md${NC}"
echo ""
cat message_bus/bug_reports.md
echo ""

echo -e "${YELLOW}步骤4: 模拟 DEV 修复 Bug${NC}"
echo "============================================"
echo ""
echo "DEV Agent 检测到 bug_reports.md 有待修复 Bug..."
echo ""
echo "DEV 读取 Bug 详情，修复代码，通知 QA 复测..."
echo ""

# 更新 Bug 报告
cat > /tmp/demo_bug_fixed.md << 'EOF'
# Bug 报告消息总线
## 待处理 Bug
## 已关闭 Bug
[2026-04-02 10:45:00] 模块: analyzer Bug描述: analyze_trace() IndexError 已修复 堆栈: - 状态: 已关闭
EOF

cp /tmp/demo_bug_fixed.md message_bus/bug_reports.md

echo -e "${GREEN}✓ DEV 修复完成，Bug 关闭${NC}"
echo ""
cat message_bus/bug_reports.md
echo ""

echo -e "${YELLOW}步骤5: 最终状态汇总${NC}"
echo "============================================"
echo ""
echo "--- feature_list.json 状态 ---"
total=$(grep -c '"passes": false' feature_list.json 2>/dev/null || echo "0")
completed=$(grep -c '"passes": true' feature_list.json 2>/dev/null || echo "0")
echo "待完成: $total"
echo "已完成: $completed"
echo ""

echo "--- message_bus/completed.md ---"
cat message_bus/completed.md 2>/dev/null || echo "暂无"
echo ""

echo -e "${CYAN}=================================================="
echo "   演示完成！"
echo "==================================================${NC}"
echo ""
echo "实际运行命令:"
echo ""
echo "  # 终端1 - 启动 DEV Agent"
echo "  claude -p \"\$(cat CLAUDE_DEV.md)\""
echo ""
echo "  # 终端2 - 启动 QA Agent"
echo "  claude -p \"\$(cat CLAUDE_QA.md)\""
echo ""
echo "  # 或运行自动化脚本（单角色模式）"
echo "  ./run-agent.sh"
