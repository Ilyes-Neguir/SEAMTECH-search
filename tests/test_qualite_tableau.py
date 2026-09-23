"""Tableau qualité — Lot K.1 (données réelles, GROUP BY, <100ms sur 1000 fiches)."""

import time

import pytest

from seamtech_search.qualite.tableau import (
    anomalies_frequentes,
    lots_stats,
    taux_correction_par_champ,
    taux_extraction_auto,
    temps_validation,
    usage_recherches,
    volume_par_statut,
)

pytestmark = pytest.mark.postgres


def test_qualite_sources_reelles(base_recherche):
    """Sources fiche_champ_extrait, fiche_validation, fiche_anomalie, recherche_log, lots existent."""
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            # Au moins les tables existent (migrations 006-015)
            cur.execute("SELECT COUNT(*) FROM fiche_champ_extrait")
            cur.execute("SELECT COUNT(*) FROM fiche_validation")
            cur.execute("SELECT COUNT(*) FROM fiche_anomalie")
            cur.execute("SELECT COUNT(*) FROM recherche_log")
            cur.execute("SELECT COUNT(*) FROM lot_import")
            cur.execute("SELECT COUNT(*) FROM lot_dossier")


def test_taux_extraction_auto_definition(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = taux_extraction_auto(cur)
            assert "definition" in res
            assert "unite" in res
            assert "periode" in res
            assert "taux_auto" in res


def test_taux_correction_par_champ_group_by(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = taux_correction_par_champ(cur)
            assert isinstance(res, list)
            # Si données présentes, tri desc par taux_correction
            if len(res) >= 2:
                assert res[0]["taux_correction"] >= res[1]["taux_correction"]


def test_temps_validation_mediane_p95(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = temps_validation(cur)
            assert "definition" in res
            assert "mediane_s" in res
            assert "p95_s" in res
            assert "nb_fiches_validees" in res


def test_anomalies_frequentes_group_by(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = anomalies_frequentes(cur)
            assert isinstance(res, list)


def test_volume_par_statut(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = volume_par_statut(cur)
            assert "par_statut" in res
            assert "en_attente_validation" in res


def test_usage_recherches_split_canal(base_recherche):
    """Distinguer recherches utilisateur vs questions assistant (canal filtres->>'canal')."""
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            # Insère deux recherches : une utilisateur, une assistant
            cur.execute(
                "INSERT INTO recherche_log (requete, filtres, nb_resultats) VALUES (%s, %s, %s)",
                ("test utilisateur", '{"canal": "utilisateur"}', 5),
            )
            cur.execute(
                "INSERT INTO recherche_log (requete, filtres, nb_resultats) VALUES (%s, %s, %s)",
                ("test assistant", '{"canal": "assistant"}', 0),
            )
            res = usage_recherches(cur)
            assert "par_canal" in res
            canaux = {c["canal"] for c in res["par_canal"]}
            # Doit distinguer au moins utilisateur et assistant si présents
            assert "utilisateur" in canaux or "assistant" in canaux
            assert "definition" in res
            assert "par_jour" in res


def test_lots_stats(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = lots_stats(cur)
            assert "total_lots" in res
            assert "total_dossiers" in res


@pytest.mark.perf
def test_qualite_perf_1000_fiches(base_recherche):
    """Requêtes <100ms sur 1000 fiches — mesure n+p50/p95."""
    import json
    import os
    from pathlib import Path

    index = base_recherche["index"]
    # Prépare 1000 fiches si pas déjà
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM fiche")
            nb = int(cur.fetchone()[0])
            if nb < 1000:
                # Crée 1000 fiches via bulk insert minimal
                for i in range(1000 - nb):
                    cur.execute(
                        "INSERT INTO fiche (code, titre, statut) VALUES (%s, %s, 'a_valider') ON CONFLICT (code) DO NOTHING",
                        (f"PERF_Q_{i}_{time.time()}", f"Fiche perf {i}"),
                    )
                # Ajoute champs extraits pour taux
                cur.execute("SELECT id_fiche FROM fiche WHERE code LIKE 'PERF_Q_%' LIMIT 500")
                ids = [r[0] for r in cur.fetchall()]
                for fid in ids[:500]:
                    cur.execute(
                        "INSERT INTO fiche_champ_extrait (id_fiche, champ, valeur_brute, valeur_normalisee, methode, confiance, corrige) "
                        "VALUES (%s, %s, %s, %s, 'test', 0.9, false) ON CONFLICT (id_fiche, champ, rang) DO NOTHING",
                        (fid, "fiche.code", "BRUT", "NORM"),
                    )

    # Mesure p50/p95 sur 60 runs de tableau_de_bord partiel (une requête)
    n = 60
    durees = []
    for _ in range(n):
        t0 = time.perf_counter()
        with index.connect() as conn:
            with conn.cursor() as cur:
                taux_extraction_auto(cur)
                taux_correction_par_champ(cur)
                volume_par_statut(cur)
        durees.append((time.perf_counter() - t0) * 1000)

    durees.sort()
    p50 = durees[n // 2]
    p95 = durees[int(n * 0.95)]
    max_ms = durees[-1]

    # Publie mesure pour CI (même mécanisme que perf existantes)
    perf_json = os.environ.get("SEAMTECH_PERF_JSON")
    if perf_json:
        Path(perf_json).parent.mkdir(parents=True, exist_ok=True)
        with open(perf_json, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "nom": "qualite_1000_fiches",
                        "n": n,
                        "p50_ms": p50,
                        "p95_ms": p95,
                        "max_ms": max_ms,
                        "seuil_p95_ms": 100.0,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    # Garde-fou : <100ms produit
    assert p95 < 100.0, f"qualite p95={p95:.1f}ms >=100ms sur 1000 fiches"
    assert p50 < 100.0


def test_qualite_cli_sans_reseau():
    """CLI ne fait pas d'appel réseau (RG14)."""
    from pathlib import Path

    src = Path("seamtech_search/qualite/tableau.py").read_text(encoding="utf-8")
    assert "requests" not in src
    assert "httpx" not in src
    # Pas d'appel réseau sortant


# ---------------------------------------------------------------------------
# Lot L (correctif C.1) — le tableau de bord ENTIER est testé.
#
# Motif qui a manqué en L.1 : le Lot K testait chaque indicateur un par un,
# mais rien ne garantissait que `tableau_de_bord()` les expose TOUS ni que la
# route les serve. Un indicateur cassé ou oublié laissait donc la CI verte et
# l'écran /qualite cassé chez l'utilisateur.
#
# Règle adoptée (et écrite dans le rapport) : tout nouvel indicateur arrive avec
#   (a) sa clé dans CLES_TABLEAU_DE_BORD ci-dessous,
#   (b) son test de valeurs,
#   (c) sa ligne dans docs/API.md.
# ---------------------------------------------------------------------------

from seamtech_search.qualite.tableau import doublons_detectes, ocr_etage3, tableau_de_bord  # noqa: E402

# Les 10 indicateurs réellement exposés après M (9 en L.2 + ocr_etage3).
CLES_TABLEAU_DE_BORD = {
    "taux_extraction_auto",
    "taux_correction_par_champ",
    "temps_validation",
    "anomalies_frequentes",
    "volume_par_statut",
    "usage_recherches",
    "lots",
    "doublons_detectes",
    "taux_par_utilisateur",
    "ocr_etage3",
}


def test_tableau_de_bord_expose_tous_les_indicateurs_declares(base_recherche):
    """C.1.1 — égalité EXACTE des clés : ajouter un indicateur sans le déclarer casse ici."""
    tableau = tableau_de_bord(base_recherche["index"])
    assert set(tableau.keys()) == CLES_TABLEAU_DE_BORD, (
        f"indicateurs manquants : {CLES_TABLEAU_DE_BORD - set(tableau.keys())} ; "
        f"indicateurs non déclarés : {set(tableau.keys()) - CLES_TABLEAU_DE_BORD}"
    )


def _poser_doublon_exact(index, id_a: int, id_b: int, empreinte: str) -> None:
    """Deux pièces jointes de MÊME empreinte + le lien exact, en SQL direct."""
    with index.connect() as conn:
        with conn.cursor() as cur:
            for id_fiche, nom in ((id_a, "a.pdf"), (id_b, "b.pdf")):
                cur.execute(
                    "INSERT INTO fiche_piece_jointe (id_fiche, chemin, empreinte_sha256, taille_octets) "
                    "VALUES (%s, %s, %s, %s)",
                    (id_fiche, f"pieces/{nom}", empreinte, 1234),
                )
            cur.execute(
                "INSERT INTO fiche_lien (id_fiche_source, id_fiche_cible, type, score) "
                "VALUES (%s, %s, 'doublon_exact', 1.000)",
                (id_a, id_b),
            )


def test_doublons_detectes_compte_vraiment(base_recherche):
    """C.1.2 — deux fiches partagent une empreinte, dont UNE non validée.

    Vérifie que le compteur compte (groupe + fiche vue avant validation) et que
    la charge définition/unité/période du Lot K est toujours là.
    """
    index = base_recherche["index"]
    id_valide = base_recherche["fiches"]["0701-GV-001"]  # valide
    id_a_valider = base_recherche["fiches"]["1001-GV-006"]  # a_valider
    empreinte = "f" * 64

    _poser_doublon_exact(index, id_valide, id_a_valider, empreinte)

    with index.connect() as conn:
        with conn.cursor() as cur:
            resultat = doublons_detectes(cur)

    assert resultat["groupes_exacts"] == 1
    assert resultat["liens_exacts"] == 1
    assert resultat["doublons_vus_avant_validation"] >= 1, (
        "la fiche non validée liée à un doublon doit être comptée comme vue AVANT validation"
    )
    for cle in ("definition", "unite", "periode"):
        assert resultat[cle], f"charge {cle} absente (exigence Lot K)"


def test_doublons_detectes_sans_doublon(base_recherche):
    """C.1.2 (suite) — base sans doublon : zéro partout, pas d'erreur."""
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            resultat = doublons_detectes(cur)
    assert resultat["groupes_exacts"] == 0
    assert resultat["liens_exacts"] == 0
    assert resultat["liens_probables"] == 0
    assert resultat["doublons_vus_avant_validation"] == 0


def test_route_tableau_de_bord(base_recherche):
    """C.1.3 — la route sert exactement les mêmes clés que la fonction (200 attendu)."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[Path(".")], database_url=base_recherche["url"])
    client = TestClient(create_app(config))

    reponse = client.get("/qualite/tableau-de-bord")
    assert reponse.status_code == 200, reponse.text
    assert set(reponse.json().keys()) == CLES_TABLEAU_DE_BORD


def test_taux_par_utilisateur_expose_les_actions_sans_utilisateur(base_recherche):
    """C.1.2/L.2.7 — l'héritage non attribué est rendu VISIBLE, pas masqué."""
    index = base_recherche["index"]
    id_fiche = base_recherche["fiches"]["1001-GV-006"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            # Une action SANS utilisateur (comme toutes celles d'avant L.2)…
            cur.execute(
                "INSERT INTO fiche_validation (id_fiche, id_utilisateur, action, etat_avant, etat_apres) "
                "VALUES (%s, NULL, 'valider', 'a_valider', 'valide')",
                (id_fiche,),
            )
    # Hors du bloc : la transaction est validée, et `tableau_de_bord` ouvre sa
    # propre connexion (sinon il lirait un état non commité).
    resultat = tableau_de_bord(index)["taux_par_utilisateur"]

    assert resultat["actions_total"] >= 1
    assert resultat["actions_sans_utilisateur"] >= 1
    assert resultat["actions_attribuees"] + resultat["actions_sans_utilisateur"] == resultat["actions_total"]
    assert resultat["comptes_actifs_sans_action"] == 0  # aucun compte nominatif en base de test
    for cle in ("definition", "unite", "periode"):
        assert resultat[cle], f"charge {cle} absente (exigence Lot K)"


def test_ocr_etage3_indicateur_valeurs(base_recherche):
    """Lot M.6 — l'indicateur ocr_etage3 expose ses compteurs réels."""
    index = base_recherche["index"]
    # Insère 2 pages océrisées et 1 ignorée (texte natif)
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ocr_etage3
                (fichier_source, empreinte_sha256, page, texte_ocr, confiance,
                 moteur, version_moteur, duree_s, page_ocerisee, motif)
                VALUES
                (%s, %s, 0, %s, 95.0, 'tesseract', 'tesseract 5.3.0', 1.8, true, ''),
                (%s, %s, 1, %s, 90.0, 'tesseract', 'tesseract 5.3.0', 2.2, true, ''),
                (%s, %s, 0, '', NULL, 'natif', NULL, 0.0, false, 'texte natif présent')
                """,
                (
                    "scan1.pdf",
                    "a" * 64,
                    "SEAMTECH VOILE TEST",
                    "scan1.pdf",
                    "a" * 64,
                    "SEAMTECH VOILE TEST 2",
                    "natif.pdf",
                    "b" * 64,
                ),
            )

    with index.connect() as conn:
        with conn.cursor() as cur:
            res = ocr_etage3(cur)

    assert res["pages_ocerisees"] == 2
    assert res["fichiers_scannes"] == 1
    assert res["pages_ignorees_texte_natif"] == 1
    assert res["taille_texte_produit"] > 0
    assert res["duree_totale_s"] >= 4.0
    assert res["debit_moyen_pages_par_minute"] > 0
    for cle in ("definition", "unite", "periode"):
        assert res[cle], f"charge {cle} absente (exigence Lot K)"
