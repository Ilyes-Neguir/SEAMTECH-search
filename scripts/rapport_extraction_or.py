#!/usr/bin/env python3
"""Rapport d'or Lot 3a — extraction structurée des voiles.

Par défaut, le banc mesure deux échantillons déjà versionnés :

* le PDF client réel 7792-SO, à la fois sur les 74 cibles historiques de la
  fiche et sur les champs normalisés du Lot 3a ;
* le PDF génois synthétique sur les champs normalisés du Lot 3a.

Le traitement est 100 % local et déterministe (gabarits pdfplumber, aucune
API externe). Un manifeste JSON permet d'ajouter, côté entreprise, des
échantillons d'or sans committer leurs PDF :

.. code-block:: json

    {"echantillons": [
      {"nom": "EXEMPLE", "pdf": "chemin/relatif.pdf",
       "attendu": {"modele_bateau_brut": "…", "dimensions.slu_m": 6.6}}
    ]}

Codes de retour : 0 = conforme (ou extraction sans attendu réussie),
1 = au moins une évaluation sous le seuil, 2 = erreur d'entrée/extraction.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from seamtech_search.extractions_voile import (  # noqa: E402
    VERSION_EXTRACTEUR_VOILE,
    enregistrer_extraction_voile,
    evaluer_extraction_or,
    extraire_voile_avec_fiche,
)
from seamtech_search.fiches.verite_7792 import VERITE_7792  # noqa: E402

ATTENDU_7792_LOT3A: dict[str, object] = {
    "code_fiche": "7792-SO",
    "type_voile": "Spi Asymétrique",
    "gamme": "Medium Régate",
    "modele_bateau_brut": "29er (15')",
    "taille_bateau_brute": "15'",
    "dimensions.slu_m": 6.6,
    "dimensions.sle_m": 5.5,
    "dimensions.sf_m": 3.08,
    "dimensions.shw_m": 3.14,
    "dimensions.spa_m2": 15.71,
    "dimensions.tetiere_m": 0.03,
    "dimensions.poids_kg": 0.7,
    "materiaux.designations_contient": "Monofilm K903",
    "gabarit_code": "FICHE_PORTANT_V1",
    "gabarit_version": 2,
    "statut_extraction": "complete",
    "statut_plausibilite": "plausible",
}

ATTENDU_GENOIS_LOT3A: dict[str, object] = {
    "code_fiche": "0901-MM",
    "type_voile": "Génois",
    "modele_bateau_brut": "Sun 2000",
    "dimensions.slu_m": 8.2,
    "dimensions.sf_m": 3.9,
    "dimensions.spa_m2": 18.2,
    "materiaux.principal": "Dacron 260",
    "gabarit_code": "FICHE_GENOIS_V1",
    "gabarit_version": 1,
    "statut_extraction": "complete",
    "statut_plausibilite": "plausible",
}


@dataclass(frozen=True)
class EchantillonOr:
    nom: str
    chemin: Path
    attendu_lot3a: dict[str, object] | None = None
    attendu_fiche: dict[str, object] | None = None


def _seuil(valeur: str) -> float:
    try:
        seuil = float(valeur.replace(",", "."))
    except ValueError as erreur:
        raise argparse.ArgumentTypeError(f"seuil invalide : {valeur!r}") from erreur
    if not 0.0 <= seuil <= 1.0:
        raise argparse.ArgumentTypeError("le seuil doit être compris entre 0 et 1")
    return seuil


def _construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Mesure l'extraction Lot 3a sur des échantillons d'or locaux."
    )
    parseur.add_argument("--pdf", action="append", default=[], help="PDF à extraire sans attendu (répétable)")
    parseur.add_argument("--manifeste", help="Manifeste JSON d'échantillons d'or")
    parseur.add_argument("--gabarit", help="Code de gabarit à forcer (par défaut : détection + filet)")
    parseur.add_argument("--format", choices=("texte", "json"), default="texte")
    parseur.add_argument("--seuil", type=_seuil, default=1.0, help="Taux minimal, entre 0 et 1 (défaut : 1)")
    parseur.add_argument("--sortie", help="Fichier recevant aussi le rapport rendu")
    parseur.add_argument(
        "--enregistrer",
        action="store_true",
        help="Enregistrer aussi les extractions dans sail_extractions (explicite, jamais par défaut)",
    )
    parseur.add_argument("--database-url", help="URL PostgreSQL pour --enregistrer")
    parseur.add_argument(
        "--database-path",
        default="/tmp/seamtech-extraction-or.db",
        help="Chemin SQLite technique du SearchIndex quand PostgreSQL est utilisé",
    )
    return parseur


def _echantillons_par_defaut() -> list[EchantillonOr]:
    return [
        EchantillonOr(
            nom="7792-SO",
            chemin=RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf",
            attendu_lot3a=dict(ATTENDU_7792_LOT3A),
            attendu_fiche=dict(VERITE_7792),
        ),
        EchantillonOr(
            nom="GENOIS-SYNTHETIQUE",
            chemin=RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf",
            attendu_lot3a=dict(ATTENDU_GENOIS_LOT3A),
        ),
    ]


def _charger_manifeste(chemin_manifeste: Path) -> list[EchantillonOr]:
    donnees = json.loads(chemin_manifeste.read_text(encoding="utf-8"))
    elements = donnees.get("echantillons") if isinstance(donnees, dict) else donnees
    if not isinstance(elements, list) or not elements:
        raise ValueError("le manifeste doit contenir une liste non vide d'échantillons")

    echantillons: list[EchantillonOr] = []
    for position, element in enumerate(elements, start=1):
        if not isinstance(element, dict) or not element.get("pdf"):
            raise ValueError(f"échantillon #{position} : clé « pdf » requise")
        chemin = Path(str(element["pdf"]))
        if not chemin.is_absolute():
            chemin = chemin_manifeste.parent / chemin
        attendu_lot3a = element.get("attendu", element.get("attendu_lot3a"))
        attendu_fiche = element.get("attendu_fiche")
        if attendu_lot3a is not None and not isinstance(attendu_lot3a, dict):
            raise ValueError(f"échantillon #{position} : « attendu » doit être un objet JSON")
        if attendu_fiche is not None and not isinstance(attendu_fiche, dict):
            raise ValueError(f"échantillon #{position} : « attendu_fiche » doit être un objet JSON")
        echantillons.append(
            EchantillonOr(
                nom=str(element.get("nom") or chemin.stem.upper()),
                chemin=chemin,
                attendu_lot3a=dict(attendu_lot3a) if attendu_lot3a is not None else None,
                attendu_fiche=dict(attendu_fiche) if attendu_fiche is not None else None,
            )
        )
    return echantillons


def _index_pour_enregistrement(arguments: argparse.Namespace) -> Any:  # noqa: ANN401
    url = (
        arguments.database_url
        or os.environ.get("SEAMTECH_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("SEAMTECH_TEST_DATABASE_URL")
    )
    if not url:
        raise ValueError(
            "--enregistrer exige --database-url ou SEAMTECH_DATABASE_URL/DATABASE_URL"
        )
    from seamtech_search.indexer import SearchIndex

    index = SearchIndex(Path(arguments.database_path), url)
    if not getattr(index, "is_postgres", False):
        raise ValueError("--enregistrer exige une URL PostgreSQL")
    index.initialize()
    index.run_migrations()
    return index


def _lier_par_code_si_fiche_existante(index: Any, extraction: Any) -> Any:  # noqa: ANN401
    """Lie une extraction autonome à une fiche existante de même code.

    Aucune fiche n'est créée : le rapport d'or mesure/enregistre une
    extraction, il ne contourne ni RG3 ni RG11.
    """
    if not extraction.code_fiche or extraction.id_fiche is not None:
        return extraction
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT id_fiche FROM fiche WHERE code = %s", (extraction.code_fiche,))
            ligne = curseur.fetchone()
    if ligne is None:
        return extraction
    return extraction.model_copy(update={"id_fiche": int(ligne[0]), "cle_idempotence": ""})


def _rendre_texte(rapport: dict[str, Any]) -> str:
    lignes = [
        f"Rapport d'or Lot 3a — extracteur {rapport['version_extracteur']}",
        f"Seuil : {float(rapport['seuil']):.1%} ; conforme : {'oui' if rapport['conforme'] else 'non'}",
    ]
    for echantillon in rapport["echantillons"]:
        if echantillon.get("erreur"):
            lignes.append(f"- {echantillon['nom']} ({echantillon['nom_fichier']}) : ERREUR {echantillon['erreur']}")
            continue
        extraction = echantillon["extraction"]
        morceaux = [
            f"statut {extraction['statut_extraction']}/{extraction['statut_plausibilite']}",
            f"modèle brut {extraction['modele_bateau_brut'] or '—'}",
            f"type {extraction['type_voile'] or '—'}",
            f"SLU {extraction['slu_m'] if extraction['slu_m'] is not None else '—'} m",
            f"SF {extraction['sf_m'] if extraction['sf_m'] is not None else '—'} m",
        ]
        for etiquette, cle in (("Lot 3a", "evaluation_lot3a"), ("fiche", "evaluation_fiche")):
            evaluation = echantillon.get(cle)
            if evaluation is not None:
                morceaux.append(
                    f"{etiquette} {evaluation['corrects']}/{evaluation['total']} "
                    f"({float(evaluation['taux']):.1%})"
                )
        lignes.append(f"- {echantillon['nom']} ({echantillon['nom_fichier']}) : " + " ; ".join(morceaux))
        for cle in ("evaluation_lot3a", "evaluation_fiche"):
            evaluation = echantillon.get(cle)
            if not evaluation:
                continue
            for detail in evaluation["details"]:
                if not detail["correct"]:
                    lignes.append(
                        f"  écart {detail['champ']} : attendu {detail['attendu']!r}, obtenu {detail['obtenu']!r}"
                    )
    resume = rapport["resume"]
    taux = "—" if resume["taux"] is None else f"{float(resume['taux']):.1%}"
    lignes.append(
        f"Résumé : {resume['corrects']}/{resume['total_champs']} champs corrects ({taux}), "
        f"{resume['erreurs']} erreur(s)."
    )
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    arguments = _construire_parseur().parse_args(argv)

    index = None
    rapport: dict[str, Any]
    try:
        echantillons: list[EchantillonOr] = []
        if arguments.manifeste:
            echantillons.extend(_charger_manifeste(Path(arguments.manifeste)))
        for chemin_pdf in arguments.pdf:
            chemin = Path(chemin_pdf)
            echantillons.append(EchantillonOr(nom=chemin.stem.upper(), chemin=chemin))
        if not echantillons:
            echantillons = _echantillons_par_defaut()

        if arguments.enregistrer:
            index = _index_pour_enregistrement(arguments)

        rapports_echantillons: list[dict[str, Any]] = []
        total = 0
        corrects = 0
        erreurs = 0
        evaluations_conformes = True

        for echantillon in echantillons:
            base: dict[str, Any] = {
                "nom": echantillon.nom,
                "nom_fichier": echantillon.chemin.name,
            }
            try:
                fiche, extraction = extraire_voile_avec_fiche(
                    echantillon.chemin,
                    gabarit_code=arguments.gabarit,
                )
                id_extraction = None
                if index is not None:
                    extraction_a_enregistrer = _lier_par_code_si_fiche_existante(index, extraction)
                    id_extraction = enregistrer_extraction_voile(index, extraction_a_enregistrer)

                evaluation_lot3a = (
                    evaluer_extraction_or(extraction, echantillon.attendu_lot3a)
                    if echantillon.attendu_lot3a is not None
                    else None
                )
                evaluation_fiche = (
                    evaluer_extraction_or(extraction, echantillon.attendu_fiche, fiche=fiche)
                    if echantillon.attendu_fiche is not None
                    else None
                )
                for evaluation in (evaluation_lot3a, evaluation_fiche):
                    if evaluation is None:
                        continue
                    total += int(evaluation["total"])
                    corrects += int(evaluation["corrects"])
                    if evaluation["taux"] is None or float(evaluation["taux"]) < arguments.seuil:
                        evaluations_conformes = False

                rapports_echantillons.append(
                    {
                        **base,
                        "erreur": None,
                        "id_extraction": id_extraction,
                        "extraction": extraction.model_dump(mode="json"),
                        "evaluation_lot3a": evaluation_lot3a,
                        "evaluation_fiche": evaluation_fiche,
                    }
                )
            except Exception as erreur:  # le rapport doit dire l'échec, pas le masquer
                erreurs += 1
                rapports_echantillons.append(
                    {
                        **base,
                        "erreur": f"{type(erreur).__name__}: {erreur}",
                        "id_extraction": None,
                        "extraction": None,
                        "evaluation_lot3a": None,
                        "evaluation_fiche": None,
                    }
                )

        conforme = erreurs == 0 and evaluations_conformes
        rapport = {
            "version_extracteur": VERSION_EXTRACTEUR_VOILE,
            "seuil": arguments.seuil,
            "conforme": conforme,
            "resume": {
                "echantillons": len(rapports_echantillons),
                "total_champs": total,
                "corrects": corrects,
                "taux": round(corrects / total, 4) if total else None,
                "erreurs": erreurs,
            },
            "echantillons": rapports_echantillons,
        }
        code_retour = 2 if erreurs else 0 if conforme else 1
    except Exception as erreur:
        rapport = {
            "version_extracteur": VERSION_EXTRACTEUR_VOILE,
            "seuil": arguments.seuil,
            "conforme": False,
            "resume": {
                "echantillons": 0,
                "total_champs": 0,
                "corrects": 0,
                "taux": None,
                "erreurs": 1,
            },
            "echantillons": [],
            "erreur": f"{type(erreur).__name__}: {erreur}",
        }
        code_retour = 2
    finally:
        if index is not None:
            index.close()

    rendu = (
        json.dumps(rapport, ensure_ascii=False, indent=2)
        if arguments.format == "json"
        else _rendre_texte(rapport)
    )
    print(rendu)
    if arguments.sortie:
        destination = Path(arguments.sortie)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendu + "\n", encoding="utf-8")
    return code_retour


if __name__ == "__main__":
    raise SystemExit(main())
