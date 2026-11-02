"""Un flux lu par blocs doit s'arrêter — même quand ``read()`` ne s'arrête pas.

Pourquoi ce fichier existe.

Le produit lisait ses flux avec ``iter(lambda: flux.read(TAILLE), b"")``. Cet
idiome est correct pour un fichier réel : la lecture finit toujours par rendre
``b""``. Il est faux pour un flux qu'on ne maîtrise pas — un corps S3, un
mandataire, une doublure : si ``read()`` rend ``None``, une chaîne, un objet,
ou n'importe quoi d'autre que ``b""``, la boucle **ne s'arrête jamais**.

Mesuré le 2026-11-02, qualification Windows : la route
``GET /imports/{id}/artifacts/{artifact}`` entrait dans ce cas et ne rendait
jamais la main — l'hôte de test a été tué par épuisement de mémoire
(rc=137), sans message, sans échec de test, sans fin. Ce n'était ni Windows ni
POSIX : c'était la confiance accordée à ``read()``.

Défaut reproduit AVANT correctif par le premier test ci-dessous : sans la
borne, il ne s'arrête pas (le faux corps lève ``AssertionError`` au bout de
quatre lectures, ce qui fait échouer le test AU LIEU de bloquer la suite —
un test qui démontre un blocage ne doit jamais bloquer).

Ce que ces tests verrouillent :

1. un bloc qui n'est pas des octets est une erreur NOMMÉE, pas une itération
   de plus ;
2. une borne déclarée est appliquée : dépasser la taille annoncée est une
   erreur, pas une attente ;
3. ``None`` et ``b""`` terminent la lecture (sortie normale) ;
4. le comportement nominal — servir un objet — est inchangé : les octets
   servis sont exactement ceux du stockage ;
5. les paramètres invalides (taille de bloc nulle ou négative, borne
   négative) sont refusés, parce qu'ils réintroduisent la boucle.

Aucune donnée client, aucun réseau, aucun secret : les corps sont fabriqués ici.
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest

from seamtech_search import api
from seamtech_search.flux_bornes import lire_par_blocs

BLOC = 1024 * 1024


# ---------------------------------------------------------------------------
# Doubles : des flux qui NE se comportent PAS comme un fichier
# ---------------------------------------------------------------------------


class _CorpsSansFin:
    """``read()`` rend toujours quelque chose, jamais ``b''`` — le cas mesuré.

    Le compteur de lectures est le cœur du test : une boucle sans fin se lit
    dans ce compteur, et lève au bout de ``lectures_max`` lectures pour que le
    test ÉCHOUE au lieu de bloquer.
    """

    def __init__(self, bloc: Any, lectures_max: int = 4) -> None:
        self.bloc = bloc
        self.lectures = 0
        self.lectures_max = lectures_max
        self.ferme = False

    def read(self, taille: int) -> Any:
        self.lectures += 1
        if self.lectures > self.lectures_max:
            raise AssertionError(
                f"la lecture ne s'arrête pas : {self.lectures} appels à read() sans fin de flux"
            )
        return self.bloc

    def close(self) -> None:
        self.ferme = True


class _Stockage:
    """Le minimum appelé par ``_reponse_objet_s3`` : ``head_object``, ``get_object``."""

    def __init__(self, entete: dict[str, Any], corps: Any) -> None:
        self.entete = entete
        self.corps = corps

    def head_object(self, object_key: str) -> dict[str, Any]:
        return self.entete

    def get_object(self, object_key: str) -> dict[str, Any]:
        return {"Body": self.corps}


def _consommer(reponse: object) -> bytes:
    """Lit tout le corps d'une réponse en flux (Starlette itère en tâche de fond)."""

    async def _boucle() -> bytes:
        sortie = b""
        async for bloc in reponse.body_iterator:  # type: ignore[attr-defined]
            sortie += bloc
        return sortie

    return asyncio.run(_boucle())


# ---------------------------------------------------------------------------
# 1-3. Le défaut : un flux qui ne s'arrête pas
# ---------------------------------------------------------------------------


def test_flux_sans_fin_est_arrete_par_la_taille_annoncee() -> None:
    """Taille annoncée 10 octets, corps de 1 Mo qui n'en finit pas → borne."""
    corps = _CorpsSansFin(b"X" * BLOC)
    reponse = api._reponse_objet_s3(
        "cle", "piece.pdf", "application/pdf", _Stockage({"ContentLength": 10}, corps),
        limite_defaut=64 * BLOC,
    )

    with pytest.raises(ValueError, match="borne"):
        _consommer(reponse)

    assert corps.lectures == 1, "une seule lecture suffit à dépasser la taille annoncée"


def test_flux_sans_fin_est_arrete_par_la_borne_de_l_application() -> None:
    """Sans taille annoncée, la borne de l'application joue le même rôle."""
    corps = _CorpsSansFin(b"X" * BLOC)
    reponse = api._reponse_objet_s3(
        "cle", "piece.pdf", "application/pdf", _Stockage({}, corps), limite_defaut=BLOC // 2,
    )

    with pytest.raises(ValueError, match="borne"):
        _consommer(reponse)

    assert corps.lectures == 1, "le premier bloc dépasse déjà la borne de l'application"


def test_flux_qui_rend_autre_que_des_octets_est_refuse() -> None:
    """Un corps non bytes n'est jamais « une itération de plus » : c'est une erreur."""
    corps = _CorpsSansFin(object())
    reponse = api._reponse_objet_s3(
        "cle", "piece.pdf", "application/pdf", _Stockage({"ContentLength": None}, corps),
        limite_defaut=64 * BLOC,
    )

    with pytest.raises(ValueError, match="bloc invalide"):
        _consommer(reponse)

    assert corps.lectures == 1


def test_flux_qui_rend_une_chaine_est_refuse() -> None:
    """Cas vicieux : une chaîne est « vraie » et non vide, donc piégeuse."""
    corps = _CorpsSansFin("pas des octets")
    reponse = api._reponse_objet_s3(
        "cle", "piece.pdf", "application/pdf", _Stockage({}, corps), limite_defaut=64 * BLOC,
    )

    with pytest.raises(ValueError, match="bloc invalide"):
        _consommer(reponse)


# ---------------------------------------------------------------------------
# 4. Le nominal ne change pas
# ---------------------------------------------------------------------------


def test_objet_servi_par_proxy_reste_identique_au_stockage() -> None:
    """Le correctif ne doit rien changer au cas nominal : les octets sont servis."""
    corps = io.BytesIO(b"abcdef")
    reponse = api._reponse_objet_s3(
        "cle", "piece.pdf", "application/pdf", _Stockage({"ContentLength": 6}, corps),
        limite_defaut=64 * BLOC,
    )

    assert _consommer(reponse) == b"abcdef"
    assert reponse.headers["content-length"] == "6"


def test_objet_vide_est_servi_sans_erreur() -> None:
    """Un objet de zéro octet se sert : sortie immédiate, corps vide."""
    corps = io.BytesIO(b"")
    reponse = api._reponse_objet_s3(
        "cle", "vide.pdf", "application/pdf", _Stockage({"ContentLength": 0}, corps),
        limite_defaut=64 * BLOC,
    )

    assert _consommer(reponse) == b""


# ---------------------------------------------------------------------------
# 5. L'aide elle-même : sorties normales et paramètres refusés
# ---------------------------------------------------------------------------


def test_lire_par_blocs_rend_le_contenu_d_un_fichier(tmp_path: Path) -> None:
    """Sur un vrai fichier, le comportement est celui d'avant : blocs puis fin."""
    fichier = tmp_path / "piece.bin"
    fichier.write_bytes(b"a" * (2 * 64) + b"b" * 10)

    with fichier.open("rb") as flux:
        blocs = list(lire_par_blocs(flux, 64, nom="piece"))

    assert b"".join(blocs) == fichier.read_bytes()
    assert [len(bloc) for bloc in blocs] == [64, 64, 10]


def test_lire_par_blocs_s_arrete_sur_none() -> None:
    """``None`` (flux non bloquant à sec) termine la lecture comme ``b''``."""

    class _Flux:
        def __init__(self) -> None:
            self.rendus = [b"abc", None]

        def read(self, taille: int) -> Any:
            return self.rendus.pop(0) if self.rendus else None

    assert list(lire_par_blocs(_Flux(), nom="flux")) == [b"abc"]


def test_lire_par_blocs_refuse_une_taille_de_bloc_invalide() -> None:
    """Une taille de bloc nulle ou négative réintroduirait la boucle : refusée."""
    flux = io.BytesIO(b"abc")
    for taille in (0, -1, "8"):  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="taille de bloc invalide"):
            list(lire_par_blocs(flux, taille, nom="flux"))


def test_lire_par_blocs_refuse_une_borne_invalide() -> None:
    flux = io.BytesIO(b"abc")
    for borne in (-1, "10"):  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="borne invalide"):
            list(lire_par_blocs(flux, 8, limite_octets=borne, nom="flux"))


def test_lire_par_blocs_depasse_la_borne_est_une_erreur() -> None:
    flux = io.BytesIO(b"a" * 100)
    with pytest.raises(ValueError, match="plus volumineux"):
        list(lire_par_blocs(flux, 8, limite_octets=10, nom="flux"))
