# Development workspace bootstrap

This provider-owned helper is for the approved UID-1000 Coder workspace. It adds no host services, sockets, public database ports or new workspaces. Source/dependencies are installed in the workspace home volume; existing apps keep their original database/lifecycle.

`install.sh` runs only when provisioning/updating the workspace capability, not per project. It installs locked npm dependencies and Playwright's matching Chromium headless shell. Browser libraries/fonts are extracted to a private user directory from the workspace's signed Ubuntu package indexes (or reused from the verified existing pack). Current fallback provisioning supports Ubuntu 26.04 amd64 and fails on another image. No sudo or host apt install is performed. Browser doctor verifies text rendering and input at two viewport sizes, not just process launch. Do not overwrite these files/npm dependencies during an active helper-managed app; schedule upgrades with its owners and preserve persistent state.

Install the provided `coder-dev` shim to `~/.local/bin/`, source to `~/.local/share/coder-development-tools/`, and run `bash install.sh` there as UID 1000. Apply the short workspace instructions after saving the previous projects/AGENTS.md privately. The internal skill `references/bootstrap.md` is the agent-facing command contract. Shared package versions and lockfile are retained here for reproducible recovery. Restarting a workspace preserves the installed helper and state.

Commands run from one real directory directly under `~/projects`:

- `postgres -- COMMAND ...`: exclusive per-project flock; starts the private PostgreSQL cluster and a process group for the application; lifecycle exit stops both. The wrapper does not run migrations, seed, reset data or expose PostgreSQL.
- `env -- COMMAND ...`: injects the project's least-privileged DATABASE_URL and preview APP_ORIGIN. It does not start a second database. Use for explicit migration/developer commands against the running managed cluster.
- `account provision --name qa -- COMMAND ...`: persistently generate/reuse a synthetic profile and inject TEST_EMAIL/TEST_PASSWORD into an idempotent seed command. Mark provisioned only after success. Child output masks those exact values. The application must verify a real login before handoff.
- `account run --name qa -- COMMAND ...`: require a provisioned profile and inject the same values for reviewers' API tests. `account status --name qa` returns only readiness metadata.
- `browser doctor`: offline render/input smoke at desktop/mobile sizes using the installed browser/library/font pack.
- `qa run qa/browser.mjs --name qa`: invokes a project test module's default async function with browser, getTestAccount, previewUrl, artifactsDir. Closes the browser, redacts console credentials, keeps artifacts outside public app files. Test assertions are the reviewer's responsibility; a test that asserts nothing proves nothing.
- `ready`: combined PostgreSQL, browser, provisioned-qa capability check. Reports authentication_verified=false deliberately; it never grants Security/QA approval.

Persistent state uses private mode-700 directories keyed by the real project path and mode-600 configuration/account files, outside project/public source. Unsafe permissions/symlink files are rejected. Database admin credentials are used only for cluster setup; child applications receive a separate NOSUPERUSER/NOCREATEDB/NOCREATEROLE role. Database ports are chosen dynamically on loopback, and incomplete state is never automatically deleted or reinitialized. Renaming a project changes its identity; migrate only with an explicit plan. There is no reset/drop/credential-print endpoint.

These controls are operational project scoping within the existing shared UID. They are not tenant OS isolation, a secret broker or a new customer credential-delivery API. Reviewers share profiles through the same approved runtime; customer credentials still require an authorized private delivery channel. Never use this for provider credentials or production account management.

The integration fixture in tests/ must be copied into an isolated Coder project (with pg installed) and run as `coder-dev postgres -- node fixture.mjs`, twice. It checks real PostgreSQL privileges/listen scope, exclusive launch, account reuse/redaction/permission denial, browser+account handoff and persistence. `coder-dev env -- node stopped.mjs` then checks that the listener is gone. It does not alter production/other-app databases.

Signal handling is covered by tests/signal.mjs (with tests/hold.mjs). The CLI takes ownership of its PostgreSQL library signal hooks so app termination finishes before database shutdown. A real SIGTERM must leave no app/database listener and exit normally. Package versions, including exit-hook used for graceful completion, are pinned.

Version 1.1 adds `job --kind build -- npm run build` (also test/migration/seed/other/qa). QA runs track themselves automatically. It preserves child exit codes and records private lifecycle metadata for host resource/OOM attribution; it does not enforce resource quotas or authorize a tenant. Use the workspace-instructions.md addition for future jobs; do not restart or migrate running projects to adopt it. See ../observability/README.md for sampling limitations and operator-only telemetry.
