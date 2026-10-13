"""Reproduction AVANT correction — parité casse/accents du moteur de recherche.

Ce script n'est PAS un cas particulier « génois » : il sème plusieurs familles
de termes (métier, client, fichier, description, mot ordinaire) et compare les
identifiants renvoyés pour des requêtes équivalentes (casse / diacritiques /
Unicode NFC vs NFD).

Il documente aussi le constat A-03 de la recette : la source trigrammes est
littérale, donc GENOIS et GÉNOIS ne votent pas les mêmes fiches approximatives.

Sortie JSON sur stdout. Code de sortie 0 si le script s'est exécuté (même si
le défaut est observé) ; 2 si PostgreSQL est indisponible.
"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
import uuid
from pathlib import Path

DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")


def _nfd(texte: str) -> str:
    return unicodedata.normalize("NFD", texte)


FAMILLES = (
    # (id_logique, titre, extra_champs)
    ("facade", "Façade atelier nord", "description façade peinte"),
    ("reparation", "Réparation voile d'avant", "réparation urgente"),
    ("ete", "Contrôle été 2026", "campagne d'été"),
    ("noel", "Noël chantier", "fermeture Noël"),
    ("cable", "Câble d'écoute spectra", "câble dyneema"),
    ("muller", "Client Müller", "Müller voiles"),
    ("sao", "Navire São Miguel", "São Miguel Açores"),
    ("coeur", "Cœur de spi", "pièce cœur"),
    ("genois_titre", "Génois lourd First 30", "type Génois"),
    ("gennaker_proche", "Gennaker course médium", "Gennaker Radial"),
)


def main() -> int:
    if not DATABASE_URL:
        print(json.dumps({"ok": False, "motif": "SEAMTECH_TEST_DATABASE_URL absent"}, ensure_ascii=False))
        return 2

    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    from seamtech_search.indexer import SearchIndex
    from seamtech_search.recherche import rechercher_fiches

    nom = f"repro_norm_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(DATABASE_URL)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    admin.close()

    principale, sep, requete = DATABASE_URL.partition("?")
    url = principale.rsplit("/", 1)[0] + "/" + nom + (f"?{requete}" if sep else "")
    index = SearchIndex(Path(f"/tmp/unused-{nom}.db"), url)
    index.initialize()
    index.run_migrations()

    ids: dict[str, int] = {}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO type_voile (code, libelle, famille) VALUES ('GEN', 'Génois', 'voile_avant')"
            )
            cursor.execute("INSERT INTO client (nom, chantier) VALUES ('Voilerie Atlantique', 'Brest')")
            cursor.execute("INSERT INTO bateau (nom, taille) VALUES ('First 30', '30 pieds')")
            cursor.execute("SELECT id_client FROM client")
            id_client = int(cursor.fetchone()[0])
            cursor.execute("SELECT id_bateau FROM bateau")
            id_bateau = int(cursor.fetchone()[0])
            cursor.execute("SELECT id_type_voile FROM type_voile")
            id_type = int(cursor.fetchone()[0])
            for logique, titre, notes in FAMILLES:
                code = f"N-{logique[:8].upper()}-{len(ids)+1:03d}"
                cursor.execute(
                    "INSERT INTO fiche (code, titre, id_type_voile, id_client, id_bateau, "
                    "date_edition, notes, statut) VALUES (%s, %s, %s, %s, %s, make_date(2024,6,15), %s, 'valide') "
                    "RETURNING id_fiche",
                    (code, titre, id_type, id_client, id_bateau, notes),
                )
                id_fiche = int(cursor.fetchone()[0])
                ids[logique] = id_fiche
                cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (id_fiche,))

            cursor.execute(
                "SELECT unaccent('GÉNOIS') AS u_acc, unaccent('GENOIS') AS u_plain, "
                "to_tsvector('seamtech_unaccent', 'Génois') AS lex_acc, "
                "to_tsvector('seamtech_unaccent', 'GENOIS') AS lex_plain, "
                "word_similarity('GENOIS', 'Gennaker course médium') AS sim_plain, "
                "word_similarity('GÉNOIS', 'Gennaker course médium') AS sim_acc, "
                "word_similarity('génois', 'Gennaker course médium') AS sim_min"
            )
            mesures_sql = cursor.fetchone()
            mesures = {
                "unaccent_GENois_accentue": mesures_sql[0],
                "unaccent_GENOIS": mesures_sql[1],
                "lexemes_accent": str(mesures_sql[2]),
                "lexemes_plain": str(mesures_sql[3]),
                "word_similarity_GENOIS_Gennaker": float(mesures_sql[4]),
                "word_similarity_GENois_accentue_Gennaker": float(mesures_sql[5]),
                "word_similarity_genois_Gennaker": float(mesures_sql[6]),
            }

    variantes = {
        "genois": ["GENOIS", "GÉNOIS", "génois", "Génois", "GeNoIs", _nfd("Génois")],
        "facade": ["facade", "façade", "FAÇADE", "FACADE", _nfd("façade")],
        "reparation": ["reparation", "réparation", "RÉPARATION", "Reparation"],
        "ete": ["ete", "été", "Été", "ETE", _nfd("été")],
        "noel": ["noel", "Noël", "NOEL", "NOËL"],
        "cable": ["cable", "câble", "CÂBLE"],
        "muller": ["muller", "Müller", "MULLER", "müller"],
        "sao": ["sao", "São", "SAO"],
        "coeur": ["coeur", "cœur", "CŒUR"],
    }

    resultats: dict[str, dict[str, list[str]]] = {}
    sources: dict[str, dict[str, list[str]]] = {}
    for famille, requetes in variantes.items():
        resultats[famille] = {}
        sources[famille] = {}
        for q in requetes:
            reponse = rechercher_fiches(index, requete=q, limit=50)
            codes = [r["code"] for r in reponse.get("resultats") or []]
            resultats[famille][q] = codes
            sources[famille][q] = reponse.get("sources_actives") or []

    def _ensembles(famille: str) -> dict[str, list[str]]:
        vus: dict[tuple[str, ...], list[str]] = {}
        for q, codes in resultats[famille].items():
            cle = tuple(codes)
            vus.setdefault(cle, []).append(q)
        return {str(i): qs for i, (cle, qs) in enumerate(vus.items())}

    parite = {famille: len(_ensembles(famille)) == 1 for famille in variantes}

    index.close()
    admin = psycopg2.connect(DATABASE_URL)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom)))
    admin.close()

    rapport = {
        "phase": "avant_correction",
        "commit": os.popen("git rev-parse HEAD").read().strip(),
        "mesures_sql": mesures,
        "resultats": resultats,
        "sources_actives": sources,
        "parite_ensembles": parite,
        "defaut_observe": not all(parite.values()),
        "commentaire": (
            "Un False dans parite_ensembles signifie que des requêtes ne différant "
            "que par la casse ou les accents n'ont pas renvoyé les mêmes identifiants."
        ),
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
