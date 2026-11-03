"""Supprimer la copie locale d'un import : uniquement ce qui est PROUVÉ, MAINTENANT.

La question n'est pas « a-t-on déjà envoyé ce dossier ? » mais « les octets qui
sont sur ce disque, à l'instant où je m'apprête à les effacer, existent-ils
ailleurs, vérifiés ? ». Ces deux questions ont longtemps été confondues, et
c'est cette confusion qui a fait suspendre toute purge :

* une preuve d'envoi est un souvenir — elle porte sur un instant passé ;
* un dossier reste mutable — un fichier peut avoir été remplacé APRÈS
  l'inventaire, et le souvenir ne le couvre pas ;
* un dossier peut aussi s'enrichir — un fichier ajouté après l'inventaire
  n'a jamais été envoyé, donc n'a aucune copie durable ;
* une copie distante peut disparaître — le stockage objet n'est pas une
  sauvegarde, et « clé présente » ne veut pas dire « octets intègres ».

Le protocole implémenté ici répond à chacun de ces points, dans cet ordre :

1.  **Appartenance** : rien n'est supprimé hors du staging que l'application
    gère (jamais l'archive du commanditaire, jamais la quarantaine).
2.  **Preuve par fichier, relue à l'instant T** : pour chaque fichier
    candidat, l'objet distant est relu et comparé octet par octet
    (``S3StorageClient.verifier_integrite``). Ce n'est pas « il a été envoyé »,
    c'est « il y est, intact, maintenant ».
3.  **Identité locale revérifiée** : l'empreinte du fichier sur le disque est
    recalculée juste avant la suppression et doit égaler l'empreinte
    inventoriée. Un fichier modifié après inventaire est CONSERVÉ, même s'il a
    été envoyé : la preuve porte sur les anciens octets, pas sur les nouveaux.
4.  **Aucune suppression récursive aveugle** : tout fichier présent dans le
    dossier sans preuve d'envoi est conservé, et signalé. Le dossier n'est
    effacé QUE s'il ne reste rien à conserver.
5.  **Rien n'est tu** : le bilan (supprimés, conservés, modifiés, non prouvés,
    objets illisibles, octets libérés) est renvoyé et consigné dans le payload
    du job. Une purge partielle est un état NORMAL et DIT, jamais un échec
    silencieux ni un succès de façade.

Ce que ce module ne fait PAS : il ne juge pas de l'opportunité (rétention par
âge, quota disque). Il ne supprime que ce qu'il peut prouver, et laisse la
décision de l'appeler à la politique d'exploitation.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

LOGGER = logging.getLogger(__name__)

_TAILLE_BLOC = 1 << 20
_EMPREINTE = re.compile(r"[0-9a-f]{64}")

#: ``verifier_objet(cle, chemin_local) -> ResultatVerification | None``.
#: ``None`` signifie « je ne peux pas vérifier » — jamais « c'est vérifié ».
VerificateurObjet = Callable[[str, Path], Any]


@dataclass(frozen=True)
class BilanPurge:
    """Ce qui a été supprimé, ce qui a été gardé, et pourquoi."""

    purge: bool
    raison: str
    supprimes: tuple[str, ...] = ()
    conserves: tuple[str, ...] = ()
    modifies: tuple[str, ...] = ()
    non_prouves: tuple[str, ...] = ()
    objets_illisibles: tuple[str, ...] = ()
    verification_indisponible: bool = False
    octets_liberes: int = 0
    dossier_supprime: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "purge": self.purge,
            "raison": self.raison,
            "nb_supprimes": len(self.supprimes),
            "nb_conserves": len(self.conserves),
            "supprimes": list(self.supprimes),
            "conserves": [f"{chemin} — {motif}" for chemin, motif in self.conserves],
            "modifies": list(self.modifies),
            "non_prouves": list(self.non_prouves),
            "objets_illisibles": list(self.objets_illisibles),
            "verification_indisponible": self.verification_indisponible,
            "octets_liberes": self.octets_liberes,
            "dossier_supprime": self.dossier_supprime,
        }


def _valeur(entree: Any, nom: str, defaut: Any = None) -> Any:
    """Lit un champ d'une entrée, qu'elle soit un ``dict`` ou un objet."""
    if isinstance(entree, dict):
        return entree.get(nom, defaut)
    return getattr(entree, nom, defaut)


def empreinte_locale(chemin: Path) -> str | None:
    """SHA-256 du fichier, lu par blocs ; ``None`` si la lecture échoue."""
    digest = hashlib.sha256()
    try:
        with chemin.open("rb") as flux:
            while True:
                bloc = flux.read(_TAILLE_BLOC)
                if not bloc:
                    break
                digest.update(bloc)
    except OSError:
        return None
    return digest.hexdigest()


def _cle_objet(entree: Any) -> str:
    """Clé distante de l'entrée (les deux formes coexistent dans les payloads)."""
    for nom in ("object_key", "key"):
        valeur = _valeur(entree, nom)
        if isinstance(valeur, str) and valeur.strip():
            return valeur.strip()
    return ""


def _empreinte_inventoriee(entree: Any) -> str | None:
    """Empreinte retenue à l'inventaire, si elle a la forme d'un SHA-256."""
    for nom in ("source_sha256", "sha256"):
        valeur = _valeur(entree, nom)
        if isinstance(valeur, str) and _EMPREINTE.fullmatch(valeur.strip()):
            return valeur.strip()
    return None


def _entree_prouvee(entree: Any) -> bool:
    """Une entrée n'est « prouvée » qu'avec une clé, un envoi et une empreinte.

    ``object_key`` n'est renseigné par la chaîne d'import QUE lorsque l'objet a
    été vérifié ; la forme est revérifiée ici plutôt que supposée.
    """
    statut = str(_valeur(entree, "upload_status") or _valeur(entree, "status") or "")
    return bool(_cle_objet(entree)) and statut == "uploaded" and _empreinte_inventoriee(entree) is not None


def fichiers_presents(racine: Path) -> list[Path]:
    """Fichiers réels sous ``racine`` ; un lien symbolique n'est JAMAIS suivi.

    Un lien est remonté comme présent (donc conservé faute de preuve) : suivre
    un lien pour supprimer sa cible reviendrait à effacer un fichier qui
    n'appartient peut-être pas au staging.
    """
    presents: list[Path] = []
    for dossier, sous_dossiers, noms in os.walk(racine, followlinks=False):
        # Un sous-dossier qui est un lien : ne pas le traverser.
        sous_dossiers[:] = [d for d in sous_dossiers if not (Path(dossier) / d).is_symlink()]
        for nom in noms:
            presents.append(Path(dossier) / nom)
    return presents


def _supprimer_dossiers_vides(racine: Path) -> bool:
    """Efface les sous-dossiers devenus vides, puis ``racine`` si elle l'est.

    Seuls les dossiers situés sous ``racine`` sont touchés ; aucune erreur
    n'est masquée (un dossier non vide est simplement laissé en place).
    """
    for dossier, _, _ in os.walk(racine, topdown=False, followlinks=False):
        if Path(dossier) == racine:
            continue
        try:
            Path(dossier).rmdir()
        except OSError:
            continue  # non vide : on ne force jamais
    try:
        racine.rmdir()
        return True
    except OSError:
        return False


def purger_copie_prouvee(
    *,
    racine: Path,
    entrees: Iterable[Any],
    sous_staging: bool,
    verifier_objet: VerificateurObjet | None = None,
) -> BilanPurge:
    """Supprime, sous ``racine``, les fichiers dont l'objet distant est prouvé.

    ``sous_staging`` est fourni par l'appelant (il détient la configuration) :
    ``False`` interdit toute suppression, sans examen plus avant.

    ``verifier_objet`` relit l'objet distant. Sans vérificateur, RIEN n'est
    supprimé : « je n'ai pas pu vérifier » n'autorise aucune suppression.
    """
    racine = Path(racine)
    if not sous_staging:
        return BilanPurge(False, "source hors staging : jamais supprimée par l'application")
    try:
        if racine.is_symlink() or not racine.is_dir():
            return BilanPurge(False, f"dossier de purge inattendu : {racine}")
    except OSError as exc:
        return BilanPurge(False, f"dossier de purge illisible : {type(exc).__name__}")

    prouvees: dict[Path, tuple[str, str]] = {}
    for entree in entrees or ():
        if not _entree_prouvee(entree):
            continue
        chemin = Path(str(_valeur(entree, "path") or ""))
        if not str(chemin):
            continue
        try:
            resolu = chemin.resolve()
        except OSError:
            continue
        try:
            if racine.resolve() not in resolu.parents:
                continue  # hors du dossier de purge : hors de portée
        except OSError:
            continue
        prouvees[resolu] = (_cle_objet(entree), str(_empreinte_inventoriee(entree)))

    if not prouvees:
        return BilanPurge(False, "aucun fichier du dossier n'a de preuve d'envoi vérifiée")

    non_prouves: list[str] = []
    modifies: list[str] = []
    objets_illisibles: list[str] = []
    supprimes: list[str] = []
    conserves: list[tuple[str, str]] = []
    octets = 0
    verification_indisponible = False

    for chemin in sorted(fichiers_presents(racine), key=lambda p: str(p)):
        # Le lien est détecté AVANT résolution : ``resolve()`` suivrait le lien
        # et présenterait la cible comme un fichier ordinaire du staging.
        if chemin.is_symlink():
            conserves.append((str(chemin), "lien symbolique : jamais supprimé"))
            continue
        try:
            resolu = chemin.resolve()
        except OSError as exc:
            conserves.append((str(chemin), f"chemin illisible ({type(exc).__name__})"))
            continue
        attente = prouvees.get(resolu)
        if attente is None:
            non_prouves.append(str(chemin))
            conserves.append((str(chemin), "aucune preuve d'envoi vérifiée"))
            continue
        cle, empreinte_inventoriee = attente
        empreinte_actuelle = empreinte_locale(resolu)
        if empreinte_actuelle is None:
            conserves.append((str(chemin), "empreinte locale illisible"))
            continue
        if empreinte_actuelle != empreinte_inventoriee:
            modifies.append(str(chemin))
            conserves.append((str(chemin), "modifié depuis l'inventaire : la preuve porte sur les anciens octets"))
            continue
        if verifier_objet is None:
            verification_indisponible = True
            conserves.append((str(chemin), "vérification de l'objet distant indisponible"))
            continue
        try:
            resultat = verifier_objet(cle, resolu)
        except Exception as exc:  # un fournisseur qui échoue ne prouve rien
            LOGGER.warning("Purge : objet %s non vérifiable (%s) — copie locale conservée.", cle, type(exc).__name__)
            objets_illisibles.append(str(chemin))
            conserves.append((str(chemin), f"objet distant non vérifiable ({type(exc).__name__})"))
            continue
        if not getattr(resultat, "integrite_prouvee", False):
            objets_illisibles.append(str(chemin))
            methode = str(getattr(resultat, "methode", "?"))
            conserves.append((str(chemin), f"objet distant non prouvé ({methode})"))
            continue
        # Dernière lecture avant suppression : si le fichier a bougé entre la
        # vérification distante et l'instant présent, on ne supprime pas.
        if empreinte_locale(resolu) != empreinte_inventoriee:
            modifies.append(str(chemin))
            conserves.append((str(chemin), "modifié pendant la purge"))
            continue
        try:
            taille = resolu.stat().st_size
            resolu.unlink()
        except OSError as exc:
            conserves.append((str(chemin), f"suppression refusée ({type(exc).__name__})"))
            continue
        supprimes.append(str(chemin))
        octets += taille

    if not supprimes:
        raison = "aucune suppression : " + (
            "vérification de l'objet distant indisponible"
            if verification_indisponible
            else f"{len(conserves)} fichier(s) conservé(s), faute de preuve à l'instant de la purge"
        )
        return BilanPurge(
            False,
            raison,
            conserves=tuple(conserves),
            modifies=tuple(modifies),
            non_prouves=tuple(non_prouves),
            objets_illisibles=tuple(objets_illisibles),
            verification_indisponible=verification_indisponible,
        )

    dossier_supprime = False
    if not conserves:
        dossier_supprime = _supprimer_dossiers_vides(racine)

    reste = f" ; {len(conserves)} fichier(s) conservé(s)" if conserves else ""
    return BilanPurge(
        True,
        f"{len(supprimes)} fichier(s) supprimé(s) après relecture des octets distants{reste}",
        supprimes=tuple(supprimes),
        conserves=tuple(conserves),
        modifies=tuple(modifies),
        non_prouves=tuple(non_prouves),
        objets_illisibles=tuple(objets_illisibles),
        octets_liberes=octets,
        dossier_supprime=dossier_supprime,
    )
