# Fiches d'anomalie — SEAMTECH Search, recette technique du 2026-10-12

Version testée : commit `3304b9105dff9929011f5c299a83d6fc36ab5132` (pull request #36, fusionnée).
Environnement : bac à sable Linux isolé, pile native (voir `ENVIRONNEMENT.md`).

Chaque anomalie est écrite au format imposé : identification, gravité, environnement,
description, reproduction, résultat attendu / obtenu, preuve, impact, contournement,
recommandation. Les anomalies **d'environnement** sont distinguées des anomalies **du
produit** : une limite de l'environnement de recette ne doit pas être comptée comme un
défaut du logiciel, mais elle doit être connue.

| Réf. | Titre | Nature | Gravité | Exécution |
| --- | --- | --- | --- | --- |
| A-01 | Stockage objet testé sur un **substitut** (s3rver) et non MinIO | Environnement | Majeure (périmètre) | Constatée |
| A-02 | OCR indisponible : le cas « PDF scanné » n'est pas évalué en conditions réelles | Environnement | Majeure (périmètre) | Constatée |
| A-03 | Recherche : la couche de tolérance aux fautes **n'est pas insensible aux accents** | Produit | Mineure | Constatée |
| A-04 | Import d'un dossier pendant une **panne du stockage objet** annoncé « traité », **aucune copie objet** | Produit (réserve d'exploitation) | Mineure | Constatée |
| A-05 | Interdépendance d'ordre dans la suite e2e du dépôt : le scénario « hors ligne » casse 5 scénarios suivants | Dépôt (suite de tests) | Mineure | Constatée |
| A-06 | Le rejeu d'un dossier déjà traité renvoie HTTP 201 avec `statut=deja_traite` | Produit (comportement **documenté**) | Information | Constatée |
| A-07 | Pièces orphelines : un document dont le fichier source a disparu reste au catalogue et répond **404** | Produit | Mineure | Constatée |

---

## A-01 — Stockage objet testé sur un substitut (s3rver) et non MinIO

* **Nature / gravité** : limitation **d'environnement**, majeure pour le périmètre de recette
  (elle réduit ce que la recette peut affirmer sur le stockage objet), sans être un défaut
  du produit.
* **Environnement** : bac à sable Linux ; `minio/minio` n'était atteignable par **aucun**
  canal (releases sans binaire, `dl.min.io`, `storage.googleapis.com` et tous les registres
  de conteneurs injoignables) ; `docker`/`podman` absents.
* **Description** : le stockage objet a été testé sur **`s3rver` 3.7.1**, serveur S3 de test,
  déclaré comme substitut. Trois écarts mesurés au provisionnement :
  1. **versioning refusé** (`BucketAlreadyExists` sur `PUT ?versioning`) ;
  2. **aucune vérification de signature** : un **secret faux est accepté** (écritures
     autorisées avec un mauvais `secret_access_key`) ;
  3. **aucune politique IAM** : l'identité applicative écrit sans obstacle dans le bucket de
     sauvegarde (`seamtech-backups`), le principe de moindre privilège n'est pas applicable.
* **Reproduction** : `bash rv/harnais/provisionner_stockage.sh` (ajoute un aller-retour
  d'objet témoin et les essais d'identités ci-dessus).
* **Attendu / obtenu** : attendu — versioning actif, requêtes non signées ou mal signées
  refusées, identité « sauvegarde » seule autorisée sur le bucket de sauvegarde. Obtenu —
  versioning indisponible, signature non contrôlée, cloisonnement par politique inexistant.
* **Preuve** : `PREUVES/provisionnement_stockage.json`.
* **Impact** : les scénarios « versioning objet », « refus par politique » et « signature
  invalide » **ne sont pas couverts** par cette recette. La vérification d'intégrité
  applicative (relecture et empreinte après écriture, `SEAMTECH_STORAGE_VERIFY_REREAD=true`)
  a, elle, été vérifiée sur le substitut.
* **Contournement** : aucun côté produit ; à rejouer sur MinIO en environnement cible.
* **Recommandation** : rejouer les scénarios de stockage objet sur MinIO ou sur le stockage
  objet réel de l'entreprise avant le pilote atelier.

## A-02 — OCR indisponible : le cas « PDF scanné » n'est pas évalué en conditions réelles

* **Nature / gravité** : limitation d'environnement, majeure pour le périmètre.
* **Description** : `tesseract` et `ocrmypdf` sont absents du bac à sable et ne peuvent pas
  être installés (dépôts APT injoignables). La configuration de recette porte donc
  `enable_ocr=false`.
* **Reproduction** : `rv/corpus/sources/03_synthetique/SYNTH-PDF-SCANNE` (PDF image sans
  couche texte) déposé dans un dossier : la fiche n'est pas reconnue.
* **Attendu / obtenu** : attendu (avec OCR) — texte extrait de l'image, fiche détectée si le
  gabarit correspond. Obtenu (sans OCR) — « gabarit inconnu (scores de détection = 0) », la
  détection s'appuie uniquement sur la couche texte.
* **Preuve** : `PREUVES/recette_api.json` (C6 à C10, lot synthétique : `SYNTH-PDF-SCANNE`
  en échec explicite et non en faux succès), `ENVIRONNEMENT.md` §7.
* **Impact** : aucune conclusion sur la qualité d'extraction OCR ; le comportement « échec
  explicite et tracé plutôt que faux succès » est, lui, vérifié.
* **Recommandation** : installer `tesseract-ocr`/`ocrmypdf` dans l'environnement cible et
  rejouer la section « PDF scanné ».

## A-03 — Recherche : la couche de tolérance aux fautes n'est pas insensible aux accents

* **Nature / gravité** : **anomalie du produit**, mineure (aucune perte de correspondance
  exacte ; seule la tolérance aux fautes est affectée).
* **Environnement** : PostgreSQL 16.2, extensions `unaccent` et `pg_trgm` actives.
* **Description** : la même recherche écrite avec et sans accent ne renvoie pas le même
  ensemble. `GET /recherche?q=GENOIS` → **4** fiches ; `GET /recherche?q=GÉNOIS` → **2**.
  L'index plein-texte est bien insensible aux accents (config `seamtech_unaccent`) : les
  deux orthographes produisent les **mêmes lexèmes** (`250323 JA`, `0901-MM`). Ce sont les
  résultats supplémentaires qui viennent du volet de tolérance aux fautes
  (`pg_trgm`, `word_similarity`, seuil `0,3`) : la requête accentuée y est comparée telle
  quelle, sans dé-accentuation — `word_similarity('genois', 'Gennaker …') = 0,43`
  (au-dessus du seuil) alors que `word_similarity('GÉNOIS', 'Gennaker …') = 0,29`
  (sous le seuil, donc rejeté).
* **Reproduction** :
  ```
  curl -s "http://127.0.0.1:8000/recherche?q=GENOIS" -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN"
  curl -s "http://127.0.0.1:8000/recherche?q=GÉNOIS" -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN"
  ```
  (voir aussi le contrôle `E11` de `rv/harnais/recette_api.py`).
* **Attendu / obtenu** : attendu — ensemble de résultats identique, quelle que soit
  l'orthographe accentuée ou non (c'est l'intention documentée du dépôt :
  « accented and unaccented queries » doivent se comporter pareil, `indexer.py` §005).
  Obtenu — 4 résultats sans accent, 2 avec accent.
* **Preuve** : `PREUVES/recette_api.json` → contrôles `E11-accents-equivalents` (FAIL) et
  `E11b-lexemes-identiques-dans-l-index` (PASS) ; mesure SQL ci-dessus.
* **Impact** : un utilisateur qui saisit le mot accentué peut ne pas voir une fiche que son
  collègue trouve sans accent (« Gennaker » remonté sur « genois » mais pas sur « génois »).
  L'effet est limité aux correspondances approximatives.
* **Contournement** : saisir la requête sans accent.
* **Recommandation** : dé-accentuer la requête transmise au volet `pg_trgm`
  (par exemple `unaccent(q)` côté SQL, ou `word_similarity(unaccent(%s), unaccent(champs_texte))`),
  puis rejouer `E11`.

## A-04 — Import d'un dossier pendant une panne du stockage objet

* **Nature / gravité** : **réserve d'exploitation** du produit, mineure : aucun faux succès
  au sens de « données perdues » n'a été mesuré, mais un import se déclare « traité » alors
  qu'**aucune copie n'a été déposée dans le stockage objet** et que rien ne le signale dans
  le résultat de l'import.
* **Environnement** : pile de recette, `SEAMTECH_STORAGE_BACKEND=s3`,
  `SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD=false`.
* **Description (protocole et mesures)** — `rv/harnais/essai_panne_stockage_import.py` :
  1. avant : **153** objets dans le stockage, **936** pièces au catalogue ;
  2. le serveur de stockage est arrêté ; l'écriture témoin est **refusée**
     (`EndpointConnectionError`) ;
  3. dépôt d'un dossier **jamais importé** (copie fraîche de `BAVARIA 34 - 250323JA`,
     51 fichiers, 50 pièces) :
     `HTTP 201 statut="traite" fiche="250323 JA" pièces=50 raison="remplacee"` ;
  4. pendant : catalogue **936 → 987**, objets dans le stockage **153 → 153** ;
  5. les documents restent **servis** pendant la panne (`/pieces/295/telecharger` → 200,
     34 224 octets) ;
  6. après reprise du stockage : objets **153 → 153** — **aucun rattrapage** des copies
     objet manquantes ; aucune erreur de stockage n'apparaît au journal de l'API.
* **Attendu / obtenu** : attendu — soit l'import est refusé/atténué explicitement quand le
  stockage objet est en panne, soit il est traité **et** signale que les copies objet n'ont
  pas été faites. Obtenu — import traité, aucune copie objet, aucune mention dans la
  réponse, dans la fiche ou dans le lot.
* **Analyse de la cause (vérifiée dans le code)** : les dossiers déposés depuis une
  **archive locale** ne passent pas par le stockage objet : la copie locale est conservée
  (`SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD=false`) et le stockage objet n'est écrit que par les
  envois depuis le navigateur — ce que confirment les 150 objets observés, qui correspondent
  aux envois de la recette et aux objets témoins. La purge de la copie locale est, elle,
  conditionnée à une copie durable **vérifiée** dans le stockage objet
  (`worker.py`, messages « artefacts non vérifiés : … », « intégrité non prouvée »).
* **Preuve** : `PREUVES/essai_panne_stockage_import.json` (7 contrôles, `A04.6` en échec :
  « objets rattrapés après reprise : 0 ») ; contrôles `P8.1*` de
  `PREUVES/pannes_et_reprises.json`.
* **Impact** : dans cette configuration, **aucune perte** : les documents sont complets et
  servis. Le risque porte sur un déploiement qui attendrait du stockage objet qu'il porte la
  copie durable (ou avec `delete_local_after_upload=true`) : l'exploitant n'a aucun signal
  lui disant que la copie objet n'a pas eu lieu pour cet import.
* **Contournement** : vérifier `/health` (`storage_backend`, `s3_configured`) avant une
  campagne d'import massive ; ne pas compter sur une copie objet pour un import fait pendant
  une panne.
* **Recommandation** : tracer explicitement dans le résultat d'import (champs `copie_objet`
  / `objets_ecrits`) et dans le lot l'état réel des copies objet, et alerter si un import se
  termine alors que le stockage est indisponible.

## A-05 — Interdépendance d'ordre dans la suite e2e du dépôt (scénario « hors ligne »)

* **Nature / gravité** : anomalie de la **suite de tests du dépôt** (elle ne concerne pas le
  produit livré), mineure — mais elle fausse un « rouge » de CI si le mode hors ligne est
  activé en même temps que le reste.
* **Description** : `e2e/hors-ligne.spec.ts` **valide la fiche `7792-SO`** (étape 6 du
  scénario, `bouton-valider`) et **ne restaure pas** son état à la fin. Cinq scénarios
  exécutés après lui dans le même run attendent cette fiche **encore dans la file de
  validation** et échouent alors que le produit fonctionne :
  `parcours-fichiers.spec.ts:37`, `validation.spec.ts:85`, `validation.spec.ts:153`,
  `validation.spec.ts:170`, `validation.spec.ts:238`.
* **Reproduction (mesurée trois fois)** :

  | Passe | Configuration | Résultat |
  | --- | --- | --- |
  | passe 4 | mode live PostgreSQL, hors ligne **désactivé**, worker d'API absent | 57/60 — les 3 échecs sont des imports jamais traités (défaut de montage, corrigé) |
  | passe 5 | + worker en fil activé | 55/60 — les 5 échecs sont ceux décrits ici |
  | passe 6 | base jetable neuve **et** base Redis vidée | 55/60 — **les mêmes 5 échecs** (donc ni état résiduel, ni cache) |
  | passe 7 | mode **nominal du dépôt** (hors ligne non activé) | **59 PASS + 1 ignoré** (rapport écrasé par la passe 8 ; journal `LOGS/e2e_depot_passe7.log`) |
  | passe 8 | scénario hors ligne **seul** (`--grep "hors ligne"`) | **1/1 PASS** (`PREUVES/e2e-rapport_passe8.json`) |
  | passe 9 | rejeu du mode nominal, rapport archivé | **59 PASS + 1 ignoré** (`PREUVES/e2e-rapport_passe9.json`) |

  Le journal de la base e2e de la passe 6 situe la validation de `7792-SO`
  24 s après le début du run — soit pendant le scénario hors ligne, avant les cinq
  scénarios en échec.
* **Preuve** : `PREUVES/e2e-rapport_passe4.json`, `PREUVES/e2e-rapport_passe5.json`,
  `PREUVES/e2e-rapport_passe6.json`, `PREUVES/e2e-rapport_passe8.json` (hors ligne isolé),
  `PREUVES/e2e-rapport_passe9.json` (mode nominal) et les journaux
  `LOGS/e2e_depot_passe{4,5,6,7,8,9}.log`.
* **Impact** : en activant le mode hors ligne dans la suite complète, cinq scénarios
  échouent pour une raison d'ordonnancement des tests (et non de produit) — d'où le risque
  de conclure à tort à une régression, ou à l'inverse de masquer une vraie régression. Le
  scénario hors ligne lui-même (§10) est **vérifié** : 1/1 PASS.
* **Recommandation** : dans `e2e/hors-ligne.spec.ts`, restaurer l'état (`rouvrir` la fiche
  `7792-SO` en fin de scénario) ou utiliser une fiche dédiée au scénario hors ligne ; à
  défaut, exécuter ce scénario dans un run séparé (ce qui est fait ici).

## A-06 — Rejeu d'un dossier déjà traité : 201 avec « deja_traite »

* **Nature / gravité** : comportement **documenté** (`docs/API.md`, ligne de
  `POST /imports/dossier` : « un refus est un **résultat** avec raison …, jamais une erreur
  500 : il est tracé dans son propre lot suivi. 201. »).
* **Description** : le rejeu d'un dossier déjà importé répond `HTTP 201` avec
  `statut="deja_traite"` (et 0 nouveau document). Un intégrateur qui ne teste que le code
  HTTP peut croire à un nouvelle importation.
* **Preuve** : `PREUVES/recette_api.json` → `C3-redepot-meme-dossier-non-duplique` (PASS,
  comportement conforme à la spécification) ; `PREUVES/recette_verif.log` →
  `rejeu-idempotent` (PASS, lot rejoué sans doublon).
* **Recommandation** : aucune correction demandée ; à rappeler dans la documentation
  d'intégration côté atelier (lire `statut`, pas seulement le code HTTP).

## A-07 — Pièces orphelines : document au catalogue dont le fichier source a disparu

* **Nature / gravité** : anomalie du produit, mineure (elle ne concerne que les documents
  importés « en direct » depuis un chemin local, cas d'usage courant à l'atelier : une
  archive partagée peut être réorganisée ou déplacée).
* **Description (mesurée pendant la recette)** : les documents déposés depuis un **chemin
  local** restent référencés par ce chemin (`documents.path`,
  `category='storage_direct'`, `object_key` vide). Si le fichier disparaît ensuite du
  disque, la pièce reste **au catalogue** et l'API répond **404** à son téléchargement,
  sans état « fichier manquant » visible côté interface ni nettoyage de la référence.
  Mesuré ici à 560 documents : le script officiel `scripts/recette_verif.py` **vide son
  répertoire de travail** (`RECETTE_TRAVAIL`) avant chaque exécution, ce qui a supprimé les
  dossiers de travail des autres essais de recette (essais de panne, trois sessions,
  reprises) dont les documents étaient référencés.
* **Reproduction** :
  1. importer un dossier local (`POST /imports/dossier`), puis supprimer un fichier de ce
     dossier (ou relancer `scripts/recette_verif.py`, qui vide son répertoire de travail) ;
  2. `GET /pieces` liste toujours la pièce ; `GET /pieces/{id}/telecharger` → **404**.
* **Attendu / obtenu** : attendu — soit la pièce disparaît du catalogue, soit elle porte un
  état explicite (« fichier source manquant ») et les contrôles d'intégrité la signalent.
  Obtenu — la pièce reste listée sans état particulier et le téléchargement échoue en 404.
  (Le comportement *est* correct au sens « pas de faux 200 » : l'API ne sert jamais un
  contenu absent.)
* **Preuve** : `PREUVES/reparation_copies_locales.json` (560 copies rétablies depuis le
  corpus, 0 irrécupérable, contrôle `copies-locales-retablies`), `PREUVES/verification_telechargements.json`
  (20/20 pièces téléchargées après rétablissement), `LOGS/recette_verif_relance.log`
  (les deux contrôles `pieces-par-id` et `pdf-7-fiches-integrite` en 404 avant réparation).
* **Impact** : après un simple déplacement de l'archive source, l'opérateur voit des pièces
  qu'il ne peut plus ouvrir (404) ; inversement, une exploitation qui supprime des fichiers
  peut laisser le catalogue incohérent — sans alerte.
* **Contournement** : conserver l'archive source en place (c'est la configuration de recette,
  `SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD=false`) ; vérifier périodiquement l'existence des
  chemins référencés (script fourni : `rv/harnais/reparer_copies_locales.py`).
* **Recommandation** : ajouter un état `fichier_manquant` détecté par un contrôle
  périodique (et un compteur dans `/qualite/tableau-de-bord`), ou copier l'objet dans le
  stockage objet à l'ingestion dans tous les cas (`object_key` non vide), afin qu'une pièce
  cataloguée soit toujours servie.
* **Suivi après traitement (2026-10-12)** : les 560 copies locales ont été rétablies et
  l'ensemble du catalogue vérifié — **987/987 chemins présents** (`PREUVES/reparation_copies_locales.json`)
  et **20/20 téléchargements** réussis avec empreinte conforme
  (`PREUVES/verification_telechargements.json`). L'anomalie reste ouverte **en tant que
  défaut de conception** (aucun état « fichier manquant » côté produit) même si, dans cette
  recette, la donnée a été réparée.
* **Cas résiduel requalifié — document 982** (`fiche-genois.pdf`) : lors du contrôle final,
  un document restait sans copie objet ; requalification faite en interrogeant la base et le
  disque :
  * ligne `documents` : `id=982`, `name='fiche-genois.pdf'`, `category='analyzed'`,
    `role='fiche_pdf'`, `extraction_status='metadata'`, `object_key` **vide**,
    `upload_status='pending'`, `id_fiche` **NULL** (aucune fiche liée, aucune ligne dans
    `fiche_piece_jointe`) ;
  * chemin `rv/data/recette-lot/affaires-api/sessions-173519/fiche-genois.pdf` : **le fichier
    est présent** (1 584 octets) ; il appartient aux **141 documents** déposés par les essais
    automatisés sous `data/recette-lot/affaires-api/…` (tous en `upload_status='pending'`,
    jamais copiés vers le stockage objet) et **ne fait pas partie du corpus manifesté**
    (453 fichiers) ;
  * conclusion : **reliquat de l'environnement d'essai**, pas une pièce perdue ni un défaut
    produit ; l'intégrité du corpus (453/453) n'est pas affectée. Le comportement « pièce
    déposée par chemin local jamais copiée vers le stockage objet » reste, lui, couvert par
    l'anomalie A-04/A-07.
