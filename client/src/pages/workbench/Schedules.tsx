/**
 * 定时任务 —— 对标 MyLevis 的「定时任务」/ WorkBuddy 的「自动化」。
 *
 * 一句话：**到点让 Agent 自己跑一轮，结果留在历史里等你看。**
 *
 * 界面上把一条安全底线说明白：无人值守时永远只读。Agent 会巡检、分析、把要改的
 * 东西列清楚，但不会真的动线上数据 —— 没人在屏幕前的时候，"自动批准写操作"
 * 是绝对不该有的东西。
 */
import { useCallback, useEffect, useState } from "react";
import { ToastProvider, useToast } from "../../components/toast";
import { useConfirm } from "../../components/ConfirmDialog";
import { MarkdownReport } from "../../lib/reportFormat";
import {
  CRON_PRESETS,
  createSchedule,
  deleteSchedule,
  listSchedules,
  patchSchedule,
  previewCron,
  runScheduleNow,
  scheduleRuns,
  type ScheduleRun,
  type ScheduleTask,
} from "../../api/schedules";
import { ivyeaSkills, type IvyeaSkillInfo } from "../../api/ivyeaAgent";
import { errText } from "../../lib/errText";

const BLANK = { name: "", cron: "0 9 * * *", prompt: "", skill: "", enabled: true };

function fmt(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function StatusDot({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    done: ["cs-ok", "✓"], error: ["cs-err", "✕"], running: ["cs-blocked", "⟳"],
  };
  const [cls, ch] = map[status] || ["", "·"];
  return <span className={cls}>{ch}</span>;
}

function RunHistory({ taskId }: { taskId: string }) {
  const [runs, setRuns] = useState<ScheduleRun[] | null>(null);
  const [openRun, setOpenRun] = useState("");

  useEffect(() => {
    let alive = true;
    scheduleRuns(taskId).then((r) => alive && setRuns(r)).catch(() => alive && setRuns([]));
    return () => { alive = false; };
  }, [taskId]);

  if (runs === null) return <div className="skeleton line md" />;
  if (!runs.length) return <div className="cap-empty">还没有运行记录。可以点「立即运行」先试一次。</div>;

  return (
    <div className="sch-runs">
      {runs.map((r) => (
        <div key={r.id} className="sch-run">
          <button className="sch-run-head" onClick={() => setOpenRun(openRun === r.id ? "" : r.id)}>
            <StatusDot status={r.status} />
            <span className="sch-run-time">{fmt(r.started)}</span>
            <span className="cap-tag">{r.trigger === "manual" ? "手动" : "定时"}</span>
            <span className="sch-run-sum">
              {r.status === "error" ? r.error.slice(0, 60) : (r.output || "").slice(0, 60)}
            </span>
            <span className="cs-caret">{openRun === r.id ? "▾" : "▸"}</span>
          </button>
          {openRun === r.id && (
            <div className="sch-run-body">
              {r.status === "error"
                ? <div className="cc-answer-error">{r.error}</div>
                : <MarkdownReport text={r.output || "（无输出）"} />}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function TaskForm({
  initial, skills, onCancel, onSaved,
}: {
  initial?: ScheduleTask;
  skills: IvyeaSkillInfo[];
  onCancel: () => void;
  onSaved: () => void;
}) {
  const notify = useToast();
  const [form, setForm] = useState(() => initial
    ? { name: initial.name, cron: initial.cron, prompt: initial.prompt,
        skill: initial.skill, enabled: initial.enabled }
    : { ...BLANK });
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<
    { ok: boolean; next?: string[]; error?: string; timezone?: string } | null>(null);

  // cron 改一次就预览一次：让人在保存前就看清它到底什么时候跑，而不是等第二天。
  useEffect(() => {
    let alive = true;
    const t = window.setTimeout(() => {
      previewCron(form.cron).then((d) => alive && setPreview(d)).catch(() => void 0);
    }, 300);
    return () => { alive = false; window.clearTimeout(t); };
  }, [form.cron]);

  const save = async () => {
    if (!form.name.trim() || !form.prompt.trim()) {
      notify("warn", "任务名和「让 Agent 做什么」都要填");
      return;
    }
    setSaving(true);
    try {
      if (initial) await patchSchedule(initial.id, form);
      else await createSchedule(form);
      notify("success", initial ? "已保存" : "定时任务已创建");
      onSaved();
    } catch (e: any) {
      notify("error", errText(e, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="cap-form sch-form">
      <input className="inp" placeholder="任务名，例：每日广告巡检"
             value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />

      <textarea className="inp sch-prompt" rows={4}
                placeholder="让 Agent 做什么？像跟人交代一样写，例：拉最近 7 天的广告数据，找出高花费零转化的搜索词，给出否词建议。"
                value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} />

      <div className="sch-presets">
        {CRON_PRESETS.map((p) => (
          <button key={p.cron} type="button"
                  className={"cc-scene" + (form.cron === p.cron ? " active" : "")}
                  onClick={() => setForm({ ...form, cron: p.cron })}>{p.label}</button>
        ))}
      </div>

      <div className="cap-form-row">
        <input className="inp" placeholder="cron：分 时 日 月 星期"
               value={form.cron} onChange={(e) => setForm({ ...form, cron: e.target.value })} />
        <select className="inp" value={form.skill}
                onChange={(e) => setForm({ ...form, skill: e.target.value })}>
          <option value="">自动选技能</option>
          {skills.map((s) => <option key={s.id} value={s.id}>{s.title || s.id}</option>)}
        </select>
      </div>

      <div className={"sch-preview" + (preview && !preview.ok ? " bad" : "")}>
        {preview === null ? "计算中…"
          : preview.ok
            ? `接下来会在：${(preview.next || []).slice(0, 3).join(" · ")}`
              + (preview.timezone ? `（${preview.timezone}）` : "")
          : `cron 不对：${preview.error}`}
      </div>

      <label className="cap-check">
        <input type="checkbox" checked={form.enabled}
               onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
        <span>启用</span>
      </label>

      <div className="cap-form-actions">
        <button className="cs-btn cs-btn-primary" disabled={saving || (preview ? !preview.ok : false)}
                onClick={() => void save()}>{saving ? "保存中…" : "保存"}</button>
        <button className="cs-btn" onClick={onCancel}>取消</button>
      </div>
    </div>
  );
}

function SchedulesInner() {
  const notify = useToast();
  const confirm = useConfirm();
  const [tasks, setTasks] = useState<ScheduleTask[] | null>(null);
  const [skills, setSkills] = useState<IvyeaSkillInfo[]>([]);
  const [editing, setEditing] = useState<string>("");     // task id 或 "new"
  const [expanded, setExpanded] = useState("");
  const [running, setRunning] = useState("");
  // 调度用的是**服务器**本地时区，不是浏览器的。不写出来，跨时区的人会按自己的
  // 钟去理解「每天 09:00」，然后发现报告在半夜到。
  const [tz, setTz] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await listSchedules();
      setTasks(d.tasks || []);
      setTz(d.timezone || "");
    } catch (e: any) {
      setTasks([]);
      notify("error", errText(e, "读取定时任务失败"));
    }
  }, [notify]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    ivyeaSkills().then((d) => setSkills(d.skills || [])).catch(() => void 0);
  }, []);

  const toggle = async (t: ScheduleTask) => {
    try {
      await patchSchedule(t.id, { enabled: !t.enabled });
      await load();
    } catch (e: any) {
      notify("error", errText(e, "操作失败"));
    }
  };

  const remove = async (t: ScheduleTask) => {
    const ok = await confirm({
      title: `删除定时任务「${t.name}」？`,
      message: "运行历史会一并删除，且无法恢复。",
      danger: true,
    });
    if (!ok) return;
    try { await deleteSchedule(t.id); await load(); notify("success", "已删除"); }
    catch (e: any) { notify("error", errText(e, "删除失败")); }
  };

  const runNow = async (t: ScheduleTask) => {
    setRunning(t.id);
    notify("info", `「${t.name}」已开始跑，完成后会出现在运行历史里。`);
    try {
      const run = await runScheduleNow(t.id);
      notify(run.status === "error" ? "error" : "success",
             run.status === "error" ? `执行失败：${run.error.slice(0, 60)}` : "执行完成");
      setExpanded(t.id);
      await load();
    } catch (e: any) {
      notify("error", errText(e, "触发失败"));
    } finally {
      setRunning("");
    }
  };

  return (
    <div className="cap-page">
      <div className="home-topbar">
        <span className="home-title"><span style={{ color: "var(--acc)" }}>⏱</span> 定时任务</span>
        <span style={{ fontSize: 11, color: "var(--t3)" }}>到点让 Agent 自己跑一轮</span>
      </div>

      <div className="sch-notice">
        <span className="cs-icon">⚑</span>
        <span>
          定时任务**始终只读**：Agent 会巡检、分析、把要改的东西列清楚，但不会真的动线上数据。
          没人在屏幕前的时候，自动批准写操作太危险 —— 要落地就看完结果再去对应板块执行。
          {tz && <> 触发时刻按 <b>服务器时区 {tz}</b> 计算，不是你浏览器所在的时区。</>}
        </span>
      </div>

      {editing === "new" ? (
        <TaskForm skills={skills} onCancel={() => setEditing("")}
                  onSaved={() => { setEditing(""); void load(); }} />
      ) : (
        <button className="cs-btn" onClick={() => setEditing("new")}>+ 新建定时任务</button>
      )}

      <div className="cap-section">
        {tasks === null ? <div className="skeleton line lg" />
          : tasks.length === 0 ? (
            <div className="cap-empty">
              还没有定时任务。常见用法：每天早上让 Agent 跑一遍广告巡检、每周一出一份类目大盘摘要。
            </div>
          ) : tasks.map((t) => (
            <div className={"sch-card" + (t.enabled ? "" : " off")} key={t.id}>
              <div className="sch-card-head">
                <b>{t.name}</b>
                <code>{t.cron}</code>
                <span className="sch-next">{t.enabled ? t.next_text : "已停用"}</span>
                <span className="sch-actions">
                  <button className="cs-btn" disabled={running === t.id}
                          onClick={() => void runNow(t)}>
                    {running === t.id ? "运行中…" : "立即运行"}
                  </button>
                  <button className="cs-btn" onClick={() => void toggle(t)}>
                    {t.enabled ? "停用" : "启用"}
                  </button>
                  <button className="cs-btn" onClick={() => setEditing(editing === t.id ? "" : t.id)}>
                    编辑
                  </button>
                  <button className="cs-btn" onClick={() => void remove(t)}>删除</button>
                </span>
              </div>
              <div className="sch-card-prompt">{t.prompt}</div>
              <div className="sch-card-meta">
                上次运行 {fmt(t.last_run)}
                {t.skill && <span className="cap-tag">技能 {t.skill}</span>}
                <button className="sch-toggle"
                        onClick={() => setExpanded(expanded === t.id ? "" : t.id)}>
                  {expanded === t.id ? "收起运行历史 ▾" : "运行历史 ▸"}
                </button>
              </div>

              {editing === t.id && (
                <TaskForm initial={t} skills={skills} onCancel={() => setEditing("")}
                          onSaved={() => { setEditing(""); void load(); }} />
              )}
              {expanded === t.id && <RunHistory taskId={t.id} />}
            </div>
          ))}
      </div>
    </div>
  );
}

export default function Schedules() {
  return (
    <ToastProvider>
      <SchedulesInner />
    </ToastProvider>
  );
}
