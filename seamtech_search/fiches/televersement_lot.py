"""Téléversement navigateur d'un grand dossier, par envois successifs.

Le navigateur ne peut pas envoyer une année d'archive en une seule requête
(et les objets ``File`` ne survivent pas à un rechargement de page) : le
transfert est donc une SESSION côté serveur — jeton opaque, manifeste JSON
atomique, fichiers promus un à un depuis un dossier temporaire de session.

Contrat (testé) :

- les envois sont bornés (nombre et octets) mais le NOMBRE TOTAL de fichiers
  ne connaît pas l'ancien plafond de 500 fichiers d'une requête unique ;
- renvoyer un envoi déjà reçu est idempotent quand les octets sont les
  mêmes (empreinte SHA-256 comparée) et un conflit explicite sinon ;
- aucun chemin relatif ne peut sortir du dossier de session, être caché ou
  entrer en collision (casse comprise, fichier contre dossier compris) ;
- la finalisation exige le compte ET la taille annoncés à la création ;
- le dossier finalisé devient la racine de travail du lot d'ingestion. Il
  vit sous ``uploads/lots-televerses`` et n'est jamais élagué par âge : les
  fiches déposées référencent ces fichiers, comme pour un dépôt par chemin.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import unicodedata
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator, Sequence

try:
    import fcntl
except ImportError:  # Windows : repli interprocessus SQLite
    fcntl = None  # type: ignore[assignment]

from seamtech_search.import_pipeline import staging_root

from ..flux_bornes import lire_par_blocs

LOGGER = logging.getLogger("seamtech_search.fiches.televersement_lot")

DOSSIER_LOTS_TELEVERSES = "lots-televerses"
FICHIER_MANIFESTE = "session.json"
FICHIER_VERROU = "session.lock"
DOSSIER_FICHIERS = "fichiers"
DOSSIER_TEMPORAIRE = ".tmp"
MAX_PROFONDEUR_CHEMIN = 32

# Pas de purge automatique des clés : une ancienne clé ne doit jamais
# ressusciter une session annulée. Le plafond borne le registre et refuse
# explicitement les nouvelles créations lorsqu'il est atteint.
MAX_RESERVATIONS_CREATION = 1_000_000
MAX_FICHIERS_PAR_SESSION = 100_000
MAX_TAILLE_TOTALE_OCTETS = 20 * 1024 * 1024 * 1024
MAX_FICHIERS_PAR_ENVOI = 200
MAX_TAILLE_ENVOI_OCTETS = 512 * 1024 * 1024
FICHIERS_PAR_ENVOI_RECOMMANDES = 100
TAILLE_ENVOI_RECOMMANDEE_OCTETS = 32 * 1024 * 1024
TAILLE_BLOC_LECTURE = 1024 * 1024

_RE_JETON = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_RE_LECTEUR_WINDOWS = re.compile(r"^[A-Za-z]:")
_NOMS_RESERVES_WINDOWS = {
    "con", "prn", "aux", "nul",
    *(f"com{numero}" for numero in range(1, 10)),
    *(f"lpt{numero}" for numero in range(1, 10)),
}


class TeleversementLotErreur(ValueError):
    """Demande de téléversement invalide (chemin, bornes, manifeste…)."""


class TeleversementLotInterrompu(TeleversementLotErreur):
    """Promotion interrompue par le contrôle de continuation."""


class TeleversementLotIntrouvable(TeleversementLotErreur):
    """Session inconnue — même réponse pour un jeton mal formé."""


class TeleversementLotConflit(TeleversementLotErreur):
    """État ou contenu incompatible avec la demande (409 côté HTTP)."""


class TeleversementLotTropGrand(TeleversementLotErreur):
    """Une borne de taille ou de nombre est dépassée (413 côté HTTP)."""


class TeleversementLotEspaceInsuffisant(TeleversementLotErreur):
    """Le disque ne peut pas recevoir le transfert annoncé (507 côté HTTP)."""


def _maintenant_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _racine_lots(config: Any) -> Path:
    return staging_root(config) / DOSSIER_LOTS_TELEVERSES


def _limites(config: Any) -> dict[str, int]:
    taille_fichier = int(getattr(config, "max_file_size_bytes", 512 * 1024 * 1024) or 512 * 1024 * 1024)
    return {
        "fichiers_par_session_max": MAX_FICHIERS_PAR_SESSION,
        "taille_totale_max_octets": MAX_TAILLE_TOTALE_OCTETS,
        "taille_fichier_max_octets": taille_fichier,
        "fichiers_par_envoi_max": MAX_FICHIERS_PAR_ENVOI,
        "taille_envoi_max_octets": MAX_TAILLE_ENVOI_OCTETS,
        "fichiers_par_envoi_recommande": FICHIERS_PAR_ENVOI_RECOMMANDES,
        "taille_envoi_recommandee_octets": TAILLE_ENVOI_RECOMMANDEE_OCTETS,
        "profondeur_chemin_max": MAX_PROFONDEUR_CHEMIN,
    }


def _dossier_session(config: Any, token: str) -> Path:
    if not isinstance(token, str) or not _RE_JETON.fullmatch(token):
        raise TeleversementLotIntrouvable("Session de téléversement inconnue.")
    return _racine_lots(config) / token


@contextmanager
def _verrou_session(config: Any, token: str) -> Iterator[None]:
    """Sérialise toutes les mutations d'une même session, processus compris.

    Le frontend envoie séquentiellement, mais l'API peut être servie par
    plusieurs processus et un même envoi peut être rejoué : sans verrou
    inter-processus, deux manifestes lus en parallèle pouvaient s'écraser.
    """
    dossier = _dossier_session(config, token)
    if not (dossier / FICHIER_MANIFESTE).is_file():
        raise TeleversementLotIntrouvable("Session de téléversement inconnue.")
    # Tous les processus utilisent le même verrou SQLite, y compris ceux qui
    # disposent de fcntl. Sinon deux modes différents ne se verraient pas.
    # Les garanties effectives dépendent du système de fichiers sous-jacent.
    connexion = None
    try:
        connexion = sqlite3.connect(str(dossier / "session.lock.sqlite"), timeout=30, isolation_level=None)
        connexion.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as erreur:
        if connexion is not None:
            connexion.close()
        raise TeleversementLotConflit("Verrou de session indisponible ; réessayez.") from erreur
    try:
        if fcntl is None:
            yield
        else:
            # Conserve aussi le verrou historique pour les processus anciens
            # encore présents lors d'un arrêt/restart coordonné.
            try:
                flux_verrou = (dossier / FICHIER_VERROU).open("a+b")
            except OSError as erreur:
                raise TeleversementLotIntrouvable("Session de téléversement inconnue.") from erreur
            with flux_verrou:
                fcntl.flock(flux_verrou.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(flux_verrou.fileno(), fcntl.LOCK_UN)
    finally:
        connexion.close()  # annule la transaction et libère le verrou, même en cas d'erreur


def _sauver_manifeste(dossier: Path, manifeste: dict[str, Any]) -> None:
    manifeste["mis_a_jour_le"] = _maintenant_iso()
    temporaire = dossier / f".{FICHIER_MANIFESTE}.tmp-{uuid.uuid4().hex}"
    try:
        with temporaire.open("w", encoding="utf-8") as flux:
            json.dump(manifeste, flux, ensure_ascii=False, indent=2, sort_keys=True)
            flux.flush()
            os.fsync(flux.fileno())
        os.replace(temporaire, dossier / FICHIER_MANIFESTE)
        try:
            descripteur_dossier = os.open(dossier, os.O_RDONLY)
            try:
                os.fsync(descripteur_dossier)
            finally:
                os.close(descripteur_dossier)
        except OSError:
            pass  # certains systèmes de fichiers ne savent pas fsync un dossier
    finally:
        temporaire.unlink(missing_ok=True)


def _charger_manifeste(config: Any, token: str) -> tuple[Path, dict[str, Any]]:
    dossier = _dossier_session(config, token)
    chemin = dossier / FICHIER_MANIFESTE
    if not chemin.is_file():
        raise TeleversementLotIntrouvable("Session de téléversement inconnue.")
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erreur:
        raise TeleversementLotErreur("Manifeste de session illisible ; conséquence : transfert refusé.") from erreur
    if not isinstance(donnees, dict) or donnees.get("token") != token or not isinstance(donnees.get("fichiers"), dict):
        raise TeleversementLotErreur("Manifeste de session incohérent ; conséquence : transfert refusé.")
    return dossier, donnees


def _etat_public(config: Any, manifeste: dict[str, Any], **extra: Any) -> dict[str, Any]:
    fichiers: dict[str, Any] = manifeste.get("fichiers") or {}
    recus = len(fichiers)
    taille_recue = sum(int((infos or {}).get("taille_octets") or 0) for infos in fichiers.values())
    attendus = int(manifeste.get("nombre_fichiers_attendus") or 0)
    taille_attendue = int(manifeste.get("taille_totale_attendue_octets") or 0)
    if taille_attendue > 0:
        progression = round(100.0 * taille_recue / taille_attendue, 1)
    elif attendus > 0:
        progression = round(100.0 * recus / attendus, 1)
    else:
        progression = 0.0
    etat = {
        "token": manifeste.get("token"),
        "nom_racine": manifeste.get("nom_racine"),
        "statut": manifeste.get("statut"),
        "nombre_fichiers_attendus": attendus,
        "taille_totale_attendue_octets": taille_attendue,
        "nombre_fichiers_recus": recus,
        "taille_recue_octets": taille_recue,
        "progression_pct": min(100.0, progression),
        "id_lot": manifeste.get("id_lot"),
        "limites": _limites(config),
        "cree_le": manifeste.get("cree_le"),
        "mis_a_jour_le": manifeste.get("mis_a_jour_le"),
    }
    etat.update(extra)
    return etat


def _verifier_espace(config: Any, octets_requis: int) -> None:
    racine = _racine_lots(config)
    controle = racine
    while not controle.exists() and controle.parent != controle:
        controle = controle.parent
    try:
        libre = shutil.disk_usage(controle).free
    except OSError as erreur:
        LOGGER.warning("Espace disque non mesurable sous %s : %s", controle, erreur)
        return
    reserve = int(getattr(config, "min_free_bytes", 0) or 0)
    if libre < reserve + max(0, octets_requis):
        raise TeleversementLotEspaceInsuffisant(
            "Espace disque insuffisant pour ce téléversement : "
            f"{libre} octets libres, {reserve + max(0, octets_requis)} octets requis."
        )


def _nom_racine_sur(nom_racine: Any) -> str:
    brut = str(nom_racine or "").replace("\\", "/").strip()
    parties = [partie for partie in brut.split("/") if partie]
    nom = parties[-1].strip() if parties else ""
    if not nom or nom in {".", ".."} or nom.startswith(".") or len(nom) > 255:
        raise TeleversementLotErreur("Nom du dossier racine invalide.")
    return nom


def creer_session_televersement_lot(
    config: Any,
    *,
    nom_racine: Any,
    nombre_fichiers: Any,
    taille_totale_octets: Any,
    cle_idempotence: Any = None,
) -> dict[str, Any]:
    """Crée une session vide et retourne son jeton opaque et ses bornes."""
    if isinstance(nombre_fichiers, bool) or isinstance(taille_totale_octets, bool):
        raise TeleversementLotErreur("Nombre de fichiers et taille totale doivent être des entiers.")
    try:
        nombre = int(nombre_fichiers)
        taille = int(taille_totale_octets)
    except (TypeError, ValueError) as erreur:
        raise TeleversementLotErreur("Nombre de fichiers et taille totale doivent être des entiers.") from erreur
    if nombre < 1 or taille < 0:
        raise TeleversementLotErreur("Le dossier à téléverser doit contenir au moins un fichier.")
    if nombre > MAX_FICHIERS_PAR_SESSION or taille > MAX_TAILLE_TOTALE_OCTETS:
        raise TeleversementLotTropGrand(
            f"Dossier trop grand pour une session : maximum {MAX_FICHIERS_PAR_SESSION} fichiers "
            f"et {MAX_TAILLE_TOTALE_OCTETS} octets. Scindez par année ou catégorie."
        )
    nom_racine_valide = _nom_racine_sur(nom_racine)
    if cle_idempotence is not None:
        return _creer_session_idempotente(config, nom_racine_valide, nombre, taille, cle_idempotence)
    _verifier_espace(config, taille)

    racine = _racine_lots(config)
    racine.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    dossier = racine / token
    while dossier.exists():  # collision cryptographique quasi impossible, mais jamais d'écrasement
        token = secrets.token_urlsafe(24)
        dossier = racine / token
    (dossier / DOSSIER_FICHIERS).mkdir(parents=True)
    (dossier / DOSSIER_TEMPORAIRE).mkdir()
    maintenant = _maintenant_iso()
    manifeste: dict[str, Any] = {
        "version": 1,
        "token": token,
        "nom_racine": nom_racine_valide,
        "statut": "en_cours",
        "nombre_fichiers_attendus": nombre,
        "taille_totale_attendue_octets": taille,
        "fichiers": {},
        "id_lot": None,
        "cree_le": maintenant,
        "mis_a_jour_le": maintenant,
    }
    _sauver_manifeste(dossier, manifeste)
    LOGGER.info("Session de téléversement créée : %d fichier(s), %d octet(s) annoncés.", nombre, taille)
    return _etat_public(config, manifeste)


def _creer_session_idempotente(
    config: Any, nom: str, nombre: int, taille: int, cle: Any,
) -> dict[str, Any]:
    """Réserve durablement un jeton avant de créer les fichiers de session.

    La clé est un secret à 256 bits produit par le client. Le registre ne
    conserve que son empreinte, pas la clé. Un verrou SQLite sérialise les
    processus sur un système de fichiers local compatible SQLite.
    """
    if not isinstance(cle, str) or not re.fullmatch(r"[0-9a-f]{64}", cle):
        raise TeleversementLotErreur("Clé d'idempotence invalide.")
    racine = _racine_lots(config)
    racine.mkdir(parents=True, exist_ok=True)
    if racine.is_symlink():
        raise TeleversementLotConflit("Racine de téléversement symbolique interdite.")
    base = racine / ".creations.sqlite"
    if base.is_symlink():
        raise TeleversementLotConflit("Registre de création symbolique interdit.")
    # Créer avec des permissions privées avant que SQLite ne l'ouvre.
    try:
        fd = os.open(base, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    if os.name != "nt" and base.stat().st_mode & 0o077:
        raise TeleversementLotConflit("Permissions du registre de création trop ouvertes.")
    empreinte_cle = hashlib.sha256(cle.encode("ascii")).hexdigest()
    empreinte_demande = hashlib.sha256(
        json.dumps([nom, nombre, taille], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    try:
        with closing(sqlite3.connect(str(base), timeout=30, isolation_level=None)) as connexion:
            connexion.execute("PRAGMA synchronous=FULL")
            connexion.execute(
                "CREATE TABLE IF NOT EXISTS creations (cle TEXT PRIMARY KEY, demande TEXT NOT NULL, "
                "token TEXT NOT NULL UNIQUE, cree INTEGER NOT NULL DEFAULT 0)"
            )
            connexion.execute("BEGIN IMMEDIATE")
            ligne = connexion.execute("SELECT demande, token FROM creations WHERE cle = ?", (empreinte_cle,)).fetchone()
            if ligne is None:
                nombre_reservations = connexion.execute("SELECT COUNT(*) FROM creations").fetchone()[0]
                if nombre_reservations >= MAX_RESERVATIONS_CREATION:
                    raise TeleversementLotEspaceInsuffisant(
                        "Registre de création saturé ; aucune nouvelle session ne peut être réservée."
                    )
                token = secrets.token_urlsafe(24)
                while (racine / token).exists() or connexion.execute(
                    "SELECT 1 FROM creations WHERE token = ?", (token,),
                ).fetchone():
                    token = secrets.token_urlsafe(24)
                connexion.execute(
                    "INSERT INTO creations (cle, demande, token) VALUES (?, ?, ?)",
                    (empreinte_cle, empreinte_demande, token),
                )
            elif ligne[0] != empreinte_demande:
                raise TeleversementLotConflit("Clé d'idempotence déjà utilisée pour une autre demande.")
            connexion.execute("COMMIT")  # réservation durable avant toute création de dossier
            try:
                descripteur = os.open(racine, os.O_RDONLY)
                try:
                    os.fsync(descripteur)
                finally:
                    os.close(descripteur)
            except OSError:
                pass  # certaines plateformes ne permettent pas fsync sur un répertoire
            connexion.execute("BEGIN IMMEDIATE")
            _demande, token, cree = connexion.execute(
                "SELECT demande, token, cree FROM creations WHERE cle = ?", (empreinte_cle,),
            ).fetchone()
            dossier = racine / token
            if dossier.is_symlink():
                raise TeleversementLotConflit("Dossier de session symbolique interdit.")
            if (dossier / FICHIER_MANIFESTE).is_file():
                with _verrou_session(config, token):
                    _, manifeste = _charger_manifeste(config, token)
                    if (manifeste.get("nom_racine"), manifeste.get("nombre_fichiers_attendus"),
                        manifeste.get("taille_totale_attendue_octets")) != (nom, nombre, taille):
                        raise TeleversementLotConflit("Session réservée incohérente.")
                    resultat = _etat_public(config, manifeste)
            else:
                if cree:
                    raise TeleversementLotConflit("Cette session réservée a été supprimée et ne peut pas être recréée.")
                _verifier_espace(config, taille)
                dossier.mkdir(exist_ok=True)
                # Après une interruption pendant le premier manifeste, ne
                # récupérer que la structure initiale vide attendue.
                autorises = {DOSSIER_FICHIERS, DOSSIER_TEMPORAIRE}
                for entree in dossier.iterdir():
                    if entree.is_symlink():
                        raise TeleversementLotConflit("Création interrompue : entrée symbolique interdite.")
                    if entree.name in autorises and entree.is_dir() and not any(entree.iterdir()):
                        continue
                    if entree.name.startswith(f".{FICHIER_MANIFESTE}.tmp-") and entree.is_file():
                        entree.unlink()
                        continue
                    raise TeleversementLotConflit("Création interrompue : contenu inattendu, reprise refusée.")
                (dossier / DOSSIER_FICHIERS).mkdir(exist_ok=True)
                (dossier / DOSSIER_TEMPORAIRE).mkdir(exist_ok=True)
                maintenant = _maintenant_iso()
                manifeste = {
                    "version": 1, "token": token, "nom_racine": nom, "statut": "en_cours",
                    "nombre_fichiers_attendus": nombre, "taille_totale_attendue_octets": taille,
                    "fichiers": {}, "id_lot": None, "cree_le": maintenant, "mis_a_jour_le": maintenant,
                }
                _sauver_manifeste(dossier, manifeste)
                resultat = _etat_public(config, manifeste)
                LOGGER.info("Session de téléversement créée : %d fichier(s), %d octet(s) annoncés.", nombre, taille)
            connexion.execute("UPDATE creations SET cree = 1 WHERE cle = ?", (empreinte_cle,))
            connexion.execute("COMMIT")
            return {**resultat, "idempotence_creation": 1}
    except sqlite3.Error as erreur:
        raise TeleversementLotConflit("Registre de création indisponible ; réessayez.") from erreur


def _chemin_relatif_sur(brut: Any) -> tuple[str, Path]:
    texte = unicodedata.normalize("NFC", str(brut or ""))
    if not texte or "\x00" in texte:
        raise TeleversementLotErreur("Chemin relatif vide ou invalide.")
    if _RE_LECTEUR_WINDOWS.match(texte):
        raise TeleversementLotErreur(f"Chemin absolu refusé : {texte}")
    normalise = texte.replace("\\", "/")
    if normalise.startswith("/") or len(normalise) > 1024:
        raise TeleversementLotErreur(f"Chemin relatif invalide : {texte}")
    parties = normalise.split("/")
    if len(parties) - 1 > MAX_PROFONDEUR_CHEMIN:
        raise TeleversementLotErreur(
            f"Profondeur de chemin supérieure à {MAX_PROFONDEUR_CHEMIN} dossiers : {texte}"
        )
    for partie in parties:
        if partie in {"", ".", ".."} or partie.startswith("."):
            raise TeleversementLotErreur(f"Composant de chemin refusé : {texte}")
        if partie != partie.strip() or partie.endswith(".") or len(partie.encode("utf-8")) > 255:
            raise TeleversementLotErreur(f"Nom de fichier invalide : {texte}")
        if any(ord(caractere) < 32 for caractere in partie):
            raise TeleversementLotErreur(f"Nom de fichier invalide : {texte}")
        if partie.split(".", 1)[0].casefold() in _NOMS_RESERVES_WINDOWS:
            raise TeleversementLotErreur(f"Nom réservé refusé : {texte}")
    return "/".join(parties), Path(*parties)


def _empreinte_fichier(chemin: Path) -> str:
    empreinte = hashlib.sha256()
    with chemin.open("rb") as flux:
        for bloc in lire_par_blocs(flux, TAILLE_BLOC_LECTURE, nom=f"empreinte {chemin.name}"):
            empreinte.update(bloc)
    return empreinte.hexdigest()


def _supprimer_temporaires(temporaires: Sequence[Path]) -> None:
    for chemin in temporaires:
        chemin.unlink(missing_ok=True)


def _recevoir_fichiers_televersement_lot_interne(
    config: Any,
    token: str,
    *,
    chemins_relatives: Sequence[Any],
    flux: Sequence[BinaryIO],
) -> dict[str, Any]:
    """Reçoit un envoi borné ; rien n'est promu si l'envoi entier échoue."""
    dossier, manifeste = _charger_manifeste(config, token)
    if manifeste.get("statut") != "en_cours":
        raise TeleversementLotConflit("Cette session n'accepte plus de fichiers (déjà finalisée ou annulée).")
    if len(chemins_relatives) != len(flux):
        raise TeleversementLotErreur("La liste des chemins et celle des fichiers doivent avoir la même longueur.")
    if not chemins_relatives:
        raise TeleversementLotErreur("Envoi vide : aucun fichier reçu.")
    if len(chemins_relatives) > MAX_FICHIERS_PAR_ENVOI:
        raise TeleversementLotTropGrand(
            f"Envoi trop grand : {len(chemins_relatives)} fichiers, maximum {MAX_FICHIERS_PAR_ENVOI} par envoi."
        )

    plan: list[tuple[str, Path]] = [_chemin_relatif_sur(brut) for brut in chemins_relatives]
    cles_envoi: dict[str, str] = {}
    for cle, _relatif in plan:
        insensible = cle.casefold()
        if insensible in cles_envoi:
            raise TeleversementLotConflit(f"Collision dans l'envoi : {cles_envoi[insensible]} et {cle}.")
        cles_envoi[insensible] = cle
    for cle, _relatif in plan:
        parties = cle.casefold().split("/")
        for profondeur in range(1, len(parties)):
            if "/".join(parties[:profondeur]) in cles_envoi:
                raise TeleversementLotConflit(f"Un chemin est à la fois fichier et dossier : {cle}.")

    fichiers_recus: dict[str, Any] = manifeste.get("fichiers") or {}
    existants_insensibles = {str(cle).casefold(): str(cle) for cle in fichiers_recus}
    racine_fichiers = dossier / DOSSIER_FICHIERS
    if racine_fichiers.is_symlink():
        raise TeleversementLotConflit("Racine de fichiers symbolique interdite.")
    for cle, relatif in plan:
        autre = existants_insensibles.get(cle.casefold())
        if autre is not None and autre != cle:
            raise TeleversementLotConflit(f"Collision de casse avec un fichier déjà reçu : {autre} et {cle}.")
        destination = racine_fichiers / relatif
        if destination.is_symlink() or not destination.resolve().is_relative_to(racine_fichiers.resolve()):
            raise TeleversementLotConflit("Destination symbolique ou hors de la session interdite.")
        if destination.is_dir():
            raise TeleversementLotConflit(f"La destination est un dossier : {cle}.")
        parent = racine_fichiers
        for composant in relatif.parts[:-1]:
            parent = parent / composant
            if parent.is_symlink():
                raise TeleversementLotConflit("Parent symbolique interdit pour un téléversement.")
            if parent.exists() and not parent.is_dir():
                raise TeleversementLotConflit(f"Un parent de {cle} est déjà un fichier.")

    taille_deja_recue = sum(int((infos or {}).get("taille_octets") or 0) for infos in fichiers_recus.values())
    nombre_attendu = int(manifeste.get("nombre_fichiers_attendus") or 0)
    taille_attendue_totale = int(manifeste.get("taille_totale_attendue_octets") or 0)
    nouveaux_potentiels = sum(1 for cle, _relatif in plan if cle not in fichiers_recus)
    if len(fichiers_recus) + nouveaux_potentiels > nombre_attendu:
        raise TeleversementLotConflit(
            "Cet envoi dépasserait le nombre de fichiers annoncé à la création de la session."
        )
    reste_annonce = taille_attendue_totale - taille_deja_recue
    _verifier_espace(config, max(0, reste_annonce))

    limites = _limites(config)
    dossier_tmp = dossier / DOSSIER_TEMPORAIRE
    dossier_tmp.mkdir(exist_ok=True)
    temporaires: list[dict[str, Any]] = []
    taille_envoi = 0
    taille_nouvelle_envoi = 0
    temporaire_courant: Path | None = None
    try:
        for (cle, relatif), source in zip(plan, flux):
            try:
                source.seek(0)
            except (AttributeError, OSError):
                pass  # flux non repositionnable : lu tel quel, une seule fois
            temporaire = dossier_tmp / f"{uuid.uuid4().hex}.part"
            temporaire_courant = temporaire
            empreinte = hashlib.sha256()
            taille = 0
            recu_avant = fichiers_recus.get(cle)
            taille_avant = int(recu_avant.get("taille_octets") or 0) if recu_avant is not None else None
            with temporaire.open("wb") as sortie:
                while True:
                    bloc = source.read(TAILLE_BLOC_LECTURE)
                    if not bloc:
                        break
                    if isinstance(bloc, str):
                        raise TeleversementLotErreur("Flux textuel reçu ; des octets sont attendus.")
                    taille += len(bloc)
                    taille_envoi += len(bloc)
                    if taille > limites["taille_fichier_max_octets"]:
                        raise TeleversementLotTropGrand(f"Fichier trop grand : {cle} dépasse la limite configurée.")
                    if taille_envoi > MAX_TAILLE_ENVOI_OCTETS:
                        raise TeleversementLotTropGrand("Envoi trop volumineux ; réduisez la taille des envois.")
                    if taille_avant is not None and taille > taille_avant:
                        raise TeleversementLotConflit(
                            f"Le fichier {cle} a déjà été reçu avec un contenu différent."
                        )
                    if taille_avant is None:
                        taille_nouvelle_envoi += len(bloc)
                        if taille_deja_recue + taille_nouvelle_envoi > taille_attendue_totale:
                            raise TeleversementLotTropGrand(
                                "Cet envoi dépasserait la taille totale annoncée à la création de la session."
                            )
                    empreinte.update(bloc)
                    sortie.write(bloc)
                sortie.flush()
                os.fsync(sortie.fileno())
            temporaires.append(
                {
                    "cle": cle,
                    "relatif": relatif,
                    "temporaire": temporaire,
                    "taille_octets": taille,
                    "empreinte_sha256": empreinte.hexdigest(),
                }
            )
            temporaire_courant = None
    except TeleversementLotErreur:
        _supprimer_temporaires(
            [item["temporaire"] for item in temporaires]
            + ([temporaire_courant] if temporaire_courant is not None else [])
        )
        raise
    except OSError:
        _supprimer_temporaires(
            [item["temporaire"] for item in temporaires]
            + ([temporaire_courant] if temporaire_courant is not None else [])
        )
        raise TeleversementLotErreur("Écriture du téléversement impossible.") from None

    apres = dict(fichiers_recus)
    nouveaux: list[dict[str, Any]] = []
    deja_recus = 0
    try:
        for item in temporaires:
            cle = str(item["cle"])
            recu_avant = apres.get(cle)
            if recu_avant is not None:
                if (
                    int(recu_avant.get("taille_octets") or 0) != item["taille_octets"]
                    or str(recu_avant.get("empreinte_sha256") or "") != item["empreinte_sha256"]
                ):
                    raise TeleversementLotConflit(
                        f"Le fichier {cle} a déjà été reçu avec un contenu différent."
                    )
                deja_recus += 1
                continue
            destination = racine_fichiers / Path(item["relatif"])
            if destination.is_file():
                # Promotion interrompue avant la sauvegarde du manifeste :
                # adopter le fichier seulement si ses octets sont identiques.
                if destination.stat().st_size != item["taille_octets"] or _empreinte_fichier(destination) != item["empreinte_sha256"]:
                    raise TeleversementLotConflit(f"Un fichier différent occupe déjà {cle}.")
                apres[cle] = {
                    "taille_octets": item["taille_octets"],
                    "empreinte_sha256": item["empreinte_sha256"],
                }
                deja_recus += 1
                continue
            apres[cle] = {
                "taille_octets": item["taille_octets"],
                "empreinte_sha256": item["empreinte_sha256"],
            }
            nouveaux.append(item)
    except TeleversementLotErreur:
        _supprimer_temporaires([item["temporaire"] for item in temporaires])
        raise

    attendus = int(manifeste.get("nombre_fichiers_attendus") or 0)
    taille_attendue = int(manifeste.get("taille_totale_attendue_octets") or 0)
    taille_apres = sum(int(infos.get("taille_octets") or 0) for infos in apres.values())
    if len(apres) > attendus or taille_apres > taille_attendue:
        _supprimer_temporaires([item["temporaire"] for item in temporaires])
        raise TeleversementLotConflit(
            "Cet envoi dépasserait le nombre ou la taille annoncés à la création de la session."
        )

    try:
        for item in nouveaux:
            destination = racine_fichiers / Path(item["relatif"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(item["temporaire"], destination)
    except OSError:
        _supprimer_temporaires([item["temporaire"] for item in temporaires])
        raise TeleversementLotErreur(
            "Promotion d'un fichier interrompue ; renvoyez l'envoi — les fichiers déjà "
            "présents seront réconciliés par empreinte."
        ) from None
    _supprimer_temporaires([item["temporaire"] for item in temporaires])

    manifeste["fichiers"] = apres
    _sauver_manifeste(dossier, manifeste)
    return _etat_public(
        config,
        manifeste,
        fichiers_acceptes=len(nouveaux),
        fichiers_deja_recus=deja_recus,
    )


def _reconcilier_manifeste(dossier: Path, manifeste: dict[str, Any]) -> bool:
    """Rattache au manifeste les fichiers promus avant un arrêt brutal."""
    racine_fichiers = dossier / DOSSIER_FICHIERS
    fichiers: dict[str, Any] = dict(manifeste.get("fichiers") or {})
    presents: dict[str, Path] = {}
    if racine_fichiers.is_symlink():
        raise TeleversementLotConflit("Racine de fichiers symbolique interdite.")
    if racine_fichiers.is_dir():
        for chemin in racine_fichiers.rglob("*"):
            if chemin.is_symlink():
                raise TeleversementLotConflit("Lien symbolique interdit dans une session de téléversement.")
            if chemin.is_file():
                presents[chemin.relative_to(racine_fichiers).as_posix()] = chemin
    change = False
    for cle in list(fichiers):
        if cle not in presents:
            if manifeste.get("id_lot") is not None:
                raise TeleversementLotConflit("Un fichier associé au lot est manquant ; intégrité non vérifiée.")
            del fichiers[cle]
            change = True
    for cle, chemin in presents.items():
        taille = chemin.stat().st_size
        recu = fichiers.get(cle)
        if recu is None:
            fichiers[cle] = {"taille_octets": taille, "empreinte_sha256": _empreinte_fichier(chemin)}
            change = True
        elif (int(recu.get("taille_octets") or 0) != taille
              or recu.get("empreinte_sha256") != _empreinte_fichier(chemin)):
            # Ne jamais réécrire l'empreinte de référence avec celle d'un
            # fichier connu qui a été altéré, même de taille inchangée.
            raise TeleversementLotConflit("L'intégrité d'un fichier reçu ne correspond plus au manifeste.")
    if change:
        manifeste["fichiers"] = fichiers
        attendus = int(manifeste.get("nombre_fichiers_attendus") or 0)
        taille_attendue = int(manifeste.get("taille_totale_attendue_octets") or 0)
        taille_recue = sum(int(infos.get("taille_octets") or 0) for infos in fichiers.values())
        if manifeste.get("statut") == "pret" and manifeste.get("id_lot") is None:
            if len(fichiers) != attendus or taille_recue != taille_attendue:
                manifeste["statut"] = "en_cours"
    return change


def etat_session_televersement_lot(
    config: Any,
    token: str,
    *,
    reconcilier: bool = False,
) -> dict[str, Any]:
    if not reconcilier:
        _dossier, manifeste = _charger_manifeste(config, token)
        return _etat_public(config, manifeste)
    with _verrou_session(config, token):
        dossier, manifeste = _charger_manifeste(config, token)
        if _reconcilier_manifeste(dossier, manifeste):
            _sauver_manifeste(dossier, manifeste)
        return _etat_public(config, manifeste)


def _terminer_session_televersement_lot_interne(config: Any, token: str) -> dict[str, Any]:
    """Finalise si compte et taille correspondent exactement à l'annonce."""
    dossier, manifeste = _charger_manifeste(config, token)
    if _reconcilier_manifeste(dossier, manifeste):
        _sauver_manifeste(dossier, manifeste)
    if manifeste.get("statut") == "pret":
        return _etat_public(config, manifeste)
    fichiers: dict[str, Any] = manifeste.get("fichiers") or {}
    attendus = int(manifeste.get("nombre_fichiers_attendus") or 0)
    taille_attendue = int(manifeste.get("taille_totale_attendue_octets") or 0)
    taille_recue = sum(int(infos.get("taille_octets") or 0) for infos in fichiers.values())
    if len(fichiers) != attendus or taille_recue != taille_attendue:
        raise TeleversementLotConflit(
            "Téléversement incomplet : "
            f"{len(fichiers)}/{attendus} fichiers et {taille_recue}/{taille_attendue} octets reçus."
        )
    manifeste["statut"] = "pret"
    _sauver_manifeste(dossier, manifeste)
    LOGGER.info("Session de téléversement finalisée : %d fichier(s).", len(fichiers))
    return _etat_public(config, manifeste)


def _annuler_session_televersement_lot_interne(config: Any, token: str) -> dict[str, Any]:
    """Supprime une session non encore transformée en lot."""
    dossier, manifeste = _charger_manifeste(config, token)
    if manifeste.get("id_lot") is not None:
        raise TeleversementLotConflit("Un lot utilise déjà ces fichiers ; la session ne peut plus être annulée.")
    racine = _racine_lots(config)
    if dossier.parent != racine:
        raise TeleversementLotErreur("Dossier de session incohérent ; suppression refusée.")
    fichiers: dict[str, Any] = manifeste.get("fichiers") or {}
    resultat = {
        "token": token,
        "statut": "annule",
        "fichiers_supprimes": len(fichiers),
        "octets_supprimes": sum(int(infos.get("taille_octets") or 0) for infos in fichiers.values()),
    }
    shutil.rmtree(dossier)
    LOGGER.info("Session de téléversement annulée et supprimée (%d fichier(s)).", len(fichiers))
    return resultat


def _racine_session_televersement_lot_interne(config: Any, token: str) -> Path:
    """Racine serveur d'une session finalisée — jamais exposée au navigateur."""
    dossier, manifeste = _charger_manifeste(config, token)
    if manifeste.get("statut") != "pret":
        raise TeleversementLotConflit("Le téléversement n'est pas finalisé.")
    if _reconcilier_manifeste(dossier, manifeste):
        _sauver_manifeste(dossier, manifeste)
        if manifeste.get("statut") != "pret":
            raise TeleversementLotConflit("Les fichiers téléversés ne sont plus complets.")
    racine_fichiers = dossier / DOSSIER_FICHIERS
    if not racine_fichiers.is_dir():
        raise TeleversementLotConflit("Les fichiers téléversés sont introuvables.")
    return racine_fichiers


def _associer_lot_session_televersement_interne(config: Any, token: str, id_lot: int) -> dict[str, Any]:
    """Lie la session au lot créé, pour rendre un second envoi idempotent."""
    dossier, manifeste = _charger_manifeste(config, token)
    if manifeste.get("statut") != "pret":
        raise TeleversementLotConflit("Le téléversement n'est pas finalisé.")
    existant = manifeste.get("id_lot")
    if existant is not None and int(existant) != int(id_lot):
        raise TeleversementLotConflit("Cette session est déjà associée à un autre lot.")
    if existant is None:
        manifeste["id_lot"] = int(id_lot)
        _sauver_manifeste(dossier, manifeste)
    return _etat_public(config, manifeste)


def recevoir_fichiers_televersement_lot(
    config: Any,
    token: str,
    *,
    chemins_relatives: Sequence[Any],
    flux: Sequence[BinaryIO],
) -> dict[str, Any]:
    with _verrou_session(config, token):
        return _recevoir_fichiers_televersement_lot_interne(
            config,
            token,
            chemins_relatives=chemins_relatives,
            flux=flux,
        )


def terminer_session_televersement_lot(config: Any, token: str) -> dict[str, Any]:
    with _verrou_session(config, token):
        return _terminer_session_televersement_lot_interne(config, token)


def annuler_session_televersement_lot(config: Any, token: str) -> dict[str, Any]:
    with _verrou_session(config, token):
        return _annuler_session_televersement_lot_interne(config, token)


def racine_session_televersement_lot(config: Any, token: str) -> Path:
    with _verrou_session(config, token):
        return _racine_session_televersement_lot_interne(config, token)


def associer_lot_session_televersement(config: Any, token: str, id_lot: int) -> dict[str, Any]:
    with _verrou_session(config, token):
        return _associer_lot_session_televersement_interne(config, token, id_lot)


def _creer_lot_session_televersement_interne(
    config: Any,
    token: str,
    *,
    creer_lot: Callable[[Path], int],
) -> dict[str, Any]:
    dossier, manifeste = _charger_manifeste(config, token)
    racine = dossier / DOSSIER_FICHIERS
    existant = manifeste.get("id_lot")
    if existant is not None:
        racine = _racine_session_televersement_lot_interne(config, token)
        return {
            "session": _etat_public(config, manifeste),
            "racine": racine,
            "id_lot": int(existant),
            "lot_cree": False,
        }

    racine = _racine_session_televersement_lot_interne(config, token)
    id_lot = int(creer_lot(racine))
    if id_lot <= 0:
        raise TeleversementLotErreur("La création du lot n'a pas renvoyé d'identifiant valide.")
    session = _associer_lot_session_televersement_interne(config, token, id_lot)
    return {
        "session": session,
        "racine": racine,
        "id_lot": id_lot,
        "lot_cree": True,
    }


def creer_lot_session_televersement(
    config: Any,
    token: str,
    *,
    creer_lot: Callable[[Path], int],
) -> dict[str, Any]:
    """Crée ou retrouve le lot d'une session, en une seule section critique.

    Le verrou inter-processus de la session est conservé pendant l'appel à
    ``creer_lot`` puis l'association au manifeste. Une annulation concurrente
    attend donc la fin de la création et échoue ensuite explicitement, tandis
    qu'une seconde création retrouve le lot existant sans appeler le callback
    une deuxième fois. Le callback ne doit pas rappeler les fonctions
    publiques de cette même session (elles attendraient le même verrou).
    """
    with _verrou_session(config, token):
        return _creer_lot_session_televersement_interne(config, token, creer_lot=creer_lot)


def _reprendre_journal_promotion(chemin: Path, fichiers: dict[str, Any], bucket: str) -> dict[str, str]:
    """Recharge les écritures complètes d'un journal interrompu sans faire confiance aux octets S3."""
    if chemin.is_symlink():
        raise TeleversementLotConflit("Journal de promotion symbolique interdit.")
    if not chemin.exists():
        return {}
    candidats: dict[str, str] = {}
    try:
        contenu = chemin.read_bytes()
        # Une coupure peut laisser une dernière ligne incomplète. Elle n'est
        # pas une preuve d'envoi et sera rejouée à partir de la source locale.
        limite = contenu.rfind(b"\n") + 1
        if limite != len(contenu):
            with chemin.open("r+b") as flux:
                flux.truncate(limite)
                flux.flush()
                os.fsync(flux.fileno())
        for ligne in contenu[:limite].splitlines():
            entree = json.loads(ligne)
            nom = entree["nom"]
            infos = fichiers[nom]
            cle = entree["object_key"]
            if (not isinstance(cle, str) or not cle or entree["object_bucket"] != bucket
                    or entree["sha256"] != infos.get("empreinte_sha256")
                    or (infos.get("object_key") and infos["object_key"] != cle)
                    or (nom in candidats and candidats[nom] != cle)):
                raise ValueError("entrée incohérente")
            candidats[nom] = cle
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as erreur:
        raise TeleversementLotErreur("Journal de promotion illisible ou incohérent.") from erreur
    return candidats


def promouvoir_session_televersement_objets(
    config: Any, token: str, client_s3: Any, *, doit_continuer: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Copie les fichiers d'une session vers S3 puis relit chaque objet.

    Un journal append-only borné par la taille de la session évite de réécrire
    le manifeste complet après chaque fichier. Les sources restent locales ;
    les clés journalisées sont revérifiées au redémarrage. Ne modifie pas le
    catalogue PostgreSQL : l'intégration au dépôt reste nécessaire.
    """
    if client_s3 is None or not client_s3.is_configured() or not getattr(client_s3, "bucket_name", None):
        raise TeleversementLotErreur("Stockage objet non configuré : copies locales conservées.")
    with _verrou_session(config, token):
        racine = _racine_session_televersement_lot_interne(config, token).resolve()
        dossier, manifeste = _charger_manifeste(config, token)
        journal = dossier / "promotion-objets.jsonl"
        candidats = _reprendre_journal_promotion(journal, manifeste["fichiers"], client_s3.bucket_name)
        recuperees = 0
        verifies = 0
        nouvelles = 0
        objets_promus: dict[str, dict[str, str]] = {}
        identite_session = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            with journal.open("ab") as flux:
                try:
                    for nom, infos in sorted(manifeste["fichiers"].items()):
                        if doit_continuer is not None and not doit_continuer():
                            raise TeleversementLotInterrompu("Promotion interrompue ; fichiers locaux conservés.")
                        _cle, relatif = _chemin_relatif_sur(nom)
                        chemin = (racine / relatif).resolve()
                        if not chemin.is_relative_to(racine) or not chemin.is_file():
                            raise TeleversementLotConflit("Fichier de session absent ou hors de la racine autorisée.")
                        empreinte = infos.get("empreinte_sha256")
                        if _empreinte_fichier(chemin) != empreinte:
                            raise TeleversementLotConflit("L'intégrité d'un fichier reçu ne correspond plus au manifeste.")
                        cle_existante = infos.get("object_key")
                        cle_journal = candidats.get(nom)
                        cle = cle_existante or cle_journal
                        bucket = infos.get("object_bucket") if cle_existante else client_s3.bucket_name
                        if cle and bucket != client_s3.bucket_name:
                            raise TeleversementLotConflit("La copie existante appartient à un autre bucket.")
                        try:
                            if cle:
                                verification = client_s3.verifier_integrite(cle, chemin, relire=True)
                                if not verification.integrite_prouvee:
                                    raise TeleversementLotErreur("L'intégrité de la copie objet existante n'est pas prouvée.")
                            else:
                                identite_chemin = hashlib.sha256(nom.encode("utf-8")).hexdigest()
                                cle_proposee = f"lots-televerses/{identite_session}/{identite_chemin}/{empreinte}"
                                cle = client_s3.upload_file(chemin, cle_proposee, avoid_overwrite=True)
                                if not isinstance(cle, str) or not cle:
                                    raise TeleversementLotErreur("Le stockage objet n'a pas retourné de clé valide.")
                                verification = client_s3.verifier_integrite(cle, chemin, relire=True)
                                if not verification.integrite_prouvee:
                                    raise TeleversementLotErreur("L'intégrité de la copie objet n'est pas prouvée.")
                            if _empreinte_fichier(chemin) != empreinte:
                                raise TeleversementLotConflit("L'intégrité d'un fichier a changé pendant la promotion.")
                        except TeleversementLotErreur:
                            raise
                        except Exception:
                            # Les exceptions du fournisseur peuvent contenir des
                            # secrets, des URL signées ou des chemins clients.
                            raise TeleversementLotErreur("Échec de la promotion objet ; copie locale conservée.") from None
                        if not cle_existante:
                            if cle_journal:
                                recuperees += 1
                            else:
                                entree = {"nom": nom, "object_key": cle, "object_bucket": client_s3.bucket_name, "sha256": empreinte}
                                flux.write((json.dumps(entree, ensure_ascii=False) + "\n").encode("utf-8"))
                                nouvelles += 1
                                if nouvelles % 64 == 0:
                                    flux.flush()
                                    os.fsync(flux.fileno())
                            infos["object_key"] = cle
                            infos["object_bucket"] = client_s3.bucket_name
                        objets_promus[str(chemin)] = {
                            "object_key": cle, "object_bucket": client_s3.bucket_name, "sha256": empreinte,
                        }
                        verifies += 1
                finally:
                    flux.flush()
                    os.fsync(flux.fileno())
        finally:
            # Même après une panne ordinaire, les clés déjà vérifiées sont
            # conservées. Après SIGKILL, le journal sert à les reconstruire.
            if nouvelles or recuperees:
                _sauver_manifeste(dossier, manifeste)
        journal.unlink(missing_ok=True)
        return {"nb_verifies": verifies, "objets_promus": objets_promus}
