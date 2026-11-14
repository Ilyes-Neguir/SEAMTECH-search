"""L'extracteur de causes du verdict Windows : ce qu'il publie, et ce qu'il jette.

Pourquoi ce fichier existe.

Pendant deux lots, le diagnostic Windows est resté aveugle sur un échec réel,
en publiant comme cause :

    tests/...::test_... :: assert 1 == 0

« assert 1 == 0 » ne dit rien : c'est l'assertion nue. Or une assertion qui
PORTE un message est rendue par pytest sur deux temps — le message d'abord,
l'assertion ensuite, en dernière ligne. Prendre la dernière ligne revenait
donc à jeter systématiquement le message, c'est-à-dire la cause.

Ces épreuves verrouillent les quatre formes de rendu, dont deux doivent rester
inchangées : toute « amélioration » de l'extracteur qui casserait les cas
d'assertion nue serait une régression déguisée.
"""
from __future__ import annotations

import ci.resumer_echecs as resumer


def _cause(texte: str) -> str:
    return resumer._cause(texte, None)


def test_un_message_de_trace_n_est_plus_ecrase_par_l_assertion():
    """Le cas qui a coûté deux lots : la cause est dans le message, pas dans l'assertion."""
    texte = "\n".join(
        [
            "E       AssertionError: Traceback (most recent call last):",
            'E         File "…\\sauvegarde.py", line 2333, in main',
            "E           rapport = restaurer_complet(",
            "E       seamtech_search.sauvegarde.SauvegardeError: Restauration locale sûre indisponible sur cette plateforme",
            "E       assert 1 == 0",
        ]
    )
    cause = _cause(texte)
    assert "SauvegardeError" in cause, cause
    assert "indisponible sur cette plateforme" in cause, cause
    # L'assertion nue n'est PAS la cause : si elle reparait, le message est perdu.
    assert cause != "assert 1 == 0", cause


def test_un_message_qui_identifie_n_est_pas_ecrase():
    texte = "\n".join(
        [
            "E       AssertionError: sur 123 fiches déposées, la recherche n'en rend que 100",
            "E       assert 100 == 123",
        ]
    )
    cause = _cause(texte)
    assert "123 fiches" in cause, cause


def test_une_assertion_nue_reste_publiee_tel_quelle():
    """Sans message, l'assertion EST la cause : on ne doit surtout pas la jeter."""
    texte = "E       assert 'c:\\\\users\\\\r...\\\\fiche.pdf' == 'C:\\\\Users\\\\r...\\\\fiche.pdf'"
    cause = _cause(texte)
    assert cause.startswith("assert "), cause
    assert "c:\\users" in cause or "c:\\\\users" in cause, cause


def test_l_explication_pytest_reste_publiee():
    """« Right contains one more item » est plus disant que l'assertion : inchangé."""
    texte = "\n".join(
        [
            "E       AssertionError: assert ['a'] == ['a', True]",
            "E        ",
            "E         Right contains one more item: True",
            "E        ",
            "E         Full diff:",
            "E         - ['a']",
            "E         + ['a', True]",
        ]
    )
    assert "Right contains one more item" in _cause(texte)
