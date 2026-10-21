import { type NextRequest, NextResponse } from "next/server"
import {
  CSRF_COOKIE,
  SESSION_COOKIE,
  csrfCookieOptions,
  currentSession,
  sessionCookieOptions,
  shouldMarkCookieSecure,
} from "@/lib/auth"
import { authHeaders, backendBase } from "@/lib/backend"

export const dynamic = "force-dynamic"

const SANS_CACHE = {
  "Cache-Control": "no-store",
  Vary: "Cookie",
  "Clear-Site-Data": '"cache", "cookies", "storage"',
} as const

/**
 * Sign out — POST ONLY.
 *
 * `GET /api/auth/logout` intentionally has no handler: Next answers 405. The
 * old navigation link used GET, which is exactly the reported bug (« cliquer
 * Déconnexion → HTTP 405, cookie jamais supprimé »). A GET must never mutate
 * state, and a link must never be the logout mechanism (CSRF / prefetch).
 *
 * Order of operations:
 *   1. revoke the `sid` in Redis via the backend (the authority);
 *   2. clear BOTH cookies with the exact attributes used when setting them
 *      (Path=/, HttpOnly, SameSite=Lax, Secure when behind TLS);
 *   3. send `Clear-Site-Data: "cache", "cookies", "storage"`.
 *
 * The response NEVER claims a revocation it did not obtain. When a `sid` is
 * presented and the backend is not configured, unreachable, or answers
 * non-2xx (Redis down → 503), this route answers **503** with `ok: false`
 * and `revoquee: false`. The local cookies are still cleared and
 * `Clear-Site-Data` is still sent — this browser must not stay signed in —
 * but the server-side session is reported as what it is: NOT revoked.
 */
/**
 * GET is refused with an explicit 405 and an `Allow` header.
 *
 * Next would answer 405 anyway (no GET export), but a deterministic handler
 * makes the contract testable and tells a human/proxy exactly what is allowed.
 * A GET must never mutate state: prefetchers, link scanners and <img> tags all
 * issue GETs, and a logout must never be triggerable by one.
 */
export function GET() {
  return NextResponse.json(
    { detail: "Déconnexion : méthode POST uniquement." },
    { status: 405, headers: { Allow: "POST", "Cache-Control": "no-store" } },
  )
}

export async function POST(req: NextRequest) {
  const session = await currentSession()
  const base = backendBase()
  const secure = shouldMarkCookieSecure(req.headers.get("x-forwarded-proto"))

  const deconnecteLocalement = (reponse: NextResponse): NextResponse => {
    // Mêmes attributs qu'à la pose : un effacement avec un Path/SameSite
    // différent laisserait le cookie en place.
    reponse.cookies.set(SESSION_COOKIE, "", sessionCookieOptions(0, secure))
    reponse.cookies.set(CSRF_COOKIE, "", csrfCookieOptions(0, secure))
    return reponse
  }

  if (!session?.sid) {
    // Aucun sid présenté : il n'y a rien à révoquer, l'action est idempotente
    // et le navigateur est déjà déconnecté.
    return deconnecteLocalement(
      NextResponse.json({ ok: true, revoquee: false, deja_deconnecte: true }, { status: 200, headers: SANS_CACHE }),
    )
  }

  if (!base) {
    console.error("[auth] SEAMTECH_API_URL absent : sid NON révoqué (fail-closed, 503).")
    return deconnecteLocalement(
      NextResponse.json(
        {
          ok: false,
          revoquee: false,
          reason: "backend_non_configure",
          detail:
            "Backend non configuré (SEAMTECH_API_URL) : la session n'a PAS été révoquée côté serveur. " +
            "Le navigateur est déconnecté localement, le sid reste valide jusqu'à expiration (fail-closed).",
        },
        { status: 503, headers: SANS_CACHE },
      ),
    )
  }

  let reponseBackend: Response
  try {
    reponseBackend = await fetch(`${base}/auth/deconnexion`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ sid: session.sid }),
      cache: "no-store",
    })
  } catch {
    console.error("[auth] Backend injoignable : sid NON révoqué (fail-closed, 503).")
    return deconnecteLocalement(
      NextResponse.json(
        {
          ok: false,
          revoquee: false,
          reason: "backend_injoignable",
          detail:
            "Backend injoignable : la session n'a PAS été révoquée côté serveur. " +
            "Le navigateur est déconnecté localement, le sid reste valide jusqu'à expiration (fail-closed).",
        },
        { status: 503, headers: SANS_CACHE },
      ),
    )
  }

  if (!reponseBackend.ok) {
    const corps = (await reponseBackend.json().catch(() => ({}))) as Record<string, unknown>
    console.error(`[auth] Révocation refusée par le backend (${reponseBackend.status}) : sid NON révoqué.`)
    return deconnecteLocalement(
      NextResponse.json(
        {
          ok: false,
          revoquee: false,
          reason: "revocation_refusee",
          statut_backend: reponseBackend.status,
          detail:
            "La révocation de session a échoué côté serveur (réponse non-2xx du backend) : " +
            "le sid reste valide jusqu'à expiration — le navigateur est déconnecté localement, " +
            "mais la session serveur NE l'est PAS (fail-closed).",
          detail_backend: typeof corps.detail === "string" ? corps.detail : null,
        },
        { status: 503, headers: SANS_CACHE },
      ),
    )
  }

  // 200 du backend = le sid n'est plus vivant (supprimé à l'instant, ou déjà
  // absent — la révocation est idempotente côté backend).
  return deconnecteLocalement(
    NextResponse.json({ ok: true, revoquee: true }, { status: 200, headers: SANS_CACHE }),
  )
}
