# Mesure « validation < 2 minutes » — critère de sortie Phase 1 (§17.14)

Statut au 22/09/2026 : **mesure scriptée VERTE en CI, chrono publié sur la
FENÊTRE CORRIGÉE** — parcours machine complet de la vraie fiche 7792-SO
(navigation → fiche ouverte, champs et PDF rendus → correction RG11 →
validation) : **665 ms** (run 35715024408, commit d7a0026) et **656 ms**
(run 35715783739) sur deux runs verts consécutifs — variance ~1 %, ordre de
la demi-seconde (critère < 120 000 ms) ; valeurs lisibles en annotation
`mesure-phase1` du job e2e.

Historique des corrections d'honnêteté (audit indépendant du 22/09) :
1. **La valeur n'était traçable nulle part** — le reporter `list` de
   Playwright n'imprime jamais les annotations de test (banc minimal
   reproduit), le run n'avait aucun artefact et l'API GitHub aucune
   annotation ; l'annonce « visible dans l'onglet Actions » décrivait quelque
   chose qui n'existait pas. Corrigé : la CI extrait le chrono du rapport
   JSON (`PLAYWRIGHT_JSON_OUTPUT_NAME=report.json`) et le publie en
   `::notice mesure-phase1`, avec garde-fous (3 passés au premier essai,
   0 flaky, 0 saut, annotation exigée).
2. **La fenêtre de mesure ne couvrait pas ce que le libellé annonçait** —
   7792-SO est auto-sélectionnée à l'arrivée sur la page (première de la
   file triée par confiance croissante), le chrono démarrait après : les
   valeurs mesurées sous cette fenêtre — 351 ms (run 35666837231) et
   449 ms (run 35667273452), variance ~30 % entre deux runs verts, ordre de
   la demi-seconde — étaient un PLANCHER (relecture DOM + deux écritures sur
   une fiche déjà affichée ; le rendu PDF n'était jamais attendu). Corrigé au
   commit d7a0026 : le chrono démarre avant la navigation et exige le rendu
   réel (file, titre, 48 champs, PDF « 1 / N »). Ces deux valeurs plancher
   restent publiées ici pour mémoire, à ne plus citer comme temps
   d'ouverture.

**Mesure humaine : procédure fournie, chiffres non mesurés (0/3 fiches) — la
Phase 1 n'est pas close au sens de l'acceptation tant que ce tableau est
vide.**

Le critère complet de la Phase 1 est : « une fiche entre en base par
l'interface, validée, avec traçabilité complète — ≥ 90 % des champs lus
automatiquement, validation en moins de 2 minutes, 0 régression ». Le ≥ 90 %
est atteint et mesuré sur le document réel (banc étendu 74/74, `validate_extraction`
74/74 et 6/6 Phase 0 — voir `EMPREINTES.md`). Restait le chrono, jamais produit sur un vrai
document.

## 1. Mesure scriptée (Playwright) — ce que la CI publie

Test : `frontend/e2e/validation.spec.ts`, « la vraie fiche 7792-SO : parcours
machine complet (< 2 min) ».

- Début du chrono : AVANT `page.goto("/validation")` (signIn est le harnais,
  hors chrono). La fiche 7792-SO étant auto-sélectionnée à l'arrivée
  (première de la file), démarrer après l'ouverture mesurerait un plancher —
  c'est ce que faisait la première version, corrigée le 22/09.
- Fin du chrono : apparition du message de confirmation de validation.
- Dans la fenêtre : navigation, chargement de la file, ouverture de la fiche
  (titre), 48 champs réels comptés + valeurs spot, rendu du PDF par pdf.js
  (attente explicite « 1 / N »), correction d'un champ (RG11), validation.
- Le test échoue au-delà de 120 000 ms ; la valeur exacte est extraite du
  rapport JSON par l'étape CI (`--reporter=list,json` +
  `PLAYWRIGHT_JSON_OUTPUT_NAME=report.json`) et publiée en `::notice` (le
  reporter `list` seul n'imprime jamais les annotations). La porte CI exige
  en outre 3 passés au PREMIER essai, 0 flaky, 0 saut.

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
