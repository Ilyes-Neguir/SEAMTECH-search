"use client"

import Link from "next/link"
import useSWR from "swr"
import { Bell } from "lucide-react"
import { chargerNotifications } from "@/lib/approbations"
import { useSession } from "@/lib/use-session"

export function NotificationsBell() {
  const { session } = useSession()
  const nominatif = session.mode === "nominatif"
  const { data } = useSWR(
    nominatif ? "notifications-non-lues" : null,
    () =>
      chargerNotifications(
        new URLSearchParams({ seulement_non_lues: "true", limit: "1", offset: "0" }),
      ),
    { refreshInterval: 10_000, shouldRetryOnError: false, revalidateOnFocus: true },
  )

  if (!nominatif) return null
  const nonLues = data?.non_lues ?? 0

  return (
    <Link
      href="/notifications"
      className="relative rounded p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      aria-label={`Notifications, ${nonLues} non lue${nonLues > 1 ? "s" : ""}`}
      title="Notifications"
      data-testid="notifications-bell"
    >
      <Bell className="size-4" aria-hidden />
      {nonLues > 0 && (
        <span
          className="absolute -right-0.5 -top-0.5 flex min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[9px] font-bold leading-4 text-white"
          data-testid="notifications-non-lues"
        >
          {nonLues > 99 ? "99+" : nonLues}
        </span>
      )}
    </Link>
  )
}
