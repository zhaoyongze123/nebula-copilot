#!/bin/bash
# Nebula-Copilot 环境初始化脚本 v2.0
# 验证环境 + 检查框架完整性

set -e

echo "========== Nebula-Copilot v2.0 初始化 =========="
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"

cd "$(dirname "$0")"

# 1. 检查项目结构
echo "[1/6] 检查项目结构..."
required_files=(
    "nebula_copilot/cli.py"
    "nebula_copilot/analyzer.py"
    "nebula_copilot/agent/graph.py"
    "CLAUDE.md"
    "feature_list.json"
    "task.json"
)
for file in "${required_files[@]}"; do
    if [ ! -f "$file" ]; then
        echo "错误: 缺少 $file"
        exit 1
    fi
done
echo "✓ 项目结构完整"

# 2. 检查 Python 环境
echo "[2/6] 检查 Python 环境..."
if [ -d "venv" ]; then
    PYTHON_BIN="./venv/bin/python"
elif [ -f "/Users/mac/Documents/python/venv/bin/python" ]; then
    PYTHON_BIN="/Users/mac/Documents/python/venv/bin/python"
else
    echo "错误: 未找到 Python 虚拟环境"
    exit 1
fi
echo "✓ Python: $PYTHON_BIN"
$PYTHON_BIN --version

# 3. 安装项目
echo "[3/6] 安装项目..."
$PYTHON_BIN -m pip install -e . -q
echo "✓ 安装完成"

# 4. 检查框架文件
echo "[4/6] 检查框架文件..."
framework_files=(
    "CLAUDE_DEV.md"
    "CLAUDE_QA.md"
    "CLAUDE_DOCS.md"
    "run-agent.sh"
    "init.sh"
)
for file in "${framework_files[@]}"; do
    if [ ! -f "$file" ]; then
        echo "⚠ 警告: 缺少框架文件 $file"
    fi
done

# 5. 检查消息总线
echo "[5/6] 检查消息总线..."
if [ ! -d "message_bus" ]; then
    echo "创建 message_bus/ 目录..."
    mkdir -p message_bus
fi
bus_files=(
    "message_bus/ready_for_test.md"
    "message_bus/bug_reports.md"
    "message_bus/completed.md"
)
for file in "${bus_files[@]}"; do
    if [ ! -f "$file" ]; then
        echo "警告: 创建 $file"
        echo "# 初始化" > "$file"
    fi
done

# 6. 进度摘要
echo "[6/6] 进度摘要..."
total=$(grep -c '"passes": false' feature_list.json 2>/dev/null || echo "0")
completed=$(grep -c '"passes": true' feature_list.json 2>/dev/null || echo "0")
pending_bugs=$(grep -c "待修复" message_bus/bug_reports.md 2>/dev/null || echo "0")
pending_tests=$(grep -c "待测试" message_bus/ready_for_test.md 2>/dev/null || echo "0")

echo ""
echo "========== 初始化完成 =========="
echo "待完成任务: $total"
echo "已完成任务: $completed"
echo "待修复 Bug: $pending_bugs"
echo "待测试模块: $pending_tests"
echo ""
echo "快速命令:"
echo "  ./run-agent.sh        # 单角色 DEV 模式"
echo "  SINGLE_MODE=false ./run-agent.sh  # 多角色协同模式"
