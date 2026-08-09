import { useState } from "react";
import { useNavigate } from "react-router-dom";
import Modal from "./Modal";
import { importFromGitHub } from "../../api/skill";
import { errText } from "../../lib/errText";

// Accept either "https://github.com/owner/repo" or "owner/repo".
function normalizeRepo(input: string): string | null {
  const s = input.trim();
  if (!s) return null;
  const shortRe = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
  if (shortRe.test(s)) return s;
  try {
    const u = new URL(s);
    if (u.hostname !== "github.com") return null;
    const parts = u.pathname.replace(/^\/|\.git$|\/$/g, "").split("/");
    if (parts.length < 2) return null;
    return `${parts[0]}/${parts[1]}`;
  } catch {
    return null;
  }
}

export type ImportGitHubDialogProps = {
  onClose: () => void;
};

export default function ImportGitHubDialog({ onClose }: ImportGitHubDialogProps) {
  const navigate = useNavigate();
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("main");
  const [subdir, setSubdir] = useState("");
  const [targetName, setTargetName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const normalized = normalizeRepo(repo);
  const canSubmit = !!normalized && !submitting;

  const submit = async () => {
    setErr(null);
    if (!normalized) {
      setErr("仓库格式不合法，请填 'owner/repo' 或完整 GitHub URL。");
      return;
    }
    setSubmitting(true);
    try {
      const res = await importFromGitHub({
        repo: normalized,
        branch: branch.trim() || undefined,
        subdir: subdir.trim() || undefined,
        target_name: targetName.trim() || undefined,
      });
      onClose();
      navigate(`/skill/browse?name=${encodeURIComponent(res.imported_name)}`);
    } catch (e: any) {
      setErr(errText(e, "导入失败"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="从 GitHub 导入 Skill"
      onClose={onClose}
      locked={submitting}
      width={520}
      footer={
        <>
          <button className="tbtn" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button className="tbtn primary" onClick={submit} disabled={!canSubmit}>
            {submitting ? "导入中…（可能需几秒）" : "导入"}
          </button>
        </>
      }
    >
      {err && <div className="sks-error" style={{ marginBottom: 10 }}>⚠ {err}</div>}

      <div className="sks-form-row">
        <label>仓库<span className="req">*</span></label>
        <input
          className="sks-input"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder="owner/repo 或 https://github.com/owner/repo"
          autoFocus
          disabled={submitting}
        />
        <div className="hint">
          {repo && !normalized
            ? <span style={{ color: "var(--red)" }}>格式不合法</span>
            : normalized
              ? <span style={{ color: "var(--acc)" }}>识别为 {normalized}</span>
              : "只支持 GitHub 公开仓库。"}
        </div>
      </div>

      <div className="sks-form-row" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div>
          <label>分支</label>
          <input
            className="sks-input"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="main"
            disabled={submitting}
          />
        </div>
        <div>
          <label>目标名（可选）</label>
          <input
            className="sks-input"
            value={targetName}
            onChange={(e) => setTargetName(e.target.value.trim())}
            placeholder="留空自动推导"
            disabled={submitting}
          />
        </div>
      </div>

      <div className="sks-form-row">
        <label>子目录（可选）</label>
        <input
          className="sks-input"
          value={subdir}
          onChange={(e) => setSubdir(e.target.value)}
          placeholder="例如 skills/my-skill，留空=仓库根"
          disabled={submitting}
        />
        <div className="hint">
          要定位到仓库内 SKILL.md 所在目录。不确定就留空先试。
        </div>
      </div>
    </Modal>
  );
}
