# napflow — Versioning & Releasing

Status: adopted 2026-07-05 (S1 closeout).

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
- After 0.1.0: SemVer; breaking flow-format changes bump the
  `schema: napflow/vN` marker, not just the package version.
- **Workflow flips at the v0.1.0 tag** (owner call 2026-07-08): no
  more direct commits to `main` — feature branches + PRs (conventional
  commits feed git-cliff), changelog regenerated per release. Per-PR
  CI also closes the NFR-10 batch-push blind spot.

## Release flow (automated — `.github/workflows/release.yml`)

Releases are tag-driven. The workflow is already in place and inert
until the first tag.

1. **Prepare** (one commit, `chore(release): v0.1.0`):
   - bump `version` in `pyproject.toml` to the final number
   - regenerate the changelog: `uvx git-cliff --tag v0.1.0 -o CHANGELOG.md`
   - tick anything release-worthy in REQUIREMENTS/PLAN; journal entry
2. **Tag & push**: `git tag v0.1.0 && git push && git push --tags`
3. **The workflow** (on `v*` tag): runs lint + tests, builds sdist +
   wheel with `uv build`, generates release notes with
   `git-cliff --latest`, and creates the GitHub Release with the dist
   artifacts attached.
4. **Verify**: `uv tool install` from the release artifacts;
   `napf init && napf check` first-touch (later: `napf run flows/smoke`
   offline, EC34).

## PyPI (deferred, pinned here so it's a checklist not a research task)

When going public on PyPI:

- Reserve the `napflow` name early (first upload claims it).
- Use **trusted publishing** (OIDC — no long-lived API tokens): add the
  GitHub repo as a trusted publisher in PyPI project settings, then
  append a `pypi` job to `release.yml` with `permissions: id-token: write`
  using `pypa/gh-action-pypi-publish`.
- After that, NFR-03's `uv tool install napflow` is real; update the
  README install section.

## Non-goals for now

- No release branches, no backports — `main` is the only line.
- No signed artifacts / SLSA until there are actual users asking.
