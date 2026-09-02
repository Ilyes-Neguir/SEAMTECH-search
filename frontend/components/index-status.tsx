"use client"

import useSWR from "swr"
import { Database, Circle } from "lucide-react"
import type { HealthResponse } from "@/lib/types"
import { formatDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"

const fetcher = (url: string) => fetch(url).then((r) => r.json())

export function IndexStatus() {
  const { data } = useSWR<HealthResponse & { sample?: boolean }>("/api/health", fetcher, {
    refreshInterval: 60_000,
  })

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

      {data && (
        <>
          <span className="inline-flex items-center gap-1.5">
            <Database className="size-3.5" aria-hidden />
            {data.documents.toLocaleString()} indexed
          </span>
          <span className="hidden sm:inline">
            {data.files.toLocaleString()} files · {data.folders.toLocaleString()} folders
          </span>
          {data.last_scan && (
            <span className="hidden md:inline">Last scan {formatDateTime(data.last_scan.started_at)}</span>
          )}
        </>
      )}
    </div>
  )
}
