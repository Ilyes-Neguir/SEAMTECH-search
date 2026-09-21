"""Fixtures partagées du Lot E — recherche hybride (plan v3.0 §17.2).

``base_recherche`` : base PostgreSQL JETABLE (créée puis supprimée par le
test), migrée 001→012, avec un corpus de voilerie semé de façon
déterministe : référentiels, 12 fiches validées + 2 a_valider + 1 rejetée,
chunks de texte PDF, documents avec embeddings. Les fiches validées ont leur
texte de recherche pondéré rempli par la VRAIE fonction de migration — la
recherche ne trouve jamais une fiche non validée par accident d'index.

Chaque test crée sa propre base : aucune dépendance entre exécutions.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

# Corpus déterministe (pas de tirage aléatoire : les attendus des tests sont
# écrits en clair à partir de ces constantes).
TYPES_VOILE = (
    ("GV", "Grand-voile", "voile_triangulaire"),
    ("GEN", "Génois", "voile_avant"),
    ("SPI", "Spinnaker", "voile_porteuse"),
    ("TRM", "Tourmentin", "voile_avant"),
)
MATERIAUX = (("Monofilm", "film", "150.0"), ("Dacron", "tissu", "220.0"), ("Mylar", "film", "100.0"))
CLIENTS = (("Voilerie Atlantique", "Brest"), ("Chantier Méditerranée", "La Ciotat"))
BATEAUX = (("First 30", "30 pieds"), ("Sun Fast 36", "36 pieds"), ("Figaro 3", "29 pieds"))

# 15 fiches : 12 validées (cherchables), 2 a_valider, 1 rejetée.
# (code, titre, type, gamme, client, bateau, matiere, annee, notes, statut)
FICHES_CORPUS: tuple[tuple[str, str, str, str, int, int, int, int, str, str], ...] = (
    ("0701-GV-001", "Grand-voile régate First 30", 0, "Régate", 0, 0, 0, 2024, "", "valide"),
    ("0701-GV-002", "Grand-voile croisière Sun Fast 36", 0, "Croisière", 0, 1, 1, 2023, "", "valide"),
    ("0702-GV-003", "Grand-voile lattée Figaro 3", 0, "Régate", 1, 2, 0, 2025, "", "valide"),
    ("0801-GEN-001", "Génois lourd First 30", 1, "Croisière", 0, 0, 1, 2023, "", "valide"),
    ("0801-GEN-002", "Génois médium Sun Fast 36", 1, "Régate", 1, 1, 2, 2024, "", "valide"),
    ("0802-GEN-003", "Génois léger Figaro 3", 1, "Régate", 1, 2, 2, 2025, "", "valide"),
    ("0812-SPI-001", "Spinnaker asymétrique Sun Fast 36", 2, "Régate", 0, 1, 2, 2024, "", "valide"),
    ("0812-SPI-002", "Spinnaker symétrique First 30", 2, "Course", 1, 0, 2, 2023, "", "valide"),
    ("0901-TRM-001", "Tourmentin Dacron First 30", 3, "Croisière", 0, 0, 1, 2022, "", "valide"),
    ("0902-GV-004", "Grand-voile coupe triradiale", 0, "Régate", 1, 1, 0, 2025, "", "valide"),
    ("0903-GEN-004", "Génois solent croisière", 1, "Croisière", 0, 2, 1, 2024, "", "valide"),
    # La fiche « notes » : le mot régatier n'existe QUE dans les notes (poids C).
    ("0904-GV-005", "Grand-voile coupe horizontale", 0, "Croisière", 0, 0, 1, 2023,
     "profil régatier demandé par le client malgré la gamme croisière", "valide"),
    ("1001-GV-006", "Grand-voile en attente de validation", 0, "Régate", 0, 0, 0, 2025, "", "a_valider"),
    ("1002-GEN-005", "Génois en attente de validation", 1, "Croisière", 1, 1, 1, 2025, "", "a_valider"),
    ("1003-SPI-003", "Spinnaker rejeté contrôle surface", 2, "Régate", 0, 2, 2, 2025, "", "rejete"),
)

# Texte PDF (chunks) — mots volontairement absents des champs métier.
CHUNKS = (
    ("0812-SPI-002", "croquis", "renforts de guindant en kevlar, lattes forcées sur la bordure"),
    ("0701-GV-001", "croquis", "coulisseaux de guindant montés sur rail, nerf de chute spectra"),
)

# Embeddings synthétiques (384 dimensions) pour la branche vectorielle.
EMBEDDING_A = "[" + ",".join(["1"] + ["0"] * 383) + "]"
EMBEDDING_B = "[" + ",".join(["0", "1"] + ["0"] * 382) + "]"
# documents.embedding : deux fiches portent un vecteur (la branche vecteurs du
# RRF est testable sans encodeur de production — le Lot F livrera le vrai).
EMBEDDINGS_DOCUMENTS = {"0701-GV-001": EMBEDDING_A, "0812-SPI-001": EMBEDDING_B}


def _creer_base_jetable() -> tuple[str, str]:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"recherche_test_{uuid.uuid4().hex[:10]}"
    administrateur = psycopg2.connect(DATABASE_URL)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom_base)))
    finally:
        administrateur.close()
    partie = DATABASE_URL.rsplit("/", 1)
    return nom_base, f"{partie[0]}/{nom_base}"


def _supprimer_base_jetable(nom_base: str) -> None:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nettoie = psycopg2.connect(DATABASE_URL)
    nettoie.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with nettoie.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom_base)))
    finally:
        nettoie.close()


def semer_corpus(index: Any) -> dict[str, int]:  # noqa: ANN401 - SearchIndex réel
    """Sème le corpus ; rend {code: id_fiche}. Les fiches validées passent par
    la vraie fonction de rafraîchissement — l'état réel de production."""
    from seamtech_search.recherche import invalider_cache_synonymes

    invalider_cache_synonymes()
    ids: dict[str, int] = {}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            for code, libelle, famille in TYPES_VOILE:
                cursor.execute(
                    "INSERT INTO type_voile (code, libelle, famille) VALUES (%s, %s, %s)",
                    (code, libelle, famille),
                )
            for nom, famille, grammage in MATERIAUX:
                cursor.execute(
                    "INSERT INTO materiau (nom, famille, grammage_g_m2) VALUES (%s, %s, %s)",
                    (nom, famille, grammage),
                )
            ids_clients = []
            for nom, chantier in CLIENTS:
                cursor.execute("INSERT INTO client (nom, chantier) VALUES (%s, %s) RETURNING id_client", (nom, chantier))
                ids_clients.append(int(cursor.fetchone()[0]))
            ids_bateaux = []
            for nom, taille in BATEAUX:
                cursor.execute("INSERT INTO bateau (nom, taille) VALUES (%s, %s) RETURNING id_bateau", (nom, taille))
                ids_bateaux.append(int(cursor.fetchone()[0]))

            for (code, titre, i_type, gamme, i_client, i_bateau, i_matiere, annee, notes, statut) in FICHES_CORPUS:
                cursor.execute(
                    "INSERT INTO fiche (code, titre, id_type_voile, gamme, id_client, id_bateau,"
                    " date_edition, notes, statut) "
                    "VALUES (%s, %s, (SELECT id_type_voile FROM type_voile ORDER BY id_type_voile OFFSET %s LIMIT 1),"
                    " %s, %s, %s, make_date(%s, 6, 15), %s, %s) RETURNING id_fiche",
                    (code, titre, i_type, gamme, ids_clients[i_client], ids_bateaux[i_bateau], annee, notes, statut),
                )
                id_fiche = int(cursor.fetchone()[0])
                ids[code] = id_fiche
                # id_materiau EST renseigné : la facette « matière » joint
                # fiche_materiau → materiau (un lien NULL la rendrait aveugle).
                cursor.execute(
                    "INSERT INTO fiche_materiau (id_fiche, role, niveau, id_materiau, designation_texte) "
                    "VALUES (%s, 'panneau', 1, "
                    "(SELECT id_materiau FROM materiau ORDER BY id_materiau OFFSET %s LIMIT 1), "
                    "%s || ' ' || (SELECT grammage_g_m2 FROM materiau ORDER BY id_materiau OFFSET %s LIMIT 1) || 'g')",
                    (id_fiche, i_matiere, MATERIAUX[i_matiere][0], i_matiere),
                )
                cursor.execute(
                    "INSERT INTO fiche_galon (id_fiche, bande, couleur, matiere) VALUES (%s, 'guindant', 'bleu', 'polyester')",
                    (id_fiche,),
                )

            for code, nature, contenu in CHUNKS:
                cursor.execute(
                    "INSERT INTO chunk (id_fiche, nature, contenu) VALUES (%s, %s, %s)",
                    (ids[code], nature, contenu),
                )
            for code, embedding in EMBEDDINGS_DOCUMENTS.items():
                cursor.execute(
                    "INSERT INTO documents (path_key, path, name, parent_path, extension, size,"
                    " modified_at, is_dir, content, search_vector, id_fiche, role, embedding) "
                    "VALUES (%s, %s, %s, %s, '.pdf', 1, %s, FALSE, %s,"
                    " to_tsvector('simple', %s), %s, 'fiche', %s::vector)",
                    (
                        f"/archive/{code}/fiche.pdf", f"/archive/{code}/fiche.pdf",
                        "fiche.pdf", f"/archive/{code}", time.time(),
                        f"document {code}", f"document {code}",
                        ids[code], embedding,
                    ),
                )

            # Les fiches VALIDÉES reçoivent leur texte de recherche pondéré —
            # exactement comme le fait la validation en production.
            for (code, *_reste) in FICHES_CORPUS:
                if _reste[-1] == "valide":
                    cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (ids[code],))
    return ids


@pytest.fixture()
def base_recherche() -> Iterator[dict[str, Any]]:
    """Base PostgreSQL jetable migrée + corpus semé ; rend
    {index, url, nom, fiches:{code: id_fiche}}."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    from seamtech_search.indexer import SearchIndex

    nom_base, url_base = _creer_base_jetable()
    index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    ids = semer_corpus(index)
    try:
        yield {"index": index, "url": url_base, "nom": nom_base, "fiches": ids}
    finally:
        index.close()
        _supprimer_base_jetable(nom_base)
