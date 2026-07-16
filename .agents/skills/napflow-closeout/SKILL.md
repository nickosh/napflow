---
name: napflow-closeout
description: Close napflow development sessions, useful development slices, and milestones by reconciling implementation evidence with the project memory documents. Use when wrapping up or handing off work, ending a state-changing session, finishing a PLAN milestone, updating project memory after implementation, or preparing a session or milestone closeout. Do not use for ordinary in-progress documentation edits or planning-only discussion that produced no durable decision, blocker, reproduced edge case, or repository change.
---

# Napflow Closeout

Perform an evidence-based closeout without duplicating project knowledge inside this skill. Treat `AGENTS.md` and the documents it names as authoritative.

## Select the closeout mode

Choose exactly one mode before editing:

- **Session**: Use for state-changing work, an independently useful development
  slice, or a useful handoff that does not complete a `docs/PLAN.md` milestone.
- **Milestone**: Use only when the named milestone's deliverables and Definition of Done are satisfied with implementation and test evidence.
- **No update**: Use for a read-only or abandoned session with no durable decision, blocker, reproduced edge case, useful handoff, or repository change. Report why no memory edit is warranted.

Default to Session when milestone completion is uncertain. Never infer completion from elapsed effort, a nearly complete checklist, or documentation alone.

## Gather evidence

1. Read `AGENTS.md`, `docs/PLAN.md`, the newest relevant `docs/JOURNAL.md` entries, and every authoritative specification affected by the work.
2. Inspect `git status --short --branch`, staged and unstaged diffs, and relevant recent commits. Preserve unrelated user changes.
3. Collect verification evidence from the current work. Re-run only checks proportionate to the closeout risk; run the milestone's required gate when claiming milestone completion.
4. Classify the landed changes: behavior or schema, requirement, edge case, durable decision, release process, public contract, or project workflow.
5. Distinguish completed work from planned work. Do not claim a passing test,
   owner decision, or resolved issue without evidence.

## Reconcile project memory

Audit every row, but edit only rows supported by the evidence.

| Memory surface | Update rule |
| --- | --- |
| `docs/JOURNAL.md` | Prepend one dated entry for a Session or Milestone closeout. Keep it to 2–5 concise bullets covering done, decided when applicable, verification or blockers, and the next technical action. Do not create an entry in No update mode. |
| `docs/PLAN.md` | Update when sequencing or scope changed. Tick a milestone or deliverable only in Milestone mode and only when its evidence and Definition of Done are satisfied. Preserve historical course corrections. |
| Relevant authoritative spec | Update in the same change whenever behavior, schema, API, event, CLI, or format semantics changed. Treat specs as hypotheses, not immutable history. |
| `docs/REQUIREMENTS.md` | Tick a requirement only when the implementation landed with a test. Cite the evidence concisely. Do not tick from a plan, baseline, skip, or expected failure. |
| `docs/EDGE_CASES.md` | Record a reproduced edge case or its tested resolution. Keep `OPEN` for planned or documentation-only treatment; allocate the next available EC number for a new case. |
| `docs/DECISIONS.md` | Record only durable architectural or owner decisions. Never manufacture owner intent; allocate the next available D number for a new decision. |
| `docs/RELEASING.md`, release notes, changelog | Update only when the release contract or current release state changed. Generate the changelog through the documented release workflow rather than editing generated history casually. |
| `README.md`, `docs/PRODUCT.md`, `AGENTS.md` | Update only when the public status, product direction, or durable repository-wide instructions changed. Do not use these as session logs. |

Do not edit every document merely to show that it was reviewed. Prefer an accurate unchanged file over a speculative synchronization edit.

Project memory records product behavior, engineering decisions, verified
development outcomes, blockers, and future technical work. It does not track
branch names, commit SHAs, PR numbers/status, push/pull/merge steps, or CI
run/job identifiers; keep that delivery bookkeeping in chat, Git history, and
the hosting service. A summarized CI, artifact, or release result belongs in
memory only when it proves a technical, cross-platform, or release contract.
Durable release mechanics belong in `docs/RELEASING.md`, not session entries.

## Close a session

1. Reconcile the matrix.
2. Prepend a single journal entry under the introductory text, newest first.
3. Include a concrete next technical action that lets a fresh agent resume
   without reconstructing the session.
4. Mention technical blockers accurately. Report transient delivery status in
   chat only; do not copy it into project memory.

## Close a milestone

1. Locate the exact milestone checklist and Definition of Done in `docs/PLAN.md`.
2. Verify each claimed deliverable against code, tests, and any required cross-platform, build, performance, or release evidence.
3. Reconcile all affected specifications and ledgers before ticking the milestone.
4. Update requirements only after locating their landing tests.
5. Prepend one journal entry summarizing the milestone outcome, verification, carry items, and next milestone.
6. Leave partial or expected-failure cases open and name their owning future milestone.

## Validate the closeout

1. Review the final diff for unsupported claims, duplicated history, stale references, and accidental edits to unrelated work.
2. Run `git diff --check` and any targeted documentation or skill validation relevant to the changed files.
3. Confirm the journal remains newest first and concise.
4. Confirm planned work is not described as landed and that tests are not described as passing unless observed.
5. Confirm memory edits contain no transient delivery identifiers or steps.
6. Do not commit, push, tag, publish, or open a pull request unless the user separately requested it.

Report the selected mode, memory files changed, verification evidence, important files intentionally left unchanged, and the next action.
