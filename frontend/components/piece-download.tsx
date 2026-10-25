"use client"

import { useId, useState, type MouseEvent } from "react"
import Link from "next/link"
import useSWR from "swr"
import { CheckCircle2, Clock3, Download, Send } from "lucide-react"
import { chargerDemandes, creerDemande, type ListeDemandes } from "@/lib/approbations"
import type { PieceJointe } from "@/lib/fiche"
import {
  estApprobateur,
  peutDemander,
  ROLE_UTILISATEUR,
} from "@/lib/roles"
import { useSession } from "@/lib/use-session"
import { cn } from "@/lib/utils"

type PieceTelechargeable = Pick<PieceJointe, "id" | "name"> & { fiche_code?: string }

export function PieceDownload({
  piece,
  codeFiche,
  className,
  label,
  ariaLabel,
  stopPropagation = false,
}: {
  piece: PieceTelechargeable
  codeFiche?: string
  className?: string
  label?: string
  ariaLabel?: string
  stopPropagation?: boolean
}) {
  const { session, role, chargement } = useSession()
  const [formulaireOuvert, setFormulaireOuvert] = useState(false)
  const [motif, setMotif] = useState("")
  const [envoi, setEnvoi] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const idReact = useId().replace(/[^a-zA-Z0-9_-]/g, "")

  const code = codeFiche ?? piece.fiche_code
  const estUtilisateurNominatif = session.mode === "nominatif" && role === ROLE_UTILISATEUR
  const params = new URLSearchParams({ vue: "miennes", type_demande: "telechargement", limit: "100" })
  if (code) params.set("code_fiche", code)
  const cleDemandes = estUtilisateurNominatif ? `piece-download:${params.toString()}` : null
  const { data, mutate } = useSWR<ListeDemandes>(cleDemandes, () => chargerDemandes(params), {
    shouldRetryOnError: false,
  })

  const href = `/api/pieces/${encodeURIComponent(piece.id)}/telecharger`
  const nomAccessible = ariaLabel ?? label ?? `Télécharger ${piece.name}`

  function arreter(event: MouseEvent) {
    if (stopPropagation) event.stopPropagation()
  }

  if (chargement) {
    return (
      <span className={cn("inline-flex items-center gap-2 opacity-50", className)} aria-hidden="true">
        <Download className="size-3.5" />
        {label}
      </span>
    )
  }

  if (session.mode === "nominatif" && estApprobateur(role)) {
    return (
      <a
        href={href}
        onClick={arreter}
        className={className}
        aria-label={nomAccessible}
        title="Télécharger"
        data-testid={`piece-download-${piece.id}`}
      >
        <Download className={label ? "mr-2 inline size-4" : "size-3.5"} aria-hidden />
        {label}
      </a>
    )
  }

  if (!estUtilisateurNominatif || !peutDemander(role)) {
    // admin (including rescue) and lecture never receive a piece-download link.
    return null
  }

  const maintenant = Date.now()
  const demandesPiece = (data?.demandes ?? []).filter((demande) => demande.id_piece === piece.id)
  const approuvee = demandesPiece.find(
    (demande) =>
      demande.statut === "approuvee" &&
      demande.expire_le !== null &&
      new Date(demande.expire_le).getTime() > maintenant,
  )
  const enAttente = demandesPiece.find((demande) => demande.statut === "en_attente")

  if (approuvee) {
    return (
      <a
        href={href}
        onClick={arreter}
        className={className}
        aria-label={nomAccessible}
        title={`Téléchargement approuvé jusqu’au ${new Date(approuvee.expire_le ?? "").toLocaleString("fr-FR")}`}
        data-testid={`piece-download-${piece.id}`}
      >
        <Download className={label ? "mr-2 inline size-4" : "size-3.5"} aria-hidden />
        {label ?? "Télécharger (approuvé)"}
      </a>
    )
  }

  if (enAttente && !formulaireOuvert) {
    return (
      <Link
        href="/approvals?vue=miennes"
        onClick={arreter}
        className={cn("inline-flex items-center gap-1.5 text-amber-300", className)}
        aria-label={`Demande de téléchargement en attente pour ${piece.name}`}
        title="Demande en attente de décision"
        data-testid={`piece-download-pending-${piece.id}`}
      >
        <Clock3 className="size-3.5" aria-hidden />
        {label ?? "Demande en attente"}
      </Link>
    )
  }

  async function soumettre(event: React.FormEvent) {
    event.preventDefault()
    event.stopPropagation()
    setErreur(null)
    setMessage(null)
    if (motif.trim().length < 3) {
      setErreur("Expliquez le motif en au moins 3 caractères.")
      return
    }
    setEnvoi(true)
    try {
      await creerDemande({
        type_demande: "telechargement",
        motif: motif.trim(),
        id_piece: piece.id,
        ...(code ? { code_fiche: code } : {}),
      })
      setMessage("Demande envoyée à un approbateur.")
      setFormulaireOuvert(false)
      await mutate()
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "La demande n’a pas pu être créée.")
    } finally {
      setEnvoi(false)
    }
  }

  const idMotif = `motif-telechargement-${piece.id}-${idReact}`

  return (
    <span className="inline-flex max-w-full flex-col items-stretch gap-2" onClick={arreter}>
      <button
        type="button"
        onClick={(event) => {
          arreter(event)
          setFormulaireOuvert((ouvert) => !ouvert)
        }}
        className={className}
        aria-label={`Demander le téléchargement de ${piece.name}`}
        aria-expanded={formulaireOuvert}
        aria-controls={idMotif}
        title="Demander le téléchargement"
        data-testid={`piece-download-request-${piece.id}`}
      >
        <Download className={label ? "mr-2 inline size-4" : "size-3.5"} aria-hidden />
        {label ?? "Demander le téléchargement"}
      </button>

      {message && (
        <span className="inline-flex items-center gap-1 text-[10px] text-emerald-300" role="status">
          <CheckCircle2 className="size-3.5" aria-hidden /> {message}
        </span>
      )}
      {erreur && !formulaireOuvert && (
        <span className="text-[10px] text-destructive" role="alert">{erreur}</span>
      )}

      {formulaireOuvert && (
        <form
          id={idMotif}
          onSubmit={soumettre}
          className="w-72 max-w-[80vw] rounded border border-border bg-card p-3 text-left shadow-xl"
          data-testid={`piece-download-form-${piece.id}`}
        >
          <label htmlFor={`${idMotif}-champ`} className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Motif du téléchargement
          </label>
          <textarea
            id={`${idMotif}-champ`}
            value={motif}
            onChange={(event) => setMotif(event.target.value)}
            minLength={3}
            maxLength={1000}
            required
            rows={3}
            className="mt-1 w-full rounded border border-border bg-input px-2 py-1.5 text-xs outline-none focus:border-primary"
            placeholder="Pourquoi ce fichier est-il nécessaire ?"
          />
          {erreur && <p className="mt-1 text-[10px] text-destructive" role="alert">{erreur}</p>}
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setFormulaireOuvert(false)}
              className="rounded border border-border px-2 py-1 text-[10px]"
            >
              Annuler
            </button>
            <button
              type="submit"
              disabled={envoi || motif.trim().length < 3}
              className="inline-flex items-center gap-1 rounded bg-primary px-2 py-1 text-[10px] font-semibold text-primary-foreground disabled:opacity-50"
            >
              <Send className="size-3" aria-hidden /> {envoi ? "Envoi…" : "Envoyer la demande"}
            </button>
          </div>
        </form>
      )}
    </span>
  )
}
