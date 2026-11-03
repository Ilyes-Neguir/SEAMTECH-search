# Remédiation — état en cours, NON PRÊT

Cette campagne n'est pas terminée. Aucun déploiement ni fusion vers `main` n'a été effectué. Les résultats suivants s'appliquent à l'arbre de travail local, pas à un commit final qualifié en CI.

## P0 — préservation et récupération

**Correction partielle réalisée :** le contrôle des copies référencées dans le bundle est maintenant effectué avant de lancer la copie des fichiers locaux ou l'envoi des objets. Les chemins sources hors du bundle, les liens symboliques sortants, les identifiants de fichiers invalides et les destinations sortant de la racine de restauration sont refusés. Une corruption détectée dans une entrée tardive ne déclenche plus l'écriture des entrées précédentes.

**Preuve locale :** `.venv/bin/python -m pytest tests/test_restauration_confinement.py tests/test_sauvegarde_references.py -q -p no:cacheprovider` : 15 réussis, 3 ignorés. `.venv/bin/ruff check seamtech_search/sauvegarde.py tests/test_restauration_confinement.py` : réussi. Les trois tests ignorés requièrent PostgreSQL réel ; aucune preuve de sauvegarde/restauration complète n'en découle.

**Limites :** ce précontrôle n'est pas une transaction atomique : une panne ou mutation concurrente pendant les copies peut encore produire un résultat partiel. La restauration de la base et celle des fichiers/objets restent des opérations distinctes. La cohérence base + `./data` + stockage objet + hors site, la planification, la rétention et la restauration coordonnée ne sont pas qualifiées en conditions finales. **Actualisation :** la promotion des lots vers le stockage objet est maintenant implémentée et des tests isolés ont été exécutés, mais sa sûreté complète, les fournisseurs actuels et la réconciliation après panne restent non qualifiés : voir l'état le plus récent en fin de document. Migration et rollback représentatifs restent non prouvés.

## P1 / P2

Les points OCR/CI, rotation des PDF, environnement cible, gros volume, matrice complète, flux multipart, pannes, sécurité offensive, accessibilité et endurance restent ouverts. Les résultats et réserves de la campagne antérieure figurent dans `docs/CAMPAGNE_PRODUCTION_COMPLEMENTAIRE.md` ; ils ne constituent pas la clôture de cette remédiation.

## Environnement et méthode

Compétences ECC appliquées à ce premier incrément : `production-audit` et `tdd-workflow` (tests en échec avant correction). Pas de Docker disponible dans cet environnement au moment de ce contrôle ; aucun test MinIO/R2 réel ni test Windows n'a été exécuté pour cet incrément. Aucun numéro de migration n'a été utilisé.

### Manifeste de sauvegarde hors site — incrément supplémentaire

- Défaut reproduit : le manifeste distant était envoyé avant l'ajout de la clé S3 réellement attribuée au dump ; il ne permettait donc pas de localiser ce dump. Son intégrité n'était pas relue avant la rétention.
- Correction : écrire la clé réelle dans le manifeste avant envoi, relire le manifeste distant et comparer son SHA-256 avant de lancer la purge. Un échec de vérification interrompt la sauvegarde avant rétention.
- TDD : deux échecs reproduits avant correction, puis `.venv/bin/python -m pytest tests/test_sauvegarde_manifeste_distant.py tests/test_restauration_confinement.py tests/test_sauvegarde_unites.py tests/test_sauvegarde_references.py -q -p no:cacheprovider` : **33 réussis, 3 ignorés** ; `ruff check` des fichiers modifiés : réussi. Stockage simulé en mémoire ; pas de validation MinIO/R2 réelle dans ce test.
- Limites inchangées : les copies de pièces ne sont pas, de ce fait, sauvegardées hors site ; cohérence des instantanés et restauration coordonnée non démontrées. La clé du manifeste et le résultat de rétention ne sont connus qu'après son envoi et restent dans le manifeste local / résultat de la commande, pas dans le manifeste distant.

### Sauvegarde des références : signalement des objets manquants et confinement

- Lorsque des objets sont référencés mais que le client du bucket documents n'est pas disponible, la sauvegarde est maintenant marquée incomplète et les références non copiées sont comptées. Auparavant elle pouvait être déclarée complète à tort.
- La copie locale refuse les noms ou identifiants invalides et les destinations résolues hors du bundle. Cette vérification ne rend pas la copie atomique et ne constitue pas une sauvegarde de tout `./data`.
- TDD : deux tests ciblés ont d'abord reproduit les défauts ; après correction la suite ciblée de sauvegarde et restauration donne **35 réussis, 3 ignorés** (PostgreSQL nécessaire) et Ruff réussit. Aucun service S3 réel n'a été utilisé pour ces tests.

### Sécurité du téléchargement de dump et garde immédiate de rétention

- Le téléchargement distant du dump refuse désormais les noms de fichier non confinés et utilise un répertoire temporaire privé, nettoyé même en cas d'erreur. Tests synthétiques de traversée, non-écrasement et nettoyage.
- Une sauvegarde signalée incomplète n'exécute plus la rétention dans cette même opération. **Attention : cela ne résout pas la politique de rétention globale** : une sauvegarde incomplète déjà présente peut encore être prise en compte lors d'une purge ultérieure. La rétention reste NON QUALIFIÉE et ne doit pas être considérée sûre.
- Tests ciblés : **38 réussis, 3 ignorés** ; Ruff ciblé réussi. Suite Python hors S3/performance/corpus/Docker : **1 148 réussis, 411 ignorés, 40 exclus, 3 xfail, 2 avertissements**, sans serveur PostgreSQL ni Redis configuré. Cette suite ne qualifie pas les tests ignorés ni les services réels.

### Rétention et isolement des générations de sauvegarde

- Deux sauvegardes enchaînées pouvaient utiliser le même nom de dump et partager les mêmes copies de documents. Test reproduit puis corrigé : identifiant de sauvegarde unique et copies rangées dans un bundle propre à chaque génération. La rétention ne supprime pas encore ces bundles : risque d'accumulation disque, à traiter sans compromettre les anciennes générations.
- La rétention construit désormais son plan avant suppression et conserve les sauvegardes incomplètes sans les compter parmi les complètes ; un manifeste manquant, illisible ou sans statut booléen bloque toute purge automatique. Ceci corrige le risque de remplacement par une génération explicitement incomplète cité ci-dessus, mais ne prouve pas qu'une sauvegarde marquée complète soit réellement restaurable : les limites de cohérence et de copie hors site restent ouvertes.
- Tests unitaires ajoutés pour une génération incomplète ultérieure, un manifeste manquant et la conservation des deux versions d'un document.
- PostgreSQL 16.14 isolé avec extensions locales, échantillons synthétiques : sauvegarde/restauration ciblées **48 réussis, 3 exclus** ; suite Python hors S3/perf/corpus/Docker : **1 465 réussis, 98 ignorés, 40 exclus, 3 xfail, 5 avertissements**. Les résultats précédents indiquant des échecs avec PostgreSQL 16.2 sans extensions ou PostgreSQL 16.14 avant installation de pgvector étaient dus à cet environnement incomplet ; ils ont été relancés sur 16.14 avec extensions. Ni MinIO/R2, ni Windows/Docker Desktop, ni une sauvegarde complète hors site ne sont qualifiés par ces résultats.

### Précontrôle de restauration multi-composants

- La commande de restauration vérifie désormais tous les fichiers et objets demandés, y compris les chemins de destination, avant de créer la base. Une corruption d'un objet ne déclenche donc plus d'abord une restauration de base ou de fichiers. Deux tests en échec avant correction couvrent ce scénario avec des données synthétiques.
- En cas de refus de suppression du dump distant, la rétention conserve son manifeste. Test de panne ajouté.
- PostgreSQL 16.14 isolé et stockage simulé : suite ciblée **51 réussis, 3 exclus, 1 avertissement** ; Ruff ciblé réussi.
- Ce précontrôle ne garantit ni l'atomicité des opérations ultérieures ni le rétablissement automatique des chemins enregistrés en base vers les fichiers restaurés. Ces problèmes demeurent ouverts. La copie des bundles hors site et la promotion des lots restent également ouvertes.

### Vérification des références objet et fichiers

- Un objet dont le bucket déclaré diffère de celui du client documents est désormais signalé manquant plutôt que copié depuis le mauvais bucket. La copie d'objets refuse aussi les destinations résolues hors bundle.
- Les références de fichiers sans source locale ni clé objet sont signalées comme manquantes, tandis que les entrées de répertoires sont distinguées et ne sont pas prises pour des fichiers manquants. Tests synthétiques reproduits avant correction.
- Suite ciblée avec PostgreSQL 16.14 : **54 réussis, 3 exclus, 1 avertissement** ; Ruff ciblé réussi. Aucun objet MinIO/R2 réel n'a été testé pour ces corrections.

### Références copiées hors site — incrément P0

- Avec `--avec-references` et un client de sauvegarde S3, les copies référencées locales et objets sont maintenant envoyées dans des clés propres à la génération, sans nom ni chemin d'origine dans la clé ; chaque copie est relue et son empreinte comparée avant publication du manifeste et rétention. Les clés retournées par le stockage figurent dans le manifeste distant.
- La restauration depuis un manifeste distant peut télécharger les copies demandées dans un bundle temporaire privé, vérifier leurs empreintes, puis restaurer la base et les pièces. Traversées et copies altérées sont refusées avant création de la base. Tests à stockage simulé : **61 réussis, 3 exclus** sur la suite ciblée PostgreSQL 16 ; test intégré complémentaire : **1 réussi** après suppression des copies locales et des sources synthétiques.
- Limites bloquantes : stockage simulé, pas de MinIO/R2 réel dans ces tests ; ne couvre que les documents référencés, **pas tout `./data`** ; les chemins en base ne sont pas automatiquement réconciliés avec les fichiers restaurés à un nouvel emplacement ; pas de transaction multi-composants ni instantané cohérent avec les écritures concurrentes ; la rétention ne supprime pas les bundles distants ou locaux et le risque d'accumulation demeure. Le manifeste peut contenir les métadonnées de chemins des documents : il doit rester protégé et ne doit jamais être publié comme preuve contenant des données réelles.
- Relance de la suite Python après cet incrément sur PostgreSQL 16.14 : **1 479 réussis, 98 ignorés, 40 exclus, 3 xfail, 5 avertissements** (`pytest -m 'not s3 and not perf and not recette_corpus and not integration_docker'`). Ces exclusions et échecs attendus restent des limites, pas une preuve de préparation à la production.

### P0.2 — lots téléversés : intégrité avant utilisation

- Analyse du flux : les envois sont assemblés sous `uploads/lots-televerses`, la session finale est liée à un lot, puis le dépôt catalogue les documents. Le lecteur des pièces sait utiliser `object_key`, mais ce flux ne promeut pas encore les octets vers S3 et ne renseigne pas cette clé. La promotion réelle reste **NON PRÊTE**.
- Défauts reproduits par cinq tests synthétiques : un fichier connu altéré à taille identique passait la finalisation ou l'accès au lot ; une modification de taille pouvait remplacer l'empreinte de référence ; un fichier disparu dans une session déjà associée pouvait être accepté.
- Correction : lors de la réconciliation, les fichiers déjà référencés sont relus et comparés à leur taille et à leur SHA-256 initial ; une différence est refusée sans réécrire l'empreinte. Un fichier disparu dans une session associée à un lot est refusé. La finalisation répétée réconcilie elle aussi avant de répondre.
- `.venv/bin/python -m pytest tests/test_lot10_televersement_lot.py -q -p no:cacheprovider` : **24 réussis**, 1 avertissement ; Ruff et `git diff --check` réussis. ECC utilisés : `search-first`, `tdd-workflow`, `security-review`, `verification-loop`.
- Limites : l'empreinte est calculée en relisant les fichiers ; coût sur une grande année et comportement sous mutation simultanée non qualifiés. La vérification n'est pas une preuve que le worker a lu les mêmes octets après le contrôle ; elle ne remplace ni la promotion S3, ni une preuve bout en bout de récupération et de lecture depuis l'objet. Aucun MinIO/R2 réel testé pour cette étape.

### P0.2 — primitive de promotion objet (non intégrée)

- Compétences ECC utilisées : `search-first` (lecture du flux de session et réutilisation de `S3StorageClient.verifier_integrite`), `tdd-workflow` (tests en échec avant ajout), `security-review` (clés sans noms ni jeton, vérification des chemins, conservation locale), `verification-loop` (pytest, Ruff, `git diff --check`). Ces compétences se trouvent dans `.agents/skills/` ; aucun sous-agent n'était disponible dans cet environnement.
- Ajout d'une primitive appelable de promotion d'une session : envoi de chaque fichier à une clé opaque, relecture SHA-256 obligatoire, clé réellement retournée enregistrée uniquement après preuve, conservation des fichiers locaux ; reprise après panne testée avec stockage simulé.
- Le rejeu d'une création de lot déjà associée recontrôle désormais aussi l'intégrité des sources ; un test reproduisait son contournement.
- Commande : `.venv/bin/python -m pytest tests/test_promotion_lot_objets.py tests/test_lot10_televersement_lot.py -q -p no:cacheprovider` : **29 réussis, 1 avertissement** ; Ruff et vérification du diff réussis.
- **Pas encore intégré à l'API ni au worker, ni au catalogue PostgreSQL** : une utilisation normale de l'application ne déclenche pas cette primitive et les vues ne disposent donc pas encore des clés objet. Le fournisseur est simulé, pas MinIO/R2. Le manifeste est réécrit après chaque fichier ; son coût sur une grande archive est inconnu et peut devenir prohibitif. Échecs à mi-écriture, processus concurrents, vieillissement d'objets orphelins et récupération après perte de la session locale nécessitent qualification. La promotion P0.2 reste **NON PRÊTE**.

### Sessions de lots : liens symboliques et reprise

- Deux scénarios reproduits avant correction : un fichier de session remplacé par un lien vers l'extérieur pouvait être accepté à la finalisation ; un parent symbolique pouvait faire écrire un envoi hors de la session. Les liens symboliques sont maintenant refusés dans ces chemins, avant l'écriture ou la réconciliation. Comme pour tout contrôle par chemin, une substitution concurrente entre vérification et accès n'est pas qualifiée.
- Suite ciblée lots et primitive objet : **31 réussis, 1 avertissement** ; Ruff réussi. Suite Python avec PostgreSQL 16.14 hors S3/performance/corpus/Docker : **1 491 réussis, 98 ignorés, 40 exclus, 3 xfail, 5 avertissements**.
- P0.2 reste non prête : la primitive objet n'est toujours pas reliée à l'API/worker/catalogue ; MinIO/R2 réel, reprise à grande échelle et consommation des objets depuis les vues ne sont pas démontrés. Aucune CI finale sur l'arbre actuel.

### P0.2 — journal de promotion et mesure 100 000 fichiers

- TDD : un test a reproduit la réécriture intégrale du manifeste 16 fois pour 1 000 fichiers. La primitive utilise maintenant un journal append-only, `flush`/`fsync` tous les 64 envois et une consolidation du manifeste en fin d'appel ; un fragment final tronqué est ignoré, et les clés récupérées sont relues avant d'être considérées valides. Une panne simulée de consolidation et la reprise sans second envoi sont testées. Un arrêt brutal peut laisser des objets orphelins à réconcilier ; aucun objet local n'est supprimé.
- Premier essai 100 000 fichiers synthétiques d'un octet, avec ancienne consolidation par lots de 64 : interrompu au délai de 900 s ; le manifeste rapportait 100 000 fichiers reçus et 78 336 promotions enregistrées. Le stockage était simulé en mémoire et a disparu à l'arrêt ; aucun résultat complet ne peut en être déduit.
- Avec le journal : essai séparé de promotion sur 100 000 fichiers construits directement dans une session synthétique : **100 000 promus, 14,08 s, RSS max 214 776 Kio**, stockage simulé. Essai complet depuis l'API Python de réception, en envois de 200 : **100 000 reçus en 299,11 s, finalisation 3,12 s, 100 000 promus en 13,50 s, RSS max 201 400 Kio**. Ces mesures utilisent des fichiers d'un octet, un stockage en mémoire, Linux et un disque éphémère : elles ne qualifient ni le volume réel, ni le réseau, ni MinIO/R2, ni le navigateur. La réception reste coûteuse, avec ralentissement croissant au fil des fichiers.
- Tests ciblés après journal : **35 réussis** (promotion et téléversement), à reconfirmer par la commande complète avant clôture. La primitive n'est toujours pas appelée par l'API/worker et les clés ne sont pas encore publiées dans le catalogue : **P0.2 NON PRÊTE**.

### Prochaine action précise

Intégrer la promotion au traitement durable et au mode synchrone sans bloquer indéfiniment l'API, avec persistance transactionnelle des clés vérifiées dans `documents` et protection des rejeux (notamment les dossiers déjà traités), fencing/heartbeat et reprise après panne. Écrire d'abord les tests PostgreSQL et E2E correspondants, puis mesurer sous MinIO réel si disponible ; ne pas supprimer les sources ni prétendre que les copies sont restaurables tant que les contrôles ne sont pas satisfaits. Ensuite reprendre P0.1 (snapshot coordonné incluant `./data`, réconciliation des chemins, rétention des bundles), P0.3 et les portes P1/P2. Branche active inchangée, aucun commit final ni fusion.

### Revue de la reprise du journal — état de fin de tour

- Revue `security-review` / TDD : deux défauts supplémentaires ont été reproduits puis corrigés : une reprise qui n'avait aucun nouvel envoi supprimait le journal sans consolider les clés récupérées ; une reprise pouvait consolider une clé du journal avant d'avoir revérifié l'objet. Les clés candidates ne sont maintenant inscrites dans le manifeste qu'après relecture réussie et les clés récupérées sont consolidées avant suppression du journal.
- Tests actuels : `.venv/bin/python -m pytest tests/test_promotion_lot_objets.py tests/test_lot10_televersement_lot.py -q -p no:cacheprovider` : **36 réussis, 1 avertissement** ; Ruff ciblé réussi. `git diff --check` à relancer avec la prochaine série. La suite Python générale **1 491 réussis / 98 ignorés / 40 exclus / 3 xfail** est antérieure aux changements de journal et ne doit pas être attribuée à cet arbre précis.
- Prochaine action exacte : intégrer la primitive dans le worker et le mode synchrone, alimenter `documents.object_key` / `object_bucket` uniquement dans une transaction qui lie des copies revérifiées aux documents correspondants, traiter les rejeux déjà importés et la reprise/fencing/heartbeat ; écrire tests PostgreSQL puis navigateur et MinIO réels avant de conclure. Le service objet n'est pas encore branché ; P0.2, P0.1, P0.3, P1, P2 et la CI finale restent ouverts. Aucune donnée réelle n'a été utilisée.

### P0.2 — raccordement partiel et rejeux (état actuel)

- Compétences ECC appliquées : `tdd-workflow`, `security-review`, `postgres-patterns`, `redis-patterns` et `verification-loop`. La promotion est maintenant appelée par `executer_lot` lorsque la configuration est fournie, et la configuration est transmise par le worker et les routes synchrone et asynchrone locale. Seules les racines reconnues comme sessions téléversées sont promues ; le stockage local et les archives externes ne déclenchent pas l'envoi. Si S3 est sélectionné mais non configuré, le traitement échoue au lieu de prétendre avoir préservé les fichiers. Les originaux locaux sont conservés.
- Les clés/buckets des copies relues sont liées aux lignes `documents` dans la transaction de dépôt, avec contrôle de SHA et refus des conflits. Un rejeu de PDF et annexes identiques peut rattacher les anciennes lignes à de nouvelles copies dont les octets ont été vérifiés ; les objets historiques ne sont pas supprimés. Un test reproduit un rejet transactionnel si une annexe a changé, plutôt qu'un faux succès ; ce cas doit encore être géré fonctionnellement comme une modification, pas rester un échec. La gestion des approbations, des remplacements concurrents et du document réellement courant reste à auditer ; la réaffectation d'une clé historique ne prouve pas que l'ancien objet était encore lisible.
- Tests synthétiques avec PostgreSQL 16.14 : `tests/test_depot_transactionnel.py tests/test_lot10_televersement_lot.py tests/test_promotion_lot_objets.py tests/test_fiches_api.py` : **79 réussis, 1 avertissement**, stockage objet simulé ; le worker est appelé directement sans Redis dans un des tests. `ruff check` ciblé et `git diff --check` réussis. La suite générale **1 491 réussis / 98 ignorés / 40 exclus / 3 xfail** précède le journal et ce raccordement ; aucune CI verte sur l'arbre présent.
- **P0.2 NON PRÊTE** : pas de test S3/MinIO/R2 réel ni E2E navigateur/route/worker/lecture depuis objet ; annulation pendant la promotion actuellement convertie en erreur de promotion et donc en échec du job, au lieu d'un état annulé ; contrôle de propriété du worker seulement à l'entrée, pas de fencing durant la promotion ou les transactions ; heartbeat de progression non émis pendant la promotion ; concurrence/reprise de processus non qualifiées. La route synchrone ne traite pas encore explicitement les erreurs de promotion. Le contrôle de la racine résolue dans `_preparer_objets_lot` peut traiter comme archive externe une racine lexicale de session remplacée par un lien symbolique vers l'extérieur : écrire un test de contournement puis faire échouer explicitement ce cas. Le mode asynchrone local utilise un thread et sa gestion d'échec est à vérifier. Pas de preuve de résilience à une perte de stockage local, et les fichiers des dossiers refusés doivent aussi être retrouvables.
- **Prochaine action exacte** : commencer par un test PostgreSQL pour la racine de session devenue lien symbolique et corriger le contournement ; tests de perte de claim et d'annulation durant la promotion, puis raccordement heartbeat/fencing (ne pas confondre perte de claim et annulation) et erreurs des deux routes. Tester le rejeu avec fiche remplacée et annexes ajoutées/supprimées/modifiées, les accès des vues, puis exécuter intégration réelle MinIO/R2 et E2E. Reprendre ensuite sauvegarde/restauration complète `./data`, migration/rollback, OCR CI, rotations PDF, qualification Windows/Docker/volume réel et P2. Aucune fusion, aucun commit final ni déploiement ; branche active inchangée, aucune donnée réelle utilisée.

### P0.2 — continuation : racine, interruption, claim et route

- ECC : `tdd-workflow`, `security-review`, `redis-patterns`, `postgres-patterns`, `verification-loop`. Un test PG a reproduit le contournement de promotion par substitution de la racine de session avec un lien symbolique vers l'extérieur. La détection compare maintenant le chemin lexical et le chemin résolu et refuse les liens de la session ou de sa racine de fichiers. Le contrôle par chemin reste exposé à une substitution concurrente (TOCTOU) : il n'est pas une garantie d'ouverture par descripteur confinée.
- Un test a reproduit une annulation pendant promotion transformée en échec. Une exception d'interruption distincte permet au worker de marquer le lot et le job annulés ; fichiers locaux conservés et reprise synthétique vérifiée sans réenvoyer la première copie. La requête synchrone renvoie désormais HTTP 503 avec l'identifiant du lot en cas de `DepotImpossible`, au lieu d'une exception non traitée ; cas HTTP testé sur PG. Le repli en thread local reste à auditer.
- Tests de perte de claim PG et Redis réel pendant la promotion, et avant publication du résultat : le worker s'arrête avec `stale_worker` sans annuler le lot. Contrôle PG + Redis dans le callback, heartbeat PG périodique entre fichiers ; le claim Redis correct est celui de la file `imports` (et non `lots`), y compris pour la progression. Un test de reprise durable a été aligné sur la boucle réelle : prise du claim Redis avant l'appel direct du worker. La première relance a ainsi révélé et permis de corriger la mauvaise file ; elle n'était pas verte.
- Qualification ciblée : PostgreSQL 16.14 et Redis **7.4.7 réel** sur 127.0.0.1, fournisseur objet simulé. `tests/test_file_durable_operations.py tests/test_file_durable_postgres.py tests/test_depot_transactionnel.py tests/test_promotion_lot_objets.py tests/test_fiches_api.py tests/test_lot10_televersement_lot.py` : **117 réussis, 1 avertissement**, y compris redémarrage brutal Redis AOF dédié. Ruff ciblé et `git diff --check` réussis. Redis a été compilé sous `/tmp` et lancé sur le port 56379 ; ne pas exposer ni utiliser ce service comme instance de production.
- Suite générale PostgreSQL précédente dans ce tour : **1 507 réussis, 98 ignorés, 40 exclus, 3 xfail, 5 avertissements**, hors S3/perf/corpus/Docker et avant les dernières modifications de contrôle de claim ; pas de CI finale. Les trois rotations PDF restent `xfail`.
- **Toujours NON PRÊT** : le contrôle de claim n'est pas atomiquement verrouillé avec les transactions de dépôt ou l'annulation et ne constitue pas un fencing complet ; perte pendant un dépôt ou après contrôle peut encore permettre des écritures ou statuts concurrents. Le callback ne peut battre le cœur pendant un seul envoi S3 bloqué ni pendant l'attente du verrou de session ; son interrogation PG et Redis par fichier n'a pas été mesurée à 100 000 fichiers. Les sessions sont protégées par `flock` sous Linux, mais aucun verrou équivalent n'est pris quand `fcntl` est absent sous Windows. Pas de test MinIO/R2 réel ni de lecture E2E depuis objet. Annexes modifiées sur rejeu toujours refusées plutôt que traitées ; remplacement de fiche et approbations à auditer. Pas de preuve de sauvegarde complète/restauration coordonnée, qualification Windows/Docker, correction OCR/rotations, campagne P2 ni CI verte à HEAD.
- **Prochaine action exacte** : écrire les tests de perte/transfert de claim pendant la transaction de dépôt et avant annulation, puis implémenter un fencing transactionnel PostgreSQL qui ne laisse pas un worker déchu publier ou annuler le lot ; préserver l'acquisition Redis et la reprise, sans attendre sous verrou indéfiniment. Tester la gestion du thread local et des erreurs, les rejeux avec fiche remplacée et annexes ajoutées/supprimées/modifiées, puis la lecture depuis S3 réel et E2E. Reprendre ensuite P0.1/P0.3, OCR/rotations P1, toutes les portes P2 et la CI exacte. Branche inchangée, pas de commit final, push, déploiement ni fusion.

### P0.2 — continuation : transactions et publication du job

- ECC appliquées : `tdd-workflow`, `postgres-patterns`, `redis-patterns`, `security-review`, `verification-loop`. Tests synthétiques PG écrits en premier : transfert de claim pendant le scan persistait une fiche malgré le worker déchu ; échec de l'écriture de la ligne de lot persistait la fiche sans sa clé d'idempotence ; annulation par worker déchu et transfert au moment de la fermeture de job. Ces cas ont été reproduits en échec avant correction.
- Le worker transmet désormais `(job_id, worker_id)` au dépôt. `_verrouiller_claim` prend `SELECT ... FOR UPDATE` sur la ligne du job et refuse un propriétaire différent dans la transaction de fiche, les liaisons de rejeu, le suivi, les compteurs, les changements d'état du lot et son annulation. Un test PostgreSQL avec `lock_timeout` vérifie qu'un transfert concurrent ne traverse pas la transaction d'écriture. La ligne de lot, la clé d'idempotence, la fiche et ses documents sont désormais écrits dans la même transaction ; une faute SQL injectée sur la ligne les annule tous. Un worker ayant perdu le claim pendant le scan ou avant une annulation retourne `stale_worker` sans marquer le dossier en échec ou le lot annulé.
- La fermeture d'un job publie dorénavant statut, étape, progression, résultat et libération de claim dans une seule mise à jour PG/SQLite conditionnelle ; un transfert intercalé avant fermeture ne publie plus `completed`. Les appels existants de `terminer_job` gardent leurs valeurs non fournies. Une annulation impossible à enregistrer est traitée comme un échec et non comme une annulation réussie. Ces chemins ont des tests PG/Redis ; la mise à jour du cache Redis final reste conditionnée à la possession du claim Redis.
- Suite générale sur PostgreSQL **16.14** + Redis **7.4.7** : `pytest -q -m 'not s3 and not perf and not recette_corpus and not integration_docker' -p no:cacheprovider` : **1 599 réussis, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements**, dont tests réels Redis, mais stockage objet toujours simulé ; 3 rotations PDF restent `xfail`. Ruff ciblé et `git diff --check` réussis. Les services PG/Redis sont des processus éphémères locaux, pas une qualification Docker ou une CI. Aucune CI verte sur l'arbre courant.
- Limites importantes : le verrou PG ne crée pas d'atomicité entre le claim Redis et PostgreSQL : un claim Redis peut expirer pendant une longue transaction alors que le verrou PG est tenu, et un transfert peut rester en attente ; le test ne prouve pas l'absence de double traitement en cas de partition ou d'expiration TTL. La transaction métier garde le verrou PG pendant ses écritures et n'a pas été qualifiée sous délais extrêmes ou panne réseau. La réconciliation des compteurs reste séparée de la transaction de fiche ; un crash peut temporairement les laisser périmés. Le rejeu des objets et la mise à jour de sa ligne sont encore deux transactions. La garantie Linux `flock`, la conservation des originaux, l'utilisation des objets depuis les vues, la résistance aux mutations de chemins et les annexes changées nécessitent encore qualification/correction. Les autres portes P0/P1/P2 et la CI sont ouvertes : **NON PRÊT**.
- **Prochaine action exacte** : tester et corriger la perte de claim Redis pendant une transaction PG longue (ne pas revendiquer de fencing distribué sans preuve), les crashs entre rejet/rejeu et suivi, le repli en thread local ; puis rendre fonctionnel le rejeu avec annexes ajoutées/supprimées/modifiées sans écraser les documents ou approbations historiques. Qualifier le flux complet via MinIO/R2 réel et navigateur avant de reprendre sauvegarde/restauration complète, migration/rollback, OCR/rotations et campagne P2. Aucun commit final, push, déploiement ni fusion ; branche inchangée, aucune donnée réelle utilisée.

### P0.2 — continuation : reprises, préservation et repli local

- ECC appliquées : `tdd-workflow`, `security-review`, `postgres-patterns`, `redis-patterns`, `error-handling`, `verification-loop`. Le transfert du claim Redis pendant une transaction PG a été reproduit : la fiche était validée malgré la perte de claim. Un contrôle du claim Redis au plus près de la fin de la transaction métier déclenche maintenant son rollback ; test PG16 + Redis7 réel avec transfert pendant `ecrire_fiche_resultat`. La liaison d'objets d'un rejeu et sa ligne de suivi sont désormais dans la même transaction : une panne SQL injectée annule les deux. Ces contrôles ne constituent **pas** une atomicité Redis/PostgreSQL : une perte entre contrôle et commit reste possible ; partition, durée et réveil d'un worker concurrent restent à tester.
- Rejeux : un PDF identique ne suffit plus à déclarer un dossier déjà traité lorsque ses annexes sont différentes. Une annexe ajoutée/modifiée dans une nouvelle session est maintenant traitée, avec nouvelle ligne `documents`, conservation de l'ancien document et nouvelle liaison ; test stockage simulé et PG. Une annexe absente est explicitement refusée pour revue, sans supprimer l'ancienne liaison. Un rejeu d'une ancienne version PDF alors que la fiche courante a changé est refusé, au lieu d'un faux `deja_traite` ; l'identité de la source courante est dérivée de la dernière clé de lot, **pas** de `fiche.fichier_source` (champ extrait du PDF, pas chemin garanti). Les comparaisons de rejeu restent faites avant la transaction finale : leur protection contre des changements concurrents doit encore être implémentée et testée.
- Un dépôt différent visant une fiche déjà validée est maintenant refusé avant modification des documents et pièces (RG11), au lieu de déclarer la fiche conservée tout en modifiant ses liaisons. Des fichiers modifiés au même chemin absolu sont refusés si une empreinte précédente non vide diffère, au lieu d'écraser l'empreinte historique de `documents` et potentiellement ses approbations. Cela ne restaure pas les octets déjà écrasés hors application, et les anciens enregistrements à empreinte vide restent une limite. La suppression volontaire d'annexes, les fichiers/versions manquants, les documents déjà approuvés et la concurrence doivent encore être qualifiés ; un refus pour revue n'est pas une solution fonctionnelle complète.
- Repli local : le thread passe désormais par le worker pour mettre à jour le registre, avec filet d'erreur inattendue ; tests HTTP avec échec métier et exception. Le mode reste explicitement non durable. Le renvoi HTTP en cas d'échec d'enfilage Redis est maintenant 503, avec identité du lot et mise en garde qu'une exécution différée reste possible : le job reste en attente pour réconciliation ; test HTTP à panne simulée. Aucun test n'établit le comportement après une panne réseau ambiguë réelle.
- Suite générale PG16.14 + Redis7.4.7 réel après ces changements : `pytest -q -m 'not s3 and not perf and not recette_corpus and not integration_docker' -p no:cacheprovider` : **1 610 réussis, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements** ; S3/MinIO/R2 toujours simulé ou exclu. Ruff ciblé et `git diff --check` réussis. Le test ciblé précédent, avant les dernières corrections de garde des documents, comptait **159 réussis, 1 avertissement** ; ne pas le confondre avec la suite générale actuelle. Pas de CI finale ni de commit final.
- **Prochaine action exacte** : écrire les tests PG de concurrence entre vérification et commit des rejeux, verrouiller la fiche et revérifier source et annexes dans la transaction (y compris rejeu sans objet) ; vérifier les conflits/approbations pour annexes modifiées et décider un traitement sûr des suppressions explicites. Tester partition/expiration Redis pendant transactions et attente du verrou de session, puis vrai MinIO/R2 et lecture bout en bout via navigateur. Continuer P0.1/P0.3, OCR/rotations P1, qualification Windows/Docker, toutes les portes P2 et la CI exacte. **NON PRÊT** : stockage objet réel, sauvegarde/restauration coordonnée intégrale, migrations sur copie représentative, trois rotations xfail, OCR CI, matrice complète, endurance et CI restent ouverts. Branche active inchangée ; aucun commit final, push, déploiement ou fusion ; aucune donnée réelle utilisée.

### P0.2 — concurrence des rejeux et navigateur avec S3 émulé

- ECC appliquées : `tdd-workflow`, `postgres-patterns`, `security-review`, `e2e-testing`, `verification-loop`. Deux courses ont été reproduites par des connexions PostgreSQL distinctes : modification de la source entre comparaison et publication et modification concurrente d'une annexe alors qu'un autre dépôt la remplace. `depot.py` verrouille maintenant la fiche et revérifie transactionnellement la source et les annexes, y compris le rejeu sans objets ; il compare l'état exact antérieur des annexes avant écriture. Tests ciblés PG, puis ensemble PG16+Redis7+S3rver : **167 réussis, 1 avertissement**, `/tmp/seamtech-next-target2.log`. Il reste à examiner toutes les autres voies d'écriture/approbation qui pourraient ne pas prendre ce verrou et les courses impliquant des objets.
- Deux tests opt-in utilisent le vrai client réseau HTTP S3 et PostgreSQL contre **S3rver 3.7.1**, avec documents synthétiques et session humaine. Ils vérifient la promotion et les octets, puis déplacent les sources de test et lisent PDF et annexe par les vues ; l'un couvre création de session, multipart, finalisation et création du lot par HTTP. Ils ne portent pas le marqueur `s3` réservé par `tests/test_selection_ci.py` aux services réels ; sans URL d'émulateur ils sont ignorés. Le premier lancement a échoué à 401 faute de session, puis les deux passent avec session. Le marquage `s3` initial a fait échouer la sélection CI ; corrigé, test de sélection **18 réussis**.
- Nouveau test Playwright opt-in `frontend/e2e/televersement-lot.spec.ts` : Chromium Linux, Next.js production, PostgreSQL 16.14, Redis 7.4.7 (file `imports` et worker en fil web) et S3rver réseau, deux fichiers synthétiques. Sélection d'un dossier par input, transfert, suivi jusqu'à `terminé`/`traite`, déplacement temporaire des sources, consultation via BFF et comparaison des octets de l'aperçu PDF et de l'annexe : **1 réussi**, `/tmp/seamtech-e2e-s3-redis-emule.log`. Le même parcours en repli local sans Redis a également réussi, `/tmp/seamtech-e2e-s3-emule2.log`. Ce n'est ni un glisser-déposer réel, ni une archive volumineuse, ni un worker séparé, ni une qualification de MinIO/R2. Chromium et dépendances récupérés du registre npm sous `/tmp`, aucun secret réel ; `SEAMTECH_E2E_CHROMIUM_EXECUTABLE` permet d'utiliser un navigateur local. S3rver signale l'absence de versioning ; aucune garantie de versioning fournisseur n'est déduite.
- Suite canonique après corrections, avec PG16.14 + Redis7.4.7 + les deux tests S3rver opt-in : `pytest -q -m 'not s3 and not perf and not recette_corpus and not integration_docker' -p no:cacheprovider` : **1 616 réussis, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements**, `/tmp/seamtech-suite-canonique-s3rver.log`. Une autre commande de sélection différente a donné **1 620 réussis, 18 ignorés, 34 exclus, 3 xfail** sans URL S3rver ; ne pas comparer directement les comptes. Build Next.js, `npx tsc --noEmit`, Ruff ciblé et `git diff --check` réussis. Pas de commit final, pas de push, pas de CI sur l'arbre actuel.
- État : **NON PRÊT**. Le comportement Redis–PG n'est pas atomique entre contrôle du claim et commit ; partition, expiration, attente sous verrou et heartbeat restent à tester. S3rver n'est pas MinIO ni R2. Sauvegarde/restauration coordonnée de tout `./data`, hors site et rétention, migration/rollback sur copie représentative, OCR CI, 3 rotations PDF `xfail`, Windows/Docker/Chrome/Edge, matrice complète, charges multipart, sécurité, accessibilité étendue et endurance restent ouverts. Aucune donnée cliente utilisée.
- **Prochaine action exacte** : examiner et tester les autres écritures d'annexes et les approbations concurrentes vis-à-vis du verrou fiche ; reproduire des courses avec objets S3 et perte/expiration/partition du claim Redis, corriger sans revendiquer une atomicité distribuée inexistante. Ensuite qualifier un vrai MinIO/R2, worker séparé et parcours glisser-déposer réel ; poursuivre toutes les portes P0/P1/P2 et la CI sur le commit exact. Environnement éphémère : PG `127.0.0.1:55432`, Redis `127.0.0.1:56379`, S3rver `127.0.0.1:54568` et `:54569` ; reconstituer si ces processus ont disparu. Branche `arena/b936b3c0-seamtech-search`, aucune fusion ni déploiement.

### Indexation, approbations et rollback de scan — continuation interrompue

- ECC : `tdd-workflow`, `postgres-patterns`, `security-review`, `verification-loop`. Défauts reproduits avant correction : le crawler pouvait écraser le SHA brut et les clés objet d'un document de fiche avec son SHA de texte ; un dépôt refusait un PDF préalablement indexé car les deux SHA n'ont pas la même signification ; un scan pouvait supprimer le catalogue d'un PDF promu si la source locale n'était plus vue ; un rebuild sans FK détruisait une clé objet ; un rollback de scan effaçait un dépôt ou rétablissait l'état antérieur d'un document adopté pendant le scan. Une insertion d'approbation a également été intercalée après l'UPSERT du crawler.
- PostgreSQL : l'indexeur conserve les documents protégés (rôle/fiche, objet, liens et demandes), signale les omissions, et en mode strict annule tout le lot au lieu de signaler un import partiel ; `import_pipeline` emploie le mode strict. Il prend ensuite un verrou sur les lignes modifiées et revérifie les références : une approbation concurrente entraîne le rollback. Le dépôt accepte l'adoption d'un document indexé seulement sans objet ni référence métier détectée, réserve les chemins nouveaux en transaction et verrouille les documents existants, notamment contre l'insertion concurrente d'une approbation ; les documents avec copie objet ou approbation préexistante sont refusés pour revue. Suppression des absents, rebuild et rollback du snapshot conservent désormais les documents protégés. Tests sur deux connexions PostgreSQL et fixtures synthétiques.
- Tests : suite ciblée scan/rebuild/dépôt/grammaire/import/index **117 réussis** avant l'ajout du dernier test d'approbation, ce dernier **1 réussi** ; Ruff ciblé et `git diff --check` réussis. Suite générale précédente après garde d'indexation, avant corrections du rollback de scan : **1 627 réussis, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements**. Playwright Chromium + PG16 + Redis7 + S3rver **1 réussi** après garde d'indexation, avant changements de rollback. La suite générale suivante a été interrompue par l'utilisateur, puis tentative de reprise impossible car l'environnement éphémère a disparu : **aucun résultat de suite complète après corrections du rollback**.
- Limites : un import strict de chemin déjà protégé est refusé plutôt que versionné automatiquement ; ce n'est pas une solution fonctionnelle complète. La restauration du snapshot peut encore écraser des modifications concurrentes sur des documents non protégés ; les suppressions/références historiques et voies SQLite exigent revue. Le hash texte/brut reste partagé dans la même colonne, sans migration de séparation. Redis–PG n'est pas atomique. S3rver reste un émulateur, pas MinIO/R2. Les autres portes P0/P1/P2 et la CI exacte restent ouvertes : **NON PRÊT**. Aucun commit final, push, déploiement ou fusion.
- **Prochaine action exacte** : reconstruire l'environnement éphémère Python/PG16/Redis7/S3rver, relancer la suite canonique après rollback, puis écrire et corriger la course de rollback avec modifications concurrentes de documents non protégés. Vérifier la compatibilité SQLite et l'import strict sur chemins protégés ; poursuivre fencing Redis–PG, MinIO/R2 réel, sauvegarde/restauration coordonnée, migrations, OCR/rotations et P2. Branche `arena/b936b3c0-seamtech-search`, aucune donnée réelle utilisée.

### Reprise après interruption — SQLite et validation post-rollback

- Environnement éphémère reconstitué : PostgreSQL **16.14**, encodage UTF-8, extensions `vector`, `pg_trgm`, `unaccent` ; Redis **7.4.7** ; S3rver **3.7.1** ; Python `.venv` depuis `requirements.txt`/`requirements-dev.txt`. Une première initialisation PG en `SQL_ASCII` a provoqué des erreurs Unicode dans la campagne ; le cluster isolé a été réinitialisé en UTF-8 puis la campagne a été relancée entièrement. `pip install -e '.[dev]'` a échoué sur la découverte automatique de packages ; l'installation par les fichiers de dépendances a réussi. Aucun changement du dépôt pour ces essais.
- Suite générale après le rollback de scan PG : **1 630 réussis, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements**, `/tmp/seamtech-suite-utf8.log`. Cette suite comprend les deux tests réseau S3rver opt-in mais n'est pas une qualification MinIO/R2.
- TDD SQLite : un scan pouvait effacer la clé objet, la suppression des absents et le rebuild pouvaient supprimer le catalogue d'un objet. L'indexation SQLite conserve maintenant les documents avec clé objet ou référence FK et le mode strict échoue sans publication partielle ; la suppression et le rebuild conservent les objets. Un import d'un objet nouvellement promu sur un document non protégé met à jour les colonnes objet même quand taille/date/version sont identiques : reproduit puis corrigé. Tests ciblés SQLite/PG/index/rebuild/scan/import : **112 réussis** avant le dernier test d'import, puis suite générale complète après celui-ci : **1 632 réussis, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements**, `/tmp/seamtech-suite-sqlite-protection.log`. Ruff ciblé et `git diff --check` réussis.
- Limites non levées : la restauration d'instantané PostgreSQL peut encore écraser des écritures concurrentes sur des documents non protégés ; en SQLite le rollback restaure une sauvegarde de l'ensemble de la base et peut effacer d'autres écritures concurrentes. Le mode strict refuse les objets existants plutôt que fournir une gestion complète des versions/rejeux. Le hash texte et le hash brut restent dans la même colonne. Redis–PG n'est pas atomique ; S3rver n'est pas MinIO/R2 ; les autres portes P0/P1/P2 et la CI exacte restent ouvertes. **NON PRÊT**.
- **Prochaine action exacte** : reproduire les courses de rollback des scans PG et SQLite sur des écritures concurrentes, puis concevoir une isolation/traçabilité de transaction empêchant leur écrasement sans bloquer les propres écritures du scan ; couvrir annulations, crashs, et performances. Examiner ensuite une stratégie de versions/rejeux avec objets existants et séparation des empreintes, les partitions/expirations Redis, vrai MinIO/R2, sauvegarde/restauration intégrale, migration/rollback, OCR/rotations, Windows/Docker et P2. Services isolés en cours : PG `127.0.0.1:55432`, Redis `127.0.0.1:56379`, S3rver `127.0.0.1:54568`. Branche inchangée, aucun commit final, push, déploiement ni fusion.

### Courses de rollback reproduites et suivi des scans

- ECC appliquées : `search-first`, `tdd-workflow`, `postgres-patterns`, `security-review`, `error-handling`, `verification-loop`. Deux courses d'écrasement auparavant seulement suspectées ont été **effectivement reproduites** avec bases synthétiques et deux `SearchIndex` indépendants : après snapshot, le scan écrit une version, une autre connexion écrit une nouvelle version puis le scan échoue ; après rollback la taille observée est l'ancienne, pas celle écrite par l'autre connexion, **sur SQLite et PostgreSQL**. Script éphémère `/tmp/seamtech-rollback-race-probe.py`. Ce défaut critique demeure ouvert : les protections des documents liés/objets ne couvrent pas les documents ordinaires et le backup SQLite restaure aussi d'autres tables. N'affirmer aucune isolation ni restauration concurrente correcte.
- Autres défauts reproduits puis corrigés avec tests : échec du dernier paquet, suppression des absents, lecture du manifeste ou marquage de fin laissaient le scan `running` ; ces étapes passent maintenant par le suivi `failed`, sans masquer l'erreur d'origine si l'écriture du suivi échoue. Au tout premier scan SQLite, un échec du rollback sur base vide pouvait remplacer l'erreur initiale ; le schéma est initialisé avant snapshot. Un rollback SQLite échoué conserve désormais l'instantané et annote l'erreur d'origine ; les connexions SQLite sont fermées. Un snapshot SQLite existant est réservé par création exclusive et jamais écrasé ; un snapshot PostgreSQL de même nom existant est refusé sans le supprimer. La journalisation dit désormais « rollback demandé », pas « index préservé » avant preuve de restauration. Tests synthétiques sur échecs injectés et snapshots existants.
- Le test HTTP S3rver de bout en bout inclut désormais un scan qui ne voit plus les deux sources déplacées, puis relit les copies objet via les vues : **1 réussi**. S3rver reste un émulateur, pas MinIO/R2.
- Campagne ciblée scan/rebuild/indexeur/PG/dépôt : **106 réussis** avant l'ajout du dernier test de suivi ; `tests/test_scan_safety.py` final **12 réussis**. Suite générale PG16.14 + Redis7.4.7 + S3rver réseau, hors marqueurs `s3`, `perf`, `recette_corpus`, `integration_docker` : **1 640 réussis, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements**, `/tmp/seamtech-suite-scan-suivi.log`. Ruff ciblé et `git diff --check` réussis avant la suite. Pas de CI finale, commit final, push, fusion ou déploiement.
- **Prochaine action exacte** : concevoir et tester une vraie isolation transactionnelle ou un journal fiable des mutations du scan qui restaure uniquement ses écritures tout en préservant les modifications concurrentes et toutes les autres tables SQLite ; tester crash, suppression et charge sans affirmer une garantie non prouvée. Les solutions de verrou global nécessitent d'éviter les blocages de propres connexions du scan, d'intégrer toutes les voies d'écriture, les claims/heartbeats et Windows ; ne pas poser un verrou partiel présenté comme complet. Poursuivre ensuite MinIO/R2 réel, sauvegarde/restauration intégrale, migration/rollback, OCR/rotations, plateformes et toutes portes P2. **NON PRÊT**, branche inchangée, aucune donnée réelle utilisée.

### Scan CLI transactionnel — rollback sans restaurer l'état global

- ECC appliquées : `tdd-workflow`, `postgres-patterns`, `security-review`, `error-handling`, `verification-loop`. La commande d'indexation `run_index` utilise maintenant une transaction unique plutôt que le rollback par instantané : la connexion est propagée aux opérations imbriquées dans le contexte du scan, sans commit partiel, puis validée ou annulée par son propriétaire. Le suivi du scan est créé avant cette transaction ; la clôture « completed » est transactionnelle et « failed » est écrite après rollback, avec conservation de l'erreur d'origine si ce suivi échoue. `KeyboardInterrupt` annule aussi la transaction et marque l'échec si la base est disponible.
- PostgreSQL : rollback limité aux propres écritures ; tests avec seconde connexion : mise à jour concurrente sur une autre ligne conservée et écriture de la même ligne bloquée pendant le scan ; test du CLI réel sur lignes distinctes. SQLite : `BEGIN IMMEDIATE` bloque les autres écritures pendant le scan, mais les propres opérations utilisent la même connexion ; tests sur table tierce, annulation et rebuild DDL. Les scripts SQLite sont exécutés sans `executescript` dans la transaction pour éviter son COMMIT implicite ; `journal_mode` reste configuré avant transaction. Tests de crash de processus (`os._exit`) PG et SQLite : la transaction non validée est annulée. Ce n'est pas une preuve de résistance à une panne système, saturation, coupure réseau, deadlocks ou toutes les plateformes. Un premier essai de concurrence PostgreSQL a révélé que `initialize()` exécuté DANS la transaction prenait un verrou de relation bloquant même les lignes distinctes : corrigé en initialisant hors transaction pour un scan normal ; un rebuild peut toujours poser des verrous DDL étendus.
- L'ancienne méthode publique `scan_snapshot` demeure dans le code et **reste dangereuse si appelée directement pendant des écritures concurrentes**, mais aucune voie de production trouvée ne l'appelle depuis la modification de `run_index` ; ses tests historiques restent présents. Il faut la retirer ou la remplacer proprement, traiter les appels externes éventuels et ne pas présenter la protection du CLI comme une preuve globale. Une transaction longue SQLite bloque tous les autres écrivains ; PostgreSQL peut bloquer des écritures sur les mêmes lignes et des rebuilds peuvent bloquer davantage. Le statut peut rester `running` après un arrêt brutal du processus, même si les octets transactionnels sont annulés. Valider timeouts, starvation, mémoire/WAL, heartbeat et reprise sous charge représentative avant GO.
- Tests ciblés scan/PG/SQLite/rebuild/index/import **131 réussis** avant les tests crash et interruption ; nouveaux tests crash et interruption réussis individuellement. Suite générale après tous ces changements : **1 648 réussis, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements**, PG16.14 + Redis7.4.7 + S3rver, `/tmp/seamtech-suite-scan-atomique-final.log`. Ruff ciblé et `git diff --check` réussis. Micro-mesure synthétique d'insertion dans une unique transaction : 10 000 enregistrements métadonnées, SQLite **0,54 s** RSS max **32 892 Kio**, PostgreSQL **1,14 s** RSS max **40 916 Kio**, `/tmp/seamtech-scan-volume.log` ; pas de lecture de fichiers, charge concurrente ou volume réel.
- **Prochaine action exacte** : supprimer ou sécuriser l'ancienne méthode `scan_snapshot` sans perdre les garanties ni masquer les erreurs ; tester en charge les transactions longues, interruptions et contention du scan, statut de crash, erreurs de commit et reprise. Reprendre les versions/rejeux d'objets, partition/expiration Redis et vrai MinIO/R2, sauvegarde/restauration coordonnée, migration/rollback, OCR/rotations, plateformes et P2. S3rver n'est pas MinIO/R2. CI sur commit exact non réalisée ; **NON PRÊT**, aucun commit final, push, fusion ou déploiement, aucune donnée cliente.

### Ancienne API de snapshot sécurisée et contention

- ECC : `tdd-workflow`, `postgres-patterns`, `security-review`, `error-handling`, `verification-loop`. Les deux courses de l'ancienne API `scan_snapshot` ont été reproduites en tests automatisés : SQLite permettait d'écraser une autre table et PostgreSQL rétablissait une ancienne valeur après un commit concurrent. Cette API utilise maintenant elle aussi `scan_transaction` : seul le travail de la connexion du scan est annulé. Sa copie historique est conservée uniquement comme secours manuel si le rollback échoue ; elle n'est **plus rejouée automatiquement**. Les deux tests de dépôt concurrent ont été corrigés pour utiliser une seconde instance et une connexion réellement distincte, plutôt que de simuler une concurrence dans la même transaction.
- Tests PG et SQLite : écriture concurrente effectivement bloquée pendant le scan puis réussissant après commit/rollback ; accès concurrent PG à une autre ligne préservé ; crash de processus, retour arrière, rebuild et suivi déjà éprouvés. En cas d'échec de rollback, l'erreur initiale est conservée avec note, la connexion PG défaillante est abandonnée, et la copie de secours reste présente ; en cas d'échec du nettoyage ou du pool, l'erreur initiale reste également visible avec note. Un test ciblé vérifie qu'un échec de commit simulé entraîne rollback et reste visible : il ne prouve pas l'issue d'un commit réseau ambigu déjà accepté côté serveur.
- Suite ciblée après sécurisation de l'API : **119 réussis** avant tests supplémentaires de contention ; les deux tests de contention puis deux tests ciblés pool/commit réussis. Suite générale après la sécurisation et les tests de contention, avant l'ajout du seul test de commit : **1 654 réussis, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements**, `/tmp/seamtech-suite-snapshot-transactionnel.log`. Ruff ciblé et `git diff --check` réussis ; le test de commit ajouté après la suite a réussi isolément.
- Limites : transaction longue SQLite bloque tous les autres écrivains (timeout de connexion 30 s) ; PG bloque les mêmes lignes et les rebuilds DDL peuvent bloquer une table. Pas de qualification sur le volume annuel réel, WAL, espace disque, latence, Windows ou panne système ; un crash brutal peut laisser le statut `running`. Résultat d'un commit PG perdu sur le réseau peut être ambigu ; la remise à `failed` ne prouve pas que le commit n'a pas été appliqué. La méthode interne `_restaurer_documents` n'est plus utilisée automatiquement et son invocation manuelle demeure à risque de conflit concurrent : toute récupération nécessite isolation préalable. Les autres portes P0/P1/P2, MinIO/R2 réel et CI exacte restent ouvertes ; **NON PRÊT**.
- **Prochaine action exacte** : tester et corriger les états de suivi après crash/commit ambigu et les interactions de contention/timeout avec le worker, sans prétendre une atomicité réseau distribuée ; qualifier charge/WAL et Windows. Ensuite reprendre versions/rejeux objet, partition/expiration Redis, vrai MinIO/R2, sauvegarde/restauration coordonnée, migration/rollback, OCR/rotations et toutes les portes P2. Branche inchangée, aucun commit final, push, fusion ou déploiement, aucune donnée cliente.

### Claims de l'import classique et suite à reprendre

- ECC : `search-first`, `tdd-workflow`, `redis-patterns`, `security-review`, `verification-loop`. Deux tests d'abord rouges ont révélé que l'import classique pouvait commencer sans claim du registre alors que son claim Redis était encore présent, et qu'un worker déchu pouvait effacer l'annulation destinée au repreneur. Le contrôle initial exige maintenant explicitement la propriété du registre et, si configuré, celle de Redis ; le nettoyage de l'annulation est évité lorsque la propriété est perdue ou ne peut pas être vérifiée, et les cas d'échec/annulation du worker déchu retournent `stale_worker`. Tests utilisant Redis réel et registre SQLite : les deux passent. Ce contrôle au début et au nettoyage ne constitue pas une transaction distribuée ni un fencing de toutes les mutations pendant l'import ; la course entre vérification et suppression du drapeau existe encore.
- Tests ciblés worker, Redis et PG : **58 réussis** avec un avertissement, `/tmp/seamtech-worker-claim-check.log`. Suite générale suivante : **1 656 réussis, 1 échec, 16 ignorés, 40 exclus, 3 xfail, 5 avertissements**, `/tmp/seamtech-suite-claim-preflight.log`. L'échec venait du test de volume absent qui ne créait ni job ni claims : il testait auparavant un appel irréaliste. Test corrigé en créant le job et les deux claims, sans masquer les exceptions ; il passe isolément et exige toujours l'échec de l'import pour chemin absent. Ruff ciblé et `git diff --check` passent. **La suite complète n'a pas encore été relancée après cette correction.**
- Inspection supplémentaire : la reprise Redis parcourt la liste et exécute `LREM` puis `RPUSH` séparément : un crash entre les deux peut perdre la représentation de la tâche ; la gestion de lectures claim en panne et les interleavings doivent être vérifiés sur Redis réel. L'acquittement dans la boucle précède la reprogrammation de l'échec ; sa robustesse en cas de crash doit également être analysée. Ne pas affirmer que la file ne perd jamais de tâche avant de démontrer ou corriger ces cas. Les statuts `running` après crash, commit réseau ambigu, transactions longues, objets versionnés, MinIO/R2 réel, sauvegarde coordonnée, OCR/rotations, plateformes et P2 restent ouverts. **NON PRÊT.**
- **Prochaine action exacte** : relancer la suite complète après correction du test de volume, puis écrire des tests de panne et concurrence Redis réels pour `reprendre_taches_abandonnees` et la transition ack/retry, corriger atomiquement sans perdre ni dupliquer silencieusement les tâches ; préserver les annulations sous transfert de claim. Poursuivre ensuite toutes les autres portes documentées. Aucun commit, push, fusion ou déploiement.

### Suite générale et reprise Redis — travail en cours

- La suite générale après correction du test volume absent a réussi **1 657 passed, 16 skipped, 40 deselected, 3 xfailed, 5 warnings**, `/tmp/seamtech-suite-correction-volume.log` ; elle précède tous les changements Redis ci-dessous et ne vaut pas CI sur commit exact.
- ECC `redis-patterns`, `tdd-workflow`, `error-handling`, `verification-loop` : tests rouges sur Redis 7 réel ont montré (1) duplication de reprise par deux balayeurs, (2) vol après lecture périmée du claim, (3) perte si destination Redis a le mauvais type, (4) worker retardé revendiquant après déplacement, (5) perte entre ack et retry, (6) retry/lettre morte répétée, (7) deux promotions concurrentes d'un retry, (8) heartbeat ressuscitant un claim expiré, (9) redélivrance d'un job déjà terminé qui le remettait en `running`. Les corrections utilisent des scripts Lua avec précontrôle de type, test atomique de présence de la livraison/claim, transfert conditionné de la livraison exacte, heartbeat par prolongation seulement, et évitent l'ack préalable pour retry/lettre morte. La boucle refuse de réactiver les jobs terminaux et acquitte leur redélivrance. Ces correctifs ne prouvent pas l'atomicité PostgreSQL–Redis ni les crashs réseau ambigus.
- Suite ciblée intermédiaire avant les deux derniers changements heartbeat et statut terminal : **91 passed, 1 warning**, `/tmp/seamtech-reprise-suite2.log`. Les nouveaux tests heartbeat, redélivrance et crash ack passent isolément : **4 passed** ; autres tests de scripts : **9 passed** dans sélection ciblée. Deux échecs intermédiaires sur la raison de lettre morte ont été corrigés, puis vérifiés dans la suite 91. Ruff ciblé et `git diff --check` réussis avant les derniers changements ; **relancer suite ciblée et générale sur l'état final**.
- Risques encore ouverts : `reconcilier_file` peut remettre à `pending` et effacer un claim PostgreSQL d'un worker ayant acquis la tâche après sa remise en file Redis ; plusieurs réconciliateurs peuvent réenfiler le même job absent de la file ; la fin des jobs de lot en échec libère le claim PostgreSQL avant que la boucle programme le retry ; la fermeture et l'acquittement restent séparés ; le repli `BLPOP` est non atomique, les scripts Redis ne résolvent ni perte d'AOF ni panne entre bases. Les conditions de concurrence, réception et purge demeurent non qualifiées. **NON PRÊT**.
- Prochaine action exacte : test rouge puis corriger le retry d'un lot échoué (claim libéré avant `pending`) et la course réconciliation / transfert de claim en PG avec deux workers réels ; étudier le protocole durable plutôt que prétendre une transaction distribuée. Terminer les tests sur Redis de promotion/rejeu/défaillance, relancer ciblés et général, puis reprendre les autres portes P0/P1/P2 déjà listées. Aucun commit/push/fusion/déploiement.

### Retry de lot, claim PG et acquittement précis

- Test rouge puis vert PostgreSQL 16 + Redis 7 : un lot en échec transitoire conserve maintenant son claim et le statut actif jusqu'à la décision de retry ; après échéance forcée le deuxième worker l'exécute et le termine. L'erreur reste visible en étape `failed` avant la transition. Appel local sans worker conserve la clôture immédiate en échec.
- Test rouge puis vert avec PostgreSQL et Redis : si un worker reprend la livraison et incrémente `attempts` après le transfert Redis mais avant la mise à jour du registre par le balayeur, la mise à jour compare maintenant le compteur observé avant transfert et ne supprime plus le nouveau claim. Cette protection ne couvre pas encore le second chemin « job absent de la file » ni les courses entre deux réconciliateurs ; les deux restent à traiter.
- Test rouge puis vert sur Redis : l'acquittement avec identité de worker n'efface plus une autre livraison du même job lorsque la livraison exacte est absente et retourne faux. Les modes historiques sans worker restent distincts et nécessitent un audit.
- Ciblé après les corrections lot et réconciliation : **95 passed, 1 warning**, `/tmp/seamtech-reprise-suite4.log`. La suite générale suivante a donné **1 667 passed, 2 failed, 16 skipped, 40 deselected, 3 xfailed, 5 warnings**, `/tmp/seamtech-suite-reprise-redis.log`. Les deux échecs étaient des tests unitaires reposant respectivement sur une clôture prématurée du lot et des jobs inexistants avec un ack avant retry ; leurs préconditions/assertions ont été corrigées pour tester le nouveau protocole, sans masquer l'échec métier. Ces deux tests et le test d'ack précis : **3 passed**. Ruff ciblé et `git diff --check` réussis. Nouvelle suite générale en cours : `/tmp/seamtech-suite-reprise-redis2.log` ; son résultat n'est pas encore connu à l'écriture de cette note.
- Limites persistantes : Redis et PostgreSQL ne forment pas une transaction ; l'issue d'un transfert dont la réponse réseau est perdue est ambiguë ; reprise des jobs absents peut doubler une livraison et effacer un nouveau claim, la boucle d'ack terminal peut tomber avant la clôture SQL, repli `BLPOP` non atomique, AOF et véritable arrêt brutal encore insuffisamment qualifiés. La fermeture des jobs classiques, l'annulation, la quarantaine/purge et le heartbeat lors d'opérations longues nécessitent davantage de fencing. Aucune des portes P0/P1/P2 complètes, ni CI exacte, n'est validée. **NON PRÊT.**
- Prochaine action exacte : récupérer résultat de la suite en cours, corriger toute régression ; tester puis sécuriser le chemin « absent de la file » avec deux réconciliateurs et prise de claim concurrente sans supposer une transaction distribuée ; traiter le crash ack terminal/SQL et les autres limites ci-dessus, puis poursuivre P0/P1/P2. Aucun nouveau commit, push, déploiement ou fusion.

### Réconciliation d'un job absent et import classique

- ECC `redis-patterns`, `tdd-workflow`, `postgres-patterns`, `verification-loop`. Deux tests rouges `/tmp/seamtech-reconciliation-db-rouge.log` ont démontré, sur PostgreSQL 16 et Redis 7 réels, la double remise en file par deux réconciliateurs et l'effacement du nouveau claim PG. La remise en file conditionnelle est maintenant un script Lua qui vérifie claim, file, traitement et retry avant insertion ; la mise à jour PG compare la version `attempts` observée. Les deux tests passent, ainsi que le refus d'insertion quand le claim ou une livraison est présent. Ce script parcourt les files complètes côté Redis : ses performances et l'effet sur la latence/heartbeat sous forte charge ne sont pas qualifiés. Aucune garantie d'atomicité Redis–PG ni de réponse réseau non ambiguë.
- Deux tests rouges `/tmp/seamtech-simple-reprise-rouge.log` ont montré qu'un import classique pouvait inscrire `failed` ou `upload_incomplete` en base avant le transfert retry, le rendant non récupérable après crash. Pour les workers identifiés, ces cas restent maintenant `running` avec l'étape/erreur visible et le claim PG conservé jusqu'à la transition de la boucle ; les appels locaux gardent leur comportement terminal. Les deux tests passent. La purge/quarantaine et les écritures d'index pendant la perte de claim restent à auditer.
- Résultats : suite générale `/tmp/seamtech-suite-reprise-redis2.log` **1 669 passed, 1 failed** (ancien test utilisait une charge différente de la livraison réellement sortie ; corrigé et passé). Suite `/tmp/seamtech-suite-reprise-redis3.log` **1 670 passed, 3 failed, 16 skipped, 40 deselected, 3 xfailed, 5 warnings** (trois doubles de file avaient encore l'ancien nom de méthode ; adaptés et testés **13 passed**). Suite ciblée après ces corrections et l'import classique : **27 passed**, `/tmp/seamtech-reprise-suite6.log` ; tests ciblés précédents de concurrence, ack et panne : **6 passed**. Ruff ciblé et `git diff --check` passent. Suite générale nouvelle `/tmp/seamtech-suite-reprise-redis4.log` en cours, résultat encore inconnu ; elle comprend les changements et nouveaux tests disponibles à sa collecte.
- Restent : vérifier suite générale et régression PG/Redis ; crash ack terminal avant SQL, erreurs Redis ambiguës, repli BLPOP, promotion/rejeu et AOF, coût du script sur gros backlog, opérations longues et annulations ; puis sauvegarde, MinIO/R2 véritable, migrations, OCR/rotations, plateformes et P2. **NON PRÊT.** Aucune CI sur commit exact, aucun nouveau commit, push, fusion ou déploiement.
- **Prochaine action exacte** : récupérer `/tmp/seamtech-suite-reprise-redis4.log`, corriger tout échec ; écrire un test de crash entre ack terminal et fermeture SQL, concevoir reprise sûre sans promettre une transaction distribuée ; qualifier les scripts en concurrence et volume, puis continuer tous les autres points du plan.

### Vérification après correctifs de reprise

- Suite générale PG16 UTF-8 + Redis7 + S3rver : **1 675 passed, 16 skipped, 40 deselected, 3 xfailed, 5 warnings**, `/tmp/seamtech-suite-reprise-redis4.log` ; elle est antérieure au seul nouveau test ci-dessous, non à une modification de code postérieure. Elle n'est pas une CI sur commit exact ; les exclusions, skips et xfail restent des réserves.
- Test ultérieur `/tests/test_worker_crash_terminal.py` : interruption Python injectée après publication `completed` dans SQLite et avant ack Redis, puis redélivrance et reprise par un autre worker : **1 passed**, l'import n'est exécuté qu'une fois et la source reste présente. Ce test n'est ni un SIGKILL ni une panne réseau, n'exerce pas PG et ne prouve pas tous les chemins terminaux ou la robustesse du commit ambigu.
- Micro-mesure isolée `/tmp/seamtech-reconciliation-volume.log` : une recherche/ajout en Lua parmi 100 000 petites charges synthétiques en file sur Redis local a duré **0,119 s**, avec ping concurrent bloqué au maximum **0,119 s** (4 173 pings) ; une seule mesure sans réseau réel ni concurrence de production, pas une qualification volume ni endurance.
- Ruff ciblé et `git diff --check` réussis avant le test supplémentaire, dont Ruff passe séparément. Aucun commit, push, fusion ou déploiement. Application **NON PRÊTE**.
- **Prochaine action exacte** : vérifier et corriger les crashs, pannes Redis et reprises sur processus distincts avec PostgreSQL et disque, notamment entre écriture terminale/ack et transfert retry/état PG ; traiter les modes anciens `BLPOP`, replay et persistance AOF, les opérations longues/perte de claim, puis toutes les portes P0/P1/P2 restantes et la CI sur SHA exact. Ne pas affirmer une atomicité entre PostgreSQL et Redis.

### Rejeu des lettres mortes et compatibilité Redis

- Deux tests d'abord rouges sur Redis réel et double contrôlé : `replay_deadletters` perdait une lettre si la destination n'était pas une liste après `LPOP`, et le repli `BLPOP` pouvait dépiler avant de tracer la livraison. Le rejeu utilise maintenant `LMOVE` atomique ; le consommateur refuse les clients sans `BLMOVE` au lieu d'utiliser le repli dangereux. Ceci exige un client/serveur compatibles et ne résout pas les réponses réseau ambiguës ni la persistance Redis.
- Suite ciblée Redis + PG, nouveaux tests de reprise, worker, rejouement et échecs : **65 passed**, `/tmp/seamtech-queue-suite-final.log`. Ruff ciblé et `git diff --check` réussis. La suite générale **1 675 passed** ci-dessus précède ces deux dernières modifications et n'a pas été relancée depuis. Le test de crash terminal ajouté juste après cette suite et les derniers tests ne sont pas couverts par ce total général.
- **Prochaine action exacte** : relancer la suite générale et vérifier notamment les chemins de compatibilité Redis ; poursuivre vrais crashs processus/AOF/PG, rejouement d'objets et fencing ; puis P0 sauvegarde/MinIO et l'ensemble P1/P2 avant CI exacte. Les limites déjà listées persistent. **NON PRÊT**, aucun commit/push/fusion/déploiement.

### Suite après rejeu atomique

- Suite générale `/tmp/seamtech-suite-reprise-redis5.log` : **1 676 passed, 2 failed, 16 skipped, 40 deselected, 3 xfailed, 5 warnings**. Deux anciens tests utilisaient encore le repli BLPOP ou simulaient l'échec de `LPOP` alors que le rejeu emploie `LMOVE` ; leurs doubles ont été corrigés sans relâcher la sûreté. Les deux tests corrigés et les deux nouveaux tests dédiés réussissent : **4 passed, 1 warning**. Ruff ciblé et `git diff --check` passent. La suite générale doit être relancée après ces corrections : aucun résultat global vert ne couvre encore les modifications `LMOVE`/retrait de `BLPOP`.
- **Prochaine action exacte** : relancer la suite générale avec PG16, Redis7 et S3rver, corriger toute régression, puis poursuivre les interruptions par processus/AOF, le fencing PG–Redis et tous les autres points P0/P1/P2 déjà listés. Toujours **NON PRÊT**, aucun nouveau commit/push/fusion/déploiement.

### Reconstitution du banc et suite complète après LMOVE

- ECC `verification-loop`, `postgres-patterns`, `redis-patterns` : le sandbox ayant été réinitialisé, environnement Python, PostgreSQL **16.14 UTF-8**, Redis **7.4.7** et S3rver 3.7.1 ont été reconstruits isolément. PostgreSQL embarqué sans pgvector a reçu une extension pgvector 0.8.1 compilée à partir des en-têtes source 16.14 ; une opération vectorielle a été vérifiée. Les exécutables clients pg_dump/pg_restore 16.14 ont été compilés séparément sans zlib/ICU et ne qualifient pas une installation officielle de production.
- Échecs d'environnement observés et corrigés : première tentative après réinitialisation `.venv` absente ; `/tmp/seamtech-suite-reprise-redis7.log` **1 287 passed, 22 failed, 369 errors** car le rôle PostgreSQL attendu était absent ; `/tmp/seamtech-suite-reprise-redis8.log` **1 291 passed, 18 failed, 369 errors** car pgvector absent ; `/tmp/seamtech-suite-reprise-redis9.log` **1 669 passed, 9 failed** car les binaires pg_dump/pg_restore étaient absents. Les tests de sauvegarde ciblés avec les binaires reconstitués ont ensuite donné **20 passed, 2 skipped**.
- Suite générale complète sur l'état de travail courant : **1 678 passed, 16 skipped, 40 deselected, 3 xfailed, 4 warnings**, `/tmp/seamtech-suite-reprise-redis10.log`, commande `pytest -q -m 'not s3 and not perf and not recette_corpus and not integration_docker' -p no:cacheprovider` avec PG16/Redis7/S3rver. Résultat local sur fichiers non commités, pas CI sur SHA exact. Les 3 rotations PDF restent xfail ; MinIO/R2 réel, endurance et les autres exclusions ne sont pas qualifiés. **NON PRÊT.**
- Prochaine action exacte : tests de vrais crashs processus/AOF et PG du worker et des transitions Redis, puis auditer fencing opérations longues et défaillances ; poursuivre toutes les portes sauvegarde, migration, MinIO/R2, OCR/rotations, plateformes et P2. Aucun commit/push/fusion/déploiement, aucune donnée cliente.

### Crash réel du worker séparé sur PostgreSQL

- ECC `e2e-testing`, `redis-patterns`, `tdd-workflow`, `verification-loop` : nouveau test `tests/test_worker_process_postgres.py` sur base PostgreSQL 16.14 jetable et Redis 7 réel. Un vrai processus `worker_service` démarre un import synthétique, est tué par **SIGKILL** après prise du claim PG/Redis ; le claim expire réellement, un second processus reprend, le job finit `completed`, les documents ne sont pas dupliqués par chemin et les fichiers d'origine restent présents. **1 passed** `/tmp/seamtech-worker-kill-pg.log`.
- Campagne ciblée avec test existant de serveur Redis dédié AOF `appendfsync always` tué puis redémarré, test précédent du worker SQLite et nouveau test PG : **5 passed**, `/tmp/seamtech-crash-services.log`. Ruff ciblé et `git diff --check` réussis. Ce n'est pas une simulation de coupure électrique, de réponse réseau perdue, de Redis `appendfsync everysec`, d'un lot avec stockage objet réel ni d'une panne durant chacune des instructions Redis/PG. La suite générale **1 678 passed** précède l'ajout du nouveau test seul ; aucune modification de code depuis cette suite, sauf ajout de test.
- **Prochaine action exacte** : instrumenter et tuer le processus aux frontières de transfert retry / mise à jour PG et clôture / ack, sans donnée cliente, avec PostgreSQL et Redis AOF ; vérifier reprise et préservation, puis poursuivre fencing des opérations longues, stockage MinIO/R2 réel, sauvegarde/restauration, migration, OCR/rotations, plateformes et P2. CI sur SHA exact et portes finales non validées : **NON PRÊT**. Aucun commit, push, fusion ou déploiement.

### Frontières de transfert, SIGKILL réel

- ECC `e2e-testing`, `redis-patterns`, `postgres-patterns`, `verification-loop`. Quatre scénarios instrumentés supplémentaires dans `tests/test_worker_process_postgres.py` : processus séparé tué par SIGKILL immédiatement avant/après le transfert atomique vers retry et immédiatement avant/après l'ack terminal. PostgreSQL 16 et Redis 7 réels ; après expiration réelle du claim, un autre processus reprend. Les états PG et la localisation Redis attendus sont vérifiés avant reprise, puis le job termine et la source synthétique subsiste. **4 passed**, `/tmp/seamtech-worker-kill-frontieres2.log` ; Ruff et `git diff --check` réussis. Le traitement lui-même est remplacé par un double ciblé dans ces quatre scénarios ; Redis utilisé n'est pas l'instance AOF du test séparé ; ils ne prouvent pas les réponses réseau ambiguës ni toutes les frontières possibles. Première tentative de ces tests : quatre échecs de harnais (import erroné de la fonction de chargement de configuration), corrigés avant la réussite.
- Suite générale `1 678 passed` ne comprend pas ces cinq nouveaux scénarios (quatre frontières et le worker PG) ; relancer avant toute qualification. Les portes précédentes restent ouvertes : **NON PRÊT**. Aucun commit/push/fusion/déploiement.
- **Prochaine action exacte** : tester arrêt Redis et PG pendant des opérations longues, claim Redis/PG et réponse perdue, puis qualifier AOF et impacts de latence ; reprendre stockage MinIO/R2 réel, sauvegarde/restauration coordonnée, migration, OCR/rotations, plateformes et P2, puis CI sur commit exact.

### Suite générale avec les crashs PG séparés

- Suite PG16 + Redis7 + S3rver, après ajout des cinq tests de worker PG : **1 683 passed, 16 skipped, 40 deselected, 3 xfailed, 4 warnings**, `/tmp/seamtech-suite-worker-pg-crash.log` ; sélection `not s3 and not perf and not recette_corpus and not integration_docker`. Résultat local, sans CI sur commit exact. Les précédentes réserves sur compilation locale de pgvector et des outils client PostgreSQL restent applicables.
- Tentative de recherche d'un exécutable MinIO autonome : aucun Go, Docker ou Podman dans ce sandbox, paquet npm de test téléchargé mais qui ne contient pas le serveur. Une copie tierce non vérifiée n'a pas été exécutée ; S3rver reste un émulateur et ne remplace pas MinIO/R2. Les autres portes et réserves demeurent ouvertes : **NON PRÊT**, aucun commit/push/fusion/déploiement.
- **Prochaine action exacte** : poursuivre sans extrapoler les crashs instrumentés à l'ensemble des pannes : tester interruption Redis et PG sous opérations longues et claims, puis poursuivre préservation/versions d'objets, vraie qualification MinIO/R2, sauvegarde coordonnée, migrations, OCR/rotations, plateformes et P2 avant CI sur SHA exact.

### Annulation et claim d'un import classique

- ECC `tdd-workflow`, `redis-patterns`, `error-handling`, `security-review`, `verification-loop` : deux tests rouges sur Redis réel ont mis en évidence la suppression d'une annulation reçue pendant un échec réessayable, et celle d'une annulation du repreneur lorsque le claim change entre contrôle et nettoyage. Le worker identifié conserve désormais le drapeau en cas d'échec/`upload_incomplete` ; sur terminaison, le nettoyage Redis et le raccourcissement du heartbeat sont conditionnés atomiquement en Lua au claim du même worker. En cas d'échec ou perte de claim, le drapeau local est également conservé. Ces deux tests sont verts.
- Troisième test rouge : après perte du claim lors d'une extraction, le callback de progression autorisait l'étape d'indexation. Chaque callback d'import classique contrôle maintenant les claims et interrompt si perte constatée ; test vert. Ce contrôle ponctuel **ne clôt pas** la course entre callback et upsert, ne protège pas atomiquement Redis et PostgreSQL et n'interrompt pas nécessairement une extraction/un upload déjà en cours ; les effets sur objets, rapports et fichiers restent à auditer.
- Campagne ciblée PG16/Redis7 avec travailleurs et crashs processus : **73 passed**, `/tmp/seamtech-cancel-fence-cible.log` ; Ruff ciblé et `git diff --check` réussis. La suite générale précédente **1 683 passed** précède ces modifications de code ; suite complète à relancer. Aucune CI sur SHA exact, MinIO/R2 réel ni autres portes P0/P1/P2 validées : **NON PRÊT**. Aucun commit/push/fusion/déploiement.
- **Prochaine action exacte** : tester l'interleaving entre callback/indexation et transfert du claim, concevoir une transaction PG avec verrouillage/fencing couvrant `_save_import` et les documents sans prétendre atomicité Redis–PG ; analyser annulation, objets et quarantaine/purge sous panne et reprise. Relancer la suite générale puis poursuivre sauvegarde, stockage réel, migrations, OCR/rotations, plateformes et P2.

### Publication transactionnelle de l'import classique

- ECC `tdd-workflow`, `postgres-patterns`, `redis-patterns`, `security-review`, `verification-loop` : test rouge PostgreSQL + Redis réel `/tmp/seamtech-simple-upsert-claim-rouge.log` : perte du claim pendant l'upsert laissait un document indexé malgré retour `stale_worker`. L'import classique d'un worker identifié publie désormais les documents et la ligne `imports` dans une même transaction ; il verrouille le job PG `FOR UPDATE` (SQLite : transaction d'écriture), vérifie le claim Redis et l'annulation avant et après les écritures. Le rollback est déclenché si la perte ou l'annulation est déjà visible avant commit. Les appels historiques sans worker gardent leur voie habituelle.
- Tests PG réels : perte du claim pendant l'upsert annule documents et import, panne `_save_import` annule l'upsert, transfert concurrent de claim PG sur deuxième connexion attend la fin du commit puis réussit. Test SQLite + Redis de perte du claim : rollback également constaté. **4 passed** ciblés ; campagne élargie import, workers, PG/Redis et crashs : **83 passed, 1 warning**, `/tmp/seamtech-import-fencing-target.log`. Une première commande de campagne n'a rien exécuté car elle citait un fichier de test inexistant ; commande corrigée ensuite. Ruff ciblé et `git diff --check` réussis.
- Limites : l'upload/rapports se produisent avant cette transaction ; ils peuvent laisser des objets ou rapports orphelins. Le contrôle Redis juste avant commit et le commit PG ne sont pas atomiques : perte du claim dans cette fenêtre ou réponse réseau ambiguë restent à qualifier ; pas de garantie distribuée. L'import historique hors worker ne bénéficie pas du nouveau verrou. La transaction accroît la contention sur le job, et SQLite bloque les autres écrivains ; latence et gros volumes non qualifiés. La suite générale **1 683 passed** précède ces modifications ; relancer. MinIO/R2 réel et toutes les autres portes demeurent ouvertes, **NON PRÊT** ; aucun commit/push/fusion/déploiement.
- **Prochaine action exacte** : relancer la suite générale après le fencing de publication ; tester annulation et pannes entre vérification Redis et commit, effets objets/rapports et concurrence PG réelle, puis reprendre les portes sauvegarde, stockage réel, migrations, OCR/rotations, plateformes et P2. Ne pas présenter cette correction comme une transaction distribuée.

### Suite générale après fencing d'import

- Suite PG16/Redis7/S3rver `/tmp/seamtech-suite-import-fencing.log` : **1 689 passed, 1 failed, 16 skipped, 40 deselected, 3 xfailed, 4 warnings**. L'échec exposait une régression de compatibilité : le nettoyage local historique d'annulation après une panne Redis ne supprimait plus le drapeau mémoire même pour un appel sans worker. Corrigé : conservation fail-closed si un worker identifié est impliqué, comportement local historique sinon. Tests de régression annulation/claims/publication PG : **34 passed**, `/tmp/seamtech-cancel-regression.log`. Ruff ciblé et `git diff --check` réussis.
- **Prochaine action exacte** : relancer la suite générale après cette correction puis approfondir la concurrence et l'annulation entre contrôle Redis et commit, effets objets/rapports, avant toutes les portes P0/P1/P2 et CI exacte. Les limites exposées ci-dessus persistent ; **NON PRÊT**, aucun commit/push/fusion/déploiement.

### Contrôle de l'annulation pendant publication et suite relancée

- Test PostgreSQL 16 + Redis 7 réel : drapeau d'annulation posé après `_save_import` mais avant le contrôle final ; transaction annulée, aucun document ni import publié, source préservée et statut annulé. Les quatre tests PG de publication passent ; ne couvre pas l'annulation après le dernier contrôle Redis.
- Suite générale relancée après correction : `/tmp/seamtech-suite-import-fencing2.log` **1 691 passed / 16 skipped / 40 deselected / 3 xfailed / 4 warnings** (sélection excluant `s3`, `perf`, `recette_corpus`, `integration_docker`). Ruff ciblé et `git diff --check` passent. Ce n'est ni une CI sur commit exact ni une qualification des catégories exclues.
- **Prochaine action précise** : instrumenter l'interstice entre dernier contrôle Redis et commit PG et l'effet du transfert de claim ou d'une annulation ; examiner si la synchronisation est réellement possible sans transaction distribuée et protéger les effets objets/rapports sans suppression d'objets partagés. Puis continuer les portes sauvegarde/restauration complète, MinIO/R2 réel, migration, OCR/rotation, plateformes et P2. P0/P1/P2 demeurent **NON PRÊTS** ; aucun commit/push/fusion/déploiement.

### Défaut confirmé dans l'interstice Redis–PostgreSQL

- Injection déterministe réelle après le dernier contrôle Redis et avant la sortie de `scan_transaction` (donc avant commit PG) : transfert du claim à un autre worker, résultat du premier `stale_worker`, **mais un document et une ligne import sont néanmoins publiés**. Preuve `/tmp/seamtech-probe-interstice.log` (1 passed, assertion du comportement observé). Le défaut n'est donc pas seulement théorique.
- Test de non-régression exprimant la propriété attendue sans l'affaiblir ajouté dans `tests/test_import_simple_fencing_pg.py::test_transfert_redis_apres_dernier_controle_ne_doit_pas_publier`, **xfail strict temporaire** : `/tmp/seamtech-interstice-xfail.log` : 4 passed / 1 xfailed. Ce marqueur est une dette bloquante, pas une validation ; le supprimer après correction. Ruff ciblé et `git diff --check` réussis. La suite 1 691 passed précède l'ajout de ce test, pas le code qu'il évalue.
- **Prochaine action précise** : concevoir et tester un unique arbitrage de propriété faisant autorité en PG pour claim, annulation et publication, incluant transferts/reprises concurrents et mort du worker ; vérifier si tous les chemins de transfert respectent le verrou PG et éviter de prétendre à une transaction Redis–PG. Tester ensuite tous effets en amont et de quarantaine, et retirer l'xfail uniquement après preuve réelle. Toutes portes restantes inchangées ; P0/P1/P2 **NON PRÊTS**, pas de commit/push/fusion/déploiement.

### Annulation d'import classique inter-processus et résultat concurrent

- ECC `search-first`, `tdd-workflow`, `postgres-patterns`, `security-review`, `verification-loop` : l'API `cancel_job` écrit l'état en base et son drapeau mémoire n'est pas partagé par un worker séparé. Test PG/Redis avec annulation SQL depuis une autre connexion, sans drapeau mémoire ni Redis : rouge `/tmp/seamtech-cancel-interprocess-rouge.log` (le worker ne voyait pas la demande et renvoyait `failed`). Le contrôle d'annulation du worker vérifie désormais aussi le statut en base ; le callback s'interrompt si cette annulation est visible.
- Deuxième injection concurrente, annulation en PG immédiatement avant l'écriture du résultat, après les précédents contrôles : rouge `/tmp/seamtech-cancel-final-rouge.log`, le worker renvoyait `completed` et effectuait une purge simulée malgré l'annulation. Après mise à jour conditionnelle du job, le worker vérifie maintenant l'état et le propriétaire renvoyés et s'arrête avant la purge si l'annulation ou la perte de propriétaire est déjà visible. Campagne PG/Redis/SQLite `/tmp/seamtech-cancel-interprocess-suite2.log` **47 passed / 1 xfailed** ; Ruff ciblé et `git diff --check` réussis.
- **Limites** : l'annulation ou le transfert peuvent encore arriver après ce contrôle et avant purge/quarantaine ; les écritures objet et rapports antérieures ne sont pas transactionnelles ; l'API peut aussi annuler un job terminal et la sémantique métier de cette course reste à examiner. Le test utilise une deuxième connexion PG, pas deux processus réels pour l'annulation. L'xfail strict de transfert Redis avant commit demeure. La suite générale 1 691 passed précède ces changements.
- **Prochaine action précise** : qualifier avec test de processus séparé et concurrence réelle la synchronisation PG entre annulation, fin et effets destructifs ; revoir la purge/quarantaine et le droit d'annuler un job terminal sans perdre les données ; relancer suite générale et poursuivre toutes les portes P0/P1/P2 décrites plus haut. **NON PRÊT**, aucun commit/push/fusion/déploiement.

### Processus réel, nouvelle course et campagne générale

- Le test d'annulation inter-processus utilise désormais un vrai sous-processus Python invoquant `cancel_job` sur PostgreSQL, sans mémoire ni drapeau Redis partagés ; `/tmp/seamtech-cancel-processus.log` **6 passed / 1 xfailed**. La suite générale post-correction `/tmp/seamtech-suite-cancel-interprocess.log` : **1 693 passed / 16 skipped / 40 deselected / 4 xfailed / 4 warnings** ; elle exclut toujours `s3`, `perf`, `recette_corpus`, `integration_docker` et précède la dernière correction ci-dessous.
- Injection d'une annulation PG après résultat et pendant décision de purge : rouge `/tmp/seamtech-cancel-purge-rouge.log`, le worker rapportait `completed` et supprimait la source simulée. Ajout d'un contrôle d'annulation juste après décision et avant purge : le test conserve la source et renvoie `cancelled`. Campagne `/tmp/seamtech-cancel-purge-suite.log` **53 passed / 1 xfailed**, incluant crash worker PG ; Ruff ciblé et `git diff --check` réussis. Cette simple vérification ne ferme pas la fenêtre entre contrôle et suppression ni la course de quarantaine ; ne pas présenter la purge comme sûre sous concurrence.
- **Prochaine action précise** : reproduire la course après ce nouveau contrôle et sécuriser effectivement les effets destructifs / quarantaine, examiner et définir la sémantique d'annulation des jobs terminaux et la sérialisation sous verrou PG ; relancer la suite générale. L'interstice Redis–PG confirmé (xfail strict), les catégories exclues et toutes les portes P0/P1/P2 demeurent ouvertes. **NON PRÊT**, aucun commit/push/fusion/déploiement.

## Nouvelle campagne demandée : retester les suites et les parcours

- ECC relues et appliquées : `e2e-testing`, `verification-loop`, `postgres-patterns`, `redis-patterns`, `security-review` ; cette campagne ne reprend pas comme preuves les chiffres antérieurs fournis dans le message de demande. Environnement éphémère reconstruit : Python `.venv` selon `requirements-dev.txt`, dépendances frontend selon `pnpm-lock.yaml`, PostgreSQL 16.14 UTF-8 avec pgvector compilé localement, Redis 7.4.7 et S3rver. Clients PG16 reconstruits sans zlib/ICU ni `psqlscan` (usage test seulement). Première tentative `npm ci` a échoué faute de package-lock ; `pnpm install --frozen-lockfile` réussit. Chromium 140 obtenu via paquet npm et bibliothèques fournies par ce paquet ; il fonctionne dans ce sandbox, pas une qualification Windows/Chrome/Edge.
- Vérifications autonomes actuelles : `ruff check .` réussi, `pnpm exec tsc --noEmit` réussi, `pnpm build` réussi. **Campagnes en cours, résultats encore inconnus** : suite backend PG/Redis/S3rver `/tmp/seamtech-nouvelle-suite-backend.log`, et les 107 tests Playwright live sur PostgreSQL avec zéro retry `/tmp/seamtech-nouveaux-parcours-navigateur.log`. Le backend Playwright actuel indique Redis absent et traitement en processus web ; le stockage objet réel et OCR n'y sont pas qualifiés. Les parcours sont automatisés au navigateur, pas des gestes physiques humains. Aucun document client n'est utilisé pour ce run ; ne pas enregistrer de valeurs réelles.
- **Prochaine action exacte** : attendre les deux processus, relever résultats, échecs et skips ; corriger les défauts réels sans affaiblir les assertions, relancer puis exécuter séparément les catégories S3/sauvegarde, perf, OCR et cas exclus ainsi que les parcours UI encore manquants. Tester la pile worker/Redis et S3rver puis poursuivre les portes ouvertes ci-dessus. État **NON PRÊT**, pas de commit/push/fusion/déploiement.

### Résultat du premier retest navigateur intégral

- `pnpm test:e2e --retries=0`, PostgreSQL 16.14 isolé et Chromium 140 : **97 passed / 6 failed / 4 skipped**, `/tmp/seamtech-nouveaux-parcours-navigateur.log`. Tests synthétiques, suite entière sur une base partagée, backend en repli mémoire sans Redis ; ce n'est pas un test manuel humain ni une preuve production.
- Trois échecs (concurrence et deux imports) montrent `Indexation refusée : un document de fiche ou objet nécessite une voie de mise à jour protégée` : la garde stricte actuelle refuse notamment la réimportation d'un document déjà lié. Ne pas simplement supprimer la protection contre l'écrasement d'un document lié/objet. Analyser une voie idempotente prouvant l'identité des octets et conservant toutes les références, avec tests de changement et de concurrence.
- Confiance : la population renvoyée par l'évaluation est vide alors que l'attente prévoit cinq références ; analyser contamination des données de tests versus erreur métier. Parcours 3 clics : une autorisation existante fait échouer la précondition 403, probablement état partagé à vérifier ; un autre parcours croisé dépasse le timeout (cause non déterminée). Ne pas masquer ces échecs par retry ou assertion affaiblie. Les 4 skips comprennent notamment les parcours OCR et stockage non configurés ; inventaire exact à relever dans le log.
- Suite backend autonome toujours en cours (résultat inconnu au moment de cette note). **Prochaine action exacte** : attendre son résultat, investiguer puis corriger les six échecs, réexécuter la campagne entière sans retry ; traiter les skips, S3/sauvegarde, perf et tous les parcours manquants. P0/P1/P2 toujours **NON PRÊTS** ; pas de déploiement ou fusion.

### Résultats propres et diagnostics isolés

- Suite backend demandée, nouvelle exécution autonome après dernière correction : `/tmp/seamtech-nouvelle-suite-backend.log` **1 694 passed / 16 skipped / 40 deselected / 4 xfailed / 4 warnings** en 640,52 s. Sélection excluant les catégories S3, perf, corpus et Docker. PG16/Redis7/S3rver isolés ; la suite est locale et l'arbre n'est pas un commit qualifié par CI.
- Diagnostics navigateur sans retry sur base nouvellement semée : confiance **3 passed** `/tmp/seamtech-e2e-confiance-isole.log` ; parcours 3 clics **3 passed** `/tmp/seamtech-e2e-3clics-isole.log`. Ces diagnostics montrent que les scénarios peuvent réussir isolément, mais **n'annulent pas les six échecs** de la suite entière : état partagé/contamination ou concurrence restent à analyser et corriger. Ne pas compter ces exécutions isolées comme une suite globale verte.
- Nouvelle campagne `(s3 or sauvegarde) and not recette_corpus and not integration_docker` en cours, sur S3rver émulateur et clients PostgreSQL compilés : `/tmp/seamtech-nouvelle-suite-stockage.log`, résultat inconnu. **Prochaine action exacte** : attendre ce résultat ; analyser et corriger réimport de document protégé et état partagé des E2E, sans écrasement ni affaiblissement ; exécuter séparément perf/OCR et autres exclus, relancer les suites sur même arbre. **NON PRÊT**.

### Retest sauvegarde/S3 simulé et répétabilité

- Première campagne `(s3 or sauvegarde) and not recette_corpus and not integration_docker` : **30 passed / 1 failed**, car le test S3 réel appelait `AppConfig.load()` sans fichier de configuration (`/tmp/seamtech-nouvelle-suite-stockage.log`). Banc corrigé par configuration d'exemple temporaire et supprimée après test. Deuxième campagne : **30 passed / 1 failed**, le test S3 de clé fixe échouait au second passage à cause de la protection anti-écrasement (`/tmp/seamtech-nouvelle-suite-stockage2.log`).
- Le test S3 utilise désormais une clé de test unique et vérifie toujours la clé exacte, les octets et l'URL, puis supprime l'objet créé. Deux exécutions consécutives du test ciblé réussies. Troisième campagne `/tmp/seamtech-nouvelle-suite-stockage3.log` : **31 passed / 1 723 deselected / 1 warning** avec S3rver, configuration temporaire et PostgreSQL16 ; **ne valide pas MinIO/R2 ni les politiques/versioning réels**. Ruff ciblé et diff check réussis.
- Campagne perf/OCR/gardes en cours `/tmp/seamtech-nouvelle-suite-perf-ocr.log`, résultat inconnu. **Prochaine action exacte** : en relever le résultat et les skips, puis corriger le réimport de fichier protégé sans perte de données et les six échecs navigateur ; poursuivre les parcours manquants et toutes les portes P0/P1/P2. NON PRÊT.

### Gardes/perf/OCR et reproduction ciblée du réimport

- Nouvelle campagne `(perf or ocr_suite or ci_guard_dimension or ci_guard_cotes or ci_guard_dedup or ci_guard_attribution or ci_guard_ocr) and not integration_docker and not recette_corpus` : `/tmp/seamtech-nouvelle-suite-perf-ocr.log` **88 passed / 15 skipped / 1 651 deselected / 1 warning**. Tesseract/pdftoppm sont absents ; les skips ne constituent pas une validation OCR réelle. La mesure locale ne qualifie pas la performance annuelle ni l'endurance.
- Test PG réel `tests/test_reimport_document_protege_pg.py` : après dépôt d'une fiche, réimport strict d'un PDF inchangé échoue alors que le test exige succès et conservation des identifiants, références et métadonnées. Rouge `/tmp/seamtech-reimport-identique-rouge.log` ; marqué **xfail strict temporaire** pour tracer cette régression bloquante, vérification `1 xfailed`. Pas de changement de production sur cette garde : il serait dangereux de la supprimer sans prouver l'égalité des octets et la résistance aux mutations concurrentes. Ruff ciblé et diff check réussis.
- **Prochaine action exacte** : implémenter et tester un rejeu idempotent de document déjà protégé, avec preuve d'octets (attention `content_hash` texte vs octets) et verrous, cas changé/objets/FK/panne/concurrence ; supprimer l'xfail une fois le test vert. Corriger aussi l'isolement entre tests Playwright, puis relancer les 107 sans retry, la suite backend complète et poursuivre toutes les portes. Aucune production déclarée prête.

### Isolation des parcours E2E confiance et décision

- ECC `e2e-testing`, `tdd-workflow`, `verification-loop` : diagnostic du test complet rouge. Le scénario extraction Lot 3b modifiait la fiche de référence utilisée ensuite comme population de confiance et lien source ; une approbation précédente laissait aussi une autorisation active au même utilisateur. Dans la base jetable uniquement, ajout d'une population synthétique dédiée liée à une fiche existante, et d'un utilisateur dédié au parcours d'approbation. Les assertions métier (cinq références, score, lien vers fiche, refus avant approbation, comptage des clics) restent présentes.
- Première exécution groupée sans retry après isolement partiel : **15 passed / 1 failed** `/tmp/seamtech-e2e-isolation-ensemble.log` ; le parcours de lien utilisait toujours la population modifiée par Lot 3b. Après isolation de ce lien aussi : **16 passed** `/tmp/seamtech-e2e-isolation-ensemble2.log` (approbations, extractions, confiance, parcours 3 clics dans cet ordre, PostgreSQL + Chromium, Redis non configuré). Ces jeux de données restent synthétiques ; cela n'élimine pas les défauts de production de réimport ou de fencing.
- Relance des **107** scénarios navigateur entiers sans retry en cours, `/tmp/seamtech-e2e-campagne-isolation.log` ; résultat inconnu. **Prochaine action exacte** : attendre le résultat, corriger tout échec nouveau sans affaiblir les assertions ; corriger le réimport d'un PDF protégé uniquement avec preuve d'identité des octets et protection de référence ; poursuivre P0/P1/P2 et suite générale. NON PRÊT.

### Suite navigateur complète et OCR local natif

- Suite entière 107 scénarios sans retry après isolement Lot 5/Lot 6 : `/tmp/seamtech-e2e-campagne-isolation.log` **98 passed / 5 failed / 4 skipped**. Les trois échecs d'import protégé persistent. Le test de comparaison sélectionnait par fragment et prenait désormais la fiche bis au lieu de la référence attendue ; il sélectionne à présent le code exact, en conservant l'assertion d'écart de 160 cm. Le test clavier de rejet a échoué également ; cause à déterminer. Diagnostic comparaison/validation groupé en cours `/tmp/seamtech-e2e-comparaison-validation.log`, résultat inconnu.
- Pour exercer l'OCR sans utiliser de données client, construction de l'exécutable CLI officiel Tesseract **5.5.3** depuis sa source GitHub, lié aux bibliothèques natives Tesseract/Leptonica du wheel PyPI `tesserocr`, packs fra/eng officiels `tessdata_fast` téléchargés via API GitHub. Banc éphémère, pas installation cible certifiée ; pas de `pdftoppm` ; l'OCR utilise ici des images extraites des PDF. `/tmp/seamtech-e2e-ocr-native.log` **4 passed**, avec `SEAMTECH_OCR_OBLIGATOIRE=1`, zéro retry : sélection fichier et glisser-déposer simulé, OCR, recherche et comparaison SHA, cas invalide et page blanche. C'est un passage local, pas une correction prouvée du flaky CI ni une qualification OCR exhaustive ou Windows.
- **Prochaine action exacte** : recueillir diagnostic validation/comparaison ; campagne OCR répétée sans retry ni assertion relâchée ; corriger en sûreté le réimport protégé et les courses Redis–PG puis relancer suites complètes et toutes portes. NON PRÊT.

### Diagnostics complémentaires et répétition OCR en cours

- Suite groupée Lot 3b → comparaison → validation, sans retry : `/tmp/seamtech-e2e-comparaison-validation.log` **13 passed**. Le sélecteur de comparaison utilise désormais le code exact et teste toujours l'écart de 160 cm. Le test clavier qui avait échoué en suite complète réussit ici ; son instabilité n'est **pas** considérée résolue et doit être reproduite/investiguée en suite complète.
- Campagne de répétition OCR engagée : 15 répétitions de chacun des deux scénarios d'envoi de scan, assertions de recherche et SHA inchangées, zéro retry, `/tmp/seamtech-e2e-ocr-repetition15.log` ; résultat encore inconnu. L'exécutable Tesseract reconstruit est de la même version que les bibliothèques du wheel et la compilation CLI utilise les en-têtes Leptonica 1.87.0 correspondants ; ce banc ne représente pas l'installation de production.
- **Prochaine action exacte** : attendre et analyser chaque résultat OCR, corriger l'éventuel défaut sans réessai salvateur ; poursuivre correction réellement sûre du réimport protégé et des courses critiques puis relancer tous les parcours. NON PRÊT.

### Répétition OCR : échec confirmé, non masqué

- `/tmp/seamtech-e2e-ocr-repetition15.log` : **27 passed / 3 failed** sur 30 scénarios (15 répétitions de chacun des deux parcours, aucun retry), OCR obligatoire avec le CLI officiel reconstruit et moteur 5.5.3. Dans les trois erreurs, Tesseract lit la lettre O à la place du chiffre 0 dans le terme synthétique aléatoire ; assertion du texte OCR exact échoue avant recherche et SHA. Ce résultat confirme le flaky observé en CI : ne pas comptabiliser le passage unique antérieur comme correction ni qualifier l'OCR. Ne pas retirer les caractères ambigus, ni assouplir l'assertion ou les remplacer par des retries.
- Prochaine action : concevoir un traitement robuste et honnête des ambiguïtés OCR/recherche et un jeu déterministe couvrant 0/O (ainsi que les autres caractères confondables), sans prétendre retrouver de manière certaine une information ambiguë ; examiner simultanément les contraintes du parcours exact et le comportement réel attendu. Conserver les échecs dans le bilan, puis relancer la campagne entière sans retry. Les blocages P0/P1/P2 précédents demeurent.

### Annulation d'un job déjà clos — correction ciblée, limites maintenues

- Compétences ECC appliquées : `tdd-workflow`, `postgres-patterns`, `security-review`, `verification-loop`. Tests rouges initiaux `/tmp/seamtech-annulation-terminal-rouge.log` : 6 échecs (états clos écrasés, flag sur job absent, clôture écrasant l'annulation) ; test `/tmp/seamtech-annulation-lot-rouge.log` : le worker de lot ne voyait pas l'annulation SQL sans drapeau local ; `/tmp/seamtech-annulation-api-rouge.log` : 4 statuts HTTP incorrects.
- `cancel_job` ne change atomiquement que `pending`/`running` ; un état clos reste inchangé, une demande inexistante ne crée pas de drapeau local. Les workers import et lot consultent l'état SQL, y compris lorsque l'API est dans un autre processus ; une clôture non annulée ne peut plus écraser `cancelled`, et une clôture annulée peut libérer son claim. Le endpoint répond 409 pour un import terminé ou historique sans job (et audite 409), 404 pour un identifiant absent, et conserve l'idempotence d'un job déjà annulé. Le test HTTP d'annulation active synchronise maintenant explicitement la création et le traitement, au lieu de présumer que l'import n'a pas déjà fini. Deux tests antérieurs attendaient explicitement l'écrasement d'un `completed` par une annulation tardive : attentes corrigées pour conserver l'état clos.
- Régression ciblée PG16/Redis7 `/tmp/seamtech-annulation-terminal-suite2.log` : **130 passed / 1 skipped / 1 xfailed**, dont 40 courses réelles PG annulation contre clôture, tests API, lots et workers. Test supplémentaire import historique/API `/tmp/seamtech-annulation-api-cible.log` : **6 passed**. Ruff complet et `git diff --check` réussis. La suite générale `/tmp/seamtech-suite-annulation-terminale.log` est en cours : **résultat inconnu**.
- **Limites critiques inchangées** : le job classique peut être marqué `completed` avant la purge, donc la réponse 409 ne signifie pas que tous les effets physiques sont achevés ; la fenêtre entre vérification d'annulation/propriété et purge ou quarantaine n'est toujours pas verrouillée. La course Redis–PG reste ouverte, de même que la cohérence ack Redis/clôture PG. Ne pas affirmer la correction globale de l'annulation, ni une garantie d'atomicité distribuée. Le test lot simulait la requête SQL d'un autre processus (pas un lot réel) ; le test import séparé PG existant reste la preuve inter-processus pour cette autre voie.
- **Prochaine action exacte** : recueillir la suite générale et traiter ses échecs ; concevoir un état de finalisation et une protection transactionnelle/fencing couvrant les mutations physiques, sans effacer la preuve ni supprimer la garde des documents référencés ; poursuivre l'OCR et les autres portes. P0/P1/P2 toujours **NON PRÊTS** ; aucun commit/push/fusion/déploiement.

### Suite générale et observation inter-processus

- Premier essai de suite générale `/tmp/seamtech-suite-annulation-terminale.log` : **1 695 passed / 9 failed / 19 skipped / 40 deselected / 5 xfailed**. Les neuf échecs relevés concernent tous la recherche de `pg_dump`, omis par erreur de l'environnement de cette exécution ; ce résultat n'est pas une suite verte. Les 5 xfail et les skips restent des réserves indépendantes.
- La campagne complète a été redémarrée une fois avec `SEAMTECH_PG_BINDIR=/tmp/postgres-REL_16_14/src/bin/pg_dump`, clients 16.14 explicitement vérifiés, journal `/tmp/seamtech-suite-annulation-terminale2.log`, résultat encore inconnu. Elle ne contient pas le test ajouté ensuite ci-dessous (collecte déjà effectuée).
- `/tmp/seamtech-annulation-course-processus.log` : **2 passed** sur PostgreSQL 16 : 40 courses annulation/clôture et, en plus, le callback de lot observe une annulation `cancel_job` lancée depuis un processus séparé sans drapeau mémoire partagé. Le corps de dépôt et l'annulation de lot y sont simulés : ce n'est pas un test bout en bout d'un lot réel ni une preuve contre les fenêtres sur filesystem ou Redis.
- **Prochaine action exacte** : attendre la suite générale configurée correctement, analyser tous les résultats et les skips, et poursuivre les blocages cités plus haut ; ne pas déclarer la production prête. Aucun commit/push/fusion/déploiement.

### Suite générale configurée et tests additionnels

- `/tmp/seamtech-suite-annulation-terminale2.log` : **1 705 passed / 19 skipped / 40 deselected / 5 xfailed / 4 warnings**, PG16 UTF-8, Redis7 et clients PG16 reconstruits, sans S3/perf/corpus/Docker. Aucun échec dans cette sélection ; les skips et xfail demeurent des réserves, ce n'est pas une CI ni une qualification de production. Cette collecte précède les trois tests additionnels ci-dessous.
- Tests additionnels séparés : `/tmp/seamtech-annulation-course-processus.log` **2 passed** (dont 40 courses PG et annulation de lot par processus séparé avec corps métier simulé) ; `/tmp/seamtech-annulation-claim-perdu.log` **9 passed**, dont rollback d'une annulation SQL avant commit sans drapeau résiduel et interdiction pour un worker ayant perdu le claim Redis de clôturer un lot même si l'état est annulé. `ruff check .` et `git diff --check` réussis. Ces tests n'éliminent pas les courses cross-systèmes ni purge/quarantaine indiquées plus haut.
- **Prochaine action exacte** : stabiliser l'OCR sans supprimer l'ambiguïté ni amoindrir les assertions ; concevoir un cycle de finalisation protégeant les effets fichiers et l'ownership distribué ; débloquer en sûreté le réimport identique, puis relancer E2E/CI et autres portes. P0/P1/P2 demeurent **NON PRÊTS**, sans commit/push/fusion/déploiement.

### Investigation OCR : polices et taille ne suffisent pas

- Compétences ECC `search-first`, `tdd-workflow` et `verification-loop` appliquées à une comparaison locale **diagnostique**, sans changer le produit ni modifier le test E2E. `/tmp/seamtech-ocr-font-probe.log` : 50 termes synthétiques déterministes par configuration, incluant les trois échecs observés et des mélanges explicites O/0 ; 5 polices DejaVu, tailles 32/48/64, Tesseract 5.5.3 et packs fra+eng, `--psm 6`. Chaque configuration présente des erreurs de lecture ou de segmentation (7 à 15 erreurs par 50 lorsque les 50 lignes sont détectées ; une configuration produit 52 lignes). Les trois chaînes problématiques restent généralement erronées. Le simple changement de police/taille ne démontre donc aucune correction ; il n'a pas été appliqué et ne doit pas servir à déclarer le flaky CI résolu. Cette expérience ne qualifie pas non plus toutes les versions et langues de Tesseract.
- **Prochaine action exacte** : élaborer une stratégie d'indexation et de recherche des ambiguïtés explicitement signalées, sans falsifier le texte source reconnu, avec jeux déterministes et contrôles faux positifs, puis définir séparément un parcours E2E exact réaliste qui conserve des vérifications sur des cas O/0 ; poursuivre parallèlement les bloquants d'atomicité et de réimport protégés. P0/P1/P2 restent NON PRÊTS.

### Sauvegardes : suppression d'affirmations trompeuses et garde de rétention

- ECC appliquées : `search-first`, `tdd-workflow`, `security-review`, `verification-loop`. Constat : le manifeste affirmait que les références provenaient de l'instantané `pg_dump`, alors qu'elles sont lues dans une autre transaction ; `complet` ne couvrait que les copies demandées, tout en pouvant déclencher une purge automatique. `/health` ne lisait pas la clé réellement produite `date_iso`, pouvant entraîner un dump à chaque passage du planificateur.
- TDD rouges : `/tmp/seamtech-sauvegarde-coherence-rouge.log`, `/tmp/seamtech-sauvegarde-retention-globale-rouge.log`, `/tmp/seamtech-sauvegarde-cli-rouge.log`, `/tmp/seamtech-sauvegarde-health-rouge.log`, `/tmp/seamtech-sauvegarde-planificateur-rouge.log`. Le manifeste local et distant explicite désormais snapshots indépendants, inclusion effective des références, `protection_globale_prouvee=false`, `coherence_references_snapshot=false` et rétention automatique interdite. `complet` reste compatible mais **ne signifie que le périmètre demandé**. Le CLI publie les indicateurs, renvoie **2** et avertit que la protection globale n'est pas qualifiée, même si une copie partielle a bien été créée. Le planificateur ne journalise plus un succès global pour un manifeste non qualifié ; `/health` affiche `non_qualifiee` et reconnaît `date_iso`. Le runbook avertit du statut réel.
- **Conséquence opérationnelle importante : rétention automatique suspendue**, aucune ancienne sauvegarde n'est purgée tant que la protection globale n'est pas prouvée ; risque de saturation disque, à surveiller. L'outil de rétention isolé reste présent, mais l'exécution automatique ne constitue plus une qualification. Ce sont des gardes fail-closed, **pas** la réalisation du P0 sauvegarde complète : absence de copie intégrale `data`, snapshot PG/fichiers/objets coordonné, réconciliation des chemins et qualification hors site et rétention.
- `/tmp/seamtech-sauvegarde-coherence-suite2.log` : **87 passed / 2 skipped**, PG16 et clients natifs compilés, restauration ciblée incluse (précède le changement de supervision). `/tmp/seamtech-sauvegarde-supervision-suite2.log` : **57 passed**, dont supervision, planificateur, manifeste distant simulé, CLI et rétention isolée. `ruff check .` et `git diff --check` réussis. Ces derniers changements n'ont pas encore été soumis à une suite backend entière ni à la CI, et ne qualifient pas S3 réel/Windows.
- **Prochaine action exacte** : construire et tester une sauvegarde réellement cohérente et complète avant toute réactivation de la purge et du succès CLI, puis reprendre les autres blocages (réimport protégé, atomicité Redis–PG et fichiers, OCR, rotations, P1/P2) et relancer intégralement backend/E2E/CI sur le commit exact. NON PRÊT ; aucun commit/push/fusion/déploiement.

### Snapshot PostgreSQL partagé pour le dump et les références

- TDD PostgreSQL réel `/tmp/seamtech-sauvegarde-snapshot-rouge.log` : après insertion concurrente d'un document juste après `pg_dump`, le dump restauré contient **1** document mais le manifeste et le bundle en annoncent **2**. Le test était brièvement `xfail strict` ; il a été retiré après correction, sans affaiblir les assertions.
- `sauver` ouvre maintenant une transaction PostgreSQL `REPEATABLE READ` en lecture seule, exporte son snapshot, passe `--snapshot` à `pg_dump` et lit les comptes et les références via la même connexion avant de fermer la transaction. Le manifeste indique le partage du snapshot SQL lorsqu'il copie les références. **Attention : seuls les enregistrements SQL sont coordonnés** : fichiers, objets S3 et inventaire de l'archive sont toujours lus ultérieurement sans gel/versionnement prouvé ; `protection_globale_prouvee=false`, code CLI 2 et suspension de la rétention automatique restent obligatoires.
- `/tmp/seamtech-sauvegarde-snapshot-course.log` : **3 passed** sur PostgreSQL 16 avec un vrai dump puis une vraie restauration : écriture concurrente après export **avant** le dump et après le dump, vérification que le bundle concorde avec la base restaurée ; échec injecté de `pg_dump` vérifie fermeture de la connexion exportatrice et absence de manifeste publié. `/tmp/seamtech-sauvegarde-snapshot-suite.log` : **93 passed / 2 skipped**, restauration ciblée/PG, mocks distants et supervision ; cette collecte précède le test additionnel d'échec de dump exécuté séparément. Ruff et diff check réussis. Les clients PG16 restent compilés pour ce sandbox, et MinIO/R2/Windows ne sont pas qualifiés.
- Suite générale backend relancée sur l'arbre après correction : `/tmp/seamtech-suite-backend-snapshot.log`, **résultat inconnu**. **Prochaine action exacte** : attendre et analyser la suite ; concevoir des instantanés immuables/cohérents des octets locaux et S3 et la restauration complète de `data`, puis traiter réimport protégé, Redis–PG/fichiers, OCR et autres portes. Ne pas réactiver la purge sur cette seule correction SQL. P0/P1/P2 **NON PRÊTS** ; aucun commit/push/fusion/déploiement.

### Course des octets confirmée après le snapshot SQL

- `/tmp/seamtech-sauvegarde-octets-xfail.log` : **3 passed / 1 xfailed strict**. Le nouveau test synthétique PostgreSQL modifie après le dump un fichier référencé, en conservant la même taille ; le bundle contient la nouvelle version tandis que le manifeste annonce encore `complet` pour le périmètre. Le test exige soit les octets de l'instantané, soit un refus/incomplétude : cette garantie échoue actuellement. `protection_globale_prouvee` reste faux, CLI code 2, rétention suspendue ; il ne faut surtout pas assimiler `content_hash` (hash texte parfois) à une empreinte des octets.
- Prochaine action : mettre en place un véritable protocole d'instantanés immuables et de coordination des références/fichiers/objets, avec protections contre les mutations transitoires et restauration réelle, puis retirer le xfail après preuve. La suite `/tmp/seamtech-suite-backend-snapshot.log` est toujours en cours, collecte antérieure à ce nouveau test ; résultat inconnu. NON PRÊT.

### Régression générale après snapshot SQL

- `/tmp/seamtech-suite-backend-snapshot.log` : **1 717 passed / 19 skipped / 40 deselected / 5 xfailed / 4 warnings**, PG16/Redis7 et clients PG16 compilés, hors S3/perf/corpus/Docker. Elle a été collectée avant l'ajout du test strict xfail de mutation d'octets décrit ci-dessus ; son résultat séparé est `/tmp/seamtech-sauvegarde-octets-xfail.log` **3 passed / 1 xfailed**. Il ne s'agit pas d'une CI ni d'une preuve globale de production.
- Prochaine action exacte : corriger réellement la cohérence entre les octets fichiers/objets et le snapshot PG, y compris mutation transitoire et restauration complète `data`, puis supprimer le xfail ; continuer ensuite réimport protégé, fencing Redis–PG, OCR, rotations et toutes les portes. La rétention reste suspendue et le CLI sauvegarde retourne 2. P0/P1/P2 **NON PRÊTS**, aucun commit/push/fusion/déploiement.

### Statut de sauvegarde et rétention héritée fail-closed

- ECC `tdd-workflow`, `security-review`, `verification-loop` : `/tmp/seamtech-sauvegarde-complet-rouge.log` a confirmé qu'une sauvegarde non qualifiée publiait encore l'ancien champ `complet=true`, susceptible de tromper un consommateur historique. `complet` est désormais **toujours faux tant que la protection globale n'est pas prouvée** ; `complet_perimetre_demande` exprime séparément si toutes les copies demandées ont réussi, et devient faux en cas de manquants. Le manifeste distant, le manifeste local et le CLI publient les deux champs. Le CLI reste code 2 ; la rétention automatique reste désactivée. Les tests de restauration vérifient toujours leurs copies, leur SHA et les résultats réels, en plus des nouveaux indicateurs.
- `appliquer_retention` exige désormais des booléens cohérents `complet` et `protection_globale_prouvee` dans **tous** les manifestes du plan local/distant ; un ancien manifeste sans qualification, un type invalide ou une contradiction interdit toute suppression. `/tmp/seamtech-retention-heritage-rouge.log` et `/tmp/seamtech-retention-contradiction-rouge.log` démontrent les suppressions précédemment possibles. Après correction `/tmp/seamtech-retention-contradiction-vert.log` : **37 passed**, tests de purge simulée avec manifestes explicitement qualifiés et scénarios fail-closed. Cela n'établit pas une rétention de production ni la véracité d'une qualification falsifiée.
- `/tmp/seamtech-sauvegarde-complet-suite2.log` : **95 passed / 2 skipped / 1 xfailed**, PG16 + sauvegarde ciblée, résultat précédant le durcissement additionnel de `appliquer_retention` ; les tests unitaires ciblés ont été relancés après ce durcissement. Le xfail exige désormais les octets d'avant la mutation, et constate séparément que `complet=false` : le garde ne résout pas la cohérence des octets.
- **Prochaine action exacte** : relancer la suite sauvegarde complète sur l'arbre actuel, puis résoudre les instantanés immuables fichiers/S3 et la restauration complète avant de réactiver une rétention. Garder les autres bloquants Redis–PG, réimport, OCR, rotations et P1/P2 ouverts. P0/P1/P2 **NON PRÊTS** ; aucun commit/push/fusion/déploiement.

### Vérification des gardes et diagnostic OCR complémentaire

- `/tmp/seamtech-sauvegarde-retention-suite3.log` : **102 passed / 2 skipped / 1 xfailed**, sur l'arbre incluant les nouvelles règles de rétention, le statut `complet=false`, les contradictions de qualification et les horodatages anormalement futurs. Les tests supervisent toujours une copie partielle ; le xfail concerne les octets locaux non figés. En plus des contrôles de rétention, `/health` et le planificateur refusent un manifeste contradictoire (`complet=false`, qualification globale vraie) ; une date future de plus de cinq minutes ne masque pas une sauvegarde due. Les résultats ne qualifient ni S3 réel ni une restauration globale.
- Diagnostic OCR `/tmp/seamtech-ocr-config-probe.log` : 50 termes synthétiques déterministes (dont ambiguïtés O/0), Tesseract 5.5.3, modes fra+eng, eng seul et fra seul, avec et sans dictionnaires Tesseract pour les deux premiers. **39 à 43 lectures exactes sur 50**, aucune configuration ne résout les trois chaînes précédemment échouées. Ce test direct `--psm 6` est un diagnostic, pas le pipeline produit ; aucune configuration n'a été changée et aucune correction OCR n'est revendiquée.
- Prochaine action exacte : réaliser une sauvegarde complète et coordonnée des octets locaux et objets ; puis réimport protégé, fencing Redis–PG, OCR, rotations et reste des portes, avec preuves et CI exactes. P0/P1/P2 **NON PRÊTS** ; aucune fusion ou déploiement.

### Identifiants PostgreSQL dans les commandes de sauvegarde

- ECC `security-review`, `tdd-workflow`, `verification-loop` : tests rouges `/tmp/seamtech-sauvegarde-secret-rouge.log`, `/tmp/seamtech-sauvegarde-password-args-rouge.log` et `/tmp/seamtech-sauvegarde-password-logs-rouge.log` ont révélé respectivement une URI complète dans une erreur de nom de base, le mot de passe dans les arguments de `pg_dump` et un `stderr` de commande propagé sans garantie de confidentialité. Les tests n'utilisent que des identifiants synthétiques.
- `pg_dump` et `pg_restore` reçoivent maintenant un DSN analysé par libpq sans mot de passe dans leurs arguments ; le mot de passe éventuel est transmis à leur environnement `PGPASSWORD`. Les erreurs ne recopient plus `stderr`, qui pourrait contenir des secrets ; une URI sans base ne ressort plus dans l'erreur. **Limite :** l'environnement d'un processus reste accessible selon les permissions du système à des processus du même compte ; ceci n'est ni un coffre-fort ni une isolation des comptes. Le code retour reste disponible, mais le diagnostic détaillé de `stderr` n'est plus exposé.
- `/tmp/seamtech-sauvegarde-securite-suite.log` : **107 passed / 2 skipped / 1 xfailed**. Qualification supplémentaire sans données réelles sur PostgreSQL 16 SCRAM-SHA-256 local dédié : `/tmp/seamtech-sauvegarde-auth-scram.log` **9 passed / 1 skipped / 1 xfailed** ; deuxième passage avec mot de passe synthétique contenant espaces, apostrophe, arobase et antislash : `/tmp/seamtech-sauvegarde-auth-special.log` **4 passed / 1 xfailed**. Dump et restauration réels réussis ; le serveur de test dédié a été arrêté et son fichier temporaire de mot de passe supprimé. Le xfail reste la mutation des octets locaux. Ruff/diff check réussis. Pas une qualification des serveurs ni identifiants réels.
- Suite générale backend relancée sur l'arbre courant : `/tmp/seamtech-suite-sauvegarde-securite.log`, **résultat inconnu**. Prochaine action : attendre la suite ; poursuivre la cohérence des octets/data, le réimport protégé, le fencing Redis–PG, l'OCR, les rotations et toutes les autres portes. La rétention reste suspendue, le CLI sauvegarde code 2. P0/P1/P2 **NON PRÊTS** ; aucun commit/push/fusion/déploiement.

### Erreurs libpq et arguments CLI

- `/tmp/seamtech-sauvegarde-uri-rouge.log` : une URI PostgreSQL malformée provoquait une exception libpq dont la trace répétait le mot de passe synthétique. `_connecter` remplace désormais les erreurs libpq par un message sans URI et sans contexte chaîné affiché ; `/tmp/seamtech-sauvegarde-uri-vert.log` **50 passed**. Cette garde masque aussi le diagnostic détaillé de connexion et ne prouve pas l'absence de fuite dans toutes les autres couches.
- Les commandes `sauver` et `verifier` peuvent utiliser `SEAMTECH_DATABASE_URL`, et `restaurer` utilise `SEAMTECH_RESTORE_DATABASE_URL`, sans URL en argument ; l'ancienne option explicite reste compatible mais reste visible dans les arguments du processus appelant. Documentation mise à jour, ne jamais afficher les valeurs. L'environnement reste accessible selon les permissions locales ; il faut un gestionnaire de secrets et une isolation OS adaptés.
- `/tmp/seamtech-sauvegarde-url-env-rouge.log` a montré l'absence du mode environnement ; après correction, `/tmp/seamtech-sauvegarde-url-env-vert.log` **52 passed**, et `/tmp/seamtech-sauvegarde-url-env-pg.log` **57 passed / 1 xfailed**, dont véritables aller-retours PG16 avec URLs en arguments et en variables d'environnement. Le xfail porte sur la mutation des octets.
- `/tmp/seamtech-suite-sauvegarde-securite.log` reste en cours ; elle a été démarrée avant la dernière modification de `_connecter` et du CLI, et ne pourra donc pas à elle seule qualifier l'arbre actuel. Prochaine action : recueillir cette suite, refaire les vérifications nécessaires sur l'état exact, puis poursuivre les blocages P0/P1/P2. Rétention suspendue, CLI sauvegarde code 2, NON PRÊT ; aucun commit/push/fusion/déploiement.

- Suite précédente recueillie : `/tmp/seamtech-suite-sauvegarde-securite.log` **1 730 passed / 19 skipped / 40 deselected / 6 xfailed / 4 warnings** en 561 s, démarrée avant les derniers changements mentionnés ci-dessus. Nouvelle suite backend relancée après ces changements : `/tmp/seamtech-suite-sauvegarde-env-finale.log`, processus `suite-backend-sur-les-derni-res--c7885604`, résultat encore inconnu. Recueillir son résultat avant de le revendiquer ; les exclusions, skips et xfails ne sont pas des succès de qualification.

- `/tmp/seamtech-suite-sauvegarde-env-finale.log` : **1 734 passed / 19 skipped / 40 deselected / 6 xfailed / 4 warnings**, PG16/Redis7, 566 s ; cette sélection exclut S3, perf, corpus et Docker et ne constitue pas la CI. Un test unitaire supplémentaire de la commande `verifier` avec URL en environnement a été ajouté après le lancement et exécuté séparément : **1 passed** ; pas de modification de code produit pendant cette suite. `ruff check .` et `git diff --check` réussis. Prochaine action précise : concevoir puis tester une voie de réimport inchangé protégée sans comparer le hash de texte au hash des octets et sans fenêtre de mutation ; maintenir la garde stricte jusqu'à preuve de non-écrasement et relancer les parcours navigateur concernés. Ensuite traiter l'atomicité Redis–PG, la sauvegarde/restauration coordonnée et les autres portes restant NON PRÊTES.

### Supervision et planification : refus des états invalides

- ECC `security-review`, `tdd-workflow`, `error-handling`, `verification-loop` : `/tmp/seamtech-planificateur-type-rouge.log` montrait cinq faux succès si la fonction de sauvegarde retournait autre chose qu'un manifeste ; désormais seul un dictionnaire comportant les deux booléens explicitement vrais est considéré qualifié. Le test historique utilise désormais un manifeste qualifié **simulé**, sans prétendre que la sauvegarde réelle est qualifiée.
- `/tmp/seamtech-health-json-rouge.log` montrait sept erreurs `AttributeError` au lieu d'un état lisible pour des manifestes JSON valides mais mal structurés, et `/tmp/seamtech-health-utf8-rouge.log` une exception sur des octets invalides. La supervision signale maintenant `illisible` et le planificateur considère une sauvegarde due ; les documents manquants restent non qualifiés, pas une preuve de restauration.
- `/tmp/seamtech-planificateur-secret-rouge.log` montrait qu'une exception arbitraire pouvait mettre un secret synthétique dans la trace du planificateur : le journal conserve seulement le type de l'erreur, sans message ni trace. Limite : perte de détails de diagnostic, et pas d'audit exhaustif de toutes les autres sorties ou couches.
- `/tmp/seamtech-planificateur-duree-rouge2.log` et `/tmp/seamtech-planificateur-nan-rouge.log` : des intervalles non numériques, non finis ou non positifs pouvaient tuer la boucle ou masquer une sauvegarde. La CLI planificateur rejette ces paramètres avec code 2 avant la boucle ; un appel direct avec intervalle invalide considère la sauvegarde due. Vérification de limite de durée compatible `time.sleep` incluse.
- `/tmp/seamtech-sauvegarde-supervision-robuste.log` **128 passed / 2 skipped / 1 xfailed** avec PG16, avant la validation d'intervalles ; `/tmp/seamtech-planificateur-duree-vert.log` **60 passed** sur la supervision/planificateur après toutes ces modifications ; `ruff check .` et `git diff --check` réussis. La suite générale précédente **1 734 passed / 19 skipped / 40 deselected / 6 xfailed** précède ces modifications. Rétention suspendue, sauvegarde globale non prouvée : P0/P1/P2 restent **NON PRÊTS**.
- Prochaine action : reprendre la conception du réimport inchangé protégé, en lisant seulement des instantanés immuables et en préservant les références, puis démontrer en tests les mutations concurrentes ; ne pas confondre SHA du texte et SHA des octets, et ne pas affaiblir la garde stricte. Les défauts de cohérence octets sauvegardés, Redis–PG, OCR, rotations et qualifications de plateformes restent ouverts. Aucun commit/push/fusion/déploiement.

### Confinement de l'import : chemins Excel et liens

- ECC `search-first`, `security-review`, `tdd-workflow` : pendant l'analyse du réimport, `/tmp/seamtech-import-excel-externe-rouge.log` a montré qu'une sélection Excel absolue, relative traversante ou liée hors du dossier était lue sans confinement ; `/tmp/seamtech-import-symlink-externe-rouge.log` a montré la lecture possible de fichiers extérieurs via liens dans l'arbre. Les échantillons sont synthétiques.
- Désormais la sélection Excel doit résoudre à un fichier présent dans le dossier scanné ; l'inventaire refuse tous les liens symboliques, y compris internes, de répertoire et cassés, avant toute extraction. Ce refus explicite change la prise en charge des dossiers avec liens : ils ne sont pas acceptés silencieusement. `/tmp/seamtech-import-confinement-vert.log` **12 passed**, `/tmp/seamtech-import-supervision-suite.log` **103 passed** ; Ruff et diff check réussis.
- **Limite de sécurité confirmée, non corrigée :** `/tmp/seamtech-import-toctou-xfail.log` **1 xfailed strict** démontre qu'un fichier peut être remplacé par un lien externe après inventaire et avant extraction. Le contrôle de chemin seul n'est pas un confinement contre une substitution concurrente. Ne pas déclarer P2 prêt ni supprimer le xfail sans extraction à partir de descripteurs/snapshots confinés, tests de course et compatibilité Windows. Les mêmes enjeux de mutation empêchent encore d'autoriser le réimport protégé.
- Prochaine action : concevoir l'ouverture et la copie confinées, étudier la propagation des chemins/artefacts au travers d'extraction et upload, puis tester les substitutions à chaque frontière ; maintenir la garde stricte du réimport jusqu'à preuve des octets. Les autres blocages P0/P1/P2 restent ouverts. Aucun commit/push/fusion/déploiement.

- Contrôle API synchrone supplémentaire `/tmp/seamtech-import-confinement-api.log` : **2 passed**, refus HTTP 400 d'un Excel externe et d'un PDF lié vers l'extérieur sans lecture du fichier externe. Ne couvre ni worker durable, ni substitution concurrente, ni la matrice complète.
- Suite backend sélectionnée relancée après les corrections de supervision et de confinement : `/tmp/seamtech-suite-import-confinement.log`, processus `suite-backend-apr-s-confinement--cc66391f`, résultat à recueillir. Elle exclut S3, perf, corpus et Docker ; le nouveau xfail de substitution ne vaut pas une protection contre les courses. Prochaine action inchangée : conception d'une lecture confinée par descripteurs et snapshots pour le scan/l'import, incluant Windows, puis rejouer les attaques et parcours complets sans affaiblir les assertions.

### Repli de verrou interprocessus des sessions sans `fcntl`

- ECC `search-first`, `tdd-workflow`, `security-review`, `verification-loop` : l'audit de la qualification Windows a trouvé un repli `fcntl is None` qui ne verrouillait rien ; `/tmp/seamtech-televersement-windows-verrou-rouge.log` le reproduit avec deux threads en section critique simultanément.
- Le repli utilise désormais une transaction SQLite `BEGIN IMMEDIATE` dans un fichier de verrou par session, avec refus explicite si la prise du verrou échoue ; fermeture et libération sur exception ou arrêt du processus. `/tmp/seamtech-televersement-windows-verrou-vert.log` **3 passed** : deux threads, deux processus séparés, libération après arrêt forcé ; `/tmp/seamtech-televersement-windows-envoi.log` **1 passed** : deux processus envoient 20 fichiers et les octets/manifeste finalisés sont conservés. Tests exécutés sous Linux en forçant le repli, pas sur Windows réel, Docker Desktop, partage réseau ou disque défaillant : ces plateformes restent non qualifiées. Le verrou `fcntl` existant reste utilisé sous Linux.
- `/tmp/seamtech-televersement-confinement-suite.log` est en cours, processus `tests-de-t-l-versement-et-confin-cd0b1314`. La suite générale `/tmp/seamtech-suite-import-confinement.log` a commencé avant ce changement de verrou et ne couvre donc pas l'état exact. Recueillir les résultats. Suite et prochaines actions : vérifier le repli sur Windows natif dans un environnement isolé, traiter les courses de confinement et la sauvegarde cohérente, puis les autres portes P0/P1/P2. Aucun commit/push/fusion/déploiement ; NON PRÊT.

- `/tmp/seamtech-televersement-confinement-suite.log` **107 passed / 1 xfailed** avant l'unification des verrous. `/tmp/seamtech-suite-import-confinement.log` **1 782 passed / 19 skipped / 40 deselected / 7 xfailed / 4 warnings**, sélection PG16/Redis7 démarrée avant les modifications finales du verrou, hors S3/perf/corpus/Docker.
- Un nouveau test rouge `/tmp/seamtech-televersement-verrou-mixte-rouge.log` a montré que des processus utilisant respectivement `fcntl` et le repli SQLite ne se voyaient pas. Tous les processus prennent maintenant d'abord le verrou SQLite ; ceux ayant `fcntl` prennent aussi l'ancien verrou. `/tmp/seamtech-televersement-verrou-mixte-vert2.log` **6 passed** (deux modes, threads/processus, crash, envois concurrents, erreur de verrou). Un premier lancement de ce groupe a bloqué dans le nettoyage de l'`Event` après SIGKILL du processus de test ; processus arrêté, test corrigé pour ne plus toucher au sémaphore du processus tué. Ce problème était dans le harnais de test, pas une preuve de sûreté générale.
- `/tmp/seamtech-televersement-verrou-mixte-suite.log` **138 passed / 1 xfailed / 2 warnings**, dernier xfail : substitution concurrente de lien dans le scan. Ruff/diff check réussis. La prise du verrou SQLite expire après 30 s avec conflit explicite ; les très gros envois concurrents et la politique de reprise sous cette limite restent à qualifier. SQLite dépend des garanties de verrouillage du système de fichiers : aucun résultat ne démontre ici la coopération Windows/Linux sur un volume partagé, ni une mise à jour progressive de processus de versions différentes. Windows natif reste non qualifié.
- Prochaines actions : qualifier la concurrence et le débit avec les nouveaux verrous, concevoir des lectures confinées et des snapshots immuables (sans en déduire la cohérence globale), résoudre le réimport protégé et les autres blocages P0/P1/P2. Retester entièrement le backend sur l'arbre final avant toute conclusion ; CI exacte absente, NON PRÊT. Aucun commit/push/fusion/déploiement.

### Dossier navigateur : refus des lectures partielles

- ECC `search-first`, `tdd-workflow`, `frontend-patterns`, `react-patterns`, `e2e-testing`, `verification-loop` : `/tmp/seamtech-drop-incomplet-rouge.log` a montré qu'un dépôt comprenant une entrée accessible et une entrée inaccessible était présenté comme une sélection complète ; `/tmp/seamtech-drop-inconnu-rouge.log` a montré qu'une entrée inconnue dans un sous-dossier était ignorée silencieusement. Les fichiers sont synthétiques.
- Le navigateur refuse désormais toute entrée manquante ou de type inconnu ; sans accès aux entrées ni chemins relatifs, il refuse une liste potentiellement aplatie et invite à choisir le dossier avec le sélecteur. Une nouvelle lecture efface immédiatement l'ancienne sélection, même en cas d'échec, afin d'éviter l'envoi d'un ancien dossier. Cette mesure change le comportement des navigateurs ne fournissant pas d'entrées de dépôt : le bouton de sélection reste disponible ; les navigateurs réels Chrome/Edge et OS cibles doivent encore être qualifiés.
- `/tmp/seamtech-drop-double-rouge.log` a montré que deux événements de dépôt dans le même tour React pouvaient déclencher deux lectures ; une ref verrouille maintenant la lecture avant le rerendu. `/tmp/seamtech-drop-incomplet-vert.log` **2 passed** ; `/tmp/seamtech-drop-suite-navigateur.log` **7 passed / 1 skipped**, avant le dernier test et le verrou ref, Chromium local, zéro retry. `/tmp/seamtech-drop-suite-navigateur-final.log` en cours, processus `r-gression-navigateur-du-d-p-t-c-0b371132`. Les objets `DataTransfer` de ces tests d'échec sont simulés dans un vrai navigateur : ils ne prouvent pas un glisser-déposer OS réel, ni Chrome/Edge/Windows, ni tout le pipeline S3/PG/Redis.
- Mesure locale `/tmp/seamtech-mesure-verrou-session.log` : 10 000 fichiers synthétiques d'un octet, 50 envois, un passage par mode. Ancien verrou : réception **7,835 s**, finalisation **0,338 s**, RSS max **45 192 Kio** ; nouveau verrou : réception **7,371 s**, finalisation **0,351 s**, RSS max cumulé **47 152 Kio**. Pas de comparaison statistique ni de qualification sur volume réel, grosses pièces, concurrence lourde, réseau ou Windows ; la limite d'attente de 30 s reste non qualifiée.
- P0/P1/P2 restent **NON PRÊTS**. Prochaine action : recueillir la suite navigateur, poursuivre les snapshots/lectures confinées et réimport protégé, puis retester tous les parcours et corriger les blocages identifiés. Aucun commit/push/fusion/déploiement.

- Résultat navigateur final `/tmp/seamtech-drop-suite-navigateur-final.log` : **8 passed / 1 skipped**, Chromium local, zéro retry, SQLite/serveur web local ; `tsc --noEmit` et `pnpm build` réussis. Le test optionnel de stockage simulé a ensuite été exécuté séparément avec PostgreSQL 16, Redis 7, worker dans le processus web, S3rver dédié et Chromium : `/tmp/seamtech-drop-e2e-pg-s3-2.log` **1 passed**, promotion et lecture du PDF/annexe après déplacement des sources synthétiques. Le premier lancement `/tmp/seamtech-drop-e2e-pg-s3.log` avait refusé au démarrage le secret de démonstration trop court de l'émulateur ; résolution par un compte synthétique long dans un second émulateur, sans désactiver le contrôle de sécurité. Le fichier de configuration temporaire a été supprimé et le second émulateur arrêté. Ce résultat ne qualifie ni S3 MinIO/R2 réel, ni worker séparé, ni glisser-déposer natif du système, ni Windows/Edge.
- Prochaine action : concevoir et tester l'ouverture confinée et les snapshots d'import, poursuivre tous les blocages identifiés, et réexécuter les suites sur l'arbre final ; P0/P1/P2 restent NON PRÊTS et la CI exacte n'est pas disponible. Aucun commit/push/fusion/déploiement.

### Reprise du téléversement dans la même page

- ECC `search-first`, `tdd-workflow`, `frontend-patterns`, `e2e-testing`, `error-handling` : `/tmp/seamtech-reprise-session-rouge2.log` a reproduit la création d'une seconde session après panne d'un envoi, et `/tmp/seamtech-reprise-finalisation-rouge.log` la tentative de créer un lot sans avoir refinalisé après une erreur de finalisation. `/tmp/seamtech-annulation-session-reprise-rouge.log` a montré qu'un échec de suppression pendant l'annulation faisait perdre au navigateur le jeton permettant de réessayer.
- Tant que la page et la sélection de fichiers restent en mémoire, le bouton d'envoi réutilise désormais le jeton, relit l'état de la session et refuse les paramètres contradictoires ; une session ouverte rejoue tous les envois, vérifiés idempotemment par le backend, puis finalise ; une session déjà prête passe directement à la finalisation idempotente et à la création de lot. Le bouton de reprise de création suit maintenant le même parcours. Si la suppression d'une session connue échoue, la sélection et le jeton restent disponibles pour réessayer ; aucune suppression réussie n'est annoncée sans réponse positive.
- `/tmp/seamtech-reprises-ciblees-final.log` **4 passed**, Chromium local zéro retry : reprise après envoi interrompu, finalisation échouée, réponse d'envoi perdue après réception par le vrai backend et échec d'annulation. Le test backend réel utilise SQLite et intercepte la création du lot ; il vérifie les octets des deux fichiers. Un premier essai du harnais utilisait `route.fetch()` sur un multipart de fichier et transmettait un fichier vide ; corrigé en envoyant explicitement les octets synthétiques au backend, `/tmp/seamtech-reprise-backend-reel2.log` **1 passed**. Ce n'était pas une validation du flux navigateur original pour ce paquet intercepté.
- Limites importantes : le rejeu d'une session ouverte renvoie **tous les fichiers**, donc peut être très coûteux sur une archive annuelle ; ce n'est pas une reprise optimisée par inventaire/empreinte. Aucun mécanisme de reprise après rechargement du navigateur n'a été ajouté. La réponse perdue pendant **création** de session reste ambiguë : le navigateur peut ne jamais recevoir le jeton alors que le serveur a créé la session ; il n'existe pas encore de clé d'idempotence de création, ni de nettoyage démontré. Une annulation sans jeton ne revendique plus la suppression, mais le comportement ultérieur reste à durcir. Le nouveau test de finalisation déjà effectuée est ajouté et reste à exécuter. P0/P1/P2 **NON PRÊTS**, CI exacte non qualifiée, aucun commit/push/fusion/déploiement.
- Prochaine action : tester finalisation/réponse perdue et annulation sans jeton, concevoir une clé d'idempotence persistante et sécurisée pour la création, puis supprimer le rejeu intégral en comparant un inventaire serveur fiable avec les fichiers sélectionnés sans affaiblir les preuves d'intégrité ; poursuivre snapshots/réimport, Redis–PG, OCR, rotations et autres portes.

#### Complément de qualification de reprise

- `/tmp/seamtech-creation-incertaine-rouge.log` a reproduit le message erroné « aucun fichier » après annulation alors que la réponse de création de session n'avait pas été reçue. En mode fail-closed, la page conserve maintenant l'incertitude : elle ne promet pas la suppression et n'ouvre pas silencieusement une nouvelle session pour la même sélection. Cela ne retrouve pas la session orpheline ; une nouvelle sélection ou un rafraîchissement perd cet état mémoire. Une vraie idempotence de création et un nettoyage sûr restent nécessaires.
- `/tmp/seamtech-reprise-navigateur-complet.log` : **14 passed / 1 skipped**, Chromium, zéro retry, backend SQLite ; inclut finalisation déjà effectuée avec réponse perdue et annulation de création incertaine. `/tmp/seamtech-reprise-navigateur-pg-s3.log` : **15 passed**, Chromium, zéro retry, PostgreSQL 16, Redis 7, worker dans processus web, S3rver dédié ; le parcours de bout en bout lit PDF et annexe après déplacement de la source, les autres scénarios utilisent en majorité des réponses simulées. S3rver arrêté et configuration provisoire supprimée. Aucun MinIO/R2 réel, worker séparé, Windows/Edge ou dépôt OS natif n'est ainsi qualifié.
- `/tmp/seamtech-reprise-session-incoherente.log` : **1 passed**, refus des métadonnées contradictoires sans nouvelle session ni nouvel envoi ; test ajouté après le groupe de 15. `tsc --noEmit`, `/tmp/seamtech-reprise-build.log` (build production), Ruff et `git diff --check` : réussis.
- Prochaine action précise : concevoir et tester création idempotente persistante, sécurisée, concurrente et résiliente aux crashs/annulations, avant de prétendre résoudre une réponse de création perdue ; s'assurer également qu'une nouvelle sélection ne fasse pas oublier silencieusement une session existante. Ensuite traiter le coût du rejeu intégral avec preuve d'intégrité, et les autres portes P0/P1/P2 déjà listées. Toujours **NON PRÊT**, CI exacte non exécutée, aucune fusion ni déploiement.

### Création idempotente de session — implémentation en qualification

- ECC `search-first`, `tdd-workflow`, `frontend-patterns`, `e2e-testing`, `error-handling`, `security-review` : `/tmp/seamtech-selection-session-rouge.log` a montré qu'une nouvelle sélection effaçait le jeton d'une session inachevée. Sélection et glisser-déposer sont maintenant refusés tant que la session connue doit être reprise/annulée, tant que la création est incertaine ou pendant une suppression ; une session dont le lot est créé ne bloque pas une nouvelle sélection. Le texte sur le rejeu a été rectifié : la reprise manuelle d'une session ouverte renvoie toujours tous les fichiers.
- `/tmp/seamtech-idempotence-creation-rouge.log` : 11 échecs attendus avant implémentation. Nouveau registre SQLite local `.creations.sqlite` sous la racine des téléversements : clé client aléatoire 256 bits (seule son empreinte est enregistrée), association à l'empreinte des paramètres et à un jeton opaque aléatoire, réservation commitée avant création, verrou interprocessus, reprise de l'initialisation interrompue, refus de changement de paramètres et non-résurrection après suppression. Le navigateur conserve la clé en mémoire, vérifie la capacité backend avant POST, puis la réutilise après une réponse perdue ; l'annulation retrouve la session avant de la supprimer. Une réponse de création qui ne confirme pas le protocole bloque tout nouveau POST pour cette sélection. Les anciens clients sans clé restent compatibles et ne sont pas idempotents.
- `/tmp/seamtech-idempotence-backend-suite.log` : **48 passed**, dont concurrence threads/processus et SIGKILL avant/après manifeste, erreurs et réutilisation après annulation. `/tmp/seamtech-creation-perdue-e2e-rouge.log` a reproduit l'échec navigateur ; `/tmp/seamtech-creation-perdue-e2e-vert.log` **4 passed**, dont perte de réponse de création réellement traitée par BFF/backend et mêmes jeton/clé au second POST. `/tmp/seamtech-annulation-creation-reelle.log` **1 passed**, vraie création backend puis réponse interrompue, récupération, suppression et rejeu HTTP 409. `/tmp/seamtech-idempotence-e2e-suite.log` **20 passed / 1 skipped** Chromium zéro retry avec backend SQLite : inclut ces chemins et les refus de protocole/capacité. La création de lot est simulée dans les deux tests de récupération ; ce n'est pas une qualification PG/S3 ni une coupure réseau OS.
- **Réserves critiques** : registre local exige système de fichiers partagé à verrous SQLite fiables et sauvegarde/restauration cohérente avec les sessions ; pas qualifié sur Windows/Docker Desktop, volumes réseau ou plusieurs hôtes. Une coexistence de versions backend derrière un répartiteur peut rendre le contrôle de capacité insuffisant : la réponse d'un ancien backend ignorant la clé peut être perdue avant détection ; déploiement coordonné sans versions mixtes nécessaire, non encore démontré. Perte/restauration incohérente du registre peut faire perdre l'idempotence ; coupure électrique non qualifiée. Registre sans purge ni quota qualifiés ; clés restent en mémoire uniquement, rafraîchissement/fermeture toujours non pris en charge. La matrice auth et une course entre suppression et sélection ont été lancées ou ajoutées, résultat à relever avant conclusion. Aucun commit/push/fusion/déploiement ; P0/P1/P2 **NON PRÊTS**.
- Prochaine action : relever les tests en cours, qualifier sérialisation de suppression, BFF/backend et authentification de la capacité, corriger les résultats rouges, réaliser build et campagne PG/Redis/S3 émulé sur les modifications finales ; vérifier gestion bornée et sauvegarde du registre et l'interdiction effective de versions mixtes. Poursuivre snapshots/réimport, Redis–PG, OCR, rotations et toutes autres portes précédemment consignées.

#### Suites et garde-fous complémentaires

- `/tmp/seamtech-session-suppression-course.log` **1 passed** : sélection et nouvel envoi refusés pendant un DELETE en attente, puis sélection autorisée après suppression confirmée. `/tmp/seamtech-idempotence-matrice-pg.log` **4 passed** (matrice backend avec PG16) et `/tmp/seamtech-idempotence-roles-cible.log` **1 passed** : capacité et création refusées au rôle lecture, autorisées aux rôles opérationnels et au service ; ces matrices restent partielles par rapport à toutes les combinaisons métier. Le test navigateur BFF sans session/lecture figure dans la campagne live.
- `/tmp/seamtech-idempotence-e2e-pg-s3.log` **23 passed**, Chromium zéro retry, PG16, Redis7, worker dans processus web, S3rver. Le parcours réel de promotion et de lecture est distinct des tests de création perdue/annulation, dont la création du lot reste simulée. Pas de MinIO/R2 réel, worker séparé, Windows/Edge, dépôt OS ou preuve de versions mixtes. S3rver arrêté et fichier de configuration temporaire supprimé. Build frontend `/tmp/seamtech-idempotence-build.log` réussi avant dernière gestion d'erreur aléatoire.
- `/tmp/seamtech-idempotence-regression-pg-redis.log` **87 passed / 4 warnings** ; `/tmp/seamtech-idempotence-permissions-vert.log` **51 passed / 1 warning** après ajout d'un contrôle fail-closed des permissions POSIX du registre et tests d'intégrité, de corruption et de concurrence/crash. Ce contrôle ne qualifie pas les ACL ni Windows, et ne ferme pas les substitutions concurrentes de chemins. `/tmp/seamtech-alea-indisponible-rouge.log` a reproduit une exception silencieuse si le générateur aléatoire est indisponible ; `/tmp/seamtech-alea-indisponible-vert.log` **1 passed**, affichage explicite et aucun POST, `tsc --noEmit` réussi.
- Étape suivante : build et tests sur l'état final, qualification détaillée de la taille/rétention/sauvegarde du registre et absence effective de versions mixtes, puis poursuite des autres défauts du plan. Pas de qualification des coupures électriques, capacité des stockages partagés, reprise après rechargement ou jeu de données réel. Toutes les portes restent **NON PRÊTES** ; aucun commit/push/fusion/déploiement.

#### Versionnement de l'API de création et refus avant émission

- Une protection supplémentaire a été ajoutée contre la coexistence d'un ancien backend : le BFF envoie les créations avec clé à `POST /imports/dossier/televersements/v2`, route qui exige une clé et ne peut pas être confondue avec l'ancienne route ; le backend actuel refuse une clé sur la route historique. Les clients historiques sans clé conservent leur ancien comportement non idempotent. `/tmp/seamtech-protocole-v2-rouge.log` a reproduit l'absence de route, `/tmp/seamtech-protocole-v2-vert.log` **51 passed** et `/tmp/seamtech-protocole-v2-e2e.log` **5 passed** (vrai BFF/backend pour création et annulation, création de lot simulée). Sonde isolée `/tmp/seamtech-protocole-v2-ancien-backend.log` : routes de `main@f1733c8` chargées dans l'application de test actuelle, POST v2 **405**, **0 session** créée. Ce test ne constitue pas une qualification complète d'un déploiement mixte ou d'un rollback, et l'ancien frontend demeure non idempotent.
- `/tmp/seamtech-capacite-absence-rouge.log` a montré un blocage inutile de la sélection après un refus avant tout POST ; le navigateur distingue maintenant l'émission effective du POST de la vérification préalable. `/tmp/seamtech-creation-gestion-finale.log` **6 passed**, `/tmp/seamtech-annulation-capacite-avant-post.log` **1 passed**, zéro retry, avec création/annulation réelles et cas simulés. Si la capacité échoue avant émission, la sélection peut changer ; si le POST est émis et sa réponse perdue, la clé reste en mémoire pour reprendre.
- Prochaine action : relancer matrice des rôles et build sur la route v2 finale, puis campagne PG/Redis/S3 émulé après build ; poursuivre qualification de l'idempotence (registre, sauvegarde, volumes et interruption/versions mixtes) ainsi que toutes les portes P0/P1/P2. Les résultats et réserves antérieurs restent valables sauf réserve de duplication par le chemin ancien pour le nouveau BFF, désormais réduite par l'URL v2 et la sonde citée. **NON PRÊT**, aucun commit/push/fusion/déploiement.

#### Registre borné et nouvelle validation

- Le registre conserve les tombstones pour empêcher la résurrection : il est maintenant borné à **1 000 000 réservations**. Au plafond, seules les nouvelles clés sont refusées (HTTP 507), les rejeux connus continuent ; aucune purge automatique risquant de réutiliser une clé n'est effectuée. `/tmp/seamtech-registre-plein-rouge.log`, puis `/tmp/seamtech-registre-plein-vert2.log` **52 passed** ; `/tmp/seamtech-registre-plein-http.log` **1 passed**. La supervision, l'alerte anticipée, la procédure d'administration, le dimensionnement sous charge et une politique de rétention sûre restent à créer : ce plafond ne constitue pas une exploitation prête.
- `/tmp/seamtech-protocole-v2-build.log` : build frontend réussi après versionnement et modifications finales de reprise. `/tmp/seamtech-protocole-v2-matrice.log` **56 passed / 3 warnings** avec PostgreSQL 16/Redis 7 ; inclut contrôle des rôles pour les deux routes de création. Campagne navigateur PG/S3 sur la route v2 `/tmp/seamtech-protocole-v2-e2e-pg-s3.log` en cours ; relever le résultat et arrêter l'émulateur de stockage avant fin de tour. Continuer ensuite tout le plan et les qualifications restantes, sans conclure à un GO.

- Campagne terminée `/tmp/seamtech-protocole-v2-e2e-pg-s3.log` : **25 passed**, Chromium zéro retry, PostgreSQL 16, Redis 7, worker web et S3rver ; l'un des parcours vérifie promotion et lecture après déplacement source, les tests de reprise réelle simulent seulement la création de lot. Un job **synthétique** résiduel issu de `tests/test_reconciliation_concurrente.py` a été signalé en arrière-plan (livraison conservée), puis seule sa livraison Redis identifiée a été retirée après arrêt des processus de test. Cela montre une isolation/nettoyage inter-campagnes à corriger ; ce résultat ne prouve pas une file durable propre ni une qualification de charge. S3rver arrêté, configuration temporaire supprimée. MinIO/R2, worker séparé, Windows/Edge et dépôt OS restent non qualifiés.
- Prochaine action : isoler et nettoyer systématiquement les ressources Redis/PG de test sans toucher à des données non synthétiques, superviser le registre borné, qualifier sa sauvegarde/restauration coordonnée et sa charge, puis poursuivre snapshots/réimport, Redis–PG, OCR, rotations, sécurité et endurance. État global **NON PRÊT**, CI exacte non qualifiée, aucun commit/push/fusion/déploiement.

### Isolation d'une partie des tests Redis

- ECC `security-review`, `redis-patterns`, `tdd-workflow`, `verification-loop` : la fixture `base_durable` effaçait toutes les clés `seamtech:*` du Redis désigné par l'environnement, puis laissait des tâches de test après fermeture. `/tmp/seamtech-banc-redis-danger-rouge.log` a reproduit la suppression d'un témoin synthétique dans un Redis séparé. La fixture utilise désormais pour chaque test son propre processus Redis local sur socket Unix dans un répertoire temporaire privé, sans port TCP ; arrêt du processus et suppression du répertoire en fin de fixture. Les overrides `SEAMTECH_REDIS_URL`/`SEAMTECH_DATABASE_URL` sont neutralisés pour les processus de test couverts. `/tmp/seamtech-banc-redis-danger-vert.log` **1 passed** : le témoin externe est préservé ; `/tmp/seamtech-banc-redis-isole-suite.log` **15 passed**, PG16 et processus Redis réels, y compris SIGKILL, quatre frontières retry/ack et les deux courses de réconciliation. Serveur témoin artificiel à arrêter avant fin de tour.
- **Réserve importante :** d'autres fixtures et tests du dépôt effacent encore des clés Redis, certains même `FLUSHDB`, sur `SEAMTECH_TEST_REDIS_URL`. Ils ne sont pas couverts par cette correction ; ne jamais les lancer sur une instance contenant des données réelles. La fixture corrigée exige un binaire redis-server et sa stratégie par socket Unix refuse explicitement Windows : Windows/Docker Desktop toujours non qualifiés. Il reste aussi à vérifier les échecs de démarrage et teardown, ainsi que les processus fils résiduels.
- Prochaine action précise : factoriser un serveur Redis de test isolé réutilisable, migrer et tester les autres fixtures destructrices sans toucher au témoin externe ni casser les suites/CI, puis poursuivre supervision/sauvegarde du registre et les autres portes de remédiation. Toutes les portes restent **NON PRÊTES**, aucun commit/push/fusion/déploiement.

#### Isolation étendue du banc Redis

- Facteur commun `tests/helpers_redis_isole.py` et fixture `redis_isole` : un processus Redis dédié par test, socket Unix privé, port TCP désactivé, arrêt et répertoire temporaire nettoyés même si le démarrage échoue. `/tmp/seamtech-banc-redis-pannes.log` **2 passed** (avant factorisation), puis `/tmp/seamtech-banc-redis-flushdb-vert.log` **4 passed** après factorisation, incluant échec de démarrage/nettoyage et scénario quarantaine. Le test `test_r3_quarantaine...` utilisait `FLUSHDB` sur le Redis de l'environnement ; `/tmp/seamtech-banc-redis-flushdb-rouge.log` l'a reproduit sur une instance synthétique séparée, puis le test a été migré vers le serveur privé sans `FLUSHDB`.
- Les fixtures de file durable SQLite/PG, opérations, workers, relais, partage web/worker, claim/ack, sessions et matrice de rôles, ainsi que les tests de perte de claim et de rejeu sélectionnés, utilisent maintenant des instances privées. La variable `SEAMTECH_TEST_REDIS_URL` conserve le rôle de signal d'activation dans ces cas, mais son serveur n'est pas interrogé ; ces tests ne prouvent donc pas la disponibilité de ce serveur externe. Le témoin de non-destruction est lui-même hébergé dans un **second serveur privé**, jamais sur l'URL de l'environnement. `/tmp/seamtech-banc-redis-file-rouge.log` a mis en évidence une fixture SQLite non isolée ; `/tmp/seamtech-banc-redis-file-vert.log` **4 passed**. `/tmp/seamtech-banc-redis-elargi-suite.log` **73 passed / 1 warning** (avant les dernières migrations), `/tmp/seamtech-banc-redis-relais-suite.log` **33 passed** ; `/tmp/seamtech-banc-redis-local-independant.log` **29 passed / 65 deselected / 3 warnings** avec URL Redis d'environnement volontairement injoignable, PG16 et serveurs Redis7 éphémères. Le serveur témoin synthétique initial a été arrêté ; Ruff et `git diff --check` réussis.
- Limites : cette analyse ne démontre pas l'isolation de **tous** les tests ni des scénarios Docker/corpus ni la sécurité des adresses PostgreSQL ; certaines fonctions de purge demeurent dans les fichiers de tests mais sont exécutées sur les instances privées dans les chemins migrés. Le binaire `redis-server` est désormais nécessaire pour les tests migrés ; fallback `redislite` antérieur du scénario R3 n'est plus pris en charge et le scénario peut être sauté sans binaire ni URL de test. Windows natif n'est pas pris en charge par le helper Unix. Aucun résultat ne qualifie une coupure électrique ni Redis distant réel.
- Étape suivante : relancer ensemble la campagne Redis élargie sur les changements finaux et revoir les autres points d'accès aux services ; ensuite avancer les défauts P0/P1/P2 consignés (sauvegardes cohérentes, confinement/snapshot et réimport protégé, Redis–PG, OCR, rotations, sécurité, charge/endurance). Toutes les portes **NON PRÊTES**, aucune CI exacte, aucun commit/push/fusion/déploiement.

#### Complément : qualification Redis et sécurité du seed PostgreSQL navigateur

- `pytest -m redis_queue` avec PostgreSQL 16, Redis privé 7.4.7 et l'URL Redis externe volontairement injoignable : `/tmp/seamtech-banc-redis7-final.log` **136 passed / 1 xfailed / 1 749 deselected**, code 0 ; l'xfail du défaut Redis–PG reste bloquant. Sélection SQLite CI sans services externes : `/tmp/seamtech-suite-sqlite-banc.log` **1 207 passed / 4 xfailed / 675 deselected**, code 0. Ces chiffres ne sont pas une preuve de la disponibilité du service Redis configuré ni une qualification complète. Aucun processus ni répertoire `seamtech-redis-test-*` résiduel observé après les suites (sonde locale).
- Correction de la limite indiquée plus haut : le repli `redislite` est de nouveau pris en charge en cherchant son binaire embarqué, sans connexion à une instance tierce. `/tmp/seamtech-redislite-r3-reel.log` **1 passed / 12 deselected** sans URL ni Redis sur le PATH ; le binaire du paquet testé est Redis **6.2.14**, donc ne prouve pas Redis 7. Les tests marqués `redis_queue` et la fixture `base_durable` exigent maintenant Redis >= 7 et échouent si le binaire privé est plus ancien ; `/tmp/seamtech-banc-version-rouge.log` puis `/tmp/seamtech-banc-version-vert.log` **22 passed**, et `/tmp/seamtech-redislite-refus-version.log` vérifie le refus réel de Redis 6. Le repli ne s'applique pas si un chemin explicite invalide est fourni. Le helper reste non qualifié Windows.
- La CI installait `redis-server` **après** la suite PostgreSQL alors que les fixtures isolées en ont besoin avant cette étape ; installation déplacée avant SQLite et PostgreSQL. Garde de l'ordre `/tmp/seamtech-ci-redis-ordre-rouge.log` puis `/tmp/seamtech-ci-redis-ordre-vert.log` **1 passed**. La CI distante complète et ses jobs ne sont pas relancés/qualifiés sur ces modifications ; la disponibilité effective de Redis 7 sur les runners doit encore être vérifiée.
- Détection supplémentaire P2/sécurité du banc : `frontend/e2e/seed-live-pg.py` déconnectait et supprimait **toutes** les bases PostgreSQL préfixées `e2e_`, même appartenant à un autre run ou une autre personne. `/tmp/seamtech-seed-e2e-purge-rouge.log` prouve l'appel destructif sur un double ; suppression de cette purge et de la terminaison des connexions. `/tmp/seamtech-seed-e2e-postgres-vert.log` **4 passed**, dont une véritable base témoin et sa connexion maintenue vivante pendant la création du seed, sur PG16 éphémère ; seul le nom créé par le test a été supprimé à son terme. Désormais chaque run crée une base aléatoire 128 bits ; **aucune collecte automatique** des anciennes bases, donc accumulation/disque et procédure de nettoyage sûr restent à traiter. Ne pas faire de purge globale basée sur un préfixe.
- Le seed exposait aussi l'URL et le message d'erreur en clair dans stderr, et stockait l'URL avec identifiants dans `.live-pg-run.json`. `/tmp/seamtech-seed-e2e-secret-rouge.log` et `/tmp/seamtech-seed-e2e-fichier-secret-rouge.log` démontrent les problèmes ; le seed stocke et émet désormais seulement le nom et l'ID du run, reconstitue l'URL depuis l'environnement et ne journalise que le type de l'erreur de connexion. Le consommateur Playwright et le job hors-ligne CI ont été adaptés ; `/tmp/seamtech-seed-ci-contrat-final2.log` **25 passed** (contrat, garde CI et PG réel), `/tmp/seamtech-seed-e2e-navigateur.log` **1 passed** sans retry : création, réutilisation du même run et parcours navigateur sur PG16, sans Redis. La base créée par cette vérification seule a été supprimée après contrôle de son identifiant et absence de connexions ; fichier du run synthétique retiré. Ces tests ne qualifient ni le job hors-ligne entier ni tous les points possibles de fuite de secrets ; la configuration du job hors-ligne écrit encore sa propre configuration temporaire et mérite audit.
- ECC appliquées pour ce complément : `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `redis-patterns`, `e2e-testing`, `verification-loop`. Ruff global et `git diff --check` réussis. Le fichier seed a été ajusté après le test navigateur pour valider l'URL avant création ; la suite PG/contrat a ensuite été rejouée.
- Prochaine action précise : auditer les autres suppressions PG et les écritures de configuration/identifiants dans les harnais et le job hors-ligne, tout en gardant la suppression strictement limitée aux bases effectivement créées par le test ; vérifier compatibilité CI en entier. Puis poursuivre les défauts ouverts P0/P1/P2 (snapshot complet, TOCTOU/réimport, atomicité Redis–PG, OCR, rotations, sécurité et endurance). Toutes portes **NON PRÊTES** ; aucun commit, push, fusion ni déploiement ; CI exacte non verte/non évaluée.

#### Sécurité du banc PG et compatibilité des suites CI

- ECC `search-first`, `security-review`, `postgres-patterns`, `tdd-workflow`, `e2e-testing`, `verification-loop` : la configuration du worker de relais contenait l'URL PostgreSQL d'administration, bien que le sous-processus reçoive normalement celle de la base jetable par environnement. `/tmp/seamtech-securite-relais-conf-rouge.log` le démontre avec un secret synthétique ; la configuration n'inclut plus cette URL. `/tmp/seamtech-securite-relais-conf-vert.log` **6 passed**, processus relais et PG/Redis réels isolés. Ce changement réduit le risque de repli accidentel mais n'est pas un audit exhaustif des autres fixtures.
- Le seed navigateur ouvrait son verrou en mode tronquant et suivait les liens symboliques, pouvant donc écraser un autre fichier ; le fichier de run était lui aussi écrit en suivant les liens. `/tmp/seamtech-seed-lien-rouge.log` et `/tmp/seamtech-seed-lecture-lien-rouge.log` : régressions sur cibles synthétiques. Verrou ouvert sans troncature avec `O_NOFOLLOW` et vérification fichier ordinaire, lecture du run sans suivre les liens avec taille bornée, écriture temporaire puis remplacement atomique du lien lui-même. FIFO et fichier trop volumineux refusés ; ID de run borné et contrôlé avant connexion ; connexion de contrôle fermée après erreur ; erreur de connexion admin expurgée. Tests PG/unitaires `/tmp/seamtech-seed-dbname-vert.log` **38 passed**, avant le dernier test d'arrêt S3rver ; `/tmp/seamtech-seed-liens-navigateur.log` **1 passed** sans retry avant ajout de la fermeture de connexion et de la validation d'URL. La base synthétique de ce run a été supprimée seule après vérification qu'aucune connexion ne l'utilisait. Ces protections sont POSIX/Linux et ne démontrent pas toutes les substitutions concurrentes de chemins ni Windows.
- L'URL PG du seed pouvait garder `?dbname=...`, qui dans libpq prend priorité sur le nom de base dans le chemin et aurait pu faire travailler le navigateur sur une autre base. `/tmp/seamtech-seed-dbname-rouge.log` et `/tmp/seamtech-seed-dbname-avant-rouge.log` reproduisent le problème avec données synthétiques ; validation de la base effective via `parse_dsn` avant connexion/création. `/tmp/seamtech-seed-options-pg.log` **1 passed** sur PG16 avec une option SSL non dangereuse. Échec d'accès à l'URL admin et des autres étapes reste à auditer pour tous les points de fuite possibles.
- Le helper de test de restauration `DROP DATABASE` accepte maintenant le nom explicite fourni par la fixture, exige les formats générés par les fixtures, le même ensemble de paramètres PG et le bon `dbname` effectif, refuse la base d'administration, et utilise `sql.Identifier`. `/tmp/seamtech-drop-pg-non-sur-rouge.log` puis `/tmp/seamtech-drop-pg-non-sur-vert2.log` **4 passed** : refus de noms arbitraires, d'autre serveur et du `dbname` détourné, avant connexion. `/tmp/seamtech-drop-pg-restauration-suite2.log` **7 passed / 1 deselected** avec PG16/clients 16.14, y compris 50 000 fiches ; premier essai `/tmp/seamtech-drop-pg-restauration-suite.log` **7 failed** car `libpq.so.5` n'était pas dans `LD_LIBRARY_PATH`, corrigé pour le second essai. Cette garde dépend de l'identité fournie par la fixture, ne prouve pas l'appartenance d'une base sur tous les scénarios et ne remplace pas un cluster de test privé.
- La configuration temporaire de l'épreuve hors ligne CI contenant un jeton est créée exclusivement en mode 0600 via `scripts/fichier_prive.py`, puis installée en 0600 pour le compte applicatif et le fichier temporaire retiré. `/tmp/seamtech-banc-fichier-prive-rouge.log` puis `/tmp/seamtech-banc-hors-ligne-conf.log` **3 passed** (droits, refus d'écrasement/lien, exécution du code de configuration extrait du workflow avec secrets synthétiques). Cela ne protège pas contre les autres processus du même utilisateur ni n'élimine les secrets passés dans des arguments `sudo env` du job hors ligne ; ces limites restent ouvertes et le job entier n'a pas été exécuté ici.
- `/tmp/seamtech-seed-banc-ci-pg.log` : sélection PG CI **387 passed / 3 skipped / 1 xfailed**. Deux skips venaient de scénarios réseau S3rver ajoutés sans provisionnement dans le job ; ce job impose zéro skip, donc il aurait échoué. CI modifiée pour installer et démarrer un S3rver local sur loopback avant les suites PG/couverture. Lock npm dédié suivi sous `tests/fixtures/s3rver/` ; son `node_modules` est ignoré. S3rver **3.7.1** introduisait trois vulnérabilités élevées dans `npm audit` ; remplacé par **2.2.9**, avec `xml2js` **0.6.2** imposé, puis audit verrouillé **0 vulnérabilité** au moment du test `/tmp/seamtech-s3rver-audit-final.json`. L'émulateur est seulement un banc synthétique, ni MinIO ni R2 ; risque des dépendances et sécurité du réseau doivent rester surveillés.
- `/tmp/seamtech-ci-s3rver-verrouille-tests.log` et `/tmp/seamtech-ci-s3rver-etape-reelle.log` : **2 passed / 62 deselected** pour les deux scénarios HTTP PG avec S3rver verrouillé. L'étape CI extraite a été réellement exécutée : `npm ci`, audit 0, écoute loopback et readiness réussis ; l'étape d'arrêt utilise `pidfd` et contrôle les arguments avant signal, test automatisé de refus d'un processus tiers `/tmp/seamtech-ci-s3rver-arret-auto.log` **5 passed**, arrêt réel `/tmp/seamtech-ci-s3rver-arret.log`. Aucun serveur S3rver lancé pour ces essais n'est resté actif. La CI GitHub complète n'a pas été exécutée sur ces changements.
- `/tmp/seamtech-ci-pg-s3rver-verrouille.log` : sélection PG avec émulateur verrouillé **389 passed / 1 skipped / 1 xfailed / 1 511 deselected**, le skip étant le modèle e5 absent dans la sandbox (CI prévoit son téléchargement), l'xfail de réimport protégé reste un défaut produit. Le garde CI exige toujours zéro skip ; cette campagne locale ne satisfait donc pas sa porte. `/tmp/seamtech-seed-ci-selection-finale.log` **25 passed** (sélection CI et sécurité banc avant ajout du dernier test), Ruff global et `git diff --check` réussis.
- Prochaine action précise : traiter les secrets encore visibles dans les arguments du job hors ligne, éliminer les autres voies PG/S3 de test non isolées, puis reprendre les blocages produit existants : cohérence/restauration complète, confinement TOCTOU et réimport, atomicité Redis–PG, OCR exact navigateur, rotations PDF, MinIO/R2, matrice sécurité et endurance. Pas de qualification E5 locale, Windows, coupure électrique, Docker complet ou CI exacte ; P0/P1/P2 **NON PRÊTS**, aucun commit/push/fusion/déploiement.
- Contrôle des processus après les nouveaux essais : un ancien S3rver synthétique de cette session, sur `127.0.0.1:54568` (processus `stockage-s3-simul-9aa65d5e` démarré à 15:24), était encore actif malgré les précédentes notes ; il a été arrêté via son outil de processus, sans supprimer son répertoire de données. Les S3rver des essais de ce tour ont aussi été arrêtés. Les services PG16/Redis de banc préexistants restent en place pour la suite.

#### Protection des URL PostgreSQL et de la restauration

- ECC appliquées : `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `e2e-testing`, `verification-loop`. Défaut confirmé sur un double : `restaurer()` déterminait la base cible par le chemin de l'URI, alors que libpq donne priorité à `?dbname=...`. Il aurait pu créer une base au nom du chemin puis envoyer `pg_restore` vers une base différente déjà existante. `/tmp/seamtech-restauration-url-redirection-rouge.log` montre que la connexion administrative était tentée pour une URI détournée. La restauration rejette maintenant les URI ambiguës, y compris le paramètre `dbname` encodé, avant connexion/création ; elle vérifie que le nom effectif libpq correspond au chemin. Les messages d'erreur ne contiennent pas l'URI. `/tmp/seamtech-restauration-url-redirection-vert.log` **3 passed** avant ajout des cas supplémentaires ; `/tmp/seamtech-restauration-cible-pg-preservee.log` **1 passed** sur PG16 avec base témoin synthétique existante, aucune création de la base nommée dans le chemin et contenu témoin conservé. Cette preuve couvre la frontière testée, pas toutes les combinaisons de panne ni la restauration globale.
- Les helpers de restauration produisaient aussi `postgresql:/...` au lieu de `postgresql:///...` sans hôte, cassant les connexions par socket Unix : `/tmp/seamtech-banc-pg-socket-sauvegarde.log` **12 passed / 1 failed / 1 deselected**. Construction corrigée, paramètres de connexion préservés. `/tmp/seamtech-restauration-pg-socket-vert.log` **47 passed / 2 deselected** sur le socket PG16 ; `/tmp/seamtech-restauration-url-snapshot-final.log` **135 passed / 2 deselected / 1 xfailed**, couvrant sauvegarde/restauration, références et stockage simulé. L'xfail de mutation des octets après snapshot demeure un blocage P0 : ces résultats ne prouvent pas un snapshot coordonné, ni sauvegarde intégrale de `./data`, MinIO/R2 ou hors site.
- Sécurité du banc : certaines fixtures construisaient leurs URL par découpage de chaîne ; cela pouvait perdre les options TLS/socket ou laisser `dbname` rediriger vers une base tierce. Garde de début de session pytest et helper `tests/helpers_postgres_isole.py` : préservation des options et vérification libpq que la base cible diffère de la base d'administration. `/tmp/seamtech-banc-pg-dbn-query-rouge.log` puis `/tmp/seamtech-url-pg-garde-suite.log` **28 passed** ; création de la fixture commune validée avant toute connexion. Constructeurs identifiés de fixtures PG migrés ; seed navigateur et job hors ligne corrigés également pour les URI sans hôte. `/tmp/seamtech-pg-socket-rouge.log` a reproduit les deux erreurs de sérialisation ; `/tmp/seamtech-banc-pg-socket-suite.log` **49 passed** sur socket réel, `/tmp/seamtech-banc-pg-options-redis.log` **15 passed** PG/Redis avec options, `/tmp/seamtech-banc-pg-options-restauration.log` **7 passed / 1 deselected**. Sélection PG étendue `/tmp/seamtech-banc-pg-options-suite.log` **387 passed / 3 skipped / 1 xfailed** : S3rver non démarré dans cette campagne, modèle E5 absent, réimport protégé xfail. Les suites ont commencé avant les derniers tests et certains ajustements de seed. Le serveur désigné par l'URL de test doit toujours être dédié : la garde ne prouve pas son identité ni l'isolation de tous les scripts hors pytest.
- Job CI hors ligne : suppression des secrets des arguments `sudo env` et des arguments `curl -H` des lancements web/worker/front et des deux contrôles santé identifiés. Les variables sont sérialisées avec `shlex.quote` dans un fichier privé, installé en 0600 pour le compte applicatif ; un fichier d'en-tête privé fournit le jeton à curl. Les fichiers temporaires sont retirés après installation. `/tmp/seamtech-hors-ligne-argv-rouge.log` puis `/tmp/seamtech-hors-ligne-argv-vert.log` **29 passed** ; `/tmp/seamtech-garde-final-test.log` **74 passed / 1 skipped** (PG non configuré dans cette sélection), avec test de valeur shell hostile synthétique non exécutée. Syntaxe bash des étapes hors ligne vérifiée ; aucune exécution complète Docker/iptables du job. Les secrets restent présents dans l'environnement et des fichiers 0600 accessibles au même compte et à l'administrateur, dans `GITHUB_ENV`, ainsi que potentiellement dans d'autres commandes/logs non audités : ne pas conclure à une élimination générale des fuites.
- Ruff global et `git diff --check` réussis. Prochaine action précise : poursuivre audit et essais réels du job hors ligne complet et des frontières PG de restauration, puis corriger les blocages P0/P1/P2 déjà relevés : instantané des octets + `./data`/objets/restauration coordonnée, confinement TOCTOU et réimport, clôture Redis–PG, OCR exact et rotations, MinIO/R2, matrice offensive et endurance. P0/P1/P2 **NON PRÊTS**, CI distante du commit exact non exécutée, aucun commit/push/fusion/déploiement.

#### Copie vérifiée du dump avant restauration (sandbox réinitialisé)

- ECC : `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop`. Tests synthétiques rouges `/tmp/seamtech-restore-dump-toctou-rouge.log` (dump substitué entre hachage et `pg_restore`) et `/tmp/seamtech-restore-copie-securite-rouge.log` (lien final suivi ; **1 failed / 1 passed**). `restaurer` copie maintenant le dump dans un répertoire privé 0700, fichier 0600, en flux par blocs de 1 Mio, refuse le lien final et les fichiers non ordinaires (ouverture non bloquante), puis vérifie et restaure la même copie avant de la nettoyer. Échec fermé si `O_NOFOLLOW` indisponible : Windows reste à traiter et qualifier. Répertoire `SEAMTECH_SAUVEGARDE_TMP` respecté. Copie et vérification échouent avant la connexion PG ; messages d'erreur de ces étapes sans chemin source.
- `/tmp/seamtech-restore-copie-securite-vert.log` **7 passed / 25 deselected** ; `/tmp/seamtech-suite-sauvegarde-copie.log` **106 passed / 5 skipped** sans PG ; `/tmp/seamtech-suite-copie-et-garde.log` **60 passed / 15 skipped** sans PG. Tests ajoutés FIFO sans écrivain, liens, substitution, disque plein simulé, permissions et nettoyage ; le runbook décrit l'espace disque supplémentaire, la double copie S3 et les limites. Ruff global et `git diff --check` réussis.
- Démonstration réelle ciblée `/tmp/seamtech-restore-copie-pg18-final.log` **36 passed**, dont un test `tests/test_restauration_dump_stable_pg.py` avec PG **18.4**, `pg_dump` et `pg_restore` 18.4 sur socket isolé : substitution du fichier source après vérification, lecture en base de la valeur originale et nettoyage. Cela ne qualifie pas les schémas applicatifs, S3 ou PG16. Le premier essai PG16 a échoué par manque de bibliothèque de client ; le suivant a échoué car `pg_restore` 18 émet `SET transaction_timeout`, inconnu sur PG16. Ne pas interpréter cet échec de banc comme preuve produit ; les clients 16 doivent être reconstruits ou obtenus avant validation PG16. Compilation depuis l'archive GitHub interrompue par absence de bison et des fichiers générés, après désactivation ICU ; les PG16 et PG18 isolés démarrés ici ont été arrêtés. Aucun autre service ou donnée non identifiée n'a été touché.
- Limites : un processus hostile de même UID ou l'administrateur peut modifier la copie ; pas de garantie après arrêt brutal, ni protection des composants parents symlinkés, ni preuve d'authenticité du manifeste ; coût disque et double copie S3 ; création PG suivie d'échec peut laisser une base partielle. Ce correctif ne résout pas la cohérence des octets pendant la sauvegarde, ni `./data`, stockage objet, rétention/restauration coordonnée. Les anciens logs `/tmp` des checkpoints précédents ont disparu lors de la réinitialisation ; les résultats cités alors restent historiques. P0/P1/P2 **NON PRÊTS**, CI exacte non exécutée ; pas de commit/push/fusion/déploiement.
- Prochaine action précise : obtenir des clients PostgreSQL 16 et pgvector utilisables dans le banc isolé, rejouer le test de substitution et la suite restauration réelle sur PG16, puis poursuivre en priorité la sauvegarde/restauration coordonnée et l'xfail de mutation des octets ; continuer ensuite les défauts de confinement, Redis–PG, OCR, rotations, MinIO/R2, matrice et endurance décrits ci-dessus. Éviter `pg_restore` 18 sur serveur PG16.

#### Qualification ciblée PG16 et nouveaux défauts du bundle

- ECC utilisées : `search-first`, `tdd-workflow`, `postgres-patterns`, `security-review`, `verification-loop`. Banc local recompilé dans `/tmp` : PostgreSQL **16.14** (serveur et clients de même version), pgvector **0.8.1**, `unaccent` et `pg_trgm`, cluster neuf sur socket privé sans écoute TCP. Pour construire les sources, Bison/Flex viennent de roues PyPI et GNU m4 1.4.16 d'une source GitHub, avec adaptations locales glibc ; uniquement des outils de test éphémères. Ce n'est pas une qualification de la chaîne de compilation de production.
- `/tmp/seamtech-restauration-copie-pg16-suite3.log` : **60 passed / 2 skipped / 1 xfailed** : le test réel de substitution utilise à présent PG16, dump et restore 16 ; sauvegarde/références/snapshot et restauration 50 000 fiches sont inclus. Les deux skips exigent MinIO réel absent ; l'xfail strict reste la mutation des octets locaux après snapshot. Première exécution `/tmp/seamtech-restauration-copie-pg16-suite.log` **59 passed / 2 skipped / 1 xfailed / 1 failed** car le banc n'avait pas `unaccent` et `pg_trgm`, puis extensions compilées et suite rejouée. Les tests restent synthétiques ; ce résultat ne prouve pas la restauration coordonnée. Le cluster a été arrêté ; aucun autre service n'a été modifié.
- Trois nouveaux défauts strictement reproduits, non corrigés, dans `tests/test_restauration_confinement.py` : la validation de hash du bundle est séparée de la lecture par `shutil.copy2` ou par le client objet ; une substitution provoque copie ou upload d'octets différents de ceux validés. La restauration locale écrase aussi silencieusement un fichier existant au chemin cible. `/tmp/seamtech-bundle-toctou-xfails.log` **14 passed / 3 xfailed**. La validation CLI avant création PG répète ensuite des opérations non atomiques ; un changement ou une erreur ultérieure peut laisser une base ou des composants partiels. Ne pas interpréter ces xfails comme des tests réussis de sécurité. Ruff global et `git diff --check` réussis après ajout.
- Prochaine action précise : concevoir et tester une restauration coordonnée avec staging privé de tous les éléments, revalidation des octets effectivement publiés, cibles nouvelles sans écrasement atomique, confinement par descripteurs et gestion d'échecs/reprise ; inclure les chemins CLI et objet sans prétendre à une transaction distribuée. Lever les trois xfails seulement après les tests d'interruption, course et non-écrasement, sur PG16 et fournisseur objet adéquat. Ensuite traiter la mutation des octets durant la sauvegarde et les autres P0/P1/P2 déjà documentés. P0/P1/P2 **NON PRÊTS** ; CI exacte non exécutée ; pas de commit/push/fusion/déploiement.

#### Staging des pièces et non-écrasement local

- ECC appliquées : `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop`. `/tmp/seamtech-bundle-trois-rouge.log` **3 failed** avec xfails désactivés : substitution après hash local/objet et écrasement de cible existante. Les trois tests correspondants ne sont plus xfail : staging privé et rehash de chaque copie avant toute publication locale ou objet ; la CLI fige les deux plans avant création de la base et réutilise ces copies. En local, création exclusive via `O_EXCL` et parcours de répertoires par descripteurs avec `O_NOFOLLOW` ; refus des cibles existantes et liens substitués. Fichier partiel retiré sur erreur gérée si l'identité correspond. Échec fermé sur plateforme sans ces primitives ; Windows non qualifié. Le runbook précise espace requis et limites.
- `/tmp/seamtech-bundle-staging-vert.log` **32 passed / 3 skipped** sans PG ; `/tmp/seamtech-bundle-cli-pg16.log` **2 passed** sur PG16 réel, dont substitution de pièce pendant connexion PG et vérification base + fichier restauré ; `/tmp/seamtech-bundle-staging-s3rver.log` **1 passed** sur objet envoyé par réseau et relu depuis S3rver, bucket/key synthétiques propres supprimés explicitement. Ce n'est pas MinIO/R2. Tests supplémentaires : collision après précontrôle, substitution d'un répertoire par symlink, corruption tardive d'une copie privée avant toute écriture, erreur d'écriture et deux restaurations concurrentes ; `/tmp/seamtech-bundle-concurrence.log` **23 passed / 2 xfailed** avant ajout du test suivant.
- `/tmp/seamtech-bundle-staging-final-pg16.log` **145 passed / 2 skipped / 3 xfailed**, PG16 + S3rver ; deux skips MinIO réel, xfails mutation des octets au snapshot et deux limites ci-dessous. Ce run précède l'ajout des tests concurrence et transport. Après ceux-ci `/tmp/seamtech-bundle-limites-final.log` **23 passed / 3 xfailed** : échec en cours de publication multi-fichiers laisse les précédents, restauration d'objet écrase encore une clé existante, absence de relecture de l'objet publié (un client qui tronque peut déclarer succès). Ces limites sont des défauts ouverts, pas des validations réussies. Ruff et `git diff --check` réussis. PG16 et S3rver isolés arrêtés.
- Autres limites : aucune transaction distribuée, aucune garantie coupure brutale/reprise, changements concurrents par même UID/admin sur staging, symlinks dans les sources/parents pas entièrement qualifiés, coût au moins égal à la taille totale des copies + dump, compatibilité Windows et volume non qualifiés, bucket S3 de restauration doit être isolé. La sauvegarde ne fige toujours pas les octets au snapshot ; restauration complète `./data` et MinIO/R2 ne sont pas prouvées. P0/P1/P2 **NON PRÊTS**, CI exacte non exécutée ; aucun commit/push/fusion/déploiement.
- Prochaine action précise : sécuriser la publication objet sans écrasement atomique à clé identique (étudier condition `If-None-Match` et multipart, avec refus fail-closed si non supporté), relire et vérifier les octets effectivement reçus ; traiter ensuite publication multi-fichiers et restauration coordonnée en panne/reprise, en tenant compte des bases déjà créées et de l'absence de transaction distribuée. Lever les xfails seulement après preuves en concurrence et injections de panne, puis poursuivre snapshots et les blocages P1/P2.

#### Relecture des objets restaurés et précondition de création

- Sandbox de nouveau réinitialisé : anciens services, `/tmp` et `.venv` disparus ; `.venv` réinstallé avec `requirements-dev.txt`, et S3rver éphémère réinstallé depuis le lock. Les preuves `/tmp` des tours antérieurs restent historiques et ne sont plus présentes. ECC appliquées : `search-first`, `tdd-workflow`, `security-review`, `verification-loop`.
- `/tmp/seamtech-objet-relecture-rouge.log` : test strict rouge, un client qui tronque l'objet était déclaré réussi. Après envoi, la restauration relit maintenant l'objet dans un répertoire privé temporaire et compare SHA-256 avant de déclarer succès ; échec de lecture et divergence sont signalés sans détails d'exception sensibles et les temporaires sont nettoyés. La clé renvoyée doit correspondre à celle du manifeste. Un préfixe de `S3StorageClient` qui transformerait la clé est vérifié pour toutes les entrées avant écriture et, via la CLI, avant création PG : `/tmp/seamtech-restauration-prefixe-rouge.log` puis `-vert.log` **2 passed**. Test transport corrompu et nettoyage levé de xfail. Un échec après envoi laisse toutefois l'objet écrit ; cette étape ne garantit pas les octets futurs ni une transaction.
- `/tmp/seamtech-objet-relecture-final.log` : **135 passed / 4 skipped / 3 xfailed** avec S3rver, avant les deux derniers petits ajustements de test ; deux xfails sont les défauts publication multi-fichiers et écrasement objet, le troisième est la sonde S3rver ci-dessous. Ruff a d'abord relevé un ordre d'import puis a été corrigé. `/tmp/seamtech-objet-relecture-unit-final.log` **134 passed / 4 skipped / 2 xfailed** après correction, sans PG ni S3 ; Ruff global et `git diff --check` réussis. Le test réseau isolé de relecture `/tmp/seamtech-objet-relecture-reseau.log` **28 passed / 2 xfailed** avant le test CLI de préfixe ; S3rver arrêté. Aucun PG16 ni MinIO/R2 n'ont été rejoués dans le sandbox actuel.
- Étude sécurité : le modèle botocore verrouillé expose `IfNoneMatch` pour `PutObject` et `CompleteMultipartUpload`, mais ce n'est pas une preuve fournisseur. Sonde réseau synthétique sur bucket UUID privé `/tmp/seamtech-s3rver-precondition-probe.log` : **second PUT accepté, original non préservé**, malgré `IfNoneMatch='*'`. Test strict `/tmp/seamtech-s3rver-precondition-test.log` **1 passed / 1 xfailed**. Le bucket et la seule clé de sonde ont été supprimés ; aucune donnée externe touchée. Ne pas implémenter une protection supposée atomique en se fondant seulement sur la présence du paramètre SDK ou sur cet émulateur : il l'ignore silencieusement. Pas de preuve MinIO/R2, multipart ou concurrence ; l'écrasement d'une clé réelle reste possible, la restauration objet reste NON PRÊTE.
- Prochaine action précise : obtenir et qualifier un MinIO réellement isolé, étudier les préconditions atomiques y compris multipart/erreur ambiguë et vérifier le comportement R2 sans toucher à des données réelles ; concevoir un mode échec fermé si fournisseur non qualifié, puis traiter le rollback/reprise multi-fichiers et le snapshot des octets. P0/P1/P2 **NON PRÊTS**, CI exacte non exécutée, aucun commit/push/fusion/déploiement.

#### Confinement des sources du bundle

- ECC : `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop`. Un nouveau test synthétique `/tmp/seamtech-bundle-dossier-rouge.log` a démontré que substituer un répertoire du bundle par un lien après validation faisait relire une source extérieure, même avec `O_NOFOLLOW` sur le dernier composant. Le hachage initial et la copie privée ouvrent maintenant les composants depuis `/` avec descripteurs, `O_DIRECTORY|O_NOFOLLOW` et refus des fichiers non ordinaires/FIFO, sans suivre de liens internes. Revalidation SHA de la copie privée conservée. Si la racine résolue change entre planification et staging, refus explicite. Les liens symboliques internes précédemment acceptés sont désormais refusés ; compatibilité Windows non qualifiée et échec fermé. Cela ne prouve pas l'authenticité du manifeste, ne protège pas d'un autre processus du même UID et ne constitue pas un snapshot coordonné.
- `/tmp/seamtech-bundle-racine-test.log` **3 passed** : substitutions avant hash, après plan et lors du staging ; `/tmp/seamtech-bundle-objet-cli-confinement.log` **6 passed** : inclut refus avant création de base pour la CLI objet. Tests FIFO et lien interne également ajoutés. `/tmp/seamtech-bundle-confinement-final.log` **140 passed / 4 skipped / 3 xfailed** sans PG, S3rver réseau, avant derniers tests supplémentaires. Nouveau banc PG16 recompilé après réinitialisation, extensions pgvector 0.8.1, unaccent et pg_trgm ; `/tmp/seamtech-bundle-confinement-pg16-final.log` **157 passed / 2 skipped / 4 xfailed**, PG16 + S3rver. Skips MinIO officiel ; xfails : précondition ignorée par S3rver, publication locale multi-fichiers non transactionnelle, écrasement objet existant, mutation des octets sauvegardés au snapshot. Test CLI PG réel inclus ; données synthétiques uniquement. Services PG/S3rver arrêtés, Ruff et `git diff --check` réussis avant les deux derniers tests, à rejouer en fin de suite.
- Recherche MinIO : sources officielles au tag `RELEASE.2025-09-07T16-13-09Z` téléchargées pour banc uniquement, outil Go installé en environnement éphémère ; pas de vendor dans l'archive, `proxy.golang.org` inaccessible et les assets release redirigent hors hôtes autorisés. Aucun serveur MinIO/R2 n'a été obtenu ou qualifié ; S3rver continue d'ignorer silencieusement `If-None-Match`. Ne pas confondre présence du paramètre botocore et garantie du fournisseur.
- Prochaine action précise : achever qualification MinIO/R2 isolés et concevoir un mode d'écriture objet sans écrasement réellement vérifié ; ensuite traiter transaction/reprise des publications multiples, état PG/objets et snapshot coordonné des octets, puis OCR/rotations et le reste des portes. P0/P1/P2 **NON PRÊTS**, CI exacte non exécutée, pas de commit/push/fusion/déploiement.

#### Protection du verrou de sauvegarde

- ECC : `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop`. Analyse du verrou : `open(..., 'a+')` suivait un lien et `truncate()` pouvait détruire un fichier tiers, y compris en l'absence de sauvegarde effective. `/tmp/seamtech-verrou-sauvegarde-rouge.log` **3 failed** : lien suivi, fichier régulier existant modifié, fuite du flux sur erreur `flock`. Le verrou ne modifie plus le contenu du fichier, refuse liens symboliques/durs et fichiers non ordinaires via ouverture non bloquante `O_NOFOLLOW`, ferme les descripteurs sur erreurs et refuse l'absence de verrouillage sûr. Import du module possible sans `fcntl`, mais sauvegarde verrouillée refuse alors de fonctionner ; Windows reste non qualifié.
- `/tmp/seamtech-verrou-reentree-rouge.log` et `/tmp/seamtech-verrou-thread-rouge.log` : la réentrée du même objet et deux threads pouvaient perdre l'exclusion ; une garde d'instance non bloquante précède désormais l'ouverture et est libérée sur échec/sortie. `/tmp/seamtech-verrou-thread-vert2.log` **11 passed / 48 deselected / 1 xfailed** : tests lien, FIFO, lien dur, contenu existant, erreur d'acquisition puis reprise, deux processus, réentrée et course entre threads. Un xfail strict montre qu'un acteur pouvant supprimer/remplacer le fichier de verrou pendant l'opération peut encore contourner l'exclusion par changement d'inode ; conserver fichier/dossier en place, pas de garantie hostile même UID, Windows ou volume réseau. Le runbook ne prétend pas que la présence du fichier prouve une sauvegarde en cours.
- `/tmp/seamtech-verrou-final-pg16.log` **167 passed / 2 skipped / 5 xfailed** sur PG16 + S3rver ; le run précède un ajustement de test d'erreur d'acquisition, rejoué dans `-thread-vert2.log`. Les 2 skips nécessitent MinIO réel ; xfails : verrou remplacé, précondition ignorée S3rver, absence transaction multi-fichiers, écrasement objet existant, mutation au snapshot. Ruff global et `git diff --check` réussis. PG16 et S3rver isolés arrêtés.
- Prochaine action précise : sécuriser la restauration objet contre écrasement sur fournisseur réellement qualifié et traiter la reprise atomique multi-fichiers/PG/objets ainsi que les snapshots d'octets et la robustesse du verrou face au remplacement. MinIO/R2, Windows, endurance et CI exacte restent non qualifiés ; P0/P1/P2 **NON PRÊTS**, aucun commit/push/fusion/déploiement.

#### Verrouillage du dossier et interruptions

- ECC : `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop`. `/tmp/seamtech-verrou-inode-rouge.log` reproduit le contournement par suppression du fichier verrouillé. Le nouveau code verrouille maintenant d'abord le descripteur du dossier puis le fichier `sauvegarde.lock` ouvert relativement à celui-ci, sans suivre de lien : entre processus de cette version sur Linux local, retirer le fichier ne libère pas le verrou de dossier. Le verrou du fichier est conservé pour la coexistence normale avec l'ancienne version. Le test strict initial n'est plus xfail. L'ouverture du dossier lui-même refuse un lien final et les deux descripteurs sont fermés même si l'acquisition est interrompue. `/tmp/seamtech-verrou-interruption-rouge.log` a montré une fuite sur `KeyboardInterrupt`, corrigée avec nettoyage sur `BaseException` et propagation de l'interruption.
- `/tmp/seamtech-verrou-double-pannes.log` **17 passed / 48 deselected / 2 xfailed** avant les deux tests interruption ; `/tmp/seamtech-verrou-interruption-vert.log` **19 passed / 48 deselected / 2 xfailed**. Tests : suppression du fichier pendant verrou détenu, refus interprocessus, compatibilité ancienne version sans suppression, SIGKILL du détenteur puis acquisition, erreur d'ouverture, interruption avant et après acquisition du dossier, symlink. Les deux xfails ajoutés montrent que remplacer le dossier complet, ou retirer le fichier alors qu'une ancienne version ne verrouille que ce fichier, contourne toujours l'exclusion. Ne pas toucher au fichier/dossier lors de la sauvegarde et ne pas mélanger versions durant migration ; Windows, volumes réseau et coupure électrique restent non qualifiés.
- `/tmp/seamtech-verrou-double-pg16-final.log` **175 passed / 2 skipped / 6 xfailed** sur PG16 + S3rver, inclut les deux tests interruption ; les skips exigent MinIO réel. Xfails : incompatibilité mélange de versions avec suppression, remplacement du dossier, absence transaction multi-fichiers, écrasement objet, précondition S3rver ignorée et mutation des octets au snapshot. Services isolés arrêtés. Ruff global et `git diff --check` réussis après ce checkpoint.
- Prochaine action précise : sécuriser la publication objet sans écrasement sur fournisseur qualifié, reprendre transaction/reprise PG-fichiers-objets et snapshot des octets, puis OCR/rotation/matrice/endurance ; préserver les limites du verrou et la compatibilité de déploiement. P0/P1/P2 **NON PRÊTS**, CI exacte non exécutée, aucun commit/push/fusion/déploiement.

#### Prévalidation de restauration PostgreSQL

- ECC `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop`. `/tmp/seamtech-restore-binaire-rouge.log` montrait que la création de base précédait la recherche de `pg_restore`, ce qui pouvait laisser une base vide si le binaire était absent. Recherche du binaire et préparation de son environnement déplacées avant toute connexion ; les chemins de repli écartent les fichiers non exécutables ou non ordinaires (`/tmp/seamtech-restore-executable-rouge.log`). La recherche PATH conserve son ordre historique. Les vérifications de présence et de droits ne garantissent pas que le programme pourra effectivement s'exécuter, ni la compatibilité de version, ni l'absence de substitution après contrôle : un échec plus tardif peut toujours laisser une base partielle.
- `/tmp/seamtech-restore-manifest-taille-rouge.log` reproduit une connexion malgré une taille de dump manquante, booléenne, négative, textuelle ou incohérente. Après vérification du SHA et de la cible, la taille du dump privé doit être un entier naturel égal à la taille du fichier, avant connexion ; un manifeste sans taille est désormais refusé. Tests PG16 réels : l'absence de binaire et la taille incohérente ne créent pas la base cible. Un test existant de secret a reçu la taille valide attendue sans réduire ses assertions.
- `/tmp/seamtech-restore-preflight-pg16-final.log` **143 passed / 2 skipped / 5 xfailed** sur PG16 isolé, sans S3rver réseau : les skips concernent des services absents, les xfails sont les limites connues (double verrou, publication locale partielle, écrasement objet, mutation au snapshot). Ruff global et `git diff --check` réussis ; PostgreSQL arrêté. Les preuves précédentes S3rver ne sont pas rejouées dans cette sélection. CI exacte non exécutée, aucun commit/push/fusion/déploiement ; P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : éprouver l'échec à l'exécution et les versions incompatibles de `pg_restore` avant création, sans prétendre éliminer toutes les courses ; concevoir la reprise sûre des bases partielles et publications locales/objets, obtenir un MinIO isolé pour qualifier la protection contre l'écrasement, puis poursuivre snapshot coordonné, OCR/rotations, matrice et endurance.

#### Contrôle du dump avant création et limites de reprise

- ECC `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop` : `/tmp/seamtech-archive-preflight-rouge.log` démontrait la connexion avant constat d'un binaire inexécutable ou d'une archive incompatible. `pg_restore` lit maintenant l'archive avant connexion et génère le SQL vers une sortie supprimée, sans exécuter de SQL ni publier les diagnostics ou le contenu. Refus explicite et expurgé si l'exécution est impossible ou échoue. Tests PG16 : archive invalide et version d'archive délibérément trop récente refusées sans création de base (`/tmp/seamtech-archive-version-pg16.log`).
- Un premier essai avec `pg_restore --list` était insuffisant : `/tmp/seamtech-archive-corps-tronque-rouge.log` montre une archive synthétique dont la liste passe mais dont les données tronquées laissent une base partielle. Le contrôle lit désormais aussi les blocs de données en générant tout le SQL vers `/dev/null` ; `/tmp/seamtech-archive-corps-tronque-vert.log` **7 passed** sur PG16, y compris ce cas qui ne crée plus de base. Ce contrôle n'exécute pas les instructions, n'assure pas le succès sur la base cible, ne protège pas contre une modification ultérieure par le même UID et n'est pas une transaction. Il ajoute une lecture/décompression, sans limite de temps ; temps et ressources à qualifier à l'échelle réelle. Le runbook expose ces limites.
- Si le lancement réel après création échoue, l'exception OS est maintenant expurgée et signale que la base peut être partielle ; les erreurs de retour le signalent aussi. Aucun `DROP DATABASE` automatique risqué n'est réalisé. `/tmp/seamtech-restore-exec-rouge.log` montre l'exception non traitée avant correctif. Un échec SQL, une interruption ou une erreur réseau peut toujours laisser une base partielle ; rollback et reprise coordonnés restent à concevoir et éprouver.
- `/tmp/seamtech-archive-preflight-pg16-s3-final.log` **150 passed / 2 skipped / 6 xfailed** avec PG16 et S3rver ; skips MinIO réel, xfails : deux limites du verrou, précondition S3rver ignorée, publication locale partielle, écrasement objet, mutation des octets au snapshot. Services isolés arrêtés. Ruff global et `git diff --check` réussis ; pas de CI exacte, commit, push, fusion ou déploiement. P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : sécuriser de bout en bout la reprise des bases partielles sans jamais supprimer une base non possédée et coordonner les publications PG/fichiers/objets ; qualifier MinIO réel et sa non-écriture conditionnelle avant de modifier les objets ; traiter ensuite snapshot d'octets, OCR/rotation, matrice et endurance. Évaluer le coût du nouveau contrôle avant d'en faire une garantie sur des archives volumineuses.

#### Confinement du dump local de restauration

- ECC `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop` : contrairement au dump distant, le repli CLI local acceptait un nom du manifeste contenant chemin absolu ou traversée et le transmettait comme source de restauration. `/tmp/seamtech-dump-chemin-rouge.log` **7 failed** avant correctif. Le repli et le téléchargement distant partagent désormais la validation d'un nom de fichier simple (refus chemin, séparateurs, NUL, vide, types invalides et `.`/`..`). `--dump` reste un choix explicite indépendant du nom du manifeste, couvert par test.
- L'ancien staging du dump protégeait seulement son dernier composant : un répertoire parent symbolique pouvait donc être suivi vers l'extérieur. `/tmp/seamtech-dump-parent-rouge.log` le reproduit. La copie utilise désormais le même parcours par descripteurs et `O_NOFOLLOW` sur chaque composant que le bundle, en refusant les liens et fichiers non ordinaires. Tests : parent lien existant, substitution du parent juste avant son ouverture, refus avant création d'une base PG réelle, nettoyage, FIFO et droits du staging ; le runbook précise cette restriction de compatibilité. Cela ne prouve pas l'authenticité du manifeste, ne protège pas des mutations par même UID et ne qualifie ni Windows ni volumes réseau.
- `/tmp/seamtech-dump-parent-pg16-s3.log` **164 passed / 2 skipped / 6 xfailed** avec PG16 et S3rver. Skips MinIO réel ; xfails inchangés : deux limites de verrou, précondition ignorée S3rver, publication locale partielle, écrasement objet et mutation au snapshot. Services arrêtés. Ruff global et `git diff --check` réussis ; CI exacte non exécutée ; aucun commit, push, fusion ou déploiement. P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : concevoir et tester la reprise des bases partielles avec preuve durable de propriété avant toute suppression, la transaction/reprise PG-fichiers-objets et la protection sans écrasement sur MinIO réel, puis snapshots d'octets et reste P1/P2. Ne pas confondre ces protections de lecture avec une restauration complète et coordonnée.

#### Publication locale d'un fichier restauré

- ECC `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop` : `/tmp/seamtech-publication-atomique-rouge.log` **2 failed** montre que le nom final était visible pendant une écriture partielle et qu'une interruption `KeyboardInterrupt` laissait ce fichier partiel. La copie est désormais écrite dans un nom temporaire aléatoire créé exclusivement dans le dossier de destination, puis publiée par `link` sans remplacement ; nettoyage du temporaire également lors d'une interruption gérée, après vérification d'identité. Les répertoires sont toujours ouverts sans suivre les liens. Les plateformes/filesystems ne supportant pas ces opérations échouent fermés.
- `/tmp/seamtech-publication-atomique-final-unit.log` **6 passed / 49 deselected** : aucune visibilité du nom final pendant copie, interruption, cible concurrente préservée, deux processus concurrents (un gagnant), SIGKILL en cours d'écriture (nom final absent, temporaire restant), refus si liens durs indisponibles. Le runbook documente les fichiers `.seamtech-restore-*` pouvant subsister après arrêt brutal. Cette preuve porte sur les conditions testées sur le filesystem local Linux ; pas de durabilité après coupure électrique, aucun contrôle hostile des répertoires ou du même UID, Windows et volumes réseau non qualifiés. Le nettoyage du temporaire est au mieux en cas d'erreur et ne doit pas être lancé pendant une restauration active.
- La publication de **plusieurs** fichiers demeure non transactionnelle (xfail strict conservé), ainsi que la coordination PG/fichiers/objets ; cette modification ne résout pas l'écrasement objet et ne prouve pas une restauration globale. `/tmp/seamtech-publication-atomique-pg16-s3.log` **170 passed / 2 skipped / 6 xfailed** avec PG16 et S3rver ; skips MinIO, xfails inchangés : deux limites de verrou, non-transaction multifichier, écrasement objet, précondition ignorée S3rver, mutation au snapshot. Services arrêtés ; CI exacte non exécutée, aucun commit/push/fusion/déploiement ; P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : concevoir une publication multi-fichiers avec journal durable et preuve d'appartenance avant tout rollback, puis coordination avec la base partielle et les objets. Obtenir MinIO isolé et qualifier ses préconditions sans écraser de données ; traiter le snapshot d'octets, OCR/rotation et toutes les portes P1/P2. Ne pas extrapoler l'atomicité d'un seul nom à une transaction globale.

#### Relecture du fichier local avant publication

- ECC `search-first`, `tdd-workflow`, `security-review`, `verification-loop` : `/tmp/seamtech-fichier-relecture-rouge.log` démontre que la restauration déclarait un fichier restauré avec l'empreinte attendue alors que la copie finale avait été silencieusement tronquée. Avant publication, le fichier temporaire de destination est maintenant vidé des tampons, relu par son descripteur ouvert et son SHA-256 comparé à l'empreinte vérifiée du manifeste. En cas d'écart, aucune cible finale n'est publiée et le temporaire est nettoyé au mieux. Le résumé ne déclare plus la réussite sur la base de la seule empreinte source.
- `/tmp/seamtech-fichier-relecture-vert.log` **4 passed** : troncature simulée, mutation de la copie privée après staging, fichier vide, fichier dépassant un bloc de 1 Mio. Les tests existants d'interruption, SIGKILL et concurrence ont été conservés et adaptés à l'empreinte obligatoire. La relecture ne prouve que ce que le système renvoie à cet instant : cache, perte d'alimentation, altération ultérieure, acteur du même UID et authenticité du manifeste restent hors garantie. Coût de relecture et volumes réels non qualifiés ; runbook actualisé.
- `/tmp/seamtech-fichier-relecture-pg16-s3.log` **174 passed / 2 skipped / 6 xfailed** PG16/S3rver. Les deux skips nécessitent MinIO ; xfails : deux limites du verrou, non-transaction multifichier, écrasement objet, précondition ignorée S3rver et mutation au snapshot. Services arrêtés. CI exacte non exécutée, aucun commit/push/fusion/déploiement. P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : concevoir un journal de reprise durable et une stratégie non destructive pour restaurations PG/fichiers/objets, en considérant explicitement les interruptions et courses ; qualifier un MinIO isolé avant toute garantie d'écriture sans écrasement. Ensuite traiter snapshot coordonné des octets, OCR exact, rotations, matrice, sécurité et endurance.

#### Cohérence des clés objets répétées

- ECC `search-first`, `tdd-workflow`, `security-review`, `verification-loop` : `/tmp/seamtech-cle-objet-dupliquee-rouge.log` **2 failed**. Un manifeste pouvait associer deux contenus différents à une même clé : la restauration envoyait les deux successivement, écrasait le premier et renvoyait deux succès incohérents. À l'inverse, plusieurs documents peuvent légitimement référencer une même clé et une même empreinte.
- La planification refuse maintenant toute clé répétée avec empreintes différentes avant écriture et avant la création PG par la CLI. Des références identiques restent acceptées ; la clé physique n'est envoyée et relue qu'une fois, tandis que le résumé conserve le nombre de références. Validation défensive aussi au point de publication. `/tmp/seamtech-cle-objet-dupliquee-vert.log` **3 passed** : conflit avant tout envoi, déduplication de références identiques, CLI sans création PG ni publication locale en cas de conflit. Test réseau séparé sur bucket synthétique UUID S3rver : deux références, un seul envoi, relecture identique et suppression du seul bucket de test.
- `/tmp/seamtech-cle-objet-dupliquee-pg16-s3.log` **178 passed / 2 skipped / 6 xfailed** sur PG16 et S3rver ; skips MinIO ; xfails inchangés : deux limites du verrou, publication locale multifichier partielle, écrasement objet existant, précondition ignorée S3rver, mutation d'octets au snapshot. Services arrêtés ; runbook actualisé. Ces tests ne démontrent ni non-écrasement d'une clé préexistante, ni protection contre un écrivain externe, ni atomicité PG/fichiers/objets. MinIO/R2, CI exacte et production non qualifiés ; P0/P1/P2 **NON PRÊTS**, pas de commit/push/fusion/déploiement.
- Prochaine action précise : concevoir une reprise durable et non destructive des restaurations multi-composants, vérifier possession avant tout rollback, et obtenir MinIO isolé pour tester les préconditions ; puis résoudre snapshot d'octets, OCR exact et rotations, compléter matrice de sécurité et endurance.

#### Vérification des copies locales de sauvegarde

- ECC `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop` : `/tmp/seamtech-copie-backup-rouge.log` **2 failed** démontre que `copier_fichiers_locaux` pouvait déclarer une copie silencieusement tronquée comme valide et écraser une cible déjà présente. La copie réutilise désormais la publication locale exclusive confinée, avec relecture SHA-256 avant publication ; la source est hachée avant et après et une différence interrompt la sauvegarde. Aucun contenu de source n'est volontairement modifié. La copie n'utilise plus `copy2` et n'a pas de garantie de conservation des métadonnées du fichier original ; le contrat validé ici porte sur les octets.
- `/tmp/seamtech-copie-backup-vert.log` **3 passed / 14 deselected** : troncature, cible existante préservée et mutation de la source après lecture. Un test PG16 réel confirme l'absence de manifeste si la copie est tronquée, original intact. Une copie déjà publiée peut subsister sans manifeste si la source change après publication ; un arrêt brutal peut laisser des temporaires. Pas de suppression automatique risquée. La lecture de la source suit toujours les liens ; la protection contre une substitution hostile de source et la compatibilité de ce changement avec des chemins Windows/volumes réseau restent à qualifier. Le mécanisme POSIX requis échoue fermé s'il n'est pas disponible, et sa surcharge sur le volume réel n'a pas été mesurée.
- `/tmp/seamtech-copie-backup-pg16-s3.log` **182 passed / 2 skipped / 6 xfailed**, PG16 et S3rver. Les deux skips exigent MinIO, les xfails restent les deux limites de verrou, publication multifichier partielle, écrasement objet, précondition ignorée par S3rver et octets modifiés entre snapshot PG et copie. La double empreinte ne fige PAS les octets au snapshot PG : cette limite P0 demeure. Services isolés arrêtés ; runbook actualisé ; CI exacte non exécutée ; aucun commit/push/fusion/déploiement, P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : traiter confinement/identité de la source de sauvegarde sans prétendre fournir un snapshot, puis définir une transaction/reprise non destructive PG/fichiers/objets et qualifier MinIO réel avant tout envoi conditionnel ; poursuivre OCR/rotations, sécurité et endurance.

#### Confinement des sources locales de sauvegarde

- ECC `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `verification-loop` : `/tmp/seamtech-source-backup-lien-rouge.log` **2 failed** montrait le suivi d'un lien final ou parent pendant une copie de sauvegarde. La source est maintenant ouverte par descripteurs `O_NOFOLLOW`, hachée, copiée depuis ce même descripteur puis rehachée ; une réouverture confinée vérifie son identité `(dev, inode)` après copie. `/tmp/seamtech-source-backup-vert.log` **4 passed** : deux liens refusés et substitution après ouverture par lien ou fichier distinct de même contenu détectée. Un test PG16 réel confirme le refus sans manifeste après substitution. Une copie déjà publiée peut subsister sans manifeste si l'identité change après publication ; aucune suppression automatique de fichier tiers n'est tentée.
- `/tmp/seamtech-source-abspath-rouge.log` et `/tmp/seamtech-dump-abspath-rouge.log` ont démontré qu'une normalisation `abspath` d'un chemin contenant lien puis `..` pouvait silencieusement changer le fichier effectivement lu. Les chemins de source sauvegarde et de dump contenant `..` sont maintenant refusés avant normalisation ; `/tmp/seamtech-abspath-securite-vert.log` **2 passed**. Restriction de compatibilité y compris `--dump` explicite, indiquée dans le runbook. Les autres chemins et constructions de destination ne sont pas ainsi entièrement audités.
- `/tmp/seamtech-source-confinement-pg16-s3-final.log` **189 passed / 2 skipped / 6 xfailed** sur PG16/S3rver. Skips MinIO ; xfails inchangés : deux limites de verrou, publication multifichier, écrasement objet existant, précondition S3rver ignorée, mutation au snapshot. Services arrêtés ; runbook mis à jour. Ces protections ne constituent ni un snapshot des octets, ni une preuve contre tout acteur du même UID, ni une qualification Windows, filesystem réseau, volume réel ou CI exacte. P0/P1/P2 **NON PRÊTS**, aucun commit/push/fusion/déploiement.
- Prochaine action précise : auditer les constructions de destinations et les téléchargements d'objets du bundle sauvegardé pour non-écrasement et substitution de chemin, puis concevoir la reprise non destructive multi-composants et qualifier MinIO isolé ; poursuivre OCR/rotation, sécurité et endurance.

#### Téléchargement des objets dans le bundle de sauvegarde

- ECC `search-first`, `tdd-workflow`, `security-review`, `verification-loop` : `/tmp/seamtech-objet-backup-chemin-rouge.log` **3 failed** montre que le téléchargement direct pouvait écraser une copie préexistante, suivre un dossier substitué vers un témoin tiers, et laisser un fichier partiel après erreur réseau. L'objet est maintenant téléchargé d'abord dans un répertoire temporaire privé, ouvert sans suivi de lien et copié avec relecture SHA vers le bundle par publication exclusive confinée. La source distante n'est pas supprimée. Une erreur de téléchargement reste signalée comme objet manquant ; un échec de staging/publication interrompt la sauvegarde.
- `/tmp/seamtech-objet-temp-rouge.log` a aussi révélé qu'un échec d'accès au répertoire temporaire divulguait son chemin ; erreur expurgée. `/tmp/seamtech-objet-temp-vert.log` **5 passed / 22 deselected**, y compris interruption `KeyboardInterrupt` et nettoyage normal du staging. Test réseau isolé sur S3rver : objet synthétique lu, copie et empreinte vérifiées, original toujours présent, puis seul bucket UUID de test nettoyé. `SEAMTECH_SAUVEGARDE_TMP` doit avoir l'espace nécessaire ; un arrêt brutal peut laisser un répertoire temporaire, et les protections contre acteur du même compte, coupure électrique, Windows et volumes réseau ne sont pas qualifiées.
- `/tmp/seamtech-objet-backup-pg16-s3.log` **195 passed / 2 skipped / 6 xfailed** avec PG16/S3rver. Skips MinIO ; xfails : deux limites de verrou, non-transaction multifichier, écrasement objet, précondition ignorée S3rver et mutation au snapshot. Services arrêtés ; Ruff global et `git diff --check` réussis ; runbook actualisé. Le SHA calculé sur le téléchargement n'est pas une preuve que le fournisseur a livré tous les octets de l'objet source ni une version coordonnée avec le snapshot PG ; ce risque demeure. CI exacte non exécutée ; P0/P1/P2 **NON PRÊTS**, aucun commit/push/fusion/déploiement.
- Prochaine action précise : comparer de manière fiable le téléchargement objet à une identité/version/checksum fournisseur qualifié et tester les erreurs ambiguës ; obtenir MinIO isolé pour qualifier les préconditions atomiques. Poursuivre en parallèle conception journal/reprise non destructive PG/fichiers/objets et snapshot cohérent, puis OCR/rotation, sécurité et endurance.

#### Contrôle des métadonnées de l'objet source

- ECC `search-first`, `tdd-workflow`, `security-review`, `verification-loop` : `/tmp/seamtech-head-backup-rouge.log` **3 failed** démontrait l'acceptation d'un téléchargement tronqué, d'un changement d'identité à la source et d'un client sans métadonnées. La copie objet exige maintenant un `HEAD` avant et après transfert : taille entière non négative, ETag non vide traité comme identifiant opaque, version si disponible ; absence, erreur, changement de taille/ETag/version ou longueur téléchargée différente rend l'objet manquant et interdit sa publication. Permissions HEAD et réponses de fournisseur deviennent nécessaires ; MinIO/R2 n'ont pas été qualifiés.
- `/tmp/seamtech-head-backup-vert.log` **11 passed / 26 deselected / 1 xfailed** : troncature, changement ETag ou version, erreurs de contrôle, types/valeurs invalides et absence de `HEAD`. Les doubles précédents ont été munis de métadonnées compatibles sans retirer leurs assertions. Un xfail strict supplémentaire et `/tmp/seamtech-head-meme-taille-rouge.log` démontrent qu'une corruption de même taille avec ETag inchangé reste acceptée : ETag n'est PAS interprété comme SHA/MD5 et la double lecture des métadonnées ne prouve ni intégrité ni snapshot coordonné. Aucun `VersionId` n'est imposé au téléchargement ; les courses ABA et fournisseurs non qualifiés restent ouvertes.
- `/tmp/seamtech-storage-secret-head-rouge.log` reproduisait la fuite de clé et d'erreur synthétique dans les logs/exceptions de `head_object` et `download_file` ; ces deux méthodes signalent désormais uniquement le type d'erreur et une exception générique sans chaîne explicite. `/tmp/seamtech-storage-secret-head-vert.log` **3 passed / 26 deselected**. Cela n'est pas un audit de tous les autres logs S3/API.
- `/tmp/seamtech-head-backup-pg16-s3-final.log` **233 passed / 3 skipped / 7 xfailed / 1 warning** (PG16, S3rver, stockage et fichiers API). Xfails : deux limites de verrou, non-transaction multifichier, écrasement objet, précondition S3rver ignorée, mutation des octets au snapshot et corruption objet même taille/ETag. Les skips concernent les services externes absents ; services isolés arrêtés. Ruff global et `git diff --check` à rejouer après ce checkpoint. CI exacte non exécutée, pas de commit/push/fusion/déploiement ; P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : qualifier et utiliser une preuve de contenu indépendante de l'ETag (checksum serveur FULL_OBJECT fiable ou lecture/version immuable qualifiée sur MinIO/R2), traiter intégrité et ambiguïté des échecs sans prétendre qu'un double HEAD est une preuve, puis transaction/reprise PG-fichiers-objets, snapshot d'octets, OCR/rotations et autres portes.

### Qualification limitée d'un MinIO historique (2023)

- Compétences ECC appliquées : `search-first`, `security-review`, `tdd-workflow`, `verification-loop`. Le binaire isolé téléchargé depuis un miroir a été authentifié **avant exécution** : l'API GitHub de la release officielle `minio/minio` `RELEASE.2023-08-23T10-07-06Z` donne 98 934 784 octets et SHA-256 `4741cb61cbcd5fa2c1d63f3e7c511e6e8ba4a93e448fb9a4c29e754ed2923dff`, identiques au fichier. Version affichée et commit `af564b8ba07a7805a578b5f6f2b3827490f74ccd`. Cette ancienne version/Go 1.19.12 n'est pas une recommandation de déploiement ; serveur temporaire en loopback, credentials et objets synthétiques, aucune donnée réelle. Processus arrêté après essais et buckets vidés/supprimés, y compris leurs versions et delete markers.
- `tests/test_minio_historique.py` : banc réseau opt-in explicite `SEAMTECH_TEST_MINIO_2023_URL` (HTTP sur 127.0.0.1 uniquement) et identifiants synthétiques `SEAMTECH_TEST_MINIO_2023_ACCESS` / `SEAMTECH_TEST_MINIO_2023_SECRET`, bucket UUID dédié et nettoyage des versions. Avec le test réseau existant de stockage, `/tmp/seamtech-minio-2023-suite-final.log` : **4 passed, 12 deselected, 1 xfailed**. Ruff et `git diff --check` verts. Le test conditionnel est strictement xfail pour cette version historique : l'enlever seulement après correction et nouvelle preuve, ne pas l'interpréter comme un succès.
- Résultat réel important : sur bucket non versionné, deux PUT `IfNoneMatch='*'` ont tous deux répondu 200 et le second a écrasé le premier (`/tmp/seamtech-minio-2023-condition-sonde.log`). Ne pas utiliser ce mécanisme sans qualification fournisseur/version ni présenter un HEAD préalable comme protection atomique. Test de versioning activé : deux identifiants de version distincts, HEAD courant et GET de l'ancienne version vérifiés ; **notre sauvegarde ne télécharge pas encore en épinglant VersionId**, ce test ne corrige pas la course.
- SHA-256 d'un PUT simple restitué par HEAD avec `ChecksumMode=ENABLED`, mais `ChecksumType` absent ; sur multipart 6 Mio, HEAD ne restitue pas de SHA-256, tandis que la réponse Complete renvoie un checksum composite suffixé `-2`, différent du SHA-256 des octets complets (`/tmp/seamtech-minio-2023-checksum-sonde.log`, `/tmp/seamtech-minio-2023-multipart-sonde.log`). Ne pas traiter ETag ni checksum composite comme une empreinte complète. Une copie objet de sauvegarde et une relecture fonctionnent pour le cas simple testé, sans prouver l'intégrité d'une source déjà corrompue ou un snapshot coordonné.
- Cela ne qualifie **ni MinIO actuel, ni R2, ni des versions/configurations ou scénarios concurrents différents**. Prochaine action : concevoir/tester fail-closed la protection de la restauration d'objets préexistants et concurrents sur les fournisseurs réellement qualifiés, ou refuser explicitement ce mode lorsqu'aucune primitive fiable n'est disponible ; poursuivre snapshot coordonné, OCR, rotations, matrice et endurance. P0/P1/P2 restent NON PRÊTS ; aucune CI verte sur commit exact, aucun commit/push/fusion/déploiement dans cet incrément.

### Épinglage de version au téléchargement de sauvegarde

- ECC `tdd-workflow`, `security-review`, `search-first`, `verification-loop` : `/tmp/seamtech-version-aba-rouge.log` **2 failed / 1 passed** avant correction, révélant qu'un changement de source suivi d'un retour à l'identité initiale pouvait faire accepter des octets intermédiaires et qu'un client incapable d'épingler la version était néanmoins accepté. `S3StorageClient.download_file` accepte maintenant `version_id` optionnel et le transmet à boto3 `ExtraArgs.VersionId` ; `copier_objets_s3` impose la version non `null` observée avant transfert, sans repli si le client/serveur la refuse. Le HEAD final et le contrôle de taille restent obligatoires. La version `null` S3 est mutable (versioning non actif/suspendu) et ne fait l'objet d'aucune prétention d'immuabilité.
- Nouveau test réseau sur MinIO historique authentifié : insertion d'une nouvelle version entre le HEAD et le GET, puis suppression de cette nouvelle version avant le HEAD final (ABA réel) ; la copie contient la version initiale, et non la version intermédiaire. Tests unitaires de transmission VersionId, absence de repli, version `null`, et vérification que le test de changement de version effectue bien les deux HEAD. `/tmp/seamtech-version-aba-minio-unit.log` **54 passed / 4 skipped / 2 xfailed** ; `/tmp/seamtech-version-aba-pg16-minio-s3.log` **225 passed / 3 skipped / 8 xfailed** sur PG16.14, MinIO historique et S3rver isolés. Ruff global et `git diff --check` verts ; services arrêtés. Le runbook est actualisé.
- Limites : ceci suppose un fournisseur respectant VersionId et la conservation de la version ; ni R2 ni MinIO actuel ne sont qualifiés. Version `null`/absence de version ne protège pas de l'ABA ; corruption même taille reste possible et xfail, pas de checksum complet fiable ; l'épinglage au HEAD de copie ne relie pas les octets au snapshot PG. Si la version est supprimée ou que les permissions GET par version manquent, l'objet est manquant. La restauration objet peut toujours écraser une clé et les composants ne sont pas transactionnels. P0/P1/P2 **NON PRÊTS** ; aucune CI sur commit exact, aucun commit/push/fusion/déploiement.
- Prochaine action précise : traiter fail-closed la restauration objet non destructive et ses précontrôles avant modification PG/fichiers, en préservant les tests fonctionnels existants seulement pour un contrat fournisseur réellement prouvé ; poursuivre snapshot coordonné, OCR, rotations et autres portes sans faire passer une preuve MinIO 2023 pour une qualification R2 ou actuelle.

### Échec ambigu et expurgation des opérations S3

- ECC `security-review`, `tdd-workflow`, `error-handling`, `verification-loop` : `/tmp/seamtech-s3-repli-ambigu-rouge.log` **2 failed** : les deux envois applicatifs recommençaient sans checksum après **toute** exception, y compris une réponse perdue après écriture. Ils ne font plus ce repli ; l'erreur ne révèle ni exception tierce, ni nom de fichier ou clé, et indique un état distant ambigu. Une opération ayant déjà écrit peut toutefois laisser un objet ; les réessais internes boto3/botocore restent possibles, la recherche HEAD de clé libre n'est pas atomique, et aucune garantie de non-écrasement concurrent n'est revendiquée. Les fournisseurs sans checksum d'ingestion doivent maintenant être qualifiés ou échouent ; le runbook mentionne cette incompatibilité potentielle.
- `/tmp/seamtech-s3-lecture-secret-rouge.log` **5 failed** : une exception tierce contenant un secret synthétique et/ou une clé était reflétée par signature d'URL, lecture d'objet, liste, suppression et détail de relecture. Les messages et journaux de ces chemins sont génériques, sans chaîne explicite ; test plus strict de journalisation sur succès (ni nom ni clé). `/tmp/seamtech-s3-erreurs-vert.log` **8 passed / 36 deselected**. Les autres chemins S3, métadonnées et erreurs tierces restent à auditer, et les logs ne doivent jamais servir à exposer de nouvelles données clients.
- `/tmp/seamtech-s3-repli-erreurs-reseau-final.log` **233 passed / 3 skipped / 7 xfailed**, tests unitaires et réseaux S3rver/MinIO historique, sans PostgreSQL. `/tmp/seamtech-minio-checksum-envoi.log` **5 passed / 1 xfailed**, dont PUT simple et upload multipart 9 Mio réels avec checksum sur MinIO historique, téléchargement et SHA-256 vérifiés. Cela ne qualifie ni R2 ni MinIO actuel ; les tests de défaut utilisent des doubles et ne prouvent pas toutes pannes réseau. Buckets de test vidés (versions comprises), serveurs arrêtés ; Ruff global et `git diff --check` verts. Aucune CI sur commit exact, aucun commit/push/fusion/déploiement ; P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : auditer les réessais SDK lors des mutations et l'écriture en concurrence (y compris précondition non respectée) ; concevoir une restauration objet effectivement fail-closed avant PG/fichiers, qualifier fournisseur/version et préserver reprise. Poursuivre en parallèle OCR, rotation, snapshot coordonné, matrice et endurance.

### Réessai SDK et isolation des doubles S3

- ECC `search-first`, `tdd-workflow`, `security-review`, `error-handling`, `verification-loop` : `/tmp/seamtech-s3-sdk-reessai-rouge.log` démontre sur serveur HTTP loopback qu'après un PUT accepté mais dont la réponse est perdue, botocore réémettait le PUT et écrasait une écriture concurrente synthétique. Le client S3 partagé est désormais configuré `total_max_attempts=1` ; sur le même banc, les PUT d'octets et de fichier ne sont émis qu'une fois et remontent `StorageError`, et un UploadPart multipart perdu n'est pas réémis (`/tmp/seamtech-s3-sdk-reessai-multipart.log` **3 passed**). La sonde de versioning est un autre client et garde ses réessais de lecture.
- Compromis explicite : le client partagé ne réessaie plus automatiquement **aucune opération botocore**, donc les lectures peuvent échouer davantage en réseau instable ; état distant encore ambigu, réessais éventuels dans d'autres couches/appels et interruption de CompleteMultipartUpload restent à qualifier. Cela ne protège ni l'interstice HEAD → premier PUT ni les fournisseurs ignorants les préconditions : aucun non-écrasement atomique n'est prouvé. Le runbook reflète ces limites et ne doit pas être utilisé comme procédure de reprise de production.
- `/tmp/seamtech-s3-diagnostic-rouge.log` **8 failed** : initialisation, sonde, bucket, versioning, occupation et erreurs de clé reflétaient des valeurs libres ou une clé ; expurgation ciblée. Un état de versioning non reconnu est désormais `None` et non faussement `False`. `/tmp/seamtech-s3-diagnostic-vert.log` **8 passed**. Autres messages et données retournées par tous les parcours ne sont pas entièrement audités.
- `/tmp/seamtech-s3-sdk-pg16-s3-minio-final.log` a d'abord échoué **6 tests** : la suite `test_verification_integrite.py` laissait des patches de classe actifs après chaque test et détournait les tests réseau ultérieurs vers S3 en mémoire. Patches convertis en `monkeypatch` de portée test, assertion de restauration ajoutée ; les assertions métier ne sont pas retirées. Même campagne rejouée réellement après correction : `/tmp/seamtech-s3-sdk-pg16-s3-minio-final2.log` **320 passed / 7 skipped / 8 xfailed / 1 warning** (PG16, S3rver et MinIO historique). Buckets synthétiques nettoyés, services arrêtés, Ruff global et `git diff --check` verts. Aucune CI sur commit exact, aucun commit/push/fusion/déploiement.
- P0/P1/P2 restent **NON PRÊTS** : restauration objet potentiellement destructive, sauvegarde non coordonnée, OCR/rotations, matrice, charge/endurance et qualifications non terminés. Prochaine action précise : test réseau d'échec ambigu à `CompleteMultipartUpload` et suppression externe, étude d'un contrat de création réellement atomique et refus de restaurer avant toute mutation PG/fichiers quand non prouvé ; poursuivre aussi OCR, rotations et reste des portes.

### Ambiguïté multipart et expurgation du téléversement

- ECC `tdd-workflow`, `security-review`, `error-handling`, `verification-loop` : nouveau serveur HTTP loopback synthétique simulant succès de `CompleteMultipartUpload` avec réponse perdue. `/tmp/seamtech-s3-completion-suppression.log` **5 passed** : un seul Complete émis et erreur remontée ; une suppression dont la réponse est perdue renvoie `False` alors que l'objet est déjà supprimé. La documentation de `delete_file` précise que `False` signifie résultat inconnu, jamais preuve de conservation. Ces simulations ne prouvent ni toutes pannes réseau ni absence de réessai par appelants, ni création atomique ou reprise de production.
- `/tmp/seamtech-promotion-secret-rouge.log` **1 failed** : la trace de promotion exposait le détail arbitraire d'une panne fournisseur malgré un message public générique. La cause explicite a été supprimée ; le test vérifie la trace formatée, l'absence de clés enregistrées et la conservation des sources synthétiques. `/tmp/seamtech-drop-secret-rouge.log` **2 failed** : les erreurs de lecture/écriture et de publication du téléversement exposaient un chemin synthétique dans message et trace. Les erreurs sont maintenant génériques, sans chaîne explicite ; `/tmp/seamtech-drop-secret-vert.log` **2 passed**, temporaires et manifeste contrôlés. Les autres chaînes d'exception et messages de chemin dans le module restent à auditer.
- Campagne `/tmp/seamtech-drop-s3-security-suite-final.log` **119 passed / 1 skipped / 1 warning** : banc HTTP synthétique, promotion, téléversement, idempotence et stockage ; pas de PG/MinIO dans ce lancement. Aucun test E2E navigateur ni CI exacte pour cet incrément. Aucun service persistant démarré, aucun commit/push/fusion/déploiement ; P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : concevoir un refus précoce et explicite de restauration objet lorsque la création exclusive n'est pas démontrée (avant toute écriture PG/fichiers), sans confondre test de compatibilité et preuve contre écrivain externe ; reprendre OCR et rotations avec préservation des coordonnées/tableaux, puis le reste de la campagne.

### Refus anticipé de restauration objet non exclusive

- ECC `search-first`, `security-review`, `tdd-workflow`, `contract-first`, `verification-loop` : `/tmp/seamtech-restaure-exclusive-rouge.log` **2 failed** établissait qu'un client sans primitive exclusive écrivait quand même, et que la CLI pouvait créer la base avant de découvrir l'absence de protection des objets. La restauration objet exige maintenant l'interface interne `creer_fichier_exclusif(source, cle)` sur la clé exacte ; la CLI la vérifie après les précontrôles et **avant** `restaurer` PostgreSQL ou publication de fichiers. Aucun HEAD suivi de PUT, ni fallback vers `upload_file`, n'est accepté. L'ancien xfail « clé existante écrasée » est maintenant un test de refus réussi.
- **Restriction fonctionnelle importante** : le `S3StorageClient` de production ne met pas en œuvre cette interface ; la restauration avec objets non vides est donc délibérément **indisponible** sur S3/MinIO/R2, même si le bucket est vide. Le mécanisme interne ne certifie pas les fournisseurs : une méthode de même nom pourrait mentir. Seul un adaptateur expressément développé, qualifié sur fournisseur/version/configuration, sous concurrence et échecs ambigus, pourra rétablir ce parcours. Le code générique, les tests et le runbook ne prétendent pas que le simple nom de méthode prouve l'atomicité. Un échec de relecture ou réseau peut encore laisser un objet, et la restauration multi-composants n'est pas transactionnelle.
- Tests sur adaptateurs purement simulés : source figée, vérification SHA, déduplication de clés identiques, refus de conflit et deux restaurations concurrentes (un succès, un refus). Deux tests réseau historiques qui attendaient l'envoi vers S3rver sont désormais **xfail stricts** jusqu'à disponibilité d'un fournisseur qualifié ; le test PG+MinIO de restauration complète est également xfail strict quand les services sont configurés. Aucun résultat positif n'a été inventé ni test de réussite remplacé par un simple HEAD. Tests réseau additionnels sur S3rver et MinIO historique : client réel refusé, clé synthétique existante conservée.
- `/tmp/seamtech-restaure-exclusive-pg16-s3-minio.log` **326 passed / 7 skipped / 9 xfailed / 1 warning**, avec PG16.14/S3rver/MinIO historique ; puis `/tmp/seamtech-restaure-exclusive-unite-2.log` **66 passed / 1 xfailed**, incluant un conflit via adaptateur simulé ajouté après la campagne. Buckets synthétiques vides, services arrêtés, Ruff et `git diff --check` verts. Pas de CI sur commit exact, pas de commit/push/fusion/déploiement ; P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : obtenir et authentifier un MinIO récent disposant d'une création conditionnelle réellement atomique, qualifier PUT/multipart, écriture concurrente, versioning et réponse perdue puis concevoir un adaptateur fail-closed ; ne pas utiliser MinIO 2023 ou S3rver comme preuve. Poursuivre snapshot PG/fichiers/objets, OCR navigateur, rotations/tableaux et les autres portes de la campagne.

### Confiance OCR décimale et recherche de MinIO récent

- ECC `search-first`, `tdd-workflow`, `security-review`, `verification-loop` : l'API GitHub officielle indique pour MinIO `RELEASE.2025-09-07T16-13-09Z` l'asset linux-amd64 110 989 496 octets, SHA-256 `7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f`. Le téléchargement officiel est redirigé vers un hôte non autorisé dans cette sandbox et n'a pas abouti ; aucun binaire récent tiers n'a été exécuté ni qualifié. `latest` `RELEASE.2025-10-15T17-29-55Z` n'a pas d'asset dans la release GitHub consultée. L'adaptateur d'écriture exclusive reste indisponible.
- `/tmp/seamtech-ocr-confiance-rouge.log` **2 failed / 3 passed** : l'analyseur TSV convertissait la confiance en entier alors que Tesseract renvoie habituellement des décimales ; elle était perdue, et des valeurs numériques invalides ou de lignes structurelles étaient acceptées. La confiance moyenne utilise maintenant les flottants finis entre 0 et 100 de lignes de mots non vides seulement ; aucune mesure valide donne `None` et le texte OCR n'est pas modifié. Tests automatisés : décimales, marqueurs -1, hors borne, NaN/infini, lignes sans mot, niveau non mot et TSV malformé. `/tmp/seamtech-ocr-confiance-final.log` **60 passed / 4 skipped**, dont nouveau test CLI réel sauté ici car binaire absent ; test prévu dans le job `ocr_suite` disposant de Tesseract. Ruff et `git diff --check` verts.
- Vérification complémentaire sur image synthétique avec `tesserocr==2.9.1` installé seulement dans `.venv` non persisté : moteur natif Tesseract 5.5.1 et données `fra` officielles `tessdata_fast` téléchargées dans `/tmp` (1 130 365 octets, SHA-256 `ced037562e8c80c13122dece28dd477d399af80911a28791a66a63ac1e3445ca`). `/tmp/seamtech-ocr-tsv-natif.log` : 4 mots, confiances décimales, moyenne exacte et bornée. Le TSV et texte réels du moteur ont été injectés dans le parseur via double subprocess : cela ne prouve **pas** le parcours CLI, navigateur ou CI. Aucun contenu client ni API IA externe.
- Cette correction de métrique ne corrige pas la confusion OCR O/0 ni les échecs aléatoires du navigateur ; aucune assertion n'a été affaiblie. P0/P1/P2 restent **NON PRÊTS** ; aucun service persistant, commit, push, fusion ou déploiement. Prochaine action : reconstruire ou obtenir un Tesseract CLI réel avec données `fra`/`eng` et répéter la campagne OCR navigateur sans retry ; en parallèle poursuivre la qualification d'un stockage récent authentifié, restaurations, rotations PDF et toutes les autres portes.

### Reproduction OCR avec CLI native et navigateur

- ECC `search-first`, `e2e-testing`, `tdd-workflow`, `verification-loop` : CLI Tesseract **5.5.1** construite dans `/tmp` à partir du code officiel `5.5.1`, liée aux bibliothèques du wheel `tesserocr==2.9.1` (Leptonica 1.85.0), avec données officielles `tessdata_fast` `fra` et `eng`, uniquement données synthétiques. Le test CLI réel de confiance `/tmp/seamtech-ocr-confiance-cli.log` donne **6 passed** (aucun skip), et vérifie la moyenne avec TSV réellement produit. Ce banc local n'est pas identique à l'Ubuntu/Tesseract de CI.
- Campagne 30 termes synthétiques déterministes, sans réessai, avec PDF image et pipeline réel : `/tmp/seamtech-ocr-aleatoire-551.log` **26 reconnus / 4 incorrects**. `/tmp/seamtech-ocr-benchmark-psm.log` : PSM 3/6/11 et agrandissement ×1/×2 ne réduisent pas les 4 erreurs en français ; `fra+eng` en élimine une mais laisse 3 erreurs (et une configuration en produit 4). Ne pas prendre ce changement de langue pour une correction ni remplacer silencieusement des lettres par des chiffres.
- Frontend reconstruit, Chromium 153 via package npm de test, PostgreSQL 16 isolé et Tesseract local : test navigateur de sélection fichier avec assertion exacte inchangée, **zéro retry**. `/tmp/seamtech-ocr-e2e-551-local.log` **5 passed**, puis campagne indépendante plus longue `/tmp/seamtech-ocr-e2e-551-25.log` **21 passed / 4 failed** sur 25 ; échecs observés à l'assertion de texte OCR brut exact (notamment confusion 0/O). Le premier vert ne prouve pas la stabilité. Stockage local, sans Redis, worker dans processus serveur ; S3rver lancé pour le premier essai mais secrets d'exemple courts refusés au démarrage, l'essai a ensuite été rejoué sans S3. Ce n'est donc ni une qualification MinIO/R2 ni celle de la pile de production complète.
- La base PG créée par ce run, identifiée explicitement par son registre et sans connexion active, a été supprimée ; registre correspondant supprimé, PostgreSQL et S3rver arrêtés. Les dépendances générées et navigateurs restent hors patch. Ruff global et `git diff --check` verts. Aucun commit/push/fusion/déploiement ; P0/P1/P2 restent **NON PRÊTS**.
- Prochaine action précise : concevoir une solution d'OCR vérifiable qui conserve la transcription fidèle et traite explicitement les ambiguïtés sans deviner ni affaiblir l'assertion E2E ; comparer sur corpus synthétique déterministe et nouvelles variantes sans réessai salvateur, puis poursuivre rotations PDF, sauvegardes coordonnées, stockage qualifié et autres portes.

### OCR : modèles best, expurgation ; tableaux PDF compensés

- ECC `search-first`, `tdd-workflow`, `security-review`, `verification-loop` : données officielles `tessdata_best` `fra` et `eng` comparées sur les 30 scans synthétiques déjà décrits, sans réessai : `/tmp/seamtech-ocr-benchmark-best.log` **3 erreurs/30** en `fra` et en `fra+eng`. Elles ne constituent donc pas une solution fiable à la transcription exacte ni à la CI. Aucune assertion du navigateur n'a été affaiblie, aucun alphabet n'a été modifié et aucune donnée client n'a été utilisée.
- `/tmp/seamtech-ocr-erreur-secret-rouge.log` **2 failed** démontrait la divulgation d'une chaîne synthétique par stderr de Tesseract et par message d'exception. `_ocr_image_via_tesseract` conserve le code retour ou le nom du type d'exception, mais n'expose plus ces chaînes tierces dans `motif`. `/tmp/seamtech-ocr-secret-suite.log` **62 passed / 4 skipped** ; ces tests ne représentent pas un audit intégral des messages et journaux OCR. Les causes techniques demeurent distinguables sans transmettre des chemins ou textes éventuels issus du moteur.
- Nouvelles spécifications PDF dans `tests/test_pdf_variantes.py` : pour 90/180/270 non compensés, ordre des lignes, tableaux et boîtes ramenées dans les coordonnées de la page effectivement affichée ; ces trois tests restent **xfail stricts**, en plus des trois xfails de texte antérieurs. Un second jeu contrôle trois rotations **déjà compensées**, avec texte, tableau et coordonnées initiales : ces tests verts empêcheront une correction naïve consistant à annuler systématiquement `/Rotate`. Dans ce jeu, `/tmp/seamtech-pdf-tableau-quasi-axe-rouge.log` **1 failed / 2 passed** révélait un tableau perdu à 270° : des traits horizontaux avaient une hauteur résiduelle d'environ `1e-13` point et pdfplumber les classait verticaux par égalité exacte.
- Correctif ciblé : normalisation en mémoire des seules arêtes de type ligne presque horizontales (tolérance `1e-7` point), sans déplacer les mots ni redresser une diagonale identifiable ; le test de frontière `/tmp/seamtech-pdf-axe-contrat-rouge.log` était rouge avant ajout. Les trois cas compensés passent maintenant ; cela **ne corrige pas** les rotations non compensées, ni toutes les tables ou coordonnées possibles. `/tmp/seamtech-pdf-tableau-axe-final.log` **73 passed / 4 skipped / 6 xfailed**. Campagne plus large avec CLI Tesseract native et données fast : `/tmp/seamtech-pdf-ocr-campagne-native.log` **212 passed / 7 skipped / 6 xfailed** (détection, gabarits, extraction, validation, OCR et PDF). Ruff global et `git diff --check` verts.
- P0/P1/P2 restent **NON PRÊTS** : restauration objet non vide indisponible, snapshot coordonné absent, OCR exact aléatoire, rotations non compensées non corrigées, autres portes inachevées ; aucune CI 100 % verte sur commit exact et aucun commit/push/fusion/déploiement. Services de la campagne précédente arrêtés ; aucun nouveau service persistant dans cet incrément.
- Prochaine action précise : concevoir puis tester une extraction des PDF pivotés qui distingue orientation compensée et non compensée, maintient un repère logique pour ordre/tableaux et restitue les boîtes en coordonnées affichées, sans casser les trois cas compensés verts ; retirer les six xfails uniquement après réussite des assertions réelles. Reprendre simultanément la solution générique d'ambiguïté OCR, l'adaptateur S3 réellement qualifié, le snapshot coordonné et les autres portes documentées.

### Rotations homogènes, repère PDF.js et couverture S3 en CI

- ECC appliquées : `search-first`, `tdd-workflow`, `architecture-decision-records`, `security-review`, `verification-loop`. `/tmp/seamtech-pdf-rotation-repere-rouge.log` : **6 failed / 11 passed**, correspondant aux trois rotations de texte et aux trois cas tableau/coordonnées. Les six xfails initiaux ont été retirés et les assertions sont devenues vertes dans les cas couverts : page à rotation 90/180/270, texte homogène et CropBox égal au MediaBox ; une page déjà lisible/compensée n'est pas retournée. `page_de_lecture` relit une copie privée de la page sans modifier le PDF, uniquement si tous les caractères non blancs ont une orientation compatible et si leurs boîtes projetées correspondent à celles du lecteur original ; sinon elle conserve le comportement précédent. Les règles métier utilisent le repère logique, et les zones de provenance utilisent le repère de la vue ; `CURRENT_EXTRACTOR_VERSION` passe de 6 à 7 pour réextraire l'index plein texte au prochain scan. Cela peut rendre le prochain scan plus coûteux ; cela ne réextrait pas automatiquement les fiches et zones déjà persistées.
- Tests supplémentaires : trois orientations compensées, tableaux et coordonnées, PDF multi-pages à rotations différentes et empreinte inchangée, origine MediaBox non nulle, absence de mutation des attributs page, 48 traces et 74 cibles du fixture déjà présent dans le dépôt sous chaque rotation. Le test sur le PDF de référence a d'abord été rouge sur un comptage incorrect (48 traces ne sont pas les 74 cibles), puis les deux comptes ont été vérifiés distinctement : `/tmp/seamtech-pdf-rotation-fiche-traces.log` **3 passed**. Aucune donnée cliente supplémentaire n'a été introduite.
- Contrôle indépendant avec le vrai module PDF.js installé dans le frontend sur des fichiers synthétiques : pour MediaBox décalé, les positions de texte à l'échelle 1 et les zones backend concordent à **0,9 point maximum** sur 0/90/180/270 (différence de métrique de fonte) ; ce n'est pas un test visuel navigateur complet. Ce contrôle a révélé l'origine du viewport : `/tmp/seamtech-pdf-rotation-origine-viewport-rouge.log` **3 failed**, puis zones corrigées par soustraction de l'origine effective. L'origine tient compte de l'intersection CropBox/MediaBox (ou MediaBox si intersection vide) : `/tmp/seamtech-pdf-crop-intersection-rouge.log` montrait le défaut avant correction. `/UserUnit` est également pris en compte comme par PDF.js : `/tmp/seamtech-pdf-userunit-rouge.log` **4 failed** avant correction. Les tests comprennent les cas recadrés et origines décalées ; les résultats des nouveaux tests confirment les formules, pas toutes les variantes du format PDF.
- **Limites ouvertes explicites** : un CropBox distinct n'est pas normalisé en lecture ; `/tmp/seamtech-pdf-rotation-crop-rouge.log` **3 failed** et trois xfails stricts ont été ajoutés pour ces cas. Une page contenant plusieurs orientations est aussi laissée inchangée : `/tmp/seamtech-pdf-rotation-mixte-rouge.log` **1 failed** et un xfail strict. Le code n'émet actuellement pas d'avertissement indiquant que l'ancienne extraction peut être incorrecte ; ces cas ne sont donc pas prêts. La lecture actuelle par pdfplumber utilise le MediaBox et peut prendre en compte du contenu hors CropBox : ne pas prétendre que le recadrage a été traité. Les tests de boîtes ne prouvent pas une mise en surbrillance navigateur complète, notamment sur anciens champs persistés ; fonts, annotations et autres structures restent à qualifier. `/tmp/seamtech-pdf-viewport-crop-final.log` **47 passed**, puis `/tmp/seamtech-pdf-rotation-restrictions-final.log` **47 passed / 4 xfailed**. Campagne ciblée actuelle `/tmp/seamtech-pdf-rotation-ci-campagne-finale.log` **285 passed / 7 skipped / 4 xfailed** ; 7 skips liés aux autres tests et services indisponibles dans ce lancement.
- La suite backend large a révélé une lacune CI préexistante dans cette branche : `/tmp/seamtech-rotation-suite-backend.log` **2 failed / 1493 passed / 133 skipped / 5 xfailed** ; les sondes S3rver et MinIO historique avaient été ajoutées sans mise à jour de l'inventaire, et les tests S3rver n'étaient sélectionnés par aucune étape CI. Nouveau marqueur `s3rver`, étape CI explicite après démarrage de l'émulateur, contrôle du résumé **2 passed / 3 xfailed stricts / zéro skip**, inventaire et garde de sélection ; ces xfails restent des défauts connus, pas une qualification stockage. Les sondes MinIO 2023 sont classées séparément **opt-in hors CI**, sans présenter leur inventaire comme une exécution en CI ; les deux parcours PG+S3rver sont vérifiés sélectionnés dans l'étape PG. `/tmp/seamtech-ci-s3rver-couverture-rouge.log` rouge avant l'étape ; `/tmp/s3rver-ci.log` **2 passed / 3 xfailed / 2106 deselected**, garde de résumé vérifiée ; `/tmp/seamtech-ci-selection-s3rver-final.log` **21 passed**. S3rver isolé arrêté après vérification de zéro bucket restant.
- Suite suivant le filtre SQLite exact du workflow, avec CLI Tesseract locale et avant les derniers tests CropBox/UserUnit : `/tmp/seamtech-suite-sqlite-rotation-ci.log` **1406 passed / 1 skipped / 699 deselected / 5 xfailed**. Elle n'est pas un passage CI ni une preuve zéro skip et n'inclut pas les modifications de repère effectuées ensuite ; rejouer la suite et la CI exacte après finalisation. Ruff global et `git diff --check` verts. Aucun commit/push/fusion/déploiement ni CI verte sur commit exact ; P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : résoudre sans deviner les quatre tests CropBox/multi-orientations en respectant le clipping réellement affiché, tableaux et provenance, avec avertissement/échec explicite pour toute orientation ambiguë ; tester la visionneuse PDF.js en navigateur et les migrations de zones persistées. Puis OCR exact sans retry, snapshot coordonné et restauration objet qualifiée, matrice complète traçable et endurance ; ne pas assimiler S3rver ou MinIO historique à MinIO actuel/R2.

### CropBox, navigateur, expurgation et brouillons — état le plus récent

- ECC appliquées : `search-first`, `tdd-workflow`, `security-review`, `error-handling`, `e2e-testing`, `verification-loop`. L'état ci-dessous remplace les limites CropBox et visionneuse décrites dans la section précédente pour les seuls cas testés.
- CropBox distinct : `/tmp/seamtech-pdf-crop-contenu-cache-rouge.log` 4 échecs avant correction du clipping ; projection brute CropBox/MediaBox et découpe des courbes/traits, trois xfails stricts retirés. `/tmp/seamtech-pdf-crop-projection-vert.log` 7 réussis ; `/tmp/seamtech-pdf-crop-campagne-large.log` 296 réussis, 7 ignorés, 1 xfail avant correction des courbes ; `/tmp/seamtech-pdf-crop-courbes-rouge.log` 4 échecs puis `/tmp/seamtech-pdf-crop-courbes-final.log` 24 réussis. PDF.js contrôle l'écart maximal de 0,9 point sur quatre angles (`/tmp/seamtech-pdfjs-crop-test.log`). Le fallback pypdf peut lire hors CropBox et le clipping pdfplumber n'est pas une frontière de sécurité. Les orientations mixtes restent un xfail strict sans avertissement, les zones déjà persistées ne sont pas migrées.
- PostgreSQL 16 : `/tmp/seamtech-pdf-rotation-pg.log` 3 réussis, 48 traces persistées vérifiées pour chacun des angles 90/180/270 ; `/tmp/seamtech-pdf-rotation-pg-large.log` 78 réussis, 4 ignorés. Test navigateur PDF.js : `/tmp/seamtech-pdf-zone-playwright-5.log` 5 répétitions sans retry pour les quatre rotations, vérifiant texte et pixels de surlignage ; `/tmp/seamtech-zone-validation-e2e.log` 14 réussis, zéro skip/flaky confirmé par le JSON, garde CI portée à 14. Cas synthétiques uniquement ; ce n'est pas une qualification générale des PDF, des migrations de provenance ou de Chrome/Edge Windows.
- Expurgation ciblée dépôt/persistance : `/tmp/seamtech-depot-logs-rouge.log`, `-pg-rouge.log` et `/tmp/seamtech-depot-scanner-secret-rouge.log` montrent les fuites avant correction ; le refus du scanner est générique sauf message interne statique exact « aucun gabarit ». `/tmp/seamtech-depot-logs-pg-s3-redis-final.log` 109 réussis, zéro skip sur PostgreSQL 16, S3rver et Redis 7 isolés. Extraction : `/tmp/seamtech-extraction-logs-rouge.log` 2 échecs puis `/tmp/seamtech-extraction-logs-final.log` 106 réussis, 1 xfail ; cotes, ancres et exceptions de règles ne sont plus reproduites dans ces journaux.
- Exceptions API brouillon : le premier test rouge était invalide car la fonction était capturée lors de la création de l'application. Application reconstruite après injection : `/tmp/seamtech-brouillon-logs-rouge2.log` 2 échecs démontrant fuites synthétiques ; les deux branches journalisent désormais uniquement le type d'exception sans traceback. `/tmp/seamtech-brouillon-extraction-logs-rouge.log` démontre séparément la fuite du message pdfplumber dans le générateur, désormais expurgée. `/tmp/seamtech-lot-local-logs-rouge.log` montre la fuite traceback du fil local ; correction idem avec état job `failed` préservé. Aucun audit exhaustif des journaux n'est revendiqué ; les logs du banc PostgreSQL peuvent contenir des requêtes et paramètres synthétiques en cas d'erreur.
- Le générateur de brouillon utilisait directement les coordonnées pdfplumber hors du repère commun de rotation/CropBox/UserUnit : `/tmp/seamtech-brouillon-pdf-rotation-rouge.log` 4 échecs. Il utilise désormais l'analyse et les zones communes, avec tolérances de mots antérieures conservées et dimensions du viewport ; `/tmp/seamtech-brouillon-pdf-rotation-vert.log` 63 réussis, 1 xfail avant le dernier raffinement des tolérances. `/tmp/seamtech-lot-brouillon-logs-pdf-pg-final.log` 100 réussis, 1 xfail après tous ces changements. Le nouveau traitement peut calculer aussi les tableaux et n'a pas été évalué en performance sur de très gros PDF. Les pages à orientations mixtes restent non qualifiées et les brouillons déjà persistés ne sont pas migrés.
- Suite SQLite filtrée du workflow avant les derniers changements d'extraction : `/tmp/seamtech-suite-sqlite-logs-et-crop-final.log` 1431 réussis, 707 désélectionnés, 6 xfails. Après les changements courants : `PATH=/tmp/seamtech-tessbin:$PATH TESSDATA_PREFIX=/tmp/seamtech-tessdata .venv/bin/pytest -q -rf -m 'not postgres and not s3 and not perf and not redis_queue and not ocr_suite'` : `/tmp/seamtech-suite-sqlite-brouillon-final.log` 1437 réussis, 1 ignoré, 710 désélectionnés, 6 xfails. Ruff global et `git diff --check` réussis. Un test ignoré et des xfails ne sont pas une validation exhaustive ni la CI distante sur commit exact.
- Avant arrêt des services : cluster PG privé ne contient que `postgres` hors templates ; répertoire S3rver vide et Redis DB0 sans clé. PG, S3rver et Redis privés arrêtés et ports contrôlés. Aucun nouveau commit/push/fusion/déploiement ; HEAD `f1733c8`, nombreux changements locaux. P0/P1/P2 restent **NON PRÊTS** et aucune CI 100 % verte sur commit exact.
- Prochaine action précise : traiter l'orientation mixte sans accepter une extraction incertaine, avec tests d'alerte/fail-closed et de régression des PDF lisibles ; évaluer puis optimiser le coût de génération du brouillon. Poursuivre la correction OCR fidèle sans retry, l'audit des journaux, le snapshot et la restauration coordonnés, le fournisseur objet réellement qualifié, les courses distribuées, Windows/Docker/Chrome/Edge, la matrice et l'endurance documentés ci-dessus.

### Orientations mixtes visibles et journaux d'indexation — suite la plus récente

- ECC : `search-first`, `tdd-workflow`, `security-review`, `error-handling`, `verification-loop`. `/tmp/seamtech-pdf-mixte-alerte-rouge.log` 3 échecs / 3 réussis : la lecture des pages mixtes était silencieusement incertaine. Le module de repérage émet désormais un avertissement générique sans nom, chemin, texte ou traceback si plusieurs angles de caractères sont visibles, y compris à rotation 0 et quand aucun caractère ne correspond à l'angle attendu. `/tmp/seamtech-pdf-mixte-generale-rouge.log` 4 échecs avant cette extension. Tests couvrent extraction fiche, index et brouillon et l'absence d'alerte sur les pages homogènes. **Avertissement seulement dans les logs : aucune alerte métier visible, aucun refus de validation ; l'extraction des pages mixtes reste potentiellement fausse et l'xfail strict demeure.** Ce n'est pas une correction complète.
- `/tmp/seamtech-pdf-mixte-hors-crop-rouge.log` 4 échecs : texte caché de direction différente empêchait la normalisation et déclenchait une alerte à tort. La décision est fondée sur les seuls caractères intersectant le viewport ; la vérification géométrique de la transformation continue de comparer tous les caractères. Test synthétique 0/90/180/270. Les CropBox ne sont toujours pas une frontière de sécurité pour tous les chemins PDF, en particulier le fallback pypdf ; pas de qualification de toutes les structures PDF.
- `/tmp/seamtech-brouillon-performance-rouge.log` révélait que l'extraction des mots du brouillon calculait inutilement les tableaux. Option de l'analyse commune permettant de ne pas les calculer pour le brouillon ; test prouvant l'absence d'appel à `extract_tables`, mais pas de benchmark volumineux, et le clipping des courbes peut toujours calculer des arêtes.
- `/tmp/seamtech-index-logs-rouge.log` 2 échecs : messages d'échec de table et OCR exposaient exception tierce et nom ; expurgés, type conservé. `/tmp/seamtech-pdf-ressource-rouge.log` : page pdfplumber normalisée non libérée si `extract_text` lève ; `finally` ajouté en conservant le repli existant. `/tmp/seamtech-gabarit-logs-rouge.log` 3 échecs : codes libres de gabarits, paramètres de traitements inconnus et exception de gabarit inconnu dans les logs ; journaux correspondants expurgés sans retirer les données des résultats métier. L'audit global de confidentialité reste ouvert.
- `/tmp/seamtech-gabarit-crop-logs-final.log` 151 réussis / 5 ignorés / 1 xfail ; après le dernier correctif CropBox et l'expurgation, PostgreSQL 16 isolé : `/tmp/seamtech-mixte-gabarit-pg-final.log` 129 réussis / 1 xfail. Suite SQLite complète avec filtre workflow et Tesseract locale : `/tmp/seamtech-suite-sqlite-mixte-gabarit-final.log` 1458 réussis / 1 ignoré / 710 désélectionnés / 6 xfails ; Ruff global et `git diff --check` réussis. PG ne contenait que `postgres` hors templates après tests et est arrêté. Aucun commit/push/fusion/déploiement, HEAD `f1733c8` ; aucune CI verte sur commit exact. P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : remplacer l'avertissement journal seul par un traitement des orientations mixtes réellement sûr et visible dans le parcours métier, sans bloquer silencieusement des PDF lisibles ni affaiblir l'xfail ; couvrir tableaux, coordonnées, indexation, brouillons et persistance. Poursuivre OCR fidèle sans retry, stockage/restauration coordonnée, courses distribuées, sécurité des imports et journaux, qualifications cible et autres portes non closes.

### Alerte métier orientations mixtes, validation groupée et raccourcis clavier

- ECC appliquées : `search-first`, `tdd-workflow`, `security-review`, `react-patterns`, `e2e-testing`, `verification-loop`. `/tmp/seamtech-pdf-mixte-metier-rouge.log` 4 échecs : l'avertissement n'était présent que dans les journaux. L'analyse conserve un indicateur par page, et l'extraction de fiche (y compris filet RG6) ajoute une anomalie forte `orientations_pdf_mixtes` si au moins une page présente des directions visibles différentes ; le routage indicatif demande `reprise_complete`. Les caractères hors CropBox sont exclus de cette décision. `/tmp/seamtech-pdf-mixte-file-pg-rouge.log` montre que l'indicateur manquait de la file : il est désormais persisté en anomalie et exposé en booléen pour les fiches à valider. `/tmp/seamtech-pdf-mixte-lot-rouge.log` montre que la validation en lot l'ignorait : elle exclut maintenant ces fiches avec une raison et préserve leur statut, même avec acquittement de calibration. PostgreSQL `/tmp/seamtech-pdf-mixte-lot-pg-final.log` : 110 réussis, 1 xfail. La validation unitaire reste volontairement possible après confirmation explicite ; cela ne prouve ni relecture réelle ni couverture des appels API directs, d'anciennes fiches, des autres parcours de décision et des courses concurrentes.
- L'interface montre « PDF à orientations mixtes : relecture complète nécessaire » dans la file et une confirmation spécifique en validation unitaire ; refus du dialogue ne transmet pas la décision. `/tmp/seamtech-mixte-interface-confirmation-final2.log` : 1 réussi avec session nominative réelle et réponses métier simulées. Les deux premières tentatives de ce test étaient invalides (compte secours non autorisé, puis build Next antérieur) ; après build, une tentative de test du dialogue bloquait car le gestionnaire attendait la fin du clic avant de fermer le dialogue, corrigée par un gestionnaire événementiel. Test navigateur ajouté à la sélection CI live.
- `/tmp/seamtech-mixte-validation-e2e.log` a révélé 14 réussis / 1 échec sans retry : le raccourci Entrée capturait une ancienne `revisionFiche=null` malgré une révision affichée. Test déterministe `/tmp/seamtech-raccourci-revision-rouge2.log` : motif saisi AVANT l'arrivée différée de la révision, puis Entrée ; échec reproduit. Dépendance `revisionFiche` ajoutée au gestionnaire clavier ; `/tmp/seamtech-raccourci-revision-vert.log` réussit, puis `/tmp/seamtech-raccourci-revision-vert2.log` 2 réussis (Entrée et V, révisions transmises vérifiées). Première commande d'écriture du test utilisait un mauvais chemin depuis `frontend` et n'avait exécuté aucun test.
- Nouvelle campagne navigateur complète avec les deux nouveaux fichiers dans la sélection CI : `/tmp/seamtech-mixte-validation-e2e-final.log` **17 passed**, zéro retry ; `/tmp/seamtech-mixte-validation-final-report.json` confirme expected=17, skipped=unexpected=flaky=0. Garde CI 17. C'est un résultat local, pas une exécution CI sur un commit exact ; Redis absent dans ce banc, Chrome Linux et pas Edge/Windows. Quatre bases E2E créées par ces essais identifiées dans leurs journaux, vérifiées sans connexions puis supprimées explicitement, registre de run retiré ; PG privé arrêté après vérification qu'il ne restait que `postgres`.
- `/tmp/seamtech-anomalies-logs-rouge.log` démontre fuite de référence et valeurs synthétiques dans les logs RG16 ; journal réduit à un décompte, anomalies et détails métier inchangés. Suite SQLite workflow `/tmp/seamtech-suite-sqlite-mixte-metier-final.log` 1463 passed / 1 skipped / 711 deselected / 6 xfailed ; Ruff, TypeScript et `git diff --check` verts. Logs de certains autres chemins, notamment corrections/décisions et PostgreSQL, peuvent encore révéler des données : audit global non terminé.
- **Limites prioritaires inchangées :** extraction fidèle des orientations mixtes non corrigée, xfail strict maintenu ; une détection échouée avant création d'une fiche ne produit pas l'anomalie persistée, brouillons et indexation n'exposent pas le signal métier, matrices invalides et variantes PDF restent à traiter, anciennes données non migrées. OCR exact toujours instable ; sauvegarde/restauration coordonnée, MinIO/R2 récents, sécurité TOCTOU, concurrence distribuée, qualifications cibles et endurance toujours ouverts. P0/P1/P2 **NON PRÊTS** ; aucun nouveau commit/push/fusion/déploiement, HEAD `f1733c8`, pas de CI verte sur commit exact.
- Prochaine action précise : étendre le signal/fail-closed aux brouillons, à l'indexation et à la détection échouée, avec un véritable traitement des orientations mixtes sans altérer les PDF lisibles ; contrôler les décisions unitaires/API et anciennes fiches. Poursuivre immédiatement les autres portes, notamment correction OCR sans retry et protection TOCTOU sans prétendre que `resolve()` constitue une isolation.

### Détection et brouillons mixtes ; confidentialité validation, comptes et Excel

- ECC appliquées : `search-first`, `tdd-workflow`, `security-review`, `error-handling`, `verification-loop`. `/tmp/seamtech-brouillon-mixte-rouge.log` 4 échecs (0/90/180/270) : un brouillon pouvait annoncer une confiance optimiste malgré des orientations mixtes. Désormais sa génération refuse explicitement ces pages avant écriture ; l'API répond 422 avec raison générique mais pertinente, sans confondre avec un PDF illisible. `/tmp/seamtech-brouillon-mixte-api-rouge.log` était rouge avant correction, puis vérifie sur PG que la liste des brouillons n'a pas changé ; `/tmp/seamtech-brouillon-mixte-repli-final.log` confirme que le repli texte ne contourne pas le refus. C'est un refus de sécurité, pas une extraction correcte des orientations mixtes ; matrices illisibles, exceptions de parseur et variantes restent à examiner.
- `/tmp/seamtech-detection-mixte-rouge.log` 2 échecs : la détection seule tait désormais moins l'incertitude, avec `orientations_pdf_mixtes`, avertissement et voie reprise complète, gabarit reconnu ou non. `/tmp/seamtech-scanner-mixte-rouge.log` 2 échecs : quand aucun candidat n'est sélectionnable et qu'une page mixte a empêché ou rendu incertaine la détection, le scanner indique une relecture complète plutôt que « gabarit inconnu » ; les autres candidats valides restent sélectionnables. `/tmp/seamtech-detection-mixte-depot-pg-final.log` vérifie le refus, la raison persistée et les octets inchangés sur PostgreSQL. Ce signal ne répare pas l'extraction et ne prouve pas toutes les causes d'indétermination ; l'index plein texte n'expose toujours pas d'indicateur métier, anciens résultats non migrés, xfail strict maintenu.
- `/tmp/seamtech-detection-pdf-secret-rouge.log` : la route de détection renvoyait l'exception brute d'un parseur tiers ; elle retourne maintenant un 422 générique, et le journal de nettoyage du temporaire n'expose plus son chemin. `/tmp/seamtech-detection-mixte-pg-final.log` : 221 passed, 4 skipped, 1 xfailed (les skips requièrent d'autres services). Les tests de sélection et PDF/import `/tmp/seamtech-selections-pdf-import-final.log` : 188 passed, 2 xfailed.
- `/tmp/seamtech-validation-logs-rouge.log` : codes, identifiants, anciennes/nouvelles valeurs et motifs de rejet étaient visibles dans les journaux techniques de correction/décision, y compris le chemin legacy sans révision. Journaux expurgés, tandis que la traçabilité métier transactionnelle reste inchangée et testée ; `/tmp/seamtech-validation-logs-pg-final.log` 46 passed et `/tmp/seamtech-validation-logs-lot-final.log` 2 passed, avec contrôle de validation en lot. D'autres modules et les journaux SQL PostgreSQL restent hors de cet audit ciblé.
- `/tmp/seamtech-comptes-logs-rouge.log` : noms nominatifs visibles à la création, connexion, changement de rôle/mot de passe et désactivation. Journaux techniques expurgés et audit d'authentification conservé. Le premier contrôle après correction utilisait à tort la colonne `details` au lieu de `actor` : corrigé après inspection du schéma, sans affaiblir l'exigence d'au moins deux événements. `/tmp/seamtech-comptes-logs-pg-final2.log` 57 passed ; `/tmp/seamtech-comptes-route-logs-final.log` teste aussi la route de création. L'audit métier conserve volontairement l'identité et doit être protégé : ce n'est pas une preuve de confidentialité de tous les journaux.
- `/tmp/seamtech-import-excel-logs-rouge.log` : message tiers et chemin ressortaient dans les logs, le message tiers pouvait être indexé comme résumé Excel, et le classeur n'était pas fermé sur erreur pendant l'itération. Exception remplacée par son type sans nom/chemin ni texte libre ; fermeture en `finally` ; `/tmp/seamtech-import-excel-logs-final.log` 54 passed / 1 xfailed. Les champs métier `filename`/`path` restent dans la réponse privée normale, pas dans le message d'erreur ; le problème TOCTOU d'import reste ouvert.
- Suite SQLite filtre workflow et Tesseract locale `/tmp/seamtech-suite-sqlite-comptes-mixte-final.log` : 1474 passed, 1 skipped, 718 deselected, 6 xfailed. Ruff global et `git diff --check` verts. PostgreSQL privé vérifié ne contenant que `postgres` hors templates puis arrêté. Aucun commit/push/fusion/déploiement supplémentaire ; HEAD `f1733c8`, pas de CI verte sur commit exact ; P0/P1/P2 **NON PRÊTS**.
- Prochaine action précise : traiter le TOCTOU d'import sur tout le cycle scan → sélection → extraction → upload sans contourner les chemins par un simple `resolve`, avec tests d'échanges de fichier/répertoire et d'interruption ; concevoir une lecture confinée/pinning interplateforme ou refuser explicitement l'environnement non qualifié. Poursuivre OCR fidèle sans retry, vraies rotations mixtes, signal d'indexation, restauration coordonnée/stockage réel, courses distribuées et autres portes ouvertes.

### Scan de dossiers : confinement partiel des lectures concurrentes

- ECC `security-review`, `tdd-workflow`, `verification-loop` : quatre régressions PDF/Excel, substitution d'un fichier ou d'un répertoire par lien après inventaire, rouges dans `/tmp/seamtech-scan-substitution-rouge.log` (4 failed). Le scan seul ouvre maintenant sa racine et chaque composant avec des descripteurs POSIX `O_NOFOLLOW`/`O_DIRECTORY`, refuse les fichiers non réguliers et analyse une copie temporaire privée bornée par `max_file_size_bytes`; il compare taille et horodatages avant/après copie. Les noms, chemins et tailles renvoyés restent ceux de la source. Un fichier PDF ou Excel dépassant la limite fait échouer le scan explicitement, au lieu de créer une copie sans limite ; cela modifie notamment l'ancien comportement pour les Excel trop volumineux.
- Les quatre tests et l'ancien xfail strict du **scan** passent sans marquage. Un nouveau xfail strict distinct démontre que `import_folder` reste vulnérable : l'import complet réouvre encore les chemins pour extraction, Excel et stockage. Cette modification ne ferme donc **pas** le TOCTOU du parcours global. Les hardlinks, montages, modifications en place de même UID, changements entre validation et ouverture de la racine, inventaire `rglob` par chemin, les copies et noms périmés entre scan/confirmation, les volumes/dossiers fortement concurrents, le nettoyage après SIGKILL et l'atomicité du snapshot restent à traiter ou qualifier ; la copie temporaire peut exiger de l'espace disque supplémentaire, y compris sur un autre volume. Les comparaisons stat ne constituent pas une preuve cryptographique de snapshot, et un autre processus de même UID peut accéder à la copie.
- La plateforme sans primitives POSIX nécessaires est refusée explicitement, plutôt que de prétendre protéger le scan : **Windows n'est donc pas opérationnel pour ce scan** jusqu'à une implémentation Windows qualifiée. `/tmp/seamtech-scan-limite-rouge.log` : deux limites absentes (2 failed), ajout d'une vérification avant et pendant la copie ; un test d'altération pendant la copie vérifie le refus et le nettoyage normal.
- Les nouvelles erreurs OS du scan provoquaient un 500 et, lors du multipart historique, pouvaient laisser le dépôt temporaire : `/tmp/seamtech-scan-api-os-rouge.log` 2 failed. Gestion explicite 409 générique et nettoyage du dépôt, testée sur les deux endpoints. `/tmp/seamtech-scan-permission-rouge.log` reproduit aussi l'exposition de l'exception de permission ; la réponse 403 est désormais générique. Les autres erreurs et les journaux/audits globaux restent à examiner ; suppression `rmtree` est best-effort et pas une preuve après interruption.
- `/tmp/seamtech-scan-confine-api-final3.log` : 88 passed / 1 xfailed ; `/tmp/seamtech-scan-mutation-final.log` : 2 passed. Suite SQLite `/tmp/seamtech-suite-sqlite-scan-confine.log` : 1483 passed / 1 skipped / 718 deselected / 6 xfailed, lancée avant les dernières retouches de gestion d'erreur et fermeture de descripteur ; tests ciblés relancés après celles-ci. Ruff et `git diff --check` verts. Aucun commit ou CI sur cet état.
- Prochaine action : concevoir et tester l'import **entier** à partir d'un inventaire et de fichiers ancrés/copies immuables bornées, garder la correspondance métadonnées et clés d'objets d'origine, vérifier extraction/indexation/téléversement et reprises/annulation, sans lire de liens substitués ni supprimer l'original. Conserver le xfail d'import tant que l'ensemble n'est pas prouvé ; remplacer le refus Windows uniquement après implémentation sûre et qualification réelle. Les autres portes P0/P1/P2 restent non prêtes.

### Suite du confinement du scan

- ECC `tdd-workflow`, `security-review`, `verification-loop` et `architecture-decision-records` : décision et alternatives décrites dans `docs/adr/0001-confinement-import.md`, statut proposé pour l'import global, **pas** une qualification. Le scan n'utilise plus `rglob`/`_walk_files` sur des chemins modifiables : l'inventaire descend par descripteurs avec `scandir(fd)` et ouverture des enfants `O_NOFOLLOW`, puis ancre les lectures à la même racine. `/tmp/seamtech-scan-inventaire-rouge.log` (1 failed) montre l'ancien inventaire par chemin ; `/tmp/seamtech-scan-inventaire-vert.log` 90 passed/1 xfailed, et `/tmp/seamtech-scan-racine-final.log` 3 passed vérifie notamment le remplacement d'un répertoire durant l'inventaire et le déplacement de la racine après ouverture.
- Un lien physique vers un fichier extérieur était lisible au scan (`/tmp/seamtech-scan-hardlink-rouge.log`, 1 failed) : refus si `st_nlink != 1`, avec recontrôle après copie ; ceci refuse aussi les fichiers légitimes ayant plusieurs liens. Cela ne prouve pas la provenance de toutes les données ni le confinement d'un montage ou d'un processus de même UID.
- Une deuxième régression stricte `test_import_ne_televerse_pas_source_substituee_apres_extraction` montre que l'import peut envoyer un fichier substitué entre extraction et envoi : `/tmp/seamtech-import-upload-toctou-xfail.log` 1 xfailed. L'autre xfail démontre la lecture extérieure dès l'inventaire initial de l'import. `correct_import` et `retry_upload` relisent eux aussi des chemins ; aucune correction complète n'est revendiquée.
- La première suite élargie `/tmp/seamtech-suite-scan-descripteurs-final.log` était rouge : 1 failed / 1487 passed / 1 skipped / 718 deselected / 6 xfailed, car la réponse de permission du multipart avait changé de 400 à 403. Contrat 400 initial conservé avec message expurgé, et contrôle spécifique des deux routes ; `/tmp/seamtech-scan-campagne-corrigee.log` 56 passed/2 xfailed. Suite finale `/tmp/seamtech-suite-scan-descripteurs-final2.log` 1490 passed / 1 skipped / 718 deselected / 7 xfailed, deux warnings ; Ruff et `git diff --check` verts.
- Limites précédentes maintenues : le scan peut retourner des chemins devenus périmés après déplacement de sa racine, les noms ne lient pas la confirmation au contenu scanné, l'inventaire requiert des descripteurs imbriqués et peut épuiser les ressources sur une profondeur extrême, les fichiers temporaires demandent de l'espace et ne sont pas garantis nettoyés après SIGKILL, Windows est explicitement refusé. L'ancien inventaire `rglob` reste utilisé par `import_folder` et le xfail d'import demeure. Pas de commit, CI, déploiement ou GO.
- Prochaine action précise : implémenter et tester la liaison du contenu source à un manifeste/copie durable pour l'intégralité de `import_folder`, puis `correct_import` et `retry_upload`, y compris mappage des chemins logiques et clés d'objets, conservation sur échec, contrôle de capacité pour les grands dossiers et tests de substitution à chaque phase ; ne retirer les deux xfails qu'après avoir prouvé extraction et envoi. Poursuivre toutes les autres portes ouvertes.

### OCR : signalement de certains résultats incomplets

- ECC `search-first`, `tdd-workflow`, `security-review`, `verification-loop`. Une page contenant plusieurs images dont une seule est traitée était auparavant déclarée entièrement reconnue : `/tmp/seamtech-ocr-page-partielle-rouge.log` 2 failed. Le code refuse maintenant le succès partiel et signale un échec générique ou un timeout, sans renvoyer les détails bruts de l'outil ; si une page échoue, le repli d'extraction du fichier ne publie plus les autres pages comme extraction complète. `/tmp/seamtech-ocr-integralite-rouge.log` 3 failed avant correction ; test d'intégration sur PDF image synthétique réel à deux images avec échec injecté, original inchangé. Cette politique fail-closed peut rendre indisponible le texte pourtant extrait d'autres pages : elle évite une complétude fictive, pas une récupération partielle fonctionnelle.
- OCRmyPDF retournant une chaîne vide, et Tesseract sur image retournant une chaîne vide, étaient marqués `extracted` ; `/tmp/seamtech-ocrmypdf-vide-rouge.log` et `/tmp/seamtech-tesseract-vide-rouge.log` : 1 failed chacun. Les résultats vides sont désormais `unavailable`. Le rapport d'une image seule vide comptait une page OCR réussie ; `/tmp/seamtech-ocr-rapport-vide-rouge.log` reproduit le défaut, corrigé en statut non reconnu avec motif. Une page PDF image vide reste un cas distinct : impossibilité actuelle de distinguer sûrement une page blanche d'une reconnaissance vide, les métriques ne couvrent pas encore ce cas.
- `test_pdf_avec_page_native_et_page_image_extrait_les_deux` ajouté en xfail strict : `extract_file` retourne le texte natif dès qu'il y en a, sans appliquer le pipeline aux pages image suivantes. Ce défaut réel demeure ; le corriger sans casser les PDF textuels, les rotations ni les pages vides. L'extraction pypdf des images peut aussi ignorer une image illisible, et OCR des images extraites ne garantit pas la visibilité/CropBox ni l'ordre de lecture ; ne pas qualifier l'intégralité globale à partir de la garde ajoutée. Les échecs de reconnaissance aléatoires CI restent ouverts, sans affaiblissement d'assertion ni retry.
- `/tmp/seamtech-ocr-integralite-vert.log` a d'abord révélé une régression de diagnostic timeout ; motif conservé de manière générique. `/tmp/seamtech-ocr-integralite-final.log` a échoué sur CLI Tesseract locale faute de `LD_LIBRARY_PATH` des dépendances wheel ; relance correctement configurée. `/tmp/seamtech-ocr-integralite-final5.log` : 130 passed / 3 xfailed ; PG16 isolé `/tmp/seamtech-ocr-import-pg-final.log` : 117 passed / 3 xfailed, 3 warnings. `/tmp/seamtech-suite-ocr-integralite-final.log` : 1494 passed / 1 skipped / 718 deselected / 8 xfailed, lancée avant les derniers correctifs OCR vides ; campagnes ciblées ci-dessus exécutées après. Ruff et `git diff --check` verts ; base privée ne contenant que `postgres` hors templates, arrêtée. Aucun commit/CI/déploiement.
- Prochaine action : traiter le PDF natif+image avec une stratégie par page qui conserve exactement texte/rotations des PDF lisibles et rend toute page image traitée ou signalée, teste vide/échec/multipage/sélection et original, avant de retirer le xfail ; poursuivre aussi le confinement complet import/reprise documenté dans l'ADR et les portes P0/P1/P2 non prêtes.

### OCR hybride : pages natives et images distinctes

- ECC appliquées : `tdd-workflow`, `security-review`, `verification-loop`. `/tmp/seamtech-ocr-natif-mixte-rouge.log` : 11 échecs / 1 passé, dont ordre des pages (texte/image et image/texte), rotations 0/90/180/270, page blanche, panne et absence de moteur. Lorsqu'OCR est activé et qu'un PDF comporte du texte natif, une inspection complémentaire sépare les pages nativement lisibles des pages sans texte avec image : les premières gardent la même lecture native avec tableaux, seules les secondes passent par OCR, et les textes sont assemblés dans l'ordre. Si une page image identifiée ne peut être reconnue, le résultat est `unavailable` avec texte natif partiel et motif générique, non un succès complet ; une inspection impossible est aussi indisponible. Les pages comportant déjà du texte natif ne sont pas océrisées même si elles ont aussi une image : le contenu image de telles pages reste hors couverture. Les pages visuellement vides sont ignorées. Le texte retourné au crawler legacy en cas d'indisponibilité est un marqueur, pas le texte natif partiel ; à améliorer sans masquer l'état.
- `/tmp/seamtech-ocr-mixte-cli-final.log` : 18 passed dont 4 angles avec vraie CLI Tesseract 5.5.1 ; `/tmp/seamtech-ocr-mixte-index-reel.log` : indexation SQLite avec OCR en sous-processus et recherche sur fichier hybride synthétique, original inchangé. L'ancien xfail strict `test_pdf_avec_page_native_et_page_image_extrait_les_deux` est devenu un test normal, exercé en plus des nouveaux cas. Cela ne corrige ni les caractères ambigus ni tous les PDF hybrides.
- Audit de visibilité : l'OCR d'images brutes contournait une CropBox, un cache graphique, une image débordant la MediaBox et des calques/annotations. `/tmp/seamtech-ocr-crop-securite-rouge.log`, `/tmp/seamtech-ocr-image-masquee-rouge.log`, `/tmp/seamtech-ocr-calque-rouge.log`, `/tmp/seamtech-ocr-annotations-rouge.log` étaient rouges. Garde conservative dans le pipeline : seulement une image XObject simple intégralement dans la MediaBox, sans recadrage, masque, calque, annotations, composition, clipping ou opérateur inconnu ; sinon échec explicite avant lecture OCR brute. Cette grammaire restrictive refuse aussi des PDF légitimes (multiples images, rotations dans la matrice image, texte/formes et autres opérateurs), et ne remplace pas un rendu de page complet et qualifié. Le test CLI de recadrage demeure xfail strict : il exige la reconnaissance du texte visible sans contenu caché, non un simple refus. Les cas du format PDF non couverts, les calques et interprétations malveillantes ne sont pas prouvés ; ne pas traiter CropBox comme frontière de sécurité générale.
- `/tmp/seamtech-ocr-natif-protection-final.log` : 4 échecs CLI car la fixture de test elle-même débordait la MediaBox ; elle a été redimensionnée dans la page sans modifier les assertions. `/tmp/seamtech-ocr-mixte-reindex-final2.log` : 263 passed / 4 xfailed, avec Tesseract réel ; `/tmp/seamtech-ocr-mixte-reindex-pg.log` : 52 passed / 2 xfailed, 3 warnings (avant dernière garde d'annotations, couverte par la campagne ciblée). Suite SQLite `/tmp/seamtech-suite-ocr-mixte-reindex-final.log` : 1521 passed / 1 skipped / 718 deselected / 8 xfailed, lancée avant les derniers tests de garde d'annotations/calques ; ciblés relancés après. Ruff et `git diff --check` verts.
- `CURRENT_EXTRACTOR_VERSION` passe de 7 à 8, réextraction des anciennes entrées non protégées sur prochain scan : `/tmp/seamtech-ocr-version-rouge.log` rouge avant incrément, test de sélection par version vert ensuite. Les documents protégés et les migrations/reprises représentatives ne sont pas qualifiés : l'ancien xfail PG de réimport protégé demeure ; ne pas déployer ni présumer une réindexation globale réussie. L'inspection hybride reparcourt toutes les pages, y compris après la limite de texte, et peut coûter du temps/mémoire ; volumes et interruptions restent à qualifier.
- Nouvelle campagne navigateur OCR inchangée : `/tmp/seamtech-ocr-browser-apres-mixte.log`, 25 exécutions indépendantes, zéro retry, **22 passed / 3 failed**, échecs de reconnaissance exacte de caractères proches ; aucun affaiblissement d'assertion. P1 OCR CI reste **NON PRÊT**. Base e2e du seul run identifiée puis supprimée explicitement, cluster PostgreSQL privé ne contenant plus que `postgres` hors templates et arrêté.
- `/tmp/seamtech-crawler-timeout-secret-rouge.log` révélait un chemin synthétique dans le journal de timeout du crawler ; les deux branches thread/sous-processus n'incluent plus le chemin, testées au moins sur la branche sous-processus. Audit global des logs toujours inachevé.
- Prochaine action précise : remplacer les images brutes par un rendu PDF réellement visible borné/qualifié, avec limites de résolution/mémoire et tests CropBox, masques/calques, pages et OCR sans retry ; traiter les erreurs d'OCR exact sans substituer la vérité, puis reprendre confinement global de l'import et toutes les autres portes. Aucun commit, CI 100 % verte, déploiement ou GO.

### Rendu PDF pour OCR et mise à jour de sécurité du parseur

- Nouvelle sandbox : les précédents `.venv`, `/tmp` et serveurs éphémères n'étaient plus présents ; environnement Python recréé depuis `requirements-dev.txt`, sans données client. CLI Tesseract 5.5.1 reconstruite en `/tmp` à partir des sources et du wheel `tesserocr==2.9.1`, packs `tessdata_fast` officiels fra/eng, mêmes SHA-256 que le checkpoint antérieur. Première installation du banc oubliait le fichier officiel `configs/tsv` : `/tmp/seamtech-rendu-pypdf-6214-cible-final.log` avait 2 failed / 271 passed / 3 xfailed, dont la confiance TSV sans mots ; config ajoutée puis test CLI relancé. L'autre échec révélait que le contrôle statique RG13 ne reconnaissait pas le nouveau module ; il est désormais complété par une vérification du fichier de sortie en création exclusive et du `TemporaryDirectory` parent, sans retirer le snapshot source avant/après.
- ECC `search-first`, `tdd-workflow`, `security-review`, `verification-loop` : PDFium déjà dépendance transitive de pdfplumber, version désormais explicitement épinglée `pypdfium2==5.14.0` dans les deux déclarations runtime. Lorsque l'image brute ne passe pas la garde conservative, rendu PNG de la vue dans un sous-processus dédié, sans chemin en argument de commande ni messages d'exception, limité en temps, CPU, espace d'adressage (1 Gio), 25 millions de pixels et 8192 px par dimension ; échec ou timeout = indisponible, jamais repli vers image brute non sûre. Sortie temporaire créée exclusivement, nettoyée en cas d'échec normal ; un crash dur reste une limite. POSIX seulement pour cette voie : Windows reste explicitement refusé, pas qualifié.
- `/tmp/seamtech-rendu-pdfium-rouge2.log` 4 failed avant helper ; `/tmp/seamtech-ocr-rendu-integration-rouge.log` 2 failed avant intégration. Tests de pixels pour CropBox et cache opaque, refuse limite et PDF invalide, et vérifie que l'échec du rendu ne déclenche jamais la lecture d'images brutes. `/tmp/seamtech-rendu-crop-securite-final.log` : 98 passed avec CLI locale ; `/tmp/seamtech-rendu-masque-cli.log` : vrai OCR d'une page composée, texte visible reconnu et masqué absent. `/tmp/seamtech-rendu-compense-cli.log` : 3 passed pour orientations déjà compensées. Après démonstration XPASS strict, le test CropBox CLI n'est plus xfail et exige toujours le texte visible et l'absence du caché, aux quatre rotations.
- `/tmp/seamtech-rendu-crop-angles-rouge.log` : 2 angles 180/270 erronés ; rendu contre-roté seulement lorsqu'une image unique possède une matrice d'image non compensée, avec test de 3 matrices déjà compensées. Cela ne déduit pas l'orientation intrinsèque d'un scan, ni celle d'une page à images multiples/transformations ambiguës : ces cas restent à qualifier ; pas de garantie exhaustive. Les calques optionnels, formulaires et annotations sont refusés par le rendu tant que leur visibilité n'est pas qualifiée, même si certains documents sont légitimes ; un scan texte absent de la page mais contenant seulement du graphique complexe n'est pas nécessairement détecté. PDFium et PDF.js peuvent différer, les CropBox ne sont pas une frontière de sécurité générale, décodeurs natifs peuvent avoir des défauts ; pas de garantie globale d'absence de contenu masqué. Les processus peuvent encore saturer le système en concurrence, et 1 Gio est un plafond par processus, pas un quota global.
- Audit PyPI `/tmp/seamtech-pip-audit-pdfium.log` : 7 CVE de déni de service dans `pypdf==6.21.2`, corrigées en 6.21.3/6.21.4. Dépendance montée en **6.21.4** dans requirements et pyproject ; `/tmp/seamtech-pip-audit-pdfium-pypdf-final.log` : aucune vulnérabilité connue détectée au moment de l'essai. `/tmp/seamtech-pypdf-advisories-final.log` : 10 passed, dont un PDF synthétique malformé issu du correctif upstream PR 4289 exécuté sous timeout ; cela n'exerce pas les 7 CVE ni ne prouve l'absence de vulnérabilités inconnues.
- `/tmp/seamtech-suite-rendu-pypdf-6214-final.log` : 1544 passed / 1 skipped / 718 deselected / 7 xfailed, sous pypdf 6.21.4 ; lancée avant ajout du dernier test de sécurité du parseur et des 3 tests de rotation compensée. Campagne OCR/PDF/import/deps `/tmp/seamtech-rendu-pypdf-6214-cible-final2.log` : 274 passed / 3 xfailed / 1 warning, puis trois cas compensés ci-dessus. Ruff global et `git diff --check` verts. Aucun serveur ne tourne dans cette nouvelle sandbox ; aucun test PG/Docker/CI sur les nouvelles versions, aucun commit/push/déploiement ni GO.
- Prochaine action : renforcer et qualifier le rendu contre objets PDF imbriqués, recadrages hérités/décalés/rotations, dimensions extrêmes, calques/annotations, pannes, charge concurrente et Windows ; relancer navigateur OCR exact 25 fois sans retry sous le nouvel environnement PG/Chromium, puis traiter la reconnaissance aléatoire qui échouait 3/25. Poursuivre aussi les deux xfails de confinement d'import et les portes P0/P1/P2 toujours non prêtes.

### État de reprise — OCR : pages vides et décodage d'image isolé

- ECC appliquées : `search-first`, `tdd-workflow`, `security-review`, `verification-loop`. Aucun sous-agent n'est disponible dans cet environnement. Branche `arena/b936b3c0-seamtech-search`, base `f1733c8`, modifications locales non commitées ; aucun déploiement, aucune fusion, aucune CI verte sur un commit exact. P0/P1/P2 demeurent **NON PRÊTS**.
- Une page confiée à l'OCR sans texte reconnu compte désormais comme échec : auparavant un document avec une autre page correctement reconnue pouvait être déclaré complet. `/tmp/seamtech-ocr-vide-multipage-rouge.log` reproduit le défaut ; `/tmp/seamtech-ocr-vide-multipage-final.log` : **241 réussis, 1 xfail**. Une page véritablement blanche est également signalée en échec : distinction et parcours de revue à qualifier.
- Les ressources image non dessinées, dimensions excessives et configurations globales de calques/formulaires sont exclues du raccourci image brute. Décodage et nouvelle vérification de visibilité sont déplacés dans `ocr/rendu_pdf.py`, sous-processus POSIX limité, travaillant sur les mêmes octets immuables et produisant un PNG privé ; aucun chemin confidentiel dans ses arguments, pas de repli vers un décodeur brut non borné. Ancienne fonction brute inutilisée retirée. `/tmp/seamtech-ocr-image-geante-rouge.log`, `/tmp/seamtech-ocr-image-inutilisee-rouge.log`, `/tmp/seamtech-extraction-image-isolee-rouge.log`, `/tmp/seamtech-ocr-proprietes-doc-rouge.log` documentent les échecs initiaux ; substitution entre précontrôle et extraction testée `/tmp/seamtech-ocr-precontrole-substitution-final.log` : **1 réussi**.
- Avec Tesseract CLI réelle, `/tmp/seamtech-extraction-image-isolee-final.log` : **236 réussis, 1 xfail** avant derniers ajustements de tests/catalogue ; `/tmp/seamtech-ocr-catalogue-final.log` : **229 réussis, 1 xfail** après ces ajustements. Test synthétique local distinct, 30 identifiants déterministes sans retry : `/tmp/seamtech-ocr-isole-30.log` **30/30** ; cela ne remplace pas la campagne navigateur antérieure **22/25** et ne prouve pas la fiabilité générale. Ruff complet et `git diff --check` réussis après les changements de code et tests, avant ajout de cette note.
- Limites critiques inchangées : précontrôle pypdf et extraction de texte natif dans le parent restent non bornés ; source remplaçable entre étapes, objets imbriqués, CropBox héritées/décalées, charge agrégée, nettoyage après crash dur et Windows non qualifiés. L'enfant lit jusqu'à 512 Mio avec limite d'espace d'adressage 1 Gio : certains gros documents échoueront. Le texte natif associé à `unavailable` peut être perdu par `legacy_text`/crawler. Les images sur pages contenant du texte natif ne sont pas exhaustivement reconnues. La reconnaissance navigateur reste non fiable ; le test local positif ne corrige pas les échecs observés sans retry. Import et reprises restent exposés à des courses, deux xfails stricts ; ADR `docs/adr/0001-confinement-import.md`.
- **Prochaine action précise :** reprendre l'audit des précontrôles PDF non bornés et de l'identité des octets entre extraction, indexation et upload, écrire d'abord les tests de régression des cas limites et intégrer un confinement complet sans perdre les originaux ni affaiblir les assertions. Reconstruire les bancs PG16/Chromium puis répéter 25 fois le parcours navigateur OCR sans retry. Poursuivre les portes sauvegarde/restauration coordonnée, migration/rollback, rotations et pages mixtes, fournisseurs objet actuels, Windows/Docker/Chrome/Edge, matrice d'autorisations, multipart, pannes, sécurité, accessibilité, charge et endurance ; exiger enfin CI 100 % verte sur le commit exact avant tout GO. Les journaux `/tmp` sont éphémères.

### État de reprise — résultat structuré et contrôle OCR isolé

- ECC consultées/appliquées : `tdd-workflow`, `security-review`, `verification-loop`. Branche `arena/b936b3c0-seamtech-search`, base `f1733c8` ; travail non commité, ni CI sur SHA exact, ni fusion, ni déploiement. **P0/P1/P2 NON PRÊTS.**
- Le crawler reçoit désormais un résultat JSON strict du worker d'extraction (`text`, `status`, `detail`) au lieu d'un texte ambigu ou d'un marqueur : texte natif préservé lorsque l'OCR est incomplet et statut maintenu à `unavailable`, contenu réel commençant par un marqueur non pris à tort pour une erreur, réponse malformée refusée sans exposer son contenu. Tests avec vrai sous-processus et persistance SQLite. `/tmp/seamtech-worker-structure-rouge.log` : 7 échecs reproduits ; `/tmp/seamtech-worker-version-rouge.log` : 2 échecs avant incrément de version ; `CURRENT_EXTRACTOR_VERSION = 9` force la réextraction des entrées non protégées en version 8. Les documents protégés restent soumis à la réserve de réimport déjà documentée. Le wrapper historique `extract_text()` reste un format à marqueur sans texte partiel : ne pas l'utiliser pour conserver texte ET statut. La recherche PostgreSQL des fiches filtre toujours les statuts non `extracted` ; les effets métier d'une extraction partielle restent à qualifier.
- Chemin et options du worker passent par stdin plutôt que par les arguments du processus ; requête limitée à 1 Mio, erreur silencieuse côté worker. `/tmp/seamtech-worker-arguments-rouge.log` reproduit l'ancien comportement. Cela ne dissimule pas les chemins aux décodeurs externes configurés ni aux processus tiers, et ne règle pas le confinement de l'import.
- Timeout du worker : sous POSIX, un groupe de processus séparé est tué en cas de dépassement ou interruption, avec limite de 2 secondes à la collecte des tubes ; sous Windows seul le worker est tué, les descendants ne sont pas qualifiés. `/tmp/seamtech-worker-enfants-rouge.log` démontrait un enfant externe survivant ; le test réel après correction vérifie qu'il ne produit plus son fichier témoin. Un descendant qui se détache dans un nouveau groupe n'est pas couvert, et la sortie capturée reste sans quota mémoire explicite. Un premier passage intermédiaire a révélé deux tests mockant l'ancien `subprocess.run` ; mocks adaptés sans affaiblissement, puis `/tmp/seamtech-worker-enfants-final.log` : **33 réussis**.
- Le précontrôle `_page_pdf_vue_integrale` n'est plus exécuté dans le parent OCR : processus dédié limité en temps/mémoire/CPU, chemin par stdin ; le décodage isolé revérifie toujours la page depuis ses propres octets avant de produire l'image. `/tmp/seamtech-precontrole-isole-rouge.log` démontre l'ancien appel parent ; test de timeout et non-divulgation en arguments. Le texte natif et la détection initiale hybride restent analysés dans le worker d'extraction sans limite mémoire, et d'autres chemins d'appel direct existent : confinement complet non démontré. Précontrôle, décodage et rendu peuvent encore relire des versions différentes du fichier ; seuls les octets du décodage et de sa vérification sont identiques. La multiplication de sous-processus et la charge agrégée restent à mesurer. Windows refuse toujours ces voies.
- Une première campagne ciblée `/tmp/seamtech-precontrole-isole-final.log` : **249 réussis, 1 échec, 1 xfail** ; le test de timeout Tesseract interceptait aussi les nouveaux sous-processus PDF, corrigé en ciblant le point d'injection voulu, assertion de timeout conservée. `/tmp/seamtech-precontrole-isole-final2.log` : **250 réussis, 1 xfail** avant derniers changements worker ; `/tmp/seamtech-ocr-worker-groupe-final.log` : **252 réussis, 1 xfail**, CLI Tesseract réelle, sur l'arbre après changements. `/tmp/seamtech-suite-worker-groupe-final.log` : **1570 réussis, 1 ignoré, 718 désélectionnés, 7 xfails, 2 warnings**, sélection `not postgres and not s3 and not perf and not redis_queue and not ocr_suite`, sans services PG/Redis/MinIO ni qualification des éléments exclus. Ruff global et `git diff --check` réussis avant ajout de cette note. Logs `/tmp` éphémères.
- **Prochaine action précise :** traiter l'identité des octets sur tout le parcours import/extraction/indexation/upload et les deux xfails de confinement, étendre aux reprises et corrections ; limiter mémoire/temps des parseurs et sorties dans tous les points d'entrée sans masquer les échecs. Reconstruire PostgreSQL/Chromium, rejouer OCR navigateur 25 fois sans retry ; qualifier et corriger les erreurs de reconnaissance exactes, puis reprendre les portes P0 sauvegarde/restauration/migration, P1 rotations et Windows/Docker/navigateur/volume, P2 autorisations/flux/pannes/offensive/axe/endurance et CI 100 % verte sur commit exact. Ne pas conclure GO à partir des tests locaux filtrés.

### État de reprise — intégrité objet, banc PG et OCR navigateur

- ECC relues/appliquées : `search-first`, `tdd-workflow`, `security-review`, `postgres-patterns`, `e2e-testing`, `verification-loop`. Branche `arena/b936b3c0-seamtech-search`, base `f1733c8`, travail non commité ; aucune fusion, aucun déploiement, aucune CI verte sur SHA exact. **P0/P1/P2 NON PRÊTS.**
- Sauvegarde des objets : la comparaison taille/ETag/version ne suffit pas à prouver les octets téléchargés. Le client demande désormais `ChecksumMode=ENABLED` au fournisseur ; les copies sont acceptées uniquement si deux observations identiques comportent un SHA-256 `FULL_OBJECT` valide et si ce SHA correspond aux octets téléchargés. L'origine de la preuve est inscrite au manifeste. Un checksum composite, absent, malformé ou divergent, et une simple métadonnée fournie par l'application, ne permettent plus de compter la copie comme préservée. Le test auparavant xfail strict de corruption même taille/ETag passe désormais sans xfail. Le fournisseur doit être digne de confiance : un checksum mensonger ne prouve rien ; le fait qu'il corresponde n'est pas un snapshot coordonné avec PostgreSQL. Cela bloque volontairement les fournisseurs ne renvoyant pas un checksum `FULL_OBJECT`, notamment certains multipart/anciens MinIO/R2 non qualifiés. Les originaux restent nécessaires, la rétention reste suspendue et la sauvegarde globale non complète.
- TDD `/tmp/seamtech-backup-checksum-rouge.log` : **5 échecs / 1 réussite** avant correction ; provenance ajoutée après `/tmp/seamtech-backup-preuve-type-rouge.log`. S3rver réel réseau refuse maintenant explicitement la copie sans checksum complet : `/tmp/seamtech-backup-checksum-s3rver.log` **12 réussis**, avant contrôle supplémentaire d'URL loopback et marquage CI. Ancien test positif conservé mais devenu **xfail strict** parce que S3rver ne fournit pas cette preuve ; pas de fausse qualification. `/tmp/seamtech-backup-checksum-pg-s3-final2.log` **231 réussis, 2 ignorés, 8 xfails** avant l'ajout du champ de preuve et les corrections de banc ci-dessous. `/tmp/seamtech-s3rver-ci-contrat-final.log` **2 réussis, 4 xfails, 2309 désélectionnés**, sans skip ; garde CI et inventaire ajustés au nouvel ensemble, sans changer les assertions de réussite de l'ancien test. Nouvelle preuve unitaire `/tmp/seamtech-collecte-et-checksum-final.log` : **13 réussis / 1 désélectionné**.
- Expurgation stockage : une exception d'envoi arbitraire pouvait être recopiée dans l'artefact persistant et une erreur de fichier absent incluait son chemin. Tests `/tmp/seamtech-stockage-fuite-rouge.log` **2 échecs**, puis `/tmp/seamtech-stockage-fuite-final.log` **37 réussis / 1 ignoré**. Message d'échec désormais générique avec type d'erreur. Audit global des erreurs et journaux encore ouvert.
- Banc éphémère reconstruit : paquet PyPI `pgserver==0.1.4` uniquement pour tests avec PostgreSQL **16.2**, clients 16.2, `unaccent` et `pg_trgm` compilés depuis sources PG16.2, pgvector **0.8.1** compilé depuis sources ; socket Unix privé sans TCP, UTF-8. Version ancienne et binaire de banc tiers : aucune qualification d'installation officielle ni recommandation production. S3rver **2.2.9** local loopback, Chromium **140** paquet npm, frontend `pnpm install --frozen-lockfile` et build réussis ; données de test synthétiques. Première commande PG `/tmp/seamtech-nouveau-banc-pg16-initial.log` n'a rien exécuté (nom de fichier de test inexistant) ; `/tmp/seamtech-nouveau-banc-pg16-final.log` **214 réussis / 5 ignorés / 5 xfails**.
- Suite PG initiale `/tmp/seamtech-suite-pg16-checksum.log` **2103 réussis / 5 échecs / 5 ignorés / 189 désélectionnés / 8 xfails** : deux tests du banc rôle limité perdaient les paramètres socket/port de l'URL ; deux gardes CI n'inventoriaient pas le nouveau test S3rver ; le module de recette modifiait le niveau des journaux dès la collecte et masquait un test de non-divulgation. TDD `/tmp/seamtech-banc-role-url-rouge.log`, `/tmp/seamtech-collecte-journaux-rouge.log` ; URL du rôle limitée reconstruit proprement avec contrôle de non-détournement, inventaire et garde CI complétés, journalisation recette activée seulement à l'exécution et restaurée. `/tmp/seamtech-suite-pg16-defauts-final2.log` **68 réussis**. `/tmp/seamtech-suite-pg16-checksum-final.log` **2112 réussis / 5 ignorés / 190 désélectionnés / 8 xfails / 4 avertissements**, sélection `not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue` : ne qualifie pas les exclus ni Redis. La fixture de journalisation recette a ensuite été placée en portée session uniquement quand la recette est active pour couvrir aussi les fixtures de session ; nouveau test de restauration de niveau passé dans `/tmp/seamtech-collecte-et-checksum-final.log`. Suite générale non relancée après cette seule retouche de fixture et ce test supplémentaire. Deux avertissements OpenAPI doublons restent à examiner.
- OCR navigateur : nouvelle campagne PostgreSQL 16.2/Chromium 140/Tesseract 5.5.1, `--retries=0 --repeat-each=25`, dépôt sélection fichier, OCR exact, recherche et SHA du téléchargement exigés : `/tmp/seamtech-nouveau-browser-ocr-25.log` **24 réussis / 1 échec**, confusion entre caractères visuellement proches, aucune assertion affaiblie. Les 25 ne qualifient pas le vrai glisser-déposer système, Edge, Windows ou la CI ; échec toujours bloquant. Mesure complémentaire sur 60 identifiants synthétiques avec chaque configuration sans retry : `/tmp/seamtech-ocr-config-60.log` modèle fast fra+eng PSM 3 **50/60**, PSM 6 **50/60**, eng+fra **50/60**, fra seul **47/60** ; autre série indépendante `/tmp/seamtech-ocr-modeles-60.log` fast **56/60**, best **55/60**, agrandissement ×2 **54/60**. Ces chiffres ne sont pas une métrique représentative du corpus réel ; aucune configuration n'a été déclarée remède ni activée par simple adaptation à la fixture.
- La base e2e unique de ce run a été explicitement supprimée ; après suites, seule `postgres` restait hors templates, PostgreSQL privé et S3rver arrêtés. Ruff global et `git diff --check` réussis avant ajout de cette note. Fichiers et logs `/tmp` éphémères ; les paquets de banc ne sont pas des dépendances produit.
- **Prochaine action précise :** résoudre sans deviner les ambiguïtés OCR de texte brut et rejouer l'ensemble des parcours navigateur sans retry ; poursuivre le confinement sur tout import/extraction/indexation/upload/reprises, les deux xfails de substitution, et la sauvegarde/restauration coordonnée PostgreSQL + `./data` + objets + hors site avec fournisseurs réellement qualifiés. Reprendre migration/rollback représentatifs, Redis/PG fencing, rotations mixtes, Windows/Docker/Chrome/Edge et volume, matrice backend/BFF, flux multipart, pannes, sécurité offensive, axe et endurance, puis CI 100 % verte sur commit exact. Aucun GO sur ces résultats partiels.

### État de reprise — correction, preuves et conservation des dépôts

- ECC appliquées : `security-review`, `tdd-workflow`, `verification-loop`. Branche et base inchangées, non commité ; P0/P1/P2 **NON PRÊTS**, aucun déploiement ni CI verte sur cet arbre.
- Défauts établis avant correctifs : `/tmp/seamtech-correction-preservation-rouge.log` 2 échecs (correction réouvrant des sources et effaçant une preuve de rapport) ; `/tmp/seamtech-manifeste-cle-rouge.log` et `/tmp/seamtech-manifeste-bucket-rouge.log` un échec chacun (ancienne preuve transférée à une autre clé ou un autre bucket) ; `/tmp/seamtech-manifeste-bucket-absent-rouge.log` et `/tmp/seamtech-batch-bucket-rouge.log` un échec chacun (preuve acceptée sans destination complète). Correction : la correction humaine ne réenvoie que les rapports, conserve les sources et recalcule sur l'inventaire entier ; les preuves historiques ne sont conservées que si clé et bucket ne contredisent pas le fichier, les rapports reconstruisent leur statut, et un succès exige clé **et** bucket non vides. `retry_upload` utilise la même règle pour les comptes et décisions.
- Les anciens rapports sont désormais marqués non vérifiés et cette révocation est enregistrée **avant** régénération, pour qu'une panne simple n'annonce pas l'ancienne preuve : `/tmp/seamtech-correction-crash-rouge.log` un échec avant correctif, scénario passé ensuite. La correction ne traite pas les courses entre deux corrections/reprises concurrentes, ni les substitutions de fichiers entre contrôles et utilisation ; elle ne garantit pas non plus une transaction atomique base/stockage. Les fichiers de rapport sont encore générés directement par chemin. Il faut un verrou/fencing cohérent, des sorties atomiques confinées et des tests interruption/processus/concurrence, sans présumer leur sûreté. Les retours de versions/fallbacks JSON et collisions doivent également être qualifiés.
- Avant génération, garde refusant rapport dans l'arbre source, sortie correspondant à un fichier source ou sortie existante non ordinaire/à liens multiples : `/tmp/seamtech-correction-ecrasement-rouge.log` et `/tmp/seamtech-correction-liens-rouge.log` reproduisaient écrasements ; tests ciblés passent. Ce contrôle est **non atomique**, ne protège pas contre une course de remplacement ou un même UID hostile : ne pas le présenter comme confinement complet. `retry_upload` et l'import initial rouvrent encore des chemins ; xfails stricts du scan toujours ouverts.
- Autre risque de destruction démontré : après preuve d'envoi, `worker` supprimait récursivement toute source, y compris fichiers ajoutés depuis l'inventaire ; `/tmp/seamtech-purge-mutation-rouge.log` 2 échecs (archive externe et staging). La rétention par âge supprimait également un dépôt non référencé sans preuve de copie : `/tmp/seamtech-retention-orphelin-rouge.log` un échec. Purge automatique du worker et élagage des dépôts stagés maintenant **suspendus**, même avec option de suppression activée ; corrections et reprises ne déclarent plus une purge autorisée. Cela protège les cas reproduits, mais les données vont s'accumuler et peuvent remplir le disque : gestion sûre, quotas/alertes et suppression explicitement prouvée restent à concevoir et qualifier. La rétention des rapports et des audits reste distincte, pas certifiée par ces tests. Les anciennes assertions demandant la suppression ont été adaptées à la nouvelle politique de conservation, et les deux régressions vérifient les octets et non seulement un statut.
- `/tmp/seamtech-preuve-bucket-final.log` 121 réussis / 1 ignoré / 1 désélectionné / 2 xfails avant changement de purge ; `/tmp/seamtech-purge-conserve-final2.log` 77 réussis / 1 ignoré avant révocation précoce ; `/tmp/seamtech-correction-crash-final.log` **127 réussis / 1 ignoré / 2 xfails** après révocation et inventaire. Suite filtrée `/tmp/seamtech-suite-conservation.log` **1602 réussis / 1 ignoré / 719 désélectionnés / 6 xfails / 2 avertissements**, sélection `not postgres and not s3 and not perf and not redis_queue and not ocr_suite` ; elle précède le dernier correctif de révocation des rapports, testé uniquement dans la campagne ciblée. Ruff global et `git diff --check` réussis après la dernière modification de code.
- **Prochaine action précise :** refaire suite filtrée et PG16 après dernier changement, étudier et tester interruptions et corrections/reprises concurrentes avec verrou/fencing et sorties de rapport atomiques confinées ; protéger de bout en bout l'identité des octets des sources (import et retry), identifier tous les autres chemins de suppression/retention et qualifier leur sécurité. Ensuite poursuivre toutes les portes déjà listées au checkpoint précédent, en particulier sauvegarde/restauration coordonnée, OCR navigateur 24/25, rotations mixtes, Windows/Docker/Chrome/Edge, charges, sécurité et CI exacte. Ne pas déployer ni déclarer GO.

### État de reprise — lectures épinglées, rapports et interruptions

- ECC appliquées : `tdd-workflow`, `security-review`, `api-design`, `postgres-patterns`, `verification-loop`. Branche `arena/b936b3c0-seamtech-search`, base `f1733c8`, modifications non commitées ; **P0/P1/P2 NON PRÊTS**, pas de déploiement ni CI verte sur commit exact.
- Une génération de rapports interrompue pouvait être rejouée comme si un fichier tronqué était un rapport valide : `/tmp/seamtech-reprise-rapport-partiel-rouge.log`. L'état `report_generation_pending` est désormais persisté avant génération ; `retry-upload` et le téléchargement des rapports refusent avec HTTP 409 tant que la correction n'est pas rejouée intégralement. Test API réel SQLite : écriture partielle simulée, erreur, refus reprise et téléchargements, nouvelle correction et reprise ; `/tmp/seamtech-download-rapport-tronque-rouge.log` rouge avant la garde de téléchargement. Le cache Redis `running` pouvait masquer la révocation : `/tmp/seamtech-redis-rapport-stale-rouge.log`, puis priorité à la base pendant cet état, y compris résultat imbriqué. Ces barrières n'éliminent PAS les courses entre opérations concurrentes.
- Une course entre corrections est démontrée par `/tmp/seamtech-corrections-concurrentes-rouge2.log` : deux modifications différentes, la seconde est perdue ; test strictement xfail dans `tests/test_correction_preservation.py`. Il faut concevoir un verrou/fencing distribué et une reprise après crash qui résistent à la perte de connexion et aux anciens workers, pas seulement un verrou de processus. `retry_upload` et import initial rouvrent aussi des sources non confinées ; les xfails de substitution d'import restent ouverts.
- Les rapports de correction ne doivent plus utiliser un dossier arbitraire lu en base : `/tmp/seamtech-correction-dossier-rouge2.log` ; le dossier doit désormais être `reports/<import_id>` et l'identifiant est borné. Un dossier de rapport substitué par lien a aussi été reproduit `/tmp/seamtech-correction-repertoire-lien-rouge.log`, et un remplacement pendant génération `/tmp/seamtech-correction-repertoire-course-rouge.log`. Sur POSIX/procfs uniquement : génération dans répertoire temporaire privé ancré, contrôle des fichiers réguliers, copies distinctes publiées par renommage relatif aux descripteurs, `fsync` et envoi depuis les copies temporaires, avec chemins métiers remappés. Une substitution juste avant envoi lisait auparavant des fichiers extérieurs `/tmp/seamtech-correction-envoi-course-rouge.log` ; test correspondant vert après changement. Le premier essai de publication par lien physique permettait une mutation du rapport public d'altérer sa copie privée (`/tmp/seamtech-rapport-lien-interne-rouge.log`) ; il a été remplacé par deux inodes distincts. Le repli JSON produisant deux rapports au même chemin est refusé avec état toujours incomplet. `/tmp/seamtech-correction-confinement-final.log` **18 réussis / 1 xfail** ; `/tmp/seamtech-correction-copie-privee-final.log` **87 réussis / 1 ignoré / 3 xfails** avant quelques tests supplémentaires. `/tmp/seamtech-correction-s3rver-reseau.log` : rapport PDF et DOCX réels envoyés puis relus via S3rver réseau local, octets de l'original synthétique inchangés, références logiques conservées ; avertissements versionnement émis par S3rver. Ce n'est pas une qualification MinIO/R2.
- Limites de cette stratégie : Windows refuse la génération confinée ; répertoire temporaire et copies publiées peuvent être altérés par un adversaire de même UID, montages et remplacement de racine ne sont pas prouvés, publication des deux fichiers/base/S3 non transactionnelle, nettoyage d'un crash dur non garanti, et la taille des rapports n'est pas encore bornée. Les anciens imports dont le chemin des rapports est hors du répertoire normal sont refusés, migration/reprise à concevoir. La modification d'une source ou d'un rapport en place pendant une lecture n'est pas empêchée. Ne pas conclure à un confinement général.
- `/tmp/seamtech-artifact-substitution-rouge.log` et `/tmp/seamtech-open-substitution-rouge.log` ont montré la divulgation d'un fichier extérieur en changeant le chemin entre validation et `FileResponse`. Routes historiques de téléchargement d'artefact et `/open` : sur POSIX/procfs, ouverture par composants sans liens, fichier ordinaire à lien unique, descripteur épinglé jusqu'à fin ASGI, fermeture en cas normal/erreur, support HTTP Range maintenu ; tests substitution après ouverture, lien symbolique, lien physique et 206/fermeture (`/tmp/seamtech-source-quarantaine-vert.log`, 6 réussis). L'accès aux originaux relocalisés en quarantaine a aussi été rétabli après `/tmp/seamtech-source-quarantaine-rouge.log`. Attention : ce n'est pas un snapshot immuable, `/preview` et les autres parcours n'ont pas été convertis, les racines de quarantaine/rapports sont autorisées globalement plutôt qu'isolées par import, même UID/montages/procfs, annulations et plateformes Windows restent à qualifier. `POST /open` peut continuer de révéler des chemins par son audit et d'autres branches ; audit global non achevé.
- Un cache S3 écrivait sur le chemin local de l'artefact absent : `/tmp/seamtech-artifact-cache-rouge.log`. Le repli passe maintenant l'objet en flux par l'API sans réécrire le chemin ; l'ancien test vérifie désormais l'absence de fichier plutôt que sa création, et continue à vérifier exactement les octets fournis. Nom Unicode en Content-Disposition encodé et contrôles retirés après `/tmp/seamtech-proxy-nom-unicode-rouge.log`. Envoi objet vérifié : flux fermé même en erreur et lecture bornée à la taille du fichier local, après `/tmp/seamtech-relecture-flux-rouge.log` (2 échecs) et `/tmp/seamtech-relecture-taille-rouge.log` (1). Si relecture désactivée, checksum fournisseur explicitement demandé après `/tmp/seamtech-verification-checksum-mode-rouge.log`. `/tmp/seamtech-verification-flux-checksum-final.log` **68 réussis / 1 désélectionné**, `/tmp/seamtech-open-artifact-confine-final.log` **132 réussis / 2 xfails**. La lecture S3 reste soumise aux courses objet/version et au fournisseur ; le proxy n'établit pas de snapshot immuable.
- Rétention : `/tmp/seamtech-retention-lien-rapport-rouge.log` a démontré la suppression d'un original sous une racine `reports` remplacée par lien. Élagage automatique des rapports aussi suspendu ; test de conservation renforcé `/tmp/seamtech-retention-rapport-conserve-final.log` **54 réussis**. Avec le staging déjà conservé, cela accroît encore le risque de saturation disque : il faut un protocole d'identité/suppression sûr, quotas et alertes avant production. Rétention des journaux d'audit demeure active, pas qualifiée comme sauvegarde.
- Confidentialité : `/tmp/seamtech-quarantaine-fuites-rouge.log` 3 échecs (chemins et exceptions dans logs/quarantaine), puis `/tmp/seamtech-quarantaine-fuites-final.log` **34 réussis / 1 ignoré / 1 xfail**. Les exceptions du traitement et de la boucle worker divulguaient aussi leurs messages (`/tmp/seamtech-worker-exception-fuite-rouge.log`, `/tmp/seamtech-boucle-worker-fuite-rouge.log`) : types génériques enregistrés, pas de traceback brut sur ces branches ; `/tmp/seamtech-worker-fuite-conservation-final.log` **35 réussis / 1 xfail**. L'échec de mise à jour du chemin du job après mise en quarantaine est désormais signalé, sans exception brute (`/tmp/seamtech-quarantaine-base-erreur-rouge.log` avant correctif). **Mais** le déplacement et la base ne sont pas atomiques : crash ou échec peut laisser un job au chemin périmé, et la boucle peut encore le reprogrammer avec l'ancienne charge. Corriger et tester ces fenêtres ; ne pas prétendre la reprise fiable. D'autres logs et sorties d'erreur restent à auditer.
- Suite isolée `/tmp/seamtech-suite-conservation-revocation.log` **1603 réussis / 1 ignoré / 719 désélectionnés / 6 xfails** ; `/tmp/seamtech-suite-lecture-confinee.log` **1619 réussis / 1 ignoré / 719 désélectionnés / 7 xfails**. Ces deux suites précèdent ou chevauchent les dernières modifications, ne certifient pas l'arbre final. Suite PG16 privée `/tmp/seamtech-suite-pg16-preservation.log` **2126 réussis / 7 ignorés / 190 désélectionnés / 8 xfails** et `/tmp/seamtech-suite-pg16-lecture-et-rapports.log` **2147 réussis / 7 ignorés / 190 désélectionnés / 9 xfails / 4 avertissements** ; la seconde a été lancée avant les changements finaux de rétention et de worker et avant quelques tests complémentaires, donc à relancer. Après chacune, seule `postgres` subsistait hors templates, serveur arrêté. Contrat S3rver `/tmp/seamtech-s3rver-final-revalidation.log` **2 réussis / 2353 désélectionnés / 4 xfails** ; serveur arrêté, bucket jetable du scénario correction supprimé. Ruff et `git diff --check` verts avant cette note. Logs et bancs `/tmp` éphémères.
- **Prochaine action précise :** attendre/consigner `/tmp/seamtech-suite-correction-confinement-final.log` si encore en cours ; rejouer la suite PG sur arbre stabilisé. Priorité au xfail de corrections concurrentes, au protocole de relocalisation/quarantaine transactionnel ou fail-closed et aux reprises sans réouverture non confinée ; étendre les protections de lecture à `/preview` et aux autres routes, qualifier les collisions de rapports, interruptions, mêmes UID et Windows. Poursuivre ensuite sauvegarde/restauration complète et coordonnée, OCR navigateur toujours **24/25** sans retry, rotations mixtes, Redis/PG fencing, Windows/Docker/Chrome/Edge, charge annuelle réelle, matrice backend/BFF, multipart, pannes, sécurité offensive, axe et endurance ; obtenir CI 100 % verte sur commit exact. Aucun GO.
- Suite filtrée lancée après les modifications produit de cette étape : `/tmp/seamtech-suite-correction-confinement-final.log` **1632 réussis / 1 ignoré / 719 désélectionnés / 7 xfails / 2 avertissements**, en 169 s ; sélection `not postgres and not s3 and not perf and not redis_queue and not ocr_suite`. Les tests PG, Redis, fournisseurs, performance, endurance et autres exclusions ne sont pas couverts. Les portes et la prochaine action ci-dessus restent inchangées.

### État de reprise — conservation en place et preuves d'accès

- ECC appliquées : `search-first`, `tdd-workflow`, `security-review`, `redis-patterns`, `verification-loop`. Branche `arena/b936b3c0-seamtech-search`, base `f1733c8`, modifications non commitées. P0/P1/P2 **NON PRÊTS**, aucun déploiement, aucune CI verte sur commit exact.
- `/tmp/seamtech-quarantaine-crash-rouge.log` reproduit l'interruption entre déplacement et mise à jour DB : chemin de reprise perdu. Mesure conservatoire : `mettre_en_quarantaine` ne déplace plus la source stagée, ne modifie ni chemin DB ni charge, annonce explicitement conservation sur place et absence de déplacement physique ; les sources externes sont également laissées intactes. Anciennes attentes de déplacement adaptées ; test de crash et conservation des sélections. Ce n'est **pas** une quarantaine physique ni un protocole de relocalisation qualifié.
- Reprise Redis réelle : la première exécution `/tmp/seamtech-reprise-r3-staging-redis7.log` échouait parce qu'un artefact d'envoi échoué persistait quand même une clé objet, empêchant l'indexeur de réimporter le fichier protégé. TDD `/tmp/seamtech-import-cle-echec-rouge.log` : désormais l'import ne publie une clé/bucket/timestamp dans `files` et `documents` qu'avec preuve individuelle d'envoi ; la clé incertaine reste dans l'inventaire `artifacts` pour investigation, pas comme adresse acquise. `/tmp/seamtech-reprise-r3-staging-redis7-vert.log` **2 réussis**, dont échec total suivi d'une reprise worker réellement exécutée via Redis privé 7.4.7 ; source et sélection identiques, sans déplacement. Binaire Redis 7.4.7 compilé dans `/tmp/redis-7.4.7` depuis tag upstream, non intégré au produit. Ce cas ne prouve ni reprise partielle, ni absence de doublons distants, ni sûreté TOCTOU.
- `/tmp/seamtech-reprise-partielle-protegee-rouge.log` : après succès partiel, une nouvelle tentative de l'import entier échoue sur document déjà protégé ; test conservé `xfail(strict=True)` dans `tests/test_import_workflow.py`. Il faut concevoir une reprise idempotente, non destructrice et transactionnelle qui conserve les références existantes et garantit l'identité des octets, sans contourner la protection indexeur. `retry_upload` n'établit pas à lui seul l'atomicité DB/S3 ni la synchronisation catalogue. Les envois à résultat ambigu peuvent avoir laissé un objet distant : ne pas le considérer inexistant.
- `/tmp/seamtech-balayage-staging-rouge.log` : le crawler balaie et indexe le staging conservé si la racine de recherche englobe le répertoire de données ; test `xfail(strict=True)` dans `tests/test_quarantaine_confidentialite.py`. L'exclusion et ses conséquences sur la suppression d'anciennes lignes indexées, sources déjà publiées, racines imbriquées, symlinks et races restent à concevoir/qualifier ; aucune isolation ou exclusion générale n'est revendiquée.
- `/tmp/seamtech-artifact-cle-echec-rouge.log` : la route d'artefacts utilisait une simple clé même pour un échec. Elle exige maintenant une preuve individuelle du manifeste correspondant au chemin et au bucket configuré pour redirection/proxy ; un fichier local autorisé reste accessible par descripteur épinglé. `/tmp/seamtech-artefact-preuve-bucket-final.log` **3 réussis** (échec, non vérifié, autre bucket). Les anciens imports sans preuve complète ne sont plus servis depuis l'objet distant : migration/requalification à prévoir, pas d'hypothèse que leur objet est valide. L'objet peut changer après preuve : absence de version épinglée/snapshot reste une limite.
- `/tmp/seamtech-retry-cle-non-verifiee-rouge.log` : reprise avec source perdue réexposait une clé historique malgré statut échec. `retry_upload` ne publie désormais clé/bucket/date dans `files` qu'avec preuve valide, tout en gardant la clé historique dans le manifeste. `/tmp/seamtech-retry-cle-non-verifiee-final.log` **141 réussis / 5 xfails** (ciblés avec Redis privé), Ruff global et `git diff --check` verts ; ces tests ne qualifient pas l'état réel des anciens documents déjà persistés.
- Suite locale `/tmp/seamtech-suite-quarantaine-artefacts.log` **1696 réussis / 69 ignorés / 587 désélectionnés / 8 xfails**, commande `pytest -q -m 'not postgres and not redis_queue and not s3rver'` ; skips et exclusions non qualifiés. Suite PG16 privée `/tmp/seamtech-suite-pg16-quarantaine-artefacts.log` **2126 réussis / 34 ignorés / 190 désélectionnés / 10 xfails**, commande `pytest -q -m 'not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue'` avec socket privé PG16.2 et Redis privé 7.4.7 ; ces suites précèdent le dernier correctif `retry_upload` et ne couvrent donc pas tout l'arbre actuel. Plusieurs skips tiennent à l'environnement/configuration, notamment OCR ; ne pas comparer les chiffres aux suites antérieures différemment configurées. Après PG seule la base `postgres` subsistait hors templates, serveur arrêté. Les logs `/tmp` sont éphémères.
- **Prochaine action précise :** corriger la reprise après envoi partiel sans effacer les preuves ni outrepasser la protection des références ; analyser l'exclusion du staging conservé et son impact sur les index et historiques, en commençant par les deux xfails stricts cités, puis relancer campagnes locales, PG16 et Redis7. Poursuivre ensuite toutes les portes listées précédemment, notamment confinement intégral import/reprise, sauvegarde/restauration coordonnée, OCR navigateur 24/25, rotations mixtes, fencing Redis/PG, fournisseurs et plateformes réels, charge, sécurité, accessibilité et endurance, puis CI complète verte sur commit exact. Aucun GO.

### État de reprise — rapports initiaux, garde de reprise et recouvrement du scan

- ECC relues/appliquées : `search-first`, `tdd-workflow`, `security-review`, `verification-loop`, `redis-patterns`. Branche `arena/b936b3c0-seamtech-search`, base `f1733c8`, modifications non commitées ; P0/P1/P2 **NON PRÊTS**, pas de déploiement ni de CI verte sur SHA exact.
- `/tmp/seamtech-import-rapport-liens-rouge.log` reproduit deux destructions/écritures hors périmètre pendant l'import initial : rapport symbolique pointant vers un original et racine des rapports symbolique. `/tmp/seamtech-import-rapport-arbre-source-rouge.log` montre aussi la substitution d'un fichier existant si la sortie de rapport est dans l'arbre source. L'import initial utilise désormais le mécanisme POSIX/procfs d'écriture ancrée et de copies privées déjà utilisé pour les corrections ; il refuse un dossier de rapports dans l'arbre source et des identifiants hors `[A-Za-z0-9_-]{1,128}`. Rapports générés puis publiés et envoyés depuis des copies privées, les chemins persistés restent logiques ; `/tmp/seamtech-import-rapports-confines-final.log` **11 réussis**, `/tmp/seamtech-import-rapports-confines-cible.log` **162 réussis / 5 xfails**. Le test substitue aussi le rapport public durant l'envoi et vérifie que les octets envoyés proviennent toujours de la copie privée. Ce n'est pas un snapshot immuable du rapport public : une mutation en place peut ensuite fausser la lecture locale ; aucune qualification Windows, même UID hostile, crash dur ou transaction multifichiers.
- `/tmp/seamtech-import-id-confinement-rouge.log` : 6 échecs avant validation d'identifiant ; le test couvre désormais aussi l'identifiant vide. Les liens et l'ID ont été contrôlés sur données synthétiques. Le fallback JSON double est refusé par le générateur confiné lorsque les deux rapports aboutissent à la même sortie ; aucune solution de compatibilité pour ce cas ni pour les anciennes installations hors POSIX/procfs.
- `/tmp/seamtech-reimport-partiel-garde-rouge.log` prouve qu'une reprise complète d'un import déjà partiellement préservé régénérait les rapports et lançait des envois supplémentaires avant le refus de l'indexeur. La garde bloque désormais cette reprise avant génération/envoi quand des preuves ou références d'objet antérieures existent. Ce garde-fou n'est **pas** la reprise demandée, n'est pas atomique contre des tentatives concurrentes et ne ferme pas les fenêtres où aucun import n'est encore persisté. `test_reprise_import_apres_envoi_partiel_conserve_documents_deja_proteges` reste xfail strict. `/tmp/seamtech-retry-liaison-catalogue-rouge.log` démontre en outre que `retry_upload` peut annoncer un envoi complet dans `imports.files` sans relier les clés dans `documents` ; nouveau xfail strict `test_reprise_envoi_relie_les_objets_verifies_au_catalogue`. Établir l'identité des octets et mettre à jour import + catalogue sous verrou/fencing, sans écraser les documents déjà liés, avant de déclarer P0.2 prête.
- Scan : `/tmp/seamtech-scan-staging-chevauchement-rouge.log` **4 échecs** avant garde ; l'exclusion simple pourrait faire supprimer les lignes déjà indexées par `remove_missing`. Le crawler refuse maintenant **avant tout parcours** une racine de scan qui recouvre le staging ou en est un enfant, même si le staging n'existe pas encore ; `/tmp/seamtech-scan-staging-apparition-rouge.log` reproduisait sa création pendant le parcours. Tests de rollback de l'index, racines multiples, alias symbolique et racine indépendante : `/tmp/seamtech-scan-staging-inconditionnel-vert.log` **49 réussis / 1 xfail**. Deux tests génériques ont été configurés avec une racine indépendante du répertoire applicatif, au lieu de balayer tout le dépôt de code. Cela bloque volontairement les installations dont les racines de recherche englobent le staging : pas une exclusion transparente ni une solution finale ; le test d'exclusion demeure xfail. Substitution/montage/race après précontrôle et Windows restent non qualifiés.
- Confidentialité : `/tmp/seamtech-api-redis-exception-rouge.log` **2 échecs** : les échecs Redis après correction ou reprise divulguaient les exceptions brutes dans le journal. Les deux branches enregistrent désormais le seul type d'erreur ; `/tmp/seamtech-api-redis-exception-final.log` **133 réussis / 6 désélectionnés / 6 xfails** ciblés. Ce contrôle ne constitue pas un audit exhaustif des journaux.
- S3rver réseau jetable : `/tmp/seamtech-import-initial-s3rver.log` PASS, un PDF source et deux rapports réels envoyés et relus sur S3rver avec conservation de la source et chemins logiques ; bucket supprimé et serveur arrêté. `/tmp/seamtech-s3rver-rapport-initial-confine.log` **2 réussis / 4 xfails / 2375 désélectionnés**. Pas de qualification MinIO actuel/R2, de verrouillage ni de version d'objet ; le premier lancement du script sans `PYTHONPATH` avait échoué avant connexion.
- Campagnes après garde scan et rapports : `/tmp/seamtech-suite-guard-rapport-scan-final.log` **1653 réussis / 719 désélectionnés / 9 xfails**, sélection `not postgres and not s3 and not perf and not redis_queue and not ocr_suite` ; `/tmp/seamtech-suite-pg16-guard-rapport-scan-final.log` **2174 réussis / 6 ignorés / 190 désélectionnés / 11 xfails**, PostgreSQL 16.2, sélection `not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue` ; `/tmp/seamtech-suite-redis7-guard-rapport-scan-final.log` **136 réussis / 2244 désélectionnés / 1 xfail**, PostgreSQL 16.2 et Redis privé 7.4.7, URL Redis de sélection volontairement injoignable. La première campagne Redis sans URL avait **1 échec / 21 réussis / 115 ignorés** parce qu'un test du banc importait l'ancien nom de R3 ; référence corrigée, puis campagne complète ci-dessus. Ces trois suites précèdent le dernier correctif d'expurgation API et le nouveau xfail catalogue ; elles ne certifient pas tout l'arbre actuel. Après tests seule `postgres` subsistait hors templates et PG arrêté ; Ruff global et `git diff --check` verts.
- **Prochaine action précise :** résoudre de façon sûre les deux xfails de reprise partielle et liaison catalogue, ainsi que l'exclusion transparente du staging sans pertes de lignes ; prendre en compte mutation et identité des octets, transaction et fencing, anciennes données, rapports et tentatives concurrentes. Rejouer ensuite les campagnes PG/Redis/local sur arbre stabilisé. Poursuivre sans abandonner toutes les autres portes précédemment listées : confinement source intégral, sauvegarde et restauration coordonnées, OCR navigateur 24/25, rotations mixtes, fournisseurs et plateformes réels, charge, sécurité, accessibilité, endurance et CI verte sur SHA exact ; aucun GO.
- Preuve complémentaire `/tmp/seamtech-retry-source-modifiee-rouge.log` : après preuve initiale d'un fichier, mutation locale puis reprise des fichiers manquants, `retry_upload` annonce `all_verified=true` sans vérifier que les octets actuellement présents sont ceux de la preuve historique. Nouveau test strictement xfail `test_reprise_ne_valide_pas_une_source_modifiee_apres_preuve`. Le booléen ne prouve donc pas l'identité actuelle ni l'immuabilité du dossier ; ne pas déclencher de purge ni qualifier la reprise complète sur cette base. Le correctif exige une preuve des octets et des versions/lectures épinglées, pas seulement taille, date ou hash d'un chemin rouvert.

### État de reprise — confinement des renvois d'artefacts

- ECC relues/appliquées : `search-first`, `tdd-workflow`, `security-review`, `architecture-decision-records`, `verification-loop`. Branche `arena/b936b3c0-seamtech-search`, base `f1733c8`, modifications non commitées. P0/P1/P2 **NON PRÊTS**, aucune fusion, aucun déploiement ni CI verte sur SHA exact.
- `/tmp/seamtech-reprise-fuite-hors-source-rouge.log` : deux échecs reproduisent l'envoi d'un fichier hors source via chemin persisté ou lien symbolique dans le staging. `retry_upload` vérifie maintenant les chemins absolus, racine autorisée et rapports `reports/<id>`, ouvre les racines et les composants sans suivre les liens, refuse les fichiers non ordinaires ou liés physiquement et copie un fichier à la fois dans un dossier privé avant l'envoi. Taille de copie limitée par `max_file_size_bytes` ; des fichiers plus gros sont donc explicitement refusés, ce changement n'est pas une qualification du volume réel.
- La clé S3 et le chemin de métadonnées restent fondés sur l'identité **logique lexicale**, non sur la copie temporaire ni sur une résolution du chemin original après substitution. Tests de fichier et parent symboliques, lien physique, chemin extérieur, limite de taille, substitution pendant l'envoi, contenu, clé, retour logique et accès API : `/tmp/seamtech-reprise-confinee-preuve-finale.log` **11 réussis**, puis garde supplémentaire de racine ; `/tmp/seamtech-reprise-confinee-final-cibles.log` **176 réussis / 1 ignoré / 6 xfails**. `/tmp/seamtech-reprise-api-erreur-rouge.log` 3 échecs avant traduction générique en HTTP 409, sans exception/chemin dans la réponse. `/tmp/seamtech-reprise-boucle-lien-rouge.log` reproduit une boucle de lien, désormais convertie en refus générique.
- `/tmp/seamtech-reprise-racine-remplacee-rouge2.log` démontre qu'une racine réouverte après remplacement pouvait envoyer une autre arborescence. Les racines nécessaires sont désormais ouvertes une seule fois, épinglées et comparées au chemin courant avant et après chaque copie/envoi ; la substitution de répertoire est refusée avant lecture du second fichier. Une partie déjà envoyée peut rester orpheline si un contrôle ultérieur échoue, et la comparaison n'est pas transactionnelle avec la base ni résistante à toutes les courses après son dernier contrôle. Racine déjà substituée avant ouverture, même UID, montages, liens Windows, mutation en place pendant ou après copie et attaque sur le répertoire temporaire restent non qualifiés. L'import initial et d'autres chemins rouvrent encore des sources : pas de confinement global revendiqué.
- `/tmp/seamtech-retry-reseau-racine-epinglee.log` PASS sur S3rver réseau isolé : échec total simulé puis reprise de trois fichiers, octets relus, chemins logiques et original inchangés ; bucket jetable supprimé, serveur arrêté. Pas une preuve MinIO actuel/R2 ni de stockage versionné ; trois avertissements de versionnement S3rver. `/tmp/seamtech-s3rver-reprise-confinee.log` **2 réussis / 4 xfails** sur version précédant la garde racine finale. Les requêtes de reprise envoient désormais les fichiers séquentiellement et créent un client/une copie par fichier : coût et limites disque/concurrence sur lots réels restent à mesurer, surtout pour 100 000 fichiers.
- Suites ayant précédé la dernière garde racine : `/tmp/seamtech-suite-reprise-confinee.log` **1666 réussis / 719 désélectionnés / 11 xfails**, `/tmp/seamtech-suite-pg16-reprise-confinee.log` **2187 réussis / 6 ignorés / 190 désélectionnés / 13 xfails**, `/tmp/seamtech-suite-redis7-reprise-confinee.log` **136 réussis / 2259 désélectionnés / 1 xfail**. Après PG seule `postgres` subsistait hors templates et le serveur a été arrêté. Ces résultats ne qualifient pas les dernières modifications. Ruff global et `git diff --check` verts après les ciblés.
- Suites relancées après garde finale en cours : `/tmp/seamtech-suite-reprise-racine-finale.log`, `/tmp/seamtech-suite-pg16-reprise-racine-finale.log`, `/tmp/seamtech-suite-redis7-reprise-racine-finale.log` ; attendre résultats et codes `.exit`, puis vérifier les bases jetables restantes et arrêter le PostgreSQL privé. Redis privé 7.4.7 et PG16.2 banc seulement ; URL Redis de sélection volontairement injoignable.
- **Prochaine action précise :** consigner ces résultats, puis résoudre la reprise partielle/idempotente et la liaison au catalogue sans modifier les pièces déjà protégées, tester l'identité des octets et les preuves persistées face aux mutations, collisions et crashs ; traiter les xfails stricts et l'exclusion transparente du staging. Continuer toutes les portes précédentes (sauvegarde/restauration coordonnée, OCR 24/25, rotations mixtes, fencing Redis/PG, fournisseurs/Windows/Docker/Edge, volume et endurance, sécurité, accessibilité et CI verte sur commit exact). Aucun GO.
- Suites après garde racine et correctif boucle de lien terminées avec code 0 : `/tmp/seamtech-suite-reprise-racine-finale.log` **1668 réussis / 719 désélectionnés / 11 xfails** ; `/tmp/seamtech-suite-pg16-reprise-racine-finale.log` **2189 réussis / 6 ignorés / 190 désélectionnés / 13 xfails** ; `/tmp/seamtech-suite-redis7-reprise-racine-finale.log` **136 réussis / 2261 désélectionnés / 1 xfail**. Mêmes sélections et variables d'environnement que l'entrée précédente ; ces campagnes locales ne remplacent ni les exclusions, ni la CI distante. Après test, seule `postgres` subsistait hors templates ; PG privé arrêté. Ruff global et `git diff --check` verts. Prochaine action et réserves ci-dessus inchangées.

### Reprise, concurrence et état durable — travail en cours

- **P0/P1/P2 NON PRÊTS ; aucun déploiement, aucun GO et aucune CI verte sur le commit exact.** Base `f1733c8`, branche de travail non committée. ECC appliquées sur cet incrément : `search-first`, `tdd-workflow`, `postgres-patterns`, `redis-patterns`, `security-review`, `error-handling`, `verification-loop`.
- Corrections, reprises et désormais import initial/rejoué d'un même identifiant utilisent un verrou coopératif non bloquant : `flock` par fichier sur SQLite/POSIX ; advisory lock de session sur connexion PostgreSQL dédiée ; conflit API 409 et perte de session détectée à la libération 503. `/tmp/seamtech-verrou-import-vs-correction-rouge.log` reproduit un réimport écrasant une correction ; le test vérifie maintenant le refus concurrent puis la correction après libération. `/tmp/seamtech-verrou-etat-durable-cibles.log` **149 réussis / 1 ignoré / 9 xfails** après ces modifications et les tests d'état durable. Tests PG réels de conflit et de mort de session sur serveur privé ; le test import PG simule le corps de l'import, ne qualifie pas l'import complet. Tests SQLite multiprocessus et mort de détenteur. Ruff et `git diff --check` verts avant cette note.
- **Ce n'est pas un fencing distribué** : deux xfails stricts montrent le remplacement du fichier de verrou par le même UID et la perte d'une session PG avant fin des effets externes, avec possibilité de mutations concurrentes. La détection PG tardive n'annule aucun effet déjà émis. Anciennes versions, autres chemins non coopératifs, Windows et systèmes de fichiers réseau non couverts ; fichiers de verrou persistants, capacité et permissions à qualifier. La reprise n'est pas validée par ces verrous : catalogue, identité des octets, mutations source et publication objet/base atomique restent ouverts.
- `/imports/{id}` n'utilise plus les données d'un cache Redis périmé s'il existe un résultat ou job durable. État de job actif conservé avec résultat durable ; annulation durable et révocation de rapports priment même sur un Redis `pending`. Régressions reproduites dans `/tmp/seamtech-redis-pending-perime-rouge.log`, `/tmp/seamtech-statut-actif-rouge.log`, `/tmp/seamtech-job-annule-cache-rouge.log`, `/tmp/seamtech-job-annule-rapport-rouge.log` avant leurs correctifs. Le repli Redis quand aucune ligne durable n'existe reste à examiner ; ce n'est pas une résolution de l'interstice Redis–PG ni un fencing.
- Sandbox reconstruite : PostgreSQL 16.2 du paquet de banc `pgserver`, socket `/tmp/seamtech-pgmutex/socket`, port 55498, extensions `unaccent`/`pg_trgm` PG16.2 et pgvector 0.8.1 compilées pour tests ; Redis 7.4.7 compilé, serveur privé socket `/tmp/seamtech-redis-mutex/redis.sock`, sans TCP. Ce ne sont pas des qualifications de distribution ou de plateforme. Les outils Python `bison`/`flex` installés par erreur puis retirés ont brièvement masqué le namespace `tests`; ils ont été désinstallés avant les suites complètes.
- Campagnes relancées : `/tmp/seamtech-suite-verrou-etat-local.log`, `/tmp/seamtech-suite-verrou-etat-pg.log`, `/tmp/seamtech-suite-verrou-etat-redis.log` ; **résultats non encore connus** (lignes `EXIT=` en fin de log). Sélections respectives : `not postgres and not s3 and not perf and not redis_queue and not ocr_suite`, `not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue`, et `redis_queue`. PG et Redis privés, URL Redis de sélection volontairement injoignable ; ces filtres ne valident pas les exclusions, fournisseurs ou CI. Vérifier l'issue et les bases jetables, puis arrêter les services si non nécessaires.
- **Prochaine action précise :** obtenir les résultats des trois suites, corriger tout échec et rejouer ; couvrir perte de session pendant import initial et interruption/reprise, erreurs d'acquisition et permissions, puis concevoir un fencing qui couvre les effets externes et tous les producteurs. Corriger ensuite les xfails de liaison catalogue et d'identité des octets en reprise, sans présumer `all_verified` suffisant. Poursuivre toutes les portes P0/P1/P2 ci-dessus : confinement source complet, sauvegarde/restauration coordonnées, OCR navigateur 24/25, rotations mixtes, S3 MinIO/R2 réels, Windows/Docker/Chrome/Edge, volume annuel, matrice backend/BFF, multipart, pannes, sécurité, accessibilité, endurance et CI verte au SHA exact. Aucun GO.
- Fin des trois campagnes ci-dessus, code 0 : locale **1666 réussis / 14 ignorés / 723 désélectionnés / 12 xfails**, PG16 **2176 réussis / 34 ignorés / 190 désélectionnés / 15 xfails**, Redis7 **136 réussis / 2278 désélectionnés / 1 xfail**. Les skips incluent l'OCR non disponible dans ce sandbox ; chiffres non comparables aux campagnes disposant de Tesseract. Les exclusions et xfails ne sont pas validés ; deux avertissements OpenAPI doublons restent présents. Seule la base `postgres` restait hors templates ; Redis et PostgreSQL privés arrêtés. Aucun résultat ne remplace la CI du commit exact. Prochaine action inchangée.

### Empreinte des preuves et observation de reprise — travail en cours

- ECC appliquées : `search-first`, `tdd-workflow`, `postgres-patterns`, `security-review`, `verification-loop`. **P0/P1/P2 NON PRÊTS** ; aucune CI verte au SHA exact, aucun déploiement. Branche et base inchangées, travail non committé.
- Les preuves S3 issues de la relecture ou du checksum fournisseur explicite `FULL_OBJECT` portent maintenant le SHA-256 des octets effectivement vérifiés ; absence de SHA sur les échecs ou une métadonnée déclarative. Ce SHA est conservé dans les artefacts et manifestes si clé/bucket cohérents ; pas d'inférence depuis `documents.content_hash`. La reprise vérifie maintenant par lecture confinée et copies bornées les SHA de l'inventaire complet, compare la liste source avant/après, refuse les fichiers spéciaux et n'annonce pas `all_verified` si mutation, ajout, disparition ou SHA historique manquant est observé. Les anciennes preuves individuelles ne sont pas effacées et la purge reste suspendue. Les preuves antérieures sans SHA peuvent donc rendre une reprise incomplète même si l'objet existe ; aucun rattrapage automatique n'a encore été qualifié.
- `/tmp/seamtech-empreinte-preuve-rouge.log`, `/tmp/seamtech-empreinte-manifeste-rouge.log`, `/tmp/seamtech-reprise-source-sha-rouge.log`, `/tmp/seamtech-reprise-source-ajout-rouge.log` et `/tmp/seamtech-reprise-ancien-sans-sha-rouge.log` reproduisent les manques avant correctif. `/tmp/seamtech-reprise-sha-identite-cibles.log` **213 réussis / 6 ignorés / 7 xfails**, sans service PG ; `/tmp/seamtech-reprise-identite-pg16.log` **4 réussis** dont un import/reprise réel sur base PG jetable avec S3 en mémoire. `/tmp/seamtech-reprise-sha-s3rver.log` : **PASS** réseau S3rver privé, panne partielle, reprise, relecture des objets, SHA, détection de mutation et originaux conservés ; bucket synthétique supprimé. S3rver n'est pas une qualification MinIO/R2. Ruff et `git diff --check` réussis avant cette note.
- **Limites décisives** : cette observation n'est pas un snapshot transactionnel. `/tmp/seamtech-reprise-interstice-identite-rouge.log` prouve qu'une source modifiée après le contrôle et avant l'enregistrement peut encore produire `all_verified=true` : xfail strict. La mutation lors de l'import initial, le même UID, les montages, Windows, la capacité/coût des copies et l'ajout après contrôle ne sont pas qualifiés ; l'indicateur n'autorise pas la purge et ne doit pas être pris comme preuve durable de l'identité actuelle. `/tmp/seamtech-reprise-catalogue-apres-sha-rouge.log` démontre toujours `all_verified=true` sans liaison des clés au catalogue : xfail strict. Relier des objets par simple chemin ou métadonnées sans prouver que le document indexé correspond aux octets serait dangereux ; il faut concevoir la capture de l'identité utilisée pour extraction, indexation et envoi ainsi que leur publication transactionnelle/fencée.
- Campagnes relancées en cours : `/tmp/seamtech-suite-reprise-sha-local.log` et `/tmp/seamtech-suite-reprise-sha-pg.log`, mêmes sélections filtrées que précédemment, issues **non encore connues** ; attendre `EXIT=`. PG16 privé et S3rver sont actuellement actifs ; vérifier les bases jetables, nettoyer et arrêter. Les campagnes antérieures ne qualifient pas les dernières modifications.
- **Prochaine action précise :** obtenir les résultats et corriger toute régression ; résoudre le faux succès en interstice et la liaison catalogue avec identité bout en bout, reprise idempotente et publication/fencing, puis tester interruptions, conflits, anciennes preuves, volume et vrais fournisseurs. Continuer toutes les portes P0/P1/P2 déjà listées : sauvegarde/restauration coordonnées, OCR navigateur 24/25, rotations mixtes, plateformes et navigateur réels, charge, sécurité, accessibilité, endurance, CI complète verte au SHA exact. Ne pas déclarer GO.

### Suspension conservatoire du succès global — suite

- Les deux premières suites après SHA ont révélé une rupture de sérialisation : locale **1 échec / 1674 réussis**, PG **1 échec / 2185 réussis**, même test `test_batch_d_artefacts_serialisable_pour_l_api` ; `sha256=None` n'est désormais pas sérialisé, sans modifier le contrat ancien. `/tmp/seamtech-sha-compat-api-final.log` **46 réussis / 1 ignoré / 1 xfail**. Une suite locale rejouée a donné **1675 réussis / 14 ignorés / 724 désélectionnés / 12 xfails**, avant les changements ultérieurs ; suite PG rejouée interrompue volontairement pour modifier la politique de succès, elle ne qualifie rien.
- L'observation ponctuelle ne ferme pas l'interstice source ni la liaison catalogue ; pour ne pas présenter une opération incomplète comme un succès global, `all_verified` est désormais conservatoirement **faux pour import initial, correction et reprise**, même si le transport a réussi. `transport_verifie` distingue les preuves individuelles effectivement obtenues ; pour l'import initial il exige aussi l'égalité de l'inventaire attendu et des artefacts retournés. `upload_status=partial` et, quand le stockage est configuré, `status=needs_review` signalent la réserve. Les rapports corrigés conservent aussi leur SHA prouvé ; les anciens SHA sont révoqués avant régénération. Les originaux et les objets vérifiés sont conservés ; la purge demeure suspendue.
- **Conséquence fonctionnelle importante : aucun de ces imports ne peut actuellement atteindre un succès global de préservation.** Cette suspension de sécurité n'est pas la solution finale demandée : rétablir le succès après véritable snapshot/identité de bout en bout, liaison catalogue atomique et fencing, pas en supprimant la garde. Sans stockage configuré, le comportement de développement reste distinct. Les tests de résultat positif ont été adaptés pour vérifier explicitement le transport et les preuves de chaque objet, tout en exigeant le refus du succès global ; aucun test de sécurité n'a été supprimé. Le test catalogue reste xfail strict et vérifie toujours la liaison manquante.
- Le worker place en `needs_review` un transport complet dont le succès global est suspendu, sans renvoi automatique ni déplacement ; sa redélivrance est reconnue pour éviter un nouvel upload. Un échec de transport réel reste `upload_incomplete`. La lecture API conserve `needs_review` face à l'état d'extraction terminé. Tests reproduisant les défauts : `/tmp/seamtech-succes-global-suspendu-rouge.log`, `/tmp/seamtech-import-initial-global-rouge.log`, `/tmp/seamtech-transport-lot-incomplet-rouge.log`, `/tmp/seamtech-worker-snapshot-incomplet-rouge.log`, `/tmp/seamtech-redelivraison-revue-rouge.log`, `/tmp/seamtech-api-revue-globale-rouge.log`, `/tmp/seamtech-sync-statut-snapshot-rouge.log`, `/tmp/seamtech-correction-statut-rouge2.log`. `/tmp/seamtech-revue-worker-final-cibles.log` inclut un worker réel avec registre SQLite et magasin S3 mémoire : redélivrance sans doublon ni destruction. `/tmp/seamtech-suspension-globale-cibles.log` **223 réussis / 2 ignorés / 7 xfails** avant les dernières protections worker et API ; celles-ci nécessitent requalification complète.
- La reproduction d'interstice a été conservée comme test non-xfail : elle vérifie maintenant le refus conservatoire, **pas** un snapshot qui empêcherait la mutation. Les autres limites de la section précédente restent valides. L'indicateur `identite_locale_observee` décrit une observation bornée, pas une garantie persistante ni une autorisation de purge.
- Campagnes en cours sur le code actuel : `/tmp/seamtech-suite-suspension-locale.log`, `/tmp/seamtech-suite-suspension-pg.log`, `/tmp/seamtech-suite-suspension-redis.log` ; résultats **non encore connus**. PostgreSQL privé et S3rver de test actifs ; attendre les sorties `EXIT=`, vérifier/nettoyer les bases et arrêter les services. Vérifier toute régression et rejouer après correction. Les exclusions, skips et xfails ne valent pas qualification.
- **Prochaine action précise :** finir ces campagnes et leurs éventuelles régressions, qualifier la reprise et le worker en réseau réel avec cette suspension, puis implémenter un snapshot sûr utilisé de façon identique par l'extraction, l'indexation et l'envoi, la liaison catalogue transactionnelle et le fencing des effets externes ; restaurer seulement ensuite un vrai succès global et ses tests positifs. Poursuivre toutes les autres portes P0/P1/P2, sans GO ni déploiement.
- `/tmp/seamtech-worker-s3rver-suspension.log` : **PASS** sur worker réel + SQLite + S3rver réseau isolé : relecture de l'objet, revue explicite, redélivrance sans nouvel objet, original conservé ; bucket jetable supprimé et S3rver arrêté. `/tmp/seamtech-worker-redis-revue.log` **1 réussi** sur Redis 7.4.7 privé : worker réel, acquittement, revue, pas de lettre morte ni réexpédition en redélivrance ; test S3 mémoire, non un S3 fournisseur.
- Campagnes initiales de suspension : locale **1685 réussis / 14 ignorés / 724 désélectionnés / 11 xfails**, mais PG **1 échec / 2195 réussis / 34 ignorés / 190 désélectionnés / 14 xfails** et Redis **1 échec / 135 réussis / 2297 désélectionnés / 1 xfail** ; il s'agissait du même test R3 qui attendait encore `completed` après un transport désormais mis en revue. Le test exige maintenant explicitement `needs_review`, le transport prouvé, `all_verified=false`, l'original intact et l'absence de réenfilage ; le scénario d'isolation Redis continue d'être exécuté, pas contourné. `/tmp/seamtech-revue-redis-pg-cibles-final.log` **3 réussis** incluant ces deux cas et le nouveau test de redélivrance ; une première commande avec un nom de test incomplet n'a rien exécuté.
- Reprise des campagnes après adaptation : `/tmp/seamtech-suite-suspension-pg-final.log` et `/tmp/seamtech-suite-suspension-redis-final.log` **en cours**, résultats inconnus. La suite locale précède l'ajout du test Redis et l'adaptation du test R3, et ne couvre donc pas ces derniers ; sélections, exclusions et réserves inchangées. PG privé toujours actif ; attendre, vérifier les bases jetables et arrêter. Prochaine action : lever les défauts structurels, pas lever cette suspension conservatoire sans preuve.
- Campagnes de suspension après adaptation R3 : PG `/tmp/seamtech-suite-suspension-pg-final.log` et Redis `/tmp/seamtech-suite-suspension-redis-final.log` sorties 0 ; leurs chiffres figurent dans les logs mais elles précèdent le durcissement suivant, donc ne certifient pas l'arbre final. Un test supplémentaire a montré qu'un booléen `verified` sans SHA ni méthode de preuve était accepté comme `transport_verifie` (`/tmp/seamtech-transport-preuve-incomplete-rouge.log`). Désormais le transport complet exige, pour chaque objet, clé/bucket, état et preuve cohérents, SHA-256 valide et méthode `relecture_sha256` ou `checksum_serveur` ; anciennes preuves sans SHA restent conservées mais ne satisfont pas ce nouveau signal. Les doubles R3 et catalogue ont été alignés sur le vrai contrat, sans toucher aux assertions de liaison manquante.
- `/tmp/seamtech-preuve-identifiable-cibles.log` **226 réussis / 1 ignoré / 7 xfails**, vrai PG16 privé et Redis de test ; `/tmp/seamtech-reprise-preuve-identifiable-s3rver.log` et `/tmp/seamtech-worker-preuve-identifiable-s3rver.log` **PASS** après ce durcissement sur réseau S3rver privé, buckets jetables supprimés et serveur arrêté. S3rver n'est pas MinIO/R2 et les preuves conservatrices ne lèvent aucun des risques structurels. Ruff et `git diff --check` verts avant cette note.
- Nouvelles campagnes après durcissement : `/tmp/seamtech-suite-preuve-identifiable-local.log`, `/tmp/seamtech-suite-preuve-identifiable-pg.log`, `/tmp/seamtech-suite-preuve-identifiable-redis.log` **en cours, résultats inconnus** ; attendre les lignes `EXIT=` et corriger tout échec. La suspension du succès global reste active pour toutes les voies concernées ; les précédentes priorités et portes NON PRÊTES ne changent pas.
- Campagnes après durcissement des preuves terminées code 0 : locale `/tmp/seamtech-suite-preuve-identifiable-local.log` **1686 réussis / 14 ignorés / 725 désélectionnés / 11 xfails**, PG16 `/tmp/seamtech-suite-preuve-identifiable-pg.log` **2197 réussis / 34 ignorés / 191 désélectionnés / 14 xfails**, Redis7 `/tmp/seamtech-suite-preuve-identifiable-redis.log` **137 réussis / 2298 désélectionnés / 1 xfail**. Les skips, xfails et exclusions ne sont pas qualifiés ; OCR indisponible dans ce banc, deux avertissements OpenAPI doublons. Après les campagnes seule la base `postgres` subsistait hors templates ; PostgreSQL privé et S3rver arrêtés. Ruff et `git diff --check` verts. `/tmp/seamtech-catalogue-identifiable-rouge.log` confirme que le xfail catalogue échoue bien sur la liaison réelle, pas sur un préalable devenu faux.
- **Prochaine action inchangée et prioritaire :** construire un snapshot source immuable/épinglé ou une architecture équivalente garantissant l'identité utilisée par extraction, indexation et envoi ; rendre atomique et idempotente la liaison au catalogue en tenant compte des documents déjà protégés et des historiques, puis fencing des écritures et effets externes. Tester notamment la mutation après dernière observation, le crash entre objet et base, l'ajout et suppression, les anciens imports sans SHA, les sources très volumineuses, les producteurs concurrents et la redélivrance réelle. Lever la suspension et valider le chemin positif seulement après ces preuves ; poursuivre ensuite toutes les autres portes P0/P1/P2 ci-dessus et obtenir CI verte sur commit exact. **P0/P1/P2 NON PRÊTS, aucun GO, aucun déploiement.**

## Capture, identité catalogue, accès et migration 032 — suite au 31/10/2026

**État courant : P0/P1/P2 NON PRÊTS.** Branche `arena/b936b3c0-seamtech-search`, HEAD local `f1733c8`, modifications non committées. Aucun GO, déploiement, migration réelle ni CI verte sur un commit exact. `all_verified` reste **faux** et la purge est suspendue. Ce checkpoint ne certifie pas les campagnes précédentes sur l'arbre courant.

### Capture, intentions et liaison

- L'import unitaire capture sur Linux/POSIX chaque fichier en copie temporaire confinée, calcule son SHA et l'utilise pour l'extraction, l'index et l'envoi. L'intention `publication_pending` est enregistrée avant les écritures distantes ; réponse perdue, résultat absent/dupliqué ou objet non prouvé interdisent un renvoi aveugle. Un échec avant écriture démontré peut être repris sur source inchangée. La capture fichier par fichier **n'est pas** un snapshot transactionnel du dossier ; temp, crash, même UID, gros volumes et Windows ne sont pas qualifiés.
- `032_identite_octets_documents` ajoute `source_sha256` nullable, distinct de `content_hash` ambigu, sans backfill des anciens documents. La reprise ne lie un objet qu'à chemin **et** SHA identiques, sans écraser un objet précédent ; mise à jour du catalogue et du payload dans une transaction. Les versions métier et applicative sont respectivement `031` et `032`. Les anciens manifestes restent lisibles sans preuve de `032`.
- Nouveau test rouge `/tmp/seamtech-identite-mutation-ancienne-rouge.log` : un ancien écrivain pouvait réécrire une ligne en conservant à tort l'identité. `032` crée désormais un déclencheur SQLite/PostgreSQL invalidant le SHA sur réécriture des champs de représentation, même si les valeurs sont identiques. L'indexeur qui possède effectivement une capture le rétablit ensuite dans la même transaction. Le démarrage et `/ready` vérifient le marqueur, la colonne et le déclencheur actif ; `/health` expose `schema_application_a_jour`. Ils ne vérifient pas cryptographiquement la définition SQL ni une désactivation entre opérations. Le diagnostic PG a été corrigé pour utiliser une connexion effectivement détenue, pas une connexion déjà rendue au pool.
- Une sauvegarde actuelle refuse un schéma physique incomplet ; vérification et restauration des nouveaux manifestes contrôlent aussi le déclencheur. Rouges `/tmp/seamtech-sauvegarde-schema-physique-rouge.log` et `/tmp/seamtech-restauration-schema-physique-rouge.log` ; verts `/tmp/seamtech-sauvegarde-schema-physique-cibles.log` **173 réussis / 2 ignorés / 3 xfails** et `/tmp/seamtech-declencheur-restauration-cibles.log` **93 réussis / 3 xfails**. Les déclencheurs ajoutent des coûts et verrous encore non mesurés sur copie représentative ; conserver colonne, fonction et déclencheur lors d'un rollback logiciel. Les sauvegardes et la restauration coordonnées restent NON QUALIFIÉES.
- Le scan ne réécrit ni ne supprime une capture non liée sans voie protégée, et le rebuild conserve ces lignes. CLI de scan et worker séparé appliquent/contrôlent les migrations ; un rebuild recréant la table réinstalle les migrations dans la transaction du scan. Rouges `/tmp/seamtech-scan-protection-capture-rouge.log`, `/tmp/seamtech-worker-schema-rouge.log` ; vert `/tmp/seamtech-scan-worker-schema-cibles2.log` **148 réussis** ; `/tmp/seamtech-rebuild-reimport-pg-cibles.log` **4 réussis** avec PG réel. Réexécuter les campagnes complètes après ces changements.
- Le réimport d'une pièce protégée n'écrase plus la ligne ni les références : il enregistre la nouvelle capture et, si disponible, son objet séparé ; une ligne protégée ou une identité/clé incompatible donne `catalogue_lie=false`, `needs_review`, avertissement agrégé et aucune purge. Aucune identité historique n'est déduite. Rouges `/tmp/seamtech-reimport-protege-conflit-rouge.log`, `/tmp/seamtech-reimport-protege-identique-rouge.log` ; vert `/tmp/seamtech-reimport-protege-identique-cibles.log` **214 réussis / 2 xfails**, PG+Redis privés. L'ancien xfail de réimport protégé a été retiré après preuve positive et assertions renforcées. Une réconciliation complète, la création exclusive d'objets et le fencing distribué restent absents.

### Accès aux octets et ressources

- `/pieces`, `/open` et téléchargements d'imports servent, lorsqu'un SHA initial est connu, une copie bornée dont les octets ont été vérifiés **avant** réponse ; une copie distante/locale alternative conforme peut être utilisée. Les anciens enregistrements sans SHA restent accessibles selon le contrat historique mais ne bénéficient d'aucune preuve d'identité. `HEAD` ignore `Range` comme avant. La lecture locale est confinée et épinglée ; Linux `/proc/self/fd`, capacité temporaire, écritures sous le même UID, annulations longues et Windows restent à qualifier.
- `/tmp/seamtech-integrite-pieces-catalogue-rouge2.log` avait montré **9 échecs** ; `/tmp/seamtech-integrite-acces-cibles2.log` **75 réussis / 1 ignoré** et `/tmp/seamtech-integrite-acces-head-cibles.log` **52 réussis / 1 ignoré**. Nouvelle faille `/open` : un lien vers un autre fichier *dans la même racine* faisait servir la mauvaise identité (`/tmp/seamtech-open-identite-lien-interne-rouge.log`). La consultation du catalogue utilise maintenant le chemin lexical, puis ouverture confinée ou copie objet vérifiée ; `/tmp/seamtech-open-identite-lien-cibles.log` **103 réussis** avec PG et S3rver. Le test PG de `/pieces` utilise une session humaine réelle.
- Les corps S3 hérités sont fermés en fin de réponse et si le client se déconnecte avant le premier bloc ; un échec d'audit avant réponse les ferme aussi. Les fd épinglés ferment sur interruption. Rouges `/tmp/seamtech-ressources-stream-rouge.log` **2 échecs / 1 réussite** ; verts `/tmp/seamtech-ressources-stream-cibles.log` **98 réussis**, `/tmp/seamtech-stream-panne-audit.log` **1 réussi**. Pas de qualification d'endurance ni d'annulation réseau bloquée.
- Tests réseau synthétiques d'accès corrompu/repli et de réimport conflictuel sur S3rver : `/tmp/seamtech-reimport-reseau-cibles.log` **28 réussis** avec inventaire CI ; `/tmp/seamtech-reimport-s3rver-campagne.log` **9 réussis / 2559 désélectionnés / 4 xfails**. S3rver 2.2.9 n'est pas MinIO/R2 et ne prouve pas création exclusive ni checksum serveur complet. L'inventaire exact `tests/test_selection_ci.py` et l'attendu de `.github/workflows/ci.yml` sont passés à 9 réussis + 4 xfails.

### Autres campagnes et limites

- `/tmp/seamtech-correction-intention-pg.log` avait **5 échecs / 2395 réussis / 30 ignorés / 58 désélectionnés / 12 xfails**, avec arbre modifié pendant l'exécution ; non qualifiante. Chaos corrigé ensuite en ciblé. Les quatre scénarios worker/processus auparavant rouges ont été rejoués sur arbre alors stable : `/tmp/seamtech-worker-crash-post-schema-032.log` **6 réussis** avec PG16/Redis7 privés ; le worker a depuis reçu le contrôle des migrations, donc rejouer. Redis antérieur `/tmp/seamtech-correction-intention-redis.log` **139 réussis / 2389 désélectionnés / 1 xfail**, avant ces changements, non qualifiant.
- Les xfails ouverts incluent réimport complet après envoi partiel, verrou SQLite remplaçable par même UID, perte de session advisory PG, interstice Redis–PG, instantané PG et octets locaux, et restauration exclusive S3/MinIO. L'arrêt conservatoire n'est pas une solution complète de réconciliation. OCR navigateur 24/25, rotation de PDF mixtes, Windows/Docker Desktop/Chrome/Edge, stockage actuel MinIO/R2, sauvegarde de tout `./data`, hors-site/rétention et restauration coordonnée, volume annuel réel, matrice exhaustive traçable, charge multipart, pannes, sécurité, axe-core et endurance plusieurs jours restent à corriger ou qualifier ; les exclusions/skips/xfails ne sont pas des succès.
- Services synthétiques encore actifs au moment de cette note : PG16 privé `base-de-test-postgresql-isol-e-fc3bf410`, socket `/tmp/seamtech-pgmutex/socket`, port 55498 ; S3rver `mulateur-de-stockage-isol-pour-t-1635a000`, port 14569. URL PG `postgresql:///postgres?host=/tmp/seamtech-pgmutex/socket&port=55498`. Les bancs créent leurs propres bases/buckets/Redis ; vérifier leurs états puis nettoyer et arrêter après campagnes.
- Compétences ECC appliquées : `search-first`, `tdd-workflow`, `security-review`, `database-migrations`, `postgres-patterns`, `verification-loop`, `python-reviewer`, `error-handling`. Aucun document ou valeur client réelle n'a été utilisé dans ces nouveaux tests.

**Prochaine action précise :** rejouer sans modifier l'arbre les campagnes complètes SQLite, PG16 et Redis7 ainsi que S3rver, et le test de worker SIGKILL après contrôle de migrations ; analyser et corriger chaque échec, puis rejouer. Contrôler les migrations et rollback de capture/rebuild, la concurrence des déclencheurs et l'état des fd/corps sur interruption réseau, puis poursuivre la réconciliation et le fencing P0.2, la restauration coordonnée et toutes les portes P1/P2. Mettre à jour ce document avant tout arrêt, sans bilan intermédiaire dans le chat.

### Suivi d'import, contrat Excel et campagne complète interrompue — 31/10/2026

- Campagne SQLite figée `/tmp/seamtech-schema032-reimport-local.log` : **4 échecs / 1875 réussis / 30 ignorés / 658 désélectionnés / 7 xfails**. Les quatre échecs concernaient des tests historiques sans index et sans correspondance à lier : le code appelait inutilement `lier_objets_capture` sur `None`. Il ne l'appelle maintenant que pour une liste non vide ; `/tmp/seamtech-reprise-vide-cibles.log` : **56 réussis / 1 ignoré**. La campagne PG, lancée ensuite, a été interrompue délibérément avant les corrections et n'est **pas** qualifiante ; aucune campagne complète finale n'a depuis été exécutée.
- Le suivi HTTP pouvait masquer `failed`, `upload_incomplete` ou `needs_review` avec le statut `completed` d'un ancien résultat. En cas d'intention pendante, ce masquage existait aussi et un ancien `completed` subsistait dans le résultat imbriqué. Rouges `/tmp/seamtech-statut-suivi-rouge.log` **3 échecs / 1 réussite**, `/tmp/seamtech-statut-intention-rouge.log` **2 échecs / 1 réussite**, `/tmp/seamtech-intention-succes-rouge.log` **2 échecs**. La route conserve désormais l'état non réussi du job, l'intention et les deux états `job_status`/`import_status`, sans masquer une correction ultérieure d'un job réussi. Une intention persistée ne peut être annoncée `completed`. `/tmp/seamtech-statut-intention-cibles.log` **99 réussis / 1 ignoré** ; `/tmp/seamtech-statut-suivi-reel.log` **14 réussis**, dont le registre SQLite et la route HTTP réels. Rejouer l'ensemble après ces changements ; pas de preuve de toutes les transitions concurrentes.
- Le panneau React ne terminait jamais le suivi des statuts `needs_review` ou `upload_incomplete`, perdait le détail d'un échec, pouvait réactiver un job après annulation sur réponse de suivi tardive, annonçait une quarantaine non faite et permettait des doubles clics ou une fausse confirmation d'annulation. `/tmp/seamtech-ui-suivi-rouge.log` **5 échecs**, `/tmp/seamtech-ui-annulation-rouge.log` **2 échecs** avant corrections. Le suivi série/annulable affiche maintenant les états terminaux et le résultat le plus récent, conserve les intentions incertaines, refuse un renvoi aveugle et distingue emplacement prouvé/non confirmé. Une annulation non confirmée relit le statut et une double demande est neutralisée. Une régénération de rapports interrompue peut être reprise par correction autorisée, sans relance prématurée de l'envoi. Limites : navigation avec résultat sans journal, rechargement, attente réseau indéfinie et toutes courses d'annulation réelles restent à qualifier ; l'interface n'est pas une garantie distribuée.
- L'interface interprétait aussi mal le contrat Excel réel : `filename`, `sheet_name`, `row_count`, `column_count` et les lignes en tableaux étaient lus sous des noms/types incorrects ; les cellules étaient vides et React signalait des clés manquantes. Rouge navigateur `/tmp/seamtech-ui-excel-rouge.log` **1 échec**, puis affichage corrigé et test des cellules, feuilles et absence d'avertissement de clé. Les tests d'import historiques sont maintenant ciblés sur la ligne PDF et la ligne Excel séparément ; les assertions ne sont pas affaiblies.
- Banc local Chromium 140 (paquet npm `@sparticuz/chromium`, bibliothèques Amazon Linux extraites), vrais Next + backend SQLite sans Redis, données synthétiques. `/tmp/seamtech-ui-revue-excel-import-cibles.log` : **13 réussis / 0 retry**, dont 11 scénarios de suivi à réponses backend simulées et 2 vrais imports via BFF/backend. `frontend/node_modules/.bin/tsc --noEmit` : réussi. Ces tests ne qualifient pas Chrome/Edge Windows, le réseau réel ou l'intégration Redis/MinIO. Le serveur et la base de ce banc sont jetables ; pas de déploiement.
- ECC relues/appliquées : `tdd-workflow`, `react-patterns`, `frontend-patterns`, `e2e-testing`, `api-design`, `contract-first`, `error-handling`, `verification-loop`. Les tests navigateur nouveaux sont automatiquement collectés par la suite Playwright par défaut.

**Prochaine action précise :** relancer les trois campagnes backend complètes sur arbre figé (SQLite, PG16, Redis7), la sélection S3rver et le parcours Playwright ; analyser chaque échec sans effacer les skips/xfails. Ensuite corriger l'attente réseau infinie et qualifier le rechargement/annulation réel, puis poursuivre intégrité et réconciliation P0.2, sauvegarde/restauration coordonnée, OCR/rotations et les autres portes. P0/P1/P2 restent **NON PRÊTS**, `all_verified=false`, pas de purge, pas de CI 100 % verte sur commit exact ni de GO.

### Suivi durable, sécurité du banc et sélection navigateur — 31/10/2026, suite

- **P0/P1/P2 restent NON PRÊTS.** Branche `arena/b936b3c0-seamtech-search`, HEAD `f1733c8`, travail non committé ; aucune CI verte au commit exact, aucun GO, déploiement, push ni fusion. `all_verified=false` ; purge suspendue.
- Reprise du suivi : l'interface borne ses GET, réessaie seulement la lecture, conserve l'identifiant opaque dans `sessionStorage` sous l'identité de la session, permet la reprise manuelle et l'arrêt explicite sans prétendre annuler le job. Elle annule les lectures périmées ; changement d'identité et démontage invalident aussi les résultats asynchrones, chemins et sélections. Les erreurs ou réponses inconnues ne libèrent pas le travail. La perte de réponse au POST de création peut encore créer un job sans identifiant connu ; l'autorisation serveur ne lie pas le suivi à un propriétaire. Ces limites restent ouvertes.
- Le BFF `GET /api/imports/{id}` propage l'abandon et borne vérification nominative et backend ; une révocation répond 401, une panne de vérification 502, un délai dépassé 504. Le banc réel PostgreSQL 16.2 privé + Redis 7.4.7 privé + worker séparé + Next/Chromium `/tmp/seamtech-suivi-reel-apres-depot.log` : **6 réussis**, zéro retry ; JSON `/tmp/seamtech-suivi-reel-apres-depot.json` `(expected, unexpected, skipped, flaky)=(6,0,0,0)`. Révocation, panne, fermeture réseau, rechargement, annulation et acquittement par le worker sont testés ; pas de preuve de toutes les courses ni de stockage objet réel.
- Nettoyage du banc : Playwright utilise SIGTERM ; le serveur convertit le signal retransmis par Uvicorn pour exécuter les nettoyages `finally`. `/tmp/seamtech-securite-banc-suivi-rouge.log` reproduisait la fuite, `/tmp/seamtech-securite-banc-suivi-vert1.log` **1 réussi**, `/tmp/seamtech-suivi-banc-nettoyage-final.log` **1 réussi** sans nouvelles ressources résiduelles. Dix bancs laissés par les essais rouges ont été rapprochés un à un de leurs manifestes, configurations, bases et sockets Redis, contrôlés sans connexion active, puis supprimés explicitement ; aucune suppression par motif aveugle. Après les six tests, le serveur PG privé ne contient que `postgres` hors templates et aucun manifeste/répertoire Redis ou suivi du banc ne subsiste. SIGKILL, crash machine et Windows ne sont pas qualifiés.
- `GET /imports/{id}` expose désormais l'état opérationnel prudent sans masquer `failed`, `upload_incomplete`, `needs_review` ou une intention incertaine. Un résultat `completed` contredit par une intention apparaît en `needs_review` ; `job_status` et `import_status`, lorsqu'ils existent, indiquent les états effectivement stockés. `/tmp/seamtech-statut-pg-intention-rouge.log` **6 échecs** montrait que l'état physique de l'import était précédemment réécrit dans la réponse ; après correction `/tmp/seamtech-statut-pg-intention-vert.log` **98 réussis / 3 ignorés / 1 xfail**, y compris six états à travers PostgreSQL jetable et HTTP réel. Contrat documenté dans `docs/API.md` ; les lectures concurrentes entre transactions restent à qualifier.
- Dépôt : une lecture de répertoire est sérialisée, indique son activité et empêche un deuxième dépôt ; un changement d'identité invalide une lecture tardive. Entrées manquantes/non reconnues, liste aplatie, erreur tardive ou dossier vide ne peuvent plus être présentés comme une sélection complète. Une nouvelle sélection, même illisible, efface l'ancienne analyse ; changer le chemin invalide aussi les PDF/Excel précédents et les entrées sont désactivées pendant l'analyse. `/tmp/seamtech-ui-depots-concurrents-rouge.log` et `/tmp/seamtech-ui-depot-partiel-rouge.log` reproduisaient les anciens défauts ; `/tmp/seamtech-ui-changement-source-rouge.log` **4 échecs**, puis `/tmp/seamtech-ui-changement-source-vert.log` **6 réussis**. Les tests vérifient séparément l'effacement du chemin et des fichiers au changement de compte.
- Des dépôts de fichier et d'arbre réels dans Chromium sont désormais déclenchés par Chrome DevTools Protocol avec de vrais `FileSystemEntry` ; le parcours BFF/backend SQLite vérifie les octets envoyés et les originaux de **121 fichiers synthétiques**, puis nettoie uniquement son propre staging. Ce n'est ni un geste manuel de l'explorateur ni une preuve de gros volume, Windows, Edge ou du stockage MinIO/R2 ; le parcours historique `/imports/upload` reste limité à 500 fichiers, le parcours de lot est distinct. Le test OCR utilisait `new DataTransfer()`, dont `webkitGetAsEntry()` est nul dans Chromium ; son geste a été remplacé par CDP sans réduire les assertions OCR/recherche/SHA. **Le parcours OCR complet n'a pas encore été rejoué après ce changement**, faute de Tesseract dans ce sandbox.
- `/tmp/seamtech-interface-import-34-ci.log` : **33 réussis / 1 échec** ; l'ancien test exigeait simultanément un chemin et un fichier, devenus volontairement incompatibles. Il a été séparé en deux tests vérifiant chacun l'effacement sans retirer les assertions. Ensuite `/tmp/seamtech-interface-import-38-ci.log` : **38 réussis, zéro retry**, config Playwright par défaut et vrais Next/backend SQLite ; JSON `(38,0,0,0)`. Les 38 incluent des réponses simulées pour plusieurs états ; quatre parcours d'import/dépôt utilisent le vrai backend SQLite, pas Redis ou MinIO. CI modifiée pour exiger exactement ces 38 tests et les six tests de suivi réel, sans skip ou flaky ; **étapes CI distantes non encore exécutées**.
- Campagnes backend figées, empreinte de l'ensemble des fichiers Git suivis/non ignorés avant/après identique `b90eaf799a1137244df4f7e0169a48c6750c3ffc87b844a92f58c1eb736df209` : `/tmp/seamtech-requalification-s3rver.log` **9 réussis / 2580 désélectionnés / 4 xfails** ; `/tmp/seamtech-requalification-sqlite.log` **1815 réussis / 13 ignorés / 758 désélectionnés / 7 xfails** ; `/tmp/seamtech-requalification-postgres.log` **2275 réussis / 14 ignorés / 295 désélectionnés / 9 xfails** ; `/tmp/seamtech-requalification-redis.log` **140 réussis / 2452 désélectionnés / 1 xfail** ; `/tmp/seamtech-requalification-worker-sigkill.log` **8 réussis** après contrôle des migrations. Sélections par marqueurs ; les xfails, exclusions, skips, S3rver et PG16 embarqué ne qualifient pas les fournisseurs/plateformes ni l'ensemble du plan. Ces campagnes précèdent les dernières modifications frontend et des tests ; rejouer sur l'arbre final et en CI.
- `frontend/node_modules/.bin/tsc --noEmit`, `.venv/bin/ruff check .`, analyse YAML du workflow et `git diff --check` réussis après les dernières corrections. Deux avertissements OpenAPI de routes dupliquées restent ouverts. ECC relues et appliquées : `search-first`, `tdd-workflow`, `e2e-testing`, `react-patterns`, `frontend-patterns`, `security-review`, `postgres-patterns`, `contract-first`, `error-handling`, `verification-loop`.
- **Prochaine action précise :** auditer avec PostgreSQL jetable `_restaurer_documents` : sa garde historique ne semble protéger que `role`, `id_fiche` et `object_key`, pas une capture `source_sha256` sans objet ; la méthode est aujourd'hui appelée par un test mais le chemin de scan actuel utilise une transaction, ne pas présenter ce point comme une perte en production démontrée. Écrire des reproductions avant toute modification, préserver les captures contemporaines et refuser une restauration ambiguë. Poursuivre ensuite intégrité/réconciliation P0.2 et restauration coordonnée, OCR exact sans retry, rotations mixtes (le prototype de regroupement par matrices restitue des mots synthétiques mais ne prouve ni lignes/tableaux/coordonnées ni sûreté des règles métier), plateformes/volume, matrice d'autorisations, multipart, pannes, sécurité, accessibilité, endurance et CI exacte. Aucun bilan intermédiaire dans le chat.

### Identité des captures, sauvegarde, OCR et orientations — 31/10/2026, état de reprise

- **P0/P1/P2 NON PRÊTS.** Branche `arena/b936b3c0-seamtech-search`, HEAD `f1733c8`, changements non committés ; aucune CI 100 % verte sur le commit exact, aucun GO, push, déploiement ni fusion. `all_verified=false` et purge suspendue. `main` distant a aussi avancé après le point de départ ; aucune intégration de ce changement n'a été effectuée. L'accumulation de données temporaires, quotas et alertes restent à traiter.
- **P0.2, fichiers de lot et reprise :** capture temporaire des octets, SHA-256 d'origine, intentions durables, promotion prudente vers le stockage objet, liaison conditionnelle du catalogue et signal `catalogue_lie` sont implémentés. `source_sha256` est distinct de `content_hash` ; la migration `032` ne fait aucun backfill historique. Un réimport à octets différents préserve la capture/catalogue antérieurs, émet un conflit et peut conserver une seconde capture ; une source modifiée est décomptée dans `inventaire.sources_modifiees`. Après panne ou réponse distante perdue, `publication_pending` interdit tout renvoi aveugle. Le test de réimport sans objet était d'abord **2 échecs**, puis **2 réussis** ; courses de captures SQLite/PG répétées : **35 réussis**. Ces résultats ne prouvent ni publication exclusive chez MinIO/R2 actuel, ni fencing distribué, ni conservation d'anciens octets qui n'avaient pas été archivés. Capture initiale non transactionnelle sur tout l'arbre, capacité/temporaire, crash, même UID, Windows et volumes non qualifiés.
- **Restauration de scan :** `_restaurer_documents` refuse maintenant une restauration de snapshot ambiguë ou divergente, même pour une identité sans objet. Huit tests rouges avant correction, puis **21 réussis** ; campagne ciblée **100 réussis**. Le chemin normal de scan travaille transactionnellement : ne pas présenter l'ancien défaut comme une perte de production démontrée. Les déclencheurs `032` invalident les empreintes sur les réécritures couvertes ; authentification de la définition SQL, dérive concurrente et versions mixtes non qualifiées. SQLite même UID, perte de session advisory PostgreSQL et atomicité Redis–PG restent des xfails/risques documentés. Les intentions S3 et la base ne sont pas une transaction.
- **Sauvegarde partielle :** les copies de documents locales et objets sont comparées à l'identité connue dans le snapshot PostgreSQL ; les divergences sont refusées, les historiques sans SHA restent inconnus. Tests initiaux **3 échecs / 1 réussite**, puis **4 réussis** ; campagnes **289 réussis / 4 xfails** et **11 réussis** avec snapshot PG. Cela ne couvre pas entièrement `./data`, ni l'authenticité du manifeste, ni la restauration coordonnée base/fichiers/objets/hors site, ni une publication multifichiers atomique. Les anciens manifestes restent lisibles mais n'apportent pas la preuve de `032`. Une erreur après `pg_restore` peut laisser une cible partielle. Migration/rollback sur copie représentative, rétention et fournisseurs finaux restent bloquants.
- **OCR : défaut produit confirmé, pas de contournement.** Après reconstruction du banc Tesseract **5.5.1 / Leptonica 1.85.0**, données `fra+eng` fast dont les SHA ont été vérifiés, `tests/test_ocr_identifiants_exacts.py` donne **4 échecs / 1 réussite** : substitutions `0/O` malgré `extracted`, originaux préservés. Pas de `xfail`, d'alphabet réduit, de texte attendu injecté, d'assertion affaiblie ni de retry salvateur. Le job CI `ocr` collecte ces tests stricts et doit donc rester rouge tant que ce défaut n'est pas résolu ; le simple passage des quatre scénarios navigateur ne prouve pas sa stabilité.
- **Essai comparatif antérieur hors produit :** RapidOCR PP-OCRv6 small a obtenu **32/32** sur un premier corpus artificiel mais seulement **86/128** sur le banc élargi, Tesseract **54/128**, et 36 cas étaient ratés par les deux ; deux familles de polices manquaient. Aucune intégration ni garantie générale ; licences des modèles, absence de réseau et ressources restent à qualifier. Les bancs temporaires antérieurs ont disparu lors d'un changement de sandbox ; leurs résultats ne sont pas des fichiers de preuve persistants.
- **Rotations PDF :** les lectures homogènes et quadrants orthogonaux mixtes vérifiés sont normalisés ; les tableaux, coordonnées, arêtes recadrées, `CropBox`/`MediaBox` décalées et `UserUnit` ont des tests synthétiques. L'ancien `xfail` ciblé a été retiré après succès effectif. Une orientation oblique, inversée, cisaillée, une projection ambiguë ou une lecture homogène incompatible produit `orientation_pdf_non_verifiee`, conserve les éléments disponibles et impose une relecture complète. Le signal est persisté, visible dans la file et bloque la validation en lot **même après acquittement**. Les brouillons de gabarit homogènes à 90/180/270 sont également corrigés. Reproductions : **12 échecs** initiaux, puis **3 échecs** de routage/validation et **6 échecs** de brouillon avant leurs corrections ; campagne PG 16.2 + Tesseract local `/tmp/seamtech-orientation-revue-ocr-cibles-vert.log` **201 réussis, aucun skip** (avant les toutes dernières modifications). Les cellules/tableaux à orientations mêlées, l'ordre de lecture global et un corpus réel représentatif ne sont pas qualifiés.
- **Interface orientation :** `/tmp/seamtech-signal-ui/rouge.log` montrait **1 réussite / 1 échec** (alerte non vérifiée absente), puis `/tmp/seamtech-signal-ui/vert.log` **2 réussis, zéro retry**. Sessions et authentification PostgreSQL réelles ; file métier et décisions simulées, pas de Redis, donc pas une preuve bout en bout réelle. Le banc a été attribué par son manifeste, sa configuration, la base et le fichier synthétique référencé ; après contrôle d'absence de connexion, sa base et son fichier généré ont été supprimés explicitement. Une première tentative de nettoyage s'est arrêtée prudemment car la propre connexion d'inspection était encore ouverte ; aucune suppression ni interruption n'avait alors eu lieu.
- **Repli PDF et limites :** si `pdfplumber` échoue, le texte récupérable par `pypdf` est conservé avec `unavailable` et ne peut être certifié par un OCR de repli ; le message journalisé ne contient ni chemin ni texte. Reproduction `/tmp/seamtech-repli-pypdf-incertain-rouge.log` **1 échec / 1 réussite**, correction incluse dans la suite `/tmp/seamtech-orientation-complete-cibles2.log` **217 réussis / 1 désélectionné** (avant le dernier changement OCR). Une extraction native atteignant la limite avant des pages non inspectées est également `unavailable` ; **2 échecs rouges**, puis vérification des frontières exactes. **Défaut confirmé ensuite :** avec un `CropBox` masquant un texte synthétique et `pdfplumber` en panne, le repli pypdf indexe ce texte hors vue. `/tmp/seamtech-fallback-red/rouge.log` a d'abord échoué à la collecte car le test externe n'avait pas le `PYTHONPATH` du dépôt ; après correction du banc, `/tmp/seamtech-fallback-red/rouge2.log` contient **1 échec produit** démontrant l'indexation et la recherche du texte masqué. `/tmp/seamtech-fallback-red/rouge3.log` : **8 échecs**, y compris vues `CropBox`, hors `MediaBox`, chemin de clipping, import et worker réel. Aucune correction encore apportée à ce moment de l'écriture. Un repli sans vérification visuelle ne doit pas être publié/indexé ; préserver le PDF original, ne pas compenser par un OCR non vérifié. Même avec `pdfplumber` fonctionnel, des essais synthétiques révèlent aussi l'extraction hors `MediaBox` et hors chemin de clipping ; la coupure par `CropBox` fonctionne dans l'essai. PDFium borne la `MediaBox` mais pas le chemin de clipping : ne pas le présenter comme solution générale. Une hausse de version force uniquement les documents réindexables ; les documents protégés peuvent garder leurs anciens textes, leur assainissement sans altérer l'identité requiert une réconciliation distincte.
- **Troncature OCR :** auparavant les sorties Tesseract image, OCRmyPDF, OCR PDF par pages et PDF natif+image tronquées étaient déclarées `extracted`. Huit tests synthétiques aux frontières exacte et dépassée : **4 réussis / 4 échecs** avant correction ; le préfixe est maintenant préservé et le statut devient `unavailable` si le texte dépasse la limite. Première tentative de relance : **3 erreurs de collecte** (indentation), immédiatement corrigées ; `/tmp/seamtech-ocr-troncature-vert2.log` **56 réussis / 12 ignorés** sans Tesseract dans le PATH de cette commande. Les cas ignorés doivent être rejoués avec le banc réel ; ne pas transformer cette correction de statut en correction de la reconnaissance des caractères. La limitation d'indexation des autres formats et l'impact ergonomique des gros documents restent à évaluer.
- **Environnement reconstitué :** PostgreSQL **16.2** privé (socket `/tmp/seamtech-pgmutex/socket`, `unaccent`, `pg_trgm`, pgvector **0.8.1**) et Redis **7.4.7** privé (socket `/tmp/seamtech-redis-mutex/redis.sock`) ; Tesseract reconstruit et Chromium **140.0.7339.0** sous `/tmp`, `.venv` et `frontend/node_modules` recréés. Ces ressources ne sont pas le déploiement final et peuvent disparaître à nouveau. Le serveur Redis de chaque test isolé utilise son propre socket ; pas de service de l'utilisatrice. `frontend/node_modules/.bin/tsc --noEmit`, `.venv/bin/ruff check .`, `git diff --check` et `npm run build` Next **16.4.3** ont réussi après les dernières modifications frontend. L'ancien parcours OCR navigateur a d'abord échoué avant scénario à cause d'une bibliothèque Chromium manquante, puis **4 réussis, zéro retry** après correction du banc ; PostgreSQL privé, sans Redis.
- **Autorisations :** le suivi backend exige un jeton de service et le BFF une session valide, mais l'import n'est pas lié à un propriétaire de job côté serveur ; `sessionStorage` n'est pas une autorisation. Correctif complet à concevoir sur les créations, lectures, corrections, reprises, artefacts, annulation, historiques et rôles, pas une protection partielle uniquement côté UI. La perte de réponse au POST peut créer un job sans identifiant connu ; idempotence non qualifiée. Matrice route × rôle × session × état métier/backend et BFF encore incomplète.
- **ECC appliquées dans ces travaux :** `search-first`, `tdd-workflow`, `api-design`, `contract-first`, `frontend-patterns`, `react-patterns`, `e2e-testing`, `security-review`, `postgres-patterns`, `redis-patterns`, `error-handling`, `verification-loop`, `architecture-decision-records`, `production-audit`, `docker-patterns`, `deployment-patterns`, `git-workflow`, `github-ops`, `coding-standards`. Aucun agent humain ni donnée client n'a été sollicité.
- **Campagnes figées terminées avant les derniers changements PDF :** processus privé `campagnes-de-qualification-isol--fdb6a4f2`, empreinte code/tests hors documentation identique avant/après `3f679735ac58f8182b45042b2c42eaa91dd0dfd89d397095c9db6c536becfa90`. `/tmp/seamtech-fixe-sqlite-20261031.log` **1910 réussis / 780 désélectionnés / 6 xfails**, aucun skip ; PostgreSQL **447 réussis / 3 ignorés / 2245 désélectionnés / 1 xfail** ; Redis **140 réussis / 2555 désélectionnés / 1 xfail**. Sorties du processus à zéro ; ces sélections ne couvrent ni OCR strict ni S3 ni performance et **précèdent le correctif PDF suivant**, donc ne sont pas une CI finale. Les motifs des trois skips PostgreSQL restent à identifier. Warnings : dépréciations TestClient/fixture et deux identifiants OpenAPI dupliqués.
- **Correction après ces campagnes :** le repli textuel `pypdf` n'est plus publié quand l'inspection échoue : `unavailable`, texte vide, original inchangé, pas d'OCR de substitution. Le texte reste récupérable dans le PDF, mais sa disponibilité via recherche est volontairement suspendue jusqu'à inspection fiable ; les tests antérieurs vérifiant le texte de repli ont été remplacés par des assertions supplémentaires sur l'original et l'absence de publication. `CURRENT_EXTRACTOR_VERSION=12` provoque la réextraction des documents non protégés. La vue est bornée même si `CropBox=MediaBox` et que des objets sont hors cadre ; contenu visible, quatre rotations, OCR oui/non, index, gabarit et SHA sont vérifiés. Après **8 échecs** de reproduction MediaBox, la première campagne a donné **240 réussis / 10 échecs** : huit erreurs de contrat dans le nouveau test de brouillon et deux attentes historiques incompatibles avec le rognage/la fermeture prudente ; elles ont été corrigées sans enlever les contrôles de récupération de l'original. `/tmp/seamtech-repli-mediabox-cibles2.log` **250 réussis / 1 désélectionné** avec PG16 et Tesseract réels, puis Ruff et `git diff --check` réussis. Le confinement par chemin graphique de clipping avec `pdfplumber` fonctionnel reste défaillant : nouvelle régression `tests/test_pdf_clipping_graphique.py`, `/tmp/seamtech-clipping-normal-rouge.log` **13 échecs** couvrant `W/W*` direct, formes imbriquées, `BBox` de forme, clipping parent et cinq parcours ; `/tmp/seamtech-clipping-ocr-rouge.log` ajoute **1 échec** du pipeline OCR autonome. Un prototype non intégré `/tmp/seamtech-fallback-red/prototype_graphique.py` détecte ces cas et accepte un PDF simple/une forme contenue, mais ne prouve pas encore la correction ni toutes les sémantiques PDF. L'assainissement des anciennes projections protégées reste également ouvert : le seul bump de version ne les modifie pas. Il ne faut pas revendiquer une protection générale de tout texte masqué ou des documents historiques.
- **Comparaison OCR indépendante reconstituée :** `/tmp/seamtech-ocr-general-reprise/results.json` sur 64 images synthétiques déterministes, six polices DejaVu, quatre tailles et flou modéré : Tesseract `fra+eng` PSM 3 **29/64** identifiants exacts, `eng+fra` PSM 3 **29/64**, `eng` PSM 3 **28/64**, `fra+eng` PSM 6 **29/64** ; chacune reconnaît les trois mots français contrôlés sur **64/64**, sans erreur CLI. Ce n'est pas un corpus client ni une qualification réelle. Aucun réglage adopté : changer l'ordre des langues ou PSM ne corrige pas le problème générique ; aucune assertion OCR affaiblie ni nouveau moteur intégré.
- **Prochaine action précise :** créer une régression automatisée pour le clipping graphique en lecture normale et résoudre sa prise en charge complète, y compris sous-objets et vues, sans marquer complet du texte invisible ; prévoir l'assainissement sûr des anciennes projections protégées sans falsifier `source_sha256`. Rejouer ensuite les trois campagnes sur arbre figé, identifier les trois skips PG, puis poursuivre OCR exact indépendant sans retry, intégrité/réconciliation P0.2, sauvegarde/restauration coordonnée et rollback, autorisation durable des imports, plateformes/volume, multipart, injections de pannes, sécurité offensive, accessibilité, endurance et CI verte sur le commit exact. Ne pas faire de bilan intermédiaire dans le chat.

### Fusion de `origin/main` (bc2d4b2) dans la branche de remédiation — 01/11/2026

- **P0/P1/P2 restent NON PRÊTS.** Aucun GO, aucun déploiement, aucune fusion vers `main` sans accord explicite. `all_verified=false`.
- **Fusion terminée et committée** : merge `0bb784c` (parents `cbcdd89` + `bc2d4b2`, 21 conflits), puis `085438e`. Branche `arena/b936b3c0-seamtech-search` poussée (`82936b8..085438e`). Filet de sécurité conservé : `secours/avant-merge-main` = `82936b8`.
- **Arbitrages de conflit** (aucun écrasement silencieux, vérifié par comptage `def/class` et `test(` par version) : BFF multipart → une seule implémentation `lib/relais-multipart.ts` (`relayerMultipart`, flux + 413 + abandon + inactivité 504 + concurrence 429) et suppression du doublon `proxyMultipartBorne` ; `pdf_orientation.py` en UNION (repère d'affichage HEAD + matrice des glyphes main) ; `extractors.py` v13 ; `indexer.py` 032 + `run_migrations(jusqu_a)` ; `sauvegarde.py` notre `restaurer_objets_s3` + bloc main ; `worker.py` `claim_job_id`/`verifier_claim_externe` ; `fiches/routes.py` et `fiches/depot.py` notre chaîne objet ; `ci.yml` nos garde-fous.
- **Deux fichiers TSX restaurés** : la résolution manuelle avait tronqué `components/gabarits-app.tsx` (170 lignes au lieu de 221) et `components/nav.tsx` (92 au lieu de 153) ; `tsc --noEmit` échouait sur 4 erreurs JSX. Reprise de la version main (notre contenu + accessibilité, aucun retrait).

Corrections mesurées, 18 échecs → 0 après fusion

| Défaut | Cause mesurée | Correction |
|---|---|---|
| 7 cas de la matrice contenu × `/Rotate`, `couche_90`, `scan_paysage_90` | contenu portrait tourné de 90°/270° sur une page **portrait** : débordement de 123,5 pt, premières lignes hors page (`x0 = −47,8` sur `couche_90.pdf`), donc écartées à juste titre par la garde de cadre | générateur d'échantillons et matrice de tests dimensionnent la page pour **contenir** le contenu tourné (paysage pour 90°/270°) : 0 glyphe hors boîte sur les 8 échantillons |
| `test_recherche_retrouve_terme_dans_pdf_pivote` | la racine scannée du test recouvrait le staging `uploads` → `ScanIncompleteError` | base déplacée hors de la racine scannée (le test porte sur la rotation, pas sur cette garde) |
| `test_index_libere_page_normalisee_si_lecture_echoue` | casser `extract_text` ne rend plus une page vide : la reconstruction par direction des glyphes ne l'appelle pas | assertions mises à jour sur le comportement mesuré (`extracted`, texte complet, page normalisée refermée) + nouveau test d'échec fermé : garde de vue indisponible ⇒ `unavailable`, aucun texte brut publié |
| PDF chiffré et `.pdf` qui n'en est pas un | statut `unavailable`, identique à « page image à relire » | `_verdict_pdf_illisible` : `error` si signature `%PDF-` absente ou chiffrement non ouvrable ; pypdf incapable d'analyser ne déclenche pas d'erreur (des PDF imparfaits restent lisibles par pdfplumber) |
| `test_scanner_pdf_erreur_ne_journalise_ni_nom_ni_exception` | `depot.py` journalisait le chemin (nom du client) et le message de l'exception (extrait du document) | type d'exception + catégorie (« sans couche texte » / « aucun texte extrait ») seulement |
| 2 tests qui **figeaient** un lien symbolique suivi et une boucle parcourue | le parcours refuse désormais tout lien | assertions portent maintenant sur le refus (`ValueError`), avant toute lecture |
| `test_les_jobs_dedies_selectionnent_par_chemin…` | le job CI `sauvegarde` publie les raisons de saut (`-rs`) | attendu du test aligné sur la commande réelle |
| `test_les_envois_multipart_sont_relayes_en_flux_borne` | le relais a changé de nom et de fichier côté main | test réécrit sur `lib/relais-multipart.ts` (+ unicité d'implémentation, `req.formData()` jamais appelé) |

Vérifications du commit `085438e`

- `pytest -m "not postgres and not s3 and not perf and not redis_queue"` : **2325 réussis / 58 ignorés / 756 désélectionnés / 6 xfail / 0 échec en 234 s** (`/tmp/seamtech-suite-fusion3.log`).
- `ruff check .` vert ; `frontend/node_modules/.bin/tsc --noEmit` sans erreur ; `npm run build` (Next 16.4.3) réussi ; `node --experimental-strip-types --test lib/*.test.ts` **10/10**.
- Aucune perte de contenu détectée : pour chaque fichier en conflit, le nombre de `def/class` (Python), de `test(` (e2e) et de lignes est **supérieur ou égal** aux deux parents.

Limites ouvertes à ce stade

- La suite verte ci-dessus exclut PostgreSQL, S3, performance et file Redis : les campagnes PG16/Redis7/S3rver n'ont **pas** été rejouées après la fusion.
- Jobs CI encore ouverts avant la fusion et non réexaminés : `ocr` (Tesseract 5.3.4), `sauvegarde` (sources MinIO), `recette-corpus-reel`, `integration`, `hors-ligne-reel`, `e2e` (concurrence), `recette-locale` (`upload=partial`).
- P0.2 (promotion effective des originaux), sauvegarde/restauration coordonnée PG + `./data` + objet + hors-site, migration/rollback sur copie représentative, OCR navigateur CI sans assertion affaiblie, qualification Windows/Docker Desktop/PG16/Redis7/Chrome/Edge, volume annuel, matrice route × rôle × session × état métier, multipart sous charge/interruption/concurrence, injections de pannes, sécurité offensive, axe-core étendu et endurance plusieurs jours restent **non prêts**.

**Prochaine action précise :** rejouer sur arbre figé les campagnes PostgreSQL 16.2, Redis 7.4.7 et S3rver après fusion, puis reprendre les jobs CI ouverts un par un (`ocr`, `sauvegarde`, `integration`, `recette-*`, `e2e`, `hors-ligne-reel`) avec les bancs locaux du dépôt, en corrigeant chaque échec sans affaiblir d'assertion ni ajouter de skip. Poursuivre ensuite P0.2 et la sauvegarde coordonnée. Mettre à jour ce fichier avant tout arrêt ; aucun bilan intermédiaire dans le chat.

---

## Reprise du 01/11 — bancs reconstruits, campagnes PG/Redis, corrections CI (commits `8c4b833`, `d549cbf`, `556ccad`, `7735701`)

### Bancs reconstruits (perdus avec `/tmp`)

| Banc | État | Commande / preuve |
|---|---|---|
| PostgreSQL 16.2 TCP UTF8 | **opérationnel** | `initdb -D /tmp/seamtech-pgtcp/data -U postgres --encoding=UTF8 --locale=C.utf8` ; `pg_ctl -D /tmp/seamtech-pgtcp/data -o "-p 55498 -k /tmp/seamtech-pgtcp -c listen_addresses=127.0.0.1" -l /tmp/seamtech-pgtcp/server.log start` ; extensions `vector`, `unaccent`, `pg_trgm` créées |
| Redis 7.4.7 | **opérationnel** | compilé depuis `redis/redis` tag 7.4.7 (`make -j2 MALLOC=libc BUILD_TLS=no`) ; service sur `127.0.0.1:6390` |
| Tesseract 5.5.1 + Leptonica 1.85.0 | **compilé mais inutilisable** | `cmake` installé par pip, leptonica et tesseract construits dans `/tmp/outillage-seamtech` ; leptonica **sans PNG/TIFF** (aucun en-tête `png.h`/`zlib.h`/`tiffio.h` dans l'image, aucun dépôt système joignable) ⇒ `pixReadStreamPng: function not present`. Le banc OCR **reste non qualifiant** dans ce sandbox. |
| S3rver / MinIO / Chromium | **absents** | npm `s3rver` non qualifiant ; MinIO indisponible (registries retirés) ; Chromium non reconstruit |

### Campagnes exécutées (arbre figé, `-p no:randomly`)

| Campagne | Commande | Résultat |
|---|---|---|
| Suite non-PG | `pytest -q -rf -p no:randomly -m "not postgres and not s3 and not perf and not redis_queue"` | **2331 réussis / 58 ignorés / 6 xfail / 0 échec en 296 s** (`/tmp/seamtech-suite-fusion5.log`) |
| PostgreSQL | `SEAMTECH_TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:55498/postgres" pytest -q -rf -p no:randomly -m postgres` | **558 réussis / 50 ignorés / 2 xfail / 0 échec / 3 erreurs en 404 s** (`/tmp/seamtech-pg-fusion7.log`) ; les 3 erreurs = `redis-server introuvable`, rejouées avec le banc Redis : **3 réussis / 0 échec** ⇒ 561 réussis / 0 échec |
| Redis durable | `SEAMTECH_TEST_REDIS_SERVER=… SEAMTECH_TEST_REDIS_URL="redis://127.0.0.1:6390/1" pytest -q -rf -p no:randomly -m redis_queue` | **100 réussis / 41 ignorés / 0 échec en 107 s** (`/tmp/seamtech-redis-fusion2.log`) |
| Sauvegarde | `pytest -q -rf -p no:randomly tests/test_sauvegarde_*.py` | **73 réussis / 3 ignorés / 0 échec en 23 s** |
| Latence 1 500 fiches | `SEAMTECH_TEST_DATABASE_URL=… pytest -q -p no:randomly tests/test_recherche_hybride.py::test_charge_modeste_1500_fiches` | 3 mesures : p95 = **109,6 / 95,1 / 114,8 ms**, p50 = 31,9 / 40,5 / 42,1 ms, max ≈ 116 ms. Le critère produit p95 < 100 ms **flotte** sur ce banc 2 vCPU partagé (1 passage sur 3 au-dessus) ; `SEAMTECH_PERF_P95_CI_MS` permet de l'étiqueter sans jamais le desserrer. |

### Défauts corrigés (avec mesure avant/après)

| Défaut | Cause mesurée | Correction | Preuve |
|---|---|---|---|
| Restauration objet impossible | `copier_objets_s3` exigeait le `ChecksumSHA256` FULL_OBJECT du fournisseur | preuve hiérarchisée : checksum fournisseur → **ETag « contenu » (MD5 calculé au dépôt)** → identité `source_sha256` du catalogue → refus | 6 nouveaux tests (`tests/test_sauvegarde_references.py`, dont 1 rouge avant / vert après) + série sauvegarde 73 réussis |
| `S3StorageClient` sans création exclusive | méthode absente | `creer_fichier_exclusif()` (`IfNoneMatch: *`, `ChecksumAlgorithm=SHA256`) ; `StorageCleExistante` si 412, `StockageExclusiviteIndisponible` si paramètre non supporté, **jamais** de PUT inconditionnel en repli | `test_aller_retour_complet_base_donnees_et_objets` vert |
| Double S3 qui n'acceptait pas un flux | `bytes(Body)` sur un fichier ouvert ⇒ `TypeError` | le double lit un flux comme boto3 | idem |
| Migration PG : reconstitution arrêtée à une version antérieure impossible | le contrôle du schéma d'identité des octets s'appliquait même à une migration non demandée | le contrôle ne porte que sur une migration qui **devait** être appliquée | `tests/test_migrations_metier.py` **11 réussis** |
| Tout import annoncé « partial » et « à revoir » | rabaissement **inconditionnel** de `uploaded` dès qu'une destination est configurée (introduit par `8d03693`) | rabaissement **conditionnel aux preuves** : `uploaded` conservé si chaque artefact porte clé + empreinte vérifiée | 2 nouveaux tests (`tests/test_import_workflow.py`) ; explique `recette-corpus-reel` (REF-001..007 « envoi S3 en échec ») et `recette-locale` (`upload=partial`) |
| CI `ocr` : garde-fou déclenché par un nom de fichier | `grep -qiE "moteur|tesseract|ocr"` sur toute la ligne `SKIPPED … tests/test_scan_browser_upload_ocr.py` | garde-fou sur le **motif** seul + services `postgres`/`redis` ajoutés au job + échec explicite si le parcours saute faute de PostgreSQL ou d'outil de rendu | YAML validé (`yaml.safe_load`) |
| Parcours navigateur OCR jamais exécuté | condition de saut `pdftoppm absent` obsolète (le rendu est interne : `ocr/rendu_pdf.py`) | condition retirée ; l'OCR est exigé par la présence du terme unique de l'image | `ruff` vert |
| E2E `concurrence.spec.ts` / `hors-ligne.spec.ts` | `getByText(/REF-2026-CLIENT123/i)` résolu à 2 éléments (valeurs extraites **et** aperçu du classeur) — Playwright en mode strict | occurrence désignée explicitement (`.first()`), exigence métier inchangée | `tsc --noEmit` vert |
| Garde-fou `sauvegarde` comptant les xfail comme sautés | pytest range les xfail dans les `<skipped>` du JUnit XML (`type="pytest.xfail"`) | séparation xfail / saut réel (croisement XML + console), échecs attendus **publiés** avec motif | logique rejouée localement sur un vrai `junitxml` : `{'pytest.xfail'}` |
| Garde-fou S3rver figé | chaîne attendue `9 passed, … 4 xfailed` gravée ; la campagne compte **13** tests | exigence par comptage réel : collectés == passés + xfail, 0 saut, 0 échec, deux catégories non vides | collecte locale `-m s3rver` : 13 tests |

### CI au moment de l'écriture

- `main@bc2d4b2` (référence) : **tous les jobs verts** (run 41076604936).
- Branche à `e218134` : 8 jobs rouges (backend, ocr, sauvegarde, integration, e2e, hors-ligne-reel, recette-locale, recette-corpus-reel).
- Après `d549cbf` : `sauvegarde` ne présente plus `objets_s3.nb_copies = 0` (le défaut est corrigé) mais échoue sur le garde-fou xfail/saut ; `backend (3.13)` échoue sur **« Qualification réseau S3rver isolé »** (chaîne figée).
- `7735701` corrige ces deux garde-fous ; **résultat CI non encore connu**.

### Limites dites franchement

- Le banc **OCR n'est pas qualifiant** : tesseract construit ne sait pas lire d'image (leptonica sans PNG). Les tests `-m ocr_suite` qui exigent un moteur réel échouent ici de façon **environnementale** (11 échecs localement, tous « tesseract obligatoire … mais absent »).
- Docker n'est pas disponible dans le sandbox : `integration`, `recette-locale`, `recette-corpus-reel`, `hors-ligne-reel`, `e2e` ne sont reproductibles **que par lecture de code et d'annotations CI**.
- Chromium n'est pas reconstruit : les specs Playwright ne sont pas rejouées localement.

**Prochaine action précise :** lire le CI du commit `7735701` (`gh run view <id> --json jobs`), traiter `integration` (dossier de rapports vide : relire les annotations du job `compose-partage-web-worker`), puis mesurer la couverture (`coverage_gate`, plancher 85 % global et par module) et compléter si un module passe sous son seuil. Mettre à jour ce fichier avant tout arrêt.

---

## Reprise (suite) — `8c4b833`…`b16882f` : CI, dépôt, statut d'envoi

### Jobs CI : état mesuré par commit

| Job | `e218134` | `556ccad` | `7735701`/`3be3b5e` | Cause / correction |
|---|---|---|---|---|
| `ocr` | rouge | **vert** | vert | garde-fou sur le motif du saut (plus sur le nom du fichier) + services `postgres`/`redis` dans le job + condition « pdftoppm » obsolète retirée |
| `hors-ligne-reel` | rouge | **vert** | vert | sélecteur Playwright ambigu (référence affichée deux fois) |
| `recette-locale` | rouge | **vert** | vert | « upload=partial » ⇒ envoi prouvé désormais annoncé « uploaded » |
| `sauvegarde` | rouge (`objets_s3.nb_copies = 0`) | rouge (garde-fou) | **suite verte, garde-fou à corriger** | preuve d'identité par ETag contenu ; garde-fou : `SyntaxError` (`:=` dans une compréhension imbriquée) + marque XFAIL cherchée après le motif au lieu d'avant |
| `backend (3.11/3.12/3.13)` | rouge | rouge | rouge (étape S3rver) | chaîne figée « 9 passed, 4 xfailed » puis couples `findall` dépouillés à l'envers (`int("skipped")`) |
| `integration` | rouge | rouge | rouge | dossier de rapports vide (Docker : non reproductible ici) |
| `e2e` | rouge | rouge | rouge | à ré examiner après `3be3b5e` |
| `recette-corpus-reel` | rouge | rouge | — | REF-001..007 « envoi S3 en échec » (corrigé) puis « dépôt non traité — Un PDF existant au même chemin a changé » (corrigé par `1f37d16`) |

### Défauts corrigés depuis le précédent point

| Défaut | Cause | Correction | Preuve |
|---|---|---|---|
| Dépôt refusé sur un fichier déjà importé | la reprise ne comparait que le texte indexé (`content_hash`), jamais l'identité des octets | reprise accordée si texte identique, OU `source_sha256` identique, OU ligne vierge ; identité RÉTABLIE après l'upsert (`_etablir_identite_octets`) car le déclencheur `documents_invalider_source_sha256` la met à NULL sur toute mise à jour surveillée (y compris le rafraîchissement du vecteur) | nouveau test `test_meme_pdf_deja_passe_par_un_autre_flux_est_repris` ; `tests/test_depot_transactionnel.py` **66 réussis / 4 ignorés / 0 échec** |
| Publication incertaine déclarée « failed » | `status` = failed quand aucune donnée n'est extraite, même si un objet a pu être écrit | publication en attente ⇒ « à revoir », jamais un échec clos | `tests/test_snapshot_import.py` **54 réussis** |
| Envoi prouvé annoncé « partial » | rabaissement inconditionnel lié à la destination | « uploaded » conditionné aux preuves ; succès global (`all_verified`) toujours suspendu | sonde locale `POST /imports/confirm` : `upload_status = uploaded`, `transport_verifie = True`, `all_verified = False`, `status = needs_review` |
| Garde-fou S3rver | couples `(nombre, nom)` dépouillés à l'envers | ordre corrigé, logique rejouée sur un résumé synthétique (9 + 4 = 13) | exécution locale du bloc extrait du YAML |
| Garde-fou sauvegarde | `SyntaxError` + regex `^XFAIL` | boucle simple + « l'identifiant précède la marque » ; bloc EXÉCUTÉ sur un vrai junitxml : « 0 sauté (2 xfail exécutés) », motifs publiés | voir ci-dessus |

### Couverture (mesure locale, PostgreSQL + Redis réels, commande exacte CI)

`SEAMTECH_TEST_DATABASE_URL=… SEAMTECH_TEST_REDIS_SERVER=… pytest -m "not s3 and not perf" -q --cov=seamtech_search --cov-report=json:coverage.json`
→ **2929 réussis / 149 ignorés / 8 xfail / 7 échecs en 1057 s**, dont 6 échecs « environnement banc » ou règles désormais corrigées (1 échec restant : `test_ocr_qualification.py::test_scan_propre_retrouve_les_mots_attendus`, faute de moteur OCR lisible dans ce sandbox).

`python scripts/coverage_gate.py coverage.json` :

| Module | Mesuré | Plancher | Verdict |
|---|---|---|---|
| **TOTAL** | **85,5 %** | ≥ 85 % | **ok** |
| `api.py` | 91,5 % | 87 % | ok |
| `import_pipeline.py` | 94,2 % | 90 % | ok |
| `indexer.py` | 95,7 % | 90 % | ok |
| `ocr/*` (4 modules) | 75,2…92,6 % | 40…80 % | ok |
| `jobs.py` | 88,8 % | 94 % | **FAIL** |
| `redis_store.py` | 70,5 % | 92 % | **FAIL** |
| `storage.py` | 95,6 % | 97 % | **FAIL** |
| `worker.py` | 82,9 % | 92 % | **FAIL** |

Ces quatre modules ont grossi pendant la remédiation (worker 642 instructions, redis_store et storage réécrits) sans que leur couverture suive : la mesure est faite SANS s3rver ni MinIO (les tests qui les exigent sont ignorés), elle doit donc être refaite sur un banc complet avant de conclure — mais l'écart (jusqu'à −21,5 pts sur `redis_store`) est trop grand pour n'être qu'un effet de banc.

**Prochaine action précise :** (1) relire le CI de `b16882f` ; (2) combler la couverture de `redis_store.py`, `worker.py`, `jobs.py` et `storage.py` par des tests qui EXÉCUTENT les chemins ajoutés (aucun ajustement de plancher — les planchers sont une exigence produit) ; (3) traiter `integration` (dossier de rapports vide) à partir des annotations du job `compose-part-web-worker` ; (4) réexaminer `e2e`. Mettre à jour ce fichier avant tout arrêt.

### Couverture — seconde mesure, AVEC le banc Redis branché

La première mesure (ci-dessus) ne branchait pas `SEAMTECH_TEST_REDIS_URL` : les
140 tests de file durable étaient donc ignorés et les modules mesurés comme s'ils
n'étaient presque jamais exécutés (exactement le défaut déjà documenté dans le
workflow, lignes 626-634). Seconde mesure, commande exacte :

`SEAMTECH_TEST_REDIS_URL="redis://127.0.0.1:6390/1" SEAMTECH_TEST_REDIS_SERVER=… SEAMTECH_TEST_DATABASE_URL=… pytest -q -p no:randomly -m redis_queue --cov=<module>`
→ **140 réussis / 1 xfail / 0 échec en 270 s**, et :

| Module | Avec Redis (campagne `redis_queue` seule) | Plancher |
|---|---|---|
| `redis_store.py` | 86 % (566 instr., 79 manquantes) | 92 % |
| `worker.py` | 78 % (642 instr., 142 manquantes) | 92 % |
| `jobs.py` | 79 % (348 instr., 72 manquantes) | 94 % |
| `storage.py` | 49 % (520 instr., 267 manquantes — la campagne `redis_queue` n'exerce presque pas le stockage objet) | 97 % |

En CI la mesure est l'UNION de toutes les campagnes : `storage` et `worker`
remontent (95,6 % et 82,9 % mesurés sans Redis), `jobs` et `redis_store` aussi,
mais aucun des quatre n'atteint son plancher au vu des écarts mesurés
(jusqu'à −21,5 pts pour `redis_store` sur le banc complet, −6 pts pour `worker`).

Lecture honnête : la remédiation a ajouté beaucoup de code exécutable
(worker 642 instructions, redis_store et storage réécrits, jobs étendu) sans
ajouter les tests qui l'exercent. **Le plancher n'est pas une formalité : c'est
l'exigence produit.** Aucun plancher ne sera abaissé — le travail à faire est
d'écrire les tests.

**Prochaine action précise (dans cet ordre) :**
1. relire le CI de `2426f92` et noter job par job ;
2. combler la couverture de `redis_store.py` (92 %), `worker.py` (92 %),
   `jobs.py` (94 %) et `storage.py` (97 %) : lire les lignes manquantes
   (`coverage.json` → `files[<module>]["missing_lines"]`) et écrire les tests
   qui les exécutent réellement, jamais un test qui ne fait que passer ;
3. traiter `integration` (dossier de rapports vide : annotations du job
   `compose-partage-web-worker`) et réexaminer `e2e` ;
4. rejouer la suite complète et la campagne PG après chaque lot ;
5. ne RIEN résumer dans le chat avant le rapport final PRÊT/NON PRÊT.

---

## 2026-11-01 (suite) — `integration` résolu, `/open` corrigé, scan élagué, 2 xfail levés

HEAD poussé à la fin de ce bloc : **`9bf0c72`**. Tous les commits ci-dessous sont
poussés sur `arena/b936b3c0-seamtech-search`. Aucune fusion vers `main`.

### 1. `integration` / `compose-partage-web-worker` — CAUSE TROUVÉE (PermissionError avalée)

Symptôme, run après run : « dossier de rapports vide », alors que la sonde
conteneur voyait `technical-report.pdf` + `technical-report.docx` dans
`/app/data/reports/<id>`.

Cause réelle (`9bf0c72`) : le dossier de rapports d'un import est créé en
**0700** (`import_pipeline._generer_rapports_confines` :
`os.mkdir(composant, mode=0o700, dir_fd=parent)` — confinement des données
extraites). Web, worker et sauvegarde partagent la même image, donc le même UID,
et y accèdent ; **l'hôte n'est pas propriétaire** et ne peut ni parcourir ni
statuer. Or le test lisait le dossier depuis l'hôte avec `Path.rglob`, **qui
avale silencieusement les `PermissionError`** : un dossier confiné y est
indiscernable d'un dossier vide. Diagnostic tombé grâce au témoin ajouté au run
précédent, qui a fait remonter l'erreur réelle :

```
PermissionError: [Errno 13] Permission denied: '.../data/reports/<id>/.sonde-partage-rapports'
```

Correctif : la preuve de partage se fait **dans** les conteneurs (le service
`web` liste les rapports écrits par le `worker`, avec tailles ; le worker relit
les siens au même chemin), le confinement est **verrouillé** par une assertion
(`mode & 0o077 == 0`), et l'observation hôte est désormais **non silencieuse**
(`_observation_hote` : existence, mode, refus de parcours, refus de stat —
annoncés dans une annotation). Fenêtre d'attente bornée de 30 s conservée
(publication par renommage depuis un dossier temporaire). Témoin retiré : la
lecture par le web le remplace avantageusement.

**Leçon à garder** : jamais de `rglob`/`glob` pour constater un contenu — ils
transforment un refus d'accès en absence.

### 2. `recette-corpus-reel` — `/open` et l'endpoint public (`57c5701`)

Échec : `test_10_sauvegarde_restauration_base_neuve` — « endpoint public déclaré
: HTTP 200 (attendu 302) ». Après le correctif 04c (`f10627f`), le reste de la
recette passe (`fichiers=14 pages=30 refus=0 motifs[aucun]`) ; seul ce point
restait.

Cause : depuis la promotion des dépôts vers l'objet (P0.2), un document stagé
porte une empreinte d'octets, et `POST /open` réservait la redirection
présignée aux documents SANS empreinte — condamnant tout déploiement déclarant
un endpoint public à faire transiter chaque octet par l'API, contre le contrat
2026-10-07 (E-28).

Règle retenue (preuve, pas préférence) : la table `documents` ne reçoit
`object_key` que par `lier_objets_capture`, dont l'`UPDATE` exige
`source_sha256` = l'empreinte de l'envoi **vérifié** et le bon compartiment. La
ligne prouve donc que l'objet est CES octets → redirection permise. Sinon,
repli antérieur au mot près : l'API lit et vérifie les octets. Même règle sur
`/imports/{id}/artifacts/{artifact}`. La preuve est **datée** (elle ne couvre
pas une réécriture ultérieure de l'objet) : écrit dans le code et les tests.

Nouveau fichier `tests/test_open_capture_endpoint_public.py` (5 tests) :
capture prouvée → 302 vers l'hôte PUBLIC (jamais un hôte interne) ;
capture + objet d'un autre compartiment → 200 vérifié, aucune présignature ;
sans endpoint public → 200 sans `Location` ; fichier non conforme → 404 ;
et le garde-fou : `lier_objets_capture` n'attache aucune clé à un document
d'une autre empreinte. **Rouge → vert mesuré** : sans le correctif, le premier
test échoue avec le message exact de la CI.

Mesures : `74 passed, 24 skipped` (présignature/téléchargement/API/stockage).

### 3. Scan : élaguer staging et quarantaine au lieu de refuser la racine (`b1901de`)

`xfail(strict)` levé : « le balayage indexe le staging conservé lorsque data est
sous une racine ». Le choix assumé était de **refuser** tout recouvrement
(`ScanIncompleteError`) tant qu'une exclusion ne savait pas préserver les
lignes d'index. Conséquence bien plus grave que le xfail : une racine contenant
le répertoire `data` n'était plus balayable **du tout**.

L'exclusion sûre est maintenant écrite, des deux bouts : `crawl` élague
`uploads` et `quarantine` (filtre par nom + confirmation par résolution, liens
symboliques résolus), une racine **dans** une zone reste refusée ; et
`remove_missing` accepte des **préfixes protégés** transmis par
`cli.run_index`, sans quoi exclure un sous-arbre le ferait *supprimer* de
l'index.

Tests : racine dans le staging → refus, index intact (2 formes) ; racine
contenant le staging → scan effectué, reste de la racine indexé, aucun document
du staging indexé, lignes préexistantes conservées (2 formes) ; quarantaine
élaguée (nouveau) ; staging absent puis apparu (exclusion indépendante de son
existence). Mesures : **253 passed, 19 skipped**.

### 4. Reprise après envoi partiel : le bon chemin prouvé (`bf7f397`)

`xfail(strict)` « Reprise après upload partiel refuse le document déjà
préservé » : le test appelait `import_folder` une seconde fois et attendait
`transport_verifie`, ce que le produit **refuse volontairement** (garde voisine
`test_reimport_partiel_refuse_avant_regeneration_et_nouvel_envoi`). Les deux
attentes étaient incompatibles ; le vrai chemin de reprise est `retry_upload`
(bouton « renvoyer », idem worker). Le test le prouve désormais : rien n'est
régénéré (`generate_report` piégé + octets du rapport comparés), les preuves
acquises gardent leur clé, les documents déjà liés gardent leur `object_key`,
les envois manquants sont complétés (`transport_verifie`), et le succès global
reste suspendu (`all_verified` faux — ni snapshot ni liaison transactionnelle).
Aucune assertion affaiblie, `import_pipeline` **non modifié**.

### 5. État CI au moment de l'arrêt

| Run | HEAD | Jobs |
|---|---|---|
| `41347433108` | `d4b3489` | échecs : `integration` (rapports vides → corrigé `9bf0c72`), `recette-corpus-reel` (`/open` → corrigé `57c5701`) |
| `41348061461` | `1b278cb` | échecs : `integration` (PermissionError révélée), `recette-corpus-reel` (avant `57c5701`) ; `backend (3.11/3.12/3.13` en cours) |
| `41349826847` | `b1901de` | en cours (ne contient pas encore `bf7f397`/`9bf0c72`) |

`xfail(strict)` restants (documentés, non levés) : `test_import_simple_fencing_pg`
(atomicité distribuée), `test_minio_historique` (MinIO 2023, substitut
historique), `test_restauration_bundle_s3rver` ×3 (limites du substitut S3rver),
`test_restauration_confinement` (publication multi-fichiers non transactionnelle),
`test_sauvegarde_snapshot_pg` (octets locaux non figés au snapshot),
`test_sauvegarde_unites` ×2 (verrou de dossier), `test_verrou_mutation_import` ×2
(verrou SQLite / session advisory).

**Prochaine action précise :** (1) relire le CI de `9bf0c72` job par job —
`integration` et `recette-corpus-reel` doivent passer, sinon repartir des
annotations ; (2) vérifier `backend (3.11/3.12/3.13)` sur le HEAD exact ;
(3) reprendre `test_scan_propre_retrouve_les_mots_attendus` (OCR) ; (4) attaquer
les xfail restants un par un, chacun avec un test rouge→vert ; (5) ne RIEN
résumer dans le chat avant le rapport final PRÊT/NON PRÊT.

### 6. Correction de `57c5701` (CI) — deux routes, deux contrats (`d68b905`)

La CI a refusé l'extension de la règle `/open` à la route artefacts :

```
FAILED tests/test_integrite_telechargement_import.py::
  test_telechargement_verifie_ne_redirige_pas_vers_s3[source_pdf-None]
  test_telechargement_verifie_ne_redirige_pas_vers_s3[report_pdf-report_path]
```

À juste titre : la route artefacts est le « téléchargement VÉRIFIÉ » (octets
relus et contrôlés à la réponse ; une URL présignée ne peut pas revérifier —
jamais de 302 pour une source capturée), `/open` est la route historique dont
le contrat E-28 impose la redirection dès qu'un endpoint PUBLIC est déclaré.
La règle artefacts est rétablie au mot près, la différence est commentée dans
le code, et le garde-fou de comptage des appels reste à 4. Mesures locales :
`55 passed`.

### 7. Verrou de sauvegarde : dossier remplacé (`4d38e3a`)

`VerrouSauvegarde` verrouille désormais aussi le **parent** du dossier de
sauvegarde. Sans lui, un détenteur dont le dossier est renommé (l'inode
verrouillé n'est plus le chemin) laissait un second venu verrouiller le
nouveau dossier du même nom : deux sauvegardes simultanées, invisibles l'une
pour l'autre. xfail strict levé (rouge → vert mesuré). Non-régression sur toute
la surface sauvegarde/restauration : **306 passed, 2 skipped, 6 xfailed**.

### 8. État CI relu sur `e5c2439` (run `41350813297`)

- **succès** : `recette-corpus-reel` (le correctif `/open` est donc validé par
  la CI), `sauvegarde`, `ocr`, `docker`, `securite-dependances`,
  `hors-ligne-reel`, `frontend`, `Qualification native`, `Perf comparaison` ;
- **échec** : `backend (3.13)` — 2 tests, la route artefacts (voir §6, corrigé
  dans `d68b905`) ;
- `backend (3.11)` et `backend (3.12)` : `cancelled` (annulés, pas échoués) —
  à relire sur le HEAD suivant ;
- `integration`, `recette-locale`, `e2e` : en cours au moment de l'arrêt.

**Prochaine action précise :** (1) relire le CI de `d68b905` : `backend
(3.11/3.12/3.13)`, `integration` (le correctif `9bf0c72` doit le faire passer)
et `recette-locale` ; (2) si `integration` échoue encore, repartir de
l'annotation « rapports vus par le web » ; (3) reprendre
`test_scan_propre_retrouve_les_mots_attendus` (OCR) ; (4) xfail restants, un
par un, chacun avec un test rouge→vert ; (5) ne RIEN résumer dans le chat avant
le rapport final PRÊT/NON PRÊT.

### 9. `integration` — la sonde conteneur était elle-même cassée (`3900a06`)

Le run `41350813297` (HEAD `e5c2439`) a livré un diagnostic complet… et
trompeur :

- hôte : `data/reports/<id>` **existe**, mode **0o700**, parcours refusé
  (`refus_attendu: true`) — l'analyse du §1 est confirmée ;
- `data/reports` : mode 0755, contient bien le dossier du job ;
- la sonde `_report_dir_for` du worker voit `technical-report.docx` +
  `technical-report.pdf` ;
- **mais** `vu_par_web = null` et `vu_par_worker = null`.

Ce n'était pas le produit : c'était la sonde. `import json,os,sys;p=sys.argv[1];try: …`
est un **SyntaxError** (un `try:` ne peut pas suivre un point-virgule). stdout
restait vide, `json.loads` échouait, la fonction rendait `(False, None, None)`
— « le conteneur ne voit rien », alors que rien ne s'était exécuté.

Corrigé par un vrai script multiligne, **vérifié hors conteneur** (dossier 0700
→ `{"existe": true, "contenu": [{"nom": "a.pdf", "taille": 0}], "mode": 448}`).
Et une sonde illisible est désormais ANNONCÉE (rc, stdout, stderr) : un défaut
de mesure ne doit plus jamais se faire passer pour une absence de fichier.

À noter, vérifié dans `docker-compose.yml` : aucun `user:` n'est surchargé —
`web`, `worker` et `sauvegarde` partagent la même image, donc le même UID, et
peuvent lire les rapports en 0700. C'est ce qui rend la preuve « le web voit les
fichiers du worker » valide malgré le confinement.

**État à l'arrêt du tour** (HEAD `3900a06`, poussé) : les `backend (3.11/3.12)`
étaient `cancelled` sur `e5c2439` et n'ont donc PAS été lus ; `backend (3.13)`
échouait sur les 2 tests de la route artefacts, corrigés par `d68b905`. Aucun
run n'a encore été lu sur `d68b905`/`3900a06`.

**Prochaine action précise :** relire le CI du HEAD `3900a06` job par job —
`integration` (le correctif de sonde doit le faire passer), `backend
(3.11/3.12/3.13)`, `recette-corpus-reel`, `e2e` ; puis reprendre
`test_scan_propre_retrouve_les_mots_attendus` (OCR), puis les xfail restants un
par un (chacun avec un test rouge→vert), puis P0 sauvegarde hors-site, P1
rotations PDF, P2 matrice/charge/pannes. Ne RIEN résumer dans le chat avant le
rapport final PRÊT/NON PRÊT.

---

### 10. Historique git local resynchronisé (incident de banc, pas de produit)

Au début de ce tour, `git status` montrait ~150 fichiers modifiés et ~150 non
suivis : le dépôt local avait été **remonté à `f1733c8`** (base de la branche)
alors que l'arbre de travail, lui, contenait tout le travail des tours
précédents. Les 38 commits poussés (`e079aae` et ses ancêtres) étaient absents
du dépôt local — ils existaient sur `origin`.

Diagnostic : divergence locale/distant de **38 / 1** (`git rev-list
--left-right --count origin/...HEAD`), parent de HEAD = `f1733c8`.

Correction appliquée, sans toucher à l'arbre de travail :

```
git fetch origin arena/b936b3c0-seamtech-search
git reset --mixed origin/arena/b936b3c0-seamtech-search
```

`--mixed` (jamais `--hard`) : HEAD revient à `e079aae`, l'index est refait,
**l'arbre de travail n'est pas touché**. Après l'opération, `git status` ne
montrait plus que 5 fichiers : les 2 du travail en cours et 3 fixtures PDF
régénérées à l'identique (vérifié : même `/Rotate`, même texte extrait — seules
les métadonnées reportlab diffèrent) → `git checkout --` sur les 3 fixtures.

Le piège, noté pour la suite : un `git stash push -- <fichier>` sur un dépôt
dont l'historique a été remonté **rejoue l'ancien contenu** (celui de
`f1733c8`), ce qui casse tout (`ImportError: cannot import name 'copier_data'`).
Comparer « avant/après » un correctif se fait donc en rééditant le fichier, en
mesurant, puis en restaurant la copie — pas avec `stash`.

### 11. Bancs reconstruits (perdus avec la réinitialisation)

Le `.venv` et les bancs de `/tmp` avaient disparu. Reconstruits, avec les
commandes exactes :

```bash
# Environnement Python
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# PostgreSQL 16.2 (pgserver ne fournit QUE des sockets unix : on l'initialise
# à la main pour obtenir le TCP qu'attendent les fixtures)
PGBIN=.venv/lib/python3.11/site-packages/pgserver/pginstall/bin
$PGBIN/initdb -D /tmp/pg-tcp -U postgres --auth=trust -E UTF8 --locale=C
$PGBIN/pg_ctl -D /tmp/pg-tcp -o "-p 55498 -h 127.0.0.1 -k /tmp" -l /tmp/pg-tcp.log start

# Redis 7.4.7 compilé depuis les sources archivées
cd /tmp/redis-banc && curl -sSL -o redis.tar.gz \
  https://codeload.github.com/redis/redis/tar.gz/refs/tags/7.4.7 && tar xzf redis.tar.gz \
  && cd redis-7.4.7 && make -j4
/tmp/redis-banc/redis-7.4.7/src/redis-server --port 6390 --save '' --appendonly no --dir /tmp

# S3rver (npm) — écoute sur 0.0.0.0, sinon IPv6 seul et 127.0.0.1 échoue
cd /tmp/s3rver-banc && npm install s3rver@3.7.1
./node_modules/.bin/s3rver -d /tmp/s3rver-data -p 14569 -a 0.0.0.0
```

Variables de banc pour toute commande pytest :

```
SEAMTECH_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55498/postgres
SEAMTECH_TEST_REDIS_URL=redis://127.0.0.1:6390/1
SEAMTECH_TEST_REDIS_SERVER=/tmp/redis-banc/redis-7.4.7/src/redis-server
SEAMTECH_TEST_S3RVER_URL=http://127.0.0.1:14569
```

Deux pièges coûteux, à ne pas revivre :

* **`initdb` sans `-E UTF8`** : le cluster démarre en `SQL_ASCII` et toute la
  suite échoue en `UnicodeEncodeError` (14 failed, 23 erreurs) — ce n'est pas
  le produit, c'est le banc ;
* **`SEAMTECH_TEST_REDIS_SERVER` omis** : les tests Redis ne sautent pas, ils
  **échouent** (`redis-server introuvable`, 12 erreurs).

**Limite du banc local, dite franchement** : l'image `pgserver` n'embarque pas
les modules `contrib` — ni `unaccent` ni `pg_trgm`. Deux tests échouent donc
localement et **uniquement** localement :
`test_sauvegarde_references.py::test_aller_retour_fichiers_vers_cible_distincte`
et les 4 de `tests/test_migration_rollback.py`
(`text search configuration "seamtech_unaccent" does not exist`). La CI utilise
`pgvector/pgvector:pg16`, qui les fournit. Vérifié par comparaison avant/après
correctif, pas supposé.

### 12. Sauvegarde — les octets locaux dérivaient pendant le dump (`a13017b`)

Avant : `pg_dump` d'abord, copies des fichiers référencés **ensuite**. Un
fichier modifié pendant la fenêtre du dump entrait dans le bundle avec ses
NOUVEAUX octets alors que la base portait l'ancienne empreinte.

Après : les copies sont faites **AVANT** le dump, dans la **même transaction de
snapshot** (`pg_export_snapshot`, REPEATABLE READ). Puis chaque source est
revérifiée APRÈS le dump (taille + `st_mtime_ns` relevés à la copie) : toute
dérive est consignée dans `manifeste["derive_octets_locaux"]`
(`detectee`, `nb_fichiers`, `fichiers`, `methode`) et remet `complet` /
`complet_perimetre_demande` à faux.

Le correctif ne prétend pas **figer** les octets — sans gel applicatif du
système de fichiers c'est impossible. Il prétend les **détecter et les dire**,
jamais les corriger en silence. Écrit dans le code, dans le manifeste et dans
les tests.

Tests : xfail strict retiré sur
`test_mutation_locale_apres_snapshot_ne_donne_pas_copie_incoherente_complete`
(rouge → vert mesuré : la copie contenait `b"apres"`, elle contient `b"avant"`) ;
nouveau `test_copie_fidele_au_snapshot_malgre_mutation_pendant_le_dump` ;
`test_sauvegarde_ne_copie_pas_source_divergente_de_capture_pg` rendu
**déterministe** (la divergence est produite avant la sauvegarde : depuis le
changement d'ordre, la mutation « pendant le dump » ne testait plus ce qu'elle
croyait).

Mesure : **307 passed, 2 skipped, 5 xfailed** sur les 16 fichiers
sauvegarde/restauration, trois bancs branchés.

### 13. Purge de la copie locale — le serveur se remplissait (`4b046c4`, `1b092de`)

Deux pannes successives, et il faut garder les deux en tête :

1. **la panne « souvenir »** — supprimer la copie locale parce qu'un envoi a
   réussi hier. Une preuve d'envoi ne couvre ni un fichier ajouté après
   l'inventaire, ni un fichier modifié depuis, ni la disparition de l'objet.
   C'est pour elle que toute purge avait été suspendue ;
2. **la panne « suspension »** — ne jamais rien supprimer. `decider_purge`
   renvoyait `False` partout, `prune_staged_uploads` renvoyait `0` : le serveur
   se remplissait sans qu'aucun indicateur ne le dise. L'intégration compose
   l'a rattrapée (« le brouillon n'a pas été purgé après un envoi vérifié »).

Protocole retenu (`seamtech_search/purge_locale.py`) : la décision passe du
souvenir à l'**instant**. `decider_purge` n'accorde qu'un droit d'examen ; chaque
suppression est conditionnée, **fichier par fichier**, à (1) l'appartenance au
staging, (2) une **relecture des octets distants** au moment même
(`verifier_integrite`), (3) l'empreinte locale revérifiée juste avant
l'effacement, (4) l'inventaire du dossier (rien qui n'ait de preuve n'est
supprimé), (5) le refus de suivre un lien symbolique. Une purge **partielle**
est un état normal et DIT dans `job.result.cleanup`.

**Le premier correctif ne purgeait toujours rien en CI.** Cause trouvée dans
`import_folder` : `all_verified=False` y est **hard-codé** (« la vérification
d'un lot d'objets ne prouve pas le snapshot de l'inventaire »). Or
`decider_purge` exigeait `integrite_prouvee`, qui exige `all_verified` : la
condition d'accès était une porte que le produit s'est interdit d'ouvrir. Ce
n'est pas `all_verified` qui a tort, c'est la purge qui demandait la mauvaise
preuve — globale et passée au lieu de locale et contemporaine.

Confidentialité : les motifs de refus recopiaient les **chemins** des artefacts
dans le payload et le journal du worker (donc, en production, des noms de
commanditaires). On compte désormais, on ne cite pas.

Bug trouvé par un test : `S3StorageClient(config)` passait la configuration en
**positionnel** (`endpoint_url` est le premier paramètre) — un client qui se
déclarait configuré sans l'être.

Mesures : 21 nouveaux tests ; **277 passed, 2 xfailed** sur
worker/import/reprise/rétention/lots ; `integration` compose **succès**.

### 14. Endurance et sécurité offensive — deux points P2 sans preuve (`cb097bc`, `6f0fe0c`)

**Endurance** (`tests/test_endurance.py`, workflow `Endurance`) : N cycles
(25 en garde-fou, 300 dans le job dédié), annulation injectée tous les 7
(resp. 13) cycles **au pire moment** (juste avant la purge). Invariants par
comptages : aucun dossier résiduel hors cycles annulés, aucun job en vol,
aucun objet dupliqué, aucune dérive des descripteurs. Garde vérifiée :
`sous_staging=False` ⇒ échec « fuite de staging : 25 dossier(s) au lieu de 3 ».
**Trois jours d'horloge ne sont pas exécutés** — c'est écrit dans le fichier.

**Sécurité offensive** (`tests/test_securite_offensive.py`) : traversée de
chemin au téléversement (neutralisée, rien n'est écrit hors staging),
injection d'en-tête par un nom portant CR/LF, escalade de rôle par en-tête sur
session valide, création de compte par en-tête seul, lien symbolique déposé,
traversée sur les routes artefacts et `/preview`.

Deux constats honnêtes, écrits dans les tests au lieu d'être maquillés : la
gestion des comptes **ne lit aucun en-tête de rôle** (l'usurpation y est
structurellement impossible) — le test d'escalade vise donc
`/approvals/{id}/decide`, qui le lit ; `/health` expose le chemin de la base
**uniquement** sans jeton de service configuré.

Garde ROUGE→VERT prouvée : en sabordant `securite_requetes.py` (rôle pris dans
l'en-tête + garde d'incompatibilité retirée), la requête franchit
l'autorisation — « escalade de rôle réussie : HTTP 422 ». Code réel : 8 passed.

### 15. Envoi interrompu : le brouillon était abandonné (`119a51f`)

Test écrit d'abord (ROUGE), correctif ensuite (VERT). `ClientDisconnect` ne
faisait partie d'aucune des quatre issues cataloguées par `/imports/upload` :
le dossier de staging restait, avec son fichier à moitié écrit — et plus rien
ne repassait derrière (`prune_staged_uploads` suspendu). Filet ajouté :
`except BaseException: shutil.rmtree(staged, ignore_errors=True); raise` — on
nettoie, on ne propage pas un faux succès (couvre aussi `CancelledError`).
Confidentialité : le journal ne cite plus le chemin de staging.

Mesures : 3 nouveaux tests ; **187 passed** sur upload/API/import/idempotence.

### 16. Suite complète locale — mesure et lecture des 43 échecs

Commande (bancs PostgreSQL 16.2 + Redis 7.4.7 + s3rver) :

```
SEAMTECH_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55498/postgres \
SEAMTECH_TEST_REDIS_URL=redis://127.0.0.1:6390/1 \
SEAMTECH_TEST_REDIS_SERVER=/tmp/redis-banc/redis-7.4.7/src/redis-server \
SEAMTECH_TEST_S3RVER_URL=http://127.0.0.1:14569 \
PYTHONPATH=$PWD .venv/bin/python -m pytest -q -p no:randomly --ignore=tests/test_recette_corpus_reel.py
```

Résultat : **3090 passed, 43 failed, 52 skipped, 8 xfailed, 4 errors** en
14 min 32 s.

Lecture des échecs — ils ont une cause unique et elle est environnementale :
l'image `pgserver` n'embarque pas les modules `contrib`. Deux avertissements,
émis au démarrage de chaque base, le disent sans ambiguïté :

```
unaccent indisponible pour seamtech_texte_recherche (extension "unaccent" is not available)
pg_trgm indisponible (extension "pg_trgm" is not available)
```

Tous les échecs relèvent de la recherche accentuée/tolérante aux fautes et de
la déduplication probabiliste — exactement les fonctions bâties sur ces deux
modules : `test_recherche_normalisation`, `test_recherche_hybride`,
`test_recherche_dimension`, `test_recherche_fonds_reel`,
`test_passe_finale_recherche_variantes`, `test_qualification_chemins_volume`,
`test_lot4a_extrait_document`, `test_migrations_metier` (capacités/extensions),
`test_dedup`, `test_sauvegarde_references` (1 test), et les 4 erreurs de
`test_migration_rollback` (`text search configuration "seamtech_unaccent"
does not exist`).

Le produit **documente lui-même les replis** : sans `pg_trgm`, la détection des
doublons « probables » bascule sur un repli déterministe (titre identique **et**
même client+bateau+gamme+année), ce qui explique très exactement l'échec de
`test_meme_titre_clients_differents_pas_de_doublon_exact` (clients différents ⇒
le repli ne les rapproche pas).

La contre-épreuve est la CI, qui utilise `pgvector/pgvector:pg16` (contrib
complet) : c'est elle qui tranche.

### 17. Qualification Windows — un runner, pas le poste de l'utilisatrice

`.github/workflows/qualification-windows.yml` (P1). Le plan demande de
qualifier Windows / Docker Desktop / PostgreSQL 16 / Redis 7 / Chrome / Edge.
Aucun de ces points n'est atteignable depuis un banc Linux, et le poste de
l'utilisatrice ne doit pas servir de banc de découverte.

Le job installe PostgreSQL 16 et Redis 7 sur l'hôte (les services de
conteneurs GitHub ne fonctionnent pas sur Windows), exécute la suite hors
services, la suite PostgreSQL + Redis, le CLI, et un chemin à accents et
espaces.

Trois limites écrites dans le workflow, pas passées sous silence : Docker
Desktop n'est pas qualifiable (pas de conteneurs Linux sur un runner Windows
standard), Chrome et Edge ne sont pas exécutés ici (l'interface est qualifiée
par le job e2e sur Linux), et les fichiers POSIX-only (`SIGKILL`, `fork()`)
sont écartés **et nommés** dans la commande.

### 18. Limites dites franchement (aucune n'est maquillée)

1. **Modules `contrib` PostgreSQL** (`unaccent`, `pg_trgm`) absents du banc
   local → 43 échecs locaux, non reproductibles en CI. La CI tranche.
2. **Docker Desktop, Chrome, Edge, Windows réel** : non qualifiés (voir §17).
3. **Volumétrie annuelle réelle** (100 000 fichiers / 20 Gio, une année
   d'archives) : non mesurée. Les bornes de session sont testées à 100 000, un
   dépôt réel de cette taille exige le corpus du commanditaire — qui ne doit
   pas entrer dans Git. Le job `Scale benchmark (SYNTHÉTIQUE)` ne prouve que le
   synthétique.
4. **Endurance « plusieurs jours »** : non exécutée. Le soak prouve les
   invariants de fuite sur 300 cycles, pas la tenue à trois jours d'horloge.
5. **MinIO/R2 réels** : les tests d'historique MinIO (`-m minio_historique`,
   7 tests) sautent sans l'instance isolée ; le transport réel est exercé
   ailleurs (intégration compose, qualification réserves).

### 19. `contrib` PostgreSQL reconstruit : la suite locale rejoint enfin la CI

Les 43 échecs + 4 erreurs de la suite complète locale (§16) n'étaient pas du
produit : l'image `pgserver` embarquée n'installe pas les modules `contrib`.
`unaccent` et `pg_trgm` étaient donc indisponibles, le produit basculait sur
ses replis documentés, et une cinquantaine de tests qui exigent le SQL réel
(tsvector accentué, similarité trigramme, index d'expression) échouaient.

Démontré, pas supposé : `psycopg2.errors.UndefinedObject: text search
configuration "seamtech_unaccent" does not exist`.

Correctif de BANC (rien dans le produit, rien dans Git) : les deux modules ont
été reconstruits depuis les sources officielles `REL_16_2` contre le
`pg_config` de `pgserver`, puis installés dans son arbre.

```bash
curl -sSL -o pg.tar.gz https://codeload.github.com/postgres/postgres/tar.gz/refs/tags/REL_16_2
tar xzf pg.tar.gz
PG=/home/user/SEAMTECH-search/.venv/lib/python3.11/site-packages/pgserver/pginstall/bin/pg_config
make -C postgres-REL_16_2/contrib/unaccent USE_PGXS=1 PG_CONFIG=$PG && make -C postgres-REL_16_2/contrib/unaccent USE_PGXS=1 PG_CONFIG=$PG install
make -C postgres-REL_16_2/contrib/pg_trgm  USE_PGXS=1 PG_CONFIG=$PG && make -C postgres-REL_16_2/contrib/pg_trgm  USE_PGXS=1 PG_CONFIG=$PG install
```

Preuve que les modules servent :

```
CREATE EXTENSION IF NOT EXISTS unaccent; CREATE EXTENSION IF NOT EXISTS pg_trgm;
SELECT unaccent('éàç'), similarity('voiles','voile');
-->  eac | 0.625
```

Suite complète relancée, à commande identique (`pytest -q -p no:randomly
--ignore=tests/test_recette_corpus_reel.py`, 4 variables de banc) :

| mesure | avant | après |
|---|---|---|
| passés | 3090 | **3140** |
| échecs | 43 | **1** |
| erreurs | 4 | **0** |
| sautés | 52 | 48 |
| xfail | 8 | 8 |
| durée | 872,55 s | 1003,12 s (16 min 43) |

L'unique échec restant était `tests/test_ci_executable.py` — c'est-à-dire le
garde-fou des workflows, rendu rouge par le workflow Windows ajouté la veille.
Il est corrigé et prouvé au §20. **La suite locale est désormais le même
arbitre que la CI** (`pgvector/pgvector:pg16`, contrib complet) : plus aucun
écart d'environnement ne peut être invoqué pour expliquer un échec.

Conséquence directe : `tests/test_migration_rollback.py`, qui produisait 4
erreurs, passe (campagne ciblée ci-dessous), et le point P0 « migration et
rollback sur une copie représentative » est prouvé sur PostgreSQL réel.

Campagne ciblée P0/P1 (sauvegardes, restauration, hors-site, rétention,
migrations/rollback, rotations PDF 90/180/270, provenance OCR), bancs levés :

```
357 passed, 6 skipped, 1 xfailed in 48.47 s
```

Aucun `xfail` sur les rotations : `tests/test_pdf_orientation.py` le dit
lui-même (« aucun test n'est marqué xfail : une régression casse la CI »), ce
qui valide le point P1 « retirer les xfail des rotations ».

### 20. Le garde-fou des blocs `run:` analysait du PowerShell avec `bash -n` (`5c4bd1a`)

Run `41360147937` : job `backend (3.12)` rouge sur l'étape « Run SQLite
suite ». Le coupable :

```
FAILED tests/test_ci_executable.py::test_chaque_bloc_run_est_un_script_analyse
  qualification-windows.yml · windows / Démarrer Redis 7 :
  /tmp/…sh: line 1: syntax error near unexpected token `('
  /tmp/…sh: line 1: `$candidats = @()'
```

Le garde-fou soumettait chaque bloc `run:` à `bash -n`, y compris les huit
blocs PowerShell du nouveau workflow. `$candidats = @()` est une erreur pour le
shell bourne et ne dit rien de la validité du PowerShell : **le contrôle se
trompait d'outil et produisait des verdicts faux**. C'est exactement le défaut
que ce fichier combat — retourné contre lui, et rendu visible par le
garde-fou d'un autre fichier.

Correctif : le shell de chaque étape est résolu comme GitHub le fait (`shell:`
de l'étape, sinon `defaults.run.shell` du job, sinon du workflow, sinon `pwsh`
sur Windows et `bash` ailleurs), puis analysé par `bash -n` ou par le parseur
PowerShell réel (`[System.Management.Automation.Language.Parser]::ParseFile`).

Trois précautions, parce qu'un vérificateur défaillant est pire qu'absent :

* des marqueurs de sortie (`SEAMTECH_PWSH_ANALYSE_OK` /
  `SEAMTECH_PWSH_ERREUR_SYNTAXE`) distinguent « le script a des erreurs » de
  « le contrôle n'a pas tourné ». Une faute de frappe dans la commande de
  vérification ne fait donc pas échouer la CI sur un workflow sain ;
* quand l'interpréteur est absent du poste — pas de `pwsh` dans le banc — le
  bloc n'est PAS compté comme bon : il est signalé par un avertissement
  visible à chaque exécution, et le test échoue si plus aucun bloc n'est
  analysable (plancher : 20) ;
* `tests/test_ci_executable.py` prouve ces trois comportements avec des
  analyseurs factices, sans dépendre d'un binaire absent du banc : un bloc
  cassé échoue, un bloc illisible est signalé, zéro bloc analysable échoue.

Où les blocs PowerShell sont-ils réellement vérifiés ? Le runner `ubuntu-24.04`
embarque PowerShell 7.6.9 (image `actions/runner-images`,
`images/ubuntu/Ubuntu2404-Readme.md`, section « PowerShell Tools »). La CI
les parse donc pour de vrai ; le banc local les signale comme non vérifiés
plutôt que de prétendre les avoir lus.

### 21. Qualification Windows : PostgreSQL 16 et Redis 7.4.11 tiennent

Trois runs pour y arriver, et chacun a appris quelque chose de précis :

1. `41358977874` — `choco install redis-64` : `redis-server.exe` introuvable
   sous `C:\ProgramData\chocolatey\lib\redis-64`.
2. `41360147830` — recherche élargie (shim PATH, `lib\redis-64`,
   `C:\Program Files\Redis`) : toujours introuvable. **Le paquet ne livre pas
   de serveur exploitable sur ce runner.** Fin de cette piste.
3. `41362919313` — Redis 7 installé depuis `redis-windows/redis-windows`,
   version épinglée **7.4.11**, reconstruite depuis les sources officielles.

Preuves publiées en annotation (seul canal lisible quand l'hôte des journaux
est injoignable) :

```
windows-identite  : os=Microsoft Windows Server 2025 Datacenter version=10.0.26100
redis-install     : version=7.4.11 binaire=C:\redis7\Redis-7.4.11-Windows-x64-msys2\redis-server.exe
redis-demarrage   : PONG obtenu, version=Redis server v=7.4.11 sha=00000000:0 malloc=libc bits=64
```

PostgreSQL 16 (choco, hôte) démarre et la base est créée — les services
conteneurs de GitHub ne fonctionnent pas sur Windows, d'où l'installation sur
l'hôte.

**Dit franchement sur Redis 7** : il n'existe plus de binaire Windows OFFICIEL
depuis Redis 5 (port `MicrosoftArchive` abandonné). La ligne Redis 7 est
obtenue par un port tiers reconstruit depuis les sources de Redis, épinglé en
version et en URL dans le workflow. Ce n'est pas un binaire signé par Redis
Labs ; c'est la version exigée par le plan, sur Windows, avec la source dite.

État au moment d'écrire ces lignes : la suite sans service échoue encore sur
Windows (§22).

### 22. Windows, suite sans service : rouge, et la lisibilité du rouge

L'étape 10 tourne 3 min 41 s (elle exécute donc bien la suite) puis échoue,
sans la moindre annotation. Deux runs dans cet état. Le diagnostic a été
repris à trois endroits (commits `41766a0`, `1f20083`) :

* `2>&1 | Tee-Object` → `> fichier 2>&1`. Dans un pipeline PowerShell, la
  sortie d'erreur d'un exécutable natif devient des `ErrorRecords`, et
  l'avertissement le plus anodin de pytest suffisait à tuer l'étape AVANT les
  lignes qui publient le diagnostic. La redirection évite le pipeline.
* les 60 dernières lignes du journal sont réaffichées, donc présentes dans la
  sortie de l'étape ;
* une fonction `Publier()` échappe `%`, CR et LF et tronçonne à 3 000
  caractères — une annotation non conforme est ignorée SILENCIEUSEMENT par
  GitHub, ce qui donne une étape rouge sans rien à lire ;
* un message est publié au DÉBUT de chaque suite : témoin de bon déroulement.
  S'il manque, l'étape est morte avant d'agir.

Ce point reste OUVERT : les échecs de la suite sur Windows ne sont pas encore
connus. Ils seront corrigés, fichier par fichier, dès que le canal de
diagnostic les rendra lisibles — et les tests qui ne peuvent pas exister sur
Windows (SIGKILL, `fork()`) sont déjà écartés NOMMÉMENT dans le workflow :
`test_chaos.py`, `test_worker_process.py`, `test_worker_process_postgres.py`,
`test_worker_crash_terminal.py`, `test_securite_banc_suivi.py`,
`test_migration_rollback.py`, `test_partage_web_worker.py`.

### 23. Provenance OCR : le compte rendu attribuait à Tesseract le travail de RapidOCR (`297aa97`)

L'annotation CI du job `ocr` affichait :

```
taux mots retrouvés propre=1.000 … moteur=rapidocr version=tesseract 5.3.4
```

RapidOCR avait fait la reconnaissance ; le rapport nommait la version de
Tesseract. Cause : `ocriser_page` lisait `moteur.version` AVANT la première
reconnaissance. RapidOCR est paresseux — son esclave ne démarre qu'au premier
appel — donc sa version valait `None` à cet instant ; l'agrégat se rabattait
alors sur `_tesseract_version()`, et le compte rendu mélangeait deux moteurs.

Corrigé : la version est celle que le moteur déclare EN RÉPONSE (lue après
coup), l'agrégat ne se rabat plus sur celle d'un moteur qui n'a pas tourné, et
le « moteur principal » est réellement le plus fréquent au lieu du premier.

Pourquoi ce n'est pas cosmétique : la provenance d'un texte extrait est une
preuve. Après une cote reconnue de travers, la question est « quel moteur,
quelle version ».

`tests/test_ocr_provenance.py` (2 tests) prouve le défaut ROUGE→VERT avec un
moteur factice paresseux et une version Tesseract concurrente :

```
sans correctif : AssertionError: le compte rendu attribue la version d'un
                 moteur qui n'a pas tourné : 'tesseract 5.3.4-faux'
                 (attendu 'rapidocr 9.9.9-faux')
avec correctif : 2 passed
```

Qualité OCR mesurée en CI (job `ocr`, moteur élu RapidOCR, modèles groupés
dans la roue — aucun téléchargement de modèle) :

```
ocr-moteur          : demande=auto rapidocr_installable=True elu=MoteurRapidOCR
ocr-qualite         : taux mots retrouvés propre=1.000 pages=1 océrisées=1 duree_s=1.85
ocr-qualite-degrade : taux mots retrouvés degrade=1.000 pages=1 océrisées=1 duree_s=1.72
ocr-debit           : debit mesure 33.79 pages/min
```

### 24. Accessibilité : 77 écrans scannés, 0 violation (`9ae3d3d`)

`frontend/e2e/accessibilite.spec.ts` existait et **n'était exécutée nulle
part** : le job `e2e` énumère ses specs à la main, et celle-ci n'y figurait
pas. Ajoutée au job `e2e`, avec garde-fou (plancher de 8 écrans scannés, zéro
écran en violation) et artefact `rapport-accessibilite-axe`.

Résultat mesuré (run `41359948630`, job `e2e`, commit `9ae3d3d`) :

```
accessibilite : 77 écrans scannés, 0 violation WCAG 2.1 A/AA
```

### 25. Windows, run `41368359949` : ce que le runner a dit, enfin

Après l'ajout des témoins, le job parle. Convervation des chiffres :

```
windows-identite     : os=Microsoft Windows Server 2025 Datacenter version=10.0.26100
pgvector-install     : version=0.8.7 (port Windows, pg16 x64)
redis-install        : version=7.4.11 binaire=C:\redis7\Redis-7.4.11-Windows-x64-msys2\redis-server.exe
redis-demarrage      : PONG obtenu, version=Redis server v=7.4.11 …
windows-postgres-redis : rc=1 ; 110 failed, 320 passed, 3 skipped, 1 xfailed,
                         122 errors in 980 s
Ligne de commande et chemins Windows : success
```

Les services Windows tiennent donc : PostgreSQL 16 + pgvector 0.8.7 + Redis
7.4.11 installés sur l'hôte, base créée, PONG obtenu, CLI fonctionnel sur un
chemin accentué (`Dépôt d'atelier 2024`).

**Diagnostic de l'étape « sans service », qui mourait sans rien dire** : les
cinq seuls fichiers de la sélection à manipuler des processus ont été exécutés
un par un — quatre rendent la main (`rc=1`, échecs de tests), un passe
(`test_relais_file_base`, `rc=0`). Aucun ne tue l'étape. Reste une piste, non
élucidée : GitHub plafonne les annotations par étape (dix publiées, la onzième
perdue, constaté sur « diag-fin »), et une ligne de journal commençant par
`::` consomme ce quota. Correction appliquée : témoins réduits, lignes
préfixées.

### 26. Trois défauts de PRODUIT révélés par Windows (pas des défauts de CI)

**a) `import fcntl` en tête de module rendait l'application non importable.**
`seamtech_search/ml/routes.py` importait `fcntl` au niveau du module. Quatre
fichiers de tests échouaient sur `ModuleNotFoundError: No module named
'fcntl'` — mais le défaut n'était pas dans les tests : sur Windows, l'import
du module échoue, donc l'application entière meurt à l'import au lieu de
refuser une seule route. Corrigé (`404f180`) comme le faisaient déjà
`sauvegarde.py` et `fiches/televersement_lot.py` : import gardé, `fcntl=None`
si absent, usage refusé explicitement (503 pour l'entraînement ML, qui ne peut
pas être protégé contre un lancement concurrent).

Trois tests le prouvent, ROUGE→VERT vérifié en réintroduisant le défaut :

* un interpréteur séparé où `import fcntl` est rendu impossible importe le
  module sans erreur ;
* aucun module du produit n'importe `fcntl`/`pwd`/`grp`/`termios` hors garde
  (filet statique, il couvre aussi les modules qu'aucune sonde n'importe) ;
* sans verrou, la route refuse en 503 ; avec, elle passe.

**b) Le confinement des fichiers est POSIX par construction.** `scan_folder`,
le cœur de l'import d'un dossier, s'appuie sur l'ancrage d'un répertoire par
descripteur (`dir_fd`), `O_NOFOLLOW`, `/proc/self/fd` et `fcntl.flock`. Ces
primitives n'ont aucun équivalent Windows : il n'existe pas de « même chose en
plus lent », il n'existe rien. Conséquence, mesurée : `OSError: Secure folder
scanning is not supported on this platform` sur `test_verification_integrite`,
`test_snapshot_import_pg`, `test_depot_apres_import` ; et `SauvegardeError:
Verrouillage sûr indisponible sur cette plateforme` sur
`test_sauvegarde_snapshot_pg`.

Ce n'est pas un contournement qu'on repousse : c'est une propriété du design.
Le produit refuse plutôt que de traverser des liens à l'aveugle, et il a
raison. **La conséquence pour le déploiement est tranchée : sur poste Windows,
l'application doit tourner dans Docker avec des conteneurs Linux** — ce que le
dépôt sait déjà faire (`docker-compose`). Le parcours prioritaire (glisser un
gros dossier) y est intact : les chemins sont ceux du conteneur, POSIX.

**c) Le refus était anonyme.** `seamtech_search/plateforme.py` (commit
`3a8d922`) rend la limite énumérable (`capacites()`), opposable (`exiger()`
nomme la capacité, sa conséquence, la plateforme, et dit qu'aucun réglage ne
la contourne) et testable (la branche « primitives absentes » est prouvée en
forçant `os.name`). Cinq sites de refus disséminés y sont désormais rattachés.

Suite complète locale après ces deux changements :

```
3149 passed, 48 skipped, 8 xfailed, 7 warnings in 920.06s (0:15:20)
```

aucun échec, aucune erreur.

### 27. État du point P1 « Windows » — ce qui reste

Reste ouvert, et dit comme tel :

1. **Suite sans service** : échec non encore attribué (l'étape rend la main
   sans diagnostic ; le plafond d'annotations est la piste en cours).
2. **Suite PostgreSQL + Redis** : 110 échecs et 122 erreurs, dont une part
   devrait disparaître avec le correctif `fcntl` (`404f180`) et le
   `redis-server` réel ; le reste est attendu sur les primitives POSIX
   (§26b) et devra être soit adapté, soit écarté NOMMÉMENT avec sa raison —
   jamais sauté en silence.
3. **Chrome et Edge** : non qualifiés. Playwright tourne sur Linux dans le job
   `e2e` ; le runner Windows n'a pas été configuré pour l'interface, faute
   d'avoir terminé les suites d'abord.
4. **Docker Desktop** : non qualifiable sur un runner GitHub Windows, qui ne
   fournit pas de conteneurs Linux. C'est pourtant LA cible de déploiement
   Windows (§26b) : la preuve devra être faite autrement, ou être dite comme
   limite de moyens.

Ce qui est déjà prouvé sur Windows, noir sur blanc : PostgreSQL 16 + pgvector
+ Redis 7 démarrent et répondent, la base est créée, le CLI accepte un chemin
accentué avec espaces, et 320 tests PostgreSQL passent.

### 28. CI 100 % verte au commit `00f7c2b` — état mesuré, job par job

Run `41372172470`, workflow `CI`, HEAD `00f7c2b` : **tous les jobs au vert**.

```
success  backend (3.11)   success  backend (3.12)   success  backend (3.13)
success  e2e              success  frontend         success  sauvegarde
success  integration      success  ocr              success  docker
success  hors-ligne-reel  success  recette-locale   success  recette-corpus-reel
success  securite-dependances
```

Cette CI porte, ensemble : les modules `contrib` reconstruits côté banc (§19),
le garde-fou des blocs `run:` désormais shell-aware (§20), la provenance OCR
(§23), l'audit axe-core ajouté au job `e2e` (§24), la plateforme énumérable
(§26c) et le correctif `fcntl` (§26a).

Mesures publiées par le job `backend (3.12)` :

```
coverage         : overall=88.15% floor=85.0%
coverage-details : inventaire=92.6% etat=77.2% pipeline=75.7% cli=81.1%
                   api=91.8% indexer=95.7%
accessibilite    : 77 écrans scannés, 0 violation WCAG 2.1 A/AA
ocr-qualite      : taux mots retrouvés propre=1.000 degrade=1.000
```

Les autres workflows du commit `00f7c2b` sont verts également : Endurance,
Qualification native, Qualification reserves. Perf comparaison était encore en
cours au moment d'écrire ces lignes.

**Seul le workflow « Qualification Windows » reste rouge** (§27), et il est le
seul endroit où le rouge est encore instructif plutôt que bloquant : il n'a
rien cassé d'autre, il révèle.

### 29. Prochaine action, sans ambiguïté

1. Lire le verdict du workflow Windows sur `6e63407` : l'exécution et le
   verdict sont désormais deux étapes, donc même une exécution interrompue
   laisse un diagnostic. Il dira enfin pourquoi la suite sans service meurt.
2. Reporter les 110 échecs / 122 erreurs de la suite PostgreSQL + Redis sur
   Windows après `404f180` (correctif `fcntl`) et `d3f1437` (`redis-server`
   réel) : une part doit avoir disparu, le reste est attendu sur les
   primitives POSIX (§26b).
3. Pour chaque test resté rouge : soit l'adapter à Windows (équivalent
   explicite), soit l'écarter NOMMÉMENT dans le workflow avec sa raison.
   Jamais un saut silencieux.
4. Puis seulement : reprendre le point P0 « sauvegarde / restauration / hors
   site / rétention » avec les chiffres de ce tour (campagne ciblée : 357
   passés) et rédiger le rapport final PRÊT / NON PRÊT.

## 2026-11-02 — Windows : deux limites levées, écarts opposables, banc sans `contrib`

### 30. Le verrou de sauvegarde ne refusait pas « par sécurité », il refusait tout

`VerrouSauvegarde` verrouillait trois descripteurs avec `flock` : le parent, le
dossier, puis `sauvegarde.lock`. Windows n'a ni `flock` ni verrou de répertoire.
La conséquence n'était pas une dégradation : c'était

```
SauvegardeError: Verrouillage sûr indisponible sur cette plateforme
```

sur **toute** sauvegarde. Le point P0 « sauvegarde / restauration » était donc
inexécutable sur le poste de l'atelier, et une sauvegarde qui refuse n'est pas
une sauvegarde. Deux tests Windows (`test_sha_snapshot_propage_aux_copies_
locale_et_objet_pg[False]` et `[True]`) en étaient la trace visible.

**Correctif (`f59dfc2`)** : sur les plateformes sans `flock`, le verrou porte
sur deux **fichiers** verrouillés par plage d'octets (`msvcrt.locking`), dans le
même ordre que sur POSIX :

1. `.sauvegarde-parent.lock`, posé **à côté** du dossier de sauvegarde — c'est
   le parent, verrouillé le premier, qui empêche le scénario du dossier
   REMPLACÉ : un renommage ne doit pas ouvrir la porte à un second détenteur ;
2. `sauvegarde.lock`, dans le dossier — le même nom qu'avant, donc la
   coordination avec les anciennes versions est conservée.

Libération en ordre inverse, puis fermeture des descripteurs. Les errnos
EACCES / EDEADLK / EWOULDBLOCK sont traduits en « une sauvegarde est déjà en
cours — chevauchement refusé », la même phrase des deux côtés : une opératrice
qui lit « impossible d'acquérir » ne sait pas si elle doit attendre, tuer un
processus ou changer de dossier.

**Ce que POSIX ne voit pas** : le chemin POSIX est inchangé, et un test le
prouve — `tests/test_verrou_sauvegarde_plateforme.py::test_sur_posix_le_fichier_
de_verrou_du_parent_n_est_jamais_cree` vérifie que le fichier du parent
n'apparaît jamais sur Linux.

**Ce qui est prouvé, et comment** : la logique du chemin Windows est exécutée
sur un banc POSIX avec un `msvcrt` double (ordre des verrous, ordre inverse de
libération, refus nommé au chevauchement, refus nommé sur erreur inattendue,
fermeture de tous les descripteurs) — 5 tests. Forcer un double n'est pas
Windows : c'est la seule façon honnête d'exécuter cette branche avant d'avoir
un poste Windows. La preuve plateau viendra du workflow « Qualification
Windows ».

### 31. Redis de test : un refus qui privait Windows de deux preuves

`serveur_redis_isole` levait `RuntimeError: Isolation Redis par socket Unix non
qualifiée sous Windows`, ce qui mettait deux tests en échec — perte de claim
Redis pendant la promotion, et pendant une transaction. Le message ressemblait
à une panne de produit ; c'était une limite de banc.

**Correctif** : là où le socket Unix n'existe pas, l'instance dédiée est lancée
sur un port TCP inoccupé, lié à `127.0.0.1`, dans un processus qu'on détruit
nous-mêmes. POSIX garde le socket Unix. Un port TCP n'isole pas aussi bien
qu'un objet du système de fichiers : c'est écrit dans le test, pas sous-entendu.

La branche Windows est **exécutée pour de vrai** sur le banc Linux via
`serveur_redis_isole(nom_os="nt")` : le serveur démarre, répond, et le port est
rendu à la sortie (`test_le_chemin_windows_demarre_vraiment_un_serveur_prive`,
7 tests au total). Le saut conditionnel de `test_qualification_redis7_refuse_
serveur_ancienne_version` a été retiré : cette preuve n'a rien de spécifique à
POSIX.

### 32. Les échecs Windows sont désormais OPPOSABLES, plus seulement visibles

Deux mécaniques, dans `ci/verdict-windows.ps1` et `ci/ecarts-windows.txt` :

* **l'inventaire COMPLET** : GitHub n'affiche que dix annotations par étape. Une
  suite à 73 échecs ne se lisait donc qu'aux dix premiers — le reste était
  invisible depuis le banc. Les identifiants sont publiés par paquets de 25.
* **l'opposabilité** : chaque échec est comparé à `ci/ecarts-windows.txt`
  (format `motif|raison`). Un échec **non nommé fait échouer le job**. Un test
  écarté doit être écrit quelque part avec sa raison ; sans cela, un périmètre
  et une dissimulation se ressemblent.
* **la mort localisée** : quand l'étape est interrompue (aucun code de sortie
  écrit — vu au run `41375766036`), la fin du journal est publiée. Les deux
  suites passent en `-v --tb=line` : la dernière ligne nomme le dernier test
  démarré. « Ça meurt » sans « où ça meurt » ne se répare pas.

Six écarts sont nommés à ce stade, tous de la même famille — le confinement
d'un dossier ancré par descripteur (`dir_fd` + `O_NOFOLLOW`), inexistant sous
Windows (§26b) — avec la contrepartie : refus NOMMÉ par
`seamtech_search/plateforme.py`, déploiement Windows dans Docker avec des
conteneurs Linux, et couverture intégrale de ces mêmes tests par la CI Linux.

Trois échecs restent **non élucidés** et sont instruits par une étape de
diagnostic ciblée qui publie leur trace : deux rejeux téléversés
(`TestRejeuSansDoublon`) et `test_corrections_pg_concurrentes_refusees_avant_
mutation`. Ils passent sur Linux : soit ils révèlent un vrai défaut produit
propre à Windows, soit une différence de banc. Ils ne seront pas nommés avant
d'avoir été lus.

### 33. Deux tests plus exigeants que le produit (`8cd77e6`)

Le banc local utilise le paquet `pgserver`, qui ne fournit pas
`postgresql-contrib` : ni `unaccent`, ni `pg_trgm`. L'application **dégrade
proprement** dans ce cas (`indexer._ensure_unaccent_config`, repli documenté
vers la configuration `simple`), mais deux tests, eux, n'admettaient aucun
repli :

* `tests/test_migration_rollback.py` écrivait `to_tsvector('seamtech_unaccent')`
  en dur → `UndefinedObject` sur tout PostgreSQL sans contrib, pour une raison
  étrangère à la migration qu'il doit vérifier. Il emploie désormais la
  configuration que la base a réellement résolu
  (`index.config_recherche_texte`). **4 passés** (4 erreurs avant).
* `tests/test_sauvegarde_references.py` cherchait « GENOIS » et attendait
  « Génois ». L'écart est maintenant **nommé** (`parite_accents_active` +
  skipif explicite) au lieu d'un échec qu'on attribuait de mémoire à la limite
  du banc. La contrepartie qui interdit de s'en contenter :
  `tests/test_indexer_accent_parity.py`, rouge si une CI complète empruntait le
  chemin dégradé.

**Mesures du banc local, PostgreSQL sans contrib** : sauvegarde 94 passés / 3
nommés / 1 xfail ; restauration et purge 107 passés / 4 xfail ;
migration/rollback 4 passés ; multipart en flux 8 passés ; verrou de plateforme
et Redis 26 passés. Zéro rouge.

### 34. P2 « multipart en flux » — huit tests enfin exécutés

Le fichier `tests/test_multipart_flux.py` avait été écrit sans interpréteur
disponible (`.venv` perdu avec la réinitialisation du banc) : il n'avait
**jamais tourné**. Exécuté et vert — **8 passés en 6,68 s** — coupure client au
milieu du flux (aucun brouillon résiduel), refus 413/413 sans brouillon,
concurrence à deux envois, envoi normal borné, profondeur d'arborescence 1/8/32.

### 35. Prochaine action, sans ambiguïté

1. Lire le verdict du workflow Windows sur `f59dfc2` : inventaire complet des
   échecs, écarts nommés ou non, et la trace des trois échecs non élucidés.
2. Adapter ou nommer chaque échec restant ; un échec non nommé doit rester
   rouge jusqu'à décision écrite.
3. Relancer la suite complète locale et publier le bilan chiffré.

### 36. Suite complète locale : 3 024 passés, 37 échecs, attribution prouvée un par un

Commande (banc PostgreSQL sans `contrib`, Redis 7.4.7, S3rver) :

```
SEAMTECH_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55498/postgres \
SEAMTECH_TEST_REDIS_URL=redis://127.0.0.1:6390/1 \
SEAMTECH_TEST_REDIS_SERVER=/tmp/redis-banc/redis-7.4.7/src/redis-server \
SEAMTECH_TEST_S3RVER_URL=http://127.0.0.1:14569 \
.venv/bin/python -m pytest -q -rf -p no:randomly \
  -m "not perf and not endurance and not recette_corpus and not ocr_suite \
      and not integration_docker and not minio_historique"
```

Résultat : **37 échecs, 3 024 passés, 23 sauts, 8 xfail, 12 min 49 s**.

Ces 37 échecs ne sont PAS restés une impression : chaque test a été relancé
seul, en demandant si sa sortie mentionnait `unaccent` ou `pg_trgm`. Verdict :
**39 sur 39** (les 37 du lot, plus deux que l'exécution fichier par fichier fait
apparaître). Tous viennent de ce que le paquet `pgserver` ne fournit pas
`postgresql-contrib` : l'application dégrade alors proprement (repli vers la
configuration `simple`, trigrammes indisponibles), et les tests de parité des
accents et de tolérance aux fautes — qui exigent justement ces extensions —
échouent. Aucun n'est lié aux modifications de ce tour.

Ce qui interdit de s'en contenter, et qui est en place :

* `tests/test_indexer_accent_parity.py` reste **bruyant** sur une CI complète :
  il échoue si une base qui PEUT installer `unaccent` ne l'a pas fait ;
* la CI `backend (3.11/3.12/3.13)` installe PostgreSQL avec `contrib` et était
  verte au commit `00f7c2b` (§28), ces tests compris.

Limite dite franchement : ce banc ne peut pas prouver la parité des accents ni
la tolérance aux fautes. La preuve est portée par la CI, pas par lui.

### 37. Un VRAI défaut produit trouvé par Windows : le rejeu n'y était pas reconnu

Le runner Windows (run `41420706781`) a publié, pour la première fois, la trace
complète des échecs qu'on lui demandait. Deux tests de rejeu téléversé
attendaient `deja_traite` et obtenaient :

```
AssertionError: {'statut': 'traite', 'fiche': '7792-SO', 'raison': 'remplacee', …}
WARNING seamtech_search.fiches.persistance — Fiche id 1 REMPLACÉE sur place en
        révision 2 : tout écran resté sur la révision 1 sera refusé (409)
```

Ce n'était ni une panne de banc ni une limite de plateforme : **c'était un
défaut produit, invisible sur Linux**.

La clé d'idempotence stocke `os.path.normcase(chemin) + "|" + empreinte`. Sur
Windows, `normcase` met en minuscules ; sur POSIX, il ne fait rien. La
reconnaissance du rejeu comparait ensuite ce chemin stocké, plié, avec le
chemin logique du fichier qu'on vient de recevoir, resté dans sa casse
d'origine : `ANNEE/AFFAIRE/fiche.pdf` contre `annee/affaire/fiche.pdf`. Égaux
sur Linux, différents sur Windows — donc le rejeu n'y était PAS reconnu, et le
second envoi du même dossier REMPLAÇAIT la fiche (révision 2) au lieu d'être
sans effet, alors que l'écran promet « rejouer un dossier ne crée rien ».

**Correctif** (`1b6e4a9`) : normalisation dans `_chemin_logique_televersement`,
point unique d'où sortent tous les chemins logiques comparés (rejeu, pièces
jointes, liens objets). Les deux côtés de chaque comparaison y passent ; POSIX
ne change pas d'un caractère.

**Preuve** : un test force `os.path.normcase` à se comporter comme sur Windows
(minuscules, séparateur inversé) puis rejoue un envoi en deux sessions. **ROUGE
avant** (révision 2, même symptôme que le runner), **VERT après**. Forcer une
primitive ne prouve pas Windows : cela prouve que la comparaison ne dépend plus
de la casse — et le runner l'avait constaté en échec, ce qui vaut mieux qu'une
intuition.

Conséquence pour l'atelier, dite telle quelle : sans ce correctif, chaque
renvoi d'un dossier déjà déposé créait une révision de trop sur le poste
Windows, et les écrans restés sur la révision précédente étaient refusés en 409.

### 38. Les écarts Windows sont désormais PROUVÉS, plus seulement écrits

Méthode, reproductible : exécuter la suite POSIX avec les primitives déclarées
absentes — c'est-à-dire exactement ce que Windows est pour ce produit. Les
tests qui échouent alors avec l'`OSError` nommé de
`seamtech_search/plateforme.py` sont ceux qu'aucune adaptation de banc ne peut
faire passer ; les autres relèvent d'une autre cause et **ne sont pas nommés**.

Sur les 71 échecs du run `41420706781`, **19** sont expliqués ainsi et figurent
dans `ci/ecarts-windows.txt` avec leur raison. Les 52 autres restent NON NOMMÉS
: aucune cause n'a été établie, et nommer une ignorance en ferait un périmètre.
Le verdict publie maintenant leurs causes regroupées par famille ; elles
seront, une fois lues, corrigées ou nommées.

Le fichier d'écarts est gardé par `tests/test_ecarts_windows.py` : chaque motif
doit nommer un fichier et un test qui EXISTENT, chaque raison doit être une
phrase opposable (≥ 40 caractères), aucun doublon. Il a rejeté deux lignes «
idem » du fichier qu'il garde — son office, rempli dès le premier run.

Aussi corrigé au passage : `test_corrections_pg_concurrentes_refusees_avant_
mutation` propage désormais l'exception du thread au lieu d'un `assert False`
sans cause, et `PYTHONFAULTHANDLER=1` est posé pour qu'une mort native du
processus laisse une pile lisible plutôt qu'un silence (la suite sans service
meurt encore sans code de sortie, pendant `tests/test_sauvegarde_unites.py`).

### 39. Mesures du tour

| Campagne | Résultat |
|---|---|
| Suite complète locale (banc sans `contrib`) | 3 024 passés, 37 échecs tous attribués (39/39 relancés seuls : `unaccent` / `pg_trgm`) |
| Sauvegarde (marqueur) | 94 passés, 3 écarts nommés, 1 xfail |
| Restauration, bundle, confinement, purge | 107 passés, 4 xfail |
| Migration / rollback | 4 passés (4 erreurs avant le correctif du banc) |
| Multipart en flux (P2) | 8 passés |
| Verrou de plateforme + Redis | 26 passés |
| Dépôt transactionnel + après-import | 71 passés, 2 sauts |
| CI `f59dfc2` | tous les jobs verts sauf `e2e` (un test flaky, corrigé) |

### 40. Prochaine action, sans ambiguïté

1. Lire les causes publiées par le run Windows en cours (familles + exemples) :
   corriger ce qui est un défaut produit, nommer ce qui est une limite, avec
   raison écrite. Rien ne doit être nommé sans cause lue.
2. Élucider la mort de la suite sans service (le journal dit maintenant où ;
   `PYTHONFAULTHANDLER` dira pourquoi).
3. Une fois le workflow Windows vert : rédiger le rapport final PRÊT / NON
   PRÊT point par point, avec commandes, résultats, SHA et limites.

### 41. P1 « volume » — mesure réelle à 10 000 fiches, sur le commit courant

Le point « volume annuel » restait au stade des bornes (`MAX_FICHIERS_PAR_
SESSION` = 100 000, 20 Gio par session) sans mesure d'ensemble. Le dépôt
possède l'instrument : `scripts/scale_bench.py`, 10 000 fiches SYNTHÉTIQUES,
50 mesures par scénario, PostgreSQL jetable — jamais de données réelles.

Exécuté sur le banc local, arbre au commit `328afa4` :

```
SCALE_BENCH_FICHES=10000 SCALE_BENCH_SAMPLES=50 \
SEAMTECH_SCALE_DATABASE_URL=postgresql://postgres@127.0.0.1:55498/seamtech_scale \
PYTHONPATH=. .venv/bin/python scripts/scale_bench.py
```

| Scénario | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) |
|---|---:|---:|---:|---:|
| mot_simple | 93,93 | 106,13 | 116,39 | 116,39 |
| multi_mots | 132,64 | 151,91 | 163,35 | 163,35 |
| code | 20,52 | 22,56 | 24,47 | 24,47 |
| facette_dimension | 132,17 | 150,72 | 151,81 | 151,81 |
| filtres | 176,81 | **211,68** | 224,80 | 224,80 |
| suggestions | 105,58 | 161,52 | 187,84 | 187,84 |

La cible que porte ce banc lui-même (`target_p95_ms_indicatif`) est **250 ms** :
elle est respectée partout. La cible produit de 100 ms appartient au job `perf`,
sur son propre corpus, et n'est PAS revendiquée à cette échelle — à 10 000
fiches, plusieurs scénarios la dépassent, ici comme sur le runner CI.

Ce que cette mesure ne prouve pas, dit d'avance : corpus synthétique (aucun
PDF réel, aucun réseau, aucun MinIO/R2, aucun navigateur), et banc local SANS
`postgresql-contrib` — donc sans index `pg_trgm`, c'est-à-dire un **pire cas**
par rapport à une installation complète. Le même banc, exécuté en CI sur le
commit `0ab735c`, donnait p95 de 134 à 199 ms selon les scénarios : les deux
mesures ne sont pas comparables terme à terme (index et charge différents), et
c'est pourquoi elles sont publiées toutes les deux.

### 42. Ce que le run `41427629667` a appris — cinq familles de causes

Le verdict publie désormais les causes regroupées. Sur 71 échecs, 49 restaient
hors périmètre ; les voici par famille, avec ce qu'on en fait :

| # | Cause (telle que lue) | Décision |
|---|---|---|
| 5 | `OSError: Capacité de plateforme absente : scan_ancre_descripteur` | **nommée** (§32, §38) : confinement POSIX par descripteur, prouvé par simulation |
| 4 | `SauvegardeError: binaire « pg_dump » introuvable (PATH, SEAMTECH_PG_BINDIR)` | **banc, pas produit** : Chocolatey ne met pas `pg_dump` dans le PATH. Le workflow pose `SEAMTECH_PG_BINDIR` et l'ajoute au PATH, et échoue fort si `pg_dump.exe` est absent — une installation muette ne doit pas se transformer en 7 échecs de restauration |
| 2 | `AssertionError: document introuvable : dossier-client/fiche-A.txt parmi ['c:\users\runner…']` | **test, pas produit** : la base stocke le séparateur de la plateforme. `tests/reconstruire_aide.id_document` normalise désormais avec `os.path.normpath` (identité sur POSIX), ainsi que l'assertion de `test_rebuild_index.py` |
| 3 | `AssertionError: Regex pattern did not match` (3 tests de restauration) | **à instruire** : un `SauvegardeError` est bien levé, mais pas celui attendu. Cibles ajoutées au diagnostic |
| 1 | `TypeError: 'NoneType' object is not subscriptable` (liaison capture, courses) | **à instruire** : cibles ajoutées au diagnostic |

Le diagnostic ciblé ne porte plus sur les trois échecs dont la cause est
connue et corrigée (rejeu ×2, verrou de mutation), mais sur six tests choisis
parmi les familles 1 et 3, dont on lira la trace complète au prochain run.

Mesure locale de contrôle après les correctifs de ce tour : suite complète
**3 031 passés, 37 échecs** — les 37 étant, comme au §36, tous attribués un par
un à l'absence de `unaccent` / `pg_trgm` sur ce banc (ni plus, ni moins).

### 43. Prochaine action, sans ambiguïté

1. Lire les traces des six cibles du diagnostic ciblé (run en cours) : corriger
   ce qui est un défaut produit, nommer ce qui est une limite, jamais plus.
2. Élucider la mort de la suite sans service : le journal dit maintenant OÙ
   (`fin du journal`), `PYTHONFAULTHANDLER` dira POURQUOI. Si la cause est une
   limite de ressources du runner, découper la suite en paquets plutôt que
   perdre une exécution entière.
3. Puis : rapport final PRÊT / NON PRÊT point par point.

### 44. CI `5b56591` — tous les jobs verts, `e2e` compris

Run `41433224924`, HEAD `5b56591` :

```
success backend (3.11)   success backend (3.13)   success ocr
success e2e              success frontend         success sauvegarde
success integration      success docker           success recette-locale
success recette-corpus-reel   success hors-ligne-reel   success securite-dependances
(backend 3.12 encore en cours au moment d'écrire ces lignes)
```

`e2e` est revenu au vert : le budget du test « liens croisés voiles » (§33 du
tour précédent) n'était pas une indulgence, c'était la correction d'un budget
par test inadapté à huit navigations sous charge CI. Aucune assertion n'a été
affaiblie — chaque attente garde sa borne.

Mesures publiées par `backend (3.11)` :

```
coverage         : overall=88.15%  floor=85.0%
coverage-details : inventaire=92.6% etat=77.2% pipeline=75.7% cli=81.1%
                   api=91.5% indexer=95.7%
suite-postgres   : 530 passés, 0 saut
```

Seul le workflow « Qualification Windows » reste rouge, et le rouge y est
aujourd'hui instructif : il attribue une cause à chaque famille d'échecs (§42),
ce qui a déjà fait corriger un défaut produit (§37), deux défauts de banc
(pg_dump, séparateur de chemin) et un défaut de test.

### 45. État courant, dit sans détour

**PRÊT** : tout ce que la CI Linux peut prouver, à ce commit, est vert —
backend 3.11/3.13, e2e, frontend, ocr, sauvegarde, integration, docker,
recette-corpus-reel, recette-locale, hors-ligne-reel, securite-dependances ;
plus Endurance, Qualification native, Qualification reserves, Perf comparaison.

**NON PRÊT** : la qualification Windows. Deux familles d'échecs y restent sans
cause (§42, familles 1 et 3) et la suite sans service y meurt encore sans code
de sortie. Tant que ces trois points ne sont pas clos, le déploiement sur le
poste Windows de l'atelier ne peut pas être déclaré prêt — et comme le
confinement des fichiers est POSIX par construction, la cible de déploiement
Windows reste Docker avec des conteneurs Linux.

**Prochaine action** : lire les six traces du diagnostic ciblé et la fin du
journal de la suite sans service sur le run en cours, corriger ou nommer, puis
rédiger le rapport final.

### 46. Le mystère de la suite sans service est levé, et il change tout

Le run `41433225057` a enfin publié la FIN du journal. La suite sans service ne
mourait pas : **elle allait au bout**, puis un `KeyboardInterrupt` survenait
pendant la finalisation — après le résumé :

```
FAILED … - AssertionError: Regex pattern did not match.
  Expected regex: 'taille'
  Actual message: 'Lecture confinée du bundle indisponible sur cette plateforme'
!!!!! KeyboardInterrupt !!!!!
C:\hostedtoolcache\windows\Python\3.12.10\x64\Lib\threading.py:355: KeyboardInterrupt
= 314 failed, 1607 passed, 56 skipped, 857 deselected, 1 xfailed, 16 errors in 380.53s =
```

Deux conséquences, toutes deux utiles :

1. **Le code de sortie n'était pas écrit** parce que l'interruption tuait
   l'hôte PowerShell avant `Set-Content`. Désormais c'est `cmd /V /C` qui
   l'écrit lui-même (`& echo !errorlevel!> logs\rc-….txt`) : même si la console
   reçoit l'interruption, le verdict a son code de sortie et son inventaire.
2. **Les 314 échecs ont une seule cause** : « Lecture confinée du bundle
   indisponible sur cette plateforme » — c'est-à-dire la même famille que
   `scan_ancre_descripteur` mais prononcée à l'endroit de la lecture du bundle
   de sauvegarde (`procfs_fd` + descripteur ancré). Ce n'était pas 314
   mystères : c'était une cause, 314 fois.

D'où un troisième mécanisme, faute duquel la liste des écarts serait devenue un
annuaire : **l'écart par CAUSE**. Une ligne `CAUSE|<message prononcé par le
produit>|<raison>` accepte tout échec dont le message est celui-là. Le message
doit nommer une capacité absente, et le garde-fou l'exige (≥ 25 caractères) :
un écart par cause rédigé « erreur » engloutirait tout le rouge, il est refusé.

Quatre causes sont nommées : lecture confinée du bundle, scan ancré par
descripteur, `procfs_fd`, `verrou_flock`. Contrepartie inchangée : refus NOMMÉ
par `plateforme.py`, déploiement Windows dans des conteneurs Linux, et
couverture intégrale de ces tests par la CI Linux à chaque commit.

### 47. Prochaine action, sans ambiguïté

1. Relire le verdict Windows sur le run en cours : avec les écarts par cause,
   les familles nommées doivent disparaître du décompte « hors périmètre ».
2. Ce qui restera rouge sera alors, pour de bon, la liste courte des échecs à
   instruire — plus une brume de 300 lignes.
3. Puis : rapport final PRÊT / NON PRÊT, point par point, commandes et SHA.

## 48. Tableau de bord PRÊT / NON PRÊT (point par point, preuves à l'appui)

Cette section est le brouillon du rapport final. Chaque ligne porte : ce qui
est demandé, ce qui est prouvé, la commande qui le prouve, et ce qui reste.

### P0 — sauvegarde, restauration, hors-site, rétention, migration

| Exigence | État | Preuve |
|---|---|---|
| Sauvegarde PostgreSQL + `./data` + objet, un seul `sauver` à la fois | **PRÊT** | `tests/test_sauvegarde_complete.py`, `test_sauvegarde_unites.py` (verrou parent/dossier/fichier) ; 94 passés / 3 écarts nommés |
| Verrou opposable : « une sauvegarde est déjà en cours », pas « impossible » | **PRÊT** | `tests/test_verrou_sauvegarde_plateforme.py` (5 tests) ; errnos EACCES/EDEADLK/EWOULDBLOCK traduits |
| Verrou sur plateforme sans `flock` (Windows) | **PRÊT** | même fichier : `msvcrt.locking`, ordre parent→dossier, libération inverse ; POSIX inchangé (test de frontière) |
| Restauration : dump vérifié avant toute écriture, base partielle signalée | **PRÊT** | `test_restauration_dump_stable_pg.py` (7 tests), `test_restauration_confinement.py` |
| Restauration des fichiers locaux et des objets | **PRÊT** | `test_sauvegarde_references.py`, `test_restauration_bundle_s3rver.py` |
| Hors site | **PRÊT** | `test_sauvegarde_hors_site_integree.py`, `test_sauvegarde_manifeste_distant.py` |
| Rétention / purge locale et worker | **PRÊT** | `test_purge_locale.py`, `test_purge_worker.py` (politique : destination configurée, aucun artefact sans clé ni non vérifié) |
| Migration et retour arrière sur copie représentative | **PRÊT** | `test_migration_rollback.py` : 4 passés (montée, retour arrière répété, refus base existante, dump de sécurité absent) |
| Restauration COORDONNÉE (base + fichiers + objets en un seul geste) | **PARTIEL** | les primitives existent et sont testées séparément ; l'orchestration unique reste à éprouver en situation réelle |

### P1 — OCR, rotations, plateformes, volume

| Exigence | État | Preuve |
|---|---|---|
| OCR exact en CI, sans assertion affaiblie ni réessai salvateur | **PRÊT** | job `ocr` vert sur `5b56591` ; provenance OCR corrigée (§23) |
| Rotations PDF 90/180/270 avec tableaux et coordonnées, `xfail` retirés | **PRÊT** | `test_pdf_orientation.py`, `test_pdf_orientations_mixtes_tables.py` : 95 passés ; campagne §10-§24 |
| PostgreSQL 16 + pgvector + Redis 7 réels | **PRÊT** | CI Linux (services conteneurs) et runner Windows (installation hôte) |
| Chrome / Edge | **NON QUALIFIÉ** | Playwright tourne sur Linux dans `e2e` ; le runner Windows n'a pas été configuré pour l'interface. Limite de moyens, dite |
| Docker Desktop | **NON QUALIFIABLE** | un runner GitHub Windows ne fournit pas de conteneurs Linux. C'est pourtant la cible de déploiement Windows : la preuve devra être faite sur le poste réel, jamais en premier |
| Volume annuel | **MESURÉ, PARTIEL** | `scripts/scale_bench.py` : 10 000 fiches, p95 ≤ 211,68 ms pour une cible indicative de 250 ms (§41). Corpus synthétique : ni PDF réels, ni réseau, ni stockage objet |
| Windows natif | **NON PRÊT** | ~300 tests exigent le confinement POSIX par descripteur ; refus NOMMÉ, déploiement cible = Docker Linux |

### P2 — matrice, multipart, pannes, sécurité, accessibilité, endurance

| Exigence | État | Preuve |
|---|---|---|
| Matrice route × rôle × session × état métier | **PRÊT** | `test_matrice_roles_routes.py` (énumération automatique des routes, six identités, aucun 500), `test_matrice_routes_auth.py`, `test_passe_finale_sessions.py`, `test_bff_multipart_borne.py` |
| Multipart en flux : charge, interruption, concurrence | **PRÊT** | `tests/test_multipart_flux.py` : 8 passés — coupure réelle du corps à 40 %, refus 413 sans brouillon, deux envois concurrents, envoi borné, profondeur 1/8/32 |
| Injections de pannes | **PRÊT** | `test_chaos.py`, `test_worker_crash_terminal.py`, endurance avec panne injectée |
| Sécurité offensive | **PRÊT** | `test_securite_offensive.py` : 8 passés ; traversée neutralisée prouvée par sonde hors test |
| Accessibilité (axe-core étendu) | **PRÊT** | 77 écrans scannés, 0 violation WCAG 2.1 A/AA (`e2e`) |
| Endurance | **PARTIEL** | `test_endurance.py` : fuites de staging prouvées à 25 et 300 cycles avec panne tous les 7 ; « plusieurs jours » d'horloge NON exécutés |

### Compétences ECC appliquées, nommément

`search-first` (chercher avant d'écrire : chaque comportement existant a été
localisé avant modification), `tdd-workflow` (rouge → vert : le test de casse
Windows a été écrit rouge avant le correctif), `verification-loop` (chaque
correctif rejoué, jamais cru), `security-review` (traversée, en-têtes de rôle,
refus nommés), `postgres-patterns`, `redis-patterns`, `error-handling`
(messages opposables : capacité, conséquence, plateforme), `architecture-
decision-records` (les décisions de plateforme sont écrites ici, pas implicites).

## 49. Un flux qui ne s'arrête pas, 232 écarts démontrés, P0 bouclé, Chrome/Edge

### 49.1 Le défaut le plus grave du tour : une boucle infinie, pas un échec

En rejouant la suite Windows **sans les primitives POSIX**
(`ci/plugin_simulation_sans_primitives.py`), un test ne rendait plus la main :

    tests/test_url_presignee_302.py::test_artefact_presignature_indisponible_sert_le_repli_sans_exception

Le diagnostic (thread de vidage mémoire armé à 20 s, jamais déclenché : le GIL
était retenu) a mené à `seamtech_search/api.py`, fonction `_reponse_objet_s3` :

```python
def _blocs() -> Iterator[bytes]:
    yield from iter(lambda: corps.read(1024 * 1024), b"")
```

`iter(appelable, sentinelle)` s'arrête quand l'appelable rend **exactement**
`b""`. Un corps distant qui rend `None`, une chaîne, un objet — ou des octets
sans jamais s'épuiser — fait tourner la boucle **sans fin**, en allouant
jusqu'à l'épuisement de la mémoire. Mesuré : l'hôte de test tué (rc=137), sans
message, sans échec de test, sans fin. Ce n'était ni Windows ni POSIX : c'était
la confiance accordée à `read()`.

**Correctif** — nouveau module `seamtech_search/flux_bornes.py` :
`lire_par_blocs(flux, taille, limite_octets=…, nom=…)` refuse un bloc qui
n'est pas des octets, applique une borne, et refuse une taille de bloc nulle ou
négative (elle réintroduirait la boucle). Les **dix** idiomes
`iter(lambda…)` du produit passent par lui ; la route API impose une borne
(taille annoncée par le stockage, sinon la taille maximale de pièce).

**Preuve** — `tests/test_flux_bornes.py`, 11 tests. Le corps aberrant
n'attend pas : il lève `AssertionError` au bout de quatre lectures, donc le
test **échoue** au lieu de bloquer la suite. Sur le commit `a338f21`, le
fichier qui ne rendait jamais la main s'exécute en **4,94 s** (5 échecs
attribuables, 15 passés) au lieu de ne jamais finir.

### 49.2 Les écarts Windows ne sont plus une opinion : 232 démontrés un par un

Méthode, reproductible par n'importe qui :

1. exécuter la suite POSIX avec **toutes** les primitives déclarées absentes
   (`PYTHONPATH=.:ci python -m pytest -p plugin_simulation_sans_primitives …`) ;
2. retirer les tests qui échouent **aussi** sans la simulation (4 d'entre eux :
   `pg_trgm` absent du banc local, pas une limite Windows) ;
3. les 232 restants échouent **parce que** les primitives manquent.

Chaque ligne de `ci/ecarts-windows.txt` porte le constat mesuré : le message
nomme la capacité (`scan_ancre_descripteur`, `verrou_flock`, `procfs_fd`) ou
montre le refus de l'opération (`assert 409 == 200`). Le garde-fou
(`tests/test_ecarts_windows.py`, 6 tests) accepte désormais les tests
paramétrés.

**Limite dite, non contournée** : la simulation dit ce que le test **exige**,
pas le détail de l'échec observé sur Windows. C'est pour le lever que le verdict
publie les causes par familles (§49.4).

### 49.3 P0 — la restauration coordonnée passe par la COMMANDE

`tests/test_sauvegarde_cli_coordonnee.py`, 2 tests :

* **deux processus réels** : `sauver --sans-s3 --data`, destruction de
  l'arborescence, puis `restaurer --coordonnee --data-cible`. Le manifeste est
  écrit par le premier processus et **relu sur le disque** par le second —
  plus rien n'est partagé en mémoire ;
* **le périmètre complet** : `--avec-references` puis
  `restaurer --coordonnee --restaurer-objets` (base + données + fichiers
  référencés + objets). Le manifeste ne devient « protection globale prouvée »
  qu'après le contrôle final.

La ligne « restauration coordonnée » du tableau de bord passe donc de
**PARTIEL** à **PRÊT** : la fonction était testée, la commande ne l'était pas.

### 49.4 Le verdict Windows publie les causes par tranches (plafond de dix)

GitHub limite à **dix annotations par étape**. Au run `41439011809`,
l'inventaire prenait toute la place et les causes disparaissaient.
`ci/verdict-windows.ps1` gagne un paramètre `-Tranche` : la tranche 1 publie
sept familles + deux paquets d'inventaire, les tranches suivantes huit familles
chacune ; le workflow ajoute cinq étapes (sans service) et trois (PG + Redis).
La cause publiée est la **dernière ligne `E`** de la trace (`--tb=short`), pas
le message de la ligne `FAILED` : « Regex pattern did not match. » ne dit rien
de ce qui manque.

### 49.5 Chrome et Edge, enfin exécutés

Nouveau workflow `qualification-navigateurs-windows.yml` : runner
`windows-latest`, **Chrome et Edge réels** (`channel: "chrome"`,
`channel: "msedge"`). `frontend/e2e/navigateurs-windows.spec.ts` vérifie
l'agent-utilisateur (sinon un vert ne voudrait rien dire), la connexion par
session, l'accessibilité axe-core WCAG 2.1 A/AA des écrans de lecture, et
l'absence d'erreur de console. `pnpm exec tsc --noEmit` passe.
Reste non qualifié **et dit** : l'import d'un dossier depuis Windows, que le
produit refuse faute de primitives POSIX ; la cible de déploiement Windows
reste un conteneur Linux.

## 50. Causes 1 et 2 élucidées, et pourquoi le verdict se trompait

### 50.1 Un test paramétré ne se reconnaît JAMAIS dans les écarts

Run `41454897907` : 232 écarts écrits, et pourtant **127 échecs** annoncés
« hors périmètre connu ». La comparaison du verdict était :

```powershell
if ($echec -like "$motif*") { $connu = $true }
```

`-like` interprète `[` et `]` comme une **classe de caractères**. Un test
paramétré — `test_plages_invalides_refusees_en_416[bytes=-0]` — ne pouvait donc
jamais correspondre à son propre écart, alors qu'il y était écrit noir sur
blanc. Correction : comparaison **exacte** d'abord (`-eq`), et le motif
générique n'est plus accepté que lorsqu'il ne contient aucun métacaractère
(sinon il pourrait couvrir un autre test).

Contrôle fait ici, sur les 120 identifiants Windows connus : avec la bonne
sémantique, **73 couverts, 47 non couverts** — les 47 restants sont les vraies
questions, pas un artefact de comparaison.

### 50.2 La vraie cause n'est pas dans le journal : elle est dans le rapport JUnit

Deuxième leçon du même run : la lecture du journal ne peut pas fonctionner.
Avec `-v`, pytest marque les échecs en **fin** de ligne de progression
(`tests/x.py::test FAILED`), et les lignes `E` de la trace ne sont pas
attenantes au résumé court. Résultat publié : « 314 échecs ; CAUSE: (aucune
ligne E) ».

`ci/resumer_echecs.py` lit donc le **`--junitxml`** de pytest : la cause est la
dernière ligne `E` **utile** (les lignes « `+ where 409 = …` » que pytest ajoute
pour décortiquer une assertion sont écartées — elles expliquent, elles ne sont
pas la cause). Vérifié en local :

```
5|assert 409 == 200|tests/test_url_presignee_302.py::test_open_…
4|OSError: Capacité de plateforme absente : verrou_flock — …|tests/test_api_extra_coverage.py::…
2|ValueError: objet … : bloc invalide (MagicMock au lieu d'octets)|tests/test_url_presignee_302.py::…
```

Le script écrit aussi un fichier de **détail** (`identifiant|cause`) : le
rattachement par en-tête de traceback ne vaut pas pour un test en classe, dont
l'en-tête ne porte pas le chemin du fichier.

### 50.3 Causes 1 et 2 : la clé d'index du test n'était pas celle du produit

Les cibles `diag-cible-1` et `diag-cible-2` du run `41454455187` ont livré les
deux mystères ouverts depuis plusieurs runs :

* cause 1 — `TypeError: 'NoneType' object is not subscriptable` :
  `path_key` est stocké en `os.path.normcase(str(path.resolve()))`, donc **tout
  en minuscules** sur Windows ; les tests cherchaient `WHERE path_key = str(piece)`
  avec le chemin brut → aucune ligne → `_document()` rend `None`.
* cause 2 — `document introuvable : dossier-client/fiche-A.txt` : même origine,
  comparaison de suffixe sensible à la casse alors que la base porte
  `dossier-client\fiche-a.txt`.

Correction : les aides de test utilisent la clé du **produit**
(`normcase` + `normpath` + `resolve`). Ce n'est pas une tolérance ajoutée — sur
un système insensible à la casse les deux écritures désignent le même fichier —
et sur POSIX `normcase` est l'identité (19 + 14 tests passent, inchangés).

Reste la cause 3, « Lecture confinée du bundle indisponible sur cette
plateforme » : ce n'est pas un défaut, c'est la limite de plateforme nommée par
`seamtech_search/plateforme.py`.

## 51. 02/11 (suite) — Chrome et Edge verts : la violation de `/fiches` nommée, mesurée, corrigée

**Run 41497594641** (commit `d037bcf`) a enfin nommé ce que le run précédent
cachait : `/fiches`, règle `color-contrast`, gravité « serious », cibles
`span[data-testid="nav-groupe-explorer|atelier|pilotage"]`, dans **les deux**
navigateurs. Le message d'échec porte désormais la règle, la gravité et les
cibles (commit `d037bcf`) : une violation qu'on ne peut pas nommer ne se
répare pas.

**Mesure hors navigateur** (composition alpha sRGB + formule WCAG, fait à la
main, vérifiable) :

| classe | thème jour | thème nuit | token plein (jour / nuit) |
|---|---|---|---|
| `text-muted-foreground/80` (`components/nav.tsx`) | **3,52:1** | 5,79:1 | 5,30 / 8,49 |
| `text-primary/80` (`components/index-status.tsx`) | **3,46:1** | 6,37:1 | 5,04 / 9,51 |

Deux défauts réels sous le seuil AA de 4,5:1. Le second n'était signalé par
personne : son élément n'apparaît que si le backend sert un jeu d'exemple.
Correctif : l'opacité est retirée — le token plein passe dans les deux thèmes.
La hiérarchie visuelle est portée par la casse, le graisse et l'interlettrage,
pas par une division du contraste.

**Trois trous fermés** (commit `6f2355d`) :

1. `frontend/lib/contraste.test.ts` — 15 tests verts avec le relais multipart,
   déjà câblés en CI (le job `frontend` exécute
   `node --experimental-strip-types --test lib/*.test.ts`). Il mesure la
   SOURCE : couples canoniques de la palette (jour et nuit) et **chaque**
   classe `text-<token>/<opacité>` écrite dans `app/` et `components/`. Là où
   axe ne voit qu'un écran dans un état, ce test voit toutes les couleurs. Il
   reproduit exactement les deux défauts ci-dessus avant correction. Les
   exemptions (décoratifs `aria-hidden` du panneau de connexion) sont écrites
   avec leur motif et vérifiées vivantes ; les fonds locaux (plage ambre à 10 %
   du bandeau de doublon) sont déclarés et composés.
2. `/fiches` ajouté à la liste d'écrans de l'audit Linux : c'était un écran NON
   SCANNÉ, donc « non vérifié », pas « conforme ». 15 écrans désormais.
3. Animations **figées** avant l'analyse dans la qualification navigateurs,
   comme l'audit Linux le fait déjà et comme axe le recommande : une mesure
   prise pendant la révélation (opacité 0 → 1) lit des couleurs mélangées au
   fond et fabrique de faux échecs. Le message d'échec publie les couleurs et
   le rapport MESURÉS par axe.

**Run 41499874660** (commit `6f2355d`) : `verdict-navigateurs attendus=2
passes=4 echecs=0`, `rc=0`. Chrome **156.0.8078.80** et Edge
**156.0.4314.56** (agent-utilisateur `Edg/153.0.8010.12`), cinq écrans chacun,
**0 violation** WCAG 2.1 A/AA. Chrome et Edge sont donc qualifiés.

Restent publiés sans être assertés : 5 erreurs de console, dont des
`Failed to load resource: 503`. Un 503 sans URL ne se diagnostique pas : la
spec publie désormais le couple **statut + chemin** (`RESEAU <navigateur> :: …`),
filtré du bruit `_next`/favicon. La prochaine exécution dira quelle route les
sert — ce sera vérifié, pas supposé.

## 52. Le refus d'OCR hors POSIX est un contrat, plus un mystère

Dix-huit échecs Windows sans message (`assert None is not None`,
`assert 0 == 1`). La cause est dans le produit et elle est **voulue** :
`_rendre_page_pdf_borne` et `_extraire_images_pdf_borne` rendent `None` / `[]`
dès que `os.name != "posix"`, parce que le confinement du rendu
(sous-processus borné, `/proc`) n'existe que sur POSIX.

`tests/test_ocr_refus_non_posix.py` (4 tests) en fait un contrat : hors POSIX
le produit refuse, ne lance **aucun** sous-processus (un espion qui lèverait),
ne laisse **aucun** fichier, et **nomme** la plateforme (`os.name=nt`,
« ne se contourne pas par un réglage »). Un garde-fou vérifie que la garde ne
mord pas sur POSIX — sinon un refus silencieux désactiverait l'OCR en
production Linux. Le motif du refus nomme la plateforme dans
`seamtech_search/ocr/pipeline.py`. Douze identifiants entrent dans
`ci/ecarts-windows.txt` avec cette raison ; 281 tests OCR locaux passent.

Piège rencontré : patcher `os.name` **globalement** fait instancier
`WindowsPath` à `pathlib` et effondre pytest en plein rendu d'échec
(`NotImplementedError`). La bascule est donc locale au module testé
(module `os` factice délégant tout sauf `name`).

**Causes qui ne se reconnaissaient pas** : Windows écrit
`<module 'os' (frozen)> has no attribute 'killpg'`, alors que l'écart cherchait
`module 'os' has no attribute 'killpg'` — le test correspondant était donc
compté comme inexpliqué. Raccourci au suffixe stable (`has no attribute
'killpg'`, idem pour `geteuid`).

**Annotations tronquées** : 34 identifiants publiés sur 128.
`PublierErreursParPaquets` remplit désormais des seaux de 2800 caractères —
chaque échec inattendu est publié **avec la cause lue dans le rapport JUnit**.
La prochaine exécution nommera les 128 au lieu d'en échantillonner 34.

## 53. La suite Windows s'arrête en route : `rc=2`, et pourquoi ça change les comptes

**Run 41501814193** (commit `4257bea`) : `rc=2`, **313 échecs, 1622 passés,
442 s** — contre `rc=1`, 392 échecs, 1913 passés, 561 s au run `41467111570`
(`48e8be2`). La suite ne va pas au bout : ~380 tests sur 2 369 ne tournent pas.
Idem au run `41499874854` (`6f2355d`) : `rc=2`, 311 échecs, 1621 passés.

Conséquence, et elle compte : **les compteurs ne sont plus comparables**.
Dire « on est passés de 392 à 313 échecs » serait faux — on a surtout arrêté
de compter à mi-parcours. D'où, dans `ci/verdict-windows.ps1` : un `rc=2`
publie « **suite INTERROMPUE (rc=2)** » avec la FIN du journal (c'est elle qui
dit où ça s'arrête) et sort en échec, au lieu d'additionner des chiffres qui
ne se comparent pas.

`rc=2` = session interrompue : typiquement un `KeyboardInterrupt` échappé d'un
test qui simule un Ctrl+C. Le candidat le plus probable se déduit de
l'inventaire : aucun échec n'est publié pour un fichier situé APRÈS
`tests/test_sauvegarde_unites.py` dans l'ordre alphabétique — donc l'arrêt a
lieu autour de `tests/test_scan_safety.py`, qui contient justement
`test_interruption_clavier_scan_annule_et_indique_echec`. C'est une déduction
sur l'inventaire, **pas une preuve** : la preuve viendra de la fin de journal
que publie désormais le verdict. À vérifier au prochain run.

**Les 84 échecs hors périmètre sont publiés EN ENTIER** (avant : 34 sur 128,
plafond d'annotation). Répartition par fichier :

| fichier | identifiants |
|---|---|
| `test_sauvegarde_unites.py` | ~24 |
| `test_preflight_archive.py` | ~16 |
| `test_restauration_confinement.py` | ~15 |
| `test_sauvegarde_manifeste_distant.py` | ~4 |
| `test_sauvegarde_references.py` | ~3 |
| `test_ocr_page_integralite.py`, `test_lot10_televersement_lot.py`, `test_rebuild_index.py`, `test_detection_fiches.py`, `test_inventaire_archive.py`, `test_liaison_catalogue_capture.py`, `test_passe_finale_ingestion_dossiers.py` | 1 à 3 chacun |

**Ils portent TOUS la mention « aucune cause dans le rapport »** : le rapport
JUnit ne dit pas tout (et il est sans doute tronqué par l'interruption). Le
rattachement par les lignes « E » du journal n'était qu'un REPLI utilisé faute
de fichier de détails ; il devient un **complément**, appliqué aux seuls tests
qui n'ont encore aucune cause. Limite écrite dans le code : l'en-tête de
traceback ne porte pas le chemin du fichier pour un test en classe, donc ce
rattachement reste partiel.

Prochaine action, précise : lire, au premier run qui publie la fin de journal,
le nom du test où la suite s'arrête ; le corriger ou l'écarter nominativement
; relancer pour retrouver une suite COMPLÈTE et des comptes comparables.
Tant que la suite est interrompue, aucun décompte d'écarts Windows n'a de
valeur.

### 53 bis. Run 41508594754 — l'arrêt est déterministe, pas dû au temps

`rc=2`, **313 échecs, 1622 passés, 697 s**. Deux runs consécutifs
(41501814193 : 442 s ; 41508594754 : 697 s) donnent les **mêmes comptes** avec
des durées très différentes : l'arrêt est donc DÉTERMINISTE, il ne vient pas
d'un délai dépassé. La référence complète (run 41467111570, `48e8be2`) comptait
392 échecs et 1913 passés : **environ 380 tests ne tournent pas**.

La branche « suite INTERROMPUE » a rempli son office (rc=2 publié, sortie en
échec), mais la fin de journal publiée ne disait rien : les dernières lignes du
journal sont des lignes vides. Le verdict cherche désormais la dernière ligne
de **progression** (`tests/x.py::test_y PASSED|FAILED|ERROR`) : c'est elle qui
nomme l'arrêt (commit `f10622b`).

Hypothèse à vérifier au prochain run, et non une conclusion : le point d'arrêt
se situerait autour de `tests/test_scan_safety.py` — premier fichier, dans
l'ordre alphabétique, dont aucun échec n'est publié — fichier qui contient
justement `test_interruption_clavier_scan_annule_et_indique_echec`. Rien n'a
été trouvé qui appelle `pytest.exit` ou pose `shouldstop` (ni dans les tests,
ni dans `tests/conftest.py`, ni dans la configuration) ; aucun `--maxfail`
n'est passé. La preuve viendra du journal.

**Prochaine action, précise** : lire l'annotation « suite INTERROMPUE (rc=2) »
du prochain run de qualification Windows, y relever « DERNIER TEST : … », et
traiter ce test (le corriger s'il laisse échapper un `KeyboardInterrupt`, ou
l'écarter nominativement). Ensuite seulement relancer pour retrouver une suite
complète et des comptes comparables.

## 54. Le point d'arrêt est nommé, et la fouille a trouvé un vrai défaut

**Le point d'arrêt est nommé.** Run `41515990228` : la branche
« suite INTERROMPUE » du verdict publie

```
DERNIER TEST : tests/test_scan_safety.py::test_scan_snapshot_restores_documents_after_failure PASSED [ 84%]
```

L'arrêt a donc lieu dans le test **suivant** du même fichier, et les fichiers
suivants ne tournent pas : **379 tests** s'y trouvent (comptés), ce qui
correspond exactement aux ~378 manquants. Le mécanisme reste inconnu — aucune
piste (`pytest.exit`, `shouldstop`, `--maxfail`) n'a rien donné, et le produit
ne lève rien de visible. Le workflow rejoue désormais **ce fichier seul**
(commit `a0f5efd`) : une minute, et la bannière d'interruption de pytest
apparaît avec sa trace — ou, si le fichier passe seul, la preuve que l'arrêt
vient d'un cumul d'état.

**La fouille a trouvé un vrai défaut, indépendant de l'arrêt** (commit
`1abd6b2`) :

`os.kill(pid, 0)` est l'idiome POSIX pour demander « ce PID vit-il encore ? » —
le signal 0 n'est pas délivré, l'appel ne sert qu'à sonder. **Sur Windows,
`os.kill` ne connaît pas le signal 0 : il appelle `TerminateProcess` avec la
valeur reçue comme code de sortie.** Un test de vie y devient une mise à mort,
et comme les PID sont recyclés, le processus tué n'est pas forcément celui
dont on a lu l'identifiant dans le fichier de verrou.

L'idiome était employé à deux endroits : `indexer.scan_lock` (verrou de scan
SQLite — « reprendre un scan après un crash » pouvait tuer un processus vivant)
et `ocr.etat.VerrouOCR._pid_vivant` (verrou de la nuit OCR).

Correction : `plateforme.pid_vivant` interroge sans jamais tuer. POSIX garde
l'idiome (sémantique inchangée, `PermissionError` = vivant) ; Windows passe par
`ctypes` (`OpenProcess` + `GetExitCodeProcess`, distinction accès refusé /
paramètre invalide) ; plateforme inconnue → « vivant », car un verrou périmé
reste un verrou (faux positif bénin) alors que supprimer le verrou d'un
processus vivant ne l'est pas.

`tests/test_pid_vivant.py` (6 tests) verrouille le contrat, et d'abord
l'essentiel : sur la branche non POSIX, **`os.kill` n'est jamais appelé**
(l'espion lève). Réintroduire l'idiome fait donc échouer le test au lieu de
tuer un processus. Deux tests d'intégration rejouent le scénario réel du
verrou de scan : verrou tenu par un processus vivant (refus propre, processus
épargné) et verrou périmé (repris, pas supprimé d'autorité). 110 tests locaux
passent (scan_safety, plateforme, verrou, indexer, OCR).

## 55. La suite Windows tourne de nouveau ENTIÈREMENT — et c'est le correctif `pid_vivant` qui l'a rendue

**Run 41523086709** (commit `a0f5efd`) :

```
rc=1 ; 393 failed, 1920 passed, 66 skipped, 859 deselected, 2 xfailed,
20 warnings, 16 errors in 814.80s
```

Plus d'interruption. La suite va au bout, et ses chiffres rejoignent ceux de la
dernière exécution complète (run 41467111570 : 392 échecs, 1913 passés).

**Le seul changement de produit entre la dernière suite interrompue
(41515990228, `22c1846`) et celle-ci est `1abd6b2`** — le remplacement de
`os.kill(pid, 0)` par `plateforme.pid_vivant`. Quatre exécutions consécutives
interrompues avant (41499874854, 41501814193, 41508594754, 41515990228), une
exécution complète juste après, sans autre modification de code : le lien est
établi par la répétition, même si le mécanisme exact reste à démontrer — sur
Windows, `os.kill(pid, 0)` appelle `TerminateProcess(handle, 0)`, et le
processus ainsi tué n'était pas forcément celui dont on avait lu le PID dans le
fichier de verrou (recyclage). Ce qui est certain, c'est qu'un test de vie ne
doit pas tuer, et qu'il ne tue plus.

**Ce que la suite complète dit maintenant** : 393 échecs, **94 hors périmètre
connu** (299 nommés, soit 76 %). Ventilation des 94 :

| fichier | identifiants |
|---|---|
| `test_sauvegarde_unites.py` | 24 |
| `test_preflight_archive.py` | 21 |
| `test_restauration_confinement.py` | 20 |
| `test_url_presignee_302.py` | 6 |
| `test_sauvegarde_manifeste_distant.py` | 5 |
| `test_sauvegarde_references.py` | 4 |
| `test_ocr_page_integralite.py` | 3 |
| `test_lot10_televersement_lot.py` | 2 |
| `test_scan_safety.py` | 2 |
| `test_detection_fiches.py`, `test_inventaire_archive.py`, `test_liaison_catalogue_capture.py` | 1 chacun |

74 des 94 ont désormais une famille publiée (`resumer_echecs.py`), par exemple
`assert 409 == 200` (6), `TypeError: 'NoneType' object is not subscriptable`
(2), `ValueError: … MagicMock au lieu d'octets` (2), `assert 6 == 0` (banc
shell). Les 20 restants portent « aucune cause dans le rapport ».

**Deux échecs de plus sont expliqués et corrigés** (commit `f541dad`) :
`test_crash_processus_annule_transaction_scan_sqlite` et
`test_interruption_clavier_scan_annule_et_indique_echec` interrogeaient l'index
avec `str(chemin.resolve())` alors que la base stocke la clé du produit
(`normcase(normpath(resolve()))`). Sur Windows la casse diffère, `fetchone()`
rend `None`, et l'échec devient un `TypeError` anonyme.

Reste : 94 échecs à nommer un par un, dont la moitié déjà regroupés par famille.
Aucun n'est maquillé en écart sans raison démontrée.

### 55 bis. 26 échecs de plus nommés, avec raison démontrée (commit `065d113`)

1. **`tests/test_preflight_archive.py`, 21 échecs** : ils portent la MÊME cause
   que les deux identifiants déjà nommés — « assert 6 == 0 » — parce que le
   préflight simule `tesseract` et `pdftoppm` par des scripts « `#!/bin/sh` »
   que Windows ne sait pas exécuter. C'est le BANC, pas le produit. Écart pris à
   l'échelle du **fichier** : énumérer 21 identifiants n'ajouterait rien, et en
   oublier un ferait croire à une régression.

2. **`tests/test_url_presignee_302.py`, 5 échecs** : démontrés sur le banc POSIX.
   Sous `ci/plugin_simulation_sans_primitives.py`, ces tests échouent avec
   **exactement** les mêmes assertions qu'à Windows (`assert 409 == 200`) : le
   confinement local étant refusé, la route retombe sur le stockage objet, dont
   le corps n'est pas des octets, et répond 409 au lieu de 200.

3. **`tests/test_scan_safety.py`, 2 échecs** (commit `f541dad`) : requête par le
   chemin brut au lieu de la clé du produit (`normcase`), d'où un `TypeError`
   anonyme.

**Restent 66 échecs sans raison démontrée**, surtout
`test_sauvegarde_unites.py` (~24) et `test_restauration_confinement.py` (~20) :
ces tests **passent** sous la simulation des primitives absentes, donc leur
échec Windows vient d'ailleurs. Leurs familles sont publiées
(« une sauvegarde est déjà en cours » ×6, « Lecture confinée du bundle
indisponible » ×7, `PermissionError` ×1, `assert False` ×1) mais la cause
première n'est pas établie : les nommer serait une dissimulation.

**Prochaine action, précise** : pour ces deux fichiers, exécuter sur Windows une
campagne ciblée qui publie, pour chacun, la PREMIÈRE ligne de trace complète
(`--tb=long`) et non seulement la dernière — c'est la piste qui reste. Le
diagnostic ciblé (étape « le fichier qui interrompt la suite ») a montré qu'un
fichier seul se rejoue en une minute : la même mécanique sert pour un fichier
qu'on veut comprendre.

## 56. Run 41530741118 (`065d113`) : 391 échecs, **66** hors périmètre connu

```
rc=1 ; 391 failed, 1922 passed, 66 skipped, 859 deselected, 2 xfailed,
20 warnings, 16 errors in 559.80s
```

Le compte d'échecs a baissé de 393 à 391 (les deux tests `test_scan_safety.py`
corrigés en `f541dad`) et, surtout, **les échecs sans raison démontrée sont
passés de 94 à 66** grâce aux deux familles nommées en `065d113` (préflight :
21 ; URL présignée : 5).

| fichier | restants |
|---|---|
| `test_sauvegarde_unites.py` | 24 |
| `test_restauration_confinement.py` | 20 |
| `test_sauvegarde_manifeste_distant.py` | 5 |
| `test_sauvegarde_references.py` | 4 |
| `test_ocr_page_integralite.py` | 3 |
| `test_lot10_televersement_lot.py` | 2 |
| `test_detection_fiches.py`, `test_inventaire_archive.py`, `test_liaison_catalogue_capture.py`, `test_passe_finale_ingestion_dossiers.py`, `test_rebuild_index.py`, `test_securite_banc_pg.py`, `test_televersement_creation_idempotente.py`, `test_url_presignee_302.py` | 1 chacun |

**325 échecs sur 391 (83 %) sont désormais nommés.**

Contre-vérification locale, faite après le changement `pid_vivant` (qui touche
le verrou de scan utilisé partout) : la sélection exacte de la suite « sans
service » tourne à **2 346 passés, 47 sautés, 3 xfail, 0 échec** en 3 min 42 —
aucune régression.

L'étape de diagnostic ciblé (commit `5178f1c`) rejoue SEULS, avec `--tb=long`,
les cinq fichiers qui concentrent les échecs inexpliqués et publie les lignes
« E » dédoublées : c'est la prochaine lecture, et c'est elle qui dira si ces 66
échecs relèvent d'une limite de plateforme (à nommer) ou d'un défaut (à
corriger).

## 57. Diagnostic Windows : un vrai défaut PRODUIT trouvé, et un outil de verdict qui ne savait plus relire

### 57.1 Le diagnostic devient un job à part (commit `d52c7dd`)

L'étape de diagnostic réussissait mais ne publiait rien de lisible (runs
`41523086709`, `41536354571`) : une exécution de vérification plafonne à
**50 annotations**, et dans le job principal les tranches de causes saturent
ce plafond. Le diagnostic est donc devenu un **job séparé**
(`.github/workflows/qualification-windows.yml`, job `diagnostic-causes`), avec
son propre budget d'annotations et son propre artefact. Il rejoue SEULS, avec
`--tb=long`, les quatre fichiers qui concentrent les échecs inexpliqués, puis
publie, par fichier : les familles de causes (`fam-<fichier>`), le détail
« identifiant|cause » par test (`det-<fichier>`), et les lignes « E »
dédoublées (`diag-<fichier>`).

### 57.2 UN DÉFAUT PRODUIT, trouvé par ce diagnostic (commit `74f03e8`)

`seamtech_search/sauvegarde.py` écrivait les chemins relatifs des manifestes
avec le **séparateur de l'OS qui les produit** :
`str(chemin.relative_to(racine))` → `CLIENT-A\fiche.pdf` sur Windows.
Restauré ailleurs (Linux), l'antislash n'est pas un séparateur :
`Path("data") / "CLIENT-A\fiche.pdf"` crée un fichier **dont le nom contient un
antislash**. Une sauvegarde illisible hors de la machine qui l'a produite est
l'inverse d'une sauvegarde hors site — précisément ce que le P0 demande.

Les quatre points d'écriture passent en `as_posix()` ; `pathlib` accepte « / »
sur les deux plateformes, la restauration est inchangée. Preuve :
`tests/test_sauvegarde_unites.py::test_inventaire_archive_chemins_portables`,
qui échoue sur Windows avant la correction. **Le fichier
`test_sauvegarde_unites.py` passe de 31 à 30 échecs sur Windows** (run
`41543899126`, job `diagnostic-causes`) : la correction est vérifiée sur la
plateforme, pas seulement en Linux.

Vérification locale complète : **2 347 passés, 47 sautés, 3 xfail, 0 échec**
(sélection exacte de la suite « sans service », 2 min 59).

### 57.3 Ce que le diagnostic dit des 66 restants

Les causes lues sur le runner, par fichier :

| cause | nombre | déjà écrite dans `ci/ecarts-windows.txt` ? |
|---|---|---|
| `Lecture confinée du bundle indisponible sur cette plateforme` (y compris sous la forme `Actual message: '…'`) | ~45 | **OUI** (cause n° 63) |
| `Restauration locale sûre indisponible sur cette plateforme` | 3 | **OUI** (cause n° 339) |
| `ModuleNotFoundError: No module named 'fcntl'` | 2 | **OUI** (cause n° 337) |
| `Failed: lien de verrou suivi` / `lien dur non autorisé` / `lien de dossier suivi` | ~3 | **OUI** (causes 345/347/349) |
| `une sauvegarde est déjà en cours — chevauchement refusé` | 6 | non |
| `PermissionError: [WinError 5] Access is denied` (renommer un dossier ouvert) | ~2 | non |
| `OSError: [WinError 10038] not a socket` | 1 | non |
| `ValueError: the environment variable is longer than 32767 characters` | ~1 | non |
| `FileNotFoundError`, `IndexError`, `assert False`, « Full diff: » | ~5 | non |

**La majorité des 66 échecs « sans cause » a donc une cause DÉJÀ ÉCRITE.**

### 57.4 Le vrai problème : le verdict ne savait plus RELIRE (commit `07f546b`)

Relu ligne à ligne, l'inventaire des 66 échecs non nommés du run
`41530741118` porte, pour **chacun**, la mention
« (aucune cause dans le rapport) » — alors que les familles publiées par la
même étape proviennent bien du rapport JUnit (`156 echecs ; CAUSE:
OSError: Capacité de plateforme absente : verrou_flock`, `38 echecs ; CAUSE:
SauvegardeError: Lecture confinée du bundle…`).

Le rapport est donc lu, les causes nommées sont dans le fichier d'écarts, et
pourtant **aucun identifiant d'échec ne se retrouve dans le fichier de
détail** : l'appariement est cassé entre les deux. Vérifié par ailleurs en
Linux : les chaînes s'apparient parfaitement en Python (`'Lecture confinée du
bundle indisponible sur cette plateforme'` est bien contenue dans
`"Actual message: 'Lecture confinée du bundle…'"`), ce n'est donc pas un
problème de texte ni d'accents.

Conséquence, dite franchement : **le chiffre « 66 échecs sans cause » ne
signifie pas « 66 échecs inexpliqués »**. Il signifie « 66 échecs que le
verdict n'a pas su relire ». Tant que ceci n'est pas tranché, aucun écart
supplémentaire ne doit être écrit sur la foi de ce compte.

Le verdict gagne un mode `-DebugAppariement` (commit `07f546b`) qui publie les
comptes — échecs du journal, entrées du détail, causes nommées, écarts
attendus —, trois exemples de clés du détail, et pour trois échecs du journal
si la clé est présente et avec quel texte. Une étape dédiée l'appelle sans
jamais prononcer de verdict.

**Prochaine action** : lire les annotations `windows-sans-service-debug-*` du
run de `07f546b`, réparer l'appariement, puis recompter. Le nombre d'échecs
réellement inexpliqués se situe, d'après le diagnostic, entre 10 et 15 — pas
66.

### 57.5 Deux obstacles de mise au point, et où en est la lecture

La mise au point de l'appariement (commit `7acfeb7`) s'est heurtée à deux
obstacles, l'un après l'autre, tous deux visibles dans les runs :

1. **run `41544601483`** : l'étape de mise au point, placée dans le job
   principal, a réussi et n'a rien publié — les cinquante annotations du job
   sont saturées par les tranches de causes. D'où son déplacement dans le job
   de diagnostic, qui a son propre budget (commit `7acfeb7`).
2. **run `41549514516`** : le script de verdict s'arrêtait avant d'atteindre le
   bloc de mise au point, parce que la garde « suite INTERROMPUE » se
   déclenchait — le journal du diagnostic est la concaténation de quatre
   journaux, ce qui ressemble à une suite interrompue. Corrigé : en mode
   `-DebugAppariement`, les gardes d'interruption publient mais ne coupent pas
   la parole (commit `5a46e4a`).
3. **run `41549920537`** : le verdict a publié `rc= ; echecs=85` — le code de
   sortie est vide, donc la lecture a échoué et le script s'est arrêté juste
   après les familles. Le fichier de code de sortie n'était pas retrouvé tel
   qu'écrit. Correctif en cours de vérification (commit à venir) : en mode
   mise au point, un fichier de code de sortie absent vaut 1 au lieu
   d'arrêter le script — partout ailleurs, il reste une anomalie bruyante.

**Ce qui est déjà établi, sans attendre** : les quatre-vingt-cinq échecs des
quatre fichiers rejoués se répartissent en causes DÉJÀ ÉCRITES pour
l'essentiel — « Lecture confinée du bundle indisponible sur cette plateforme »
(apparaissant aussi sous la forme `Actual message: '…'` quand le test attend
une expression régulière), « Restauration locale sûre indisponible sur cette
plateforme », `ModuleNotFoundError: No module named 'fcntl'`, et les trois
refus de liens (lien de verrou suivi, lien dur non autorisé, lien de dossier
suivi). Le reste tient en six familles non encore écrites : « une sauvegarde
est déjà en cours — chevauchement refusé » (6), `PermissionError: [WinError 5]`
sur un renommage de dossier ouvert (2), `OSError: [WinError 10038]` (1),
`ValueError: the environment variable is longer than 32767 characters` (1),
`FileNotFoundError` (1), `IndexError` + `assert False` + « Full diff: » (3).

**Prochaine action, très exactement** : lire les annotations
`diag-debug-appariement`, `diag-debug-ligne`, `diag-debug-cles` et
`diag-debug-cle` du premier run de qualification Windows postérieur au
correctif du code de sortie. Elles disent si les identifiants du journal se
retrouvent comme clés du fichier de détail. Si oui, réparer l'appariement et
recompter ; si non, écrire les six familles manquantes dans
`ci/ecarts-windows.txt` avec leur cause démontrée et recompter. Dans les deux
cas, le nombre d'échecs réellement inexpliqués est de l'ordre d'une dizaine,
pas de soixante-six.

## 58. LA CAUSE DES 66 ÉCHECS : le verdict n'a jamais relu le fichier de détail (commit `af56523`)

Trois obstacles d'outillage ont d'abord masqué la lecture — chacun d'eux est
une limite à dire dans le rapport final, parce qu'une étape « success » peut
n'avoir strictement rien dit :

1. **cinquante annotations par exécution de vérification** : une étape qui
   publie après d'autres perd ses annotations (runs 41523086709, 41536354571,
   41544601483) ;
2. **dix annotations par étape** : l'étape de mise au point appelait le
   verdict, qui publiait résumé + sept familles + deux inventaires — dix — et
   les annotations de mise au point passaient à la trappe (run 41550362469) ;
3. **les gardes du verdict** : suite jugée interrompue (`41549514516`), puis
   code de sortie illisible (`41549920537`) coupaient la parole avant le bloc
   de mise au point.

Ces trois obstacles levés, l'annotation `diag-debug-appariement` du run
**41550712445** a donné ceci :

```
fichier_detail=System.Collections.Hashtable ; existe=False ; lignes=0 ;
cles_chargees=1 ; echecs_journal=85 ; causes_nommees=11 ; attendus=284
```

**PowerShell ne distingue pas la casse des noms de variables.** Le script
déclarait `[string]$Details` (le CHEMIN du fichier de détail) et, plus loin,
`$details = @{}` (la table de bord « cause par test »). C'était la même
variable : l'affectation écrasait le chemin, `Test-Path` portait alors sur une
table de hachage, et le fichier `identifiant|cause` produit par
`ci/resumer_echecs.py` n'était **jamais lu**.

Conséquence, depuis l'introduction de l'option `-Details` : **chaque échec
n'était classé que par identifiant, jamais par cause**. Les 66 échecs annoncés
« sans cause » avaient leur cause écrite dans le rapport JUnit, dans
`ci/ecarts-windows.txt`, et publiée en clair dans les familles — mais le
verdict ne la relisait pas. Ce n'est pas « 66 échecs inexpliqués », c'est
« 66 échecs qu'un outil ne savait pas lire ».

Le paramètre s'appelle désormais `-FichierDetails` ; les dix appels du
workflow suivent ; un commentaire de garde-fou est écrit à l'endroit du
piège.

**Prochaine action** : relire le verdict du premier run de qualification
Windows postérieur à `af56523`. Le nombre d'échecs réellement non nommés doit
tomber de 66 à une quinzaine : les causes « Lecture confinée du bundle
indisponible sur cette plateforme », « Restauration locale sûre indisponible
sur cette plateforme », `ModuleNotFoundError: No module named 'fcntl'` et les
trois refus de liens sont déjà écrites. Puis écrire les six familles qui
restent, avec leur cause démontrée :

| famille | nombre | cause à établir |
|---|---|---|
| `une sauvegarde est déjà en cours — chevauchement refusé` | 6 | verrou : Windows ne permet pas de remplacer un fichier ouvert |
| `PermissionError: [WinError 5]` sur `backup -> backup-original` | 2 | renommage d'un dossier ouvert, refusé sur Windows |
| `OSError: [WinError 10038]` (« not a socket ») | 1 | à démontrer |
| `ValueError: environment variable longer than 32767 characters` | 1 | plafond Windows sur la longueur d'une variable d'environnement |
| `FileNotFoundError` (`…\out\1\piece`) | 1 | à démontrer |
| `IndexError`, `assert False`, « Full diff: » | 3 | à démontrer |

**Le point P1 « Windows natif » reste NON PRÊT** — ce n'est pas ce mesurage
qui le change : il repose sur l'absence des primitives POSIX, lue dans
`plateforme.py` et refusée explicitement par le produit.

### 58.1 Confirmation sur le runner (run 41551125074)

```
fichier_detail=logs/det-concat.txt ; existe=True ; lignes=86 ;
cles_chargees=167 ; echecs_journal=85 ; causes_nommees=11 ; attendus=284
...::test_adaptateur_exclusif_simule_refuse_une_cle_deja_presente ::
present_dans_details=True :: Actual message: 'Lecture confinée du bundle
indisponible sur cette plateforme'
```

Le fichier est lu, les clés sont présentes, les causes sont là. Le verdict va
donc classer par cause, et non plus seulement par identifiant.
