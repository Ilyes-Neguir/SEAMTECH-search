# Rapport de livraison — accès et consultation des fichiers

**Projet :** SEAMTECH Search

**Date :** 1 octobre 2026

**Branche :** `arena/01a0f848-seamtech-search`

## Synthèse

Les vues Recherche, Dossiers, Validation, Fiche et Fichiers disposent maintenant d’un accès aux documents par identifiant de catalogue. Les lectures passent par des routes authentifiées de session, avec prise en charge de `GET`, `HEAD`, `Range`/`206`, aperçu et téléchargement. Les réponses destinées au navigateur n’incluent ni chemin local, ni clé d’objet, ni URL présignée.

Le lecteur PDF.js est commun aux écrans concernés. Le flux Import et ses actions existantes sont conservés; les libellés principaux de l’interface sont harmonisés en français et l’accent jaune est remplacé par un accent lagon. Aucune nouvelle dépendance lourde n’a été ajoutée : le lecteur PDF.js déjà présent est réutilisé.

## Cause du HTTP 501 — trois lignes

1. L’ancienne route `GET /api/pdf` appelait `POST /open?path=…` avec `redirect: "manual"`.
2. Si `/open` renvoyait une redirection 3xx vers une URL S3 présignée, la route historique renvoyait explicitement HTTP 501.
3. Le nouvel accès utilise l’identifiant de catalogue et relaie le flux/Range; le navigateur ne reçoit ni chemin local ni URL S3.

La reproduction a été faite dynamiquement en exécutant le handler historique extrait de `HEAD` contre un faux `/open` HTTP renvoyant un 302 signé : le handler a bien répondu 501. Ce test reproduit le défaut de la route, mais ne constitue pas un essai sur une instance SEAMTECH ou un MinIO réel.

## Inventaire des changements

### API et confidentialité

- `seamtech_search/fiches/routes.py` : catalogue de fichiers accessibles, association des pièces de fiche à un identifiant, routes d’aperçu/téléchargement, contrôle des racines autorisées, réponses `GET`/`HEAD`, plages d’octets locales et S3, codes `206`/`416`, types MIME et `Content-Disposition` sûrs. Les chemins historiques restent internes et les champs de chemin de la réponse sont expurgés.
- `seamtech_search/storage.py` : ajout des opérations S3 `head_object` et `get_object` avec prise en charge de `Range`; la signature historique de `get_presigned_url` est conservée.
- `seamtech_search/api.py` et `seamtech_search/indexer.py` : ajout de l’identifiant aux résultats et retrait des chemins, `path_key` et `object_key`; le parent renvoyé est réduit au nom du dossier.
- `seamtech_search/fiches/depot.py` : liaison des pièces déposées à l’index documentaire par identifiant.
- Nouvelles routes Next authentifiées pour les pièces, le détail et l’historique des fiches. Les proxys transmettent les en-têtes de session au backend et filtrent les en-têtes de réponse.
- `/api/open` et `/api/preview` renvoient désormais 410 après contrôle de session; l’ancienne route `/api/pdf` a été retirée.
- Le proxy des artefacts Import suit une éventuelle redirection présignée côté serveur, vérifie qu’il s’agit d’une URL signée et **ne relaie pas** les en-têtes d’authentification au stockage. Le test dynamique local a vérifié que le jeton atteint le backend mais pas le serveur S3 simulé et qu’aucun `Location` signé ne ressort vers le client.
- Documentation de vérification et audit stockage mises à jour.

### Interface

- Nouveau `VisionneusePiece` commun basé sur le PDF.js déjà installé : pagination, zoom, surlignage de zone, aperçu texte/image et liens d’ouverture/téléchargement par identifiant.
- Recherche conserve son moteur, ses facettes et sa sidebar; les résultats ouvrent la pièce par ID et ne rendent plus de chemin.
- Fiche affiche ses métadonnées, pièces, champs et historique; Dossiers et Recherche ouvrent `/fiches/[code]`.
- Nouveau catalogue Fichiers avec filtres, pagination, aperçu et téléchargement.
- Validation utilise le même lecteur et les mêmes routes par ID.
- Import reste disponible : dépôt de fichiers/dossiers, analyse, correction, progression et reprise conservés; seuls les libellés et formats de taille ont été francisés. Le type interne `DroppedFile.file` est cohérent avec ses usages.
- Métadonnées de page en français (`lang="fr"`) et thème d’interface à accent lagon (`#55b9aa`), l’ambre restant réservé aux alertes.

### Tests et recette

- Ajout de tests API pour fichiers locaux et stockage S3 simulé : catalogue, autorisation, ID inconnu, `GET`, `HEAD`, `Range`/`206`, disposition inline/attachment et absence de chemin/clé/URL de stockage dans les réponses.
- Recherche : tests vérifiant l’ID et l’absence de champs de chemin/objet.
- Recette : sept contrôles fonctionnels ajoutés — quatre parcours métier, pièces par ID, intégrité SHA-256 des sept PDF sources (dont GIB SEA `250328 AJA`) et catalogue Fichiers. Le total déclaré reste **19 fonctionnels + 8 orchestration = 27**.
- Ajout des parcours Playwright des fichiers et de sondes d’authentification pour le catalogue et les routes d’aperçu/téléchargement.
- `.gitattributes` fixe le checkout CRLF des scripts PowerShell. Les 9 scripts ont été contrôlés en BOM UTF-8 et CRLF; aucun volume de données ni archive RG13 n’a été modifié.

## Vérifications exécutées

| Vérification | Résultat |
|---|---|
| `corepack pnpm install --frozen-lockfile` | **PASS** — 373 paquets installés; lockfile inchangé. |
| `corepack pnpm exec tsc --noEmit` | **PASS**. |
| `corepack pnpm build` | **PASS** — compilation de production et vérification TypeScript. |
| `python -m pytest -q` dans `.venv` | **PASS** — 834 passed, 233 skipped, 1 avertissement de dépréciation Starlette/httpx, en 84,74 s. |
| `ruff check` sur les fichiers Python modifiés | **PASS**. |
| `git diff --check` | **PASS**. |
| BOM UTF-8 + CRLF sur 9 scripts PowerShell | **PASS**. |
| Playwright `e2e/auth.spec.ts` ciblé | **PASS partiel** — 22 passés, 4 ignorés (parcours nominatif nécessitant PostgreSQL). |
| Suite Playwright complète | **Bloquée par l’environnement** — 22 passés, 20 ignorés et 7 échecs de lancement parce que Chromium n’est pas installé; l’installation a échoué avec `ECONNRESET` vers `cdn.playwright.dev`. Les échecs sont `browserType.launch` (exécutable absent), pas des assertions applicatives. |
| Reproduction de l’ancien 501 | **PASS** — handler historique + faux `/open` 302, résultat observé : 501. |
| Proxy Import face à une redirection présignée simulée | **PASS** — octets servis, jeton absent côté stockage, `Location` absent de la réponse. |

## Statut des 27 contrôles de recette

**Lecture du tableau :** `PASS*` signifie que le comportement a un test automatisé local équivalent; il ne prétend pas qu’une recette complète sur le corpus/MinIO a été exécutée. `NON EXÉCUTÉ` signifie que le contrôle intégré de recette n’a pas été lancé. Aucun contrôle de cette matrice n’a été déclaré `FAIL` sur la base d’un échec métier; la suite UI bloquée par Chromium est rapportée séparément ci-dessus.

Les 20 contrôles précédemment déclarés PASS ne sont pas déclarés en régression : la suite de non-régression Python et les tests locaux ont été relancés avec succès. En revanche, faute de pile Docker/PostgreSQL/MinIO, je ne les présente pas comme une nouvelle exécution de la recette opérationnelle complète.

| # | Contrôle | Statut | Preuve / limite |
|---:|---|---|---|
| 1 | `compte-recette` | NON EXÉCUTÉ | Le compte nominatif de recette exige la base PostgreSQL; les quatre tests Playwright nominatifs ont été ignorés. Les tests d’authentification de secours ont passé. |
| 2 | `depot-archives` | NON EXÉCUTÉ | Le dépôt réel du corpus n’a pas été lancé; tests unitaires backend passés dans la suite. |
| 3 | `suivi-lots` | NON EXÉCUTÉ | Pas de recette du lot sur le corpus réel; contrôles backend unitaires inclus dans les 834 tests. |
| 4 | `fiches-a-valider` | NON EXÉCUTÉ | Requiert le corpus et la base PostgreSQL métier. |
| 5 | `validation-fiche` | NON EXÉCUTÉ | Requiert PostgreSQL et parcours de validation complet. |
| 6 | `recherche-texte` | NON EXÉCUTÉ | Le contrôle cible la recherche métier sur les fiches du corpus PostgreSQL. |
| 7 | `recherche-dimension` | NON EXÉCUTÉ | Mesure sur les cotes réelles non lancée sans base/corpus PostgreSQL. |
| 8 | `filtres-facettes` | NON EXÉCUTÉ | Le contrôle métier complet requiert les fiches semées en PostgreSQL. |
| 9 | `suggestions` | NON EXÉCUTÉ | Le contrôle métier complet requiert les fiches semées en PostgreSQL. |
| 10 | `pdf-presigne` | PASS* | `tests/test_url_presignee_302.py` vérifie le 302, `Location` et l’expiration de 900 s avec boto3 simulé; MinIO réel non testé. |
| 11 | `zone-surlignee` | NON EXÉCUTÉ | Parcours sur fiche réelle et rendu navigateur non exécutés. |
| 12 | `rejeu-idempotent` | NON EXÉCUTÉ | Rejeu du dépôt sur PostgreSQL non lancé. |
| 13 | `parcours-recherche` | NON EXÉCUTÉ | Parcours métier UI requiert PostgreSQL et Chromium. |
| 14 | `parcours-dossiers` | NON EXÉCUTÉ | Parcours de fiche/catégorie réel non lancé; navigateur absent. |
| 15 | `parcours-validation` | NON EXÉCUTÉ | Requiert PostgreSQL et navigateur Chromium. |
| 16 | `parcours-fiche` | NON EXÉCUTÉ | Requiert une fiche métier en PostgreSQL et navigateur Chromium. |
| 17 | `pieces-par-id` | PASS* | Test API local + S3 simulé : ID, authentification, `GET`/`HEAD`, plage `206`, téléchargement et absence de fuite. MinIO réel non testé. |
| 18 | `pdf-7-fiches-integrite` | NON EXÉCUTÉ | Téléchargement et comparaison des sept PDF source réels non lancés; empreintes SHA-256 épinglées dans le vérificateur. |
| 19 | `fichiers-catalogue` | PASS* | Test du catalogue local et S3 simulé : ID et absence de chemin/clé dans la réponse; corpus réel non testé. |
| 20 | `prereqs` | NON EXÉCUTÉ | Orchestrateur local non lancé; `docker` et PowerShell absents. Vérifications statiques/unitaires passées. |
| 21 | `ports-libres` | NON EXÉCUTÉ | Étape de l’orchestrateur non lancée. |
| 22 | `env-secrets` | NON EXÉCUTÉ | Génération et contrôle des secrets par l’orchestrateur non lancés. |
| 23 | `image-minio` | NON EXÉCUTÉ | Construction d’image impossible sans Docker. |
| 24 | `pile-sante` | NON EXÉCUTÉ | Pile Docker/MinIO/PostgreSQL non démarrée. |
| 25 | `sauvegarde` | NON EXÉCUTÉ | Sauvegarde de recette réelle non lancée; tests unitaires de sauvegarde inclus dans la suite. |
| 26 | `restauration` | NON EXÉCUTÉ | Restauration sur service/base temporaires non lancée. |
| 27 | `persistance-volumes` | NON EXÉCUTÉ | Redémarrage de pile et contrôle de volumes impossibles sans Docker; aucun volume n’a été touché. |

### Pourquoi les contrôles intégrés n’ont pas tous été lancés

`docker`, `pwsh`/`powershell` et le navigateur Chromium ne sont pas disponibles dans cet environnement. Le téléchargement Playwright a été refusé par une rupture TLS (`ECONNRESET`). La base/corpus PostgreSQL et MinIO de recette ne sont pas configurés ici. Le vérificateur local complet n’a donc pas été lancé; cela évite également d’écrire dans son répertoire de travail de recette ou de toucher aux archives/volumes RG13.

## Commits demandés

1. `feat(api): pieces`
2. `feat(front): viewer`
3. `feat(front): fiche page`
4. `feat(front): fichiers browser`
5. `test: recette` — inclut le vérificateur, les tests de recette et ce rapport.

## Suivi des échecs CI — 1 octobre 2026

Le run initial [36899890884](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36899890884) échouait sur trois jobs et quatre causes distinctes (deux tests dans la recette, le garde E2E et le gate de couverture). Les corrections ont été poussées sur la même branche dans `69ba186`, `9925d15` et `aaf3a11`; elles n'abaissent aucun seuil de qualité.

### Causes et correctifs

1. **`recette-corpus-reel`, test 07 — recherche du document de fiche.** À l'upsert, le dépôt remplaçait le contenu déjà extrait/indexé du PDF par la chaîne générique « PDF source de la fiche … ». Pour une nouvelle ligne, cette chaîne ne contenait pas non plus les métadonnées recherchées. L'upsert conserve maintenant tout contenu déjà indexé et, seulement s'il est vide, utilise les métadonnées textuelles de la fiche comme solution de repli; les champs identifiant un chemin ou une clé de stockage en sont exclus. Le test de préservation ajouté utilise un fichier et une fiche synthétiques, sans lire le PDF client.
2. **`recette-corpus-reel`, test 10 — `AttributeError` en cascade.** Le test dépendait de `RECETTE["fiche_attendue"]`, état partagé renseigné par le test 07. Quand le 07 échouait, le 10 tombait à son tour avant de vérifier la restauration. Le test 10 recherche maintenant directement REF-001 à partir de sa référence fixe et ne dépend plus de l'ordre ou du résultat du test 07.
3. **`e2e`, garde d'authentification nominative.** Les 26 tests Playwright avaient tous réussi (0 flaky, 0 sauté), mais le garde-fou CI attendait encore exactement 23. C'était un désaccord de comptage, pas un défaut d'authentification; l'attendu et l'annotation ont été portés à 26.
4. **`backend (3.13)`, porte de couverture.** La suite pytest/PostgreSQL terminait, puis le gate refusait le rapport contre les planchers existants. Le journal/artifact du run initial n'a pas pu être téléchargé (réponse EOF du service de logs), donc le pourcentage/module précis sous le seuil à ce SHA n'est pas récupérable et n'est pas attribué par supposition. Des tests synthétiques supplémentaires couvrent les accès par ID — catalogue PostgreSQL, association d'une pièce à une fiche, GET/HEAD/Range, cas 404/415/416, erreurs S3 et absence de fuite — ainsi que les erreurs d'import et l'indexation. Le gate CI publie désormais son diagnostic détaillé en annotation en cas de nouvel échec.

### Validation après correction

Le run [36909952842](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36909952842), SHA `aaf3a11e25658737c3b34d599388ce03d1d02850`, est **vert sur tous les jobs**, dont `recette-corpus-reel`, `e2e` et les trois versions Python. Le corpus de recette a été extrait dans le répertoire temporaire CI sous les gardes RG13, puis l'empreinte des ZIP a été contrôlée de nouveau; aucun ZIP ni volume du dépôt n'a été modifié.

- E2E auth nominatif : **26 tests verts, 0 sauté** (dont les 4 comptes nominatifs).
- Suite PostgreSQL : **221 passés, 0 sauté**; les nouveaux tests PostgreSQL de l'accès par ID et de préservation du contenu ont été exécutés.
- Couverture : **86,93 % au global** pour un plancher inchangé de **85 %**; gate global et portes par module tous verts. Mesures publiées : `api.py` 89,7 %, `indexer.py` 94,9 %, OCR `inventaire.py` 85,2 %, `etat.py` 77,2 %, `pipeline.py` 79,4 % et `cli.py` 79,8 %.
- Vérifications locales ciblées : **47 passés, 1 sauté**; Ruff, `git diff --check` et analyse YAML du workflow passent. Le seul saut local concernait l'intégration PostgreSQL sans URL de base; elle a ensuite passé dans CI.
