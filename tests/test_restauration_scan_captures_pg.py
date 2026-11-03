"""Retour manuel d'un instantané : ne pas écraser les captures contemporaines.

La voie de scan normale utilise un rollback transactionnel, pas cette fonction
historique. Ces tests utilisent une base PostgreSQL jetable et des octets
synthétiques : ils ne prouvent pas une restauration globale coordonnée.
"""

# ruff: noqa: F811
from __future__ import annotations

from typing import Any

import pytest

from tests.test_rebuild_index_postgres import base_rebuild  # noqa: F401

pytestmark = pytest.mark.postgres
INSTANTANE = "scan_backup_captures_test"


def _document(curseur: Any, nom: str, empreinte: str | None = None) -> int:
    curseur.execute(
        "INSERT INTO documents "
        "(path_key,path,name,parent_path,extension,size,modified_at,is_dir,content,source_sha256) "
        "VALUES (%s,%s,%s,'/','.txt',1,1,false,%s,%s) RETURNING id",
        (nom, "/" + nom, nom, nom, empreinte),
    )
    return int(curseur.fetchone()[0])


def _instantane(curseur: Any) -> None:
    curseur.execute(f'CREATE TABLE "{INSTANTANE}" AS TABLE documents')


def test_restauration_conserve_captures_ajoutees_et_modifiees_apres_instantane(
    base_rebuild: dict[str, Any],
) -> None:
    index = base_rebuild["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        existant = _document(curseur, "existant.txt")
        _instantane(curseur)
        ajoute = _document(curseur, "ajoute.txt", "a" * 64)
        curseur.execute("UPDATE documents SET content='capture-contemporaine' WHERE id=%s", (existant,))
        curseur.execute("UPDATE documents SET source_sha256=%s WHERE id=%s", ("b" * 64, existant))
        curseur.execute("SELECT id, content, source_sha256 FROM documents ORDER BY id")
        avant = curseur.fetchall()

    with pytest.raises(RuntimeError, match="instantané.*ambigu"):
        with index.connect() as connexion, connexion.cursor() as curseur:
            index._restaurer_documents(curseur, INSTANTANE)
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT id, content, source_sha256 FROM documents ORDER BY id")
        assert curseur.fetchall() == avant
        assert {ligne[0] for ligne in avant} == {existant, ajoute}


@pytest.mark.parametrize("disparu", [False, True], ids=["identite-effacee", "ligne-disparue"])
def test_restauration_refuse_capture_anterieure_devenue_ambigue_sans_ecriture(
    base_rebuild: dict[str, Any], disparu: bool,
) -> None:
    index = base_rebuild["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        identifiant = _document(curseur, "capture.txt", "c" * 64)
        _instantane(curseur)
        if disparu:
            curseur.execute("DELETE FROM documents WHERE id=%s", (identifiant,))
        else:
            # La migration 032 invalide automatiquement l'empreinte précédente.
            curseur.execute("UPDATE documents SET content='autre-version' WHERE id=%s", (identifiant,))
        _document(curseur, "nouveau.txt")
        curseur.execute("SELECT id, content, source_sha256 FROM documents ORDER BY id")
        avant = curseur.fetchall()

    with pytest.raises(RuntimeError, match="instantané.*ambigu"):
        with index.connect() as connexion, connexion.cursor() as curseur:
            index._restaurer_documents(curseur, INSTANTANE)

    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT id, content, source_sha256 FROM documents ORDER BY id")
        assert curseur.fetchall() == avant
        curseur.execute("SELECT to_regclass(%s)", (INSTANTANE,))
        assert curseur.fetchone()[0] == INSTANTANE


def test_restauration_refuse_instantane_sans_identite_physique(base_rebuild: dict[str, Any]) -> None:
    index = base_rebuild["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        _document(curseur, "capture.txt", "d" * 64)
        _instantane(curseur)
        curseur.execute(f'ALTER TABLE "{INSTANTANE}" DROP COLUMN source_sha256')
        curseur.execute("SELECT id, content, source_sha256 FROM documents")
        avant = curseur.fetchall()

    with pytest.raises(RuntimeError, match="instantané.*identité"):
        with index.connect() as connexion, connexion.cursor() as curseur:
            index._restaurer_documents(curseur, INSTANTANE)

    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT id, content, source_sha256 FROM documents")
        assert curseur.fetchall() == avant


def test_restauration_preserve_reference_creee_apres_instantane_meme_en_cascade(
    base_rebuild: dict[str, Any],
) -> None:
    index = base_rebuild["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        _instantane(curseur)
        identifiant = _document(curseur, "reference.txt")
        curseur.execute("CREATE TABLE reference_test (document_id bigint REFERENCES documents(id) ON DELETE CASCADE)")
        curseur.execute("INSERT INTO reference_test VALUES (%s)", (identifiant,))

    with pytest.raises(RuntimeError, match="instantané.*ambigu"):
        with index.connect() as connexion, connexion.cursor() as curseur:
            index._restaurer_documents(curseur, INSTANTANE)
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT id FROM documents WHERE id=%s", (identifiant,))
        assert curseur.fetchone() == (identifiant,)
        curseur.execute("SELECT document_id FROM reference_test")
        assert curseur.fetchall() == [(identifiant,)]


def test_restauration_refuse_ecrivain_concurrent_sans_attendre(base_rebuild: dict[str, Any]) -> None:
    import psycopg2
    from psycopg2.errors import LockNotAvailable

    index = base_rebuild["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        _document(curseur, "concurrent.txt")
        _instantane(curseur)

    with psycopg2.connect(base_rebuild["url"]) as ecrivain:
        with ecrivain.cursor() as curseur:
            curseur.execute("UPDATE documents SET content='ecriture-concurrente' WHERE path_key='concurrent.txt'")
        with pytest.raises(LockNotAvailable):
            with psycopg2.connect(base_rebuild["url"]) as autre, autre.cursor() as curseur:
                index._restaurer_documents(curseur, INSTANTANE)

    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT content FROM documents WHERE path_key='concurrent.txt'")
        assert curseur.fetchone() == ("ecriture-concurrente",)


def test_restauration_refuse_aussi_modification_sans_empreinte(base_rebuild: dict[str, Any]) -> None:
    """Sans journal d'origine on ne distingue pas scan et écriture concurrente."""
    index = base_rebuild["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        identifiant = _document(curseur, "sans-empreinte.txt")
        _instantane(curseur)
        curseur.execute("UPDATE documents SET content='changement-legitime' WHERE id=%s", (identifiant,))

    with pytest.raises(RuntimeError, match="instantané.*ambigu"):
        with index.connect() as connexion, connexion.cursor() as curseur:
            index._restaurer_documents(curseur, INSTANTANE)
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT content FROM documents WHERE id=%s", (identifiant,))
        assert curseur.fetchone() == ("changement-legitime",)


def test_instantane_identique_ne_reecrit_pas_et_n_invalide_pas_l_identite(base_rebuild: dict[str, Any]) -> None:
    index = base_rebuild["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        identifiant = _document(curseur, "identique.txt", "e" * 64)
        _instantane(curseur)

    with index.connect() as connexion, connexion.cursor() as curseur:
        resultat = index._restaurer_documents(curseur, INSTANTANE)
        assert resultat["supprimees"] == resultat["reinserees"] == 0
        assert resultat["colonnes"] > 0
        curseur.execute("SELECT content, source_sha256 FROM documents WHERE id=%s", (identifiant,))
        assert curseur.fetchone() == ("identique.txt", "e" * 64)


def test_restauration_refuse_identifiants_dupliques_dans_instantane(base_rebuild: dict[str, Any]) -> None:
    index = base_rebuild["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        _document(curseur, "duplique.txt")
        _instantane(curseur)
        curseur.execute(f'INSERT INTO "{INSTANTANE}" SELECT * FROM "{INSTANTANE}"')

    with pytest.raises(RuntimeError, match="instantané.*ambigu"):
        with index.connect() as connexion, connexion.cursor() as curseur:
            index._restaurer_documents(curseur, INSTANTANE)
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT count(*) FROM documents")
        assert curseur.fetchone() == (1,)
