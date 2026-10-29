# Rapport — passe finale de vérification indépendante (29/10/2026)

Objet : re-vérifier **sans rien reprendre du rapport du 28/10**, éprouver chaque
fonction du produit (nominal, bornes, entrée invalide, rôle non autorisé, état
incohérent), corriger ce qui est corrigeable ici, et dire précisément ce qui
n'est **pas** prouvable en bac à sable.

- Branche : `arena/a4dbb7f0-seamtech-search`
- SHA de code évalué : **`2340826`** (au-dessus de `f9b3235`, le SHA du rapport
  du 28/10), plus les commits de cette passe : `b38ee39`, `7eb70fb`, `dc0021a`,
  `2340826`.
- PR #42 : **non fusionnée, non modifiée dans son état** (toujours en brouillon).
- `main` : **jamais touchée**.

---

## 1. Environnement de la campagne (reconstruit de zéro le 29/10)

| Élément | Réalité |
|---|---|
| Python | 3.11.2, `venv` local, `requirements.txt` + `requirements-local.txt` (pytest 9.1.1, psycopg2 2.9.10) |
| PostgreSQL | **16.2 réel**, `initdb -E UTF8 --locale=C.UTF-8`, port 5432, trust |
| `unaccent` / `pg_trgm` | la distribution binaire du bac à sable **n'a pas `contrib`** : les deux `.so` ont été **compilés depuis les sources `REL_16_2`** (en-têtes générés à la main : `errcodes.h`, `nodetags.h`, `lwlocknames.h`, `fmgrprotos.h`, `pg_ts_dict_d.h`, `pg_type_d.h`). Vérifié en base : `unaccent('Lattée élève') = 'Lattee eleve'`, `similarity('chose','choses') = 0.625` |
| Redis | 6.2.14 (`redislite`), `127.0.0.1:6390` |
| Chromium | **153.0.8010.0** (`@sparticuz/chromium`) + **NSPR/NSS compilés** (`make` / `build.sh` gyp+ninja) |
| OCR | **shim `tesseract.js` (WASM)** en lieu et place du binaire natif — voir §7 |

Conséquence de méthode : **chaque chiffre ci-dessous a été reproduit ici**, pas
recopié du rapport du 28/10.

---

## 2. Re-vérification : les chiffres du rapport sont-ils réels ?

| Commande | Rapport 28/10 | Mesure du 29/10 (SHA `2340826`) |
|---|---|---|
| `ruff check .` | propre | **`All checks passed!`** |
| suite SQLite `-m "not postgres and not s3 and not perf and not redis_queue and not ocr_suite"` | 1060 passés | **1128 passés, 27 sautés** (104 s) |
| suite PostgreSQL `-m "postgres and not perf and not ocr_suite"` | 372 passés | **418 passés, 3 sautés** (217 s) |
| suite OCR `-m "ocr_suite and not postgres"` | 89 passés | **88 passés, 5 sautés** (19 s) |
| `node --experimental-strip-types --test lib/*.test.ts` | 10 passés | **10 passés** |
| `npx tsc --noEmit` | 0 erreur | **0 erreur** |
| `pnpm build` | rc=0 | **rc=0** |
| e2e mode CI (SQLite) | 49 passés / 63 sautés | **49 passés, 64 sautés, 0 échec** (1,6 min) |
| e2e « live » PostgreSQL + Redis + OCR | 110/1/1 puis 108/3 | **112 passés, 1 sauté, 0 échec** (4,8 min) |
| `pip-audit` (requirements) | aucune vulnérabilité | **aucune** |
| `pnpm audit --prod` | aucune vulnérabilité | **aucune** |
| axe-core (rapport machine) | 59 écrans → 73 | **73 écrans, 6 rôles, 0 violation A/AA** (`frontend/accessibilite/rapport-axe.json`) |
| Routes exposées | — | **114 routes** énumérées depuis la table de routage réelle |
| Tests collectés | 1633 | **1774** |

Les écarts à la hausse viennent des 8 modules ajoutés par cette passe (§4) ; les
écarts structurels (27 sautés en SQLite) sont *nommés*, pas masqués : ce sont
les tests PostgreSQL (sans `SEAMTECH_TEST_DATABASE_URL`) et le test OCR sans
binaire natif. En PostgreSQL, les 3 sautés sont nommés : poids E5 absent
(`SEAMTECH_ML_E5_DIR`), et deux tests de sauvegarde qui exigent un MinIO réel
(`SEAMTECH_S3_ENDPOINT_URL`) — ces deux derniers sont couverts par le job CI
`sauvegarde`, qui démarre MinIO.

### Les trois correctifs annoncés sont-ils réels ?

| Correctif annoncé | Contrôle effectué | Verdict |
|---|---|---|
| Doublon `<main>` | `grep -rn "<main" app components` → **une seule** balise, `app/layout.tsx:60` ; les 14 pages sont des `<div>` | **réel** |
| axe : faux positifs de contraste pendant le fondu | `e2e/accessibilite.spec.ts` : `FIGER_ANIMATIONS` injecté **avant** `analyze()`, reprise sur « execution context destroyed », `waitForLoadState("load")` + marge | **réel** |
| Variable d'environnement manquante en CI | sans `SEAMTECH_E2E_DATABASE_URL`, la spec de rôles fait un `test.skip` **explicite** ; l'assertion reste `violations === []`, aucun filtre par gravité ; les 14 écrans sont scannés par le compte de secours | **réel, pas un test affaibli** |

**Une affirmation n'était pas vérifiable et a été corrigée** : le docstring de
`tests/test_pdf_orientation.py` affirmait « ces tests remplacent les trois
`xfail` de la campagne précédente ». Le dépôt de base (`f1733c8`) ne contient
**aucun** marqueur `xfail` : la provenance est invérifiable. Le docstring dit
désormais ce qui est mesuré, sans revendiquer d'origine (commit `dc0021a`).
*Le comportement, lui, est bien prouvé : 39 tests, 0 `xfail`.*

---

## 3. Défauts trouvés par cette passe et corrigés

### 3.1 `POST /lots/{id}/annuler` renvoyait **500** sur un lot inconnu — **corrigé**

Trouvé par le balayage automatique de la table de routage (6 acteurs, 648
appels) : `DepotImpossible` levait jusqu'à la couche ASGI alors que
`GET /lots/{id}` répond 404 pour la même situation. Patch
`seamtech_search/fiches/routes.py` : `try/except DepotImpossible` →
`HTTPException(404)`. Vérifié : `POST /lots/1/annuler` → **404 `Lot #1 inconnu.`**
pour secours/admin/utilisateur/approbateur, **403** pour `lecture`, **401**
anonyme. Test permanent : `tests/test_passe_finale_matrice_roles.py`.

### 3.2 Le repli documenté de la recherche n'était pas honoré — **corrigé**

L'indexeur **promet** une dégradation gracieuse : si `CREATE EXTENSION
unaccent` est refusé (rôle non privilégié, base gérée), la recherche retombe sur
la configuration `simple`, l'application reste debout, la parité d'accents est
perdue. Le schéma métier est bien créé avec la configuration **résolue**.
Mais `seamtech_search/fiches/depot.py` écrivait
`to_tsvector('seamtech_unaccent', …)` **en dur** : sur une telle base, **chaque
dépôt de dossier échouait** en `UndefinedObject` et la fiche était perdue.

Mesure avant correctif, sur le PostgreSQL 16.2 du bac à sable **réellement privé
de contrib** : **74 échecs / 32 erreurs**, toutes en
`text search configuration "seamtech_unaccent" does not exist`.

Correctif (commit `2340826`) : `SearchIndex.config_recherche_texte()` expose la
configuration résolue ; la requête d'écriture la reçoit en **paramètre**
(`to_tsvector(%s::regconfig, …)`). Preuve : `tests/test_passe_finale_repli_recherche.py`
(4 tests) simule fidèlement l'échec de `CREATE EXTENSION` — le seul point qui
échoue — et vérifie (a) la configuration résolue, (b) la **colonne générée**
`chunk.tsv` et la **fonction PL/pgSQL** `rafraichir_texte_recherche_fiche`,
(c) un **dépôt de dossier réel de bout en bout** qui aboutit et dont le texte de
recherche est rempli.

### 3.3 Instabilités de tests (pas des défauts produit) — corrigées

- `e2e/validation.spec.ts:99` échouait **1 fois sur 2** : la décision était
  envoyée avant que l'écran ait lu la révision, qui refuse **à raison**
  (« Décision bloquée : la révision de la fiche n'a pas été lue »). Le test
  attend désormais `data-revision` — ce que fait déjà la fonction `ouvrirFiche`
  du même fichier. Preuve : 5 exécutions consécutives **5/5 vertes**.
- `ci.yml` : le garde-fou de l'étape « live E2E validation » attendait 13 tests ;
  le 3e test de `recherche-badge.spec.ts` (aller-retour validation →
  réouverture) porte le total à 14. Garde-fou mis à jour **avec** la
  composition documentée. Preuve : sous-ensemble exact rejoué ici →
  **14 passés, 0 sauté, 0 flaky** (`stats.expected = 14`).

---

## 4. Épreuve exhaustive par fonction — résultats

8 modules ajoutés, **141 tests**, tous sur PostgreSQL réel quand la couche
métier l'exige. Commande type :
`SEAMTECH_TEST_DATABASE_URL=… .venv/bin/python -m pytest tests/test_passe_finale_*.py -q`

| Fonction | Ce qui est réellement éprouvé | Tests | Verdict |
|---|---|---|---|
| **Dépôt par glisser-déposer** (`test_passe_finale_ingestion_dossiers.py`) | PDF valide / invalide / corrompu / vide, noms accentués et spéciaux, liens symboliques, dossier vide, arborescence très profonde (limite 32), extensions inconnues, classification `.pdf`→analysé / reste→stockable, tag accessoire en sous-type | 22 | **PRÊT** |
| **Recherche « type Google »** (`…recherche_variantes.py`) | dimension, matière, bateau, abréviations, tirets, casse, mots partiels, requête vide, requête sans résultat ; normalisation identique des deux côtés | 31 | **PRÊT** |
| **Confiance d'usage** (`…confiance_seuils.py`) | cas identique, écart de quelques cm, modèle hétérogène, **< 5 voiles comparables → `donnees_insuffisantes`**, **< 75 → `a_examiner`** (aucun verdict auto), IQR/médiane > 0,075 → `heterogene`, 7 dimensions | 13 | **PRÊT** |
| **Rôles / droits** (`…matrice_roles.py`) | balayage de **toutes** les routes (114) × 6 acteurs ; refus explicites ; approbateur seul décideur ; admin jamais décideur métier ; `lecture` ne mute rien ; téléchargement interdit hors approbateur ; surfaces techniques réservées à l'admin ; IDOR notification ; désactivation qui révoque la session ; mot de passe faux ; jeton de service seul | **36** | **PRÊT** |
| **Extraction PDF dégradée** (`…extraction_pdf.py`) | PDF chiffré, tronqué, vide, `.pdf` qui n'en est pas un, classeur corrompu, PDF scanné sans OCR (rien d'inventé) et avec OCR (terme retrouvé) ; **fichier source jamais modifié** (SHA-256 avant/après) | 9 | **PRÊT** |
| **Sessions** (`…sessions.py`) | expiration par inactivité, révocation, jeton falsifié, changement de rôle qui révoque, compte désactivé qui ne peut plus se connecter (message indistinguable d'un mot de passe faux) | 6 | **PRÊT** |
| **Comparaison / accessoires / notifications** (`…comparaison_accessoires.py`) | comparaison bloc par bloc : identique, écart de 3 cm mesuré au cm, modèle hétérogène, type différent, données partielles (aucun écart inventé), chevauchement insuffisant signalé, comparaison à soi-même refusée ; liaison accessoire 1:1 (ni les deux, ni aucun), refus explicites, extraction accessoire d'un PDF réel, notification déclenchée/lue/idempotente/invisible pour autrui | 20 | **PRÊT** |
| **Repli de recherche** (`…repli_recherche.py`) | cf. §3.2 | 4 | **PRÊT** |

### Le point « un rôle `lecture` peut appeler un POST » — tranché, ce n'est pas un défaut

`POST /fiches/doublons/scan` (défaut `appliquer=false`) et
`POST /validation/doublons` sont des POST **par la forme** (corps JSON
potentiellement long), pas par l'effet. Preuve mesurée : `count(*) FROM
fiche_lien` **inchangé** après appel par `lecture` et `utilisateur` ; le
`dry_run` reste `true` par défaut ; la seule variante qui **écrit**
(`appliquer=true`) renvoie **403** à `lecture`, `utilisateur`, `admin` et
`secours`, et **200** à l'approbateur nominatif. Aucune ligne écrite lors des
tentatives refusées.

### Refus mesurés (648 appels, matrice de rôles)

- Anonyme : **aucun 2xx** hors `/live`, `/ready` ; les 422 observés sont la
  validation de corps de FastAPI, rejouée avec une **charge valide** → 401 exigé.
- Décisions (`valider | rejeter | corriger | rouvrir | /validation/lot |
  /extractions-voile/*/corriger | /accessoires/extractions/*/lier |
  /approvals/*/decide`) : **403** pour admin/lecture/utilisateur/secours ;
  seul l'approbateur passe au-delà.
- `/pieces/{id}/telecharger` : **403** pour admin/lecture/utilisateur/secours.
- `/audit`, `/auth/utilisateurs`, `/ml/modeles`, `/maintenance/cleanup`,
  `/referentiel/bateaux/*/fusion` : **403** pour tout non-admin.
- Jeton de service **seul** : 200 sur les routes de lecture, **401/403** sur
  `/auth/utilisateurs`, `/audit`, `/recherche/journal` et **toutes** les
  mutations. Conséquence opérationnelle consignée : le port du backend ne doit
  jamais être exposé hors du réseau du frontal.
- Reste : `503 Redis not configured` sur `/maintenance/deadletters` et
  `/maintenance/replay-deadletters` — limite d'environnement, pas de défaut.

---

## 5. CI

`2340826` : **tous les contrôles verts** — `backend (3.11)`, `backend (3.12)`,
`backend (3.13)`, `frontend`, `docker`, `integration`, `e2e`, `sauvegarde`,
`hors-ligne-reel`, `recette-corpus-reel`, `recette-locale`, `ocr`,
`securite-dependances`, plus les workflows `qualification-native`,
`qualification-reserves` et `perf-comparaison`. Aucun `continue-on-error`.

---

## 6. Ce que cette passe N'A PAS couvert (à dire, pas à masquer)

Chacun de ces points reste **NON PROUVÉ EN SANDBOX — nécessite qualification en
conditions réelles**, avec sa procédure.

| Point | Pourquoi le bac à sable ne prouve rien | Procédure réelle exigée |
|---|---|---|
| **Windows 10/11 + Docker Desktop** | ici : Linux, conteneurs absents | install depuis `docker-compose.yml` sur un poste client, `scripts/preflight_windows.ps1`, dépôt d'un dossier réel, sauvegarde puis restauration |
| **PostgreSQL 16 natif de production** (rôle non superutilisateur, `contrib` présent ou absent) | ici : `pgserver` local, superutilisateur | dérouler `SEAMTECH_TEST_DATABASE_URL` + suite `-m postgres` sur l'instance cible, **avec** un rôle applicatif non superutilisateur : c'est le scénario du §3.2 |
| **MinIO / R2 réel** | `SEAMTECH_S3_ENDPOINT_URL` absent → 3 tests sautés | job CI `sauvegarde`, puis cible réelle : promotion d'un lot, coupure réseau pendant la promotion, reprise `fiches promouvoir` |
| **Chrome / Edge natifs** | Chromium 153 compilé/packagé, pas un navigateur installé | rejouer `frontend/e2e` sur le poste cible |
| **Volumétrie réelle (100 000 fichiers, 20 Gio)** | jeux synthétiques uniquement | déposer une année d'archives, mesurer p95 et l'occupation disque |
| **Endurance multi-jours** | aucun run > 8 min ici | laisser tourner 72 h (worker, files Redis, purge) |
| **Coupure d'alimentation pendant une migration** | non simulable ; les migrations PostgreSQL sont transactionnelles, mais aucune coupure n'est rejouée | couper l'alimentation sur la VM de recette pendant `migrer`, vérifier `diagnostiquer` puis `rollback` |
| **Restauration d'archive RG13** | dépend d'un support externe non fourni | restaurer une archive réelle sur un poste vierge |
| **Tesseract natif** | le bac à sable n'a **aucun** tesseract installable : un **shim `tesseract.js` (WASM)** imite l'interface (`IMAGE stdout -l fra [tsv]`). Le chemin de code produit est exercé pour de vrai (commande externe, stdout, TSV), mais **la qualité OCR n'est pas celle de tesseract natif** | installer `tesseract-ocr` + `tesseract-ocr-fra` sur la cible, rejouer `pytest -m ocr_suite` et la spec `scan-browser-ocr` |

---

## 7. Verdict

| Point | Verdict |
|---|---|
| Re-vérification indépendante des chiffres du 28/10 | **PRÊT** — reproduits, écarts expliqués |
| Correctif doublon `<main>` | **PRÊT** (une seule balise, vérifié) |
| Correctif axe pendant le fondu | **PRÊT** (73 écrans, 6 rôles, 0 violation) |
| Correctif variable CI manquante | **PRÊT** (skip explicite, assertion non affaiblie) |
| Dépôt / classement des dossiers | **PRÊT** |
| Extraction PDF (dont malformés, protégés, pivotés) | **PRÊT** |
| Écran d'approbation : correction, validation, refus | **PRÊT** |
| Recherche « type Google » | **PRÊT** |
| Comparaison entre voiles du même modèle | **PRÊT** |
| Confiance d'usage (seuils, données insuffisantes) | **PRÊT** |
| Accessoires (liaison 1:1, extraction PDF) | **PRÊT** |
| Rôles et droits (admin / lecture / utilisateur / approbateur) | **PRÊT** — 648 appels, refus explicites |
| Session et sécurité (déconnexion, expiration, révocation, cookie, IDOR, jeton de service) | **PRÊT** |
| Notifications (déclenchement, lecture, états, isolation) | **PRÊT** |
| `POST /lots/{id}/annuler` sur lot inconnu | **PRÊT** (500 → 404, corrigé) |
| Repli de recherche sans `unaccent` | **PRÊT** (dépôt réparé, 4 tests dédiés) |
| CI verte sur le SHA évalué | **PRÊT** — `2340826` |
| Windows/Docker Desktop, PG natif de prod, MinIO/R2, Chrome/Edge natifs, volumétrie, endurance, coupure d'alimentation, restauration RG13, tesseract natif | **NON PROUVÉ EN SANDBOX** — §6 |

**Aucun P0/P1 ouvert n'est connu à l'issue de cette passe sur le périmètre
testable.** Cela ne vaut **pas** quitus « production ready » global : les neuf
lignes du §6 restent à qualifier sur le poste cible, et la mention « production
ready » du rapport du 28/10 n'est valable que pour ce qui est simulable ici.
