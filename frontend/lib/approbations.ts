"use client"

import { authedFetch } from "@/lib/authed-fetch"

export type TypeDemande = "telechargement" | "modification" | "suppression"

export type StatutDemande =
  | "en_attente"
  | "approuvee"
  | "refusee"
  | "annulee"
  | "executee"
  | "expiree"

export type ModificationDemandee = {
  champ: string
  rang?: number | null
  valeur: string
  revision: number
}

export type DemandeApprobation = {
  id_demande: number
  type_demande: TypeDemande
  statut: StatutDemande
  motif: string
  id_demandeur: number
  demandeur: string
  nom_demandeur: string | null
  id_fiche: number | null
  code_fiche: string | null
  statut_fiche: string | null
  revision_fiche_actuelle: number | null
  id_piece: number | null
  nom_piece: string | null
  champ: string | null
  rang: number | null
  valeur_proposee: string | null
  revision_fiche: number | null
  modification?: ModificationDemandee
  id_decideur: number | null
  decideur: string | null
  decidee_le: string | null
  commentaire_decision: string | null
  executee_le: string | null
  expire_le: string | null
  resultat_execution: Record<string, unknown>
  cree_le: string | null
  maj_le: string | null
}

export type ListeDemandes = {
  demandes: DemandeApprobation[]
  total: number
  limit: number
  offset: number
  vue: string
}

export type NotificationInApp = {
  id_notification: number
  id_destinataire: number
  id_demande: number | null
  type_notification: string
  titre: string
  message: string
  lue: boolean
  lue_le: string | null
  cree_le: string | null
  type_demande: TypeDemande | null
  statut_demande: StatutDemande | null
  code_fiche: string | null
}

export type ListeNotifications = {
  notifications: NotificationInApp[]
  non_lues: number
  limit: number
  offset: number
}

export type CorpsCreationDemande = {
  type_demande: TypeDemande
  motif: string
  code_fiche?: string
  id_piece?: number
  modification?: ModificationDemandee
  revision?: number
}

export type CorpsDecision = {
  decision: "approuver" | "refuser"
  commentaire?: string
  revision_confirmee?: number
}

async function appelJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await authedFetch(url, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  })
  const corps = (await response.json().catch(() => ({}))) as Record<string, unknown>
  if (!response.ok) {
    const detail = typeof corps.detail === "string" ? corps.detail : `HTTP ${response.status}`
    throw Object.assign(new Error(detail), { status: response.status, body: corps })
  }
  return corps as T
}

export async function chargerDemandes(params: URLSearchParams): Promise<ListeDemandes> {
  const query = params.toString()
  return appelJson<ListeDemandes>(`/api/approvals${query ? `?${query}` : ""}`)
}

export async function chargerDemande(id: number): Promise<DemandeApprobation> {
  return appelJson<DemandeApprobation>(`/api/approvals/${id}`)
}

export async function creerDemande(corps: CorpsCreationDemande): Promise<DemandeApprobation> {
  return appelJson<DemandeApprobation>("/api/approvals", {
    method: "POST",
    body: JSON.stringify(corps),
  })
}

export async function deciderDemande(id: number, corps: CorpsDecision): Promise<DemandeApprobation> {
  return appelJson<DemandeApprobation>(`/api/approvals/${id}/decide`, {
    method: "POST",
    body: JSON.stringify(corps),
  })
}

export async function annulerDemande(id: number): Promise<DemandeApprobation> {
  return appelJson<DemandeApprobation>(`/api/approvals/${id}/annuler`, {
    method: "POST",
    body: JSON.stringify({}),
  })
}

export async function chargerNotifications(params = new URLSearchParams()): Promise<ListeNotifications> {
  const query = params.toString()
  return appelJson<ListeNotifications>(`/api/notifications${query ? `?${query}` : ""}`)
}

export async function marquerNotificationLue(id: number): Promise<NotificationInApp> {
  return appelJson<NotificationInApp>(`/api/notifications/${id}/lu`, {
    method: "POST",
    body: JSON.stringify({}),
  })
}

export async function marquerNotificationsLues(): Promise<{ ok: boolean; marquees_lues: number; non_lues: number }> {
  return appelJson("/api/notifications/tout-lire", {
    method: "POST",
    body: JSON.stringify({}),
  })
}

export function typeDemandeLibelle(type: TypeDemande | string | null | undefined): string {
  switch (type) {
    case "telechargement":
      return "Téléchargement"
    case "modification":
      return "Modification"
    case "suppression":
      return "Suppression logique"
    default:
      return String(type ?? "Demande")
  }
}

export function statutDemandeLibelle(statut: StatutDemande | string | null | undefined): string {
  switch (statut) {
    case "en_attente":
      return "En attente"
    case "approuvee":
      return "Approuvée"
    case "refusee":
      return "Refusée"
    case "annulee":
      return "Annulée"
    case "executee":
      return "Exécutée"
    case "expiree":
      return "Expirée"
    default:
      return String(statut ?? "Inconnu")
  }
}

export function dateLisible(valeur: string | null | undefined): string {
  if (!valeur) return "—"
  const date = new Date(valeur)
  if (Number.isNaN(date.getTime())) return valeur
  return date.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })
}
