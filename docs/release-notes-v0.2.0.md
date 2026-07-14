## v0.2.0 compatibility and format notes

v0.2.0 deliberately changes several experimental v0.1 surfaces. There is no
automatic rewrite or compatibility guarantee during package v0.x; review
these points before upgrading a workspace or a history consumer.

- **Flow files:** `schema: napflow/v1` remains the current experimental marker
  and the v0.2 flow-YAML shape is otherwise compatible on a best-effort basis.
  v0.2 now rejects YAML anchors and aliases that v0.1 accepted; expand shared
  values into ordinary mappings/sequences before upgrading. The removed
  manifest settings `defaults.run.body_capture_mb` and
  `defaults.run.run_capture_mb` are now validation errors and must be deleted.
- **Run history:** new production logs start with `format: napflow-run/1` and
  declare `content-blobs/1`. Large request, response, message, Log, error, and
  End values use hash-verified blob descriptors instead of previews or
  truncation. Markerless v0.1 logs are read best-effort; replay and report
  readers otherwise require a `run_started` record at `seq: 1` and refuse
  malformed/null metadata, unknown/newer format majors, or unknown storage
  features rather than guessing. v0.1 readers do not understand v0.2
  blob-backed records.
- **Events and secrets:** canonical JSONL and the local WebSocket now preserve
  raw full values. Effective prepared requests, complete response aggregates,
  and `frame_finished` records replace the old preview-oriented observations.
  Declared-secret masking applies only to terminal and JSON/JUnit presentation;
  it does not sanitize local JSONL, blobs, or the browser history view.
- **Replay API:** historical inspection uses versioned `napflow-replay/1`
  cursor pages, bounded scalar/view projections, direct-child frame pages, and
  per-sequence lazy content reads. Clients that assumed one unbounded event
  response must adopt the page cursors and detail endpoint.
- **Python and distribution:** `run_flow`, `run_flow_async`, and reusable
  Workspace/Flow handles are public from `napflow.core`. Supported installs are
  PyPI or GitHub Release wheels/sdists containing the compiled UI; direct VCS
  and raw-checkout package builds are unsupported.

Workspaces remain trusted local code. Raw histories can contain credentials and
complete payloads. v0.2.0 does not provide secure export, encryption, remote
authentication, runtime-acquired-secret registration, descendant process-tree
cleanup, or a hard preemptive deadline for synchronous Jinja rendering.
