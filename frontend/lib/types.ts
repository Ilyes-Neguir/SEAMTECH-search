// Contract mirrors the SEAMTECH Search FastAPI backend (see repo README + api.py).

export type MatchType = "exact_name" | "name" | "path" | "content"
export type ExtractionStatus = "extracted" | "unavailable" | "skipped" | "error" | "timeout" | "not_applicable"

export interface ScanSummary {
  started_at: string
  finished_at?: string | null
  status: string
  scanned: number
  changed: number
  removed: number
  error?: string | null
}

export interface SearchResult {
  name: string
  path: string
  parent: string
  extension: string
  size: number
  modified: string // ISO date string
  is_dir: boolean
  match_type: MatchType
  snippet: string // may contain <mark>/<em> highlight tags
  extraction_status?: ExtractionStatus
  extraction_detail?: string
}

export interface SearchResponse {
  query: string
  count: number
  offset: number
  limit: number
  has_more: boolean
  results: SearchResult[]
}

export interface PreviewChild {
  name: string
  path: string
  is_dir: boolean
  size: number
}

export interface PreviewResponse {
  path: string
  name: string
  is_dir: boolean
  extension?: string
  size?: number
  text?: string
  children?: PreviewChild[]
  extraction_status?: ExtractionStatus
  extraction_detail?: string
}

export interface HealthResponse {
  status: string
  documents: number
  files: number
  folders: number
  last_scan: ScanSummary | null
  disk_free_bytes?: number
  disk_total_bytes?: number
}

export interface ImportCandidate {
  path: string
  name: string
  size: number
  anchors_matched: string[]
  anchor_count: number
}

export interface ExcelSheetSummary {
  name: string
  max_row: number
  max_column: number
  headers: string[]
  sample_rows: Record<string, string>[]
}

export interface ExcelSummary {
  path: string
  name: string
  sheet_names: string[]
  total_sheets: number
  sheets: ExcelSheetSummary[]
}

export interface ImportFileInfo {
  path: string
  name: string
  category: string
  size: number
  extension: string
  extraction_status: string
  report_path?: string | null
  upload_status?: string
}

export interface ImportDimensions {
  length?: number | null
  width?: number | null
  height?: number | null
  unit?: string | null
  length_mm?: number | null
  width_mm?: number | null
  height_mm?: number | null
  unit_normalized?: string | null
}

export interface ImportData {
  reference?: string | null
  material?: string | null
  dimensions?: ImportDimensions | null
  quantity?: number | null
  description?: string | null
  extraction_status: string
  confidence: number
  warnings: string[]
}

export interface ImportResultPayload {
  import_id: string
  source_path: string
  status: string
  files_detected: number
  analyzed_files: number
  technical_pdf?: string | null
  excel_file?: string | null
  excel_summary?: ExcelSummary | null
  data?: ImportData | null
  report_path?: string | null
  report_docx_path?: string | null
  upload_status: string
  warnings: string[]
  files: ImportFileInfo[]
  candidates: ImportCandidate[]
  excel_candidates?: ImportCandidate[]
}

export interface ImportScanPayload {
  source_path: string
  staged_path?: string
  files_detected: number
  candidates: ImportCandidate[]
  excel_candidates?: ImportCandidate[]
  warnings: string[]
}

export interface ImportCorrection {
  reference?: string
  material?: string
  quantity?: number
  description?: string
  dimensions?: {
    length?: number
    width?: number
    height?: number
    unit?: string
  }
}

export interface ImportJobPayload {
  id?: string
  job_id?: string
  import_id?: string
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "needs_review" | "needs_confirmation"
  progress: number
  stage: string
  source_path?: string
  error?: string | null
  result?: ImportResultPayload | null
  created_at?: string
  updated_at?: string
  // Merged top-level fields
  data?: ImportData | null
  technical_pdf?: string | null
  excel_file?: string | null
  excel_summary?: ExcelSummary | null
  report_path?: string | null
  report_docx_path?: string | null
  upload_status?: string
  warnings?: string[]
  files?: ImportFileInfo[]
  candidates?: ImportCandidate[]
  excel_candidates?: ImportCandidate[]
  files_detected?: number
  analyzed_files?: number
}
