"""Tâche 3 — recherche hybride mesurée sur le FONDS RÉEL (plan v3.0 §10, §17).

Le Lot E a livré l'architecture (pondération A/B/C, fusion RRF k=60, facettes,
trigrammes, texte du PDF) et l'a mesurée sur un corpus SYNTHÉTIQUE de 12 fiches
semées en fixture. Ce fichier fait autre chose : il rejoue le JEU DE REQUÊTES
RÉEL (``docs/verite_terrain/JEU_REQUETES_REELLES.md``) contre un index nourri
par le pipeline complet réglé sur la vraie fiche 7792-SO :

    extraction (gabarit v2 du registre) → écriture (RG3 : a_valider)
    → validation humaine (qui rafraîchit le search_vector, migration 013).

Le fonds réel ne compte qu'UNE fiche à ce jour : chaque requête qui la trouve
la classe au rang 1 par construction. Le critère est donc binaire — présente
ou absente — pour les 13 requêtes du jeu réel, plus les facettes à vide (qui
doivent montrer exactement les valeurs de la fiche) et la régression de la
migration 013 (chantier + année d'édition présents dans le vecteur).

Marque ``-m postgres`` : un seul PostgreSQL sur base jetable, comme le Lot E.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

from tests.conftest import (
    DATABASE_URL,
    _creer_base_jetable,
    _supprimer_base_jetable,
    publier_mesure_perf,
    seuil_perf_p95_ms,
)

RACINE = Path(__file__).resolve().parents[1]
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"

pytestmark = pytest.mark.postgres

# Jeu de requêtes RÉEL — chaque terme provient du document 7792-SO (audit du
# 21/09, vérité terrain §13) ; aucune requête inventée.
JEU_REQUETES_REELLES: tuple[tuple[str, str], ...] = (
    ("7792-SO", "code exact (séparateurs)"),
    ("7792", "code partiel"),
    ("sailonet", "client"),
    ("cruette", "client — raison sociale (migration 013)"),
    ("29er", "bateau"),
    ("spi", "type de voile"),
    ("spi asymétrique", "type complet"),
    ("monofilm", "matière"),
    ("monofilm k903", "matière précise"),
    ("spi sailonet 2026", "combinaison opérateur, année incluse (migration 013)"),
    ("spi 29er", "type + bateau"),
    ("monofime", "FAUTE sur matière (filet trigrammes)"),
    ("voile de portant", "titre"),
)

# Facettes attendues à vide : exactement les valeurs de la fiche 7792-SO.
FACETTES_7792: dict[str, str] = {
    "type_voile": "Spi Asymétrique",
    "client": "Sailonet",
    "bateau": "29er 15'",
    "gamme": "Medium Régate",
    "annee": "2026",
    "matiere": "Monofilm K903",
}


@pytest.fixture(scope="module")
def fonds_reel() -> Iterator[dict[str, Any]]:
    """Base jetable migrée, puis la vraie fiche 7792-SO entrée par le pipeline
    complet réglé : registre → extraction → écriture → validation. Aucun seed
    synthétique : le fonds réel EST la fixture."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    from seamtech_search.fiches.extraction import extraire_fiche
    from seamtech_search.fiches.gabarits import charger_gabarits, initialiser_gabarits
    from seamtech_search.fiches.persistance import ecrire_fiche
    from seamtech_search.fiches.routes import valider_fiche
    from seamtech_search.indexer import SearchIndex

    nom_base, url_base = _creer_base_jetable()
    index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    fiche = extraire_fiche(PDF_7792, gabarits=charger_gabarits(index))
    id_fiche, action = ecrire_fiche(index, fiche)
    assert action == "creee", "le fonds réel ne doit contenir qu'une seule fiche"
    valider_fiche(index, fiche.code, "test-fonds-reel")
    try:
        yield {"index": index, "fiche": fiche, "id_fiche": id_fiche}
    finally:
        index.close()
        _supprimer_base_jetable(nom_base)


def _retrouver(index: Any, requete: str) -> tuple[int | None, list[str]]:
    """Rang (1-indexé) de la fiche réelle dans le top 20, ou None si absente."""
    from seamtech_search.recherche import rechercher_fiches

    reponse = rechercher_fiches(index, requete=requete, limit=20)
    codes = [r["code"] for r in reponse["resultats"]]
    return (codes.index("7792-SO") + 1 if "7792-SO" in codes else None), codes


@pytest.mark.postgres
def test_extraction_reelle_nourrit_l_index(fonds_reel: dict[str, Any]) -> None:
    """Le pipeline réglé produit une fiche valide, cherchable, dont le vecteur
    contient le chantier et l'année d'édition (régression migration 013)."""
    assert fonds_reel["fiche"].code == "7792-SO"
    with fonds_reel["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT statut, champs_texte, search_vector::text FROM fiche WHERE id_fiche = %s",
                (fonds_reel["id_fiche"],),
            )
            statut, champs_texte, vecteur = cursor.fetchone()
    assert statut == "valide", "la validation est ce qui rend la fiche cherchable (RG3)"
    assert champs_texte and "Sailonet" in champs_texte
    # Migration 013 : la raison sociale du client ET l'année sont tokenisées.
    assert "cruette" in champs_texte.lower(), "le chantier du client doit être agrégé"
    assert "2026" in champs_texte, "l'année d'édition doit être tokenisée"
    assert "cruette" in vecteur.lower() and "2026" in vecteur


@pytest.mark.postgres
def test_jeu_requetes_reelles_13_sur_13(fonds_reel: dict[str, Any]) -> None:
    """Rejeu du jeu réel : les 13 requêtes retrouvent la fiche, rang 1.
    Chiffres MESURÉS sur le fonds réel (1 fiche) — publiés dans
    ``docs/verite_terrain/JEU_REQUETES_REELLES.md``. Le critère de latence
    (p95 < 100 ms) est asserté hors instrumentation par
    ``test_perf_p95_fonds_reel`` (marqueur ``perf``, étape CI dédiée sans
    --cov ; audit du 22/09) ; la latence affichée ici reste indicative."""
    index = fonds_reel["index"]
    _retrouver(index, "7792-SO")  # échauffement : plans, caches de connexions
    manquants: list[str] = []
    durees_ms: list[float] = []
    for requete, nature in JEU_REQUETES_REELLES:
        debut = time.perf_counter()
        rang, _codes = _retrouver(index, requete)
        durees_ms.append((time.perf_counter() - debut) * 1000.0)
        if rang is None:
            manquants.append(f"{requete!r} ({nature})")
        else:
            assert rang == 1, f"{requete!r} ({nature}) : la seule fiche du fonds doit être au rang 1, trouvée au rang {rang}"
    p50 = statistics.median(durees_ms)
    p95 = sorted(durees_ms)[int(len(durees_ms) * 0.95) - 1]
    print(
        f"\n[mesure Tâche 3 — fonds réel] {len(JEU_REQUETES_REELLES) - len(manquants)}/13 "
        f"requêtes retrouvent 7792-SO au rang 1 ; latence indicative sous ce contexte : "
        f"p50 = {p50:.1f} ms, p95 = {p95:.1f} ms"
    )
    assert not manquants, "requêtes réelles sans la fiche : " + " ; ".join(manquants)


@pytest.mark.perf
@pytest.mark.postgres
def test_perf_p95_fonds_reel(fonds_reel: dict[str, Any]) -> None:
    """CRITÈRE DE SORTIE LOT E (p95 < 100 ms) sur le fonds réel (vraie fiche
    7792-SO, 13 requêtes réelles), mesuré HORS INSTRUMENTATION — même
    justification que ``test_perf_p95_jeu_50_reference`` (audit du 22/09 : le
    critère sous --cov a flaké à 108,8 ms). Chauffe complète du jeu avant le
    passage mesuré ; publication p50/p95/max via SEAMTECH_PERF_JSON."""
    index = fonds_reel["index"]
    for requete, _nature in JEU_REQUETES_REELLES:  # chauffe : premier passage jeté
        _retrouver(index, requete)
    durees_ms: list[float] = []
    for requete, nature in JEU_REQUETES_REELLES:
        debut = time.perf_counter()
        rang, _codes = _retrouver(index, requete)
        durees_ms.append((time.perf_counter() - debut) * 1000.0)
        assert rang == 1, f"{requete!r} ({nature}) : la seule fiche du fonds doit être au rang 1, trouvée au rang {rang}"
    p50 = statistics.median(durees_ms)
    p95 = sorted(durees_ms)[int(len(durees_ms) * 0.95) - 1]
    seuil = seuil_perf_p95_ms()
    print(
        f"\n[perf Tâche 3 — fonds réel] p50 = {p50:.1f} ms, p95 = {p95:.1f} ms, "
        f"max = {max(durees_ms):.1f} ms (seuil p95 applicable : {seuil:.0f} ms)"
    )
    publier_mesure_perf("fonds réel 7792-SO (13 requêtes)", p50, p95, max(durees_ms), len(durees_ms))
    assert p50 < 100.0, f"critère d'architecture violé : p50 = {p50:.1f} ms ≥ 100 ms"
    assert p95 < seuil, (
        f"le critère de sortie du Lot E doit tenir sur le fonds réel : p95 = {p95:.1f} ms ≥ {seuil:.0f} ms"
        + ("" if seuil == 100.0 else " [seuil d'environnement CI étiqueté, critère produit 100 ms]")
    )


@pytest.mark.postgres
def test_facettes_a_vide_valeurs_de_la_fiche(fonds_reel: dict[str, Any]) -> None:
    """Requête vide sur le fonds réel : chaque axe de facette montre exactement
    les valeurs de la fiche 7792-SO (aucune valeur inventée par le seed)."""
    from seamtech_search.recherche import rechercher_fiches

    reponse = rechercher_fiches(fonds_reel["index"], requete="")
    facettes = reponse["facettes"]
    for axe, valeur_attendue in FACETTES_7792.items():
        assert axe in facettes, f"l'axe de facette {axe!r} manque"
        valeurs = [(v["valeur"], v["effectif"]) for v in facettes[axe]]
        assert valeurs == [(valeur_attendue, 1)], (
            f"facette {axe!r} : attendu exactement [{(valeur_attendue, 1)}], mesuré {valeurs}"
        )
