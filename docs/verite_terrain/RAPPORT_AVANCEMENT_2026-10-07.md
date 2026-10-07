# Rapport d'avancement — 2026-10-07

**Ce qui a été fait, ce qui est prouvé, ce qui reste ouvert.**

| | |
|---|---|
| Branche de session | `arena/7da80c2f-seamtech-search` |
| Commit de tête | voir l'en-tête de `docs/verite_terrain/TRACABILITE_LIVRAISON.md`, section « Revue indépendante 2026-10-07 » (SHA exact + liens de runs) |
| Pull request | [#35](https://github.com/Ilyes-Neguir/SEAMTECH-search/pull/35) — ouverte, **non fusionnée** |
| Base de comparaison | `main` = `4e374f6` (« docs: record CI diagnoses and green validation ») |
| Volume de la passe | 42 fichiers, **+7 005 / −332** lignes depuis `4e374f6` |
| Deuxième revue indépendante | reçue le 2026-10-07 **après** le premier handoff : quatre constats (F1 → F4) à fermer avant toute revendication de fin — traités au §2.7 |

> **Règle de lecture de ce rapport.** Chaque affirmation est marquée
> **PROUVÉ (local)**, **PROUVÉ (CI)**, ou **PENDING**. Les preuves brutes
> (commandes + sorties) sont dans `REVUE_INDEPENDANTE_2026-10-07.md` ; les
> écarts corrigés sont numérotés **E-1 → E-27** dans
> `ECARTS_ET_CORRECTIFS_2026-10-06.md`. Aucune preuve qui n'aurait pu venir que
> de la CI n'est présentée ici comme acquise avant d'avoir lu le job
> correspondant.

---

## 1. Résumé

La mission demandait dix chantiers. Cette passe a livré le **socle de
durabilité** (file Redis, worker séparé, reprise après crash, lettre morte,
idempotence), la **vérification d'intégrité du stockage objet** (preuve par
relecture des octets, purge interdite sans preuve), la **preuve de partage
web/worker** (fichiers, quarantaine, modèles, permissions), et surtout des
**tests d'exécution réelle** : vrais processus worker tués au SIGKILL, vrai
serveur Redis redémarré, vrai PostgreSQL, vraie pile Compose en CI.

**Cinq défauts réels ont été trouvés et corrigés**, dont trois qui rendaient la
CI rouge sans rapport avec le code applicatif :

| # | Défaut | Effet | Corrigé |
|---|---|---|---|
| E-22 | Le job CI `integration` ne démarrait jamais le conteneur `worker` | les preuves multi-conteneurs ne s'exécutaient pas | nouveaux tests + inventaire CI |
| E-23 | `SEAMTECH_STORAGE_VERIFY_REREAD` mal lue (`KeyError`) | le conteneur `web` du compose documenté **ne démarrait plus** | `config.py` + variable déclarée |
| E-24 | `max_task_attempts` offrait une tentative de trop | 3 tentatives réglées ⇒ 4 réellement | garde corrigée |
| E-25 | `pytest.skip(allow_module_level=True)` compté comme « sauté » même quand la sélection `-m` a désélectionné le module | 2 jobs rouges pour des tests jamais demandés (`sauvegarde`, `backend`) | fixture autouse par test |
| E-26 | `./data` / `./logs` créés par Docker en root, application en `seamtech` | imports/rapports impossibles en conteneur (échec du job `integration`) | préparation + **preuve d'écriture** en CI, exigence documentée |
| E-27 | Verrou pnpm en retard : 2 avis HIGH (`sharp`, `source-map-js`) | jobs `frontend` et `securite-dependances` rouges | verrou régénéré, audit à 0 |
| E-28 | Téléchargements redirigés (302) vers l'endpoint S3 **interne** (`minio:9000`), non résolvable depuis un poste | rapport/original **intéléchargeables depuis un autre poste** | octets servis par l'API par défaut ; redirection seulement vers un endpoint public déclaré |
| E-29 | Les politiques MinIO étaient lues par `mc` depuis un chemin de l'**hôte** | provisionnement impossible (`recette-locale` rouge) | politiques copiées DANS le conteneur (`docker cp`) |
| E-30 | Le helper e2e supposait le poste déjà sur la file | 2 scénarios de concurrence rouges | le helper navigue lui-même et attend la révision armée |
| E-32 | La CI n'exposait pas `MINIO_ROOT_*` à l'**étape** de provisionnement | `sauvegarde` et `recette-corpus-reel` rouges avant tout test | variables au niveau de l'étape (jamais du job), tests de contrat CI |
| E-33 | Test e2e figé sur `champ-materiau`, champ inexistant (familles préfixées) | scénario de concurrence rouge | champ réel choisi dynamiquement, unique à l'écran |
| E-34 | Le tableau des champs gardait le brouillon local après « Recharger la fiche à jour » | l'opérateur relisait sa saisie périmée en croyant lire celle du collègue | brouillons oubliés (un champ après enregistrement, tous après rechargement) |
| E-35 | La recette écrivait la sauvegarde avec l'identité **applicative** (et retombait sur `minioadmin` en silence) | `recette-corpus-reel` rouge ; faux ALLOW masqué par la racine | identité choisie par bucket, **aucun** repli, échec clair |
| E-36 | Preuve intermittente : champs choisis avant leur rendu (1 test flaky), garde-fou muet sur le nom du test, build MinIO tributaire du réseau | `e2e` + `recette-corpus-reel` rouges sur un run pull_request alors que le push du même SHA était vert | attente du rendu des champs, annotation nommant les tests instables, réessais bornés du build |

État de la CI au moment de la rédaction : **12/12 jobs verts sur `c65b7d8`**,
sur le run push `37642194285` ET sur les deux runs pull_request du même SHA
(`37642211480`, `37642211503`) — dont l'étape « Run multi-session E2E
(concurrence, sessions indépendantes) », lue `success` (`gh api
actions/jobs/<id>`). Un run vert n'est revendiqué que parce qu'il a été LU ;
l'historique complet des lectures est au §5.

**Deuxième revue indépendante (2026-10-07) : quatre constats, quatre
corrections.** Le relecteur a rouvert la mission sur des défauts que la
première passe n'avait pas vus — la protection par révision existait, mais
elle était **contournable** (l'écran pouvait écrire sans révision après un
échec de lecture, les champs et la révision étaient lus par deux requêtes
séparées, les décisions n'étaient pas liées à la révision revue) et le
harnais « hors ligne » était présenté plus largement que ce qu'il protège
(sockets **Python** seulement). Les quatre constats sont traités au §2.7 ; le
détail du périmètre du harnais est dit au §2.5. **Aucune revendication de fin
d'ingénierie n'est faite dans ce rapport tant que les exécutions CI de cette
passe n'ont pas été lues.**

**Verdicts séparés demandés par la mission :**

* **Tests développeur** : **ATTEINT** — 1180 tests passés localement (base
  PostgreSQL et Redis réelles, 264 s), porte de couverture franchie sans
  abaissement (86,65 %), `tsc` + build de production OK, et **CI 12/12 verte sur
  `c65b7d8` (push ET pull_request)** y compris les preuves qui exigent Docker,
  MinIO réel et un vrai navigateur (§5, §5bis).
* **Pilote atelier contrôlé** : **NON atteint** — exige un drill de restauration
  humain indépendant, une destination de sauvegarde de production distincte, la
  validation humaine du corpus réel (0 fiche sur 7 validée à ce jour) et la
  recette trois postes sur le serveur cible.
* **Production** : **NON revendiquée** — voir §6, liste exhaustive.

**Handoff (mission, point 8)** : **dernier commit de CODE `c65b7d8`** ; branche
`arena/7da80c2f-seamtech-search` ; PR **#35** (ouverte, **jamais fusionnée**) ;
runs lus, sur les deux déclencheurs, pour le code : `37642194285` (push, 12/12),
`37642211480` et `37642211503` (pull_request, 12/12). Le commit de
documentation `b1eb5a9` a lui aussi été lu : push `37643239027` **12/12** et
pull_request `37643251469` / `37643251577` **12/12**, étape « Run multi-session
E2E » `success` — la règle « un run vert sur push ne vaut pas pour le
pull_request » (E-36) est donc satisfaite sur ce couple aussi. **Convention de
preuve** : tout commit postérieur qui ne touche aucun fichier de code
(`docs/` uniquement, vérifiable par `git show --stat`) hérite de ces preuves ;
dès qu'un fichier de code change, une nouvelle lecture des runs des deux
déclencheurs est obligatoire. Détail par constat : §4 (E-22 → E-36) ; tests
ajoutés : §3 et §4 ; défauts de code restants : **aucun connu** à ce jour ;
portes restantes : §6 et §8. **La revue de ce travail par l'agent
d'implémentation n'est pas une approbation indépendante** : une revue externe
reste pendante.

---

## 2. Chantier par chantier (les 10 workstreams de la mission)

| # | Chantier | État dans cette passe |
|---|---|---|
| 1 | Relecture baseline, écarts doc/code, puis implémentation | **fait** — registre `ECARTS_ET_CORRECTIFS_2026-10-06.md` (E-1 → E-27), conversions sous `seamtech_search/` |
| 2 | Durabilité Redis (file, worker séparé, états, reprise, lettre morte, idempotence) | **fait et prouvé** — `redis_store.py`, `jobs.py`, `worker.py`, `worker_service.py` ; 37 tests réels + 5 tests multi-processus |
| 3 | S3/MinIO (fournisseur unique, intégrité, clés, quarantaine, moindre privilège) | **code + tests écrits** ; exécution contre un MinIO vivant : **CI uniquement** ; moindre privilège **PENDING** |
| 4 | Glisser-déposer / comptabilité des imports | **fait** — comptage par fichier, motifs d'échec par fichier, refus 507, anti-traversée, reprise de lot |
| 5 | Recherche (indexation, visibilité après import, accents, dimensions, filtres) | **vérifié sur socle** — suites SQLite + PostgreSQL ; pertinence sur archives réelles **PENDING** (exige le jeu de requêtes étiqueté par un humain) |
| 6 | Protection du flux de fabrication (validé / non validé / révisé) | **vérifié sur socle** — états de fiche, badge « Non vérifiée », journal de validation, verrous optimistes ; acceptation atelier **PENDING** |
| 7 | Sauvegarde / restauration | **automatisée** (job CI `sauvegarde`, 19+ tests, dont 50 000 fiches) ; drill humain indépendant + destination de production **PENDING** |
| 8 | Déploiement local (compose durci, non-root, secrets, liveness) | **fait** — 6 services, conteneurs non-root, endpoints privés, `/health` complet ; preuve « réseau externe bloqué » **PENDING** (à exécuter sur le serveur) |
| 9 | Scénario trois postes au navigateur | **spécifié + exécuté en CI** (`e2e`, Playwright) ; non exécutable dans ce bac à sable (navigateurs non installables) |
| 10 | Livrables (correctifs, tests, docs, rapport, portes restantes) | **ce document** + `REVUE_INDEPENDANTE_2026-10-07.md` + traçabilité |

### 2.1 Durabilité des imports (chantier 2) — détail

* **Worker séparé** : service `worker` du compose (`python -m
  seamtech_search.worker_service`), healthcheck `--verifier`, l'API web ne
  traite plus les imports en arrière-plan
  (`SEAMTECH_WEB_WORKER_ENABLED=false`).
* **États explicites** : `pending → running → completed | failed |
  upload_incomplete | needs_review | cancelled`, plus **lettre morte**
  (`deadletter_task`, `replay_deadletters`) — `jobs.py` l.420‑421.
* **Revendication atomique** : scripts Lua dans `redis_store.py`
  (`revendiquer_tache`, `rafraichir_revendications`,
  `revendication_appartient_a`, `liberer_revendication`) — un worker ne peut
  pas voler la tâche d'un autre, et un worker « zombie » ne peut ni libérer ni
  écrire l'état terminal après reprise (test dédié).
* **TTL de revendication** (300 s) + battement de cœur (`_BattementDeCoeur`) :
  un worker tué laisse une revendication expirante, la reprise
  (`reprendre_taches_orphelines`, `reconcilier_file`) remet la tâche en file
  sans dupliquer documents ni rapports.
* **Tentatives bornées** : `SEAMTECH_MAX_TASK_ATTEMPTS` (défaut 3, **total**),
  backoff `2**n`, raison lisible en base (`failure_reason`), et le worker
  **n'écrase pas** un état terminal déjà écrit par la reprise (garde E-24).
* **Idempotence** : les points de crash testés sont « avant envoi », « après
  envoi objet, avant écriture en base », « après indexation sans marquage » —
  dans tous les cas, reprise sans doublon de fiche ni de document
  (`test_file_durable.py`, 23 tests).
* **Redis indisponible** : en production
  (`SEAMTECH_REQUIRE_DURABLE_QUEUE=true`), l'API répond **503 et ne crée pas de
  job** (jamais d'acceptation « durable » pour une tâche qui ne vit qu'en
  mémoire) ; hors production, la réponse porte explicitement
  `durability: "process_memory"`.
* **Annulation inter-processus** : drapeau Redis + `doit_continuer()` ; le
  worker vérifie l'annulation entre chaque lot.

Preuves multi-processus (`tests/test_relais_file_base.py`, 5 tests) : **vrais
sous-processus `worker_service --une-passe`**, base PostgreSQL jetable par test,
base Redis 1 vidée avant chaque test, SIGKILL en plein import, expiration de
revendication, refus de double revendication par deux workers simultanés.

### 2.2 Stockage objet et intégrité (chantier 3) — détail

Réponse à la question de la revue — *« le SHA-256 des métadonnées est-il validé
contre les octets stockés ? une vérification par la taille autorise-t-elle la
suppression locale ? »* :

* `S3StorageClient.verifier_integrite` renvoie un
  `ResultatVerification(integrite_prouvee, methode, detail)` avec quatre
  méthodes possibles : `relecture_sha256` (l'objet est **relu par l'API** et
  l'empreinte recalculée sur les octets reçus), `checksum_serveur` (empreinte
  calculée par le fournisseur sur l'**objet entier** : `ChecksumSHA256` **et**
  `ChecksumType == FULL_OBJECT`), `metadata_seule`, `taille_seule`.
* **Seules les deux premières prouvent l'intégrité.** `metadata_seule` et
  `taille_seule` sont explicitement **non preuves** : elles ne valident pas les
  octets stockés, et **interdisent la purge** de la copie locale
  (`integrite_prouvee=False`). Donc : **non, une vérification par la taille
  seule n'autorise pas la suppression locale** — c'est ce que verrouille
  `tests/test_verification_integrite.py` (10 tests).
* L'upload demande `ChecksumAlgorithm: SHA256` ; la purge n'a lieu qu'après
  `UploadBatch.all_verified()`.
* Le double de test `tests/s3_en_memoire.py` **conserve les vrais octets**, ce
  qui rend la corruption détectable : trois scénarios prouvés — objet tronqué
  avec métadonnée intacte, objet altéré **de même taille**, objet absent.
* Clés collision-safe (`artifact_object_key`, `first_free_key`), nom/chemin
  d'origine conservés en métadonnées, bucket privé, quarantaine sur upload
  incomplet.
* **Identités restreintes — FERMÉ par le correctif du défaut bloquant** (revue
  du 2026-10-07, constat n° 1) : le déploiement documenté ne donne **plus** le
  compte racine MinIO à `web`/`worker`. `docker-compose.yml` exige désormais
  une identité APPLICATIVE dédiée (`:?` : la composition refuse de démarrer si
  elle manque), `scripts/provisionner_stockage.sh` crée les buckets, le
  versioning et **deux identités distinctes** (application = son bucket
  seulement ; sauvegarde = le sien), et la sauvegarde utilise
  `SEAMTECH_BACKUP_ACCESS_KEY`/`SECRET_KEY` — jamais `MINIO_ROOT_*`. Aucun
  repli silencieux : une identité absente fait échouer clairement
  (`credentials_presentes()`, `/health` → `s3_credentials: absent|dedie|root_like`).
  Preuves : `tests/test_credentials_s3_restreintes.py` (**18 tests locaux + 4
  tests Docker ALLOW/DENY**) ; exécution contre MinIO RÉEL : **CI** (jobs
  `integration`, `sauvegarde`, `recette-corpus-reel`, `recette-locale`).
  Deux défauts de plus ont été trouvés **par la CI** dans cette famille et
  corrigés — la CI ne donnait pas au provisionnement les identifiants dont il a
  besoin (E-32, job `sauvegarde` **redevenu vert**), et la recette écrivait la
  sauvegarde avec l'identité applicative en s'appuyant, en silence, sur un repli
  `minioadmin` (E-35) : le moindre privilège ne se prouve que si les tests ne
  peuvent PAS retomber sur l'administrateur.
* **R2 — optionnel, hors périmètre local-first** : MinIO reste le fournisseur
  unique ; R2/AWS S3 n'est ni testé ni revendiqué, et ne sera traité que si le
  propriétaire le demande explicitement.
* **Exécution réelle contre MinIO : CI seulement** (pas de Docker dans la
  session d'implémentation) — et cette phrase reste vraie après le correctif.

### 2.3 Téléchargements, navigateur, conception d'interface

* Les téléchargements passent par l'API (`…/pieces/{id}/telecharger`,
  `GET /imports/{id}/artifacts/{artifact}`, `POST /open`) : les octets sont
  servis par le serveur, jamais par une redirection vers un nom d'hôte interne
  au conteneur ni vers `localhost` du navigateur. C'est ce qui les rend
  utilisables **depuis un autre poste de l'atelier** — et c'est l'écart E-28,
  corrigé dans cette passe : la redirection présignée par défaut pointait vers
  `minio:9000`, injoignable hors du réseau Docker. Si l'exploitant expose MinIO
  sur le réseau de l'atelier, il peut déclarer
  `SEAMTECH_S3_PUBLIC_ENDPOINT_URL` et retrouver la redirection (moins de charge
  serveur) ; sinon tout passe par l'API.
* L'aperçu et le téléchargement sont distingués (aperçu ≠ téléchargement) ;
  les originaux restent téléchargeables.
* **La conception UI de l'atelier n'a pas été modifiée** : les changements sont
  comportementaux (états, messages d'erreur, motifs par fichier, badges de
  validation) et non visuels.

### 2.4 Édition concurrente — verrou optimiste, refermé après la DEUXIÈME revue

Défaut d'origine : `corriger_champ` écrivait sans comparer l'état lu (« dernier
écrivain gagne »). Deux postes ouvrant la même fiche s'écrasaient donc
mutuellement, sans trace ni avertissement. Une **première** correction avait
introduit la révision optimiste — mais la deuxième revue indépendante
(2026-10-07) a montré que cette protection était **contournable** : elle
existait sans être obligatoire ni liée à ce qui était relu. Les constats F1, F2
et F3 sont traités au §2.7 ; ce paragraphe décrit l'état FINAL du verrou, tel
qu'il est aujourd'hui dans le code.

Mesure de clôture (navigateur, sessions indépendantes) : les **7 scénarios
passent au premier essai, 0 flaky, 0 sauté** (run push `37668475085`, job `e2e`).
Le scénario « décision liée à la révision revue » a été durci à cette occasion :
il exige désormais que le refus ne laisse **aucune ligne d'audit** (une ligne
« valider » ferait croire à une approbation) en plus du 409, de l'état inchangé
et de la relecture obligatoire avant la nouvelle décision.

* migration `021_revision_fiche` : `fiche.revision INTEGER NOT NULL DEFAULT 1`
  (colonne seulement ; les 33 tables sont inchangées) ;
* **F1 — refus fermé** : sans révision, `corriger` et les trois décisions sont
  refusés (**428**) ; le repli pour un client ancien n'existe plus que sous une
  option d'exploitation explicite (`SEAMTECH_REQUIRE_REVISION=false`), jamais
  par défaut, et toute écriture non protégée y est **journalisée comme telle**.
  Une requête de révision en échec côté poste ne peut donc plus désactiver la
  protection ;
* **F2 — lecture cohérente** : `GET /fiches/{code}/etat` renvoie statut +
  révision + champs dans **UN seul instantané** PostgreSQL, et l'écran jette
  toute réponse tardive d'une fiche déjà quittée (jeton de génération) ;
* **F3 — décision liée à la révision revue** : valider, rejeter, rouvrir et la
  validation en lot vérifient statut ET révision dans **le même `UPDATE`**
  (compare-and-swap). Approuver un écran périmé est refusé en **409
  `conflit_decision`**, sans écriture et **sans ligne « valider » trompeuse**
  dans le journal ; l'écran exige alors un rechargement et une nouvelle
  décision explicite. En lot, une fiche qui a bougé est **ignorée avec sa
  raison** (`ignorees[].raison`) et non validée en silence ;
* l'écran Validation affiche le conflit et propose « Recharger la fiche à
  jour ». Design conservé : les seuls ajouts sont des attributs **non visuels**
  (`data-fiche`, `data-revision`, `data-statut`, `data-chargement-etat`) qui
  rendent observable l'état dont dépend la justesse.

Preuves (locales, PostgreSQL réel, HTTP réel) :

* `tests/test_revision_optimiste.py` — **12 tests, 12 passés** : publication de
  la révision, correction à jour acceptée (révision +1), correction périmée
  refusée sans écraser le collègue, **deux enregistrements simultanés** (une
  seule correction passe), refus fermé sans révision (428, aucune écriture,
  aucune ligne de journal), écriture sans verrou seulement sous option
  explicite (et journalisée), décision qui invalide les postes ouverts,
  validation d'un écran périmé refusée sans trace, rejet/réouverture liés à la
  révision revue, lot qui ignore une fiche modifiée depuis la sélection, et
  lecture champs+révision dans le **même instantané** (un espion prouve le
  nombre de requêtes : la variante à deux requêtes échoue le test) ;
* `tests/test_validation_workflow.py` — 17 passés : les scénarios historiques
  de transition de statut rejouent désormais le geste réel d'un poste (relire
  la révision, puis décider) et vérifient que la décision fait avancer la
  révision ;
* `frontend/e2e/concurrence.spec.ts` — **7 scénarios** multi-navigateurs
  (deux postes même fiche et reprise après conflit ; import pendant recherche +
  téléchargement d'octets PDF réels ; session expirée sans faux succès ; refus
  d'administrateur par le **backend** ; décision liée à la révision revue ;
  révision non lue ⇒ écriture bloquée ; réponse tardive qui n'écrase pas la
  fiche courante), dont le garde-fou CI exige exactement `7 passés, 0 sauté,
  0 flaky` — un garde-fou qui compte les scénarios réellement présents dans la
  spec (test ajouté) ;
* `tests/test_garde_fous_preparation.py` — un garde-fou neuf vérifie, par
  analyse AST de **tous** les scripts du dépôt, qu'aucun n'écrit l'état d'une
  fiche sans porter la révision : le repli d'exploitation ne peut pas se
  réintroduire par un script oublié.

**Statut de la preuve navigateur : la CI est le premier exécutant.** Les trois
nouveaux scénarios (7 au total) et la spec hors ligne n'ont **pas** pu tourner
dans cette session (CDN Playwright inaccessible, vérifié à nouveau) : ils sont
exécutés par les jobs CI et ne sont revendiqués qu'après lecture des runs. Les
quatre défauts de test/produit successifs (E-30, E-33, E-34, E-36) et leurs
corrections restent décrits au §4.

### 2.5 Vérification hors ligne — portée CORRIGÉE (constat F4)

Le harnais `scripts/verifier_hors_ligne.py` avait été présenté comme une preuve
« cœur hors ligne ». C'était trop large : il installe un garde réseau qui
n'intercepte que les connexions **Python** de son propre processus et exécute
la pile via `TestClient` — **ni le navigateur, ni Next.js, ni un worker séparé,
ni un sous-processus, ni une bibliothèque native (libpq) ne sont isolés par
ce garde**. Le script a donc été **relabellisé** en diagnostic de processus
Python : en-tête, ligne de portée, champ `portee` du rapport JSON et conclusion
disent tous la même limite, et la conclusion finale ne revendique plus de
fonctionnement hors ligne pleine pile.

La preuve PLEINE PILE, elle, est désormais **acquise et lue** : le job CI
`hors-ligne-reel` a démarré la pile (web + **worker séparé** + front de
production) sous un compte applicatif dédié, posé un blocage `iptables` réel par
uid avec contrôle négatif ET positif, exécuté le parcours navigateur complet et
relevé **0 paquet rejeté**. Annotation produite par le job : « parcours
navigateur verts avec sortie externe RÉELLEMENT bloquée — 0 paquet rejeté
(aucune dépendance externe tentée) ». Portée dite sans embellissement dans
l'en-tête de la spec : le blocage système couvre les processus du compte
applicatif ; le **navigateur** (utilisateur du runner) est couvert par un
garde-fou DANS le test — toute ressource demandée hors boucle locale fait
échouer le parcours (assertion finale : liste vide).

Ce que le harnais garde comme valeur (et qui reste vrai) :

* `--inventaire` : classe chaque capacité en REQUISE ou OPTIONNELLE et dit la
  conséquence exacte d'une absence (OCR étage 3, rendu image, modèles e5,
  conteneurs) ; il vérifie que les points de terminaison configurés (base,
  Redis, S3) sont loopback/privés ;
* `--executer` : garde réseau + parcours réels dans CE processus (démarrage,
  connexion nominative, import d'un dossier réel, rapport PDF, dépôt d'une
  fiche, levée **explicite** du verrou RG11 par le chemin documenté —
  `rouvrir` + `effacer_corrections`, désormais **avec la révision relue**,
  correction + verrou optimiste, recherche, aperçu + original PDF, `/open`) ;
* `--autoriser-externe` : diagnostic (compte sans bloquer).

**Preuve pleine pile, elle, par une épreuve de bout en bout réellement
isolée** : le job CI `hors-ligne-reel` (`.github/workflows/ci.yml`) démarre la
pile complète **sous un compte applicatif dédié** (`seamtech-app`) — web, worker
séparé et front de production — puis **bloque sa sortie réseau au niveau du
système** (chaîne `iptables` dédiée à l'`--uid-owner`, `REJECT` sauf loopback
et `127.0.0.0/8`). Avant l'épreuve, un **contrôle négatif** exige qu'une requête
vers une adresse externe **échoue** (sinon le job s'arrête : « blocage
inopérant ») et un **contrôle positif** exige que la pile locale réponde
encore. Le parcours navigateur
(`frontend/e2e/hors-ligne.spec.ts` : connexion, import, recherche, fiche +
aperçu PDF, téléchargement de l'original ET du rapport) tourne alors dans ce
régime, puis le job relit le **compteur de paquets rejetés** : il doit valoir
**zéro** (sinon « dépendance externe cachée »). Le compteur est remis à zéro
juste avant l'épreuve, pour que les rejets des contrôles eux-mêmes ne soient
pas comptés comme des tentatives de la pile. La spec reçoit en outre
`SEAMTECH_E2E_DATABASE_URL=""` : sans cela, Playwright re-semerait une base
e2e en **supprimant** celle de la pile bloquée — défaut trouvé par relecture du
job avant sa première exécution.

**Statut : ce job n'a JAMAIS tourné** (il exige les runners GitHub). Il est
vérifié statiquement par `tests/test_selection_ci.py` (contrôle négatif,
compteur remis à zéro puis relevé, `REJECT` non nul ⇒ échec, spec navigateur,
variables d'environnement) — c'est un contrat, pas une exécution. La
vérification hors ligne pleine pile reste donc **OUVERTE** tant que le run n'a
pas été lu.

`tests/test_verification_hors_ligne.py` (5 tests, verts) prouve que le garde
**Python** bloque vraiment : adresse publique refusée et nommée, loopback
accepté, nom non déclaré refusé, mode diagnostic qui compte sans bloquer.

### 2.6 Exploitation

`/health` expose désormais, pour l'exploitant : file (profondeur, workers
vivants, lettres mortes), jobs par état + jobs actifs, état de sauvegarde,
espace disque, type d'identifiants S3 (`s3_credential_kind`) et statut du
versioning du bucket. `worker_service --verifier` rend un diagnostic exploitable
et **refuse de démarrer sans Redis**.

### 2.7 Deuxième revue indépendante — les quatre constats, un par un

| Constat | Ce qui a été trouvé | Ce qui a été fait | Preuve |
|---|---|---|---|
| **F1 — échec ouvert** | l'écran laissait corriger quand la révision n'avait pas été lue (`.catch(() => setRevisionFiche(null))`) et le backend acceptait une écriture sans révision | `require_revision=True` par défaut (valeur lue, pas vérité d'une chaîne) ; refus **428** pour correction ET décisions ; repli legacy only sous `SEAMTECH_REQUIRE_REVISION=false`, journalisé ; l'écran bloque le bouton et l'annonce (`revision-indisponible`) | `test_sans_revision_la_correction_est_refusee_ferme`, `test_ecriture_sans_revision_seulement_sous_option_explicite`, `test_require_revision_actif_par_defaut_et_desactivable_explicitement`, `test_une_configuration_sans_le_champ_reste_fermee` |
| **F2 — champs et révision incohérents** | deux requêtes séparées pouvaient apparier des valeurs périmées avec une révision fraîche ; une réponse tardive d'une autre fiche pouvait écraser l'état affiché | route `GET /fiches/{code}/etat` (statut + révision + champs dans **une** requête SQL) et proxy front ; l'écran n'utilise QUE cet instantané (ouverture ET rechargement), avec jeton de génération qui jette les réponses périmées | `test_etat_fiche_lit_champs_et_revision_dans_le_meme_instantane` (espion : 1 requête exigée), `test_etat_fiche_expose_le_statut_la_revision_et_les_champs_par_http`, scénario e2e « réponse tardive » |
| **F3 — décision non liée à la révision** | `valider` ne portait ni ne vérifiait la révision : incrémenter APRÈS la validation ne prouvait rien — on pouvait approuver une valeur jamais relue | compare-and-swap `statut + révision` dans le même `UPDATE` pour valider, rejeter, rouvrir ; **409 `conflit_decision`** sans écriture ni ligne de journal ; lot : fiche modifiée **ignorée avec raison** ; écran : décision bloquée sans révision relue et rechargement exigé après conflit | `test_valider_un_ecran_perime_est_refuse_sans_aucune_trace`, `test_rejeter_et_rouvrir_sont_lies_a_la_revision_revue`, `test_validation_en_lot_ignore_une_fiche_modifiee_depuis_la_selection`, `test_une_decision_invalide_la_revision_des_postes_restes_ouverts` |
| **F4 — portée du harnais hors ligne** | garde socket Python + `TestClient` ≠ isolation réseau : navigateur, Next.js, worker séparé, sous-processus et bibliothèques natives passent à côté | script **relabellisé** (portée dite dans l'en-tête, le rapport JSON et la conclusion) ; **nouvelle épreuve pleine pile** en CI sous blocage `iptables` réel par uid, avec contrôle négatif, contrôle positif, remise à zéro des compteurs et relevé final `REJECT = 0` ; spec navigateur dédiée | `tests/test_verification_hors_ligne.py` (5), `tests/test_selection_ci.py::test_le_job_hors_ligne_bloque_reellement_la_sortie_et_exige_le_controle_negatif`, `frontend/e2e/hors-ligne.spec.ts` — **exécutée et VERTE** : run push **`37668475085`** (étapes 17→21, annotation « 0 paquet rejeté ») ; garde-fou navigateur ajouté depuis (run **`37670079596`**) |

**Défauts trouvés PENDANT cette passe de correction** (ils n'étaient ni dans la
revue, ni dans le premier handoff) :

* **E-37** — `route_etat_fiche` avait été déclarée sous DEUX décorateurs
  (`@app.get("/gabarits")` puis `@app.get("/fiches/{code}/etat")`) : FastAPI
  enregistrait `GET /gabarits` **deux fois**, dont une fois sous la signature
  `(code)` — `GET /gabarits` répondait **422 « champ code requis »** au lieu de
  la liste des gabarits, et `GET /gabarits/{code}/versions` devenait
  inatteignable. Trois tests l'ont montré (`TestRoutesSansPostgreSQL`,
  `test_routes_protegees_par_token`, `TestRoutesLivePostgreSQL`). Corrigé :
  chaque route sous son seul décorateur.
* **E-38** — un test de la première passe (`test_validation_attribuee_au_compte_connecte`,
  attribution nominative) validait encore **sans révision** : il ne pouvait
  passer que tant que la protection était facultative. Il relit désormais la
  révision avant de décider — c'est-à-dire ce que fait le poste réel.
* **E-39** — le job CI `hors-ligne-reel` (écrit dans cette passe) téléchargeait
  MinIO depuis `dl.min.io`, qui répond **410 Gone** (dépôt archivé) : le job
  aurait échoué avant toute épreuve. Il reconstruit maintenant l'image depuis
  les sources archivées, comme `integration`/`sauvegarde` ; il aurait aussi
  tenté d'écrire ses journaux avec l'utilisateur de l'ordonnanceur (droits
  refusés) et re-semé la base e2e **sous la pile en marche** — trois défauts de
  conception corrigés **avant** la première exécution, par relecture.

* **E-40** — `seamtech_search index --rebuild` est **inutilisable sur
  PostgreSQL** : `DROP TABLE documents` échoue (`DependentObjectsStillExist` —
  `chunk.id_document` et `fiche_piece_jointe.id_document` référencent
  `documents`), puis le chemin de restauration échoue à son tour
  (`TRUNCATE TABLE documents` → `FeatureNotSupported`), ce qui **masque**
  l'erreur initiale. Aucune donnée perdue (le DROP échoue avant d'écrire) ; la
  commande fonctionne sur SQLite. Reproduit dans cette session, **hors du
  périmètre F1–F4** (outil d'exploitation, pas un flux de livraison) → §6.
* **E-41** — scénarios de concurrence **dépendants de l'ordre** : la file de
  validation RÉTRÉCIT d'un scénario à l'autre (les précédents valident), donc
  « il reste 2 fiches » devenait faux ; mesuré en CI (0 fiche pour l'un, 1 au
  lieu de 2 pour l'autre). Chaque scénario CONSTITUE désormais son jeu par
  l'API (`fichesDeTravail`) et la comparaison globale « l'état n'a pas bougé »
  est remplacée par des faits précis (valeur absente partout, journal inchangé,
  statut et révision inchangés).
* **E-42** — assertion de décision **ambiguë** : `message-ok` porte aussi le
  message de rechargement, donc l'attendre par son identifiant de test validait
  AVANT l'application de la décision (flaky mesuré). Le test exige maintenant
  le TEXTE de la décision (« valider enregistré au journal »).
* **E-43** — attente morte de 3×30 s : le test cliquait
  `bouton-recharger-revision`, qui n'existe QUE lorsque la révision est déjà
  introuvable (donc jamais à cet instant) — Playwright attendait un élément
  inexistant jusqu'à la fin du test. L'échec est désormais **créé** (un poste
  ouvre la fiche pendant que la lecture d'état échoue) et la reprise est
  éprouvée (révision relue, champs rendus, bandeau disparu).
* **E-44** — suite live `validation.spec.ts` : la fixture simulait l'ancien
  endpoint `/champs` alors que l'écran ne lit plus que l'instantané `/etat` —
  la révision restait inconnue, l'écran s'arrêtait **fail closed** (comportement
  CORRECT du produit) et le rejet n'était jamais envoyé. Fixture alignée sur le
  contrat réel (instantané avec révision et statut).
* **E-45** — job `hors-ligne-reel`, quatre défauts de mise en œuvre mesurés :
  (a) `sudo` applique son `secure_path` → `python3` résolvait vers
  l'interpréteur SYSTÈME (dépendances absentes, pile morte avant `/health`) ;
  (b) le compte applicatif ne pouvait pas **TRAVERSER** le chemin du dépôt —
  `chmod -R a+rX "$GITHUB_WORKSPACE"` ne corrige pas les ancêtres, d'où « No
  module named seamtech_search » et « Cannot find module …/next » alors que les
  fichiers EXISTAIENT ; (c) `node node_modules/.bin/next` ne peut pas marcher :
  sous pnpm ce fichier est un **shim shell** (`#!/bin/sh`) que node lit comme du
  JavaScript (`SyntaxError`) — on lance désormais le point d'entrée JS du paquet ;
  (d) les sondes `/health` ne portaient pas le jeton de service : le contrat
  exige `X-SEAMTECH-TOKEN` (sinon **401**), y compris pour le contrôle positif
  du blocage réseau.
* **E-46** — spec hors ligne, deux défauts de conception du test lui-même :
  (a) elle attendait un `GET /api/imports/<entier>` alors que l'identifiant
  d'import est un **UUID** hexadécimal (`uuid.uuid4().hex`) — attente
  impossible, qui consommait tout le budget ; l'identifiant est maintenant lu
  dans le lien du rapport affiché à l'écran ; (b) elle cherchait
  « REF-2026-CLIENT123 » — **référence EXTRAITE du PDF**, donc pas un code de
  fiche — dans la recherche MÉTIER ; l'épreuve interroge désormais la recherche
  documentaire (le fichier importé, écran « Fichiers ») ET la recherche métier
  (la fiche réelle 7792-SO du jeu de données).
* **E-47** — étape CI « Run live E2E validation », deux défauts corrigés :
  (a) le helper `ouvrirFiche` de `validation.spec.ts` n'attendait pas la
  **révision** : depuis le verrou optimiste, une décision cliquée pendant le
  chargement de l'instantané est bloquée — **à raison** (fail closed) — donc le
  test devenait instable sans qu'aucun défaut produit n'existe (mesuré : run
  push `37669839566` rouge sur cette étape, **vert au run suivant sans autre
  changement de code**) ; il attend désormais l'état affiché, comme un
  opérateur. (b) Le garde-fou Python de cette étape sortait en erreur **sans
  publier sa raison** (seule annotation : « exit code 1 »), ce qui a obligé à
  diagnostiquer par déduction ; sa sortie est maintenant publiée en annotation
  `::error title=live-validation-garde-fou::…`.


---

## 3. Preuves d'exécution locales (mesurées, rejouables)

Environnement de la session : Linux sans Docker, sans MinIO, sans navigateur
(CDN Playwright bloqué) ; PostgreSQL 16.2 + pgvector 0.8.0 + `unaccent`/`pg_trgm`
(contrib compilés à la main), Redis 7.2.5 et venv Python montés à la main — la
session a été **remise à zéro en cours de passe** et l'outillage a été
reconstruit (voir §4, note de reprise).

| Suite | Sélection | Résultat |
|---|---|---|
| **Suite COMPLÈTE** (dernière exécution, arbre de clôture) | aucune sélection | **1180 passés, 38 sautés, 0 échec** (264 s) |
| Sans service | `-m "not redis_queue and not postgres and not s3 and not perf and not recette_corpus and not integration_docker and not sauvegarde"` | **825 passés, 3 sautés, 315 désélectionnés** |
| PostgreSQL réel | `-m "postgres and not perf and not sauvegarde and not redis_queue"` | **220 passés, 1 sauté** |
| File durable (Redis + PostgreSQL réels) | `-m redis_queue` | **37 passés, 0 sauté** |
| S3 vivant (MinIO) | `-m s3` | **0 passé, 29 sautés** — aucun service S3 ici (§6) |
| Compose 2 conteneurs | `tests/test_compose_partage_worker.py` | **1 sauté** — Docker absent (§6) |
| Contrat de sélection CI | `tests/test_selection_ci.py` | **14 passés** |
| Front (revérifié sur l'arbre de clôture) | `pnpm install --frozen-lockfile` / `pnpm exec tsc --noEmit` / `pnpm build` | **OK / OK / OK** |
| Vérification hors ligne | `python scripts/verifier_hors_ligne.py --executer` | **0 échec**, 7 capacités optionnelles dites, **aucune connexion externe tentée** |
| Lint + compilation | `ruff check .` / `python -m compileall seamtech_search` | **propre / OK** |
| Mise en forme `ruff format` (non exigée par la CI) | `ruff format --diff` sur les fichiers touchés, version d'avant vs version d'après | **aucun écart ajouté** (mêmes 5/14/12 hunks pré-existants) — `ruff format` n'est pas une porte du dépôt, `ruff check` l'est et passe |
| Contrat de provisionnement CI (nouveau) | `tests/test_credentials_s3_restreintes.py` | **18 passés, 4 sautés** (Docker) ; les 2 tests de recette sont ROUGES sur la version d'avant correctif |
| Porte de couverture | `pytest -m "not s3 and not perf" --cov=seamtech_search --cov-report=json` puis `python scripts/coverage_gate.py coverage.json` | **1174 passés, 5 sautés, 39 désélectionnés** (325 s) ; **porte franchie** : global **86,65 %** (plancher 85 %), `api` 90,5 %, `indexer` 95,3 %, `storage` 98,2 %, `worker` 96,5 %, `jobs` 96,5 % — aucun seuil abaissé |

**Comptabilité `skipped` vs `deselected`** (correction demandée par la revue, et
cause du défaut E-25) : `deselected` = tests écartés par la sélection `-m` ;
`skipped` = tests **demandés** qui se sont déclarés inaptes. Un saut au niveau
du **module** (`pytest.skip(allow_module_level=True)`) est compté comme « sauté »
**même lorsque `-m` a désélectionné le fichier** — démontré par une expérience
minimale reproduite à l'identique (`1 passed, 1 skipped, 1 deselected` alors que
le module fautif n'était pas sélectionné). C'est ce qui faisait échouer deux
garde-fous CI sur des tests jamais demandés.

Mécanisme exact, mesuré sur la même commande avant/après correction : les deux
modules fautifs (`tests/test_file_durable.py`, `tests/test_worker_process.py`)
comptent **26 tests**. Avant correction, l'abandon au niveau du module
interrompait la collecte : ces 26 tests n'apparaissaient **nulle part** (ni
exécutés, ni désélectionnés) et le module était reporté comme **2 sauts**.
Après correction (saut par test via fixture autouse) : **5 sauts → 3 sauts**,
**289 → 315 désélectionnés** (+26 = les 26 tests désormais collectés puis
écartés par `-m`). Après la passe « téléchargement » (§4, E-28), la même
sélection compte **825 passés** (8 tests ajoutés). Les 3 sauts restants sont les
sauts OCR légitimes (tesseract absent ici). Le saut unique restant dans la
sélection PostgreSQL correspond à un test conditionné à un service vivant
(bucket S3 / sauvegarde), exécuté par les jobs CI dédiés ; le poids de modèle e5
est, lui, téléchargé explicitement par la CI.

---

## 4. Défauts trouvés par ces tests (détail des plus coûteux)

* **E-25 — sauts fantômes.** Deux modules faisaient
  `pytest.skip(..., allow_module_level=True)`. Le garde-fou du job `sauvegarde`
  (« GARDE-FOU — every round-trip test ran, none skipped ») et celui du job
  `backend` (« 2 Postgres test(s) skipped even though
  SEAMTECH_TEST_DATABASE_URL is set ») échouaient donc à cause de tests jamais
  demandés. Corrigé par une fixture autouse qui ne saute que si le test est
  sélectionné. **Bénéfice mesurable : les garde-fous redeviennent
  informatifs** — un saut inattendu signalera désormais un vrai problème.
* **E-26 — permissions des volumes applicatifs.** `./data` et `./logs` sont
  ignorés par git : quand Docker crée ces dossiers pour un montage, ils
  appartiennent à `root:root`, alors que l'application tourne en `seamtech`.
  Toute écriture (brouillon téléversé, rapport généré, quarantaine) échoue
  alors par permission — **c'est une exigence de déploiement réelle**, pas un
  détail de CI. Le job `integration` prépare désormais ces dossiers pour
  l'utilisateur applicatif et **vérifie que l'écriture fonctionne** dans
  `/app/data` et `/app/logs` ; `docs/DEPLOYMENT.md` le documente pour le
  serveur d'atelier.
* **E-27 — avis de sécurité front.** `sharp < 0.35.5`
  (GHSA-wq5f-xc86-pv6w) et `source-map-js < 1.2.2` (GHSA-68fv-2mgg-jv7q) sont
  deux avis HIGH **corrigibles en amont** : le verrou pnpm était simplement en
  retard. Régénéré avec dépassements explicites ; `pnpm audit --prod` → 0.
* **Instrumentation d'échec.** Un échec du test Compose émet maintenant une
  **annotation GitHub** contenant la cause, l'état des deux conteneurs et leurs
  40 dernières lignes de journal. Cela répond à une contrainte constatée : les
  journaux bruts de job ne sont pas téléchargeables depuis tous les
  environnements (y compris celui de cette session), seules les annotations le
  sont — et c'est grâce à elles que la cause suivante a été lue en une minute
  au lieu d'être devinée.
* **Un défaut du test lui-même, trouvé par cette instrumentation.** La première
  exécution réelle du test Compose (run `37596092737`, job `integration`) a
  échoué avec `error: 'Selected PDF does not exist'`, alors que le
  téléversement, l'acceptation durable (`durability: durable`), la lecture du
  brouillon par le worker et le partage des volumes fonctionnaient (journal du
  web : `POST /imports/upload 200`, `POST /imports/confirm 202 Accepted`). Le
  test **supposait** que le fichier téléversé se trouverait à
  `<brouillon>/fiche.pdf` ; or un téléversement de dossier conserve
  l'arborescence envoyée par le navigateur
  (`<brouillon>/AFFAIRE-PARTAGE-CI/fiche.pdf`). **Le serveur a donc eu raison de
  refuser** — un chemin inexistant ne doit jamais produire un faux succès.
  Le test désigne maintenant le PDF par le chemin rendu par le **scan** (comme
  le fait l'interface) et vérifie, depuis le conteneur worker, que ce chemin
  exact existe et est lisible avant de confirmer l'import.

* **E-29 — le provisionnement lisait une politique restée sur l'hôte.**
  Job `recette-locale`, run `37613980912` :
  `mc: <ERROR> Unable to get policy: open /tmp/tmp.RFNzptK6JB/politique-app.json:
  no such file or directory`. Les fichiers de politique étaient écrits par
  `mktemp -d` sur l'HÔTE puis passés par leur chemin à `mc`, qui s'exécute
  **dans** le conteneur MinIO : il ne voit pas le `/tmp` de l'hôte. Les deux
  politiques sont désormais **copiées dans le conteneur** (`docker cp`, modes
  conteneur nommé et compose) puis lues là. Le défaut n'était pas visible en
  relisant le script : il fallait l'exécuter. Régression verrouillée par
  `test_script_de_provisionnement_lit_les_politiques_DANS_le_conteneur`.
* **E-30 — un test de concurrence qui n'ouvrait pas la fiche sur le poste B.**
  Première exécution du nouveau scénario multi-sessions (e2e, run
  `37613980912`) : `2 failed, 2 passed` — les deux échecs pointaient le helper
  `ouvrirFiche` (`li[data-code="0901-MM"]` introuvable). Cause : le helper
  supposait le poste déjà sur `/validation` ; le poste B venait de se connecter
  et restait sur l'accueil. Corrigé (le helper navigue lui-même) et renforcé :
  il attend désormais que l'écran ait **chargé la révision** avant
  d'enregistrer, sinon le scénario prouverait une écriture sans verrou.
  Le test a donc fait son travail : il a trouvé un défaut de test, pas de
  produit — mais un test faux aurait fait déclarer « concurrence prouvée » à
  tort.
* **E-32 — la CI ne donnait pas au provisionnement les identifiants dont il a
  besoin.** Run `37617039519` : les jobs `sauvegarde` et `recette-corpus-reel`
  échouaient à l'étape « Provisionner les identités restreintes », AVANT le
  moindre test (les étapes de test restaient « skipped » — d'où un `exit 1`
  sans aucune annotation exploitable). Cause : le conteneur MinIO était bien
  démarré avec `docker run -e MINIO_ROOT_USER=…`, mais ces variables n'étaient
  pas exposées à l'**étape** qui appelle `scripts/provisionner_stockage.sh` :
  le script s'arrêtait sur son garde-fou `:?` (« MINIO_ROOT_USER requis »).
  Autrement dit, le refus d'un repli administrateur fonctionnait — c'était
  l'appelant qui était en faute. Correctif : identifiants fournis au niveau de
  l'ÉTAPE (et non du job), pour que les suites de tests de ces jobs continuent
  de tourner avec l'identité **restreinte** et n'héritent jamais du root.
  Vérifié : job `sauvegarde` **vert** au run suivant (`37618670336`).
  Régression verrouillée par trois tests sans service
  (`test_ci_fournit_au_provisionnement_toutes_les_variables_exigees`,
  `test_ci_pointe_le_provisionnement_sur_le_conteneur_minio_hors_compose`,
  `test_ci_garde_l_administrateur_hors_des_jobs_a_identite_restreinte`),
  démontrés ROUGES sur le workflow d'avant correctif.
  Dans le même geste, l'étape `always()` « Check corpus ZIPs (RG13, après
  recette) » dit maintenant `rg13-non-verifie` au lieu de lever un
  `FileNotFoundError` illisible quand l'étape « avant » a été sautée : un
  invariant non vérifié ne doit jamais ressembler à un incident de
  restauration.
* **E-33 — un test qui figeait un nom de champ inexistant.** Depuis E-30, le
  scénario de concurrence ouvrait la bonne fiche, mais cherchait encore
  `input[data-testid="champ-materiau"]` : les fiches réelles portent des champs
  **préfixés par famille** (`materiau.tissu_principal`, `cotes.finie.slu_m`,
  `fiche.designation`…). Vérifié sur la base de recette réelle : la fiche
  `0901-MM` n'a **aucun** champ nommé `materiau`. Correctif : `choisirChamp()`
  choisit un champ réellement présent ET unique à l'écran (un champ de PDF
  `data-zone="true"` d'abord — un nom de champ peut apparaître plusieurs fois,
  une fois par **rang** de zone, et partager alors le même `data-testid` :
  la preuve « la valeur du collègue est intacte » y serait ambiguë). Le
  scénario 1 attend aussi `validation-app[data-revision]` **après** le
  rechargement (le bouton fait deux requêtes : champs puis révision) avant que
  B ne rejoue — sans cette attente, la seconde correction partait sans verrou et
  le test prouvait moins que ce qu'il annonçait.
* **E-34 — l'opérateur relisait sa propre saisie périmée (défaut PRODUIT).**
  Run `37618670336`, étape « Run multi-session E2E » : `1 failed, 3 passed`.
  Le scénario va jusqu'au bout du conflit (bandeau `conflit-revision`, valeur
  du collègue affichée, aucune écriture de B, aucun faux succès, base
  inchangée) puis échoue à une seule assertion :
  `expect(posteB.locator(selecteur)).toHaveValue("MATERIAU-POSTE-A")` reçoit
  `"MATERIAU-POSTE-B"` après le clic sur « Recharger la fiche à jour ».
  Cause réelle : le tableau des champs garde un **brouillon local** par champ,
  état React qui survit au rafraîchissement des données ; l'écran continuait
  donc d'afficher la saisie de B **après** un rechargement pourtant demandé
  explicitement — B croyait lire l'état enregistré, et rejouer sa correction
  périmée était le comportement induit. Correctif (comportemental, aucun
  changement visuel) : le parent demande au tableau d'**oublier** les
  brouillons — un seul champ après un enregistrement réussi (les autres
  saisies non envoyées sont CONSERVÉES : ne jamais perdre de travail en
  silence), tous après un rechargement explicite (l'opérateur demande l'état
  enregistré), et un remontage par fiche pour qu'aucun brouillon ne traverse
  un changement de fiche.
* **E-36 — une preuve INTERMITTENTE n'est pas une preuve (et un garde-fou muet
  l'a caché).** Run pull_request `37640455508` (même SHA que le push vert) :
  `e2e` et `recette-corpus-reel` rouges. Trois défauts distincts, tous corrigés :
  (a) `choisirChamp()` s'exécutait dès que la fiche et sa révision étaient
  chargées, alors que la **liste des champs** arrive par une seconde requête —
  choisir trop tôt levait « aucun champ corrigeable » ; le test était rattrapé
  par un retry, et le garde-fou exigeant « 0 flaky » refusait la preuve avec
  raison (mesuré : **3 passés, 1 flaky**). On attend maintenant le premier champ
  rendu avant de choisir (attente d'état, exigence inchangée).
  (b) Le garde-fou disait « 1 flaky » **sans nommer le test** : il liste
  désormais les tests en échec ET ceux rattrapés par un retry (statuts
  `unexpected` / `flaky` du rapport JSON) — c'est cette annotation qui a
  désigné le scénario de conflit et permis de le durcir au lieu de relancer à
  l'aveugle.
  (c) `recette-corpus-reel` est tombée sur l'étape **réseau** « Build MinIO
  image from archived sources » (clone GitHub + `docker build`) : un incident
  transitoire devenait un échec de recette, toutes les étapes de recette
  restant sautées. `scripts/construire_image_minio.sh` réessaie chaque étape
  réseau (3 tentatives, dites), écrit son Dockerfile dans un fichier — un
  heredoc déjà consommé ne peut pas alimenter une seconde tentative — et
  ÉCHOUE toujours si les 3 tentatives échouent : l'image doit exister et
  passer le fumigène (binaires + alias `local`). Régression verrouillée par
  `tests/test_construire_image_minio.py` (8 tests).
  Leçon retenue et inscrite ici : **un job vert une fois ne prouve pas** ; il
  faut le run pull_request ET le run push, et un garde-fou doit nommer ce
  qu'il mesure.
* **E-35 — la recette écrivait la sauvegarde avec l'identité applicative.**
  Même run, job `recette-corpus-reel` :
  `test_10_sauvegarde_restauration_base_neuve` échoue sur
  `StorageError: Could not check whether backups/seamtech-search-…dump exists
  in bucket seamtech-…`. Le client S3 du test était construit avec
  `SEAMTECH_S3_ACCESS_KEY` **quelle que soit la cible** — y compris pour
  `seamtech-backups`, sur lequel l'identité applicative restreinte n'a
  **aucun** droit. Deux défauts pour le prix d'un : (a) le client visait le
  bucket de sauvegarde avec la mauvaise identité ; (b) les constantes du test
  retombaient en silence sur `minioadmin`/`minioadmin123` quand la variable
  d'environnement manquait — un repli administrateur qui rendait la recette
  verte pour de mauvaises raisons (l'ALLOW n'existait que parce que l'identité
  en place était encore la racine MinIO). Correctif : identité choisie d'après
  le bucket visé (sauvegarde → `SEAMTECH_BACKUP_ACCESS_KEY`/`_SECRET_KEY`),
  **aucun** repli (défaut vide → échec clair et nommé), et refus explicite si
  l'identité de sauvegarde manque. Le `StorageError` de `first_free_key` n'est
  **pas** affaibli : il protège l'écriture sur une clé peut-être occupée.
  Régression verrouillée par deux tests sans service (dont un contrôle AST que
  les défauts d'environnement sont vides).
* **E-31 — le nouvel outil hors ligne violait RG14.** Le garde-fou RG14
  interdit `socket`/`urllib` dans `scripts/` ; l'outil de vérification les
  importe **par fonction** (intercepter et bloquer les connexions). Exception
  documentée dans le garde-fou lui-même + marqueur dans le module +
  `docs/DEPLOYMENT.md` — jamais pour le service, qui continue de ne rien
  importer de réseau (c'est ce que vérifie le garde-fou).

* **E-28 — téléchargements inaccessibles depuis un autre poste.** La première
  exécution *réussie* de l'import Compose a laissé apparaître la vraie panne :
  le rapport était bien généré, mais `GET …/artifacts/report_pdf` répondait
  **302 vers l'endpoint interne** (`minio:9000`) et le navigateur d'un poste
  d'atelier ne peut pas résoudre ce nom. Le test le plus précieux ici est celui
  qui a *échoué* : sans la pile réelle, le défaut restait invisible (boto3
  doublé ne résout rien). Correctif : par défaut l'API sert les octets
  (fichier local, sinon le cache est reconstitué depuis le stockage, sinon
  l'objet est transmis en flux) ; la redirection n'est émise que vers un
  endpoint **public** déclaré. `/open` sert en plus un original dont la copie
  locale a disparu **sans jamais réécrire dans l'archive** (cas d'une
  restauration). Cinq tests dédiés (`tests/test_telechargement_navigateur.py`)
  plus la mise à jour du garde-fou historique `tests/test_url_presignee_302.py`
  (15 tests) figent ce contrat.

---

## 5. Intégration continue

Les liens exacts et l'état de chaque job sont consignés dans
`docs/verite_terrain/TRACABILITE_LIVRAISON.md` (§ « Revue indépendante
2026-10-07 »), avec le SHA : dernier commit de CODE **`c65b7d8`** (push
`37642194285`, pull_request `37642211480` / `37642211503`, **12/12 verts** —
dont l'étape « Run multi-session E2E (concurrence, sessions indépendantes) »),
et commit de documentation **`b1eb5a9`** (push `37643239027`, pull_request
`37643251469` / `37643251577`, **12/12 verts**). Ce que chaque job apporte et **ne peut apporter
qu'à lui** :

| Job | Preuve exclusive |
|---|---|
| `backend` (3.11/3.12/3.13) | suites PostgreSQL + perf, garde-fou « aucun saut inattendu » |
| `frontend` | `tsc`, `pnpm audit --prod`, `pnpm build` |
| `docker` | images construites + démarrage réel + sonde `/live` |
| `integration` | **MinIO réel, PostgreSQL réel, Redis réel, pile Compose à deux conteneurs** (dont la preuve de partage web/worker) |
| `e2e` | navigateur Playwright (parcours complets, y compris multi-sessions) |
| `sauvegarde` | aller-retour sauvegarde→destruction→restauration, y compris 50 000 fiches |
| `recette-corpus-reel` | ingestion du corpus réel de démonstration |
| `recette-locale` | recette locale du poste cible |
| `ocr` | chaîne OCR (tesseract) |
| `securite-dependances` | pip-audit + pnpm audit selon la politique du dépôt |
| **`hors-ligne-reel`** (nouveau) | parcours navigateur **pleine pile** web + worker séparé + front de production, sortie réseau du compte applicatif **réellement bloquée** (iptables par uid), contrôle négatif obligatoire, compteur de paquets rejetés à zéro — le seul job qui ferme F4 |

**Historique de la passe** (chaque ligne = un run LU, pas supposé) :

1. `37613980912` — `recette-locale` rouge : les politiques MinIO étaient lues
   par `mc` depuis un chemin de l'hôte (E-29). Corrigé, plus
   `e2e` rouge sur le helper de navigation (E-30).
2. `37617039519` (push `948130d`) — 4 verts (`docker`, `ocr`, `frontend`,
   `securite-dependances`), **`e2e`** rouge (E-33 : sélecteur `champ-materiau`
   inexistant), **`sauvegarde`** et **`recette-corpus-reel`** rouges (E-32 :
   `MINIO_ROOT_*` absent de l'étape de provisionnement),
   `integration`/`recette-locale`/`backend` verts.
3. `37618670336` (push `3e13c7f`) — **`sauvegarde` VERTE** (E-32 corrigé) ;
   restent rouges **`e2e`** (`1 failed, 3 passed` : E-34, défaut produit du
   brouillon conservé après rechargement) et **`recette-corpus-reel`**
   (`test_10` : E-35, identité applicative sur le bucket de sauvegarde + repli
   `minioadmin` silencieux). Les autres jobs sont verts, y compris `backend`
   3.11/3.12/3.13, `integration` (MinIO réel) et `recette-locale`.
4. `06667a8` (E-34 + E-35 corrigés, 5 tests de régression) — push : **11 jobs
   verts sur 12**, `recette-corpus-reel` **VERTE** (E-35 fermé, identité de
   sauvegarde devant MinIO réel) mais `e2e` rouge ; le run pull_request du même
   SHA a `e2e` VERT : l'échec est **intermittent**, pas une régression.
5. `5805eb3` (sélecteur exact + garde-fou bavard) — push : **12/12 VERT**,
   `e2e` compris (les 4 scénarios multi-navigateurs passés au premier essai).
   MAIS le run pull_request `37640455508` rougit `e2e` et
   `recette-corpus-reel` : le nouveau garde-fou a parlé — « mesuré 3 passés,
   1 flaky » — et la recette était tombée sur le **build réseau** de l'image
   MinIO. Une preuve intermittente n'est pas une preuve : les deux causes sont
   corrigées au point suivant.
6. `c65b7d8` (E-36) — **push ET pull_request verts, 12/12 sur les trois runs**
   (`37642194285` push, `37642211480` et `37642211503` pull_request) ; l'étape
   « Run multi-session E2E (concurrence, sessions indépendantes) » est
   **`success`** — 4 scénarios passés au premier essai, 0 flaky, 0 sauté.

**Passe de correction F1–F4 (celle de ce document).** Chaque ligne ci-dessous
est un run **lu**, pas supposé — et les deux preuves qui manquaient à la
première rédaction (`e2e` sur les 7 scénarios, `hors-ligne-reel` de bout en
bout) sont **acquises** :

1. `85c1d10` (push `37657518095`) — 11 jobs verts ; **`e2e` rouge** (2 échecs +
   1 flaky : la spec s'auto-empoisonnait, E-41/E-42) et **`hors-ligne-reel`
   rouge** (exit 7 : `sudo` applique son `secure_path`, E-45a).
2. `2ecc562` (push `37660166174`) — **6 scénarios sur 7 verts** ; `e2e` tombe
   ensuite sur une attente morte de 3×30 s (E-43). Hors-ligne : la pile
   démarre, puis meurt — le compte applicatif ne peut pas **traverser** le
   chemin du dépôt (E-45b).
3. `af6a6d7` (push `37661028850`) — **les 7 scénarios de concurrence
   passent** ; `e2e` s'arrête plus loin sur `validation.spec.ts` (fixture qui
   simulait l'ancien endpoint `/champs`, E-44). Hors-ligne : traversée
   corrigée, il reste deux défauts de lancement/sonde (E-45c, E-45d).
4. `95e9f06` (push `37662354426`) — **job `e2e` VERT** (toutes les suites live,
   dont 26 tests de comptes nominatifs). Hors-ligne : la pile DÉMARRE et le
   blocage est posé, le parcours échoue sur une attente d'URL impossible
   (l'identifiant d'import est un UUID, E-46a).
5. `a4a6356` → `584c47a` (push `37663104045`, `37665194771`) — étapes 17 et 18
   **VERTES** (contrôle négatif ET positif concluants) ; le parcours franchit
   l'import et le rapport généré, puis échoue sur une étape mal dirigée
   (E-46b).
6. `7bb9142` → `6db88cb` (push **`37668475085`**) — **`hors-ligne-reel` VERT de
   bout en bout** (étapes 17→21, annotation « 0 paquet rejeté ») **et `e2e`
   VERT**. Le parcours exige en plus, depuis, que le PDF soit RÉELLEMENT rendu
   par le navigateur et que la décision affiche son texte.
7. `a7f3eb8` (push **`37669839566`**) — ajoute le **garde-fou navigateur**
   (aucune ressource hors boucle locale) : le job `hors-ligne-reel` reste
   **VERT**, mais l'étape « Run live E2E validation » rougit — et sa seule
   annotation est « exit code 1 ». C'est le défaut E-47 : le helper `ouvrirFiche`
   de `validation.spec.ts` n'attendait pas la révision, donc un clic de
   décision pendant le chargement était bloqué **à raison** (fail closed) ; le
   garde-fou Python, lui, ne publiait pas sa raison.
8. `f5077d7` (push **`37670079596`**, pull_request **`37670087887`** CI et
   **`37670089091`** benchmark) — ajoute l'exigence « aucune ligne d'audit
   après un refus de décision » dans le scénario navigateur. **VERT sur les
   trois déclencheurs**, étape « Run live E2E validation » comprise : la panne
   de l'étape 7 était bien **intermittente**, pas une régression.
9. `89c2b1f` — **commit de code final** : corrige la cause de l'instabilité
   (E-47a : attente de la révision dans `ouvrirFiche`) et rend le garde-fou
   bavard (E-47b). Runs lus : push **`37671726429`** (**13/13 jobs verts**, dont
   `e2e` étapes 13→16 et `hors-ligne-reel` étapes 17→21 avec l'annotation
   « 0 paquet rejeté »), pull_request **`37671732453`** (**CI**) et
   **`37671732326`** (**benchmark**) : `success` tous les deux.

**Aucun résultat n'est revendiqué sans lecture** : la leçon de cette passe est
que les échecs successifs n'étaient **pas** des défauts produit mais des défauts
de TEST et de JOB — chacun corrigé, jamais contourné (aucune assertion
supprimée, aucun seuil abaissé, aucun `skip` ajouté).

**Aucun résultat CI n'est revendiqué ici avant lecture du job** : les points
ci-dessus sont des lectures brutes (`gh run view --json jobs`,
`gh api .../check-runs/<id>/annotations`), pas des suppositions.

---

## 5 bis. Périmètre produit — chaque scénario et SA preuve

Règle de lecture : **un nom de job vert ne prouve pas un scénario que ce job
n'exerce pas.** Chaque ligne cite l'épreuve qui exerce réellement le scénario.

| Scénario du périmètre | Épreuve qui l'exerce | État |
|---|---|---|
| Glisser-déposer + comptabilité COMPLÈTE des fichiers d'un dossier | `frontend/e2e/import.spec.ts` (dépôt de dossier, `REF-2026-CLIENT123`) ; `tests/test_import_pipeline.py`, `tests/test_lot_ingestion.py` ; harness hors ligne (« 3 fichier(s) détecté(s), 1 analysé(s) ») | **PROUVÉ** (navigateur : CI) |
| Imports unitaires ET par lot, récupérables (un échec ne bloque pas le lot) | `tests/test_file_durable_operations.py`, `tests/test_lot_ingestion.py` | **PROUVÉ** (local) |
| File durable + reprise après crash du worker (relais en base) | `tests/test_file_durable.py` (23), `tests/test_relais_file_base.py` (vrais sous-processus, SIGKILL), `tests/test_jobs_resilience.py` | **PROUVÉ** (local, Redis réel) |
| Visibilité en recherche après import ET après correction | harness hors ligne (fiche retrouvée après dépôt) ; `tests/test_recherche_hybride.py`, `tests/test_recherche_dimension.py` ; sur corpus réel : job `recette-corpus-reel` | **PROUVÉ** (local) ; corpus réel : **CI** |
| Validation / révision : la fabrication ne peut pas écraser une correction humaine | `tests/test_fiches_persistance.py` + `tests/test_lot_ingestion.py` (RG11) ; harness (levée explicite du verrou) | **PROUVÉ** |
| Récupération de l'original ET du rapport généré | `tests/test_telechargement_navigateur.py` (5), `tests/test_url_presignee_302.py` (15), harness (aperçu + original + rapport) | **PROUVÉ** (local) ; conteneurs : **CI** |
| Édition concurrente : deux postes, même fiche, aucun écrasement silencieux | `tests/test_revision_optimiste.py` (6, dont 2 clients SIMULTANÉS) ; `frontend/e2e/concurrence.spec.ts` (4 scénarios multi-navigateurs) | **PROUVÉ** : backend (local) + navigateur **CI 12/12** sur `c65b7d8` (push et pull_request) — 4 défauts trouvés par ces exécutions et corrigés (E-30, E-33, E-34, E-36) |
| Permissions restreintes : l'opérateur est refusé par le BACKEND | `tests/test_comptes.py`, `tests/test_credentials_s3_restreintes.py` (ALLOW/DENY réels contre MinIO) ; scénario 4 de la suite concurrence (403 backend) | **PROUVÉ** (local) ; MinIO réel + navigateur : **CI** |
| Sauvegarde + restauration en environnement propre | `tests/test_sauvegarde_restauration.py`, `tests/test_sauvegarde_unites.py` ; job `sauvegarde` (dont 50 000 fiches) | **PROUVÉ** en CI ; **drill HUMAIN : OUVERT** |
| Fonctionnement hors ligne | `scripts/verifier_hors_ligne.py --executer` (réseau externe bloqué, 0 échec) ; `tests/test_verification_hors_ligne.py` (5) | **PROUVÉ automatiquement** (local) ; **acceptation atelier : OUVERTE** |
| Identités de stockage restreintes (applicative ≠ sauvegarde ≠ racine) | `tests/test_credentials_s3_restreintes.py` (**18** locaux + 4 Docker), `tests/test_construire_image_minio.py` | **PROUVÉ** : local + **CI 12/12** sur `c65b7d8` (`integration` et `sauvegarde` et `recette-corpus-reel` vertes, MinIO réel) |
| Trois postes réels sur le serveur d'atelier, réseau coupé | — | **OUVERT** (portes §6) |

Rappel de méthode (revue du 2026-10-07) : la revue de ce travail par l'agent
d'implémentation n'est **pas** une approbation indépendante. Une revue externe
reste pendante ; aucune ligne ci-dessus ne la remplace.

## 6. Ce qui n'est PAS prouvé (liste exhaustive, avec ce qui le fermerait)

| Point non prouvé | Pourquoi | Ce qui le ferme |
|---|---|---|
| MinIO réel : intégrité, quarantaine, upload, téléchargement | pas de Docker/MinIO dans cette session ; 29 tests `s3` sautés **localement** | job CI `integration` (+ `sauvegarde`) — **lu VERT sur `c65b7d8`** (push `37642194285` et les deux pull_request du même SHA), et `sauvegarde` déjà verte sur `3e13c7f` (`37618670336`). Ce point est **FERMÉ** tant que le code ne change pas |
| Pile Compose à deux conteneurs (partage fichiers/quarantaine/modèles/permissions) | idem | `tests/test_compose_partage_worker.py` dans le job `integration` |
| Navigateurs (scénario trois postes) | navigateurs Playwright non installables ici (CDN bloqué) | job CI `e2e` |
| Sauvegarde → restauration réelle de bout en bout | dépend de MinIO | job CI `sauvegarde` (19+ tests, dont 50 000 fiches) |
| **Drill de restauration humain indépendant** | aucun humain n'a restauré une sauvegarde | procédure `RUNBOOK_RESTAURATION.md`, exécutée et signée par l'exploitant |
| **Destination de sauvegarde de production** | non choisie/validée par le propriétaire | décision du propriétaire + test d'écriture hors serveur |
| **Pertinence de la recherche sur archives réelles** | aucune étiquette humaine sur corpus réel ; 7 fiches de référence, 0 validée | jeu de requêtes étiqueté (`JEU_REQUETES_REELLES.md`) + validation humaine |
| **Moindre privilège S3 — contre MinIO RÉEL** | les tests Docker correspondants ne tournent qu'en CI (pas de Docker ici) | job CI `integration` **lu VERT sur `c65b7d8`** (push et pull_request) ; ALLOW/DENY réels dans `tests/test_credentials_s3_restreintes.py` |
| **Fonctionnement hors ligne — sur le SERVEUR d'atelier** | le harnais prouve l'absence de dépendance externe **de cette machine** ; il ne prouve ni les postes réels, ni le serveur cible | exécution du harnais sur le serveur cible + acceptation trois postes réseau coupé |
| **Concurrence vue du NAVIGATEUR** | les navigateurs ne s'installent pas dans cette session (CDN `cdn.playwright.dev` bloqué — vérifié à nouveau le 2026-10-07) | **FERMÉ** : run push **`37668475085`** (job `e2e`) — **7 scénarios passés au premier essai, 0 flaky, 0 sauté**, garde-fou qui REFUSE tout autre compte. Les 3 scénarios neufs : décision liée à la révision revue (409 sans ligne d'audit, relecture exigée), révision non lue (correction ET décision bloquées, backend 428 sur requête directe), réponse tardive sans écrasement |
| **Vérification hors ligne pleine pile** | le harnais Python a une portée limitée (constat F4) ; il ne dit rien du navigateur, de Next.js, du worker séparé ni des bibliothèques natives | **FERMÉ** : run push **`37668475085`**, job `hors-ligne-reel` vert — pile complète (web + worker séparé + front de production) sous compte applicatif dédié, blocage `iptables` par uid, contrôle négatif ET positif, parcours navigateur au premier essai, `REJECT = 0` (annotation « 0 paquet rejeté »). Portée exacte, sans embellissement : le blocage couvre les processus du compte applicatif ; le NAVIGATEUR est couvert par un garde-fou dans le test (aucune requête hors boucle locale, run **`37670079596`**). Ne prouve toujours pas : les postes d'atelier réels, ni le serveur cible |
| **`index --rebuild` sur PostgreSQL** (E-40) | `DROP TABLE documents` est refusé (`chunk` et `fiche_piece_jointe` le référencent) ; la restauration échoue ensuite sur `TRUNCATE`, ce qui masque l'erreur initiale. Aucune donnée perdue ; la commande fonctionne sur SQLite | correction de l'outil : `DROP … CASCADE` (ou purge ordonnée) + test PostgreSQL dédié. **Hors périmètre F1–F4** (outil d'exploitation) — repro locale reproductible |
| **Acceptation atelier (fabrication)** | décision humaine | trois postes, un import pendant une recherche, validation d'une fiche |

Aucun de ces points n'est présenté comme résolu, et la revendication de
production est **explicitement repoussée** tant qu'ils ne sont pas fermés.

Quatre précisions de périmètre, pour éviter les glissements de sens :

* **Calibration ML ≠ porte de sortie générale.** Le produit exige une
  **validation humaine obligatoire** des fiches ; la calibration statistique
  des modèles n'est donc pas une condition de mise en service, et aucune ligne
  de ce rapport ne la présente comme telle.
* **R2 reste optionnel et hors périmètre local-first** : il ne sera traité que
  sur demande explicite du propriétaire.
* **« Hors-site » ≠ « stockage séparé ».** Une sauvegarde écrite depuis le même
  serveur vers un bucket voisin n'est pas une copie indépendante : il faut une
  destination distincte (et, pour une vraie résilience, hors du site). La
  configuration actuelle documente la **séparation des identités**, pas
  l'indépendance de la destination — celle-ci est une porte ouverte (§6).
* **Les preuves automatisables ont toutes été exécutées** : la suite complète
  sur PostgreSQL et Redis réels, le front buildé, les garde-fous statiques,
  ET les deux preuves qui exigeaient un runner — les **7 scénarios navigateur**
  de la passe F1–F4 et le job **`hors-ligne-reel`** en pile complète sous
  blocage réseau réel. Le détail des runs (SHAs, jobs, annotations) est dans
  `docs/verite_terrain/TRACABILITE_LIVRAISON.md`, section « Clôture CI de la
  passe F1–F4 ».

---

## 7. Reproductibilité

```bash
# Outillage (session sans Docker) : voir l'en-tête de REVUE_INDEPENDANTE_2026-10-07.md
export SEAMTECH_TEST_DATABASE_URL="postgresql://seamtech@127.0.0.1:5433/seamtech_search"
export SEAMTECH_TEST_REDIS_URL="redis://:sandboxredis@127.0.0.1:6379/1"

# Suites
.venv/bin/python -m pytest -q -m "not redis_queue and not postgres and not s3 and not perf \
  and not recette_corpus and not integration_docker and not sauvegarde"
.venv/bin/python -m pytest -q -m "postgres and not perf and not sauvegarde and not redis_queue"
.venv/bin/python -m pytest -q -m redis_queue
.venv/bin/python -m pytest -q tests/test_relais_file_base.py      # vrais processus worker

# Front
cd frontend && pnpm install --frozen-lockfile && pnpm exec tsc --noEmit && pnpm audit --prod && pnpm build

# Compose (poste avec Docker)
docker compose up -d --build web worker && pytest -m integration_docker -v
```

---

## 8. Prochaines étapes, dans l'ordre

**Les quatre constats de la deuxième revue sont traités dans le code, avec
leurs tests de régression.** Ce qui reste, dans l'ordre :

1. **Lire les runs CI de cette passe** (push ET pull_request) : le job `e2e`
   exécute pour la première fois les 7 scénarios de concurrence, et le job
   `hors-ligne-reel` son épreuve pleine pile. Un seul des deux déclencheurs ne
   suffit pas (E-36). Tant que ces runs ne sont pas lus, la fermeture de F4 et
   des scénarios navigateur reste **ouverte**.
2. Puis seulement, les portes qui exigent un humain ou le serveur d'atelier :

1. **Installation sur le serveur d'atelier** avec les identités restreintes
   provisionnées (`bash scripts/provisionner_stockage.sh`) et la destination de
   sauvegarde de production choisie par le propriétaire (aujourd'hui : la
   séparation des IDENTITÉS est prouvée, pas l'indépendance de la DESTINATION).
2. **Drill de restauration humain** dans un environnement propre
   (`RUNBOOK_RESTAURATION.md`), signé par l'exploitant.
3. **Validation humaine du corpus de référence** (0/7 fiches validées à ce
   jour) et jugement de pertinence sur un jeu de requêtes réelles étiqueté.
4. **Recette atelier trois postes** : import réel avec comptabilité complète,
   recherche pendant l'import, validation d'une fiche, travail de fabrication
   avec acceptation des révisions par les opérateurs.
5. **Capacité/performance réelles** sur le poste cible, puis décision de pilote
   contrôlé — la production reste **non revendiquée** (§6).
