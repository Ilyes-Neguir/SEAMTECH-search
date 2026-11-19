#!/usr/bin/env python3
"""Audit des axes mesurés, par l'historique git de l'accumulateur.

Le Lot 19 a corrigé le défaut ``ajouter`` pour l'avenir : un axe absent ne
l'efface plus. Restait à savoir ce que l'ancien comportement avait DÉJÀ
cassé — et à le réparer, ou à le signaler faute de source.

La source existe, et c'est git : ``donnees/etape0/accumulateur.json`` est
versionné depuis le 2026-11-07, treize états. Chaque état porte la valeur de
chaque axe pour chaque fiche. Un axe qui passe d'une valeur à vide entre deux
états consécutifs est un effacement, daté, identifié, et cité par son commit.

Deux précautions, parce qu'un audit qui surestime sa portée est pire qu'un
audit qui avoue sa borne :

* **Les identifiants ont été rebâtis deux fois.** L'identifiant d'une fiche est
  ``sha256(sel|chemin)``. Le sel a changé aux commits ``92352b7`` et
  ``0fb7622`` : toutes les fiches ont alors changé d'identifiant. Suivre un
  identifiant à travers un rebasement est impossible — les fiches « disparues »
  de ces deux commits ne sont donc PAS des pertes, et l'outil les distingue
  explicitement des effacements. La fenêtre où le suivi par fiche est fiable
  commence au premier état au sel stabilisé.
* **Un effacement n'est réparé que s'il est attesté.** La valeur restaurée
  n'est jamais devinée : elle est relue dans l'état git qui la portait, et le
  commit est cité comme source.

Sous-commandes ::

    python scripts/auditer_axes_effaces_corpus.py auditer [--json]
    python scripts/auditer_axes_effaces_corpus.py reparer --id ... --axe ... \\
        --source <sha> [--operateur ...] [--motif ...]
    python scripts/auditer_axes_effaces_corpus.py journal

Codes de retour de ``auditer`` : 0 = rien à réparer, 1 = effacements en
souffrance.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE_DEPOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE_DEPOT))

from scripts.etape0_corpus import AXES_MESURES, FICHIER_ACCUMULATEUR, charger_accumulateur, sauvegarder_accumulateur  # noqa: E402

JOURNAL_REPARATIONS = RACINE_DEPOT / "donnees" / "etape0" / "reparations_axes.json"


# ---------------------------------------------------------------------------
# Le cœur : analyser une suite d'états. Pure, donc éprouvable sans git.
# ---------------------------------------------------------------------------


def analyser_historique(etats: list[tuple[str, dict]]) -> dict:
    """Compare des états successifs de l'accumulateur.

    ``etats`` est une liste de ``(sha, accumulateur)`` **du plus ancien au plus
    récent**. Rend un rapport complet : comptages, rebasements de sel,
    effacements, réparations, et ce qui reste en souffrance.

    La détection ne suit pas deux états à la fois : elle construit la
    chronologie de chaque couple ``(fiche, axe)``, puis lit cette chronologie.
    C'est ce qui permet de dire « effacé au rang 7, réparé au rang 9 » sans
    rien supposer sur les états intermédiaires.
    """
    comptages: list[dict] = []
    rebasements: list[dict] = []
    effacements: list[dict] = []
    restaurations: list[dict] = []

    # Chronologie de chaque couple (fiche, axe) : [(rang, sha, valeur), ...]
    chrono: dict[tuple[str, str], list[tuple[int, str, object]]] = {}
    # Rangs où chaque fiche est présente, et le sel de chaque état.
    presence: dict[str, set[int]] = {}
    sels: list[str] = []
    rangs_par_id: dict[str, list[int]] = {}

    for rang, (sha, acc) in enumerate(etats):
        fiches = acc.get("fiches") or []
        sels.append(str(acc.get("sel")))
        comptages.append(
            {
                "sha": sha,
                "total": len(fiches),
                "axes": {axe: sum(1 for f in fiches if f.get(axe)) for axe in AXES_MESURES},
            }
        )
        for fiche in fiches:
            id_fiche = fiche["id"]
            presence.setdefault(id_fiche, set()).add(rang)
            rangs_par_id.setdefault(id_fiche, []).append(rang)
            for axe in AXES_MESURES:
                chrono.setdefault((id_fiche, axe), []).append((rang, sha, fiche.get(axe)))

    # Rebasements : le sel change, donc les identifiants aussi.
    rebasement_rangs = {rang for rang in range(1, len(etats)) if sels[rang] != sels[rang - 1]}
    for rang in sorted(rebasement_rangs):
        avant = {f["id"] for f in (etats[rang - 1][1].get("fiches") or [])}
        apres = {f["id"] for f in (etats[rang][1].get("fiches") or [])}
        rebasements.append(
            {
                "sha": etats[rang][0],
                "sel_avant": sels[rang - 1][:8],
                "sel_apres": sels[rang][:8],
                "disparus": len(avant - apres),
                "nouveaux": len(apres - avant),
            }
        )

    dernier = len(etats) - 1

    # Fiches qui quittent le corpus — hors rebasement, sinon c'est un faux positif.
    for id_fiche, rangs in rangs_par_id.items():
        for rang in sorted(rangs):
            if rang + 1 <= dernier and (rang + 1) not in presence[id_fiche]:
                if (rang + 1) in rebasement_rangs:
                    continue  # identifiants rebâtis : ce n'est pas une perte
                racine = next(f.get("racine") for f in (etats[rang][1].get("fiches") or []) if f["id"] == id_fiche)
                effacements.append(
                    {
                        "sha": etats[rang + 1][0],
                        "id": id_fiche,
                        "axe": "(fiche entière)",
                        "avant": racine,
                        "racine": racine,
                        "nature": "fiche_disparue",
                        "repare": False,
                    }
                )

    # Effacements et restaurations, lus dans la chronologie.
    for (id_fiche, axe), suite in chrono.items():
        for k in range(1, len(suite)):
            _, sha_ap, v_ap = suite[k]
            _, _, v_av = suite[k - 1]
            if v_av is not None and v_ap is None:
                reparé = any(v is not None for _, _, v in suite[k + 1 :])
                effacements.append(
                    {
                        "sha": sha_ap,
                        "id": id_fiche,
                        "axe": axe,
                        "avant": v_av,
                        "racine": next(
                            (
                                f.get("racine")
                                for f in (etats[suite[k - 1][0]][1].get("fiches") or [])
                                if f["id"] == id_fiche
                            ),
                            None,
                        ),
                        "nature": "axe_efface",
                        "repare": reparé,
                    }
                )
            elif v_av is None and v_ap is not None:
                restaurations.append({"sha": sha_ap, "id": id_fiche, "axe": axe, "apres": v_ap})

    if etats:
        courant = {f["id"]: f for f in (etats[-1][1].get("fiches") or [])}
    else:
        courant = {}
    en_souffrance = [
        e
        for e in effacements
        if e.get("nature") == "axe_efface" and not e["repare"] and courant.get(e["id"], {}).get(e["axe"]) is None
    ]

    return {
        "etats": comptages,
        "rebasements": rebasements,
        "effacements": effacements,
        "restaurations": restaurations,
        "en_souffrance": en_souffrance,
        "total_etats": len(etats),
    }


# ---------------------------------------------------------------------------
# Lecture de l'historique réel
# ---------------------------------------------------------------------------


#: Étiquette de l'état non encore versionné. L'auditer SANS lui ferait
#: paraître « en souffrance » une réparation déjà appliquée mais pas encore
#: committée — ce qui est arrivé au premier essai du Lot 20.
ETAT_ARBRE = "arbre"


def lire_etats_git(chemin: Path) -> list[tuple[str, dict]]:
    """Les états du fichier : les commits, puis l'arbre de travail.

    L'arbre de travail est ajouté comme DERNIER état. Sans lui, l'audit
    décrirait le passé versionné en le faisant passer pour le présent — et une
    réparation appliquée mais non committée resterait signalée en souffrance.
    """
    relatif = chemin.relative_to(RACINE_DEPOT).as_posix()
    shas = subprocess.run(
        ["git", "log", "--format=%h", "--", relatif],
        cwd=RACINE_DEPOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    etats: list[tuple[str, dict]] = []
    for sha in reversed(shas):
        brut = subprocess.run(
            ["git", "show", f"{sha}:{relatif}"],
            cwd=RACINE_DEPOT,
            capture_output=True,
            check=True,
        ).stdout
        etats.append((sha, json.loads(brut.decode("utf-8"))))

    if chemin.exists():
        etats.append((ETAT_ARBRE, charger_accumulateur(chemin)))
    return etats


# ---------------------------------------------------------------------------
# Réparation attestée
# ---------------------------------------------------------------------------


def _journal() -> dict:
    if JOURNAL_REPARATIONS.exists():
        return json.loads(JOURNAL_REPARATIONS.read_text(encoding="utf-8"))
    return {"version": 1, "reparations": []}


def _ecrire_journal(journal: dict) -> None:
    JOURNAL_REPARATIONS.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL_REPARATIONS.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reparer(
    chemin: Path,
    *,
    id_fiche: str,
    axe: str,
    source: str,
    operateur: str = "audit-lot20",
    motif: str = "",
) -> dict:
    """Restaure un axe depuis un état git cité. Ne devine jamais la valeur."""
    if axe not in AXES_MESURES:
        raise ValueError(f"Axe inattendu : {axe!r}. Attendus : {list(AXES_MESURES)}.")

    etats = lire_etats_git(chemin)
    if not any(sha == source for sha, _ in etats):
        raise ValueError(f"{source} n'est pas un état connu de {chemin.name}.")

    etat_source = next(acc for sha, acc in etats if sha == source)
    fiche_source = next((f for f in etat_source["fiches"] if f["id"] == id_fiche), None)
    if fiche_source is None:
        raise ValueError(f"Aucune fiche {id_fiche} dans l'état {source}.")
    valeur = fiche_source.get(axe)
    if valeur is None:
        raise ValueError(
            f"L'état {source} ne porte PAS de valeur pour {axe} sur {id_fiche} : "
            "il ne peut pas servir de source. Rien n'a été écrit."
        )

    acc = charger_accumulateur(chemin)
    fiche = next((f for f in acc["fiches"] if f["id"] == id_fiche), None)
    if fiche is None:
        raise ValueError(f"Aucune fiche {id_fiche} dans le corpus actuel.")
    avant = fiche.get(axe)
    if avant is not None:
        raise ValueError(f"La fiche {id_fiche} porte déjà {axe}={avant!r} : rien à réparer.")

    fiche[axe] = valeur
    sauvegarder_accumulateur(acc, chemin)

    entree = {
        "id": id_fiche,
        "axe": axe,
        "avant": avant,
        "apres": valeur,
        "source_sha": source,
        "source_date": _date_du_commit(source),
        "operateur": operateur,
        "motif": motif or "valeur relue dans un état versionné antérieur",
        "horodatage": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    journal = _journal()
    journal["reparations"].append(entree)
    _ecrire_journal(journal)
    return entree


def _date_du_commit(sha: str) -> str:
    try:
        return subprocess.run(
            ["git", "log", "-1", "--format=%ad", "--date=short", sha],
            cwd=RACINE_DEPOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # pragma: no cover
        return "inconnue"


# ---------------------------------------------------------------------------
# Sortie
# ---------------------------------------------------------------------------


def formater(rapport: dict) -> str:
    lignes = [f"{rapport['total_etats']} états versionnés de l'accumulateur", ""]
    lignes.append(f"{'sha':<9} {'fiches':>6} " + " ".join(f"{a:>10}" for a in AXES_MESURES))
    for e in rapport["etats"]:
        axes = " ".join(f"{e['axes'][a]:>10}" for a in AXES_MESURES)
        lignes.append(f"{e['sha']:<9} {e['total']:>6} {axes}")

    if rapport["rebasements"]:
        lignes += ["", "Sel changé — identifiants rebâtis (PAS des pertes) :"]
        for r in rapport["rebasements"]:
            lignes.append(
                f"  {r['sha']}  {r['sel_avant']} → {r['sel_apres']}  "
                f"{r['disparus']} identifiants remplacés par {r['nouveaux']}"
            )

    lignes += ["", f"Effacements détectés : {len(rapport['effacements'])}"]
    for e in rapport["effacements"]:
        if e.get("nature") == "fiche_disparue":
            lignes.append(f"  {e['sha']}  FICHE DISPARUE id={e['id']} ({e['racine']})")
            continue
        etat = "réparé plus tard" if e["repare"] else "EN SOUFFRANCE"
        lignes.append(f"  {e['sha']}  id={e['id']}  {e['axe']}={e['avant']!r} → vide  ({etat})")

    souffrance = rapport["en_souffrance"]
    lignes += ["", f"En souffrance : {len(souffrance)}"]
    for e in souffrance:
        lignes.append(f"  id={e['id']}  {e['axe']}  (perdu en {e['sha']}, valeur {e['avant']!r})")
    if not souffrance:
        lignes.append("  aucun — tout effacement détecté a été réparé.")
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fichier", type=Path, default=FICHIER_ACCUMULATEUR)
    sous = parser.add_subparsers(dest="commande", required=True)

    p_auditer = sous.add_parser("auditer", help="Comparer les états versionnés.")
    p_auditer.add_argument("--json", action="store_true")

    p_reparer = sous.add_parser("reparer", help="Restaurer un axe depuis un état cité.")
    p_reparer.add_argument("--id", required=True, dest="id_fiche")
    p_reparer.add_argument("--axe", required=True, choices=list(AXES_MESURES))
    p_reparer.add_argument("--source", required=True, help="SHA de l'état portant la valeur.")
    p_reparer.add_argument("--operateur", default="audit-lot20")
    p_reparer.add_argument("--motif", default="")

    sous.add_parser("journal", help="Les réparations déjà appliquées.")

    options = parser.parse_args(argv)

    if options.commande == "auditer":
        rapport = analyser_historique(lire_etats_git(options.fichier))
        if options.json:
            print(json.dumps(rapport, ensure_ascii=False, indent=2))
        else:
            print(formater(rapport))
        return 1 if rapport["en_souffrance"] else 0

    if options.commande == "reparer":
        entree = reparer(
            options.fichier,
            id_fiche=options.id_fiche,
            axe=options.axe,
            source=options.source,
            operateur=options.operateur,
            motif=options.motif,
        )
        print(f"réparé : id={entree['id']}  {entree['axe']} : {entree['avant']!r} → {entree['apres']!r}")
        print(f"  source : {entree['source_sha']} ({entree['source_date']})")
        print(f"  trace  : {JOURNAL_REPARATIONS.relative_to(RACINE_DEPOT).as_posix()}")
        return 0

    journal = _journal()
    if not journal["reparations"]:
        print("Aucune réparation consignée.")
        return 0
    for r in journal["reparations"]:
        print(
            f"  {r['horodatage']}  id={r['id']}  {r['axe']} : "
            f"{r['avant']!r} → {r['apres']!r}  (source {r['source_sha']}, "
            f"{r['source_date']}, par {r['operateur']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
