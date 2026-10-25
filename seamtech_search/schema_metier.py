"""Schéma métier « fiche technique » — migrations 006 à 028 (Lots A → Lot 3a).

DÉCISION DE COUCHE ACTÉE (plan v3.0 §17.1) : la couche métier est
**PostgreSQL uniquement**. Elle s'appuie sur pgvector, pg_trgm, les index GIN,
les vues et le JSONB — aucune variante SQLite n'est maintenue pour les fiches.
Sur SQLite, les migrations 006-009 ne font rien (voir
``SearchIndex._migration_006_fiche_technique``) : le mode SQLite reste supporté
uniquement pour l'indexation de fichiers héritée. Ne pas « rétablir la parité
SQLite » : c'est une décision assumée, pas un oubli.

Le SQL est transcrit du §6.2 du plan v3.0, rendu idempotent
(``IF NOT EXISTS`` partout) pour qu'un rejeu après un échec partiel ne
casse rien. Chaque script est exécuté UNE fois par base via le mécanisme
standard (``SearchIndex.run_migrations``, table ``schema_migrations``) :
jamais depuis un gestionnaire de requête.

Prérequis d'ordre : la migration 006 crée ``chunk`` dont la colonne générée
``tsv`` référence la configuration ``seamtech_unaccent`` (migration 005) —
l'appelant garantit que ``initialize()`` (qui installe cette configuration)
a été exécuté avant ``run_migrations()``. C'est l'ordre du démarrage standard
(``api._initialize_schema``) et celui des tests d'intégration.
"""

from __future__ import annotations

from typing import Any

# Version du schéma métier — incrémentée à chaque nouvelle migration.
VERSION_SCHEMA_METIER = "028_sail_extractions"

# Marqueur injecté par le code au moment de la migration (constat 1 de revue) :
# le nom de la configuration de recherche effective — 'seamtech_unaccent' ou
# 'simple' en repli — résolu via SearchIndex._postgres_ts_config(). Le SQL ne
# doit JAMAIS référencer la configuration en dur.
MARQUEUR_TS_CONFIG = "__TS_CONFIG__"

# Tables créées par les migrations 006-009 (contrôlées par les tests de schéma
# et exposées en agrégat par le diagnostic /health).
TABLES_METIER: tuple[str, ...] = (
    # 006 — référentiels
    "client",
    "bateau",
    "type_voile",
    "materiau",
    "utilisateur",
    "gabarit",
    # 006 — affaire et fiche
    "commande",
    "fiche",
    "fiche_cotes",
    "fiche_materiau",
    "fiche_galon",
    "fiche_jonction",
    "fiche_finition",
    "fiche_option",
    "fiche_renfort",
    "fiche_mesure_libre",
    "fiche_lien",
    "fiche_champ_extrait",
    "fiche_validation",
    "fiche_anomalie",
    "chunk",
    # 007 — recherche
    "synonyme",
    "recherche_log",
    # 008 — corpus ML
    "ml_modele",
    "ml_exemple",
    "ml_run",
    # 009 — qualité
    "gabarit_test",
    # 010 — ingestion & lots (Lot C) : 30 tables métier (27 du Lot A + 3)
    "lot_import",
    "lot_dossier",
    "fiche_piece_jointe",
    # 015 — Lot K : brouillons gabarits
    "gabarit_brouillon",
    # 016 — Lot L.2 : sessions nominatives. La table `utilisateur` existait déjà
    # (006) et n'est donc PAS recomptée ici ; `session_ui` est la 32e.
    "session_ui",
    # 017 — Lot M : staging OCR étage 3 (texte OCR par page, jamais dans chunk/document)
    "ocr_etage3",
    # 025 — Lot 0b : journal d'audit canonique, append-only et chaîné par
    # empreinte. La table héritée `audit_log` reste le substrat de
    # compatibilité (SQLite, verrouillage des connexions), mais ce n'est plus
    # la source d'autorité en PostgreSQL.
    "audit_logs",
    # 026 — Lot 2 : référentiel canonique unique des modèles de bateaux,
    # alias réversibles/historisés et synonymes de recherche versionnés.
    # La table historique `bateau` reste le libellé source ; elle reçoit un
    # lien nullable vers `boat_models`, jamais une réécriture destructive.
    "boat_models",
    "boat_model_aliases",
    "boat_model_merge_history",
    "search_synonyms",
    # 027 — Lot 1 : demandes métier soumises à décision d'un approbateur et
    # notifications applicatives instantanées. Aucun e-mail ni service externe :
    # la notification est une ligne PostgreSQL relue par l'interface locale.
    "approval_requests",
    "in_app_notifications",
    # 028 — Lot 3a : extraction de voile normalisée et traçable. Le modèle de
    # bateau y reste un libellé BRUT non résolu ; la résolution vers le
    # référentiel canonique appartient au Lot 3b.
    "sail_extractions",
)

SQL_006_FICHE_TECHNIQUE = """
-- ============================================================================
-- 006_fiche_technique — couche métier « fiche technique » (plan v3.0 §6.2)
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- Référentiels ----------
CREATE TABLE IF NOT EXISTS client (
    id_client      BIGSERIAL PRIMARY KEY,
    nom            TEXT NOT NULL,
    chantier       TEXT,
    contact        TEXT,
    email          TEXT,
    telephone      TEXT,
    actif          BOOLEAN NOT NULL DEFAULT true,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (nom, chantier)
);

CREATE TABLE IF NOT EXISTS bateau (
    id_bateau      BIGSERIAL PRIMARY KEY,
    nom            TEXT NOT NULL,
    taille         TEXT,
    classe         TEXT,
    id_client      BIGINT REFERENCES client(id_client) ON DELETE SET NULL,
    UNIQUE (nom, taille)
);

CREATE TABLE IF NOT EXISTS type_voile (
    id_type_voile  BIGSERIAL PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,
    libelle        TEXT NOT NULL,
    famille        TEXT,
    sous_type      TEXT
);

CREATE TABLE IF NOT EXISTS materiau (
    id_materiau    BIGSERIAL PRIMARY KEY,
    nom            TEXT NOT NULL UNIQUE,
    famille        TEXT,
    grammage_g_m2  NUMERIC(7,1),
    fournisseur    TEXT
);

CREATE TABLE IF NOT EXISTS utilisateur (
    id_utilisateur BIGSERIAL PRIMARY KEY,
    identifiant    TEXT NOT NULL UNIQUE,
    nom            TEXT,
    role           TEXT NOT NULL DEFAULT 'operateur',
    actif          BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Registre des gabarits d'extraction ----------
CREATE TABLE IF NOT EXISTS gabarit (
    id_gabarit       BIGSERIAL PRIMARY KEY,
    code             TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1,
    description      TEXT,
    regles           JSONB NOT NULL DEFAULT '{}'::jsonb,
    ancres_detection TEXT[] NOT NULL DEFAULT '{}',
    actif            BOOLEAN NOT NULL DEFAULT true,
    nb_fiches        INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (code, version)
);

-- ---------- Affaire ----------
CREATE TABLE IF NOT EXISTS commande (
    id_commande    BIGSERIAL PRIMARY KEY,
    numero         TEXT NOT NULL UNIQUE,
    id_client      BIGINT REFERENCES client(id_client) ON DELETE SET NULL,
    date_commande  DATE,
    quantite       INTEGER,
    statut         TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fiche (
    id_fiche          BIGSERIAL PRIMARY KEY,
    code              TEXT NOT NULL,
    titre             TEXT,
    id_type_voile     BIGINT REFERENCES type_voile(id_type_voile) ON DELETE SET NULL,
    gamme             TEXT,
    segment           TEXT,
    atelier           TEXT,
    id_bateau         BIGINT REFERENCES bateau(id_bateau) ON DELETE SET NULL,
    id_client         BIGINT REFERENCES client(id_client) ON DELETE SET NULL,
    id_commande       BIGINT REFERENCES commande(id_commande) ON DELETE SET NULL,
    quantite          INTEGER DEFAULT 1,
    tissu_texte       TEXT,
    montage_type      TEXT,
    montage_fil       TEXT,
    cible_code        TEXT,
    cible_grammage    TEXT,
    cible_rayon_mm    NUMERIC(7,1),
    distance_cosse_mm NUMERIC(7,1),
    logo              TEXT,
    notes             TEXT,
    dessinateur       TEXT,
    date_dessin       DATE,
    date_edition      DATE,
    fichier_source    TEXT,
    id_gabarit        BIGINT REFERENCES gabarit(id_gabarit) ON DELETE SET NULL,
    id_document       BIGINT,
    statut            TEXT NOT NULL DEFAULT 'importe',
    score_qualite     NUMERIC(4,3),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (code)
);

CREATE INDEX IF NOT EXISTS idx_fiche_statut    ON fiche(statut);
CREATE INDEX IF NOT EXISTS idx_fiche_type      ON fiche(id_type_voile);
CREATE INDEX IF NOT EXISTS idx_fiche_client    ON fiche(id_client);
CREATE INDEX IF NOT EXISTS idx_fiche_bateau    ON fiche(id_bateau);
CREATE INDEX IF NOT EXISTS idx_fiche_edition   ON fiche(date_edition DESC);

-- ---------- Cotes ----------
CREATE TABLE IF NOT EXISTS fiche_cotes (
    id_cotes     BIGSERIAL PRIMARY KEY,
    id_fiche     BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    jeu          TEXT NOT NULL CHECK (jeu IN ('dessin','finie')),
    slu_m        NUMERIC(7,3),
    sle_m        NUMERIC(7,3),
    sf_m         NUMERIC(7,3),
    shw_m        NUMERIC(7,3),
    spa_m2       NUMERIC(8,3),
    tetiere_cm   NUMERIC(6,2),
    poids_kg     NUMERIC(7,3),
    UNIQUE (id_fiche, jeu)
);

-- ---------- Matériaux / épaisseurs ----------
CREATE TABLE IF NOT EXISTS fiche_materiau (
    id_fiche_materiau BIGSERIAL PRIMARY KEY,
    id_fiche     BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    role         TEXT NOT NULL,
    niveau       INTEGER,
    id_materiau  BIGINT REFERENCES materiau(id_materiau) ON DELETE SET NULL,
    designation_texte TEXT,
    grammage_g_m2 NUMERIC(7,1),
    mesure_mm    NUMERIC(7,1),
    UNIQUE (id_fiche, role, niveau)
);

-- ---------- Galons ----------
CREATE TABLE IF NOT EXISTS fiche_galon (
    id_galon      BIGSERIAL PRIMARY KEY,
    id_fiche      BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    bande         TEXT NOT NULL CHECK (bande IN ('guindant','chute','bordure')),
    couleur       TEXT,
    largeur_mm    NUMERIC(6,1),
    matiere       TEXT,
    grammage_g_m2 NUMERIC(6,1),
    UNIQUE (id_fiche, bande)
);

-- ---------- Jonctions ----------
CREATE TABLE IF NOT EXISTS fiche_jonction (
    id_jonction   BIGSERIAL PRIMARY KEY,
    id_fiche      BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    nature        TEXT NOT NULL,
    ordre         INTEGER NOT NULL DEFAULT 1,
    description   TEXT,
    nb_zigzag     INTEGER,
    nb_points     INTEGER,
    espacement_mm NUMERIC(6,1),
    surplus       TEXT,
    UNIQUE (id_fiche, nature, ordre)
);

-- ---------- Finitions ----------
CREATE TABLE IF NOT EXISTS fiche_finition (
    id_finition   BIGSERIAL PRIMARY KEY,
    id_fiche      BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    poste         TEXT NOT NULL,
    valeur_texte  TEXT,
    oeillet_type  TEXT,
    sangle        BOOLEAN,
    UNIQUE (id_fiche, poste)
);

-- ---------- Options ----------
CREATE TABLE IF NOT EXISTS fiche_option (
    id_option     BIGSERIAL PRIMARY KEY,
    id_fiche      BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    code          TEXT NOT NULL,
    valeur_bool   BOOLEAN,
    valeur_texte  TEXT,
    UNIQUE (id_fiche, code)
);

-- ---------- Renforts ----------
CREATE TABLE IF NOT EXISTS fiche_renfort (
    id_renfort    BIGSERIAL PRIMARY KEY,
    id_fiche      BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    repere        TEXT,
    quantite      INTEGER,
    forme         TEXT,
    diametre_mm   NUMERIC(7,1),
    matiere       TEXT,
    description   TEXT
);

-- ---------- Extensibilité (variantes inconnues, RG6) ----------
CREATE TABLE IF NOT EXISTS fiche_mesure_libre (
    id_mesure     BIGSERIAL PRIMARY KEY,
    id_fiche      BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    code          TEXT NOT NULL,
    libelle       TEXT,
    valeur_num    NUMERIC(12,3),
    unite         TEXT,
    valeur_texte  TEXT,
    UNIQUE (id_fiche, code, libelle)
);

-- ---------- Liens entre fiches ----------
CREATE TABLE IF NOT EXISTS fiche_lien (
    id_lien         BIGSERIAL PRIMARY KEY,
    id_fiche_source BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    id_fiche_cible  BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    type            TEXT NOT NULL,
    score           NUMERIC(4,3),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_fiche_source, id_fiche_cible, type)
);

-- ---------- Traçabilité de l'extraction (boucle d'amélioration) ----------
CREATE TABLE IF NOT EXISTS fiche_champ_extrait (
    id_champ          BIGSERIAL PRIMARY KEY,
    id_fiche          BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    champ             TEXT NOT NULL,
    rang              INTEGER,
    table_cible       TEXT,
    colonne_cible     TEXT,
    valeur_brute      TEXT,
    valeur_normalisee TEXT,
    methode           TEXT NOT NULL,
    confiance         NUMERIC(4,3),
    page              INTEGER,
    zone              JSONB,
    version_gabarit   INTEGER,
    corrige           BOOLEAN NOT NULL DEFAULT false,
    corrige_par       BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    corrige_le        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_fiche, champ, rang)
);

CREATE INDEX IF NOT EXISTS idx_champ_corrige ON fiche_champ_extrait(corrige) WHERE corrige;

CREATE TABLE IF NOT EXISTS fiche_validation (
    id_validation  BIGSERIAL PRIMARY KEY,
    id_fiche       BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    id_utilisateur BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    action         TEXT NOT NULL,
    etat_avant     TEXT,
    etat_apres     TEXT,
    commentaire    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fiche_anomalie (
    id_anomalie  BIGSERIAL PRIMARY KEY,
    id_fiche     BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    code         TEXT NOT NULL,
    gravite      TEXT NOT NULL DEFAULT 'moyenne',
    message      TEXT,
    statut       TEXT NOT NULL DEFAULT 'a_traiter',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Recherche vectorielle (chunks) ----------
-- NB (correctif de revue, constat 1) : le nom de la configuration de recherche
-- est injecté par le code au moment de la migration (marqueur __TS_CONFIG__
-- remplacé par 'seamtech_unaccent' ou, si le rôle ne peut pas l'installer,
-- par 'simple' — même dégradation gracieuse que la migration 005 pour
-- documents.search_vector). Le DDL ci-dessous NE suppose PAS que la
-- configuration existe.
CREATE TABLE IF NOT EXISTS chunk (
    id_chunk     BIGSERIAL PRIMARY KEY,
    id_document  BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    id_fiche     BIGINT REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    nature       TEXT NOT NULL,
    contenu      TEXT NOT NULL,
    embedding    vector(384),
    tsv          tsvector GENERATED ALWAYS AS
                 (to_tsvector('__TS_CONFIG__', contenu)) STORED,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunk_tsv    ON chunk USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_chunk_fiche  ON chunk(id_fiche);
-- À 100 000 vecteurs, la recherche exacte reste instantanée : l'index ANN
-- (ivfflat) ne se crée que si le volume double (décision plan v3.0 §11.3).

-- ---------- Extension de la couche fichiers existante ----------
ALTER TABLE documents ADD COLUMN IF NOT EXISTS id_fiche BIGINT REFERENCES fiche(id_fiche);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS role TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding vector(384);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS traite_le TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_documents_fiche ON documents(id_fiche);
CREATE INDEX IF NOT EXISTS idx_documents_role  ON documents(role);

-- ---------- Vue de recherche : une ligne par fiche, tout agrégé ----------
CREATE OR REPLACE VIEW v_fiche_recherche AS
SELECT f.id_fiche,
       f.code,
       f.titre,
       f.statut,
       tv.libelle                    AS type_voile,
       c.nom                         AS client,
       b.nom || ' ' || coalesce(b.taille,'') AS bateau,
       f.gamme,
       f.segment,
       f.date_edition,
       cd.slu_m, cd.sle_m, cd.sf_m, cd.shw_m, cd.spa_m2, cd.poids_kg
FROM fiche f
LEFT JOIN type_voile tv ON tv.id_type_voile = f.id_type_voile
LEFT JOIN client     c  ON c.id_client      = f.id_client
LEFT JOIN bateau     b  ON b.id_bateau      = f.id_bateau
LEFT JOIN fiche_cotes cd ON cd.id_fiche = f.id_fiche AND cd.jeu = 'finie';
"""

# Constat 1 de revue (volet pg_trgm) : le cœur de 007 est séparable des index
# trigrammes. Sans le privilège CREATE sur la base, `pg_trgm` ne s'installe
# pas (même « trusted ») : le cœur s'applique quand même, seuls les index de
# tolérance aux fautes sont omis (avertissement + conséquence journalisés).
SQL_007_TRGM = """
-- Tolérance aux fautes (pg_trgm) : « monofim » trouve Monofilm.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_fiche_code_trgm    ON fiche USING GIN (code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fiche_titre_trgm   ON fiche USING GIN (titre gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fiche_champs_trgm  ON fiche USING GIN (champs_texte gin_trgm_ops);
"""

SQL_007_RECHERCHE_INDEX = """
-- ============================================================================
-- 007_recherche_index — cœur « à la Google » (plan v3.0 §11, §17.3)
-- (les index trigrammes vivent dans SQL_007_TRGM, dégradable, voir ci-dessus)
-- ============================================================================

-- Texte de recherche par fiche : agrégat PONDÉRÉ rempli par la fonction
-- rafraichir_texte_recherche_fiche(), appelée à l'ÉCRITURE de chaque fiche
-- (statut a_valider — migration 018, décision commanditaire 2026-09-29)
-- puis rafraîchie à sa VALIDATION (une fiche à la fois — jamais en boucle
-- ligne par ligne sur la table).
ALTER TABLE fiche ADD COLUMN IF NOT EXISTS champs_texte TEXT NOT NULL DEFAULT '';
ALTER TABLE fiche ADD COLUMN IF NOT EXISTS search_vector TSVECTOR;

CREATE INDEX IF NOT EXISTS idx_fiche_search       ON fiche USING GIN(search_vector);

-- Pondération : A = code + titre (le plus fort) ; B = client, bateau, type de
-- voile, matériaux, galons, jonctions, finitions ; C = notes (texte libre).
CREATE OR REPLACE FUNCTION rafraichir_texte_recherche_fiche(p_id_fiche BIGINT)
RETURNS void
LANGUAGE plpgsql
AS $fn$
BEGIN
    UPDATE fiche f
    SET champs_texte = agg.texte,
        search_vector =
            setweight(to_tsvector('__TS_CONFIG__',
                                  regexp_replace(coalesce(f.code, ''), '[-_/]+', ' ', 'g')
                                  || ' ' || coalesce(f.titre, '')), 'A')
            || setweight(to_tsvector('__TS_CONFIG__', agg.secondaire), 'B')
            || setweight(to_tsvector('__TS_CONFIG__', coalesce(f.notes, '')), 'C')
    FROM (
        SELECT f2.id_fiche AS id_fiche,
               concat_ws(' | ',
                         f2.code, f2.titre, f2.gamme, f2.segment,
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche)
               ) AS texte,
               concat_ws(' ',
                         f2.gamme, f2.segment,
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche)
               ) AS secondaire
        FROM fiche f2
        WHERE f2.id_fiche = p_id_fiche
    ) AS agg
    WHERE f.id_fiche = agg.id_fiche;
END
$fn$;

-- Synonymes et équivalences : spi = spinnaker, GV = grand-voile, m2 = m²…
-- Table modifiable sans redéploiement (plan v3.0 §11.4).
CREATE TABLE IF NOT EXISTS synonyme (
    terme   TEXT PRIMARY KEY,
    cible   TEXT NOT NULL,
    domaine TEXT
);

-- Journal des recherches : alimente suggestions et réglage du classement.
CREATE TABLE IF NOT EXISTS recherche_log (
    id_recherche   BIGSERIAL PRIMARY KEY,
    requete        TEXT NOT NULL,
    filtres        JSONB,
    nb_resultats   INTEGER,
    id_utilisateur BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

SQL_008_ML_CORPUS = """
-- ============================================================================
-- 008_ml_corpus — modèles, exemples étiquetés, historique (plan v3.0 §10.5)
-- Aucun modèle binaire en base : seul le chemin du fichier sur le disque est
-- stocké (contrainte « tout reste local », postes à 8 Go).
-- ============================================================================
CREATE TABLE IF NOT EXISTS ml_modele (
    id_modele       BIGSERIAL PRIMARY KEY,
    version         TEXT NOT NULL UNIQUE,
    algorithme      TEXT NOT NULL,
    hyperparametres JSONB NOT NULL DEFAULT '{}'::jsonb,
    metriques       JSONB NOT NULL DEFAULT '{}'::jsonb,
    chemin          TEXT NOT NULL,
    actif           BOOLEAN NOT NULL DEFAULT false,
    cree_par        BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ml_exemple (
    id_exemple    BIGSERIAL PRIMARY KEY,
    origine       TEXT NOT NULL CHECK (origine IN ('synthetique','reel')),
    entree        JSONB NOT NULL,
    etiquette     TEXT NOT NULL,
    id_fiche      BIGINT REFERENCES fiche(id_fiche) ON DELETE SET NULL,
    id_modele     BIGINT REFERENCES ml_modele(id_modele) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ml_exemple_origine ON ml_exemple(origine);

CREATE TABLE IF NOT EXISTS ml_run (
    id_run         BIGSERIAL PRIMARY KEY,
    id_modele      BIGINT REFERENCES ml_modele(id_modele) ON DELETE SET NULL,
    lance_le       TIMESTAMPTZ NOT NULL DEFAULT now(),
    fini_le        TIMESTAMPTZ,
    statut         TEXT NOT NULL DEFAULT 'en_cours',
    taille_corpus  INTEGER,
    scores         JSONB NOT NULL DEFAULT '{}'::jsonb,
    note           TEXT
);
"""

SQL_009_QUALITE_ET_GABARITS = """
-- ============================================================================
-- 009_qualite_et_gabarits — jeu de référence des gabarits, vue qualité
-- ============================================================================
-- Fiches de test d'un gabarit : le PDF (par chemin ou empreinte) et les
-- valeurs attendues, pour vérifier qu'un changement de règle ne casse rien
-- (même culture que les 262 tests du dépôt, plan v3.0 §10.2).
CREATE TABLE IF NOT EXISTS gabarit_test (
    id_gabarit_test BIGSERIAL PRIMARY KEY,
    code_gabarit    TEXT NOT NULL,
    version_gabarit INTEGER NOT NULL DEFAULT 1,
    nom_fichier     TEXT NOT NULL,
    empreinte_pdf   TEXT,
    attendu         JSONB NOT NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (code_gabarit, version_gabarit, nom_fichier)
);

-- Ces deux index existent déjà depuis 006 (§6.2 du plan) ; le §17.3 les
-- rattache aussi à 009 : rejeu no-op grâce à IF NOT EXISTS.
CREATE INDEX IF NOT EXISTS idx_fiche_statut    ON fiche(statut);
CREATE INDEX IF NOT EXISTS idx_champ_corrige   ON fiche_champ_extrait(corrige) WHERE corrige;

-- Vue qualité : une ligne par fiche, pour agréger taux de passage direct,
-- taux de correction et temps de validation (plan v3.0 §17.3, RG3/RG16).
CREATE OR REPLACE VIEW v_qualite AS
SELECT f.id_fiche,
       f.code,
       f.statut,
       f.score_qualite,
       f.created_at AS cree_le,
       (SELECT COUNT(*) FROM fiche_champ_extrait ce WHERE ce.id_fiche = f.id_fiche) AS nb_champs_extraits,
       (SELECT COUNT(*) FROM fiche_champ_extrait ce WHERE ce.id_fiche = f.id_fiche AND ce.corrige) AS nb_champs_corriges,
       ((SELECT COUNT(*) FROM fiche_champ_extrait ce WHERE ce.id_fiche = f.id_fiche AND ce.corrige) = 0)
           AS passage_direct,
       (SELECT MIN(v.created_at) FROM fiche_validation v
         WHERE v.id_fiche = f.id_fiche AND v.action = 'validee') AS valide_le,
       (SELECT MAX(v.created_at) FROM fiche_validation v
         WHERE v.id_fiche = f.id_fiche AND v.action = 'validee') AS derniere_validation_le
FROM fiche f;
"""

# Les quatre migrations, dans l'ordre d'exécution. Structure consommée par
# SearchIndex.run_migrations() et par le test de grammaire pglast.
SQL_010_LOTS_INGESTION = """
-- ============================================================================
-- 010_lots_ingestion — dépôt de dossiers, lots suivis et reprenables (Lot C,
-- plan v3.0 §17.2, §11, §12.2). Idempotent, ordonné, PostgreSQL uniquement.
-- ============================================================================
-- Un lot = une opération de dépôt (une racine d'archive désignée, ou un
-- dossier unique). L'état vit en BASE : consultable et reprenable sans Redis.
CREATE TABLE IF NOT EXISTS lot_import (
    id_lot         BIGSERIAL PRIMARY KEY,
    dossier_racine TEXT NOT NULL,
    statut         TEXT NOT NULL DEFAULT 'en_cours'
                   CHECK (statut IN ('en_cours','termine','interrompu')),
    nb_dossiers    INTEGER NOT NULL DEFAULT 0,
    nb_traites     INTEGER NOT NULL DEFAULT 0,
    nb_echecs      INTEGER NOT NULL DEFAULT 0,
    cree_le        TIMESTAMPTZ NOT NULL DEFAULT now(),
    termine_le     TIMESTAMPTZ,
    notes          TEXT
);

-- Une ligne par dossier du lot. La clé d'idempotence = chemin normalisé
-- (os.path.normcase, même fonction que le crawler) + SHA-256 du PDF de fiche ;
-- l'unicité ne porte que sur les dossiers TRAITÉS (index unique partiel) :
-- un échec reste retentable à la reprise, un succès n'est jamais refait.
CREATE TABLE IF NOT EXISTS lot_dossier (
    id_lot_dossier  BIGSERIAL PRIMARY KEY,
    id_lot          BIGINT NOT NULL REFERENCES lot_import(id_lot) ON DELETE CASCADE,
    chemin_dossier  TEXT NOT NULL,
    cle_idempotence TEXT,
    statut          TEXT NOT NULL DEFAULT 'en_attente'
                    CHECK (statut IN ('en_attente','traite','echec')),
    raison          TEXT,
    id_fiche        BIGINT REFERENCES fiche(id_fiche) ON DELETE SET NULL,
    nb_pieces       INTEGER NOT NULL DEFAULT 0,
    traite_le       TIMESTAMPTZ,
    UNIQUE (id_lot, chemin_dossier)
);
CREATE INDEX IF NOT EXISTS idx_lot_dossier_cle
    ON lot_dossier (cle_idempotence) WHERE cle_idempotence IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_lot_dossier_traite
    ON lot_dossier (cle_idempotence) WHERE statut = 'traite' AND cle_idempotence IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lot_dossier_statut ON lot_dossier (statut);

-- Pièces jointes rattachées à une fiche (croquis, plans, fichiers de
-- production) : fiche_lien relie des FICHES entre elles (clés NOT NULL vers
-- fiche) ; les fichiers non-fiche du dossier vont ICI — MLD honnête, même
-- mécanisme de migration.
CREATE TABLE IF NOT EXISTS fiche_piece_jointe (
    id_piece       BIGSERIAL PRIMARY KEY,
    id_fiche       BIGINT NOT NULL REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    chemin         TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'piece_jointe',
    empreinte_sha256 TEXT NOT NULL,
    taille_octets  BIGINT,
    cree_le        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_fiche, chemin, empreinte_sha256)
);
CREATE INDEX IF NOT EXISTS idx_piece_fiche ON fiche_piece_jointe (id_fiche);
"""

# 011 — Décision revue (pièces jointes, RG12) : un fichier est décrit UNE FOIS,
# dans `documents` (catalogue unique du crawler, métadonnées indexées plein-texte) ;
# `fiche_piece_jointe` devient une table de LIEN (id_fiche ↔ id_document) qui garde
# chemin/rôle/empreinte pour la traçabilité du lien. Le dépôt (Lot C) écrit la ligne
# documents avec path_key = os.path.normcase(chemin résolu) — la même clé que le
# crawler — pour qu'un futur passage réconcilie au lieu de dupliquer.
SQL_011_PIECES_CATALOGUE_DOCUMENTS = """
ALTER TABLE documents ADD COLUMN IF NOT EXISTS role TEXT;
ALTER TABLE fiche_piece_jointe ADD COLUMN IF NOT EXISTS id_document BIGINT REFERENCES documents(id);
CREATE INDEX IF NOT EXISTS idx_piece_document ON fiche_piece_jointe (id_document);
"""

# 012 — Lot E : recherche hybride (plan v3.0 §17.2, §17.5, §11).
# Le cœur : index d'axes de facettes, suivi des recherches sans résultat,
# fonction de rafraîchissement GLOBALE (un seul UPDATE ensembliste — la
# culture « jamais en boucle ligne par ligne » de la fonction par fiche) et
# backfill des fiches déjà validées. Les index trigrammes des référentiels
# (suggestions tolérantes aux fautes) vivent dans SQL_012_TRGM, dégradable
# sous SAVEPOINT exactement comme SQL_007_TRGM (constat 1 de revue).
SQL_012_RECHERCHE_HYBRIDE = """
-- ============================================================================
-- 012_recherche_hybride — Lot E : facettes, suivi, rafraîchissement global
-- ============================================================================

-- Axes de facettes de GET /recherche : type de voile / client / bateau sont
-- déjà indexés (006) ; la matière passe par fiche_materiau et l'année par
-- date_edition (idx_fiche_edition existe depuis 006).
CREATE INDEX IF NOT EXISTS idx_fiche_materiau_materiau ON fiche_materiau (id_materiau);
CREATE INDEX IF NOT EXISTS idx_fiche_gamme ON fiche (gamme);

-- Suivi des recherches SANS RÉSULTAT (critère de sortie Phase 3 : elles sont
-- comptabilisées pour améliorer le lexique). Index partiel : le comptage et
-- l'extraction des requêtes mortes ne parcourent jamais le journal entier.
CREATE INDEX IF NOT EXISTS idx_recherche_log_sans_resultat
    ON recherche_log (created_at) WHERE nb_resultats = 0;

-- Rafraîchissement GLOBAL du texte de recherche : un seul UPDATE ensembliste
-- (même agrégation pondérée A/B/C que rafraichir_texte_recherche_fiche, sans
-- filtre par id). Sert au backfill de cette migration et aux futures
-- réindexations — jamais de boucle ligne par ligne sur la table.
CREATE OR REPLACE FUNCTION rafraichir_texte_recherche_toutes()
RETURNS INTEGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    nb INTEGER;
BEGIN
    UPDATE fiche f
    SET champs_texte = agg.texte,
        search_vector =
            setweight(to_tsvector('__TS_CONFIG__',
                                  regexp_replace(coalesce(f.code, ''), '[-_/]+', ' ', 'g')
                                  || ' ' || coalesce(f.titre, '')), 'A')
            || setweight(to_tsvector('__TS_CONFIG__', agg.secondaire), 'B')
            || setweight(to_tsvector('__TS_CONFIG__', coalesce(f.notes, '')), 'C')
    FROM (
        SELECT f2.id_fiche AS id_fiche,
               concat_ws(' | ',
                         f2.code, f2.titre, f2.gamme, f2.segment,
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche)
               ) AS texte,
               concat_ws(' ',
                         f2.gamme, f2.segment,
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche)
               ) AS secondaire
        FROM fiche f2
    ) AS agg
    WHERE f.id_fiche = agg.id_fiche;
    GET DIAGNOSTICS nb = ROW_COUNT;
    RETURN nb;
END
$fn$;

-- Backfill : les fiches déjà validées deviennent cherchables immédiatement
-- (aucune fiche réelle en production aujourd'hui — la migration est correcte
-- par construction le jour où il y en a). DO : pas de lignes retournées.
DO $seamtech_backfill$
BEGIN
    PERFORM rafraichir_texte_recherche_toutes();
END
$seamtech_backfill$;
"""

# Volet dégradable de 012 : index trigrammes des RÉFÉRENTIELS pour les
# suggestions tolérantes aux fautes (« monofim » propose Monofilm). Petites
# tables, mais l'index sert l'opérateur % ; sans privilège CREATE ou sans
# pg_trgm, le SAVEPOINT de l'indexeur omet ce volet avec avertissement —
# les suggestions par préfixe restent opérationnelles.
SQL_012_TRGM = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_type_voile_libelle_trgm ON type_voile USING GIN (libelle gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_client_nom_trgm        ON client     USING GIN (nom gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_bateau_nom_trgm        ON bateau     USING GIN (nom gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_materiau_nom_trgm      ON materiau   USING GIN (nom gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fiche_gamme_trgm       ON fiche      USING GIN (gamme gin_trgm_ops);
"""

# ---------------------------------------------------------------------------
# 013 — Recherche sur le FONDS RÉEL (Tâche 3)
# Le rejeu du jeu de requêtes réel sur la vraie fiche 7792-SO (21/09) a
# mesuré deux angles morts du vecteur de recherche :
#   * « cruette » (raison sociale du client Sailonet) — le chantier du client
#     n'était pas agrégé au texte ;
#   * « spi sailonet 2026 » (combinaison opérateur type + client + année) —
#     l'année d'édition n'était pas tokenisée dans le vecteur.
# Les DEUX valeurs sont pourtant lues par l'extraction et présentes en base ;
# elles sont ajoutées au texte pondéré (poids B, comme les autres champs
# métier). L'architecture du lot E (A/B/C, RRF, facettes) est inchangée.
# ---------------------------------------------------------------------------
SQL_013_RECHERCHE_FONDS_REEL = """
CREATE OR REPLACE FUNCTION rafraichir_texte_recherche_fiche(p_id_fiche BIGINT)
RETURNS void
LANGUAGE plpgsql
AS $fn$
BEGIN
    UPDATE fiche f
    SET champs_texte = agg.texte,
        search_vector =
            setweight(to_tsvector('__TS_CONFIG__',
                                  regexp_replace(coalesce(f.code, ''), '[-_/]+', ' ', 'g')
                                  || ' ' || coalesce(f.titre, '')), 'A')
            || setweight(to_tsvector('__TS_CONFIG__', agg.secondaire), 'B')
            || setweight(to_tsvector('__TS_CONFIG__', coalesce(f.notes, '')), 'C')
    FROM (
        SELECT f2.id_fiche AS id_fiche,
               concat_ws(' | ',
                         f2.code, f2.titre, f2.gamme, f2.segment,
                         coalesce(to_char(f2.date_edition, 'YYYY'), ''),
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom || ' ' || coalesce(c.chantier, '') FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche)
               ) AS texte,
               concat_ws(' ',
                         f2.gamme, f2.segment,
                         coalesce(to_char(f2.date_edition, 'YYYY'), ''),
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom || ' ' || coalesce(c.chantier, '') FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche)
               ) AS secondaire
        FROM fiche f2
        WHERE f2.id_fiche = p_id_fiche
    ) AS agg
    WHERE f.id_fiche = agg.id_fiche;
END;
$fn$;

-- Variante ensembliste (backfill / réindexation) : même agrégation que la
-- fonction ci-dessus, sur toutes les fiches VALIDÉES.
CREATE OR REPLACE FUNCTION rafraichir_texte_recherche_toutes()
RETURNS INTEGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    nb INTEGER;
BEGIN
    UPDATE fiche f
    SET champs_texte = agg.texte,
        search_vector =
            setweight(to_tsvector('__TS_CONFIG__',
                                  regexp_replace(coalesce(f.code, ''), '[-_/]+', ' ', 'g')
                                  || ' ' || coalesce(f.titre, '')), 'A')
            || setweight(to_tsvector('__TS_CONFIG__', agg.secondaire), 'B')
            || setweight(to_tsvector('__TS_CONFIG__', coalesce(f.notes, '')), 'C')
    FROM (
        SELECT f2.id_fiche AS id_fiche,
               concat_ws(' | ',
                         f2.code, f2.titre, f2.gamme, f2.segment,
                         coalesce(to_char(f2.date_edition, 'YYYY'), ''),
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom || ' ' || coalesce(c.chantier, '') FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche)
               ) AS texte,
               concat_ws(' ',
                         f2.gamme, f2.segment,
                         coalesce(to_char(f2.date_edition, 'YYYY'), ''),
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom || ' ' || coalesce(c.chantier, '') FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche)
               ) AS secondaire
        FROM fiche f2
    ) AS agg
    WHERE f.id_fiche = agg.id_fiche;
    GET DIAGNOSTICS nb = ROW_COUNT;
    RETURN nb;
END;
$fn$;
"""

SQL_014_FACETTE_DIMENSION = """
-- ============================================================================
-- 014_facette_dimension — Lot J : facette « dimension » (plan v3.0 §11.4)
-- La vue v_fiche_recherche omettait tetiere_cm ; on la complète avec les
-- 7 cotes métier (slu_m, sle_m, sf_m, shw_m, spa_m2, tetiere_cm, poids_kg).
-- Les valeurs sont déjà normalisées en unités métier (m, m², cm, kg) lors
-- de l'extraction — aucune conversion à la lecture.
-- Index pour le filtre par plage (cote=...&min=...&max=...).
-- Ancienne vue avait 6 colonnes cotes (sans tetiere_cm) : CREATE OR REPLACE
-- échoue avec « cannot change name of view column poids_kg to tetiere_cm »
-- (colonne 6 change de nom). On DROP puis CREATE pour autoriser le changement
-- d'ordre/nom — idempotent et sans dépendance (v_qualite est indépendante).
-- ============================================================================
DROP VIEW IF EXISTS v_fiche_recherche;
CREATE VIEW v_fiche_recherche AS
SELECT f.id_fiche,
       f.code,
       f.titre,
       f.statut,
       tv.libelle                    AS type_voile,
       c.nom                         AS client,
       b.nom || ' ' || coalesce(b.taille,'') AS bateau,
       f.gamme,
       f.segment,
       f.date_edition,
       cd.slu_m, cd.sle_m, cd.sf_m, cd.shw_m, cd.spa_m2, cd.tetiere_cm, cd.poids_kg
FROM fiche f
LEFT JOIN type_voile tv ON tv.id_type_voile = f.id_type_voile
LEFT JOIN client     c  ON c.id_client      = f.id_client
LEFT JOIN bateau     b  ON b.id_bateau      = f.id_bateau
LEFT JOIN fiche_cotes cd ON cd.id_fiche = f.id_fiche AND cd.jeu = 'finie';

CREATE INDEX IF NOT EXISTS idx_fiche_cotes_slu_m ON fiche_cotes(slu_m);
CREATE INDEX IF NOT EXISTS idx_fiche_cotes_sle_m ON fiche_cotes(sle_m);
CREATE INDEX IF NOT EXISTS idx_fiche_cotes_sf_m ON fiche_cotes(sf_m);
CREATE INDEX IF NOT EXISTS idx_fiche_cotes_shw_m ON fiche_cotes(shw_m);
CREATE INDEX IF NOT EXISTS idx_fiche_cotes_spa_m2 ON fiche_cotes(spa_m2);
CREATE INDEX IF NOT EXISTS idx_fiche_cotes_tetiere_cm ON fiche_cotes(tetiere_cm);
CREATE INDEX IF NOT EXISTS idx_fiche_cotes_poids_kg ON fiche_cotes(poids_kg);
"""

SQL_015_QUALITE_GABARIT_BROUILLON = """
-- ============================================================================
-- 015_qualite_gabarit_brouillon — Lot K : tableau qualité + gabarit brouillon
-- - Index pour qualité : accélère GROUP BY sur champs corrigés, recherche_log
--   par canal (filtres->>'canal'), fiche_validation par action, fiche_anomalie
--   par code, etc. Toutes les requêtes qualité doivent rester <100 ms sur
--   1 000 fiches (mesuré p50/p95).
-- - Table gabarit_brouillon : brouillons de gabarits produits depuis un PDF
--   variante inconnue (K.2). Un brouillon NON validé ne doit JAMAIS servir à
--   l'extraction réelle — garde-fou : charger_gabarits() ne lit que gabarit
--   WHERE actif, jamais gabarit_brouillon. La publication copie le brouillon
--   vers gabarit comme nouvelle version active (registre existant, pas parallèle).
-- ============================================================================

-- Index qualité (si absents)
CREATE INDEX IF NOT EXISTS idx_champ_champ ON fiche_champ_extrait(champ);
CREATE INDEX IF NOT EXISTS idx_champ_corrige_champ ON fiche_champ_extrait(champ, corrige);
CREATE INDEX IF NOT EXISTS idx_validation_action ON fiche_validation(action);
CREATE INDEX IF NOT EXISTS idx_validation_fiche_action ON fiche_validation(id_fiche, action);
CREATE INDEX IF NOT EXISTS idx_anomalie_code ON fiche_anomalie(code);
CREATE INDEX IF NOT EXISTS idx_recherche_log_canal ON recherche_log((filtres->>'canal'));
CREATE INDEX IF NOT EXISTS idx_recherche_log_created ON recherche_log(created_at DESC);

-- Table brouillons gabarits
CREATE TABLE IF NOT EXISTS gabarit_brouillon (
    id_brouillon     BIGSERIAL PRIMARY KEY,
    code             TEXT NOT NULL,
    description      TEXT,
    regles           JSONB NOT NULL DEFAULT '{}'::jsonb,
    ancres_detection TEXT[] NOT NULL DEFAULT '{}',
    zones            JSONB NOT NULL DEFAULT '[]'::jsonb,
    confiance        JSONB NOT NULL DEFAULT '{}'::jsonb,
    statut           TEXT NOT NULL DEFAULT 'brouillon' CHECK (statut IN ('brouillon','valide','rejete')),
    source_pdf_sha256 TEXT,
    source_pdf_nom   TEXT,
    cree_par         BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_brouillon_code ON gabarit_brouillon(code);
CREATE INDEX IF NOT EXISTS idx_brouillon_statut ON gabarit_brouillon(statut);
"""

SQL_016_DEDUP_COMPTES_NOMINATIFS = """
-- ============================================================================
-- 016_dedup_comptes_nominatifs — Lot L : doublons (L.1) + comptes nominatifs (L.2)
-- ============================================================================
-- L.1 — DÉTECTION DE DOUBLONS. Rien n'efface, rien ne fusionne : la détection
-- est PROPOSITIVE et s'appuie sur deux colonnes qui EXISTAIENT DÉJÀ :
--   * fiche_piece_jointe.empreinte_sha256 (remplie à l'import par
--     `empreinte_fichier()`) → doublons EXACTS ;
--   * fiche.titre → doublons PROBABLES par similarité.
-- `fiche_lien` (schéma initial 006, jamais utilisée jusqu'ici) porte les liens
-- avec type='doublon_exact' | 'doublon_probable'.
--
-- AUCUNE NOUVELLE TABLE dans cette partie : TABLES_METIER reste à 31. La table
-- de sessions (L.2) est ajoutée plus bas, dans la même migration.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- L.1 — Normalisation de titre partagée SQL/Python
-- ---------------------------------------------------------------------------
-- Pourquoi une FONCTION et pas un appel direct à `unaccent()` :
-- `unaccent()` est STABLE, pas IMMUTABLE (elle dépend du dictionnaire chargé) —
-- un index ne peut donc pas la porter. `translate()` est IMMUTABLE : le pliage
-- des accents français ci-dessous est indexable en GIN trigrammes, ce dont la
-- détection des doublons probables a besoin. Le miroir Python exact est
-- `seamtech_search.dedup.detection.normaliser_titre`.
CREATE OR REPLACE FUNCTION seamtech_titre_normalise(t TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $fonction$
    SELECT btrim(
        lower(
            regexp_replace(
                translate(
                    coalesce(t, ''),
                    'àâäáãåÀÂÄÁÃÅèéêëÈÉÊËìíîïÌÍÎÏòóôöõÒÓÔÖÕùúûüÙÚÛÜçÇñÑÿŸ',
                    'aaaaaaAAAAAAeeeeEEEEiiiiIIIIoooooOOOOOuuuuUUUUcCnNyY'
                ),
                '[[:space:]]+', ' ', 'g'
            )
        )
    )
$fonction$;

COMMENT ON FUNCTION seamtech_titre_normalise(TEXT) IS
    'Titre normalisé (casse pliée, espaces compactés, accents FR dépliés) — IMMUTABLE, donc indexable ; miroir Python : seamtech_search.dedup.detection.normaliser_titre';

-- ---------------------------------------------------------------------------
-- L.1 — Index des doublons
-- ---------------------------------------------------------------------------
-- Doublons exacts : GROUP BY empreinte_sha256 HAVING count(DISTINCT id_fiche) > 1.
CREATE INDEX IF NOT EXISTS idx_pj_empreinte ON fiche_piece_jointe (empreinte_sha256);

-- Lecture des liens par nature (« tous les doublons exacts »).
CREATE INDEX IF NOT EXISTS idx_fiche_lien_type ON fiche_lien (type);

-- Sens de parcours de l'affichage (liens d'une fiche donnée), dans les deux sens.
CREATE INDEX IF NOT EXISTS idx_fiche_lien_source ON fiche_lien (id_fiche_source);
CREATE INDEX IF NOT EXISTS idx_fiche_lien_cible ON fiche_lien (id_fiche_cible);

-- ---------------------------------------------------------------------------
-- L.2 — COMPTES NOMINATIFS « qui a validé quoi »
-- ---------------------------------------------------------------------------
-- La table `utilisateur` EXISTAIT depuis la 006 (identifiant UNIQUE, nom, role,
-- actif) mais n'était JAMAIS alimentée : l'attribution se faisait par un simple
-- identifiant recopié du corps de requête. On l'étend au lieu de la remplacer —
-- et les colonnes d'attribution existantes (`fiche_validation.id_utilisateur`,
-- `fiche_champ_extrait.corrige_par`) restent celles qui portent la traçabilité.
ALTER TABLE utilisateur ADD COLUMN IF NOT EXISTS empreinte_mot_de_passe TEXT;
ALTER TABLE utilisateur ADD COLUMN IF NOT EXISTS mot_de_passe_modifie_le TIMESTAMPTZ;
ALTER TABLE utilisateur ADD COLUMN IF NOT EXISTS derniere_connexion TIMESTAMPTZ;
ALTER TABLE utilisateur ADD COLUMN IF NOT EXISTS doit_changer_mot_de_passe BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN utilisateur.empreinte_mot_de_passe IS
    'Format scrypt$n=16384$r=8$p=1$<sel_b64>$<hash_b64> (hashlib.scrypt, sel de 16 octets). Jamais renvoyée par une API, jamais journalisée.';

-- Sessions nominatives. Le cookie signé — seule chose que détient le navigateur —
-- porte l'id_session ; la VÉRITÉ est ici. C'est ce qui rend une déconnexion
-- réellement effective : `revoque_le` est posé, et un cookie encore
-- cryptographiquement valide ne vaut plus rien.
CREATE TABLE IF NOT EXISTS session_ui (
    id_session        BIGSERIAL PRIMARY KEY,
    id_utilisateur    BIGINT NOT NULL REFERENCES utilisateur(id_utilisateur) ON DELETE CASCADE,
    empreinte_jeton   TEXT NOT NULL UNIQUE,
    cree_le           TIMESTAMPTZ NOT NULL DEFAULT now(),
    expire_le         TIMESTAMPTZ NOT NULL,
    derniere_activite TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoque_le        TIMESTAMPTZ,
    user_agent        TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_ui_utilisateur ON session_ui (id_utilisateur);
CREATE INDEX IF NOT EXISTS idx_session_ui_expire ON session_ui (expire_le);

-- Verrouillage des connexions (5 échecs en 5 minutes ⇒ 429 pendant 5 minutes).
-- Le compteur n'a PAS sa propre table : une connexion refusée est un événement
-- d'audit, journalisé dans `audit_log` (action = 'connexion_refusee'). Cela
-- évite une 33e table métier et laisse une trace consultable après coup.
-- L'index existant porte sur `timestamp` seul : le filtre du verrou porte sur
-- (actor, action), d'où celui-ci. Aucune purge : on ne supprime jamais
-- automatiquement de données.
CREATE INDEX IF NOT EXISTS idx_audit_log_actor_action ON audit_log (actor, action, timestamp DESC);
"""

# ---------------------------------------------------------------------------
# 016 — volet TRIGRAMMES, DÉGRADABLE (même motif que SQL_007_TRGM)
# ---------------------------------------------------------------------------
# Sans le privilège CREATE sur la base, `pg_trgm` ne s'installe pas : l'indexeur
# isole ce bloc dans un SAVEPOINT et l'omet avec un avertissement. Conséquence
# écrite : la détection des doublons PROBABLES perd l'accès rapide par index et
# bascule sur son repli déterministe — jamais une erreur, jamais un silence.
#
# L'index porte `seamtech_titre_normalise(titre)` et non `titre` nu : c'est la
# même normalisation que celle comparée dans la détection, donc un index que le
# planificateur peut réellement utiliser pour l'opérateur `%` du JOIN.
SQL_016_TRGM_TITRE = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_fiche_titre_trgm
    ON fiche USING gin (seamtech_titre_normalise(titre) gin_trgm_ops);

-- Tolérance sur la recherche plein-texte de la recherche : l'index trigrammes
-- existant porte sur fiche.champs_texte (007). Cet index-ci cible le TITRE,
-- dont dépend la détection des doublons.
CREATE INDEX IF NOT EXISTS idx_fiche_titre_trgm_brut
    ON fiche USING gin (titre gin_trgm_ops);
"""

SQL_017_OCR_ETAGE3 = """
-- ============================================================================
-- 017_ocr_etage3 — Lot M : staging du texte OCR par page (préparation Lot G)
-- ============================================================================
-- AUCUNE écriture dans chunk ni document : la promotion vers l'index
-- appartient au Lot G. Cette table est du STAGING pur, jamais lue par la
-- recherche tant que le Lot G ne l'a pas promue.
--
-- Une ligne = une page océrisée (ou tentative). Les pages avec texte natif
-- ne sont PAS insérées (elles sont comptées comme ignorées dans le tableau
-- de bord, mais pas stockées ici — le texte natif est déjà dans le PDF).
-- Les images isolées (png/jpg/tif) sont traitées comme un scan d'une page.
--
-- Colonnes :
-- - fichier_source : chemin du fichier source (pour traçabilité humaine)
-- - empreinte_sha256 : empreinte du fichier source (idempotence, jamais mtime)
-- - page : numéro de page 0-based
-- - texte_ocr : texte produit par OCR
-- - confiance : confiance moyenne tesseract (0-100) ou NULL
-- - moteur : nom du moteur (tesseract)
-- - version_moteur : version du binaire (ex: tesseract 5.3.0)
-- - duree_s : durée OCR de la page
-- - page_ocerisee : bool, True si OCR a produit du texte
-- - motif : raison de non-OCR ou d'échec (texte natif présent, tesseract absent, etc.)
-- - horodatage : date d'insertion
-- ============================================================================
CREATE TABLE IF NOT EXISTS ocr_etage3 (
    id               BIGSERIAL PRIMARY KEY,
    fichier_source   TEXT NOT NULL,
    empreinte_sha256 TEXT NOT NULL,
    page             INTEGER NOT NULL,
    texte_ocr        TEXT,
    confiance        REAL,
    moteur           TEXT,
    version_moteur   TEXT,
    duree_s          REAL,
    page_ocerisee    BOOLEAN NOT NULL DEFAULT false,
    motif            TEXT,
    horodatage       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ocr_etage3_empreinte ON ocr_etage3 (empreinte_sha256);
CREATE INDEX IF NOT EXISTS idx_ocr_etage3_fichier ON ocr_etage3 (fichier_source);
CREATE INDEX IF NOT EXISTS idx_ocr_etage3_page_ocerisee ON ocr_etage3 (page_ocerisee);
CREATE INDEX IF NOT EXISTS idx_ocr_etage3_horodatage ON ocr_etage3 (horodatage DESC);
"""

# ---------------------------------------------------------------------------
# Migration 018 — Phase 2.1 (production-readiness, décision commanditaire du
# 2026-09-29) : les fiches a_valider deviennent CHERCHABLES (badgées « non
# vérifiée » par l'interface). Depuis cette version, ecrire_fiche remplit le
# texte de recherche pondéré DÈS l'écriture ; cette migration RATTRAPE les
# fiches déjà posées en base avant le changement (search_vector IS NULL) en
# leur appliquant la même fonction, quels que soient leur statut et leur
# ancienneté. Aucune table nouvelle, aucun changement de statut : RG3 est
# intact (rien ne devient « valide » sans décision humaine).
# ---------------------------------------------------------------------------
SQL_018_RECHERCHE_A_VALIDER = """
SELECT rafraichir_texte_recherche_fiche(id_fiche) FROM fiche WHERE search_vector IS NULL;
"""

# ---------------------------------------------------------------------------
# 019 — Recherche par dimension (Phase 1 « ça marche chez moi », 2026-09-30).
#
# Le test de tokenisation RÉEL (PostgreSQL 16.2, config seamtech_unaccent) a
# mesuré : websearch_to_tsquery('6,60') → '6' <-> '60' (la virgule coupe en
# deux lexèmes) alors que '6.60' → '6.60' (float unique) — les deux formes ne
# se croisent JAMAIS. Les 7 cotes entrent donc dans le texte de recherche
# pondéré (poids B, champs métier) dans les DEUX formes « 6.60 » ET « 6,60 »,
# numérisées en 2 décimales via round(..., 2)::text (sortie numérique
# locale-indépendante, contrairement à to_char). La VALEUR, elle, est cherchée
# par le chemin numérique existant côté requête (tolérance ±0,5 %) — cette
# migration ne touche pas à ce chemin.
#
# REPLACE propre des deux définitions de la fonction globale
# rafraichir_texte_recherche_toutes (héritées de 012 puis 013) et de la
# fonction par fiche rafraichir_texte_recherche_fiche (héritée de 007 puis
# 013) : les deux corps sont redéfinis ici, puis backfill global — comme la
# 018 a rattrapé ses fiches, la 019 rattrape TOUTES les fiches déjà posées
# (leur texte ne contient pas encore les formes de cotes). Idempotente :
# rejouer la migration ne change rien (CREATE OR REPLACE + UPDATE déterministe).
# ---------------------------------------------------------------------------
SQL_019_RECHERCHE_DIMENSION = """
CREATE OR REPLACE FUNCTION rafraichir_texte_recherche_fiche(p_id_fiche BIGINT)
RETURNS void
LANGUAGE plpgsql
AS $fn$
BEGIN
    UPDATE fiche f
    SET champs_texte = agg.texte,
        search_vector =
            setweight(to_tsvector('__TS_CONFIG__',
                                  regexp_replace(coalesce(f.code, ''), '[-_/]+', ' ', 'g')
                                  || ' ' || coalesce(f.titre, '')), 'A')
            || setweight(to_tsvector('__TS_CONFIG__', agg.secondaire), 'B')
            || setweight(to_tsvector('__TS_CONFIG__', coalesce(f.notes, '')), 'C')
    FROM (
        SELECT f2.id_fiche AS id_fiche,
               concat_ws(' | ',
                         f2.code, f2.titre, f2.gamme, f2.segment,
                         coalesce(to_char(f2.date_edition, 'YYYY'), ''),
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom || ' ' || coalesce(c.chantier, '') FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche),
                         (SELECT concat_ws(' ',
                                 round(cd.slu_m, 2)::text, replace(round(cd.slu_m, 2)::text, '.', ','),
                                 round(cd.sle_m, 2)::text, replace(round(cd.sle_m, 2)::text, '.', ','),
                                 round(cd.sf_m, 2)::text, replace(round(cd.sf_m, 2)::text, '.', ','),
                                 round(cd.shw_m, 2)::text, replace(round(cd.shw_m, 2)::text, '.', ','),
                                 round(cd.spa_m2, 2)::text, replace(round(cd.spa_m2, 2)::text, '.', ','),
                                 round(cd.tetiere_cm, 2)::text, replace(round(cd.tetiere_cm, 2)::text, '.', ','),
                                 round(cd.poids_kg, 2)::text, replace(round(cd.poids_kg, 2)::text, '.', ',')
                          ) FROM fiche_cotes cd WHERE cd.id_fiche = f2.id_fiche AND cd.jeu = 'finie')
               ) AS texte,
               concat_ws(' ',
                         f2.gamme, f2.segment,
                         coalesce(to_char(f2.date_edition, 'YYYY'), ''),
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom || ' ' || coalesce(c.chantier, '') FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche),
                         (SELECT concat_ws(' ',
                                 round(cd.slu_m, 2)::text, replace(round(cd.slu_m, 2)::text, '.', ','),
                                 round(cd.sle_m, 2)::text, replace(round(cd.sle_m, 2)::text, '.', ','),
                                 round(cd.sf_m, 2)::text, replace(round(cd.sf_m, 2)::text, '.', ','),
                                 round(cd.shw_m, 2)::text, replace(round(cd.shw_m, 2)::text, '.', ','),
                                 round(cd.spa_m2, 2)::text, replace(round(cd.spa_m2, 2)::text, '.', ','),
                                 round(cd.tetiere_cm, 2)::text, replace(round(cd.tetiere_cm, 2)::text, '.', ','),
                                 round(cd.poids_kg, 2)::text, replace(round(cd.poids_kg, 2)::text, '.', ',')
                          ) FROM fiche_cotes cd WHERE cd.id_fiche = f2.id_fiche AND cd.jeu = 'finie')
               ) AS secondaire
        FROM fiche f2
        WHERE f2.id_fiche = p_id_fiche
    ) AS agg
    WHERE f.id_fiche = agg.id_fiche;
END;
$fn$;

CREATE OR REPLACE FUNCTION rafraichir_texte_recherche_toutes()
RETURNS INTEGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    nb INTEGER;
BEGIN
    UPDATE fiche f
    SET champs_texte = agg.texte,
        search_vector =
            setweight(to_tsvector('__TS_CONFIG__',
                                  regexp_replace(coalesce(f.code, ''), '[-_/]+', ' ', 'g')
                                  || ' ' || coalesce(f.titre, '')), 'A')
            || setweight(to_tsvector('__TS_CONFIG__', agg.secondaire), 'B')
            || setweight(to_tsvector('__TS_CONFIG__', coalesce(f.notes, '')), 'C')
    FROM (
        SELECT f2.id_fiche AS id_fiche,
               concat_ws(' | ',
                         f2.code, f2.titre, f2.gamme, f2.segment,
                         coalesce(to_char(f2.date_edition, 'YYYY'), ''),
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom || ' ' || coalesce(c.chantier, '') FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche),
                         (SELECT concat_ws(' ',
                                 round(cd.slu_m, 2)::text, replace(round(cd.slu_m, 2)::text, '.', ','),
                                 round(cd.sle_m, 2)::text, replace(round(cd.sle_m, 2)::text, '.', ','),
                                 round(cd.sf_m, 2)::text, replace(round(cd.sf_m, 2)::text, '.', ','),
                                 round(cd.shw_m, 2)::text, replace(round(cd.shw_m, 2)::text, '.', ','),
                                 round(cd.spa_m2, 2)::text, replace(round(cd.spa_m2, 2)::text, '.', ','),
                                 round(cd.tetiere_cm, 2)::text, replace(round(cd.tetiere_cm, 2)::text, '.', ','),
                                 round(cd.poids_kg, 2)::text, replace(round(cd.poids_kg, 2)::text, '.', ',')
                          ) FROM fiche_cotes cd WHERE cd.id_fiche = f2.id_fiche AND cd.jeu = 'finie')
               ) AS texte,
               concat_ws(' ',
                         f2.gamme, f2.segment,
                         coalesce(to_char(f2.date_edition, 'YYYY'), ''),
                         (SELECT tv.libelle FROM type_voile tv WHERE tv.id_type_voile = f2.id_type_voile),
                         (SELECT c.nom || ' ' || coalesce(c.chantier, '') FROM client c WHERE c.id_client = f2.id_client),
                         (SELECT b.nom || ' ' || coalesce(b.taille, '') FROM bateau b WHERE b.id_bateau = f2.id_bateau),
                         (SELECT string_agg(m.designation_texte, ' ') FROM fiche_materiau m WHERE m.id_fiche = f2.id_fiche),
                         (SELECT string_agg(concat_ws(' ', g.couleur, g.matiere), ' ') FROM fiche_galon g WHERE g.id_fiche = f2.id_fiche),
                         (SELECT string_agg(j.description, ' ') FROM fiche_jonction j WHERE j.id_fiche = f2.id_fiche),
                         (SELECT string_agg(fi.valeur_texte, ' ') FROM fiche_finition fi WHERE fi.id_fiche = f2.id_fiche),
                         (SELECT concat_ws(' ',
                                 round(cd.slu_m, 2)::text, replace(round(cd.slu_m, 2)::text, '.', ','),
                                 round(cd.sle_m, 2)::text, replace(round(cd.sle_m, 2)::text, '.', ','),
                                 round(cd.sf_m, 2)::text, replace(round(cd.sf_m, 2)::text, '.', ','),
                                 round(cd.shw_m, 2)::text, replace(round(cd.shw_m, 2)::text, '.', ','),
                                 round(cd.spa_m2, 2)::text, replace(round(cd.spa_m2, 2)::text, '.', ','),
                                 round(cd.tetiere_cm, 2)::text, replace(round(cd.tetiere_cm, 2)::text, '.', ','),
                                 round(cd.poids_kg, 2)::text, replace(round(cd.poids_kg, 2)::text, '.', ',')
                          ) FROM fiche_cotes cd WHERE cd.id_fiche = f2.id_fiche AND cd.jeu = 'finie')
               ) AS secondaire
        FROM fiche f2
    ) AS agg
    WHERE f.id_fiche = agg.id_fiche;
    GET DIAGNOSTICS nb = ROW_COUNT;
    RETURN nb;
END;
$fn$;

-- Backfill global (à la 018 : rattraper les fiches déjà posées) : TOUTES les
-- fiches sont rafraîchies, leur texte doit porter les deux formes de cotes.
DO $seamtech_backfill$
BEGIN
    PERFORM rafraichir_texte_recherche_toutes();
END
$seamtech_backfill$;
"""

SQL_020_FILE_DURABLE = """
-- ============================================================================
-- 020_file_durable — imports durables et traçables (file Redis + worker séparé)
-- ============================================================================
-- Motivations mesurées (voir docs/FILE_DURABLE.md) :
--   * un job dont le worker meurt était marqué « failed » sans trace de la
--     tentative ni du worker propriétaire : impossible de savoir qui traitait
--     quoi, ni combien de fois ;
--   * le lot multi-dossiers (porte A bis) restait « en_cours » à jamais après
--     un redémarrage du serveur web : aucun état terminal, aucune reprise ;
--   * une livraison dupliquée (redelivery Redis) relançait l'import complet.
--
-- La BASE reste le registre de vérité (le prompt de mission l'exige) : ces
-- colonnes décrivent l'état durable d'un job, Redis ne fait que transporter.
-- Toutes additives et idempotentes : un rejeu après échec partiel est sans
-- effet, et une base déjà à jour n'est pas modifiée.

ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS claimed_by TEXT;
ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS failure_reason TEXT;
-- durability : "durable" (accepté par la file Redis), "process_memory"
-- (repli de développement, perdu si le processus meurt), "sync" (traité dans
-- la requête HTTP). Un état « accepté durablement » ne doit jamais être
-- rapporté pour un job resté en mémoire.
ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS durability TEXT NOT NULL DEFAULT 'durable';

-- Les états terminaux doivent être lisibles sans jointure : index partiel sur
-- les jobs actifs (requête de supervision : combien de jobs en vol, qui les
-- détient, depuis quand).
CREATE INDEX IF NOT EXISTS idx_import_jobs_actifs
    ON import_jobs (status, heartbeat_at DESC)
    WHERE status IN ('pending', 'running');

-- Statut de lot : ajout de « annule » (arrêt volontaire, distinct d'un échec)
-- et de « relance » (lot repris après une interruption). Le CHECK d'origine
-- est remplacé, jamais contourné : une valeur inconnue reste refusée.
ALTER TABLE lot_import DROP CONSTRAINT IF EXISTS lot_import_statut_check;
ALTER TABLE lot_import ADD CONSTRAINT lot_import_statut_check
    CHECK (statut IN ('en_cours', 'termine', 'interrompu', 'annule', 'relance'));
ALTER TABLE lot_import ADD COLUMN IF NOT EXISTS annule_le TIMESTAMPTZ;
-- Le worker propriétaire d'un lot : sans cette colonne, un lot resté
-- « en_cours » après la mort d'un worker est indistinguable d'un lot en cours.
ALTER TABLE lot_import ADD COLUMN IF NOT EXISTS worker_id TEXT;
ALTER TABLE lot_import ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
"""

SQL_021_REVISION_FICHE = """
-- ============================================================================
-- 021_revision_fiche — verrou optimiste : deux postes ne s'écrasent plus
-- ============================================================================
-- Défaut constaté par la revue indépendante du 2026-10-07 : `corriger_champ`
-- écrivait un UPDATE de fiche_champ_extrait filtre par identifiant de champ,
-- SANS comparer
-- l'état lu. Deux opérateurs ouvrant la même fiche (deux postes, deux onglets)
-- et corrigeant le même champ aboutissaient donc à « dernier écrivain gagne » :
-- la correction de A disparaissait sans trace, et B n'en était jamais averti.
--
-- Cette colonne porte le NUMÉRO DE RÉVISION d'une fiche. Toute écriture qui
-- modifie son contenu (correction d'un champ, validation, rejet, réouverture)
-- l'incrémente ; un client qui renvoie une révision périmée est REFUSÉ (409)
-- au lieu d'écraser le travail du collègue. La base reste le registre de
-- vérité : c'est elle qui arbitre, jamais l'interface.
--
-- Additive et idempotente : une base déjà à jour n'est pas modifiée, et les
-- fiches existantes démarrent à la révision 1 (aucune donnée à convertir).

ALTER TABLE fiche ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;

-- Une révision négative ou nulle serait un état impossible : le CHECK le rend
-- impossible aussi en base (défense en profondeur, pas seulement côté code).
ALTER TABLE fiche DROP CONSTRAINT IF EXISTS fiche_revision_positive;
ALTER TABLE fiche ADD CONSTRAINT fiche_revision_positive CHECK (revision >= 1);
"""

SQL_022_SELECTIONS_DURABLES = """
-- ============================================================================
-- 022_selections_durables — la reprise ne substitue plus un autre document
-- ============================================================================
-- Défaut constaté par l'audit de préparation du 2026-10-08 (A06) : les
-- sélections MANUELLES de l'opérateur (le PDF « fiche » choisi, le classeur
-- Excel choisi) ne vivaient que dans la charge de la tâche Redis. Quand cette
-- charge disparaissait (Redis redémarré sans persistance, entrée évincée), la
-- réconciliation ré-enfilait le job sans elles — et l'import repartait sur
-- « le premier PDF trouvé », c'est-à-dire un AUTRE document, sans que rien ne
-- le signale. Le registre de vérité (la base) doit porter ce qui définit le
-- travail à refaire.
--
-- Additive et idempotente : une base déjà à jour n'est pas modifiée, et les
-- jobs existants gardent NULL (= « aucune sélection manuelle enregistrée »),
-- ce qui laisse la reprise se comporter comme avant POUR CES JOBS-LÀ sans
-- prétendre qu'ils avaient une sélection.

ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS selected_pdf TEXT;
ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS selected_excel TEXT;
"""

# ---------------------------------------------------------------------------
# 023 — Clé de recherche normalisée (casse + diacritiques + ligatures)
# ---------------------------------------------------------------------------
# Constat A-03 (recette 2026-10-12) : l'index tsvector (seamtech_unaccent) plie
# déjà les accents, mais la source trigrammes comparait le texte BRUT. Une
# requête « GENOIS » et « GÉNOIS » produisaient donc des votes RRF différents
# (word_similarity littérale). Ce n'est PAS un cas « génois » : toute paire
# ne différant que par la casse ou les diacritiques latin doit renvoyer les
# mêmes identifiants, dans le même ordre déterministe.
#
# Pourquoi une FONCTION IMMUTABLE plutôt que unaccent() nu dans les index :
# unaccent() est STABLE (dictionnaire chargeable). L'enveloppe IMMUTABLE
# (forme à deux arguments, dictionnaire nommé) est le motif PostgreSQL
# documenté pour indexer unaccent. Limite assumée : si l'exploitant CHANGE
# le dictionnaire unaccent.rules, les index d'expression deviennent faux
# jusqu'à REINDEX — opération d'exploitation, pas un chemin applicatif.
#
# Miroir Python : seamtech_search.fiches.normalisation.normaliser_cle_recherche
# (NFD + retrait des combinants + casefold + ligatures œ/æ + ß). Les deux
# doivent rester alignés. Pas de translittération arabe/cyrillique.
SQL_023_FONCTION_UNACCENT = """
CREATE OR REPLACE FUNCTION seamtech_texte_recherche(t TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = public, pg_temp
AS $fonction$
    SELECT btrim(
        replace(replace(replace(
            lower(
                regexp_replace(
                    translate(
                        public.unaccent('public.unaccent'::regdictionary, coalesce(t, '')),
                        E'\\u00a0\\u202f\\u2007\\u2009',
                        '    '
                    ),
                    '[[:space:]]+', ' ', 'g'
                )
            ),
            'œ', 'oe'),
            'æ', 'ae'),
            'ß', 'ss')
    )
$fonction$;

COMMENT ON FUNCTION seamtech_texte_recherche(TEXT) IS
    'Clé de recherche (casse pliée, diacritiques latin via unaccent, ligatures œ/æ/ß, espaces comprimés, NFC≡NFD) — IMMUTABLE donc indexable ; miroir Python : seamtech_search.fiches.normalisation.normaliser_cle_recherche';
"""

# Repli si unaccent n'est pas installable : translate() du latin-1 + latin
# étendu A (1 caractère → 1 caractère) + ligatures par replace(). Couverture
# plus étroite qu'unaccent (ø, ł, etc. sont couverts ; pas les symboles du
# dictionnaire unaccent). Documenté, jamais silencieux.
SQL_023_FONCTION_REPLI = r"""
CREATE OR REPLACE FUNCTION seamtech_texte_recherche(t TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = public, pg_temp
AS $fonction$
    SELECT btrim(
        replace(replace(replace(
            lower(
                regexp_replace(
                    translate(
                        coalesce(t, ''),
                        'ÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜÝàáâãäåèéêëìíîïòóôöõùúûüýÿÇçÑñØøŸĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĔĕĖėĘęĚěĜĝĞğĠġĢģĤĥĦħĨĩĪīĬĭĮįİıĴĵĶķĹĺĻļĽľĿŀŁłŃńŅņŇňŌōŎŏŐőŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŦŧŨũŪūŬŭŮůŰűŲųŴŵŶŷŹźŻżŽž' || E'\u00a0\u202f\u2007\u2009',
                        'AAAAAAEEEEIIIIOOOOOUUUUYaaaaaaeeeeiiiiooooouuuuyyCcNnOoYAaAaAaCcCcCcCcDdDdEeEeEeEeEeGgGgGgGgHhHhIiIiIiIiIiJjKkLlLlLlLlLlNnNnNnOoOoOoRrRrRrSsSsSsSsTtTtTtUuUuUuUuUuUuWwYyZzZzZz    '
                    ),
                    '[[:space:]]+', ' ', 'g'
                )
            ),
            'œ', 'oe'),
            'æ', 'ae'),
            'ß', 'ss')
    )
$fonction$;

COMMENT ON FUNCTION seamtech_texte_recherche(TEXT) IS
    'Clé de recherche (repli sans unaccent : translate latin-1 + étendu A) — IMMUTABLE ; miroir Python : normaliser_cle_recherche';
"""

SQL_023_INDEX = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Index de RECHERCHE : la comparaison trigrammes porte sur la clé normalisée,
-- des deux côtés (requête et colonne). Les index bruts de 007 (titre/code/
-- champs_texte nus) ne servent plus cette source.
DROP INDEX IF EXISTS idx_fiche_code_trgm;
DROP INDEX IF EXISTS idx_fiche_champs_trgm;

CREATE INDEX IF NOT EXISTS idx_fiche_code_trgm_norm
    ON fiche USING gin (seamtech_texte_recherche(code) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fiche_titre_trgm_norm
    ON fiche USING gin (seamtech_texte_recherche(titre) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fiche_champs_trgm_norm
    ON fiche USING gin (seamtech_texte_recherche(champs_texte) gin_trgm_ops);

-- idx_fiche_titre_trgm : 007 le créait sur titre NU ; 016 voulait
-- seamtech_titre_normalise(titre) pour la dédup, mais IF NOT EXISTS sautait
-- si 007 avait déjà le nom. On le recrée sur l'expression de dédup.
DROP INDEX IF EXISTS idx_fiche_titre_trgm;
CREATE INDEX IF NOT EXISTS idx_fiche_titre_trgm
    ON fiche USING gin (seamtech_titre_normalise(titre) gin_trgm_ops);

-- Suggestions : mêmes clés normalisées sur les référentiels.
DROP INDEX IF EXISTS idx_type_voile_libelle_trgm;
DROP INDEX IF EXISTS idx_client_nom_trgm;
DROP INDEX IF EXISTS idx_bateau_nom_trgm;
DROP INDEX IF EXISTS idx_materiau_nom_trgm;
DROP INDEX IF EXISTS idx_fiche_gamme_trgm;

CREATE INDEX IF NOT EXISTS idx_type_voile_libelle_trgm_norm
    ON type_voile USING gin (seamtech_texte_recherche(libelle) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_client_nom_trgm_norm
    ON client USING gin (seamtech_texte_recherche(nom) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_bateau_nom_trgm_norm
    ON bateau USING gin (seamtech_texte_recherche(nom) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_materiau_nom_trgm_norm
    ON materiau USING gin (seamtech_texte_recherche(nom) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fiche_gamme_trgm_norm
    ON fiche USING gin (seamtech_texte_recherche(gamme) gin_trgm_ops);
"""

# 024 — Lot 0a : les sessions nominatives vivent dans Redis (invalidation
# immédiate) et les anciens jetons `v1.*` sont rejetés. Aucune DDL : la
# migration est un marqueur de séquence et de déploiement. Elle évite aussi un
# trou 024 avant la 025, que les gardes de sauvegarde refuseraient.
SQL_024_SESSIONS_REDIS = """
-- 024_sessions_redis : marqueur sans DDL (état de session porté par Redis).
SELECT 1;
"""

# 025 — Lot 0b : journal d'audit canonique PostgreSQL.
#
# Invariants portés par la base, pas seulement par le code applicatif :
# - une ligne par événement, jamais modifiée ni supprimée (trigger) ;
# - chaque ligne porte l'empreinte SHA-256 de son contenu canonique et celle
#   de la ligne précédente : une altération hors API casse la chaîne ;
# - l'acteur est structuré (type, compte, session, rôle) sans jamais stocker
#   un jeton ou un mot de passe ;
# - la ressource est à la fois lisible (`resource`, compatibilité historique)
#   et adressable (`resource_type` + `resource_id`) pour les contrôles IDOR.
SQL_025_AUDIT_LOGS = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id_audit             BIGSERIAL PRIMARY KEY,
    cree_le              TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id           TEXT,
    actor_type           TEXT NOT NULL DEFAULT 'systeme'
                         CHECK (actor_type IN ('utilisateur', 'secours', 'service', 'systeme', 'anonyme')),
    id_utilisateur       BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    identifiant          TEXT,
    role                 TEXT,
    id_session           BIGINT REFERENCES session_ui(id_session) ON DELETE SET NULL,
    actor                TEXT NOT NULL,
    action               TEXT NOT NULL,
    resource_type        TEXT NOT NULL DEFAULT '',
    resource_id          TEXT NOT NULL DEFAULT '',
    resource             TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'success',
    code_statut          INTEGER CHECK (code_statut IS NULL OR (code_statut BETWEEN 100 AND 599)),
    adresse_ip           TEXT,
    user_agent           TEXT,
    details              JSONB NOT NULL DEFAULT '{}'::jsonb,
    empreinte_precedente TEXT CHECK (
                             empreinte_precedente IS NULL
                             OR empreinte_precedente ~ '^[0-9a-f]{64}$'
                         ),
    empreinte            TEXT NOT NULL CHECK (empreinte ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_cree_le
    ON audit_logs (cree_le DESC, id_audit DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_action
    ON audit_logs (actor, action, cree_le DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_identifiant
    ON audit_logs (identifiant, cree_le DESC)
    WHERE identifiant IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_logs_resource
    ON audit_logs (resource_type, resource_id, cree_le DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_request_id
    ON audit_logs (request_id)
    WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_logs_empreinte
    ON audit_logs (empreinte);

CREATE OR REPLACE FUNCTION seamtech_audit_logs_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $fonction$
BEGIN
    RAISE EXCEPTION 'audit_logs est append-only : opération % interdite', TG_OP
        USING ERRCODE = '42501';
END;
$fonction$;

DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs;
CREATE TRIGGER trg_audit_logs_append_only
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW
    EXECUTE FUNCTION seamtech_audit_logs_append_only();

COMMENT ON TABLE audit_logs IS
    'Journal d''audit canonique du Lot 0b : append-only, chaîné par empreinte SHA-256, sans secret en clair.';
"""

# 026 — Lot 2 : référentiel canonique des modèles de bateaux et recherche
# étendue. La migration est additive : les libellés `bateau.nom` restent la
# source historique, `boat_model_id` les rattache à une identité canonique.
# Le backfill des groupes (casse/accents/ponctuation/abréviations ponctuées)
# est fait en Python par `synchroniser_referentiel_bateaux`, car la clé
# canonique est une règle métier testée — pas une approximation SQL. Les
# suffixes distinctifs (`45`, `45.1`, `45 CC`) produisent des clés distinctes.
SQL_026_BOAT_MODELS_ALIASES = """
CREATE TABLE IF NOT EXISTS boat_models (
    id_modele          BIGSERIAL PRIMARY KEY,
    nom_canonique      TEXT NOT NULL,
    cle_normalisee     TEXT NOT NULL UNIQUE CHECK (cle_normalisee <> ''),
    source             TEXT NOT NULL DEFAULT 'manuel',
    actif              BOOLEAN NOT NULL DEFAULT true,
    id_modele_fusion   BIGINT REFERENCES boat_models(id_modele) ON DELETE SET NULL,
    fusionne_le        TIMESTAMPTZ,
    fusionne_par       BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    cree_par           BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (id_modele_fusion IS NULL OR id_modele_fusion <> id_modele),
    CHECK ((id_modele_fusion IS NULL) = (fusionne_le IS NULL))
);

CREATE TABLE IF NOT EXISTS boat_model_aliases (
    id_alias           BIGSERIAL PRIMARY KEY,
    id_modele          BIGINT NOT NULL REFERENCES boat_models(id_modele) ON DELETE CASCADE,
    alias_brut         TEXT NOT NULL,
    cle_normalisee     TEXT NOT NULL UNIQUE CHECK (cle_normalisee <> ''),
    source             TEXT NOT NULL DEFAULT 'manuel',
    cree_par           BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS boat_model_merge_history (
    id_historique          BIGSERIAL PRIMARY KEY,
    action                 TEXT NOT NULL CHECK (action IN ('fusion', 'defusion')),
    id_modele_source       BIGINT NOT NULL REFERENCES boat_models(id_modele) ON DELETE RESTRICT,
    id_modele_cible        BIGINT NOT NULL REFERENCES boat_models(id_modele) ON DELETE RESTRICT,
    id_historique_origine  BIGINT REFERENCES boat_model_merge_history(id_historique) ON DELETE SET NULL,
    effectue_par           BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    motif                  TEXT,
    instantane             JSONB NOT NULL DEFAULT '{}'::jsonb,
    cree_le                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (id_modele_source <> id_modele_cible)
);

CREATE TABLE IF NOT EXISTS search_synonyms (
    id_synonyme        BIGSERIAL PRIMARY KEY,
    terme              TEXT NOT NULL,
    terme_normalise    TEXT NOT NULL CHECK (terme_normalise <> ''),
    cible              TEXT NOT NULL,
    cible_normalisee   TEXT NOT NULL CHECK (cible_normalisee <> ''),
    domaine            TEXT,
    source             TEXT NOT NULL DEFAULT 'manuel',
    actif              BOOLEAN NOT NULL DEFAULT true,
    cree_par           BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (terme_normalise, cible_normalisee)
);

ALTER TABLE bateau
    ADD COLUMN IF NOT EXISTS boat_model_id BIGINT
    REFERENCES boat_models(id_modele) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_bateau_boat_model
    ON bateau (boat_model_id) WHERE boat_model_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_boat_model_aliases_modele
    ON boat_model_aliases (id_modele);
CREATE INDEX IF NOT EXISTS idx_boat_model_merge_history_source
    ON boat_model_merge_history (id_modele_source, cree_le DESC);
CREATE INDEX IF NOT EXISTS idx_boat_model_merge_history_cible
    ON boat_model_merge_history (id_modele_cible, cree_le DESC);
CREATE INDEX IF NOT EXISTS idx_search_synonyms_terme
    ON search_synonyms (terme_normalise) WHERE actif;

DO $lot2_trgm$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_boat_models_cle_trgm '
             || 'ON boat_models USING gin (cle_normalisee gin_trgm_ops)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_boat_model_aliases_cle_trgm '
             || 'ON boat_model_aliases USING gin (cle_normalisee gin_trgm_ops)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_search_synonyms_terme_trgm '
             || 'ON search_synonyms USING gin (terme_normalise gin_trgm_ops)';
    END IF;
END
$lot2_trgm$;

INSERT INTO search_synonyms
    (terme, terme_normalise, cible, cible_normalisee, domaine, source)
VALUES
    ('GV', 'gv', 'Grand-voile', 'grand voile', 'type_voile', 'lot2'),
    ('spi', 'spi', 'Spinnaker', 'spinnaker', 'type_voile', 'lot2'),
    ('gen', 'gen', 'Génois', 'genois', 'type_voile', 'lot2'),
    ('genoa', 'genoa', 'Génois', 'genois', 'type_voile', 'lot2'),
    ('voile de portant', 'voile de portant', 'Spinnaker', 'spinnaker', 'type_voile', 'lot2'),
    ('spinnaker asymétrique', 'spinnaker asymetrique', 'Spi Asymétrique', 'spi asymetrique', 'type_voile', 'lot2'),
    ('m2', 'm 2', 'm²', 'm²', 'unite', 'lot2')
ON CONFLICT (terme_normalise, cible_normalisee) DO NOTHING;

INSERT INTO search_synonyms
    (terme, terme_normalise, cible, cible_normalisee, domaine, source)
SELECT terme,
       seamtech_texte_recherche(terme),
       cible,
       seamtech_texte_recherche(cible),
       domaine,
       'legacy_synonyme'
FROM synonyme
ON CONFLICT (terme_normalise, cible_normalisee) DO NOTHING;

COMMENT ON TABLE boat_models IS
    'Référentiel canonique unique des modèles de bateaux (Lot 2).';
COMMENT ON TABLE boat_model_aliases IS
    'Formes rencontrées ou apprises désignant un modèle canonique (Lot 2).';
COMMENT ON TABLE boat_model_merge_history IS
    'Fusions et défusions de modèles, avec instantané de restauration (Lot 2).';
COMMENT ON TABLE search_synonyms IS
    'Synonymes de recherche normalisés et versionnés (Lot 2).';
"""

# 027 — Lot 1 : quatre rôles canoniques et approbations instantanées.
#
# La conversion préserve les capacités historiques : avant Lot 1, un
# ``operateur`` corrigeait et validait. Le convertir en ``utilisateur``
# pourrait laisser une installation sans aucun approbateur et retirer en
# silence une capacité métier ; il devient donc ``approbateur``. Un admin
# peut ensuite rétrograder explicitement les comptes concernés. Toute valeur
# inconnue tombe fermée en ``lecture``. Toutes les sessions PostgreSQL
# existantes sont révoquées : un cookie émis sous l'ancien vocabulaire ne
# doit pas continuer avec une sémantique de rôle différente.
SQL_027_ROLES_APPROBATIONS = """
UPDATE utilisateur
SET role = CASE lower(btrim(role))
    WHEN 'administrateur' THEN 'admin'
    WHEN 'operateur' THEN 'approbateur'
    WHEN 'admin' THEN 'admin'
    WHEN 'lecture' THEN 'lecture'
    WHEN 'utilisateur' THEN 'utilisateur'
    WHEN 'approbateur' THEN 'approbateur'
    ELSE 'lecture'
END;

UPDATE session_ui SET revoque_le = now() WHERE revoque_le IS NULL;

ALTER TABLE utilisateur ALTER COLUMN role SET DEFAULT 'lecture';

DO $lot1_role_check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'utilisateur_role_canonique'
    ) THEN
        ALTER TABLE utilisateur
            ADD CONSTRAINT utilisateur_role_canonique
            CHECK (role IN ('admin', 'lecture', 'utilisateur', 'approbateur'));
    END IF;
END
$lot1_role_check$;

CREATE TABLE IF NOT EXISTS approval_requests (
    id_demande           BIGSERIAL PRIMARY KEY,
    id_demandeur         BIGINT NOT NULL REFERENCES utilisateur(id_utilisateur) ON DELETE RESTRICT,
    type_demande         TEXT NOT NULL CHECK (type_demande IN ('telechargement', 'modification', 'suppression')),
    statut               TEXT NOT NULL DEFAULT 'en_attente'
                         CHECK (statut IN ('en_attente', 'approuvee', 'refusee', 'annulee', 'executee', 'expiree')),
    id_fiche             BIGINT REFERENCES fiche(id_fiche) ON DELETE RESTRICT,
    id_piece             BIGINT REFERENCES documents(id) ON DELETE RESTRICT,
    champ                TEXT,
    rang                 INTEGER,
    valeur_proposee      TEXT,
    revision_fiche       INTEGER,
    motif                TEXT NOT NULL CHECK (char_length(btrim(motif)) BETWEEN 1 AND 1000),
    id_decideur          BIGINT REFERENCES utilisateur(id_utilisateur) ON DELETE SET NULL,
    decidee_le           TIMESTAMPTZ,
    commentaire_decision TEXT CHECK (commentaire_decision IS NULL OR char_length(commentaire_decision) <= 1000),
    executee_le          TIMESTAMPTZ,
    expire_le            TIMESTAMPTZ,
    resultat_execution   JSONB NOT NULL DEFAULT '{}'::jsonb,
    cree_le              TIMESTAMPTZ NOT NULL DEFAULT now(),
    maj_le               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (type_demande = 'telechargement' AND id_piece IS NOT NULL)
        OR (type_demande = 'modification' AND id_fiche IS NOT NULL AND champ IS NOT NULL
            AND valeur_proposee IS NOT NULL AND revision_fiche IS NOT NULL)
        OR (type_demande = 'suppression' AND id_fiche IS NOT NULL AND revision_fiche IS NOT NULL)
    ),
    CHECK (revision_fiche IS NULL OR revision_fiche >= 0),
    CHECK (expire_le IS NULL OR expire_le > cree_le)
);

CREATE INDEX IF NOT EXISTS idx_approval_requests_demandeur
    ON approval_requests (id_demandeur, cree_le DESC, id_demande DESC);
CREATE INDEX IF NOT EXISTS idx_approval_requests_file
    ON approval_requests (statut, cree_le, id_demande);
CREATE INDEX IF NOT EXISTS idx_approval_requests_fiche
    ON approval_requests (id_fiche, cree_le DESC, id_demande DESC)
    WHERE id_fiche IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_approval_requests_piece_active
    ON approval_requests (id_piece, statut, expire_le)
    WHERE id_piece IS NOT NULL;

-- Une seule demande en attente par objet et par demandeur : la borne est dans
-- la base, pas seulement dans un pré-contrôle applicatif soumis aux courses.
CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_pending_download
    ON approval_requests (id_demandeur, id_piece)
    WHERE statut = 'en_attente' AND type_demande = 'telechargement';
CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_pending_modification
    ON approval_requests (id_demandeur, id_fiche, champ, COALESCE(rang, -1))
    WHERE statut = 'en_attente' AND type_demande = 'modification';
CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_pending_suppression
    ON approval_requests (id_demandeur, id_fiche)
    WHERE statut = 'en_attente' AND type_demande = 'suppression';

CREATE TABLE IF NOT EXISTS in_app_notifications (
    id_notification   BIGSERIAL PRIMARY KEY,
    id_destinataire   BIGINT NOT NULL REFERENCES utilisateur(id_utilisateur) ON DELETE CASCADE,
    id_demande        BIGINT REFERENCES approval_requests(id_demande) ON DELETE CASCADE,
    type_notification TEXT NOT NULL CHECK (
        type_notification IN ('nouvelle_demande', 'decision', 'execution', 'information')
    ),
    titre             TEXT NOT NULL CHECK (char_length(btrim(titre)) BETWEEN 1 AND 200),
    message           TEXT NOT NULL CHECK (char_length(btrim(message)) BETWEEN 1 AND 1000),
    lue               BOOLEAN NOT NULL DEFAULT false,
    lue_le            TIMESTAMPTZ,
    cree_le           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((lue AND lue_le IS NOT NULL) OR (NOT lue AND lue_le IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_notifications_destinataire
    ON in_app_notifications (id_destinataire, lue, cree_le DESC, id_notification DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_demande
    ON in_app_notifications (id_demande, cree_le DESC)
    WHERE id_demande IS NOT NULL;

COMMENT ON TABLE approval_requests IS
    'Demandes métier Lot 1 : téléchargement, modification ou suppression logique, décidées par un approbateur.';
COMMENT ON TABLE in_app_notifications IS
    'Notifications applicatives locales du Lot 1, sans service externe.';
"""

# 028 — Lot 3a : extraction structurée d'une voile depuis son PDF.
#
# ``sail_extractions`` est la projection normalisée et requêtable d'une
# extraction par gabarit : dimensions canoniques (m, m², kg), matériaux,
# provenance champ par champ (valeur brute/normalisée, méthode, confiance,
# page, zone) et anomalies de plausibilité. Elle ne remplace ni la fiche ni
# ``fiche_champ_extrait`` : elle donne aux lots suivants une forme stable de
# voile, indépendante des tables filles historiques.
#
# Le libellé de bateau est volontairement BRUT et non résolu dans ce lot.
# La résolution vers le référentiel canonique et les corrections humaines
# appartiennent au Lot 3b ; aucune fusion silencieuse ne doit donc être
# déduite de cette table.
SQL_028_SAIL_EXTRACTIONS = """
CREATE TABLE IF NOT EXISTS sail_extractions (
    id_extraction       BIGSERIAL PRIMARY KEY,
    cle_idempotence     TEXT NOT NULL UNIQUE
                        CHECK (char_length(btrim(cle_idempotence)) BETWEEN 1 AND 240),
    id_fiche            BIGINT REFERENCES fiche(id_fiche) ON DELETE SET NULL,
    id_document         BIGINT REFERENCES documents(id) ON DELETE SET NULL,
    code_fiche          TEXT,
    nom_fichier_source  TEXT,
    empreinte_sha256    CHAR(64)
                        CHECK (empreinte_sha256 IS NULL OR empreinte_sha256 ~ '^[0-9a-f]{64}$'),
    gabarit_code        TEXT,
    gabarit_version     INTEGER CHECK (gabarit_version IS NULL OR gabarit_version > 0),
    version_extracteur  TEXT NOT NULL CHECK (char_length(btrim(version_extracteur)) BETWEEN 1 AND 80),
    methode_principale  TEXT NOT NULL CHECK (
        methode_principale IN ('gabarit', 'gabarit_inconnu', 'ocr', 'fiche_extraite', 'inconnue')
    ),
    type_voile          TEXT,
    gamme               TEXT,
    designation_brute   TEXT,
    modele_bateau_brut  TEXT,
    taille_bateau_brute TEXT,
    slu_m               NUMERIC(10, 3) CHECK (slu_m IS NULL OR slu_m > 0),
    sle_m               NUMERIC(10, 3) CHECK (sle_m IS NULL OR sle_m > 0),
    sf_m                NUMERIC(10, 3) CHECK (sf_m IS NULL OR sf_m > 0),
    shw_m               NUMERIC(10, 3) CHECK (shw_m IS NULL OR shw_m > 0),
    spa_m2              NUMERIC(12, 3) CHECK (spa_m2 IS NULL OR spa_m2 > 0),
    tetiere_m           NUMERIC(10, 3) CHECK (tetiere_m IS NULL OR tetiere_m > 0),
    poids_kg            NUMERIC(10, 3) CHECK (poids_kg IS NULL OR poids_kg > 0),
    materiaux           JSONB NOT NULL DEFAULT '[]'::jsonb
                        CHECK (jsonb_typeof(materiaux) = 'array'),
    champs              JSONB NOT NULL DEFAULT '[]'::jsonb
                        CHECK (jsonb_typeof(champs) = 'array'),
    anomalies           JSONB NOT NULL DEFAULT '[]'::jsonb
                        CHECK (jsonb_typeof(anomalies) = 'array'),
    score_qualite       NUMERIC(5, 4)
                        CHECK (score_qualite IS NULL OR score_qualite BETWEEN 0 AND 1),
    confiance_moyenne   NUMERIC(5, 4)
                        CHECK (confiance_moyenne IS NULL OR confiance_moyenne BETWEEN 0 AND 1),
    statut_extraction   TEXT NOT NULL CHECK (
        statut_extraction IN ('complete', 'partielle', 'a_reprendre')
    ),
    statut_plausibilite TEXT NOT NULL CHECK (
        statut_plausibilite IN ('plausible', 'a_verifier', 'implausible', 'indeterminee')
    ),
    extrait_le          TIMESTAMPTZ NOT NULL DEFAULT now(),
    cree_le             TIMESTAMPTZ NOT NULL DEFAULT now(),
    maj_le              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sail_extractions_fiche
    ON sail_extractions (id_fiche, extrait_le DESC, id_extraction DESC)
    WHERE id_fiche IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sail_extractions_document
    ON sail_extractions (id_document, extrait_le DESC, id_extraction DESC)
    WHERE id_document IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sail_extractions_empreinte
    ON sail_extractions (empreinte_sha256)
    WHERE empreinte_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sail_extractions_code
    ON sail_extractions (code_fiche)
    WHERE code_fiche IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sail_extractions_a_reprendre
    ON sail_extractions (statut_extraction, statut_plausibilite, cree_le, id_extraction)
    WHERE statut_extraction <> 'complete' OR statut_plausibilite <> 'plausible';

COMMENT ON TABLE sail_extractions IS
    'Extractions de voiles normalisées du Lot 3a : dimensions canoniques, matériaux, provenance et plausibilité. Le modèle de bateau reste brut et non résolu.';
COMMENT ON COLUMN sail_extractions.modele_bateau_brut IS
    'Libellé de bateau tel que lu dans le PDF, sans résolution canonique (Lot 3b).';
COMMENT ON COLUMN sail_extractions.champs IS
    'Provenance champ par champ : valeurs brute et canonique, unités, méthode, confiance, page et zone.';
"""

MIGRATIONS_METIER: tuple[tuple[str, str], ...] = (
    ("006_fiche_technique", SQL_006_FICHE_TECHNIQUE),
    ("007_recherche_index", SQL_007_RECHERCHE_INDEX),
    ("008_ml_corpus", SQL_008_ML_CORPUS),
    ("009_qualite_et_gabarits", SQL_009_QUALITE_ET_GABARITS),
    ("010_lots_ingestion", SQL_010_LOTS_INGESTION),
    ("011_pieces_catalogue_documents", SQL_011_PIECES_CATALOGUE_DOCUMENTS),
    ("012_recherche_hybride", SQL_012_RECHERCHE_HYBRIDE),
    ("013_recherche_fonds_reel", SQL_013_RECHERCHE_FONDS_REEL),
    ("014_facette_dimension", SQL_014_FACETTE_DIMENSION),
    ("015_qualite_gabarit_brouillon", SQL_015_QUALITE_GABARIT_BROUILLON),
    ("016_dedup_comptes_nominatifs", SQL_016_DEDUP_COMPTES_NOMINATIFS),
    ("017_ocr_etage3", SQL_017_OCR_ETAGE3),
    ("018_recherche_a_valider", SQL_018_RECHERCHE_A_VALIDER),
    ("019_recherche_dimension", SQL_019_RECHERCHE_DIMENSION),
    ("020_file_durable", SQL_020_FILE_DURABLE),
    ("021_revision_fiche", SQL_021_REVISION_FICHE),
    ("022_selections_durables", SQL_022_SELECTIONS_DURABLES),
    ("023_recherche_normalisee", SQL_023_FONCTION_UNACCENT),
    ("024_sessions_redis", SQL_024_SESSIONS_REDIS),
    ("025_audit_logs", SQL_025_AUDIT_LOGS),
    ("026_boat_models_aliases", SQL_026_BOAT_MODELS_ALIASES),
    ("027_roles_approbations", SQL_027_ROLES_APPROBATIONS),
    ("028_sail_extractions", SQL_028_SAIL_EXTRACTIONS),
)


def diagnostic_metier(cursor: Any) -> dict[str, Any]:  # noqa: ANN401 - curseur psycopg2 réel
    """Diagnostic exposé par /health : version de schéma + extensions réelles.

    « Présence effective » : on interroge pg_extension, pas la configuration —
    c'est ce qui permet de voir en un coup d'œil si une installation est à
    jour et capable de porter les fiches.
    """
    cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
    versions = [str(ligne[0]) for ligne in cursor.fetchall()]
    cursor.execute("SELECT extname FROM pg_extension WHERE extname = ANY(%s)", (["vector", "pg_trgm", "unaccent"],))
    presentes = {str(ligne[0]) for ligne in cursor.fetchall()}
    return {
        "schema_migrations": versions,
        "schema_metier_a_jour": VERSION_SCHEMA_METIER in versions,
        "extensions": {
            "vector": "vector" in presentes,
            "pg_trgm": "pg_trgm" in presentes,
            "unaccent": "unaccent" in presentes,
            # Symétrie avec la branche SQLite de health_details : sur
            # PostgreSQL, les extensions sont applicables (c'est le backend).
            "applicables": True,
        },
    }
