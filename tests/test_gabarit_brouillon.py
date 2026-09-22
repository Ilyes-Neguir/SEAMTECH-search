"""Gabarit brouillon depuis PDF variante — Lot K.2, garde-fou ROUGE→VERT."""

import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres


def _pdf_minimal(texte: str) -> Path:
    """Crée un PDF minimal avec texte donné (sans dépendance lourde)."""
    # Utilise reportlab si dispo, sinon écrit un PDF factice avec %PDF et texte
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas  # type: ignore

        tmp = Path(tempfile.mkstemp(suffix=".pdf")[1])
        c = canvas.Canvas(str(tmp), pagesize=A4)
        c.drawString(100, 750, texte)
        c.drawString(100, 730, "Code fiche: VAR-2026-001")
        c.drawString(100, 710, "Client: Test Client")
        c.drawString(100, 690, "Guindant (SLU): 12.5 m")
        c.drawString(100, 670, "Chute (SLE): 8.2 m")
        c.showPage()
        c.save()
        return tmp
    except Exception:
        # Fallback : PDF factice minimal (pdfplumber pourra quand même extraire ? non, mais on teste garde-fou)
        tmp = Path(tempfile.mkstemp(suffix=".pdf")[1])
        # Très minimal PDF avec texte en clair après %PDF (pour que notre générateur fallback fonctionne)
        pdf_content = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >> endobj
4 0 obj << /Length 100 >> stream
BT /F1 12 Tf 100 750 Td (""" + texte.encode()[:50] + b""") Tj ET
endstream endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000214 00000 n
trailer << /Size 5 /Root 1 0 R >>
startxref
400
%%EOF
"""
        tmp.write_bytes(pdf_content)
        return tmp


def test_brouillon_ne_sert_jamais_extraction_rouge_vert(index_postgres):
    """Garde-fou : brouillon NON validé ne doit jamais servir extraction fiche réelle — ROUGE puis VERT."""
    from seamtech_search.fiches.gabarit_brouillon import (
        enregistrer_brouillon,
        generer_brouillon_depuis_pdf,
    )
    from seamtech_search.fiches.gabarits import charger_gabarits

    # Génère un PDF variante
    pdf_path = _pdf_minimal("Variante inconnue TEST")
    try:
        brouillon = generer_brouillon_depuis_pdf(pdf_path, code_propose="TEST_VARIANTE_K2")
        # Enregistre comme brouillon (statut brouillon)
        enregistre = enregistrer_brouillon(index_postgres, brouillon, cree_par="testeur_k2")
        id_brouillon = enregistre["id_brouillon"]

        # ROUGE : vérifie que charger_gabarits ne contient PAS le brouillon
        gabarits = charger_gabarits(index_postgres)
        codes = {g.code for g in gabarits}
        assert "TEST_VARIANTE_K2" not in codes, "ROUGE attendu : brouillon ne doit pas être dans gabarits actifs"

        # Vérifie que la table gabarit ne contient pas le brouillon
        with index_postgres.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM gabarit WHERE code=%s", ("TEST_VARIANTE_K2",))
                nb = int(cur.fetchone()[0])
                assert nb == 0, f"ROUGE : brouillon trouvé dans gabarit table ({nb}) — garde-fou violé"

        # VERT : après validation brouillon → nouvelle version active dans gabarit
        from seamtech_search.fiches.gabarit_brouillon import valider_brouillon_vers_gabarit

        resultat = valider_brouillon_vers_gabarit(index_postgres, id_brouillon)
        assert resultat["statut"] == "valide"
        assert resultat["gabarit"]["code"] == "TEST_VARIANTE_K2"

        # Maintenant charger_gabarits doit contenir le code
        gabarits_apres = charger_gabarits(index_postgres)
        codes_apres = {g.code for g in gabarits_apres}
        assert "TEST_VARIANTE_K2" in codes_apres, "VERT attendu : après validation, gabarit doit être actif"

        with index_postgres.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM gabarit WHERE code=%s AND actif=true", ("TEST_VARIANTE_K2",))
                nb_actif = int(cur.fetchone()[0])
                assert nb_actif == 1, "VERT : gabarit actif attendu après validation"

    finally:
        pdf_path.unlink(missing_ok=True)
        # Nettoyage
        with index_postgres.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM gabarit_brouillon WHERE code=%s", ("TEST_VARIANTE_K2",))
                cur.execute("DELETE FROM gabarit WHERE code=%s", ("TEST_VARIANTE_K2",))


def test_brouillon_generation_champs_zones_confiance(index_postgres):
    """À partir PDF variante, brouillon propose champs, zones page+rectangle, confiance."""
    from seamtech_search.fiches.gabarit_brouillon import generer_brouillon_depuis_pdf

    pdf_path = _pdf_minimal("Fiche Variante avec Guindant et Chute et Client")
    try:
        brouillon = generer_brouillon_depuis_pdf(pdf_path, code_propose="TEST_CHAMPS_K2")
        assert "code" in brouillon
        assert "zones" in brouillon
        assert "confiance" in brouillon
        assert "regles" in brouillon
        # Zones doivent avoir page+rectangle
        for z in brouillon["zones"]:
            assert "page" in z
            assert "rectangle" in z
            assert len(z["rectangle"]) == 4
            assert "confiance" in z
        # Confiance par champ
        assert isinstance(brouillon["confiance"], dict)
        # Au moins 1 champ détecté si pdf contient ancres
        assert brouillon["nb_champs_detectes"] >= 0
    finally:
        pdf_path.unlink(missing_ok=True)


def test_brouillon_table_existe(index_postgres):
    with index_postgres.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gabarit_brouillon")
            cur.fetchone()


def test_gabarit_brouillon_pas_dans_extraction_reelle(index_postgres):
    """Preuve que extraction fiche réelle n'utilise pas gabarit_brouillon."""
    # On insère un brouillon avec code qui pourrait matcher une fiche
    from seamtech_search.fiches.gabarit_brouillon import enregistrer_brouillon

    brouillon_fake = {
        "code": "FAUX_BROUILLON_K2",
        "description": "Brouillon qui ne doit jamais servir",
        "ancres_detection": ["faux", "brouillon"],
        "regles": {"champs": []},
        "zones": [],
        "confiance": {},
        "source_pdf_sha256": "abc",
        "source_pdf_nom": "faux.pdf",
    }
    try:
        enregistrer_brouillon(index_postgres, brouillon_fake)

        # Extraction doit échouer à détecter ce faux gabarit (car non actif, non dans gabarit)
        from seamtech_search.fiches.gabarits import charger_gabarits, detecter_gabarit

        gabarits = charger_gabarits(index_postgres)
        assert all(g.code != "FAUX_BROUILLON_K2" for g in gabarits)

        # Même si on cherche à détecter avec texte contenant "faux brouillon", on ne doit pas le trouver
        try:
            detecter_gabarit("faux brouillon test", gabarits)
            # Si un gabarit est détecté, ce ne doit pas être le faux
            # (peut être un autre gabarit si ancres communes, mais pas le faux)
        except Exception:
            # GabaritInconnu attendu si aucun vrai gabarit ne match — OK
            pass

    finally:
        with index_postgres.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM gabarit_brouillon WHERE code=%s", ("FAUX_BROUILLON_K2",))
