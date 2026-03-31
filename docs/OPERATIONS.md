# Nebula Copilot 运维手册

## 1. 日常巡检

- 检查最近运行记录：
  ```bash
  python -m nebula_copilot.cli query-runs --runs-path data/agent_runs.json --limit 20
  ```
- 关注状态分布：ok、degraded、deduped、rate_limited、failed
- 检查向量索引健康状态（Phase 2-4）：
  ```bash
  python -c "from nebula_copilot.history_vector import HistoryVectorStore; store = HistoryVectorStore(); print(f'索引大小: {len(store._cases)}')"
  ```

## 2. 常见故障排查

### 2.1 LLM 调用未生效

现象：输出没有 LLM 润色

排查：
- 检查 LLM_ENABLED 是否为 true
- 检查 GH_MODELS_API_KEY 是否存在
- 检查模型参数 LLM_MODEL 是否正确

### 2.2 通知发送失败

现象：run 状态为 degraded，notify.status=failed

排查：
- webhook 地址是否可达
- 目标平台限流或返回 4xx/5xx
- notify 重试次数是否过低

### 2.3 任务被跳过

现象：run 状态为 deduped 或 rate_limited

排查：
- RUN_DEDUPE_WINDOW_SECONDS 是否过大
- RUN_RATE_LIMIT_PER_MINUTE 是否过小
- 是否短时间重复触发同一 trace_id

### 2.4 向量检索返回无结果（Phase 2-4）

现象：历史案例或源码片段检索空结果

排查：
- 历史向量库是否已构建：`data/history_vector.db` 是否存在
- 白名单目录配置是否正确
- 向量最小相似度阈值是否过高（建议 0.1-0.5）
- 查看日志是否有向量库初始化错误

解决方案：
```bash
# 重建历史向量索引
python scripts/build_history_index.py --runs-file data/agent_runs.json --validate

# 重新扫描源码白名单
python -m nebula_copilot.cli rebuild-code-whitelist --dirs src/service src/api
```

## 3. 关键配置项

### LLM 相关
- GH_MODELS_API_KEY：模型密钥
- LLM_ENABLED：LLM 开关
- LLM_PROVIDER：模型提供商（github/azure/openai）
- LLM_MODEL：模型名称
- LLM_TIMEOUT_MS：超时（默认 8000）

### 诊断优化
- RUN_DEDUPE_WINDOW_SECONDS：去重窗口（默认 300）
- RUN_RATE_LIMIT_PER_MINUTE：每分钟限流（默认 60）
- METRICS_ENABLED：观测开关
- VECTOR_MIN_SCORE：向量相似度阈值（默认 0.2）

### 数据治理（Phase 4）
- DATA_RETENTION_DAYS：数据保留期（默认 90）
- SENSITIVE_FIELDS_REGEX：敏感字段正则（password/token/secret/api_key）
- ENABLE_DEDUPLICATION：去重开关（默认 true）
- ENABLE_MASKING：脱敏开关（默认 true）

## 4. 运行命令模板

### 本地单次执行
```bash
# 规则诊断
python -m nebula_copilot.cli agent-analyze <trace_id> --source data/mock_trace.json

# 启用 LLM
python -m nebula_copilot.cli agent-analyze <trace_id> --source data/mock_trace.json --llm-enabled

# 启用向量增强（Phase 2-4）
python -m nebula_copilot.cli agent-analyze <trace_id> --source data/mock_trace.json --with-vector
```

### 监控与查询
```bash
# 查看最近失败任务
python -m nebula_copilot.cli query-runs --status failed --limit 50

# 查看降级任务
python -m nebula_copilot.cli query-runs --status degraded --limit 50

# 检查诊断指标（周报）
python -c "from nebula_copilot.evaluation import MetricsCollector; c = MetricsCollector(); print(c.get_metrics())"
```

### 向量库维护
```bash
# 构建历史向量索引
python scripts/build_history_index.py --runs-file data/agent_runs.json --validate

# 重新索引源码白名单
python -m nebula_copilot.cli rebuild-code-whitelist --dirs src

# 清理过期诊断记录
python -c "from nebula_copilot.evaluation import DataGovernance; g = DataGovernance(); g.cleanup_old_records(days=90)"
```

## 5. 升级策略

- 先在 M4 分支完成模块验收
- 观察 24 小时失败率与降级率
- 检查向量库性能与召回质量
- 达标后再合并主干

## 6. PR 合并自动化

### 6.1 按 PR 号合并

- 运行：
  ```bash
  bash scripts/merge_pr.sh <pr_number> [labels] [merge_method]
  ```
- 示例：
  ```bash
  bash scripts/merge_pr.sh 1
  bash scripts/merge_pr.sh 1 "automerge,needs-release-note" rebase
  ```

脚本会自动执行：
- 检查最新 workflow run 状态（`gh run list`）
- 给 PR 添加标签
- 执行合并并删除分支（`gh pr merge`）

### 6.2 按当前分支自动定位 PR 并合并

- 运行：
  ```bash
  bash scripts/merge_current_pr.sh [labels] [merge_method]
  ```
- 示例：
  ```bash
  bash scripts/merge_current_pr.sh
  bash scripts/merge_current_pr.sh "automerge,ci-passed" squash
  ```

脚本会先读取当前 git 分支，再自动查找该分支对应的 OPEN PR，随后复用 `merge_pr.sh` 执行完整流程。

### 6.3 可选环境变量

- `RUN_LIMIT`：检查 workflow run 数量，默认 20
- `WAIT_SECONDS`：轮询间隔秒数，默认 20
- `MAX_WAIT_SECONDS`：CI 最长等待秒数，默认 1800
- `ALLOW_NO_RUNS`：设为 1 时，分支无 run 记录也允许继续

## 7. 监控告警

### 关键指标
- 诊断成功率：> 98%（低于 90% 告警）
- 平均诊断延迟：< 100ms（高于 500ms 告警）
- 向量检索命中率：> 80%（低于 70% 告警）
- LLM 调用成功率：> 95%（低于 85% 告警）

### 告警规则建议
```yaml
- name: DiagnosisFailureRate
  threshold: 0.10  # 失败率 > 10%
  duration: 5m

- name: VectorSearchLatency
  threshold: 200ms  # 向量检索延迟 > 200ms
  duration: 5m

- name: DedupHitRate
  threshold: 0.95  # 去重命中率 > 95%
  alert: 低于 90% 时告警
```
