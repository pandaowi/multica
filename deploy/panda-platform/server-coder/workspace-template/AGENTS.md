# Workspace application development

Work in projects directly under /home/coder/projects as UID 1000. The user describes an application and receives a working direct HTTPS preview; handle source edits, dependencies, startup and verification yourself.

Run `coder-preview start` in the project. It allocates a free port and stable per-project URL, then manages the process. For other frameworks use an argv command after `--` with literal `{port}` or PORT. Never hardcode a shared port. Verify public HTTPS, assets and application behavior before returning the preview URL: local readiness alone is insufficient. Host provisioning can take several minutes. Configure exact allowed hosts and API/WebSocket proxies.

Use `coder-preview status` and project logs under ~/.local/state/coder-preview to diagnose failures. Only use `coder-preview start --restart` for the current project. Never replace the multi-app start.sh, kill another application's processes or use ad hoc nohup servers. All projects share workspace CPU/RAM/PID limits. Preview stops with the workspace and recovers on startup.

Only expose the frontend. Never expose repositories, editors, credentials, databases or internal telemetry. Provider credentials and host registration stay outside the workspace. Do not commit, push or publish unless explicitly requested; preview uses working files.

## Standard development database and QA

The installed `coder-dev` helper supplies project-scoped PostgreSQL, the pinned QA browser with libraries/fonts, and private synthetic test-account profiles. In non-interactive container shells first set `PATH="$HOME/.local/bin:$PATH"`. Read the approved Multica coder-development skill's `references/bootstrap.md` when using these capabilities.

For a new PostgreSQL app, run its initial explicit migration with `coder-dev postgres -- npm run migrate`, then register the existing supervisor with `"$HOME/.local/bin/coder-preview" start -- "$HOME/.local/bin/coder-dev" postgres -- npm start`. It allocates the database port, injects DATABASE_URL/APP_ORIGIN, persists project data, starts the app and stops its database on exit. Run explicit migrations via `coder-dev env -- npm run migrate`. Existing apps keep their existing lifecycle; do not migrate or restart them merely to adopt this helper.

Seed a synthetic account through `coder-dev account provision --name qa -- npm run seed` with the project's idempotent seed script reading TEST_EMAIL/TEST_PASSWORD. Verify actual login plus `coder-dev ready` before the first auth-candidate handoff. Reviewers use profile qa through `coder-dev account run` or `coder-dev qa run`; no manual credential relay or per-project Playwright/library/font install is needed. Never log or publish the account values. A readiness result is not independent QA or Security approval; those checks remain required.

## Managed job resource attribution

For builds and longer verification commands, use `coder-dev job --kind build -- npm run build` or `coder-dev job --kind test -- npm test`. Kinds: build, qa, test, migration, seed, other. `coder-dev qa run` already tracks its own job; do not double-wrap it. Do not wrap the long-lived preview server as a build job. Existing apps keep their lifecycle. The wrapper preserves command exit codes and records private job outcome metadata without storing command arguments/environment values. Host sampling attributes resources and OOM victims when process identity is observed; short/raw-shell jobs can remain unattributed. Task labels do not grant access or prove billable usage. Never restart another app or exceed workspace limits to complete a job.
