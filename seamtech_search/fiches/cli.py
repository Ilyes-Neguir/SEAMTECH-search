"""CLI de démonstration du Lot B (plan v3.0 §10.2) — fiche technique.

Usage :
    python -m seamtech_search.fiches.cli extraire CHEMIN.pdf [--gabarit CODE]
    python -m seamtech_search.fiches.cli ecrire CHEMIN.pdf --database-url URL
    python -m seamtech_search.fiches.cli init --database-url URL [--pdf CHEMIN]
    python -m seamtech_search.fiches.cli banc CHEMIN.pdf --database-url URL

``extraire`` ne touche À AUCUNE base : il lit le PDF et imprime le rapport
champ par champ (valeur, confiance, page, zone). ``ecrire`` ajoute
l'écriture transactionnelle en base (statut ``a_valider``). ``init``
applique les migrations, enregistre les gabarits et la vérité 7792 dans
``gabarit_test``. ``banc`` rejoue la non-régression du gabarit de référence.

La route HTTP et l'interface opérateur sont du ressort des lots C et D —
volontairement hors périmètre ici.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from seamtech_search.fiches.extraction import extraire_avec_filet, extraire_fiche
from seamtech_search.fiches.gabarits import (
    GABARITS_EMBARQUES,
    charger_gabarits,
    initialiser_gabarits,
)
from seamtech_search.fiches.modeles import FicheExtraite
from seamtech_search.fiches.persistance import (
    SEUIL_GABARIT_TEST,
    VERITE_7792,
    charger_seuils,
    ecrire_fiche,
    evaluer_verite,
    initialiser_verite_7792,
    routage,
    verifier_non_regression,
)

LOGGER = logging.getLogger("seamtech_search.fiches.cli")


def _index(database_url: str):
    """Construit l'index PostgreSQL (SearchIndex, §17.1 — jamais SQLite ici)."""
    from seamtech_search.indexer import SearchIndex

    return SearchIndex(Path(f"/tmp/unused-fiches-{abs(hash(database_url))}.db"), database_url)


def _rapport(fiche: FicheExtraite, seuils: dict[str, float] | None = None) -> str:
    """Rapport champ par champ : valeur, confiance, page, zone (traçabilité)."""
    lignes: list[str] = []
    titre = fiche.code or "(code non lu)"
    lignes.append(f"Fiche {titre} — gabarit {fiche.gabarit_code or '(inconnu)'} v{fiche.gabarit_version or '-'}")
    lignes.append(f"PDF : {fiche.fichier_pdf} — score de qualité global : {fiche.score_qualite()}")
    lignes.append("")
    lignes.append("Champs de tête :")
    if not fiche.champs:
        lignes.append("  (aucun champ lu)")
    for champ in fiche.champs:
        zone = f"p{champ.page + 1} {champ.zone.en_dict()}" if champ.zone else "sans zone"
        lignes.append(
            f"  {champ.champ:<34} {str(champ.valeur_normalisee)[:48]:<48} conf={champ.confiance:.2f}  {zone}"
        )
    if fiche.cotes:
        lignes.append("Cotes :")
        for cotes in fiche.cotes:
            valeurs = {k: getattr(cotes, k) for k in ("slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_cm", "poids_kg") if getattr(cotes, k) is not None}
            lignes.append(f"  jeu {cotes.jeu} : {valeurs}")
            for champ in cotes.champs:
                zone = f"p{champ.page + 1} {champ.zone.en_dict()}" if champ.zone else "sans zone"
                lignes.append(f"    {champ.champ:<34} {str(champ.valeur_normalisee)[:32]:<32} conf={champ.confiance:.2f}  {zone}")
    for etiquette, groupe in (
        ("Matériaux", fiche.materiaux),
        ("Galons", fiche.galons),
        ("Jonctions", fiche.jonctions),
        ("Finitions", fiche.finitions),
        ("Options", fiche.options),
        ("Renforts", fiche.renforts),
    ):
        if groupe:
            lignes.append(f"{etiquette} : {len(groupe)}")
            for element in groupe:
                lignes.append(f"  - {element.model_dump_json(exclude={'champs'}, exclude_none=True)}")
    if fiche.mesures_libres:
        lignes.append(f"Mesures libres (RG6) : {len(fiche.mesures_libres)}")
        for libre in fiche.mesures_libres:
            lignes.append(f"  - {libre.valeur_brute}  (p{libre.page + 1}, conf={libre.confiance:.2f})")
    if fiche.anomalies:
        lignes.append("Anomalies RG16 :")
        for anomalie in fiche.anomalies:
            lignes.append(f"  [{anomalie.gravite}] {anomalie.code} : {anomalie.message}")
    decision = routage(fiche, seuils)
    lignes.append(f"Routage proposé : {decision['voie']} — {decision['motif']}")
    for sous_seuil in decision["champs_sous_seuil"]:
        lignes.append(f"  sous le seuil ({sous_seuil['seuil']}) : {sous_seuil['champ']} conf={sous_seuil['confiance']:.2f}")
    return "\n".join(lignes)


def _cmd_extraire(arguments: argparse.Namespace) -> int:
    gabarits = list(GABARITS_EMBARQUES)
    if arguments.gabarit:
        fiche = extraire_fiche(Path(arguments.chemin), gabarits=gabarits, gabarit_code=arguments.gabarit)
    else:
        fiche = extraire_avec_filet(Path(arguments.chemin), gabarits)
    print(_rapport(fiche, charger_seuils(arguments.seuils)))
    return 0


def _cmd_ecrire(arguments: argparse.Namespace) -> int:
    gabarits = list(GABARITS_EMBARQUES)
    fiche = extraire_avec_filet(Path(arguments.chemin), gabarits)
    index = _index(arguments.database_url)
    id_fiche, action = ecrire_fiche(index, fiche)
    print(_rapport(fiche, charger_seuils(arguments.seuils)))
    if action == "conservee_validee":
        statut = "« valide » conservé — aucune donnée validée n'est écrasée (RG11)"
    else:
        statut = "« a_valider » (RG3 : jamais valide à l'arrivée)"
    print(f"\nBase : fiche #{id_fiche} — {action} — statut {statut}.")
    return 0


def _cmd_init(arguments: argparse.Namespace) -> int:
    index = _index(arguments.database_url)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    initialiser_verite_7792(index, Path(arguments.pdf) if arguments.pdf else None)
    print(f"Gabarits enregistrés : {', '.join(g.code for g in charger_gabarits(index))}")
    print("Vérité 7792 enregistrée dans gabarit_test (seuil 0,90).")
    return 0


def _cmd_banc(arguments: argparse.Namespace) -> int:
    gabarits = list(GABARITS_EMBARQUES)
    fiche = extraire_fiche(Path(arguments.chemin), gabarits=gabarits, gabarit_code=arguments.code)
    if arguments.database_url:
        index = _index(arguments.database_url)
        resultat = verifier_non_regression(index, fiche, nom_fichier=arguments.nom)
    else:
        taux, ecarts = evaluer_verite(fiche, VERITE_7792)
        seuil = SEUIL_GABARIT_TEST
        resultat = {
            "taux": taux,
            "seuil": seuil,
            "conforme": taux >= seuil,
            "ecarts": ecarts,
            "total": len(VERITE_7792),
        }
    print(f"Banc gabarit {fiche.gabarit_code} / {arguments.nom}")
    print(
        f"  champs corrects : {resultat['total'] - len(resultat['ecarts'])}/{resultat['total']} "
        f"({resultat['taux']:.1%}, seuil {resultat['seuil']:.0%})"
    )
    if resultat["ecarts"]:
        print("  écarts :")
        for ecart in resultat["ecarts"]:
            print(f"    {ecart['champ']}: attendu {ecart['attendu']!r}, lu {ecart['lu']!r}")
    print(f"  verdict : {'CONFORME' if resultat['conforme'] else 'NON CONFORME'}")
    return 0 if resultat["conforme"] else 1


def _cmd_deposer(arguments: argparse.Namespace) -> int:
    from seamtech_search.fiches.depot import deposer_dossier, etat_lot

    index = _index(arguments.database_url)
    resultat = deposer_dossier(index, Path(arguments.dossier))
    print(f"Dossier : {arguments.dossier}")
    print(f"  statut : {resultat['statut']}" + (f" — {resultat['raison']}" if resultat["raison"] else ""))
    if resultat.get("fiche"):
        print(f"  fiche  : {resultat['fiche']} ({resultat['pieces']} pièce(s) jointe(s))")
    if resultat.get("id_lot"):
        etat = etat_lot(index, int(resultat["id_lot"]))
        print(f"  lot    : #{etat['id_lot']} {etat['statut']} — {etat['nb_traites']}/{etat['nb_dossiers']} traité(s)")
    return 0 if resultat["statut"] in ("traite", "deja_traite") else 1


def _cmd_lot(arguments: argparse.Namespace) -> int:
    from seamtech_search.fiches.depot import creer_lot, executer_lot

    index = _index(arguments.database_url)
    id_lot = creer_lot(index, Path(arguments.racine))
    print(f"Lot #{id_lot} créé ({arguments.racine}).")
    etat = executer_lot(index, id_lot, interrompre_apres=arguments.interrompre_apres)
    print(
        f"  statut : {etat['statut']} — {etat['nb_traites']} traité(s), {etat['nb_echecs']} échec(s), "
        f"{len(etat['restants'])} restant(s)"
    )
    for dossier in etat["dossiers"]:
        if dossier["statut"] == "echec":
            print(f"    ÉCHEC {dossier['chemin_dossier']} : {dossier['raison']}")
    return 0 if etat["statut"] == "termine" else 1


def principal(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seamtech_search.fiches", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sous = parser.add_subparsers(dest="commande", required=True)

    extraire = sous.add_parser("extraire", help="lit un PDF et imprime le rapport champ par champ (aucune écriture)")
    extraire.add_argument("chemin")
    extraire.add_argument("--gabarit", default=None, help="force un code de gabarit (sinon détection)")
    extraire.add_argument("--seuils", default=None, help="chemin du fichier seuils_confiance.json")

    ecrire = sous.add_parser("ecrire", help="extrait PUIS écrit la fiche en base (transaction, statut a_valider)")
    ecrire.add_argument("chemin")
    ecrire.add_argument("--database-url", required=True)
    ecrire.add_argument("--seuils", default=None)

    init = sous.add_parser("init", help="migrations + gabarits embarqués + vérité 7792 (idempotent)")
    init.add_argument("--database-url", required=True)
    init.add_argument("--pdf", default=None, help="chemin du PDF 7792 (empreinte SHA-256 enregistrée)")

    deposer = sous.add_parser("deposer", help="Lot C : dépose UN dossier (fiche + pièces jointes, lot suivi)")
    deposer.add_argument("dossier")
    deposer.add_argument("--database-url", required=True)

    lot = sous.add_parser("lot", help="Lot C : crée et exécute un lot (une racine, un sous-dossier par affaire)")
    lot.add_argument("racine")
    lot.add_argument("--database-url", required=True)
    lot.add_argument("--interrompre-apres", type=int, default=None, help="interruption volontaire (tests de reprise)")

    banc = sous.add_parser("banc", help="non-régression : fiche extraite vs vérité gabarit_test")
    banc.add_argument("chemin")
    banc.add_argument("--database-url", default=None, help="base PostgreSQL (si omis : banc autonome)")
    banc.add_argument("--code", default="FICHE_PORTANT_V1")
    banc.add_argument("--nom", default="fiche-7792-SO_ffab.pdf")

    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s : %(message)s")
    try:
        return {
            "extraire": _cmd_extraire,
            "ecrire": _cmd_ecrire,
            "init": _cmd_init,
            "banc": _cmd_banc,
            "deposer": _cmd_deposer,
            "lot": _cmd_lot,
        }[arguments.commande](arguments)
    except Exception as erreur:
        LOGGER.exception("Échec du traitement (conséquence : rien n'a été écrit).")
        print(f"Erreur : {erreur}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(principal())
