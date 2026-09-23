"""Empreintes de mot de passe — Lot L.2.

AUCUNE dépendance nouvelle : tout vient de la bibliothèque standard
(``hashlib.scrypt``, ``secrets``, ``hmac``, ``base64``).

Format stocké, imposé par la mission ::

    scrypt$n=16384$r=8$p=1$<sel_b64>$<hash_b64>

- ``n=16384`` (2**14), ``r=8``, ``p=1`` : coût mémoire/temps choisi pour des
  postes modestes (2-5 utilisateurs, mono-cœur) — un scrypt plus coûteux
  allongerait la connexion sans bénéfice proportionnel ici, et le mot de passe
  protège une application d'atelier derrière un réseau local, pas un service
  exposé à l'Internet.
- sel de 16 octets tiré par ``secrets.token_bytes`` (CSPRNG), jamais réutilisé.
- ``hash_b64`` = ``scrypt(mot_de_passe, sel, n, r, p, dklen=32)``.
- ``sel`` et ``hash`` sont encodés en base64 STANDARD (avec ``=``), pas en
  base64url : le séparateur du format est ``$``, les deux conviennent, mais
  l'encodage standard est celui que la mission a spécifié.

Vérification par ``hmac.compare_digest`` (temps constant). Toute anomalie de
format renvoie ``False`` — jamais d'exception : une empreinte illisible ne doit
pas produire une erreur 500 sur une route de connexion, encore moins un accès.

Le mot de passe en clair n'est JAMAIS journalisé, jamais renvoyé, jamais
comparé en direct.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# Paramètres scrypt. Les valeurs sont écrites DANS l'empreinte : une montée de
# coût ultérieure n'invalide pas les empreintes existantes.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SEL_OCTETS = 16

PREFIXE = "scrypt"
FORMAT_ATTENDU = "scrypt$n=16384$r=8$p=1$<sel_b64>$<hash_b64>"


def _b64(octets: bytes) -> str:
    return base64.b64encode(octets).decode("ascii")


def _de_b64(texte: str) -> bytes:
    return base64.b64decode(texte.encode("ascii"), validate=True)


def empreinte_mot_de_passe(mot_de_passe: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    """Empreinte scrypt d'un mot de passe, au format ``scrypt$n=…$r=…$p=…$sel$hash``."""
    if mot_de_passe is None:
        raise ValueError("Mot de passe manquant.")
    sel = secrets.token_bytes(SEL_OCTETS)
    derive = hashlib.scrypt(
        mot_de_passe.encode("utf-8"), salt=sel, n=n, r=r, p=p, dklen=SCRYPT_DKLEN
    )
    return f"{PREFIXE}$n={n}$r={r}$p={p}${_b64(sel)}${_b64(derive)}"


def _analyser(empreinte: str) -> tuple[int, int, int, bytes, bytes] | None:
    """Décompose une empreinte ; ``None`` si elle n'est pas au format attendu."""
    if not isinstance(empreinte, str):
        return None
    morceaux = empreinte.split("$")
    if len(morceaux) != 6 or morceaux[0] != PREFIXE:
        return None
    try:
        parametres = {}
        for bloc in morceaux[1:4]:
            cle, _, valeur = bloc.partition("=")
            parametres[cle] = int(valeur)
        n, r, p = parametres["n"], parametres["r"], parametres["p"]
    except (KeyError, ValueError):
        return None
    # Bornes de sûreté : une empreinte forgée avec n=2**30 ferait consommer toute
    # la mémoire de la machine à la vérification (déni de service trivial).
    if not (1024 <= n <= 2**20) or not (1 <= r <= 32) or not (1 <= p <= 16):
        return None
    try:
        sel = _de_b64(morceaux[4])
        attendu = _de_b64(morceaux[5])
    except Exception:  # noqa: BLE001 - base64 invalide = empreinte invalide
        return None
    if len(sel) < 8 or len(attendu) < 16:
        return None
    return n, r, p, sel, attendu


def verifier_mot_de_passe(mot_de_passe: str | None, empreinte: str | None) -> bool:
    """Vérifie un mot de passe contre une empreinte, en temps constant.

    Rend ``False`` pour toute empreinte absente, vide, malformée ou de
    paramètres hors bornes — sans jamais lever, et sans jamais laisser fuiter
    par le temps de réponse si c'est le mot de passe ou l'empreinte qui est en
    cause (``compare_digest``).
    """
    if not mot_de_passe:
        return False
    analyse = _analyser(empreinte or "")
    if analyse is None:
        return False
    n, r, p, sel, attendu = analyse
    try:
        derive = hashlib.scrypt(
            mot_de_passe.encode("utf-8"), salt=sel, n=n, r=r, p=p, dklen=len(attendu)
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(derive, attendu)


def empreinte_jeton(jeton: str) -> str:
    """Empreinte SHA-256 d'un jeton de session, en hexadécimal.

    On ne stocke JAMAIS le jeton de session en clair : ``session_ui`` ne garde
    que son empreinte. Un vol du dump de la base ne permet donc pas de rejouer
    une session — exactement comme pour un mot de passe, mais avec SHA-256 seul :
    le jeton est déjà 256 bits d'aléa, il n'a rien à protéger contre une attaque
    par dictionnaire.
    """
    return hashlib.sha256(jeton.encode("utf-8")).hexdigest()


def nouveau_jeton() -> str:
    """Jeton de session : 32 octets de CSPRNG, encodés en base64url."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
