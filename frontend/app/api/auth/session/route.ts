import { cookies } from "next/headers"
import { NextResponse } from "next/server"
import { MODE_NOMINATIF, SESSION_COOKIE, isAuthConfigured, readSessionToken } from "@/lib/auth"
import { authHeaders, backendBase } from "@/lib/backend"

export const dynamic = "force-dynamic"

/**
 * Who is signed in?
 *
 * For a nominative session the backend is asked to confirm it on every call:
 * a cookie whose session was revoked (logout on another tab, administrator
 * disabling the account) must NOT keep the UI logged in. If the backend says
 * the session is gone, this route reports `authenticated: false` — the UI then
 * sends the user back to /login.
 *
 * The response never contains the session token, the password, any hash, or
 * backend data beyond the identity of the caller.
 */
export async function GET() {
  const store = await cookies()
  const session = readSessionToken(store.get(SESSION_COOKIE)?.value)
  const configure = isAuthConfigured()

  if (!session) {
    return NextResponse.json(
      { authenticated: false, configured: configure },
      { status: 200, headers: { "Cache-Control": "no-store" } },
    )
  }

  const base = backendBase()
  if (session.mode === MODE_NOMINATIF && base && session.idSession !== null && session.jeton) {
    try {
      const res = await fetch(`${base}/auth/session`, {
        headers: {
          ...authHeaders(),
          "X-SEAMTECH-SESSION": String(session.idSession),
          "X-SEAMTECH-SESSION-JETON": session.jeton,
        },
        cache: "no-store",
      })
      if (!res.ok) {
        // Session révoquée, expirée, ou compte désactivé côté base.
        return NextResponse.json(
          { authenticated: false, configured: configure, reason: "session_revoquee" },
          { status: 200, headers: { "Cache-Control": "no-store" } },
        )
      }
      const backend = await res.json().catch(() => ({}))
      return NextResponse.json(
        {
          authenticated: true,
          configured: configure,
          identifiant: session.identifiant,
          nom: session.nom,
          role: session.role,
          mode: session.mode,
          doit_changer_mot_de_passe:
            backend?.doit_changer_mot_de_passe === true || session.doitChangerMotDePasse,
        },
        { status: 200, headers: { "Cache-Control": "no-store" } },
      )
    } catch {
      // Backend injoignable : on ne peut PAS confirmer la session. Refuser
      // serait brutal (panne passagère) mais accepter serait mentir sur une
      // révocation possible — on refuse, l'opérateur se reconnecte.
      return NextResponse.json(
        { authenticated: false, configured: configure, reason: "backend_injoignable" },
        { status: 200, headers: { "Cache-Control": "no-store" } },
      )
    }
  }

  // Session de secours : aucun état en base à interroger.
  return NextResponse.json(
    {
      authenticated: true,
      configured: configure,
      identifiant: session.identifiant,
      nom: session.nom,
      role: session.role,
      mode: session.mode,
      doit_changer_mot_de_passe: false,
    },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  )
}
