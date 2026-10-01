# Cost-aware Linux runners

This is the infrastructure pilot. Existing repository workflows and required checks remain on their current runners until the live smoke test passes. Only `.github` can use the new runner group initially.

| Work | Route |
| --- | --- |
| Small Linux checks, aggregate gates, route selection | One DO worker on `t3-remote` |
| Heavy Linux jobs | Blacksmith allowance → GitHub included allowance → GitHub paid buffer |
| macOS | Existing local Mac only |
| Unavailable billing data or exhausted allowances | Use the other verified provider, otherwise fail the selector |

Heavy jobs do not fall back to this small droplet until measured and explicitly classified as compatible. Nothing here changes the local Mac, application code, branch rules, Socket, Renovate, or model API billing.

## Policy and variables

`config.json` is the reviewed policy, installed root-owned at `/opt/vyamoh-ci/config.json`.

- `CI_ROUTING_STATE`: private-repository organization variable, written by the controller every two minutes. Contains backend, UTC month, generation/expiry timestamps and observed usage. A single JSON value avoids partially published routing decisions. It expires after ten minutes and cannot carry over a month boundary.
- `CI_ROUTING_OVERRIDE`: optional organization variable: `automatic` (default), `github`, `blacksmith`, or `blocked`. A provider override still requires valid usage and its allowance. Unknown values block heavy work. This is a temporary operational switch, not permission to exceed a budget.
- `CI_RUNNER_SMOKE_ENABLED`: `.github` repository variable, `true` only while testing the pilot. Enables `Runner smoke test` on PRs and manual dispatch.

Consumer workflows call `.github/workflows/runner-select.yml@<release>` on DO, then use `fromJSON(needs.routing.outputs.runner)` on heavy jobs. The selector executes root-owned installed policy, without checking out PR code or receiving provider credentials. Sizes `2vcpu` and `4vcpu` select their Blacksmith SKU; both use standard `ubuntu-24.04` on GitHub. Cheap jobs use `[self-hosted, Linux, X64, vyamoh-do-light]` directly.

Existing aggregate gates must include the selector in `needs` and reject failed/skipped prerequisites. A skipped heavy job must never make its required `ci` gate pass. Keep all existing job IDs/names and repo-specific gates during migration. Repo/environment variables with these names override organization variables, so remove conflicting overrides before rollout.

Blacksmith's 3,000 included x64 2-vCPU minutes correspond to 6,000 CLI `billing_minutes`. Routing leaves 600 billing minutes (300 equivalent minutes) in reserve. The query spans the current UTC calendar month. GitHub uses the current month's total Actions `netAmount`, including storage, and verifies the existing organization Actions budget is exactly $5 with `prevent_further_usage=true`. It leaves $0.50 in reserve. Gross usage already discounted by GitHub does not consume this paid buffer.

These reserves are conservative estimates, not reservations. Provider reports can lag and parallel jobs can cross thresholds. Blacksmith spending alerts are not hard caps, and the router cannot guarantee zero Blacksmith overage. Confirm the account's included-usage/reset terms before the production cutover. GitHub's configured spending stop is the final $5 guard. A selected or already queued job is not migrated when the variable changes. A DO outage queues cheap jobs/selectors; there is no paid rescue route.

## Services and isolation

`vyamoh-ci-router.timer` runs the usage controller as `vyamoh-ci-router`. `vyamoh-ci-cycle.service` is a small root-owned lifecycle coordinator: it starts the fixed broker service, copies a single-use runner credential, waits for the fixed worker service, and stops the entire worker cgroup. It accepts no job-supplied paths or commands.

The broker authenticates afresh with Infisical and the GitHub App, registers a JIT runner, and refuses a duplicate online runner or unexpected runner-group access. It can remove only the offline registration matching this worker's fixed name. The runner is ephemeral and handles one job. A previous offline registration is cleaned up before retrying. The group starts selected/private-only with `.github`'s repository ID.

`vyamoh-ci-worker.service` runs as `vyamoh-ci`, with no sudo, host Docker socket or provider credentials. It has one CPU, 768 MiB memory soft pressure, 1 GiB RAM maximum, 512 MiB swap maximum and 256 tasks. Its home, workspace, tool cache and temporary files live in fresh bounded storage; runner binaries are mounted read-only. Systemd removes this storage and kills descendant processes at job completion or the one-hour service limit. No persistent runner cache is shared between jobs.

Host homes, router state, credentials and common service sockets are hidden. Private, loopback, link-local, Tailscale and host-interface IPs are denied, with the systemd DNS stub allowed. `smoke.py` verifies actual network denial rather than assuming the kernel attached the policy. This is native systemd isolation sharing the host kernel, not a VM boundary. Only trusted private repositories belong in the group. Public/fork work is not an intended workload.

Provider credentials come from `t3-remote / Production /runner-router` in Infisical:

- `GITHUB_APP_PRIVATE_KEY` for app `5144130`, installation `166729174`.
- `BLACKSMITH_ORG_TOKEN` passed only to the controller's CLI subprocess.

`/etc/vyamoh-ci-router/infisical.env` remains root-only, delivered to the broker/controller through `LoadCredential`. The worker receives only its JIT credential through a separate `LoadCredential`; it does not receive the App token, key, Blacksmith token or Infisical login. Access tokens expire within one hour. Fetching each invocation picks up secrets rotated in Infisical without personal login. The approved Infisical Viewer identity can also read backup secrets because folder restriction requires Pro; the code requests only `/runner-router` without imports, recursion or expansion.

## Installation and verification

Prerequisites: Ubuntu 24.04 x64, systemd 255 with cgroup v2, the existing locked accounts (`vyamoh-ci` UID 997/GID 987), provisioned swap, and the root-only Infisical credential. Run the reviewed `install.sh` as root from a reviewed checkout/bundle. It installs OS runtime dependencies, verifies pinned artifact hashes, installs services, publishes initial state and starts the one-job lifecycle. No sudo access is granted to either service user.

The GitHub runner is pinned at 2.337.0. Blacksmith's published `latest` Linux artifact is pinned by SHA-256: the installer refuses an upstream change instead of installing unchecked code. A later update requires updating the reviewed URL/hash and rerunning the installer. Read-only runner binaries cannot self-update; track GitHub runner releases/deprecation deadlines and refresh promptly. Setup failure before enabling services must be resolved before any consumer migration.

After installation:

1. Verify `systemctl status vyamoh-ci-{router,cycle,worker}` and `journalctl -u vyamoh-ci-router -u vyamoh-ci-broker`. Do not print credentials or runner diagnostic credential files.
2. Verify the runner group contains only `.github` and the worker is online. Check the current `CI_ROUTING_STATE` timestamp/backend.
3. Set `.github`'s `CI_RUNNER_SMOKE_ENABLED=true` and trigger the pilot PR workflow (manual dispatch works once the workflow exists on the default branch).
4. Verify checkout/Python tests, filesystem isolation, actual cgroup bounds/private-network denial, fresh workspace on the next job, and the selected hosted job. Temporarily choose `github` and test again, then restore `automatic`. Test blocked/stale decisions locally without exhausting real quotas.
5. Check memory/disk pressure, cleanup, service restart/recovery and T3/previews before and after. Reboot recovery needs a coordinated reboot; do not reboot an active server solely to test this.
6. Only then expand the reviewed repository-ID list and selected runner-group repository access together, release the reusable selector, and create consumer PRs preserving existing checks. Measure workloads before moving anything beyond simple checks/aggregation onto DO.

To halt the pilot, disable `vyamoh-ci-cycle.service` and `vyamoh-ci-router.timer` with `systemctl disable --now`, and stop `vyamoh-ci-worker.service`. Delete the dedicated runner/group and routing variables if retiring the feature. Do not delete unrelated runner groups. For rollout rollback, restore previous workflow runner labels first; merely stopping the service would leave migrated jobs queued. Before updating installed code, stop the cycle/controller/worker as the installer does.

## Validation

`python3 -m unittest discover -s tests -v` covers preference, normalized thresholds, the paid buffer, hard-budget drift, missing/malformed data, month reset, stale state and bounded overrides. `actionlint`, `zizmor` and `shellcheck runner/install.sh` validate the workflow/shell surfaces. Live smoke checks are a separate rollout gate and cannot be replaced by unit tests.

Sources: [GitHub JIT runner API](https://docs.github.com/en/rest/actions/self-hosted-runners), [GitHub billing summary](https://docs.github.com/en/rest/billing/usage), [Blacksmith included usage](https://docs.blacksmith.sh/blacksmith-runners/overview), [Blacksmith spending alerts](https://docs.blacksmith.sh/introduction/settings).
