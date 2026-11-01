"""Dépôt d'une ARBORESCENCE entière (tout un Dropbox) — mesures sur disque réel.

Ces tests n'utilisent aucun faux serveur : ils vérifient la découverte des
dossiers sur une arborescence construite, puis le rapport produit par un
déposant bouché (le réseau n'est jamais touché dans la suite de tests).
"""

from __future__ import annotations

import csv
from pathlib import Path

from seamtech_search.fiches import arborescence as module
from seamtech_search.fiches.arborescence import (
    deposer_arborescence,
    ecrire_rapport_csv,
    iterer_dossiers_sans_pdf,
    lister_dossiers,
    resume,
)


def _ecrire(chemin: Path, contenu: bytes = b"%PDF-1.4\n") -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(contenu)


def test_lister_dossiers_ne_perd_aucun_niveau(tmp_path: Path) -> None:
    """Un dossier parent AVEC pdf ET un sous-dossier avec pdf : les deux sortent."""
    _ecrire(tmp_path / "VOILES 2026" / "BAVARIA 38 ST735" / "ST 735_ffab.pdf")
    _ecrire(tmp_path / "VOILES 2026" / "BAVARIA 38 ST735" / "TISSU" / "plan.pdf")
    _ecrire(tmp_path / "VOILES 2026" / "590 ST653" / "ST 653_ffab.pdf")
    trouves = lister_dossiers(tmp_path / "VOILES 2026")
    noms = sorted(d.chemin.name for d in trouves)
    assert noms == ["590 ST653", "BAVARIA 38 ST735", "TISSU"]


def test_un_dossier_sans_pdf_n_est_pas_deposable_mais_reste_visible(tmp_path: Path) -> None:
    _ecrire(tmp_path / "1.5M" / "1.dxf")
    _ecrire(tmp_path / "3M" / "2.dxf")
    _ecrire(tmp_path / "bon" / "fiche.pdf")
    assert [d.chemin.name for d in lister_dossiers(tmp_path)] == ["bon"]
    assert sorted(p.name for p in iterer_dossiers_sans_pdf(tmp_path)) == ["1.5M", "3M"]


def test_racine_a_plat_est_examinee(tmp_path: Path) -> None:
    _ecrire(tmp_path / "ST 508_ffab.pdf")
    assert [d.chemin for d in lister_dossiers(tmp_path)] == [tmp_path]


def test_depot_rapporte_traite_echec_et_deja_traite(tmp_path: Path, monkeypatch) -> None:
    for nom in ("A", "B", "C"):
        _ecrire(tmp_path / nom / f"{nom}.pdf")
    reponses = {
        "A": ("traite", "ST 001", 2, ""),
        "B": ("echec", None, None, "aucune fiche de fabrication dans ce dossier"),
        "C": ("deja_traite", None, 1, "deja_traite (lot #7)"),
    }
    vus: list[str] = []

    def faux_depot(url, jeton, dossier, delai):  # noqa: ANN001 - signature miroir
        vus.append(dossier.name)
        return reponses[dossier.name]

    monkeypatch.setattr(module, "_deposer_un", faux_depot)
    resultats = deposer_arborescence(tmp_path, "http://x", "jeton")

    assert vus == ["A", "B", "C"], "chaque dossier est déposé une fois, aucun n'est sauté"
    assert resume(resultats) == {"traite": 1, "echec": 1, "deja_traite": 1}
    assert resultats[1].raison == "aucune fiche de fabrication dans ce dossier"


def test_rapport_csv_est_lisible_sous_excel(tmp_path: Path, monkeypatch) -> None:
    _ecrire(tmp_path / "A" / "fiche.pdf")
    monkeypatch.setattr(module, "_deposer_un", lambda *a, **k: ("traite", "ST 001", 1, ""))
    rapport = tmp_path / "rapport.csv"
    resultats = deposer_arborescence(tmp_path, "http://x", "jeton", rapport_csv=rapport)
    ecrire_rapport_csv(resultats, rapport)
    contenu = rapport.read_text(encoding="utf-8-sig").splitlines()
    assert contenu[0] == "dossier;nb_fichiers;statut;fiche;pieces;raison;fichiers"
    with rapport.open(encoding="utf-8-sig", newline="") as flux:
        lignes = list(csv.reader(flux, delimiter=";"))
    assert lignes[1][2] == "traite" and lignes[1][3] == "ST 001"
