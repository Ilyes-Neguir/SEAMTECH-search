"""Persistance PostgreSQL des traces produites à partir d'un PDF pivoté."""
from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.fiches.extraction import extraire_fiche
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES
from seamtech_search.fiches.persistance import ecrire_fiche
from tests.test_fiches_persistance import PDF_7792, base_fiches  # noqa: F401
from tests.test_pdf_variantes import _transformer

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_traces_rotations_persistantes_identiques_aux_zones_calculees(base_fiches, tmp_path: Path, angle: int):  # noqa: F811
    pivot = _transformer(PDF_7792, tmp_path / "pivot.pdf", lambda w: [p.rotate(angle) for p in w.pages])
    fiche = extraire_fiche(pivot, gabarits=list(GABARITS_EMBARQUES))
    fiche.code = f"SYNTH-ROT-{angle}"
    identifiant, action = ecrire_fiche(base_fiches["index"], fiche)
    assert action == "creee"
    attendus = {(c.champ, c.rang): c for c in fiche.tous_les_champs()}
    assert len(attendus) == 48
    with base_fiches["index"].connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT champ, rang, zone, page, valeur_brute, valeur_normalisee "
                            "FROM fiche_champ_extrait WHERE id_fiche = %s", (identifiant,))
            lignes = curseur.fetchall()
    assert len(lignes) == len(attendus)
    for nom, rang, zone, page, brute, normalisee in lignes:
        champ = attendus[nom, rang]
        assert (page, brute, normalisee) == (champ.page, champ.valeur_brute, champ.valeur_normalisee)
        assert zone == (champ.zone.en_dict() if champ.zone is not None else None)


def test_orientation_mixte_persistante_et_file_explicite(base_fiches, tmp_path: Path):  # noqa: F811
    from reportlab.pdfgen import canvas

    from seamtech_search.fiches.gabarits import GabaritDef, RegleChamp
    from seamtech_search.fiches.routes import fichier_validation

    chemin = tmp_path / "fiche.pdf"
    pdf = canvas.Canvas(str(chemin))
    pdf.drawString(40, 700, "FICHE CODE : SYNTH-MIXTE")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.rotate(90)
    pdf.drawString(0, 0, "LEGENDE")
    pdf.restoreState()
    pdf.save()
    gabarit = GabaritDef(code="TEST", ancres_detection=["fiche"], champs=[RegleChamp(cible="fiche.code", ancres=["code"])])
    fiche = extraire_fiche(chemin, [gabarit], gabarit_code="TEST")
    fiche.code = "SYNTH-MIXTE"
    id_fiche, action = ecrire_fiche(base_fiches["index"], fiche)
    assert action == "creee"
    with base_fiches["index"].connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT code, gravite, statut FROM fiche_anomalie WHERE id_fiche = %s", (id_fiche,))
        anomalies = curseur.fetchall()
    assert ("orientations_pdf_mixtes", "forte", "a_traiter") in anomalies
    entree = next(row for row in fichier_validation(base_fiches["index"]) if row["code"] == fiche.code)
    assert entree["a_anomalies"] and entree["orientations_pdf_mixtes"] is True
    from seamtech_search.fiches.routes import valider_lot
    decision = valider_lot(base_fiches["index"], [fiche.code], None, acquittement_humain=True,
                           revisions={fiche.code: entree["revision"]})
    assert decision["nb_validees"] == 0 and decision["nb_ignorees"] == 1
    assert "orientations" in decision["ignorees"][0]["raison"].lower()
    with base_fiches["index"].connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT statut FROM fiche WHERE id_fiche = %s", (id_fiche,))
        assert curseur.fetchone()[0] == "a_valider"


def test_orientation_non_verifiee_persistante_refuse_validation_lot(base_fiches, tmp_path: Path):  # noqa: F811
    from reportlab.pdfgen import canvas

    from seamtech_search.fiches.gabarits import GabaritDef, RegleChamp
    from seamtech_search.fiches.persistance import routage
    from seamtech_search.fiches.routes import fichier_validation, valider_lot

    chemin = tmp_path / "fiche.pdf"
    pdf = canvas.Canvas(str(chemin))
    pdf.drawString(40, 700, "FICHE CODE : SYNTH-INCONNU")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.transform(1, 0, .4, 1, 0, 0)
    pdf.drawString(0, 0, "LEGENDE")
    pdf.restoreState()
    pdf.save()
    avant = chemin.read_bytes()
    gabarit = GabaritDef(code="TEST", ancres_detection=["fiche"], champs=[RegleChamp(cible="fiche.code", ancres=["code"])])
    fiche = extraire_fiche(chemin, [gabarit], gabarit_code="TEST")
    fiche.code = "SYNTH-INCONNU"
    assert routage(fiche)["voie"] == "reprise_complete"
    id_fiche, action = ecrire_fiche(base_fiches["index"], fiche)
    assert action == "creee"
    with base_fiches["index"].connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT code, gravite, statut FROM fiche_anomalie WHERE id_fiche = %s", (id_fiche,))
        anomalies = curseur.fetchall()
    assert ("orientation_pdf_non_verifiee", "forte", "a_traiter") in anomalies
    entree = next(row for row in fichier_validation(base_fiches["index"]) if row["code"] == fiche.code)
    assert entree["a_anomalies"] and entree["orientations_pdf_non_verifiees"] is True
    assert entree["orientations_pdf_mixtes"] is False
    decision = valider_lot(base_fiches["index"], [fiche.code], None, acquittement_humain=True,
                           revisions={fiche.code: entree["revision"]})
    assert decision["nb_validees"] == 0 and decision["nb_ignorees"] == 1
    assert "orientation" in decision["ignorees"][0]["raison"].lower()
    with base_fiches["index"].connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT statut FROM fiche WHERE id_fiche = %s", (id_fiche,))
        assert curseur.fetchone()[0] == "a_valider"
    assert chemin.read_bytes() == avant


def test_depot_sans_gabarit_mixte_preserve_raison_et_original(base_fiches, tmp_path: Path):  # noqa: F811
    from reportlab.pdfgen import canvas

    from seamtech_search.fiches.depot import deposer_dossier
    dossier = tmp_path / "synthese"
    dossier.mkdir()
    chemin = dossier / "original.pdf"
    pdf = canvas.Canvas(str(chemin))
    pdf.drawString(40, 700, "DESSIN SYNTHETIQUE SANS GABARIT")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.rotate(90)
    pdf.drawString(0, 0, "LEGENDE")
    pdf.restoreState()
    pdf.save()
    octets_avant = chemin.read_bytes()
    resultat = deposer_dossier(base_fiches["index"], dossier)
    assert resultat["statut"] == "echec"
    assert "orientations mixtes" in resultat["raison"].lower()
    assert chemin.read_bytes() == octets_avant
    with base_fiches["index"].connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT raison FROM lot_dossier WHERE chemin_dossier = %s ORDER BY id_lot_dossier DESC LIMIT 1", (str(dossier),))
        assert curseur.fetchone()[0] == resultat["raison"]
