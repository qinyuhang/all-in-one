# 🚀 容器化高性能自建网络与应用集群服务设计规范 (Spec)
本规范定义了一个基于 Docker-Compose 的高度隔离、安全且兼顾高性能翻墙与多应用托管的服务器架构。
1. 核心设计原则与架构总体拓扑
1.1 设计原则
网络单入口：公网仅暴露 443 端口，隐藏所有后台管理面板（3X-UI、Caddy 控制台及内部应用）。
责任彻底分离 (SoC)：
3X-UI (Xray-core) 占据最前端，负责流量筛选（翻墙加密流量本地消化，网页流量无感回落）。
Caddy 在幕后只接听容器网络内部的 80 端口，通过 Cloudflare DNS-01 挑战 独立完成证书的自动化申请与续期。
IP 纯净度隔离：在服务端建立基于应用特征的链式代理 (WARP 出站)，防止机房 IP 污染导致 AI 助理认证失败或游戏联机封号。
1.2 流量全生命周期拓扑
```
[外部请求]
│
▼
┌────────────────────────────────────────────────────────┐
│ VPS 公网端口 :443 (由 3X-UI 容器内的 Xray-core 监听)    │
└──────────────────────────┬─────────────────────────────┘
│
┌─────────────┴─────────────┐
▼                           ▼
【加密科学上网流量】            【标准网页/探测流量】
(VLESS-XTLS-Vision)           (自动无感知 Fallback 回落)
│                           │
▼                           ▼
┌─────────────────┐        ┌──────────────────────────────────┐
│ 路由规则分流引擎 │        │ Docker 内部网络 -> caddy:80      │
└────────┬────────┘        └─────────────────┬────────────────┘
│                                   │ (根据 Host/SNI 三级域名分流)
┌──────┴──────┐                    ┌───────┼────────┬───────┐
▼             ▼                    ▼       ▼        ▼       ▼
【普通流量】  【特种隐私流量】       [博客]  [3X-UI]  [密码]  [AI网关]
(直连发出)  (走 WARP 节点)          (:80)   (:2053)  (:80)   (:3000)
```
2. 域名与网络规划
由于一级域名已在外部被其他生产业务占用，本系统全面采用三级域名（泛解析）方案，实现对一级的零侵扰。
2.1 域名资产分配
设定专属于此 VPS 系统的二级基础域名为：vps.example.com
主站/博客：vps.example.com
3X-UI 面板控制台：admin.vps.example.com
密码管理器 (Vaultwarden)：vault.vps.example.com
AI 助理网关 (OpenClaw)：claw.vps.example.com
2.2 Cloudflare DNS 规范
在 Cloudflare 后台仅需维护两条 DNS Only (灰小云) 记录：
A 记录 | vps ➡️ 你的 VPS 真实 IP
A 记录 或 CNAME | *.vps ➡️ vps.example.com (泛解析)
3. 服务端配置规范 (Spec Implementations)
3.1 环境变量架构 (.env)
基础定义
MY_DOMAIN=vps.example.com
CF_API_TOKEN=hZ7..._xF4 # 具备 DNS:Edit 权限的 Cloudflare Token
3X-UI 强置初始化凭证
XUI_ADMIN_USER=admin
XUI_ADMIN_PWD=secure_password_2026
3.2 容器编排架构 (docker-compose.yml)
version: '3.8'
networks:
app-net:
driver: bridge
services:
1. 流量大门与节点管理器
3x-ui:
image: ghcr.io/mhsanaei/3x-ui:latest
container_name: 3x-ui
restart: always
volumes:
- ./3x-ui/db:/etc/x-ui
- ./3x-ui/config:/etc/xray
ports:
- "443:443/tcp"
environment:
- XRAY_VMESS_AEAD_FORCED=false
- XUI_ADMIN_USER=XUI 
A
​	
 DMIN 
U
​	
 SER−XUI 
A
​	
 DMIN 
P
​	
 WD={XUI_ADMIN_PWD}
cap_add:
- NET_ADMIN
- NET_RAW
networks:
- app-net
2. 自动编译集成 Cloudflare DNS 插件的 Caddy
caddy:
build:
context: .
dockerfile_inline: |
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare
FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
container_name: caddy
restart: always
environment:
- CF_API_TOKEN=CF 
A
​	
 PI 
T
​	
 OKEN−MY 
D
​	
 OMAIN={MY_DOMAIN}
volumes:
- ./caddy/Caddyfile:/etc/caddy/Caddyfile
- ./caddy/data:/data
- ./caddy/config:/config
networks:
- app-net
depends_on:
- 3x-ui
3. 密码管理器 (轻量级 Rust 版)
vaultwarden:
image: vaultwarden/server:latest
container_name: vaultwarden
restart: always
volumes:
- ./vaultwarden:/data
networks:
- app-net
4. OpenClaw AI 网关
openclaw:
image: openclaw/openclaw:latest
container_name: openclaw
restart: always
volumes:
- ./openclaw/config:/app/config
networks:
- app-net
3.3 反向代理控制规范 (./caddy/Caddyfile)
全局块声明：注入 DNS-01 挑战插件
{
tls {
dns cloudflare {env.CF_API_TOKEN}
}
}
内部 80 端口多域名路由分流
:80 {
# 3X-UI 面板后台
@admin host admin.{$MY_DOMAIN}
handle @admin {
reverse_proxy 3x-ui:2053
}
# 密码管理器
@vault host vault.{$MY_DOMAIN}
handle @vault {
    reverse_proxy vaultwarden:80
}

# OpenClaw 智能助理
@claw host claw.{$MY_DOMAIN}
handle @claw {
    reverse_proxy openclaw:3000
}

# 兜底降级策略：非匹配域名一律响应 404 或重定向
fallback {
    respond "Not Found" 404
}
}
4. 3X-UI 节点配置核心规范 (Inbound Specification)
为了满足 CN2 GIA / 9929 顶级线路的极限加速性能与伪装需求，3X-UI 网页后台的入站必须严格对齐以下标准：
端口 (Port): 443
协议 (Protocol): vless
流控 (Flow): xtls-rprx-vision (客户端开启 TUN 模式，完美转发游戏 UDP 流量)
安全层 (Security): reality
回落目标 (Dest - 核心): caddy:80 (充分利用 Docker 容器网络，禁止填写 127.0.0.1)
伪装域名 (Server Names): admin.vps.example.com (可填自己通过 Caddy 签好证书的任意内部三级域名)
5. 客户端（软路由 ImmortalWrt）接入规范
5.1 协议适配
选用客户端核心：sing-box (可通过 HomeProxy 插件驱动)。
全局 TUN 配置：必须启用 auto_route: true 以及 strict_route: true，以在底层无缝截获电脑、PS5 主机发出的游戏联机 UDP 流量。
5.2 游戏联机优化
针对 EA（Apex）、Steam 等外服联机，数据不通过本地进行 WARP 封包，而是利用 CN2 GIA 高速直达 VPS，在 VPS 内部的 Xray/sing-box 层面通过配置 outbound-detour 路由给服务端 WARP 网卡。在享受顶级低延迟骨干网的同时，成功洗白机房 IP，规避封号风险。
