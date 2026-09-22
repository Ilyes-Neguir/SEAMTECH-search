#!/usr/bin/env python3
"""Garde-fou du projet SEAMTECH-search — vérifie les invariants qui ont DÉJÀ cassé.

Chaque contrôle ci-dessous correspond à un incident réel du projet (21/09/2026) :

  1. `.github/workflows/ci.yml` présent          → le fichier avait été déplacé à la
     racine `workflows/ci.yml` : GitHub Actions ne tournait plus, et la fusion
     aurait retiré la CI de `main`.
  2. Fixture 7792 = DOCUMENT RÉEL (SHA-256)      → le gabarit était réglé sur une
     reconstruction ; 16,7 % de champs lus au lieu de 100 %.
  3. Aucun `except: pass`                        → une faute de frappe avalée
     silencieusement a fait recréer des bases pendant toute une session.
  4. Verrou de calibration présent               → la validation groupée doit
     refuser tant que les seuils ne sont pas calibrés sur de vraies fiches.
  5. Fichiers de test imposés par le plan §17.11 présents.
  6. Suite de tests : aucune régression.

Usage :
    python3 scripts/audit_projet.py                 # tous les contrôles
    python3 scripts/audit_projet.py --rapide         # sans lancer pytest

Code de sortie : 0 si tout est vert, 1 sinon (utilisable comme porte CI).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

# Empreinte du document client réel (166 990 octets, 1 page).
SHA_FICHE_REELLE = "43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40"

FICHIERS_DE_TEST_IMPOSES = (
    "tests/test_extraction_fiche_reference.py",   # §17.11 — Lot B
    "tests/test_detection_gabarit.py",            # §17.11 — Lot B
    "tests/test_normalisation_unites.py",         # §17.11 — Lot B
    "tests/test_anomalies_coherence.py",          # §17.11 — Lot B
    "tests/test_depot_transactionnel.py",         # §17.11 — Lot C
    "tests/test_validation_workflow.py",          # §17.11 — Lot D
    "tests/test_recherche_hybride.py",            # §17.11 — Lot E
    "tests/test_facettes_et_suggestions.py",      # §17.11 — Lot E
)

resultats: list[tuple[str, bool, str]] = []


def controler(nom: str, ok: bool, detail: str = "") -> None:
    resultats.append((nom, ok, detail))
    marque = "OK  " if ok else "ÉCHEC"
    print(f"  [{marque}] {nom}" + (f" — {detail}" if detail else ""))


def controle_ci() -> None:
    print("\n1. Emplacement du workflow GitHub Actions")
    correct = RACINE / ".github" / "workflows" / "ci.yml"
    egare = RACINE / "workflows" / "ci.yml"
    controler(".github/workflows/ci.yml présent", correct.is_file(),
              "" if correct.is_file() else "GitHub Actions ne tournerait pas")
    if egare.is_file() and not correct.is_file():
        controler("workflow égaré à la racine `workflows/`", False,
                  "git mv workflows/ci.yml .github/workflows/ci.yml")


def controle_fixture() -> None:
    print("\n2. Fixture = document client réel (et non une reconstruction)")
    fixture = RACINE / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf"
    if not fixture.is_file():
        controler("fixture 7792 présente", False, str(fixture))
        return
    empreinte = hashlib.sha256(fixture.read_bytes()).hexdigest()
    controler("SHA-256 = document réel", empreinte == SHA_FICHE_REELLE,
              "" if empreinte == SHA_FICHE_REELLE else f"obtenu {empreinte[:16]}…")
    controler("taille = 166 990 octets", fixture.stat().st_size == 166990,
              f"{fixture.stat().st_size} octets")


def controle_exceptions() -> None:
    print("\n3. Aucune exception avalée dans la couche métier")
    fautifs: list[str] = []
    for chemin in (RACINE / "seamtech_search").rglob("*.py"):
        for numero, ligne in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
            # Seules les exceptions AVEUGLES sont un défaut : `except:` nu, ou
            # `except Exception:` (trop large). Un `except ValueError: pass`
            # étroit et commenté est un choix délibéré, pas un défaut.
            if re.match(r"^\s*except\s*(Exception\s*)?:\s*$", ligne):
                suite = chemin.read_text(encoding="utf-8").splitlines()[numero:numero + 1]
                if suite and suite[0].strip() == "pass":
                    fautifs.append(f"{chemin.relative_to(RACINE)}:{numero}")
    controler("aucune exception aveugle avalée (`except:` nu → `pass`)",
              not fautifs, ", ".join(fautifs[:5]))


def controle_calibration() -> None:
    print("\n4. Verrou de calibration des seuils")
    chemin = RACINE / "config" / "seuils_confiance.json"
    if not chemin.is_file():
        controler("config/seuils_confiance.json présent", False)
        return
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    controler("clé `calibre` présente", "calibre" in donnees, f"calibre={donnees.get('calibre')}")
    if donnees.get("calibre") is False:
        controler("verrou actif (valider en lot refusera)", True,
                  "attendu tant qu'aucune fiche réelle n'est validée")


def controle_fichiers_de_test() -> None:
    print("\n5. Fichiers de test imposés par le plan §17.11")
    manquants = [f for f in FICHIERS_DE_TEST_IMPOSES if not (RACINE / f).is_file()]
    controler(f"les {len(FICHIERS_DE_TEST_IMPOSES)} fichiers imposés existent",
              not manquants, ", ".join(manquants))


def controle_suite() -> None:
    print("\n6. Suite de tests (sortie brute)")
    # Le harnais du projet exige > 1 Go libre : sous ce seuil il répond 507 et
    # 10 tests d'import échouent — un faux positif d'environnement, pas une
    # régression. On choisit donc un basetemp sur un volume assez large.
    basetemp = Path(os.environ.get("SEAMTECH_AUDIT_BASETEMP", RACINE.parent / ".pytest_audit"))
    libre_go = shutil.disk_usage(basetemp.parent).free / 1e9
    if libre_go < 1.0:
        controler("espace disque pour la suite (>= 1 Go libre)", False,
                  f"{libre_go:.2f} Go sur {basetemp.parent} — le projet renverra 507")
        return
    controler(f"espace disque pour la suite ({libre_go:.1f} Go libres)", True, str(basetemp))
    commande = [sys.executable, "-m", "pytest", "-q", f"--basetemp={basetemp}"]
    process = subprocess.run(commande, cwd=RACINE, capture_output=True, text=True)
    derniere = [ligne for ligne in process.stdout.strip().splitlines() if "passed" in ligne or "failed" in ligne]
    print("     " + (derniere[-1] if derniere else "aucune ligne de résultat"))
    controler("aucun test en échec", process.returncode == 0,
              "" if process.returncode == 0 else "voir la sortie ci-dessus")


def main() -> int:
    parseur = argparse.ArgumentParser(description="Garde-fou du projet SEAMTECH-search")
    parseur.add_argument("--rapide", action="store_true", help="ne pas lancer la suite pytest")
    arguments = parseur.parse_args()

    print("=" * 74)
    print("GARDE-FOU SEAMTECH-search — invariants déjà cassés par le passé")
    print("=" * 74)

    controle_ci()
    controle_fixture()
    controle_exceptions()
    controle_calibration()
    controle_fichiers_de_test()
    if not arguments.rapide:
        controle_suite()

    echecs = [r for r in resultats if not r[1]]
    print("\n" + "=" * 74)
    print(f"BILAN : {len(resultats) - len(echecs)}/{len(resultats)} contrôles verts")
    if echecs:
        print("À CORRIGER :")
        for nom, _, detail in echecs:
            print(f"   - {nom}" + (f" ({detail})" if detail else ""))
    print("=" * 74)
    return 1 if echecs else 0


if __name__ == "__main__":
    raise SystemExit(main())
