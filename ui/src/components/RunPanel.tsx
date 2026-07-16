import { useEffect, useRef, useState } from "react";
import {
  ArrowLineDown,
  CaretDown,
  CheckCircle,
  ClockCounterClockwise,
  ListDashes,
  Prohibit,
  Stethoscope,
  WarningCircle,
  X,
  XCircle,
} from "@phosphor-icons/react";

import { fetchRunEventDetail } from "../api";
import {
  ROOT_FRAME,
  matchesTraffic,
  messageValue,
  preview,
  summarize,
  trafficLabel,
  type RunFrameSummary,
  type RunRecord,
} from "../runview";
import { useAppStore } from "../store";
import DiagnosticsPanel from "./DiagnosticsPanel";

const STATE_COLORS: Record<string, string> = {
  running: "#5f8fd9",
  passed: "#3a9e83",
  failed: "#c75c5c",
  error: "#b34a4a",
  aborted: "#75798c",
  incomplete: "#bd8a44",
  indeterminate: "#bd8a44",
};

// keep the DOM sane on huge runs — the JSONL stays the full record
const MAX_ROWS = 500;

function StateChip({
  state,
  testId = "run-state",
}: {
  state: string;
  testId?: string;
}) {
  return (
    <span
      className="nf-state"
      data-testid={testId}
      data-state={state}
      style={{ background: STATE_COLORS[state] ?? "#75798c" }}
    >
      {state}
    </span>
  );
}

function clock(ts: string | undefined): string {
  return ts?.slice(11, 23) ?? "";
}

type ReplayTarget = { runId: string; flow: string } | null;

type RecordExpansion = {
  open: boolean;
  toggle: () => void;
  detail: RunRecord | null;
  loading: boolean;
  error: string | null;
};

/** REST pages and production WebSocket records both carry canonical blob
 * descriptors. Fetch and resolve exactly one event only after its row opens;
 * a record without a server run/sequence is the only local fallback. */
function useRecordExpansion(
  record: RunRecord,
  replay: ReplayTarget,
): RecordExpansion {
  const seq = typeof record.seq === "number" ? record.seq : null;
  const remote = replay !== null && seq !== null;
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<RunRecord | null>(
    remote ? null : record,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestGeneration = useRef(0);

  useEffect(
    () => () => {
      requestGeneration.current += 1;
    },
    [],
  );

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (
      !next ||
      !remote ||
      replay === null ||
      seq === null ||
      detail !== null ||
      loading
    ) {
      return;
    }
    const generation = ++requestGeneration.current;
    setLoading(true);
    setError(null);
    void fetchRunEventDetail(replay.runId, replay.flow, seq)
      .then((payload) => {
        if (generation === requestGeneration.current) setDetail(payload.event);
      })
      .catch((caught: unknown) => {
        if (generation === requestGeneration.current) {
          setError(
            `full event unavailable: ${
              caught instanceof Error ? caught.message : String(caught)
            }`,
          );
        }
      })
      .finally(() => {
        if (generation === requestGeneration.current) setLoading(false);
      });
  };

  return { open, toggle, detail, loading, error };
}

function ExpandedRecord({ expansion }: { expansion: RecordExpansion }) {
  if (expansion.loading) {
    return (
      <p
        data-testid="run-event-detail-loading"
        style={{ margin: "4px 0 6px 92px", color: "var(--muted)" }}
      >
        loading full event…
      </p>
    );
  }
  if (expansion.error !== null) {
    return (
      <p
        data-testid="run-event-detail-error"
        style={{ margin: "4px 0 6px 92px", color: "var(--err)" }}
      >
        {expansion.error}
      </p>
    );
  }
  if (expansion.detail === null) return null;
  return (
    <pre
      data-testid="run-event-detail"
      style={{
        margin: "2px 0 6px 92px",
        padding: "6px 8px",
        background: "var(--surface2)",
        border: "1px solid var(--border)",
        borderRadius: "var(--rsm)",
        maxHeight: 300,
        overflow: "auto",
        whiteSpace: "pre-wrap",
        wordBreak: "break-all",
        userSelect: "text",
      }}
    >
      {JSON.stringify(expansion.detail, null, 2)}
    </pre>
  );
}

function EventRow({
  record,
  replay,
}: {
  record: RunRecord;
  replay: ReplayTarget;
}) {
  const expansion = useRecordExpansion(record, replay);
  return (
    <li
      data-testid="run-event"
      data-event={record.event}
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      <div
        onClick={expansion.toggle}
        style={{
          display: "flex",
          gap: 10,
          padding: "2px 0",
          cursor: "pointer",
          alignItems: "baseline",
          whiteSpace: "nowrap",
        }}
      >
        <span
          style={{
            color: "var(--muted)",
            minWidth: 84,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {clock(record.ts)}
        </span>
        {record.frame !== undefined && record.frame !== ROOT_FRAME && (
          <span style={{ color: "var(--accent)" }}>{record.frame}</span>
        )}
        <span style={{ minWidth: 90, fontWeight: 600 }}>
          {record.node ?? "—"}
        </span>
        <span style={{ minWidth: 120, color: "var(--muted)" }}>
          {record.event}
        </span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
          {summarize(record)}
        </span>
      </div>
      {expansion.open && <ExpandedRecord expansion={expansion} />}
    </li>
  );
}

/** One crossed message on the selected wire/port (M5.5): complete value,
 * ts, msg_id — with value_preview fallback for featureless legacy replay. */
function MessageRow({
  record,
  replay,
}: {
  record: RunRecord;
  replay: ReplayTarget;
}) {
  const expansion = useRecordExpansion(record, replay);
  return (
    <li
      data-testid="run-message"
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      <div
        onClick={expansion.toggle}
        style={{
          display: "flex",
          gap: 10,
          padding: "2px 0",
          cursor: "pointer",
          alignItems: "baseline",
          whiteSpace: "nowrap",
        }}
      >
        <span
          style={{
            color: "var(--muted)",
            minWidth: 84,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {clock(record.ts)}
        </span>
        <span style={{ color: "var(--muted)", minWidth: 90 }}>
          {String(record.msg_id ?? "—")}
        </span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
          {preview(messageValue(record), 200)}
        </span>
      </div>
      {expansion.open && (
        <div data-testid="run-message-detail">
          <ExpandedRecord expansion={expansion} />
        </div>
      )}
    </li>
  );
}

function HistoryTab() {
  const { runHistory, runId, openHistoryRun } = useAppStore();
  if (runHistory === null) {
    return <p style={{ color: "var(--muted)", margin: "0.5rem 0" }}>loading…</p>;
  }
  if (runHistory.length === 0) {
    return (
      <p
        data-testid="history-empty"
        style={{ color: "var(--muted)", margin: "0.5rem 0" }}
      >
        no runs yet — every run (canvas or `napf run`) lands here
      </p>
    );
  }
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {runHistory.map((entry) => {
        const StatusIcon =
          entry.state === "passed"
            ? CheckCircle
            : entry.state === "failed" || entry.state === "error"
              ? XCircle
              : WarningCircle;
        return (
          <li
            key={entry.run_id}
            data-testid="history-run"
            className={`nf-row${entry.run_id === runId ? " nf-active" : ""}`}
            onClick={() => void openHistoryRun(entry.run_id)}
            style={{ fontFamily: "var(--mono)", padding: "5px 8px" }}
          >
            <StatusIcon
              size={14}
              weight="fill"
              color={STATE_COLORS[entry.state] ?? "var(--muted)"}
            />
            <StateChip state={entry.state} testId="history-state" />
            <span>{entry.run_id}</span>
          </li>
        );
      })}
    </ul>
  );
}

function frameLabel(summary: RunFrameSummary): string {
  const index =
    typeof summary.loop_index === "number" ? ` #${summary.loop_index}` : "";
  return `${summary.kind} ${summary.flow}${index}`;
}

/** Direct-child summaries are one replaceable bounded page. Earlier loop
 * iterations remain reachable via "first" and later ones via "next"; no
 * expanding frame tree is retained in browser memory. */
function FrameBrowser() {
  const {
    runRootFrame,
    runFramePath,
    runFrameChildren,
    runFrameChildrenAfterSeq,
    runFrameChildrenHasMore,
    runFrameLoading,
    runFrameError,
    openRunFrame,
    backRunFrame,
    rootRunFrame,
    pageRunFrames,
  } = useAppStore();
  const active = runFramePath.at(-1) ?? null;
  return (
    <div
      data-testid="run-frame-browser"
      style={{
        borderBottom: "1px solid var(--border)",
        paddingBottom: 4,
        marginBottom: 4,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <strong>frame</strong>
        {active === null ? (
          <span data-testid="run-frame-active">root {runRootFrame}</span>
        ) : (
          <>
            <button
              data-testid="run-frame-root"
              className="nf-btn"
              onClick={() => void rootRunFrame()}
              style={{ fontSize: 11, padding: "1px 8px" }}
            >
              root
            </button>
            <button
              data-testid="run-frame-parent"
              className="nf-btn"
              onClick={() => void backRunFrame()}
              style={{ fontSize: 11, padding: "1px 8px" }}
            >
              ← parent
            </button>
            <span data-testid="run-frame-active" title={active.frame}>
              {frameLabel(active)}
            </span>
            <StateChip state={active.state} testId="run-frame-state" />
          </>
        )}
        <span style={{ flex: 1 }} />
        {runFrameChildrenAfterSeq > 0 && (
          <button
            data-testid="run-frames-first"
            className="nf-btn"
            onClick={() => void pageRunFrames("first")}
            style={{ fontSize: 11, padding: "1px 8px" }}
          >
            first children
          </button>
        )}
        {runFrameChildrenHasMore && (
          <button
            data-testid="run-frames-next"
            className="nf-btn"
            onClick={() => void pageRunFrames("next")}
            style={{ fontSize: 11, padding: "1px 8px" }}
          >
            next children →
          </button>
        )}
      </div>
      {runFrameLoading && (
        <span data-testid="run-frame-loading" style={{ color: "var(--muted)" }}>
          loading frame detail…
        </span>
      )}
      {runFrameError !== null && (
        <span data-testid="run-frame-error" style={{ color: "var(--err)" }}>
          {runFrameError}
        </span>
      )}
      {runFrameChildren.length > 0 && (
        <ul
          data-testid="run-frame-children"
          style={{
            display: "flex",
            gap: 6,
            margin: "3px 0 0",
            padding: 0,
            overflowX: "auto",
            listStyle: "none",
          }}
        >
          {runFrameChildren.map((summary) => (
            <li key={summary.frame}>
              <button
                data-testid="run-frame-child"
                data-frame={summary.frame}
                className="nf-btn"
                onClick={() => void openRunFrame(summary)}
                title={`${summary.frame} · ${Math.round(summary.duration_ms)}ms`}
                style={{
                  gap: 5,
                  whiteSpace: "nowrap",
                  fontSize: 11,
                  padding: "2px 8px",
                }}
              >
                <StateChip state={summary.state} testId="run-frame-child-state" />
                {frameLabel(summary)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** F1 console (FR-1005): one bottom panel hosting the live/replayed
 * event stream with expandable full wire detail, the run history
 * browser (replay = re-read the JSONL, D13; EC20 dangling starts read
 * `incomplete`), and the checker diagnostics as a third tab. */
export default function RunPanel() {
  const {
    detail,
    runView,
    runId,
    runLive,
    runSource,
    runPanelTab,
    selectedFlow,
    runReplayLoading,
    runReplayError,
    runFramePath,
    runFrameView,
    runEventPage,
    runEventPageAfterSeq,
    runEventPageHasMore,
    runEventPageLoading,
    selectedNode,
    selectNode,
    runSelection,
    selectRunTraffic,
    abortRun,
    exitRun,
    openRunPanel,
    closeRunPanel,
    pageRunEvents,
  } = useAppStore();
  const listRef = useRef<HTMLDivElement>(null);
  // tail-following with an explicit toggle (owner fork): the button
  // holds its pressed state; scrolling up auto-releases it, scrolling
  // back to the bottom (or pressing it) re-engages
  const [follow, setFollow] = useState(true);
  const recordCount = runView?.recordCount ?? 0;
  const diagnostics = detail?.diagnostics ?? [];

  // a fresh run (or replay) starts back at the tail
  useEffect(() => {
    setFollow(true);
  }, [runId]);

  // follow the live tail (replays render settled, no need to chase)
  useEffect(() => {
    if (runLive && follow && listRef.current !== null) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [runLive, follow, recordCount]);

  if (runPanelTab === null) return null;
  const tab = runPanelTab;

  const inFrameDetail = runFramePath.length > 0;
  const activeView = inFrameDetail ? runFrameView : runView;
  const sourceRecords = runEventPage ?? activeView?.records ?? [];
  const records =
    activeView === null
      ? []
      : selectedNode === null
        ? sourceRecords
        : sourceRecords.filter((r) => r.node === selectedNode);
  const overflow =
    activeView === null || runEventPage !== null
      ? 0
      : selectedNode !== null
      ? records.length - MAX_ROWS
      : activeView.recordCount - Math.min(records.length, MAX_ROWS);
  // M5.5: a selected wire/port swaps the stream for its message list
  const messages =
    activeView === null || runSelection === null
      ? []
      : sourceRecords.filter((r) =>
          matchesTraffic(r, runSelection, activeView.scopeFrame),
        );
  const replayTarget: ReplayTarget =
    runId !== null && selectedFlow !== null
      ? { runId, flow: selectedFlow }
      : null;

  return (
    <section
      data-testid="run-panel"
      style={{
        borderTop: "1px solid var(--border)",
        height: 250,
        display: "flex",
        flexDirection: "column",
        fontSize: 12,
        background: "var(--surface)",
        flexShrink: 0,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 12px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <button
          data-testid="tab-events"
          className={`nf-tab${tab === "events" ? " nf-active" : ""}`}
          disabled={runView === null}
          onClick={() => openRunPanel("events")}
        >
          <ListDashes size={13} />
          Events
        </button>
        <button
          data-testid="tab-history"
          className={`nf-tab${tab === "history" ? " nf-active" : ""}`}
          onClick={() => openRunPanel("history")}
        >
          <ClockCounterClockwise size={13} />
          History
        </button>
        <button
          data-testid="tab-diagnostics"
          className={`nf-tab${tab === "diag" ? " nf-active" : ""}`}
          onClick={() => openRunPanel("diag")}
        >
          <Stethoscope size={13} />
          Diagnostics
          {diagnostics.length > 0 && (
            <span className="nf-badge">{diagnostics.length}</span>
          )}
        </button>
        {runView !== null && (
          <>
            <StateChip state={runView.state} />
            <span style={{ color: "var(--muted)", fontFamily: "var(--mono)" }}>
              {runId}
            </span>
            {runView.durationMs !== null && (
              <span>{Math.round(runView.durationMs)}ms</span>
            )}
            <span data-testid="run-asserts">
              asserts{" "}
              <span style={{ color: "var(--ok)" }}>
                {runView.asserts.passed}✓
              </span>{" "}
              <span
                style={{
                  color:
                    runView.asserts.failed > 0 ? "var(--err)" : "var(--muted)",
                }}
              >
                {runView.asserts.failed}✗
              </span>
            </span>
            {runView.errorReason !== null && (
              <span style={{ color: "var(--err-bright)" }} title={runView.errorReason}>
                {runView.errorReason}
              </span>
            )}
          </>
        )}
        {selectedNode !== null && (
          <button
            data-testid="run-filter"
            className="nf-btn nf-btn-accent"
            title="showing this node's events only — click to clear"
            onClick={() => selectNode(null)}
            style={{ fontSize: 11, padding: "0 8px", borderRadius: 8 }}
          >
            {selectedNode} ✕
          </button>
        )}
        {runSelection !== null && (
          <button
            data-testid="traffic-filter"
            className="nf-btn nf-btn-accent"
            title="showing the messages that crossed this wire/port — click to clear"
            onClick={() => selectRunTraffic(null)}
            style={{ fontSize: 11, padding: "0 8px", borderRadius: 8 }}
          >
            {trafficLabel(runSelection)} ×{messages.length} ✕
          </button>
        )}
        <span style={{ flex: 1 }} />
        {runLive && tab === "events" && (
          <button
            data-testid="follow-toggle"
            aria-pressed={follow}
            title="follow the live event tail (scrolling up releases it)"
            className="nf-btn"
            onClick={() => {
              const next = !follow;
              setFollow(next);
              if (next && listRef.current !== null) {
                listRef.current.scrollTop = listRef.current.scrollHeight;
              }
            }}
            style={
              follow
                ? {
                    background: "var(--accent-t)",
                    borderColor: "var(--accent)",
                    color: "var(--accent)",
                  }
                : undefined
            }
          >
            <ArrowLineDown size={12} />
            follow
          </button>
        )}
        {runLive && (
          <button
            data-testid="abort-run"
            className="nf-btn nf-btn-danger"
            onClick={() => void abortRun()}
          >
            <Prohibit size={12} />
            abort
          </button>
        )}
        {runView !== null && (
          <button
            data-testid="exit-run"
            className="nf-btn"
            onClick={exitRun}
            title={
              runLive ? "stop watching (the run keeps going)" : "back to editing"
            }
          >
            <X size={12} />
            close
          </button>
        )}
        <button
          className="nf-iconbtn"
          title="Collapse console"
          onClick={closeRunPanel}
          style={{ padding: 4 }}
        >
          <CaretDown size={14} />
        </button>
      </header>
      <div
        ref={listRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          // 40px of slack: "near the bottom" counts as at the bottom
          const atBottom =
            el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          if (follow !== atBottom) setFollow(atBottom);
        }}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "0.4rem 1rem",
          fontFamily: "var(--mono)",
          fontSize: 11.5,
          lineHeight: 1.75,
          display: "flex",
          flexDirection: "column",
        }}
      >
        {tab === "diag" && <DiagnosticsPanel diagnostics={diagnostics} />}
        {tab === "events" && runSource === "history" && <FrameBrowser />}
        {tab === "events" && runReplayLoading && (
          <p
            data-testid="run-replay-loading"
            style={{ color: "var(--muted)", margin: 0 }}
          >
            loading replay page…
          </p>
        )}
        {tab === "events" && runReplayError !== null && (
          <p
            data-testid="run-replay-error"
            style={{ color: "var(--err)", margin: 0 }}
          >
            {runReplayError}
          </p>
        )}
        {tab === "events" &&
          runSource === "history" &&
          replayTarget !== null &&
          activeView !== null && (
          <div
            data-testid="run-event-pager"
            style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 3 }}
          >
            {runEventPageLoading && <span>loading event page…</span>}
            {runEventPage === null && activeView.recordCount > 0 && (
                <button
                  data-testid="run-events-first"
                  className="nf-btn"
                  onClick={() => void pageRunEvents("first")}
                  style={{ fontSize: 11, padding: "1px 8px" }}
                >
                  browse from first event
                </button>
              )}
            {runEventPage !== null && (
              <>
                <span>page after seq {runEventPageAfterSeq}</span>
                {runEventPageAfterSeq > 0 && (
                  <button
                    data-testid="run-events-first"
                    className="nf-btn"
                    onClick={() => void pageRunEvents("first")}
                    style={{ fontSize: 11, padding: "1px 8px" }}
                  >
                    first
                  </button>
                )}
                {runEventPageHasMore && (
                  <button
                    data-testid="run-events-next"
                    className="nf-btn"
                    onClick={() => void pageRunEvents("next")}
                    style={{ fontSize: 11, padding: "1px 8px" }}
                  >
                    next →
                  </button>
                )}
              </>
            )}
          </div>
        )}
        {tab === "history" ? (
          <HistoryTab />
        ) : tab === "events" && runSelection !== null ? (
          <>
            {messages.length === 0 && (
              <p
                data-testid="traffic-empty"
                style={{ color: "var(--muted)", margin: "0.5rem 0" }}
              >
                nothing crossed here in this run
              </p>
            )}
            <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {messages.slice(-MAX_ROWS).map((record, index) => (
                <MessageRow
                  key={`${runId ?? "live"}:${activeView?.scopeFrame ?? ROOT_FRAME}:${record.seq ?? index}`}
                  record={record}
                  replay={replayTarget}
                />
              ))}
            </ul>
          </>
        ) : tab === "events" ? (
          <>
            {overflow > 0 && (
              <p style={{ color: "var(--muted)", margin: "0 0 4px" }}>
                … {overflow} earlier events (full record in the run's JSONL)
              </p>
            )}
            <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {records.slice(-MAX_ROWS).map((record, index) => (
                <EventRow
                  key={`${runId ?? "live"}:${activeView?.scopeFrame ?? ROOT_FRAME}:${record.seq ?? index}`}
                  record={record}
                  replay={replayTarget}
                />
              ))}
            </ul>
            {records.length === 0 && !runReplayLoading && (
              <p style={{ color: "var(--muted)", margin: "0.5rem 0" }}>
                no events yet — press Run.
              </p>
            )}
          </>
        ) : null}
      </div>
    </section>
  );
}
