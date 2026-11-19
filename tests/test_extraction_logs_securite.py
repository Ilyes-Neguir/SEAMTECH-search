"""Les extraits et exceptions de parsing ne doivent pas être journalisés."""
from pathlib import Path

from seamtech_search.fiches import extraction
from seamtech_search.fiches.gabarits import GabaritDef, RegleChamp
from seamtech_search.fiches.modeles import FicheExtraite


def test_erreur_regle_ne_journalise_pas_exception_ou_trace(monkeypatch, caplog):
    secret = "SECRET-SYNTHETIQUE-TEST"
    gabarit = GabaritDef(code="TEST", ancres_detection=["test"], champs=[
        RegleChamp(cible="fiche.code", ancres=["code"]),
    ])
    def panne(*_args, **_kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(extraction, "_executer_regle", panne)
    with caplog.at_level("DEBUG", logger=extraction.LOGGER.name):
        fiche = extraction._extraire_pages([extraction.PageAnalysee(numero=0)], Path("synthese.pdf"), gabarit, [gabarit])
    assert fiche.gabarit_code == "TEST"
    assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == extraction.LOGGER.name)


def test_cote_invalide_ne_journalise_pas_texte_extrait(caplog):
    secret = "SECRET-SYNTHETIQUE-TEST"
    regle = RegleChamp(cible="cotes.finie.slu_m", ancres=["mesure"], type="decimal_m")
    fiche = FicheExtraite(fichier_pdf="synthese.pdf")
    with caplog.at_level("DEBUG", logger=extraction.LOGGER.name):
        extraction._construire_et_ranger(fiche, regle, secret, 0, [])
    assert not fiche.cotes
    assert secret not in caplog.text


def test_brouillon_panne_pdfplumber_ne_journalise_pas_exception(tmp_path, monkeypatch, caplog):
    import pdfplumber

    from seamtech_search.fiches import gabarit_brouillon
    secret = "SECRET-SYNTHETIQUE-TEST"
    def panne(*_args, **_kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(pdfplumber, "open", panne)
    monkeypatch.setattr(extraction, "analyser_pdf", lambda _path: [extraction.PageAnalysee(numero=0)])
    with caplog.at_level("DEBUG", logger=gabarit_brouillon.LOGGER.name):
        mots = gabarit_brouillon._extraire_mots_avec_bbox(tmp_path / "synthese.pdf")
    assert len(mots) == 1 and mots[0]["largeur_page"] == 0
    assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == gabarit_brouillon.LOGGER.name)


def test_index_table_panne_ne_journalise_pas_nom_ou_exception(tmp_path, monkeypatch, caplog):
    import pdfplumber.page
    from reportlab.pdfgen import canvas

    from seamtech_search import extractors
    secret = "SECRET-SYNTHETIQUE-TEST"
    chemin = tmp_path / (secret + ".pdf")
    pdf = canvas.Canvas(str(chemin))
    pdf.drawString(40, 700, "TEXTE SYNTHETIQUE")
    pdf.save()
    def panne(*_args, **_kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(pdfplumber.page.Page, "extract_tables", panne)
    with caplog.at_level("DEBUG", logger=extractors.logger.name):
        resultat = extractors.extract_file(chemin)
    assert resultat.status == "extracted" and "TEXTE SYNTHETIQUE" in resultat.text
    assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == extractors.logger.name)


def test_index_repli_ocr_ne_journalise_pas_nom_ou_exception(tmp_path, monkeypatch, caplog):
    from seamtech_search import extractors
    secret = "SECRET-SYNTHETIQUE-TEST"
    chemin = tmp_path / (secret + ".pdf")
    chemin.write_bytes(b"synthetique")
    monkeypatch.setattr(extractors, "_extract_pdf", lambda *_args: "[extraction unavailable: no text]")
    monkeypatch.setattr(extractors, "_has_command", lambda *_args: True)
    def panne(*_args, **_kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(extractors, "_extract_with_ocrmypdf", panne)
    monkeypatch.setattr(extractors, "_extract_pdf_via_pipeline_ocr", lambda *_args: ("texte synthétique", ""))
    with caplog.at_level("DEBUG", logger=extractors.logger.name):
        resultat = extractors.extract_file(chemin, enable_ocr=True)
    assert resultat.status == "extracted" and resultat.text == "texte synthétique"
    assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == extractors.logger.name)


def test_detection_gabarit_ne_journalise_pas_code_libre(caplog):
    from seamtech_search.fiches import gabarits
    secret = "SECRET-SYNTHETIQUE-TEST"
    gabarit = GabaritDef(code=secret, ancres_detection=["exemple"])
    with caplog.at_level("DEBUG", logger=gabarits.LOGGER.name):
        resultat = gabarits.detecter_gabarit("exemple", [gabarit])
    assert resultat is gabarit
    assert secret not in caplog.text


def test_gabarit_inconnu_ne_journalise_pas_exception(monkeypatch, caplog):
    from seamtech_search.fiches.gabarits import GabaritInconnu
    secret = "SECRET-SYNTHETIQUE-TEST"
    def panne(*_args, **_kwargs):
        raise GabaritInconnu(secret)
    monkeypatch.setattr(extraction, "extraire_fiche", panne)
    monkeypatch.setattr(extraction, "analyser_pdf", lambda _path: [extraction.PageAnalysee(numero=0)])
    with caplog.at_level("WARNING", logger=extraction.LOGGER.name):
        fiche = extraction.extraire_avec_filet(Path("synthese.pdf"), [])
    assert fiche.gabarit_code is None
    assert secret not in caplog.text


def test_traitement_inconnu_ne_journalise_pas_parametres_libres(caplog):
    secret = "SECRET-SYNTHETIQUE-TEST"
    for cible in (f"fiche.{secret}", f"cotes.{secret}"):
        fiche = FicheExtraite(fichier_pdf="synthese.pdf")
        regle = RegleChamp(cible=cible, ancres=["ancre"], traitement=secret)
        with caplog.at_level("WARNING", logger=extraction.LOGGER.name):
            extraction._construire_et_ranger(fiche, regle, "texte synthétique", 0, [])
        assert fiche.champs
    assert secret not in caplog.text


def test_anomalies_ne_journalisent_ni_fiche_ni_valeurs(caplog):
    from seamtech_search.fiches import anomalies
    from seamtech_search.fiches.modeles import Cotes

    secret = "SECRET-SYNTHETIQUE-TEST"
    fiche = FicheExtraite(code=secret, type_voile_libelle="Spi")
    fiche.cotes.append(Cotes(jeu="finie", slu_m=99.1234567, sf_m=0.1))
    with caplog.at_level("DEBUG", logger=anomalies.LOGGER.name):
        resultats = anomalies.evaluer_anomalies(fiche)
    assert resultats and any("99.1234567" in a.message for a in resultats)
    assert secret not in caplog.text and "99.1234567" not in caplog.text


def test_timeout_crawler_ne_journalise_pas_le_chemin(tmp_path, monkeypatch, caplog):
    import logging
    import subprocess

    from seamtech_search import crawler
    from seamtech_search.config import AppConfig

    secret = 'CHEMIN-SYNTHETIQUE-SECRET'
    cible = tmp_path / f'{secret}.pdf'
    cible.write_bytes(b'synthetique')

    class ProcessusExpire:
        pid = 1
        returncode = None
        premier = True

        def communicate(self, **kwargs):
            if self.premier:
                self.premier = False
                raise subprocess.TimeoutExpired(cmd=['synthetique'], timeout=1)
            return '', ''

        def kill(self):
            pass

    monkeypatch.setattr(crawler.subprocess, 'Popen', lambda *args, **kwargs: ProcessusExpire())
    # ``raising=False``, et c'est volontaire : Windows n'a PAS ``os.killpg``,
    # et le produit le sait déjà (``crawler.py`` : ``if os.name == "posix":
    # killpg(...) else process.kill()``). Écrire l'attribut sans cette
    # précaution faisait échouer l'épreuve AVANT même qu'elle ne mesure quoi
    # que ce soit — le secret n'était plus protégé par une preuve, mais par
    # une panne d'échafaudage. Ici, sous Windows, c'est ``process.kill()``
    # qui s'exerce, et ``ProcessusExpire.kill()`` le neutralise : l'épreuve
    # mesure la même chose partout.
    monkeypatch.setattr(crawler.os, 'killpg', lambda *args: None, raising=False)
    with caplog.at_level(logging.WARNING, logger=crawler.LOGGER.name):
        resultat = crawler._extract_in_process(cible, AppConfig(root_paths=[tmp_path]))
    assert resultat.status == 'timeout'
    assert caplog.records
    assert all(secret not in record.getMessage() for record in caplog.records)
