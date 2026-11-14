"""Sauvegarde hors-git du corpus mesuré — Lot 21, priorité 0.

Le corpus (``donnees/etape0/``) est le seul exemplaire de fiches mesurées à la
main, document par document. Il est versionné — ce qui a sauvé l'audit du
Lot 20 — mais **git n'est pas une sauvegarde** : un ``reset``, une
réinitialisation de poste ou une erreur de manipulation effacent l'arbre de
travail, et l'historique distant ne protège que ce qui a été poussé.

Ce module verse donc le corpus sur le stockage objet déjà en place pour les
documents (S3/R2), avec un manifeste d'empreintes, et surtout une
**restauration éprouvée** : on ne croit pas une sauvegarde, on la restaure.

La logique vit dans le paquet (et non dans un script) parce que le
planificateur de sauvegarde, dans le conteneur, doit l'importer — un script
``scripts/`` n'est pas dans l'image. ``scripts/sauvegarder_corpus.py`` n'est
que la ligne de commande, pour le geste à la main sur le poste.

**Le client est injecté.** Aucune lecture de configuration ici : c'est
l'appelant qui fournit un client déjà construit (en production,
``sauvegarde._client_s3_depuis_env()``). Sans client, il n'y a pas de
sauvegarde du tout — une copie sur le même disque n'en est pas une, et le
message ne prétend pas le contraire.

SÉCURITÉ — deux points que l'écriture naïve rate et qui sont traités ici :

* ``upload_bytes`` applique le préfixe applicatif et renvoie la CLÉ RÉELLE
  (qui peut différer si ``avoid_overwrite`` a dû la décaler), alors que
  ``download_file`` n'applique AUCUN préfixe. On enregistre donc dans le
  manifeste la clé retournée par l'envoi, jamais la clé demandée ; sans quoi
  toute restauration échoue dès qu'un préfixe est configuré. Le manifeste
  lui-même est relu par la CLÉ TROUVÉE à l'inventaire, jamais recomposée.
* Un manifeste est une donnée distante : un nom de fichier qu'il contient
  n'est JAMAIS utilisé tel quel pour écrire sur le disque (``../..``),
  et un horodatage qui n'a pas la forme attendue est refusé avant de servir
  à composer une clé (injection de clé).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Racine du projet : ``seamtech_search/`` vit juste sous elle.
RACINE_PROJET = Path(__file__).resolve().parents[1]

#: Variable d'environnement qui désigne le corpus (le conteneur ne partage pas
#: la disposition du dépôt : voir le montage ``donnees/etape0`` du service
#: compose ``sauvegarde``).
VARIABLE_DOSSIER_CORPUS = "SEAMTECH_CORPUS_DIR"

#: Ce qui constitue le corpus. Les fichiers absents sont simplement ignorés :
#: le registre des verdicts ou des qualifications n'existe pas avant la
#: première saisie, et ce n'est pas une erreur.
FICHIERS_CORPUS = (
    "accumulateur.json",
    "decisions.json",
    "reparations_axes.json",
    "qualifications.json",
    "verdicts_reutilisation.json",
)

PREFIXE_CORPUS = "corpus"
VERSION_MANIFESTE = 1

#: Forme d'un horodatage : %Y%m%dT%H%M%SZ, strict.
MOTIF_HORODATAGE = re.compile(r"^\d{8}T\d{6}Z$")


def dossier_corpus() -> Path:
    """Le dossier du corpus : ``SEAMTECH_CORPUS_DIR``, sinon ``donnees/etape0``.

    La variable est nécessaire en conteneur, où le corpus est monté à un
    chemin qui n'a rien à voir avec la racine du dépôt.
    """
    declare = os.environ.get(VARIABLE_DOSSIER_CORPUS, "").strip()
    if declare:
        return Path(declare).expanduser()
    return RACINE_PROJET / "donnees" / "etape0"


class SauvegardeCorpusError(RuntimeError):
    """Échec de sauvegarde ou de restauration du corpus."""


# ---------------------------------------------------------------------------
# Empreintes
# ---------------------------------------------------------------------------


def empreinte(chemin: Path) -> str:
    """SHA-256 d'un fichier, lu par blocs."""
    h = hashlib.sha256()
    with chemin.open("rb") as flux:
        for bloc in iter(lambda: flux.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def fichiers_presents(dossier: Path | None = None) -> list[Path]:
    """Les fichiers du corpus qui existent, dans l'ordre déclaré."""
    racine = dossier or dossier_corpus()
    return [racine / nom for nom in FICHIERS_CORPUS if (racine / nom).is_file()]


def _temporaire():
    """Fichier temporaire supprimé en sortie de bloc, même en cas d'échec."""

    @contextlib.contextmanager
    def _cm():
        with tempfile.NamedTemporaryFile(delete=False) as flux:
            chemin = Path(flux.name)
        try:
            yield chemin
        finally:
            chemin.unlink(missing_ok=True)

    return _cm()


# ---------------------------------------------------------------------------
# Sauvegarde
# ---------------------------------------------------------------------------


def _verser(client: Any, cle: str, contenu: bytes) -> str:
    """Envoie des octets et rend la CLÉ RÉELLE (préfixe appliqué côté client).

    Accepte ``upload_bytes`` comme ``upload_file``, et lève si le client ne
    propose ni l'un ni l'autre : mieux vaut un échec franc qu'une sauvegarde
    qu'on croit faite.
    """
    envoi = getattr(client, "upload_bytes", None)
    if callable(envoi):
        cle_reelle = envoi(contenu, cle)
        return str(cle_reelle) if cle_reelle else cle
    envoi = getattr(client, "upload_file", None)
    if callable(envoi):
        with tempfile.NamedTemporaryFile(delete=False) as flux:
            flux.write(contenu)
            chemin = Path(flux.name)
        try:
            cle_reelle = envoi(chemin, cle)
        finally:
            chemin.unlink(missing_ok=True)
        return str(cle_reelle) if cle_reelle else cle
    raise SauvegardeCorpusError("Client de stockage inutilisable : ni upload_bytes ni upload_file.")


def sauvegarder(
    client: Any | None,
    *,
    dossier: Path | None = None,
    maintenant: datetime | None = None,
) -> dict:
    """Verse le corpus sur le stockage distant. Rend le manifeste.

    ``client`` est un client de stockage exposant ``upload_bytes`` (ou
    ``upload_file``). ``None`` signifie « pas de hors-site » : rien n'est
    envoyé et le manifeste le dit — on ne maquille pas une copie locale en
    sauvegarde.
    """
    racine = dossier or dossier_corpus()
    presents = fichiers_presents(racine)
    if not presents:
        raise SauvegardeCorpusError(f"Aucun fichier de corpus dans {racine}.")

    instant = maintenant or datetime.now(timezone.utc)
    horodatage = instant.strftime("%Y%m%dT%H%M%SZ")
    racine = f"{PREFIXE_CORPUS}/{horodatage}"

    fichiers: list[dict[str, Any]] = []
    for chemin in presents:
        contenu = chemin.read_bytes()
        somme = hashlib.sha256(contenu).hexdigest()
        entree: dict[str, Any] = {
            "nom": chemin.name,
            "sha256": somme,
            "taille": len(contenu),
        }
        if client is None:
            entree["cle"] = None
        else:
            entree["cle"] = _verser(client, f"{racine}/{chemin.name}", contenu)
        fichiers.append(entree)

    manifeste = {
        "version": VERSION_MANIFESTE,
        "instant": instant.isoformat(timespec="seconds"),
        "horodatage": horodatage,
        "hors_site": client is not None,
        "nb_fichiers": len(fichiers),
        "fichiers": fichiers,
    }
    if client is not None:
        manifeste["cle_manifeste"] = _verser(
            client,
            f"{racine}/manifest.json",
            json.dumps(manifeste, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    return manifeste


# ---------------------------------------------------------------------------
# Restauration éprouvée
# ---------------------------------------------------------------------------


def inventorier(client: Any) -> list[tuple[str, str]]:
    """Les sauvegardes présentes : (horodatage, clé réelle du manifeste), triées.

    L'instant est le segment situé juste avant ``manifest.json`` : ainsi un
    éventuel préfixe applicatif (``s3_prefix``) ne fausse pas l'analyse, et un
    horodatage qui n'a pas la forme attendue est écarté au lieu de servir à
    composer une clé.

    On retient la CLÉ TROUVÉE, jamais une clé recomposée : c'est le second
    versant du piège du préfixe — recomposer ``corpus/<instant>/manifest.json``
    ne fonctionne que sans préfixe applicatif.
    """
    if not callable(getattr(client, "list_keys", None)):
        raise SauvegardeCorpusError("Client de stockage sans list_keys : inventaire impossible.")
    try:
        cles = client.list_keys(f"{PREFIXE_CORPUS}/")
    except Exception as exc:
        raise SauvegardeCorpusError(f"Inventaire du stockage impossible ({type(exc).__name__}).") from None
    trouves: dict[str, str] = {}
    for cle in cles:
        segments = str(cle).split("/")
        if len(segments) < 3 or segments[-1] != "manifest.json":
            continue
        if segments[-3] != PREFIXE_CORPUS:
            continue
        if MOTIF_HORODATAGE.match(segments[-2]):
            trouves[segments[-2]] = str(cle)
    return sorted(trouves.items())


def instants_disponibles(client: Any) -> list[str]:
    """Les horodatages présents sur le stockage, du plus ancien au plus récent."""
    return [instant for instant, _ in inventorier(client)]


def dernier_instant(client: Any) -> str | None:
    """L'horodatage de la sauvegarde la plus récente, ou None s'il n'y en a aucune."""
    instants = instants_disponibles(client)
    return instants[-1] if instants else None


def _cle_manifeste(client: Any, horodatage: str) -> str:
    """Clé réelle du manifeste d'un instant connu du stockage."""
    for instant, cle in inventorier(client):
        if instant == horodatage:
            return cle
    raise SauvegardeCorpusError(f"Aucune sauvegarde du corpus à l'instant {horodatage}.")


def _telecharger(client: Any, cle: str, destination: Path) -> None:
    """Télécharge ``cle`` vers ``destination``, sans jamais composer de chemin distant."""
    recevoir = getattr(client, "download_file", None)
    if callable(recevoir):
        recevoir(cle, destination)
        return
    lire = getattr(client, "get_object", None)
    if callable(lire):
        objet = lire(remote_key=cle)
        corps = objet.get("Body") if isinstance(objet, dict) else objet
        destination.write_bytes(corps.read())
        return
    raise SauvegardeCorpusError("Client de stockage sans download_file ni get_object.")


def _lire_manifeste(client: Any, horodatage: str) -> dict:
    """Télécharge et valide le manifeste d'un instant donné."""
    if not MOTIF_HORODATAGE.match(horodatage):
        raise SauvegardeCorpusError(f"Horodatage refusé (injection de clé ?) : {horodatage!r}")
    cle = _cle_manifeste(client, horodatage)
    # Le manifeste n'est PAS récupéré sous le nom qu'il annonce : on lit d'abord
    # dans un fichier temporaire, et on ne le croit qu'après validation.
    with _temporaire() as chemin:
        _telecharger(client, cle, chemin)
        try:
            manifeste = json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SauvegardeCorpusError(f"Manifeste illisible ({horodatage}) : {exc}") from None
    if not isinstance(manifeste, dict) or manifeste.get("version") != VERSION_MANIFESTE:
        raise SauvegardeCorpusError(
            f"Version de manifeste inattendue : {manifeste.get('version')!r} (attendu {VERSION_MANIFESTE})."
        )
    if manifeste.get("horodatage") != horodatage:
        raise SauvegardeCorpusError(
            f"Manifeste incohérent : annonce {manifeste.get('horodatage')!r}, demandé {horodatage!r}."
        )
    return manifeste


def restaurer(
    client: Any,
    destination: Path,
    *,
    horodatage: str | None = None,
) -> dict:
    """Restaure le corpus et **vérifie chaque empreinte**.

    C'est le cœur du lot : une sauvegarde qu'on n'a pas restaurée n'est pas une
    sauvegarde, c'est une opinion. Toute divergence d'empreinte lève avant
    qu'un fichier tronqué ne soit pris pour le corpus.
    """
    if client is None:
        raise SauvegardeCorpusError("Aucun stockage distant : restauration impossible.")
    choisi = horodatage or dernier_instant(client)
    if choisi is None:
        raise SauvegardeCorpusError("Aucune sauvegarde du corpus sur le stockage distant.")

    manifeste = _lire_manifeste(client, choisi)
    destination.mkdir(parents=True, exist_ok=True)

    restaures: list[dict[str, Any]] = []
    for entree in manifeste.get("fichiers", []):
        nom = entree.get("nom")
        # DÉFENSE : un nom venant du distant ne commande pas une écriture sur
        # le disque. Seuls les noms attendus du corpus sont acceptés — une
        # valeur comme « ../../.bashrc » est rejetée ici, pas plus loin.
        if nom not in FICHIERS_CORPUS:
            raise SauvegardeCorpusError(f"Nom de fichier refusé par le manifeste : {nom!r}")
        cle = entree.get("cle")
        if not cle:
            raise SauvegardeCorpusError(
                f"Le manifeste n'indique aucune clé distante pour {nom} : "
                "cette sauvegarde n'a jamais été envoyée hors-site, il n'y a rien à restaurer."
            )
        cible = destination / nom
        _telecharger(client, cle, cible)
        somme = empreinte(cible)
        attendu = entree.get("sha256")
        if not attendu or somme != attendu:
            raise SauvegardeCorpusError(
                f"INTÉGRITÉ ROMPUE sur {nom} : attendu {str(attendu)[:16]}…, "
                f"lu {somme[:16]}… — la restauration est abandonnée."
            )
        restaures.append({"nom": nom, "sha256": somme, "taille": cible.stat().st_size})

    if not restaures:
        raise SauvegardeCorpusError(f"Manifeste {choisi} sans aucun fichier : restauration refusée.")

    return {
        "horodatage": choisi,
        "destination": str(destination),
        "nb_fichiers": len(restaures),
        "fichiers": restaures,
        "integrite": "verifiee",
    }


# ---------------------------------------------------------------------------
# Supervision
# ---------------------------------------------------------------------------


def verifier(
    client: Any | None,
    *,
    fraicheur_heures: float = 25.0,
    maintenant: datetime | None = None,
) -> dict:
    """La protection du corpus est-elle prouvée ?

    Rend ``protege`` faux quand il n'y a pas de hors-site, pas de sauvegarde,
    ou une sauvegarde trop ancienne. Même contrat de sortie que
    ``verifier_sauvegarde_quotidienne.py`` : 0 prouvé, 1 non prouvé.
    """
    if client is None:
        return {"protege": False, "motif": "aucun stockage distant configuré"}
    try:
        dernier = dernier_instant(client)
    except SauvegardeCorpusError as exc:
        return {"protege": False, "motif": str(exc)}
    if dernier is None:
        return {"protege": False, "motif": "aucune sauvegarde du corpus sur le stockage distant"}

    # L'horodatage est en UTC compact : %Y%m%dT%H%M%SZ.
    instant = datetime.strptime(dernier, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    age = ((maintenant or datetime.now(timezone.utc)) - instant).total_seconds() / 3600.0
    if age > fraicheur_heures:
        return {
            "protege": False,
            "motif": f"dernière sauvegarde vieille de {age:.1f} h (max {fraicheur_heures:.0f} h)",
            "dernier": dernier,
            "age_heures": round(age, 2),
        }
    return {"protege": True, "dernier": dernier, "age_heures": round(age, 2)}
