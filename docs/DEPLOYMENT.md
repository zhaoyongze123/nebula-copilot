# Nebula Copilot 部署手册

## 1. 部署目标

提供两类部署方式：
- Docker Compose：适合本地联调与预发环境
- Kubernetes：适合线上集群部署

## 2. 前置准备

- Python 项目代码已拉取
- 已准备 GH 模型密钥，并写入环境变量 GH_MODELS_API_KEY
- 本地或集群可访问模型网关地址

## 3. Docker Compose 部署

### 3.1 准备环境变量

在项目根目录创建 .env，至少包含：

GH_MODELS_API_KEY=<your-key>
LLM_ENABLED=true
LLM_PROVIDER=github
LLM_MODEL=gpt-4.1-mini
LLM_TIMEOUT_MS=8000
LLM_MAX_RETRY=2
LLM_REPORT_POLISH_ENABLED=true
RUN_DEDUPE_WINDOW_SECONDS=300
RUN_RATE_LIMIT_PER_MINUTE=60
METRICS_ENABLED=true

### 3.2 启动

docker compose up -d --build

### 3.3 验证

- 检查容器状态：docker compose ps
- 查看日志：docker compose logs -f nebula-copilot
- 执行命令验证：
  docker compose run --rm nebula-copilot --help

## 4. Kubernetes 部署

### 4.1 创建密钥

kubectl apply -f deploy/k8s/secret.example.yaml

注意：实际使用时请先替换 GH_MODELS_API_KEY 内容，或通过 CI/CD 动态注入。

### 4.2 应用部署

kubectl apply -f deploy/k8s/deployment.yaml

### 4.3 验证

- 查看 Pod：kubectl get pods -l app=nebula-copilot
- 查看日志：kubectl logs deploy/nebula-copilot

## 5. 回滚

- Docker Compose：回滚到上一个镜像标签并重启服务
- Kubernetes：kubectl rollout undo deployment/nebula-copilot

## 6. 安全建议

- 不要将 .env 提交到仓库
- GH_MODELS_API_KEY 必须定期轮换
- 生产建议使用独立密钥和最小权限策略
