"""Schéma métier « fiche technique » — migrations 006 à 009 (Lot A).

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
VERSION_SCHEMA_METIER = "009_qualite_et_gabarits"

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
-- NB : la colonne générée tsv référence la configuration seamtech_unaccent
-- créée par initialize() (migration 005) — voir l'ordre d'exécution en tête
-- de module.
CREATE TABLE IF NOT EXISTS chunk (
    id_chunk     BIGSERIAL PRIMARY KEY,
    id_document  BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    id_fiche     BIGINT REFERENCES fiche(id_fiche) ON DELETE CASCADE,
    nature       TEXT NOT NULL,
    contenu      TEXT NOT NULL,
    embedding    vector(384),
    tsv          tsvector GENERATED ALWAYS AS
                 (to_tsvector('seamtech_unaccent', contenu)) STORED,
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

SQL_007_RECHERCHE_INDEX = """
-- ============================================================================
-- 007_recherche_index — index « à la Google » (plan v3.0 §11, §17.3)
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Texte de recherche par fiche : agrégat PONDÉRÉ rempli par la fonction
-- rafraichir_texte_recherche_fiche(), appelée à la VALIDATION d'une fiche
-- (une fiche à la fois — jamais en boucle ligne par ligne sur la table).
ALTER TABLE fiche ADD COLUMN IF NOT EXISTS champs_texte TEXT NOT NULL DEFAULT '';
ALTER TABLE fiche ADD COLUMN IF NOT EXISTS search_vector TSVECTOR;

CREATE INDEX IF NOT EXISTS idx_fiche_search       ON fiche USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_fiche_code_trgm    ON fiche USING GIN (code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fiche_titre_trgm   ON fiche USING GIN (titre gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fiche_champs_trgm  ON fiche USING GIN (champs_texte gin_trgm_ops);

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
            setweight(to_tsvector('seamtech_unaccent',
                                  coalesce(f.code, '') || ' ' || coalesce(f.titre, '')), 'A')
            || setweight(to_tsvector('seamtech_unaccent', agg.secondaire), 'B')
            || setweight(to_tsvector('seamtech_unaccent', coalesce(f.notes, '')), 'C')
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
MIGRATIONS_METIER: tuple[tuple[str, str], ...] = (
    ("006_fiche_technique", SQL_006_FICHE_TECHNIQUE),
    ("007_recherche_index", SQL_007_RECHERCHE_INDEX),
    ("008_ml_corpus", SQL_008_ML_CORPUS),
    ("009_qualite_et_gabarits", SQL_009_QUALITE_ET_GABARITS),
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
        },
    }
