/**
 * 社区市场：从门道装 Skill。
 *
 * 界面上最重要的一件事是**安装前那个确认弹窗**。Skill 不是文档，是能跑的东西，
 * 装进来就在用户自己的机器上。所以这里不做"一键安装" —— 必须先把能力清单
 * （这个 Skill 会执行什么、访问什么、读写什么）摆到用户面前，他点了确认才装。
 *
 * 另外三条对应后端的约束：
 * - 市场**默认关闭**（它会往外发请求，而这个产品的卖点是数据不出本机），
 *   关着的时候这里显示说明而不是空列表；
 * - 连不上的时候优雅降级成一条提示，**不白屏**；
 * - 装过的 Skill 落在本地，断网照常用 —— 所以"已安装"那栏不依赖市场可达。
 */
import { useCallback, useEffect, useState } from "react";

import {
  marketBrowse,
  marketInstall,
  marketPreview,
  marketStatus,
  marketUninstall,
  type MarketAttribution,
  type MarketItem,
  type MarketPreview,
  type MarketStatus,
} from "../../api/client";


/**
 * 来源标注。
 *
 * 分享类的 Skill 是**别人写的**，界面上必须把这件事说清楚 —— 原作者、许可证、
 * 出处。署名只存在数据库里等于没保留：用户看到的仍然是"门道提供的 Skill"，
 * 那对原作者不公平，也让使用者不知道自己拿到的东西受什么许可证约束。
 */
function Attribution({ item }: { item: MarketAttribution }) {
  if (item.origin !== "shared") return null;
  return (
    <div style={{ marginTop: 8, fontSize: 12.5, color: "#5b6560", lineHeight: 1.6 }}>
      <span
        style={{
          fontSize: 11, background: "#eef1ee", color: "#5b6560",
          padding: "1px 6px", borderRadius: 2, marginRight: 6,
        }}
      >
        社区分享
      </span>
      原作者 <b>{item.original_author || "未注明"}</b>
      {item.license && <> · {item.license}</>}
      {item.source_url && (
        item.source_url.startsWith("http") ? (
          <> · <a href={item.source_url} target="_blank" rel="noreferrer noopener">出处</a></>
        ) : (
          <> · {item.source_url}</>
        )
      )}
    </div>
  );
}

function CapabilityDialog({
  preview,
  busy,
  onConfirm,
  onCancel,
}: {
  preview: MarketPreview;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const m = preview.manifest;
  const blocked = !m.installable;
  const risky = (m.capabilities || []).filter((c) => c.severity !== "info");

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(21,26,24,.45)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50,
      }}
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#fff", borderRadius: 3, padding: "20px 22px",
          width: "min(560px, 92vw)", maxHeight: "84vh", overflowY: "auto",
        }}
      >
        <h3 style={{ margin: 0, fontSize: 16 }}>
          安装 {preview.slug} <span style={{ color: "#78827e", fontWeight: 400 }}>v{preview.version}</span>
        </h3>

        <div
          style={{
            marginTop: 12, padding: "10px 12px", borderRadius: 2, fontSize: 13.5, lineHeight: 1.65,
            background: blocked ? "#fdecea" : risky.length ? "#fdf3e3" : "#e6f0f2",
            color: blocked ? "#a8382c" : risky.length ? "#8a5410" : "#1d4e5a",
          }}
        >
          {m.human_summary}
        </div>

        {risky.length > 0 && (
          <ul style={{ marginTop: 12, paddingLeft: 18, fontSize: 13, display: "grid", gap: 5 }}>
            {risky.map((c, i) => (
              <li key={i}>
                {c.detail}
                {c.where && <span style={{ color: "#78827e" }}>（{c.where}）</span>}
              </li>
            ))}
          </ul>
        )}

        <Attribution item={preview.attribution || {}} />

        {!preview.integrity.ok && (
          <div style={{ marginTop: 12, color: "#a8382c", fontSize: 13 }}>
            完整性校验没通过：{preview.integrity.problems.join("；")}
          </div>
        )}

        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: "pointer", fontSize: 13, color: "#5b6560" }}>
            包含 {m.files.length} 个文件
          </summary>
          <div style={{ marginTop: 6, fontSize: 12.5, color: "#5b6560", lineHeight: 1.7 }}>
            {m.files.join("、")}
          </div>
        </details>

        <div style={{ marginTop: 18, display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="tbtn" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button
            className="tbtn"
            onClick={onConfirm}
            disabled={blocked || !preview.integrity.ok || busy}
            title={blocked ? "这个 Skill 没通过本地安全检查" : ""}
            style={{
              background: blocked ? "#eee" : "#1d4e5a",
              color: blocked ? "#999" : "#fff",
              borderColor: blocked ? "#ddd" : "#1d4e5a",
            }}
          >
            {busy ? "安装中…" : "我已了解，安装"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SkillMarket() {
  const [status, setStatus] = useState<MarketStatus | null>(null);
  const [items, setItems] = useState<MarketItem[]>([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<MarketPreview | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await marketStatus());
    } catch (e: any) {
      setError(e?.detail || "读取市场状态失败");
    }
  }, []);

  const search = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setItems((await marketBrowse({ q })).items || []);
    } catch (e: any) {
      // 连不上不该白屏 —— 给一条能看懂的提示，已安装那栏照常可用。
      setError(e?.detail || "连不上能力市场");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [q]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    if (status?.enabled) void search();
  }, [status?.enabled, search]);

  const installed = status?.installed || {};

  if (status && !status.enabled) {
    return (
      <div style={{ padding: "18px 4px", maxWidth: 640 }}>
        <h3 style={{ marginTop: 0 }}>社区市场未开启</h3>
        <p style={{ fontSize: 14, lineHeight: 1.75, color: "#47514d" }}>
          能力市场会向门道社区发起请求，而 IvyeaOps 的默认立场是<b>数据不出你的机器</b>，
          所以它默认关着。
        </p>
        <p style={{ fontSize: 14, lineHeight: 1.75, color: "#47514d" }}>
          开启后也只在你主动浏览或安装时联网：请求匿名、不带机器标识、不回传任何使用统计；
          装过的 Skill 落在本地，断网照常用。你也可以把地址换成自建镜像。
        </p>
        <p style={{ fontSize: 13.5, color: "#78827e" }}>
          去「系统配置 → 能力市场」打开。
        </p>
      </div>
    );
  }

  return (
    <div style={{ padding: "12px 4px" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          className="inp"
          placeholder="搜索社区 Skill…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void search()}
          style={{ minWidth: 220 }}
        />
        <button className="tbtn" onClick={() => void search()} disabled={loading}>
          {loading ? "搜索中…" : "搜索"}
        </button>
        <span style={{ fontSize: 12, color: "#78827e" }}>
          来源 {status?.url} · 当前只提供纯提示词（A 类）Skill
        </span>
      </div>

      {error && (
        <div style={{ marginTop: 12, padding: "10px 12px", background: "#fdf3e3",
                      color: "#8a5410", fontSize: 13.5, borderRadius: 2 }}>
          {error}
        </div>
      )}

      <div style={{ marginTop: 14, display: "grid", gap: 10 }}>
        {items.map((it) => {
          const has = installed[it.slug];
          return (
            <div key={it.slug}
                 style={{ border: "1px solid #d2d8d3", borderRadius: 2, padding: "12px 14px" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <b style={{ fontSize: 15 }}>{it.title}</b>
                <code style={{ fontSize: 12, color: "#78827e" }}>{it.slug}</code>
                {has && (
                  <span style={{ fontSize: 11, background: "#e6f0f2", color: "#1d4e5a",
                                 padding: "2px 7px", borderRadius: 2 }}>
                    已安装 v{has.version}
                  </span>
                )}
              </div>
              {it.summary && (
                <div style={{ marginTop: 5, fontSize: 13.5, color: "#47514d" }}>{it.summary}</div>
              )}
              <Attribution item={it} />
              <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
                <button
                  className="tbtn"
                  onClick={async () => {
                    setBusy(true);
                    setError("");
                    try {
                      setPreview(await marketPreview(it.slug, it.latest || "latest"));
                    } catch (e: any) {
                      setError(e?.detail || "读取能力清单失败");
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  查看能力清单
                </button>
                {has && (
                  <button
                    className="tbtn"
                    onClick={async () => {
                      await marketUninstall(it.slug);
                      await refreshStatus();
                    }}
                  >
                    卸载
                  </button>
                )}
              </div>
            </div>
          );
        })}
        {!loading && !error && items.length === 0 && (
          <div style={{ color: "#78827e", fontSize: 13.5 }}>没有匹配的 Skill。</div>
        )}
      </div>

      {preview && (
        <CapabilityDialog
          preview={preview}
          busy={busy}
          onCancel={() => setPreview(null)}
          onConfirm={async () => {
            setBusy(true);
            try {
              await marketInstall(preview.slug, preview.version, preview.confirm_token);
              setPreview(null);
              await refreshStatus();
            } catch (e: any) {
              setError(e?.detail || "安装失败");
            } finally {
              setBusy(false);
            }
          }}
        />
      )}
    </div>
  );
}
