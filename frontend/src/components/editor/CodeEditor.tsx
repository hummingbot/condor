import { useEffect, useRef } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { syntaxHighlighting, defaultHighlightStyle, bracketMatching } from "@codemirror/language";
import { yaml } from "@codemirror/lang-yaml";
import { python } from "@codemirror/lang-python";
import { oneDark } from "@codemirror/theme-one-dark";

interface CodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  language: "yaml" | "python";
  readOnly?: boolean;
  height?: string;
  className?: string;
}

export function CodeEditor({
  value,
  onChange,
  language,
  readOnly = false,
  height = "400px",
  className,
}: CodeEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // Create editor on mount
  useEffect(() => {
    if (!containerRef.current) return;

    const langExtension = language === "yaml" ? yaml() : python();

    const extensions = [
      lineNumbers(),
      highlightActiveLine(),
      highlightActiveLineGutter(),
      history(),
      bracketMatching(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      keymap.of([...defaultKeymap, ...historyKeymap]),
      langExtension,
      oneDark,
      EditorView.theme({
        "&": { height, fontSize: "13px" },
        ".cm-scroller": { overflow: "auto" },
        ".cm-content": { fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace" },
        ".cm-gutters": { fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace" },
      }),
    ];

    if (readOnly) {
      extensions.push(EditorState.readOnly.of(true));
      extensions.push(EditorView.editable.of(false));
    } else {
      extensions.push(
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onChangeRef.current?.(update.state.doc.toString());
          }
        })
      );
    }

    const state = EditorState.create({ doc: value, extensions });
    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // Only recreate on language/readOnly change, not on value change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language, readOnly, height]);

  // Sync external value changes into the editor
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current !== value) {
      view.dispatch({
        changes: { from: 0, to: current.length, insert: value },
      });
    }
  }, [value]);

  return (
    <div
      ref={containerRef}
      className={`rounded-md border border-[var(--color-border)] overflow-hidden ${className ?? ""}`}
    />
  );
}
