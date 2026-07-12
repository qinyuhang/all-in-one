# 分节点规划说明（首个分节点：NODE_1）

## 目标

新增一台 VPS，作为现有 3x-ui 的首个分节点 `NODE_1`。当前主 VPS 继续作为 `MASTER`，承担主面板和主生产节点职责。

第一阶段 `NODE_1` 必须同时提供两件事：

- 作为 3x-ui 节点，通过现有 `MASTER` 面板里的 `Nodes` 功能接入。
- 部署公开博客镜像，内容和 `MASTER` 保持一致，并直接作为 active mirror 对外服务。

## 已确认决策

- 新增 `docker-compose.node.yml`，不直接复用完整 `MASTER` `docker-compose.yml`。
- `docker-compose.node.yml` 只部署 `3x-ui` 和 `caddy`，去掉 `openclaw` 和 `open-webui`。
- `caddy/Caddyfile.node` 参考现有 `caddy/Caddyfile`，删除 `claw.*` 和 `claw-ui.*` 两段。
- 分节点域名全部进入 `.env`，优先使用 `NODE_1_DOMAIN`。
- Caddy 域名使用 `{$NODE_1_DOMAIN}`、`admin.{$NODE_1_DOMAIN}`、`sub.{$NODE_1_DOMAIN}` 这类写法，尽量贴近 `MASTER` 的 Caddyfile。
- `MASTER` 到 `NODE_1` 的 3x-ui Node API 先使用 HTTPS。
- 博客仓库会新增 `NODE_1` webhook，让 release 发布后同时通知 `MASTER` 和 `NODE_1`。
- `NODE_1` 的 Tailscale 必须设置 `--accept-dns=false`，避免 Tailscale 接管或破坏宿主机 `/etc/resolv.conf`。
- `NODE_1` 需要在 Tailnet 中宣告为 exit node，但客户端是否允许使用该出口由 Tailscale admin console 和 ACL/grants 控制。
- `NODE_1` 上线前需要做宿主机网络优化，包括 BBR、队列调度、转发、连接队列、文件句柄和基础观测。
- DNS 解析已经完成，`NODE_1` 上线后直接作为 active mirror，而不是先做隐藏验证节点。

## 当前 MASTER 基线

现有 `docker-compose.yml` 主要运行这些服务：

- `3x-ui`
  - 镜像：`ghcr.io/mhsanaei/3x-ui:latest`
  - 公开端口：`443/tcp` 和 `443/udp`
  - 持久化数据：`./3x-ui/db:/etc/x-ui` 和 `./3x-ui/config:/etc/xray`
- `caddy`
  - 镜像：`ghcr.io/qinyuhang/caddy-cloudflare:latest`
  - 负责转发 admin、subscription、AI 服务和 blog。
  - 挂载 `/srv/blog:/srv/blog:ro`
  - 当前使用 `MY_DOMAIN` 拼出 `admin.{$MY_DOMAIN}`、`sub.{$MY_DOMAIN}` 和 `{$MY_DOMAIN}`。
- `openclaw` 和 `open-webui`
  - 目前只属于 `MASTER` 节点，不在 `NODE_1` 第一阶段部署。

博客发布流程不在 Docker 里构建，而是 VPS 本机的 systemd 流程：

- GitHub Actions 构建静态博客。
- Action 发布不可变 GitHub Release，包含 `blog.tar.gz` 和 `blog.tar.gz.sha256`。
- Action 调用 VPS webhook。
- VPS 校验 HMAC 签名，通过后由 systemd 触发 `deploy-blog-release`。
- `deploy-blog-release` 下载最新 release、校验 checksum、解压，并原子切换 `/srv/blog/current`。

所以 `NODE_1` 不需要自己构建博客。`NODE_1` 应该拉取和 `MASTER` 同一份 GitHub Release artifact。

## 节点清单

节点编号是稳定身份，地理位置只是属性。后续审计、`.env`、workflow 和日志都应该优先使用稳定身份，而不是地理位置。

- `MASTER`：当前地理位置为美国洛杉矶，角色为主面板和主生产节点。
- `NODE_1`：当前地理位置为日本，角色为首个 3x-ui 分节点和博客 active mirror。
- `NODE_2`、`NODE_3`：预留给后续分节点。

编号一经分配不因地域、供应商或用途变化而复用。比如 `NODE_1` 未来更换供应商或迁移地区，仍然叫 `NODE_1`，只更新节点清单里的属性。

## 环境变量设计

分节点变量集中放进 `.env` 或 `.env.node`，Compose 和 Caddyfile 都从环境变量读取。第一阶段建议使用：

- `TZ`
- `NODE_1_DOMAIN`
- `ACME_EMAIL`
- `CF_API_TOKEN`

Caddyfile 中不再硬编码节点域名：

```caddyfile
admin.{$NODE_1_DOMAIN}:8443 {
    handle /_hooks/blog {
        reverse_proxy unix//srv/blog/.webhook/blog.sock
    }

    handle {
        reverse_proxy 3x-ui:2053
    }
}

sub.{$NODE_1_DOMAIN}:8443 {
    reverse_proxy 3x-ui:2096
}

{$NODE_1_DOMAIN}:8443 {
    root * /srv/blog/current
    encode zstd gzip
    file_server
}
```

这样 `NODE_2` 以后可以复用同一套模板，只需要换成对应的 `.env` 内容，或者后续把变量泛化为 `NODE_DOMAIN`。

## 推荐形态

新增这些文件：

- `docker-compose.node.yml`
- `caddy/Caddyfile.node`
- `.env.node.example`
- `scripts/bootstrap_node_host.sh`

`docker-compose.node.yml` 保留 `MASTER` compose 的基础结构：

- `x-logging`
- `3x-ui`
- `caddy`
- `edge-net`
- `app-net`

同时删除这些只属于 `MASTER` 的服务和配置：

- `openclaw`
- `open-webui`
- `KIMI_API_KEY`
- `OPENCLAW_BASE_URL`
- `WEBUI_SECRET_KEY`
- `claw.{$...}` 和 `claw-ui.{$...}` 的 Caddy 路由

`caddy` 继续挂载 `/srv/blog:/srv/blog:ro`，继续使用 Cloudflare DNS challenge 申请证书。

Tailscale 不放进 Compose，直接安装在 `NODE_1` 宿主机上。这样它管理宿主机的 Tailnet 接口和路由，3x-ui 仍只管理自己的容器网络命名空间。

`NODE_1` 主机还需要安装现有博客 systemd puller：

- `scripts/install_blog_puller.sh`
- `deploy/systemd/blog-webhook.service`
- `deploy/systemd/blog-deploy.service`
- `deploy/systemd/blog-deploy.path`

## NODE_1 目标拓扑

公网流量：

- `443/tcp` 和 `443/udp` 进入 `3x-ui` 容器里的 Xray。
- Caddy 监听 `8443`。
- Xray fallback 可以把普通 HTTPS 流量转发到 `caddy.internal:8443`。

DNS：

- `{$NODE_1_DOMAIN}`：`NODE_1` 的公开 Xray/blog active mirror 入口。
- `admin.{$NODE_1_DOMAIN}`：`NODE_1` 的 3x-ui node panel API 和 blog webhook 入口。
- `sub.{$NODE_1_DOMAIN}`：`NODE_1` 的订阅入口，第一阶段按现有 `MASTER` Caddyfile 结构保留。

如果这些域名会用于 Xray/REALITY 流量，Cloudflare 记录应保持 DNS-only。

## 博客要求

`NODE_1` 必须从 `/srv/blog/current` 提供和 `MASTER` 一样的博客内容，并直接作为 active mirror。

实现方向：

- 完全复用 `/srv/blog` 目录结构。
- 复用 `scripts/deploy_blog_release.py`。
- 复用 `qinyuhang/qinyuhang.github.io` 的同一份 GitHub Release artifact。
- `NODE_1` 单独安装 webhook secret。
- 博客仓库新增 `NODE_1` webhook，和 `MASTER` 一样由 GitHub Actions release trigger 触发部署。

博客仓库发布 release 后，应该同时通知两个 endpoint：

- `MASTER`：现有 webhook endpoint。
- `NODE_1`：`https://admin.{$NODE_1_DOMAIN}:8443/_hooks/blog`，或最终 HTTPS 入口对应的实际 URL。

GitHub Secrets 建议使用稳定编号命名：

- `BLOG_DEPLOY_WEBHOOK_URL_MASTER`
- `BLOG_DEPLOY_WEBHOOK_SECRET_MASTER`
- `BLOG_DEPLOY_WEBHOOK_URL_NODE_1`
- `BLOG_DEPLOY_WEBHOOK_SECRET_NODE_1`

host-side systemd timer 可以作为可选兜底，但不作为第一阶段主路径。主路径是 GitHub Actions 同时调用 `MASTER` 和 `NODE_1` webhook。

暂不采用单个 `BLOG_DEPLOY_WEBHOOKS` JSON Secret。它会把多个节点的 URL 和 secret 聚合在一起，使单节点轮换、权限隔离和审计变得困难。

## 3x-ui 多节点计划

`MASTER` 继续作为主面板。

`NODE_1` 运行第二个 3x-ui 实例，并使用自己的本地数据库。不要跨 VPS 共享 `MASTER` 的 SQLite 数据库，也不建议为了第一阶段引入 PostgreSQL。

`NODE_1` 上需要做：

- 部署轻量 node compose。
- 私密地完成分节点 panel bootstrap。理想情况下，初始配置时只把 panel 绑定到 localhost，然后通过 SSH tunnel 访问。
- 在 `NODE_1` panel 上生成或启用 API token。
- 配置 `NODE_1` inbound、REALITY keys、fallback 和 Caddy upstream。

`MASTER` 面板上需要做：

- 打开 `Nodes`。
- 添加 `NODE_1` 节点，字段大致为：
  - Scheme：`https`
  - Address：`admin.{$NODE_1_DOMAIN}` 对应的实际域名。
  - Port：`443` 或 `8443`，取决于 API 流量是走 Xray fallback，还是直接暴露 Caddy。
  - Base path：`NODE_1` panel 的 base path。
  - API token：在 `NODE_1` 上生成。
  - TLS verification：如果证书链正常，建议开启。
  - Private address allowance：仅为通过 Tailscale 接入的节点明确开启，并用 Tailnet ACL/grants 限制来源。

第一版偏好的方案：

- `MASTER` 和 `NODE_1` 宿主机都加入同一个 Tailnet。
- 3x-ui node API 先使用 HTTPS。
- 如果走公网 HTTPS，确保 `admin.{$NODE_1_DOMAIN}` 只有必要路径暴露，并使用强路径、强凭据和 API token。
- 如果改为 Tailnet-only HTTPS，则让 Caddy 在 `NODE_1` 的 Tailscale IP 上提供 HTTPS，再反向代理到 `3x-ui:2053`。
- 博客和 Xray 入口继续面向公网；博客 webhook 如果继续使用 webhook，则暂时保持公网可达，因为 GitHub-hosted Actions 默认不在 Tailnet 内。

## Tailscale 设计与权限边界

### 部署方式

- Tailscale 安装在 `MASTER`、`NODE_1` 两台 VPS 的宿主机上，由 systemd 管理。
- `NODE_1` 加入 Tailnet 时必须使用 `--accept-dns=false`。
- 不在 `docker-compose.node.yml` 中运行 Tailscale 容器。
- 不给 Tailscale 使用 Docker 的 `network_mode: host`、`privileged: true` 或共享 3x-ui 网络命名空间。
- `NODE_1` 需要宣告为 exit node。
- `NODE_1` 第一阶段不发布 subnet routes，也不接受外部 routes，除非后续有明确路由需求并单独验证。

推荐初始化命令形态：

```bash
tailscale up --advertise-exit-node --accept-dns=false
```

如果使用 auth key、tag 或 hostname，则在同一个命令里追加相应参数，但保留 `--advertise-exit-node` 和 `--accept-dns=false`。`--accept-dns=false` 是硬要求，因为之前 `MASTER` 遇到过 Tailscale 修改 `/etc/resolv.conf` 导致 DNS 异常的问题；分节点不需要让 Tailscale 接管系统 DNS。

### Exit Node 设计

`NODE_1` 会宣告为 Tailnet exit node，但这不等于默认让所有 Tailnet 设备都使用它。

需要完成：

- 在 `NODE_1` 宿主机开启 IPv4/IPv6 转发。
- 使用 `tailscale up --advertise-exit-node --accept-dns=false` 宣告 exit node。
- 在 Tailscale admin console 中批准 `NODE_1` 作为 exit node。
- 用 ACL/grants 控制哪些用户或设备可以使用 `NODE_1` exit node。
- `NODE_1` 自身不使用其他 exit node，也不启用 `--accept-routes`。

需要注意：

- exit node 会让被允许的 Tailnet 客户端把默认出口流量经由 `NODE_1` 转发，会增加 `NODE_1` 的带宽、连接数和防火墙压力。
- exit node 不应该影响 Docker 容器自己的 Xray 入站监听，但会增加宿主机路由和 NAT 复杂度，部署后必须验证 3x-ui、Caddy、博客 webhook 和 Xray fallback 都仍然正常。
- `--accept-dns=false` 仍然必须保留；宣告 exit node 不要求让 Tailscale 接管 `NODE_1` 的 DNS。

### 与 3x-ui 的 `NET_ADMIN` 关系

当前 Compose 给 3x-ui 容器授予：

- `NET_ADMIN`
- `NET_RAW`

这些 capability 默认只在 3x-ui 容器自己的网络命名空间中生效。宿主机上的 Tailscale 在宿主机网络命名空间创建 `tailscale0`，并管理相关路由及防火墙规则。因此两者不是在争抢一个全局的 `NET_ADMIN`，按本方案通常可以共存。

需要避免的配置：

- 不把 3x-ui 改成 `network_mode: host`。
- 不给 3x-ui 或 Tailscale 容器设置 `privileged: true`。
- 不让 3x-ui 容器直接操作宿主机的 `tailscale0`。
- 不启用 Tailscale subnet router 或 `--accept-routes`，除非有明确路由需求并单独验证。
- 宣告 exit node 后，不允许 Tailnet 全员默认可用；必须通过 Tailscale admin console 和 ACL/grants 限制使用范围。
- 不启用 Tailscale DNS 接管；`--accept-dns=false` 必须持久化。
- 不手工清空 Docker 或 Tailscale 创建的 iptables/nftables chain。

主要风险不是 capability 冲突，而是 Docker、UFW 和 Tailscale 都可能写入宿主机的 iptables/nftables；exit node 还会引入默认路由转发和 NAT；另外 Tailscale DNS 设置可能影响 `/etc/resolv.conf`。部署后需要验证 Docker 端口映射、Tailnet 连通性、exit node 出口、DNS 解析和重启后的规则持久性。

### 3x-ui Node API 的访问方式

当前决策是先使用 HTTPS。待实现时需要在以下两种 HTTPS 形态里选定一种：

1. 公网 HTTPS：`MASTER` 通过 `https://admin.{$NODE_1_DOMAIN}` 访问 `NODE_1` node API。
2. Tailnet-only HTTPS：`MASTER` 通过 `NODE_1` 的 Tailscale 地址或 Tailnet DNS 名访问 Caddy，再由 Caddy 反代到 `3x-ui:2053`。

公网 HTTPS 更接近现有 `MASTER` Caddyfile，部署简单；Tailnet-only HTTPS 暴露面更小，但证书、监听地址和 3x-ui Nodes 字段需要实测。无论哪种方式，都应开启 TLS verification，除非 3x-ui Nodes 的实现不支持对应证书链。

建议为两台 VPS 使用 Tailscale tag（例如 `tag:infra-node`），通过 ACL/grants 只允许 `MASTER` 访问 `NODE_1` 的管理端口和 SSH。不要让整个 Tailnet 默认访问 node API。

Tailscale auth key 应作为主机初始化 secret 管理，不写入 Git、`.env` 或 Compose。节点加入成功后，应启用合适的 key expiry 策略，并保留公网 SSH 作为初期故障恢复通道；确认 Tailnet 稳定后再收紧公网 SSH。

## 宿主机网络优化

`NODE_1` 同时承担 Xray 分节点、博客 active mirror 和 Tailscale exit node，因此宿主机网络参数需要作为部署的一部分处理，不建议依赖 VPS 默认值。

### 必做项

- 启用 BBR 拥塞控制。
- 使用 `fq` 作为默认队列调度。
- 开启 IPv4/IPv6 转发，满足 Tailscale exit node 需要。
- 提高 TCP listen backlog 和 accept queue，避免高并发握手时排队过小。
- 提高本机 ephemeral port 范围，降低大量出站连接时端口耗尽概率。
- 提高文件句柄限制，覆盖 3x-ui、Caddy、Tailscale 和 systemd 服务。
- 保留 Docker、Tailscale 自己创建的 iptables/nftables 规则，不使用一键脚本清空防火墙。

建议 sysctl 目标：

```conf
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_mtu_probing = 1
```

其中 BBR 和 `fq` 是明确要做的；backlog、port range、fastopen、MTU probing 属于低风险基础优化，但仍需要在部署后观察连接错误和内核日志。

文件句柄建议：

- systemd 全局或服务级 `LimitNOFILE` 至少设置为 `1048576`。
- 验证 `3x-ui`、`caddy`、`tailscaled` 的实际 limits，而不是只看配置文件。

### 验证命令

部署后需要确认：

```bash
sysctl net.ipv4.tcp_congestion_control
sysctl net.core.default_qdisc
sysctl net.ipv4.ip_forward
sysctl net.ipv6.conf.all.forwarding
ulimit -n
systemctl show tailscaled --property=LimitNOFILE
```

预期：

- `net.ipv4.tcp_congestion_control = bbr`
- `net.core.default_qdisc = fq`
- IPv4/IPv6 forwarding 为 `1`
- 关键服务的 `LimitNOFILE` 足够高

### 暂不做或谨慎做

- 不使用来路不明的一键 BBR/锐速/魔改内核脚本。
- 不切换到非发行版内核，除非当前内核不支持 BBR 或存在明确性能问题。
- 不盲目调大所有 TCP buffer；先观察吞吐、丢包、重传和内存占用，再决定是否调整。
- 不在未验证的情况下启用 aggressive conntrack 或 NAT 参数，避免影响 Docker 和 Tailscale。

## 里程碑

### Milestone 1：Review 规划

- 确认 `.env` 变量名：第一阶段使用 `NODE_1_DOMAIN`，后续是否泛化为 `NODE_DOMAIN`。
- 确认 `admin.{$NODE_1_DOMAIN}` 的 HTTPS 入口是公网 HTTPS 还是 Tailnet-only HTTPS。
- 确认博客仓库新增 `NODE_1` webhook 的具体 secret 名和 URL。
- 确认 `sub.{$NODE_1_DOMAIN}` 第一阶段保留。

### Milestone 2：新增 Node Compose

- 添加 `docker-compose.node.yml`。
- 添加 `caddy/Caddyfile.node`。
- 添加 `.env.node.example`。
- 只保留 `3x-ui` 和 `caddy`。
- `caddy/Caddyfile.node` 参考 `caddy/Caddyfile`，删除 `claw` 和 `claw-ui`。
- `caddy` 使用 `NODE_1_DOMAIN`，而不是 `MY_DOMAIN`。
- 挂载 `/srv/blog:/srv/blog:ro`。
- 先保留 HTTPS 入口，具体端口暴露随 `admin.{$NODE_1_DOMAIN}` 的访问方式决定。

### Milestone 3：NODE_1 主机 Bootstrap

- 购买并初始化 `NODE_1` VPS。
- 安装 Docker 和 Docker Compose plugin。
- 应用宿主机网络优化：BBR、`fq`、转发、连接队列和文件句柄。
- 在 `MASTER`、`NODE_1` 宿主机安装 Tailscale 并加入同一 Tailnet。
- `NODE_1` 开启 IPv4/IPv6 转发。
- `NODE_1` 执行 `tailscale up` 时明确带上 `--advertise-exit-node --accept-dns=false`。
- 在 Tailscale admin console 中批准 `NODE_1` 作为 exit node。
- 配置 Tailscale tag 和最小权限 ACL/grants。
- clone 当前 infrastructure repository。
- 创建 `NODE_1` `.env`。
- 创建 `/etc/blog-deploy/webhook-secret`。
- 执行 `scripts/install_blog_puller.sh`。
- 手动触发一次博客 release 部署，让 `/srv/blog/current` 立即可用。
- 启动 node compose。
- 验证 `https://{$NODE_1_DOMAIN}` 能作为 active mirror 提供预期博客内容。
- 验证 BBR、`fq`、IPv4/IPv6 转发和文件句柄限制实际生效。

### Milestone 4：博客镜像持续更新

- 在博客仓库新增 `NODE_1` webhook URL 和 secret。
- 修改 GitHub Actions，让 release 发布后同时通知 `MASTER` 和 `NODE_1`。
- 可选：保留 `NODE_1` host-side systemd timer 作为兜底，但不作为主路径。
- 在 `NODE_1` 上验证：
  - `systemctl status blog-webhook.service blog-deploy.path`
  - `journalctl -u blog-deploy.service --since today`
  - `readlink /srv/blog/current`
  - `https://{$NODE_1_DOMAIN}` 能提供预期博客内容。

### Milestone 5：接入 3x-ui Node

- 在 `NODE_1` 生成 API token。
- 从 `MASTER` panel 的 `Nodes` 添加 `NODE_1`。
- 用面板里的 test/probe 按钮验证状态。
- 确认 inbound 同步行为。
- 确认新 client 配置可以选择 `NODE_1` 节点。

### Milestone 6：加固

- 收紧 admin 暴露面。
- 确认防火墙规则。
- 验证重启 `NODE_1` 后 `tailscale0`、Docker 网络和防火墙规则仍正常。
- 验证重启 `NODE_1` 后 Tailscale 仍为 `--accept-dns=false`，且 `/etc/resolv.conf` 没有被 Tailscale 改坏。
- 确认 Tailnet ACL/grants 只允许 `MASTER` 访问 `NODE_1` 管理端口。
- 确认 `NODE_1` 已宣告并被批准为 exit node。
- 确认只有被允许的用户或设备可以使用 `NODE_1` exit node。
- 确认未意外启用 subnet routes 或接受外部路由。
- 从一个被允许的 Tailnet 客户端选择 `NODE_1` exit node，验证公网出口 IP、DNS、HTTPS 访问正常。
- 关闭客户端 exit node 后，验证客户端路由恢复正常。
- 确认 Xray 相关域名在 Cloudflare 中为 DNS-only。
- 确认 `NODE_1` 上 Caddy ACME DNS challenge 可用。
- 确认 `NODE_1` 3x-ui 本地数据备份策略。
- 观察 `NODE_1` 的重传、丢包、CPU steal、内存、conntrack 和带宽占用，确认 exit node 不挤压 Xray/blog。
- 后续按需增加 monitoring。

## 需要继续讨论

- `NODE_1_DOMAIN` 是否就是节点裸域名，形如 `example-node-1.com`，由 Caddy 拼出 `admin.*` 和 `sub.*`。
- `admin.{$NODE_1_DOMAIN}` 的 3x-ui Node API 第一阶段是否允许公网 HTTPS；如果允许，需要确认强路径、强 token、失败限速和日志审计。
- 博客仓库新增 `NODE_1` webhook 后，是否还需要 host-side systemd timer 作为兜底。
- Tailscale 使用 `--accept-dns=false` 后，是否需要显式指定宿主机 DNS resolver，避免不同 VPS 镜像默认 DNS 不稳定。
- `NODE_1` exit node 允许哪些用户、设备或 tags 使用；是否只给个人设备使用，还是也给 `MASTER` 使用。
- exit node 流量是否需要单独限速或监控，避免影响 3x-ui 和博客服务质量。
- 宿主机优化是否只做 BBR/fq/limits 基线，还是额外加入连接跟踪、buffer、队列长度等更激进参数。
- `NODE_1` 的 3x-ui 数据、Caddy data/config、博客 current release 是否需要进入同一套备份脚本。
- `.env.node.example` 是否使用 `NODE_1_DOMAIN` 这种具名变量，还是为了未来复用改成通用 `NODE_DOMAIN`。

## 当前建议

使用单独的 `docker-compose.node.yml` 部署 `NODE_1`，并保持它足够轻量。

`caddy/Caddyfile.node` 直接参考 `MASTER` 的 `caddy/Caddyfile`，仅去掉 `claw` 和 `claw-ui`，并把 `MY_DOMAIN` 改成 `NODE_1_DOMAIN`。

Tailscale 安装在宿主机，不放入 Compose。`NODE_1` 需要使用 `--advertise-exit-node --accept-dns=false`，宣告为 exit node，但不让 Tailscale 接管 `/etc/resolv.conf`。`MASTER` 到 `NODE_1` 的 3x-ui node API 先走 HTTPS；是否公网可达或 Tailnet-only，在实现前最后确认一次。

`NODE_1` 上线前应用宿主机网络优化：启用 BBR 和 `fq`，开启 IPv4/IPv6 转发，提高 backlog、ephemeral port range 和文件句柄限制。更激进的 buffer、conntrack 或内核替换先不做，等有观测数据再调。

本仓库提供 `scripts/bootstrap_node_host.sh` 作为宿主机 bootstrap 入口。它负责：

- 按 Docker 官方 Ubuntu apt source 方法安装 Docker Engine 和 Compose plugin。
- 写入 BBR/fq/forwarding/backlog/port range 的 sysctl 基线。
- 写入 systemd、Docker、Tailscale 的文件句柄限制。
- 安装 Tailscale；如果提供 `TAILSCALE_AUTHKEY`，或显式设置 `ALLOW_INTERACTIVE_TAILSCALE_UP=1`，则以 `--advertise-exit-node --accept-dns=false --accept-routes=false` 启动。
- 配置 Docker daemon 默认日志轮转、`live-restore` 和容器默认 `nofile` ulimit。

常用执行形态，只安装、优化和准备 Tailscale，不等待网页登录授权：

```bash
sudo TAILSCALE_HOSTNAME=node-1 ./scripts/bootstrap_node_host.sh
```

非交互加入 Tailnet 时：

```bash
sudo TAILSCALE_AUTHKEY=tskey-auth-... TAILSCALE_HOSTNAME=node-1 TAILSCALE_ADVERTISE_TAGS=tag:infra-node ./scripts/bootstrap_node_host.sh
```

如果想交互式打开授权 URL：

```bash
sudo ALLOW_INTERACTIVE_TAILSCALE_UP=1 TAILSCALE_HOSTNAME=node-1 ./scripts/bootstrap_node_host.sh
```

如果明确跳过 `tailscale up`：

```bash
sudo RUN_TAILSCALE_UP=0 ./scripts/bootstrap_node_host.sh
```

第一阶段包含博客，且 `NODE_1` 直接作为 active mirror。博客仓库新增 `NODE_1` webhook，GitHub Actions release trigger 同时通知 `MASTER` 和 `NODE_1`；systemd timer 只作为可选兜底。

第一阶段不迁移 `MASTER` 到 PostgreSQL。每个 3x-ui 实例保留自己的本地持久化数据，由 `MASTER` panel 通过官方 `Nodes` 功能管理 `NODE_1`。
