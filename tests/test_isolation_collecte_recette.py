"""La collecte d'un module optionnel ne modifie pas les journaux des autres tests."""
import subprocess
import sys


def test_import_recette_ne_change_pas_niveau_global():
    script = '''import logging
logger = logging.getLogger("seamtech_search")
logger.setLevel(logging.INFO)
import tests.test_recette_corpus_reel
assert logger.level == logging.INFO
'''
    resultat = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=20)
    assert resultat.returncode == 0, resultat.stderr


def test_garde_journaux_recette_est_active_et_restauree(monkeypatch):
    import logging

    from tests import test_recette_corpus_reel as recette

    logger = logging.getLogger('seamtech_search')
    ancien = logger.level
    monkeypatch.setattr(recette, 'ACTIF', True)
    garde = recette._masquer_journaux_recette.__wrapped__()
    try:
        next(garde)
        assert logger.level == logging.WARNING
    finally:
        try:
            next(garde)
        except StopIteration:
            pass
    assert logger.level == ancien
