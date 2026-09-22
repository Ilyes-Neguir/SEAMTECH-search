# Runbook de fusion dans `main` — consolidation du 22/09/2026

Ce document dit **comment amener le travail dans `main` sans perdre la CI**,
avec les commandes exactes, le résultat attendu à chaque étape, et la conduite
à tenir si le résultat ne correspond pas. Il a été vérifié par répétition
générale (clone jetable, aucune écriture sur origin) — sorties brutes en §4.

**L'acte de fusion appartient au commanditaire.** Ce runbook et la preuve, oui ;
la fusion, non (règle du projet : l'agent ne fusionne jamais, ne réécrit aucun
historique, ne force-push jamais).

## 0. État des lieux mesuré le 22/09

| Fait | Preuve |
|---|---|
| `origin/main` = `c69dd18` | `git ls-remote origin refs/heads/main` |
| Tête de pile arena = **7cf52dc** (consolidation du 22/09 : flake corrigé — run PUSH 35721372171 vert, garde-fous sandbox, ce runbook) | `git ls-remote origin refs/heads/arena/01a0c56d-seamtech-search` |
| 10 PR sur 11 (#10 → #19) sont **déjà entièrement contenues** dans la tête de pile | `git merge-base --is-ancestor <tête-PR> <tête-pile>` → « Already up to date » au merge |
| PR #9 (`fix/test-normcase-basetemp`) est la seule hors pile ; son contenu (normcase dans `tests/test_reindex_skip.py`) est **déjà dans la pile** — le conflit au merge est un conflit de COMMENTAIRE (les deux branches réécrivent le même commentaire) | `gh pr view 9 --json files` = 1 fichier, +8/−1 ; pile : `os.path.normcase` déjà aux lignes 48-51 |
| ☠️ Fusionner `lot-d/interface-5-ecrans` SEUL dans `main` **supprime `.github/workflows/` en entier** (il ne contient que `ci.yml`, absent de lot-d) → plus aucune CI sur main | vérifié en clone jetable : `git merge lot-d` puis `find .github -type f` → vide |

## 1. Option A — UN SEUL merge de la tête de pile (recommandée, prouvée)

```bash
git fetch origin
git checkout main && git pull --ff-only origin main
# La tête de pile du jour :
TETE=$(git ls-remote origin refs/heads/arena/01a0c56d-seamtech-search | cut -f1)
git merge --no-ff "$TETE" -m "Consolidation Phase 1 → Lot F : pile complète"
```

- **Attendu** : 0 conflit (prouvé en répétition générale §4), ~88 fichiers
  ajoutés, un seul `.github/workflows/ci.yml`.
- **Si conflit** : la seule source connue de conflit est le commentaire de
  `tests/test_reindex_skip.py` (contenu de #9 vs pile) — garder la version de
  la pile (`os.path.normcase` + commentaire). Tout autre conflit = arrêt et
  vérification, rien ne doit être résolu « au jugé ».
- Pousser : `git push origin main`.
- **Contrôles post-fusion** (dans l'ordre) :
  1. `find .github -type f` → exactement `.github/workflows/ci.yml` ;
  2. `git ls-files | wc -l` → **259** (mesuré le 22/09 sur la tête de
     consolidation : 167 sur main + 92 ; la mesure intermédiaire à 64e090c
     était 255 — quatre fichiers de consolidation sont venus depuis) ;
  3. le run **push** sur main doit être VERT — c'est lui qui protège la
     branche (un run pull_request vert ne suffit pas : le run push 35715779367
     était rouge alors que le pull_request du même SHA était vert) ;
  4. `pytest -q -m "not postgres"` → 487 passés / 3 sautés (mesuré, filtre
     cité) ; avec PostgreSQL : `-m "postgres and not perf"` → 113/0 en CI
     (poids e5 présents).

Puis fermer les PR devenues sans objet, **dans l'ordre #19 → #10** (du plus
récent au plus ancien, pour que chaque fermeture ne laisse pas de chaîne
pendante), chacune avec le commentaire :
`Contenu intégré dans main par le merge de consolidation <sha-du-merge> —
l'historique des commits reste complet.`

```bash
for n in 19 18 17 16 15 14 13 12 11 10; do
  gh pr close "$n" --comment "Contenu intégré dans main par le merge de consolidation du 22/09 — historique des commits conservé."
done
```

Cas particulier **#14** (`main ← lot-a/schema-metier`) : après le merge de la
pile, sa tête est déjà ancêtre de main → la PR apparaît « Already up to date »
/ non mergeable : simple fermeture, c'est un no-op.
Cas particulier **#9** : son correctif (normcase) est déjà dans la pile —
vérifier `gh pr view 9 --json files` (1 fichier : `tests/test_reindex_skip.py`)
puis fermer avec :
`Correctif déjà intégré par la pile de consolidation (os.path.normcase,
tests/test_reindex_skip.py l.48-51) — fermé sans merge.`

## 2. Option B — par lot (si la traçabilité « une PR = un lot » est exigée)

Ordre topologique EXACT (chaque PR cible la précédente ; chaque fusion rend la
suivante « Already up to date » ou quasi) :

```
#10 → #11 → #12 → #13 → #15 → #16 → #17 → #18 → #19 → #20, puis #14 (no-op), puis #9 (fermeture seule).
```

- À chaque fusion : vérifier que le run **push** sur la branche cible passe.
- Le conflit de commentaire de #9 (si on tente de la fusionner plutôt que de la
  fermer) se résout en gardant la version pile — 30 secondes, mais il faut le
  prévoir.
- ⚠️ La tête de `lot-d/interface-5-ecrans` (base de #19) doit rester la
  DERNIÈRE chaîne fusionnée via #19/#20 : fusionner lot-d seul supprime la CI
  de main (§0). Si le cherry-pick `bf8b98b` (réparation ci.yml de lot-d) a été
  fait avant, ce risque disparaît — mais l'ordre ci-dessus reste valable.

## 3. Nettoyage post-fusion (branche dédiée, jamais un commit direct sur main)

Après la fusion, `main` contient le PDF client **en double** : `7792-SO_ffab.pdf`
à la racine (venu de l'historique de main, « Add files via upload ») et
`sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf` (copie canonique venue de
la pile). Les deux sont byte-identiques (sha256 `43afc51e55ae…`, 166 990 o —
mesuré le 22/09). La copie canonique est `sample_data/` : c'est elle que le
pipeline, les fixtures de tests et les empreintes épinglées
(`tests/test_empreintes_fixtures.py`) référencent.

```bash
git checkout main && git pull --ff-only
git checkout -b nettoyage/doublon-pdf-racine
git rm 7792-SO_ffab.pdf
git commit -m "Nettoyage : suppression du doublon 7792-SO_ffab.pdf à la racine

La copie canonique est sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf
(byte-identique, sha256 43afc51e55ae…, 166 990 o) : c'est elle que le
pipeline, les e2e live et tests/test_empreintes_fixtures.py référencent.
La copie racine venait d'un upload direct dans l'historique de main."
git push origin nettoyage/doublon-pdf-racine
gh pr create --base main --head nettoyage/doublon-pdf-racine \
  --title "Nettoyage : doublon du PDF client à la racine" \
  --body "Suppression de la copie non canonique ; la copie sample_data/ reste."
```

**Règle des documents clients dans Git** (tranchée le 22/09) : l'archive est
la source de vérité (RG13) ; un document client ne se versionne PAS. Les
documents présents dans `sample_data/` et `frontend/e2e/live-fixtures/` sont
des échantillons de travail nécessaires aux tests ; le dépôt doit passer
**privé** (côté commanditaire) — tant qu'il est public, la fiche client réelle
et le nom du client sont exposés. À terme : remplacer la fiche réelle par un
échantillon anonymisé et retirer les documents clients du dépôt.

## 4. Répétition générale du 22/09 — sorties brutes

**Répétition n°2 (tête de consolidation, état final du lot)** — clone jetable
depuis le vrai remote, AUCUNE écriture sur origin :

```
$ git clone --quiet https://github.com/Ilyes-Neguir/SEAMTECH-search /tmp/fusion-repetition
$ git remote add local <dépôt de travail> && git fetch local arena/01a0c56d-seamtech-search
$ git checkout main && git log --oneline -1
c69dd18 Add files via upload
$ git ls-files | wc -l
167                                        # fichiers sur main avant fusion
$ git merge --no-ff local/arena/01a0c56d-seamtech-search -m "REPETITION GENERALE 2"
Merge made by the 'ort' strategy.          # 0 conflit
$ git ls-files | wc -l
259                                        # fichiers après fusion (167 + 92)
$ git diff --diff-filter=D --name-only HEAD^1 HEAD | wc -l
0                                          # aucune suppression
$ find .github -type f
.github/workflows/ci.yml                   # exactement UN workflow, au bon endroit
```

**Répétition n°1 (état 64e090c, pré-consolidation)** — mêmes conclusions :
0 conflit, 167 → **255** fichiers, un seul ci.yml, doublon PDF racine
byte-identique à la copie `sample_data/` (sha256 `43afc51e55ae…`, 166 990 o) :

Suites exécutées SUR LE RÉSULTAT FUSIONNÉ (PostgreSQL 16.2 + pgvector +
pg_trgm + unaccent, SEAMTECH_TEST_DATABASE_URL positionné) :

```
$ pytest -q -m "not postgres"
483 passed, 3 skipped, 114 deselected in 32.09s     # état 64e090c (pré-perf)
$ pytest -q -m postgres
113 passed, 1 skipped, 486 deselected in 25.69s     # le saut = test e5 sans poids hors CI
```

(La tête réelle de fusion porte en plus les tests du lot de consolidation :
empreintes des fixtures ×4 et la séparation perf — comptes à re-mesurer sur la
tête du jour avec les mêmes commandes, chaque compte publié avec son filtre.)

## 5. Garde-fous à respecter le jour de la fusion

1. Lancer `scripts/etat_sandbox.sh` : HEAD doit être sur origin ou son
   descendant (le sandbox a déjà réinitialisé un historique local — la vérité
   est sur origin).
2. Vérifier que le DERNIER run **push** de la tête à fusionner est vert
   (`gh run list --branch <branche>` : un run push compte pour la protection
   de branche, un run pull_request seul ne prouve rien).
3. Ne jamais fusionner `lot-d/interface-5-ecrans` seul (§0).
4. Après fusion : contrôles post-fusion §1, puis le run push sur main doit
   être vert.
