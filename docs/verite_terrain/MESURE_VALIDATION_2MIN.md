# Mesure « validation < 2 minutes » — critère de sortie Phase 1 (§17.14)

Statut au 22/09/2026 : **mesure scriptée VERTE en CI et CHRONO PUBLIÉ**
(run 35666837231, commit 6c5b0ae) — parcours ouverture→validation de la vraie
fiche 7792-SO : **351 ms** (critère < 120 000 ms), valeur lisible en
annotation `mesure-phase1` du job e2e. **Correction d'honnêteté (audit
indépendant du 22/09)** : sur les runs antérieurs, la valeur en ms n'était
traçable NULLE PART — le reporter `list` de Playwright n'imprime jamais les
annotations de test (banc minimal reproduit), le run n'avait aucun artefact et
l'API GitHub aucune annotation de test ; l'annonce « visible dans l'onglet
Actions » décrivait quelque chose qui n'existait pas. Depuis le 22/09, la CI
extrait le chrono du rapport JSON (`PLAYWRIGHT_JSON_OUTPUT_NAME=report.json`)
et le publie en `::notice mesure-phase1`, avec garde-fou : 3 tests live
réellement exécutés (0 saut) et annotation exigée. **Mesure humaine :
procédure fournie, chiffres non mesurés (0/3 fiches) — la Phase 1 n'est pas
close au sens de l'acceptation tant que ce tableau est vide.**

Le critère complet de la Phase 1 est : « une fiche entre en base par
l'interface, validée, avec traçabilité complète — ≥ 90 % des champs lus
automatiquement, validation en moins de 2 minutes, 0 régression ». Le ≥ 90 %
est atteint et mesuré sur le document réel (banc 31/31, `validate_extraction`
6/6 — voir `EMPREINTES.md`). Restait le chrono, jamais produit sur un vrai
document.

## 1. Mesure scriptée (Playwright) — ce que la CI publie

Test : `frontend/e2e/validation.spec.ts`, « la vraie fiche 7792-SO : champs
réels, correction RG11, validation < 2 min ».

- Début du chrono : clic sur la fiche dans la file (ouverture).
- Fin du chrono : apparition du message de confirmation de validation.
- Entre les deux : affichage des 48 champs réels (comptages assertés),
  relecture/correction d'un champ (RG11), validation individuelle.
- Le test échoue au-delà de 120 000 ms ; la valeur exacte est poussée en
  annotation `mesure-phase1`, extraite du rapport JSON par l'étape CI
  (`--reporter=list,json` + lecture de `report.json`) et publiée en
  `::notice` — c'est ce mécanisme qui rend le chiffre lisible dans les
  journaux du run (le reporter `list` seul ne l'imprime pas).

C'est le temps de RENDU + TRAITEMENT, pas le temps de lecture humain — il
borne la part machine du parcours. Il est rejoué à chaque CI sur la base
jetable semée par `frontend/e2e/seed-live-pg.py` (la vraie fiche 7792-SO y
entre par le pipeline complet).

## 2. Mesure humaine — procédure (3 fiches, chrono à la main)

Le script mesure la machine ; l'humain mesure le TRAVAIL. À exécuter sur un
poste équipé (navigateur + dépôt PostgreSQL) par un opérateur qui n'a pas
écrit le test :

1. Déposer un dossier réel via l'écran Dépôt (ou `cli depot`) ; attendre la
   fin du traitement.
2. Démarrer le chrono à l'ouverture de la fiche dans la file de validation.
3. Relire chaque champ en le comparant au PDF affiché à droite (les zones
   surlignables aident) ; corriger ce qui doit l'être (le champ corrigé est
   verrouillé RG11).
4. Arrêter le chrono au clic de validation (individuelle).
5. Noter : code de la fiche, durée, nombre de champs corrigés.

| # | Fiche | Durée (s) | Champs corrigés | Opérateur | Date |
|---|-------|-----------|-----------------|-----------|------|
| 1 | | non mesuré | | | |
| 2 | | non mesuré | | | |
| 3 | | non mesuré | | | |

Règle de publication : tant qu'une case est vide, écrire « non mesuré ». Le
critère est rempli quand les 3 durées sont < 120 s. La fiche 7792-SO (48
champs, tous à ≥ 0,85 de confiance, un seul palier « décomposé ») est la
candidate n°1 du premier rang.

## 3. Pourquoi deux mesures

- La mesure scriptée est répétable à chaque CI : elle détecte toute
  dégradation de la part machine (latence API, rendu, PDF).
- La mesure humaine est la seule qui vaille pour l'acceptation du critère :
  un opérateur qui relit 48 champs doit tenir sous 2 minutes. Elle dépend de
  la densité d'écran et de l'entraînement — d'où 3 fiches, pas une.
