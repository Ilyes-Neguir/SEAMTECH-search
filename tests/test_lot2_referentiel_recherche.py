"""Lot 2 — référentiel canonique des bateaux et recherche étendue.

Le périmètre prouvé ici est volontairement le parcours complet, pas un
module isolé : normalisation prudente, migration/backfill via l'outil de
réindexation, suggestions classées exact > alias > préfixe > flou,
recherche et filtre par modèle, fusion historique puis défusion, et
rattachement à l'écriture d'une fiche.

La règle métier centrale est négative autant que positive : ``45``,
``45.1`` et ``45 CC`` ne doivent jamais être fusionnés en silence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from seamtech_search.recherche import (
    invalider_cache_modeles_bateaux,
    rechercher_fiches,
    suggerer,
)
from seamtech_search.referentiel_bateaux import (
    AliasBateauEnConflit,
    assurer_alias,
    defusionner_modele,
    fusionner_modeles,
    normaliser_modele_bateau,
    resoudre_modele_depuis_texte,
)
from seamtech_search.reindex_recherche import (
    _afficher_texte,
    _construire_parseur,
    _timeout_batch_ms,
    rapport_reindex,
)

CODES_45 = {"LOT2-45-A", "LOT2-45-B", "LOT2-45-C"}


def test_normalisation_modele_bateau_prudente() -> None:
    assert normaliser_modele_bateau("BAVARIA 45") == "bavaria 45"
    assert normaliser_modele_bateau("Bavaria-45") == "bavaria 45"
    assert normaliser_modele_bateau("Bavaria_45") == "bavaria 45"
    assert normaliser_modele_bateau("Bav. 45") == "bavaria 45"
    assert normaliser_modele_bateau("Bénéteau Océanis 38.1") == "beneteau oceanis 38.1"
    assert normaliser_modele_bateau("Bavaria 45CC") == normaliser_modele_bateau("Bavaria 45 CC")

    cles = {
        normaliser_modele_bateau("Bavaria 45"),
        normaliser_modele_bateau("Bavaria 45.1"),
        normaliser_modele_bateau("Bavaria 45 CC"),
    }
    assert len(cles) == 3, "les suffixes métier ne doivent jamais être écrasés"


def test_reindex_cli_simulation_par_defaut_et_timeout_borne(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _construire_parseur().parse_args([])
    assert args.appliquer is False
    assert args.dry_run is False  # la simulation reste le défaut de main()

    monkeypatch.delenv("SEAMTECH_STATEMENT_TIMEOUT_MS", raising=False)
    assert _timeout_batch_ms(None) == 1_800_000
    assert _timeout_batch_ms(1) == 100
    monkeypatch.setenv("SEAMTECH_STATEMENT_TIMEOUT_MS", "valeur-invalide")
    assert _timeout_batch_ms(None) == 1_800_000

    mesures = {
        "fiches_total": 3,
        "fiches_sans_search_vector": 1,
        "bateaux_sans_modele": 2,
        "modeles_actifs": 4,
        "alias_bateaux": 5,
        "synonymes_recherche": 6,
    }
    _afficher_texte(
        {
            "dry_run": True,
            "avant": mesures,
            "apres": mesures,
            "actions": {
                "bateaux_lies": 0,
                "modeles_crees": 0,
                "synonymes_copies": 0,
                "fiches_rafraichies": 0,
            },
        }
    )
    assert "SIMULATION" in capsys.readouterr().out


def _ajouter_bateaux_et_fiches(index: Any) -> dict[str, dict[str, int]]:  # noqa: ANN401
    bateaux = [
        ("BAVARIA 45", None),
        ("Bavaria-45", "45 pieds"),
        ("Bav. 45", "Cruiser"),
        ("Bavaria 45.1", None),
        ("Bavaria 45 CC", None),
    ]
    fiches = [
        ("LOT2-45-A", "Grand-voile atelier un"),
        ("LOT2-45-B", "Génois atelier deux"),
        ("LOT2-45-C", "Spinnaker atelier trois"),
        ("LOT2-451", "Grand-voile atelier quatre"),
        ("LOT2-45CC", "Grand-voile atelier cinq"),
    ]
    resultat: dict[str, dict[str, int]] = {}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT id_client FROM client ORDER BY id_client LIMIT 1")
            id_client = int(cursor.fetchone()[0])
            cursor.execute("SELECT id_type_voile FROM type_voile ORDER BY id_type_voile LIMIT 1")
            id_type = int(cursor.fetchone()[0])
            for (nom, taille), (code, titre) in zip(bateaux, fiches, strict=True):
                cursor.execute(
                    "INSERT INTO bateau (nom, taille) VALUES (%s, %s) RETURNING id_bateau",
                    (nom, taille),
                )
                id_bateau = int(cursor.fetchone()[0])
                cursor.execute(
                    "INSERT INTO fiche "
                    "(code, titre, id_type_voile, gamme, id_client, id_bateau, date_edition, statut) "
                    "VALUES (%s, %s, %s, 'Régate', %s, %s, make_date(2026, 1, 15), 'valide') "
                    "RETURNING id_fiche",
                    (code, titre, id_type, id_client, id_bateau),
                )
                id_fiche = int(cursor.fetchone()[0])
                cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (id_fiche,))
                resultat[code] = {"id_bateau": id_bateau, "id_fiche": id_fiche}
    return resultat


def _codes(reponse: dict[str, Any]) -> set[str]:
    return {str(r["code"]) for r in reponse["resultats"]}


def _modele(index: Any, texte: str):  # noqa: ANN401, ANN202
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            modele = resoudre_modele_depuis_texte(cursor, texte)
    assert modele is not None, f"modèle non résolu : {texte!r}"
    return modele


@pytest.mark.postgres
def test_lot2_reindex_suggestions_recherche_et_filtre_modele(
    base_recherche: dict[str, Any],
) -> None:
    index = base_recherche["index"]
    _ajouter_bateaux_et_fiches(index)

    simulation = rapport_reindex(index, appliquer=False)
    assert simulation["dry_run"] is True
    assert simulation["avant"]["bateaux_sans_modele"] >= 8
    assert simulation["apres"]["bateaux_sans_modele"] == simulation["avant"]["bateaux_sans_modele"]

    application = rapport_reindex(index, appliquer=True)
    assert application["dry_run"] is False
    assert application["actions"]["bateaux_lies"] >= 8
    assert application["apres"]["bateaux_sans_modele"] == 0
    assert application["apres"]["modeles_actifs"] >= 6  # 3 du corpus + 45 + 45.1 + 45 CC

    modele45 = _modele(index, "Bav. 45")
    modele451 = _modele(index, "Bavaria 45.1")
    modele45cc = _modele(index, "BAVARIA 45CC")
    assert modele45.nom_canonique == "Bavaria 45"
    assert len({modele45.id_modele, modele451.id_modele, modele45cc.id_modele}) == 3

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            assurer_alias(cursor, modele45.id_modele, "Bavaria Cruiser 45", source="test_lot2")
            with pytest.raises(AliasBateauEnConflit):
                assurer_alias(cursor, modele451.id_modele, "Bavaria Cruiser 45", source="test_lot2")
    invalider_cache_modeles_bateaux()

    exactes = suggerer(index, "Bavaria 45", limite=10)["suggestions"]
    bateaux_exact = [s for s in exactes if s["nature"] == "bateau"]
    assert bateaux_exact[0]["valeur"] == "Bavaria 45"
    assert bateaux_exact[0]["type_correspondance"] == "exact"
    assert {s["valeur"] for s in bateaux_exact} >= {"Bavaria 45", "Bavaria 45.1", "Bavaria 45 CC"}
    rangs = [int(s["rang"]) for s in bateaux_exact]
    assert rangs == sorted(rangs), "exact doit précéder préfixe/flou"

    alias = suggerer(index, "Bavaria Cruiser 45", limite=5)["suggestions"]
    assert alias[0]["valeur"] == "Bavaria 45"
    assert alias[0]["type_correspondance"] == "alias"
    assert alias[0]["alias"] == "Bavaria Cruiser 45"

    prefixes = suggerer(index, "Bav", limite=10)["suggestions"]
    assert any(
        s["valeur"] == "Bavaria 45" and s["type_correspondance"] == "prefixe" for s in prefixes
    )

    floues = suggerer(index, "bavariaa 45", limite=10)["suggestions"]
    assert any(
        s["valeur"] == "Bavaria 45" and s["type_correspondance"] == "flou" for s in floues
    )

    spi = suggerer(index, "spi", limite=10)["suggestions"]
    assert any(
        s["valeur"] == "Spinnaker" and s["type_correspondance"] == "alias" for s in spi
    ), "le synonyme spi doit proposer le libellé canonique réellement présent"

    par_alias = rechercher_fiches(index, requete="Bavaria Cruiser 45", limit=20)
    assert CODES_45 <= _codes(par_alias)
    assert "modele_bateau" in par_alias["sources_actives"]
    assert par_alias["modeles_bateaux_detectes"][0]["nom_canonique"] == "Bavaria 45"
    detail = next(r for r in par_alias["resultats"] if r["code"] == "LOT2-45-A")
    assert detail["modele_bateau"] == "Bavaria 45"
    assert detail["id_modele_bateau"] == modele45.id_modele
    assert "modele_bateau" in detail["sources"]

    exacte451 = rechercher_fiches(index, requete="Bavaria 45.1", limit=20)
    assert [m["nom_canonique"] for m in exacte451["modeles_bateaux_detectes"]] == ["Bavaria 45.1"]
    assert "LOT2-451" in _codes(exacte451)

    floue = rechercher_fiches(index, requete="bavariaa 45", limit=20)
    assert "LOT2-45-A" in _codes(floue)
    assert any(
        m["nom_canonique"] == "Bavaria 45" and m["type_correspondance"] == "flou"
        for m in floue["modeles_bateaux_detectes"]
    )

    filtree = rechercher_fiches(index, requete="", filtres={"bateau": "Bav. 45"}, limit=20)
    assert _codes(filtree) == CODES_45


@pytest.mark.postgres
def test_lot2_fusion_historisee_reversible(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    ajoutes = _ajouter_bateaux_et_fiches(index)
    rapport_reindex(index, appliquer=True)
    modele45 = _modele(index, "Bavaria 45")
    modele451 = _modele(index, "Bavaria 45.1")
    id_bateau451 = ajoutes["LOT2-451"]["id_bateau"]

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            fusion = fusionner_modeles(
                cursor,
                modele451.id_modele,
                modele45.id_modele,
                motif="décision explicite du test Lot 2",
            )
            assert fusion["bateaux_relie"] == 1
            cursor.execute("SELECT boat_model_id FROM bateau WHERE id_bateau = %s", (id_bateau451,))
            assert int(cursor.fetchone()[0]) == modele45.id_modele
            cursor.execute(
                "SELECT actif, id_modele_fusion FROM boat_models WHERE id_modele = %s",
                (modele451.id_modele,),
            )
            assert cursor.fetchone() == (False, modele45.id_modele)
            cursor.execute(
                "SELECT count(*) FROM boat_model_merge_history WHERE action = 'fusion'"
            )
            assert int(cursor.fetchone()[0]) == 1
    invalider_cache_modeles_bateaux()
    fusionnee = rechercher_fiches(index, requete="", filtres={"bateau": "Bavaria 45"}, limit=20)
    assert "LOT2-451" in _codes(fusionnee)

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            defusion = defusionner_modele(
                cursor, modele451.id_modele, motif="annulation prouvée du test Lot 2"
            )
            assert defusion["id_historique_origine"] == fusion["id_historique"]
            cursor.execute("SELECT boat_model_id FROM bateau WHERE id_bateau = %s", (id_bateau451,))
            assert int(cursor.fetchone()[0]) == modele451.id_modele
            cursor.execute(
                "SELECT actif, id_modele_fusion FROM boat_models WHERE id_modele = %s",
                (modele451.id_modele,),
            )
            assert cursor.fetchone() == (True, None)
            cursor.execute(
                "SELECT action FROM boat_model_merge_history ORDER BY id_historique"
            )
            assert [ligne[0] for ligne in cursor.fetchall()] == ["fusion", "defusion"]
    invalider_cache_modeles_bateaux()
    restauree = rechercher_fiches(index, requete="", filtres={"bateau": "Bavaria 45"}, limit=20)
    assert "LOT2-451" not in _codes(restauree)


@pytest.mark.postgres
def test_lot2_ecriture_fiche_lie_le_bateau_au_modele(base_recherche: dict[str, Any]) -> None:
    from seamtech_search.fiches.modeles import FicheExtraite
    from seamtech_search.fiches.persistance import ecrire_fiche

    index = base_recherche["index"]
    fiche = FicheExtraite(
        code="LOT2-PERS-001",
        titre="Grand-voile écrite par la persistance",
        bateau_nom="BAVARIA-45",
        bateau_taille="45 pieds",
        fichier_pdf="synthese-lot2.pdf",
    )
    id_fiche, _action = ecrire_fiche(index, fiche)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT b.nom, b.boat_model_id, bm.nom_canonique "
                "FROM fiche f JOIN bateau b ON b.id_bateau = f.id_bateau "
                "LEFT JOIN boat_models bm ON bm.id_modele = b.boat_model_id "
                "WHERE f.id_fiche = %s",
                (id_fiche,),
            )
            nom, id_modele, canonique = cursor.fetchone()
    assert nom == "BAVARIA-45", "le libellé source n'est pas réécrit"
    assert id_modele is not None
    assert canonique == "Bavaria 45"


@pytest.mark.postgres
def test_lot2_endpoints_suggestions_et_referentiel(base_recherche: dict[str, Any]) -> None:
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.comptes.comptes import IDENTIFIANT_SECOURS, creer_utilisateur, ouvrir_session
    from seamtech_search.config import AppConfig

    index = base_recherche["index"]
    rapport_reindex(index, appliquer=True)
    compte_admin = creer_utilisateur(
        index,
        "lot2-admin",
        "Admin Lot2",
        "Secret!2026",
        role="admin",
        doit_changer_mot_de_passe=False,
    )
    compte_operateur = creer_utilisateur(
        index,
        "lot2-operateur",
        "Opérateur Lot2",
        "Secret!2026",
        role="approbateur",
        doit_changer_mot_de_passe=False,
    )
    admin = {
        **ouvrir_session(index, int(compte_admin["id_utilisateur"])),
        "identifiant": compte_admin["identifiant"],
    }
    operateur = {
        **ouvrir_session(index, int(compte_operateur["id_utilisateur"])),
        "identifiant": compte_operateur["identifiant"],
    }

    config = AppConfig(
        root_paths=[Path("/tmp")],
        database_url=base_recherche["url"],
        auth_token="jeton-service-lot2",
        min_free_bytes=0,
    )
    entetes_service = {"X-SEAMTECH-TOKEN": "jeton-service-lot2"}

    def entetes(session: dict[str, Any], role: str) -> dict[str, str]:
        return {
            **entetes_service,
            "X-SEAMTECH-UTILISATEUR": str(session["identifiant"]),
            "X-SEAMTECH-ROLE": role,
            "X-SEAMTECH-SESSION": str(session["id_session"]),
            "X-SEAMTECH-SESSION-JETON": str(session["jeton"]),
        }

    secours = {
        **entetes_service,
        "X-SEAMTECH-UTILISATEUR": IDENTIFIANT_SECOURS,
        "X-SEAMTECH-ROLE": "admin",
    }
    with TestClient(create_app(config)) as client:
        ancien = client.get(
            "/recherche/suggestions", params={"prefix": "First"}, headers=entetes_service
        )
        nouveau = client.get(
            "/search/suggestions", params={"prefix": "First"}, headers=entetes_service
        )
        assert ancien.status_code == nouveau.status_code == 200
        assert nouveau.json() == ancien.json()

        liste = client.get("/referentiel/bateaux", headers=entetes_service)
        assert liste.status_code == 200, liste.text
        modeles = liste.json()["modeles"]
        first30 = next(m for m in modeles if m["nom_canonique"] == "First 30")

        refuse_service = client.post(
            f"/referentiel/bateaux/{first30['id_modele']}/alias",
            json={"alias": "First Thirty"},
            headers=entetes_service,
        )
        assert refuse_service.status_code == 401
        refuse_operateur = client.post(
            f"/referentiel/bateaux/{first30['id_modele']}/alias",
            json={"alias": "First Thirty"},
            headers=entetes(operateur, "approbateur"),
        )
        assert refuse_operateur.status_code == 403

        cree = client.post(
            f"/referentiel/bateaux/{first30['id_modele']}/alias",
            json={"alias": "First Thirty"},
            headers=entetes(admin, "admin"),
        )
        assert cree.status_code == 200, cree.text
        assert cree.json()["alias"] == "First Thirty"

        conflit = client.post(
            f"/referentiel/bateaux/{first30['id_modele']}/alias",
            json={"alias": "Sun Fast 36"},
            headers=secours,
        )
        assert conflit.status_code == 409
