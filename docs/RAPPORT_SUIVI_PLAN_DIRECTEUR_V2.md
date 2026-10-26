# Rapport de suivi — Plan directeur v2 (SEAMTECH-search)

**Objet :** suivi complet de toutes les actions réalisées lot par lot, avec
leurs preuves. Chaque affirmation est traçable : commit, run CI au SHA exact,
rapport de lot dans `docs/`, addendum dans `docs/VERIFICATION.md`.

- **Branche de travail :** `arena/b02ed560-seamtech-search`
- **Base de départ :** `e4c9fdf` (`arena/c154b2fa-seamtech-search`), jamais
  `main` — règle d'or du plan respectée jusqu'au bout.
- **SHA final :** `d07b21697405f99a7d93af001d79deaace61ab3e`
- **Période :** 2026-10-24 → 2026-10-26 — **50 commits**.
- **`main` : jamais touchée.** La fusion attend l'autorisation explicite du
  commanditaire (et se fera en `--no-ff`, jamais squash/rebase).

## Méthode appliquée à chaque lot (sans exception)

1. **Audit** de l'état réel avant de coder (y compris réfutation d'audits
   non vérifiés : deux affirmations d'un audit fourni ont été démenties par
   la mesure avant le redémarrage depuis `e4c9fdf`).
2. **Contrat RED** : les tests d'abord, échec constaté et commité.
3. **Implémentation** en respectant les règles figées : 100 % local (aucune
   API d'IA externe ne voit un PDF d'entreprise), aucun document client dans
   Git, aucune valeur devinée (unité absente = non convertie, modèle ambigu
   = décision humaine, % de confiance = jamais sans échantillon).
4. **Preuve** : les 4 workflows GitHub Actions (CI 13 jobs, Qualification
   native, Qualification réservés, Perf) verts **au SHA exact**, suites
   réelles PostgreSQL/Redis/MinIO/Playwright — zéro mock sur les briques
   critiques, zéro « PASS » simulé.
5. **Rapport de lot** (`docs/PLAN_DIRECTEUR_V2_LOT*.md`) + sections
   `DEPLOYMENT.md`/`VERIFICATION.md`, avec procédure de rollback écrite
   avant livraison.

## Synthèse

| Lot | Migration | Livré (essentiel) | Preuve au SHA exact | Rapport |
|---|---|---|---|---|
| Préparation | — | Socle ECC curaté (skills `.agents/` + `AGENTS.md`, MIT, épinglé `90606c8`) | `99945b5` | `.agents/` |
| **0a** | — | Sessions serveur Redis (SID CSPRNG, invalidation immédiate, idle 30 min / absolu 8 h), CSRF sur toutes les mutations, fix logout 405 | CI 13/13 @ `5c218c0` | `PLAN_DIRECTEUR_V2_AUDIT_BASE_LOT0A.md` |
| **0b** | 025 | Journal d'audit canonique, anti-IDOR, réduction de surface, gardes d'accès | PG 279/0, Redis 89/0, cov. 86,35 % | `PLAN_DIRECTEUR_V2_LOT0B.md` |
| **2** | 026 | Référentiel bateau canonique unique (alias, fusions historisées réversibles, jamais `45`=`45.1`), recherche punctuation/préfixe/pg_trgm, suggestions, outil de réindexation avant/après | PG 283/0 @ `e31bb2c` | `PLAN_DIRECTEUR_V2_LOT2.md` |
| **4a** | — | Aperçu produit « Extrait du document » dans les résultats de recherche (correspondances surlignées) | PG 289/0 @ `0c8d659` | `PLAN_DIRECTEUR_V2_LOT4A.md` |
| **1** | 027 | 4 rôles serveur (admin technique sans décision métier, lecture, utilisateur, approbateur seul décideur), demandes télécharger/modifier/supprimer, décisions instantanées en app, notifications, anti-auto-approbation, révocation des sessions au changement de rôle | PG 297/0 @ `406a6ff`, e2e multi-rôles 7/7 | `PLAN_DIRECTEUR_V2_LOT1.md` |
| **3a** | 028 | Extraction structurée des fiches voiles : confiance 0–1 par champ, méthode + page/zone/valeur brute (provenance complète), conversion mm/cm→m, plausibilité géométrique, CLI d'échantillon d'or | PG 300/0 @ `be3a6fa`, or **102/102** | `PLAN_DIRECTEUR_V2_LOT3A.md` |
| **3b** | 029 | Liaison extraction → modèle canonique, écran approbateur côte à côte (PDF + valeurs), corrections historisées, motifs appris localement (suggestions, jamais d'application silencieuse), apprentissage d'alias | PG 311/0 @ `52a61fe` (CI `40235516711`), e2e 3/3 | `PLAN_DIRECTEUR_V2_LOT3B.md` |
| **4b** | — | **Fonction A** : comparaison de voiles bloc par bloc sur valeurs vérifiées (identification, Δ cm et Δ % par dimension, matériaux appariés rôle+niveau, qualité) ; « comparable » = même modèle canonique + même type, sinon dit franchement | PG 314/0 @ `97f6b85` (CI `40241173904`), e2e 3/3 | `PLAN_DIRECTEUR_V2_LOT4B.md` |
| **5** | 030 | **Fonction B** : confiance de réutilisation robuste (médiane/MAD/IQR) ; N<5 ⇒ « Données insuffisantes (N/5) » sans % ; population hétérogène ⇒ aucun score ; seuil d'alerte 75 ; justification obligatoire ; évaluations approbateur persistées et auditées ; écran `/confiance` | PG 317/0 @ `921669b` (CI `40249015850`), e2e 3/3 | `PLAN_DIRECTEUR_V2_LOT5.md` |
| **6** | — | Cohérence UI globale : liens croisés voiles→fiche (jamais de lien mort), navigation × rôle sans impasse ; parcours comptés : demande **3 clics**, décision **3 clics**, téléchargement **1 clic** (octets `%PDF` relus) | PG 317/0 @ `41b01d8` (CI `40253683828`), e2e 3/3 | `PLAN_DIRECTEUR_V2_LOT6.md` |
| **DEP** | — | Paquet de transfert hors ligne (bundle Git figé, images Docker, `SHA256SUMS`, aucun secret transporté) + runbook serveur complet (dimensionnement, preflight, dédup avant OCR, vagues, reprise) | CI verte @ `d07b216` + dédup prouvée en réel | `PLAN_DIRECTEUR_V2_DEP.md` |

## Journal des actions par lot

### Lot 0a/0b — sécurité d'abord (`f36749b`, `093da73`, `5c218c0`)
- Remplacement des sessions signées par des sessions serveur Redis
  révocables (les anciens jetons `v1.*` sont rejetés), fail-closed 503,
  `Clear-Site-Data` à la déconnexion.
- Table `audit_logs` (025) alimentée par les actions sensibles ; contrôles
  d'appartenance (anti-IDOR) sur les ressources nominatives.
- Incident pris en compte : un garde-fou CI d'attribution a dû être recalé
  (`5c218c0`) — corrigé, pas contourné.

### Lot 2 — référentiel bateau (`7071961`, `d3447de`, `e31bb2c`)
- Quatre tables (modèles, alias, historique de fusions, synonymes) ;
  normalisation casse/accents/tirets sans jamais confondre `45`, `45.1` et
  `45 CC` ; classement exact > alias > préfixe > flou (pg_trgm).
- Deux correctifs de robustesse avant livraison : compatibilité pré-026 et
  protection des clés canoniques des alias.

### Lot 4a — extrait produit (`6a954f9` → `0c8d659`)
- Extraits de documents rendus dans les résultats (performance retravaillée
  en 2 passes). Une tentative CI « flaky » a été **rejetée et documentée**
  (`0c8d659`) plutôt que relancée jusqu'à passer.

### Lot 1 — rôles et approbations (`6ad0572` → `406a6ff`, docs `b534723`)
- Backend des 4 rôles + demandes (027), interface des demandes, parcours
  navigateur canonisés par rôle. Série de correctifs mesurés en CI
  (attribution de session, pièces PDF sources, fixture de suivi de dépôt,
  nom accessible du téléchargement approuvé, course de validation) — chaque
  échec a produit un fix ciblé, jamais un assouplissement de test.

### Lot 3a — extraction voiles (`2fcac3f`, `e190978`, `be3a6fa`, docs `480ecea`)
- Contrat RED figé avant code ; `sail_extractions` (028) avec provenance par
  champ ; le lot 3a ne résout **volontairement** aucun modèle bateau.
- Correctif `be3a6fa` : ne jamais deviner une unité absente/incompatible
  (`6600` nu ≠ 6,6 m) — la correction d'unité est de la provenance, pas de
  l'implausibilité.

### Lot 3b — liaison et corrections (`4d2776a` → `52a61fe`, docs `21de51b`)
- Migration 029, résolution déterministe ou décision approbateur, écran côte
  à côte avec le PDF source, propagation des corrections aux valeurs
  canoniques, motifs appris proposés (jamais appliqués seuls).
- Premier run CI rouge (`3d8ae9a`) : une assertion 3a obsolète + une spec
  live non gardée — corrigés dans le lot (`52a61fe`), règle retenue pour
  tous les lots suivants.

### Lot 4b — Fonction A (`fb51ce6` → `97f6b85`, docs `98fce87`)
- Comparaison bloc par bloc, candidats même-modèle+même-type uniquement,
  valeur corrigée par l'approbateur = valeur comparée (prouvé : SF 3,1).
- Mesure locale (hors Git) sur 9 fiches réelles transmises par le
  commanditaire : spi 100 %, génois/GV/jade partiels → chantier de
  calibration nommé, aucune vérité client versionnée.

### Lot 5 — Fonction B (`2dbe348` → `921669b`, docs `868a3c0`)
- Statistiques robustes + persistance (030, cohérence score/verdict portée
  par contrainte SQL), candidate exclue de sa propre population (N=5 pour
  6 voiles liées), écran `/confiance`, seed live de 5 voiles de référence
  pour prouver le score en conditions réelles.
- Contexte : corpus Dropbox `VOILES 2024.zip` (1 205 608 885 octets)
  constaté inaccessible depuis l'environnement (limite connecteur 100 Mio,
  CDN hors liste blanche) — action d'extraction demandée au commanditaire,
  lots poursuivis sans attendre.

### Lot 6 — cohérence et 3 clics (`6c2829e` → `41b01d8`, docs `8c1e180`)
- Gardes statiques (navigation → pages réelles, matrice de rôles figée) +
  recette navigateur comptée en clics sur 4 rôles.
- La recette a **réfuté une hypothèse de l'agent** (secours sans dépôt) :
  le compte de secours est l'admin d'urgence documenté — la matrice encode
  le design réel (`41b01d8`), la garde « aucun refus sur lien promis »
  reste le juge.

### DEP — préparation du serveur (`d07b216`)
- Script de transfert hors ligne (3 modes : préparer / vérifier / charger),
  runbook en 9 sections avec critères de fin cochables.
- Outil d'inventaire exécuté en réel sur arborescence synthétique : le même
  PDF sous deux noms = exactement **1 groupe de doublons** (dédup SHA-256
  avant OCR effective).

## État final mesuré (SHA `d07b216`)

- **Schéma métier `030_evaluations_confiance`** — migrations 001→030,
  44 tables métier (+6 héritées = 50 tables publiques).
- **Suites réelles :** PostgreSQL **317 passés / 0 sauté**, Redis/file
  durable **89/0**, couverture globale **≈ 86 %** (plancher 85 %).
- **Parcours navigateur (premier essai) :** Lot 6 3/3, Lot 5 3/3, Lot 4b
  3/3, Lot 3b 3/3, Lot 1 7/7, parcours canonisés 8/8, OCR 4/4 ;
  parcours machine complet fiche 7792-SO : **578 ms**.
- **4 workflows verts** au SHA final : CI `40255784871`, Qualification
  native `40255784784`, Qualification réservés `40255784841`, Perf
  comparaison `40255784825`.

## Règles de confidentialité tenues

- **100 % local** : aucune API d'IA externe n'a reçu de PDF d'entreprise ;
  les 9 fiches transmises et le corpus Dropbox n'ont produit **aucun
  artefact Git** (mesures éphémères, agrégats anonymisés uniquement).
- Le dépôt GitHub est **public par décision explicite du commanditaire**
  (dérogation RG13 consignée dans chaque rapport de lot) ; en conséquence
  aucun document client, extrait nominatif ou secret n'y a été ajouté.
- Le stock physique reste hors périmètre, comme au plan.

## Ce qui reste à faire — côté commanditaire / serveur (pas du code)

1. **Fusion vers `main`** : sur autorisation explicite uniquement.
2. **Extraire `VOILES 2024.zip`** dans l'interface web Dropbox (clic droit
   → Extraire) → calibration des gabarits génois/GV/jade PDF par PDF via le
   connecteur ; sinon voie production : décompression directe sur le
   serveur d'entreprise (DEP §7).
3. **Exécuter le DEP** sur le serveur (runbook §1→§9) : paquet hors ligne,
   preflight + inventaire de l'archive réelle, comptes nominatifs
   (≥ 2 approbateurs) puis `SEAMTECH_UI_PASSWORD` vidé, vague pilote
   mesurée, ingestion par vagues, sauvegarde testée par restauration.
4. **Chrono humain** « ouverture → validation < 2 min » : la part machine
   est à 578 ms ; la lecture humaine se confirme sur place (DEP §9).

## Comment vérifier ce rapport

```bash
git log --oneline e4c9fdf..HEAD          # les 50 commits, dans l'ordre
ls docs/PLAN_DIRECTEUR_V2_LOT*.md         # un rapport par lot (preuves + rollback)
grep -n "^## Addendum" docs/VERIFICATION.md   # preuves rejouables par lot
pytest -q tests/test_lot6_coherence_ui.py tests/test_dep_transfert_hors_ligne.py
```
