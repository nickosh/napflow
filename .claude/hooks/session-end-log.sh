#!/usr/bin/env bash
# SessionEnd hook: append a one-line mechanical breadcrumb per session to
# .claude/sessions.log (gitignored). The narrative journal stays
# agent-written (docs/JOURNAL.md, rule in CLAUDE.md); this log answers
# "when did sessions happen, at what commit, how dirty was the tree".
# No jq dependency — stdin fields are extracted with sed.
set -u

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

LOG=".claude/sessions.log"

input="$(cat 2>/dev/null || true)"
get() {
  printf '%s' "$input" |
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" |
    head -n 1
}
session_id="$(get session_id)"
reason="$(get reason)"

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
sha="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
dirty="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"

printf '%s | session=%s | reason=%s | %s@%s | dirty=%s\n' \
  "$(date '+%Y-%m-%d %H:%M:%S %z')" \
  "${session_id:-?}" "${reason:-?}" "$branch" "$sha" "$dirty" >> "$LOG"

exit 0
