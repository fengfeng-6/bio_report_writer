# 云主机部署模块

该模块面向当前 Dify 1.16.1 社区版主机，并且**只用于实现自检**。它复用主机已经
缓存的、按摘要锁定的 `python:3.12-slim`，只挂载超算生成的确定性发布包及包内合成
工作簿，不上传或挂载任何真实项目数据。正式报告、DOCX 和 PDF 始终由超算 Slurm Job
生成。

运行时配置必须保存在仓库之外且权限设为 `0600`。部署前将
`runtime.env.example` 复制到服务器私有配置目录，替换三个值：解压后的发布目录、
长随机服务令牌。令牌不得作为命令行参数或 URL 查询参数传入。

部署命令使用独立 Compose 项目，但接入现有 Dify 的 `docker_default` 外部网络：

```bash
docker compose --project-name eco-report \
  --env-file /opt/eco-report-agent/config/runtime.env \
  -f /opt/eco-report-agent/current/deploy/cloud/docker-compose.eco-report.yml \
  config --quiet

docker compose --project-name eco-report \
  --env-file /opt/eco-report-agent/config/runtime.env \
  -f /opt/eco-report-agent/current/deploy/cloud/docker-compose.eco-report.yml \
  up -d --no-build --pull never
```

服务不发布宿主机端口，只能通过容器网络名 `eco-report-smoke-api:8000` 访问。健康检查
不需要令牌；报告接口只接受 `X-Report-Service-Token`。固定 smoke 请求为
`project_type=coal&fid=900001&target_year=2025`。该服务不得作为正式报告后端；DOCX 只作
接口兼容测试，PDF 固定关闭。

## Dify SSRF 最小白名单

Dify 1.16.1 默认阻止 HTTP 请求节点访问 Docker 私网。`docker-compose.dify-ssrf.yml`
仅将 `eco-report-smoke-api` 加入 `ssrf_proxy` 的私网域名白名单，不放行其他容器或私网
网段。使用当前 `dify-native` 部署时，先校验合并配置，再只重建代理容器：

```bash
install -m 0644 \
  /opt/eco-report-agent/current/deploy/cloud/docker-compose.dify-ssrf.yml \
  /root/dify-native/source/rag_workspace/deploy/dify-native/docker-compose.eco-report.override.yml

docker compose --env-file /root/dify-native/config/native.env \
  -f /root/dify/docker/docker-compose.yaml \
  -f /root/dify-native/source/rag_workspace/deploy/dify-native/docker-compose.override.yml \
  -f /root/dify-native/source/rag_workspace/deploy/dify-native/docker-compose.eco-report.override.yml \
  config --quiet

docker compose --env-file /root/dify-native/config/native.env \
  -f /root/dify/docker/docker-compose.yaml \
  -f /root/dify-native/source/rag_workspace/deploy/dify-native/docker-compose.override.yml \
  -f /root/dify-native/source/rag_workspace/deploy/dify-native/docker-compose.eco-report.override.yml \
  up -d --no-deps --force-recreate ssrf_proxy
```

以后若重建 Dify Compose 项目，必须继续包含该覆盖文件，或在等价配置中保留
`SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=eco-report-smoke-api`；否则 Dify 自检工作流会再次被
SSRF 防护拦截。

从 Dify API 容器复查完整接口边界时，可在主机加载私有环境文件后，将探针通过标准输入
执行；探针只打印状态码、内容类型和字节数，不打印令牌或报告正文：

```bash
set -a
. /opt/eco-report-agent/config/runtime.env
set +a
docker exec -i -e ECO_REPORT_SERVICE_TOKEN docker-api-1 python - \
  < /opt/eco-report-agent/current/deploy/cloud/smoke_probe.py
```
