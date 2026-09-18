// Session authentication for the SEAMTECH Search UI.
//
// Audit issue #5: every route in app/api/* forwarded SEAMTECH_AUTH_TOKEN to
// the backend unconditionally, so anyone who could reach the frontend had
// full read/write access to the document archive — there was no login at all.
//
// This module implements a single shared password (one operator uses this app)
// exchanged for a signed, httpOnly session cookie. Design notes:
//
//   * Stateless HMAC tokens — no session table, no Redis dependency, survives
//     a frontend restart as long as SEAMTECH_SESSION_SECRET is stable.
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

const TOKEN_VERSION = "v1"
const DEFAULT_SESSION_HOURS = 12
const MAX_TOKEN_AGE_MS = 1000 * 60 * 60 * 24 * 30 // refuse absurd expiry claims

// Login throttling. In-memory and per-process: good enough for one operator on
// one container, and deliberately documented as NOT distributed. A real
// brute-force defence across replicas would need the Redis the backend already
// runs; that is out of scope for a single-worker deployment.
const MAX_FAILED_ATTEMPTS = 5
const FAILURE_WINDOW_MS = 5 * 60 * 1000
const LOCKOUT_MS = 5 * 60 * 1000
const failures = new Map<string, { count: number; first: number; lockedUntil: number }>()

let processSecret: string | null = null

function stableCompare(a: string, b: string): boolean {
  const left = Buffer.from(a)
  const right = Buffer.from(b)
  if (left.length !== right.length) return false
  return timingSafeEqual(left, right)
}

/**
 * The shared UI password. Empty string means "not configured", which makes
 * every login attempt fail — the app is unusable rather than unprotected.
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

/** Issue a signed session token valid from now until now + lifetime. */
export function createSessionToken(now: number = Date.now(), lifetimeMs: number = sessionLifetimeMs()): string {
  const issuedAt = now
  const expiresAt = now + lifetimeMs
  const body = `${TOKEN_VERSION}.${issuedAt}.${expiresAt}`
  return `${body}.${sign(body)}`
}

/**
 * Verify a session token. Returns the expiry timestamp, or null when the token
 * is malformed, tampered with, signed by a different secret, or expired.
 *
 * The signature is compared over the *exact* received body string, so any
 * edit to the version, issued-at or expiry invalidates it.
 */
export function verifySessionToken(token: string | undefined | null, now: number = Date.now()): number | null {
  if (!token) return null
  const parts = token.split(".")
  if (parts.length !== 4) return null
  const [version, issuedRaw, expiresRaw, signature] = parts
  if (version !== TOKEN_VERSION) return null

  const issuedAt = Number(issuedRaw)
  const expiresAt = Number(expiresRaw)
  if (!Number.isFinite(issuedAt) || !Number.isFinite(expiresAt)) return null
  if (expiresAt <= issuedAt) return null
  // Guard against a token minted with a ridiculous lifetime being replayed
  // forever if the secret ever leaks into logs.
  if (expiresAt - issuedAt > MAX_TOKEN_AGE_MS) return null

  const body = `${version}.${issuedRaw}.${expiresRaw}`
  const expected = sign(body)
  // Both are base64url of a SHA-256 digest, so lengths always match; the
  // length check inside stableCompare still guards against truncation.
  if (!stableCompare(expected, signature)) return null

  if (expiresAt <= now) return null
  return expiresAt
}

export function isTokenValid(token: string | undefined | null, now: number = Date.now()): boolean {
  return verifySessionToken(token, now) !== null
}

/** True when the current request carries a valid session cookie. */
export async function isAuthenticated(): Promise<boolean> {
  const store = await cookies()
  return isTokenValid(store.get(SESSION_COOKIE)?.value)
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
