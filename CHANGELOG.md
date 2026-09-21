## Unreleased — Garde-fous de la revue du 21/09 (verrou de calibration, échelle ordinale, dette surface)

- **Verrou de calibration** (Tâche 1a) : `config/seuils_confiance.json` porte `calibre: false`,
  `calibre_le: null`, `fiches_reelles_utilisees: 0` ; `verifier_autorisation_validation_lot()`
  répond « interdit » tant que la calibration n'a pas eu lieu (défaut sûr : fichier absent =
  non calibré), avec acquittement humain explicite prévu — la future `POST /validation/lot`
  (lot D) renverra son message en 409. Testé (`TestVerrouCalibration`) : interdit / acquitté /
  calibré / fichier absent.
- **Échelle ORDINALE, pas probabiliste** (Tâche 1b) : `docs/CONTROLES_RG16.md` §4 — les paliers
  0,99/0,90/0,85 sont des paliers de décision ; le tableau de bord (lot E) affichera des
  **comptes par palier** (`compter_par_palier()`, testé), jamais une « confiance moyenne » ;
  `score_qualite` reste un indicateur brut par fiche, jamais agrégé en moyenne de flotte.
- **Tolérance surface = filtre de grosses erreurs** (Tâche 1c, dette documentée) : §5 du même
  document — resserrage par type de voile dès les fiches réelles, pas avant (le facteur réel de
  la fiche de référence est 0,8655).
- Correctif normcase : le commentaire du test pointe désormais les call-sites exacts
  (`crawler.py:154`, `models.py:56`) sur la branche `fix/test-normcase-basetemp` (`ce44507`).

## Unreleased — Lot B.2 : endpoints de traçabilité et registre de gabarits (`lot-b2/endpoints-tracabilite`)

- Rattrapage du §17.11 (l'écart « pas de route HTTP au Lot B » venait de la consigne, pas du plan) :
  `GET /fiches/{code}/champs` (forme exacte de `fiche_champ_extrait` : valeur brute/normalisée,
  méthode, confiance, page, **zone**, version de gabarit, corrections — l'écran Fiche du lot D
  consommera cette route), `GET /gabarits`, `GET /gabarits/{code}/versions`,
  `POST /gabarits/{code}/versions` (publie max+1, **jamais destructif** : les versions précédentes
  restent consultables, désactivées) et `POST /gabarits/detecter` (détection sur PDF multipart,
  **aucune écriture** ; une non-détection est un résultat « reprise_complete », pas une erreur).
- PostgreSQL uniquement (§17.1) : 503 documenté sans `database_url`. Aucune écriture de fiche par
  ces routes. SQL validés pglast (test étendu au module `routes`).
- Tests : 18 (unitaire sur index simulé, TestClient 503/auth, live PostgreSQL flux complet) ;
  `docs/API.md` créé ; tableau des endpoints du README complété.

## Unreleased — Lot B, corrections de la 2e revue (constats A, B, C) (`lot-b/extraction-fiche`)

- **Constat A — tolérance surface NOMMÉE et FIGÉE** : facteur = SPA/(½·SLU·SLE) accepté dans
  [0,55 ; 1,30] (=[−45 % ; +30 %]), couvrant la formule équilatérale du tableur (√3/4 → 0,866) et le
  triangle quelconque (1,0). Mesure revue : la fiche de référence (SLU 6,60 / SLE 5,50 / SPA 15,71,
  écart −13,4 %) n'est PAS signalée ; SPA 9,0 et 25,4 le sont. Tests figés `TestToleranceSurfaceFigee`,
  documentation `docs/CONTROLES_RG16.md`.
- **Constat B — plafond de confiance corrigé (échelle, pas seuils)** : échelle à paliers
  **0,99 / 0,90 / 0,85** — 0,99 = ancre exacte + valeur intégralement bornée + format entièrement
  consommé (ou texte à borne naturelle) ; troncature par libellé stop → 0,90. Le palier haut dépasse
  le seuil structurel 0,98 : « passage direct » est atteignable par construction (§17.14). Preuves
  testées : champs parfaitement ancrés ≥ 0,98 ; cotes ≥ 0,95 ; reconstruction 7792 en
  `passage_direct` aux seuils réels du dépôt. Les renforts passent en famille « matériaux »
  (objets physiques, pas champs structurels).
- **Constat C — noms de fichiers de test alignés sur le §17.11** : `test_extraction_fiche_reference.py`,
  `test_detection_gabarit.py`, `test_normalisation_unites.py`, `test_anomalies_coherence.py`
  (+ `test_fiches_persistance.py`, `test_fiches_cli.py`, `test_fiches_sql_grammaire.py`).
- **Correctif d'une ligne hors lot** (constat D de revue) : `fix/test-normcase-basetemp` (base main,
  commit `eb3c96a`) — `tests/test_reindex_skip.py` utilise `os.path.normcase` au lieu de `.lower()`,
  preuve : suite verte avec un basetemp à majuscules. Référence de non-régression corrigée :
  **265/9 sur main** (le « 262 » du §17.13 du plan était erroné ; aucune occurrence de « 262 »
  dans le dépôt — vérifié par grep).
- **Delta de tests du Lot B, en une ligne** : 92 tests nouveaux par rapport au correctif
  (422 = 328 + 92, dont 2 ignorés devenus passés une fois le serveur PostgreSQL démarré) ;
  compteur de cette révision : voir section Lot B ci-dessous (les tests du plafond et de la
  tolérance s'ajoutent au delta).

## Unreleased — Lot B : extraction structurée de la fiche technique (`lot-b/extraction-fiche`)

- **Paquet `seamtech_search/fiches/`** — lecture pilotée par gabarit (plan v3.0 §10, §13) :
  `gabarits.py` (registre en base `gabarit`, détection par ancres normalisées, règles JSONB),
  `extraction.py` (pdfplumber : mots, lignes, tableaux réglés ; chaque valeur porte méthode,
  confiance, page et **zone** dans le PDF), `normalisation.py` (6,60 m → 6.600 ; g/m² et gr/m² ;
  mm ; dates françaises), `anomalies.py` (contrôles RG16 indépendants de la confiance),
  `persistance.py` (écriture en UNE transaction, statut `a_valider` — jamais valide, RG11),
  `cli.py` (démo `extraire`/`ecrire`/`init`/`banc`).
- **Deux gabarits embarqués**, issus du vocabulaire réel des fiches disponibles :
  `FICHE_PORTANT_V1` (réf. 7792-SO, tableau réglé de cotes) et `FICHE_GENOIS_V1`
  (mono-colonne) — le génois est le test de généralisation ; une fiche hors gabarits
  part en reprise complète avec conservation des libellés connus (RG6).
- **Mesure** : banc hérité `validate_extraction --verite` muni de `--moteur gabarit` :
  **16,7 % (1/6) → 100 % (6/6)** sur la vérité 7792 (champs hérités) ; le banc
  `gabarit_test` (vérité §13 en base, 31 champs nommés dont cotes SLU/SLE/SF/SHW/SPA,
  galons, jonctions, finitions, options, renforts) tourne à 100 % via le CLI `banc`.
  Seuils `config/seuils_confiance.json` désormais **consommés** pour le routage
  (passage direct / relecture ciblée / reprise complète) — points de départ, à calibrer
  sur fiches réelles (Tâche 3).
- **Écritures** : fiche, fiche_cotes (jeu finie), fiche_materiau (épaisseurs 01→10),
  fiche_galon (guindant/chute/bordure), fiche_jonction (laizes, horizontale, verticale,
  surplus), fiche_finition, fiche_option, fiche_renfort, fiche_champ_extrait (valeur brute,
  normalisée, méthode, confiance, page, zone, version de gabarit), fiche_mesure_libre,
  fiche_anomalie ; référentiels client/bateau/type_voile/materiau résolus ou créés.
  PostgreSQL uniquement (§17.1). Aucune dépendance nouvelle.
- **Jeux de cotes et rôles complets** (complément de périmètre) : le JEU d'une cote vient de sa
  cible de gabarit (`cotes.dessin.*` autant que `cotes.finie.*` — le gabarit 7792 ne lit que
  « finies », la fiche n'imprimant qu'elles) ; `fiche_materiau` alimente le **tissu principal**
  quand le gabarit désigne l'ancre comme matériau réel (démontré sur le génois : « Tissu :
  Dacron 260 » → `fiche_materiau` rôle `tissu_principal`, la fiche 7792 gardant « Tissu(s) » en
  texte libre — son contenu n'est pas un matériau). Le rôle `cache_insignia` est prêt au même
  titre, sans donnée dans les fiches disponibles.
- **Tests** : 51 unitaires extraction/normalisation/RG16/routage, 12 live PostgreSQL
  (transaction, idempotence RG11, fiche validée jamais écrasée), grammaire pglast de
  chaque écriture SQL, banc moteur gabarit bout-en-bout. Branché sur Lot A (dépendance
  documentée dans le message du commit de fusion).

# Changelog

## Unreleased — Correctifs de revue, constats 1, 2 et 3

Branche `lot-a/fix-privileges-et-lexique`. Le constat 1 (privilèges PostgreSQL) est corrigé
directement sur `lot-a/schema-metier` (seul endroit où `schema_metier.py` existe) — voir son
CHANGELOG. L'ordre de fusion PR 1 → préparation → correctif → Lot A est inchangé.

- **Constat 2 — fiches mono-colonne** : deuxième voie d'admission
  `candidat = (nb_termes ≥ vocabulaire_fort) OU (nb_termes ≥ vocabulaire_min ET structure détectée)`,
  seuil `vocabulaire_fort` (défaut 5) lu depuis le lexique JSON (cohérence fort ≥ min validée au
  chargement). Le motif d'exclusion nomme l'échec de CHACUNE des deux voies. Une fiche
  « Libellé : valeur » riche (15 termes, une seule colonne détectée) est désormais candidate ; une
  fiche mono-colonne à 2 termes reste exclue, avec l'explication des deux échecs.
- **Constat 3 — singulier/pluriel** : tolérance BIDIRECTIONNELLE sur les termes mono-mots
  (« jonction » du lexique trouve « Jonctions », « epaisseurs » du lexique trouve « Epaisseur 01 » —
  le cas exact de la revue) ; expressions multi-mots toujours exactes ; variante « grand voile »
  (sans trait d'union) ajoutée au lexique, l'entrée avec trait d'union continuant de matcher.
  Mesure rejouée sur la reconstruction 7792 : **30/45 → 31/45 termes** (gain « Jonctions
  horizontales » ; la reconstruction imprime les pluriels — les formes singulières de la fiche
  réelle sont couvertes par les tests unitaires des deux sens).
- **Points mineurs** : le rapport d'inventaire n'embarque plus de chemin absolu du lexique
  (chemin relatif au dépôt + empreinte SHA-256 du fichier — deux machines produisent des rapports
  comparables) ; `docs/STRUCTURE.md` liste `detection_fiches.py` et `lexique.py`
  (`schema_metier.py` documenté sur la branche Lot A) ; `/health` — `extensions.applicables`
  aligné côté PostgreSQL (fait sur Lot A).
- **9 nouveaux tests** ; suite complète : 328 passés / 9 ignorés ; ruff propre ; aucune dépendance.

## Unreleased — Préparation du lot B (`phase0/preparation-lot-b`)

- **Modèle de vérité terrain** (`docs/verite_terrain/modele_verite_terrain.json`) — documenté, prêt à
  remplir dès réception des fiches réelles (format du mode `--verite`).
- **Contrôle de format** dans `charger_verite` : refuse un fichier incomplet — placeholders (« ... »,
  « à remplir », « todo », « ? », « x »), `attendu` vide ou tout-null. Un rapport de calibration bâti
  sur une vérité partielle serait trompeur. Les clés `_documentation` sont ignorées (métadonnées).
- **Fiche de référence n°2** — `docs/verite_terrain/7792-SO_ffab.json` : valeurs attendues du §13 du
  plan v3.0 pour la fixture `sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf` (reconstruction ; le
  PDF réel la remplacera sans changer le JSON). Mesure baseline au banc (`--verite`) :
  **16,7 % de champs corrects (1/6), 84 ms** — la justification chiffrée du lot B (les motifs hérités
  lisent la quantité, ratent la référence réelle, bavent sur la matière, ignorent les cotes nommées).
- **`config/seuils_confiance.json`** — seuils de passage direct par famille du §10.3 (structurels
  0,98 ; cotes 0,95 + contrôle croisé RG16 ; matériaux 0,85 ; finitions/options 0,80 ; notes 0,50),
  routage des trois voies. État honnête : PRÉPARÉ, NON CONSOMMÉ — le lot B les chargera (variable
  `SEAMTECH_SEUILS_CONFIANCE`) et les calibrera sur fiches réelles.
- **6 nouveaux tests** (contrôle de format, template refusé, vérité 7792 mesurable de bout en bout).

## Unreleased — Phase 0 (inventaire de l'archive & banc d'essai d'extraction)

Branche `phase0/inventaire-banc-essai` sur `4efe2ad`. Rapport complet : `docs/PHASE0_RAPPORT.md`.

### PR 1 — Correctif du classement des fiches (`phase0/fix-classement-fiches`)

- **Détection structurelle (`seamtech_search/detection_fiches.py`, nouveau)** — un PDF est « candidat
  fiche » s'il combine assez de vocabulaire de cotes (lexique) ET une structure de tableau (grille
  tracée vue par pdfplumber ou grille inférée des positions). Verdict expliqué : termes, colonnes,
  lignes, grille, score pondéré, motif d'exclusion. Scans et non-PDF exclus.
- **Lexique configurable (`config/lexique_fiches.json`, nouveau ; `seamtech_search/lexique.py`)** — le
  vocabulaire n'est plus codé en dur ; ajout à chaud sans redéploiement (`--lexique` pour un chemin
  alternatif), échec bruyant si le fichier est absent/invalide. Vocabulaire de départ bâti sur les
  fiches disponibles (fixture CLIENT-123, reconstruction 7792-SO §13, fiche génois).
- **Double vue de l'inventaire** — `scripts/inventaire_archive.py` rapporte (a) la classification
  actuelle, (b) la détection structurelle, plus la section `desaccords` (documents ratés par le
  classifieur actuel ; fiches sans structure détectable). CSV : colonnes `candidat_fiche`, `score_fiche`.
- **Empreintes de gabarit en positions relatives à la page** (pas de 2 %) quand les dimensions sont
  connues : un même gabarit sur deux formats de page reste dans la même famille.
- **Fixtures** — `sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf` (reconstruction des valeurs §13 du
  plan, documentée comme telle) et `sample_data/CLIENT-GENOA/fiche-genois.pdf` (reproduction du point
  mort : classée `plan_pdf` par le classifieur, candidate par la détection).
- **15 nouveaux tests** (`tests/test_detection_fiches.py`) ; suite complète 313 passés / 9 ignorés ;
  l'archive reste rigoureusement inchangée (empreintes avant/après). Doc : `docs/DETECTION_FICHES.md`.

- **`scripts/inventaire_archive.py` (nouveau)** — inventaire en lecture seule d'une arborescence :
  comptages, volumes par type et par année (mtime), doublons probables (taille + SHA-256, plafonnés par
  `--limite-empreinte`), part de scans probables (marqueur « no embedded text » de l'extracteur, OCR
  désactivé), localisation probable des fiches techniques (classifieur d'ancres existant), familles de
  gabarits par empreinte de libellés alphabétiques + positions quantifiées (regroupement exact puis
  fusion Jaccard ≥ 0,85). Sorties : console + `inventaire.json` + 2 CSV (`;`, utf-8-sig). Refus d'écrire
  le rapport dans une racine analysée. Aucune dépendance ajoutée, aucun appel réseau.
- **`scripts/validate_extraction.py` (étendu, rétrocompatible)** — mode `--verite` : mesure champ par
  champ du taux de lecture correcte contre un JSON de vérité terrain, verdicts OK/ECART/MANQUANT/
  SUSPECT/INATTENDU/OK_ABSENCE, agrégats par champ / par gabarit / global, temps par fiche, export JSON
  (`--sortie-json`) et garde (`--seuil`). Cotes comparées en mm (tolérance 1 mm ou 0,1 %). Réalise le
  `benchmark_gabarit.py` prévu au plan v3.0 §17.8 comme mode du harnais existant. Le mode historique
  (revue par document) est inchangé et toujours épinglé par ses tests.
- **Tests** — `tests/test_inventaire_archive.py` (14) prouve la non-modification de l'archive par
  empreintes avant/après + 12 erreurs d'usage couvertes ; `tests/test_validate_extraction_mesure.py` (19)
  épingle verdicts, tolérances, agrégats, gardes et refus de sortie. Suite complète : 298 passés /
  9 ignorés (265 préexistants restants verts), ruff propre, pip-audit propre sur les requirements,
  gate de couverture atteint.
- **Constat mesuré (lot B)** — les fiches dont les libellés sortent du vocabulaire d'ancres actuel
  (`Guindant`, `Bordure`, `Tissu`…) sont classées `plan_pdf` aujourd'hui : `TECHNICAL_ANCHORS` devra être
  étendu sur fiches réelles ; documenté dans README et `docs/PHASE0_RAPPORT.md`.

## Unreleased — Lot A : schéma métier & migrations 006-009 (`lot-a/schema-metier`)

- **Image PostgreSQL → `pgvector/pgvector:pg16`** (docker-compose + service PostgreSQL du job
  d'intégration CI). `unaccent` et la configuration `seamtech_unaccent` (migration 005) restent
  disponibles dans cette image — vérifié par les tests live (`to_tsvector('seamtech_unaccent', …)`
  toujours opérationnel après migrations).
- **`seamtech_search/schema_metier.py` (nouveau)** — SQL des quatre migrations, transcrit du §6.2 du
  plan v3.0, rendu idempotent (`IF NOT EXISTS` partout) :
  - `006_fiche_technique` : extension `vector` ; référentiels (client, bateau, type_voile, materiau,
    utilisateur, gabarit) ; commande, fiche (+index), fiche_cotes, fiche_materiau, fiche_galon,
    fiche_jonction, fiche_finition, fiche_option, fiche_renfort, fiche_mesure_libre, fiche_lien,
    fiche_champ_extrait (+index partiel corrige), fiche_validation, fiche_anomalie, chunk (embedding
    `vector(384)`, tsv généré sur `seamtech_unaccent`) ; extension de `documents` (id_fiche, role,
    embedding, traite_le) ; vue `v_fiche_recherche`. 21 tables.
  - `007_recherche_index` : extension `pg_trgm` ; `fiche.champs_texte` + `fiche.search_vector` ; index
    GIN plein-texte et trigrammes (code, titre, champs_texte) ; fonction
    `rafraichir_texte_recherche_fiche(BIGINT)` (pondération A=code+titre, B=référentiels et champs
    structurés, C=notes — appelée à la validation d'une fiche, jamais en boucle) ; synonyme ;
    recherche_log.
  - `008_ml_corpus` : ml_modele, ml_exemple (origine synthetique|reel), ml_run. Aucun modèle binaire
    en base : seul le chemin du fichier est stocké.
  - `009_qualite_et_gabarits` : gabarit_test (valeurs attendues en JSONB) ; vue `v_qualite` (passage
    direct, corrections, validations) ; reprise idempotente des index fiche(statut) et
    fiche_champ_extrait(corrige).
- **Décision actée dans le code (§17.1)** : couche métier PostgreSQL uniquement — sur SQLite,
  006-009 ne font rien (warning + migration enregistrée). Commentaire en tête de module et sur chaque
  migration pour éviter toute « restauration de parité SQLite ». Test de décision
  `test_sqlite_ne_recoit_pas_la_couche_metier`.
- **Tout le SQL validé par pglast** : le harnais `tests/test_postgres_sql_grammar.py` parcourt
  `run_migrations()` et parse chaque émission avec libpg_query — les quatre nouveaux scripts sont
  couverts automatiquement.
- **/health enrichi** : `schema_migrations` (versions appliquées), `schema_metier_a_jour`, et présence
  EFFECTIVE des extensions (`vector`, `pg_trgm`, `unaccent` via `pg_extension`). Le diagnostic échoué
  dégrade la réponse (warning journalisé), jamais le service.
- **Tests** — `tests/test_migrations_metier.py` (8, marqueur `postgres`, base jetable par test) :
  base vide → 27 tables métier créées ; idempotence (rejeu sans effet) ; capacités réelles
  (`SELECT '[1,2,3]'::vector`, similarité pg_trgm, `seamtech_unaccent`) ; insertion fiche +
  `v_fiche_recherche` + `v_qualite` + fonction 007 ; `/health` ; démarrage réel de l'application
  (TestClient, `/ready` + `/health`) sur base métier ; mesure de taille. Le tout-SQLite reste vert.
- **Mesures (PostgreSQL 17.11 + pgvector 0.8, serveur local — la CI rejoue sur l'image pg16)** :
  migrations 006-009 sur base vide : **0,09 s** ; schéma métier créé (tables vides, index inclus) :
  **~728 ko** ; suite live `-m postgres` : 13 passés.
- Aucune dépendance Python ajoutée (pgvector et pg_trgm sont des extensions PostgreSQL).

### Constat 1 de revue — privilèges PostgreSQL (correctif appliqué sur cette branche)

- La configuration de recherche n'est plus référencée en dur dans le DDL : marqueur `__TS_CONFIG__`
  injecté au moment de la migration avec la configuration EFFECTIVE (`seamtech_unaccent`, repli
  `simple` — même dégradation gracieuse que la migration 005). Vise `chunk.tsv` (006) et la fonction
  de rafraîchissement (007). Un rôle sans privilège ne bloque donc PLUS le démarrage sur ce point.
- `vector` (extension non « trusted ») restant obligatoire, son échec de création reste fatal mais
  porte désormais un message actionnable (image pgvector/pgvector:pg16 ou préinstallation par
  l'administrateur) — testé contre un rôle réellement non superutilisateur.
- `pg_trgm` (« trusted » mais exigeant CREATE sur la base) : les index trigrammes deviennent
  dégradables — cœur de 007 appliqué, index omis avec avertissement et conséquence journalisés.
- `/health` : clé `extensions.applicables` ajoutée côté PostgreSQL (symétrie avec la branche SQLite).
- Nouveaux tests : échec actionnable (live), migrations passant avec rôle limité + vector
  préinstallé (live), injection `simple` validée pglast (unitaire). Doc : `docs/DEPLOYMENT.md`
  (section « Privilèges PostgreSQL requis par la couche métier »).
 lot-a/schema-metier

## 0.5.0 — Remediation (audited commit b7be72a → fixes)

Audited commit `b7be72a` had data-loss, security, and doc-honesty defects. This release fixes them in audit order, verified by `ruff check . && pytest -k "not postgres and not s3"`.

### Phase 1 — Data-loss bugs (blocking)

- **1.1 Purge gate:** `worker.py` now purges `staging_root` only when `upload_status == uploaded` and `all_verified` (head_object verified) and every file has object_key. Otherwise marks `upload_incomplete`, moves to `quarantine/` (never pruned), UI surfaces status. Tests: upload-fails keeps files, upload-succeeds purges, partial keeps everything (see VERIFICATION).
- **1.2 Collision-free keys:** `storage.py:artifact_object_key` → `{prefix}/{import_id}/{sha256(relative_path)}/{filename}` preserving internal structure. `first_free_key` appends `-2`, `-3` if occupied. `put_bucket_versioning` called at bucket creation, wrapped try/except for R2 (no versioning). `versioning_status()` reports `versioning_available: true/false/None`, cached 60s, read-only probe, `/health` includes it.
- **1.3 Persist object keys:** `documents` table adds `object_key`, `object_bucket`, `uploaded_at`, `upload_status`. `upload_artifacts_to_storage` returns `UploadBatch` with `list[UploadedArtifact]` (path, key, bucket, status, verified, error) persisted per-file. `retry_upload` retries every file where `upload_status != uploaded` (was only technical PDF + reports + Excel, now includes .xin, .PLX, plan PDFs).
- **1.4 Health read-only:** Moved DDL/backfill out of `initialize()` into versioned `schema_migrations` table (`run_migrations()` runs once at startup, never from request handler). Removed `index.initialize()` from `/health`. Postgres backfill now guarded `WHERE category IS NULL OR ''`, not overwriting `technical_pdf`/`plan_pdf`. Test: call `/health` 3× against Postgres, assert category unchanged.
- **1.5 Duplicate import id:** `_save_import` uses `INSERT ... ON CONFLICT (id) DO UPDATE` (Postgres) / `INSERT OR REPLACE` (SQLite), idempotent for given import_id.

### Phase 2 — Downloadable reports

- **2.1 Download endpoint:** `GET /imports/{id}/artifacts/{artifact}` where artifact ∈ {report_pdf, report_docx, source_pdf, source_excel} → 302 to presigned URL (900s) or FileResponse from disk, fallback downloads from S3 if cache cold. Auth-gated, rate-limited, audit-logged. Next.js proxy route + real download buttons in `import-panel.tsx`. E2E: import sample, click buttons, assert non-empty MIME.
- **2.2 Reports source of truth:** Object storage is source of truth, local `data/reports/<id>/` is cache only. Serving falls back to S3 download when cache cold.
- **2.3 /open:** Replaced `os.startfile` (Windows-only, 501 on Linux) with presigned URL redirect if object_key known, else FileResponse or dir JSON. Frontend `/api/open` updated, no Windows host mention.

### Phase 3 — Security

- **3.1 Token compare:** Uses `secrets.compare_digest` constant-time.
- **3.2 Docs auth:** `docs_url=None, redoc_url=None, openapi_url=None` when `auth_token` set. Removed from rate-limiter exempt.
- **3.3 Vercel Analytics:** Removed `@vercel/analytics` from `package.json` and `layout.tsx`, removed `generator: v0.app`, renamed package to `seamtech-search-frontend`.
- **3.4 Sample fallback:** Gated on `SEAMTECH_DEMO_MODE=1`, impossible when `NODE_ENV === production` → 503 with clear message. `/health` tags demo with `sample: true`.
- **3.5 Container hardening:** Dockerfile adds non-root `seamtech` user, `HEALTHCHECK` hitting `/live`, drops `config/` copy, splits test deps to `requirements-dev.txt` (no pytest/httpx in prod image).
- **3.6 Config footguns:** MinIO creds mandatory `:?`, Redis `requirepass` set and in URL, `BEHIND_TLS_PROXY` is `false` in app config — compose sets `true` for the web service because the documented deployment sits behind a TLS terminator and web must bind `0.0.0.0` (the backend refuses non-loopback bind + token + `false`), adds commented Caddy reverse proxy service, `config.example.json` uses Linux path `/data/SEAMTECH/DesignFiles` no hardcoded minioadmin, adds `SEAMTECH_ROOT_PATHS` env override (colon/comma), `default_config_path()` fails loudly if `config.json` missing, `restart: unless-stopped` everywhere.

### Phase 4 — Correctness

- **4.1 Search parity:** SQLite FTS5 OR + `*` prefix, Postgres now `to_tsquery` OR prefix `"voile:* | bleue:*"` with rank boost for AND `"voile:* & bleue:*"` + `ts_rank_cd + 0.5`. Identical result ordering. Uses `simple` config (no French stemming) documented.
- **4.2 Health integrity:** `health_details` Postgres branch now runs real checks: `COUNT(*) FROM documents`, `pg_indexes`, `pg_index.indisvalid`, returns `ok`/`degraded`/`invalid_indexes:N`/`check_failed`.
- **4.3 Retention path:** Fixed `staging_root` vs `staging_uploads` mismatch — now uses `staging_root()` (`data/uploads`). `quarantine/` preserved. Added daily asyncio scheduler (60s after startup, then 86400s) + manual `/maintenance/cleanup`. Cadence documented.
- **4.4 Background task GC:** `asyncio.create_task` references kept in `background_tasks` set with discard callback.
- **4.5 Cancellation distributed:** Cancel flag moved to Redis `seamtech:cancel:{id}` with in-memory fallback, `is_job_cancelled` checks Redis first.
- **4.6 Queue ack:** `dequeue_task` uses `BLMOVE queue→processing` with `BLPOP` fallback, `ack_task` removes by job_id JSON match, `retry_task` uses `seamtech:retry:<queue>` sorted set exponential backoff `2**attempt`, `seamtech:deadletter:<queue>` list after 3 attempts, `upload_dead_letters` in `/health`, endpoints `/maintenance/deadletters` + `/maintenance/replay-deadletters`.
- **4.7 Stale recovery scoped:** `recover_stale_jobs(heartbeat_threshold_seconds=300)` only marks jobs where `updated_at < now()-interval`, plus worker heartbeat via `set_heartbeat` in `progress_cb`.
- **4.8 Classifier:** Stricter — `STRONG_ANCHORS = fiche de fabrication, mesures finies, mesures dessin, cotes`. Rule: strong present → need ≥2 total, else need ≥3 total. `scan_folder` now returns **all PDFs** with `anchor_count`, `anchors_matched`, `classification`, `is_technical` ranking hint, sorted technical first then anchor_count desc.
- **4.9 Double extraction:** `import_folder` caches extractions by path during initial walk, reuses for technical_pdf and extra_pdfs, avoiding 2N extraction.
- **4.10 Smaller:** `request_timestamps` swept each request (cutoff 60s) to prevent unbounded growth, `/imports/upload` aggregate cap 10× single file + free-space re-check while writing, `update_job` checks rowcount returns None if missing, `read_import` DB-first to avoid stale Redis cache shadowing after PATCH (invalidates via `update_job` on write), `scan_snapshot` docstring documents O(N) full copy limit.

### Phase 5 — Testing (gaps)

**Met.** Coverage: 89% overall (SQLite + mocked-postgres selection, `-k "not s3"`), api 87%, import_pipeline 90%, indexer 90%, jobs 94%, redis_store 92%, storage 97%, worker 92%. Enforced in CI by `scripts/coverage_gate.py`, which reads `coverage.json` and exits 1 when the overall 85% floor or any per-module threshold is breached — the gate logic is itself unit-tested (`tests/test_coverage_gate.py`, including the one-decimal rounding boundary).

Chaos tests (`tests/test_chaos.py`) with assertions that can actually fail: S3 down mid-import (job `upload_incomplete`, all artifacts failed, source quarantined byte-for-byte), Redis killed mid-job (stale recovery, exact error message), worker **SIGKILLed as a real subprocess** (`os.kill(pid, SIGKILL)`, exit code -9, job stuck in `running`, recovered exactly once on restart, files preserved), disk full (507 + `InsufficientStorageError`, no purge), S3 versioning unavailable (R2), Redis rate-limit fallback.

Docker compose integration test (`tests/test_integration_docker.py`) with strict mode in CI (`SEAMTECH_INTEGRATION_STRICT=1`): backends that CI health-checked must actually work.

CI integrity — every check can now fail (no `|| echo` masking anywhere):
- Coverage gate is the real script above (was a heredoc that loaded coverage and printed a static "passed" message).
- `pip-audit` (full environment) and `pnpm audit --prod --audit-level=high` fail the build on findings; the vulnerable deps they exposed were upgraded (see Phase 3 addendum below) instead of being ignored.
- Docker smoke test requires the container to start and `/live` to answer (was `docker ps | grep || echo`).
- Integration job waits up to 300s for Postgres/Redis/MinIO to be genuinely reachable using the app's own clients, runs pytest without swallowing failures, always tears the infra down.
- `docker-compose.yml` minio healthcheck was a no-op (`python3` does not exist in the minio image, `|| exit 0` hid it); now a real `wget` probe of `/minio/health/live`.
- `config/config.json` is seeded from `config.example.json` in the Docker image — the server refuses to start without it (by design) and the image never shipped one, so the container always crashed at boot (hidden by the old smoke test).
- CLI no longer crashes at parse time when `config/config.json` is absent (`default_config_path()` was resolved eagerly for the argparse default, killing every `--config` invocation too — this is what took the e2e backend down in CI); the file check happens at load time with the same clear error.
- `httpx==0.28.1` restored to `requirements.txt` (it had been dropped, so the SQLite/API suite could not run in CI at all).
- `frontend/package.json` pins `packageManager: pnpm@9.15.9` so the Docker build (corepack) uses the same pnpm as CI — pnpm ≥10 ignores `pnpm.overrides` in `package.json`, which broke the frozen install.
- `shadcn` moved to devDependencies (code-gen CLI, not shipped); `next` 16.3.3→16.3.5; patch-level overrides for `nanoid`/`browserslist`/`baseline-browser-mapping` (next's transitive CVEs). `pnpm audit --prod`: no known vulnerabilities.
- `minio/minio` repointed to `quay.io/minio/minio` — MinIO removed its images from Docker Hub on 2026-09-11, so `docker compose up` failed with a misleading "pull access denied / docker login" (the repository is simply gone; quay.io is MinIO's current official distribution, same tags).
- Docker smoke test now sets `SEAMTECH_ALLOW_NETWORK_ACCESS=true` and `SEAMTECH_BEHIND_TLS_PROXY=true`: it binds `0.0.0.0` (the port mapping needs it), which trips the app's network-exposure config guards — the container exited at startup with "non-local host requires allow_network_access=true".

Phase 3 addendum (security): fastapi 0.116.1→0.141.1 (starlette 0.47.3→1.6.0 — Host-header auth-bypass PYSEC-2026-161 + Range ReDoS), pypdf 5.8.0→6.19.0, python-multipart 0.0.20→0.0.32 (path traversal + DoS), pytest 8.4.1→9.1.1 — so `pip-audit` can pass honestly.

### Phase 6 — Documentation honesty

Previous README claimed "Redis 7 Cluster" (single), "Pipeline Worker Daemon" separate (thread), "Append-Only Audit Logging / Immutable" (regular table pruned), "Scratch purged upon upload… no customer data is lost" (purged on failed too), "Download links via presigned URLs" (no endpoint), "direct presigned download links" in UI (printed "PDF + Word"), "146 passed / dead-letter / upload_dead_letters / artifacts 302 / compose sets env on both web and worker" (none existed at b7be72a). Rewritten to verified facts, limits stated (R2 no versioning, no separate worker service, single-user no RBAC).

---

## 0.4.0 — Decoupled Cloud-Native (pre-audit, aspirational)

- S3/MinIO/R2 client, Redis queue, Postgres, multi-PDF extraction, dual reports, audit logging, rate limiting. Docs were aspirational, not verified. See 0.5.0 for fixes.

## 0.3.0 — Import workflow

- Shared anchors, pdfplumber, unit normalization, two-phase scan/confirm, Word reports, browser upload staging.

## 0.2.0 — Search & crawling

- Recursive crawler, FTS5, FastAPI, Next.js search UI.

## 0.1.0 — Init

- Project scaffold.
