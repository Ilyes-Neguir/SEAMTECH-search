/**
 * Throttling des tentatives de connexion (frontend, en mémoire).
 *
 * Module PUR (aucune dépendance Next) pour être testable hors navigateur
 * (`node --test`). `lib/auth.ts` le ré-exporte pour ne pas casser les imports
 * existants.
 *
 * Le chemin nominatif est throttlé par le BACKEND (5 échecs / 5 minutes, en
 * base, survit à un redémarrage) ; cette map ne couvre que le mot de passe
 * partagé de secours, et est documentée comme par processus.
 */

// The rescue account. Its password is the historical SEAMTECH_UI_PASSWORD and
// it exists so an operator can still get in when no nominative account does.
// It is deliberately NOT a database row: it therefore has no session to revoke
// and no identity to attribute. Known, documented limitation — see docs/API.md.
export const IDENTIFIANT_SECOURS = "secours"

const MAX_FAILED_ATTEMPTS = 5
const FAILURE_WINDOW_MS = 5 * 60 * 1000
const LOCKOUT_MS = 5 * 60 * 1000
const failures = new Map<string, { count: number; first: number; lockedUntil: number }>()

function attemptKey(identifier: string): string {
  return identifier || "unknown"
}

/**
 * Clé de compteur pour le throttling frontend.
 *
 * Le compte de secours a sa propre clé (`secours:<ip>`) pour qu'une connexion
 * nominative réussie (qui efface le compteur nominatif) ne remette pas à zéro
 * le compteur du secours — sinon 4 essais sur le secours, une connexion
 * nominative réussie, et on recommence indéfiniment.
 */
export function cleTentative(identifiant: string, ip: string): string {
  return identifiant === IDENTIFIANT_SECOURS ? `secours:${ip}` : ip
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
