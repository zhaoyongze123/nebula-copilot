#!/bin/bash
# Nebula-Copilot 自我循环运行脚本
# 基于 Anthropic 文章《Effective harnesses for long-running agents》设计
#
# 核心思路：
# 1. 每次会话只做一个优化功能（增量开发）
# 2. 通过 feature_list.json 跟踪进度
# 3. 通过 progress.txt 记录历史
# 4. Git 用于版本控制和回滚

set -e

# 配置
MAX_ITERATIONS=${MAX_ITERATIONS:-50}
SESSION_DELAY=3
LOG_FILE="progress.txt"
PYTHON_BIN="${PYTHON_BIN:-./venv/bin/python}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_feature() { echo -e "${CYAN}[FEATURE]${NC} $1"; }

# 检查环境
check_environment() {
    log_info "检查运行环境..."

    if [ ! -f "feature_list.json" ]; then
        log_error "缺少 feature_list.json，请先初始化项目"
        exit 1
    fi

    # 查找 Python 解释器
    if [ ! -f "$PYTHON_BIN" ]; then
        if [ -f "/Users/mac/Documents/python/venv/bin/python" ]; then
            PYTHON_BIN="/Users/mac/Documents/python/venv/bin/python"
        else
            log_error "未找到 Python 虚拟环境"
            exit 1
        fi
    fi

    log_success "环境检查通过"
    log_info "Python: $PYTHON_BIN"
}

# 检查是否有待完成的功能
has_pending_features() {
    local pending=$(grep -c '"passes": false' feature_list.json 2>/dev/null || echo "0")
    [ "$pending" -gt 0 ]
}

# 获取下一个要完成的功能
get_next_feature() {
    python3 -c "
import json
with open('feature_list.json', 'r') as f:
    data = json.load(f)
    for feature in data.get('features', []):
        if not feature.get('passes', False):
            print(json.dumps(feature))
            break
" 2>/dev/null
}

# 记录进度
record_progress() {
    local status=$1
    local feature_id=$2
    local message=$3
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$status] $feature_id - $message" >> "$LOG_FILE"
}

# 验证项目完整性
verify_project() {
    log_info "验证项目完整性..."
    if [ ! -f "nebula_copilot/cli.py" ]; then
        log_warning "nebula_copilot/cli.py 不存在"
        return 1
    fi
    log_success "项目结构完整"
}

# 运行一个会话
run_session() {
    local iteration=$1
    log_info "========== 会话 $iteration 开始 =========="

    # 1. 获取上下文
    log_info "获取项目上下文..."
    echo "当前目录: $(pwd)"
    echo "--- 最近进度 ---"
    tail -5 "$LOG_FILE" 2>/dev/null || echo "暂无记录"
    echo "---"

    # 2. 检查是否有待处理功能
    if ! has_pending_features; then
        log_success "所有功能已完成！项目优化工作完成！"
        return 1
    fi

    # 3. 获取下一个功能
    local next_feature=$(get_next_feature)
    if [ -z "$next_feature" ]; then
        log_warning "无法获取下一个功能"
        return 0
    fi

    local feature_id=$(echo "$next_feature" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id','unknown'))" 2>/dev/null)
    local feature_desc=$(echo "$next_feature" | python3 -c "import json,sys; print(json.load(sys.stdin).get('description','unknown'))" 2>/dev/null)
    local feature_category=$(echo "$next_feature" | python3 -c "import json,sys; print(json.load(sys.stdin).get('category','unknown'))" 2>/dev/null)
    local feature_steps=$(echo "$next_feature" | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin).get('steps',[])))" 2>/dev/null)

    log_feature "[$feature_id] $feature_desc"
    log_info "类别: $feature_category"

    # 4. 构建任务提示
    local task_prompt="
当前任务: 完成 Nebula-Copilot 优化功能 [$feature_id]

功能描述: $feature_desc

实现步骤:
$feature_steps

请执行以下步骤:
1. 仔细阅读 CLAUDE.md 了解项目规范
2. 读取相关源代码理解当前实现
3. 按照步骤实现该功能
4. 编写或更新测试用例
5. 运行测试确保功能正确: $PYTHON_BIN -m pytest -q
6. 功能验证通过后，更新 feature_list.json 将 passes 改为 true
7. 提交代码: git add . && git commit -m 'opt: 完成 $feature_id'
8. 记录进度到 progress.txt

重要提醒:
- 每次只做一个功能，不要试图一次完成多个
- 代码完成后必须可以直接合并到 main 分支
- 必须实际运行测试，不能只靠代码审查
- 如果遇到问题，记录失败原因并继续下一个功能
"

    # 5. 执行 Claude 会话
    log_info "启动 Claude Agent..."

    if command -v claude &> /dev/null; then
        claude --print "$task_prompt" 2>&1
        local exit_code=$?
    else
        log_warning "Claude CLI 未安装，使用 Python 直接执行"
        # 如果没有 Claude CLI，至少记录任务信息
        echo "$task_prompt"
        exit_code=0
    fi

    # 6. 记录结果
    if [ $exit_code -eq 0 ]; then
        record_progress "SUCCESS" "$feature_id" "会话正常完成"
        log_success "会话 $iteration 完成"
    else
        record_progress "FAILED" "$feature_id" "会话异常退出，代码: $exit_code"
        log_warning "会话 $iteration 异常退出"
    fi

    return 0
}

# 主循环
main() {
    echo ""
    echo "========================================"
    echo "  Nebula-Copilot 自我循环运行框架"
    echo "  基于 Anthropic 长运行智能体方案"
    echo "========================================"
    echo ""

    check_environment
    verify_project

    local iteration=0

    while [ $iteration -lt $MAX_ITERATIONS ]; do
        iteration=$((iteration + 1))

        # 检查是否还有待完成功能
        if ! has_pending_features; then
            log_success "所有优化功能已完成！"
            break
        fi

        # 运行一个会话
        if ! run_session $iteration; then
            break
        fi

        # 会话间隔
        if [ $iteration -lt $MAX_ITERATIONS ] && has_pending_features; then
            log_info "等待 ${SESSION_DELAY}s 后开始下一个会话..."
            sleep $SESSION_DELAY
        fi
    done

    echo ""
    echo "========================================"
    echo "  框架执行完成"
    echo "  总会话数: $iteration"
    echo "========================================"

    # 显示最终进度
    if [ -f "feature_list.json" ]; then
        echo ""
        echo "--- 最终进度 ---"
        total=$(grep -c '"passes": false' feature_list.json 2>/dev/null || echo "0")
        completed=$(grep -c '"passes": true' feature_list.json 2>/dev/null || echo "0")
        echo "待完成功能: $total"
        echo "已完成功能: $completed"
    fi
}

# 处理中断信号
trap 'echo ""; log_warning "收到中断信号，正在退出..."; exit 130' INT TERM

# 运行
main "$@"
