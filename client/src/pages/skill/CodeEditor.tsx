import { useMemo } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { EditorView, keymap } from "@codemirror/view";
import { Prec } from "@codemirror/state";
import { markdown } from "@codemirror/lang-markdown";
import { yaml } from "@codemirror/lang-yaml";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { oneDark } from "@codemirror/theme-one-dark";
import { themeMode } from "../../lib/themes";

export type CodeEditorProps = {
  value: string;
  onChange: (v: string) => void;
  /** File path, used only for language detection. */
  path: string;
  readonly?: boolean;
  /** Ctrl+S / Cmd+S handler. */
  onSaveShortcut?: () => void;
  /** Blur handler (used to flush autosave). */
  onBlur?: () => void;
};

function extForPath(p: string): string {
  const m = /\.([A-Za-z0-9]+)$/.exec(p);
  return m ? m[1].toLowerCase() : "";
}

function languageForExt(ext: string) {
  switch (ext) {
    case "md":
    case "markdown":
      return markdown();
    case "yml":
    case "yaml":
      return yaml();
    case "py":
      return python();
    case "js":
    case "jsx":
    case "ts":
    case "tsx":
    case "mjs":
    case "cjs":
    case "json":
      return javascript({ jsx: true, typescript: true });
    default:
      return null;
  }
}

export default function CodeEditor({
  value,
  onChange,
  path,
  readonly = false,
  onSaveShortcut,
  onBlur,
}: CodeEditorProps) {
  // Ctrl+S (Cmd+S on mac) → flush save; prevent the browser's Save Page.
  const saveKeymap = useMemo(
    () =>
      Prec.high(
        keymap.of([
          {
            key: "Mod-s",
            preventDefault: true,
            run: () => {
              onSaveShortcut?.();
              return true;
            },
          },
        ]),
      ),
    [onSaveShortcut],
  );

  const extensions = useMemo(() => {
    const ext = extForPath(path);
    const lang = languageForExt(ext);
    const base = [
      saveKeymap,
      EditorView.lineWrapping,
      EditorView.domEventHandlers({
        blur: () => {
          onBlur?.();
          return false;
        },
      }),
      EditorView.theme({
        "&": { fontSize: "13px", height: "100%" },
        ".cm-scroller": { fontFamily: "var(--font, ui-monospace, monospace)" },
        ".cm-content": { padding: "10px 0" },
      }),
    ];
    return lang ? [...base, lang] : base;
  }, [path, saveKeymap, onBlur]);

  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      extensions={extensions}
      // 这里原本是无条件 oneDark —— 浅色主题下整个编辑器是一块深色补丁。
      // CodeMirror 的 theme={undefined} 就是它自带的浅色，够用。
      theme={themeMode(localStorage.getItem("ivyea-ops.theme")) === "dark" ? oneDark : undefined}
      editable={!readonly}
      readOnly={readonly}
      basicSetup={{
        lineNumbers: true,
        highlightActiveLine: !readonly,
        highlightActiveLineGutter: !readonly,
        foldGutter: true,
        tabSize: 2,
      }}
      style={{ height: "100%", minHeight: 0 }}
    />
  );
}
