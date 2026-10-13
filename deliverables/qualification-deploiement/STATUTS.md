# Statuts de clôture A–D

Les quatre points **ne sont pas tous un PASS production**. PR #37 **non
fusionnée**. Aucun PV humain. Recette historique intacte.

## Identité

| | |
|---|---|
| Départ | `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09` |
| SHA applicatif recherche (SQL 023 aligné index) | `51d277217a02d5af2488260e281eadcc40ad124c` |
| SHA **perf CI 100 ms** (même `recherche.py`) | `ee776d0218489074a9449b94ebd1714bbfa4e034` |
| SHA **essai natif PASS** | `6ccc383` (HEAD de cette branche au moment du job) |
| Recette historique | préservée |

Un run vert sur un SHA ne qualifie pas un SHA suivant si le code métier change.

## A — Recherche — **CORRIGÉ ET TESTÉ** / **EN ATTENTE HUMAINE**

Corpus synthétique. Postgres 279/0. Qualification-reserves 74/0 sur `a3506c8`
(fonctionnel). Volumes 800/2000 indicatifs.

## Perf — **PARTIEL**

Critère produit p50 < 100 ms et p95 < 100 ms **non desserré**. Seuil CI 250 ms
étiqueté, test non retiré.

| SHA | Job | 50 req. | fonds réel | dimension | 1 500 |
|---|---|---|---|---|---|
| `8dd5ae7` | CI 38267250173 backend 3.12 | p50 8,7 / p95 11,0 | 8,9 / 10,0 | 8,0 / 13,9 | 13,9 / 46,9 |
| `a3506c8` | CI 38405546959 backend 3.12 **FAIL** | p95 280,9 | **p50 275,2** | p95 326,7 | p95 324,4 |
| `ee776d0` | CI 38422805119 backend 3.12 **SUCCESS** | p50 5,0 / p95 7,0 | 6,1 / 6,3 | 6,0 / 11,1 | 13,7 / 34,9 |

Sur `ee776d0`, p50 et p95 sont **sous 100 ms** (critère produit tenu **sur ce
run CI**, pas un PASS atelier). Ce n'est **pas** encore la comparaison
8dd5ae7 vs a3506c8 vs HEAD sur **un même** runner (job `Perf comparaison`
en cours de correction). Le motif « p50 bas / p95 haut » du runner **ne
décrit pas** l'échec a3506c8 (p50 275 ms).

## B — Sauvegarde — **CORRIGÉ ET TESTÉ** (`a3506c8` CI + essai natif)

Restore conjoint PG + fichiers + objets, sources indisponibles, SHA-256.
B-03 exécuté skip=0. Essai natif : `restaurer` vers une base **créée par
le CLI** (cible distincte). Pas R2 / pas bucket atelier.

## C — OCR — **CORRIGÉ ET TESTÉ** pour les chemins exercés

- CI qualification : **crawl → upsert → GET /search → POST /open** (pas
  l'import navigateur).
- Essai natif `6ccc383` : tesseract **hôte** fra+eng (`ocr-tesseract-hote`)
  + crawl d'index ; import **worker** = lot 7792-SO (`/imports/dossier/lot`),
  pas un scan OCR via ce lot.

## D — Déploiement — **CORRIGÉ ET TESTÉ** (essai) / **EN ATTENTE HUMAINE**

Exécuté sur runner vierge `ubuntu-24.04` (machine jetable autorisée, pas le
PC opérateur, pas Docker Compose) : job
[Qualification native 38425712093](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38425712093)
SHA `6ccc383`, notice `native-summary` : tous les contrôles PASS.
Procédure : `scripts/qualification_native.sh`. **Pas** une homologation
production.

**EN ATTENTE DE VALIDATION HUMAINE.**
