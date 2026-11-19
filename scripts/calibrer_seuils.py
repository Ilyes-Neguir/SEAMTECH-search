#!/usr/bin/env python3
"""Calibration des seuils de validation automatique — Lot 3.

Pourquoi ce script existe : ``config/seuils_confiance.json`` porte
``calibre: false`` et ``fiches_reelles_utilisees: 0``. Tant que cette preuve
manque, la validation groupée est INTERDITE, et elle a raison de l'être. Ce
script fabrique la preuve — ou dit pourquoi il ne peut pas.

Il ne calibre pas une confiance avec des confiances. Savoir que le moteur
s'accorde 0,92 n'apprend rien : il compare la confiance ANNONCÉE au sort RÉEL
des champs, c'est-à-dire à ce que l'approbateur a corrigé. Un champ corrigé
était faux ; un champ non corrigé sur une fiche validée est accepté, non prouvé
exact — la nuance est écrite dans le rapport.

**Le script ne écrit rien par défaut.** Il montre, il propose, et il faut
``--appliquer`` pour que le fichier change. Une calibration qu'on n'a pas lue
n'est pas une calibration, c'est un acquiescement.

.. code-block:: shell

    export SEAMTECH_DATABASE_URL=postgresql://…          # simulation
    python scripts/calibrer_seuils.py
    python scripts/calibrer_seuils.py --appliquer        # écrit, si la preuve suit

Codes de retour : 0 = au moins une famille calibrable, 1 = aucune famille
calibrable (ce n'est pas une erreur, c'est un manque de preuves), 2 = erreur
d'entrée.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seamtech_search.fiches.calibration import (  # noqa: E402
    MIN_FICHES_CALIBRATION,
    CalibrationRefusee,
    appliquer_calibration,
    nombre_fiches_arbitrees,
    observations_depuis_base,
    proposition_famille,
)
from seamtech_search.fiches.persistance import charger_seuils  # noqa: E402

LOGGER = logging.getLogger("calibrer_seuils")


def _index() -> object:
    from seamtech_search.config import AppConfig
    from seamtech_search.indexer import SearchIndex

    url = os.environ.get("SEAMTECH_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("SEAMTECH_DATABASE_URL est requis (base PostgreSQL de l'atelier).")
    cfg = AppConfig.load()
    return SearchIndex(Path(cfg.database_path), url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibre les seuils de validation automatique.")
    parser.add_argument(
        "--appliquer",
        action="store_true",
        help="Écrit config/seuils_confiance.json (refusé si la preuve est insuffisante).",
    )
    parser.add_argument(
        "--chemin",
        default=None,
        help="Chemin du fichier de seuils (défaut : config/seuils_confiance.json).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    index = _index()
    seuils_actuels = charger_seuils()
    observations = observations_depuis_base(index)
    fiches = nombre_fiches_arbitrees(index)

    print(f"Fiches validées portant au moins un champ arbitré : {fiches}")
    print(f"Minimum requis pour déclarer les seuils calibrés : {MIN_FICHES_CALIBRATION}")
    print()

    propositions = [
        proposition_famille(famille, seuils_actuels.get(famille, 0.9), observations.get(famille, []))
        for famille in sorted({*seuils_actuels, *observations})
    ]
    for proposition in propositions:
        print(f"— {proposition['lecture']}")
        for palier in proposition["paliers"]:
            exactitude = (
                f"{palier['exactitude']:.0%}" if palier["exactitude"] is not None else "non mesurable"
            )
            print(
                f"    ≥ {palier['seuil']:.2f} : {palier['nb']} champ(s), "
                f"{palier['nb_corrects']} juste(s), exactitude {exactitude}"
            )
    print()

    applicables = [proposition for proposition in propositions if proposition["applicable"]]
    if not applicables:
        print("Aucune famille calibrable : pas assez de preuves. Aucun seuil n'a été changé.")
        return 1

    if not args.appliquer:
        print(f"{len(applicables)} famille(s) calibrable(s). Relancez avec --appliquer pour écrire.")
        return 0

    chemin = Path(args.chemin) if args.chemin else Path("config/seuils_confiance.json")
    try:
        resultat = appliquer_calibration(chemin, propositions, nb_fiches=fiches)
    except CalibrationRefusee as erreur:
        print(f"Écriture refusée : {erreur}", file=sys.stderr)
        return 1
    print(f"Écrit : {resultat['chemin']}")
    for entree in resultat["familles_appliquees"]:
        print(f"  {entree['famille']} → {entree['seuil']:.2f} (sur {entree['effectif']} champ(s) arbitré(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
