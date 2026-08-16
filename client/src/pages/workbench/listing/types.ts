// Listing 工作台类型契约（对应 server/app/routers/listing/*）

export type JobStatus = "running" | "done" | "failed";

export interface Job {
  id: string;
  project_id: string;
  kind: string;
  status: JobStatus;
  stage: string;
  message: string;
  progress: number;
  total: number;
  done_count: number;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: string;
  created_at: number;
  updated_at: number;
}

export interface ProjectSummary {
  id: string;
  asin: string;
  marketplace: string;
  status: string;
  title?: string | null;
  created_at: number;
  updated_at: number;
  active_jobs: string[];
}

export interface ProjectDetail extends Omit<ProjectSummary, "active_jobs"> {
  scrape_data?: string | null;
  analysis_data?: string | null;
  copy_result?: string | null;
  copy_job_id?: string | null;
  creative_sets?: string | null;
  imgflow_project_id?: string | null;
}

export interface ProductInfo {
  product_name: string;
  description: string;
  selling_points: string;
  target_audience: string;
}

export interface ScrapeSummary {
  title: string;
  bullets: string[];
  description: string;
  images: string[];
  source: string;
  fullImagesAvailable: boolean;
}

export interface UploadedRef {
  filename: string;
  url: string;
  white_ready: boolean;
}

export interface RefImages {
  scraped: string[];
  uploaded: UploadedRef[];
  white_product_source: string;
}

export interface CopyResult {
  rationale?: string;
  titles?: string[];
  highlights?: string;
  bullets_a?: string[];
  bullets_b?: string[];
  search_terms?: string[];
  compliance_notes?: string[];
  raw?: string;
}

export interface RenderQa {
  ready?: boolean;
  score?: number;
  issues?: { code: string; severity: string; message: string }[];
  manual_visual_review_required?: boolean;
  retry_guidance?: string[];
}

export interface PlanVersion {
  url: string;
  base_url?: string;
  render_qa?: RenderQa | null;
  created_at?: string;
}

export interface PlanImage {
  slot: string;
  role: string;
  shot_type: string;
  product_presence?: "hero" | "supporting" | "environmental" | "absent";
  layout?: string;
  subline?: string | null;
  big_number?: string | null;
  template_url?: string;
  buyer_question?: string;
  selling_point?: string | null;
  evidence?: string;
  headline?: string | null;
  eyebrow?: string | null;
  callout?: string | null;
  supporting_text?: string | null;
  proof?: string | null;
  text_on_image?: boolean;
  text_zone?: string;
  text_pos?: string;
  layout_style?: string;
  size?: string;
  render_prompt?: string;
  acceptance_criteria?: string[];
  product_source_url?: string;
  base_url?: string;
  final_url?: string;
  render_qa?: RenderQa | null;
  versions?: PlanVersion[];
  human_reviewed?: boolean;
  auto_retry_count?: number;
  last_retry_guidance?: string[];
}

export interface PlanQuality {
  score: number;
  ready: boolean;
  issues: { code: string; severity: string; message: string }[];
  /** 因视觉能力不足被跳过的分析（如竞品套图版式逆向）。
   *  必须显式展示：静默跳过会让用户以为功能坏了，而不知道配个视觉模型就能解锁。 */
  skipped_analyses?: { stage: string; code: string; message: string }[];
  /** 1 主脑直读 · 2 视觉旁路 · 3 本地 CV 量化 · 0 无 */
  vision_tier?: 0 | 1 | 2 | 3;
  vision_tier_label?: string;
}

export interface Plan {
  deliverable: "gallery" | "aplus";
  planner?: "ai" | "fallback" | "";
  style?: { direction?: string; palette?: string; lighting?: string; accent_color?: string } | null;
  product_profile?: { category_family?: string; object_behavior?: string; fidelity_anchors?: string[] } | null;
  product_lock?: string;
  story?: string;
  creative_brief?: string;
  language?: string;
  product_source_url?: string;
  images: PlanImage[];
  quality?: PlanQuality;
  set_qa?: { ready?: boolean; score?: number; issues?: { code: string; severity: string; message: string }[] } | null;
}

export type CreativeSets = Partial<Record<"gallery" | "aplus", Plan>>;

export type ToastTone = "success" | "warn" | "error" | "info";
