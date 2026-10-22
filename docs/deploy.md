# 部署到网站服务器

目标目录 `/opt/elite-hub/`（与本仓库结构一致）。不覆盖任何现有服务和数据。

## 1. 检查端口与代码

```bash
ss -ltnp | grep -E ':18889|:221[0-2][0-9]' || echo free
git clone <本仓库> /opt/elite-hub
```

## 2. 安装依赖

```bash
cd /opt/elite-hub/services/hub-api
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. CPU 沙箱镜像（Elite Power 真实 CPU Demo）

```bash
docker build -t elite-hub/cpu-python:latest /opt/elite-hub/services/hub-api/cpu-image
```

容器限 1–2 核、2–4 GiB、256 进程；SSH 端口 22100–22120，需在云防火墙放行。
这仍是单机 Demo，不等于具备公网多人隔离的算力平台。

## 4. 配置与账户

```bash
mkdir -p /opt/elite-hub/data
cp /opt/elite-hub/deploy/elite-hub.env.example /etc/elite-hub/hub.env   # 按需修改
set -a; . /etc/elite-hub/hub.env; set +a
cd /opt/elite-hub/services/hub-api
.venv/bin/python manage.py add-user admin1 "管理员" --tier elite --admin
.venv/bin/python manage.py add-user stu_a "测试同学A" --tier elite
.venv/bin/python manage.py add-user stu_b "测试同学B"
```

## 5. New API：千问渠道与个人 Key

1. 先记录 `yuejie-new-api` 当前镜像版本，备份 `/root/one-api/data`。
2. New API 管理后台新增渠道：类型选阿里/OpenAI 兼容，Key 取 `/etc/elite-hub/dashscope.env` 的 `DASHSCOPE_API_KEY`，
   Base URL 取 `DASHSCOPE_BASE_URL`。确认最终请求恰为 `.../compatible-mode/v1/chat/completions`（不要出现 `/v1/v1`）。
3. 为 stu_a、stu_b 建两个独立 New API 用户，各自设置用户额度，再各建一个令牌（Key），并限制可用模型。
4. 把令牌绑定到 Hub 账户（Key 只存在服务端）：

```bash
.venv/bin/python manage.py set-key stu_a sk-...
.venv/bin/python manage.py set-key stu_b sk-...
```

## 6. 启动与接入 Nginx

```bash
cp /opt/elite-hub/deploy/elite-hub.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now elite-hub
# 把 deploy/nginx-hub.conf 内容加入 /etc/nginx/nginx.conf 的 xjtuaep.com HTTPS server
/usr/sbin/nginx -t && /usr/sbin/nginx -s reload
```

## 7. 官网入口

`https://xjtuaep.com/admin/` →“导航与入口”：把“算力平台”改为 `Elite Hub`，链接 `/hub/`，应用部署。

## 8. 验收清单

- [ ] stu_a、stu_b 分别登录，各自对话互不可见；刷新后历史仍在
- [ ] 网页对话与 `curl $ELITE_HUB_BASE_URL/chat/completions` 都扣减同一个人的额度
- [ ] 图片理解：逐个模型测试图片输入
- [ ] stu_a 申请 CPU → admin1 审批 → 获得 SSH → 运行 `python -c "import numpy"` → 停止 → 时长流水退回
- [ ] 申请短时长，等待到期自动停止
- [ ] GPU 卡片显示“模拟流程”标记
