// Contract mirrors the SEAMTECH Search FastAPI backend (see repo README + api.py).

export type MatchType = "exact_name" | "name" | "path" | "content"

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
}

export interface HealthResponse {
  status: string
  documents: number
  files: number
  folders: number
  last_scan: string | null
  disk_free_bytes?: number
  disk_total_bytes?: number
}
