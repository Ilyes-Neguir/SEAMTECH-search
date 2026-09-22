# Runbook de fusion dans `main` — re-mesuré le 22/09/2026

**Fusion et fermetures réservées au commanditaire.** Aucun push sur `main`,
aucune fusion distante, aucune fermeture ou création de PR n'est exécuté par
ce runbook. Les commandes opérateur ci-dessous sont une préparation, pas une
preuve de leur exécution. Aucun force-push.

## 0. État vérifié et limites

Mesures du 22/09/2026 UTC, sorties brutes en §4 (commande puis sortie ;
`EXIT=n` est le code de retour capturé, pas du texte émis par Git).

- Pile `arena/01a0c56d-seamtech-search` : **fc4f99eb123413604b92c43b20d6bae418fbfc8e**,
  commit de fusion de la PR #21. Run **push 35751552283 : success, 8/8**
  (statut individuel en §4). Ce run ne prouve pas la CI d'un futur commit documentaire.
- `main` : **c69dd1811fc671d6d3a93d5bbcbdadcc98c5cb43**, **167 fichiers**,
  **5 sections** de CHANGELOG (`## `).
- **Écart signalé : 52 commits côté pile et 1 côté main**, selon
  `git rev-list --left-right --count origin/main...origin/arena/01a0c56d-seamtech-search`.
  Le chiffre annoncé de 41 n'est pas reproduit (49 côté pile en excluant les merges).
- **12 PR ouvertes**, #9 à #20 : **11 contenues (#10 à #20)** ; #9 hors chaîne.
  Dans `tests/test_reindex_skip.py`, la différence #9/pile est uniquement un
  commentaire ; `os.path.normcase` est présent ligne 51. Ne pas fusionner ces PR.
- Le seul fichier de `main` absent de la pile est **7792-SO_ffab.pdf**,
  **166 990 octets**. Son maintien après fusion est **attendu**. Empreinte identique
  à `sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf` (preuve en §4).
- Dépôt **public** (`isPrivate: false`) : les deux copies du document client
  sont publiques. Supprimer ultérieurement une copie ne retire pas son historique.
- Piège confirmé séparément : fusionner **lot-d seul** laisse `.github` absent.
  Seule la pile complète fait l'objet de la validation ci-dessous.

**Périmètre de livraison :** la session est fixée à
`arena/01a0c9eb-seamtech-search`. Les modifications documentaires sont livrées
sur cette branche, pas sur la branche de base. La répétition porte sur la pile
**fc4f99e**, sans ces modifications. Toute nouvelle tête devra être revalidée.
Aucune PR de consolidation n'est ouverte par cette session ; son run
`pull_request` est donc **NON PROUVÉ**. La CI du commit documentaire est à lire
sur son propre SHA, jamais à déduire du run de la pile.

## 1. Procédure réservée au commanditaire — pile complète uniquement

Préconditions : récupérer l'historique complet, vérifier la tête distante,
refaire les mesures si elle diffère de fc4f99e, et consulter la CI de ce SHA.
Si GitHub est inaccessible : arrêt. Si push refusé : arrêt, aucun force-push.
Si conflit : arrêt et analyse, aucune résolution automatique « au jugé ».

```bash
# NON EXÉCUTÉ par l'agent : dans le checkout du commanditaire uniquement.
git fetch origin --prune
TETE=$(git ls-remote origin refs/heads/arena/01a0c56d-seamtech-search | cut -f1)
test "$TETE" = fc4f99eb123413604b92c43b20d6bae418fbfc8e || exit 1
git checkout main
git pull --ff-only origin main
test "$(git rev-parse HEAD)" = c69dd1811fc671d6d3a93d5bbcbdadcc98c5cb43 || exit 1
git merge --no-ff --no-commit "$TETE"
# Vérifier avant de committer :
git diff --name-only --diff-filter=U
git ls-files | wc -l
find .github -type f
git diff --cached --diff-filter=D --name-only HEAD
git diff --cached --shortstat HEAD
grep -c '^## ' CHANGELOG.md
```

Attendus **mesurés sur fc4f99e** : 0 conflit ; **272 fichiers** ; exactement
`.github/workflows/ci.yml` ; aucune suppression ; **128 fichiers modifiés,
+22 953 / −28** ; **23 sections** du CHANGELOG et aucune section de main perdue.
Le contrôle intégral des anciennes sections est donné en §4.

```bash
# NON EXÉCUTÉ ici : après validation et décision explicite du commanditaire.
git commit -m "Consolidation Phase 0 → Lot H.1 + intégration vérité 74 cibles"
pytest -q -m "not postgres"
# Attendu mesuré : 510 passed, 3 skipped (123 deselected).
# Ne pousser que si les contrôles sont bons :
git push origin main
# Arrêter immédiatement si refusé. Attendre ensuite les 8 jobs du run push
# de CE SHA ; un ancien run vert ou un run pull_request ne le remplace pas.
```

## 2. PR à fermer après consolidation — commandes NON EXÉCUTÉES

Pour chaque ligne, la commande est `git merge-base --is-ancestor origin/<branche>
origin/arena/01a0c56d-seamtech-search` : code 0 = oui, code 1 = non ; aucune
sortie texte n'est normale. Les commandes complètes et codes sont en §4.

| PR | Branche | Contenue / commande | Action après consolidation |
|---|---|---|---|
| #9 | `fix/test-normcase-basetemp` | non (exit 1) — `git merge-base --is-ancestor origin/fix/test-normcase-basetemp origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #10 | `phase0/inventaire-banc-essai` | oui (exit 0) — `git merge-base --is-ancestor origin/phase0/inventaire-banc-essai origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #11 | `phase0/fix-classement-fiches` | oui (exit 0) — `git merge-base --is-ancestor origin/phase0/fix-classement-fiches origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #12 | `phase0/preparation-lot-b` | oui (exit 0) — `git merge-base --is-ancestor origin/phase0/preparation-lot-b origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #13 | `lot-a/fix-privileges-et-lexique` | oui (exit 0) — `git merge-base --is-ancestor origin/lot-a/fix-privileges-et-lexique origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #14 | `lot-a/schema-metier` | oui (exit 0) — `git merge-base --is-ancestor origin/lot-a/schema-metier origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #15 | `lot-b/extraction-fiche` | oui (exit 0) — `git merge-base --is-ancestor origin/lot-b/extraction-fiche origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #16 | `lot-b2/endpoints-tracabilite` | oui (exit 0) — `git merge-base --is-ancestor origin/lot-b2/endpoints-tracabilite origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #17 | `lot-c/ingestion-files` | oui (exit 0) — `git merge-base --is-ancestor origin/lot-c/ingestion-files origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #18 | `fix/tache1-pip-audit-deps-declarees` | oui (exit 0) — `git merge-base --is-ancestor origin/fix/tache1-pip-audit-deps-declarees origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #19 | `lot-d/interface-5-ecrans` | oui (exit 0) — `git merge-base --is-ancestor origin/lot-d/interface-5-ecrans origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |
| #20 | `arena/01a0c56d-seamtech-search` | oui (exit 0) — `git merge-base --is-ancestor origin/arena/01a0c56d-seamtech-search origin/arena/01a0c56d-seamtech-search` | fermeture si encore ouverte |

**#10, #14 et #9 ciblent main.** GitHub peut marquer automatiquement comme
« merged » une PR dont les commits deviennent ancêtres de main : c'est
applicable à #10 et #14. **Attention à #9 : sa tête n'est pas ancêtre de la
pile ; la fusion de fc4f99e seule ne la rend pas ancêtre de main.** Son correctif
équivalent n'est pas une preuve d'ascendance. Relever l'état de ces trois PR
après fusion ; fermer seulement celles encore ouvertes, sans les fusionner.

Remplacer `<sha-du-merge>` par le SHA réel de consolidation validé. Ordre :
**#19 → #10, puis #20, puis #9**. Chaque commande est à omettre si la PR
n'est plus ouverte. Ce bloc n'a PAS été exécuté :

```bash
gh pr close 19 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 18 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 17 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 16 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 15 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 14 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 13 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 12 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 11 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 10 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 20 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
gh pr close 9 --comment "Contenu intégré dans main par le merge de consolidation <sha-du-merge> — l'historique des commits reste complet."
```

## 3. Suite à décider par le commanditaire

- Ouvrir éventuellement la PR base → main, titre :
  **Consolidation Phase 0 → Lot H.1 + intégration vérité 74 cibles**.
  Corps : pile complète ; répétition sur fc4f99e (0 conflit, 272 fichiers,
  510/3) ; **« fusion à décider par le commanditaire »**. Joindre ensuite les
  statuts par job du run `pull_request` testant le résultat de fusion.
- Après fusion et CI push verte, fermer les PR encore ouvertes selon §2.
- Traiter séparément la confidentialité des documents clients et le doublon
  racine ; aucun retrait de fichier ni changement de visibilité dans cette mission.

## 4. Preuves brutes — répétition du 22/09/2026 sur fc4f99e

### Préparation de l'historique

Le checkout initial était superficiel et son refspec ne récupérait que main.
Les premiers tests avec refs absentes (exit 128), puis sur historique tronqué,
ne constituaient PAS des preuves d'ascendance. Historique complété par
`git fetch origin 'refs/heads/*:refs/remotes/origin/*' --prune` puis
`git fetch --unshallow origin 'refs/heads/*:refs/remotes/origin/*'` avant les
mesures valides suivantes. Aucun push par ces commandes.

### État distant, ascendance, PDF et CI de la pile

```text
$ date -u +%FT%TZ
2026-09-22T16:22:06Z
EXIT=0
$ git fetch origin --prune
EXIT=0
$ git ls-remote --heads origin | grep -E "01a0c56d|main"
fc4f99eb123413604b92c43b20d6bae418fbfc8e	refs/heads/arena/01a0c56d-seamtech-search
c69dd1811fc671d6d3a93d5bbcbdadcc98c5cb43	refs/heads/main
EXIT=0
$ gh pr list --state open --limit 100 --json number,headRefName,baseRefName
[{"baseRefName":"lot-d/interface-5-ecrans","headRefName":"arena/01a0c56d-seamtech-search","number":20},{"baseRefName":"fix/tache1-pip-audit-deps-declarees","headRefName":"lot-d/interface-5-ecrans","number":19},{"baseRefName":"lot-c/ingestion-files","headRefName":"fix/tache1-pip-audit-deps-declarees","number":18},{"baseRefName":"lot-b2/endpoints-tracabilite","headRefName":"lot-c/ingestion-files","number":17},{"baseRefName":"lot-b/extraction-fiche","headRefName":"lot-b2/endpoints-tracabilite","number":16},{"baseRefName":"lot-a/fix-privileges-et-lexique","headRefName":"lot-b/extraction-fiche","number":15},{"baseRefName":"main","headRefName":"lot-a/schema-metier","number":14},{"baseRefName":"phase0/preparation-lot-b","headRefName":"lot-a/fix-privileges-et-lexique","number":13},{"baseRefName":"phase0/fix-classement-fiches","headRefName":"phase0/preparation-lot-b","number":12},{"baseRefName":"phase0/inventaire-banc-essai","headRefName":"phase0/fix-classement-fiches","number":11},{"baseRefName":"main","headRefName":"phase0/inventaire-banc-essai","number":10},{"baseRefName":"main","headRefName":"fix/test-normcase-basetemp","number":9}]
EXIT=0
$ git merge-base --is-ancestor origin/fix/test-normcase-basetemp origin/arena/01a0c56d-seamtech-search
EXIT=1
$ git merge-base --is-ancestor origin/phase0/inventaire-banc-essai origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/phase0/fix-classement-fiches origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/phase0/preparation-lot-b origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/lot-a/fix-privileges-et-lexique origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/lot-a/schema-metier origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/lot-b/extraction-fiche origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/lot-b2/endpoints-tracabilite origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/lot-c/ingestion-files origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/fix/tache1-pip-audit-deps-declarees origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/lot-d/interface-5-ecrans origin/arena/01a0c56d-seamtech-search
EXIT=0
$ git merge-base --is-ancestor origin/arena/01a0c56d-seamtech-search origin/arena/01a0c56d-seamtech-search
EXIT=0
$ gh repo view --json isPrivate
{"isPrivate":false}
EXIT=0
$ gh pr view 21 --json state,mergedAt,mergeCommit
{"mergeCommit":{"oid":"fc4f99eb123413604b92c43b20d6bae418fbfc8e"},"mergedAt":"2026-09-22T16:03:35Z","state":"MERGED"}
EXIT=0
$ gh run view 35751552283 --json headSha,event,status,conclusion,jobs --jq '{headSha,event,status,conclusion,jobs:[.jobs[]|{name,status,conclusion}]}'
{"conclusion":"success","event":"push","headSha":"fc4f99eb123413604b92c43b20d6bae418fbfc8e","jobs":[{"conclusion":"success","name":"frontend","status":"completed"},{"conclusion":"success","name":"backend (3.11)","status":"completed"},{"conclusion":"success","name":"docker","status":"completed"},{"conclusion":"success","name":"e2e","status":"completed"},{"conclusion":"success","name":"backend (3.12)","status":"completed"},{"conclusion":"success","name":"integration","status":"completed"},{"conclusion":"success","name":"sauvegarde","status":"completed"},{"conclusion":"success","name":"backend (3.13)","status":"completed"}],"status":"completed"}
EXIT=0
$ git ls-tree -r --name-only origin/main | wc -l
167
EXIT=0
$ git rev-list --count origin/main..origin/arena/01a0c56d-seamtech-search
52
EXIT=0
$ git show origin/main:CHANGELOG.md | grep '^## '
## 0.5.0 — Remediation (audited commit b7be72a → fixes)
## 0.4.0 — Decoupled Cloud-Native (pre-audit, aspirational)
## 0.3.0 — Import workflow
## 0.2.0 — Search & crawling
## 0.1.0 — Init
EXIT=0
$ bash -c 'comm -23 <(git ls-tree -r --name-only origin/main | sort) <(git ls-tree -r --name-only origin/arena/01a0c56d-seamtech-search | sort)'
7792-SO_ffab.pdf
EXIT=0
$ git cat-file -s origin/main:7792-SO_ffab.pdf
166990
EXIT=0
$ git show origin/main:7792-SO_ffab.pdf | sha256sum
43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40  -
EXIT=0
$ git show origin/arena/01a0c56d-seamtech-search:sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf | sha256sum
43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40  -
EXIT=0
$ git diff origin/fix/test-normcase-basetemp origin/arena/01a0c56d-seamtech-search -- tests/test_reindex_skip.py
diff --git a/tests/test_reindex_skip.py b/tests/test_reindex_skip.py
index 4696900..be415d2 100644
--- a/tests/test_reindex_skip.py
+++ b/tests/test_reindex_skip.py
@@ -45,12 +45,9 @@ def test_unchanged_file_skips_extraction(tmp_path: Path, monkeypatch: pytest.Mon
     # Second pass with metadata reflecting what's now stored: same size/mtime/version.
     stat = (root / "reference.txt").stat()
     existing = {
-        # Cette clé reproduit VOLONTAIREMENT la fonction de clé du produit :
-        # seamtech_search/crawler.py:154 (os.path.normcase(str(path.resolve())))
-        # et seamtech_search/models.py:56 — identité sous Linux, minuscules sous
-        # Windows. Si vous changez la fonction de clé du crawler, changez ICI
-        # aussi (c'est le test qui la suit) — un .lower() sur le chemin complet
-        # ferait échouer la suite dès qu'un workspace contient une majuscule.
+        # même normalisation que le crawler (os.path.normcase : no-op sur
+        # Linux, .lower() casserait la clé dès qu'un segment du basetemp
+        # contient une majuscule — ex. .pytest_tmpD1)
         os.path.normcase(str((root / "reference.txt").resolve())): (
             stat.st_size,
             stat.st_mtime,
EXIT=0
$ git show origin/arena/01a0c56d-seamtech-search:tests/test_reindex_skip.py | nl -ba | sed -n '42,55p'
    42	    list(crawl(config))
    43	    assert len(calls) == 1
    44	
    45	    # Second pass with metadata reflecting what's now stored: same size/mtime/version.
    46	    stat = (root / "reference.txt").stat()
    47	    existing = {
    48	        # même normalisation que le crawler (os.path.normcase : no-op sur
    49	        # Linux, .lower() casserait la clé dès qu'un segment du basetemp
    50	        # contient une majuscule — ex. .pytest_tmpD1)
    51	        os.path.normcase(str((root / "reference.txt").resolve())): (
    52	            stat.st_size,
    53	            stat.st_mtime,
    54	            CURRENT_EXTRACTOR_VERSION,
    55	        ),
EXIT=0
$ git diff --shortstat origin/main...origin/arena/01a0c56d-seamtech-search
 128 files changed, 22953 insertions(+), 28 deletions(-)
EXIT=0
```

### Fusion d'essai et tests sur son commit local (jamais poussé)

Worktree créé avec `git worktree add --detach /tmp/rep-fusion origin/main` :
HEAD détachée pour ne créer ni changer aucune branche. Environnement isolé :
`python -m venv /home/user/fusion-preuves/venv`, puis
`/home/user/fusion-preuves/venv/bin/pip install -r requirements-dev.txt`.

```text
$ date -u +%FT%TZ
2026-09-22T16:23:01Z
EXIT=0
$ git rev-parse --is-shallow-repository
false
EXIT=0
$ git rev-parse HEAD origin/arena/01a0c56d-seamtech-search
c69dd1811fc671d6d3a93d5bbcbdadcc98c5cb43
fc4f99eb123413604b92c43b20d6bae418fbfc8e
EXIT=0
$ git merge --no-ff --no-commit fc4f99eb123413604b92c43b20d6bae418fbfc8e
Automatic merge went well; stopped before committing as requested
EXIT=0
$ git diff --name-only --diff-filter=U
EXIT=0
$ git ls-files | wc -l
272
EXIT=0
$ find .github -type f
.github/workflows/ci.yml
EXIT=0
$ git diff --cached --diff-filter=D --name-only HEAD
EXIT=0
$ git diff --cached --shortstat HEAD
 128 files changed, 22953 insertions(+), 28 deletions(-)
EXIT=0
$ grep -c '^## ' CHANGELOG.md
23
EXIT=0
$ diff -u <(git show origin/main:CHANGELOG.md) <(sed -n '/^## 0.5.0 /,$p' CHANGELOG.md)
--- /dev/fd/63	2026-09-22 16:23:01.681637017 +0000
+++ /dev/fd/62	2026-09-22 16:23:01.681637017 +0000
@@ -1,5 +1,3 @@
-# Changelog
-
 ## 0.5.0 — Remediation (audited commit b7be72a → fixes)
 
 Audited commit `b7be72a` had data-loss, security, and doc-honesty defects. This release fixes them in audit order, verified by `ruff check . && pytest -k "not postgres and not s3"`.
EXIT=1
$ sha256sum 7792-SO_ffab.pdf sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf
43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40  7792-SO_ffab.pdf
43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40  sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf
EXIT=0
$ git -c user.name="Repetition locale" -c user.email="repetition@example.invalid" commit -m "Essai local uniquement : consolidation fc4f99e"
[detached HEAD 0055a91] Essai local uniquement : consolidation fc4f99e
EXIT=0
$ git log -1 --format="%H%n%P%n%s"
0055a91cc17509479a9cb84c5512ea520036185b
c69dd1811fc671d6d3a93d5bbcbdadcc98c5cb43 fc4f99eb123413604b92c43b20d6bae418fbfc8e
Essai local uniquement : consolidation fc4f99e
EXIT=0
$ export PATH=/home/user/fusion-preuves/venv/bin:$PATH; python --version; pytest --version; pytest -q -m "not postgres"
Python 3.11.2
pytest 9.1.1
........................................................................ [ 14%]
........................................................................ [ 28%]
........................................................................ [ 42%]
......................................................ss................ [ 56%]
........................................................................ [ 70%]
........................................................................ [ 84%]
s....................................................................... [ 98%]
.........                                                                [100%]
=============================== warnings summary ===============================
../../home/user/fusion-preuves/venv/lib/python3.11/site-packages/fastapi/testclient.py:1
  /home/user/fusion-preuves/venv/lib/python3.11/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

../../home/user/fusion-preuves/venv/lib/python3.11/site-packages/starlette/testclient.py:53
  /home/user/fusion-preuves/venv/lib/python3.11/site-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
510 passed, 3 skipped, 123 deselected, 2 warnings in 32.50s
EXIT=0
```

Le premier diff du CHANGELOG ci-dessus inclut le titre global côté main
seulement (exit 1). Comparaison corrigée des **cinq sections complètes**, titre
global exclu des deux côtés : sortie vide et exit 0, donc aucun contenu perdu.
Le compte 41 annoncé n'est pas reproduit :

```text
$ diff -u <(git show origin/main:CHANGELOG.md | sed -n '/^## /,$p') <(sed -n '/^## 0.5.0 /,$p' CHANGELOG.md)
EXIT=0
$ git rev-list --left-right --count origin/main...origin/arena/01a0c56d-seamtech-search
1	52
$ git rev-list --count --no-merges origin/main..origin/arena/01a0c56d-seamtech-search
49
```

### Piège lot-d et nettoyage des worktrees jetables

Le constat décisif pour lot-d est l'absence de `.github` (`find` exit 1),
non le diff filtré D. Aucune branche `essai-fusion` n'a été créée : il n'y a
pas de branche à supprimer. Les deux worktrees sont retirés.

```text
$ git worktree add --detach /tmp/rep-lot-d origin/main
Preparing worktree (detached HEAD c69dd18)
HEAD is now at c69dd18 Add files via upload
$ git -C /tmp/rep-lot-d merge --no-ff --no-commit origin/lot-d/interface-5-ecrans
Automatic merge went well; stopped before committing as requested
EXIT=0
$ git -C /tmp/rep-lot-d diff --cached --diff-filter=D --name-only HEAD
$ find /tmp/rep-lot-d/.github -type f
find: '/tmp/rep-lot-d/.github': No such file or directory
EXIT=1
$ git worktree remove --force /tmp/rep-lot-d
EXIT=0
$ git worktree remove --force /tmp/rep-fusion
EXIT=0
$ git worktree list
/home/user/SEAMTECH-search  fc4f99e [arena/01a0c9eb-seamtech-search]
```

## 5. Porte de livraison

La preuve ci-dessus valide **fc4f99e + c69dd181**, pas une autre tête.
Le SHA documentaire poussé et ses statuts CI sont fournis dans le rapport de
livraison de la session. **Sans ses 8 jobs success, la livraison documentaire
n'est pas déclarée verte.** Aucune fermeture, création de PR ou fusion distante
ne doit être déduite des commandes préparées dans ce document.
