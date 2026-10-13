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

## Perf — **PARTIEL** (produit 100 ms tenu sur HEAD CI ; régression a3506c8 prouvée)

Critère produit p50 < 100 ms et p95 < 100 ms **non desserré**. Seuil CI 250 ms
étiqueté, test non retiré.

**Même runner, même PG, 3 runs** — job
[Perf comparaison 38426744727](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38426744727)
(médianes p50 / p95) :

| SHA | 50 req. | fonds réel 7792-SO | dimension | 1 500 |
|---|---|---|---|---|
| `8dd5ae7` | 5,4 / 6,4 | **4,7 / 5,5** | 4,7 / 7,9 | 9,0 / 26,7 |
| `a3506c8` | 4,4 / 210 | **203 / 212** (×43 sur p50) | 4,7 / 255 | 16,5 / 242 |

L'échec a3506c8 **n'est pas** un bruit de runner : sur le fonds réel le p50
passe de 4,7 ms à 203 ms, trois fois. Les autres jeux gardent un p50 bas et
un p95 ~210–250 ms (queue). Alignement SQL/index 023 poussé en `51d2772`.

HEAD CI backend 3.12 `ee776d0` run 38422805119 : p50 5–14 ms, p95 7–35 ms
(**sous 100 ms** sur ce run, pas un PASS atelier).

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
