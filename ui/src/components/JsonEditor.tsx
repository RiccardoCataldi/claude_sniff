import JsonView from "react18-json-view";
import "react18-json-view/src/style.css";
import { forwardRef, memo, useCallback, useImperativeHandle, useLayoutEffect, useRef, useState, type MouseEvent } from "react";

export type JsonEditorHandle = {
  getText: () => string;
  load: (text: string) => void;
  contentIsObject: () => boolean;
};

type Mode = "tree" | "text";

type TreeProps = {
  value: unknown;
  collapsed?: number;
};

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function dump(value: unknown): string {
  return value === undefined ? "" : JSON.stringify(value, null, 2);
}

export function JsonTree({ value, collapsed = 2 }: TreeProps) {
  return (
    <div className="json-tree">
      <JsonView
        src={value ?? {}}
        collapsed={collapsed}
        theme="github"
        collapseStringsAfterLength={120}
        collapseObjectsAfterLength={80}
        matchesURL={false}
      />
    </div>
  );
}

type Props = {
  className?: string;
  hidden?: boolean;
  onChange?: () => void;
};

export const JsonEditor = memo(
  forwardRef<JsonEditorHandle, Props>(function JsonEditor({ className, hidden, onChange }, ref) {
  const [mode, setMode] = useState<Mode>("tree");
  const [text, setText] = useState("");
  const [tree, setTree] = useState<unknown>({});
  const [gen, setGen] = useState(0);
  const treeRef = useRef(tree);
  const textRef = useRef(text);
  const modeRef = useRef(mode);
  const onChangeRef = useRef(onChange);

  useLayoutEffect(() => {
    onChangeRef.current = onChange;
    treeRef.current = tree;
    textRef.current = text;
    modeRef.current = mode;
  });

  useImperativeHandle(ref, () => ({
    getText: () => (modeRef.current === "tree" ? dump(treeRef.current) : textRef.current),
    load: (raw: string) => {
      const parsed = parseJson(raw);
      textRef.current = raw;
      setText(raw);
      if (isPlainObject(parsed)) {
        treeRef.current = parsed;
        setTree(parsed);
        setMode("tree");
      } else {
        setMode("text");
      }
      setGen((n) => n + 1);
    },
    contentIsObject: () =>
      modeRef.current === "tree" ? isPlainObject(treeRef.current) : isPlainObject(parseJson(textRef.current)),
  }));

  const notify = useCallback(() => {
    onChangeRef.current?.();
  }, []);

  const handleEdit = useCallback((params: { parentType: string | null; newValue: unknown }) => {
    if (params.parentType === null) {
      treeRef.current = params.newValue;
      setTree(params.newValue);
    }
  }, []);

  const handleDelete = useCallback((params: { parentType: string | null }) => {
    if (params.parentType === null) {
      treeRef.current = undefined;
      setTree(undefined);
      setMode("text");
    }
  }, []);

  const startEdit = useCallback((ev: MouseEvent) => {
    const el = ev.target as HTMLElement;
    if (el.isContentEditable) return;
    if (el.closest(".json-view--edit, .json-view--copy")) return;
    if (!el.classList.contains("json-view--string") && !el.classList.contains("json-view--number") && !el.classList.contains("json-view--boolean") && !el.classList.contains("json-view--null")) {
      return;
    }
    el.closest(".json-view--pair")?.querySelector<HTMLElement>(":scope > .json-view--edit")?.click();
  }, []);

  const canTree = isPlainObject(tree);

  function go(next: Mode) {
    if (next === mode) return;
    if (next === "text") {
      const dumped = dump(tree);
      textRef.current = dumped;
      setText(dumped);
    } else {
      const parsed = parseJson(text);
      if (!isPlainObject(parsed)) return;
      treeRef.current = parsed;
      setTree(parsed);
      setGen((n) => n + 1);
    }
    setMode(next);
  }

  return (
    <div className={className} hidden={hidden}>
      <div className="json-modes">
        <button type="button" className={mode === "tree" ? "active" : undefined} disabled={!canTree} onClick={() => go("tree")}>
          tree
        </button>
        <button type="button" className={mode === "text" ? "active" : undefined} onClick={() => go("text")}>
          text
        </button>
      </div>
      {mode === "tree" ? (
        <div className="json-tree json-tree-edit" onClick={startEdit}>
          <JsonView
            key={gen}
            src={tree ?? {}}
            collapsed={2}
            theme="github"
            collapseStringsAfterLength={120}
            collapseObjectsAfterLength={80}
            matchesURL={false}
            editable
            onChange={notify}
            onEdit={handleEdit}
            onDelete={handleDelete}
          />
        </div>
      ) : (
        <textarea
          className="json-text"
          spellCheck={false}
          value={text}
          onChange={(ev) => {
            textRef.current = ev.target.value;
            setText(ev.target.value);
            onChangeRef.current?.();
          }}
        />
      )}
    </div>
  );
  }),
);
