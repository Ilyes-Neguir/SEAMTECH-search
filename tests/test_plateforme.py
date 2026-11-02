"""Les refus de plateforme doivent être ÉNUMÉRABLES, OPPOSABLES et TESTABLES.

Trois exigences, une chacune :

* énumérable : ``capacites()`` répond pour chaque primitive POSIX utilisée par
  le confinement des fichiers. Une limite qu'on ne peut pas lister est une
  limite qu'on découvre en panne.
* opposable : le message d'un refus nomme la capacité manquante, sa conséquence
  et la plateforme. « Secure folder scanning is not supported on this
  platform » ne disait pas s'il fallait changer de poste, de configuration ou
  de produit — c'est ce message, rencontré pendant la qualification Windows,
  qui a motivé ``seamtech_search/plateforme.py``.
* testable : la branche « plateforme sans ces primitives » est exercée ici en
  forçant ``os.name``. Sans cela, elle ne serait vérifiée nulle part avant
  d'être rencontrée par l'utilisatrice. Forcer ``os.name`` ne prouve PAS que
  Windows se comporte ainsi — cela prouve que le produit refuse proprement
  quand la capacité est absente. La différence est dite, pas maquillée.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from seamtech_search import plateforme
from seamtech_search.import_pipeline import _racine_scan_confinee


def test_chaque_capacite_connue_est_mesuree() -> None:
    """Une capacité déclarée mais non mesurée serait un trou silencieux."""
    assert set(plateforme.capacites()) == set(plateforme.CAPACITES)


def test_une_capacite_inconnue_est_rejetee() -> None:
    """``exiger("nawak")`` doit échouer fort, pas passer faute de quoi exiger."""
    with pytest.raises(ValueError, match="capacité inconnue"):
        plateforme.exiger("capacite_qui_nexiste_pas")


@pytest.mark.skipif(os.name != "posix", reason="le banc POSIX est la référence")
def test_sur_posix_le_confinement_est_disponible() -> None:
    etat = plateforme.capacites()
    assert etat["scan_ancre_descripteur"], f"capacités : {plateforme.resume()}"
    assert etat["procfs_fd"], f"capacités : {plateforme.resume()}"
    assert etat["verrou_flock"], f"capacités : {plateforme.resume()}"
    assert "scan_ancre_descripteur=oui" in plateforme.resume()


def test_sans_les_primitives_posix_le_refus_nomme_la_capacite(monkeypatch, tmp_path: Path) -> None:
    """Plateforme sans primitives : refus, et le message dit laquelle manque."""
    monkeypatch.setattr(os, "name", "nt")
    etat = plateforme.capacites()
    assert etat and not any(etat.values()), f"aucune capacité ne devrait être disponible : {etat}"

    for capacite in plateforme.CAPACITES:
        with pytest.raises(OSError) as erreur:
            plateforme.exiger(capacite)
        message = str(erreur.value)
        assert capacite in message, message
        # Le message doit dire que ce n'est pas un réglage : sinon l'atelier
        # cherche une option qui n'existe pas.
        assert "ne se contourne pas par un réglage" in message, message
        assert "os.name=nt" in message, message

    # Et le refus atteint Effectivement l'appelant du confinement.
    dossier = tmp_path / "dossier"
    dossier.mkdir()
    with pytest.raises(OSError, match="scan_ancre_descripteur"):
        with _racine_scan_confinee(dossier):
            pass  # pragma: no cover - n'est jamais atteint


def test_le_resume_reste_lisible_quand_tout_manque(monkeypatch) -> None:
    monkeypatch.setattr(os, "name", "nt")
    resume = plateforme.resume()
    for capacite in plateforme.CAPACITES:
        assert f"{capacite}=non" in resume, resume


_SONDE_IMPORT_SANS_FCNTL = '''
import sys


class _BloqueFcntl:
    """Rend ``import fcntl`` impossible, comme sur Windows."""

    def find_spec(self, name, path=None, target=None):
        if name == "fcntl":
            raise ImportError("No module named 'fcntl'")
        return None


sys.meta_path.insert(0, _BloqueFcntl())
import seamtech_search.ml.routes as routes

assert routes.fcntl is None, "fcntl devrait valoir None quand le module est absent"
print("OK")
'''


def test_le_module_ml_s_importe_sans_fcntl() -> None:
    """Un ``import fcntl`` au niveau du module rend l'application NON IMPORTABLE hors POSIX.

    C'est exactement ce qui est arrivé : quatre fichiers de tests échouaient
    sur ``ModuleNotFoundError: No module named 'fcntl'`` pendant la
    qualification Windows (run 41368359949) — ``seamtech_search/ml/routes.py``
    l'importait au niveau du module, donc l'application entière mourait à
    l'import au lieu de refuser une seule route.

    La sonde tourne dans un interpréteur SÉPARÉ : bloquer ``fcntl`` dans celui
    du banc casserait les autres tests.
    """
    import subprocess
    import sys as _sys

    resultat = subprocess.run(
        [_sys.executable, "-c", _SONDE_IMPORT_SANS_FCNTL],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert resultat.returncode == 0, (
        f"import impossible sans fcntl : {resultat.stdout}{resultat.stderr}"
    )
    assert "OK" in resultat.stdout, resultat.stdout


def test_aucun_import_posix_non_garde_au_niveau_du_module() -> None:
    """Aucun module du produit n'importe ``fcntl`` ou ``pwd`` hors garde.

    Filet statique complémentaire du test précédent : il couvre les modules
    qu'aucune sonde n'importe explicitement, et il échoue dès qu'un
    ``import fcntl`` réapparaît en tête de fichier.
    """
    import re

    racine = Path(__file__).resolve().parent.parent / "seamtech_search"
    motifs = re.compile(r"^(?:import (?:fcntl|pwd|grp|termios)|from (?:fcntl|pwd|grp|termios) import)\b", re.M)
    coupables: list[str] = []
    for fichier in sorted(racine.rglob("*.py")):
        texte = fichier.read_text(encoding="utf-8")
        for numero, ligne in enumerate(texte.splitlines(), start=1):
            if motifs.match(ligne):
                coupables.append(f"{fichier.relative_to(racine)}:{numero} {ligne.strip()}")
    assert not coupables, (
        "import POSIX non gardé au niveau du module (l'application devient "
        "non importable hors POSIX) :\n  - " + "\n  - ".join(coupables)
    )


def test_entrainement_refuse_sans_verrou_exclusif(monkeypatch) -> None:
    """Sans ``fcntl``, l'entraînement est REFUSÉ (503), pas lancé à l'aveugle."""
    from fastapi import HTTPException

    from seamtech_search.ml import routes

    monkeypatch.setattr(routes, "fcntl", None)
    with pytest.raises(HTTPException) as erreur:
        routes._exiger_verrou_exclusif()
    assert erreur.value.status_code == 503
    assert "Verrou exclusif indisponible" in erreur.value.detail

    # Et dès que le verrou existe, la fonction ne refuse plus rien.
    monkeypatch.setattr(routes, "fcntl", object())
    assert routes._exiger_verrou_exclusif() is None
