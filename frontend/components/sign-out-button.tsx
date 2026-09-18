"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"

/** Clear the httpOnly session cookie and return to the sign-in screen. */
export function SignOutButton() {
  const router = useRouter()
  const [busy, setBusy] = useState(false)

  async function signOut() {
    if (busy) return
    setBusy(true)
    try {
      await fetch("/api/auth/logout", { method: "POST", cache: "no-store" })
    } catch {
      // Even if the request fails, drop the user back to /login: the cookie is
      // httpOnly so we cannot clear it client-side, but the gated routes will
      // reject it once it expires, and /login will re-authenticate cleanly.
    } finally {
      router.replace("/login")
      router.refresh()
    }
  }

  return (
    <Button variant="ghost" size="sm" onClick={signOut} disabled={busy} aria-label="Sign out">
      {busy ? "Signing out…" : "Sign out"}
    </Button>
  )
}
