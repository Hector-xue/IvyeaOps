import { useCallback, useEffect, useState } from "react";
import { getSettings, updateSettings, StudioSettings } from "../../api/skill";
import SheetSelect from "../../components/SheetSelect";
import { errText } from "../../lib/errText";

/**
 * Settings are stored on the server (used by the snapshot prune job and trash
 * TTL on the backend). A few fields are informational only for now:
 *   - autosave_debounce_ms: saved but not yet consumed by the editor (hardcoded 600).
 *   - theme: saved but light mode is not styled yet.
 */
export default function SettingsPage() {
  const [saved, setSaved] = useState<StudioSettings | null>(null);
  const [draft, setDraft] = useState<StudioSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const s = await getSettings();
      setSaved(s);
      setDraft(s);
    } catch (e: any) {
      setErr(errText(e, "加载失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const dirty =
    draft != null &&
    saved != null &&
    (
      draft.snapshot_retention !== saved.snapshot_retention ||
      draft.trash_ttl_days !== saved.trash_ttl_days ||
      draft.autosave_debounce_ms !== saved.autosave_debounce_ms ||
      draft.theme !== saved.theme
    );

  const update = <K extends keyof StudioSettings>(key: K, v: StudioSettings[K]) => {
    setDraft((d) => (d ? { ...d, [key]: v } : d));
  };

  const doSave = async () => {
    if (!draft) return;
    setSaving(true);
    setErr(null);
    try {
      const s = await updateSettings(draft);
      setSaved(s);
      setDraft(s);
      setToast("设置已保存");
      window.setTimeout(() => setToast(null), 3000);
    } catch (e: any) {
      setErr(errText(e, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  const doReset = () => { if (saved) setDraft(saved); };

  if (loading || !draft) {
    return <div className="sks-loading">加载中…</div>;
  }

  return (
    <div className="sks-settings">
      {err && <div className="sks-error">⚠ {err}</div>}

      <section className="sks-set-section">
        <div className="ct">保留策略</div>

        <div className="sks-form-row">
          <label>快照保留数量（每个 skill）</label>
          <input
            type="number"
            className="sks-input"
            value={draft.snapshot_retention}
            min={1}
            max={200}
            onChange={(e) => update("snapshot_retention", Number(e.target.value))}
          />
          <div className="hint">超出数量的旧快照会在后台被清理。默认 20。</div>
        </div>

        <div className="sks-form-row">
          <label>回收站保留天数</label>
          <input
            type="number"
            className="sks-input"
            value={draft.trash_ttl_days}
            min={1}
            max={90}
            onChange={(e) => update("trash_ttl_days", Number(e.target.value))}
          />
          <div className="hint">超过天数的回收站条目会在下次启动时清理。默认 7。</div>
        </div>
      </section>

      <section className="sks-set-section">
        <div className="ct">编辑器</div>

        <div className="sks-form-row">
          <label>自动保存防抖（毫秒）</label>
          <input
            type="number"
            className="sks-input"
            value={draft.autosave_debounce_ms}
            min={100}
            max={5000}
            step={50}
            onChange={(e) => update("autosave_debounce_ms", Number(e.target.value))}
          />
          <div className="hint">
            该值已保存至后端，<b>当前客户端硬编码为 600ms</b>，动态消费将在后续版本启用。
          </div>
        </div>

        <div className="sks-form-row">
          <label>主题</label>
          <SheetSelect
            className="sks-input"
            value={draft.theme}
            onChange={(v) => update("theme", v as "dark" | "light")}
            title="主题"
            options={[
              { value: "dark", label: "暗色（当前唯一已实装）" },
              { value: "light", label: "明色（保留字段，尚未样式化）" },
            ]}
          />
        </div>
      </section>

      <div className="sks-set-foot">
        <button className="tbtn" onClick={doReset} disabled={!dirty || saving}>重置</button>
        <button className="tbtn primary" onClick={doSave} disabled={!dirty || saving}>
          {saving ? "保存中…" : "保存"}
        </button>
      </div>

      {toast && <div className="sks-toast">{toast}</div>}
    </div>
  );
}
