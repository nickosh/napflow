import { python } from "@codemirror/lang-python";
import { EditorView, basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

// CodeMirror 6 pane (owner call 2026-07-08, replacing the Monaco
// swap-in): same whole-file editing surface behind the /api/code
// GET/PUT+etag contract, at ~1/9th the bundle weight and with no
// worker, no webfont, and no EditContext workarounds. Lazy-imported so
// the canvas doesn't pay for it until the editor opens.

export default function CodeMirrorPane({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const view = useRef<EditorView | null>(null);
  const latest = useRef(onChange);
  latest.current = onChange;

  useEffect(() => {
    if (host.current === null) return;
    const v = new EditorView({
      doc: value,
      parent: host.current,
      extensions: [
        basicSetup,
        python(),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            latest.current(update.state.doc.toString());
          }
        }),
        EditorView.theme({
          "&": { height: "100%", fontSize: "13px" },
          ".cm-scroller": {
            fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace",
          },
        }),
      ],
    });
    view.current = v;
    return () => {
      view.current = null;
      v.destroy();
    };
    // the view owns the doc after mount; external `value` changes sync
    // via the effect below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // external replacement only (conflict reload): a dispatch for text
    // the view already holds would fight the user's cursor
    const v = view.current;
    if (v !== null && value !== v.state.doc.toString()) {
      v.dispatch({
        changes: { from: 0, to: v.state.doc.length, insert: value },
      });
    }
  }, [value]);

  return <div ref={host} style={{ height: "100%", overflow: "hidden" }} />;
}
