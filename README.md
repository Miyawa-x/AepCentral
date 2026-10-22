# Elite Hub

越杰计划 Elite Hub：一个入口，两类资源——**AI 模型服务**（网页对话 + 个人 API Key）与 **Elite Power 计算服务**（CPU 容器 + SSH，GPU 暂为模拟）。

```text
越杰官网 /hub/  ──→  portal/（前端，无构建步骤）
                     services/hub-api/（FastAPI，127.0.0.1:18889）
                        ├─ 登录 / 会话：user → conversations → messages（按所有者鉴权）
                        ├─ 对话：用当前用户的个人 Key 流式转发 New API
                        ├─ 额度：New API 令牌额度 + 计算时长流水（分开记账）
                        └─ Elite Power：申请 → 审批 → Docker CPU 容器 → 到期回收
/hub/v1/  ──→  New API 127.0.0.1:12221/v1/（同学代码直接用个人 Key）
```

## MVP 已实现

- 服务端会话登录；前端从不传 `user_id`，所有会话 / 实例按所有者校验
- 对话：历史列表、新建 / 改名 / 删除、流式输出、停止、重新生成、模型切换、图片附件；上下文只取当前会话并按长度截断
- API 与额度：个人 Key（脱敏显示）、可用模型、模型额度、计算时长流水、代码示例
- Elite Power：规格卡片、申请预留时长、管理员审批（幂等）、开通失败自动退回、按实际分钟扣费、停止 / 到期自动回收；执行层 `create/stop` 可替换为 AutoDL
- GPU 规格明确标注为模拟流程

## 本地运行

```bash
cd services/hub-api
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export HUB_DEMO_ECHO=1 HUB_COMPUTE_MODE=mock HUB_COOKIE_SECURE=0
.venv/bin/python manage.py add-user alice Alice --tier elite --admin
.venv/bin/uvicorn app.main:app --port 18889
# 打开 http://127.0.0.1:18889/hub/
```

服务器部署见 [docs/deploy.md](docs/deploy.md)。

## 安全约定

- 千问上游 Key 只在 `/etc/elite-hub/dashscope.env`，由 New API 渠道使用；不进前端、不进仓库
- 个人 Key 只保存在 Hub 服务端数据库；`*.env`、`*.db` 已被 `.gitignore` 排除
- 服务器密码、后台账号等不写入本仓库

## 目录

| 目录 | 内容 |
| --- | --- |
| `portal/` | Hub 首页、聊天、API 与额度、算力、审批页面 |
| `services/hub-api/` | 业务服务、管理 CLI、CPU 沙箱镜像 |
| `upstream/` | 需二次开发时检出的 New API / LibreChat 固定版本 |
| `deploy/` | systemd、Nginx、环境变量示例 |
| `docs/` | 部署与验收说明 |
