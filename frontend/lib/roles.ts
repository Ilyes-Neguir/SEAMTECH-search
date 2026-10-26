/** Canonical Lot 1 roles shared by server guards and client affordances. */

export const ROLE_ADMIN = "admin"
export const ROLE_LECTURE = "lecture"
export const ROLE_UTILISATEUR = "utilisateur"
export const ROLE_APPROBATEUR = "approbateur"

export type Role =
  | typeof ROLE_ADMIN
  | typeof ROLE_LECTURE
  | typeof ROLE_UTILISATEUR
  | typeof ROLE_APPROBATEUR

export const ROLES: readonly Role[] = [
  ROLE_ADMIN,
  ROLE_LECTURE,
  ROLE_UTILISATEUR,
  ROLE_APPROBATEUR,
] as const

export const ROLE_DEFAUT: Role = ROLE_LECTURE
export const ROLE_SECOURS: Role = ROLE_ADMIN

/** Map only the two historical stored values; unknown values are invalid. */
export function normaliserRole(valeur: unknown): Role | null {
  if (typeof valeur !== "string") return null
  switch (valeur.trim().toLowerCase()) {
    case ROLE_ADMIN:
    case "administrateur":
      return ROLE_ADMIN
    case ROLE_APPROBATEUR:
    case "operateur":
      // Capability-preserving migration: the historical operator corrected and
      // validated fiches, so it maps to approbateur, never utilisateur.
      return ROLE_APPROBATEUR
    case ROLE_UTILISATEUR:
      return ROLE_UTILISATEUR
    case ROLE_LECTURE:
      return ROLE_LECTURE
    default:
      return null
  }
}

/** Secure fallback used only when creating our own signed session payload. */
export function roleOuLecture(valeur: unknown): Role {
  return normaliserRole(valeur) ?? ROLE_DEFAUT
}

export function roleLibelle(role: Role): string {
  switch (role) {
    case ROLE_ADMIN:
      return "Administrateur technique"
    case ROLE_APPROBATEUR:
      return "Approbateur"
    case ROLE_UTILISATEUR:
      return "Utilisateur"
    case ROLE_LECTURE:
      return "Lecture seule"
  }
}

export function estAdmin(role: Role | null | undefined): boolean {
  return role === ROLE_ADMIN
}

export function estApprobateur(role: Role | null | undefined): boolean {
  return role === ROLE_APPROBATEUR
}

export function peutAdministrer(role: Role | null | undefined): boolean {
  return estAdmin(role)
}

export function peutDeciderMetier(role: Role | null | undefined): boolean {
  return estApprobateur(role)
}

/** Imports are operational work: every role except read-only may use them. */
export function peutDeposer(role: Role | null | undefined): boolean {
  return role === ROLE_ADMIN || role === ROLE_UTILISATEUR || role === ROLE_APPROBATEUR
}

/** Only nominative utilisateur/approbateur accounts may create requests. */
export function peutDemander(role: Role | null | undefined): boolean {
  return role === ROLE_UTILISATEUR || role === ROLE_APPROBATEUR
}

/** Direct piece bytes are reserved to a nominative approver. */
export function peutTelechargerPieceDirect(role: Role | null | undefined): boolean {
  return estApprobateur(role)
}
