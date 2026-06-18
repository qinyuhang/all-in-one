#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 1
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if ! id blog-deploy >/dev/null 2>&1; then
    useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin blog-deploy
fi

install -d -o blog-deploy -g blog-deploy -m 0755 /srv/blog /srv/blog/releases
install -d -o root -g root -m 0700 /etc/blog-deploy
if [ ! -s /etc/blog-deploy/webhook-secret ]; then
    echo "missing /etc/blog-deploy/webhook-secret" >&2
    echo "create a random secret of at least 32 bytes before installation" >&2
    exit 1
fi
chown root:root /etc/blog-deploy/webhook-secret
chmod 0600 /etc/blog-deploy/webhook-secret
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 \
    "$repo_root/scripts/deploy_blog_release.py" \
    /usr/local/libexec/deploy-blog-release
install -o root -g root -m 0755 \
    "$repo_root/scripts/blog_webhook.py" \
    /usr/local/libexec/blog-webhook
install -o root -g root -m 0644 \
    "$repo_root/deploy/systemd/blog-deploy.service" \
    /etc/systemd/system/blog-deploy.service
install -o root -g root -m 0644 \
    "$repo_root/deploy/systemd/blog-deploy.path" \
    /etc/systemd/system/blog-deploy.path
install -o root -g root -m 0644 \
    "$repo_root/deploy/systemd/blog-webhook.service" \
    /etc/systemd/system/blog-webhook.service

systemctl daemon-reload
systemctl disable --now blog-deploy.timer >/dev/null 2>&1 || true
rm -f /etc/systemd/system/blog-deploy.timer
systemctl enable --now blog-webhook.service blog-deploy.path
