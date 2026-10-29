"""Termes uniques du contrôle OCR navigateur : invariants vérifiés.

Contexte (échec de CI du 28/10) : le spec Playwright tirait un terme unique
hexadécimal (``crypto.randomBytes``), donc contenant des « 0 ». Sur l'image de
synthèse, tesseract lit « O » : l'assertion stricte du spec échouait sur un
document pourtant parfaitement océrisé — « GENOA0FFF… » devenait
« GENOAOFFF… ». Un faux négatif qui masquait le vrai contrat pendant que la
CI restait rouge.

Deux causes distinctes ont été mesurées (voir docs/OCR_CI.md) :

1. l'ambiguïté de caractère (0/O) — d'où l'alphabet sans chiffre, ni I, ni O ;
2. le rendu : une police **proportionnelle** à 32 px fait halluciner des
   caractères à tesseract (« GENOABMWFX » → « GENOABMWEFX », « …FXJ » →
   « …FX] »). Une police monospace rend les mêmes chaînes sans erreur.

Ces tests verrouillent les deux. Ils ne desserrent AUCUNE assertion du spec :
ils garantissent au contraire qu'il peut rester strict.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.helpers_ocr_scan import (
    ALPHABET_OCR_SUR,
    _get_font,
    gen_pdf_custom,
    terme_unique_ocr_sur,
)

RACINE = Path(__file__).resolve().parents[1]

#: Caractères dont la confusion avec un autre est mesurée, ou qui ne sont pas
#: des lettres : ils ne doivent jamais servir de terme de preuve.
CARACTERES_INTERDITS = set("0123456789IOJ")


def test_alphabet_sans_caractere_ambigu() -> None:
    assert len(ALPHABET_OCR_SUR) == len(set(ALPHABET_OCR_SUR)), "doublon dans l'alphabet"
    assert not (set(ALPHABET_OCR_SUR) & CARACTERES_INTERDITS), sorted(
        set(ALPHABET_OCR_SUR) & CARACTERES_INTERDITS
    )
    assert ALPHABET_OCR_SUR == ALPHABET_OCR_SUR.upper()
    assert len(ALPHABET_OCR_SUR) >= 20, "alphabet trop petit : unicité des termes en jeu"


def test_termes_generes_sont_surs_et_uniques() -> None:
    vus: set[str] = set()
    for _ in range(2000):
        terme = terme_unique_ocr_sur("ZEPHYR")
        assert terme.startswith("ZEPHYR"), terme
        suffixe = terme[len("ZEPHYR") :]
        assert len(suffixe) == 10, terme
        assert set(suffixe) <= set(ALPHABET_OCR_SUR), terme
        assert not (set(terme) & CARACTERES_INTERDITS), terme
        assert terme not in vus, f"collision de terme unique : {terme}"
        vus.add(terme)
    assert len(vus) == 2000


def test_prefixe_est_assaini(memory_safe: None = None) -> None:
    """Un préfixe lisible est conservé, mais nettoyé de tout caractère ambigu."""
    terme = terme_unique_ocr_sur("gén oa-01")
    # é, espace, tiret, « o » (confusion 0/O) et les chiffres sont retirés.
    assert terme.startswith("GNA"), terme
    assert not (set(terme) & CARACTERES_INTERDITS), terme


def test_rendu_en_police_monospace() -> None:
    """La police de rendu est à chasse fixe quand elle existe sur la machine.

    C'est la moitié du correctif : en proportionnelle, tesseract invente un
    caractère entre deux glyphes (« BMWFX » → « BMWEFX »).
    """
    police = _get_font(32)
    chemin = getattr(police, "path", None)
    if isinstance(chemin, str) and Path(chemin).exists():
        assert "Mono" in Path(chemin).name, (
            f"police de rendu non monospace ({chemin}) : risque d'hallucination OCR"
        )
    else:  # pragma: no cover - image sans police TrueType disponible
        pytest.skip("aucune police TrueType sur cette machine")


def test_pdf_de_preuve_n_a_pas_de_couche_texte(tmp_path: Path) -> None:
    """Le terme n'est présent QUE dans l'image : la recherche prouve l'OCR."""
    import pdfplumber

    terme = terme_unique_ocr_sur("GENOA")
    pdf = gen_pdf_custom(tmp_path, terme, lang="en", filename="preuve.pdf")
    assert pdf.exists() and pdf.stat().st_size > 1000
    with pdfplumber.open(str(pdf)) as document:
        natif = "".join((page.extract_text() or "") for page in document.pages)
    assert terme.lower() not in natif.lower(), "couche texte présente : la preuve OCR serait factice"
    assert terme not in pdf.name


def test_script_e2e_prend_le_terme_cote_python() -> None:
    """Le spec Playwright ne tire plus de terme lui-même.

    Le terme doit venir de ``generate_scan_pdf.py --prefix … --json``, pour
    qu'un test unitaire puisse verrouiller son alphabet. Un retour à
    ``crypto.randomBytes`` dans le spec réintroduirait l'échec de CI.
    """
    script = (RACINE / "frontend" / "e2e" / "generate_scan_pdf.py").read_text(encoding="utf-8")
    assert "--prefix" in script and "--json" in script
    assert "terme_unique_ocr_sur" in script

    spec = (RACINE / "frontend" / "e2e" / "scan-browser-ocr.spec.ts").read_text(encoding="utf-8")
    assert "generatePdfAvecTermeSur" in spec
    assert "--prefix" in spec
    assert "crypto.randomBytes(" not in spec, "le spec retire à nouveau ses termes au hasard"
    # Les assertions strictes qui FONT la preuve doivent rester en place.
    assert "expect(normRaw).toContain(normUniq)" in spec
    assert "freshFixture" not in spec and "test.describe.configure({ retries" not in spec
    assert not re.search(r"retries:\s*[1-9]", spec), "un réessai salvateur a été ajouté au spec"
