#!/usr/bin/env python3
"""Contrôle d'intégrité du corpus de recette : SHA-256 pré / post (§6 du protocole).

Recalcule l'empreinte de chaque fichier du corpus et la compare au manifeste
produit AVANT les essais (`MANIFESTE_CORPUS.csv`). Écrit
`preuves/integrite_corpus.{json,csv}` : aucune modification, aucun ajout,
aucune suppression ne doit être constatée — les essais travaillent sur des
copies en zone jetable.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

CORPUS = Path("/home/user/rv/corpus/sources")
MANIFESTE = Path("/home/user/SEAMTECH-search/deliverables/recette-virtuelle/MANIFESTES/MANIFESTE_CORPUS.csv")
SORTIE = Path("/home/user/rv/preuves")


def sha256(chemin: Path) -> str:
    h = hashlib.sha256()
    with chemin.open("rb") as flux:
        for bloc in iter(lambda: flux.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def main() -> int:
    attendu: dict[str, dict[str, str]] = {}
    with MANIFESTE.open(encoding="utf-8") as flux:
        for ligne in csv.DictReader(flux):
            attendu[ligne["chemin_relatif"]] = ligne
    constate: dict[str, dict[str, object]] = {}
    for chemin in sorted(p for p in CORPUS.rglob("*") if p.is_file()):
        relatif = chemin.relative_to(CORPUS).as_posix()
        constate[relatif] = {"taille_octets": chemin.stat().st_size, "sha256": sha256(chemin)}
    modifies = [c for c, v in constate.items() if c in attendu and (v["sha256"] != attendu[c]["sha256"]
                                                                   or str(v["taille_octets"]) != attendu[c]["taille_octets"])]
    ajoutes = sorted(set(constate) - set(attendu))
    supprimes = sorted(set(attendu) - set(constate))
    rapport = {
        "manifeste": str(MANIFESTE),
        "corpus": str(CORPUS),
        "fichiers_au_manifeste": len(attendu),
        "fichiers_constates": len(constate),
        "identiques": len(constate) - len(modifies),
        "modifies": modifies,
        "ajoutes": ajoutes,
        "supprimes": supprimes,
        "conforme": not (modifies or ajoutes or supprimes),
    }
    SORTIE.mkdir(parents=True, exist_ok=True)
    (SORTIE / "integrite_corpus.json").write_text(json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8")
    with (SORTIE / "integrite_corpus.csv").open("w", newline="", encoding="utf-8") as flux:
        ecrivain = csv.writer(flux)
        ecrivain.writerow(["chemin_relatif", "sha256_pre", "sha256_post", "taille_octets", "etat"])
        for relatif, valeurs in sorted(constate.items()):
            avant = attendu.get(relatif, {}).get("sha256", "")
            etat = "identique" if avant == valeurs["sha256"] else ("ajoute_apres_manifeste" if not avant else "modifie")
            ecrivain.writerow([relatif, avant, valeurs["sha256"], valeurs["taille_octets"], etat])
    print(f"CONTROLE|corpus-integrite-pre-post|{'PASS' if rapport['conforme'] else 'FAIL'}|"
          f"{rapport['fichiers_constates']} fichiers constatés, {rapport['identiques']} identiques au manifeste, "
          f"{len(modifies)} modifiés, {len(ajoutes)} ajoutés, {len(supprimes)} supprimés")
    return 0 if rapport["conforme"] else 1


if __name__ == "__main__":
    sys.exit(main())
