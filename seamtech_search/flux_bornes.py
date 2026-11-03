"""Lecture par blocs d'un flux, avec une borne.

Pourquoi ce module existe.

L'idiome ``iter(lambda: flux.read(TAILLE), b"")`` était employé à dix endroits
du produit. Il est correct pour un fichier réel : ``read()`` finit toujours par
rendre ``b""``. Il ne l'est plus dès que l'objet lu n'est PAS un fichier :

* un corps distant (objet S3, mandataire, doublure de test) dont ``read()``
  rend ``None``, une chaîne, un objet, ou n'importe quoi d'autre que
  ``b""`` → la boucle **ne s'arrête jamais** ;
* un corps distant qui rend des octets valides sans jamais s'épuiser →
  la boucle **ne s'arrête jamais non plus**.

Mesuré le 2026-11-02 pendant la qualification Windows (simulation d'une
plateforme sans primitives POSIX, ci/plugin_simulation_sans_primitives.py) :
la route ``GET /imports/{id}/artifacts/{artifact}`` tombait dans le premier cas
et ne rendait jamais la main — l'hôte de test a été tué par épuisement de
mémoire (rc=137), sans message. La cause n'était ni Windows, ni POSIX : c'est
la confiance accordée à ``read()``.

Ce que ce module garantit, et ce qu'il ne garantit pas :

* **garanti** : un bloc qui n'est pas des octets est une erreur explicite
  (``ValueError``), jamais une itération de plus ; une borne déclarée est
  appliquée, et la dépasser est une erreur explicite ;
* **non garanti** : sans borne déclarée et face à un flux d'octets infini, la
  boucle reste infinie. Un fichier réel s'arrête ; c'est le cas de tous les
  appelants internes. Les appelants qui lisent un flux QU'ILS NE CONTRÔLENT
  PAS (corps distant) doivent passer ``limite_octets`` — c'est obligatoire
  pour ``api._reponse_objet_s3``, et un test le verrouille.
"""
from __future__ import annotations

from typing import Any, Iterator

TAILLE_BLOC_DEFAUT = 1024 * 1024

_TYPES_OCTETS = (bytes, bytearray, memoryview)


def lire_par_blocs(
    flux: Any,
    taille_bloc: int = TAILLE_BLOC_DEFAUT,
    *,
    limite_octets: int | None = None,
    nom: str = "flux",
) -> Iterator[bytes]:
    """Itère sur les blocs d'un flux en s'arrêtant net sur un flux aberrant.

    ``flux``
        tout objet exposant ``read(n)``.
    ``taille_bloc``
        taille demandée à chaque appel. Refusée si elle n'est pas strictement
        positive : une taille nulle ou négative ferait rendre ``b""`` ou
        ``None`` indéfiniment, c'est-à-dire exactement la boucle que ce module
        supprime.
    ``limite_octets``
        nombre total d'octets au-delà duquel on lève ``ValueError``. ``None``
        (défaut) signifie « pas de borne » : réservé aux flux que l'appelant
        maîtrise, comme un fichier local qu'il vient d'ouvrir.
    ``nom``
        mot qui nomme le flux dans le message d'erreur, pour que celui qui lit
        le journal sache DE QUEL flux il s'agit.

    Lève ``ValueError`` si un bloc n'est pas des octets, si la borne est
    dépassée, ou si ``taille_bloc`` est invalide.
    """
    if not isinstance(taille_bloc, int) or isinstance(taille_bloc, bool) or taille_bloc <= 0:
        raise ValueError(f"{nom} : taille de bloc invalide ({taille_bloc!r})")
    if limite_octets is not None:
        if not isinstance(limite_octets, int) or isinstance(limite_octets, bool) or limite_octets < 0:
            raise ValueError(f"{nom} : borne invalide ({limite_octets!r})")

    total = 0
    while True:
        bloc = flux.read(taille_bloc)
        if not bloc:
            # ``b""`` (fin de fichier) comme ``None`` (flux non bloquant à sec)
            # terminent la lecture ; c'est la seule sortie normale.
            return
        if not isinstance(bloc, _TYPES_OCTETS):
            raise ValueError(f"{nom} : bloc invalide ({type(bloc).__name__} au lieu d'octets)")
        total += len(bloc)
        if limite_octets is not None and total > limite_octets:
            raise ValueError(f"{nom} : flux plus volumineux que la borne de {limite_octets} octets")
        yield bloc
