"use client"

// Écran NOUVEAU DOSSIER (lot D) : dépôt d'un dossier complet (porte A, lot C)
// et suivi du lot (progression, raisons d'échec, fichiers restants — GET
// /lots/{id}). Un refus est un RÉSULTAT affiché avec sa raison, jamais un
// crash silencieux.

import { useCallback, useEffect, useRef, useState } from "react"
import { FolderInput, RefreshCw, Upload, XCircle } from "lucide-react"
import { AccesRefuse } from "@/components/acces-refuse"
import { authedFetch } from "@/lib/authed-fetch"
import { peutDeposer } from "@/lib/roles"
import { useSession } from "@/lib/use-session"
import type { LotDetail } from "@/lib/fiche"
import {
  annulerTeleversement,
  creerLotDepuisTeleversement,
  fichiersDepuisDepot,
  fichiersDepuisListe,
  formaterOctets,
  preparerFichiers,
  televerserEtCreerLot,
  type FichierATeleverser,
  type PreparationTeleversement,
  type ProgressionTeleversement,
} from "@/lib/televersement-lot"
import { cn } from "@/lib/utils"

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authedFetch(url, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw Object.assign(new Error(String(body?.detail ?? `HTTP ${res.status}`)), { status: res.status })
  return body as T
}

/** Message d'échec de transfert lisible par l'opérateur (MAN-05).
 *
 * Un échec réseau du navigateur arrive en ``TypeError`` au libellé anglais
 * dépendant du navigateur (« Failed to fetch ») : l'écran ne doit jamais
 * l'afficher tel quel. Les refus métier (Error avec detail serveur) sont,
 * eux, déjà rédigés pour l'opérateur et passent inchangés. */
function messageErreurTeleversement(e: unknown): string {
  if (e instanceof TypeError) {
    return "Connexion au serveur impossible ou interrompue pendant le transfert. Vérifiez le réseau, puis réessayez."
  }
  return e instanceof Error ? e.message : "Téléversement impossible."
}

//: Statuts de lot pour lesquels on arrête d'interroger le serveur. « interrompu »
//: et « relance » sont des états où un worker peut encore reprendre le lot : on
//: continue de suivre tant que le lot n'est pas terminal.
const STATUTS_LOT_TERMINAUX = new Set(["termine", "annule"])
//: Statuts de lot que l'écran sait nommer. Un statut inconnu est AFFICHÉ tel
//: quel (jamais masqué) : c'est une information, pas une erreur de format.
const LIBELLES_LOT: Record<string, string> = {
  en_cours: "en cours",
  termine: "terminé",
  interrompu: "interrompu",
  annule: "annulé",
  relance: "relancé",
}

const LIBELLES_PHASE_TELEVERSEMENT: Record<ProgressionTeleversement["phase"], string> = {
  analyse: "Lecture récursive du dossier…",
  creation_session: "Ouverture de la session d'envoi…",
  transfert: "Envoi des fichiers par paquets…",
  finalisation: "Contrôle du compte et de la taille reçus…",
  creation_lot: "Création du lot d'ingestion…",
  termine: "Envoi terminé — traitement serveur en cours de suivi",
}

function pourcentageTeleversement(progression: ProgressionTeleversement | null): number {
  if (!progression) return 0
  if (progression.phase === "termine") return 100
  if (progression.octetsTotal > 0) {
    return Math.min(100, Math.round((progression.octetsRecus / progression.octetsTotal) * 100))
  }
  return progression.fichiersTotal > 0
    ? Math.min(100, Math.round((progression.fichiersRecus / progression.fichiersTotal) * 100))
    : 0
}

function nombreDossiersAvecPdfDirect(preparation: PreparationTeleversement | null): number {
  if (!preparation) return 0
  const dossiers = new Set<string>()
  for (const item of preparation.fichiers) {
    if (!item.rel.toLocaleLowerCase("fr-FR").endsWith(".pdf")) continue
    const parent = item.rel.includes("/") ? item.rel.slice(0, item.rel.lastIndexOf("/")) : "."
    dossiers.add(parent)
  }
  return dossiers.size
}

function NouveauAppInterne() {
  const [dossier, setDossier] = useState("")
  const [resultat, setResultat] = useState<{ statut: string; fiche: string | null; raison: string | null; pieces: number } | null>(null)
  const [idLot, setIdLot] = useState<number | null>(null)
  const [lot, setLot] = useState<LotDetail | null>(null)
  const [occupe, setOccupe] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)
  const [erreurSuivi, setErreurSuivi] = useState<string | null>(null)
  // Jeton de génération : une réponse de lot arrivée APRÈS que l'opérateur a
  // quitté le suivi (autre dépôt, démontage) ne doit pas réécrire l'écran. Le
  // minuteur de rafraîchissement est lui aussi identifié par ce jeton — sans
  // quoi un lot abandonné continuait d'interroger le serveur indéfiniment.
  const generation = useRef(0)
  const [jetonSuivi, setJetonSuivi] = useState(0)
  const [dragActif, setDragActif] = useState(false)
  const [analyseDossier, setAnalyseDossier] = useState(false)
  const [preparation, setPreparation] = useState<PreparationTeleversement | null>(null)
  const [progression, setProgression] = useState<ProgressionTeleversement | null>(null)
  const [jetonTeleversement, setJetonTeleversement] = useState<string | null>(null)
  const [transfertEnCours, setTransfertEnCours] = useState(false)
  const [creationLotReprisePossible, setCreationLotReprisePossible] = useState(false)
  const [messageTeleversement, setMessageTeleversement] = useState<string | null>(null)
  const controleurTransfert = useRef<AbortController | null>(null)
  const annulationDemandee = useRef(false)
  const jetonTeleversementRef = useRef<string | null>(null)
  const phaseTransfertRef = useRef<ProgressionTeleversement["phase"]>("analyse")
  const profondeurDrag = useRef(0)
  const inputDossierLot = useRef<HTMLInputElement>(null)
  const inputFichiersLot = useRef<HTMLInputElement>(null)

  const chargerLot = useCallback(async (id: number, jeton: number) => {
    try {
      const detail = await jsonFetch<LotDetail>(`/api/lots/${id}`)
      if (generation.current !== jeton) return // suivi abandonné : ne rien écrire
      setLot(detail)
      setErreurSuivi(null)
      if (!STATUTS_LOT_TERMINAUX.has(detail.statut)) {
        setTimeout(() => {
          if (generation.current === jeton) void chargerLot(id, jeton)
        }, 1000)
      }
    } catch (e) {
      if (generation.current !== jeton) return
      // Plus jamais d'échec MUET (défaut A08) : l'écran dit que le suivi est
      // indisponible, avec la raison — l'opérateur sait qu'il ne voit rien
      // plutôt que de croire qu'il n'y a rien à voir.
      setErreurSuivi(
        e instanceof Error
          ? `Suivi du lot #${id} indisponible : ${e.message}`
          : `Suivi du lot #${id} indisponible.`,
      )
    }
  }, [])

  useEffect(() => {
    if (idLot == null) {
      setLot(null)
      setErreurSuivi(null)
      return
    }
    const jeton = ++generation.current
    setJetonSuivi(jeton)
    setErreurSuivi(null)
    void chargerLot(idLot, jeton)
    // Démontage / changement de lot : invalide toute réponse ET tout minuteur
    // en vol. Sans ce nettoyage, un lot abandonné continuaient d'actualiser
    // l'écran (et d'interroger le serveur) après le départ de l'opérateur.
    return () => {
      generation.current += 1
    }
  }, [idLot, chargerLot])

  useEffect(() => {
    return () => {
      controleurTransfert.current?.abort()
    }
  }, [])

  function selectionnerFichiers(bruts: FichierATeleverser[]) {
    if (transfertEnCours) return
    setErreur(null)
    setMessageTeleversement(null)
    setResultat(null)
    setCreationLotReprisePossible(false)
    jetonTeleversementRef.current = null
    setJetonTeleversement(null)
    try {
      const prep = preparerFichiers(bruts)
      setPreparation(prep)
      phaseTransfertRef.current = "analyse"
      setProgression({
        phase: "analyse",
        fichiersRecus: 0,
        fichiersTotal: prep.fichiers.length,
        octetsRecus: 0,
        octetsTotal: prep.tailleTotale,
        envoi: 0,
        envoisTotal: 0,
        tentative: 1,
      })
    } catch (e) {
      setPreparation(null)
      setProgression(null)
      setErreur(e instanceof Error ? e.message : "Dossier illisible.")
    }
  }

  async function lancerTeleversement() {
    if (!preparation || transfertEnCours) return
    const controleur = new AbortController()
    controleurTransfert.current = controleur
    annulationDemandee.current = false
    setTransfertEnCours(true)
    setErreur(null)
    setMessageTeleversement(null)
    setResultat(null)
    setCreationLotReprisePossible(false)
    generation.current += 1
    setIdLot(null)
    setLot(null)
    setErreurSuivi(null)
    try {
      const resultatTeleversement = await televerserEtCreerLot(preparation, {
        signal: controleur.signal,
        onSession: (jeton) => {
          jetonTeleversementRef.current = jeton
          setJetonTeleversement(jeton)
        },
        onProgression: (maj) => {
          phaseTransfertRef.current = maj.phase
          setProgression(maj)
        },
      })
      setMessageTeleversement(
        `Dossier reçu intégralement : ${resultatTeleversement.session.nombre_fichiers_recus} fichier(s). ` +
          `Le lot #${resultatTeleversement.lot.id_lot} est maintenant traité côté serveur.`,
      )
      setIdLot(resultatTeleversement.lot.id_lot)
    } catch (e) {
      if (annulationDemandee.current) {
        const jeton = jetonTeleversementRef.current
        try {
          if (jeton) await annulerTeleversement(jeton)
          setMessageTeleversement("Téléversement annulé : les fichiers déjà reçus pour cette session ont été supprimés.")
        } catch (erreurAnnulation) {
          setErreur(
            erreurAnnulation instanceof Error
              ? `Transfert arrêté, mais suppression de la session impossible : ${erreurAnnulation.message}`
              : "Transfert arrêté, mais suppression de la session impossible.",
          )
        } finally {
          jetonTeleversementRef.current = null
          setJetonTeleversement(null)
          setPreparation(null)
          setProgression(null)
        }
      } else {
        setErreur(messageErreurTeleversement(e))
        setCreationLotReprisePossible(
          Boolean(jetonTeleversementRef.current) &&
            (phaseTransfertRef.current === "finalisation" || phaseTransfertRef.current === "creation_lot"),
        )
      }
    } finally {
      annulationDemandee.current = false
      controleurTransfert.current = null
      setTransfertEnCours(false)
    }
  }

  async function reprendreCreationLot() {
    const jeton = jetonTeleversementRef.current ?? jetonTeleversement
    if (!jeton || transfertEnCours) return
    setTransfertEnCours(true)
    setErreur(null)
    try {
      const lotCree = await creerLotDepuisTeleversement(jeton)
      setCreationLotReprisePossible(false)
      setMessageTeleversement(`Création reprise : le lot #${lotCree.id_lot} est suivi ci-dessous.`)
      setIdLot(lotCree.id_lot)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Création du lot impossible.")
    } finally {
      setTransfertEnCours(false)
    }
  }

  async function demanderAnnulationTeleversement() {
    if (transfertEnCours) {
      annulationDemandee.current = true
      controleurTransfert.current?.abort()
      return
    }
    const jeton = jetonTeleversementRef.current ?? jetonTeleversement
    setErreur(null)
    try {
      if (jeton) await annulerTeleversement(jeton)
      setMessageTeleversement(
        jeton
          ? "Session de téléversement annulée et supprimée."
          : "Sélection retirée. Aucun fichier n'avait encore été envoyé.",
      )
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Annulation impossible.")
      return
    }
    jetonTeleversementRef.current = null
    setJetonTeleversement(null)
    setCreationLotReprisePossible(false)
    setPreparation(null)
    setProgression(null)
    if (inputDossierLot.current) inputDossierLot.current.value = ""
    if (inputFichiersLot.current) inputFichiersLot.current.value = ""
  }

  async function deposer() {
    setOccupe(true)
    setErreur(null)
    setResultat(null)
    // Nouveau dépôt : l'ancien suivi et son minuteur sont abandonnés.
    generation.current += 1
    setIdLot(null)
    setLot(null)
    setErreurSuivi(null)
    try {
      const corps = await jsonFetch<{ statut: string; fiche: string | null; raison: string | null; pieces: number; id_lot: number | null; lot?: { id_lot: number } }>(
        "/api/imports/dossier",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dossier }),
        },
      )
      setResultat({ statut: corps.statut, fiche: corps.fiche, raison: corps.raison, pieces: corps.pieces })
      const lotId = corps.id_lot ?? corps.lot?.id_lot
      if (lotId) setIdLot(lotId)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Dépôt impossible.")
    } finally {
      setOccupe(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6" data-testid="nouveau-app">
      <div>
        <h1 className="flex items-center gap-2 text-lg font-bold">
          <FolderInput className="size-5" /> Déposer un dossier complet
        </h1>
        <p className="mt-1 text-xs text-muted-foreground">
          Le dossier de fabrication (fiche PDF + croquis, plans, photos). La fiche est reconnue et extraite ; les autres
          fichiers sont rattachés comme pièces jointes (non analysés, RG12). La fiche arrive en <em>a_valider</em> —
          jamais valide sans décision humaine (RG3). Idempotent : rejouer un dossier ne crée rien.
        </p>
      </div>
      <section className="space-y-3 rounded border border-primary/30 bg-primary/5 p-4" data-testid="panneau-televersement-lot">
        <div>
          <h2 className="text-sm font-semibold">Importer un dossier complet (recommandé)</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Glissez ici une arborescence entière — par exemple une année complète. Le navigateur lit récursivement
            tous les sous-dossiers, envoie les fichiers par paquets bornés, puis le serveur découvre chaque dossier
            contenant un PDF et crée un seul lot. Aucune copie PowerShell et aucun dossier à traiter un par un.
          </p>
        </div>
        <div
          data-testid="zone-depot-lot"
          className={cn(
            "rounded border border-dashed px-4 py-6 text-center transition-colors",
            dragActif ? "border-primary bg-primary/10" : "border-border bg-background/70",
          )}
          onDragEnter={(e) => {
            e.preventDefault()
            profondeurDrag.current += 1
            setDragActif(true)
          }}
          onDragOver={(e) => {
            e.preventDefault()
            setDragActif(true)
          }}
          onDragLeave={(e) => {
            e.preventDefault()
            profondeurDrag.current = Math.max(0, profondeurDrag.current - 1)
            if (profondeurDrag.current === 0) setDragActif(false)
          }}
          onDrop={(e) => {
            e.preventDefault()
            profondeurDrag.current = 0
            setDragActif(false)
            if (transfertEnCours) return
            setAnalyseDossier(true)
            void fichiersDepuisDepot(e.dataTransfer)
              .then(selectionnerFichiers)
              .catch((erreurDepot) => {
                setErreur(erreurDepot instanceof Error ? erreurDepot.message : "Lecture du dossier déposé impossible.")
              })
              .finally(() => setAnalyseDossier(false))
          }}
        >
          <Upload className="mx-auto size-7 text-primary" aria-hidden="true" />
          <p className="mt-2 text-sm font-medium">Glissez-déposez le dossier complet ici</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Tous les niveaux sont parcourus. Les fichiers cachés/système sont comptés puis ignorés.
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            <button
              type="button"
              onClick={() => inputDossierLot.current?.click()}
              disabled={transfertEnCours || analyseDossier}
              className="rounded bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-40"
              data-testid="bouton-choisir-dossier-lot"
            >
              Choisir un dossier
            </button>
            <button
              type="button"
              onClick={() => inputFichiersLot.current?.click()}
              disabled={transfertEnCours || analyseDossier}
              className="rounded border border-border bg-background px-4 py-2 text-sm font-medium disabled:opacity-40"
              data-testid="bouton-choisir-fichiers-lot"
            >
              Choisir des fichiers
            </button>
          </div>
        </div>
        <input
          ref={inputDossierLot}
          type="file"
          multiple
          className="hidden"
          data-testid="input-dossier-lot"
          {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
          onChange={(e) => {
            selectionnerFichiers(fichiersDepuisListe(Array.from(e.target.files ?? [])))
            e.target.value = ""
          }}
        />
        <input
          ref={inputFichiersLot}
          type="file"
          multiple
          className="hidden"
          data-testid="input-fichiers-lot"
          onChange={(e) => {
            selectionnerFichiers(fichiersDepuisListe(Array.from(e.target.files ?? [])))
            e.target.value = ""
          }}
        />
        {analyseDossier && (
          <p className="text-xs text-muted-foreground" data-testid="analyse-dossier-televersement">
            Lecture récursive du dossier dans le navigateur…
          </p>
        )}
        {preparation && (
          <div className="rounded border border-border bg-background p-3 text-sm" data-testid="resume-televersement">
            <p className="font-medium">
              {preparation.nomRacine} — {preparation.fichiers.length} fichier(s), {formaterOctets(preparation.tailleTotale)}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {nombreDossiersAvecPdfDirect(preparation)} dossier(s) contenant au moins un PDF direct seront examinés
              récursivement côté serveur.
              {preparation.ignores > 0 && <> {preparation.ignores} fichier(s) caché(s)/système ignoré(s).</>}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void lancerTeleversement()}
                disabled={transfertEnCours || progression?.phase === "termine"}
                className="rounded bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-40"
                data-testid="bouton-televerser-lot"
              >
                {transfertEnCours ? "Envoi en cours…" : "Envoyer et créer le lot"}
              </button>
              {creationLotReprisePossible && jetonTeleversement && (
                <button
                  type="button"
                  onClick={() => void reprendreCreationLot()}
                  disabled={transfertEnCours}
                  className="rounded border border-primary bg-background px-4 py-2 text-sm font-medium text-primary disabled:opacity-40"
                  data-testid="bouton-reprendre-creation-lot"
                >
                  Reprendre la création du lot
                </button>
              )}
              <button
                type="button"
                onClick={() => void demanderAnnulationTeleversement()}
                className="inline-flex items-center gap-1 rounded border border-destructive/50 bg-background px-4 py-2 text-sm font-medium text-destructive"
                data-testid={transfertEnCours ? "bouton-annuler-televersement" : "bouton-retirer-televersement"}
              >
                <XCircle className="size-4" aria-hidden="true" />
                {transfertEnCours ? "Annuler le transfert" : jetonTeleversement ? "Annuler la session" : "Retirer"}
              </button>
            </div>
          </div>
        )}
        {progression && preparation && (
          <div className="space-y-1" data-testid="progression-televersement">
            <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
              <span className="font-medium">{LIBELLES_PHASE_TELEVERSEMENT[progression.phase]}</span>
              <span className="font-mono text-muted-foreground">
                {progression.fichiersRecus}/{progression.fichiersTotal} fichiers — {formaterOctets(progression.octetsRecus)} / {formaterOctets(progression.octetsTotal)}
                {progression.envoisTotal > 0 && <> — envoi {progression.envoi}/{progression.envoisTotal}</>}
                {progression.tentative > 1 && <> — tentative {progression.tentative}</>}
              </span>
            </div>
            <div
              className="h-2 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-label="Progression du téléversement"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pourcentageTeleversement(progression)}
            >
              <div
                className="h-full bg-primary transition-all duration-300"
                style={{ width: `${pourcentageTeleversement(progression)}%` }}
              />
            </div>
          </div>
        )}
        {messageTeleversement && (
          <p className="text-sm text-success" data-testid="message-televersement" role="status">
            {messageTeleversement}
          </p>
        )}
      </section>
      <div>
        <h2 className="text-sm font-semibold">Mode avancé — dossier déjà présent sur le serveur</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Ce mode reste disponible pour un dossier d’affaire unique déjà accessible au backend.
        </p>
      </div>
      <div className="flex gap-2">
        <input
          value={dossier}
          onChange={(e) => setDossier(e.target.value)}
          placeholder="/archive/CLIENT-7792-SO"
          className="flex-1 rounded border border-border bg-input px-3 py-2 font-mono text-sm"
          data-testid="champ-dossier"
        />
        <button
          type="button"
          onClick={deposer}
          disabled={!dossier.trim() || occupe}
          className="rounded bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-40"
          data-testid="bouton-deposer"
        >
          Déposer
        </button>
      </div>
      {erreur && <p className="text-sm text-destructive" data-testid="erreur-depot">{erreur}</p>}
      {resultat && (
        <div
          className={cn(
            "rounded border p-4 text-sm",
            resultat.statut === "traite" ? "border-emerald-500/30 bg-emerald-500/10" : "border-amber-500/30 bg-amber-500/10",
          )}
          data-testid="resultat-depot"
        >
          <p className="font-semibold">
            {resultat.statut === "traite" ? "Dossier traité" : `Dossier ${resultat.statut}`}{" "}
            {resultat.fiche && <>— fiche {resultat.fiche}</>} {resultat.pieces > 0 && <>— {resultat.pieces} pièce(s) jointe(s)</>}
          </p>
          {resultat.raison && <p className="mt-1 text-xs text-muted-foreground">Raison : {resultat.raison}</p>}
          {resultat.fiche && (
            <a href={`/dossier/${encodeURIComponent(resultat.fiche)}`} className="mt-2 inline-block text-xs text-primary underline">
              Ouvrir la fiche →
            </a>
          )}
        </div>
      )}
      {erreurSuivi && (
        <p role="alert" className="text-sm text-destructive" data-testid="erreur-suivi">
          {erreurSuivi}
        </p>
      )}
      {idLot != null && !lot && !erreurSuivi && (
        <p className="text-xs text-muted-foreground" data-testid="suivi-en-chargement">
          Chargement du suivi du lot #{idLot}…
        </p>
      )}
      {lot && (
        <div className="rounded border border-border p-4" data-testid="suivi-lot">
          <h2 className="flex items-center justify-between text-sm font-semibold">
            <span>
              Lot #{lot.id_lot} — {LIBELLES_LOT[lot.statut] ?? lot.statut}
            </span>
            <button type="button" onClick={() => void chargerLot(lot.id_lot, jetonSuivi)} aria-label="Rafraîchir le lot" className="text-muted-foreground hover:text-foreground">
              <RefreshCw className="size-4" />
            </button>
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {lot.nb_traites + lot.nb_echecs} / {lot.nb_dossiers} dossier(s) — {lot.progression_pct ?? 0} %
          </p>
          <ul className="mt-2 space-y-1 text-xs">
            {lot.dossiers.map((ligne) => (
              <li key={ligne.chemin_dossier} className="flex items-center justify-between gap-2" data-testid="ligne-lot">
                <span className="truncate font-mono">{ligne.chemin_relatif ?? ligne.chemin_dossier}</span>
                <span className={ligne.statut === "traite" ? "text-success" : "texte-attention"}>
                  {ligne.statut}
                  {ligne.raison ? ` — ${ligne.raison}` : ""}
                </span>
              </li>
            ))}
            {lot.restants.length > 0 && <li className="text-muted-foreground">{lot.restants.length} fichier(s) restant(s)</li>}
          </ul>
        </div>
      )}
    </div>
  )
}


export function NouveauApp() {
  const { role, chargement } = useSession()
  if (chargement) return <div className="p-6 text-sm text-muted-foreground">Vérification de votre rôle…</div>
  if (!peutDeposer(role)) {
    return <AccesRefuse message="Le dépôt de dossiers est réservé aux rôles admin, utilisateur et approbateur. Le rôle lecture peut consulter et prévisualiser l’archive, sans y déposer de fichiers." />
  }
  return <NouveauAppInterne />
}
