import { NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { SAMPLE_STATS } from "@/lib/sample-data"
import type { HealthResponse } from "@/lib/types"

export async function GET() {
  const base = backendBase()
  if (base) {
    try {
      const res = await fetch(`${base}/health`, { headers: authHeaders(), cache: "no-store" })
      return NextResponse.json(await res.json(), { status: res.status })
    } catch {
      return NextResponse.json({ status: "unreachable" }, { status: 502 })
    }
  }

  const payload: HealthResponse = {
    status: "ok",
    documents: SAMPLE_STATS.documents,
    files: SAMPLE_STATS.files,
    folders: SAMPLE_STATS.folders,
    last_scan: SAMPLE_STATS.last_scan,
  }
  return NextResponse.json({ ...payload, sample: true })
}
