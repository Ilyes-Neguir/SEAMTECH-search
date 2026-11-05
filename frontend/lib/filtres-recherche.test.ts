/**
 * Libellés des filtres actifs — ce que voit l'exploitante.
 *
 * Ces tests protègent une chose précise : une pastille de filtre ne doit
 * jamais afficher une clé à la place d'un libellé, et jamais inventer un nom
 * qu'elle n'a pas.
 */

import assert from "node:assert/strict"
import { test } from "node:test"

import { LIBELLES_VALEURS_FACETTES, libelleFiltreActif } from "./filtres-recherche.ts"

test("une valeur codée s'affiche avec son libellé humain", () => {
  assert.equal(libelleFiltreActif("categorie", "voile"), "Voiles")
  assert.equal(libelleFiltreActif("categorie", "accessoire"), "Accessoires")
})

test("un identifiant de modèle n'est jamais affiché comme un nombre nu", () => {
  // « 42 » ne veut rien dire pour personne : on écrit ce que c'est.
  assert.equal(libelleFiltreActif("boat_model_id", "42"), "modèle n° 42")
})

test("une valeur déjà lisible reste telle quelle", () => {
  assert.equal(libelleFiltreActif("gamme", "Ocean"), "Ocean")
  assert.equal(libelleFiltreActif("client", "Dupont"), "Dupont")
})

test("une valeur inconnue est rendue telle quelle, jamais vide", () => {
  assert.equal(libelleFiltreActif("categorie", "inconnu"), "inconnu")
  assert.equal(libelleFiltreActif("type_voile", "SPI"), "SPI")
})

test("un identifiant de modèle non numérique est laissé tel quel", () => {
  assert.equal(libelleFiltreActif("boat_model_id", "First 18"), "First 18")
})

test("la table des libellés ne traduit que des valeurs réellement codées", () => {
  for (const [cle, traductions] of Object.entries(LIBELLES_VALEURS_FACETTES)) {
    assert.ok(cle.length > 0, "une clé de facette vide ne veut rien dire")
    for (const [valeur, libelle] of Object.entries(traductions)) {
      assert.ok(valeur.length > 0, `valeur vide pour la facette « ${cle} »`)
      assert.ok(libelle.length > 0, `libellé vide pour « ${cle}.${valeur} »`)
    }
  }
})
