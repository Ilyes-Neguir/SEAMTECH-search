"""Le verrou de sauvegarde sur une plateforme SANS ``flock`` (Windows).

Pourquoi ce fichier existe : le verrou de sauvegarde est un verrou de
RÉPERTOIRE sur POSIX — le parent, puis le dossier, puis le fichier. Windows
n'a ni ``flock`` ni verrou de répertoire. Le produit refusait donc toute
sauvegarde sur Windows (``SauvegardeError: Verrouillage sûr indisponible sur
cette plateforme``), ce qui rendait le point P0 « sauvegarde / restauration »
inexécutable sur le poste de l'atelier — et une sauvegarde qui refuse n'est pas
une sauvegarde.

L'équivalent retenu verrouille deux FICHIERS par plage d'octets
(``msvcrt.locking``) : un dans le parent, un dans le dossier. Le parent est
verrouillé le premier pour la même raison que sur POSIX : un renommage du
dossier de sauvegarde ne doit pas ouvrir la porte à un second détenteur.

Ces tests sont exécutés sur un banc POSIX en injectant un ``msvcrt`` factice
et en désactivant le chemin POSIX. Ce n'est PAS la preuve que Windows se
comporte comme ce double — c'est la preuve que la LOGIQUE du chemin Windows
est la bonne : deux verrous, dans cet ordre, libérés en ordre inverse, avec
refus nommé au chevauchement. La preuve plateau sera faite par le workflow
« Qualification Windows ».

Un test garde aussi la frontière : sur POSIX, le fichier du parent n'est
JAMAIS créé — le chemin Windows ne doit pas déborder sur le chemin Linux.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any

import pytest

from seamtech_search.sauvegarde import (
    NOM_VERROU,
    NOM_VERROU_PARENT,
    PLAGE_VERROU_WINDOWS,
    SauvegardeError,
    VerrouSauvegarde,
)


class _MsvcrtFactice:
    """Double de ``msvcrt`` : il enregistre les appels au lieu de verrouiller."""

    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self, *, echec_sur_chemin: Path | None = None) -> None:
        self.appels: list[tuple[str, int]] = []
        self._echec_sur = echec_sur_chemin

    def locking(self, fd: int, mode: int, taille: int) -> None:
        self.appels.append(("NBLCK" if mode == self.LK_NBLCK else "UNLCK", fd))
        assert taille == PLAGE_VERROU_WINDOWS
        if self._echec_sur is None or mode != self.LK_NBLCK:
            return
        # Comparaison par identité de fichier, pas par numéro de descripteur :
        # une seconde ouverture du même fichier reçoit un autre numéro.
        if os.fstat(fd)[1:3] == os.stat(self._echec_sur)[1:3]:
            raise OSError(errno.EACCES, "plage déjà verrouillée")


def _forcer_windows(monkeypatch: pytest.MonkeyPatch, msvcrt: Any) -> None:
    import seamtech_search.sauvegarde as sauvegarde

    monkeypatch.setattr(sauvegarde, "msvcrt", msvcrt, raising=False)
    monkeypatch.setattr(VerrouSauvegarde, "_confinement_posix", staticmethod(lambda: False))


def test_windows_verrouille_le_parent_puis_le_dossier_et_libere_en_ordre_inverse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    double = _MsvcrtFactice()
    _forcer_windows(monkeypatch, double)
    dossier = tmp_path / "sauvegardes"

    with VerrouSauvegarde(dossier) as verrou:
        fds = list(verrou._fds_verrouilles)
        assert len(fds) == 2, "deux verrous : le parent, puis le dossier"
        assert [double.appels[i][0] for i in (0, 1)] == ["NBLCK", "NBLCK"]
        # Le parent est verrouillé AVANT le dossier (renommage du dossier).
        assert (tmp_path / NOM_VERROU_PARENT).is_file()
        assert (dossier / NOM_VERROU).is_file()
        # Les descripteurs sont bien ceux des deux fichiers, dans cet ordre.
        assert os.read(fds[0], 0) == b""
        assert os.read(fds[1], 0) == b""

    # Libération en ordre inverse du verrou du dossier, puis du parent.
    assert [mode for mode, _ in double.appels[2:]] == ["UNLCK", "UNLCK"]
    assert [fd for _, fd in double.appels[2:]] == [fds[1], fds[0]]
    for fd in fds:
        with pytest.raises(OSError):
            os.fstat(fd)  # tous les descripteurs sont fermés


def test_windows_chevauchement_refuse_avec_un_motif_nomme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le second venu doit lire « une sauvegarde est déjà en cours »."""
    dossier = tmp_path / "sauvegardes"
    double = _MsvcrtFactice()
    _forcer_windows(monkeypatch, double)

    # Premier détenteur : on le laisse en place, on rejoue l'acquisition.
    with VerrouSauvegarde(dossier):
        deuxieme_appels = [appel for appel in double.appels if appel[0] == "NBLCK"]
        assert len(deuxieme_appels) == 2
        double_occupe = _MsvcrtFactice(echec_sur_chemin=tmp_path / NOM_VERROU_PARENT)
        _forcer_windows(monkeypatch, double_occupe)
        with pytest.raises(SauvegardeError) as erreur:
            VerrouSauvegarde(dossier).__enter__()
        assert "déjà en cours" in str(erreur.value), str(erreur.value)


def test_windows_echec_inattendu_est_un_refus_de_verrou_pas_une_panne_brute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une erreur qui n'est pas « déjà verrouillé » reste un refus nommé."""
    import seamtech_search.sauvegarde as sauvegarde

    class _MsvcrtCasse:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def locking(self, fd: int, mode: int, taille: int) -> None:
            raise OSError(errno.EIO, "erreur d'entrée-sortie")

    _forcer_windows(monkeypatch, _MsvcrtCasse())
    with pytest.raises(SauvegardeError, match="Impossible d'acquérir"):
        VerrouSauvegarde(tmp_path / "sauvegardes").__enter__()
    assert sauvegarde is not None


def test_sans_flock_ni_msvcrt_le_refus_reste_nomme_et_explicite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import seamtech_search.sauvegarde as sauvegarde

    monkeypatch.setattr(sauvegarde, "msvcrt", None, raising=False)
    monkeypatch.setattr(VerrouSauvegarde, "_confinement_posix", staticmethod(lambda: False))
    with pytest.raises(SauvegardeError, match="indisponible sur cette plateforme"):
        VerrouSauvegarde(tmp_path / "sauvegardes").__enter__()


def test_sur_posix_le_fichier_de_verrou_du_parent_n_est_jamais_cree(tmp_path: Path) -> None:
    """Frontière : le chemin Windows ne doit pas déborder sur le chemin POSIX."""
    if not VerrouSauvegarde._confinement_posix():
        pytest.skip("banc sans primitives POSIX : la frontière n'a pas de sens ici")
    dossier = tmp_path / "sauvegardes"
    with VerrouSauvegarde(dossier):
        pass
    assert not (tmp_path / NOM_VERROU_PARENT).exists(), (
        "le verrou du parent est propre au chemin sans flock : il ne doit pas "
        "apparaître sur POSIX, où le répertoire parent est verrouillé directement"
    )
