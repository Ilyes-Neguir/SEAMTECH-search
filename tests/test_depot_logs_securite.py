"""Les erreurs tierces de lecture ne doivent pas atteindre les logs de dépôt."""
from pathlib import Path

from seamtech_search.fiches import depot
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES


def test_scanner_pdf_erreur_ne_journalise_ni_nom_ni_exception(tmp_path: Path, monkeypatch, caplog):
    secret = "SECRET-SYNTHETIQUE-TEST"
    dossier = tmp_path / secret
    dossier.mkdir()
    (dossier / f"{secret}.pdf").write_bytes(b"pdf synthetique")
    def panne(*_args, **_kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(depot, "analyser_candidat_fiche", panne)
    with caplog.at_level("INFO", logger=depot.LOGGER.name):
        plan = depot.scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert not plan.accepte and "PDF illisible" in plan.raison_refus
    assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == depot.LOGGER.name)
