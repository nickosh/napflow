# napflow — Versioning & Releasing

Status: adopted 2026-07-05 (S1 closeout); experimental-v0.x policy
amended 2026-07-11 (D33); PyPI trusted publishing + dry-run path added
2026-07-11.

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

## Release flow (automated — `.github/workflows/release.yml`)

Releases are tag-driven and publish to PyPI + a GitHub Release in one
pass. `workflow_dispatch` is the **dry-run** path: trigger it manually
(`gh workflow run release.yml` or the Actions tab) to run the full
gate + UI bundle + build + wheel checks and upload the dist artifact —
nothing is published. Validate the dry-run green before the first tag.

1. **Prepare** (one commit, `chore(release): v0.1.0`):
   - bump `version` in `pyproject.toml` to the final number — the
     workflow hard-fails unless `v<version>` equals the pushed tag
     (automated gate; supersedes the old manual check)
   - regenerate the changelog: `uvx git-cliff --tag v0.1.0 -o CHANGELOG.md`
   - write honest release notes: developer preview, experimental v0.x
     compatibility, trusted workspace/localhost posture, known limits
   - tick anything release-worthy in REQUIREMENTS/PLAN; journal entry
2. **Flip the repo public** (owner call 2026-07-11: at the v0.1.0 tag,
   not before). The sdist on PyPI exposes the source regardless; a
   public repo makes the package's GitHub links, issues, and Releases
   real and keeps Actions minutes free.
3. **Tag & push**: `git tag v0.1.0 && git push && git push --tags`
4. **The workflow** (on `v*` tag), three jobs:
   - `build`: tag↔version gate, lint + tests, UI bundle, `uv build`,
     wheel-carries-UI check (NFR-03), `git-cliff --latest` notes;
     dist + notes become workflow artifacts
   - `pypi`: publishes dist to PyPI via trusted publishing (OIDC,
     GitHub environment `pypi`, no tokens; isolated job holds the only
     `id-token: write`)
   - `github-release`: creates the GitHub Release with dist attached —
     deliberately after PyPI, so a failed publish never leaves a
     Release page implying the version is installable; `v0.*` tags are
     auto-marked pre-release (D33)
5. **Verify**: `uv tool install napflow` from real PyPI;
   `napf init && napf check` first-touch (later: `napf run flows/smoke`
   offline, EC34).

A tag is the immutable working checkpoint, not certification that the
known v0.2 hardening backlog is complete. Do not publicly promise the
direct Git install path until FR-1113 lands; verify using the built
release artifact.

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
