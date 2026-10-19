import { cookies } from "next/headers"
import { NextResponse } from "next/server"
import { SESSION_COOKIE, isAuthConfigured, readSessionToken } from "@/lib/auth"
import { sessionBackend } from "@/lib/backend"

export const dynamic = "force-dynamic"

const SANS_CACHE = { "Cache-Control": "no-store", Vary: "Cookie" } as const

/**
 * Who is signed in?
 *
 * The signed cookie is NEVER enough. The backend (Redis) is asked to confirm
 * the `sid` on every call: a session revoked by a logout, by « Déconnecter
 * tous mes appareils », by an administrator, idle-expired (> 30 min) or
 * absolutely expired (> 8 h) must NOT keep the UI logged in.
 *
 * Statuses:
 *   * no cookie                         → 200 {authenticated:false}
 *   * cookie of an OLD format (v1/v2),
 *     tampered, or without a server sid → 401 {authenticated:false, reason}
 *   * cookie signed, session dead       → 401 {authenticated:false, reason}
 *   * Redis/backend unavailable         → 503 (fail-closed, explicit)
 *   * live session                      → 200 {authenticated:true, …}
 *
 * A cookie the server refuses must never answer 200: that is exactly what an
 * attacker would read as « still valid ».
 */
export async function GET() {
  const store = await cookies()
  const session = readSessionToken(store.get(SESSION_COOKIE)?.value)
  const configure = isAuthConfigured()

  if (!session) {
    if (store.get(SESSION_COOKIE)?.value) {
      // Un cookie est présent mais il est refusé (ancien format v1/v2, signature
      // invalide, sid absent). Refus IMMÉDIAT, sans appeler le backend.
      return NextResponse.json(
        { authenticated: false, configured: configure, reason: "cookie_invalide" },
        { status: 401, headers: SANS_CACHE },
      )
    }
    return NextResponse.json(
      { authenticated: false, configured: configure },
      { status: 200, headers: SANS_CACHE },
    )
  }

  try {
    const reponse = await sessionBackend(session.sid)
    if (!reponse) {
      return NextResponse.json(
        {
          authenticated: false,
          configured: configure,
          reason: "session_store_indisponible",
          detail: "Backend non configuré (SEAMTECH_API_URL) : session non vérifiable (fail-closed).",
        },
        { status: 503, headers: SANS_CACHE },
      )
    }
    if (reponse.status === 503) {
      return NextResponse.json(
        {
          authenticated: false,
          configured: configure,
          reason: "session_store_indisponible",
          detail:
            typeof reponse.body.detail === "string"
              ? reponse.body.detail
              : "Magasin de sessions indisponible (Redis) : session non vérifiable (fail-closed).",
        },
        { status: 503, headers: SANS_CACHE },
      )
    }
    if (reponse.status !== 200) {
      return NextResponse.json(
        {
          authenticated: false,
          configured: configure,
          reason: "session_revoquee",
        },
        { status: 401, headers: SANS_CACHE },
      )
    }

    const backend = reponse.body
    return NextResponse.json(
      {
        authenticated: true,
        configured: configure,
        identifiant: backend.identifiant ?? session.identifiant,
        nom: backend.nom ?? session.nom,
        role: backend.role ?? session.role,
        mode: backend.mode ?? session.mode,
        issued_at: backend.issued_at ?? null,
        expire_le: backend.expire_le ?? null,
        session_idle_minutes: backend.session_idle_minutes ?? null,
        session_hours: backend.session_hours ?? null,
        doit_changer_mot_de_passe:
          backend.doit_changer_mot_de_passe === true || session.doitChangerMotDePasse,
      },
      { status: 200, headers: SANS_CACHE },
    )
  } catch {
    // Backend injoignable : on ne peut PAS confirmer la session. Accepter
    // serait mentir sur une révocation possible — refus explicite 503.
    return NextResponse.json(
      {
        authenticated: false,
        configured: configure,
        reason: "backend_injoignable",
        detail: "Backend SEAMTECH injoignable : session non vérifiable (fail-closed).",
      },
      { status: 503, headers: SANS_CACHE },
    )
  }
}
