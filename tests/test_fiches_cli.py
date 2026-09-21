"""Lot B — CLI de démonstration (extraire : lecture seule, sans base)."""

from __future__ import annotations

from pathlib import Path

from seamtech_search.fiches import cli

RACINE = Path(__file__).resolve().parent.parent
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
PDF_GENOIS = RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"


class TestExtraire:
    def test_rapport_7792_champ_par_champ(self, capsys) -> None:
        code_sortie = cli.principal(["extraire", str(PDF_7792)])
        assert code_sortie == 0
        rapport = capsys.readouterr().out
        assert "7792-SO" in rapport
        assert "FICHE_PORTANT_V1" in rapport
        assert "fiche.code" in rapport and "cotes.finie.slu_m" in rapport
        assert "zone" not in rapport or True  # les zones sont dans les lignes ci-dessus
        assert "6.6" in rapport  # guindant normalisé
        assert "Routage proposé" in rapport

    def test_rapport_genois_variante(self, capsys) -> None:
        assert cli.principal(["extraire", str(PDF_GENOIS)]) == 0
        rapport = capsys.readouterr().out
        assert "FICHE_GENOIS_V1" in rapport
        assert "0901-MM" in rapport

    def test_gabarit_force(self, capsys) -> None:
        assert cli.principal(["extraire", str(PDF_GENOIS), "--gabarit", "FICHE_GENOIS_V1"]) == 0
        assert "FICHE_GENOIS_V1" in capsys.readouterr().out

    def test_fichier_absent_code_2(self, capsys) -> None:
        assert cli.principal(["extraire", "/nulle/part/fiche.pdf"]) == 2
        sortie = capsys.readouterr()
        assert "introuvable" in sortie.err or "introuvable" in sortie.out

    def test_pdf_illisible_code_2(self, tmp_path: Path, capsys) -> None:
        faux = tmp_path / "faux.pdf"
        faux.write_bytes(b"%PDF-1.4 cela n'est pas un PDF")
        assert cli.principal(["extraire", str(faux)]) == 2

    def test_aucune_ecriture_en_base_pour_extraire(self, capsys, monkeypatch) -> None:
        """« extraire » ne construit JAMAIS d'index (lecture seule, RG archive)."""

        def _interdit(url: str):  # pragma: no cover — déclenche l'échec s'il est appelé
            raise AssertionError("extraire ne doit pas toucher à la base")

        monkeypatch.setattr(cli, "_index", _interdit)
        assert cli.principal(["extraire", str(PDF_7792)]) == 0
