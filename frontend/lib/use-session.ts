"use client"

import useSWR, { mutate } from "swr"
import { authedFetch } from "@/lib/authed-fetch"
import { ROLE_DEFAUT, roleOuLecture, type Role } from "@/lib/roles"

export const CLE_SESSION_SWR = "/api/auth/session"

export type SessionClient = {
  authenticated: boolean
  identifiant: string | null
  nom: string | null
  role: Role
  mode: "nominatif" | "secours" | null
  doitChangerMotDePasse: boolean
}

type SessionApi = {
  authenticated?: unknown
  identifiant?: unknown
  nom?: unknown
  role?: unknown
  mode?: unknown
  doit_changer_mot_de_passe?: unknown
}

const SESSION_ANONYME: SessionClient = {
  authenticated: false,
  identifiant: null,
  nom: null,
  role: ROLE_DEFAUT,
  mode: null,
  doitChangerMotDePasse: false,
}

async function chargerSession(url: string): Promise<SessionClient> {
  const response = await authedFetch(url, { cache: "no-store" })
  const corps = (await response.json().catch(() => ({}))) as SessionApi
  if (!response.ok || corps.authenticated !== true) return SESSION_ANONYME
  return {
    authenticated: true,
    identifiant: typeof corps.identifiant === "string" ? corps.identifiant : null,
    nom: typeof corps.nom === "string" ? corps.nom : null,
    role: roleOuLecture(corps.role),
    mode: corps.mode === "nominatif" || corps.mode === "secours" ? corps.mode : null,
    doitChangerMotDePasse: corps.doit_changer_mot_de_passe === true,
  }
}

/**
 * Browser view of the verified session. UI hiding is only an affordance:
 * every mutation still passes through a Next guard and the backend guard.
 */
export function useSession() {
  const { data, error, isLoading, mutate: rafraichir } = useSWR<SessionClient>(
    CLE_SESSION_SWR,
    chargerSession,
    { revalidateOnFocus: true, shouldRetryOnError: false },
  )
  const session = data ?? SESSION_ANONYME
  return {
    session,
    role: session.role,
    estNominatif: session.mode === "nominatif",
    chargement: isLoading,
    erreur: error,
    rafraichir,
  }
}

/**
 * Force every mounted session consumer to re-read /api/auth/session.
 *
 * The root layout (and therefore Nav/useSession) stays mounted across the
 * SPA login/logout transitions. Without this explicit revalidation, SWR can
 * retain the pre-login anonymous session (role=lecture) after the cookie has
 * been set, hiding role-gated work until a focus/reconnect event happens.
 * Identity transitions must invalidate that cache immediately.
 */
export async function rafraichirSessionGlobale(): Promise<void> {
  await mutate(CLE_SESSION_SWR)
}
