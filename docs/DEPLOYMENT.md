# Nebula Copilot 部署手册

## 1. 部署目标

提供多种部署方式：
- Docker Compose：适合本地联调与预发环境
- Kubernetes：适合线上集群部署（推荐）
- 本地开发环境：用于调试与测试

## 2. 前置准备

- Python 3.11+ 项目代码已拉取
- 已准备 GH 模型密钥，并写入环境变量 GH_MODELS_API_KEY
- 本地或集群可访问模型网关地址
- 可选：Elasticsearch 8.10+ 接入生产数据源

## 3. Docker Compose 部署

### 3.1 准备环境变量

在项目根目录创建 `.env`，至少包含：

```bash
# LLM 配置
GH_MODELS_API_KEY=<your-key>
LLM_ENABLED=true
LLM_PROVIDER=github
LLM_MODEL=gpt-4.1-mini
LLM_TIMEOUT_MS=8000
LLM_MAX_RETRY=2
LLM_REPORT_POLISH_ENABLED=true

# 诊断优化
RUN_DEDUPE_WINDOW_SECONDS=300
RUN_RATE_LIMIT_PER_MINUTE=60
METRICS_ENABLED=true

# 向量库配置（Phase 2-4）
VECTOR_MIN_SCORE=0.2
DATA_RETENTION_DAYS=90
ENABLE_DEDUPLICATION=true
ENABLE_MASKING=true

# Elasticsearch（可选，用于生产数据源）
NEBULA_ES_URL=https://your-es-host:9200
NEBULA_ES_USERNAME=your_user
NEBULA_ES_PASSWORD=your_password
```

### 3.2 启动

```bash
docker compose up -d --build
```

### 3.3 验证

```bash
# 检查容器状态
docker compose ps

# 查看日志
docker compose logs -f nebula-copilot

# 执行命令验证
docker compose run --rm nebula-copilot --help

# 测试诊断功能
docker compose exec nebula-copilot \
  python -m nebula_copilot.cli seed demo --scenario timeout
docker compose exec nebula-copilot \
  python -m nebula_copilot.cli analyze demo --source data/mock_trace.json --format rich
```

## 4. Kubernetes 部署

### 4.1 创建密钥

先替换 `deploy/k8s/secret.example.yaml` 中的实际密钥值，然后：

```bash
kubectl apply -f deploy/k8s/secret.example.yaml
```

或通过 CI/CD 动态注入：

```bash
kubectl create secret generic nebula-secret \
  --from-literal=GH_MODELS_API_KEY=$GH_MODELS_API_KEY \
  --from-literal=NEBULA_ES_USERNAME=$NEBULA_ES_USERNAME \
  --from-literal=NEBULA_ES_PASSWORD=$NEBULA_ES_PASSWORD
```

### 4.2 应用部署

```bash
kubectl apply -f deploy/k8s/deployment.yaml
```

### 4.3 验证

```bash
# 查看 Pod 状态
kubectl get pods -l app=nebula-copilot

# 查看日志
kubectl logs deploy/nebula-copilot -f

# 健康检查
kubectl get pod -l app=nebula-copilot -o jsonpath='{.items[0].status.containerStatuses[0].ready}'

# 进入容器测试
kubectl exec -it $(kubectl get pod -l app=nebula-copilot -o jsonpath='{.items[0].metadata.name}') -- bash
```

### 4.4 扩展与监控

```bash
# 自动扩展配置（HPA）
kubectl apply -f - <<EOF
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: nebula-copilot-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: nebula-copilot
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
EOF

# 查看 Pod 分布
kubectl get pods -l app=nebula-copilot -o wide
```

## 5. 本地开发部署

```bash
# 安装依赖
python -m pip install -e ".[dev]"

# 运行单元测试（115+ 测试）
python -m pytest -q

# 启动 Web Dashboard
nebula-web --host 0.0.0.0 --port 8080
# 访问 http://127.0.0.1:8080/dashboard

# 启动 CLI 模式
python -m nebula_copilot.cli seed demo --scenario timeout
python -m nebula_copilot.cli analyze demo --format rich
```

## 6. 生产环境检查清单

- [ ] GH_MODELS_API_KEY 已安全注入，不在代码中
- [ ] Elasticsearch 数据源已连接并测试
- [ ] 敏感信息脱敏规则已配置
- [ ] 向量库索引已构建：`scripts/build_history_index.py` 
- [ ] 源码白名单已配置到 CodeWhitelistStore
- [ ] 监控告警已配置：诊断成功率、延迟、向量命中率
- [ ] 日志收集已接入：ELK/Loki/CloudWatch
- [ ] 备份策略已制定：data/agent_runs.json / 向量索引定期备份
- [ ] 容量规划已完成：并发诊断能力评估

## 7. 回滚

### Docker Compose
```bash
# 回滚到上一个镜像标签
docker compose down
docker pull nebula-copilot:previous-tag
docker compose up -d
```

### Kubernetes
```bash
# 查看部署历史
kubectl rollout history deployment/nebula-copilot

# 回滚到上一版本
kubectl rollout undo deployment/nebula-copilot

# 回滚到特定版本
kubectl rollout undo deployment/nebula-copilot --to-revision=3
```

## 8. 安全建议

- 不要将 `.env` 提交到仓库（已在 `.gitignore` 中）
- GH_MODELS_API_KEY 必须定期轮换（建议 90 天）
- 生产建议使用独立密钥和最小权限策略
- 启用 RBAC：K8s ServiceAccount 只赋予必要权限
- 网络隔离：诊断服务不应直接暴露到互联网
- 日志脱敏：输出日志中应自动脱敏敏感信息

## 9. 性能优化

### 向量库优化（Phase 2-4）
- 向量最小相似度阈值调整：0.1（宽松）~ 0.5（严格）
- 历史案例 Top-K 值：默认 5，可根据延迟调整
- 源码片段检索范围：限制在白名单目录（减少扫描量）

### LLM 调用优化
- 启用 LLM 缓存：相同问题避免重复调用
- 异步 LLM 推理：不阻塞主诊断路径
- 降级策略：LLM 超时 > 3000ms 后自动降级到规则诊断

### 资源优化
- CPU：单个诊断约 50-100ms CPU，建议 2+ cores
- 内存：100K 历史案例约 200MB，建议 512MB+
- 存储：日志与索引每月增长 ~50MB，建议 monthly rotation
