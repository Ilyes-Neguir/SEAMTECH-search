import type { SearchResult } from "./types"

// Representative SEAMTECH design/technical corpus. Used only when the app is
// NOT pointed at a live FastAPI backend (SEAMTECH_API_URL is unset), so the
// interface is fully explorable on its own.

interface SampleFile {
  name: string
  path: string
  is_dir: boolean
  size: number
  modified: string
  content?: string
}

const R = "\\\\SERVER\\SEAMTECH\\DesignFiles"

export const SAMPLE_FILES: SampleFile[] = [
  {
    name: "SAIL-2041 Mainsail Cut Plan.pdf",
    path: `${R}\\Clients\\Regate-Marine\\SAIL-2041\\SAIL-2041 Mainsail Cut Plan.pdf`,
    is_dir: false,
    size: 2_418_221,
    modified: "2026-08-24T09:12:00Z",
    content:
      "Mainsail cut plan for SAIL-2041. Panel layout tri-radial, luff 14.20 m, leech 15.05 m, foot 4.85 m. Cloth: Dimension-Polyant CZ90 laminate. Broadseam distribution per station table. Reinforcement patches at head, tack and clew. Reference client Regate-Marine order RM-2041.",
  },
  {
    name: "SAIL-2041 Rig Dimensions.xlsx",
    path: `${R}\\Clients\\Regate-Marine\\SAIL-2041\\SAIL-2041 Rig Dimensions.xlsx`,
    is_dir: false,
    size: 48_112,
    modified: "2026-08-20T15:40:00Z",
    content:
      "P=15.30 E=5.10 I=16.05 J=4.72 forestay length 16.40 mast rake 1.2deg spreader sweep 18deg. Genoa LP 150%. Reference SAIL-2041.",
  },
  {
    name: "SAIL-2041",
    path: `${R}\\Clients\\Regate-Marine\\SAIL-2041`,
    is_dir: true,
    size: 0,
    modified: "2026-08-24T09:12:00Z",
  },
  {
    name: "Genoa Panel Layout REV-C.dxf",
    path: `${R}\\Clients\\Regate-Marine\\SAIL-2041\\CAD\\Genoa Panel Layout REV-C.dxf`,
    is_dir: false,
    size: 1_204_998,
    modified: "2026-08-22T11:05:00Z",
  },
  {
    name: "PVC Laminate Spec Sheet.docx",
    path: `${R}\\Materials\\Datasheets\\PVC Laminate Spec Sheet.docx`,
    is_dir: false,
    size: 132_540,
    modified: "2026-07-30T08:00:00Z",
    content:
      "Flexible PVC laminate technical datasheet. Weight 900 g/m2, tensile strength warp 4200 N/5cm, weft 4000 N/5cm. UV resistance class 8. Welding temperature 420-460 C. Suitable for marine biminis and outdoor structures.",
  },
  {
    name: "Bimini Frame Assembly.pdf",
    path: `${R}\\Products\\Marine\\Bimini\\Bimini Frame Assembly.pdf`,
    is_dir: false,
    size: 875_320,
    modified: "2026-08-11T13:22:00Z",
    content:
      "Bimini top frame assembly instructions. 25mm 316 stainless tube, 4-bow configuration. Fabric tension guidelines and fastening pattern. Torque values for T-fittings. Waterproof welded seams.",
  },
  {
    name: "Spinnaker SAIL-1988 Design Notes.pdf",
    path: `${R}\\Clients\\Voile-Sud\\SAIL-1988\\Spinnaker SAIL-1988 Design Notes.pdf`,
    is_dir: false,
    size: 640_115,
    modified: "2026-06-18T10:44:00Z",
    content:
      "Asymmetric spinnaker design notes. Mid-girth 90% of foot. Nylon 0.75 oz. Radial head construction. Sheet load estimate 380 kg. Client Voile-Sud reference VS-1988.",
  },
  {
    name: "Sailonet Production Standards.docx",
    path: `${R}\\Documentation\\Sailonet Production Standards.docx`,
    is_dir: false,
    size: 210_880,
    modified: "2026-05-02T09:30:00Z",
    content:
      "Sailonet production standards. Seam overlap minimum 22mm. Double-sided tape then triple-step zigzag stitch. Corner rings hydraulically pressed. QA checklist before delivery. Tolerances on finished dimensions +/- 0.3%.",
  },
  {
    name: "Regate-Marine",
    path: `${R}\\Clients\\Regate-Marine`,
    is_dir: true,
    size: 0,
    modified: "2026-08-24T09:12:00Z",
  },
  {
    name: "Outdoor Structure Tension Membrane Calc.xlsx",
    path: `${R}\\Products\\Outdoor\\Outdoor Structure Tension Membrane Calc.xlsx`,
    is_dir: false,
    size: 96_400,
    modified: "2026-07-14T16:10:00Z",
    content:
      "Tension membrane structure calculations. Prestress 3.0 kN/m warp, 2.5 kN/m weft. Wind load 1.1 kN/m2. Anchor point reactions table. PVC-coated polyester type III.",
  },
  {
    name: "Aeronautics Cover Template.dxf",
    path: `${R}\\Products\\Aeronautics\\Aeronautics Cover Template.dxf`,
    is_dir: false,
    size: 512_004,
    modified: "2026-04-27T12:00:00Z",
  },
  {
    name: "SAIL-2041 Delivery Note.pdf",
    path: `${R}\\Clients\\Regate-Marine\\SAIL-2041\\SAIL-2041 Delivery Note.pdf`,
    is_dir: false,
    size: 88_210,
    modified: "2026-08-25T17:00:00Z",
    content:
      "Delivery note for order SAIL-2041. 1 mainsail, 1 genoa. Packed in sail bag. Shipped to Regate-Marine, Port de Sousse. Inspected and approved.",
  },
]

function highlight(text: string, terms: string[]): string {
  let out = text
  for (const t of terms) {
    if (!t) continue
    const re = new RegExp(`(${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig")
    out = out.replace(re, "<mark>$1</mark>")
  }
  return out
}

function makeSnippet(content: string, terms: string[]): string {
  const lower = content.toLowerCase()
  let idx = -1
  for (const t of terms) {
    const i = lower.indexOf(t.toLowerCase())
    if (i >= 0 && (idx === -1 || i < idx)) idx = i
  }
  const start = idx < 0 ? 0 : Math.max(0, idx - 60)
  const slice = content.slice(start, start + 220)
  return (start > 0 ? "…" : "") + highlight(slice, terms) + (start + 220 < content.length ? "…" : "")
}

export function searchSample(query: string, limit: number, offset: number) {
  const terms = query.trim().split(/\s+/).filter(Boolean)
  const q = query.trim().toLowerCase()

  const scored = SAMPLE_FILES.map((f) => {
    const name = f.name.toLowerCase()
    const path = f.path.toLowerCase()
    const ext = f.name.includes(".") ? f.name.slice(f.name.lastIndexOf(".")).toLowerCase() : ""
    const content = f.content?.toLowerCase() ?? ""

    let matchType: SearchResult["match_type"] | null = null
    let rank = 0

    if (name === q) {
      matchType = "exact_name"
      rank = 100
    } else if (terms.some((t) => name.includes(t.toLowerCase()))) {
      matchType = "name"
      rank = 80
    } else if (terms.some((t) => path.includes(t.toLowerCase()))) {
      matchType = "path"
      rank = 60
    } else if (terms.some((t) => content.includes(t.toLowerCase())) || (q.startsWith(".") && ext === q)) {
      matchType = content && terms.some((t) => content.includes(t.toLowerCase())) ? "content" : "name"
      rank = 40
    }

    if (!matchType) return null

    const result: SearchResult = {
      name: f.name,
      path: f.path,
      parent: f.path.slice(0, f.path.lastIndexOf("\\")),
      extension: ext,
      size: f.size,
      modified: f.modified,
      is_dir: f.is_dir,
      match_type: matchType,
      snippet:
        matchType === "content" && f.content
          ? makeSnippet(f.content, terms)
          : highlight(f.name, terms),
    }
    return { result, rank }
  }).filter((x): x is { result: SearchResult; rank: number } => x !== null)

  scored.sort((a, b) => b.rank - a.rank || a.result.name.localeCompare(b.result.name))

  const all = scored.map((s) => s.result)
  const page = all.slice(offset, offset + limit)
  return { results: page, has_more: offset + limit < all.length, total: all.length }
}

export const SAMPLE_STATS = {
  documents: SAMPLE_FILES.length,
  files: SAMPLE_FILES.filter((f) => !f.is_dir).length,
  folders: SAMPLE_FILES.filter((f) => f.is_dir).length,
  last_scan: "2026-08-25T18:04:00Z",
}
