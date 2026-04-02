#!/bin/bash
# Nebula-Copilot 环境初始化脚本
# 用于启动开发环境和验证基础功能

set -e  # 遇到错误立即退出

echo "========== Nebula-Copilot 环境初始化 =========="
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查必要的文件
echo "[1/5] 检查项目结构..."
required_files=("nebula_copilot/cli.py" "nebula_copilot/analyzer.py" "nebula_copilot/agent/graph.py")
for file in "${required_files[@]}"; do
    if [ ! -f "$file" ]; then
        echo "错误: 缺少必要文件 $file"
        exit 1
    fi
done
echo "✓ 项目结构完整"

# 检查 Python 环境
echo "[2/5] 检查 Python 环境..."
if [ -d "venv" ]; then
    PYTHON_BIN="./venv/bin/python"
elif [ -d "/Users/mac/Documents/python/venv" ]; then
    PYTHON_BIN="/Users/mac/Documents/python/venv/bin/python"
else
    echo "错误: 未找到 Python 虚拟环境"
    exit 1
fi

if [ ! -f "$PYTHON_BIN" ]; then
    echo "错误: Python 解释器不存在"
    exit 1
fi

echo "✓ Python 环境: $PYTHON_BIN"
$PYTHON_BIN --version

# 安装项目
echo "[3/5] 安装项目..."
$PYTHON_BIN -m pip install -e . -q
echo "✓ 项目安装完成"

# 检查依赖
echo "[4/5] 检查核心依赖..."
$PYTHON_BIN -c "import typer; import rich; import elasticsearch; import langchain; import langgraph" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✓ 核心依赖已安装"
else
    echo "⚠ 警告: 部分依赖可能缺失，运行 pip install -e '.[dev]' 补全"
fi

# 读取并显示当前进度
echo "[5/5] 当前进度摘要..."
if [ -f "feature_list.json" ]; then
    total=$(grep -c '"passes": false' feature_list.json 2>/dev/null || echo "0")
    completed=$(grep -c '"passes": true' feature_list.json 2>/dev/null || echo "0")
    echo "待完成功能: $total"
    echo "已完成功能: $completed"
else
    echo "⚠ feature_list.json 不存在"
fi

# Git 状态
if [ -d ".git" ]; then
    echo ""
    echo "--- 最近 Git 提交 ---"
    git log --oneline -3
fi

echo ""
echo "========== 初始化完成 =========="
echo ""
echo "快速验证命令:"
echo "  $PYTHON_BIN -m nebula_copilot.cli --help"
echo "  ./run-agent.sh  # 启动自我循环执行"
