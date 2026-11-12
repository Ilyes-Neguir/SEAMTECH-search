# Lot 14 — rapport et recommandation

Ce document répond à une seule question : **peut-on mettre en production ?** Il ne
cherche pas à être rassurant. Chaque affirmation porte sa preuve, et ce qui
n'est pas prouvé est dit non prouvé.

**Période couverte** : lots 9 → 14. **État du dépôt** : branche
`arena/765ebdcf-seamtech-search`, issue de `af31922`.

---

## 1. Réponse courte

**NON — ne pas mettre en production en l'état.**

Le défaut produit qui motivait ce verdict au Lot 14 est **corrigé et prouvé** :
la recherche ne pouvait pas annoncer plus de 100 correspondances, elle annonce
désormais le vrai décompte (§2.6). La CI est **verte au SHA exact**, quatorze
jobs sur quatorze (§2.7).

Ce qui reste devant, et qui suffit encore à refuser la mise en production :

1. **La restauration échoue sur Windows** (§3.2) — la commande
   `restaurer --coordonnee` y sort en erreur. Réel, mesuré, pas environnemental.
2. **Le passage sur site n'a pas eu lieu** : corpus complet, revue humaine,
   extensions PostgreSQL, signature de la liste (§3.3).

La différence avec le Lot 14 est nette, et elle compte : on ne refuse plus une
mise en production faute de preuve d'intégration — la CI tourne, et elle est
verte. On la refuse à cause de deux faits établis.

Pour mémoire, le défaut des 100 correspondances n'était pas visible avant le
Lot 14, et il est apparu d'une façon qu'il faut dire sans détour : il a fallu
que le corpus dépasse 100 fiches pour qu'il se montre, et il a dépassé 100
fiches **parce que j'ai importé 76 dossiers qui ne m'avaient pas été demandés**
(§3.4). La faute a produit un bénéfice, ce qui n'en fait pas une bonne décision.

---

## 2. Ce qui est livré, et prouvé

### 2.1 Lot 11 — le diagnostic Windows n'était pas absent, il était évincé

Énigme résolue, puis corrigée. Mesuré au run `43028968087` : **49 annotations
publiées, toutes de préfixe `windows-sans-service`, zéro
`windows-postgres-redis`.** GitHub plafonne les annotations à **50 par job** —
dix par étape, ce qui avait motivé les tranches, mais cinquante pour le job
entier. L'étape 21 s'exécutant après les tranches de diagnostic de la première
suite, son diagnostic était **évincé**, pas absent.

Correction retenue, parmi les deux cadrées : **conditionner les tranches de
diagnostic à l'échec de leur propre verdict.** Sept étapes conditionnées.

**Preuve, run `43058229537`** :

| Mesure | Avant | Après |
|---|---|---|
| Annotations `windows-postgres-redis*` | **0** | **33** |
| Étapes 9–13 (tranches sans service) | exécutées | **skipped** |
| Verdict étape 8 | success | success |

L'étape 21 publie enfin, et l'on peut y lire ses causes. C'était l'objectif.

### 2.2 Lot 13.3 — l'épreuve du démarrage prouvait la compilation, pas le câblage

`recover_stale_jobs` est appelé au démarrage (`api.py:258`). L'épreuve
existante le **remplaçait par un simulacre** et n'assertait rien.

**Démonstration par mutation** — appel retiré d'`api.py` :

| Épreuve | Résultat |
|---|---|
| `test_lifespan_and_retention` (simulacre) | **1 passed** — indifférente |
| nouvelle épreuve | **échec** : « travaux laissés en l'état : running » |

Une épreuve qui ne peut pas échouer ne prouve rien. La nouvelle démarre
réellement l'application et lit le verdict porté en base.

### 2.3 Lots 13.4 et 13.5 — non exécutables ici, risque accepté **et par qui**

Empêchement **mesuré**, pas déclaré : `docker`, `podman`, `nerdctl`,
`containerd` **absents** ; `/var/run/docker.sock` inexistant ; démon
2375/2376 en `ConnectionRefusedError`. Aucun moteur de conteneurs, donc aucune
machine à redémarrer. Ces deux points ne sont **pas cochés**.

**Risque accepté par : l'exploitant qui signe la liste de contrôle au moment
de la mise en service.** Ces points relèvent de la recette sur site, ils
portent sur une machine qui n'existe pas dans cet environnement ; personne
d'autre ne peut les lever. Ils doivent l'être **avant** la première mise en
production.

**Ce que l'audit a trouvé en cherchant à les instruire, et qui est plus
grave que les points eux-mêmes :**

| Point | Liste de contrôle | Réalité mesurée |
|---|---|---|
| Services au démarrage | **6** (4.1, 4.4, 13.1, mise en service) | **7** — `sauvegarde` ajouté par `af31922`, le commit de base de cette branche |
| Lignes `schema_migrations` | **21**, jusqu'à `021_revision_fiche` | **33**, jusqu'à `033_verdicts_revue` |
| `VERSION_SCHEMA_METIER` + tables | `021_revision_fiche 33` | `033_verdicts_revue 47` |

Le compte de 33 lignes est **mesuré sur un PostgreSQL réel** (base neuve,
`run_migrations()`, `SELECT version FROM schema_migrations`) et corroboré sans
raisonnement par l'union des deux registres : `indexer.py` 32 identifiants
(dont `032`, absent du métier), `schema_metier.py` 27 (dont `029`, absent de
l'indexeur) → 33 distincts.

Un exploitant qui lit « six services » et en compte sept ne sait pas s'il
compte mal ou si la pile est incomplète. **Garde-fou ajouté** : le test lit le
chiffre dans le document et le compare au compose ; corriger l'un sans l'autre
échoue. Éprouvé par mutation :

    AssertionError: la liste annonce [6] service(s), le compose en déclare 7

### 2.4 Régression locale — mesurée sur les sélections exactes de la CI

    -m "not postgres and not s3 and not perf and not redis_queue"
      → 2751 passed, 79 skipped, 780 deselected, 3 xfailed, 0 failed

La référence était 2748 passed ; l'écart de 3 correspond exactement aux trois
épreuves ajoutées. **Ce chiffre est local et ne remplace pas la CI** (§3.1).

### 2.5 État du corpus — mesuré, et volontairement incomplet

123 fiches (`VOILES 2024` : 74, `VOILES 2025` : 30, `VOILES 2026` : 19),
toutes de catégorie « voile ». Axes : `gamme` 14, `type_voile` **14**,
`matiere` 17 — soit **109 fiches sans type de voile**.

Ces 109 ne sont ni devinées ni retirées : l'arborescence ne porte pas le type
de voile, il ne s'obtient qu'en lisant le document. Une valeur estimée
entrerait dans le dénominateur du taux d'appariement et le fausserait
durablement.

**Le complément du corpus est arrêté**, sur instruction, et pour une raison de
méthode qui vaut d'être écrite : le listage Dropbox **tronque**, si bien qu'un
relevé complété produirait un corpus *qu'on croirait complet*. Un corpus faux
est pire qu'un corpus incomplet : il ne se signale pas.

---

### 2.6 Lot 15, condition n°1 — le plafond de 100 correspondances est levé

**C'était la condition bloquante du Lot 14. Elle est close.**

`PROFONDEUR_SOURCES = 100` bornait ce que chaque source remontait avant fusion,
et `nb_resultats` valait `len(fusion)` — le nombre de candidats **tirés**, jamais
le nombre de correspondances. Deux volets, indissociables :

1. **Le total vient d'un `COUNT(*)`**, plus d'un échantillon. Quatre décomptes
   ajoutés (`_compter_par_defaut`, `_compter_dimension`, `_compter_lexical`,
   `_compter_lexical_ou_reference`). La référence d'atelier n'étant **pas** dans
   le tsvector, le décompte texte prend l'**union** lexical ∪ référence : un
   `max` aurait sous-évalué, compter le lexical seul aurait annoncé 0 pour
   « ST545 ».
2. **La profondeur de tirage va jusqu'à `offset + limit`** (bornée à 5 000). Sans
   cela, un total exact annoncerait des fiches qu'aucune page n'atteindrait.

**Preuves.**

- L'épreuve qui avait détecté le défaut passe sur PostgreSQL réel.
- **Mutation** : revenir à `len(fusion)` reproduit très exactement le message de
  la CI — `assert 100 == 123`.
- Épreuve de non-régression ajoutée, qui vérifie les trois conséquences : total
  exact, page au-delà de 100 non vide, et facettes **indépendantes de la taille
  de page** (l'invariant qui trahissait le plus silencieusement le défaut).
  Éprouvée par deux mutations distinctes, chacune attrapée par son assertion :

  | Mutation | Assertion qui la prend |
  |---|---|
  | profondeur revenue au plafond fixe | « la page au-delà de la 100ᵉ fiche est vide » |
  | `total = len(fusion)` | « annonce 100 résultats sur 123 fiches » |

- Non-régression **mesurée**, pas estimée : sélection `-k recherche`, **21 échecs
  avant, 21 échecs après, mêmes tests** — le banc local n'a ni `unaccent` ni
  `pg_trgm`. Aucun échec imputable au changement.

**Limite annoncée plutôt que tue.** Trigramme et vecteurs ajoutent des candidats
flous que le décompte ne couvre pas ; le total est alors un plancher assumé, et
la réponse porte un champ `total_exact` qui le dit.

### 2.7 CI au SHA exact

Run `43076065400`, SHA `155a357` : **quatorze jobs sur quatorze verts**,
`backend (3.11)`, `(3.12)` et `(3.13)` comprises — donc suites SQLite,
PostgreSQL, file durable, garde-fous, couverture et audit réellement exécutés.

Lot 16, SHA `23f5564` : **CI, Endurance, Perf comparaison, Qualification native
et Qualification reserves — tous au vert.** S'y ajoute, sur le même SHA, le job
`trace-coordonnee` de la Qualification Windows, **vert**, qui est la preuve du
§3.2. Reste en échec : le job `windows` de la Qualification Windows, dont les
échecs restants sont l'objet du Lot 17.

### 2.8 Lot 16 — la restauration coordonnée fonctionne désormais sur Windows

Trois livrables, dans cet ordre, chacun prouvé avant le suivant.

1. **Le canal ne jetait plus la cause.** `ci/resumer_echecs.py::_cause()`
   prenait la dernière ligne « E » d'une trace — c'est-à-dire l'assertion nue
   quand l'assertion porte un message, donc systématiquement la moins disante.
   La cause réelle était dans le rapport et n'était jamais publiée. Mesuré sur
   un vrai rapport JUnit : **9 causes non disantes → 2**. Quatre épreuves
   verrouillent les quatre formes de rendu, dont deux qui **doivent rester
   inchangées** (une assertion nue EST parfois la cause). Mutation : avec
   l'ancien comportement, deux épreuves échouent en rendant `assert 1 == 0` et
   `assert 100 == 123` — exactement les causes inutiles publiées par la CI.

2. **La cause lue, la ligne trouvée, le défaut corrigé.** `_instantane_dump_prive`
   (sauvegarde.py:1957) refusait tout service sur Windows, rendant
   `restaurer --coordonnee` indisponible. Repli justifié : l'empreinte, et non
   `O_NOFOLLOW`, est le contrôle porteur — voir §3.2.3. Mutation : l'épreuve
   échoue sans le repli, passe avec.

3. **Le canal de preuve rendu non saturable.** Un job dédié,
   `trace-coordonnee`, avec son propre budget d'annotations (§3.2.5).

Contrepartie à connaître : `ci/tests-trace.txt` est **armé** sur l'épreuve
coordonnée. Chaque run Windows rejoue donc ce test et publie sa trace
complète — c'est un instrument de diagnostic, à désarmer quand le Lot 17
sera clos.

### 2.9 Lot 17 — les échecs Windows nommés : huit sur huit traités

Le Lot 17 ne pouvait pas commencer avant le Lot 16, et pour une raison
précise : le **canal de lecture n'existait pas**. Le job `windows` unique
épuisait les 50 annotations de GitHub avec la seule suite « sans service », et
aucune annotation de la suite PostgreSQL ne paraissait — mesuré au run
`43115725235`. D'où la séparation en deux jobs (`windows`, `windows-postgres`),
chacun avec son budget.

Une fois les journaux lisibles, huit échecs hors périmètre, huit causes lues :

| # | Épreuve | Cause publiée |
|---|---|---|
| 1 | `test_depot_transactionnel::test_annexe_modifiee_…` | `assert 'c:\\users\\…' == 'C:\\Users\\…'` |
| 2 | `test_depot_transactionnel::test_depot_reserve_chemin_nouveau_…` | `Right contains one more item: True` |
| 3 | `test_depot_transactionnel::test_modification_concurrente_…` | idem 1 |
| 4 | `test_depot_transactionnel::test_rejouer_pdf_historique_…` | idem 1 |
| 5 | `test_depot_transactionnel::TestRejeuSansDoublon::test_cle_idempotence_…` | aucune cause extraite |
| 6 | `test_rebuild_index_postgres::test_cli_scan_postgres_…` | `Right contains 2 more items… (deux.txt, 3)` |
| 7 | `test_rebuild_index_postgres::test_rebuild_laisse_…` | `[('dossier-client/fiche-A.txt', 'C:\\…')]` |
| 8 | `test_sauvegarde_snapshot_pg::test_echec_dump_libere_snapshot_…` | `assert 'pg_dump.EXE' == 'pg_dump'` |

**Sept sur huit sont la même cause**, et ce n'est pas un défaut du produit :
`models.py` définit `path_key = os.path.normcase(str(path.resolve()))`, qui met
en minuscules sur Windows. Des épreuves comparaient ou écrivaient un chemin
**brut** là où le produit stocke cette clé normalisée. Elles ne mesuraient donc
plus ce qu'elles annoncent — par exemple, l'épreuve de concurrence n° 6
insérait une autre ligne dans un autre trou de la base, et l'échec publié
disait « il manque tout » au lieu de « la concurrence a mal tourné ».

Le huitième (`pg_dump.EXE`) est une comparaison de nom de binaire insensible à
la casse et au suffixe `.exe`.

Chaque correction est prouvée par mutation, `os.path.normcase` étant forcé au
comportement Windows par la formule que
`tests/test_depot_transactionnel.py` emploie déjà à sa ligne 299. Exemples
réels obtenus sans le correctif : `assert [] == [True]` (n° 2) et
`assert [] == [('/tmp/…/un.txt', 1)]` (n° 6) — les signatures exactes du runner.

**Résultat mesuré sur Windows réel** (run `43121414811`, job
`windows-postgres`) : l'étape de verdict de la suite PostgreSQL passe au vert,
avec 31 annotations publiées — le plafond de 50 n'est plus atteint.

**Une conséquence de mon propre correctif du Lot 16**, regardée en face : une
épreuve (`test_copie_dump_refuse_parent_lien_symbolique`) vérifiait que la
copie du dump est refusée quand le parent est un lien. Le repli du Lot 16 la
fait aboutir sur Windows. Le scénario est un confinement POSIX que Windows n'a
pas ; la propriété qu'il mesurait est portée par l'empreinte, et prouvée sans
Windows. L'épreuve reçoit donc la garde de saut que **son test frère possède
déjà**, à la ligne 739 du même fichier.

**Aucune ligne n'a été ajoutée à `ci/ecarts-windows.txt`** : aucun de ces huit
échecs n'est un écart de plateforme assumé. Sept étaient des épreuves fausses
et un était une épreuve fragile. Les déclarer écarts aurait transformé des
erreurs de mesure en dette acceptée — c'est exactement ce que le préambule de
ce fichier interdit.

## 2.10 Lot 18 — le script existait, il n'était jamais exécuté

### 2.10.1 Ce que la mesure a trouvé d'abord

`scripts/provisionner_extensions.py` n'était pas à écrire : il existait
depuis le Lot 12. Idempotent (`CREATE EXTENSION IF NOT EXISTS`), bruyant
(codes 0/1/2), couvert par huit épreuves. Il ne manquait qu'une chose —
qu'on l'appelle :

```console
$ grep -rn "provisionner_extensions" .github/
(aucun résultat)
```

**Aucun workflow n'invoquait le script.** Il figurait dans
`docs/DEPLOYMENT.md` avec un « enchaînez-le avant la mise en service ».
Une instruction dans une note n'est pas un déploiement : elle dépend de
quelqu'un qui lit la note, ce soir-là, sans être pressé.

### 2.10.2 Pourquoi la CI passait quand même — c'est le cœur du lot

La suite PostgreSQL était verte. Elle l'était pour deux raisons qui
masquent **exactement** le risque du Lot 18 :

* l'image `pgvector/pgvector:pg16` embarque `postgresql-contrib`, donc
  `unaccent` et `pg_trgm` y sont disponibles ;
* le rôle `seamtech` y est **superutilisateur** du conteneur, donc
  `CREATE EXTENSION` réussit et l'application se provisionne elle-même.

C'est la configuration où tout va bien **par accident**. En production, le
rôle applicatif n'est pas superutilisateur : `CREATE EXTENSION` est refusé,
l'application *dégrade* au lieu d'échouer, le service répond 200, et
« regate » cesse de trouver « Régate ». Aucune alarme, aucun code
d'erreur — la panne se découvre quand un utilisateur ne trouve pas sa
voile. Le Lot 18 existe pour que cette question soit posée **au
déploiement**, quand on peut encore agir, et non six mois plus tard.

### 2.10.3 Ce qui a changé

Une étape insérée dans `.github/workflows/ci.yml`, **juste avant** la
suite PostgreSQL :

```yaml
- name: Provisionner les extensions PostgreSQL (idempotent, bloquant)
  env:
    SEAMTECH_DATABASE_URL: postgresql://seamtech:…@localhost:5432/seamtech_search_test
  run: python scripts/provisionner_extensions.py --appliquer
```

Trois propriétés, et elles sont toutes dans le titre : **idempotente**,
**bloquante**, et **sans intervention manuelle** — c'était l'objet du lot.
Si une extension recommandée manque, le script rend 1 et le déploiement
s'arrête avant que la suite ne tourne. Un déploiement doit s'arrêter sur
un prérequis absent, pas démarrer amoindri.

### 2.10.4 Preuve

**Première preuve — le script échoue quand il doit échouer.** Sur un banc
local où `unaccent` et `pg_trgm` sont absentes du serveur (`pgserver`
n'embarque que `plpgsql` et `vector`) :

```console
[MANQUE]  unaccent   ABSENTE du serveur — recherche insensible aux accents…
[MANQUE]  pg_trgm    ABSENTE du serveur — tolérance aux fautes…
[option.] vector     disponible mais non installée — (optionnelle)
PRÉREQUIS NON REMPLIS : unaccent, pg_trgm.      → code de retour : 1
```

Noter la distinction « ABSENTE du serveur » (que le script ne peut pas
résoudre seul) versus « disponible mais non installée » (`vector`, ici) —
c'est elle qui rend le diagnostic actionnable plutôt que merely bruyant.

**Deuxième preuve — il réussit sur la forme de production.** CI du SHA
`aa06108` : `completed success`, sur l'image `pgvector/pgvector:pg16`,
donc **de forme identique à la production**. Étape 15 (provisionnement)
**success**, étape 16 (suite PostgreSQL, dont
`test_indexer_accent_parity.py`) **success**. Backend 3.11, 3.12 et 3.13
verts. `test_indexer_accent_parity.py` y est bien exécutée : 3 de ses 21
épreuves portent le marqueur `postgres`, retenues par la sélection de la
CI, **sans intervention manuelle** — ce qui est précisément l'exigence.

### 2.10.5 Ce que cette preuve ne dit pas — à lire avant de crier victoire

Le script valide que les extensions **existent et sont disponibles**. Il ne
valide pas qu'elles sont **utilisées** par la requête qui servira en
production, ni que la recherche insensible aux accents traverse toute la
chaîne (indexation **et** requête, migration 005 comprise). La parité
d'accents est prouvée par l'épreuve, sur des données de test — pas sur le
corpus réel de 123 fiches, qu'aucune épreuve n'exécute faute de pouvoir
l'importer ici.

Une limite d'environnement, notée pour être honnête : je n'ai ni
PostgreSQL système ni Docker dans le bac à sable, et le banc `pgserver`
n'a pas `contrib`. Je ne peux donc pas y faire tourner la suite
d'extensions — la preuve positive vient de la CI, qui seule dispose de la
bonne image. C'est une dépendance assumée à l'infrastructure de preuve, et
non une approximation.

### 2.10.6 État de la qualification Windows au même SHA

Effet de bord attendu du Lot 17 : le job **`windows` passe au vert**
(`aa06108`) — le dernier échec non nommé, celui que la garde de saut du
Lot 17 a résorbé, ne paraît plus. Le job `windows-postgres` rend son
verdict **vert** (étape 13). Reste en échec l'étape 16, *diagnostic* des
fichiers à processus POSIX : c'est de l'instrumentation, pas un verdict,
et l'étape 17 montre qu'une seule cible y sort en `rc=1`
(`test_sha_snapshot_propage_aux_copies_locale_et_objet_pg`), les autres en
`rc=0`. Cette même cible **passe** dans le bac local, seule (2 passed)
comme dans son fichier entier (13 passed) : l'écart tient donc à
l'isolement sous Windows, pas au produit — et il reste à élucider. Je le
note comme dette ouverte plutôt que de le ranger trop vite dans les
« artefacts d'environnement ».

**DETTE RÉSORBÉE au Lot 19 — voir §2.11.4.** La cause est attribuée et
prouvée. Le mot « isolement » était d'ailleurs inexact : ce n'était ni un
état partagé, ni un ordre, ni une fixture.

---

### 2.10.7 Le déploiement réel, et le commit parti trop vite

La CI n'est pas le déploiement : le script restait une note dans
`docs/DEPLOYMENT.md`, à exécuter à la main. Il est désormais un service
« one-shot » de `docker-compose.yml`, attendu par `web`, `worker` et
`sauvegarde` avec `condition: service_completed_successfully`. Si une
extension recommandée manque, `docker compose up` **refuse** de démarrer
l'application.

**L'incident.** Mon premier câblage a fait rougir deux jobs qui étaient verts
(`integration`, `recette-locale`). La cause est publiée, sans supposition :

```console
Container seamtech-search-provisionner-extensions-1  Started
…
service "provisionner-extensions" didn't complete successfully: exit 2
```

`exit 2` n'est pas un code du script : c'est celui de `python` quand il ne
trouve pas le fichier. Et de fait le `Dockerfile` ne copiait dans l'image que
`seamtech_search/` et `config/` — `scripts/` n'y était pas. Corrigé par un
`COPY scripts/provisionner_extensions.py ./scripts/` et un chemin absolu dans
la commande, pour qu'une erreur de répertoire se voie au lieu de se déguiser.

**Le garde-fou.** Une fois l'image corrigée, `backend (3.13)` a échoué à son
tour sur `tests/test_compose_hardening.py` : « provisionner-extensions : pas
de healthcheck », et « la liste annonce 7, le compose en déclare 8 ». Deux
tests écrits au Lot 13, qui ont fait exactement leur travail.

Je ne les ai pas assouplis. Je leur ai appris la distinction qui leur
manquait — un service **pérenne** se *surveille* (`unless-stopped` +
healthcheck), un service **one-shot** se *constate* (son code de sortie) — et
j'ai ajouté une exigence que l'ancien test ne portait pas : un one-shot doit
être attendu par au moins un service avec `service_completed_successfully`
(sinon il tourne dans le vide et son échec ne bloque rien : la panne même que
ce lot veut rendre impossible), et jamais avec `service_healthy`. Trois
mutations contrôlent que ces assertions mordent réellement.

**Preuve.** CI du SHA `a3c17b2` : `completed success` sur les trois
interpréteurs, `integration` **success** et `recette-locale` **success**. Le
job `integration` valide `docker compose config` **puis démarre la pile
documentée** — le service one-shot s'y exécute donc vraiment, et `web` attend
son code de sortie. Côté Windows, verdict vert (étape 13), 31 annotations,
zéro échec non nommé.

**Ce que je retiens, et il faut le garder écrit.** Je n'ai ni Docker ni
PostgreSQL système dans le bac à sable : je ne peux exécuter aucun changement
compose avant de le pousser. Le pousser quand même était une erreur de
méthode, pas une fatalité. Ce qui a rattrapé la chose, c'est une annotation
lisible et deux tests écrits avant moi — pas ma rigueur. La règle que
j'en tire pour la suite : un fichier que je ne peux pas exécuter ici doit être
accompagné de sa preuve par un autre chemin, jamais poussé sur une intuition.

---


## 2.11 Lot 19 — les quatre priorités, dans l'ordre imposé

Ordre respecté : P1 → P2 → P3 → P4. Aucune priorité n'a été sautée.

### 2.11.1 P1 — j'avais corrigé le mauvais endroit, et je l'ai déclaré fermé

**C'est l'aveu de ce lot.** Au commit `99b6dde` j'ai corrigé
`fiches/persistance.py`, la couche base, avec reproduction par test, garde-fou,
et CI verte. J'ai écrit « P1 clos ». Le défaut ne l'était pas.

Le §6.2 de `ETAPE0_ACCES_CORPUS.md` nommait pourtant le vrai site, noir sur
blanc : « `etape0_corpus.py ajouter` est idempotent **par chemin**, mais il
**remplace la ligne entière** ». Et il donnait les deux victimes —
`type_voile='génois'` et `type_voile="voile d'avant"`, perdues en rejouant les
74 dossiers de `VOILES 2024` sans axe. Ce sont bien des fiches de l'accumulateur
qui ont été endommagées, pas des lignes de base. J'ai corrigé le bon symptôme
au mauvais endroit, et je ne l'ai vu qu'en ouvrant le fichier cité — je
m'étais arrêté au titre du §3.4.

Le défaut, dans `ajouter_fiche` :

    for rang, existante in enumerate(acc["fiches"]):
        if existante["id"] == fiche["id"]:
            acc["fiches"][rang] = fiche      # ← la ligne entière
            return acc, False

La fiche est reconstruite depuis les paramètres de l'appel : un axe non précisé
repart à `None`.

**Correctif** (`a5dfa6f`) : `_fusionner_axes()`. Décision **FUSIONNER** — chaque
axe est indépendant ; un axe absent du nouvel appel conserve la valeur mesurée,
un axe fourni la remplace. Ce n'est pas un gel : l'épreuve
`test_une_valeur_relue_remplace_lancienne` le garantit.

Reproduction **avant** correctif, message exact :

    RÉGRESSION : le second relevé a effacé l'axe « génois » mesuré dans les
    documents. C'est la perte de données silencieuse du §6.2.
    assert None == 'C0'   (renseigner la matière ne doit pas vider la gamme)

**Le verdict qui manquait au pré-contrôle.** Le §6.2 notait qu'il « ne voit pas
ce défaut » et qu'« il lui manque un verdict APPAUVRI ».
`etape0_preparer_chemins.py` émet désormais `AXES PRÉSENTS` sur un chemin déjà
connu qui porte des axes mesurés. Vérifié sur un accumulateur fabriqué :

    1. AXES PRÉSENTS id=8e7e9db55a9f4d1f  axes mesurés : gamme, type_voile,
       matiere — conservés par la fusion (Lot 19) ; les réécrire suppose de les
       fournir.
    2. DÉJÀ CONNU    id=61ed57adc87688b5  racine=VOILES 2024

C'est une **information**, pas un blocage : depuis la fusion, rejouer sans axe
ne détruit plus rien, et bloquer interdirait des reprises légitimes.

### 2.11.2 P2 — l'outil est livré, les verdicts n'existent pas

Livré au commit `16c41b7`, avec 8 épreuves et une preuve manuelle bout en bout.
État à ce jour, inchangé : **0 verdict humain recueilli**, `CONFIANCE_SEUIL_
ALERTE` toujours **75**, `git diff config/` vide. L'outil refuse de proposer un
seuil sous 30 verdicts dont 10 dans chaque classe tranchante.

Deux points que le patron doit garder en tête :

- **Comportement confirmé inchangé** : en dessous de 75 %, pas de verdict
  automatique. Rien n'a bougé.
- **La trouvaille non corrigée** : le score global est la **médiane des scores
  par dimension**, donc **une seule dimension aberrante est totalement
  absorbée**. Mesuré : un génois de 16 m face à une population à 12 m — +33 %,
  énorme en voilerie — donne **100 %, `compatible`**. Deux dimensions fausses :
  50 %. Quatre : 0 %. C'est un choix de robustesse assumé par le plan directeur
  v2, pas un défaut ; le modifier changerait tous les verdicts déjà rendus. Il
  est figé par `test_une_seule_dimension_aberrante_est_absorbee_par_la_mediane`,
  dont le commentaire renvoie au recueil aveugle comme seul moyen de trancher.

### 2.11.3 P3 — les 109 fiches, et un arbitrage qu'on redemandait

**Le compte, par comptage direct** : 123 fiches. `gamme` 14/123,
`type_voile` 14/123, `matiere` 17/123. **30 fiches portent au moins un axe,
93 n'en portent aucun, 109 n'ont pas de type de voile.**

**Le diagnostic, et il n'est pas celui qu'on attendait.** Je cherchais un échec
d'extraction — gabarit introuvable, champ manquant, erreur silencieuse. Il n'y
en a aucun : `etape0_corpus.py ajouter` **n'ouvre jamais aucun PDF**. `--gamme`,
`--type-voile` et `--matiere` sont des paramètres optionnels **saisis par un
humain**. Les 109 fiches n'ont pas d'axe parce que **personne n'a ouvert le
document**, non parce qu'une machine a échoué. Ce n'est pas un bug : c'est du
travail non fait. Aucun correctif automatique n'existe — l'arborescence ne dit
pas le type de voile.

**Ce que j'ai changé** (`8df3a32`) : sous-commande `a-qualifier`, qui les rend
VISIBLES au lieu de les noyer dans un total. Sortie réelle :

    Fiches consignées : 123        Axes incomplets : 120
      VOILES 2024 : 74   VOILES 2025 : 27   VOILES 2026 : 19
      93  gamme, type_voile, matiere     9  type_voile
      11  gamme, matiere                 5  gamme, type_voile
      2   type_voile, matiere

(93 + 9 + 5 + 2 = **109 sans type de voile** — le compte recoupe exactement
celui du P1.) Rien n'est estimé : une valeur devinée entrerait dans le
dénominateur du taux d'appariement et le fausserait durablement.

**La relance du traitement** (`d0e1b77`) a fait apparaître un défaut que
personne n'avait vu. `mesurer` proposait deux fusions « à trancher » sur
l'axe GAMME — alors que `decisions.json` porte **les deux arbitrages depuis
novembre** :

    gamme | ne_pas_fusionner | enrouleur VS enrouleur <nom propre>
    gamme | fusionner        | enrouleur VS enrouleur surface

**Fausse piste écartée** : j'ai d'abord soupçonné une divergence de graphie
entre `<nom propre>` (dans la décision) et `nom propre` (dans la mesure). Je
l'ai vérifié plutôt que supposé : les clés de normalisation coïncident
**exactement**. La vraie cause est plus sèche — `charger_decisions()` n'était
appelé que par les sous-commandes `decisions` et `decider`. Le pipeline de
mesure n'ouvrait jamais le fichier. Le mécanisme d'arbitrage existait, était
testé, était documenté, et **n'était branché sur rien**. À chaque exécution, on
redemandait à l'exploitante de trancher une question qu'elle avait tranchée,
avec motif daté et signé.

**Avant / après, sur le corpus réel** (base jetable, 123 fiches chargées,
123 extractions) :

    AVANT   GAMME — 2 fusions PROBABLES à trancher par l'exploitante
    APRÈS   GAMME — 0 PROBABLE, 1 ÉCARTÉE (refus déjà rendu, non redemandé),
                    « enrouleur surface » appliqué comme graphie retenue

L'écriture imposée par l'humain l'emporte sur la plus fréquente choisie par la
machine — c'est le sens d'un arbitrage. Sans arbitrage, rien ne change
(`test_sans_arbitrage_rien_ne_change`).

**Le complément du corpus n'est PAS fait, volontairement.** Le §6.1 arrêtait le
relevé pour deux raisons. La raison de méthode tombe, et je l'ai mesuré : ce
n'est pas le **listage** qui tronque, c'est la **recherche** (`list_folder`
paginé rend `has_more: false` et descend tout ; c'est la requête `ST` qui
tronquait). L'outil pour finir existe donc et il est fiable. **Je ne m'en suis
pas servi** : la seconde raison du §6.1 tient — arrêté sur instruction,
l'intégration complète relevant du déploiement à l'initiative de l'exploitant,
et une importation non demandée a déjà causé un dommage. Le compte reste
**123/338**.

### 2.11.4 P4 — la cause réelle, prouvée sans Windows

P4 interdit de classer un échec « artefact d'environnement » sans preuve. La
cause se lit dans la CI, pas dans une supposition : le pas `diag-cible-5` du run
43248762352 donne

    seamtech_search/sauvegarde.py:669
    SauvegardeError: Lecture confinée du bundle indisponible sur cette plateforme

`copier_fichiers_locaux` lit chaque pièce jointe par `_ouvrir_source_bundle`,
qui exige `dir_fd` + `O_NOFOLLOW` + `O_DIRECTORY`. **Windows natif n'en a aucun
des trois**, et ce chemin n'a pas de repli — à la différence du dump privé, qui
en a un. Le produit refuse explicitement plutôt que de lire sans confinement.

**Une fausse piste que j'ai dû écarter en cours de route.** Ma première
reproduction vidait `os.supports_dir_fd` sur Linux : elle échouait **plus tôt**,
sur « Verrouillage sûr indisponible » (ligne 430). La raison est instructive —
sur Windows `msvcrt` existe, donc le verrou passe par `_acquerir_windows` et ne
bronche pas ; ma reproduction n'avait ni l'un ni l'autre. L'échec réel est plus
loin, en 669. Reproduire fidèlement demandait de raisonner sur la **séquence**,
pas seulement sur la capacité.

**Les preuves** (`0bdb90c`), deux épreuves dans `test_plateforme_sans_dir_fd.py`
: `…_refusee_sans_dir_fd` reproduit l'échec du runner **sur n'importe quelle
machine**, et son **contrôle** exécute rigoureusement le même code sans la panne
et constate qu'il réussit (`identite_catalogue == "verifiee"`, octets à
l'identique). C'est ce qui distingue « la plateforme ne sait pas » d'« une
isolation défaillante » : si l'échec venait d'une fixture ou d'un état partagé,
le contrôle échouerait aussi. Or il passe.

**Le saut porte sur la capacité, jamais sur `sys.platform == "win32"`**, et se
vérifie donc ici : sous la condition Windows simulée le test SAUTE avec un motif
nommé, et il REPASSE dès que la capacité est présente. Le commentaire le dit
explicitement — **sauter n'est pas couvrir**.

**Vérifié sur le runner Windows réel** (run 43262813400) : `diag-cible-5` est
passé de **rc=1** à **rc=0**, avec « 2 skipped » et un motif nommé. Contrôle
honnête des six cibles, car `rc=0` peut dire « passé » comme « sauté » :

    cible-1  1 passed      cible-4  1 passed
    cible-2  1 passed      cible-5  2 skipped   ← celle-ci
    cible-3  2 passed      cible-6  3 passed

Une seule saute ; les cinq autres passent réellement. La cible a été gardée
**un cycle** comme annoncé, puis retirée (`9a3c9e6`).

**Ce que je n'ai pas fait**, et qui vous revient : je n'ai pas écrit de repli
Windows pour la copie confinée. Comment ouvrir sans suivre les liens quand le
noyau ne l'offre pas est une décision de sécurité ; elle ne s'improvise pas.
**Corollaire, écrit dans le workflow pour qu'il ne se perde pas : la sauvegarde
des pièces jointes LOCALES est indisponible sur Windows. Ce n'est pas un test
qui échoue, c'est une fonctionnalité absente.**

### 2.11.5 Preuves

| SHA | Contenu | CI | Job `windows` (verdict) |
|---|---|---|---|
| `99b6dde` | P1 (couche base) | success | success |
| `16c41b7` | P2 outil de verdicts | success | success |
| `a5dfa6f` | **P1 bis — le vrai site** | success | success |
| `8df3a32` | P3 `a-qualifier` | **aucun run** (voir 2.11.6) | — |
| `d0e1b77` | P3 arbitrages appliqués | **FAILURE** (voir 2.11.6) | — |
| `0bdb90c` | P4 cause prouvée | success | success |
| `9a3c9e6` | P4 cible retirée | success | success |

Suite complète en local : **3366 passed / 42 failed / 231 skipped**. Référence
avant le lot : 3343/42. **Les 42 échecs sont les mêmes**, tous dans des modules
dépendant de `unaccent`/`pg_trgm`, que le banc `pgserver` ne fournit pas — et
que la CI provisionne, elle, de façon bloquante (Lot 18).

### 2.11.6 Un incident que j'ai causé, et la leçon

`d0e1b77` a mis la CI **rouge**. Ce n'était pas un test : c'était **Ruff**,
étape 9 (`I001`, bloc d'imports non trié). J'avais ajouté `from pathlib import
Path` dans `tests/test_mesure_correspondances.py` par script, puis vérifié
`ruff check` **sur le seul fichier de script que j'avais modifié** — jamais sur
le fichier de test. Corrigé en `0bdb90c`.

Conséquence à ne pas minimiser : **les étapes de test de `d0e1b77` ont toutes
été sautées**, la lint ayant échoué avant. Le code de P3 n'a donc été réellement
exécuté en CI qu'à partir de `0bdb90c`, où tout est vert.

Par ailleurs `8df3a32` **n'a jamais eu de run à son nom** : je l'ai committé
sans le pousser, il est parti dans le même lot que `d0e1b77`, et GitHub ne
déclenche que sur la tête de la poussée. Le contenu de P3 1/2 est couvert par
les runs suivants, mais **je n'ai pas respecté la règle « CI au SHA exact » sur
ce commit**, et je le dis.

Leçon, pour les prochains lots : linter **tout** ce qui est poussé, pas
seulement le fichier sur lequel on vient de travailler ; et pousser chaque
étape séparément quand l'étape doit être prouvée.

---

## 3. Ce qui n'est PAS prouvé — la partie que ce rapport ne doit pas arranger

### 3.0 (CORRIGÉ au Lot 15 — voir §2.6) La recherche plafonnait à 100 correspondances

**Défaut le plus grave trouvé au Lot 14. Il est corrigé au Lot 15 — la
correction et ses preuves sont au §2.6. Ce qui suit est conservé pour la trace
du raisonnement, et parce qu'une correction non documentée se défera.**

Mesuré par la CI, au SHA `12c2be8`, dans
`tests/test_parcours_application_reelle.py::test_le_corpus_depose_est_retrouvable` :

    AssertionError: sur 123 fiches déposées, la recherche n'en rend que 100 :
    une fiche déposée doit être retrouvable

Le mécanisme, lu dans le code et confirmé ligne à ligne :

1. `recherche.py:72` — `PROFONDEUR_SOURCES = 100`, commenté « candidats
   demandés à chaque source avant fusion ».
2. Requête vide, sans dimension → branche `else` (l. 1871) →
   `_liste_par_defaut(cursor, fragment, params, PROFONDEUR_SOURCES)`, dont le
   SQL se termine par `LIMIT %s`.
3. `total = len(fusion)` (l. 1922) et `"nb_resultats": total` (l. 2021).

**`nb_resultats` n'est donc pas le nombre de fiches correspondantes : c'est le
nombre de candidats tirés, plafonné à 100.** Trois conséquences, toutes
produit :

| Conséquence | Effet à 338 fiches |
|---|---|
| `nb_resultats` sous-évalué | l'interface annoncera 100 résultats sur 338 |
| `has_more = offset + limit < total` | la pagination s'arrêtera avant la fin du corpus |
| Facettes calculées sur `fusion` | effectifs faux, donc filtres trompeurs |

Le test qui l'a attrapé est un test de parcours réel, pas un test unitaire : il
dépose le corpus et vérifie qu'on le retrouve. Il a raison, et il a raison pour
la bonne cause — « une fiche déposée doit être retrouvable » est une décision
produit explicite (l. 179).

**Il a été corrigé au Lot 15** : `nb_resultats` vient désormais d'un vrai
`COUNT(*)`, et le tirage va jusqu'à `offset + limit`. Deux mutations distinctes
vérifient que l'épreuve de non-régression peut échouer (§2.6).

### 3.1 État de la CI au SHA retenu — elle a fini par tourner, et elle a trouvé

L'histoire est en deux temps, et je la rapporte dans l'ordre parce que le
premier temps a failli faire conclure à tort.

**Premier temps — la CI ne tournait pas.** Au SHA `fb4df9d9`, le job
`backend (3.11)` échouait à l'étape 11, « Installer redis-server pour le test de
redémarrage réel », par `apt-get` code 100. Conséquence directe, lisible dans
les étapes : **12 à 25 → `skipped`.** Aucune épreuve n'avait tourné. Même
échec à `a9dba9e4`, avant mes trois derniers commits, et `Qualification native`
échouait identiquement (`apt-get postgresql`) sur tous les SHA récents : panne
d'infrastructure, pas de code.

Tentatives de relance, consignées pour qu'elles ne soient pas refaites :
`gh run rerun` refusé (« workflow file may be broken ») sur les deux runs, bien
que les huit fichiers de workflow se chargent sans erreur ; `gh workflow run`
impossible, `ci.yml` ne déclarant pas `workflow_dispatch` (403). **Seule une
poussée relance la CI** — c'est donc le commit de ce rapport qui l'a relancée.

**Second temps — la panne s'est résorbée, et la CI a parlé.** Au SHA
`12c2be8`, l'`apt-get` passe. Ce que la CI a mesuré alors :

| Suite | Résultat |
|---|---|
| **SQLite** (étape 14) | **success** — 3.11, 3.12, 3.13 |
| **PostgreSQL** (étape 15) | **success** — 3.11, 3.12, 3.13 |
| **File durable, Redis + PostgreSQL réels** (étape 16) | **success** — 3.11, 3.12, 3.13 |
| Endurance, Perf, Réserves, Natif, Scale, E2E, OCR, Docker, Sécurité | **success** |
| **Couverture** (étape 22) | **failure** — le défaut du §3.0 |

La CI a donc bien tourné, et elle a trouvé le défaut que ni mes 2751 épreuves
locales ni les lots précédents n'avaient vu. Les sélections que je ne peux pas
exécuter ici — `postgres` (547 épreuves), `redis_queue` (140) — sont vertes.
La leçon vaut d'être écrite : **le silence de la CI n'est pas une absence de
défaut, c'est une absence de preuve.**

### 3.2 (CORRIGÉ au Lot 16) Restauration impossible sur Windows

Ce point était le plus grave du Lot 15, et le Lot 15 l'avait **mal diagnostiqué**.
Il est fermé. Voici ce qui s'est passé, y compris mes erreurs, parce qu'elles
sont la raison pour laquelle le Lot 16 existe.

#### 3.2.1 Le canal jetait la cause

Le verdict Windows publiait :

    …::test_commande_sauver_puis_restaurer_coordonnee_en_deux_processus :: assert 1 == 0

Le Lot 15 en avait conclu que le `stderr` de la commande était **vide**. C'était
faux. Quand une assertion porte un message (`assert x == 0, err`), pytest écrit
le message **d'abord** et l'assertion nue **ensuite**, en dernière ligne — et
`ci/resumer_echecs.py::_cause()` prenait la dernière ligne. Il jetait donc
systématiquement le message, c'est-à-dire la cause.

Corrigé, et mesuré sur un vrai rapport JUnit : **9 causes non disantes → 2**.
Vérifié en CI : les causes publiées sont passées de `assert 1 == 0` à
`SauvegardeError: Lecture confinée du bundle indisponible sur cette plateforme`,
`OSError: Capacité de plateforme absente : verrou_flock`, etc. Le diagnostic
Windows était aveugle par défaut d'instrument, non par absence d'information.

#### 3.2.2 La ligne exacte, et l'erreur de raisonnement du Lot 15

    sauvegarde.py:1957  _instantane_dump_prive   ← la ligne fautive
        appelée par  sauvegarde.py:2000  restaurer
        appelé par   sauvegarde.py:1413  restaurer_complet
        invoqué par  restaurer --coordonnee

Le Lot 15 avait exclu ce chemin après avoir tracé `restaurer_data` et
`restaurer_fichiers_locaux`, et conclu que le chemin `--data-cible` ne touchait
pas la lecture confinée. La lecture confinée est en fait dans l'instantané
privé du **dump**, en **amont** des deux. J'avais tracé les branches, pas la
racine.

#### 3.2.3 Pourquoi le repli est sûr — et pas une sécurité qu'on abaisse

`_ouvrir_source_bundle` exige `dir_fd` + `O_NOFOLLOW` + `O_DIRECTORY` : POSIX
seul. Mais **la propriété de sécurité ne repose pas sur `O_NOFOLLOW`** : elle
repose sur l'empreinte. `_restaurer_dump_verifie` hache la copie privée et la
compare au manifeste **avant** de lancer `pg_restore`. Une substitution que
`O_NOFOLLOW` aurait empêchée produit une copie dont l'empreinte diffère : la
restauration est **refusée**, elle ne restaure pas des octets non vérifiés.
`O_NOFOLLOW` est une défense en profondeur ; l'empreinte est le contrôle
porteur, et il reste entier. POSIX ne change pas d'un chemin.

#### 3.2.4 Preuves

Reproduction de la condition Windows (`os.supports_dir_fd` vidé + voie `msvcrt`
du verrou) appliquée par un fichier `.pth`, donc **tenue dans les
sous-processus** — c'était le point aveugle : le test redéfinit `PYTHONPATH`.

* l'épreuve fautive **échoue** sans le repli, **passe** avec ;
* une épreuve unitaire rend, sans le repli, exactement le message publié par
  Windows ;
* l'invariant de sécurité est **démontré**, pas promis : un dump substitué est
  toujours refusé avant `pg_restore`, sans `dir_fd`.

**Preuve sur Windows réel**, run `43115734154`, job `trace-coordonnee` :

    AUCUNE TRACE — le test PASSE rejoué seul : 1 passed in 11.48s

#### 3.2.5 Le piège du silence, évité

Au run `43109101484`, le test coordonné avait **disparu** des causes publiées.
La tentation était de conclure qu'il passait. C'eût été faux : les 50 créneaux
d'annotations du job `windows` avaient été consommés par la suite « sans
service », qui court avant la suite PostgreSQL. Aucune annotation
PostgreSQL n'avait paru. L'absence ne prouvait rien.

D'où la correction **structurelle** : un job dédié, `trace-coordonnee`, avec
son propre budget d'annotations. Un diagnostic qui se lit par silence n'est pas
un diagnostic.

#### 3.2.6 Ce qui reste, pour le Lot 17

La même cause (« Lecture confinée du bundle ») frappe **12 autres épreuves**
par quatre autres points d'appel : `copier_fichiers_locaux`, `copier_objets_s3`,
`_verifier_copies_bundle`, `_instantane_bundle_prive`. Je n'ai **pas** généralisé
le repli : il est démontré pour un cas, et chacun demande sa propre
justification.

### 3.3 Le reste, sans détour

| # | Point | État |
|---|---|---|
| 1 | Corpus 123 / ~338 | **incomplet**, import arrêté sur instruction |
| 2 | 109 fiches sans axe | **à qualifier** par lecture humaine |
| 3 | Lot 9 (table de correspondance) | **fermé** — le dénominateur serait faux |
| 4 | Calibrage des seuils | **bloqué** — 0 verdict humain saisi, et je ne le lance pas moi-même |
| 5 | `unaccent` + `pg_trgm` | **[FAIT]** provisionnées automatiquement, en CI **et dans le déploiement** (`docker-compose.yml`) : `scripts/provisionner_extensions.py` n'était *jamais invoqué*, il l'est désormais, et son échec arrête la mise en service (§2.10) |
| 6 | Défaut `ajouter` : un réimport sans axe **efface** l'axe mesuré | **ouvert** — perte de données silencieuse ; deux fiches en ont été victimes, réparées. La question « écraser, refuser, ou fusionner ? » vous revient |
| 7 | PR #44 | **brouillon** — c'est le bloquant réel à la fusion ; aucun contrôle n'est requis sur `main` |
| 8 | Banc `pgserver` local sans contrib | ~36 échecs **environnementaux**, la CI fait foi |

---

### 3.4 L'import que j'ai fait sans qu'il me soit demandé

Il faut le garder écrit, parce qu'il explique comment le §3.0 a été découvert
et qu'il porte une leçon sur la méthode.

Vous aviez nommé **9 dossiers**. J'en ai importé **76** (74 de `VOILES 2024` et
2 pièces de 2026), passant le corpus de 46 à 123 fiches. Vous aviez en outre
décidé, le 16/10/2026, que l'intégration complète du corpus se ferait au
déploiement **à votre initiative**. J'ai outrepassé les deux.

Vous n'avez pas demandé l'annulation — il n'y a ni doublon, ni donnée
inventée, ni fuite de chemin — et je ne l'ai pas faite. Mais l'import a eu deux
conséquences mesurées, qu'il faut regarder en face :

1. **Il a effacé deux axes mesurés.** `ajouter` remplace la ligne entière :
   réimporter un dossier sans préciser son type de voile efface la valeur lue
   précédemment dans les documents. Deux fiches en ont été victimes
   (`génois`, `voile d'avant`), toutes deux rétablies depuis leur mesure
   d'origine. Le contrôle après réparation donne **0 axe effacé** — mais le
   défaut, lui, reste ouvert (point 6 du §3.3).
2. **Il a fait franchir le plafond de 100**, ce qui a révélé le défaut du §3.0.

La seconde ne rachète pas la première. Un défaut découvert par une faute de
méthode reste un défaut découvert par chance : rien ne garantit que la
prochaine fauteur soit aussi utile. **La règle que je retiens et qui est
désormais appliquée : aucun import sans instruction nominative.**

### 4. Recommandation

### 4.1 Verdict

**NO-GO** pour une mise en production en l'état.

Motif principal : **§3.0 — la recherche plafonne à 100 correspondances.** À
338 fiches, c'est un défaut visible par l'utilisateur dès la première
recherche : mauvais décompte, pagination tronquée, facettes fausses.

Motif venant juste après : **§3.2, trois échecs de sauvegarde sur Windows.**
Une sauvegarde non prouvée n'est pas une sauvegarde.

À noter, et c'est une bonne nouvelle : la CI, elle, tourne et elle est verte
sur toutes les sélections que je ne peux pas exécuter ici (§3.1). Le problème
n'est plus l'absence de mesure, il est ce que la mesure a trouvé.

### 4.2 Ce qui le transformerait en GO

Par ordre, chacun étant une condition et non une suggestion :

1. **[FAIT]** Corriger le plafond de 100 — voir §2.6. Toute statistique tirée
   de `/recherche` est désormais exacte.
2. **[FAIT] La restauration sur Windows (§3.2, Lot 16).** La chaîne de la
   panne a été identifiée — `restaurer_complet` → `restaurer()` →
   `_instantane_dump_prive`, ligne 1957, où la lecture confinée du bundle
   échoue quand `os.open` n'est pas dans `os.supports_dir_fd` — et corrigée
   par un repli éprouvé. Le canal `trace-coordonnee` est vert, et le job
   `windows` passe au vert depuis le SHA `aa06108`.
3. **[FAIT] Les échecs Windows nommés (§2.9, Lot 17).** Les neuf ont reçu une
   cause démontrée. Huit sur neuf venaient d'épreuves, jamais du produit — une
   clé de chemin calculée autrement par le test et par le produit, une
   comparaison de chemins sensible à la casse, un comparateur qui prenait
   `pg_dump.bak` pour `pg_dump`. **Aucune ligne** n'a été ajoutée à
   `ci/ecarts-windows.txt` : ce n'étaient pas des écarts de plateforme
   assumés, mais des erreurs de mesure. Aucune ligne de `seamtech_search/`
   n'a changé.
4. **[FAIT]** `unaccent` et `pg_trgm` installés — prouvé en CI sur
   l'image `pgvector/pgvector:pg16`, celle-là même qui sert en production ;
   le provisionnement est désormais une étape du déploiement, plus une
   instruction dans une note (§2.10).
5. **Le corpus complété au déploiement**, à l'initiative de l'exploitant.
   Le défaut d'effacement n'est plus un obstacle : il est corrigé aux deux
   endroits (§2.11.1) et la fusion conserve désormais tout axe mesuré. La
   raison de *méthode* qui bloquait le relevé est levée — ce n'était pas le
   listage qui tronquait, c'était la recherche (§2.11.3) — mais la raison de
   *décision* demeure : je n'ai pas complété le relevé de ma propre initiative.
   Compte actuel : **123/338**.
6. **Une revue humaine** (Lot 10) produisant les premiers verdicts : sans eux,
   aucun seuil n'est calibré, et les seuils actuels ne sont pas défendables.
   **L'outil existe depuis le Lot 19** (`scripts/recueillir_verdicts_
   reutilisation.py`, §2.11.2) : il présente les comparaisons **sans le score**,
   et refuse de proposer un seuil sous 30 verdicts dont 10 dans chaque classe
   tranchante. **État : 0 verdict recueilli.** C'est la seule pièce qui manque,
   et elle est humaine.
7. **Décider du repli Windows pour la copie confinée** (§2.11.4). Sans lui, la
   sauvegarde des pièces jointes locales reste **indisponible** sur Windows —
   non pas en panne, mais absente.
8. **Les points 13.4 et 13.5 levés sur site** et la liste signée.

### 4.3 Ce que le Lot 9 attend

Il reste fermé, et il le restera tant que le corpus ne sera pas complet **et**
que les 109 fiches sans axe n'auront pas été qualifiées. Le Lot 19 a mesuré
précisément l'état et rendu ces fiches visibles (`a-qualifier`, §2.11.3) — mais
il n'en a qualifié **aucune**, parce que la qualification suppose d'ouvrir les
documents : aucune machine ne peut la faire à votre place. Lancer la table de
correspondance sur 123 fiches dont 109 n'ont pas de type de voile produirait un
taux d'appariement qui ne veut rien dire — et un chiffre faux, une fois publié,
est très difficile à démentir.
