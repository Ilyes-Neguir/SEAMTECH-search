"""Erreurs de parseurs tiers : ni fuite en logs ni détail de panne indexé."""
from pathlib import Path

import openpyxl

from seamtech_search import import_pipeline


def test_panne_excel_expurge_exception_et_chemin(tmp_path: Path, monkeypatch, caplog):
    secret = "SECRET-SYNTHETIQUE-TEST"
    def panne(*_args, **_kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(openpyxl, "load_workbook", panne)
    with caplog.at_level("DEBUG", logger=import_pipeline.logger.name):
        resultat = import_pipeline.extract_excel_summary(tmp_path / f"{secret}.xlsx")
    assert "indisponible" in resultat.summary_text.lower()
    assert secret not in resultat.summary_text and secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == import_pipeline.logger.name)


def test_classeur_ferme_si_iteration_echoue(tmp_path: Path, monkeypatch):
    class Classeur:
        sheetnames = ["synthese"]
        ferme = False
        def __getitem__(self, _key):
            raise RuntimeError("panne synthétique")
        def close(self):
            self.ferme = True
    classeur = Classeur()
    monkeypatch.setattr(openpyxl, "load_workbook", lambda *_args, **_kwargs: classeur)
    resultat = import_pipeline.extract_excel_summary(tmp_path / "synthese.xlsx")
    assert "indisponible" in resultat.summary_text.lower()
    assert classeur.ferme
