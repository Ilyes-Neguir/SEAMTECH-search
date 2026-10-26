"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { authedFetch } from "@/lib/authed-fetch"
import { rafraichirSessionGlobale } from "@/lib/use-session"

function useSignOut(endpoint: "/api/auth/logout" | "/api/auth/logout-all") {
  const router = useRouter()
  const [busy, setBusy] = useState(false)

  async function signOut() {
    if (busy) return
    setBusy(true)
    try {
      await authedFetch(endpoint, { method: "POST", cache: "no-store" })
    } catch {
      // Even if the request fails, drop the user back to /login: the session
      // cookie is httpOnly so we cannot clear it client-side, but the gated
      // routes reject an expired/revoked session and /login re-authenticates
      // cleanly.
    } finally {
      // Do not leave the previous identity/role in the persistent layout's
      // SWR cache while the login screen is shown.
      await rafraichirSessionGlobale().catch(() => undefined)
      router.replace("/login")
      router.refresh()
    }
  }

  return { busy, signOut }
}

/** Revoke THIS browser session server-side, then return to sign-in. */
export function SignOutButton() {
  const { busy, signOut } = useSignOut("/api/auth/logout")

  return (
    <Button variant="ghost" size="sm" onClick={signOut} disabled={busy} aria-label="Se déconnecter">
      {busy ? "Déconnexion…" : "Se déconnecter"}
    </Button>
  )
}

/** Revoke every live session for the signed-in account, on every device. */
export function SignOutAllButton() {
  const { busy, signOut } = useSignOut("/api/auth/logout-all")

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={signOut}
      disabled={busy}
      aria-label="Déconnecter tous mes appareils"
      title="Déconnecter tous mes appareils"
    >
      {busy ? "Déconnexion…" : "Tous mes appareils"}
    </Button>
  )
}
