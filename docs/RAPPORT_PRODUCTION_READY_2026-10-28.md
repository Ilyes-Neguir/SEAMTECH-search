# Rapport « production ready » — campagne complémentaire du 28/10/2026

Branche : `arena/a4dbb7f0-seamtech-search` (aucune fusion vers `main` sans
accord explicite). Ce rapport répond aux deux documents du 28/10/2026
(campagne complémentaire + synthèse NON GO). Il dit **PRÊT** ou **NON PRÊT**
point par point, avec la commande exécutée et son résultat brut.

SHA de code de référence : **`9c483e1`** (branche `arena/a4dbb7f0-seamtech-search`,
PR #42) ; le présent rapport et ses mises à jour sont des commits `docs` posés
au-dessus, sans changement de code. Les 4 workflows
GitHub Actions (`CI`, `Qualification native`, `Qualification reserves`,
`Perf comparaison`) sont **verts sur ce SHA exact**, en `push` comme en
`pull_request` (runs `40780335856`/`40780345402` pour `CI`).

> Règle appliquée partout : ce qui n'a pas été exécuté ici n'est pas déclaré
> corrigé. Une limite non reproduvable en bac à sable est listée en §7 au lieu
> d'être maquillée en preuve.

---

## 1. Environnement de la campagne

| Élément | Statut dans cette campagne |
|---|---|
| Système | Debian 12 `bookworm`, 2 vCPU, ~3 Go de RAM (aucun Docker, aucun droit `apt`) |
| PostgreSQL | **16.2 réel** (binaires `pgserver`), extension `vector` 0.6.2 ; `unaccent` et `pg_trgm` compilées depuis les sources PostgreSQL 16.2 (`contrib/`) |
| Redis | **réel** (binaire `redislite`) sur `127.0.0.1:6390` |
| Stockage objet | **magasin S3 en mémoire** (`tests/s3_en_memoire.py`) qui conserve les octets et sait simuler coupure/intégrité non prouvée — **pas MinIO** |
| Navigateur | **Chromium 153 réel** (`@sparticuz/chromium`, NSPR/NSS compilés localement), piloté par Playwright 1.64 |
| Application | backend FastAPI réel + front **build de production** (`next build` puis `next start`) |
| OCR | `tesseract.js` (WASM) via le shim `PATH=/tmp/sbx/bin` — **pas tesseract natif** |

Commandes de reconstitution : `/home/user/env/setup.sh` (PostgreSQL + Redis +
`.venv`), puis

```bash
export PATH="$PWD/.venv/bin:$PWD/.venv/lib/python3.11/site-packages/pgserver/pginstall/bin:$PATH"
export LD_LIBRARY_PATH=/home/user/env/lib
export PLAYWRIGHT_CHROMIUM_EXECUTABLE=/tmp/chromium
export SEAMTECH_TEST_DATABASE_URL=postgresql://postgres:@127.0.0.1:5432/postgres
export SEAMTECH_TEST_REDIS_URL=redis://127.0.0.1:6390/1
```

---

## 2. Points P0 des rapports — verdict

### P0.1 Sauvegarde/restauration complète (base + `./data` + objets) — **PRÊT**

Ce qui manquait : la sauvegarde ne couvrait que la base et un inventaire
d'archive ; les octets des lots téléversés (`./data/uploads/…`) et les objets du
stockage n'étaient pas sauvegardés, et aucune restauration coordonnée n'existait.

Livré : `sauver --data <répertoires>`, `inventorier_data`, `copier_data`,
`restaurer_data`, `restaurer_complet`, `restaurer --data-cible --coordonnee`.
Ordre imposé : base → données → fichiers → objets → `verifier()`.

Preuve :

```bash
.venv/bin/python -m pytest -q tests/test_sauvegarde_complete.py
# 5 passed
```

Le test détruit la base, `./data`, le répertoire des documents **et le bucket**,
puis restaure et exige `verification.ok is True` avec les 4 copies de données,
l'objet S3 aux octets identiques et la ligne `documents` correspondante.

### P0.2 Promotion réelle des lots vers le stockage objet — **PRÊT** (complété)

La promotion elle-même était déjà en place (post-commit, vérifiée par relecture,
`sauvegarde objet` démontrée). Restaient deux trous, corrigés ici :

1. **rejeu** : un dossier rejoué (`deja_traite`) laissait une copie complète
   stagée sous `uploads/lots-televerses/<nouveau jeton>` que rien ne
   référençait et que la rétention n'élague pas ;
2. **absence de test** sur ce chemin.

Preuve :

```bash
.venv/bin/python -m pytest -q tests/test_promotion_lots.py
# 7 passed  (dont 3 nouveaux : rejeu nettoyé, fichier modifié CONSERVÉ,
#            archive par chemin JAMAIS touchée)
```

La suppression n'a lieu que si les octets sont **déjà enregistrés** pour cette
fiche (PDF : empreinte qui a servi à reconnaître le rejeu ; pièce : présence dans
`fiche_piece_jointe`). Un fichier modifié entre-temps n'est jamais supprimé.

### P0.3 Montée de version et retour arrière prouvés — **PRÊT**

Livré : `seamtech_search/migration_securee.py` (`avant`, `apres`, `rollback`,
`diagnostic`) + `run_migrations(jusqu_a=…)` pour reconstituer une installation
antérieure sans entretenir une seconde liste de migrations.

Preuve :

```bash
.venv/bin/python -m pytest -q tests/test_migration_rollback.py
# 4 passed
```

Scénario exécuté : installation reconstituée à `025_audit_logs`, semée de
données métier (4 documents), photographiée, migrée jusqu'à `031_accessoires` —
**aucune table perdue, aucune ligne perdue, contenu inchangé** (empreintes
md5 par table) — puis **deux retours arrière successifs** vers des bases neuves,
chacun rendant exactement l'état d'avant (versions de schéma, comptes,
empreintes) et un index de recherche fonctionnel.

### Rotation PDF 90°/180°/270° — **PRÊT**

`seamtech_search/pdf_orientation.py` reconstruit les lignes le long de la
direction réelle des glyphes (pas selon `page.rotation`, qui n'est pas fiable) et
écarte les glyphes d'orientation minoritaire avant `extract_text` (sinon
pdfplumber rend « NOPMAT » à l'envers). Version d'extracteur **6 → 7** pour
forcer la ré-extraction des PDF déjà indexés.

```bash
.venv/bin/python -m pytest -q tests/test_pdf_orientation.py   # 39 passed (matrice 4×4 : 16/16)
```

### OCR navigateur en CI (confusion 0/O) — **PRÊT**

Correctif de cause racine (chiffres retirés, police monospace, `J` exclu de
l'alphabet de test), sans aucun `retry`. Invariants verrouillés :

```bash
.venv/bin/python -m pytest -q tests/test_ocr_termes_uniques.py  # 6 passed
```

### Relais multipart durci — **PRÊT**

`frontend/lib/relais-multipart.ts` remplace `await req.formData()` sur les trois
routes d'envoi. Le corps n'est **jamais** matérialisé.

| Cas | Avant | Après |
|---|---|---|
| corps de 10 Go | mémoire du BFF épuisée | `413` avant lecture (Content-Length) ou coupure du flux |
| multipart tronqué | `502 backend injoignable` (faux) | statut réel du backend (`400`) |
| navigateur qui abandonne | envoi backend poursuivi | envoi backend **annulé** |
| backend muet | requête pendante | `504` |
| 2 onglets simultanés | concurrence illimitée | `429` au-delà de 2 par session |
| réponse d'erreur HTML | transformée en `502` | statut et corps recopiés tels quels |

```bash
cd frontend && node --experimental-strip-types --test lib/*.test.ts   # 10 passed
```

Étape ajoutée à la CI (`frontend`) : `pnpm exec node --experimental-strip-types --test lib/*.test.ts`.

### Accessibilité axe-core sur TOUS les écrans — **PRÊT**

L'audit ne couvrait que deux écrans. Nouvelle spec `e2e/accessibilite.spec.ts`
(WCAG 2.1 A/AA), 4 rôles canoniques + anonyme, écrans de détail, recherche
vide, login, 403, session absente.

```bash
cd frontend && npx playwright test e2e/accessibilite.spec.ts   # 8 passed (1,6 min)
```

**73 écrans scannés, 0 violation**, 6 rôles (`admin`, `lecture`, `utilisateur`,
`approbateur`, `anonyme`, `secours`) — `frontend/accessibilite/rapport-axe.json`.
Défauts réels trouvés puis corrigés :

| Défaut | Gravité axe | Correctif |
|---|---|---|
| logo sans le texte visible dans le nom accessible (WCAG 2.5.3) | serious | nom accessible = texte visible, `aria-label` retiré |
| 2 `<select>` sans nom (tri, dimension) | critical | `aria-label` explicite |
| champ de recherche sans nom (placeholder ≠ nom) | critical | `aria-label` |
| 3 champs de validation sans nom | critical | `aria-label` avec nom + rang |
| entrée fichier des gabarits sans nom | critical | `aria-label` |
| contraste 4,26:1 (bandeau doublon) | serious | couleur de texte ambre 900 |
| contraste 3,65:1 (bouton vert « Valider ») | serious | `emerald-700` (4,8:1) |
| aucun repère `<main>` | best-practice | `<main id="contenu">` + lien d'évitement |
| **plusieurs `<main>`** (le layout + 14 pages) | best-practice / mode strict | les 14 pages utilisent un `<div>` : un seul repère principal |
| contraste du badge de recherche 4,36:1 | serious | `text-foreground/85` |

### Stabilité de la mesure axe et mode CI sans comptes nommés — **PRÊT**

Le job `e2e` de la CI est devenu rouge sur le premier SHA de cette campagne, pour
deux raisons distinctes, toutes deux traitées :

1. **Faux positifs de contraste pendant les animations** : les écrans apparaissent
   en fondu (opacité 0 → 1 sur 0,55 s). Une analyse lancée pendant le fondu
   mesure des couleurs mélangées (2,1:1 au lieu de ~7:1) et le verdict changeait
   d'une exécution à l'autre. La spec injecte désormais
   `animation-duration/transition-duration: 0s` avant `analyze()` ; l'injection
   est rejouée une fois si Playwright lève « execution context was destroyed »
   (navigation/hydratation), et `waitForLoadState("load")` + 120 ms remplace
   `networkidle`, qui ne se stabilise jamais sur les écrans qui interrogent en
   boucle (notifications, file de validation).
2. **La CI n'expose pas `SEAMTECH_E2E_DATABASE_URL`** : les comptes de rôle
   nommés n'existent pas dans ce mode. Un test « tous les écrans — compte de
   secours » couvre les 14 écrans sans aucun compte nommé ; les tests qui
   exigent un rôle canonique ou un écran de détail sont explicitement `skipped`
   dans ce mode. Vérifié en reproduisant la CI (`SEAMTECH_E2E_DATABASE_URL`
   retiré) : **4 passed, 5 skipped** ; avec PostgreSQL + Redis : **8 passed**.

```bash
cd frontend && unset SEAMTECH_E2E_DATABASE_URL && npx playwright test \
  e2e/accessibilite.spec.ts e2e/search.spec.ts     # 4 passed, 5 skipped (mode CI)
```

### Défauts d'ORDRE dans la suite e2e (cause de rouge CI) — **PRÊT**

Trois tests ne passaient qu'isolés ; la suite complète était donc
**ordre-dépendante** (rouge ou vert selon l'ordre alphabétique) :

1. `lot3b` renommait le libellé de modèle de la fiche de référence **sans le
   restaurer** → `lot5-confiance` ne trouvait plus sa population (0/5) ;
   corrigé par une remise en état via l'API du produit (correction + résolution) ;
2. `lot6` supposait qu'aucune autorisation de téléchargement n'existait, alors
   que `lot1-approbations` en accorde une valable 24 h ; comme il n'existe
   volontairement aucune API de révocation, le parcours crée désormais **son
   propre compte utilisateur** ;
3. `test_sessions_redis` codait en dur un port Redis « mort » (6390) qui pouvait
   être vivant ; le port est maintenant demandé au système.

---

## 3. Résultats bruts (commit de référence : `9c483e1`)

| Commande | Résultat |
|---|---|
| `.venv/bin/ruff check .` | `All checks passed!` |
| `pytest -m "not postgres and not s3 and not perf and not redis_queue and not ocr_suite"` | **1060 passed**, 573 deselected (128,75 s) |
| `pytest -m "postgres and not perf and not ocr_suite"` | **372 passed, 3 skipped** (201,17 s) |
| `npx playwright test` (Chromium 153 réel, Next build de production, PostgreSQL 16 + Redis) | **108 passed, 3 skipped** (4,9 min) |
| `npx playwright test e2e/accessibilite.spec.ts` (PG 16 + Redis, 4 rôles + anonyme + secours) | **8 passed** — **73 écrans, 0 violation** |
| `npx playwright test e2e/accessibilite.spec.ts e2e/search.spec.ts` (sans `SEAMTECH_E2E_DATABASE_URL`, comme la CI) | **4 passed, 5 skipped** |
| `node --test lib/*.test.ts` | **10 passed** |
| `tsc --noEmit` | aucune erreur |
| `npm run build` | rc=0 |
| CI GitHub Actions, run `40780345402` (SHA `9c483e1`) | **13/13 jobs verts** (`backend` 3.11/3.12/3.13, `frontend`, `e2e`, `docker`, `integration`, `ocr`, `recette-locale`, `recette-corpus-reel`, `sauvegarde`, `securite-dependances`, `hors-ligne-reel`) |
| `Qualification native`, `Qualification reserves`, `Perf comparaison` (SHA `9c483e1`) | verts, en `push` et en `pull_request` |

Les 3 tests e2e « skipped » sont des tests qui exigent un stockage S3 réel
(MinIO) : ils ne sont pas comptés comme verts.

## 4. Ce que la campagne a couvert

- **Rôles × routes** : `auth.spec.ts` éprouve 30 routes (API et BFF) en anonyme
  → `401` ; matrix de navigation par rôle (`lot6`) : aucun lien promis ne
  mène à un refus ; `lecture`, `utilisateur`, `approbateur`, `admin`, secours.
- **États de session** : valide, expirée (cookie conservé mais session
  inconnue), révoquée après déconnexion, falsifiée, absente.
- **Parcours métier** : dépôt de dossier par glisser-déposer récursif en envois
  bornés, création d'un seul lot, tri Stockable/Analysé/Accessoire, analyse PDF,
  extraction, correction approbateur, recherche par dimension/matériau/bateau/
  abréviations, comparaison de voiles, confiance ≥ 5 et cas < 75 % (données
  insuffisantes), notifications, accessoires, validation 3 clics.
- **Manipulations** : envois valides/invalides/trop gros, annulation, reprise,
  téléchargement, aperçu (Range), approbation et refus, doublons, concurrence.
- **Pannes injectées** : backend injoignable (`hors-ligne.spec.ts`, messages
  français pour l'opérateur), stockage objet indisponible puis reprise
  (`test_promotion_lots`), intégrité non prouvée, base restaurée détruite,
  Redis indisponible → `503` (fail-closed), disque plein (`test_chaos`).
- **Sécurité** : IDOR et traversées de chemin (`test_lot0b_audit_securite`,
  `test_confidentialite_depots`), injections, CSRF double soumission, limites
  de surface (taille, profondeur, nombre), verrous RG11/RG13.

## 5. Ce que la campagne N'A PAS couvert (à dire, pas à masquer)

| Sujet | Pourquoi | Ce qui a été fait à la place |
|---|---|---|
| Docker Desktop / Windows natif | pas de Docker dans le bac à sable | build et exécution directs ; checklists `docs/CHECKLIST_POSTE_WINDOWS.md` inchangées |
| PostgreSQL 16 natif de production | binaire de test `pgserver` | même version majeure 16.2, mêmes extensions ; réglages `postgresql.conf` de production non éprouvés |
| MinIO / R2 réel | pas de conteneur | magasin S3 en mémoire qui conserve et relit les octets ; latence et erreurs réseau réelles non simulées |
| Chrome/Edge natifs, Firefox, Safari | un seul Chromium 153 | axe et parcours joués sur ce Chromium uniquement |
| 100 000 fichiers | volume disque et temps | bornes de session testées à 100 000 (`MAX_FICHIERS_PAR_SESSION`) mais pas un envoi réel de cette taille |
| Endurance multi-jours, fuites mémoire, dérive de latence | temps de campagne | aucune extrapolation |
| Coupure d'alimentation PENDANT une migration | non simulable | migration transactionnelle + dump de sécurité obligatoire avant montée de version |
| Restauration réelle de l'archive d'origine | RG13 (lecture seule) | inventaire d'empreintes + restauration des copies `--data` |
| OCR tesseract natif | binaire absent | `tesseract.js` (WASM) : comportement proche, **pas identique** |

## 6. Procédures d'exploitation mises à jour

`docs/verite_terrain/RUNBOOK_RESTAURATION.md` couvre désormais :

- §1 : le tableau de ce qui est sauvegardé inclut `--data` et les objets, avec
  la règle « une sauvegarde n'est utilisable que si `complet` vaut `true` » ;
- §1 bis : commande de sauvegarde **complète** de référence ;
- §2 (restauration coordonnée) : `--data-cible --restaurer-objets --coordonnee`,
  ordre imposé et vérification finale ;
- §2 ter : promotion des lots, signification exacte de `upload_status`
  (`uploaded`, `failed`, `not_configured`) et reprise par `fiches promouvoir` ;
- §2 quater : montée de version avec photographie + dump, contrôle `apres`,
  retour arrière `rollback` dans une base neuve.

## 7. Verdict

| Point du rapport | Verdict |
|---|---|
| Sauvegarde/restauration complète et vérifiable | **PRÊT** (produit + preuve d'exécution) |
| Promotion des lots vers le stockage objet | **PRÊT** (rejeu inclus) |
| Montée de version / retour arrière | **PRÊT** (double rollback prouvé) |
| Rotation PDF 90/180/270 | **PRÊT** |
| OCR navigateur en CI | **PRÊT** (invariants verrouillés, aucun retry) |
| Relais multipart (débordement, interruption, concurrence, panne) | **PRÊT** |
| Accessibilité axe sur tous les écrans | **PRÊT** (73 écrans, 6 rôles, 0 violation A/AA) |
| Stabilité de la suite e2e (ordre, animations, mode CI sans comptes nommés) | **PRÊT** (isolée à la source) |
| CI verte sur le SHA livré | **PRÊT** — `9c483e1`, 13/13 jobs `CI` + 3 autres workflows, `push` et `pull_request` |
| MinIO/R2 réel, Docker/Windows natif, 100k fichiers, endurance | **NON PROUVÉ** — voir §5 |

La mention « production ready » au sens du critère du commanditaire (« les tests
manuels/virtuels passent ») est atteinte **pour ce qui est simulable en bac à
sable**. Les lignes §5 restent à exécuter sur le poste cible avant un GO
définitif ; elles ne sont pas présentées comme acquises.
