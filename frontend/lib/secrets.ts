/**
 * Validation des secrets frontend (module PUR, testable hors navigateur).
 *
 * `lib/auth.ts` le ré-exporte pour ne pas casser les imports existants.
 */

/**
 * Lot PROD — example secrets are "not configured". `.env.example` publishes
 * placeholder values (« change-me »); accepting them would sign sessions and
 * guard the rescue account with public knowledge. Mirrors the backend
 * `exiger_secrets_production` (config.py): placeholder or shorter than the
 * minimum → unconfigured, and the login page keeps failing closed with
 * its explicit "not configured" screen instead of silently running open.
 */
const SECRETS_EXEMPLES = new Set([
  "change-me",
  "changeme",
  "change_me",
  "changez-moi",
  "exemple",
  "example",
  "motdepasse",
  "mot-de-passe",
  "password",
  "votre-secret",
  "votre-mot-de-passe",
])

export function secretInacceptable(valeur: string, longueurMinimale = 8): boolean {
  const nettoyee = valeur.trim()
  return SECRETS_EXEMPLES.has(nettoyee.toLowerCase()) || nettoyee.length < longueurMinimale
}

/**
 * The shared rescue password. Empty string means "not configured", which makes
 * the rescue path fail — the nominative accounts (backend side) remain the way
 * in, and with no password at all nobody authenticates.
 *
 * 16 caractères minimum : le secours est la seule porte d'entrée quand aucun
 * compte nominatif n'existe encore.
 */
export function uiPassword(): string {
  const brut = process.env.SEAMTECH_UI_PASSWORD?.trim() ?? ""
  if (brut && secretInacceptable(brut, 16)) {
    console.error(
      "[auth] SEAMTECH_UI_PASSWORD is an example/too-short value: treated as NOT configured. " +
        "Generate a dedicated secret (see .env.example).",
    )
    return ""
  }
  return brut
}

export function isAuthConfigured(): boolean {
  return uiPassword().length > 0
}
