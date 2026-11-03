"""Pourquoi deux tests d'artefact échouent sur Windows — et pourquoi c'est une
limite nommée, pas une régression.

Sur Windows, ``_ouvrir_artefact_confine`` refuse : il exige ``procfs_fd`` et
``scan_ancre_descripteur``, deux primitives POSIX (``plateforme.exiger`` lève
alors un ``OSError`` qui nomme la capacité). La route
``/imports/{id}/artifacts/{artifact}`` capture cet OSError
(``except (OSError, ValueError): pass``) et ne sert PAS le fichier local :
c'est une décision de sécurité, pas un oubli — sans ouverture confinée,
servir le chemin reviendrait à suivre un lien éventuel. Elle retombe donc sur
l'objet distant, et les deux tests d'artefact, dont le double S3 ne renvoie
pas d'octets, échouent.

Ce fichier verrouille le MÉCANISME, reproductible sur n'importe quelle machine
et donc dans n'importe quelle intégration continue. Le lien avec les deux tests
est démontré par l'exécution sous ``ci/plugin_simulation_sans_primitives.py``,
qui reproduit exactement l'erreur vue sur le runner Windows :

    ValueError: objet IMP-1/abc/technical-report.pdf : bloc invalide
    (MagicMock au lieu d'octets)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from seamtech_search import fichiers_securises
from seamtech_search.plateforme import capacites


def test_ouverture_confinee_refuse_sans_la_capacite(tmp_path: Path, monkeypatch) -> None:
    """Sans la capacité, l'ouverture confinée REFUSE au lieu de servir un chemin."""
    fichier = tmp_path / "rapport.pdf"
    fichier.write_bytes(b"%PDF-1.4")

    def refuser(_capacite: str) -> None:
        raise OSError("Capacité de plateforme absente : procfs_fd (test)")

    monkeypatch.setattr(fichiers_securises, "exiger", refuser)

    with pytest.raises(OSError):
        fichiers_securises._ouvrir_artefact_confine(fichier, [tmp_path])


def test_ouverture_confinee_rend_un_descripteur_avec_la_capacite(tmp_path: Path) -> None:
    """Avec la capacité, la voie confinée fonctionne.

    Si ce test échoue, ce n'est plus « la plateforme n'a pas la primitive » :
    c'est l'ouverture confinée qui est cassée partout.
    """
    if not (capacites().get("procfs_fd") and capacites().get("scan_ancre_descripteur")):
        pytest.skip("primitives POSIX d'ancrage absentes sur cette plateforme")

    sous_dossier = tmp_path / "rapports"
    sous_dossier.mkdir()
    fichier = sous_dossier / "rapport.pdf"
    fichier.write_bytes(b"%PDF-1.4 contenu")

    fd = fichiers_securises._ouvrir_artefact_confine(fichier, [tmp_path])
    try:
        assert os.read(fd, 8) == b"%PDF-1.4"
    finally:
        os.close(fd)
