/**
 * Comparaison d'un groupe — logique d'affichage, mesurée hors navigateur.
 *
 * Ce que ces tests protègent, ce sont les endroits où un écran peut mentir
 * sans que personne ne le voie : un écart de zéro pris pour une absence, une
 * valeur non lue affichée en zéro, une ligne masquée alors qu'elle portait
 * justement l'information.
 */

import assert from "node:assert/strict"
import { test } from "node:test"

import {
  MAX_FICHES_GROUPE,
  classeEcart,
  formaterNombre,
  formaterSigne,
  ligneADesEcart,
  ligneEstMesuree,
  nomFiche,
  nomModele,
  type LigneComparaison,
} from "./comparaison-groupe.ts"

function ligne(remplacements: Partial<LigneComparaison> = {}): LigneComparaison {
  return {
    champ: "slu_m",
    libelle: "SLU (guindant)",
    numerique: true,
    unite: "m",
    valeurs: [10, 10.2, null],
    etats: ["reference", "present", "manquant"],
    nb_fiches_avec_valeur: 2,
    identique: null,
    deltas: [null, 0.2, null],
    deltas_cm: [null, 20, null],
    deltas_pct: [null, 2, null],
    ecart_max_pct: 2,
    ...remplacements,
  }
}

test("une valeur absente affiche un tiret, jamais un zéro", () => {
  assert.equal(formaterNombre(null), "—")
  assert.equal(formaterNombre(undefined), "—")
  assert.equal(formaterSigne(null), "—")
  assert.equal(formaterNombre(0), "0,00")
})

test("un écart porte son signe", () => {
  assert.equal(formaterSigne(2), "+2,0")
  assert.equal(formaterSigne(-2), "-2,0")
  assert.equal(formaterSigne(0), "0,0")
})

test("les nuances d'écart suivent les seuils annoncés à l'écran", () => {
  assert.match(classeEcart(0.5), /emerald/)
  assert.equal(classeEcart(3), "badge-attention")
  assert.match(classeEcart(12), /red/)
  assert.match(classeEcart(null), /muted/)
})

test("un écart de zéro est masqué, mais il est MESURÉ et sera annoncé", () => {
  // Trois voiles identiques au centième : la ligne n'apprend rien, on la
  // masque — mais on ne la fait pas disparaître en silence. C'est tout
  // l'objet de « ligneEstMesuree » : l'écran doit annoncer « N attribut(s)
  // identique(s) masqué(s) », sinon une table vide laisserait croire qu'on
  // n'a rien comparé.
  const identiques = ligne({
    deltas_pct: [null, 0, 0],
    ecart_max_pct: 0,
    valeurs: [10, 10, 10],
    nb_fiches_avec_valeur: 3,
  })
  assert.equal(ligneADesEcart(identiques), false)
  assert.equal(ligneEstMesuree(identiques), true)
})

test("une cote jamais lue n'est ni un écart ni une mesure : rien à annoncer", () => {
  const vide = ligne({ valeurs: [null, null], deltas_pct: [null, null], ecart_max_pct: null, nb_fiches_avec_valeur: 0 })
  assert.equal(ligneADesEcart(vide), false)
  assert.equal(ligneEstMesuree(vide), false)
})

test("une seule valeur lue ne permet ni écart ni mesure", () => {
  const seule = ligne({ valeurs: [10, null], deltas_pct: [null, null], ecart_max_pct: null, nb_fiches_avec_valeur: 1 })
  assert.equal(ligneADesEcart(seule), false)
  assert.equal(ligneEstMesuree(seule), false)
})

test("un écart réel rend la ligne visible", () => {
  assert.equal(ligneADesEcart(ligne()), true)
})

test("une ligne de texte ne se masque que si elle est sûrement identique", () => {
  assert.equal(ligneADesEcart(ligne({ numerique: false, identique: true })), false)
  assert.equal(ligneADesEcart(ligne({ numerique: false, identique: false })), true)
  // « Je ne sais pas » n'est pas « c'est identique » : on garde la ligne.
  assert.equal(ligneADesEcart(ligne({ numerique: false, identique: null })), false)
  assert.equal(ligneEstMesuree(ligne({ numerique: false, identique: null })), false)
})

test("le nom d'une colonne retombe sur l'identifiant sans code fiche", () => {
  assert.equal(nomFiche({ id_extraction: 7, code_fiche: null, type_voile: null, modele_bateau_brut: null, gamme: null }), "#7")
  assert.equal(
    nomFiche({ id_extraction: 7, code_fiche: "ST101", type_voile: null, modele_bateau_brut: null, gamme: null }),
    "ST101",
  )
})

test("le modèle affiché préfère le nom canonique résolu au libellé brut", () => {
  const brut = { id_extraction: 1, code_fiche: null, type_voile: null, modele_bateau_brut: "Bateau lu", gamme: null }
  assert.equal(nomModele(brut), "Bateau lu")
  assert.equal(nomModele({ ...brut, modele_lie: "Modele canonique" }), "Modele canonique")
  assert.equal(nomModele({ ...brut, modele_lie: { nom_canonique: "Objet canonique" } }), "Objet canonique")
  assert.equal(
    nomModele({ id_extraction: 1, code_fiche: null, type_voile: null, modele_bateau_brut: null, gamme: null }),
    "bateau non lu",
  )
})

test("la borne de douze fiches est celle annoncée à l'écran", () => {
  assert.equal(MAX_FICHES_GROUPE, 12)
})
