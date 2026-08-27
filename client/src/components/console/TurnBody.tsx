/**
 * 一轮 assistant 的正文 + 执行过程，**按发生顺序交错铺开**。
 *
 * ── 为什么要交错 ──────────────────────────────────────────────────────────
 * agent 干活的形状本来就是：说一段 →（去调几个工具）→ 再说一段 → …
 * 而此前界面把它拍平成两块：所有工具堆在上面一坨，所有话拼在下面一坨。用户的原话
 * 是"一大堆叠在一起""怎么汇报进度没有即时分段式汇报"。
 *
 * 更说明问题的是他另一句：**"好像只有回答完之后刷新一下才能看到分段式汇报"** ——
 * 因为刷新之后是从存档里恢复的，而存档里每条 assistant 消息本来就是一段
 * （lib/sessionRestore 按消息切成轮次）。也就是说：正确的形态一直存在，只是直播时
 * 没有。这里把直播对齐到那个形态 —— 两边看到的必须是同一个东西。
 *
 * 段的边界由 lib/turnStream 在**工具开始跑的那一刻**封定（seq = 当时已有多少步），
 * 所以这里只需按 seq 把步骤切片摆到对应段的后面。
 */
import ActivityFeed, { type MatchedSkill, type Thought } from "./ActivityFeed";
import { MarkdownReport } from "../../lib/reportFormat";
import type { ConsoleStep } from "../../lib/stepLabels";

export type TurnBodyProps = {
  text: string;
  segments?: { seq: number; text: string }[];
  steps?: ConsoleStep[];
  thoughts?: Thought[];
  skills?: MatchedSkill[];
  memoryRecall?: string[];
  running?: boolean;
  failed?: boolean;
  elapsedMs?: number;
  liveThought?: string;
  onPickImage?: (e: React.MouseEvent<HTMLDivElement>) => void;
};

type Block =
  | { kind: "text"; key: string; text: string }
  | { kind: "steps"; key: string; from: number; to: number };

/** 段与步骤按 seq 排成一条时间线。段的 seq = 它说完时已经有多少步。 */
function blocksOf(text: string, segments: { seq: number; text: string }[],
                  stepCount: number): Block[] {
  const out: Block[] = [];
  let cursor = 0;
  segments.forEach((seg, i) => {
    const at = Math.max(cursor, Math.min(seg.seq, stepCount));
    if (at > cursor) out.push({ kind: "steps", key: `s${cursor}-${at}`, from: cursor, to: at });
    if (seg.text.trim()) out.push({ kind: "text", key: `t${i}`, text: seg.text });
    cursor = at;
  });
  if (stepCount > cursor) out.push({ kind: "steps", key: `s${cursor}-${stepCount}`, from: cursor, to: stepCount });
  if (text) out.push({ kind: "text", key: "tail", text });
  return out;
}

export default function TurnBody({
  text, segments = [], steps = [], thoughts = [], skills = [], memoryRecall = [],
  running, failed, elapsedMs, liveThought = "", onPickImage,
}: TurnBodyProps) {
  const blocks = blocksOf(text, segments, steps.length);
  // 一段都没封过（老会话、还没调过工具）时，blocks 退化成"一块过程 + 一块正文"，
  // 也就是改动前的样子 —— 没有分段信息就不假装有。
  const lastSteps = [...blocks].reverse().find((b) => b.kind === "steps");

  return (
    <>
      {blocks.map((b) => {
        if (b.kind === "text") {
          // 中间那些"边做边说"的段落**不做成卡片**：它们是旁白，卡片是结论。
          // 全都套上边框的话，一轮里会叠出四五个一模一样的框，最后那份真正的
          // 回答反而淹在里面 —— 参考 Claude Code：旁白是素文本，结论才有分量。
          // 旁白**不挂 .cc-answer**：安静/琉璃两套皮肤对那个类有
          // `padding:0 !important` + `border-color:transparent !important`
          // （它们的主张是"把盒子全拿掉"），挂上去等于自己的样式全被抹掉。
          // 实测过：加了左引线和缩进，computed 出来是 0px / transparent。
          const narration = b.key !== "tail";
          return (
            <div key={b.key}
                 className={narration ? "cc-narration"
                                      : "cc-answer" + (failed ? " cc-answer-error" : "")}
                 onClick={onPickImage}>
              {/* 首尾空行要去掉：段与段之间的 "\n\n" 是 turnStream 加的分隔符，
                  渲染成 markdown 就是一个空段落 —— 屏幕上是一截孤零零的引线。 */}
              {failed ? b.text : <MarkdownReport text={narration ? b.text.trim() : b.text} />}
            </div>
          );
        }
        const slice = steps.slice(b.from, b.to);
        const isLast = lastSteps === b;
        return (
          <ActivityFeed
            key={b.key}
            steps={slice}
            // 思考锚在"第几步"上，跟着它所属的那一段走；这里把 seq 平移到切片坐标系。
            // 区间**左闭右开**：seq === to 的那一段思考发生在下一批工具之前，属于
            // 下一组。写成闭区间的话它会在相邻两组里各画一次（实测：同一句"用户问
            // 的是…"在上下两组里出现了两遍）。最后一组要兜住结尾那一段，取闭区间。
            thoughts={thoughts.filter((t) => t.seq >= b.from && (isLast ? t.seq <= b.to : t.seq < b.to))
                              .map((t) => ({ ...t, seq: t.seq - b.from }))}
            // 技能只在第一组显示一次 —— 它说的是"这一轮选了什么技能"，不是每一组。
            skills={b.from === 0 ? skills : []}
            // 记忆同理：召回发生在这一轮开口之前，只属于第一组。
            memoryRecall={b.from === 0 ? memoryRecall : []}
            // 计时和"正在跑"只属于最后一组：前面那些组早就跑完了。
            elapsedMs={isLast ? elapsedMs : undefined}
            running={isLast ? running : false}
            liveThought={isLast ? liveThought : ""}
          />
        );
      })}
      {/* 一个字都还没出来、也还没有步骤：给个"正在准备"的过程块，别整片空白。 */}
      {!blocks.length && running && <ActivityFeed steps={[]} running elapsedMs={elapsedMs} />}
    </>
  );
}
