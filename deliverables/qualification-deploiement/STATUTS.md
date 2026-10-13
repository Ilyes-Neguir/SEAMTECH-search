# Statuts de clôture A–D

Les quatre points **ne sont pas tous corrigés**. PR #37 **non fusionnée**.
Aucun PV humain. Recette historique intacte. Pas un PASS production.

## Identité

| | |
|---|---|
| Départ | `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09` |
| Lot 3 | `0665a684133f31e5c5742faeeb1fe3104ab2a721` (CI A–C en échec) |
| SHA applicatif **précédemment** testé | `a3506c84892e97a8993fe6a9657cc93019f002d3` |
| Recette historique | préservée |

Les preuves `a3506c8` restent valables **pour ce SHA**. Tout SHA ultérieur
(requête trigrammes, procédure native) doit être **rejoué** : un run vert
sur `a3506c8` ne qualifie pas le SHA suivant.

## A — Recherche — **CORRIGÉ ET TESTÉ** (`a3506c8`) / **EN ATTENTE HUMAINE**

1. **Modifié** : clé `normaliser_cle_recherche` distincte de `sans_accents`.
2. **Exécuté** : postgres `279/0` ; qualification-reserves `74/0`.
3. **Périmètre** : corpus synthétique. Volumes 800/2000 **indicatifs**.
4. **Perf** : **non clos** (voir ci-dessous). La recherche fonctionnelle
   n’est pas la qualification de latence.

## Perf — **OUVERT**

Critère **produit** : p50 < 100 ms (architecture, sans tolérance) et
p95 < 100 ms. Seuil CI `SEAMTECH_PERF_P95_CI_MS=250` **étiqueté**, jamais
un critère desserré. Ne pas relever 250 ms, ne pas retirer le test, ne
pas neutraliser le garde-fou.

Mesures **CI backend 3.12, même workflow, machines différentes** (ce n’est
**pas** encore la comparaison à conditions identiques) :

| SHA | 50 req. synth. | fonds réel 7792-SO | dimension | 1 500 fiches |
|---|---|---|---|---|
| `8dd5ae7` run 38267250173 | p50 8,7 / p95 11,0 | p50 8,9 / p95 10,0 | p50 8,0 / p95 13,9 | p50 13,9 / p95 46,9 |
| `a3506c8` run 38405546959 | p95 280,9 ≥ 250 | **p50 275,2 ≥ 100** | p95 326,7 ≥ 250 | p95 324,4 ≥ 250 |

Le motif documenté du runner (p50 bas, p95 haut, run 35720563422) **ne
correspond pas** à un p50 fonds réel à 275 ms. Attribution « charge du
runner » **refusée** tant que 8dd5ae7 et a3506c8 n’ont pas été mesurés
sur le **même** runner, mêmes données/index, N runs. Job
`Perf comparaison` ajouté pour cela. Alignement SQL trigrammes / index
023 (`coalesce` hors expression d’index) poussé sur HEAD : à **retester**
sur le nouveau SHA.

## B — Sauvegarde — **CORRIGÉ ET TESTÉ** (`a3506c8`, MinIO officiel CI)

Chemin exercé : dump PG + fichiers locaux + objets MinIO, restauration
vers **cibles distinctes**, sources rendues indisponibles, comparaison
SHA-256. B-03 **collecté et exécuté** (skip=0), notice `…_obj`.
Pas R2, pas le bucket atelier.

## C — OCR — **CORRIGÉ ET TESTÉ** (`a3506c8`) pour le chemin **exercé**

Chemin CI qualification-reserves : **crawl → upsert → GET /search → POST /open**.
Ce n’est **pas** l’import navigateur (`POST /imports/upload` + file worker
+ écran). C-03/C-04 dans les 74 tests, skip=0. Job `ocr` = extracteurs
seuls (pas PostgreSQL). Sandbox local sans tesseract : **BLOQUÉ**.

## D — Déploiement — **PRÉPARÉ NON EXÉCUTÉ**

Procédure : `deliverables/qualification-deploiement/PROCEDURE_NATIVE.md`
et `scripts/qualification_native.sh` (Debian/Ubuntu, processus hôtes,
MinIO officiel compilé, OCR fra+eng). L’exécution visée est le job
`Qualification native` sur `ubuntu-24.04` (machine vierge autorisée).
Tant que ce job n’a pas un rapport `CONTROLE|*|PASS` sans FAIL, D reste
non exécuté. `recette-locale` / `integration` Docker **≠ D**.

**EN ATTENTE DE VALIDATION HUMAINE.**
