"""Tester la vie d'un PID ne doit JAMAIS le tuer.

Pourquoi ce fichier existe.

``os.kill(pid, 0)`` est l'idiome POSIX pour demander « ce PID vit-il encore ? » :
le signal 0 n'est pas délivré, l'appel ne sert qu'à sonder l'existence. Sur
WINDOWS, ``os.kill`` ne connaît pas le signal 0 — il appelle
``TerminateProcess`` avec la valeur reçue comme code de sortie. Autrement dit,
**un test de vie y devient une mise à mort**, et comme les PID sont recyclés,
le processus tué n'est pas forcément celui dont on a lu l'identifiant dans un
fichier de verrou.

L'idiome était employé à deux endroits du produit : le verrou de scan SQLite
(``indexer.scan_lock``) et le verrou de la nuit OCR (``ocr.etat``). Sur un
poste Windows, « reprendre un scan après un crash » pouvait donc tuer un
processus vivant — au pire celui-là même qui reprenait le scan.

Trouvé en cherchant pourquoi la suite de qualification Windows s'arrêtait
(runs 41499874854, 41501814193, 41508594754, 41515990228) : la piste n'a pas
abouti à l'explication de cet arrêt, mais elle a mis ce défaut au jour. Il est
réel, indépendant de l'arrêt, et il est corrigé ici.

Ce que ce test verrouille :

* sur POSIX, ``pid_vivant`` garde l'idiome (signal 0) — il est exact ;
* sur Windows, ``os.kill`` n'est **jamais** appelé (la branche est prouvée en
  forçant ``os.name``, comme ``test_plateforme.py`` le fait déjà) : si quelqu'un
  réintroduit l'idiome, ce test échoue AU LIEU de tuer un processus ;
* un PID inexistant est dit mort ; un PID vivant est dit vivant ;
* sur une plateforme inconnue, la réponse est « vivant » : un verrou périmé
  reste un verrou (faux positif bénin), supprimer le verrou d'un processus
  vivant ne l'est pas.

La bascule ``os.name`` est locale au module testé : patchée globalement,
``pathlib`` instancie ``WindowsPath`` et pytest s'effondre en plein rendu
d'échec (piège rencontré dans ``test_ocr_refus_non_posix.py``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from seamtech_search import plateforme


class OsFactice:
    """Module ``os`` factice : tout est délégué au vrai, sauf ``name`` et ``kill``."""

    def __init__(self, nom: str, tuer=None) -> None:
        self._reel = os
        self.name = nom
        self._tuer = tuer

    def kill(self, pid: int, signal: int) -> None:  # type: ignore[no-untyped-def]
        if self._tuer is not None:
            self._tuer(pid, signal)
        raise AssertionError(
            f"os.kill({pid}, {signal}) appelé sur une plateforme « {self.name} » : "
            "c'est précisément l'idiome interdit — sur Windows il TERMINE le processus"
        )

    def __getattr__(self, attribut: str):  # type: ignore[no-untyped-def]
        return getattr(self._reel, attribut)


@pytest.fixture
def tuer_interdit(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Bascule ``plateforme`` hors POSIX, où ``os.kill`` est un meurtre déguisé."""
    appels: list[tuple[int, int]] = []
    monkeypatch.setattr(plateforme, "os", OsFactice("nt", lambda pid, sig: appels.append((pid, sig))))
    monkeypatch.setattr(plateforme.sys, "platform", "win32")
    return appels


def pid_mort() -> int:
    """Un PID qui n'existe plus : celui d'un processus réellement terminé."""
    processus = subprocess.Popen([sys.executable, "-c", "pass"])
    processus.wait()
    return processus.pid


def test_sur_posix_lidiome_reste_exact() -> None:
    if os.name != "posix":  # pragma: no cover — exécuté tel quel sur Windows
        pytest.skip("l'idiome POSIX ne se vérifie que sur un système POSIX")
    assert plateforme.pid_vivant(os.getpid()) is True
    # L'idiome historique reste employé, mais il n'est plus écrit ailleurs :
    # ``pid_vivant`` est le seul endroit qui a le droit de s'en servir.
    assert plateforme.pid_vivant(pid_mort()) is False


def test_hors_posix_aucun_processus_nest_tue(tuer_interdit: list[tuple[int, int]]) -> None:
    """Le cœur du contrat : sonder ne tue pas, même si le noyau n'est pas interrogable.

    Sur une machine POSIX, la branche Windows ne peut pas appeler ``kernel32`` :
    elle retombe sur « vivant » (prudence). Ce que le test vérifie, c'est
    qu'aucun ``os.kill`` n'a été émis — c'est-à-dire qu'on n'a pas TUÉ pour
    savoir.
    """
    assert plateforme.pid_vivant(os.getpid()) is True
    assert tuer_interdit == [], "os.kill a été appelé pour tester la vie d'un PID"
    # Le processus de test tourne toujours : sans cela, rien de ce qui suit
    # ne s'exécuterait. C'est la preuve la plus directe qui soit.
    assert plateforme.pid_vivant(os.getpid()) is True


def test_pid_invalide_nest_jamais_vivant() -> None:
    for pid in (0, -1, -999):
        assert plateforme.pid_vivant(pid) is False


def test_pid_absent_est_arbitre_sans_tuer(tuer_interdit: list[tuple[int, int]]) -> None:
    """Hors Windows réel, la sonde noyau n'existe pas : on ne tue pas pour savoir.

    Ce test tourne sur Linux en SIMULANT ``os.name = "nt"`` : ``kernel32`` n'y
    est pas disponible, la branche Windows retombe donc sur sa valeur de
    prudence (« vivant »). Ce qui est vérifié ici n'est pas la VALEUR — elle
    dépend de la machine — mais l'ABSENCE D'APPEL À ``os.kill`` : sur un vrai
    Windows, tuer pour savoir détruirait l'héritier éventuel du PID recyclé.
    """
    resultat = plateforme.pid_vivant(999_999_999)
    assert isinstance(resultat, bool)
    assert tuer_interdit == [], "os.kill a été appelé pour arbitrer un PID absent"
    # Sur une machine POSIX qui simule Windows, la prudence l'emporte : « vivant ».
    if os.name == "posix":
        assert resultat is True, "sans sonde noyau, la branche Windows doit rester prudente"


def test_le_verrou_de_scan_ne_tue_pas_un_processus_vivant(tmp_path: Path) -> None:
    """Le verrou de scan lit un PID dans un fichier : il doit sonder, pas tuer.

    Scénario : un premier scan a laissé un fichier de verrou avec le PID d'un
    processus encore vivant (ici, le nôtre). Le second scan doit refuser
    proprement (« un scan est déjà en cours ») — et laisser le processus en
    vie.
    """
    from seamtech_search.indexer import ScanAlreadyRunningError, SearchIndex

    base = tmp_path / "index.db"
    index = SearchIndex(base)
    index.initialize(rebuild=True)
    verrou = base.with_suffix(base.suffix + ".scan.lock")
    verrou.write_text(str(os.getpid()), encoding="ascii")

    with pytest.raises(ScanAlreadyRunningError):
        with index.scan_lock():
            pass

    # Le processus de test est toujours vivant : le verrou n'a rien tué.
    assert plateforme.pid_vivant(os.getpid()) is True
    index.close()


def test_le_verrou_de_scan_reprend_un_pid_perime(tmp_path: Path) -> None:
    """Même scénario avec un PID mort : le verrou périmé est repris, pas tué."""
    from seamtech_search.indexer import SearchIndex

    base = tmp_path / "index.db"
    index = SearchIndex(base)
    index.initialize(rebuild=True)
    verrou = base.with_suffix(base.suffix + ".scan.lock")
    verrou.write_text(str(pid_mort()), encoding="ascii")

    with index.scan_lock():
        pass  # acquis : le verrou périmé a été repris

    assert not verrou.exists()
    index.close()
