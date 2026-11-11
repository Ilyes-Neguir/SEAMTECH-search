# Lot 14 — rapport et recommandation

Ce document répond à une seule question : **peut-on mettre en production ?** Il ne
cherche pas à être rassurant. Chaque affirmation porte sa preuve, et ce qui
n'est pas prouvé est dit non prouvé.

**Période couverte** : lots 9 → 14. **État du dépôt** : branche
`arena/765ebdcf-seamtech-search`, issue de `af31922`.

---

## 1. Réponse courte

**NON — ne pas mettre en production en l'état.**

La raison n'est ni une panne, ni un test rouge isolé : c'est qu'**il n'existe
aucune preuve d'intégration complète au moment où ce rapport est écrit**. La CI
du serveur n'a pas exécuté une seule épreuve sur le dernier SHA — voir §3.1,
qui est le point le plus important de ce document.

Ce n'est pas un NON de principe. Les lots livrés depuis le début (0 → 8, puis
11, 12, 13) tiennent sur des mesures rejouables. Ce qui manque n'est pas du
travail de développement, c'est **le passage sur site** : corpus complet,
revue humaine, extensions PostgreSQL, et une CI qui tourne réellement.

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

## 3. Ce qui n'est PAS prouvé — la partie que ce rapport ne doit pas arranger

### 3.1 La CI du serveur n'a exécuté aucune épreuve sur le dernier SHA

**C'est le point bloquant.** Au SHA `fb4df9d9`, le job `backend (3.11)` échoue
à l'étape 11, « Installer redis-server pour le test de redémarrage réel », par
`apt-get` code 100. Conséquence directe, lisible dans les étapes : **12, 13,
14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25 → `skipped`.** Les suites
SQLite, PostgreSQL, la file durable, les garde-fous, la couverture et l'audit
**n'ont pas tourné**.

Ce n'est pas une régression de mon fait : la même étape 11 échoue à `a9dba9e4`
(14:14), avant mes trois derniers commits, et `Qualification native` échoue
identiquement (`apt-get postgresql`) sur **tous** les SHA récents. C'est une
panne d'infrastructure GitHub, pas du code.

Mais la conséquence est la même, et elle n'est pas négociable : **je n'ai
aucune preuve d'intégration CI au SHA exact.** Les 2751 épreuves locales du
§2.4 ne couvrent pas les sélections `postgres`, `s3rver` et `redis_queue`,
justement celles que la CI aurait dû exercer.

Tentatives de relance, consignées pour qu'elles ne soient pas refaites :
`gh run rerun` refusé (« workflow file may be broken ») sur les deux runs, bien
que les huit fichiers de workflow se chargent sans erreur ;
`gh workflow run` impossible — `ci.yml` ne déclare pas `workflow_dispatch` et
le jeton n'a pas l'autorisation (403). **Seule une poussée relance la CI.**

### 3.2 Onze échecs réels sur Windows, désormais lisibles et non traités

Grâce au Lot 11, l'étape 21 publie enfin ses causes. Elle annonce **11 échecs
hors périmètre connu sur 61**. Ils sont réels, et je ne les ai pas corrigés :

| Famille | Nombre | Nature |
|---|---|---|
| Casse des chemins Windows | 4 | `assert 'c:\\users\\…' == 'C:\\Users\\…'` — un chemin normalisé en minuscules comparé à un chemin brut |
| `pg_dump.EXE` vs `pg_dump` | 1 | même cause : casse de l'exécutable |
| Concurrence à l'indexation | 2 | « Right contains one/2 more items » |
| Sauvegarde / restauration | 3 | `assert 1 == 0` |
| `test_rebuild_laisse_la_recherche…` | 1 | `assert False` |

La casse des chemins est un défaut de fond, pas un détail de test : sur
Windows, `c:\users\…` et `C:\Users\…` désignent le même fichier, et comparer
l'un à l'autre par chaîne est faux. Les trois échecs de sauvegarde sont à
prendre au sérieux en priorité — une sauvegarde qui échoue silencieusement est
le pire défaut possible.

**Je ne les ai pas corrigés pour deux raisons** : ils sortent du mandat
ordonné (11 → 13 → 14), et les corriger sans machine Windows, c'est écrire du
code qu'on ne peut pas compiler. Le seul compilateur Windows réel est le
runner.

### 3.3 Le reste, sans détour

| # | Point | État |
|---|---|---|
| 1 | Corpus 123 / ~338 | **incomplet**, import arrêté sur instruction |
| 2 | 109 fiches sans axe | **à qualifier** par lecture humaine |
| 3 | Lot 9 (table de correspondance) | **fermé** — le dénominateur serait faux |
| 4 | Calibrage des seuils | **bloqué** — 0 verdict humain saisi, et je ne le lance pas moi-même |
| 5 | `unaccent` + `pg_trgm` | **à installer par l'exploitante** (scripté, idempotent, vérifié) |
| 6 | Défaut `ajouter` : un réimport sans axe **efface** l'axe mesuré | **ouvert** — perte de données silencieuse ; deux fiches en ont été victimes, réparées. La question « écraser, refuser, ou fusionner ? » vous revient |
| 7 | PR #44 | **brouillon** — c'est le bloquant réel à la fusion ; aucun contrôle n'est requis sur `main` |
| 8 | Banc `pgserver` local sans contrib | ~36 échecs **environnementaux**, la CI fait foi |

---

## 4. Recommandation

### 4.1 Verdict

**NO-GO** pour une mise en production en l'état.

Motif principal : §3.1 — aucune preuve d'intégration au SHA exact. Motif
second, mais qui deviendrait principal à la première sauvegarde : §3.2, trois
échecs de sauvegarde sur Windows.

Ce NO-GO ne dit pas que le logiciel est mauvais. Il dit que **personne ne peut
affirmer qu'il est bon**, faute d'avoir fait tourner les épreuves.

### 4.2 Ce qui le transformerait en GO

Par ordre, chacun étant une condition et non une suggestion :

1. **Une CI qui exécute ses épreuves** sur le SHA retenu — toutes sélections
   confondues, `postgres` et `redis_queue` comprises. Sans cela, rien de ce
   qui suit ne peut être affirmé.
2. **Les trois échecs de sauvegarde élucidés.** Une sauvegarde non prouvée
   n'est pas une sauvegarde.
3. **Les onze échecs Windows nommés**, c'est-à-dire soit corrigés, soit
   inscrits dans `ci/ecarts-windows.txt` avec leur raison — jamais oubliés.
4. **`unaccent` et `pg_trgm` installés** sur la base de production, et
   `/health` les annonçant.
5. **Le corpus complété au déploiement**, à l'initiative de l'exploitant —
   et **après** correction du défaut du point 6 du §3.3, sous peine d'effacer
   des axes déjà mesurés.
6. **Une revue humaine** (Lot 10) produisant les premiers verdicts : sans eux,
   aucun seuil n'est calibré, et les seuils actuels ne sont pas défendables.
7. **Les points 13.4 et 13.5 levés sur site** et la liste signée.

### 4.3 Ce que le Lot 9 attend

Il reste fermé, et il le restera tant que le corpus ne sera pas complet **et**
que les 109 fiches sans axe n'auront pas été qualifiées. Lancer la table de
correspondance sur 123 fiches dont 109 n'ont pas de type de voile produirait un
taux d'appariement qui ne veut rien dire — et un chiffre faux, une fois publié,
est très difficile à démentir.
