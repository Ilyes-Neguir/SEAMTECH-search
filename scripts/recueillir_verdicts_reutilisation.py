#!/usr/bin/env python3
"""Lot 19, priorité 2 — recueil de verdicts humains sur la réutilisation.

Le mécanisme de confiance (``confiance_reutilisation.py``) n'a jamais été
confronté à un jugement humain réel. Le seuil de 75 % n'est pas une valeur
mesurée : c'est une valeur choisie. Ce script fabrique le dispositif qui
permettra de la mesurer — il ne la mesure pas à votre place.

Le point qui n'est pas négociable : **l'humain ne doit pas voir le score.**
Montrer « 82 % — cette voile vous semble-t-elle réutilisable ? » ne mesure
rien qu'une docilité : l'approbateur répète la machine, et le seuil qui en
sort ne fait que retrouver celui qu'on lui a soufflé. Le jugement doit porter
sur les MESURES (la candidate contre la population), jamais sur l'évaluation
que la machine en a déjà faite.

D'où deux fichiers, et un seul circule :

* ``<sortie>.csv`` — AVEUGLE. Les mesures brutes, sans score, sans verdict,
  sans seuil. C'est celui qu'on donne à l'approbateur.
* ``<sortie>.csv.cle.json`` — les scores, rangés par identifiant opaque. Il
  reste sur la machine et NE CIRCULE PAS : c'est lui qui permet, au retour,
  de croiser le verdict humain avec ce que la machine avait calculé.

Combien de verdicts avant de pouvoir bouger le seuil
----------------------------------------------------
Règle retenue, et elle est pragmatique et non un théorème :

* **30 verdicts au minimum**, dont **au moins 10 dans chacune des deux classes
  qui tranchent** (``reutilisable`` et ``non_reutilisable``). « incertain »
  n'éclaire pas le seuil : il dit que le cas est ambigu, pas de quel côté il
  tombe.
* Et il faut que les classes se SÉPARENT : si le meilleur seuil laisse encore
  plus de 10 % du corpus d'échantillonnage du mauvais côté, ajouter des
  verdicts n'y fera rien — ce n'est plus un problème de seuil, c'est le
  descripteur qui ne porte pas l'information.

En dessous de ces 30/10, le script refuse de proposer quoi que ce soit et le
dit. **Les seuils actuels restent strictement inchangus** : sous 75 %, le
verdict est ``a_examiner``, il n'y a PAS de verdict automatique positif, et
l'approbateur décide lui-même — ce comportement est garanti par l'épreuve
``test_seuil_alerte_inaltere`` et ne bougera pas tant que le calibrage n'est
pas fait.

Usage
-----
.. code-block:: shell

    export SEAMTECH_DATABASE_URL=postgresql://…
    python scripts/recueillir_verdicts_reutilisation.py etat
    python scripts/recueillir_verdicts_reutilisation.py exporter --sortie verdicts.csv
    # … l'approbateur remplit la colonne « verdict_humain » …
    python scripts/recueillir_verdicts_reutilisation.py importer verdicts.csv

Codes de sortie : 0 = OK, 1 = constat sans proposition (pas assez de preuves),
2 = erreur d'entrée.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Verdicts que l'humain peut porter. Deux seulement informent le seuil.
VERDICTS_HUMAINS = ("reutilisable", "non_reutilisable", "incertain")
#: Verdicts qui servent réellement à placer le seuil.
VERDICTS_TRANCHANTS = ("reutilisable", "non_reutilisable")

#: Registre des verdicts. Sans lui, rien ne s'accumule : chaque import
#: repartait de zéro et « combien de verdicts ? » restait sans réponse — le
#: compte était affiché en dur à 0. C'est le défaut corrigé au Lot 20.
FICHIER_VERDICTS = Path(__file__).resolve().parent.parent / "donnees" / "etape0" / "verdicts_reutilisation.json"

MIN_VERDICTS_TOTAL = 30
MIN_VERDICTS_PAR_CLASSE = 10
#: Au-delà, ce n'est plus un seuil à régler : c'est le descripteur qui ne
#: sépare pas les deux classes.
MAX_ERREURS_RELATIVES = 0.10

_COLONNES_CSV = (
    "id_aveugle",
    "type_voile",
    "n_population",
    "mesures_candidat",
    "mesures_population",
    "materiaux_candidat",
    "materiaux_population",
    "verdict_humain",
)


def _index() -> Any:
    """Index PostgreSQL de l'atelier.

    Volontairement SANS ``AppConfig.load()`` : ce script n'a besoin d'aucune
    configuration de l'atelier, seulement de l'URL. Exiger ``config.json`` le
    rendrait inutilisable précisément là où on en a besoin — sur un poste où
    l'on n'a installé que la base. Le chemin SQLite passé à ``SearchIndex``
    est inerte dès qu'une URL PostgreSQL est fournie.
    """
    from seamtech_search.indexer import SearchIndex

    return SearchIndex(
        Path(os.environ.get("TMPDIR", "/tmp")) / "recueillir_verdicts.db",
        os.environ["SEAMTECH_DATABASE_URL"],
    )


# ---------------------------------------------------------------------------
# Aveuglement
# ---------------------------------------------------------------------------


def purger(objet: Any) -> Any:
    """Retire récursivement tout ce qui révèle une évaluation de la machine.

    Sont écartés : les clés contenant ``score``, ``z_robuste``, ``seuil``,
    ``verdict``, ``heterogene``. Ce qui reste, ce sont les MESURES : c'est
    exactement ce sur quoi l'approbateur doit se prononcer.
    """
    if isinstance(objet, dict):
        return {
            cle: purger(valeur)
            for cle, valeur in objet.items()
            if not any(marqueur in cle.lower() for marqueur in ("score", "z_robuste", "seuil", "verdict", "heterogene"))
        }
    if isinstance(objet, list):
        return [purger(element) for element in objet]
    return objet


def _formater_mesures(dimensions: list[dict[str, Any]], candidat: bool) -> str:
    """Ligne lisible : la valeur de la candidate, ou le résumé de la population."""
    morceaux: list[str] = []
    for dimension in dimensions:
        champ = dimension.get("champ", "?")
        if candidat:
            valeur = dimension.get("valeur_candidate")
            morceaux.append(f"{champ}={valeur}" if valeur is not None else f"{champ}=?")
            continue
        if not dimension.get("n"):
            continue
        mediane = dimension.get("mediane")
        q1, q3 = dimension.get("q1"), dimension.get("q3")
        morceaux.append(f"{champ}: médiane {mediane} (Q1 {q1} – Q3 {q3}, n={dimension.get('n')})")
    return " ; ".join(morceaux)


def _id_aveugle(id_extraction: int, sel: str) -> str:
    """Identifiant opaque et stable : on ne transmet pas nos identifiants internes."""
    return hashlib.sha256(f"{sel}|{id_extraction}".encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Échantillonnage
# ---------------------------------------------------------------------------


def construire_echantillon(index: Any, *, limite: int = 40) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Comparaisons AVEUGLES + la clé (scores) qui n'est PAS transmise.

    Retourne ``(lignes_aveugles, cle)``. Aucune écriture en base : l'évaluation
    est calculée puis jetée, seul le score est conservé dans la clé.
    """
    from seamtech_search.confiance_reutilisation import (
        _charger_population,
        _extraction_depuis_ligne,
        evaluer_population,
    )
    from seamtech_search.extractions_voile import _COLONNES_EXTRACTION_VOILE

    colonnes = ", ".join(_COLONNES_EXTRACTION_VOILE)
    sel = os.urandom(16).hex()

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                f"SELECT {colonnes} FROM sail_extractions "
                "WHERE boat_model_id IS NOT NULL AND statut_plausibilite <> 'implausible' "
                "ORDER BY id_extraction ASC"
            )
            candidates = [_extraction_depuis_ligne(tuple(ligne)) for ligne in curseur.fetchall()]

            lignes: list[dict[str, Any]] = []
            cle: dict[str, Any] = {"sel": sel, "entrees": {}}
            for candidat in candidates:
                if len(lignes) >= limite:
                    break
                population = _charger_population(
                    curseur,
                    candidat.boat_model_id,
                    candidat.type_voile,
                    exclure_id=candidat.id_extraction,
                )
                if not population:
                    continue
                resultat = evaluer_population(candidat, population)
                justification = purger(resultat.get("justification", {}))
                identifiant = _id_aveugle(int(candidat.id_extraction), sel)

                dimensions = justification.get("dimensions", [])
                materiaux = justification.get("materiaux", {})
                lignes.append(
                    {
                        "id_aveugle": identifiant,
                        "type_voile": candidat.type_voile or "",
                        "n_population": resultat.get("n_echantillons", 0),
                        "mesures_candidat": _formater_mesures(dimensions, candidat=True),
                        "mesures_population": _formater_mesures(dimensions, candidat=False),
                        "materiaux_candidat": json.dumps(materiaux.get("candidat"), ensure_ascii=False)
                        if isinstance(materiaux, dict)
                        else "",
                        "materiaux_population": json.dumps(materiaux.get("population"), ensure_ascii=False)
                        if isinstance(materiaux, dict)
                        else "",
                        "verdict_humain": "",
                    }
                )
                cle["entrees"][identifiant] = {
                    "id_extraction": int(candidat.id_extraction),
                    "score": resultat.get("score"),
                    "verdict_machine": resultat.get("verdict"),
                }

    return lignes, cle


def ecrire_echantillon(lignes: list[dict[str, Any]], cle: dict[str, Any], sortie: str) -> None:
    """Écrit le CSV aveugle et la clé à côté (jamais dans le même fichier)."""
    with open(sortie, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_COLONNES_CSV), delimiter=";")
        writer.writeheader()
        writer.writerows(lignes)
    with open(f"{sortie}.cle.json", "w", encoding="utf-8") as handle:
        json.dump(cle, handle, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Dépouillement
# ---------------------------------------------------------------------------


def depouiller(lignes: list[dict[str, Any]], cle: dict[str, Any]) -> dict[str, Any]:
    """Croise verdict humain et score machine. Ne modifie aucun seuil."""
    entrees = cle.get("entrees", {})
    couples: list[dict[str, Any]] = []
    for ligne in lignes:
        verdict = (ligne.get("verdict_humain") or "").strip().lower()
        if verdict not in VERDICTS_HUMAINS:
            continue
        entree = entrees.get(ligne.get("id_aveugle"))
        if not entree or entree.get("score") is None:
            continue
        couples.append(
            {
                "id_aveugle": ligne.get("id_aveugle"),
                "id_extraction": entree.get("id_extraction"),
                "score": float(entree["score"]),
                "verdict_humain": verdict,
                "verdict_machine": entree.get("verdict_machine"),
            }
        )

    compte: dict[str, int] = {verdict: 0 for verdict in VERDICTS_HUMAINS}
    for couple in couples:
        compte[couple["verdict_humain"]] += 1

    tranchants = [c for c in couples if c["verdict_humain"] in VERDICTS_TRANCHANTS]
    seuil_propose: float | None = None
    erreurs: int | None = None
    if (
        len(couples) >= MIN_VERDICTS_TOTAL
        and all(compte[v] >= MIN_VERDICTS_PAR_CLASSE for v in VERDICTS_TRANCHANTS)
        and tranchants
    ):
        seuil_propose, erreurs = _meilleur_seuil(tranchants)

    return {
        "verdicts_recueillis": len(couples),
        "par_classe": compte,
        "seuil_actuel": _seuil_actuel(),
        "seuil_propose": seuil_propose,
        "erreurs_au_seuil_propose": erreurs,
        "pret": seuil_propose is not None
        and erreurs is not None
        and erreurs <= MAX_ERREURS_RELATIVES * len(tranchants),
        "minimum_requis": {
            "total": MIN_VERDICTS_TOTAL,
            "par_classe_tranchante": MIN_VERDICTS_PAR_CLASSE,
        },
        "detail": couples,
    }


def _meilleur_seuil(tranchants: list[dict[str, Any]]) -> tuple[float, int]:
    """Seuil qui sépare le mieux « réutilisable » de « non réutilisable ».

    On note « erreur » un réutilisable sous le seuil ou un non-réutilisable
    au-dessus. Le seuil retenu est celui qui en compte le moins.
    """
    scores = sorted({couple["score"] for couple in tranchants})
    meilleur: tuple[float, int] | None = None
    for seuil in scores:
        erreurs = sum(
            1 for couple in tranchants if (couple["verdict_humain"] == "reutilisable") != (couple["score"] >= seuil)
        )
        if meilleur is None or erreurs < meilleur[1]:
            meilleur = (seuil, erreurs)
    return meilleur or (0.0, len(tranchants))


def _seuil_actuel() -> int:
    """Le seuil en vigueur — lu, jamais modifié."""
    from seamtech_search.confiance_reutilisation import CONFIANCE_SEUIL_ALERTE

    return int(CONFIANCE_SEUIL_ALERTE)


# ---------------------------------------------------------------------------
# Le registre : ce qui s'accumule
# ---------------------------------------------------------------------------


def charger_verdicts(chemin: Path = FICHIER_VERDICTS) -> list[dict[str, Any]]:
    """Les verdicts déjà recueillis ; liste vide s'il n'y en a aucun."""
    if not chemin.exists():
        return []
    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    return list(contenu.get("verdicts") or [])


def enregistrer_verdicts(
    couples: list[dict[str, Any]],
    *,
    chemin: Path = FICHIER_VERDICTS,
    operateur: str = "non précisé",
) -> tuple[int, int]:
    """Ajoute les verdicts au registre. Rend ``(nouveaux, mis_a_jour)``.

    Une même extraction peut être soumise deux fois à l'appréciation — d'où
    l'identifiant aveugle changeant à chaque export, le sel étant différent. La
    clé stable est donc ``id_extraction``. L'ancien verdict n'est pas écrasé
    dans le journal : il reste, et le décompte retient le PLUS RÉCENT. On peut
    ainsi recompter sans perdre l'historique.
    """
    existants = charger_verdicts(chemin)
    connus = {v.get("id_extraction"): v for v in existants}
    maintenant = datetime.now(timezone.utc).isoformat(timespec="seconds")

    nouveaux = 0
    maj = 0
    for couple in couples:
        cle = couple.get("id_extraction")
        if cle is None:
            continue
        entree = {
            "id_aveugle": couple.get("id_aveugle"),
            "id_extraction": cle,
            "score": couple.get("score"),
            "verdict_humain": couple.get("verdict_humain"),
            "verdict_machine": couple.get("verdict_machine"),
            "operateur": operateur,
            "recueilli_le": maintenant,
        }
        if cle in connus:
            maj += 1
        else:
            nouveaux += 1
        existants.append(entree)

    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        json.dumps({"version": 1, "verdicts": existants}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return nouveaux, maj


def compter_verdicts(verdicts: list[dict[str, Any]]) -> dict[str, int]:
    """Décompte par classe, en retenant le verdict le PLUS RÉCENT par extraction."""
    dernier: dict[Any, dict[str, Any]] = {}
    for v in verdicts:
        cle = v.get("id_extraction")
        if cle is None:
            continue
        if cle not in dernier or str(v.get("recueilli_le", "")) >= str(dernier[cle].get("recueilli_le", "")):
            dernier[cle] = v
    compte = {verdict: 0 for verdict in VERDICTS_HUMAINS}
    for v in dernier.values():
        verdict = v.get("verdict_humain")
        if verdict in compte:
            compte[verdict] += 1
    return compte


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sous = parser.add_subparsers(dest="commande", required=True)

    p_etat = sous.add_parser("etat", help="combien de verdicts recueillis à ce jour")
    p_etat.add_argument("--registre", type=Path, default=FICHIER_VERDICTS)
    p_export = sous.add_parser("exporter", help="produit un échantillon aveugle")
    p_export.add_argument("--sortie", default="verdicts_reutilisation.csv")
    p_export.add_argument("-n", "--limite", type=int, default=40)
    p_import = sous.add_parser("importer", help="dépouille un échantillon rempli")
    p_import.add_argument("fichier")
    p_import.add_argument("--registre", type=Path, default=FICHIER_VERDICTS)
    p_import.add_argument("--operateur", default="non précisé")

    arguments = parser.parse_args(argv)

    if arguments.commande == "etat":
        chemin = Path(getattr(arguments, "registre", FICHIER_VERDICTS))
        verdicts = charger_verdicts(chemin)
        compte = compter_verdicts(verdicts)
        tranchants = {v: compte[v] for v in VERDICTS_TRANCHANTS}
        total = sum(compte.values())
        manque_total = max(0, MIN_VERDICTS_TOTAL - total)
        manque_classes = {v: max(0, MIN_VERDICTS_PAR_CLASSE - n) for v, n in tranchants.items()}
        print(f"Verdicts humains recueillis à ce jour : {total}")
        for verdict in VERDICTS_HUMAINS:
            print(f"    {verdict:<18} {compte[verdict]}")
        print(f"Seuil en vigueur (inchangé)         : {_seuil_actuel()} %")
        print(
            f"Nécessaire pour proposer un seuil   : {MIN_VERDICTS_TOTAL} verdicts, "
            f"dont {MIN_VERDICTS_PAR_CLASSE} dans chacune des classes "
            "« reutilisable » et « non_reutilisable »"
        )
        if manque_total or any(manque_classes.values()):
            detail = ", ".join(f"{v} : encore {n}" for v, n in manque_classes.items() if n)
            print(
                f"Il manque                           : {manque_total} verdict(s)" + (f" ; {detail}" if detail else "")
            )
        else:
            print("Le minimum est atteint : un seuil peut être proposé.")
        print()
        print("Aucun seuil ne sera modifié sans ces verdicts. C'est voulu.")
        return 0 if not manque_total and not any(manque_classes.values()) else 1

    url = os.environ.get("SEAMTECH_DATABASE_URL", "")
    if not url:
        print("SEAMTECH_DATABASE_URL n'est pas définie — je refuse de deviner la base.", file=sys.stderr)
        return 2

    if arguments.commande == "exporter":
        try:
            index = _index()
            lignes, cle = construire_echantillon(index, limite=arguments.limite)
        except Exception as exc:
            print(f"Connexion impossible : {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        if not lignes:
            print("Aucune comparaison évaluable : population de référence insuffisante.", file=sys.stderr)
            return 1
        ecrire_echantillon(lignes, cle, arguments.sortie)
        print(f"{len(lignes)} comparaisons AVEUGLES écrites dans {arguments.sortie}")
        print(f"Les scores sont dans {arguments.sortie}.cle.json — ce fichier ne circule PAS.")
        print("Remplissez la colonne « verdict_humain » (reutilisable / non_reutilisable / incertain).")
        return 0

    # importer
    try:
        with open(arguments.fichier, encoding="utf-8", newline="") as handle:
            lignes = list(csv.DictReader(handle, delimiter=";"))
        with open(f"{arguments.fichier}.cle.json", encoding="utf-8") as handle:
            cle = json.load(handle)
    except OSError as exc:
        print(f"Fichier illisible : {exc}", file=sys.stderr)
        return 2

    rapport = depouiller(lignes, cle)
    if rapport["detail"]:
        nouveaux, maj = enregistrer_verdicts(
            rapport["detail"], chemin=arguments.registre, operateur=arguments.operateur
        )
        rapport["registre"] = {
            "chemin": str(arguments.registre),
            "nouveaux": nouveaux,
            "mis_a_jour": maj,
            "cumul": compter_verdicts(charger_verdicts(arguments.registre)),
        }
    print(json.dumps({k: v for k, v in rapport.items() if k != "detail"}, ensure_ascii=False, indent=2))
    if not rapport["pret"]:
        print(
            "\n Pas assez de preuves pour proposer un seuil — les seuils actuels restent strictement inchangés.",
            file=sys.stderr,
        )
        return 1
    print(
        f"\nSeuil proposé : {rapport['seuil_propose']} % "
        f"({rapport['erreurs_au_seuil_propose']} écart(s) sur l'échantillon)."
    )
    print("Ce script NE MODIFIE RIEN : la décision et l'écriture vous reviennent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
