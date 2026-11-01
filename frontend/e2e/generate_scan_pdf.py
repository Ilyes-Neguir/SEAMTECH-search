"""Genere un PDF image (sans couche texte) pour le spec e2e scan-browser-ocr.

Usage :
    python3 generate_scan_pdf.py <tmp_dir> <variant> [terme] [--prefix PREFIX] [--json]

``variant`` : fr | en | multi | degrade.

Sans ``--json``, le script imprime le chemin du PDF sur la derniere ligne
(compatibilite avec l'appel historique).

Avec ``--json``, il imprime ``{"pdf": ..., "terme": ...}``. Le terme unique est
alors tire par :func:`tests.helpers_ocr_scan.terme_unique_ocr_sur`, donc
**sans caractere ambigu pour l'OCR** (pas de 0/O, 1/I, 5/S, 8/B, 2/Z). C'est la
cause de l'echec CI du 28/10 : un terme hexadecimal contenant « 0 » etait
océrisé « O », et l'assertion stricte du spec le refusait — a juste titre.
Le terme est desormais choisi cote Python, la ou il est verifiable par les
tests unitaires, au lieu d'etre tire dans le spec TypeScript.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is in path for tests.helpers_ocr_scan
repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo_root))

from tests.helpers_ocr_scan import (  # noqa: E402
    gen_pdf_custom,
    gen_pdf_degrade,
    gen_pdf_en,
    gen_pdf_fr,
    gen_pdf_multi,
    terme_unique_ocr_sur,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF image pour le spec scan OCR")
    parser.add_argument("tmp_dir")
    parser.add_argument("variant")
    parser.add_argument("terme", nargs="?", default=None)
    parser.add_argument("--prefix", default=None, help="prefixe lisible du terme unique")
    parser.add_argument("--json", action="store_true", help="sortie JSON {pdf, terme}")
    args = parser.parse_args()

    tmp_dir = Path(args.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    variant = args.variant
    terme = args.terme
    if args.prefix:
        terme = terme_unique_ocr_sur(args.prefix)

    if terme:
        lang = "fr" if variant.startswith("fr") else "en"
        # Le nom du fichier ne contient pas le terme : sa presence dans
        # l'index ne peut venir que du contenu océrisé.
        pdf = gen_pdf_custom(
            tmp_dir, terme, lang=lang, filename=f"scan-{variant.split(':')[0]}.pdf"
        )
    elif variant == "fr":
        pdf = gen_pdf_fr(tmp_dir)
    elif variant == "en":
        pdf = gen_pdf_en(tmp_dir)
    elif variant == "multi":
        pdf = gen_pdf_multi(tmp_dir)
    elif variant == "degrade":
        pdf = gen_pdf_degrade(tmp_dir)
    else:
        print(f"variant inconnu : {variant}", file=sys.stderr)
        raise SystemExit(2)

    if args.json:
        print(json.dumps({"pdf": str(pdf), "terme": terme}))
    else:
        print(str(pdf))


if __name__ == "__main__":
    main()
