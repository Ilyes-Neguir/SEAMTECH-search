#!/usr/bin/env python3
"""Revue humaine des champs extraits — Lot 10.

Pourquoi ce script existe : le calibrage des seuils de confiance est bloqué
par un compte exact de ZÉRO fiche de vérité terrain. Non pas faute de
données — le corpus est déposé — mais faute de VERDICTS : personne ne s'est
prononcé sur ce que le moteur avait extrait.

Avant ce script, le calibrage lisait un signe indirect : « l'approbateur
a-t-il corrigé le champ ? ». Un champ non corrigé était compté juste, alors
qu'il n'est qu'accepté. On ne calibre pas une confiance sur une absence de
geste. Ce script fabrique donc la SEULE assiette qui vaille : un verdict
prononcé, daté, signé.

Il ne fait QUE trois choses, volontairement :

    echantillonner   proposer des champs à revoir, sans en décider un seul
    verdict          enregistrer un verdict prononcé par un humain
    bilan            dire où en est la preuve, et ce qu'il reste à faire

**Il ne lance jamais la revue à la place de l'humain.** Fournir l'outil et la
procédure est le travail de ce script ; produire les verdicts est le travail
d'un approbateur, et aucun seuil ne sera calculé sans eux.

.. code-block:: shell

    export SEAMTECH_DATABASE_URL=postgresql://…

    python scripts/revue_verdicts.py bilan
    python scripts/revue_verdicts.py echantillonner --taille 20
    python scripts/revue_verdicts.py verdict --fiche <CODE> --champ guindant \
        --valeur certain --acteur "M. Dupont"

Codes de retour : 0 = succès, 1 = état insuffisant (pas une erreur : un
manque de preuves), 2 = erreur d'entrée.
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
    VERDICT_PREUVE,
    bilan_verdicts,
    nombre_fiches_verdictees,
)

LOGGER = logging.getLogger("revue_verdicts")

#: Champs proposés à la revue, et dans quel ordre : les cotes d'abord, parce
#: que c'est là qu'une erreur coûte une voile, pas une ligne de texte.
ORDRE_CHAMPS: tuple[str, ...] = (
    "guindant",
    "bordure",
    "chute",
    "surface",
    "tissu",
    "gamme",
    "type_voile",
    "matiere",
)


def _index() -> object:
    from seamtech_search.config import AppConfig
    from seamtech_search.indexer import SearchIndex

    url = os.environ.get("SEAMTECH_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("SEAMTECH_DATABASE_URL est requis (base PostgreSQL de l'atelier).")
    cfg = AppConfig.load()
    return SearchIndex(Path(cfg.database_path), url)


def _echantillonner(index: object, taille: int) -> int:
    """Propose des champs à revoir : ceux qui n'ont ENCORE aucun verdict.

    L'échantillon n'est PAS aléatoire sans raison : il prend les confiances les
    plus basses d'abord. Un champ où le moteur se trompe volontiers apprend
    plus à la calibration qu'un champ qu'il réussit déjà — et c'est là que le
    seuil a besoin d'être mesuré.
    """
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                """
                SELECT f.code, e.champ, e.valeur_brute, e.valeur_normalisee, e.confiance
                FROM fiche_champ_extrait e
                JOIN fiche f ON f.id_fiche = e.id_fiche
                WHERE e.confiance IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM verdict_revue v
                      WHERE v.id_fiche = e.id_fiche AND v.champ = e.champ
                  )
                ORDER BY e.confiance ASC, f.code ASC
                LIMIT %s
                """,
                (taille,),
            )
            lignes = curseur.fetchall()

    if not lignes:
        print("Aucun champ en attente de verdict : soit tout est revu, soit la base est vide.")
        return 1

    print(f"{len(lignes)} champ(s) proposé(s) — confiances les plus basses d'abord :\n")
    print(f"  {'FICHE':<14} {'CHAMP':<14} {'CONF.':<7} VALEUR EXTRAITE")
    print(f"  {'-' * 13} {'-' * 13} {'-' * 6} {'-' * 40}")
    for code, champ, brute, normalisee, confiance in lignes:
        valeur = str(normalisee or brute or "")[:40]
        print(f"  {str(code):<14} {str(champ):<14} {float(confiance):<7.3f} {valeur}")
    print(
        "\nPour chacun : python scripts/revue_verdicts.py verdict"
        " --fiche <CODE> --champ <CHAMP> --valeur certain|probable|rejet --acteur <QUI>"
    )
    return 0


def _verdict(index: object, fiche: str, champ: str, valeur: str, acteur: str, motif: str | None) -> int:
    """Enregistre un verdict prononcé. N'écrase rien : il AJOUTE une ligne."""
    if valeur not in VERDICT_PREUVE and valeur != "probable":
        raise SystemExit(
            f"verdict invalide : {valeur!r} (attendu : certain, probable ou rejet)"
        )
    if not acteur.strip():
        raise SystemExit("--acteur est obligatoire : un seuil calibré sans auteur n'est pas opposable.")

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT id_fiche FROM fiche WHERE code = %s", (fiche,))
            ligne = curseur.fetchone()
            if ligne is None:
                raise SystemExit(f"fiche inconnue : {fiche!r}")
            id_fiche = int(ligne[0])

            # La confiance est FIGÉE au moment du verdict : sans cette copie,
            # un recalcul ultérieur réécrirait l'histoire de la mesure.
            curseur.execute(
                """
                SELECT confiance FROM fiche_champ_extrait
                WHERE id_fiche = %s AND champ = %s
                ORDER BY confiance IS NULL, id_champ DESC
                LIMIT 1
                """,
                (id_fiche, champ),
            )
            ligne = curseur.fetchone()
            confiance = None if ligne is None else ligne[0]

            curseur.execute(
                """
                INSERT INTO verdict_revue (id_fiche, champ, verdict, confiance_moteur, acteur, motif)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (id_fiche, champ, valeur, confiance, acteur.strip(), motif),
            )
        connexion.commit()

    suite = "" if valeur in VERDICT_PREUVE else " — consigné, mais exclu du calcul du seuil"
    confiance_affichee = f"{float(confiance):.3f}" if confiance is not None else "inconnue"
    print(f"Verdict enregistré : {fiche} / {champ} = {valeur} (confiance moteur {confiance_affichee}){suite}")
    return 0


def _bilan(index: object) -> int:
    """Dit où en est la preuve. Ne flatte pas : il manque des verdicts, il le dit."""
    repartition = bilan_verdicts(index)
    fiches = nombre_fiches_verdictees(index)

    print("=== Verdicts courants (dernier par fiche et champ) ===")
    if not repartition:
        print("  aucun verdict consigné")
    for verdict in ("certain", "probable", "rejet"):
        nombre = repartition.get(verdict, 0)
        note = "" if verdict in VERDICT_PREUVE else "   <- consigné, NE FEDE PAS le calcul"
        print(f"  {verdict:<10} {nombre:>6}{note}")

    print("\n=== Assiette du calibrage ===")
    print(f"  fiches portant un verdict exploitable : {fiches}")
    print(f"  minimum requis                        : {MIN_FICHES_CALIBRATION}")

    if fiches < MIN_FICHES_CALIBRATION:
        manque = MIN_FICHES_CALIBRATION - fiches
        print(
            f"\n  INSUFFISANT : il manque {manque} fiche(s). Aucun seuil ne sera proposé.\n"
            f"  Ce n'est pas une erreur, c'est un manque de preuves — et c'est\n"
            f"  précisément ce que le calibrage doit refuser de combler par une estimation.\n\n"
            f"  Pour avancer : python scripts/revue_verdicts.py echantillonner --taille 20"
        )
        return 1

    print(
        "\n  SUFFISANT. Le seuil peut être recalculé :\n"
        "    python scripts/calibrer_seuils.py            (propose)\n"
        "    python scripts/calibrer_seuils.py --appliquer (écrit)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Revue humaine des champs extraits — fabrique la vérité terrain du calibrage."
    )
    sous = parser.add_subparsers(dest="commande", required=True)

    p_ech = sous.add_parser("echantillonner", help="Propose des champs à revoir.")
    p_ech.add_argument("--taille", type=int, default=20, help="Nombre de champs proposés (défaut 20).")

    p_ver = sous.add_parser("verdict", help="Enregistre un verdict prononcé par un humain.")
    p_ver.add_argument("--fiche", required=True, help="Code de la fiche.")
    p_ver.add_argument("--champ", required=True, help="Nom du champ.")
    p_ver.add_argument("--valeur", required=True, help="certain | probable | rejet")
    p_ver.add_argument("--acteur", required=True, help="Qui tranche (obligatoire).")
    p_ver.add_argument("--motif", default=None, help="Motif libre, optionnel.")

    sous.add_parser("bilan", help="État de la preuve, et ce qu'il reste à faire.")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    index = _index()
    if args.commande == "echantillonner":
        return _echantillonner(index, max(1, args.taille))
    if args.commande == "verdict":
        return _verdict(index, args.fiche, args.champ, args.valeur, args.acteur, args.motif)
    return _bilan(index)


if __name__ == "__main__":
    raise SystemExit(main())
