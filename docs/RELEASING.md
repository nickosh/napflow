# napflow — Versioning & Releasing

Status: adopted 2026-07-05 (S1 closeout); experimental-v0.x policy
amended 2026-07-11 (D33); PyPI trusted publishing + dry-run path added
2026-07-11; reusable PR/tag gate, artifact refusal, and version-specific
compatibility notes added 2026-07-14.

## Versioning

Single source of truth: `version` in `pyproject.toml`
(`napflow.__version__` reads it via package metadata).

- **Pre-release scheme: `0.1.0.devN`.** N bumps in the commit that
  completes a `PLAN.md` stage — S1 → `dev1`, S2 → `dev2`, S3 → `dev3`,
  S4 → `dev4`. Dev versions are checkpoints, not releases: no tags, no
  changelog regeneration required, nothing published.
- **First real release: `v0.1.0` promotes `dev4`** (owner calls
  2026-07-06 + 2026-07-08; supersedes the original "after S1–S3"
  option): S4 closes as the `0.1.0.dev4` checkpoint, a manual-testing
  window runs on it, then the SAME scope ships as v0.1.0 through the
  flow below — dev4 is the de facto release candidate, only
  release-prep (version bump, changelog, docs ticks) lands between.
- **All v0.x releases are experimental** (D33). `v0.1.0` records the
  first version working end to end; it is not a stability claim.
  Breaking flow, event, API, and UI changes are permitted in later
  `0.x` releases with clear release notes. `schema: napflow/v1` is an
  experimental marker during package v0.x; stable schema/event/API
  compatibility and mandatory migrations begin at package v1.0.
- v0.1 flow files and run histories are not guaranteed to open in future
  versions. v0.2 adds explicit run-format versioning and may provide a
  best-effort read-only adapter, but correctness of the new design wins
  over preserving faulty v0.1 behavior.
- **Workflow flips at the v0.1.0 tag** (owner call 2026-07-08): no
  more direct commits to `main` — feature branches + PRs (conventional
  commits feed git-cliff), changelog regenerated per release. Per-PR
  CI also closes the NFR-10 batch-push blind spot.
- **Later releases use final package versions.** A release tag must equal
  `v<project.version>` exactly, and a package version containing a PEP 440
  development segment cannot publish even when its tag matches. The stdlib-only
  `tools/check_release_version.py` enforces both rules in the reusable tag gate;
  unit tests exercise matching, mismatching, missing-`v`, and exact `.dev` tags.
- **v0.2.0 promotion:** prepare `project.version = "0.2.0"`, regenerate the
  lockfile/changelog, and run the manual release dry-run on that exact commit
  before merging/tagging. The dry-run artifact metadata must already say
  `0.2.0`; a `v0.2.0` tag against the old `0.1.0` metadata is refused.

## Release flow (automated — `.github/workflows/release.yml`)

Releases are tag-driven and publish to PyPI + a GitHub Release in one
pass. `workflow_dispatch` is the **dry-run** path: trigger it manually
(`gh workflow run release.yml` or the Actions tab) to run the full
gate + UI bundle + build + wheel checks and upload the dist artifact —
nothing is published, even if the dispatch is deliberately run against a tag.
Only a `push` event for an exact valid tag can enter either publishing job.
Validate the dry-run green before every tag.

The tag workflow calls `.github/workflows/ci.yml` through `workflow_call`, so
the same three-platform Python, Vitest, production-build, Playwright, and
dependency-compat jobs gate pull requests and releases. The release build then
rebuilds and smokes the exact artifacts it uploads; it does not rely on an
artifact produced by another job or workflow run.

**Supported artifact boundary (D40).** The supported installation inputs are PyPI
and the wheel or sdist attached to
a GitHub Release. Direct VCS (`git+https`) installs and PEP 517 builds from a
raw checkout are unsupported: `src/napflow/server/static` is generated and
gitignored. This is intentional; napflow's Git-friendly promise applies to
user flow workspaces, not to installing napflow itself from arbitrary source
states.

The release build runs the frontend once, before packaging. Both published
artifacts contain that exact pre-built bundle, and the published sdist can
produce its wheel without Node. After building the sdist, the focused boundary
check is:

```sh
uv run python tools/smoke_release_artifact.py dist
```

It verifies bundle membership in the sdist, blocks Node/npm while building a
wheel from the sdist, installs that wheel into an isolated environment, runs
both public Python API forms against the scaffold smoke flow, executes the
installed `napf ui --no-browser`, and fetches the compiled root, JavaScript
and stylesheet assets (including referenced lazy chunks), and workspace API.
When passed a release directory, it also requires exactly one direct wheel and
compares every non-`RECORD` payload byte with the no-Node sdist rebuild, in
addition to requiring exact sdist/wheel static trees. The reusable PR gate runs
the smoke on Linux, and the release build reruns it on the exact upload set.

Frontend license notices are generated from the locked npm dependency tree.
After `npm ci`—and whenever `ui/package-lock.json` changes—regenerate and audit
the checked-in notice before packaging:

```sh
uv run python tools/generate_frontend_notices.py
```

`THIRD_PARTY_NOTICES` is package license metadata and must be present under the
wheel's `.dist-info/licenses/` directory. The artifact smoke above checks both
the notice and the UI bundle survive the release-sdist-to-wheel boundary.

1. **Prepare** (one commit, for example `chore(release): v0.2.0`):
   - bump `version` in `pyproject.toml` to the final number — the
     workflow hard-fails unless `v<version>` equals the pushed tag and refuses
     `.dev` versions (automated, independently tested gate)
   - regenerate the changelog, for example:
     `uvx git-cliff --tag v0.2.0 -o CHANGELOG.md`
   - release notes: the workflow prepends
     `docs/release-notes-preamble-v0.md` (developer preview,
     experimental v0.x compatibility, trusted workspace/localhost
     posture) to every `v0.*` release's git-cliff body. When
     `docs/release-notes-<tag>.md` exists, it is inserted before that generated
     body; `docs/release-notes-v0.2.0.md` carries v0.2's concrete format breaks
     and best-effort-reader notes. Re-read every applicable note each release
     and keep it honest
   - tick anything release-worthy in REQUIREMENTS/PLAN; journal entry
2. **Repository visibility**: the v0.1.0 one-time public-repository flip is
   historical and already complete. The sdist exposes source regardless;
   keeping the repository public makes package links, issues, Releases, and
   Actions behavior real.
3. **Tag & push**, for example: `git tag v0.2.0 && git push && git push --tags`
4. **The workflow** (on a pushed `v*` tag), four jobs:
   - `gate`: invokes the exact reusable PR CI workflow. Its release-version job
     refuses tag/package mismatch and `.dev` publication before build or OIDC.
   - `build`: after that gate, audits notices, builds the UI once, runs
     `uv build --clear`, smokes the exact sdist/direct-wheel set, and generates
     `git-cliff --latest` notes; dist + notes become workflow artifacts.
   - `pypi`: publishes dist to PyPI via trusted publishing (OIDC,
     GitHub environment `pypi`, no tokens; isolated job holds the only
     `id-token: write`)
   - `github-release`: creates the GitHub Release with dist attached —
     deliberately after PyPI, so a failed publish never leaves a
     Release page implying the version is installable; `v0.*` tags are
     auto-marked pre-release (D33)
5. **Verify**: `uv tool install napflow` from real PyPI; then in a fresh
   directory run `napf init`, `napf check`, and `napf run flows/smoke` offline
   (EC34), plus open `napf ui` when release verification requires the browser
   path beyond the automated artifact smoke.

A tag is the immutable working checkpoint, not certification that the
known v0.2 hardening backlog is complete. Do not advertise direct Git or raw
checkout installation; verify installation using the built release artifact.

## PyPI one-time setup (before the first tag)

- Create a PyPI account (2FA is mandatory on PyPI).
- Add a **pending trusted publisher** (PyPI → account → Publishing):
  project `napflow`, owner `nickosh`, repository `napflow`, workflow
  `release.yml`, environment `pypi`. The first successful publish
  claims the name (still free as of 2026-07-11).
- No API tokens exist anywhere in this flow — PyPI verifies the
  workflow's OIDC identity. Works from a private repo too; repo
  visibility is irrelevant to publishing.
- After the first publish, NFR-03's `uv tool install napflow` is real;
  update the README install section.

## Non-goals for now

- No release branches, no backports — `main` is the only line.
- No signed artifacts / SLSA until there are actual users asking.
