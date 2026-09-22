#!/usr/bin/env python3
"""Lot I — mesure de l'assistant sourcé sur le jeu d'essai des 8 questions.

Ce que fait ce script (§17.13 : un lot sans chiffres n'est pas terminé) :

1. Crée une base PostgreSQL JETABLE, la migre, la sème (corpus Lot E + cotes
   SYNTHÉTIQUES étiquetées + la VRAIE fiche 7792-SO entrée par le pipeline
   réglé — même semis que ``tests/test_assistant_postgres.py``).
2. Exécute les 8 questions : réponse obtenue, nombre de citations, état.
3. Mesure p50/p95/max par question (1 échauffement + 10 passages mesurés,
   n = 10 par question) DEUX FOIS étiqueté : appel direct ``poser()``
   (l'analyse) et aller-retour HTTP ``POST /assistant`` (TestClient, route
   complète avec validation et sérialisation JSON).
4. Taux de réponses sourcées (doit être 100 % des réponses effectives).
5. RG13 : empreinte SHA-256 de l'arbre ``sample_data`` avant/après (égale).
6. Journal : nombre de lignes ``recherche_log`` écrites par l'assistant.
7. RG14 (--sans-reseau) : rejoue TOUT dans un namespace réseau vide
   (``unshare -n``) et y VÉRIFIE qu'aucune socket n'est créable — la base est
   jointe par socket UNIX (objet de système de fichiers), jamais par le réseau.

Sortie : tableau lisible + JSON (variable SEAMTECH_MESURE_ASSISTANT_JSON).
Code de sortie 0 si tout est vert (taux sourcé 100 %, archive intacte), 1 sinon.

Usage :
    SEAMTECH_TEST_DATABASE_URL=… .venv/bin/python scripts/mesure_assistant.py
    SEAMTECH_TEST_DATABASE_URL=… sudo -E unshare -n \
        .venv/bin/python scripts/mesure_assistant.py --dans-namespace
    SEAMTECH_TEST_DATABASE_URL=… sudo -E .venv/bin/python scripts/mesure_assistant.py --sans-reseau
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

ARCHIVE = RACINE / "sample_data"
REPETITIONS_MESUREES = 10


def empreinte_archive() -> str:
    empreinte = hashlib.sha256()
    for chemin in sorted(p for p in ARCHIVE.rglob("*") if p.is_file()):
        empreinte.update(str(chemin.relative_to(ARCHIVE)).encode())
        empreinte.update(chemin.read_bytes())
    return empreinte.hexdigest()


def p95(valeurs: list[float]) -> float:
    trie = sorted(valeurs)
    return trie[max(0, int(len(trie) * 0.95) - 1)]


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--dans-namespace", action="store_true", help="interne : déjà lancé dans `unshare -n`")
    parseur.add_argument("--sans-reseau", action="store_true", help="relance ce script lui-même dans `unshare -n`")
    arguments = parseur.parse_args()

    if arguments.sans_reseau and not arguments.dans_namespace:
        if os.geteuid() != 0:
            print("ERREUR : --sans-reseau exige root (sudo) pour créer le namespace `unshare -n`.", file=sys.stderr)
            return 2
        resultat = subprocess.run(
            ["unshare", "-n", sys.executable, str(Path(__file__).resolve()), "--dans-namespace"],
            env=dict(os.environ),
        )
        return resultat.returncode

    if arguments.dans_namespace:
        # Dans un namespace réseau vide, CREER une socket réussit toujours :
        # ce qui prouve l'absence de réseau, ce sont (1) les interfaces (lo
        # seul) et (2) l'impossibilité de CONNECTER quoi que ce soit.
        interfaces = [nom for _indice, nom in socket.if_nameindex()]
        raison = ""
        if sorted(interfaces) != ["lo"]:
            raison = f"interfaces inattendues : {interfaces}"
        else:
            sonde = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sonde.settimeout(3.0)
            try:
                sonde.connect(("93.184.216.34", 443))  # extérieur, jamais utilisé
                raison = "connexion sortante RÉUSSIE — ce n'est pas un namespace sans réseau !"
            except OSError as erreur:
                print(f"[RG14] namespace sans réseau VÉRIFIÉ : interfaces = {interfaces}, connexion sortante impossible ({erreur})")
            finally:
                sonde.close()
        if raison:
            print(f"[RG14] ÉCHEC : {raison}")
            return 2

    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        print("ERREUR : SEAMTECH_TEST_DATABASE_URL est requis (comme pour les tests PostgreSQL).", file=sys.stderr)
        return 2

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.assistant import ETAT_AMBIGU, ETAT_OK, poser
    from seamtech_search.config import AppConfig
    from seamtech_search.indexer import SearchIndex
    from tests.conftest import (
        JEU_8_QUESTIONS,
        _creer_base_jetable,
        _supprimer_base_jetable,
        semer_base_assistant,
    )

    empreinte_avant = empreinte_archive()

    nom_base, url_base = _creer_base_jetable()
    index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    semis = semer_base_assistant(index)
    print(f"[semis] base jetable {nom_base} : corpus Lot E + cotes synthétiques + fiche réelle {semis['fiche'].code}")

    # API complète montée en mémoire pour la mesure HTTP (route, validation,
    # sérialisation JSON — sans serveur réseau : TestClient in-process).
    config = AppConfig(
        root_paths=[RACINE],
        database_path=Path("/tmp/unused-api.db"),
        database_url=url_base,
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    resultats: list[dict[str, object]] = []
    for question, etiquette in JEU_8_QUESTIONS:
        reponse = poser(index, question)  # la réponse rendue dans le rapport
        poser(index, question)  # échauffement direct
        client.post("/assistant", json={"question": question})  # échauffement HTTP
        direct: list[float] = []
        http: list[float] = []
        for _ in range(REPETITIONS_MESUREES):
            debut = time.perf_counter()
            r = poser(index, question)
            direct.append((time.perf_counter() - debut) * 1000.0)
            debut = time.perf_counter()
            reponse_http = client.post("/assistant", json={"question": question})
            http.append((time.perf_counter() - debut) * 1000.0)
            assert reponse_http.status_code == 200, reponse_http.text
            assert reponse_http.json()["etat"] == r["etat"]
        resultats.append(
            {
                "question": question,
                "etiquette": etiquette,
                "etat": reponse["etat"],
                "reponse": reponse["reponse"],
                "nb_citations": len(reponse["citations"]),
                "citations": reponse["citations"],
                "nb_interpretations": len(reponse["interpretations"]),
                "pistes": reponse["pistes"],
                "p50_direct_ms": round(statistics.median(direct), 2),
                "p95_direct_ms": round(p95(direct), 2),
                "max_direct_ms": round(max(direct), 2),
                "p50_http_ms": round(statistics.median(http), 2),
                "p95_http_ms": round(p95(http), 2),
                "max_http_ms": round(max(http), 2),
                "n": REPETITIONS_MESUREES,
            }
        )

    # p50/p95 GLOBAUX : 8 questions × 10 passages mêlés (n = 80 par mode).
    globales_direct: list[float] = []
    globales_http: list[float] = []
    for _ in range(REPETITIONS_MESUREES):
        for question, _etiquette in JEU_8_QUESTIONS:
            debut = time.perf_counter()
            poser(index, question)
            globales_direct.append((time.perf_counter() - debut) * 1000.0)
            debut = time.perf_counter()
            client.post("/assistant", json={"question": question})
            globales_http.append((time.perf_counter() - debut) * 1000.0)

    effectives = [r for r in resultats if r["etat"] in (ETAT_OK, ETAT_AMBIGU)]
    sourcees = sum(
        1
        for r in effectives
        if (r["nb_citations"] >= 1) or (r["etat"] == ETAT_AMBIGU and r["nb_interpretations"] >= 1)
    )
    taux_source = sourcees / len(effectives) if effectives else 0.0

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM recherche_log WHERE filtres->>'canal' = 'assistant'")
            lignes_journal = int(cursor.fetchone()[0])
    empreinte_apres = empreinte_archive()

    print("\n=== JEU D'ESSAI — 8 questions (réponses obtenues) ===")
    for r in resultats:
        print(f"\nQ : {r['question']}   [{r['etiquette']}]")
        print(f"   état = {r['etat']} · citations = {r['nb_citations']} · interprétations = {r['nb_interpretations']}")
        print(f"   réponse : {r['reponse']}")
        for citation in r["citations"][:3]:
            zone = "zone PDF ✓" if citation.get("zone") else "sans zone (source = base, pas le PDF)"
            print(f"   → {citation.get('code_fiche')} · {citation['champ']} · {citation['valeur']} · {zone}")

    print("\n=== MESURES p50/p95 (1 échauffement + 10 passages mesurés par question) ===")
    for r in resultats:
        print(
            f"  direct p50 {r['p50_direct_ms']:>7.2f} ms | p95 {r['p95_direct_ms']:>7.2f} ms | max {r['max_direct_ms']:>7.2f} ms "
            f"|| HTTP p50 {r['p50_http_ms']:>7.2f} ms | p95 {r['p95_http_ms']:>7.2f} ms | n = {r['n']} | {r['question'][:48]}"
        )
    print(
        f"\n  GLOBAL direct : n = {len(globales_direct)} → p50 = {statistics.median(globales_direct):.2f} ms, "
        f"p95 = {p95(globales_direct):.2f} ms, max = {max(globales_direct):.2f} ms"
    )
    print(
        f"  GLOBAL HTTP   : n = {len(globales_http)} → p50 = {statistics.median(globales_http):.2f} ms, "
        f"p95 = {p95(globales_http):.2f} ms, max = {max(globales_http):.2f} ms"
    )
    print(f"\n  Taux de réponses sourcées : {sourcees}/{len(effectives)} réponses effectives = {taux_source:.0%}")
    print(f"  Journal assistant (recherche_log, canal 'assistant') : {lignes_journal} lignes")
    print(f"  RG13 empreinte archive avant = {empreinte_avant}")
    print(f"  RG13 empreinte archive après = {empreinte_apres} ({'IDENTIQUE' if empreinte_avant == empreinte_apres else 'MODIFIÉE !'})")

    sortie = {
        "corpus": "corpus Lot E (12 fiches synthétiques + 2 a_valider + 1 rejetée) + cotes SLU synthétiques + fiche réelle 7792-SO",
        "repetitions_par_question": REPETITIONS_MESUREES,
        "questions": resultats,
        "global_direct": {
            "n": len(globales_direct),
            "p50_ms": round(statistics.median(globales_direct), 2),
            "p95_ms": round(p95(globales_direct), 2),
            "max_ms": round(max(globales_direct), 2),
        },
        "global_http": {
            "n": len(globales_http),
            "p50_ms": round(statistics.median(globales_http), 2),
            "p95_ms": round(p95(globales_http), 2),
            "max_ms": round(max(globales_http), 2),
        },
        "taux_reponses_sourcees": taux_source,
        "reponses_effectives": len(effectives),
        "journal_lignes": lignes_journal,
        "rg13_archive_sha256": {
            "avant": empreinte_avant,
            "apres": empreinte_apres,
            "identique": empreinte_avant == empreinte_apres,
        },
        "sans_reseau": bool(arguments.dans_namespace),
    }
    chemin_sortie = os.environ.get("SEAMTECH_MESURE_ASSISTANT_JSON")
    if chemin_sortie:
        Path(chemin_sortie).write_text(json.dumps(sortie, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n[JSON] mesures écrites dans {chemin_sortie}")

    index.close()
    _supprimer_base_jetable(nom_base)

    tout_vert = taux_source == 1.0 and empreinte_avant == empreinte_apres
    return 0 if tout_vert else 1


if __name__ == "__main__":
    raise SystemExit(main())
