# Rapport production-readiness — exécution du prompt d'optimisation (2026-09-29)

> Exécution des phases du prompt « Production-Readiness Optimization » sur la
> branche `arena/01a0d324-seamtech-search`, base `main` = `4a32911` (fusion
> PR #31). Chaque affirmation porte son étiquette : **RÉEL** (données réelles),
> **SYNTHÉTIQUE** (fixtures), **NON MESURÉ** (pas exécutable dans cet
> environnement — blocked, avec la raison et le prochain verrou).

## Décision synthétique

**NON VALIDABLE pour une mise en production** — inchangé au sens du rapport du
28/09, mais pour un périmètre plus petit : le flux corpus réel est prouvé en
CI (PR #31), la recherche couvre désormais les fiches non validées (Phase 2.1,
ce lot), et la validation humaine **reste à faire par le commanditaire**
(0/7 fiches validées). Les phases à infrastructure réelle (VPS01, R2, poste
atelier, 200 dossiers, 10 000 fiches) sont **NON MESURÉES** faute d'accès.

## Phase 0 — Confidentialité

| Étape | État | Détail |
|---|---|---|
| 0.1 Inventaire des données réelles | **RÉEL** | PDF client réel `7792-SO_ffab.pdf` (166 990 o, SHA-256 `43afc51e…`, désigné « DOCUMENT CLIENT RÉEL » par `docs/verite_terrain/EMPREINTES.md`) présent en double (racine + `sample_data/CLIENT-7792-SO/`) ; 7 ZIP réels du corpus 2026-09-28 à la racine ; fixtures synthétiques identifiées (CLIENT-123, CLIENT-GENOA, CLIENT-E2E-TROIS, ocr_propre/ocr_degrade) |
| 0.2 Passer le dépôt en privé | **BLOQUÉ — droits propriétaire requis** | `gh api -X PATCH repos/… -f private=true` → `403 Resource not accessible by integration` : le token de l'agent n'a pas les droits admin. **Action commanditaire (30 s)** : GitHub → Settings → General → Danger Zone → Change visibility → Private. Tant que le dépôt est public, les documents clients y restent téléchargeables. |
| 0.3 Remplacer les fixtures réelles par des équivalents anonymisés | **À décider** | La fiche 7792-SO est la vérité terrain du gabarit `FICHE_PORTANT_V1` v2 (74/74) ; les 7 ZIP alimentent le job CI `recette-corpus-reel`. Les remplacer = reconstruire ces preuves. Décision appartenant au commanditaire (voir §Décisions ouvertes). |
| 0.4 Purge de l'historique | **À décider (destructif)** | `git filter-repo` + force-push + rotation des secrets. Règle du prompt : demander d'abord. Purger les ZIP casserait le job `recette-corpus-reel` (ils sont lus au checkout) — à arbitrer avec 0.3. |
| 0.5 Garde-fou CI anti-document | **FAIT (ce lot)** | `tests/test_confidentialite_depots.py` : 17 tests (suite sans service). Tout PDF/ZIP/`.plx`/`.xin`/`.dxf`/`.db` suivi par Git doit être épinglé (chemin + SHA-256 + taille) ; tout ajout/modification non épinglé rend la CI rouge ; formats machine interdits en vrac ; doublon racine 7792 == copie canonique. |

## Phase 2 — Recherche utile avant validation

| Étape | État | Détail |
|---|---|---|
| 2.1 Indexer les `a_valider`, badgées | **FAIT (ce lot)** | `ecrire_fiche` remplit le texte de recherche pondéré dès l'écriture ; migration `018_recherche_a_valider` rattrape les fiches existantes ; défaut de `/recherche` = `valide` + `a_valider` avec `statut` exposé sur chaque résultat ; badge « Non vérifiée » + case « Validées uniquement » dans l'interface ; `inclure_a_valider=false` = archive de confiance ; fiche `rejete` jamais renvoyée. RG3 intact : aucune fiche ne devient `valide` sans humain. Vérifié **RÉEL** en CI par le job `recette-corpus-reel` (fiche du corpus retrouvée par défaut, absente de l'archive de confiance, y compris sur base restaurée) — au moment de la rédaction : suite locale verte, CI de la PR en cours. |
| 2.2 File de validation pour débit | **PARTIEL — existant** | Écran Validation + `/validation/file` + `/fiches?statut=a_valider` déjà livrés (PR #28-31). Tri par confiance croissante, flux clavier seul, minuteur de session : **NON FAIT** (chantier frontend suivant). |
| 2.3 Chronométrer 3 fiches avec l'ouvrier | **NON MESURÉ** | Aucun ouvrier dans cet environnement ; cible médiane ≤ 2 min/fiche à mesurer au poste atelier (protocole : `MESURE_VALIDATION_2MIN.md`). |
| 2.4 Validation groupée verrouillée | **DÉJÀ EN PLACE (vérifié)** | `valider_lot` : 409 tant que `calibre:false`, acquittement humain explicite exigé. Calibration 300-500 fiches : **NON MESURÉ** (0 fiche validée humainement). |

## Phases 1, 3-9 — état condensé

- **Phase 1 (200 dossiers réels)** : **NON MESURÉ** — seuls 7 dossiers réels
  existent (corpus ZIP), déjà traités de bout en bout en CI (PR #31 :
  import 7/7, extraction 173 champs, recherche 8 familles, PDF ouvert,
  présigné 302/900 s, sauvegarde/restauration). Le seuil « ≥ 200 dossiers,
  ≥ 95 % champs corrects sur held-out » est inatteignable sans nouvelle
  livraison de données (décision commanditaire).
- **Phase 3 (robustesse extraction)** : gabarits 7/7 sur le corpus (PR #31) ;
  gabarit-brouillon guidé **existant** (`gabarit_brouillon.py`, statut
  `valide` requis avant usage — vérifié) ; banc de régression par correction
  humaine : **À FAIRE** (dépend des corrections réelles).
- **Phase 4 (OCR)** : pipeline par étages livré et prouvé sur corpus natif
  (0 page OCRisée nécessaire, 1 image scannée non OCRisée faute de Tesseract
  local) ; précision OCR réelle sur scans : **NON MESURÉ** (corpus sans scan
  PDF) ; job nocturne : **À FAIRE**.
- **Phase 5 (stockage/VPS01/DR)** : audit S3/MinIO livré (PR #29) ; docs
  runbook VPS01+R2 vs poste atelier : **À FAIRE** ; remplacement MinIO→R2 en
  production : **décision D-2 commanditaire** ; sauvegarde/restauration
  PostgreSQL réelles prouvées en CI (job `sauvegarde`), RPO/RTO réels :
  **NON MESURÉS** (nécessite l'infrastructure cible).
- **Phase 6 (échelle 10 000 fiches)** : **NON MESURÉ** — exige VPS01 ou job
  de charge dédié (proposition : job CI `scale-bench` synthétique borné, à
  décider).
- **Phase 7 (revue frontend atelier)** : **PARTIEL** — écrans existants
  (Recherche, Fichiers, Dossiers, Nouveau, Validation, Fiche, Qualité),
  recherche dimensionnelle par bornes et URL partageable livrées ; passage
  complet sur la machine atelier : **NON MESURÉ**.
- **Phase 8 (allègement)** : **À FAIRE** — candidats identifiés (assistant,
  endpoints ML) à masquer derrière un drapeau avec l'accord du commanditaire.
- **Phase 9 (portail de release)** : checklist 18 rubriques existante ; les
  rubriques à infrastructure réelle resteront vides tant que VPS01/R2/ouvrier
  ne sont pas disponibles.

## Vérifications de ce lot (mesures locales, venv neuf + CI 10/10)

```bash
python -m pytest tests/test_confidentialite_depots.py tests/test_empreintes_fixtures.py -q
# → 21 passed
python -m pytest -m "not postgres and not s3 and not perf" -q
# → 764 passed, 3 skipped, 234 deselected  (main au même arbre : 746 passed, 3 skipped, 232 deselected)
ruff check .        # → All checks passed
cd frontend && ./node_modules/.bin/tsc --noEmit && pnpm build   # → verts
```

**CI complète : run [36569073423](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36569073423), 10/10 jobs VERTS** sur `c10449e` (PR #32) : backend 3.11/3.12/3.13 (suite PostgreSQL avec le contrat 018 : fiche nouvelle écrite → vecteur rempli → retrouvée badgée par défaut, absente de l'archive de confiance ; comptages 14/12), frontend, docker, integration, sauvegarde, ocr, e2e (suite défaut SQLite : le spec badge skippe proprement ; **suite live PostgreSQL : badge « Non vérifiée » + filtre « Validées uniquement » prouvés au premier essai, garde de porte 4/4**), recette-corpus-reel (**contrat 018 éprouvé sur le corpus réel : fiche a_valider retrouvée par défaut avec statut exposé, absente avec `inclure_a_valider=false`, y compris sur base restaurée**).

Premier run (`36567702950`) : 4 échecs PostgreSQL (fiche 7792-SO déjà validée dans le banc → `conservee_validee` ; deux comptages 12 restés à l'ancien contrat) et 1 échec e2e (spec badge exécuté dans la suite SQLite où la recherche de fiches répond 503 et où `BIS-7792` n'existe pas) — tous corrigés par le commit `c10449e` : fiche NOUVELLE à code unique pour les tests d'écriture, comptages 14/12, garde de saut `SEAMTECH_E2E_DATABASE_URL` + spec ajouté à la commande live (garde de porte 3→4, documenté dans `ci.yml`).

## Décisions ouvertes pour le commanditaire

1. **Passer le dépôt en privé** (recommandé immédiatement — action
   propriétaire, 30 secondes).
2. Faut-il retirer/purger les documents réels du dépôt (fiche 7792-SO, 7 ZIP)
   au prix de la reconstruction des preuves qui s'y appuient, ou les conserver
   dans un dépôt privé ?
3. Où héberger le corpus réel hors Git (partage chiffré / bucket privé) pour
   la recette locale et le poste atelier ?
4. Seuils d'acceptation Phase 1 (≥ 200 dossiers, ≥ 95 % held-out) : confirmer
   ou ajuster.
5. D-2 : backend objet de production (R2 ?) — prérequis Phase 5.
6. Accès VPS01 / poste atelier / ouvrier pour les phases 5-7.

## Ce qui reste avant « product-ready pour le corpus fourni »

1. Validation humaine REF-001..REF-007 (guide :
   `GUIDE_VALIDATION_HUMAINE_CORPUS.md`) — **au commanditaire**.
2. CI verte de la PR de ce lot (10 jobs, recette réelle incluse).
3. Décision 1-3 ci-dessus (confidentialité).
