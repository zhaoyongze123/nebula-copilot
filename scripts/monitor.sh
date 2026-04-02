#!/bin/bash
#==============================================================================
# 多角色协同监控脚本
# 实时监控 message_bus 和任务进度
#==============================================================================

watch -n 5 'echo "========== Nebula-Copilot 多角色协同监控 =========="; echo ""; echo "--- 待测试队列 (ready_for_test.md) ---"; cat message_bus/ready_for_test.md 2>/dev/null || echo "暂无"; echo ""; echo "--- Bug 报告 (bug_reports.md) ---"; cat message_bus/bug_reports.md 2>/dev/null || echo "暂无"; echo ""; echo "--- 已完成 (completed.md) ---"; cat message_bus/completed.md 2>/dev/null || echo "暂无"; echo ""; echo "--- 任务进度 ---"; total=$(grep -c "\"passes\": false" feature_list.json 2>/dev/null || echo "0"); completed=$(grep -c "\"passes\": true" feature_list.json 2>/dev/null || echo "0"); echo "待完成: $total | 已完成: $completed"; echo ""; echo "--- 最近 Git 提交 ---"; git log --oneline -3'
