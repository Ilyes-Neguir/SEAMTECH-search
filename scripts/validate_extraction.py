"""Validate extract_structured_pdf against real fabrication documents.

Audit item 1 requires proving the regex fixes against real SEAMTECH sheets,
not just synthetic fixtures. This harness prints the FULL ExtractedData for
every document given and flags fields that are wrong, suspect, or None when
they probably should not be.

Usage:
    python scripts/validate_extraction.py path/to/dossier [more/paths ...]
    python scripts/validate_extraction.py            # defaults to sample_data/

Point it at a folder of real (redacted) fabrication PDFs. For each document
it prints every extracted field, the confidence, the extraction status, and
all warnings, then a REVIEW block listing what a human must check.

Nothing here mutates the documents or the index; extraction only.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from seamtech_search.config import AppConfig  # noqa: E402
from seamtech_search.import_pipeline import (  # noqa: E402
    _MAX_PLAUSIBLE_FIELD_LENGTH,
    FIELD_PATTERNS,
    extract_structured_pdf,
)

LOGGER = logging.getLogger("seamtech_search.validate_extraction")


SUPPORTED = {".pdf"}

# Fields the fixed-layout sheets are expected to carry. A None here is a
# candidate miss, not automatically an error: some sheets genuinely lack it.
EXPECTED_FIELDS = ("reference", "material", "quantity", "description")


def discover(paths: list[str]) -> list[Path]:
    """Expand the given paths into a sorted list of PDF files."""
    if not paths:
        paths = [str(REPO_ROOT / "sample_data")]
    found: list[Path] = []
    for raw in paths:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if candidate.is_dir():
            found.extend(sorted(p for p in candidate.rglob("*") if p.suffix.lower() in SUPPORTED))
        elif candidate.is_file():
            found.append(candidate)
        else:
            print(f"WARNING: {raw} does not exist; skipping.", file=sys.stderr)
    # De-duplicate while preserving order.
    seen: set[Path] = set()
    unique = []
    for path in found:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def review_notes(data: Any, text: str) -> list[str]:
    """Human-review flags for one document."""
    notes: list[str] = []
    for field_name in EXPECTED_FIELDS:
        value = getattr(data, field_name, None)
        if value is None:
            notes.append(f"{field_name}: None — no pattern matched. Add the real line to FIELD_PATTERNS.")
            continue
        if field_name != "description":
            # Mirror the shipped heuristic so the report explains *why* it is suspect.
            if len(str(value)) > _MAX_PLAUSIBLE_FIELD_LENGTH:
                notes.append(f"{field_name}: {len(str(value))} chars, over the {_MAX_PLAUSIBLE_FIELD_LENGTH} cap — probable bleed.")
    if data.dimensions is None or (
        data.dimensions.length is None and data.dimensions.width is None
    ):
        notes.append("dimensions: no length/width found at all.")
    elif data.dimensions.unit is None:
        notes.append("dimensions: unit is None — values were NOT converted to mm. Confirm the real unit on the sheet.")
    if data.extraction_status != "success":
        notes.append(f"extraction_status={data.extraction_status} (confidence {data.confidence:.2f}).")
    if not text.strip():
        notes.append("raw_text is empty — scanned PDF, needs OCR.")
    return notes


# ---------------------------------------------------------------------------
# Mode « mesure » (Phase 0) : taux de lecture correcte, champ par champ,
# contre une vérité terrain fournie par l'opérateur.
#
# Le plan v3.0 (§17.8) prévoit un script `benchmark_gabarit.py` ; il est
# réalisé ici comme mode `--verite` de ce harnais, pour garder une seule
# porte d'entrée et un seul format de rapport de calibration.
#
# Fichier de vérité (JSON) attendu :
# {
#   "fiche-7792-SO.pdf": {
#     "gabarit": "spi_asymetrique_ref",
#     "attendu": {
#       "reference": "7792-SO",
#       "material": "Monofilm K903",
#       "quantity": 1,
#       "description": "Spi asymétrique Medium Régate",
#       "dimensions": {"length": 6.6, "width": 5.5, "unit": "m"}
#     }
#   }
# }
#
# Les clés de "attendu" sont les champs d'ExtractedData (reference, material,
# quantity, description, dimensions). Un champ attendu à null vérifie
# l'ABSENCE de valeur. Les cotes sont comparées en millimètres (tolérance de
# 1 mm ou 0,1 %), ce qui rend le verdict indépendant de l'unité du PDF.
# ---------------------------------------------------------------------------

CHAMPS_TEXTE = ("reference", "material", "description")
# Tolérance absolue en mm, plus une tolérance relative (0,1 %) pour les
# grandes cotes : une cote lue à 6 600 mm contre 6 599,4 mm attendus est une
# bonne lecture, pas une erreur.
TOLERANCE_MM_ABSOLUE = 1.0
TOLERANCE_MM_RELATIVE = 0.001

VERDICT_OK = "OK"
VERDICT_OK_ABSENCE = "OK_ABSENCE"
VERDICT_ECART = "ECART"
VERDICT_MANQUANT = "MANQUANT"
VERDICT_SUSPECT = "SUSPECT"
VERDICT_INATTENDU = "INATTENDU"


def normaliser_valeur(valeur: Any) -> str:
    """Normalisation pour comparaison : sans accents, casse et espaces unifiés."""
    return " ".join(unicodedata.normalize("NFD", str(valeur)).encode("ascii", "ignore").decode().lower().split())


def charger_verite(chemin: Path) -> dict[str, dict[str, Any]]:
    """Charge le JSON de vérité terrain et valide sa forme minimale."""
    with chemin.open("r", encoding="utf-8") as fichier:
        donnees = json.load(fichier)
    if not isinstance(donnees, dict) or not donnees:
        raise ValueError(
            f"Vérité terrain invalide : {chemin} doit être un objet JSON non vide "
            '({"fiche.pdf": {"gabarit": "...", "attendu": {...}}}).'
        )
    for cle, spec in donnees.items():
        if not isinstance(spec, dict) or "attendu" not in spec:
            raise ValueError(
                f'Verité terrain invalide pour "{cle}" : entrée "attendu" manquante.'
            )
    return donnees


def champ_suspect(data: Any, champ: str) -> bool:
    """Vrai si l'extraction a levé un avertissement « à vérifier » sur ce champ."""
    return any(str(avertissement).startswith(f"{champ}:") for avertissement in data.warnings)


def comparer_champ_simple(champ: str, attendu: Any, lu: Any, suspect: bool) -> dict[str, Any]:
    """Compare un champ scalaire (texte ou entier) et rend le verdict."""
    if lu is None or (isinstance(lu, str) and not lu.strip()):
        return {
            "champ": champ,
            "verdict": VERDICT_MANQUANT,
            "attendu": attendu,
            "lu": lu,
            "note": "aucune valeur lue par les motifs actuels",
        }
    if champ == "quantity":
        conforme = lu == attendu or str(lu).strip() == str(attendu).strip()
    else:
        conforme = normaliser_valeur(lu) == normaliser_valeur(attendu)
    verdict = VERDICT_ECART
    note = ""
    if conforme:
        verdict = VERDICT_SUSPECT if suspect else VERDICT_OK
        note = "valeur conforme mais signalée suspecte : à confirmer par l'opérateur" if suspect else ""
    return {"champ": champ, "verdict": verdict, "attendu": attendu, "lu": lu, "note": note}


def comparer_dimensions(attendu: dict[str, Any], dimensions: Any, suspect: bool) -> list[dict[str, Any]]:
    """Compare les cotes attendues aux dimensions extraites (en millimètres).

    La vérité terrain donne les cotes dans l'unité ``unit`` (m, cm ou mm ;
    mm par défaut). La comparaison se fait en millimètres avec une tolérance
    de 1 mm ou 0,1 %, afin qu'une fiche imprimée « 6,60 m » lue « 6600 mm »
    reste une bonne lecture.
    """
    verdicts: list[dict[str, Any]] = []
    unite_attendue = str(attendu.get("unit") or "mm").strip().lower()
    multiplicateur = {"mm": 1.0, "cm": 10.0, "m": 1000.0}.get(unite_attendue)
    if multiplicateur is None:
        LOGGER.warning(
            "Unité de vérité terrain inconnue (%s) — conséquence : cotes comparées comme des mm.",
            unite_attendue,
        )
        multiplicateur = 1.0
    for cote in ("length", "width", "height"):
        if cote not in attendu:
            continue
        valeur_attendue = attendu[cote]
        lue_brute = getattr(dimensions, cote, None)
        if valeur_attendue is None:
            verdicts.append(
                {
                    "champ": f"dimensions.{cote}",
                    "verdict": VERDICT_OK_ABSENCE,
                    "attendu": None,
                    "lu": lue_brute,
                    "note": "absence attendue confirmée",
                }
            )
            continue
        if lue_brute is None:
            verdicts.append(
                {
                    "champ": f"dimensions.{cote}",
                    "verdict": VERDICT_MANQUANT,
                    "attendu": valeur_attendue,
                    "lu": None,
                    "note": "cote non lue",
                }
            )
            continue
        attendu_mm = float(valeur_attendue) * multiplicateur
        lue_mm = getattr(dimensions, f"{cote}_mm", None)
        tolerance = TOLERANCE_MM_ABSOLUE + TOLERANCE_MM_RELATIVE * abs(attendu_mm)
        conforme = lue_mm is not None and abs(float(lue_mm) - attendu_mm) <= tolerance
        verdict, note = VERDICT_ECART, ""
        if conforme and unite_attendue != "mm" and getattr(dimensions, "unit", None) is None:
            verdict = VERDICT_SUSPECT
            note = "valeur conforme mais unité non détectée dans le PDF : conversion à confirmer"
        elif conforme and suspect:
            verdict = VERDICT_SUSPECT
            note = "valeur conforme mais signalée suspecte : à confirmer par l'opérateur"
        elif conforme:
            verdict = VERDICT_OK
        verdicts.append(
            {
                "champ": f"dimensions.{cote}",
                "verdict": verdict,
                "attendu": valeur_attendue,
                "lu": lue_brute,
                "note": note,
            }
        )
    return verdicts


def comparer_fiche(spec: dict[str, Any], data: Any) -> list[dict[str, Any]]:
    """Produit le verdict champ par champ pour une fiche extraite."""
    attendu: dict[str, Any] = spec.get("attendu") or {}
    verdicts: list[dict[str, Any]] = []
    for champ, valeur_attendue in attendu.items():
        suspect = champ_suspect(data, champ)
        if champ == "dimensions":
            if valeur_attendue is None:
                verdicts.append(
                    {
                        "champ": "dimensions",
                        "verdict": VERDICT_OK_ABSENCE,
                        "attendu": None,
                        "lu": data.dimensions.model_dump(),
                        "note": "absence attendue confirmée",
                    }
                )
            else:
                verdicts.extend(comparer_dimensions(valeur_attendue, data.dimensions, suspect))
            continue
        if valeur_attendue is None:
            lue = getattr(data, champ, None)
            present = lue is not None and (not isinstance(lue, str) or bool(lue.strip()))
            verdicts.append(
                {
                    "champ": champ,
                    "verdict": VERDICT_INATTENDU if present else VERDICT_OK_ABSENCE,
                    "attendu": None,
                    "lu": lue,
                    "note": "valeur lue alors qu'une absence était attendue" if present else "",
                }
            )
            continue
        verdicts.append(comparer_champ_simple(champ, valeur_attendue, getattr(data, champ, None), suspect))
    return verdicts


def apparier_verite(documents: list[Path], verite: dict[str, dict[str, Any]]) -> list[tuple[Path, str, dict[str, Any]]]:
    """Associe chaque PDF découvert à sa clé de vérité (par nom, puis chemin complet)."""
    appariements: list[tuple[Path, str, dict[str, Any]]] = []
    for document in documents:
        candidats = [cle for cle in verite if Path(cle).name == document.name]
        if len(candidats) > 1:
            candidats = [cle for cle in candidats if str(document).endswith(cle) or cle in str(document)]
        if len(candidats) == 1:
            appariements.append((document, candidats[0], verite[candidats[0]]))
        else:
            LOGGER.warning(
                "Vérité terrain : %s correspond à %d entrées (%s) — fiche ignorée de la mesure.",
                document.name,
                len(candidats),
                ", ".join(candidats) or "aucune clé",
            )
    return appariements


def mesurer_echantillon(
    documents: list[Path],
    verite: dict[str, dict[str, Any]],
    config: Any,
) -> dict[str, Any]:
    """Mesure le taux de lecture correcte champ par champ sur l'échantillon."""
    appariements = apparier_verite(documents, verite)
    if not appariements:
        return {"meta": {"nb_fiches": 0}, "fiches": [], "message": "aucune fiche de vérité terrain trouvée"}

    fiches: list[dict[str, Any]] = []
    for chemin, cle, spec in appariements:
        debut = time.perf_counter()
        try:
            data = extract_structured_pdf(chemin, config)
        except Exception as exc:  # noqa: BLE001 - le harnais ne doit pas mourir sur un mauvais PDF
            LOGGER.error(
                "Extraction en échec (%s) : %s: %s — conséquence : tous les champs attendus comptés MANQUANT.",
                chemin,
                type(exc).__name__,
                exc,
            )
            data = None
        temps_ms = int((time.perf_counter() - debut) * 1000)
        if data is None:
            verdicts = [
                {
                    "champ": champ,
                    "verdict": VERDICT_MANQUANT,
                    "attendu": valeur,
                    "lu": None,
                    "note": "extraction en échec",
                }
                for champ, valeur in (spec.get("attendu") or {}).items()
                if valeur is not None
            ]
            fiches.append(
                {
                    "fichier": str(chemin),
                    "cle_verite": cle,
                    "gabarit": spec.get("gabarit", "inconnu"),
                    "temps_ms": temps_ms,
                    "extraction_status": "erreur",
                    "champs": verdicts,
                }
            )
            continue
        fiches.append(
            {
                "fichier": str(chemin),
                "cle_verite": cle,
                "gabarit": spec.get("gabarit", "inconnu"),
                "temps_ms": temps_ms,
                "extraction_status": data.extraction_status,
                "avertissements": list(data.warnings),
                "champs": comparer_fiche(spec, data),
            }
        )

    non_trouvees = sorted(set(verite) - {cle for _, cle, _ in appariements})
    return {
        "meta": {
            "nb_fiches": len(fiches),
            "cles_verite_non_trouvees": non_trouvees,
            "temps_moyen_ms": round(sum(fiche["temps_ms"] for fiche in fiches) / len(fiches), 1),
        },
        "fiches": fiches,
    }


def aggreger_mesure(mesure: dict[str, Any]) -> dict[str, Any]:
    """Agrège les verdicts : taux par champ, par gabarit et global."""
    par_champ: dict[str, dict[str, int]] = {}
    par_gabarit: dict[str, dict[str, Any]] = {}

    def compteur_champ(nom: str) -> dict[str, int]:
        return par_champ.setdefault(nom, {"attendus": 0, "ok": 0, "ecart": 0, "manquant": 0, "suspect": 0})

    def compteur_gabarit(nom: str) -> dict[str, Any]:
        return par_gabarit.setdefault(nom, {"nb_fiches": 0, "par_champ": {}})

    for fiche in mesure.get("fiches", []):
        gabarit = compteur_gabarit(fiche.get("gabarit", "inconnu"))
        gabarit["nb_fiches"] += 1
        for verdict in fiche.get("champs", []):
            if verdict["verdict"] in (VERDICT_OK_ABSENCE, VERDICT_INATTENDU):
                continue  # hors taux : l'absence attendue n'est pas une lecture
            nom = verdict["champ"]
            compteur = compteur_champ(nom)
            compteur["attendus"] += 1
            gabarit_par_champ = gabarit["par_champ"].setdefault(nom, {"attendus": 0, "ok": 0})
            gabarit_par_champ["attendus"] += 1
            if verdict["verdict"] == VERDICT_OK:
                compteur["ok"] += 1
                gabarit_par_champ["ok"] += 1
            elif verdict["verdict"] == VERDICT_ECART:
                compteur["ecart"] += 1
            elif verdict["verdict"] == VERDICT_MANQUANT:
                compteur["manquant"] += 1
            elif verdict["verdict"] == VERDICT_SUSPECT:
                compteur["suspect"] += 1

    def avec_taux(compteur: dict[str, Any]) -> dict[str, Any]:
        resultat = dict(compteur)
        resultat["taux_ok"] = round(compteur["ok"] / compteur["attendus"], 3) if compteur["attendus"] else None
        return resultat

    total = {"attendus": 0, "ok": 0, "ecart": 0, "manquant": 0, "suspect": 0}
    for compteur in par_champ.values():
        for cle in total:
            total[cle] += compteur[cle]

    gabarits_agreges = {
        nom: {
            "nb_fiches": valeurs["nb_fiches"],
            "par_champ": {champ: avec_taux(cpt) for champ, cpt in valeurs["par_champ"].items()},
        }
        for nom, valeurs in par_gabarit.items()
    }
    temps = [fiche["temps_ms"] for fiche in mesure.get("fiches", []) if "temps_ms" in fiche]
    return {
        "global": {**avec_taux(total), "temps_moyen_ms": round(sum(temps) / len(temps), 1) if temps else None},
        "par_champ": {champ: avec_taux(cpt) for champ, cpt in sorted(par_champ.items())},
        "par_gabarit": gabarits_agreges,
    }


def afficher_mesure(mesure: dict[str, Any], agregats: dict[str, Any]) -> None:
    """Rapport console de la mesure (français, exploitable par l'opérateur)."""
    global_stats = agregats["global"]
    print("=" * 72)
    print(f"MESURE DE LECTURE — {mesure['meta']['nb_fiches']} fiche(s), vérité terrain")
    print("=" * 72)
    for fiche in mesure.get("fiches", []):
        print(f"\nFICHE : {fiche['fichier']}  (gabarit : {fiche['gabarit']}, {fiche['temps_ms']} ms)")
        for verdict in fiche.get("champs", []):
            marqueur = {
                VERDICT_OK: "OK      ",
                VERDICT_OK_ABSENCE: "ABSENCE ",
                VERDICT_ECART: "ECART   ",
                VERDICT_MANQUANT: "MANQUANT",
                VERDICT_SUSPECT: "SUSPECT ",
                VERDICT_INATTENDU: "INATTENDU",
            }.get(verdict["verdict"], verdict["verdict"])
            ligne = f"  [{marqueur}] {verdict['champ']:<22} attendu={verdict['attendu']!r} lu={verdict['lu']!r}"
            if verdict.get("note"):
                ligne += f"  — {verdict['note']}"
            print(ligne)
    print("\n" + "=" * 72)
    print("TAUX DE LECTURE CORRECTE PAR CHAMP (sur champs attendus non nuls)")
    for champ, stats in agregats["par_champ"].items():
        taux = "n/a" if stats["taux_ok"] is None else f"{stats['taux_ok'] * 100:.1f} %"
        print(
            f"  {champ:<22} {stats['ok']:>3}/{stats['attendus']:<3} OK".replace(",", " ")
            + f"  (écarts {stats['ecart']}, manquants {stats['manquant']}, suspects {stats['suspect']})  → {taux}"
        )
    taux_global = global_stats["taux_ok"]
    affichage_global = "n/a" if taux_global is None else f"{taux_global * 100:.1f} %"
    print("-" * 72)
    print(f"  GLOBAL                {global_stats['ok']:>3}/{global_stats['attendus']:<3} OK → {affichage_global}")
    if global_stats.get("temps_moyen_ms") is not None:
        print(f"  Temps moyen d'extraction : {global_stats['temps_moyen_ms']} ms/fiche")
    for nom, gabarit in agregats["par_gabarit"].items():
        champs = ", ".join(
            f"{champ} {(cpt['taux_ok'] or 0) * 100:.0f} %" for champ, cpt in sorted(gabarit["par_champ"].items())
        )
        print(f"  Gabarit {nom} ({gabarit['nb_fiches']} fiche(s)) : {champs}")
    for cle in mesure["meta"].get("cles_verite_non_trouvees", []):
        print(f"  ! clé de vérité sans PDF correspondant : {cle}", file=sys.stderr)
    print()


def verifier_sortie_hors_echantillon(sortie_json: Path, documents: list[Path]) -> None:
    """Refuse d'écrire le rapport de mesure dans un dossier contenant un PDF mesuré.

    Les dossiers SEAMTECH sont en lecture seule : le rapport doit sortir de l'archive.
    """
    sortie_resolue = sortie_json.resolve()
    for document in documents:
        dossier = document.resolve().parent
        if sortie_resolue == dossier or dossier in sortie_resolue.parents:
            raise ValueError(
                f"Le rapport de mesure {sortie_resolue} serait écrit dans le dossier des fiches mesurées "
                f"({dossier}) : archive en lecture seule, choisissez un emplacement ailleurs."
            )


def executer_mode_mesure(arguments: argparse.Namespace) -> int:
    """Banc d'essai Phase 0 : mesure contre vérité terrain, rapport JSON + console."""
    documents = discover(list(arguments.racines))
    if not documents:
        print("No PDFs found. Pass a folder of real fabrication sheets.", file=sys.stderr)
        return 2
    try:
        verite = charger_verite(Path(arguments.verite))
    except (OSError, ValueError) as erreur:
        # JSONDecodeError hérite de ValueError ; OSError couvre un fichier illisible.
        print(f"ERREUR : vérité terrain illisible : {erreur}", file=sys.stderr)
        return 2
    if arguments.sortie_json:
        try:
            verifier_sortie_hors_echantillon(Path(arguments.sortie_json), documents)
        except ValueError as erreur:
            print(f"ERREUR : {erreur}", file=sys.stderr)
            return 2
    # Dossiers dédupliqués : AppConfig refuse les racines en double, et tous
    # les PDF mesurés viennent souvent du même dossier.
    dossiers = sorted({str(document.parent) for document in documents})
    config = AppConfig(root_paths=[Path(dossier) for dossier in dossiers])
    mesure = mesurer_echantillon(documents, verite, config)
    if mesure["meta"]["nb_fiches"] == 0:
        print(
            "ERREUR : aucune fiche de vérité terrain n'a été retrouvée dans les PDF découverts "
            "(vérifiez que les clés du JSON correspondent aux noms des fichiers).",
            file=sys.stderr,
        )
        return 2
    agregats = aggreger_mesure(mesure)
    afficher_mesure(mesure, agregats)
    if arguments.sortie_json:
        chemin_sortie = Path(arguments.sortie_json)
        chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
        with chemin_sortie.open("w", encoding="utf-8") as fichier:
            json.dump({"meta": mesure["meta"], **agregats, "fiches": mesure["fiches"]}, fichier, ensure_ascii=False, indent=2)
            fichier.write("\n")
        print(f"Rapport de mesure écrit : {chemin_sortie}")
    if arguments.seuil is not None:
        taux = agregats["global"]["taux_ok"]
        if taux is None or taux < arguments.seuil:
            print(
                f"SEUIL NON ATTEINT : taux global {taux} < seuil {arguments.seuil}.",
                file=sys.stderr,
            )
            return 1
    return 0


def main(argv: list[str]) -> int:
    parseur = argparse.ArgumentParser(
        prog="validate_extraction.py",
        description=(
            "Harnais de validation de l'extraction. Sans --verite : revue humaine "
            "des champs extraits. Avec --verite : mesure champ par champ du taux "
            "de lecture correcte contre un JSON de vérité terrain (Phase 0)."
        ),
    )
    parseur.add_argument("racines", nargs="*", help="PDF ou dossiers à traiter (défaut : sample_data/)")
    parseur.add_argument(
        "--verite",
        default=None,
        help="JSON de vérité terrain {\"fiche.pdf\": {\"gabarit\": ..., \"attendu\": {...}}} → mode mesure",
    )
    parseur.add_argument("--sortie-json", default=None, help="écrire le rapport de mesure en JSON (mode --verite)")
    parseur.add_argument(
        "--seuil",
        type=float,
        default=None,
        help="code 1 si le taux global passe sous ce seuil (0..1), pour servir de garde",
    )
    arguments = parseur.parse_args(argv[1:])

    if arguments.verite:
        return executer_mode_mesure(arguments)

    documents = discover(arguments.racines)
    if not documents:
        print("No PDFs found. Pass a folder of real fabrication sheets.", file=sys.stderr)
        return 2
    return boucle_revue(documents)


def boucle_revue(documents: list[Path]) -> int:
    """Mode historique : affichage complet des champs extraits + bloc REVIEW."""
    print(f"Validating {len(documents)} document(s).")
    print(f"Configured fields: {', '.join(FIELD_PATTERNS)}\n")

    failures = 0
    for path in documents:
        print("=" * 78)
        print(f"FILE: {path}")
        print("=" * 78)
        config = AppConfig(root_paths=[path.parent])
        try:
            data = extract_structured_pdf(path, config)
        except Exception as exc:  # noqa: BLE001 - harness must not die on one bad file
            failures += 1
            print(f"  EXTRACTION RAISED: {type(exc).__name__}: {exc}\n")
            continue

        print(f"  extraction_status : {data.extraction_status}")
        print(f"  confidence        : {data.confidence:.2f}")
        print(f"  reference         : {data.reference!r}")
        print(f"  material          : {data.material!r}")
        print(f"  quantity          : {data.quantity!r}")
        print(f"  description       : {data.description!r}")
        if data.dimensions is not None:
            dims = data.dimensions.model_dump()
            print(f"  dimensions        : {json.dumps(dims, ensure_ascii=False)}")
        if data.warnings:
            print("  warnings:")
            for warning in data.warnings:
                print(f"    - {warning}")
        print("  --- raw_text (first 1200 chars) ---")
        preview = data.raw_text[:1200]
        for line in preview.splitlines() or [""]:
            print(f"    | {line}")
        if len(data.raw_text) > 1200:
            print(f"    | ... ({len(data.raw_text) - 1200} more chars)")

        notes = review_notes(data, data.raw_text)
        if notes:
            print("  --- REVIEW (a human must confirm these) ---")
            for note in notes:
                print(f"    * {note}")
            failures += 1
        else:
            print("  --- REVIEW: clean, all expected fields present and plausible ---")
        print()

    print("=" * 78)
    if failures:
        print(f"{failures} of {len(documents)} document(s) need review or failed.")
        print("For each flagged field, add the EXACT real-world line to FIELD_PATTERNS,")
        print("LABELED_DIMENSION_PATTERNS or _FIELD_LABEL_WORDS in import_pipeline.py")
        print("(respecting the _FIELD_STOP lookahead) and add one regression test per")
        print("layout variant, following test_field_capture_stops_at_next_column_not_end_of_line.")
    else:
        print(f"All {len(documents)} document(s) extracted cleanly.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
