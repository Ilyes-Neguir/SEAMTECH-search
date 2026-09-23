// Session authentication for the SEAMTECH Search UI.
//
// Audit issue #5: every route in app/api/* forwarded SEAMTECH_AUTH_TOKEN to
// the backend unconditionally, so anyone who could reach the frontend had
// full read/write access to the document archive — there was no login at all.
//
// Lot L.2 turns that single shared password into NOMINATIVE accounts: the
// backend owns `utilisateur` + `session_ui`, and this module carries the
// identity in an httpOnly signed cookie so the validation journal can say
// « qui a validé quoi ».
//
// Design notes:
//
//   * The cookie payload (v2) holds the nominative identity AND the backend
//     session id + token. The token is what makes revocation possible: the
//     backend stores only its SHA-256, and `revoque_le` closes the session
//     even though the cookie is still cryptographically valid.
//   * Stateless signature, stateful session: we sign locally (no Redis, no
//     extra dependency) but the backend remains the source of truth for
//     whether the session still exists.
//   * v1 tokens (shared password, no identity) are REFUSED. Accepting them
//     would mean an unrevocable, unattributable session surviving the
//     upgrade; after deploy, everyone signs in again.
//   * node:crypto only — no new npm dependencies to audit.
//   * Fails closed: with no password configured, nobody can authenticate.
//
// SEAMTECH_AUTH_TOKEN (lib/backend.ts) is a *server-to-server* secret and is
// still never sent to the browser. This cookie authenticates the human; the
// token authenticates the frontend container to the backend.

import { createHmac, randomBytes, timingSafeEqual } from "crypto"
import { cookies } from "next/headers"
import { NextResponse } from "next/server"

export const SESSION_COOKIE = "seamtech_session"

const TOKEN_VERSION = "v2"
const DEFAULT_SESSION_HOURS = 12
const MAX_TOKEN_AGE_MS = 1000 * 60 * 60 * 24 * 30 // refuse absurd expiry claims

// The rescue account. Its password is the historical SEAMTECH_UI_PASSWORD and
// it exists so an operator can still get in when no nominative account does.
// It is deliberately NOT a database row: it therefore has no session to revoke
// and no identity to attribute. Known, documented limitation — see docs/API.md.
export const IDENTIFIANT_SECOURS = "secours"
export const MODE_SECOURS = "secours"
export const MODE_NOMINATIF = "nominatif"

// Login throttling for the rescue path. The nominative path is throttled by the
// BACKEND (5 failures / 5 minutes, in the database, survives a restart); this
// in-memory map only covers the shared password, and is documented as
// per-process.
const MAX_FAILED_ATTEMPTS = 5
const FAILURE_WINDOW_MS = 5 * 60 * 1000
const LOCKOUT_MS = 5 * 60 * 1000
const failures = new Map<string, { count: number; first: number; lockedUntil: number }>()

let processSecret: string | null = null

export type SessionPayload = {
  /** "nominatif" (database session) or "secours" (shared rescue password). */
  mode: string
  identifiant: string
  nom: string | null
  role: string
  /** null in rescue mode: there is no database session to revoke. */
  idSession: number | null
  /** Backend session token — never leaves the server. */
  jeton: string | null
  idUtilisateur: number | null
  doitChangerMotDePasse: boolean
  expiresAt: number
}

function stableCompare(a: string, b: string): boolean {
  const left = Buffer.from(a)
  const right = Buffer.from(b)
  if (left.length !== right.length) return false
  return timingSafeEqual(left, right)
}

/**
 * The shared rescue password. Empty string means "not configured", which makes
 * the rescue path fail — the nominative accounts (backend side) remain the way
 * in, and with no password at all nobody authenticates.
 */
export function uiPassword(): string {
  return process.env.SEAMTECH_UI_PASSWORD?.trim() ?? ""
}

export function isAuthConfigured(): boolean {
  return uiPassword().length > 0
}

/**
 * Signing key for session tokens. When SEAMTECH_SESSION_SECRET is unset we
 * fall back to a random per-process secret: sessions then die on restart,
 * which is an availability annoyance, not a security hole. Failing open here
 * would mean accepting unsigned tokens, which we never do.
 */
export function sessionSecret(): string {
  const configured = process.env.SEAMTECH_SESSION_SECRET?.trim()
  if (configured) return configured
  if (!processSecret) {
    processSecret = randomBytes(32).toString("base64url")
    console.warn(
      "[auth] SEAMTECH_SESSION_SECRET is not set; using a random per-process secret. " +
        "Sessions will not survive a restart or work across replicas.",
    )
  }
  return processSecret
}

function sign(payload: string): string {
  return createHmac("sha256", sessionSecret()).update(payload).digest("base64url")
}

export function sessionLifetimeMs(): number {
  const hours = Number(process.env.SEAMTECH_SESSION_HOURS ?? DEFAULT_SESSION_HOURS)
  if (!Number.isFinite(hours) || hours <= 0 || hours > 24 * 30) return DEFAULT_SESSION_HOURS * 3600 * 1000
  return hours * 3600 * 1000
}

export type NouvelleSession = {
  mode: string
  identifiant: string
  nom?: string | null
  role: string
  idSession?: number | null
  jeton?: string | null
  idUtilisateur?: number | null
  doitChangerMotDePasse?: boolean
}

/**
 * Issue a signed v2 session token. The identity travels INSIDE the signature:
 * anything the backend later trusts as an attribution header is recomputed from
 * this payload, never from a query parameter or a request body.
 */
export function createSessionToken(
  session: NouvelleSession,
  now: number = Date.now(),
  lifetimeMs: number = sessionLifetimeMs(),
): string {
  const expiresAt = now + lifetimeMs
  const charge = {
    mode: session.mode,
    identifiant: session.identifiant,
    nom: session.nom ?? null,
    role: session.role,
    idSession: session.idSession ?? null,
    jeton: session.jeton ?? null,
    idUtilisateur: session.idUtilisateur ?? null,
    doitChangerMotDePasse: session.doitChangerMotDePasse ?? false,
  }
  const encoded = Buffer.from(JSON.stringify(charge), "utf8").toString("base64url")
  const body = `${TOKEN_VERSION}.${now}.${expiresAt}.${encoded}`
  return `${body}.${sign(body)}`
}

function decodeCharge(encoded: string): Omit<SessionPayload, "expiresAt"> | null {
  try {
    const parsed = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"))
    if (typeof parsed?.identifiant !== "string" || typeof parsed?.role !== "string") return null
    if (parsed.mode !== MODE_NOMINATIF && parsed.mode !== MODE_SECOURS) return null
    if (parsed.mode === MODE_NOMINATIF && (!Number.isInteger(parsed?.idSession) || typeof parsed?.jeton !== "string")) {
      return null
    }
    return {
      mode: parsed.mode,
      identifiant: parsed.identifiant,
      nom: typeof parsed.nom === "string" ? parsed.nom : null,
      role: parsed.role,
      idSession: Number.isInteger(parsed.idSession) ? parsed.idSession : null,
      jeton: typeof parsed.jeton === "string" ? parsed.jeton : null,
      idUtilisateur: Number.isInteger(parsed.idUtilisateur) ? parsed.idUtilisateur : null,
      doitChangerMotDePasse: parsed.doitChangerMotDePasse === true,
    }
  } catch {
    return null
  }
}

/**
 * Verify a session token and return its payload, or null when it is malformed,
 * tampered with, signed by a different secret, expired, or an old v1 token.
 *
 * The signature is compared over the *exact* received body string, so any edit
 * to the version, timestamps or identity invalidates it.
 */
export function readSessionToken(
  token: string | undefined | null,
  now: number = Date.now(),
): SessionPayload | null {
  if (!token) return null
  const parts = token.split(".")
  if (parts.length !== 5) return null
  const [version, issuedRaw, expiresRaw, encoded, signature] = parts
  if (version !== TOKEN_VERSION) return null

  const issuedAt = Number(issuedRaw)
  const expiresAt = Number(expiresRaw)
  if (!Number.isFinite(issuedAt) || !Number.isFinite(expiresAt)) return null
  if (expiresAt <= issuedAt) return null
  if (expiresAt - issuedAt > MAX_TOKEN_AGE_MS) return null

  const body = `${version}.${issuedRaw}.${expiresRaw}.${encoded}`
  if (!stableCompare(sign(body), signature)) return null
  if (expiresAt <= now) return null

  const charge = decodeCharge(encoded)
  if (!charge) return null
  return { ...charge, expiresAt }
}

export function isTokenValid(token: string | undefined | null, now: number = Date.now()): boolean {
  return readSessionToken(token, now) !== null
}

/** True when the current request carries a valid session cookie. */
export async function isAuthenticated(): Promise<boolean> {
  const store = await cookies()
  return isTokenValid(store.get(SESSION_COOKIE)?.value)
}

/** The current session payload, or null. Server-side only. */
export async function currentSession(): Promise<SessionPayload | null> {
  const store = await cookies()
  return readSessionToken(store.get(SESSION_COOKIE)?.value)
}

/**
 * Gate for app/api/* route handlers. Returns null when the caller holds a
 * valid session; otherwise a 401 JSON response to return immediately.
 *
 * Usage:
 *   const denied = await requireAuth()
 *   if (denied) return denied
 */
export async function requireAuth(): Promise<NextResponse | null> {
  if (await isAuthenticated()) return null
  return NextResponse.json(
    { detail: "Authentication required. Sign in at /login." },
    { status: 401, headers: { "Cache-Control": "no-store" } },
  )
}

function attemptKey(identifier: string): string {
  return identifier || "unknown"
}

/** Remaining lockout in ms for an identifier, or 0 when not locked out. */
export function lockoutRemaining(identifier: string, now: number = Date.now()): number {
  const entry = failures.get(attemptKey(identifier))
  if (!entry) return 0
  if (entry.lockedUntil > now) return entry.lockedUntil - now
  return 0
}

export function noteFailedAttempt(identifier: string, now: number = Date.now()): number {
  const key = attemptKey(identifier)
  const entry = failures.get(key)
  if (!entry || now - entry.first > FAILURE_WINDOW_MS) {
    failures.set(key, { count: 1, first: now, lockedUntil: 0 })
    return MAX_FAILED_ATTEMPTS - 1
  }
  entry.count += 1
  let remaining = MAX_FAILED_ATTEMPTS - entry.count
  if (entry.count >= MAX_FAILED_ATTEMPTS) {
    entry.lockedUntil = now + LOCKOUT_MS
    remaining = 0
  }
  return Math.max(0, remaining)
}

export function clearFailedAttempts(identifier: string): void {
  failures.delete(attemptKey(identifier))
}

/** Test/maintenance hook: drop all throttling state. */
export function resetFailedAttempts(): void {
  failures.clear()
}

/**
 * Constant-time password check. Both sides are hashed first so differing
 * lengths cannot be distinguished by timing or by timingSafeEqual throwing.
 */
export function passwordMatches(candidate: string, expected: string): boolean {
  if (!expected) return false
  const left = createHmac("sha256", "seamtech-password-check").update(candidate).digest()
  const right = createHmac("sha256", "seamtech-password-check").update(expected).digest()
  return timingSafeEqual(left, right)
}

/** Best-effort client identifier for throttling (behind a proxy or not). */
export function clientIdentifier(forwardedFor: string | null, fallbackIp: string | null): string {
  const first = forwardedFor?.split(",")[0]?.trim()
  return first || fallbackIp || "unknown"
}

export function cookieMaxAgeSeconds(lifetimeMs: number = sessionLifetimeMs()): number {
  return Math.floor(lifetimeMs / 1000)
}

/**
 * Cookie `secure` flag. Set when the request arrived over TLS, or when the
 * deployment declares it sits behind a TLS terminator (SEAMTECH_BEHIND_TLS_PROXY).
 * Plain-HTTP office LAN installs must still work, so this is not unconditional.
 */
export function shouldMarkCookieSecure(xForwardedProto: string | null): boolean {
  if (process.env.SEAMTECH_SECURE_COOKIES === "true") return true
  if (process.env.SEAMTECH_BEHIND_TLS_PROXY === "true") return true
  return (xForwardedProto ?? "").split(",")[0]?.trim().toLowerCase() === "https"
}
