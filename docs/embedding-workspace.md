# Embedding napflow in an existing project

A napflow workspace is rooted by `napflow.yaml`. Every configured directory
must stay inside that root: use downward paths, never `..` or absolute paths
(D42). If napflow belongs to a larger repository, put `napflow.yaml` at the
repository level and choose roots that fit its layout.

```yaml
schema: napflow/v1

flows:
  root: "qa/flows"
  main: "qa/flows/main"

data:
  root: "tests/napflow-data"

environments:
  root: "."
  default: ".env.test"
  secrets: []
```

The equivalent scaffold command is:

```sh
napf init --flows-root qa/flows --data-root tests/napflow-data
```

Existing root directories are reused without overwriting their contents.
Required paths that are files, symlinks, junctions, or other non-directories
fail during preflight before `napflow.yaml` is written. Roots also cannot
overlap a file the scaffold plans to own, such as `napflow.yaml`,
`.gitignore`, or `flows/main/flow.yaml`. An existing scaffold source is reused
only when it is a regular file; a directory or link in that file role fails
without changing the project. Root Git metadata keeps its separate
inspect/skip behavior. Use the root options when the plain `flows/` or `data/`
names already belong to the host project.

`environments.root` is the one root allowed to be `.` (or `./`): that lets
napflow discover conventional host-project dotenv files without copying them.
Discovery is non-recursive and recognizes the literal filenames `.env`,
`.env.*`, and `*.env`. Select those exact filenames with `--env` or
`environments.default`; process environment variables still override file
values. A custom environment subdirectory passed to init is created empty;
basic init never creates a profile file.

Git metadata remains user-owned. `napf init --example` ignores only the exact
sensitive `.env` file it creates, anchored at the configured environment root;
it never adds a broad root `*.env` rule. `napf check` reports W108 when an
actual profile is not covered by the workspace-root `.gitignore`, and W109 for
napflow's fixed root metadata policy. Both are advisory and
`--no-git-meta-check` suppresses them; no command other than init edits these
files.
