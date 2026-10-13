"""Parité casse/accents du moteur de recherche — PostgreSQL réel.

Correction GÉNÉRALE (pas un cas « génois ») : des requêtes ne différant que
par la casse ou les diacritiques latin doivent renvoyer les MÊMES identifiants
dans le MÊME ordre. Les textes métier d'origine restent accentués.
"""

from __future__ import annotations

import os
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.fiches.normalisation import normaliser_cle_recherche
from seamtech_search.indexer import SearchIndex
from seamtech_search.recherche import rechercher_fiches, suggerer

DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres

# Familles (logique, titre, notes) — métier, client, fichier, description, mot
# ordinaire. Inclut un voisin trigramme (Gennaker) pour reproduire A-03.
FICHES = (
    ("facade", "Façade atelier nord", "description façade peinte"),
    ("reparation", "Réparation voile d'avant", "réparation urgente"),
    ("ete", "Contrôle été 2026", "campagne d'été"),
    ("noel", "Noël chantier", "fermeture Noël"),
    ("cable", "Câble d'écoute spectra", "câble dyneema"),
    ("muller", "Client Müller Voiles", "Müller voiles"),
    ("sao", "Navire São Miguel", "São Miguel Açores"),
    ("coeur", "Cœur de spi", "pièce cœur"),
    ("genois", "Génois lourd First 30", "type Génois"),
    ("gennaker", "Gennaker course médium", "Gennaker Radial"),
    ("code_cible", "Fiche code exact", "sans rapport"),
)


@pytest.fixture()
def base_norm() -> Iterator[dict[str, Any]]:
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom = f"norm_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(DATABASE_URL)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    admin.close()
    principale, sep, requete = DATABASE_URL.partition("?")
    url = principale.rsplit("/", 1)[0] + "/" + nom + (f"?{requete}" if sep else "")
    index = SearchIndex(Path(f"/tmp/unused-{nom}.db"), url)
    index.initialize()
    index.run_migrations()
    ids: dict[str, tuple[int, str]] = {}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO type_voile (code, libelle, famille) VALUES ('GEN', 'Génois', 'voile_avant')"
            )
            cursor.execute("INSERT INTO client (nom, chantier) VALUES ('Voilerie Atlantique', 'Brest')")
            cursor.execute("INSERT INTO bateau (nom, taille) VALUES ('First 30', '30 pieds')")
            cursor.execute("SELECT id_client FROM client")
            id_client = int(cursor.fetchone()[0])
            cursor.execute("SELECT id_bateau FROM bateau")
            id_bateau = int(cursor.fetchone()[0])
            cursor.execute("SELECT id_type_voile FROM type_voile")
            id_type = int(cursor.fetchone()[0])
            for i, (logique, titre, notes) in enumerate(FICHES, start=1):
                code = f"N-{logique[:8].upper()}-{i:03d}"
                if logique == "code_cible":
                    code = "0701-GV-001"
                cursor.execute(
                    "INSERT INTO fiche (code, titre, id_type_voile, id_client, id_bateau, "
                    "date_edition, notes, statut) "
                    "VALUES (%s, %s, %s, %s, %s, make_date(2024,6,15), %s, 'valide') RETURNING id_fiche",
                    (code, titre, id_type, id_client, id_bateau, notes),
                )
                id_fiche = int(cursor.fetchone()[0])
                ids[logique] = (id_fiche, code)
                cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (id_fiche,))
    try:
        yield {"index": index, "url": url, "nom": nom, "ids": ids}
    finally:
        index.close()
        admin = psycopg2.connect(DATABASE_URL)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom)))
        admin.close()


def _codes(reponse: dict[str, Any]) -> list[str]:
    return [r["code"] for r in reponse.get("resultats") or []]


def _ids(reponse: dict[str, Any]) -> list[int]:
    return [int(r["id_fiche"]) for r in reponse.get("resultats") or [] if "id_fiche" in r]


VARIANTES = {
    "genois": ["GENOIS", "GÉNOIS", "génois", "Génois", "GeNoIs", unicodedata.normalize("NFD", "Génois")],
    "facade": ["facade", "façade", "FAÇADE", "FACADE", unicodedata.normalize("NFD", "façade")],
    "reparation": ["reparation", "réparation", "RÉPARATION"],
    "ete": ["ete", "été", "Été", "ETE"],
    "noel": ["noel", "Noël", "NOEL"],
    "cable": ["cable", "câble", "CÂBLE"],
    "muller": ["muller", "Müller", "MULLER"],
    "sao": ["sao", "São", "SAO"],
    "coeur": ["coeur", "cœur", "CŒUR"],
}


@pytest.mark.parametrize("famille", list(VARIANTES))
def test_requetes_equivalentes_memes_identifiants_et_ordre(base_norm: dict[str, Any], famille: str) -> None:
    index = base_norm["index"]
    requetes = VARIANTES[famille]
    listes = []
    for q in requetes:
        reponse = rechercher_fiches(index, requete=q, limit=50)
        listes.append(_codes(reponse))
    premiere = listes[0]
    for q, codes in zip(requetes[1:], listes[1:]):
        assert codes == premiere, (
            f"{famille}: {requetes[0]!r} → {premiere} vs {q!r} → {codes} "
            f"(les ensembles/ordres doivent être identiques)"
        )


def test_document_accentue_trouve_sans_accent_et_inverse(base_norm: dict[str, Any]) -> None:
    index = base_norm["index"]
    code_facade = base_norm["ids"]["facade"][1]
    assert code_facade in _codes(rechercher_fiches(index, requete="facade"))
    assert code_facade in _codes(rechercher_fiches(index, requete="façade"))
    code_genois = base_norm["ids"]["genois"][1]
    assert code_genois in _codes(rechercher_fiches(index, requete="GENOIS"))
    assert code_genois in _codes(rechercher_fiches(index, requete="GÉNOIS"))


def test_textes_origine_preserves(base_norm: dict[str, Any]) -> None:
    index = base_norm["index"]
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT titre FROM fiche WHERE titre LIKE %s", ("%Façade%",))
            titre = cursor.fetchone()[0]
    assert "ç" in titre
    assert titre == "Façade atelier nord"


def test_code_et_separateurs_non_regresses(base_norm: dict[str, Any]) -> None:
    index = base_norm["index"]
    reponse = rechercher_fiches(index, requete="0701-GV-001")
    assert _codes(reponse)[0] == "0701-GV-001"


def test_requete_vide_et_espaces(base_norm: dict[str, Any]) -> None:
    index = base_norm["index"]
    vide = rechercher_fiches(index, requete="")
    espaces = rechercher_fiches(index, requete="   ")
    assert vide["sources_actives"] == ["parcours"]
    assert espaces["nb_resultats"] == vide["nb_resultats"]


def test_filtre_type_voile_reste_exact(base_norm: dict[str, Any]) -> None:
    """Le filtre exact n'est PAS élargi par la normalisation des accents."""
    index = base_norm["index"]
    avec = rechercher_fiches(index, requete="", filtres={"type_voile": "Génois"})
    sans = rechercher_fiches(index, requete="", filtres={"type_voile": "Genois"})
    assert avec["nb_resultats"] >= 1
    assert sans["nb_resultats"] == 0


def test_fonction_sql_alignee_sur_python(base_norm: dict[str, Any]) -> None:
    index = base_norm["index"]
    echantillons = ["GÉNOIS", "façade", "cœur", "Müller", "São", "  Été  ", unicodedata.normalize("NFD", "été")]
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT provolatile FROM pg_proc WHERE proname = 'seamtech_texte_recherche'"
            )
            volatile = cursor.fetchone()
            assert volatile is not None and volatile[0] == "i"
            for brut in echantillons:
                cursor.execute("SELECT seamtech_texte_recherche(%s)", (brut,))
                sql_val = cursor.fetchone()[0]
                assert sql_val == normaliser_cle_recherche(brut), f"{brut!r}: SQL={sql_val!r} PY={normaliser_cle_recherche(brut)!r}"


def test_suggestions_prefixe_insensible_accents(base_norm: dict[str, Any]) -> None:
    index = base_norm["index"]
    ge = {s["valeur"] for s in suggerer(index, "GE")["suggestions"]}
    ge_acc = {s["valeur"] for s in suggerer(index, "GÉ")["suggestions"]}
    assert "Génois" in ge
    assert ge == ge_acc


def test_trigrammes_fautes_toujours_actives(base_norm: dict[str, Any]) -> None:
    """La normalisation ne remplace PAS la tolérance aux fautes."""
    index = base_norm["index"]
    # « reparationn » faute de frappe sur Réparation
    reponse = rechercher_fiches(index, requete="reparationn")
    assert base_norm["ids"]["reparation"][1] in _codes(reponse)
    assert "trigrammes" in reponse["sources_actives"]


def test_plan_et_duree_trigrammes_index_expression(base_norm: dict[str, Any]) -> None:
    import inspect

    from seamtech_search.recherche import _source_trigrammes

    source = inspect.getsource(_source_trigrammes)
    assert "coalesce(f.titre" not in source, (
        "coalesce() autour de la colonne casse le matching de l'index "
        "idx_fiche_titre_trgm_norm (expression exacte seamtech_texte_recherche(titre))"
    )
    assert "seamtech_texte_recherche(f.titre)" in source
    assert "WITH q AS" in source

    index = base_norm["index"]
    debut = time.perf_counter()
    reponse = rechercher_fiches(index, requete="GÉNOIS")
    duree_ms = (time.perf_counter() - debut) * 1000.0
    assert reponse["nb_resultats"] >= 1
    # Corpus minuscule : on documente, on ne pose pas de SLA atelier.
    assert duree_ms < 2000.0
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "EXPLAIN SELECT id_fiche FROM fiche "
                "WHERE seamtech_texte_recherche(titre) %% seamtech_texte_recherche(%s)",
                ("GÉNOIS",),
            )
            plan = "\n".join(ligne[0] for ligne in cursor.fetchall())
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute(
                "EXPLAIN SELECT f.id_fiche FROM fiche f "
                "WHERE seamtech_texte_recherche(f.titre) %% seamtech_texte_recherche(%s)",
                ("GÉNOIS",),
            )
            plan_index = "\n".join(ligne[0] for ligne in cursor.fetchall())
    assert "idx_fiche_titre_trgm_norm" in plan_index, (
        "avec enable_seqscan=off, la forme de production doit utiliser "
        f"l'index d'expression 023 ; plan=\n{plan_index}"
    )
    # Sur un corpus de 11 fiches le planificateur peut rester en seq scan ;
    # l'index d'expression doit EXISTER (preuve de migration).
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE indexname = 'idx_fiche_titre_trgm_norm'"
            )
            assert cursor.fetchone() is not None, f"index manquant ; plan=\n{plan}"
            cursor.execute(
                "SELECT pg_get_functiondef('seamtech_texte_recherche(text)'::regprocedure)"
            )
            definition = cursor.fetchone()[0]
            # Preuve : la fonction IMMUTABLE est en place (unaccent ou repli).
            assert "immutable" in definition.lower()
            assert "unaccent" in definition.lower() or "translate" in definition.lower()
