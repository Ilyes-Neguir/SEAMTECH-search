import assert from "node:assert/strict"
import { test, beforeEach } from "node:test"

import {
  IDENTIFIANT_SECOURS,
  cleTentative,
  clearFailedAttempts,
  lockoutRemaining,
  lockoutSecoursGlobal,
  noteEchecSecoursGlobal,
  noteFailedAttempt,
  resetFailedAttempts,
} from "./throttling.ts"

beforeEach(() => {
  resetFailedAttempts()
})

test("cleTentative sépare le secours des comptes nominatifs", () => {
  assert.equal(cleTentative(IDENTIFIANT_SECOURS, "1.2.3.4"), "secours:1.2.3.4")
  assert.equal(cleTentative("imrane", "1.2.3.4"), "1.2.3.4")
})

test("une connexion nominative réussie ne remet pas à zéro le compteur du secours", () => {
  const ip = "1.2.3.4"
  const cleSecours = cleTentative(IDENTIFIANT_SECOURS, ip)
  const cleNominatif = cleTentative("imrane", ip)
  assert.notEqual(cleSecours, cleNominatif)

  // 4 échecs sur le secours, puis une connexion nominative réussie
  // (qui efface le compteur nominatif, pas celui du secours).
  for (let i = 0; i < 4; i++) noteFailedAttempt(cleSecours)
  clearFailedAttempts(cleNominatif)

  // Le 5e échec sur le secours verrouille malgré la connexion nominative.
  noteFailedAttempt(cleSecours)
  assert.ok(lockoutRemaining(cleSecours) > 0, "le secours doit être verrouillé")
  assert.equal(lockoutRemaining(cleNominatif), 0, "le nominatif ne doit pas être verrouillé")
})

test("clearFailedAttempts sur la clé secours déverrouille", () => {
  const cle = cleTentative(IDENTIFIANT_SECOURS, "5.6.7.8")
  for (let i = 0; i < 5; i++) noteFailedAttempt(cle)
  assert.ok(lockoutRemaining(cle) > 0)
  clearFailedAttempts(cle)
  assert.equal(lockoutRemaining(cle), 0)
})

test("plafond global du secours : 30 échecs verrouillent toutes les IP", () => {
  for (let i = 0; i < 30; i++) noteEchecSecoursGlobal()
  assert.ok(lockoutSecoursGlobal() > 0, "le plafond global doit verrouiller")
})

test("le plafond global n'est pas effacé par clearFailedAttempts (par IP)", () => {
  for (let i = 0; i < 30; i++) noteEchecSecoursGlobal()
  assert.ok(lockoutSecoursGlobal() > 0)
  // Un succès sur une IP (clearFailedAttempts) ne déverrouille pas le global.
  clearFailedAttempts(cleTentative(IDENTIFIANT_SECOURS, "1.2.3.4"))
  assert.ok(lockoutSecoursGlobal() > 0)
})
