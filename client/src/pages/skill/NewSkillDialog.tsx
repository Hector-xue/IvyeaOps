import { useState } from "react";
import { useNavigate } from "react-router-dom";
import Modal from "./Modal";
import { createSkill } from "../../api/skill";
import { errText } from "../../lib/errText";

// Mirror the server-side rule: lowercase start, 2-64 chars per segment,
// segments joined with '/'.
const NAME_RE =
  /^[a-z][a-z0-9_-]{1,63}(?:\/[a-z][a-z0-9_-]{1,63})*$/;

export type NewSkillDialogProps = {
  onClose: () => void;
};

export default function NewSkillDialog({ onClose }: NewSkillDialogProps) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const nameIsValid = NAME_RE.test(name);
  const canSubmit = nameIsValid && !submitting;

  const submit = async () => {
    setErr(null);
    if (!nameIsValid) {
      setErr("名称格式不合法：每段 2-64 字符，小写字母开头，可含数字/下划线/短横。多段用 / 分隔。");
      return;
    }
    setSubmitting(true);
    try {
      await createSkill({
        name,
        description: description.trim() || undefined,
        body: body || undefined,
      });
      onClose();
      navigate(`/skill/browse?name=${encodeURIComponent(name)}`);
    } catch (e: any) {
      setErr(errText(e, "创建失败"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="新建 Skill"
      onClose={onClose}
      locked={submitting}
      footer={
        <>
          <button className="tbtn" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button
            className="tbtn primary"
            onClick={submit}
            disabled={!canSubmit}
          >
            {submitting ? "创建中…" : "创建"}
          </button>
        </>
      }
    >
      {err && <div className="sks-error" style={{ marginBottom: 10 }}>⚠ {err}</div>}

      <div className="sks-form-row">
        <label>名称<span className="req">*</span></label>
        <input
          className="sks-input"
          value={name}
          onChange={(e) => setName(e.target.value.trim())}
          placeholder="例如：my-skill 或 research/arxiv-helper"
          autoFocus
          disabled={submitting}
        />
        <div className="hint">
          {name && !nameIsValid
            ? <span style={{ color: "var(--red)" }}>格式不合法</span>
            : "小写字母开头，2-64 字符/段，可用 / 分多段。"}
        </div>
      </div>

      <div className="sks-form-row">
        <label>描述（可选）</label>
        <input
          className="sks-input"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="一句话描述这个 skill"
          maxLength={200}
          disabled={submitting}
        />
      </div>

      <div className="sks-form-row">
        <label>正文（可选）</label>
        <textarea
          className="sks-input"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="SKILL.md 的正文，留空后再进编辑器填。"
          rows={6}
          disabled={submitting}
          style={{ fontFamily: "var(--font, ui-monospace, monospace)", fontSize: 12 }}
        />
      </div>
    </Modal>
  );
}
