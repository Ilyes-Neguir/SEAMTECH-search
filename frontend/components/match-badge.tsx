import type { MatchType } from "@/lib/types"
import { cn } from "@/lib/utils"

const LABELS: Record<MatchType, string> = {
  exact_name: "Exact name",
  name: "Name",
  path: "Path",
  content: "Content",
}

const STYLES: Record<MatchType, string> = {
  exact_name: "border-primary/40 bg-primary/15 text-primary",
  name: "border-primary/25 bg-primary/10 text-primary",
  path: "border-white/15 bg-white/5 text-muted-foreground",
  content: "border-[#5bc0de]/30 bg-[#5bc0de]/10 text-[#8fd6e8]",
}

export function MatchBadge({ type }: { type: MatchType }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        STYLES[type],
      )}
    >
      {LABELS[type]}
    </span>
  )
}
