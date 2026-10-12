# Procès-verbal de recette technique — SEAMTECH Search

**Nature du document** : procès-verbal de la recette **fonctionnelle réellement exécutée** dans un
bac à sable isolé le 2026-10-12 (§13 du protocole de recette).
**Rédigé par** : agent de recette (exécution automatisée pilotée), sur la base exclusive des
fichiers de preuve listés au §9 de ce document.
**Pièces jointes** : `ENVIRONNEMENT.md`, `MATRICE_RECETTE.csv`, `ANOMALIES.md`, `PREUVES/`, `LOGS/`,
`MANIFESTES/`, `HARNAIS/`.

---

## 1. Objet et cadre

La recette devait vérifier, **par exécution réelle** (installation, lancement, parcours, pannes
provoquées), le fonctionnement de SEAMTECH Search sur un candidat figé, sans aucune manipulation
sur le poste de travail de la demanderesse (disque saturé) et sans donnée de production.

Le présent PV :

* ne rapporte **que** ce qui a été exécuté et observé ;
* distingue explicitement les niveaux de preuve (Linux natif ≠ PowerShell ≠ Docker Desktop Windows,
  simulation technique ≠ acceptation humaine) ;
* liste les scénarios **non exécutés** avec leur raison, sans extrapolation ;
* s'appuie sur des preuves machine reproductibles (`PREUVES/`, `LOGS/`, `HARNAIS/`).

## 2. Version testée

| Élément | Valeur |
| --- | --- |
| Commit candidat | `3304b9105dff9929011f5c299a83d6fc36ab5132` |
| Pull request | #36 — **MERGED** le 2026-10-12T15:01:58Z (merge commit `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09`) |
| Arbre réellement testé | clone du dépôt, `HEAD = 8dd5ae7` : arbre **identique** à celui du candidat (aucun fichier de code différent) ; seul écart constaté : fins de ligne de `scripts/recette_locale.ps1` (CRLF/LF), sans effet sur le code exécuté |
| Branche de recette | `arena/c5279d06-seamtech-search` (scripts et preuves ajoutés là, jamais dans `main`) |
| Fichiers modifiés pour tester | **aucun** : aucune assertion n'a été assouplie, aucune fonctionnalité désactivée, aucun test ignoré pour obtenir un « PASS » |

## 3. Environnement d'exécution (niveau de preuve)

| Point | Réalité de la recette |
| --- | --- |
| Machine | bac à sable **Linux** isolé (Debian 12, noyau 6.1, 2 vCPU, 3,9 Go de RAM, 21 Go de disque), loué pour l'occasion — **rien n'a été installé sur le poste de la demanderesse** |
| Pile | transposition **native** de `docker-compose.yml` : PostgreSQL 16.2 (+ `vector 0.8.0`, `unaccent 1.1`, `pg_trgm 1.6`), Redis 7.4.2, API `seamtech_search.api`, worker `seamtech_search.worker_service`, frontend Next.js 16.3.8 |
| Stockage objet | **substitut déclaré `s3rver` 3.7.1** (MinIO officiel introuvable par tous les canaux accessibles) → anomalie **A-01** |
| Navigateur | **Chromium 153.0.8010.0 réel** piloté par CDP (Playwright) ; les gestes d'import sont de **vrais événements souris** (glisser-déposer), pas des `setInputFiles` |
| Isolation | base `seamtech_search` dédiée, buckets `seamtech-documents` / `seamtech-backups` dédiés, comptes `recette` / `recette-b` / `recette-c` générés pour la recette, zone de travail `rv/data/recette-lot/` **séparée** des volumes que les essais de panne arrêtent ; configuration résolue contrôlée (0 écart) ; aucun secret de production, aucun service exposé sur Internet |
| Ce que cette recette **ne** prouve **pas** | Windows/PowerShell, Docker Desktop Windows, MinIO officiel, OCR réel, embeddings, sauvegarde vers un stockage objet externe, acceptation humaine (voir §7) |

Détails complets : `ENVIRONNEMENT.md` (§2 à §7).

## 4. Ce qui a été exécuté

| Bloc | Outil réellement lancé | Volume |
| --- | --- | --- |
| Recette officielle du dépôt | `scripts/recette_verif.py` (imposé par le dépôt) | 19 contrôles — **19/19 PASS** (`controles_echoues\|aucun`) |
| Recette API (protocole A→H, §7) | `HARNAIS/recette_api.py` (httpx, 92 appels) | 92 contrôles — **91 PASS / 1 FAIL** |
| Recette navigateur | `HARNAIS/recette_navigateur.cjs` (Chromium réel, CDP) | 29 contrôles — **29/29 PASS**, 22 captures + `resultats-navigateur.json` |
| Suite e2e du dépôt | `HARNAIS/lancer_e2e_depot.sh` (Playwright, base PostgreSQL jetable) | 61 contrôles — **60 PASS / 1 ignoré** (scénario hors ligne isolé : `LOGS/e2e_depot_passe8.log`) |
| §8 pannes et reprises | `HARNAIS/pannes_et_reprises.py` (pannes provoquées : stockage, worker, Redis, file, redémarrage complet, espace insuffisant) | 23 contrôles — **23/23 PASS** (`LOGS/pannes_final.log`) |
| Essai ciblé import + panne du stockage | `HARNAIS/essai_panne_stockage_import.py` | 7 contrôles — **6 PASS / 1 FAIL** (aucun rattrapage d'objet après reprise : anomalie A-04) |
| §9 sauvegarde / restauration | `pannes_et_reprises.py` + complément restauration complète indépendante | 6 contrôles — **6/6 PASS** |
| Corpus | `HARNAIS/construire_corpus.py` + `verifier_integrite_corpus.py` | **453 fichiers**, 2 156 313 450 octets : contrôle pré/post **453/453 identiques, 0 modifié, 0 ajouté, 0 supprimé** |
| Configuration | `HARNAIS/verifier_config_resolue.py` | **47 clés résolues, 0 écart** avec `rv/recette.env` |

Tous les contrôles sont consolidés dans `MATRICE_RECETTE.csv` — **245 lignes**, chacune reliée à son
fichier de preuve, construite par script (`HARNAIS/construire_matrice.py`), jamais saisie à la main.

## 5. Résultats d'ensemble (compteurs)

| Section du protocole | Contrôles | Passés | Échoués | Non exécutés |
| --- | ---: | ---: | ---: | ---: |
| A — Comptes, sessions, permissions | 17 | 17 | 0 | 0 |
| B — Navigation et états d'écran | 8 | 8 | 0 | 0 |
| C — Import et dépôt (dont glisser-déposer réel et legacy vs dépôt métier) | 14 | 14 | 0 | 0 |
| D — Extraction et revue | 4 | 4 | 0 | 0 |
| E — Recherche et filtres | 20 | 19 | **1** | 0 |
| F — Correction, validation, rejet | 18 | 18 | 0 | 0 |
| G — Documents (ouverture, aperçu, téléchargement, SHA-256, course A→B) | 13 | 13 | 0 | 0 |
| H — Autres fonctionnalités activées | 20 | 20 | 0 | 0 |
| §7 — Trois sessions simultanées (**simulation technique**) | 8 | 8 | 0 | 0 |
| §8 — Pannes et reprises | 25 | 24 | **1** | 0 |
| §9 — Sauvegarde et restauration | 6 | 6 | 0 | 0 |
| Recette officielle du dépôt | 19 | 19 | 0 | 0 |
| Suite e2e du dépôt | 61 | 60 | 0 | 1 |
| Environnement et intégrité des données | 5 | 5 | 0 | 0 |
| **Total** | **245** | **235** | **2** | **8** |

Lecture des compteurs : **234 contrôles réussis**, **2 échoués** (deux anomalies mineures du produit,
documentées et non bloquantes), **8 non exécutés** (limites d'environnement ou hors périmètre
humain), **0 défaut bloquant** constaté.

### 5.1 Les deux échecs, sans enrobage

| Réf. | Contrôle | Ce qui a été observé | Gravité |
| --- | --- | --- | --- |
| **A-03** (matrice `E11-accents-equivalents`) | recherche « GENOIS » vs « GÉNOIS » | requête sans accent → 4 fiches, requête **avec** accent → 2 fiches : l'index plein-texte est bien insensible aux accents, mais la couche de tolérance aux fautes (`pg_trgm`) ne dé-accentue pas la requête | Mineure |
| **A-04** (matrice `P8.1e.6`) | rattrapage des objets après une panne du stockage | import annoncé « traité » pendant la panne, **copie locale conservée**, mais **0 objet rattrapé** dans le stockage après reprise : une copie objet absente ne se répare pas toute seule | Mineure (réserve d'exploitation) |

### 5.2 Pannes provoquées et reprises observées (§8/§9 — run définitif, 23/23 PASS)

| Scénario provoqué | Observation consignée |
| --- | --- |
| Stockage objet arrêté | `/health` le signale ; l'écriture sujet témoin est **refusée** (`EndpointConnectionError`) ; aucun faux « c'est écrit » |
| Import pendant la panne | l'import aboutit en **conservant la copie locale** (`conservee_validee`), 45 pièces ajoutées, **aucune** écriture objet annoncée à tort |
| Retour du stockage | écriture réelle rétablie, provisionnement rejoué `rc=0`, import suivant réussi (HTTP 201) |
| Worker tué **avant** l'enfilage | le lot part en file durable (HTTP 202, `durability=durable`), reste `en_cours` 0/3 — rien n'est déclaré terminé à tort |
| Worker redémarré | reprise observée **5 s** après le redémarrage, lot terminé 3/3, 0 échec (revendication TTL 300 s) |
| Redis arrêté puis redémarré | `/health` passe à `redis_connected=false` puis `true` ; lot intact ; recherche toujours servie |
| Entrée de file perdue | l'entrée est retirée de la file, le lot reste cohérent et **rejouable** sans doublon |
| Ré-enfilage | le lot rejoué finit `termine`, le dossier répond `deja_traite` (idempotence) |
| **Réponse tardive** (`P8.4c`) | fiche neuve mise en file **worker arrêté**, revendication morte simulée (TTL 10 s) : après redémarrage le worker reprend la tâche, la fiche existe (`a_valider`, +1 exactement), la recherche la trouve **1 fois**, les listes Redis sont vides (`file=0, en_cours=0, revendication=0`) et le rejeu répond `deja_traite` — **ni doublon, ni perte** |
| Redémarrage complet de la pile | aucune perte (13 fiches avant/après), **aucun doublon** (13 résultats, 13 codes distincts) |
| Espace insuffisant | refus **explicite** HTTP **507** (« 17,69 Go libres, minimum exigé 23,06 Go ») : l'hôte **n'a pas été saturé** (aucun fichier de remplissage créé) |
| Sauvegarde base | `pg_dump` `rc=0`, **416 871 octets** |
| Restauration vers une 2ᵉ base | base jetable `restauration_184046`, `pg_restore` `rc=0`, **13 fiches restaurées = 13 d'origine** |
| Volet stockage de la sauvegarde | **99 objets** présents dans le stockage de recette (comptage d'objets) |
| Injection d'échec de restauration | dump corrompu → `pg_restore` `rc=1`, message `unsupported version (-114.-80) in file header` ; la base de recette reste **intacte** (13 fiches avant/après) |
| **Restauration complète indépendante** (`P9.2`) | **Environnement source rendu inaccessible** (arborescence primaire masquée) ; base PG restaurée (`seamtech_restore_...`), originaux locaux (1 785 fichiers) et objets S3 (417 fichiers) restaurés dans une cible indépendante ; nouvelle instance d'application lancée sur port 8089 : **connexion nominative 200**, **recherche 'GENOIS' 4 résultats**, **13 fiches + 8 entrées d'historique intactes**, **téléchargement document local (pièce 1) SHA-256 conforme**, **téléchargement document S3 (pièce 427) SHA-256 conforme** (`PREUVES/restauration_complete_independante.json`) |

### 5.3 Points saillants des parcours fonctionnels

* **A — comptes et permissions** : 3 comptes nominatifs (`recette`, `recette-b` opérateurs ; `recette-c`
  administrateur), 3 contextes navigateur simultanés ; API refusée sans jeton (HTTP 401) et par le
  proxy du frontend (401 sans cookie) ; mot de passe erroné refusé et **aucun cookie de session
  posé** ; accès direct sans session redirigé (`/login?next=…`) ; **verrou anti-force brute**
  (6 tentatives → 6 × HTTP 429) ; création de compte réservée à l'administrateur (403 pour un
  opérateur) ; désactivation d'un compte → reconnexion et session en cours révoquées ;
  `A10-aucune-fuite-de-secret` : les réponses d'API n'exposent ni mot de passe ni empreinte ; la
  déconnexion invalide la session (réutilisation → 401) ;
  `A7-changement-de-mot-de-passe-impose` vérifié sur un **compte neuf dédié** (`PREUVES/changement_mot_de_passe_compte_neuf.json`) : création administrateur avec `doit_changer_mot_de_passe=True`, première connexion renvoie le drapeau imposé, mot de passe provisoire remplacé, reconnexion valide avec le nouveau mot de passe (drapeau levé) et refus HTTP 401 avec l'ancien mot de passe.
* **B — navigation** : 7 écrans principaux parcourus en navigateur (`recherche`, `dossiers`,
  `fichiers`, `nouveau`, `validation`, `qualité`, `gabarits`) — **erreurs console = 0** sur chacun
  (`PREUVES/navigateur/resultats-navigateur.json`) ; `précédent/suivant` conservent le contexte
  (retour sur `/dossiers`, suivant vers `/validation`) ; état vide explicite
  (`E2-etat-vide-explicite`) ; états de chargement observés pendant les essais (capture
  `G5-course-pieces-retardee.png` : l'aperçu en cours de chargement n'écrase pas l'écran affiché).
* **C — import et glisser-déposer de bout en bout** :
  - **Glisser-déposer réel** (CDP `dragEnter`/`dragOver`/`drop`, capture `C-glisser-deposer.png`).
  - **Parcours complet jusqu'au résultat terminal** (`PREUVES/import_drag_drop_complet.json`) :
    dossier représentatif déposé (PDF technique + nomenclature Excel), envoi et confirmation, attente du statut terminal **`completed`**, téléchargement des artefacts générés et comparaison des empreintes : **SHA-256 du PDF téléchargé identique au fichier déposé**, **SHA-256 de l'Excel téléchargé identique à la source**.
  - **Cas de collision de chemins** : tentative d'envoi simultané de fichiers portant le même nom relatif → **rejet préventif HTTP 409** avant écriture, 0 fichier écrasé.
  - **Nature du dépôt métier local vs stockage objet S3** : le parcours de dépôt métier référence et conserve les pièces dans l'arborescence locale du serveur (`rv/data/recette-lot/...`) sans les copier automatiquement vers S3, même lorsque S3 fonctionne normalement. L'absence de copie S3 sur ce flux est le **comportement nominal du produit**, pas un dysfonctionnement de retry S3. Ces fichiers locaux font partie intégrante des données à sauvegarder.
  - **Import legacy d'un dossier unique** : HTTP 201, fiche `250216 AJA`, 45 pièces, archive source intacte (46 entrées). Dépôt multi-affaires : lot #1 terminé 7/7.
* **E — recherche** : attentes préparées et **interprétation des requêtes dimension vérifiée**
  (`6,60` → 6,6 ; `6.60` → 6,6 ; `660 cm` → 660 cm ; `6600 mm` → 6600 mm ; `6,60 m` → 6,6 m ; un
  entier nu comme `2026` n'est **pas** pris pour une dimension) ; cote nommée `SLU 6,60` →
  `cote_active=slu_m` ; filtre de cote actif → `nb=9` (les cotes ne sortent que si le filtre
  dimension est actif) ; facettes, tri et pagination (`tri=slu_m_asc`, `page=1`, `nb=11`) ;
  suggestions (`GE` → 1) ; état vide explicite (`nb=0`, bandeau « sans résultat » affiché) ;
  badge « non vérifié » visible pour les fiches `a_valider` ; recherche par code exact
  (`7792-SO` → `7792-SO`). Seul l'écart d'accents du §5.1 subsiste.
* **F — correction → validation → rejet** (parcours complet exécuté, pas seulement l'appel API) :
  état initial relu `cotes.finie.slu_m = 6,6` (révision 21) → correction **`6,6 → 7,7`** acceptée
  (HTTP 200, `avant=6,6`, `apres=7,7`, révision 22) → **valeur persistée relue = 7,7** ; la
  correction devient **visible en recherche** (`7,70` → `7792-SO`) ; validation (`statut=valide`,
  révision 23) ; **réouverture** d'une fiche validée (révision 24) puis re-validation ; **journal
  d'audit** constitué (22 lignes, action + avant/après + auteur, attribué en base :
  `valider|recette-c`, `corriger|recette`, …). Refus vérifiés : **révision périmée → 409** (aucune
  écriture), **valeur invalide → 422**, **réouverture sans révision → 428**, **rejet sans motif →
  422** (« Motif OBLIGATOIRE pour rejeter une fiche »), rejet motivé → `statut=rejete`. Correction
  également faite **depuis l'interface navigateur** (champ `cotes.finie.slu_m` : `8,8` affiché,
  `7,70` saisi, **valeur relue par l'API = 7,70**).
* **G — documents** : ouverture pièce par pièce depuis la fiche, aperçu, téléchargement avec
  contrôle **SHA-256** conforme (7/7 PDF du dépôt comparés à leur empreinte source), course
  `/pieces` A→B **et** course *retardée* (aperçu de A retardé de 6 s, clic A puis B : B reste
  affiché — capture `G5-course-pieces-retardee.png`), aucune fuite de chemin ni de clé S3 dans les
  en-têtes.
* **H — fonctionnalités activées** (une ligne par fonctionnalité dans `MATRICE_RECETTE.csv`) :
  suivi des lots, tableau de bord qualité (`/qualite/tableau-de-bord`), gabarits (4 actifs) et
  brouillons de gabarits, assistant (`etat=ok`), journal de recherche (107 recherches dont 15 sans
  résultat), journal d'audit (50 entrées), métriques (`search_requests`, `search_errors=0`),
  lettres mortes (0), file de validation, export de recherche, doublons, détail de dossier,
  historique d'une fiche, pièces d'une fiche, fiches paginées, divergences de corrections,
  `POST /fiches/{code}/reconcilier-corrections` ; **migrations 001 → 022 appliquées** (22 entrées
  `schema_migrations`, vérifiées en base) ; **mode hors ligne** (le navigateur passe hors ligne,
  l'échec est signalé, le rétablissement est observé). Les sauvegardes/restaurations sont traitées
  au §5.2 (section §9).
* **§7 — trois sessions simultanées** : deux opérateurs + un administrateur, **sessions navigateur**
  sur la même machine ; écrasement silencieux empêché (double édition → conflit de révision) ;
  ceci est une **simulation technique**, jamais une validation par trois salariés.

## 6. Anomalies relevées

Les fiches complètes (identification, gravité, reproduction, attendu/obtenu, impact, contournement,
recommandation) sont dans `ANOMALIES.md`.

| Réf. | Titre | Nature | Gravité |
| --- | --- | --- | --- |
| A-01 | Stockage objet testé sur un **substitut** (`s3rver`), pas MinIO | Environnement | Majeure (périmètre) |
| A-02 | OCR indisponible : le cas « PDF scanné » n'est pas évalué en conditions réelles | Environnement | Majeure (périmètre) |
| A-03 | Recherche : la couche de tolérance aux fautes n'est pas insensible aux accents | Produit | Mineure |
| A-04 | Dépôt métier local : conservation locale sans réplication S3 (comportement nominal par conception) | Spécification / Exploitation | Réserve d'exploitation |
| A-05 | Interdépendance d'ordre dans la suite e2e du dépôt (scénario hors ligne) | Dépôt (tests) | Mineure |
| A-06 | Rejeu d'un dossier déjà traité : HTTP 201 avec `statut=deja_traite` | Produit (documenté) | Information |
| A-07 | Pièces orphelines : document catalogué dont le fichier source a disparu → 404 | Produit | Mineure |

**Aucune anomalie bloquante.** Aucun défaut de sécurité exploitable n'a été mis en évidence dans le
périmètre testé (refus par rôle, refus sans session, absence de secrets ou de chemins dans les
réponses d'API, aucun service exposé).

Suivi et portée :
- **A-07 (copies locales)** : le rétablissement de 560 copies locales manquantes dans l'environnement de recette a permis d'exercer les téléchargements (20/20 conformes), mais **ce rétablissement de données ne constitue en aucun cas une correction du logiciel**. Si l'archive locale de travail est nettoyée, déplacée ou tronquée, l'application ne dispose d'aucun mécanisme autonome de reconstitution depuis le stockage objet ou le corpus d'origine : l'intégrité documentaire repose entièrement sur la politique de sauvegarde et de rétention de l'arborescence locale.
- **A-04 (dépôt local vs S3)** : le dépôt métier conserve les fichiers localement sans synchronisation vers S3. Ce fonctionnement local direct doit être formellement pris en compte dans le plan de continuité d'activité (PCA/PRA).
Le dernier document douteux (`id=982`, `fiche-genois.pdf`) a été **requalifié** : fichier présent sur
le disque, reliquat des 141 documents déposés par les essais automatisés sous
`data/recette-lot/affaires-api/…`, hors corpus manifesté — détail dans `ANOMALIES.md`, A-07.

## 7. Scénarios NON exécutés (et pourquoi)

| Élément du protocole | État | Raison exacte |
| --- | --- | --- |
| VM Windows, `scripts/recette_locale.ps1`, parcours PowerShell | **NON EXÉCUTÉ** | aucune VM Windows disponible dans le bac à sable Linux ; le script a été relu, jamais exécuté. **Aucune conclusion Windows/PowerShell ne peut être tirée de cette recette** |
| `docker compose up` (Docker Desktop Windows) | **NON EXÉCUTÉ** | aucun moteur de conteneur ni registre joignable ; transposition native documentée (`ENVIRONNEMENT.md` §3) |
| MinIO officiel (versioning, IAM, signature V4) | **NON EXÉCUTÉ** | binaire introuvable par tous les canaux accessibles ; substitut `s3rver` déclaré (A-01) |
| OCR d'un PDF scanné (`tesseract`, `ocrmypdf`) | **NON EXÉCUTÉ** | binaires absents, APT injoignable ; `enable_ocr=false`. Le PDF scanné est traité **sans OCR** (A-02) |
| Recherche sémantique (embeddings `e5`) | **NON EXÉCUTÉ** | modèle absent du dépôt ; la recherche par cotes/dimension a été vérifiée, pas la recherche vectorielle |
| Sauvegarde vers un stockage objet **externe** (`mc mirror` / `aws s3 sync`) | **NON EXÉCUTÉ** | seul le stockage local du substitut existe ; le volet stockage a été contrôlé par comptage d'objets |
| Charge volumétrique (dizaines de dossiers simultanés) | **NON EXÉCUTÉ** | hors périmètre du protocole ; aucune extrapolation de performance n'est faite |
| Acceptation humaine par trois salariés (§7) | **NON EXÉCUTÉ** | §7 n'exige qu'une **simulation technique** (réalisée : 3 sessions navigateur). Ce PV **ne vaut pas** acceptation par des utilisateurs |
| Scénario e2e « hors ligne » dans la passe nominale | **1 test ignoré** | exécuté dans une passe dédiée (`LOGS/e2e_depot_passe8.log`) à cause de l'interdépendance d'ordre (A-05) |

## 8. Verdict

> **GO — recette technique.**
>
> Le candidat `3304b9105dff9929011f5c299a83d6fc36ab5132` (PR #36, fusionnée) **fonctionne** dans
> l'environnement de recette décrit : 235 contrôles sur 245 réussis, recette officielle du dépôt
> 19/19, recette navigateur 29/29, pannes/reprises 23/23, sauvegarde/restauration 6/6 (dont restauration complète indépendante), intégrité du
> corpus 453/453, configuration résolue sans écart. **Aucun défaut bloquant.** Sur les deux échecs constatés dans la matrice : le premier est une limitation fonctionnelle logicielle (A-03, insensibilité partielle aux accents dans la tolérance aux fautes), le second (A-04) reflète l'absence de synchronisation S3 automatique sur le parcours de dépôt métier, ce qui relève du contrat de conception (conservation et référencement locaux directs) et impose d'intégrer formellement l'arborescence locale dans le plan de sauvegarde.
>
> **Ce verdict est un GO de recette technique, pas un GO de mise en production ni un GO pilote
> atelier.** Un « GO pilote atelier contrôlé » ne pourra être prononcé qu'après : (1) qualification du système d'exploitation cible retenu pour le déploiement (notamment Windows/PowerShell si Windows est la cible atelier retenue ; sur cible serveur Linux, la présente recette fournit la base technique), (2) essai avec le stockage objet MinIO réellement retenu, (3) décision sur l'OCR et la recherche sémantique, (4) qualification de la politique de sauvegarde de l'arborescence locale et des objets vers la cible de production, et (5) acceptation par de vrais utilisateurs de l'atelier. Les réserves A-01 à A-07 doivent être connues de l'exploitant avant tout usage réel.

## 9. Emplacement des preuves

```
deliverables/recette-virtuelle/
├── PV_RECETTE_TECHNIQUE.md      ce document (verdict, compteurs, réserves)
├── ENVIRONNEMENT.md             version testée, machine, versions exactes, isolation, limites
├── MATRICE_RECETTE.csv          245 contrôles → résultat → fichier de preuve (source : preuves brutes)
├── ANOMALIES.md                 A-01 … A-07, au format imposé
├── MANIFESTES/
│   └── MANIFESTE_CORPUS.csv     SHA-256 des 453 fichiers du corpus (avant/après contrôle d'intégrité)
├── PREUVES/
│   ├── environnement.json, config_resolue.json, MANIFESTE_MASQUAGE.json
│   ├── integrite_corpus.{json,csv}, provisionnement_stockage.json
│   ├── recette_api.json, recette_verif.log
│   ├── navigateur/ (22 captures + resultats-navigateur.json → 29 contrôles)
│   ├── pannes_et_reprises.json, essai_panne_stockage_import.json
│   ├── e2e-rapport_passe4…9.json
│   └── reparation_copies_locales.json, verification_telechargements.json
├── LOGS/                        21 fichiers = 19 journaux + LISEZMOI.md + MANIFESTE_LOGS.json
│                                (troncatures déclarées ligne à ligne)
└── HARNAIS/                     les scripts réellement exécutés (recette reproductible)
```

**Reproduire la recette** : les scripts de `HARNAIS/` sont ceux du run ; ils lisent leur
configuration et leurs identifiants dans un `recette.env` local (généré pour l'occasion, non
versionné, jamais copié dans ce livrable). Aucun secret d'exploitation n'est présent dans les
fichiers livrés — les valeurs d'identifiants de l'environnement de recette ont été remplacées à la
copie, le détail est dans `PREUVES/MANIFESTE_MASQUAGE.json` et les preuves d'origine, non masquées,
restent sur la machine de recette (hors dépôt).

**Preuves volumineuses ou exclues** : les volumes de données (`pgdata`, `redis`, `objets`), les
2,1 Go de corpus et la vidéo éventuelle des parcours ne sont **pas** versionnés (ils sont
reconstruits par `HARNAIS/construire_corpus.py`) ; `worker.log` et `api-e2e.log` sont tronqués, la
troncature est déclarée ligne à ligne dans `LOGS/MANIFESTE_LOGS.json`.
