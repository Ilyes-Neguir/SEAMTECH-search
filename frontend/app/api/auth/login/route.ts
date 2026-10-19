import { type NextRequest, NextResponse } from "next/server"
import {
  CSRF_COOKIE,
  IDENTIFIANT_SECOURS,
  MODE_NOMINATIF,
  MODE_SECOURS,
  SESSION_COOKIE,
  clearFailedAttempts,
  clientIdentifier,
  createCsrfToken,
  createSessionToken,
  csrfCookieOptions,
  currentSession,
  isAuthConfigured,
  lockoutRemaining,
  noteFailedAttempt,
  passwordMatches,
  secondsUntil,
  sessionCookieOptions,
  shouldMarkCookieSecure,
  uiPassword,
} from "@/lib/auth"
import { authHeaders, backendBase } from "@/lib/backend"

export const dynamic = "force-dynamic"

type Corps = { identifiant?: unknown; mot_de_passe?: unknown; password?: unknown }

type SessionBackend = {
  sid?: unknown
  identifiant?: unknown
  nom?: unknown
  role?: unknown
  mode?: unknown
  id_session?: unknown
  jeton_session?: unknown
  id_utilisateur?: unknown
  doit_changer_mot_de_passe?: unknown
  expire_le?: unknown
}

const SANS_CACHE = { "Cache-Control": "no-store", Vary: "Cookie" }

function erreur(status: number, detail: string, extra?: Record<string, string>): NextResponse {
  return NextResponse.json(
    { detail },
    { status, headers: { ...SANS_CACHE, ...(status === 429 ? { "Retry-After": extra?.["Retry-After"] ?? "300" } : {}) } },
  )
}

/**
 * Set the httpOnly session cookie (opaque sid) and the readable CSRF cookie.
 *
 * `expire_le` comes from Redis (absolute server expiry); the cookie's Max-Age
 * is aligned on it so the browser drops it at the same time the server does.
 * The server remains the authority: an idle-expired session is refused even if
 * the cookie is still present.
 */
function poserSession(
  req: NextRequest,
  corpsBackend: SessionBackend,
  mode: string,
  fallbackIdentifiant: string,
): NextResponse {
  const sid = typeof corpsBackend.sid === "string" ? corpsBackend.sid : ""
  if (!sid) {
    console.error("[auth] Le backend n'a pas renvoyé de sid : session refusée (fail-closed).")
    return erreur(503, "Session serveur non créée (sid absent). Connexion refusée.")
  }
  const expireLe = typeof corpsBackend.expire_le === "string" ? corpsBackend.expire_le : null
  const maxAge = secondsUntil(expireLe)
  const secure = shouldMarkCookieSecure(req.headers.get("x-forwarded-proto"))
  const identifiant = String(corpsBackend.identifiant ?? fallbackIdentifiant)
  const nom = typeof corpsBackend.nom === "string" ? corpsBackend.nom : null
  const role = String(corpsBackend.role ?? "operateur")
  const doitChanger = corpsBackend.doit_changer_mot_de_passe === true
  const csrf = createCsrfToken()

  let token: string
  try {
    token = createSessionToken({
      sid,
      mode,
      identifiant,
      nom,
      role,
      idUtilisateur: Number.isInteger(corpsBackend.id_utilisateur) ? Number(corpsBackend.id_utilisateur) : null,
      doitChangerMotDePasse: doitChanger,
      expireLe,
    })
  } catch (error) {
    console.error("[auth] Jeton de session non émis :", error)
    return erreur(503, "Session serveur invalide : connexion refusée (fail-closed).")
  }

  const reponse = NextResponse.json(
    {
      ok: true,
      mode,
      identifiant,
      nom,
      role,
      doit_changer_mot_de_passe: doitChanger,
      redirectTo: "/",
    },
    { status: 200, headers: SANS_CACHE },
  )
  reponse.cookies.set(SESSION_COOKIE, token, sessionCookieOptions(maxAge, secure))
  reponse.cookies.set(CSRF_COOKIE, csrf, csrfCookieOptions(maxAge, secure))
  return reponse
}

/**
 * Sign in.
 *
 * Two paths, deliberately distinct:
 *
 *   1. NOMINATIF (normal) — the identifiers are verified by the BACKEND against
 *      `utilisateur.empreinte_mot_de_passe`; the backend opens a Redis session
 *      and this route stores its opaque `sid` inside the signed cookie.
 *
 *   2. SECOURS — the historical shared password (`SEAMTECH_UI_PASSWORD`) under
 *      the reserved identifier « secours ». The password is verified here, then
 *      the BACKEND opens a real Redis session too: « secours » is no longer an
 *      unrevocable, stateless session.
 *
 * Anything else — including a nominative-looking login while the backend is
 * unreachable — fails closed. There is no third path. Redis unavailable means
 * 503: we never accept a session we cannot verify.
 */
export async function POST(req: NextRequest) {
  const base = backendBase()

  let corps: Corps = {}
  const contentType = req.headers.get("content-type") ?? ""
  try {
    if (contentType.includes("application/json")) {
      corps = ((await req.json().catch(() => null)) ?? {}) as Corps
    } else {
      const form = await req.formData().catch(() => null)
      corps = {
        identifiant: form?.get("identifiant") ?? undefined,
        mot_de_passe: form?.get("mot_de_passe") ?? form?.get("password") ?? undefined,
      }
    }
  } catch {
    corps = {}
  }

  const identifiantBrut =
    typeof corps.identifiant === "string" ? corps.identifiant : ""
  // Compatibilité : un formulaire qui n'envoie que « password » (ancien champ)
  // est traité comme une tentative du compte de secours.
  const motDePasseBrut =
    typeof corps.mot_de_passe === "string"
      ? corps.mot_de_passe
      : typeof corps.password === "string"
        ? corps.password
        : ""
  const identifiant = identifiantBrut.trim() || IDENTIFIANT_SECOURS
  const motDePasse = motDePasseBrut

  const identifiantClient = clientIdentifier(req.headers.get("x-forwarded-for"), req.headers.get("x-real-ip"))
  const verrou = lockoutRemaining(identifiantClient)
  if (verrou > 0) {
    return erreur(429, `Trop de tentatives échouées. Réessayez dans ${Math.ceil(verrou / 1000)} s.`, {
      "Retry-After": String(Math.ceil(verrou / 1000)),
    })
  }

  // Session précédente : elle sera révoquée APRÈS une authentification
  // réussie (anti-fixation), jamais avant — un échec ne doit pas déconnecter
  // l'utilisateur déjà connecté.
  const ancienne = await currentSession()

  // --- 1. Compte de secours (mot de passe partagé) -------------------------
  if (identifiant === IDENTIFIANT_SECOURS) {
    if (!isAuthConfigured()) {
      console.error("[auth] SEAMTECH_UI_PASSWORD is not set; the rescue account is disabled.")
      return erreur(
        503,
        "Authentification non configurée : définissez SEAMTECH_UI_PASSWORD (compte de secours) ou utilisez un compte nominatif.",
      )
    }
    if (!passwordMatches(motDePasse, uiPassword())) {
      const restantes = noteFailedAttempt(identifiantClient)
      return erreur(
        401,
        restantes > 0 ? `Mot de passe incorrect. ${restantes} tentative(s) restante(s).` : "Mot de passe incorrect.",
      )
    }
    if (!base) {
      return erreur(
        503,
        "Backend non configuré (SEAMTECH_API_URL) : la session de secours doit être vérifiée dans Redis. " +
          "Connexion refusée (fail-closed).",
      )
    }
    let reponseBackend: Response
    try {
      reponseBackend = await fetch(`${base}/auth/connexion-secours`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: "{}",
        cache: "no-store",
      })
    } catch {
      return erreur(503, "Backend SEAMTECH injoignable : session de secours non créée (fail-closed).")
    }
    const corpsBackend = (await reponseBackend.json().catch(() => ({}))) as SessionBackend
    if (!reponseBackend.ok) {
      console.error(`[auth] Session de secours refusée par le backend (${reponseBackend.status}).`)
      return erreur(503, "Magasin de sessions indisponible (Redis) : connexion de secours refusée.")
    }
    clearFailedAttempts(identifiantClient)
    if (ancienne?.sid) {
      await revoquerAncienne(base, ancienne.sid)
    }
    return poserSession(req, corpsBackend, MODE_SECOURS, IDENTIFIANT_SECOURS)
  }

  // --- 2. Compte nominatif (vérifié par le backend) ------------------------
  if (!base) {
    return erreur(
      503,
      "Backend non configuré (SEAMTECH_API_URL) : les comptes nominatifs ne peuvent pas être vérifiés.",
    )
  }

  let reponseBackend: Response
  try {
    reponseBackend = await fetch(`${base}/auth/connexion`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ identifiant, mot_de_passe: motDePasse }),
      cache: "no-store",
    })
  } catch {
    return erreur(502, "Backend SEAMTECH injoignable : connexion impossible.")
  }

  const corpsBackend = (await reponseBackend.json().catch(() => ({}))) as SessionBackend & { detail?: unknown }
  if (!reponseBackend.ok) {
    // Le verrouillage nominatif vit en base côté backend (il survit à un
    // redémarrage) ; on ne double-compte PAS ici.
    if (reponseBackend.status === 429) {
      return erreur(429, "Trop de tentatives échouées. Réessayez plus tard.", {
        "Retry-After": reponseBackend.headers.get("retry-after") ?? "300",
      })
    }
    if (reponseBackend.status === 503) {
      return erreur(
        503,
        typeof corpsBackend.detail === "string"
          ? corpsBackend.detail
          : "Magasin de sessions indisponible (Redis) : connexion refusée (fail-closed).",
      )
    }
    return erreur(
      reponseBackend.status === 401 ? 401 : reponseBackend.status,
      typeof corpsBackend.detail === "string" ? corpsBackend.detail : "Identifiant ou mot de passe incorrect.",
    )
  }

  clearFailedAttempts(identifiantClient)
  if (ancienne?.sid && ancienne.sid !== corpsBackend.sid) {
    await revoquerAncienne(base, ancienne.sid)
  }
  return poserSession(req, corpsBackend, MODE_NOMINATIF, identifiant)
}

/** Best-effort revocation of the pre-login sid (session fixation / rotation). */
async function revoquerAncienne(base: string, sid: string): Promise<void> {
  try {
    await fetch(`${base}/auth/deconnexion`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ sid }),
      cache: "no-store",
    })
  } catch {
    // L'ancienne session expirera d'elle-même ; ne bloque jamais le login.
    console.warn("[auth] Ancienne session non révoquée (backend injoignable) ; elle expirera par TTL.")
  }
}
