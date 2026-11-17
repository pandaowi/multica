# Stage 1: workspace recovery and private resource telemetry

Applied to the existing approved workspace on 2026-11-17. This is operational monitoring, not tenant isolation, admission control, production hosting or a customer telemetry API.

## Components

- Host user service `coder-workspace-observer.service` watches only configured existing workspace IDs with matching Docker label and home volume. It does not start a stopped workspace. Configuration is `/opt/coder/app-platform/observability/config.json`.
- Every 10 seconds (plus collection time), it checks the preview supervisor lifetime lock/heartbeat and samples processes. Missing supervisors are recovered, at most twice in 300 seconds. A stale heartbeat is recorded, never forcibly killed. Existing apps are adopted by PID plus boot/start-time identity.
- Workspace memory/cgroup OOM counters, CPU, PID count and sampled project/job CPU/RSS are stored privately. PSS is sampled every third cycle when permitted. At 95% workspace memory, collection switches to host `/proc` and bounded registry copies, avoiding an extra Python process in the pressured workspace.
- `coder-dev job --kind build -- npm run build` records managed job start/end/outcome; kinds are build, qa, test, migration, seed, other. `coder-dev qa run` is tracked automatically. Abruptly terminated jobs are observed as abandoned. Arbitrary shell commands are not automatically managed jobs.
- Rootful `coder-kernel-observer` reads the host kernel journal through a read-only journal mount. It has no Docker socket, network or capabilities; its writable mount is only the private event directory. It runs as the host Coder UID plus the journal supplementary GID, with CPU/RAM/PID limits. Image source is `Dockerfile.kernel-reader`, deployed image `coder-kernel-observer:20261117-v1`.
- Kernel OOM records are accepted only for configured workspace container IDs. Exact recent host PID/boot identity links a victim to a sampled project/job. Old or unsampled victims retain null attribution. RSS ranking never substitutes for evidence.

## Data and boundaries

Host state: `/opt/coder/app-platform/state/observability` (0700; files 0600). `latest.json` contains the most recent sample; daily `resources-*.jsonl` have bounded rotation and seven-day retention; `events.jsonl` records watchdog, pressure and job changes. Kernel events are in `kernel/oom-events.jsonl` with bounded rotation and event-ID deduplication. Agent job metadata is under `~/.local/state/coder-observability/jobs` (seven-day retention).

No command arguments, environment values, SQL, application output or credentials are serialized into telemetry. Typed project/job operational labels are not authorization or billing evidence: agents currently share one UID and can influence their own metadata. Short jobs may finish between samples; their lifecycle can be recorded without a CPU/RSS sample. RSS sums can double-count shared pages; PSS may be unavailable. Workspace totals include unattributed processes. Sampling is not peak capture or precise job billing.

Health checks use one reusable no-proxy, no-redirect HTTP client, accept redirects without rendering their destination and poll HTTP every 30 seconds once ready, using TCP checks between polls. `coder-preview start --health-path /api/health` selects a lightweight local readiness path. SIMAKOP uses its existing `/api/health`; its application/database/source were not migrated.

## Operations

As the host Coder user:

```sh
systemctl --user status coder-workspace-observer
journalctl --user -u coder-workspace-observer --since '10 minutes ago'
```

Inside the workspace as UID 1000:

```sh
~/.local/bin/coder-preview watchdog --check
~/.local/bin/coder-preview status
```

For kernel-reader state, an authorized host operator uses `docker inspect coder-kernel-observer`. Do not grant rootful Docker or journal access to customer agents. Provision the exact read-only journal/context and event-only writable mounts described above; the journal GID is host-specific. Do not expose these raw files in a customer route.

To update host observer code, copy the modules to `/opt/coder/app-platform/observability`, copy the service to the Coder user's systemd directory, daemon-reload and restart **only** `coder-workspace-observer`. Preserve the private config/state. Use UID-1000 atomic writes for workspace helpers; `docker cp` can produce incorrect ownership. Changing preview daemon code needs a planned supervisor-only replacement with identity checks and app PID comparison; merely updating its file does not replace a running daemon.

Rollback: disable/stop `coder-workspace-observer` and stop/remove only the named `coder-kernel-observer` container as operator. Existing preview apps keep running. Restore the privately backed-up helper files only if required; preserve apps.json, home volumes, databases, DNS/routes and process state. Restoring a daemon binary does not by itself replace the live daemon. Backups are in host `state/observability-backup-*` and workspace `.local/state/coder-observer-backup-*`.

## Verification and incident limits

Run `python3 -m unittest discover -s server-coder/observability -p test_observer.py` and platform test discovery. `test_recovery.py --manager PATH` uses a unique temporary home and its own application; it must run in the isolated Linux fixture, not target an existing application. The applied report contains a real 48 MiB disposable-container OOM test, exact attribution, and unchanged real-workspace OOM-kill counters.

During rollout, a natural OOM killed SIMAKOP's Next development server and its old Python supervisor. The kernel confirms four historical paired kills. Recovery happened during observer rollout and is not evidence of a continuous-service recovery SLA. The planned later supervisor upgrade preserved all ten existing app PIDs. Stage 1 records and recovers failures; it does not solve excessive application memory demand. Production-build/resource scheduling and tenant sandbox isolation remain later work.

New v2 workspaces are enrolled through `~/.local/bin/coder-workspace provision` on the host. The operator script validates label, image, UID, limits and exact home volume before adding observer/preview entries. It uses additive atomic configuration changes; the running observer rereads configuration without a restart. The kernel reader follows its approved-container context automatically. Test cleanup stops/unregisters only its own workspace and preserves its volume and historical evidence.
