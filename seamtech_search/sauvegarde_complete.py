"""Sauvegarde COMPLÈTE — base + corpus + documents. Commande HEBDOMADAIRE.

Décision de l'exploitante (2026-11-13), après le constat du Lot 21 :
l'automatisme quotidien reste « base + corpus », et la protection des PIÈCES
JOINTES est portée par une sauvegarde complète, DISTINCTE, lancée chaque
semaine.

Pourquoi un module à part plutôt que deux drapeaux de plus sur
``python -m seamtech_search.sauvegarde sauver`` :

* **un drapeau s'oublie.** Sans ``--avec-references --data …``, la commande
  produit une sauvegarde qui RESSEMBLE à une sauvegarde complète et n'en est
  pas une — le manifeste porte même ``complet: True``, puisque le périmètre
  DEMANDÉ est atteint. C'est exactement l'angle mort que le Lot 21 a trouvé
  dans le guide ; le refermer avec une option facultative serait le rouvrir.
* **ici le périmètre est fixé dans le code.** Il n'existe aucune option pour
  le réduire, et la commande échoue (code 2) en NOMMANT ce qui manque, plutôt
  que de rendre un manifeste incomplet qu'on croirait suffisant.

Et pourquoi ce module ne remplace PAS l'automatisme quotidien : les deux n'ont
ni le même coût ni le même objet. Copier les documents chaque nuit fait
transiter tout le volume de l'archive ; le faire chaque semaine borne ce coût
et ramène la perte maximale de pièces jointes à sept jours. Le guide dit l'un
et l'autre, et les DEUX lignes lisibles par une machine (``sauvegarde-auto:``
et ``sauvegarde-complete:``) sont confrontées au réel par les épreuves.

Ce que ce module ne fait pas : il ne modifie pas le rythme du planificateur
(``sauvegarde_planifiee``), qui reste quotidien et inchangé. La périodicité
hebdomadaire est portée par l'ordonnanceur de la machine hôte — la commande
exacte à y mettre est écrite dans le guide de mise en service.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from .config import AppConfig, default_config_path
from .etat_exploitation import dossier_sauvegardes
from .sauvegarde import SauvegardeError, _client_s3_depuis_env, _sha256_fichier

logger = logging.getLogger(__name__)

#: Marque portée par le manifeste. C'est ce qui distingue, dans un dossier de
#: sauvegardes, une complète hebdomadaire d'une quotidienne : sans elle, la
#: rétention et l'opérateur ne pourraient pas les différencier.
NATURE = "complete_hebdomadaire"

#: Quatre complètes conservées : un mois de recul, sans laisser le volume
#: croître sans borne (une complète emporte les documents, elle est lourde).
RETENTION_DEFAUT = 4

#: Les deux sections du manifeste qui portent les DOCUMENTS (pièces jointes).
#: C'est sur elles que ``verifier`` prouve la récupérabilité.
SECTIONS_DOCUMENTS = ("fichiers_locaux", "objets_s3")


class SauvegardeIncompleteError(SauvegardeError):
    """La sauvegarde complète n'a pas couvert tout le périmètre demandé.

    Distincte de ``SauvegardeError`` pour que l'appelant (la CLI, une tâche
    planifiée) puisse la traiter à part : une complète incomplète n'est pas
    une panne like les autres, c'est une fausse protection — la pire.
    """


def racines_data_par_defaut(config: AppConfig) -> list[Path]:
    """L'arborescence de données : le PARENT du dossier de sauvegardes.

    Dérivée de ``dossier_sauvegardes`` plutôt que codée en dur « ./data » :
    si un déploiement déplace les sauvegardes (``SEAMTECH_BACKUP_DIR``),
    les données suivent la même source de vérité au lieu de diverger.

    ``sauver`` reçoit ``exclure=[dossier_local]`` : le dossier de sauvegardes
    n'entre donc pas dans sa propre copie, même quand il vit sous ./data.
    """
    parent = dossier_sauvegardes(config).parent
    return [parent] if parent.is_dir() else []


def client_documents_ou_echec() -> Any:
    """Le client du bucket DOCUMENTS — ou un refus nommé.

    L'identité du bucket documents est celle de l'application, pas celle de
    sauvegarde : l'import local n'impose pas S3, et on ne copie que les objets
    déjà présents. Mais une sauvegarde COMPLÈTE sans ce client ne couvre pas
    les pièces jointes : elle doit donc échouer franchement plutôt que rendre
    un manifeste qu'on croirait complet.
    """
    from .storage import S3StorageClient

    try:
        config = AppConfig.load()
    except FileNotFoundError as exc:
        raise SauvegardeError(
            "configuration applicative absente : le bucket des documents est "
            "inatteignable, donc la sauvegarde complète ne peut pas couvrir les "
            "pièces jointes — refus de produire une fausse protection"
        ) from exc
    if not config.s3_endpoint_url:
        raise SauvegardeError(
            "aucun stockage S3 configuré (s3_endpoint_url vide) : les pièces "
            "jointes ne peuvent pas être copiées — refus de produire une "
            "sauvegarde complète qui ne l'est pas"
        )
    return S3StorageClient(config=config)


def _exiger_complet(manifeste: dict) -> None:
    """NOMME ce qui manque, puis refuse. Jamais un manifeste incomplet rendu.

    Le manifeste de ``sauver`` porte ``complet: True`` dès lors que le
    périmètre DEMANDÉ est atteint — une sauvegarde sans références est donc
    « complète » au sens du manifeste. S'en remettre à ce seul drapeau
    rejouerait exactement le défaut du Lot 21. On relit donc les compteurs
    de ce qui n'a PAS pu être copié.
    """
    from .sauvegarde import NATURE_MANIFESTE

    manquants: list[str] = []
    if manifeste.get(NATURE_MANIFESTE) != NATURE:
        manquants.append("le manifeste ne porte pas la marque d'une sauvegarde complète")
    if manifeste.get("references_incluses") is not True:
        manquants.append("les références de documents n'ont pas été incluses")
    for section, libelle in (("fichiers_locaux", "fichier local"), ("objets_s3", "objet S3")):
        bilan = manifeste.get(section) or {}
        nb = int(bilan.get("nb_manquants") or 0)
        if nb:
            exemples = ", ".join(str(m.get("id")) for m in (bilan.get("manquants") or [])[:5])
            manquants.append(f"{nb} {libelle}(s) non copié(s) (ex. id {exemples})")
    if (manifeste.get("data") or {}).get("nb_echecs"):
        manquants.append(f"{manifeste['data']['nb_echecs']} fichier(s) de données en échec")
    if manquants:
        raise SauvegardeIncompleteError(
            "sauvegarde complète INCOMPLÈTE — " + " ; ".join(manquants) + ". Rien n'est publié comme protection complète."
        )


def sauvegarder(
    config: AppConfig,
    *,
    dossier: Path | None = None,
    client_s3: Any | None = None,
    retention: int | None = None,
    racines_archive: list[Path] | None = None,
    racines_data: list[Path] | None = None,
    client_documents: Any | None = None,
) -> dict:
    """Une sauvegarde complète : base + archive + données + pièces jointes.

    Lève ``SauvegardeIncompleteError`` si une partie du périmètre n'a pas pu
    être copiée. Ne rend jamais un manifeste incomplet : une complète à
    moitié produite est plus dangereuse qu'une complète manquée, parce
    qu'elle rassure.
    """
    from .sauvegarde import sauver

    if not config.database_url:
        raise SauvegardeError("aucune base PostgreSQL configurée : la sauvegarde complète n'a pas d'objet")
    if client_documents is None:
        client_documents = client_documents_ou_echec()
    dossier_local = Path(dossier) if dossier else dossier_sauvegardes(config)
    donnees = [Path(r) for r in racines_data] if racines_data else racines_data_par_defaut(config)
    if not donnees:
        raise SauvegardeError(
            "aucune arborescence de données à sauvegarder : passer --data (le parent du "
            "dossier de sauvegardes est la source par défaut, il est introuvable)"
        )
    manifeste = sauver(
        config.database_url,
        list(racines_archive if racines_archive is not None else config.root_paths),
        dossier_local,
        client_s3=client_s3,
        retention=int(retention or RETENTION_DEFAUT),
        inclure_references_base=True,
        client_documents_s3=client_documents,
        racines_data=donnees,
        nature=NATURE,
    )
    _exiger_complet(manifeste)
    _sauver_corpus(client_s3, manifeste)
    return manifeste


def _sauver_corpus(client_s3: Any | None, manifeste: dict) -> None:
    """Le corpus mesuré entre dans la complète, lui aussi.

    Il est déjà couvert chaque nuit par le planificateur. Le reprendre ici
    n'est pas de la redondance : une complète qui omettrait le corpus ne
    serait pas « tout », et c'est précisément la promesse de cette commande.
    Une semaine de corpus perdu vaut une semaine de relevé à refaire.
    """
    from . import sauvegarde_corpus

    dossier = sauvegarde_corpus.dossier_corpus()
    if not dossier.is_dir():
        logger.info("Corpus absent (%s) : rien à ajouter à la sauvegarde complète.", dossier.name)
        return
    if client_s3 is None:
        logger.error(
            "Corpus NON envoyé hors-site : aucun stockage distant configuré. "
            "La sauvegarde complète reste LOCALE — ce n'est pas une reprise."
        )
        return
    try:
        manifeste["corpus"] = sauvegarde_corpus.sauvegarder(client_s3, dossier=dossier)
    except Exception as exc:
        # Type seul au journal : la trace peut porter une clé ou un endpoint.
        logger.error("Corpus en échec dans la sauvegarde complète (%s).", type(exc).__name__)
        return
    logger.info(
        "Corpus ajouté à la sauvegarde complète : %s fichier(s), instant %s.",
        manifeste["corpus"].get("nb_fichiers"),
        manifeste["corpus"].get("horodatage"),
    )


def verifier(chemin_manifeste: Path) -> dict:
    """Prouve que les DOCUMENTS du bundle sont présents et conformes.

    C'est la contrepartie du constat du Lot 21 : une sauvegarde qui annonce
    des pièces jointes doit pouvoir montrer leurs octets. On ne restaure rien
    ici — on relit chaque copie du bundle et on compare son empreinte à
    celle que le manifeste a enregistrée au moment de la copie.

    Aucune confiance dans un drapeau : c'est le SHA-256 réel qui décide.
    """
    chemin = Path(chemin_manifeste)
    manifeste = json.loads(chemin.read_text(encoding="utf-8"))
    from .sauvegarde import NATURE_MANIFESTE

    nature = manifeste.get(NATURE_MANIFESTE)
    if nature != NATURE:
        return {
            "ok": False,
            "nature": nature,
            "erreur": "manifeste d'une sauvegarde COMPLÈTE attendu (marque absente ou différente)",
        }
    racine = chemin.parent
    verifiees: list[dict] = []
    cassees: list[dict] = []
    absentes: list[dict] = []
    for section in SECTIONS_DOCUMENTS:
        for copie in (manifeste.get(section) or {}).get("copies") or []:
            relatif = copie.get("path_relatif") or ""
            entree = {"section": section, "id": copie.get("id"), "path_relatif": relatif}
            cible = racine / relatif
            if not relatif or not cible.is_file():
                absentes.append(entree)
                continue
            if _sha256_fichier(cible) != copie.get("sha256"):
                cassees.append(entree)
            else:
                verifiees.append(entree)
    return {
        "ok": not cassees and not absentes and bool(verifiees),
        "manifeste": str(chemin),
        "nature": nature,
        "nb_documents_verifies": len(verifiees),
        "nb_documents_alteres": len(cassees),
        "nb_documents_absents": len(absentes),
        "alteres": cassees[:20],
        "absents": absentes[:20],
        # Un bundle SANS document n'est pas une preuve : « 0 vérifié » doit
        # être visible comme une anomalie, pas passer pour un succès vide.
        "avertissement": None if verifiees else "aucun document dans le bundle : la preuve est vide",
    }


def restaurer(
    chemin_manifeste: Path,
    *,
    base_cible: str,
    fichiers_cible: Path,
    data_cible: Path | None = None,
    client_documents: Any | None = None,
) -> dict:
    """Restauration COORDONNÉE depuis une sauvegarde complète.

    Les documents vont dans ``fichiers_cible``, un dossier DISTINCT : jamais
    sur les sources d'origine. Une restauration qui écrase l'existant avant
    d'avoir prouvé le bundle transformerait une sauvegarde en destructeur.
    """
    from .sauvegarde import restaurer_complet

    chemin = Path(chemin_manifeste)
    manifeste = json.loads(chemin.read_text(encoding="utf-8"))
    return restaurer_complet(
        base_cible,
        manifeste,
        chemin.parent,
        data_cible=Path(data_cible) if data_cible else None,
        fichiers_cible=Path(fichiers_cible),
        client_s3=client_documents,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m seamtech_search.sauvegarde_complete",
        description="Sauvegarde COMPLÈTE hebdomadaire : base + archive + données + pièces jointes.",
    )
    sous = parser.add_subparsers(dest="commande", required=True)

    p_sauver = sous.add_parser("sauvegarder", help="produit une sauvegarde complète (périmètre non négociable)")
    p_sauver.add_argument("--config", default=None)
    p_sauver.add_argument("--dossier", default=None, help="dossier local de destination")
    p_sauver.add_argument("--retention", type=int, default=None)
    p_sauver.add_argument("--archive", nargs="*", default=None, help="racines d'archive (défaut : config.root_paths)")
    p_sauver.add_argument("--data", nargs="*", default=None, help="arborescences de données (défaut : parent des sauvegardes)")
    p_sauver.add_argument("--sans-s3", action="store_true", help="pas d'envoi hors-site (copie locale seule)")
    p_sauver.add_argument("--verbose", action="store_true")

    p_verifier = sous.add_parser("verifier", help="prouve que les documents du bundle sont récupérables")
    p_verifier.add_argument("--manifeste", required=True)
    p_verifier.add_argument("--verbose", action="store_true")

    p_restaurer = sous.add_parser("restaurer", help="restauration coordonnée vers des cibles DISTINCTES")
    p_restaurer.add_argument("--manifeste", required=True)
    p_restaurer.add_argument(
        "--base-cible",
        default=os.environ.get("SEAMTECH_RESTORE_DATABASE_URL"),
        required=not bool(os.environ.get("SEAMTECH_RESTORE_DATABASE_URL")),
        help="base DISTINCTE ; préférer SEAMTECH_RESTORE_DATABASE_URL",
    )
    p_restaurer.add_argument("--fichiers-cible", required=True, help="dossier DISTINCT pour les documents")
    p_restaurer.add_argument("--data-cible", default=None)
    p_restaurer.add_argument("--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.commande == "verifier":
        rapport = verifier(Path(args.manifeste))
        print(json.dumps(rapport, ensure_ascii=False, indent=1))
        return 0 if rapport["ok"] else 2

    if args.commande == "restaurer":
        rapport = restaurer(
            Path(args.manifeste),
            base_cible=args.base_cible,
            fichiers_cible=Path(args.fichiers_cible),
            data_cible=Path(args.data_cible) if args.data_cible else None,
            client_documents=client_documents_ou_echec(),
        )
        print(
            json.dumps(
                {
                    "base": rapport["base"]["base"],
                    "data": (rapport["data"] or {}).get("nb", 0),
                    "fichiers": (rapport["fichiers"] or {}).get("nb", 0),
                    "objets": (rapport["objets"] or {}).get("nb", 0),
                    "verification_ok": (rapport["verification"] or {}).get("ok"),
                },
                ensure_ascii=False,
            )
        )
        return 0

    config = AppConfig.load(args.config or default_config_path())
    client = None if args.sans_s3 else _client_s3_depuis_env()
    if client is None and not args.sans_s3:
        logger.warning("S3 hors-site non configuré : la sauvegarde complète restera LOCALE (pas une protection).")
    try:
        manifeste = sauvegarder(
            config,
            dossier=Path(args.dossier) if args.dossier else None,
            client_s3=client,
            retention=args.retention,
            racines_archive=[Path(r) for r in args.archive] if args.archive else None,
            racines_data=[Path(r) for r in args.data] if args.data else None,
        )
    except SauvegardeIncompleteError as exc:
        # Le message NOMME déjà ce qui manque : on le remonte tel quel. Une
        # complète incomplète doit être vue, jamais avalée par un code 0.
        logger.error("%s", exc)
        return 2
    print(
        json.dumps(
            {
                "nature": manifeste.get("nature"),
                "manifeste": str(
                    Path(args.dossier or dossier_sauvegardes(config)) / (manifeste["dump"]["fichier"] + ".manifest.json")
                ),
                "dump_sha256": manifeste["dump"]["sha256"],
                "documents_locaux": (manifeste.get("fichiers_locaux") or {}).get("nb_copies", 0),
                "documents_objets": (manifeste.get("objets_s3") or {}).get("nb_copies", 0),
                "donnees_copies": (manifeste.get("data") or {}).get("nb_copies", 0),
                "complet": manifeste.get("complet"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
