#!/usr/bin/env python3
"""Vérification opposable de la sauvegarde quotidienne — Lot 13.2.

Constat de départ à corriger : la sauvegarde quotidienne n'était PAS absente.
Un service compose (``sauvegarde``) lance ``sauvegarde_planifiee``, qui
sauvegarde dès que l'intervalle est dépassé, et ``/health`` publie
``etat_sauvegarde`` avec un statut actionnable. Le rapport qui disait
« non fonctionnelle — aucun workflow planifié » se trompait de mécanisme.

Le défaut est ailleurs, et il est plus sournois :

1. **Personne ne lit le statut.** ``/health`` peut répondre
   ``"statut": "ancienne"`` indéfiniment : aucune alerte, aucune supervision,
   rien ne consomme ce champ. Une sauvegarde qui échoue en silence tous les
   jours pendant un an est indiscernable d'une sauvegarde qui marche — jusqu'au
   jour où l'on en a besoin.

2. **``"ok"`` est renvoyé même pour une copie purement locale.** Dans
   ``etat_sauvegarde``, l'avertissement « locale uniquement » est posé APRÈS le
   calcul du statut. Une sauvegarde posée sur le même disque que les données
   est donc déclarée ``"ok"``. Or c'est le scénario qui compte le plus : si le
   serveur meurt, la sauvegarde meurt avec lui. **Une copie sur la même
   machine n'est pas une sauvegarde.**

Ce script ferme la boucle. Il lit le MÊME état que ``/health`` — pas une
variante — et rend un code de sortie qu'une supervision peut consommer :

    python scripts/verifier_sauvegarde_quotidienne.py            # verdict
    python scripts/verifier_sauvegarde_quotidienne.py --json     # pour la supervision

Il est **strict par défaut** : une copie locale seule est un ÉCHEC. C'est
volontaire, et c'est tout l'objet du script.

L'échappatoire ``--autoriser-locale`` existe pour le déploiement qui assume
(versement vers unmontage réseau, NAS) ; elle écrit un avertissement sur la
sortie d'erreur. Elle reste lisible dans la ligne de cron de l'opérateur :
c'est la trace de la décision, pas une façon de la cacher.

Codes de retour : 0 = protection prouvée, 1 = protection NON prouvée,
2 = état indéterminable (configuration illisible, pas de base configurée).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seamtech_search.config import AppConfig, default_config_path  # noqa: E402
from seamtech_search.etat_exploitation import SEUIL_SAUVEGARDE_HEURES, etat_sauvegarde  # noqa: E402

#: Statut acceptable. Un seul.
STATUT_SAIN = "ok"

#: Ce qu'il faut faire, par statut. Un constat sans remède ne se répare pas.
REMEDE: dict[str, str] = {
    "jamais": (
        "Aucune sauvegarde n'a jamais été produite. Vérifier que le service "
        "compose « sauvegarde » est bien démarré (docker compose ps) et lire ses "
        "journaux : la cause la plus fréquente est une variable d'environnement "
        "manquante au démarrage."
    ),
    "illisible": (
        "Le manifeste de sauvegarde est présent mais illisible. Ne pas repartir "
        "sur une nouvelle sauvegarde avant d'avoir compris pourquoi : c'est "
        "peut-être le symptôme d'une copie tronquée."
    ),
    "age_inconnu": (
        "Manifeste présent mais horodatage illisible : l'âge de la copie est "
        "inconnu, donc sa fraîcheur n'est pas prouvée. Vérifier à la main."
    ),
    "non_qualifiee": (
        "La dernière copie n'est pas qualifiée comme protection globale "
        "(restauration coordonnée non prouvée). Une sauvegarde partielle est "
        "pire qu'une absence de sauvegarde : elle rassure. Lire les journaux du "
        "service « sauvegarde »."
    ),
    "ancienne": (
        "La dernière sauvegarde est plus vieille que le seuil. Le service "
        "planifié tourne-t-il encore ? Une boucle qui échoue ne se voit pas "
        "toute seule."
    ),
}


def evaluer(etat: dict, *, autoriser_locale: bool) -> tuple[bool, list[str]]:
    """Décide si la protection est prouvée. Rend (sain, raisons).

    Séparé de l'affichage pour être éprouvable : c'est la décision qui
    compte, pas le format du rapport.
    """
    raisons: list[str] = []
    statut = str(etat.get("statut") or "inconnu")
    sain = statut == STATUT_SAIN

    if not sain:
        raisons.append(
            f"statut « {statut} » : {etat.get('message') or REMEDE.get(statut, 'sans message')}"
        )
        if statut in REMEDE:
            raisons.append(f"  remède : {REMEDE[statut]}")

    # Le point que /health ne tranche PAS : « ok » est renvoyé même sans copie
    # hors-site. Ici, c'est un échec — sauf clause expresse de l'opérateur.
    #
    # ``--autoriser-locale`` ne touche QUE ce constat : un statut « ancienne »
    # ou « non_qualifiee » reste un échec quoi qu'il arrive.
    if not etat.get("copie_distante"):
        if autoriser_locale:
            raisons.append(
                "AVERTISSEMENT (accepté par --autoriser-locale) : sauvegarde "
                "LOCALE uniquement. Si cette machine meurt, la sauvegarde meurt "
                "avec elle. Ce n'est pas une protection de reprise."
            )
        else:
            raisons.append(
                "Aucune copie hors-site : « ok » au sens de /health, mais une "
                "copie sur le même disque que les données ne protège de rien. "
                "Configurer SEAMTECH_BACKUP_* (voir scripts/provisionner_stockage.sh). "
                "Si ce choix est assumé, le dire explicitement avec --autoriser-locale."
            )
            sain = False

    return sain, raisons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verifier_sauvegarde_quotidienne",
        description="Vérification opposable de la sauvegarde quotidienne.",
    )
    parser.add_argument("--config", default=None, help="Fichier de configuration.")
    parser.add_argument(
        "--seuil-heures",
        type=float,
        default=SEUIL_SAUVEGARDE_HEURES,
        help=f"Âge maximum toléré (défaut : {SEUIL_SAUVEGARDE_HEURES} h).",
    )
    parser.add_argument("--json", action="store_true", help="Sortie machine lisible.")
    parser.add_argument(
        "--autoriser-locale",
        action="store_true",
        help="Accepter une sauvegarde locale seule (risque assumé, averti).",
    )
    options = parser.parse_args(argv)

    try:
        config = AppConfig.load(options.config or default_config_path())
    except Exception as exc:  # noqa: BLE001 - le message est la sortie utile
        print(f"Configuration illisible : {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if not config.database_url:
        print(
            "Aucune base PostgreSQL configurée : la sauvegarde quotidienne est "
            "sans objet. En production, c'est une anomalie.",
            file=sys.stderr,
        )
        return 2

    etat = etat_sauvegarde(config, seuil_heures=options.seuil_heures)
    sain, raisons = evaluer(etat, autoriser_locale=options.autoriser_locale)

    if options.json:
        print(
            json.dumps(
                {"sain": sain, "etat": etat, "raisons": raisons},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0 if sain else 1

    print("=== Vérification de la sauvegarde quotidienne ===")
    print(f"  dernière    : {etat.get('derniere') or 'aucune'}")
    print(f"  âge         : {etat.get('age_heures')} h (seuil {options.seuil_heures} h)")
    print(f"  statut      : {etat.get('statut')}")
    print(f"  hors-site   : {etat.get('copie_distante') or 'AUCUN'}")
    print(f"  manifeste   : {etat.get('manifeste') or 'aucun'}")
    print()

    if sain:
        # Ne pas écrire « hors-site » quand ce n'est pas le cas : un outil
        # opposable ne se contredit pas entre deux lignes du même rapport.
        if etat.get("copie_distante"):
            print("PROTECTION PROUVÉE : copie récente, complète et hors-site.")
        else:
            print("PROTECTION PARTIELLE ACCEPTÉE : copie récente et complète, ")
            print("mais LOCALE — ce n'est pas une protection de reprise.")
        for raison in raisons:
            print(f"  {raison}")
        return 0

    print("PROTECTION NON PROUVÉE :")
    for raison in raisons:
        print(f"  - {raison}")
    print(
        "\nUne sauvegarde qui n'est pas vérifiée n'existe pas. Ce script rend un\n"
        "code de sortie précisément pour être branché sur une supervision :\n"
        "croyez le code de retour, pas l'absence de nouvelles."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
