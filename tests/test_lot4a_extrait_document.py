"""Lot 4a — aperçu « Extrait du document » dans les résultats de recherche.

Le contrat prouvé ici est le parcours produit complet : la recherche rend,
pour chaque fiche de la page, un extrait borné issu en priorité du texte réel
d'un document rattaché, puis d'un chunk documentaire, puis seulement des
champs de la fiche. Les correspondances sont des segments de texte structurés
— jamais du HTML — afin que le navigateur les affiche sans interpréter le
contenu du document.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from seamtech_search.recherche import (
    _MARQUEUR_EXTRAIT_DEBUT,
    _MARQUEUR_EXTRAIT_FIN,
    EXTRAIT_DOCUMENT_MAX_CARACTERES,
    _construire_extrait_document,
    _extraits_documents,
    _segments_extrait_document,
    rechercher_fiches,
)


def _texte_segments(extrait: dict[str, Any]) -> str:
    return "".join(str(segment["texte"]) for segment in extrait["segments"])


def _segments_correspondants(extrait: dict[str, Any]) -> list[str]:
    return [
        str(segment["texte"])
        for segment in extrait["segments"]
        if segment["correspondance"] is True
    ]


def _resultat(reponse: dict[str, Any], code: str) -> dict[str, Any]:
    return next(resultat for resultat in reponse["resultats"] if resultat["code"] == code)


def test_extrait_document_segments_structures_et_texte_brut_sans_html() -> None:
    brut = (
        "Renfort\tprincipal "
        f"{_MARQUEUR_EXTRAIT_DEBUT}lattée{_MARQUEUR_EXTRAIT_FIN} "
        "<script>alert('x')</script> sur la chute"
    )

    extrait = _construire_extrait_document(brut, source="document", libelle_source="fiche.pdf")

    assert extrait is not None
    assert extrait["source"] == "document"
    assert extrait["libelle_source"] == "fiche.pdf"
    assert extrait["correspondance_requete"] is True
    assert extrait["texte"] == _texte_segments(extrait)
    assert _segments_correspondants(extrait) == ["lattée"]
    # Le texte hostile reste une donnée textuelle : aucun marqueur interne ni
    # balise de surlignage n'est injecté dans le contrat JSON.
    assert "<script>alert('x')</script>" in extrait["texte"]
    assert "<mark>" not in extrait["texte"]
    assert _MARQUEUR_EXTRAIT_DEBUT not in extrait["texte"]
    assert _MARQUEUR_EXTRAIT_FIN not in extrait["texte"]


def test_extrait_document_borne_autour_de_la_premiere_correspondance() -> None:
    avant = " ".join(f"avant{i}" for i in range(90))
    apres = " ".join(f"apres{i}" for i in range(90))
    brut = f"{avant} {_MARQUEUR_EXTRAIT_DEBUT}terme-cible{_MARQUEUR_EXTRAIT_FIN} {apres}"

    segments = _segments_extrait_document(brut)
    texte = "".join(str(segment["texte"]) for segment in segments)

    assert len(texte) <= EXTRAIT_DOCUMENT_MAX_CARACTERES
    assert "terme-cible" in texte
    assert [segment["texte"] for segment in segments if segment["correspondance"]] == ["terme-cible"]
    assert all(str(segment["texte"]) for segment in segments)
    # La fenêtre est centrée sur la correspondance au lieu de reproduire le
    # simple début du document.
    assert not texte.startswith("avant0")
    assert not texte.endswith("apres89")


def test_extrait_document_marqueurs_imbriques_ou_non_fermes_sans_crash() -> None:
    brut = (
        f"début {_MARQUEUR_EXTRAIT_DEBUT}un "
        f"{_MARQUEUR_EXTRAIT_DEBUT}deux{_MARQUEUR_EXTRAIT_FIN} fin"
    )

    segments = _segments_extrait_document(brut)

    assert "".join(str(segment["texte"]) for segment in segments) == "début un deux fin"
    assert [segment["texte"] for segment in segments if segment["correspondance"]] == ["un deux"]


@pytest.mark.postgres
def test_recherche_extrait_chunk_avec_accents_preserves(base_recherche: dict[str, Any]) -> None:
    reponse = rechercher_fiches(base_recherche["index"], requete="forcees", limit=20)
    resultat = _resultat(reponse, "0812-SPI-002")
    extrait = resultat["extrait_document"]

    assert "texte_pdf" in resultat["sources"]
    assert extrait["source"] == "chunk"
    assert extrait["correspondance_requete"] is True
    assert _segments_correspondants(extrait) == ["forcées"]
    assert "renforts de guindant en kevlar" in extrait["texte"]
    assert len(extrait["texte"]) <= EXTRAIT_DOCUMENT_MAX_CARACTERES
    # Compatibilité : l'ancien champ reste l'aperçu des champs de la fiche.
    assert "0812-SPI-002" in resultat["extrait"]
    assert "renforts de guindant" not in resultat["extrait"]


def _inserer_document(
    index: Any,  # noqa: ANN401
    *,
    id_fiche: int | None,
    chemin: str,
    nom: str,
    contenu: str,
    statut_extraction: str = "extracted",
) -> int:
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, "
                "modified_at, is_dir, content, search_vector, extraction_status, id_fiche, role) "
                "VALUES (%s, %s, %s, %s, '.pdf', %s, %s, FALSE, %s, "
                "to_tsvector('simple', %s), %s, %s, 'fiche_pdf') RETURNING id",
                (
                    chemin.lower(),
                    chemin,
                    nom,
                    str(Path(chemin).parent),
                    len(contenu.encode("utf-8")),
                    time.time(),
                    contenu,
                    contenu,
                    statut_extraction,
                    id_fiche,
                ),
            )
            return int(cursor.fetchone()[0])


@pytest.mark.postgres
def test_recherche_extrait_document_reel_prioritaire_sur_resume_genere(
    base_recherche: dict[str, Any],
) -> None:
    index = base_recherche["index"]
    id_fiche = int(base_recherche["fiches"]["0901-TRM-001"])
    _inserer_document(
        index,
        id_fiche=id_fiche,
        chemin="/archive/lot4a/tourmentin-source.pdf",
        nom="tourmentin-source.pdf",
        contenu=(
            "Document source contrôle atelier : renfort carbone tisse serge "
            "sur la bordure, contrôle final avant livraison."
        ),
    )
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            # Ce chunk est un résumé de champs généré pour les vecteurs : il
            # ne doit jamais être présenté comme le texte du document.
            cursor.execute(
                "INSERT INTO chunk (id_fiche, nature, contenu) VALUES (%s, 'resume_champs', %s)",
                (id_fiche, "Résumé généré carbone tisse serge à ne jamais afficher."),
            )

    reponse = rechercher_fiches(index, requete="carbone tisse serge", limit=20)
    extrait = _resultat(reponse, "0901-TRM-001")["extrait_document"]

    assert extrait["source"] == "document"
    assert extrait["libelle_source"] == "tourmentin-source.pdf"
    assert extrait["correspondance_requete"] is True
    assert "renfort carbone tisse serge" in extrait["texte"]
    assert "Résumé généré" not in extrait["texte"]
    assert set(_segments_correspondants(extrait)) == {"carbone", "tisse", "serge"}


@pytest.mark.postgres
def test_extrait_document_via_piece_jointe_et_chemin_source(base_recherche: dict[str, Any]) -> None:
    """Les liens réels de production ne passent pas toujours par documents.id_fiche."""
    index = base_recherche["index"]
    id_fiche_piece = int(base_recherche["fiches"]["0902-GV-004"])
    id_fiche_source = int(base_recherche["fiches"]["0903-GEN-004"])

    chemin_piece = "/archive/lot4a/piece-production.pdf"
    id_document_piece = _inserer_document(
        index,
        id_fiche=None,
        chemin=chemin_piece,
        nom="piece-production.pdf",
        contenu="Plan de production : galon renforce en spectra sur toute la chute.",
    )
    chemin_source = "/archive/lot4a/fiche-source.pdf"
    _inserer_document(
        index,
        id_fiche=None,
        chemin=chemin_source,
        nom="fiche-source.pdf",
        contenu="Fiche source : nerf de chute en dyneema et œillets inox.",
    )

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO fiche_piece_jointe "
                "(id_fiche, id_document, chemin, role, empreinte_sha256, taille_octets) "
                "VALUES (%s, %s, %s, 'plan', %s, 42)",
                (id_fiche_piece, id_document_piece, chemin_piece, "a" * 64),
            )
            cursor.execute(
                "UPDATE fiche SET fichier_source = %s WHERE id_fiche = %s",
                (chemin_source, id_fiche_source),
            )

            ts_config = index._postgres_ts_config(connexion)
            extraits = _extraits_documents(
                cursor,
                [id_fiche_piece, id_fiche_source],
                ts_config,
                ["spectra", "dyneema"],
            )

    assert extraits[id_fiche_piece]["source"] == "document"
    assert extraits[id_fiche_piece]["libelle_source"] == "piece-production.pdf"
    assert _segments_correspondants(extraits[id_fiche_piece]) == ["spectra"]
    assert extraits[id_fiche_source]["source"] == "document"
    assert extraits[id_fiche_source]["libelle_source"] == "fiche-source.pdf"
    assert _segments_correspondants(extraits[id_fiche_source]) == ["dyneema"]


@pytest.mark.postgres
def test_recherche_sans_document_repliee_sur_champs_fiche(base_recherche: dict[str, Any]) -> None:
    reponse = rechercher_fiches(
        base_recherche["index"],
        requete="",
        filtres={"type_voile": "Tourmentin"},
        limit=20,
    )
    extrait = _resultat(reponse, "0901-TRM-001")["extrait_document"]

    assert extrait["source"] == "fiche"
    assert extrait["libelle_source"] == "Champs de la fiche"
    assert extrait["correspondance_requete"] is False
    assert "0901-TRM-001" in extrait["texte"]
    assert "Tourmentin" in extrait["texte"]


@pytest.mark.postgres
def test_route_recherche_expose_extrait_document(base_recherche: dict[str, Any]) -> None:
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[Path("/tmp")],
        database_url=base_recherche["url"],
        auth_token="jeton-service-lot4a",
        min_free_bytes=0,
    )
    with TestClient(create_app(config, base_recherche["index"])) as client:
        reponse = client.get(
            "/recherche",
            params={"q": "kevlar"},
            headers={"X-SEAMTECH-TOKEN": "jeton-service-lot4a"},
        )

    assert reponse.status_code == 200
    resultat = _resultat(reponse.json(), "0812-SPI-002")
    assert resultat["extrait_document"]["source"] == "chunk"
    assert _segments_correspondants(resultat["extrait_document"]) == ["kevlar"]
