"""Crée des PDF synthétiques pivotés et calcule leurs zones par le backend."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from seamtech_search.fiches.extraction import _zone_des_mots, analyser_pdf  # noqa: E402


def main() -> None:
    dossier = Path(sys.argv[1])
    dossier.mkdir(parents=True, exist_ok=True)
    source = dossier / "source.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(595, 842))
    pdf.setFont("Helvetica", 18)
    pdf.drawString(65, 655, "MESURE")
    pdf.drawString(65, 600, "HORS SUJET")
    pdf.save()
    resultats = []
    for angle in (0, 90, 180, 270):
        page = PdfReader(source).pages[0]
        page.mediabox.lower_left = (10, 20)
        page.mediabox.upper_right = (605, 862)
        page.cropbox.lower_left = (30, 100)
        page.cropbox.upper_right = (510, 800)
        page[NameObject("/UserUnit")] = NumberObject(2)
        page.rotate(angle)
        chemin = dossier / f"rotation-{angle}.pdf"
        red = PdfWriter()
        red.add_page(page)
        red.write(chemin)
        analyse = analyser_pdf(chemin)[0]
        mot = next(mot for ligne in analyse.lignes for mot in ligne.mots if mot.texte == "MESURE")
        zone = _zone_des_mots([mot])
        if zone is None:
            raise RuntimeError("zone synthétique absente")
        resultats.append({"angle": angle, "fichier": str(chemin), "zone": zone.en_dict()})
    print(json.dumps(resultats))


if __name__ == "__main__":
    main()
