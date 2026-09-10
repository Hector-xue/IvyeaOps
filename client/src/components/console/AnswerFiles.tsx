/**
 * 回答底下的产物卡片 —— 「跑出来的东西，就在这条回答下面」。
 *
 * 为什么不是只放在右侧产物栏：那条栏是 40px 的图标条，没数据时那一格连显示都
 * 不显示，有数据也要先点开才看得见。上线之后生产库里的产物索引一直是 0 行，
 * 用户的原话是"我刚才测试了一下压根没看到"—— 一个要先知道它在哪才找得到的
 * 出口，等于没有出口。
 *
 * 参照的是 ChatGPT 那种做法：文件卡片直接铺在消息流里，点一下右边滑出预览，
 * 卡片上就带下载。产物栏保留，它管的是"这条会话总共产出过什么"的汇总视角。
 */
import Icon from "../Icon";
import { fmtSize } from "./FilePreview";
import { consoleFileRawUrl, type ConsoleFile } from "../../api/ivyeaAgent";

/** 每种产物给一个能一眼认出来的图标 + 中文类型。
 *  图标名只能取 components/Icon 里已有的（"file" 是文件夹图标，别拿来当文件用）。 */
const KIND: Record<string, { icon: string; label: string }> = {
  markdown: { icon: "report", label: "文档" },
  csv: { icon: "analysis", label: "表格" },
  text: { icon: "report", label: "文本" },
  html: { icon: "report", label: "网页" },
  image: { icon: "imagegen", label: "图片" },
  pdf: { icon: "report", label: "PDF" },
  binary: { icon: "report", label: "文件" },
};

export default function AnswerFiles({ files, onOpen }: {
  files: ConsoleFile[];
  onOpen: (file: ConsoleFile) => void;
}) {
  if (!files.length) return null;
  return (
    <div className="caf">
      {files.map((f) => {
        const meta = KIND[f.kind] || KIND.binary;
        return (
          <div key={f.id} className={"caf-card" + (f.exists ? "" : " gone")}>
            <button
              type="button"
              className="caf-main"
              onClick={() => onOpen(f)}
              title={f.exists ? `预览 ${f.path}` : "文件已不在磁盘上"}
              disabled={!f.exists}
            >
              <span className="caf-icon"><Icon name={meta.icon} size={18} /></span>
              <span className="caf-text">
                <span className="caf-name">{f.name}</span>
                <span className="caf-meta">
                  {f.exists ? `${meta.label} · ${fmtSize(f.size)}` : "已不在"}
                </span>
              </span>
            </button>
            {/* 下载走 <a download>，不是 onClick —— 让浏览器自己接管，
                大文件不必先读进内存再造 blob。 */}
            <a
              className="caf-dl"
              href={consoleFileRawUrl(f.id, true)}
              download={f.name}
              title={f.exists ? "下载" : "文件已不在"}
              aria-disabled={!f.exists}
              onClick={(e) => { if (!f.exists) e.preventDefault(); }}
            >
              <Icon name="download" size={15} />
            </a>
          </div>
        );
      })}
    </div>
  );
}
