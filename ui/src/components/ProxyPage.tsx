import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { JsonEditor } from "./JsonEditor";
import type { JsonEditorHandle } from "./JsonEditor";
import { pathOf } from "../format";
import type { HeldHead, ProxySnapshot } from "../types";

type Props = {
  proxy: ProxySnapshot;
  onForward: (body: string) => void;
  onDrop: () => void;
  children?: ReactNode;
};

function isForwardable(head: HeldHead | null, editor: JsonEditorHandle | null): boolean {
  return !!head && !!editor && editor.contentIsObject();
}

export function ProxyPage({ proxy, onForward, onDrop, children }: Props) {
  const editorRef = useRef<JsonEditorHandle>(null);
  const headIdRef = useRef<string | null>(null);
  const loadedRef = useRef("");
  const dirtyRef = useRef(false);
  const [valid, setValid] = useState(false);
  const head = proxy.head ?? null;
  const onEditorChange = useCallback(() => {
    dirtyRef.current = true;
    setValid(!!headIdRef.current && !!editorRef.current?.contentIsObject());
  }, []);

  useEffect(() => {
    const ed = editorRef.current;
    const headId = head?.id ?? null;
    const body = head?.body || "";
    if (headId !== headIdRef.current) {
      headIdRef.current = headId;
      dirtyRef.current = false;
      if (head && ed) {
        ed.load(body);
        loadedRef.current = body;
      } else {
        loadedRef.current = "";
      }
    } else if (!dirtyRef.current && head && ed && body !== loadedRef.current) {
      ed.load(body);
      loadedRef.current = body;
    }
    setValid(isForwardable(head, ed));
  }, [head]);

  const noHead = !head;
  const err = noHead ? "" : valid ? proxy.error || "" : "Forward requires a JSON object";

  return (
    <div id="page-proxy" className={proxy.intercept ? "page holding" : "page"}>
      {proxy.intercept ? (
        <section className="proxy-col intercept">
          <div className="card queue-card">
            <h2>
              Held queue <span className="count">{proxy.queue.length}</span>
            </h2>
            <div id="pending">
              {!proxy.queue.length ? (
                <div className="empty">No held invokes</div>
              ) : (
                proxy.queue.map((item, i) => (
                  <div key={item.id} className={i === 0 ? "pending head" : "pending"} title={item.session_id}>
                    <div className="sid">{item.session_id}</div>
                    <div className="meta">
                      {item.method || "POST"} {pathOf(item.url)}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
          <div className="card editor-card">
            <h2>Head request</h2>
            <JsonEditor
              ref={editorRef}
              className="intercept-editor"
              hidden={!head}
              onChange={onEditorChange}
            />
            {!head ? <div id="editor-empty" className="empty">No held invoke</div> : null}
            {err ? <div className="err">{err}</div> : null}
            <div id="intercept-actions">
              <button
                type="button"
                className="primary"
                disabled={noHead || !valid}
                onClick={() => {
                  const ed = editorRef.current;
                  if (!ed) return;
                  onForward(ed.getText());
                  dirtyRef.current = false;
                }}
              >
                Forward
              </button>
              <button
                type="button"
                className="danger"
                disabled={noHead}
                onClick={() => {
                  onDrop();
                  dirtyRef.current = false;
                }}
              >
                Drop
              </button>
            </div>
          </div>
        </section>
      ) : null}
      <section className="proxy-col trace">{children}</section>
    </div>
  );
}
