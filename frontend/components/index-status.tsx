"use client"

import useSWR from "swr"
import { Database, Circle } from "lucide-react"
import type { HealthResponse } from "@/lib/types"
import { formatDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import { authedFetch } from "@/lib/authed-fetch"

const fetcher = (url: string) => authedFetch(url).then((r) => r.json())

export function IndexStatus() {
  const { data } = useSWR<Partial<HealthResponse> & { sample?: boolean; authenticated?: boolean }>(
    "/api/health",
    fetcher,
    {
      refreshInterval: 60_000,
    },
  )

  // /api/health returns a minimal liveness payload when the session has
  // expired, so the archive counts may legitimately be absent here. Narrowing
  // all three at once keeps the render branch type-safe.
  const counts =
    typeof data?.documents === "number" && typeof data.files === "number" && typeof data.folders === "number"
      ? { documents: data.documents, files: data.files, folders: data.folders }
      : null

  const online = data?.status === "ok"

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <Circle
          className={cn("size-2 fill-current", online ? "text-success" : "text-muted-foreground")}
          aria-hidden
        />
        {data ? (online ? "Index online" : "Index offline") : "Checking…"}
        {data?.sample && <span className="text-primary/80">(sample data)</span>}
      </span>

      {counts && (
        <>
          <span className="inline-flex items-center gap-1.5">
            <Database className="size-3.5" aria-hidden />
            {counts.documents.toLocaleString()} indexed
          </span>
          <span className="hidden sm:inline">
            {counts.files.toLocaleString()} files · {counts.folders.toLocaleString()} folders
          </span>
          {data?.last_scan && (
            <span className="hidden md:inline">Last scan {formatDateTime(data.last_scan.started_at)}</span>
          )}
        </>
      )}
    </div>
  )
}
