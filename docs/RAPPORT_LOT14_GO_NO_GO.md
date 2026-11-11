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
| 5 | `unaccent` + `pg_trgm` | **à installer par l'exploitante** (scripté, idempotent, vérifié) |
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
2. **[RESTE] La restauration sur Windows (§3.2).** `restaurer --coordonnee`
   sort en 1 ; le site exact de l'échec, dans `restaurer_complet`, n'est pas
   encore identifié. Une sauvegarde qu'on ne peut pas restaurer n'est pas une
   sauvegarde — et si des postes Windows sont déployés, c'est bloquant.
   Prochaine marche : faire publier le `stderr` du CLI par l'étape de verdict.
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
