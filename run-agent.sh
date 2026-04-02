#!/bin/bash
#==============================================================================
# Nebula-Copilot 自我循环运行脚本 - 多角色协同版
# 基于 Anthropic 四大前沿理论设计
#
# 《Effective harnesses for long-running agents》(2025-11)
# 《Effective context engineering for AI agents》(2025-09)
# 《Introducing advanced tool use: Programmatic Tool Calling》(2025-11)
# 《Demystifying evals for AI agents》(2026-01)
# 《2026 Agentic Coding Trends Report》(2026)
#
# 核心升级：
# 1. 支持多角色路由（DEV/QA/DOCS）
# 2. 基于上下文工程的状态接力
# 3. 编程式数据处理规范
# 4. TDD 强制门禁
#==============================================================================

set -e

#------------------------ 配置区域 ------------------------
MAX_ITERATIONS=${MAX_ITERATIONS:-100}
SESSION_DELAY=${SESSION_DELAY:-3}
PYTHON_BIN="${PYTHON_BIN:-./venv/bin/python}"
SINGLE_MODE=${SINGLE_MODE:-true}  # true=单角色, false=多角色协同

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

#------------------------ 日志函数 ------------------------
log_info()    { echo -e "${BLUE}[INFO]${NC}    $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC}   $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC}   $1"; }
log_dev()     { echo -e "${CYAN}[DEV]${NC}     $1"; }
log_qa()      { echo -e "${MAGENTA}[QA]${NC}     $1"; }

#------------------------ 检查函数 ------------------------
check_environment() {
    log_info "检查运行环境..."

    if [ ! -f "feature_list.json" ]; then
        log_error "缺少 feature_list.json"
        exit 1
    fi

    # 查找 Python
    if [ ! -f "$PYTHON_BIN" ]; then
        if [ -f "/Users/mac/Documents/python/venv/bin/python" ]; then
            PYTHON_BIN="/Users/mac/Documents/python/venv/bin/python"
        fi
    fi

    log_success "环境检查通过"
}

#------------------------ 上下文管理 ------------------------
# 读取任务的 context_handoff
get_context_handoff() {
    local task_id=$1
    python3 -c "
import json
with open('feature_list.json', 'r') as f:
    data = json.load(f)
    for feat in data.get('features', []):
        if feat.get('id') == '$task_id':
            print(feat.get('context_handoff', ''))
            break
" 2>/dev/null
}

# 更新任务的 context_handoff
update_context_handoff() {
    local task_id=$1
    local context=$2
    python3 -c "
import json
with open('feature_list.json', 'r') as f:
    data = json.load(f)
for feat in data.get('features', []):
    if feat.get('id') == '$task_id':
        feat['context_handoff'] = '$context'
        break
with open('feature_list.json', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
" 2>/dev/null
}

#------------------------ 任务管理 ------------------------
has_pending_tasks() {
    local type=${1:-""}
    if [ -z "$type" ]; then
        grep -c '"passes": false' feature_list.json 2>/dev/null || echo "0"
    else
        python3 -c "
import json
with open('feature_list.json', 'r') as f:
    data = json.load(f)
count = sum(1 for f in data.get('features', []) if f.get('type') == '$type' and not f.get('passes', False))
print(count)
" 2>/dev/null
    fi
}

get_next_task() {
    local type=${1:-""}
    python3 -c "
import json
with open('feature_list.json', 'r') as f:
    data = json.load(f)
# 按 priority 排序，priority 相同按 id 顺序
tasks = [f for f in data.get('features', []) if not f.get('passes', False)]
tasks.sort(key=lambda x: (x.get('priority', 999), x.get('id', 'z')))
if '$type':
    tasks = [t for t in tasks if t.get('type') == '$type']
if tasks:
    print(json.dumps(tasks[0]))
" 2>/dev/null
}

#------------------------ 消息总线 ------------------------
notify_ready_for_test() {
    local module=$1
    local class=$2
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] 模块: $module 核心类: $class 状态: 待测试"
    echo "$msg" >> message_bus/ready_for_test.md
    log_qa "已通知待测试: $module"
}

report_bug() {
    local module=$1
    local desc=$2
    local stack=$3
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] 模块: $module Bug描述: $desc 堆栈: $stack 状态: 待修复"
    echo "$msg" >> message_bus/bug_reports.md
    log_qa "已报告 Bug: $module - $desc"
}

mark_completed() {
    local module=$1
    local hash=$(git log --oneline -1 --format="%h")
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] 模块: $module 提交: $hash 验证: QA"
    echo "$msg" >> message_bus/completed.md
    log_success "已完成并记录: $module"
}

#------------------------ 进度记录 ------------------------
record_progress() {
    local status=$1
    local task_id=$2
    local msg=$3
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$status] $task_id - $msg" >> progress.txt
}

#------------------------ DEV Agent 会话 ------------------------
run_dev_session() {
    local iteration=$1
    log_dev "========== DEV 会话 $iteration =========="

    # 检查待修复 Bug
    local bug_count=$(grep -c "待修复" message_bus/bug_reports.md 2>/dev/null || echo "0")
    if [ "$bug_count" -gt 0 ]; then
        log_warning "发现 $bug_count 个待修复 Bug，优先处理"
        # 从 bug_reports 读取最近一个待修复 Bug
        local bug=$(grep "待修复" message_bus/bug_reports.md | tail -1)
        log_dev "Bug 信息: $bug"
    fi

    # 领取任务（优先 backend 类型）
    local next_task=$(get_next_task "backend")
    if [ -z "$next_task" ]; then
        log_success "没有待处理的 backend 任务"
        return 1
    fi

    local task_id=$(echo "$next_task" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id','unknown'))" 2>/dev/null)
    local task_desc=$(echo "$next_task" | python3 -c "import json,sys; print(json.load(sys.stdin).get('description','unknown'))" 2>/dev/null)
    local task_steps=$(echo "$next_task" | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin).get('steps',[])))" 2>/dev/null)
    local prev_context=$(get_context_handoff "$task_id")

    log_dev "领取任务: [$task_id] $task_desc"
    [ -n "$prev_context" ] && log_dev "上轮进度: $prev_context"

    # 更新 context_handoff
    update_context_handoff "$task_id" "已领取，正在分析..."

    # 构建任务提示
    local task_prompt="你是 Nebula-Copilot 的开发工程师 (DEV Agent)。

## 当前任务
ID: $task_id
描述: $task_desc

## 上轮进度（如有）
$prev_context

## 实现步骤
$task_steps

## 上下文工程规范（必须遵守）
1. 严禁全量读取大文件，必须先 grep/awk/python 过滤
2. 每次会话结束必须更新 context_handoff（≤50字）
3. 未完成任务必须写入 context_handoff 进度

## 开发流程
1. 读取相关源码，理解当前实现
2. 按步骤实现功能
3. 编写/更新单元测试
4. 运行测试: $PYTHON_BIN -m pytest tests/test_xxx.py -v
5. 测试通过后更新 feature_list.json (passes: true, context_handoff: \"\")
6. 通知 QA: 在 message_bus/ready_for_test.md 追加
7. Git 提交: git add . && git commit -m \"feat: 完成 $task_id\"
8. 记录进度到 progress.txt

## 重要提醒
- 只写 src/main/java 和 nebula_copilot/*.py
- 禁止修改 tests/*.py（测试归属 QA）
- 如果遇到无法完成的 Bug，记录到 message_bus/bug_reports.md
"

    # 执行 Claude
    if command -v claude &> /dev/null; then
        claude --print "$task_prompt" 2>&1
        local exit_code=$?
    else
        log_warning "Claude CLI 未安装，跳过执行"
        exit_code=0
    fi

    # 记录结果
    if [ $exit_code -eq 0 ]; then
        record_progress "DEV_SUCCESS" "$task_id" "开发完成"
    else
        record_progress "DEV_FAILED" "$task_id" "开发异常"
    fi

    return 0
}

#------------------------ QA Agent 会话 ------------------------
run_qa_session() {
    local iteration=$1
    log_qa "========== QA 会话 $iteration =========="

    # 检查待测试队列
    local ready_count=$(grep -c "待测试" message_bus/ready_for_test.md 2>/dev/null || echo "0")
    if [ "$ready_count" -eq 0 ]; then
        log_qa "没有待测试的模块"
        return 1
    fi

    # 读取待测试模块
    local ready_line=$(grep "待测试" message_bus/ready_for_test.md | tail -1)
    log_qa "待测试: $ready_line"

    # 提取模块名
    local module=$(echo "$ready_line" | grep -oP '模块: \K[^ ]+')
    local class=$(echo "$ready_line" | grep -oP '核心类: \K[^ ]+')

    if [ -z "$module" ]; then
        module="unknown"
        class="unknown"
    fi

    log_qa "开始测试: $module ($class)"

    # QA 任务提示
    local task_prompt="你是 Nebula-Copilot 的测试工程师 (QA Agent)。

## 当前任务
测试模块: $module
核心类: $class

## 职责
1. 读取 $class 源码
2. 编写/更新单元测试
3. 运行测试验证
4. 如通过，更新 ready_for_test.md 状态为\"已通过\"并记录到 completed.md
5. 如失败，记录到 bug_reports.md

## 测试规范
- 核心业务逻辑覆盖率 ≥ 80%
- 必须覆盖边界条件和异常路径
- 使用 pytest: $PYTHON_BIN -m pytest tests/ -v

## 领地规则
- ✅ 可写: tests/*.py
- ❌ 禁止: src/main/java, nebula_copilot/*.py

## 消息总线操作
测试通过后，在 ready_for_test.md 找到对应行，将\"待测试\"改为\"已通过\"
测试失败后，在 bug_reports.md 追加 Bug 报告
"

    if command -v claude &> /dev/null; then
        claude --print "$task_prompt" 2>&1
    fi

    return 0
}

#------------------------ 单角色主循环 ------------------------
run_single_mode() {
    log_info "启动单角色 DEV 模式"

    local iteration=0

    while [ $iteration -lt $MAX_ITERATIONS ]; do
        iteration=$((iteration + 1))

        if ! has_pending_tasks "backend" | grep -qv "0"; then
            log_success "所有 backend 任务已完成！"
            break
        fi

        if ! run_dev_session $iteration; then
            break
        fi

        log_info "等待 ${SESSION_DELAY}s..."
        sleep $SESSION_DELAY
    done

    log_success "DEV 模式执行完成，总会话: $iteration"
}

#------------------------ 多角色主循环 ------------------------
run_multi_mode() {
    log_info "启动多角色协同模式 (DEV + QA)"

    local iteration=0

    while [ $iteration -lt $MAX_ITERATIONS ]; do
        iteration=$((iteration + 1))

        # DEV 开发
        if has_pending_tasks "backend" | grep -qv "0"; then
            run_dev_session $iteration
        fi

        # QA 测试（每次 DEV 之后）
        if grep -q "待测试" message_bus/ready_for_test.md 2>/dev/null; then
            run_qa_session $iteration
        fi

        # 检查是否全部完成
        local pending_total=$(has_pending_tasks)
        if [ "$pending_total" -eq 0 ]; then
            log_success "所有任务已完成！"
            break
        fi

        log_info "等待 ${SESSION_DELAY}s..."
        sleep $SESSION_DELAY
    done

    log_success "多角色协同执行完成，总会话: $iteration"
}

#------------------------ 主入口 ------------------------
main() {
    echo ""
    echo "=================================================="
    echo "   Nebula-Copilot 自我循环运行框架"
    echo "   多角色协同版 (DEV + QA + DOCS)"
    echo "   基于 Anthropic 前沿 Agent 理论"
    echo "=================================================="
    echo ""
    echo "模式: $([ "$SINGLE_MODE" = "true" ] && echo "单角色 DEV" || echo "多角色协同")"
    echo "最大迭代: $MAX_ITERATIONS"
    echo ""

    check_environment

    if [ "$SINGLE_MODE" = "true" ]; then
        run_single_mode
    else
        run_multi_mode
    fi

    echo ""
    echo "=================================================="
    echo "   执行完成"
    echo "=================================================="
}

trap 'echo ""; log_warning "收到中断信号"; exit 130' INT TERM

main "$@"
