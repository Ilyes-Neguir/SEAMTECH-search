#!/usr/bin/env python3
"""Répare les copies locales disparues du lot de recette (documents orphelins).

Contexte mesuré : `scripts/recette_verif.py` **vide son répertoire de travail**
(`RECETTE_TRAVAIL=/home/user/rv/data/recette-lot`) avant de ré-extraire les 7 archives.
Les dossiers de travail des autres harnais (essais de panne, trois sessions, reprise)
vivaient dans ce même répertoire : leurs fichiers ont donc disparu alors que les
documents correspondants restent référencés dans la base (`storage_direct`, copie
locale) — leurs téléchargements répondaient alors 404 (constat consigné : A-07).

Ce script rétablit les fichiers manquants **à partir du corpus de recette** (mêmes
contenus, empreintes du manifeste) et vérifie ensuite, document par document, que
chaque pièce redevient téléchargeable.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

CORPUS = Path("/home/user/rv/corpus/sources")
ZONE = Path("/home/user/rv/data/recette-lot")
PREUVES = Path("/home/user/rv/preuves")
PSQL = ["/home/user/rv/env/pg/bin/psql", "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech",
        "-d", "seamtech_search", "-tAc"]


def psql(requete: str) -> list[str]:
    sortie = subprocess.run(PSQL + [requete], capture_output=True, text=True,
                            env={**os.environ, "PGPASSWORD": os.environ["POSTGRES_PASSWORD"],
                                 "LD_LIBRARY_PATH": "/home/user/rv/env/pg.libs:/home/user/rv/env/pg/lib"})
    return [l for l in sortie.stdout.strip().splitlines() if l]


def index_corpus() -> dict[str, list[Path]]:
    par_nom: dict[str, list[Path]] = {}
    for chemin in CORPUS.rglob("*"):
        if chemin.is_file():
            par_nom.setdefault(chemin.name, []).append(chemin)
    return par_nom


def main() -> int:
    lignes = psql("SELECT id, path FROM documents WHERE path IS NOT NULL AND path <> '' ORDER BY id")
    manquants = []
    for ligne in lignes:
        identifiant, _, chemin = ligne.partition("|")
        if not chemin.startswith("/home/user/rv/data/recette-lot"):
            continue
        if not Path(chemin).exists():
            manquants.append((int(identifiant), chemin))
    print(f"# {len(lignes)} documents, {len(manquants)} copies locales manquantes")
    index = index_corpus()
    repares, irreparables = [], []
    for identifiant, chemin in manquants:
        cible = Path(chemin)
        candidats = index.get(cible.name, [])
        source = None
        if candidats:
            # préférer une source dont le dossier parent porte un nom proche du dossier manquant
            morceaux = [p.name for p in cible.parents[:6]]
            source = next((c for c in candidats
                           if any(m in str(c.parent) for m in morceaux if m and m != "recette-lot")), candidats[0])
        if source:
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, cible)
            repares.append({"id": identifiant, "chemin": chemin, "source": str(source)})
        else:
            irreparables.append({"id": identifiant, "chemin": chemin})
    print(f"# réparés : {len(repares)} ; non réparables (absents du corpus) : {len(irreparables)}")

    # Vérification réelle : chaque pièce du catalogue doit se télécharger
    entetes = {"X-SEAMTECH-TOKEN": os.environ["SEAMTECH_AUTH_TOKEN"]}
    client = httpx.Client(base_url="http://127.0.0.1:8000", headers=entetes, timeout=60.0)
    restants_ko, verifies = [], 0
    page = 1
    while True:
        corps = client.get("/pieces", params={"limit": 200, "page": page}).json()
        pieces = corps.get("pieces") or []
        for piece in pieces:
            reponse = client.get(f"/pieces/{piece['id']}/telecharger")
            verifies += 1
            if reponse.status_code != 200:
                restants_ko.append((piece["id"], piece.get("nom"), reponse.status_code))
        if not corps.get("has_more"):
            break
        page += 1
    print(f"# {verifies} pièces testées en téléchargement, {len(restants_ko)} en échec")
    PREUVES.mkdir(parents=True, exist_ok=True)
    (PREUVES / "reparation_copies_locales.json").write_text(json.dumps(
        {"manquants": len(manquants), "reparés": repares, "irreparables": irreparables,
         "pieces_testees": verifies, "pieces_encore_en_echec": restants_ko[:50]},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"CONTROLE|copies-locales-retablies|{'PASS' if not restants_ko else 'FAIL'}|"
          f"{len(repares)} fichier(s) rétabli(s) depuis le corpus ; {verifies} pièces testées, "
          f"{len(restants_ko)} téléchargement(s) encore en échec")
    return 0 if not restants_ko else 1


if __name__ == "__main__":
    sys.exit(main())
