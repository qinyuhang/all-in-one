# Blog deployment

The blog repository builds static files in GitHub Actions. Each successful `main` build publishes an immutable release containing `blog.tar.gz` and `blog.tar.gz.sha256`, then sends an HMAC-signed notification. The VPS validates the notification and pulls the release; GitHub never receives VPS credentials.

## First deployment

1. Generate a random webhook secret on the VPS and keep the command output out of logs:

   ```sh
   install -d -o root -g root -m 0700 /etc/blog-deploy
   umask 077
   openssl rand -hex 32 > /etc/blog-deploy/webhook-secret
   ```

2. Store the same value as the blog repository Actions secret `BLOG_DEPLOY_WEBHOOK_SECRET`.
3. Configure these blog repository Actions variables:

   - `SITE_URL`: the production HTTPS URL.
   - `BLOG_DEPLOY_WEBHOOK_URL`: `https://admin.<domain>:8443/_hooks/blog`.
   - `PUBLIC_UTTERANCES_REPO`: optional public `owner/name` repository with the Utterances app installed.

4. Update the infrastructure repository and submodule revision on the VPS.
5. Install the restricted webhook and pull services:

   ```sh
   sudo ./scripts/install_blog_puller.sh
   systemctl status blog-webhook.service blog-deploy.path
   ```

6. Recreate only Caddy so it receives the Unix socket mount and webhook route:

   ```sh
   docker compose up -d --no-deps --force-recreate caddy
   ```

7. Push the blog commit to `main`. The Action publishes the Release and sends the signed notification. Verify the deployment:

   ```sh
   systemctl status blog-deploy.service
   readlink /srv/blog/current
   ```

The public endpoint does not receive the shared secret itself. The Action signs `<unix timestamp>.<exact request body>` with HMAC-SHA256. The VPS accepts only a matching signature within five minutes for the configured repository. A valid request updates `/srv/blog/.deploy-trigger`; the systemd path unit then starts the pull service.

## Manual update

```sh
sudo touch /srv/blog/.deploy-trigger
sudo journalctl -u blog-deploy.service --since today
```

## Rollback

List retained releases and atomically point `current` to the selected previous version:

```sh
ls -1 /srv/blog/releases
sudo -u blog-deploy ln -sfn releases/<previous-blog-sha> /srv/blog/.rollback
sudo -u blog-deploy mv -Tf /srv/blog/.rollback /srv/blog/current
```

Caddy mounts `/srv/blog` rather than the symlink target itself, so switching `current` is visible immediately and does not require a container restart.
