# Coder app platform

## Automatic multi-app preview supervisor

The coding agent runs `coder-preview start` in a project directly under `~/projects`. The helper reserves an available port in 3000..3999 under a registry lock, assigns a persistent per-project preview name, writes the public preview manifest, and starts the workspace supervisor if needed. Existing preview URLs are preserved. New default names include a short workspace/project identity hash to avoid conflicts. The helper waits for local HTTP readiness; the agent must separately verify the public URL after asynchronous DNS/TLS provisioning.

Static `site/`, `dist/`, or `public/` frontends and Vite/Next.js/react-scripts development commands have built-in detection, with recognized development scripts preferred over stale build output. For another framework, the agent supplies an argv command after `--`, using `{port}` or the `PORT` environment variable. Dependencies must be installed first. Exact Vite host allowance is set by the supervisor; other framework host/origin/API proxy configuration remains part of the coding agent's implementation work. The end user never needs these commands.

Each app's project, command, port, enabled state and stable URL are stored in `~/.config/coder-preview/apps.json`. `start.sh` now starts the supervisor, which restores **all enabled applications** when Coder starts the workspace. Do not replace it with a single-project launcher. Per-app state and logs are in `~/.local/state/coder-preview/`; logs are bounded to 5 MiB. The supervisor checks processes every two seconds and restarts exited processes after a short backoff, validating recorded process identity before signalling any process group. All apps share the workspace's existing 2 CPU / 4 GiB / 512 PID limits.

Agent operations: `coder-preview status`; `coder-preview start --restart` in a project; `coder-preview stop PREVIEW_NAME`. Stopping an app removes its manifest so the host reconciler removes its route. It retains registry/hostname ownership for reuse. Ports remain reserved in the registry; if an unmanaged listener occupies a preferred port on startup, an available port is selected and the manifest/route is updated without changing the URL. The host rejects duplicate ports in one workspace to prevent accidental cross-project routing.

The current landing URL is unchanged. A second example project `~/projects/preview-example` demonstrates a separate automatically assigned port and subdomain. Both applications were tested together and restored automatically after workspace restart.

## Live preview without Coder login

The current landing preview is `https://preview-landing.coder.example.com/`. It serves working files directly from the workspace development server, without a commit, image build or production deployment. The user opens the link and refreshes after changes. It is intentionally public, including to visitors without a Coder account. Preview stops when the workspace stops.

The coding agent runs `coder-preview start` in the project directory. The multi-app supervisor owns the startup launcher, process management, and automatic port allocation. The old `coder-preview-link APP_NAME` command delegates to the supervisor; manual port arguments are no longer supported. The helper writes `.coder-preview.json` to the working tree; it does not commit anything. The trusted `coder-preview-controller.timer` reads manifests from approved workspace home volumes every 30 seconds and provisions exact `preview-APP_NAME.coder.example.com` hostnames with the existing free-quota guard. The agent must wait for a valid certificate and test the app before returning the URL.

Each approved workspace gets a separate internal Docker network joining only that workspace and Traefik. The reconciler verifies the container's workspace label and home-volume ownership before connecting it. Routes target only the declared frontend port. The network is reattached after workspace or Traefik recreation. Preview names are reserved and cannot be used by the production static deployment controller. The same controller lock serializes Cloudflare quota checks and creation with production provisioning.

Traefik sends `Cache-Control: no-store` and `X-Robots-Tag: noindex, nofollow`. Removing the preview manifest disables its route without deleting DNS/certificates. Explicit retirement of retained hostnames is still an administrator operation. Agent instructions are in `/home/coder/projects/AGENTS.md` in the current workspace. A framework's allowed hosts, API proxy and HMR/WebSocket settings must match its direct preview domain. Do not expose editors, secrets, repositories or databases through preview.

Inspect with `systemctl --user status coder-preview-controller.timer`, `journalctl --user -u coder-preview-controller.service`, and `/opt/coder/app-platform/state/previews.json`.

Host: `10.0.0.10`, SSH user `coder`. All containers use this user's rootless Docker daemon. Platform directory: `/opt/coder/app-platform`.

## Host-approved friendly preview domains

The host-only `preview_aliases` mapping in `config.json` can assign a friendly hostname to an existing approved preview, for example `"gbrain-notes": "preview-gbrain-notes-d4404e35"`. This routes `https://gbrain-notes.coder.example.com/` to the same workspace frontend. The preview supervisor retains its original identity and manages the process and port. The controller follows port changes and removes alias routing while its target is unavailable. DNS and HTTPS use the same guarded Cloudflare provisioning path. Production names and aliases cannot take over each other's routes; a retained alias cannot change owner without explicit host retirement.

Alias status is in `state/preview-aliases.json`; routes are in `routes/preview-alias-NAME.yaml`. Keep Cloudflare credentials on the host. Domain changes also require updating an application's origin and OAuth redirect registration. GBrain Notes redirects the previous hostname to the friendly domain; browser sessions require a new sign-in on the new hostname.

## Publish from the current workspace

The initial supported workload is a static website. Put the generated HTML/CSS/JS in `site/`, including `site/index.html`, inside a Git repository under `/home/coder/projects/`.

```sh
cd ~/projects/coder-ai-landing
coder-publish landing
git add .coder-app.json site/
git commit -m "Update landing page"
```

The controller checks committed files every 30 seconds. This publishes `https://landing.coder.example.com`. Existing `https://coder-ai.example.com` continues serving the same container. Uncommitted changes are not deployed.

For a new project use another available name, e.g. `coder-publish invoice` requests `https://invoice.coder.example.com`. The helper writes a manifest; commit it together with `site/`. The AI agent can run these commands after editing the site. Agent authentication is separate from publishing.

Only explicitly approved workspace home volumes are scanned. A new workspace must first be added to the host configuration. Names cannot be taken over by another project or silently renamed. Removing a manifest retains the existing deployment; retirement requires a host administrator.

## Isolation and resources

The workspace receives neither a Docker socket nor a Cloudflare token. A trusted host controller reads committed files through a read-only volume mount and builds a fixed Nginx image. Repository Dockerfiles and build scripts are not executed. Archives must contain regular files/directories, no links, and at most 8 MiB. Framework apps must commit their static output in `site/`; server-side apps need a separately reviewed deployment recipe.

Each app has 0.5 CPU, 128 MiB RAM and 128 PIDs, a read-only filesystem and no Linux capabilities. Traefik has the same limits, no dashboard and no Docker socket. It watches exact-host routes generated by the controller. Published app containers share the frontend network with Traefik; this is not strict tenant isolation. Each app also has a private Compose network. Workspace and PostgreSQL networks are not attached.

Traefik listens on LAN `10.0.0.10:7084`; public traffic uses the existing Cloudflare Tunnel. No router forwarding or ACM subscription is used. HTTPS certificates are managed by Cloudflare for each exact hostname; this setup does not run Let's Encrypt on Traefik.

## Cloudflare setup and recovery

1. Enable Cloudflare for SaaS for `example.com`, reviewing the account's terms/payment screen. Do not select paid add-ons. The included allowance is 100 custom hostnames, not an enforced global billing cap.
2. Create a dedicated API token restricted to the `example.com` zone: Zone Read, DNS Edit, SSL and Certificates Edit. The user must create the credential themselves in the dashboard.
3. Store it directly on the host, never in chat or a workspace:

   ```sh
   ssh -t coder@10.0.0.10 'bash /opt/coder/app-platform/save-cloudflare-token.sh'
   ```

4. The read-only check must succeed. Then set `cloudflare_enabled` to true in the host `config.json`, and start `coder-app-controller.service`. Inspect hostname and certificate activation before declaring public access ready.

The controller creates the proxied `apps-origin.example.com` CNAME to the existing tunnel and configures it as the SaaS fallback origin. Each application receives its own proxied CNAME to that origin and an individual custom hostname/certificate. Existing conflicting DNS or fallback origins cause an error instead of being replaced.

Before creating a hostname, usage and the complete hostname inventory must be readable, and both must be below 95. Five included hostnames are reserved as headroom. Unknown quota, API errors and conflicting configuration stop creation. This guard applies to this controller; concurrent changes by other tools/accounts can still affect billing. No automatic hostname deletion or paid certificate order is performed.

## Operations

```sh
systemctl --user status coder-app-controller.timer
journalctl --user -u coder-app-controller.service -n 30 --no-pager
cat /opt/coder/app-platform/state/apps.json
docker compose -f /opt/coder/app-platform/compose.yaml ps
curl -H 'Host: landing.coder.example.com' http://10.0.0.10:7084/
```

The previous landing-only timer is disabled after successful migration. Its Compose configuration is backed up as `/opt/coder/apps/coder-ai-landing/compose.before-platform.yaml`. Previous app images are retained for rollback. Builds that fail health checks attempt to restore the previous image.

Local checks: `python3 -m unittest discover -s server-coder/app-platform -p test_guard.py`.

## Verified deployment status — 2026-11-11

Cloudflare for SaaS is activated. The zone-scoped token was installed directly by the user, and automatic domain creation is enabled on the host. `landing.coder.example.com` and its individual certificate are both active; HTTPS returned 200 with certificate verification enabled. The existing landing URL also returned 200. Usage was one included hostname after provisioning; local automation refuses creation once usage reaches 95.

The client network DNS still returned the earlier tunnel CNAME during verification. Google DNS-over-HTTPS returned the correct Cloudflare public addresses; HTTPS against those addresses with the original hostname and normal certificate verification succeeded. Browser verification on that network was blocked by stale or overridden DNS. If this persists, the network administrator should inspect caching/overrides for this hostname; do not pin Cloudflare IP addresses in hosts files.

## Provisioning a new v2 workspace

Use the host operator entry point `~/.local/bin/coder-workspace provision --name NAME --owner OWNER` (or the repository workspace-template/infra-workspace.sh wrapper). It renders the pinned-image template and registers the verified workspace ID/home volume in both this controller and the resource observer under a lock. No restart of existing apps/controllers is required. New workspace UI creation alone does not approve host routing; customer agents cannot self-register. See ../workspace-template/README.md for acceptance tests and rollback.
