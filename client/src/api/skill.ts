// Skill Studio API client.
//
// Matches the /api/skill/* endpoints on the FastAPI backend. Snapshot and
// trash routes live under flat sub-paths (e.g. /snapshots/{id}/diff) because
// the backend cannot combine a greedy {name:path} converter with trailing
// path segments.

import { api } from "./client";

// ---------------------------------------------------------------------------
// Types (mirror server/app/services/skill_repo.py and friends)
// ---------------------------------------------------------------------------

export type SkillMeta = {
  name: string;
  category: string;
  description: string | null;
  description_zh: string | null;
  pinned: boolean;
  editable: boolean;
  source: string;
  updated_at: string;
  size_bytes: number;
  file_count: number;
};

export type LinkedFile = {
  path: string;
  size: number;
  mtime: string;
  is_binary: boolean;
};

export type SkillDetail = SkillMeta & {
  frontmatter: Record<string, unknown>;
  content_body: string;
  linked_files: LinkedFile[];
};

export type SkillStats = {
  total_skills: number;
  total_size_bytes: number;
  categories: Record<string, number>;
  recently_edited: SkillMeta[];
};

export type SkillListResponse = {
  skills: SkillMeta[];
  total: number;
};

export type FileContent = {
  skill_name: string;
  path: string;
  content: string;
  size: number;
  is_binary: boolean;
};

export type FileEntry = {
  path: string;
  size: number;
  mtime: string;
  is_binary: boolean;
};

export type SnapshotMeta = {
  id: string;
  label: string | null;
  created_at: string;
  size_bytes: number;
  file_count: number;
};

/** Backend returns `status` ∈ {"added","modified","deleted"} and text in `diff`. */
export type SnapshotDiffFile = {
  path: string;
  status: "added" | "modified" | "deleted";
  diff: string;
};

export type SnapshotDiff = {
  snapshot_id: string;
  files: SnapshotDiffFile[];
};

export type RestoreResult = {
  restored_from: string;
  pre_restore_snapshot_id: string | null;
};

export type TrashEntry = {
  id: string;
  original_name: string;
  trashed_at: string;
  expires_at: string;
  size_bytes: number;
  file_count: number;
};

export type GitHubImportResult = {
  imported_name: string;
  source_url: string;
  branch: string;
  subdir: string | null;
  snapshot_id: string | null;
};

export type AuditEvent = {
  ts: string;
  event_type: string;
  actor: string;
  skill_name: string | null;
  details: Record<string, unknown>;
};

export type StudioSettings = {
  snapshot_retention: number;
  trash_ttl_days: number;
  autosave_debounce_ms: number;
  theme: "dark" | "light";
};

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

export async function getStats(): Promise<SkillStats> {
  const { data } = await api.get<SkillStats>("/skill/stats");
  return data;
}

export async function listSkills(params?: {
  q?: string;
  category?: string;
}): Promise<SkillListResponse> {
  const { data } = await api.get<SkillListResponse>("/skill/list", { params });
  return data;
}

export async function getSkill(name: string): Promise<SkillDetail> {
  const { data } = await api.get<SkillDetail>(`/skill/item/${encodePath(name)}`);
  return data;
}

export async function readFile(name: string, path: string): Promise<FileContent> {
  const { data } = await api.get<FileContent>(`/skill/file/${encodePath(name)}`, {
    params: { path },
  });
  return data;
}

// ---------------------------------------------------------------------------
// Write
// ---------------------------------------------------------------------------

export async function createSkill(input: {
  name: string;
  description?: string;
  body?: string;
  frontmatter_extras?: Record<string, unknown>;
}): Promise<SkillMeta> {
  const { data } = await api.post<SkillMeta>("/skill/item", input);
  return data;
}

export async function updateSkill(
  name: string,
  frontmatter: Record<string, unknown>,
  body: string,
): Promise<SkillMeta> {
  const { data } = await api.put<SkillMeta>(`/skill/item/${encodePath(name)}`, {
    frontmatter,
    body,
  });
  return data;
}

export async function renameSkill(name: string, newName: string): Promise<SkillMeta> {
  const { data } = await api.post<SkillMeta>(
    `/skill/item/${encodePath(name)}/rename`,
    { new_name: newName },
  );
  return data;
}

export async function deleteSkill(name: string): Promise<TrashEntry> {
  const { data } = await api.delete<TrashEntry>(`/skill/item/${encodePath(name)}`);
  return data;
}

export async function writeLinkedFile(
  name: string,
  path: string,
  content: string,
): Promise<FileEntry> {
  const { data } = await api.put<FileEntry>(`/skill/file/${encodePath(name)}`, {
    path,
    content,
  });
  return data;
}

export async function deleteLinkedFile(name: string, path: string): Promise<void> {
  await api.delete(`/skill/file/${encodePath(name)}`, { params: { path } });
}

// ---------------------------------------------------------------------------
// Snapshots (flat routes, name goes in body / query)
// ---------------------------------------------------------------------------

export async function createSnapshot(name: string, label?: string): Promise<SnapshotMeta> {
  const { data } = await api.post<SnapshotMeta>("/skill/snapshots", { name, label });
  return data;
}

export async function listSnapshots(name: string): Promise<SnapshotMeta[]> {
  const { data } = await api.get<SnapshotMeta[]>("/skill/snapshots", {
    params: { name },
  });
  return data;
}

export async function diffSnapshot(
  name: string,
  id: string,
  file?: string,
): Promise<SnapshotDiff> {
  const { data } = await api.get<SnapshotDiff>(`/skill/snapshots/${id}/diff`, {
    params: { name, file },
  });
  return data;
}

export async function restoreSnapshot(name: string, id: string): Promise<RestoreResult> {
  const { data } = await api.post<RestoreResult>(`/skill/snapshots/${id}/restore`, {
    name,
  });
  return data;
}

export async function deleteSnapshot(name: string, id: string): Promise<void> {
  await api.delete(`/skill/snapshots/${id}`, { params: { name } });
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

export async function importFromGitHub(input: {
  repo: string;
  branch?: string;
  subdir?: string;
  target_name?: string;
}): Promise<GitHubImportResult> {
  const { data } = await api.post<GitHubImportResult>("/skill/import/github", input);
  return data;
}

// ---------------------------------------------------------------------------
// Trash
// ---------------------------------------------------------------------------

export async function listTrash(): Promise<TrashEntry[]> {
  const { data } = await api.get<TrashEntry[]>("/skill/trash");
  return data;
}

export async function restoreFromTrash(id: string, targetName?: string): Promise<void> {
  await api.post(`/skill/trash/${id}/restore`, { target_name: targetName ?? null });
}

export async function purgeTrash(id: string): Promise<void> {
  await api.delete(`/skill/trash/${id}`);
}

// ---------------------------------------------------------------------------
// Audit + settings
// ---------------------------------------------------------------------------

export async function listAudit(limit = 100): Promise<AuditEvent[]> {
  const { data } = await api.get<AuditEvent[]>("/skill/audit", { params: { limit } });
  return data;
}

export async function getSettings(): Promise<StudioSettings> {
  const { data } = await api.get<StudioSettings>("/skill/settings");
  return data;
}

export async function updateSettings(
  patch: Partial<StudioSettings>,
): Promise<StudioSettings> {
  const { data } = await api.put<StudioSettings>("/skill/settings", patch);
  return data;
}

// ---------------------------------------------------------------------------
// Skill Architect (rigorous multi-stage generation)
// ---------------------------------------------------------------------------

export type ArchitectInput = {
  name: string;
  label?: string;
  type?: string;
  required?: boolean;
  placeholder?: string;
  default?: string;
  options?: string[];
};

export type ArchitectPlan = {
  name?: string;
  category?: string;
  icon?: string;
  description?: string;
  description_zh?: string;
  tool_kind?: string;
  runtime?: string;
  inputs?: ArchitectInput[];
  steps?: string[];
  output_schema?: string;
  mcp_tools_used?: string[];
  pitfalls?: string[];
  [k: string]: unknown;
};

export type ArchitectClarification = {
  question: string;
  options?: string[];
  why?: string;
};

export type ArchitectPlanResponse = {
  stage: "clarify" | "plan";
  clarifications?: ArchitectClarification[];
  understanding?: Record<string, unknown>;
  plan?: ArchitectPlan;
  review?: Record<string, unknown>;
};

export type ArchitectValidation = {
  ok: boolean;
  attempts: number;
  errors: string[];
  warnings: string[];
};

export type ArchitectGenerated = {
  name: string;
  category: string | null;
  frontmatter: Record<string, unknown>;
  body: string;
  preview: string;
  validation: ArchitectValidation;
  plan?: ArchitectPlan;
};

export async function architectPlan(input: {
  idea: string;
  category?: string;
  ref_skill?: string;
  clarifications?: Record<string, string>;
}): Promise<ArchitectPlanResponse> {
  const { data } = await api.post<ArchitectPlanResponse>(
    "/skill/architect/plan",
    input,
    { timeout: 300000 },
  );
  return data;
}

export async function architectGenerate(plan: ArchitectPlan): Promise<ArchitectGenerated> {
  const { data } = await api.post<ArchitectGenerated>(
    "/skill/architect/generate",
    { plan },
    { timeout: 300000 },
  );
  return data;
}

export async function architectOneshot(input: {
  idea: string;
  category?: string;
  ref_skill?: string;
}): Promise<ArchitectGenerated> {
  const { data } = await api.post<ArchitectGenerated>(
    "/skill/architect/oneshot",
    input,
    { timeout: 300000 },
  );
  return data;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Skill names can contain forward slashes (e.g. "research/arxiv").
 * We want the slashes to remain literal path separators in the URL so the
 * backend's {name:path} converter sees them, but every segment still needs
 * individual percent-encoding so a segment like "foo bar" doesn't break.
 */
export function encodePath(name: string): string {
  return name
    .split("/")
    .filter((s) => s.length > 0)
    .map(encodeURIComponent)
    .join("/");
}

/** Skill 中心 → IvyeaAgent 技能库的同步状态。同步过去的技能任务台才能自动匹配到。 */
export interface AgentSyncStatus {
  /** 参与同步的分类。目前只有 amazon —— 全库近百个技能推过去会淹掉匹配。 */
  domains: string[];
  count: number;
  skills: { id: string; name: string; path: string }[];
}

export async function agentSyncStatus(): Promise<AgentSyncStatus> {
  const { data } = await api.get<AgentSyncStatus>("/skill/agent-sync");
  return data;
}

export async function agentSyncRun(): Promise<{ synced: number; removed: number; errors: string[] }> {
  const { data } = await api.post<{ synced: number; removed: number; errors: string[] }>(
    "/skill/agent-sync");
  return data;
}
