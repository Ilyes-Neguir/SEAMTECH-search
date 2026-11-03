"""Ce que fait le produit quand le système ne sait pas ouvrir par descripteur de
répertoire (``os.open`` absent de ``os.supports_dir_fd``) — ce qu'est Windows.

Windows natif n'est pas joignable depuis ce dépôt. On ne SUPPOSE donc pas le
comportement du produit sur Windows : on le **reproduit**. La condition testée
par ``sauvegarde._ouvrir_source_bundle`` est publique et explicite ::

    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "O_DIRECTORY") or os.open not in os.supports_dir_fd):
        raise SauvegardeError("Lecture confinée du bundle indisponible sur cette plateforme")

Vider ``os.supports_dir_fd`` déclenche ce refus sur n'importe quelle machine.
Les quatre conséquences mesurées sur le runner Windows (runs 41564776168 et
41569952579) sont donc vérifiables ici, par n'importe qui, sans Windows :

* ``assert change`` faux — ``os.open`` n'est jamais appelé (le refus précède
  toute ouverture) ;
* ``IndexError: list index out of range`` — ``restaurer`` échoue avant
  d'exécuter la moindre commande, donc aucun appel à enregistrer ;
* ``[XPASS(strict)]`` — la restauration multi-fichiers ne publie rien du tout,
  donc « le premier fichier ne reste pas » se trouve vrai pour la mauvaise
  raison.

Ce fichier est la PREUVE de ces écarts, pas leur récit.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from seamtech_search import sauvegarde
from seamtech_search.sauvegarde import SauvegardeError

pytestmark = pytest.mark.sauvegarde


@pytest.fixture
def sans_dir_fd(monkeypatch):
    """Le système ne sait pas ouvrir un chemin relatif à un descripteur."""
    monkeypatch.setattr(os, "supports_dir_fd", frozenset())
    return monkeypatch


def test_lecture_confinee_refusee_sans_dir_fd(tmp_path, sans_dir_fd):
    """Le refus est explicite, typé, et nomme la plateforme."""
    (tmp_path / "dump").write_bytes(b"contenu-synthetique")
    with pytest.raises(SauvegardeError, match="indisponible sur cette plateforme"):
        with sauvegarde._ouvrir_source_bundle(tmp_path, Path("dump")):
            pytest.fail("aucune ouverture confinée sans dir_fd")


def test_instantane_dump_prive_n_ouvre_rien(tmp_path, sans_dir_fd, monkeypatch):
    """Cause de « assert change » : aucune ouverture n'a seulement lieu."""
    dump = tmp_path / "dump"
    dump.write_bytes(b"contenu-synthetique")
    ouverts: list[object] = []
    original = os.open

    def espion(chemin, *args, **kwargs):
        ouverts.append(os.fspath(chemin) if not isinstance(chemin, int) else chemin)
        return original(chemin, *args, **kwargs)

    monkeypatch.setattr(os, "open", espion)
    with pytest.raises(SauvegardeError):
        with sauvegarde._instantane_dump_prive(dump):
            pytest.fail("aucune copie privée sans lecture confinée")
    # Le répertoire temporaire de la copie peut être ouvert ; la SOURCE, jamais.
    assert not [c for c in ouverts if isinstance(c, str) and Path(c) == dump], (
        f"la source a été ouverte ({ouverts}) alors que la lecture confinée est refusée"
    )


def test_restaurer_refuse_avant_toute_commande(tmp_path, sans_dir_fd, monkeypatch):
    """Cause de « IndexError: list index out of range » : rien n'est exécuté."""
    import hashlib

    dump = tmp_path / "dump.dump"
    contenu = b"dump synthetique"
    dump.write_bytes(contenu)
    manifeste = {"dump": {"sha256": hashlib.sha256(contenu).hexdigest(), "octets": len(contenu)}}
    commandes: list[list[str]] = []

    def run(args, **_kwargs):
        commandes.append(list(args))
        raise AssertionError("aucune commande ne doit être lancée après un refus")

    monkeypatch.setattr(sauvegarde.subprocess, "run", run)
    monkeypatch.setattr(sauvegarde, "_trouver_binaire", lambda nom: nom)
    with pytest.raises(SauvegardeError):
        sauvegarde.restaurer("postgresql://synthetique/cible", manifeste, dump)
    assert commandes == []


def test_publication_multi_fichiers_ne_publie_rien(tmp_path, sans_dir_fd, monkeypatch):
    """Cause du XPASS : rien n'est publié, donc « rien ne reste » est vrai.

    Le test ``test_erreur_seconde_publication_ne_laisse_pas_premiere`` constate
    que la première copie RESTE après l'échec de la seconde (publication non
    transactionnelle) et porte ce constat en ``xfail``. Ici la restauration
    refuse avant la première copie : le test réussirait pour la MAUVAISE
    raison. On le démontre, et il saute sur ces plateformes.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a").write_bytes(b"a")
    (bundle / "b").write_bytes(b"b")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "a", "sha256": "0" * 64},
        {"id": 2, "path_relatif": "b", "sha256": "1" * 64},
    ]}}
    sortie = tmp_path / "out"
    with pytest.raises(SauvegardeError):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, sortie)
    assert not sortie.exists() or not any(sortie.iterdir()), (
        "la publication a écrit quelque chose malgré le refus de lecture confinée"
    )
