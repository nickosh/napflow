import { useEffect, useRef, useState } from "react";

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

const STATE_COLORS: Record<string, string> = {
  running: "#1565c0",
  passed: "#2e7d32",
  failed: "#c62828",
  error: "#b71c1c",
  aborted: "#616161",
  incomplete: "#ef6c00",
  indeterminate: "#ef6c00",
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
      data-testid={testId}
      data-state={state}
      style={{
        background: STATE_COLORS[state] ?? "#616161",
        color: "#fff",
        borderRadius: 8,
        fontSize: 11,
        fontWeight: 600,
        padding: "1px 8px",
      }}
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
        style={{ margin: "4px 0 6px 92px", color: "#666" }}
      >
        loading full event…
      </p>
    );
  }
  if (expansion.error !== null) {
    return (
      <p
        data-testid="run-event-detail-error"
        style={{ margin: "4px 0 6px 92px", color: "#c62828" }}
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
        background: "#f7f7f7",
        borderRadius: 4,
        maxHeight: 300,
        overflow: "auto",
        whiteSpace: "pre-wrap",
        wordBreak: "break-all",
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
      style={{ borderBottom: "1px solid #f0f0f0" }}
    >
      <div
        onClick={expansion.toggle}
        style={{
          display: "flex",
          gap: 8,
          padding: "1px 0",
          cursor: "pointer",
          alignItems: "baseline",
          whiteSpace: "nowrap",
        }}
      >
        <span style={{ color: "#aaa", minWidth: 84, fontVariantNumeric: "tabular-nums" }}>
          {clock(record.ts)}
        </span>
        {record.frame !== undefined && record.frame !== ROOT_FRAME && (
          <span style={{ color: "#7b1fa2" }}>{record.frame}</span>
        )}
        <span style={{ minWidth: 90, fontWeight: 600 }}>{record.node ?? "—"}</span>
        <span style={{ minWidth: 120, color: "#555" }}>{record.event}</span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", color: "#333" }}>
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
      style={{ borderBottom: "1px solid #f0f0f0" }}
    >
      <div
        onClick={expansion.toggle}
        style={{
          display: "flex",
          gap: 8,
          padding: "1px 0",
          cursor: "pointer",
          alignItems: "baseline",
          whiteSpace: "nowrap",
        }}
      >
        <span style={{ color: "#aaa", minWidth: 84, fontVariantNumeric: "tabular-nums" }}>
          {clock(record.ts)}
        </span>
        <span style={{ color: "#888", minWidth: 90, fontFamily: "ui-monospace, monospace" }}>
          {String(record.msg_id ?? "—")}
        </span>
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            color: "#333",
            fontFamily: "ui-monospace, monospace",
          }}
        >
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
    return <p style={{ color: "#888", margin: "0.5rem 0" }}>loading…</p>;
  }
  if (runHistory.length === 0) {
    return (
      <p data-testid="history-empty" style={{ color: "#888", margin: "0.5rem 0" }}>
        no runs yet — every run (canvas or `napf run`) lands here
      </p>
    );
  }
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {runHistory.map((entry) => (
        <li
          key={entry.run_id}
          data-testid="history-run"
          onClick={() => void openHistoryRun(entry.run_id)}
          style={{
            display: "flex",
            gap: 10,
            alignItems: "baseline",
            padding: "2px 0",
            cursor: "pointer",
            fontFamily: "ui-monospace, monospace",
            background: entry.run_id === runId ? "#e3f2fd" : undefined,
          }}
        >
          <StateChip state={entry.state} testId="history-state" />
          <span>{entry.run_id}</span>
        </li>
      ))}
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
        borderBottom: "1px solid #e5e5e5",
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
              onClick={() => void rootRunFrame()}
              style={{ fontSize: 11 }}
            >
              root
            </button>
            <button
              data-testid="run-frame-parent"
              onClick={() => void backRunFrame()}
              style={{ fontSize: 11 }}
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
            onClick={() => void pageRunFrames("first")}
            style={{ fontSize: 11 }}
          >
            first children
          </button>
        )}
        {runFrameChildrenHasMore && (
          <button
            data-testid="run-frames-next"
            onClick={() => void pageRunFrames("next")}
            style={{ fontSize: 11 }}
          >
            next children →
          </button>
        )}
      </div>
      {runFrameLoading && (
        <span data-testid="run-frame-loading" style={{ color: "#777" }}>
          loading frame detail…
        </span>
      )}
      {runFrameError !== null && (
        <span data-testid="run-frame-error" style={{ color: "#c62828" }}>
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
                onClick={() => void openRunFrame(summary)}
                title={`${summary.frame} · ${Math.round(summary.duration_ms)}ms`}
                style={{
                  display: "flex",
                  gap: 5,
                  alignItems: "center",
                  whiteSpace: "nowrap",
                  fontSize: 11,
                  fontFamily: "inherit",
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

/** Bottom run surface (FR-1005): live/replayed event stream with
 * expandable full wire detail, plus the run history browser (replay =
 * re-read the JSONL, D13; EC20 dangling starts read `incomplete`). */
export default function RunPanel() {
  const {
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
    pageRunEvents,
  } = useAppStore();
  const listRef = useRef<HTMLDivElement>(null);
  // tail-following with an explicit toggle (owner fork): the button
  // holds its pressed state; scrolling up auto-releases it, scrolling
  // back to the bottom (or pressing it) re-engages
  const [follow, setFollow] = useState(true);
  const recordCount = runView?.recordCount ?? 0;

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

  const tabStyle = (active: boolean): React.CSSProperties => ({
    fontSize: 12,
    padding: "2px 10px",
    cursor: "pointer",
    fontFamily: "inherit",
    border: "none",
    borderBottom: active ? "2px solid #1565c0" : "2px solid transparent",
    background: "none",
    fontWeight: active ? 600 : 400,
  });

  return (
    <section
      data-testid="run-panel"
      style={{
        borderTop: "1px solid #ddd",
        height: 230,
        display: "flex",
        flexDirection: "column",
        fontSize: 12,
        background: "#fcfcfc",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "0.3rem 1rem",
          borderBottom: "1px solid #eee",
        }}
      >
        <button
          data-testid="tab-events"
          style={tabStyle(tab === "events")}
          disabled={runView === null}
          onClick={() => openRunPanel("events")}
        >
          events
        </button>
        <button
          data-testid="tab-history"
          style={tabStyle(tab === "history")}
          onClick={() => openRunPanel("history")}
        >
          history
        </button>
        {runView !== null && (
          <>
            <StateChip state={runView.state} />
            <span style={{ color: "#888", fontFamily: "ui-monospace, monospace" }}>
              {runId}
            </span>
            {runView.durationMs !== null && (
              <span>{Math.round(runView.durationMs)}ms</span>
            )}
            <span data-testid="run-asserts">
              asserts <span style={{ color: "#2e7d32" }}>{runView.asserts.passed}✓</span>{" "}
              <span style={{ color: runView.asserts.failed > 0 ? "#c62828" : "#888" }}>
                {runView.asserts.failed}✗
              </span>
            </span>
            {runView.errorReason !== null && (
              <span style={{ color: "#b71c1c" }} title={runView.errorReason}>
                {runView.errorReason}
              </span>
            )}
          </>
        )}
        {selectedNode !== null && (
          <button
            data-testid="run-filter"
            title="showing this node's events only — click to clear"
            onClick={() => selectNode(null)}
            style={{
              fontSize: 11,
              cursor: "pointer",
              fontFamily: "inherit",
              borderRadius: 8,
              border: "1px solid #1565c0",
              color: "#1565c0",
              background: "#e3f2fd",
              padding: "0 8px",
            }}
          >
            {selectedNode} ✕
          </button>
        )}
        {runSelection !== null && (
          <button
            data-testid="traffic-filter"
            title="showing the messages that crossed this wire/port — click to clear"
            onClick={() => selectRunTraffic(null)}
            style={{
              fontSize: 11,
              cursor: "pointer",
              fontFamily: "inherit",
              borderRadius: 8,
              border: "1px solid #1565c0",
              color: "#1565c0",
              background: "#e3f2fd",
              padding: "0 8px",
            }}
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
            onClick={() => {
              const next = !follow;
              setFollow(next);
              if (next && listRef.current !== null) {
                listRef.current.scrollTop = listRef.current.scrollHeight;
              }
            }}
            style={{
              fontSize: 12,
              padding: "2px 10px",
              cursor: "pointer",
              fontFamily: "inherit",
              borderRadius: 3,
              border: "1px solid #1565c0",
              // pressed = held down: filled while following
              background: follow ? "#1565c0" : "#fff",
              color: follow ? "#fff" : "#1565c0",
            }}
          >
            ⇣ follow
          </button>
        )}
        {runLive && (
          <button
            data-testid="abort-run"
            onClick={() => void abortRun()}
            style={{
              fontSize: 12,
              padding: "2px 10px",
              cursor: "pointer",
              fontFamily: "inherit",
              color: "#c62828",
              border: "1px solid #c62828",
              borderRadius: 3,
              background: "#fff",
            }}
          >
            ■ abort
          </button>
        )}
        <button
          data-testid="exit-run"
          onClick={exitRun}
          title={runLive ? "stop watching (the run keeps going)" : "back to editing"}
          style={{
            fontSize: 12,
            padding: "2px 10px",
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          ✕ close
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
        style={{ flex: 1, overflowY: "auto", padding: "0.3rem 1rem" }}
      >
        {tab === "events" && runSource === "history" && <FrameBrowser />}
        {tab === "events" && runReplayLoading && (
          <p data-testid="run-replay-loading" style={{ color: "#666", margin: 0 }}>
            loading replay page…
          </p>
        )}
        {tab === "events" && runReplayError !== null && (
          <p data-testid="run-replay-error" style={{ color: "#c62828", margin: 0 }}>
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
                  onClick={() => void pageRunEvents("first")}
                  style={{ fontSize: 11 }}
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
                    onClick={() => void pageRunEvents("first")}
                    style={{ fontSize: 11 }}
                  >
                    first
                  </button>
                )}
                {runEventPageHasMore && (
                  <button
                    data-testid="run-events-next"
                    onClick={() => void pageRunEvents("next")}
                    style={{ fontSize: 11 }}
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
        ) : runSelection !== null ? (
          <>
            {messages.length === 0 && (
              <p data-testid="traffic-empty" style={{ color: "#888", margin: "0.5rem 0" }}>
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
        ) : (
          <>
            {overflow > 0 && (
              <p style={{ color: "#888", margin: "0 0 4px" }}>
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
          </>
        )}
      </div>
    </section>
  );
}
