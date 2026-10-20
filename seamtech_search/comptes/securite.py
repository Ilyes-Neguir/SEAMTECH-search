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


def _canonique(valeur: object) -> str:
    """Forme stable d'une valeur de colonne, pour une empreinte reproductible.

    ``None`` et absence doivent produire la MÊME chaîne sur tous les process :
    l'empreinte est recalculée à chaque requête, sur une autre réplique
    éventuellement. Les horodatages PostgreSQL (``datetime``) passent par
    ``isoformat()`` ; tout le reste par ``str()``.
    """
    if valeur is None:
        return "-"
    isoformat = getattr(valeur, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(valeur)


def etat_identifiants(
    empreinte_mot_de_passe: str | None,
    mot_de_passe_modifie_le: object = None,
    sessions_invalidees_le: object = None,
    actif: bool = True,
) -> str:
    """Empreinte de l'ÉTAT D'AUTHENTIFICATION d'un compte — sa « génération ».

    C'est l'autorité durable qui remplace la comparaison d'horodatages de la
    revue précédente. Elle est calculée à partir de colonnes DÉJÀ présentes
    (``empreinte_mot_de_passe``, ``mot_de_passe_modifie_le``,
    ``sessions_invalidees_le``, ``actif``), donc SANS nouvelle migration.

    Pourquoi une empreinte et pas un horodatage comparé à ``issued_at`` :

    * **Course connexion / réinitialisation.** Un login peut vérifier l'ANCIEN
      mot de passe, puis créer sa session APRÈS qu'un changement a été validé :
      son ``issued_at`` est postérieur et une comparaison d'instants l'accepte.
      Ici, la session porte l'état lu AU MOMENT DE LA VÉRIFICATION ; si le mot
      de passe a changé entre-temps, l'état courant diffère et la session est
      refusée, quel que soit son instant de création.
    * **Répliques et horloges.** Deux machines n'ont pas forcément la même
      horloge. Une ÉGALITÉ de valeurs ne suppose aucune synchronisation ; une
      comparaison d'instants, si.
    * **Réactivation.** ``actif`` entre dans l'empreinte : désactiver puis
      réactiver un compte change l'état, donc les sessions d'avant restent
      mortes.

    La valeur n'est jamais renvoyée par l'API ni journalisée : c'est un
    identifiant interne de génération, dérivé d'un secret (l'empreinte scrypt).
    """
    base = "\x00".join(
        [
            "seamtech:etat-identifiants:v1",
            _canonique(empreinte_mot_de_passe),
            _canonique(mot_de_passe_modifie_le),
            _canonique(sessions_invalidees_le),
            "1" if actif else "0",
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def empreinte_jeton(jeton: str, *, etat_identifiants: str | None = None) -> str:
    """Empreinte SHA-256 d'un jeton de session, en hexadécimal.

    On ne stocke JAMAIS le jeton de session en clair : ``session_ui`` ne garde
    que son empreinte. Un vol du dump de la base ne permet donc pas de rejouer
    une session — exactement comme pour un mot de passe, mais avec SHA-256 seul :
    le jeton est déjà 256 bits d'aléa, il n'a rien à protéger contre une attaque
    par dictionnaire.

    ``etat_identifiants`` LIE le jeton à la génération d'identifiants sous
    laquelle il a été émis. La session historique devient alors invalide dès que
    le mot de passe change, même si sa ligne ``session_ui`` n'a pas été révoquée
    (panne au milieu d'une mutation, ligne oubliée par un ancien code) : la
    revalidation recalcule l'empreinte avec l'état COURANT et ne la reconnaît
    plus. Sans ce paramètre, l'empreinte reste celle du jeton seul (compatibilité
    des appelants hors session) — les sessions ainsi stockées sont refusées dès
    que le validateur lie l'état, ce qui est le sens sûr.
    """
    matiere = jeton if etat_identifiants is None else f"{jeton}\x00{etat_identifiants}"
    return hashlib.sha256(matiere.encode("utf-8")).hexdigest()


def nouveau_jeton() -> str:
    """Jeton de session : 32 octets de CSPRNG, encodés en base64url."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
