# Nebula Copilot 运维手册

## 1. 日常巡检

- 检查最近运行记录：
  python -m nebula_copilot.cli query-runs --runs-path data/agent_runs.json --limit 20
- 关注状态分布：ok、degraded、deduped、rate_limited、failed
- 检查通知去重缓存文件是否持续增长：data/notify_dedupe.json

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

## 3. 关键配置项

- GH_MODELS_API_KEY：模型密钥
- LLM_ENABLED：LLM 开关
- RUN_DEDUPE_WINDOW_SECONDS：去重窗口
- RUN_RATE_LIMIT_PER_MINUTE：每分钟限流
- METRICS_ENABLED：观测开关

## 4. 运行命令模板

本地单次执行：
python -m nebula_copilot.cli agent-analyze <trace_id> --source data/mock_trace.json --llm-enabled

查看最近失败任务：
python -m nebula_copilot.cli query-runs --status failed --limit 50

查看降级任务：
python -m nebula_copilot.cli query-runs --status degraded --limit 50

## 5. 升级策略

- 先在 M4 分支完成模块验收
- 观察 24 小时失败率与降级率
- 达标后再合并主干
