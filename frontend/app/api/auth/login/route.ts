import { type NextRequest, NextResponse } from "next/server"
import {
  IDENTIFIANT_SECOURS,
  MODE_NOMINATIF,
  MODE_SECOURS,
  SESSION_COOKIE,
  clearFailedAttempts,
  clientIdentifier,
  cookieMaxAgeSeconds,
  createSessionToken,
  isAuthConfigured,
  lockoutRemaining,
  noteFailedAttempt,
  passwordMatches,
  shouldMarkCookieSecure,
  uiPassword,
} from "@/lib/auth"
import { authHeaders, backendBase } from "@/lib/backend"

export const dynamic = "force-dynamic"

type Corps = { identifiant?: unknown; mot_de_passe?: unknown; password?: unknown }

/**
 * Sign in.
 *
 * Two paths, deliberately distinct:
 *
 *   1. NOMINATIF (normal) — the identifiers are verified by the BACKEND against
 *      `utilisateur.empreinte_mot_de_passe`; the backend opens a row in
 *      `session_ui` and this route stores its id + token inside the signed
 *      cookie. That is what makes « qui a validé quoi » possible, and what makes
 *      a logout effective (the backend sets `revoque_le`).
 *
 *   2. SECOURS — the historical shared password (`SEAMTECH_UI_PASSWORD`) under
 *      the reserved identifier « secours ». It exists so a workshop with no
 *      nominative account yet is not locked out. It has NO database session, so
 *      it cannot be revoked and its actions are attributed to « secours ».
 *      Known limitation, documented in docs/API.md.
 *
 * Anything else — including a nominative-looking login while the backend is
 * unreachable — fails closed. There is no third path.
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
    return NextResponse.json(
      { detail: `Trop de tentatives échouées. Réessayez dans ${Math.ceil(verrou / 1000)} s.` },
      {
        status: 429,
        headers: { "Cache-Control": "no-store", "Retry-After": String(Math.ceil(verrou / 1000)) },
      },
    )
  }

  const cookieOptions = {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: shouldMarkCookieSecure(req.headers.get("x-forwarded-proto")),
    path: "/",
    maxAge: cookieMaxAgeSeconds(),
  }

  // --- 1. Compte de secours (mot de passe partagé) -------------------------
  if (identifiant === IDENTIFIANT_SECOURS) {
    if (!isAuthConfigured()) {
      console.error("[auth] SEAMTECH_UI_PASSWORD is not set; the rescue account is disabled.")
      return NextResponse.json(
        {
          detail:
            "Authentification non configurée : définissez SEAMTECH_UI_PASSWORD (compte de secours) ou utilisez un compte nominatif.",
        },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      )
    }
    if (!passwordMatches(motDePasse, uiPassword())) {
      const restantes = noteFailedAttempt(identifiantClient)
      return NextResponse.json(
        { detail: restantes > 0 ? `Mot de passe incorrect. ${restantes} tentative(s) restante(s).` : "Mot de passe incorrect." },
        { status: 401, headers: { "Cache-Control": "no-store" } },
      )
    }
    clearFailedAttempts(identifiantClient)
    const reponse = NextResponse.json(
      { ok: true, mode: MODE_SECOURS, identifiant: IDENTIFIANT_SECOURS, role: "administrateur", redirectTo: "/" },
      { status: 200, headers: { "Cache-Control": "no-store" } },
    )
    reponse.cookies.set(
      SESSION_COOKIE,
      createSessionToken({
        mode: MODE_SECOURS,
        identifiant: IDENTIFIANT_SECOURS,
        nom: "Compte de secours",
        role: "administrateur",
        idSession: null,
        jeton: null,
        idUtilisateur: null,
      }),
      cookieOptions,
    )
    return reponse
  }

  // --- 2. Compte nominatif (vérifié par le backend) ------------------------
  if (!base) {
    return NextResponse.json(
      {
        detail:
          "Backend non configuré (SEAMTECH_API_URL) : les comptes nominatifs ne peuvent pas être vérifiés. Seul le compte de secours fonctionne.",
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
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
    return NextResponse.json(
      { detail: "Backend SEAMTECH injoignable : connexion impossible." },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    )
  }

  const corpsBackend = await reponseBackend.json().catch(() => ({}) as Record<string, unknown>)
  if (!reponseBackend.ok) {
    // Le verrouillage nominatif vit en base côté backend (il survit à un
    // redémarrage) ; on ne double-compte PAS ici.
    if (reponseBackend.status === 429) {
      return NextResponse.json(corpsBackend, {
        status: 429,
        headers: { "Cache-Control": "no-store", "Retry-After": reponseBackend.headers.get("retry-after") ?? "300" },
      })
    }
    return NextResponse.json(
      { detail: typeof corpsBackend?.detail === "string" ? corpsBackend.detail : "Identifiant ou mot de passe incorrect." },
      { status: reponseBackend.status === 401 ? 401 : reponseBackend.status, headers: { "Cache-Control": "no-store" } },
    )
  }

  clearFailedAttempts(identifiantClient)
  const reponse = NextResponse.json(
    {
      ok: true,
      mode: MODE_NOMINATIF,
      identifiant: corpsBackend.identifiant,
      nom: corpsBackend.nom ?? null,
      role: corpsBackend.role,
      doit_changer_mot_de_passe: corpsBackend.doit_changer_mot_de_passe === true,
      redirectTo: "/",
    },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  )
  reponse.cookies.set(
    SESSION_COOKIE,
    createSessionToken({
      mode: MODE_NOMINATIF,
      identifiant: String(corpsBackend.identifiant),
      nom: typeof corpsBackend.nom === "string" ? corpsBackend.nom : null,
      role: String(corpsBackend.role),
      idSession: Number(corpsBackend.id_session),
      jeton: String(corpsBackend.jeton_session),
      idUtilisateur: Number(corpsBackend.id_utilisateur),
      doitChangerMotDePasse: corpsBackend.doit_changer_mot_de_passe === true,
    }),
    cookieOptions,
  )
  return reponse
}
