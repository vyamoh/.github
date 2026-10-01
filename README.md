# Vyamoh automation

Private shared automation and repository policy for the Vyamoh organization.

| Change | Edit here | Adoption |
| --- | --- | --- |
| Shared dependency audit or aggregate CI gate | `.github/workflows/dependency-audit.yml`, `actions/require-success` | Publish a tested `v1.x.y` release; callers on `v1` update together |
| Renovate defaults | `renovate-config.json` | Consumers read the default branch on their next Renovate run |
| Node runtime approval / Cloudflare grouping | `renovate-node.json`, `renovate-cloudflare.json` | Optional presets, layered after the default |
| Repository merge settings and required checks | `policy/repositories.json` | Review the plan and run the explicit apply command |
| New-repository starting points | `workflow-templates/` | Templates seed files; they do not update existing copies |
| Swarm orchestration and model policy | [review-swarm](https://github.com/vyamoh/review-swarm) | Keep its independently tested `v1` channel |

The private repository must remain accessible to Actions in this organization.
Renovate must have access too; its current installation covers all organization
repositories. These are independent permissions. No personal access token is
passed to consumer CI or stored in this repository.

Use this Renovate baseline, retaining only domain-specific exceptions locally:

```json
{
  "extends": ["local>vyamoh/.github:renovate-config"]
}
```

Optional presets are `local>vyamoh/.github:renovate-node` and
`local>vyamoh/.github:renovate-cloudflare`. The baseline delays normal updates
seven days, allows security updates without that delay, preserves monthly
lockfile maintenance and requires approval for major/TypeScript updates.
Renovate rebases and automatically merges eligible PRs after checks pass on an
up-to-date branch. Renovate performs the merge on a subsequent bot run so its
release-age checks remain part of the decision. Major/TypeScript updates still
require dashboard approval before PR creation; after approval they can automerge.
Consumers inherit this policy from the default branch without workflow updates.
Third-party Actions are digest-pinned; first-party
major release channels intentionally remain movable. Renovate policy does not
replace each package manager's committed install-time cooldown.

## Shared workflows

```yaml
jobs:
  audit:
    uses: vyamoh/.github/.github/workflows/dependency-audit.yml@v1
    with:
      working-directories: '[".", "infra"]'
```

The audit supports `working-directories` as a JSON array, `node-version-file`
(default `.nvmrc`), optional `node-version`, `package-json-file` (default
`package.json`), optional `pnpm-version`, and `audit-level` (default `high`).
Toolchain metadata is read from the repository root unless overridden. Each
workspace installs its lockfile without lifecycle scripts and runs its own audit.
Schedule it in the caller; scheduled scans are not required PR checks.

Use `actions/require-success@v1` with `results: ${{ toJSON(needs) }}` in an
`if: always()` aggregate job named `ci`. It rejects failed, skipped, cancelled,
missing and malformed required results. Repositories with conditional jobs can
retain their domain-specific aggregator rather than declaring skipped jobs safe.
Existing swarm consumers keep their evidence-producing aggregate instead.

Preserve application-specific CI and deployments: Prisma/D1 checks in Movies,
Workers/Access checks in Ayana, macOS packaging in Vatya, Python/TypeScript in
Avidya, extension packaging in Yukti, static validation in Vyamoh, and offline
factory tests in Vyakriti. Live model calls, video rendering and narration are
not part of the generic PR gate.

## Releases

After a reviewed PR merges, run **Release shared automation** on `main` with a
stable version such as `v1.0.0`. The workflow validates the exact source revision,
creates an immutable version tag/release, then moves only its major channel.
Existing version tags cannot be replaced, and a channel cannot move backwards
to an older semantic version. Rerun the same version to repair an interrupted
release/channel update. A new `v2` leaves `v1` unchanged.

Bootstrap consumer PRs use the published immutable preview commit so they can
be validated before this repository's first merge. Once `v1.0.0` is released,
switch those reviewed callers to `v1`. Consumers requiring controlled rollout
can retain a version tag or SHA instead.

## Repository policy

`policy/repositories.json` is the source of truth. It creates organization-level
rulesets for default-branch PR/CI protection, Socket, swarm approval, up-to-date
branches where already required, and domain-specific checks. It preserves zero
required human approvals for this one-seat organization and grants no bypass.
The shared engine requires deterministic CI, not its own AI review. Auto-merge
is enabled as a repository capability; no PR is automatically opted in.

Generate the proposed API changes without writing to GitHub:

```sh
python3 scripts/repo_policy.py plan --output policy-plan.json
```

Review the JSON, then apply it from this trusted checkout using an authenticated
organization owner account:

```sh
python3 scripts/repo_policy.py apply --plan policy-plan.json
```

Apply recomputes the plan and refuses changed GitHub/configuration state. It
also requires the shared Renovate adoption on each default branch and successful
required check contexts from each repository's latest merged PR. It saves all
previous values locally, creates the shared rules before disabling the explicitly
audited legacy repository rulesets, and stops on the first API error. API writes
are not transactional: after a partial failure, regenerate and review a plan
before retrying. Unrelated rulesets are not modified. Never use this tool to
bypass a failing PR or paper over a missing check provider.

`policy/legacy-rulesets.json` records the five prior main rulesets. If one has
changed since the audit, planning refuses to retire it until the changed policy
is reviewed. `policy/exported-rulesets.json` is an importable snapshot generated
by `python3 scripts/repo_policy.py export`; CI checks it matches the source.

Initial rollout order:

1. Merge this shared PR and release `v1.0.0`.
2. Verify and merge the nine adoption PRs, updating the shared preview refs to
   `v1`. For changed swarm controls, inspect the diff and manually dispatch the
   trusted wrapper on the PR branch with that PR's successful CI run ID.
3. Confirm Socket and the required aggregate checks actually report for each
   repo. Vatya keeps its local Mac runner; restore it if a changed path requires
   a native job.
4. Generate/review/apply the settings plan. Confirm effective default-branch
   rules and repository settings afterwards.

## Validation

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_config.py
actionlint
zizmor --offline --min-severity medium .github/workflows
```

CI also validates the Renovate presets with an exact Renovate version and runs
the workflow linters. The workflow security gate fails medium/high findings;
low-severity syntax migration suggestions are advisory. Package advisories found
by consumer audits must be fixed or explicitly assessed in those repositories;
the common threshold must not be lowered to hide them.

## Cost-aware runner pilot

The [runner controller and installation guide](runner/README.md) describes the isolated DO worker, usage-based Linux routing, and staged smoke verification. Consumer workflows migrate only after the pilot passes.
