"""Lot J — facette DIMENSION, tri, pagination (plan v3.0 §11.4).

Vérifie :
- filtre par plage sur cote choisie (fiche_cotes), avec unités métier documentées
- compteurs facettes : chaque facette compte sans son propre filtre (y compris dimension)
- API GET /recherche étendue cote/min/max avec alias cote_min/cote_max
- intervalles calculés depuis données réelles
- tri optionnel appliqué sur fusion AVANT pagination
- pagination page → offset
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.postgres


def _semer_cotes(index: Any, fiches: dict[str, int]) -> None:
    """Sème des cotes finies pour tester dimension/tri."""
    # On met des SLU distinctes pour tri et filtre
    valeurs = {
        "0701-GV-001": {"slu_m": 6.60, "sle_m": 7.0, "spa_m2": 30.5, "poids_kg": 12.0},
        "0701-GV-002": {"slu_m": 8.20, "sle_m": 9.1, "spa_m2": 45.0, "poids_kg": 15.5},
        "0702-GV-003": {"slu_m": 5.10, "sle_m": 5.5, "spa_m2": 20.0, "poids_kg": 10.0},
        "0801-GEN-001": {"slu_m": 6.55, "sle_m": 6.8, "spa_m2": 25.0, "poids_kg": 11.0},
    }
    with index.connect() as conn:
        with conn.cursor() as cur:
            for code, cotes in valeurs.items():
                id_fiche = fiches.get(code)
                if id_fiche is None:
                    continue
                # upsert
                cur.execute(
                    "INSERT INTO fiche_cotes (id_fiche, jeu, slu_m, sle_m, spa_m2, poids_kg) "
                    "VALUES (%s, 'finie', %s, %s, %s, %s) "
                    "ON CONFLICT (id_fiche, jeu) DO UPDATE SET slu_m=EXCLUDED.slu_m, sle_m=EXCLUDED.sle_m, "
                    "spa_m2=EXCLUDED.spa_m2, poids_kg=EXCLUDED.poids_kg",
                    (id_fiche, cotes.get("slu_m"), cotes.get("sle_m"), cotes.get("spa_m2"), cotes.get("poids_kg")),
                )


def test_dimension_filtre_par_plage(base_recherche: dict[str, Any]) -> None:
    from seamtech_search.recherche import rechercher_fiches

    index = base_recherche["index"]
    _semer_cotes(index, base_recherche["fiches"])

    # Sans filtre dimension : les 4 fiches avec cotes + les autres sans cotes (12 validées)
    sans = rechercher_fiches(index, requete="", limit=100)
    assert sans["nb_resultats"] == 12

    # Filtre SLU entre 6.5 et 6.7 → 0701-GV-001 (6.60) + 0801-GEN-001 (6.55) = 2
    filtre = {"cote": "slu_m", "min": 6.5, "max": 6.7}
    avec = rechercher_fiches(index, requete="", filtres=filtre, limit=100)
    codes = {r["code"] for r in avec["resultats"]}
    assert codes == {"0701-GV-001", "0801-GEN-001"}

    # Alias cote_min/cote_max
    filtre_alias = {"cote": "slu_m", "cote_min": 6.5, "cote_max": 6.7}
    avec_alias = rechercher_fiches(index, requete="", filtres=filtre_alias, limit=100)
    assert {r["code"] for r in avec_alias["resultats"]} == codes


def test_dimension_facettes_et_intervalles(base_recherche: dict[str, Any]) -> None:
    from seamtech_search.recherche import rechercher_fiches

    index = base_recherche["index"]
    _semer_cotes(index, base_recherche["fiches"])

    # Optimisation 0.2 : facettes_cotes calculées seulement si dimension active (filtre cote présent) ou tri cote.
    # Pour tester intervalles, on active dimension via filtre cote.
    reponse = rechercher_fiches(index, requete="", filtres={"cote": "slu_m"}, limit=100)
    assert "facettes_cotes" in reponse
    fc = reponse["facettes_cotes"]
    # 7 cotes explicites
    for cote in ("slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_cm", "poids_kg"):
        assert cote in fc, f"cote {cote} manquante dans facettes_cotes"
        assert "unite" in fc[cote]
        assert "intervalles" in fc[cote]
    # Unités métier
    assert fc["slu_m"]["unite"] == "m"
    assert fc["spa_m2"]["unite"] == "m²"
    assert fc["tetiere_cm"]["unite"] == "cm"
    assert fc["poids_kg"]["unite"] == "kg"

    # Intervalles calculés depuis données réelles
    slu = fc["slu_m"]
    assert slu["effectif"] >= 2
    assert slu["min"] is not None and slu["max"] is not None
    assert slu["min"] <= slu["max"]
    assert len(slu["intervalles"]) >= 1
    # Chaque intervalle a effectif et label
    for iv in slu["intervalles"]:
        assert "min" in iv and "max" in iv and "effectif" in iv and "label" in iv

    # cote_active défaut slu_m ou celle filtrée
    assert reponse["cote_active"] == "slu_m"
    assert "dimension" in reponse["facettes"]
    # facettes["dimension"] = intervalles de cote_active
    assert len(reponse["facettes"]["dimension"]) == len(slu["intervalles"])

    # Chaque facette compte sans son propre filtre (dimension aussi)
    # On filtre sur slu_m, les facettes_cotes doivent quand même compter slu_m sans filtre dimension
    # → effectif slu_m avec filtre dimension = effectif sans filtre dimension (règle des facettes)
    reponse_filtree = rechercher_fiches(index, requete="", filtres={"cote": "slu_m", "min": 6.5, "max": 6.7}, limit=100)
    fc_filtree = reponse_filtree["facettes_cotes"]
    # Les cotes autres que filtrée gardent leur effectif, mais slu_m aussi (sans son propre filtre)
    assert fc_filtree["slu_m"]["effectif"] == fc["slu_m"]["effectif"]

    # Sans dimension active, facettes_cotes vide (optimisation perf 0.2)
    reponse_sans_dim = rechercher_fiches(index, requete="", limit=100)
    fc_sans = reponse_sans_dim["facettes_cotes"]
    for cote in ("slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_cm", "poids_kg"):
        assert cote in fc_sans
        assert "unite" in fc_sans[cote]


def test_tri_et_pagination(base_recherche: dict[str, Any]) -> None:
    from seamtech_search.recherche import rechercher_fiches

    index = base_recherche["index"]
    _semer_cotes(index, base_recherche["fiches"])

    # Tri pertinence par défaut
    r0 = rechercher_fiches(index, requete="grand voile", limit=20)
    assert r0["tri"] == "pertinence"

    # Tri par code asc
    r_code = rechercher_fiches(index, requete="", tri="code_asc", limit=100)
    codes = [r["code"] for r in r_code["resultats"]]
    assert codes == sorted(codes)

    # Tri par code desc
    r_code_desc = rechercher_fiches(index, requete="", tri="code_desc", limit=100)
    codes_desc = [r["code"] for r in r_code_desc["resultats"]]
    assert codes_desc == sorted(codes_desc, reverse=True)

    # Tri par SLU asc : les fiches avec SLU null en fin
    r_slu = rechercher_fiches(index, requete="", tri="slu_m_asc", limit=100)
    slu_vals = [r.get("slu_m") for r in r_slu["resultats"] if r.get("slu_m") is not None]
    assert slu_vals == sorted(slu_vals)

    # Tri appliqué AVANT pagination : page 1 et 2 triées globalement
    r_page1 = rechercher_fiches(index, requete="", tri="slu_m_asc", limit=2, offset=0)
    r_page2 = rechercher_fiches(index, requete="", tri="slu_m_asc", limit=2, offset=2)
    # Fusion complète triée, donc page1 max <= page2 min (pour les non-null)
    vals_p1 = [r["slu_m"] for r in r_page1["resultats"] if r["slu_m"] is not None]
    vals_p2 = [r["slu_m"] for r in r_page2["resultats"] if r["slu_m"] is not None]
    if vals_p1 and vals_p2:
        assert max(vals_p1) <= min(vals_p2)

    # Pagination via page param (API route)
    from pathlib import Path

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[Path("/tmp")], database_url=base_recherche["url"], min_free_bytes=0)
    app = create_app(config)
    with TestClient(app) as client:
        # page=2, limit=2 → offset 2
        resp = client.get("/recherche", params={"q": "", "tri": "code_asc", "page": 2, "limit": 2})
        assert resp.status_code == 200
        corps = resp.json()
        assert corps["page"] == 2
        assert len(corps["resultats"]) == 2
        # cote/min/max/tri dans réponse
        assert "facettes_cotes" in corps
        assert "cote_active" in corps
        assert "tri" in corps


def test_api_dimension_params(base_recherche: dict[str, Any]) -> None:
    """API accepte cote/min/max et alias cote_min/cote_max."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    index = base_recherche["index"]
    _semer_cotes(index, base_recherche["fiches"])

    config = AppConfig(root_paths=[Path("/tmp")], database_url=base_recherche["url"], min_free_bytes=0)
    app = create_app(config)
    with TestClient(app) as client:
        r = client.get("/recherche", params={"q": "", "cote": "slu_m", "min": 6.5, "max": 6.7, "limit": 100})
        assert r.status_code == 200
        codes = {x["code"] for x in r.json()["resultats"]}
        assert codes == {"0701-GV-001", "0801-GEN-001"}

        r2 = client.get("/recherche", params={"q": "", "cote": "slu_m", "cote_min": 6.5, "cote_max": 6.7, "limit": 100})
        assert r2.status_code == 200
        assert {x["code"] for x in r2.json()["resultats"]} == codes
