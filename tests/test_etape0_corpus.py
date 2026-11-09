"""Épreuves de l'accumulateur de l'Étape 0 (``scripts/etape0_corpus.py``).

Deux familles d'épreuves :

* la logique de l'accumulateur, qui ne touche à rien hors du fichier ;
* le dépôt en base, sur une base PostgreSQL jetable — c'est ce qui prouve que
  ``SELECT count(*) FROM fiche`` peut atteindre un compte réel, et que ce
  compte ne double pas quand on rejoue.

Une épreuve qui ne peut pas échouer ne prouve rien : chacune a été vérifiée en
cassant volontairement le code qu'elle couvre (voir le message du commit).
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import uuid

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "scripts"))

import etape0_corpus as e0  # noqa: E402


# ---------------------------------------------------------------------------
# Logique de l'accumulateur
# ---------------------------------------------------------------------------


def test_identifiant_est_deterministe_et_distinct():
    """Deux chemins différents ne partagent jamais un identifiant."""
    sel = "sel-de-test"
    assert e0.identifiant(sel, "/VOILES 2024/A") == e0.identifiant(sel, "/VOILES 2024/A")
    assert e0.identifiant(sel, "/VOILES 2024/A") != e0.identifiant(sel, "/VOILES 2024/B")


def test_decouper_racine_separe_famille_et_annee():
    assert e0.decouper_racine("VOILES 2024") == ("voiles", 2024)
    assert e0.decouper_racine("ACCESSOIRES 2025") == ("accessoires", 2025)


def test_racine_inconnue_est_rejetee():
    with pytest.raises(ValueError, match="Racine inattendue"):
        e0.decouper_racine("RACINE INVENTEE 2024")


def test_categorie_decoule_de_la_famille():
    assert e0.categorie_de_racine("voiles") == "voile"
    assert e0.categorie_de_racine("accessoires") == "accessoire"


def test_ajouter_cree_puis_met_a_jour_sans_doublon():
    """Rejouer le même dossier met à jour la ligne au lieu d'en créer une seconde."""
    acc = e0.nouvel_accumulateur()
    chemin = "/VOILES 2024/UN DOSSIER"

    acc, cree1 = e0.ajouter_fiche(
        acc, chemin=chemin, racine="VOILES 2024", source="dropbox", gamme="C0"
    )
    assert cree1 is True
    assert len(acc["fiches"]) == 1

    acc, cree2 = e0.ajouter_fiche(
        acc, chemin=chemin, racine="VOILES 2024", source="dropbox", gamme="C3"
    )
    assert cree2 is False
    assert len(acc["fiches"]) == 1, "un second relevé du même dossier ne doit pas le compter deux fois"
    assert acc["fiches"][0]["gamme"] == "C3", "la valeur la plus récente doit gagner"


def test_ajouter_refuse_une_source_inventee():
    acc = e0.nouvel_accumulateur()
    with pytest.raises(ValueError, match="Source inattendue"):
        e0.ajouter_fiche(
            acc, chemin="/VOILES 2024/A", racine="VOILES 2024", source="invention"
        )


def test_ajouter_refuse_une_categorie_hors_contrainte():
    acc = e0.nouvel_accumulateur()
    with pytest.raises(ValueError, match="Catégorie inattendue"):
        e0.ajouter_fiche(
            acc,
            chemin="/VOILES 2024/A",
            racine="VOILES 2024",
            source="dropbox",
            categorie="catamaran",
        )


def test_statistiques_comptent_par_racine_et_par_axe():
    acc = e0.nouvel_accumulateur()
    e0.ajouter_fiche(
        acc, chemin="/VOILES 2024/A", racine="VOILES 2024", source="dropbox",
        gamme="C0", type_voile="Grand-voile", matiere="Dacron",
    )
    e0.ajouter_fiche(
        acc, chemin="/ACCESSOIRES 2025/B", racine="ACCESSOIRES 2025", source="dropbox",
        type_voile=None,
    )
    stats = e0.statistiques(acc)
    assert stats["total"] == 2
    assert stats["par_racine"] == {"VOILES 2024": 1, "ACCESSOIRES 2025": 1}
    assert stats["axes_renseignes"] == {"gamme": 1, "type_voile": 1, "matiere": 1}


def test_accumulateur_ne_conserve_jamais_le_chemin_nominatif(tmp_path):
    """La règle de confidentialité, tenue par une épreuve et non par une promesse."""
    chemin = "/VOILES 2024/NOM DU BATEAU ST999"
    fichier = tmp_path / "acc.json"
    acc = e0.nouvel_accumulateur()
    e0.ajouter_fiche(acc, chemin=chemin, racine="VOILES 2024", source="dropbox")
    e0.sauvegarder_accumulateur(acc, fichier)

    brut = fichier.read_text(encoding="utf-8")
    assert "NOM DU BATEAU" not in brut, "le nom du dossier ne doit jamais être écrit"
    assert "ST999" not in brut
    # Le chemin ne subsiste que sous forme d'empreinte.
    assert e0.identifiant(acc["sel"], chemin) in brut


def test_sauvegarde_puis_relecture_rendent_le_meme_contenu(tmp_path):
    fichier = tmp_path / "acc.json"
    acc = e0.nouvel_accumulateur()
    e0.ajouter_fiche(
        acc, chemin="/VOILES 2026/A", racine="VOILES 2026", source="archive", matiere="Dacron"
    )
    e0.sauvegarder_accumulateur(acc, fichier)
    relu = e0.charger_accumulateur(fichier)
    assert relu == acc


# ---------------------------------------------------------------------------
# Dépôt en base (PostgreSQL jetable)
# ---------------------------------------------------------------------------

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")


def _creer_base_jetable(nom: str) -> str:
    """Crée une base neuve et migrée ; rend son URL."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    from psycopg2 import sql

    admin_url = os.environ["SEAMTECH_TEST_DATABASE_URL"]
    admin = psycopg2.connect(admin_url)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(nom)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    admin.close()

    # L'URI de pgserver porte l'hôte en paramètre de requête
    # (« …/postgres?host=/chemin/vers/pgdata ») : un simple rsplit('/') la
    # casserait. On recompose donc proprement en ne remplaçant que le dbname.
    from urllib.parse import urlsplit

    parties = urlsplit(admin_url)
    base = f"{parties.scheme}://{parties.netloc}/{nom}"
    if parties.query:
        base += f"?{parties.query}"

    sys.path.insert(0, RACINE)
    from seamtech_search.indexer import SearchIndex

    ix = SearchIndex(__import__("pathlib").Path(f"/tmp/etape0-{nom}.db"), base)
    ix.initialize()
    ix.run_migrations()
    ix.close()
    return base


def _index(base: str):
    from pathlib import Path

    sys.path.insert(0, RACINE)
    from seamtech_search.indexer import SearchIndex

    return SearchIndex(Path(f"/tmp/etape0-{uuid.uuid4().hex[:8]}.db"), base)


@pytest.fixture(scope="module")
def base_jetable():
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom = "etape0_" + uuid.uuid4().hex[:10]
    base = _creer_base_jetable(nom)
    yield base
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    from psycopg2 import sql

    admin = psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(nom)))
    admin.close()


def _compter(base: str, table: str) -> int:
    import psycopg2

    with psycopg2.connect(base) as cx:
        with cx.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table}")
            return int(cur.fetchone()[0])


def test_deposer_ecrit_les_fiches_et_prouve_le_compte(base_jetable):
    acc = e0.nouvel_accumulateur()
    for i in range(12):
        e0.ajouter_fiche(
            acc,
            chemin=f"/VOILES 2024/DOSSIER {i}",
            racine="VOILES 2024",
            source="dropbox",
            gamme="C0" if i % 2 else "C3",
            type_voile="Grand-voile",
            matiere="Dacron",
        )
    ix = _index(base_jetable)
    try:
        comptes = e0.deposer(acc, ix)
    finally:
        ix.close()

    assert comptes["fiches_ecrites"] == 12
    assert comptes["extractions_ecrites"] == 12
    # La preuve attendue par l'Étape 0 : le compte réel en base.
    assert comptes["fiche_total"] == 12
    assert _compter(base_jetable, "fiche") == 12


def test_rejouer_deposer_ne_double_pas_le_compte(base_jetable):
    """C'est l'épreuve qui rend le relevé multi-sessions sûr."""
    acc = e0.nouvel_accumulateur()
    for i in range(5):
        e0.ajouter_fiche(
            acc, chemin=f"/ACCESSOIRES 2026/DOSSIER {i}",
            racine="ACCESSOIRES 2026", source="dropbox",
        )
    ix = _index(base_jetable)
    try:
        e0.deposer(acc, ix)
        e0.deposer(acc, ix)
        comptes = e0.deposer(acc, ix)
    finally:
        ix.close()
    assert comptes["fiche_total"] == 5, "trois dépôts de suite ne doivent pas empiler les fiches"


def test_deposer_ne_fface_pas_une_extraction_reelle(base_jetable):
    """Le ménage est ciblé : une extraction du pipeline survit à ``charger``."""
    import psycopg2

    marqueur = "pipeline-reel-" + uuid.uuid4().hex[:8]
    with psycopg2.connect(base_jetable) as cx:
        with cx.cursor() as cur:
            cur.execute(
                "INSERT INTO fiche (code, titre, categorie) VALUES (%s, %s, %s) RETURNING id_fiche",
                (marqueur, "extraction réelle", "voile"),
            )
            id_fiche = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO sail_extractions (
                    cle_idempotence, id_fiche, version_extracteur, methode_principale,
                    materiaux, champs, anomalies, statut_extraction,
                    statut_plausibilite, statut_liaison
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    f"fiche:{id_fiche}:lot3a-v1", id_fiche, "lot3a-v1", "gabarit",
                    json.dumps([]), json.dumps([]), json.dumps([]),
                    "complete", "plausible", "sans_modele",
                ),
            )
        cx.commit()

    acc = e0.nouvel_accumulateur()
    e0.ajouter_fiche(acc, chemin="/VOILES 2024/AUTRE", racine="VOILES 2024", source="dropbox")
    ix = _index(base_jetable)
    try:
        e0.deposer(acc, ix)
    finally:
        ix.close()

    with psycopg2.connect(base_jetable) as cx:
        with cx.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM sail_extractions WHERE cle_idempotence = %s",
                (f"fiche:{id_fiche}:lot3a-v1",),
            )
            assert cur.fetchone()[0] == 1, "une extraction réelle ne doit pas être effacée"


# ---------------------------------------------------------------------------
# Arbitrage : le refus de fusion doit être opposable et définitif
# ---------------------------------------------------------------------------


def test_cles_de_valeur_ignorent_casse_et_espaces():
    """Sinon un refus serait contournable en changeant une majuscule."""
    assert e0._cle_de_valeur("Palma Tec") == e0._cle_de_valeur("  PALMA   TEC  ")
    assert e0._cle_de_valeur("Palma Tec") != e0._cle_de_valeur("PALMA TEC 18/13")


def test_refus_de_fusion_est_symetrique():
    """Refuser (A, B) doit refuser (B, A), sinon le garde-fou se contourne."""
    dec = e0.charger_decisions(pathlib.Path("/tmp/inexistant-etape0.json"))
    dec = e0.refuser_fusion(dec, axe="gamme", valeur_a="enrouleur",
                            valeur_b="enrouleur jouet 10.80", motif="deux gammes distinctes")
    assert e0.est_fusion_refusee(dec, "gamme", "enrouleur", "enrouleur jouet 10.80")
    assert e0.est_fusion_refusee(dec, "gamme", "enrouleur jouet 10.80", "enrouleur")
    # et insensible à la casse
    assert e0.est_fusion_refusee(dec, "gamme", "ENROULEUR", "Enrouleur Jouet 10.80")


def test_un_refus_sans_motif_n_est_pas_opposable():
    dec = e0.charger_decisions(pathlib.Path("/tmp/inexistant-etape0.json"))
    with pytest.raises(ValueError, match="sans motif"):
        e0.refuser_fusion(dec, axe="gamme", valeur_a="a", valeur_b="b", motif="   ")


def test_axe_inconnu_refuse():
    dec = e0.charger_decisions(pathlib.Path("/tmp/inexistant-etape0.json"))
    with pytest.raises(ValueError, match="Axe inattendu"):
        e0.refuser_fusion(dec, axe="catamaran", valeur_a="a", valeur_b="b", motif="x")


def test_filtrer_ecarte_la_fusion_refusee():
    """Le garde-fou : une fusion refusée ne doit plus jamais être appliquée."""
    dec = e0.charger_decisions(pathlib.Path("/tmp/inexistant-etape0.json"))
    dec = e0.refuser_fusion(dec, axe="gamme", valeur_a="enrouleur",
                            valeur_b="enrouleur jouet 10.80", motif="deux gammes distinctes")
    propositions = [
        {"ecriture_retenue": "enrouleur", "ecritures_a_remplacer": ["enrouleur jouet 10.80"]},
        {"ecriture_retenue": "PALMA TEC 18/13", "ecritures_a_remplacer": ["Palma Tec 18/13"]},
    ]
    applicables, ecartees = e0.filtrer_propositions(dec, "gamme", propositions)
    assert len(applicables) == 1, "la fusion matière reste applicable"
    assert applicables[0]["ecriture_retenue"] == "PALMA TEC 18/13"
    assert len(ecartees) == 1, "la fusion refusée doit être écartée"
    assert ecartees[0]["ecriture_retenue"] == "enrouleur"


def test_filtrer_laisse_passer_une_fusion_jamais_refusee():
    dec = e0.charger_decisions(pathlib.Path("/tmp/inexistant-etape0.json"))
    propositions = [{"ecriture_retenue": "PALMA TEC 18/13",
                     "ecritures_a_remplacer": ["Palma Tec 18/13"]}]
    applicables, ecartees = e0.filtrer_propositions(dec, "matiere", propositions)
    assert len(applicables) == 1 and ecartees == []


def test_le_refus_survit_a_la_sauvegarde(tmp_path):
    fichier = tmp_path / "decisions.json"
    dec = e0.charger_decisions(fichier)
    dec = e0.refuser_fusion(dec, axe="matiere", valeur_a="CZ 30", valeur_b="CZ30",
                            motif="références produit distinctes")
    e0.sauvegarder_decisions(dec, fichier)
    relu = e0.charger_decisions(fichier)
    assert e0.est_fusion_refusee(relu, "matiere", "CZ 30", "CZ30")


def test_axes_admis_ne_divergent_pas_de_l_instrument_1_1():
    """Une divergence rendrait le garde-fou muet sur un axe entier."""
    import mesurer_correspondances as mc

    assert tuple(e0.AXES_ADMIS) == tuple(mc.AXES), (
        "les axes de l'arbitrage ont divergé de ceux de l'instrument 1.1"
    )


def test_le_refus_resiste_a_la_ponctuation_et_a_l_accent():
    """Régression réelle : la clé du garde-fou doit être CELLE de l'instrument.

    Un refus enregistré sur « enrouleur jouet 10.80 » (point) ne retenait pas
    la valeur « enrouleur jouet 10 80 » (espace) que produit l'instrument
    après normalisation : le refus passait au travers et la fusion refusée
    restait applicable. Le garde-fou était muet. Cette épreuve l'interdit.
    """
    from seamtech_search.qualite.cartographie import cle_normalisation

    dec = e0.charger_decisions(pathlib.Path("/tmp/inexistant-etape0.json"))
    dec = e0.refuser_fusion(dec, axe="gamme", valeur_a="enrouleur",
                            valeur_b="enrouleur jouet 10.80", motif="deux gammes distinctes")
    assert e0.est_fusion_refusee(dec, "gamme", "enrouleur", "enrouleur jouet 10 80")
    # la clé employée est bien celle de l'instrument 1.1, pas une approximation
    assert e0._cle_de_valeur("enrouleur jouet 10.80") == cle_normalisation("enrouleur jouet 10.80")
    # et les accents sont repliés
    dec2 = e0.refuser_fusion(e0.charger_decisions(pathlib.Path("/tmp/inexistant-etape0.json")),
                             axe="matiere", valeur_a="semi-latté", valeur_b="semi latte",
                             motif="même notion")
    assert e0.est_fusion_refusee(dec2, "matiere", "SEMI LATTE", "semi latté")


def test_assainissement_masque_le_nom_capture_par_debordement():
    """Un débordement d'extraction ne doit jamais faire entrer un nom dans Git.

    Constaté en conditions réelles : la gamme lue valait « enrouleur <marque>
    9 mètres », la marque du bateau s'était glissée dans le champ. Le fichier
    versionné la portait. L'assainisseur remplace ce qui suit le premier mot
    par un marqueur neutre : la preuve du défaut d'extraction subsiste, le
    nom disparaît.
    """
    assert e0.assainir_valeur_axe("gamme", "enrouleur Jouet 10.80") == "enrouleur <nom propre> <nom propre>"
    assert e0.assainir_valeur_axe("gamme", "enrouleur Kelt 9 mètres") == "enrouleur <nom propre> <nom propre> <nom propre>"
    # une vraie gamme en un ou deux mots est respectée
    assert e0.assainir_valeur_axe("gamme", "Fullbatten") == "Fullbatten"
    assert e0.assainir_valeur_axe("gamme", "Semi-lattée") == "Semi-lattée"


def test_assainissement_ne_touche_pas_les_autres_axes():
    """La matière porte des désignations longues et légitimes ; on n'y touche pas."""
    v = "STORMLITE 210 ROUGE"
    assert e0.assainir_valeur_axe("matiere", v) == v
    assert e0.assainir_valeur_axe("type_voile", "Grand-voile") == "Grand-voile"


def test_l_assainissement_est_applique_a_la_consignation(tmp_path):
    """Sinon il existe mais ne sert à rien : c'est le branchement qu'on éprouve."""
    fichier = tmp_path / "acc.json"
    acc = e0.nouvel_accumulateur()
    e0.ajouter_fiche(acc, chemin="/VOILES 2025/UN", racine="VOILES 2025",
                     source="dropbox", gamme="enrouleur Jouet 10.80")
    e0.sauvegarder_accumulateur(acc, fichier)
    brut = fichier.read_text(encoding="utf-8")
    assert "Jouet" not in brut
    assert e0.MARQUEUR_NOM_PROPRE in brut
