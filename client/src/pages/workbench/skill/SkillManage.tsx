import { Suspense } from "react";
import SkillBrowse from "../../skill/SkillBrowse";

export default function SkillManage() {
  return (
    <div>
      <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 10 }}>
        浏览、搜索、编辑 Skill 文件。点击 Skill 进入编辑器。
      </div>
      <Suspense fallback={
        <div aria-busy="true" style={{ display: "grid", gap: 8, padding: "10px 0" }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="card" style={{ padding: "10px 12px" }}>
              <div className="skeleton line md" />
              <div className="skeleton line sm" />
            </div>
          ))}
        </div>
      }>
        <SkillBrowse />
      </Suspense>
    </div>
  );
}
