#!/usr/bin/env bash
set -Eeuo pipefail

# Bootstrap a NODE_* host for all-in-one edge-node usage:
# - Ubuntu host network tuning: BBR, fq, forwarding, queues, limits.
# - UDP GRO forwarding tuning for Tailscale exit-node throughput.
# - Docker Engine via Docker's official apt repository.
# - Tailscale install and optional exit-node advertisement with accept-dns=false.
#
# Designed for a fresh Ubuntu VPS. Run as root:
#   sudo ./scripts/bootstrap_node_host.sh
#
# Optional environment variables:
#   RUN_TAILSCALE_UP=0              Install Tailscale but do not run tailscale up.
#   ALLOW_INTERACTIVE_TAILSCALE_UP=1
#                                   Allow login-URL based tailscale up when no auth key is set.
#   TAILSCALE_AUTHKEY=tskey-auth-.. Non-interactive tailnet join.
#   TAILSCALE_HOSTNAME=node-1       Tailscale machine hostname.
#   TAILSCALE_ADVERTISE_TAGS=tag:infra-node
#   OVERWRITE_DOCKER_DAEMON=1       Replace existing /etc/docker/daemon.json after backup.

log() {
  printf '[bootstrap-node] %s\n' "$*"
}

die() {
  printf '[bootstrap-node] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "run as root, e.g. sudo $0"
  fi
}

require_ubuntu() {
  if [[ ! -r /etc/os-release ]]; then
    die "/etc/os-release not found"
  fi

  # shellcheck disable=SC1091
  . /etc/os-release

  if [[ "${ID:-}" != "ubuntu" ]]; then
    die "this bootstrap currently targets Ubuntu only; detected ID=${ID:-unknown}"
  fi

  if [[ -z "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}" ]]; then
    die "cannot determine Ubuntu codename"
  fi
}

apt_install_base_packages() {
  log "Installing base packages"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    procps \
    ethtool
}

configure_sysctl() {
  log "Configuring sysctl network baseline"

  install -m 0755 -d /etc/modules-load.d
  {
    printf 'tcp_bbr\n'
    printf 'tun\n'
  } >/etc/modules-load.d/99-all-in-one-node.conf

  modprobe tcp_bbr 2>/dev/null || true
  modprobe tun 2>/dev/null || true

  install -m 0755 -d /etc/sysctl.d
  cat >/etc/sysctl.d/99-all-in-one-node.conf <<'EOF'
# all-in-one NODE host baseline.
# Keep this conservative: BBR/fq, forwarding for Tailscale exit node,
# larger queues, and a wider local port range.
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_mtu_probing = 1
EOF

  sysctl --system >/dev/null
}

configure_limits() {
  log "Configuring systemd and service file descriptor limits"

  install -m 0755 -d /etc/systemd/system.conf.d /etc/systemd/user.conf.d
  cat >/etc/systemd/system.conf.d/99-all-in-one-node-limits.conf <<'EOF'
[Manager]
DefaultLimitNOFILE=1048576
EOF

  cat >/etc/systemd/user.conf.d/99-all-in-one-node-limits.conf <<'EOF'
[Manager]
DefaultLimitNOFILE=1048576
EOF

  install -m 0755 -d /etc/systemd/system/docker.service.d
  cat >/etc/systemd/system/docker.service.d/99-all-in-one-node-limits.conf <<'EOF'
[Service]
LimitNOFILE=1048576
EOF

  install -m 0755 -d /etc/systemd/system/tailscaled.service.d
  cat >/etc/systemd/system/tailscaled.service.d/99-all-in-one-node-limits.conf <<'EOF'
[Service]
LimitNOFILE=1048576
EOF

  systemctl daemon-reload
}

configure_tailscale_udp_gro() {
  log "Configuring UDP GRO forwarding for Tailscale exit-node throughput"

  local netdev
  netdev="$(ip -o route get 8.8.8.8 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}')"

  if [[ -z "${netdev}" ]]; then
    log "Cannot detect default route interface; skipping UDP GRO forwarding tuning"
    return 0
  fi

  if ! command -v ethtool >/dev/null 2>&1; then
    log "ethtool is missing; skipping UDP GRO forwarding tuning"
    return 0
  fi

  ethtool -K "${netdev}" rx-udp-gro-forwarding on rx-gro-list off || true

  install -m 0755 -d /usr/local/libexec /etc/systemd/system
  cat >/usr/local/libexec/all-in-one-tailscale-udp-gro.sh <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

NETDEV="$(ip -o route get 8.8.8.8 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}')"
if [[ -z "${NETDEV}" ]]; then
  exit 0
fi

ethtool -K "${NETDEV}" rx-udp-gro-forwarding on rx-gro-list off
EOF
  chmod 0755 /usr/local/libexec/all-in-one-tailscale-udp-gro.sh

  cat >/etc/systemd/system/all-in-one-tailscale-udp-gro.service <<'EOF'
[Unit]
Description=Configure UDP GRO forwarding for Tailscale exit node
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/all-in-one-tailscale-udp-gro.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now all-in-one-tailscale-udp-gro.service
}

install_docker_official_repo() {
  log "Installing Docker Engine from Docker official apt repository"

  # Docker official Ubuntu docs use /etc/apt/keyrings/docker.asc and
  # /etc/apt/sources.list.d/docker.sources.
  apt-get remove -y \
    docker.io \
    docker-compose \
    docker-compose-v2 \
    docker-doc \
    podman-docker \
    containerd \
    runc >/dev/null 2>&1 || true

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  local codename
  # shellcheck disable=SC1091
  . /etc/os-release
  codename="${UBUNTU_CODENAME:-$VERSION_CODENAME}"

  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${codename}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin
}

configure_docker_daemon() {
  log "Configuring Docker daemon defaults"

  install -m 0755 -d /etc/docker

  if [[ -f /etc/docker/daemon.json && "${OVERWRITE_DOCKER_DAEMON:-0}" != "1" ]]; then
    log "/etc/docker/daemon.json exists; leaving it unchanged"
    log "Set OVERWRITE_DOCKER_DAEMON=1 to replace it after backup"
  else
    if [[ -f /etc/docker/daemon.json ]]; then
      cp -a /etc/docker/daemon.json "/etc/docker/daemon.json.bak.$(date +%Y%m%d%H%M%S)"
    fi

    cat >/etc/docker/daemon.json <<'EOF'
{
  "live-restore": true,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Soft": 1048576,
      "Hard": 1048576
    }
  }
}
EOF
  fi

  systemctl enable --now docker
  systemctl restart docker
}

install_tailscale() {
  log "Installing Tailscale using official Linux install script"
  curl -fsSL https://tailscale.com/install.sh | sh
  systemctl enable --now tailscaled
  systemctl restart tailscaled
}

run_tailscale_up() {
  if [[ "${RUN_TAILSCALE_UP:-1}" == "0" ]]; then
    log "Skipping tailscale up because RUN_TAILSCALE_UP=0"
    return 0
  fi

  if [[ -z "${TAILSCALE_AUTHKEY:-}" && "${ALLOW_INTERACTIVE_TAILSCALE_UP:-0}" != "1" ]]; then
    log "Skipping tailscale up because no TAILSCALE_AUTHKEY is set"
    log "Run this manually after bootstrap if you want browser-based authorization:"
    log "tailscale up --advertise-exit-node --accept-dns=false --accept-routes=false --hostname ${TAILSCALE_HOSTNAME:-node-1}"
    log "Or rerun with ALLOW_INTERACTIVE_TAILSCALE_UP=1 to print and wait on the login URL."
    return 0
  fi

  log "Running tailscale up as exit node with accept-dns=false"

  local args
  args=(
    up
    --advertise-exit-node
    --accept-dns=false
    --accept-routes=false
  )

  if [[ -n "${TAILSCALE_AUTHKEY:-}" ]]; then
    args+=(--auth-key "${TAILSCALE_AUTHKEY}")
  fi

  if [[ -n "${TAILSCALE_HOSTNAME:-}" ]]; then
    args+=(--hostname "${TAILSCALE_HOSTNAME}")
  fi

  if [[ -n "${TAILSCALE_ADVERTISE_TAGS:-}" ]]; then
    args+=(--advertise-tags "${TAILSCALE_ADVERTISE_TAGS}")
  fi

  tailscale "${args[@]}"
}

verify_baseline() {
  log "Verification snapshot"
  sysctl net.ipv4.tcp_congestion_control
  sysctl net.core.default_qdisc
  sysctl net.ipv4.ip_forward
  sysctl net.ipv6.conf.all.forwarding

  if command -v docker >/dev/null 2>&1; then
    docker version --format 'Docker {{.Server.Version}}' || true
    docker compose version || true
  fi

  if command -v tailscale >/dev/null 2>&1; then
    tailscale version || true
    tailscale status || true
    tailscale debug prefs 2>/dev/null | grep -E 'AdvertiseRoutes|AdvertiseTags|CorpDNS|RouteAll|ExitNodeID' || true
  fi

  systemctl show docker --property=LimitNOFILE || true
  systemctl show tailscaled --property=LimitNOFILE || true
  log "Check /etc/resolv.conf manually if DNS behaves oddly; Tailscale was started with --accept-dns=false."
}

main() {
  require_root
  require_ubuntu
  apt_install_base_packages
  configure_sysctl
  configure_limits
  configure_tailscale_udp_gro
  install_docker_official_repo
  configure_docker_daemon
  install_tailscale
  run_tailscale_up
  verify_baseline

  log "Done. Approve NODE_1 as an exit node in the Tailscale admin console if it is not approved yet."
}

main "$@"
