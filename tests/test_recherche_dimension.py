"""Phase 1 — recherche par dimension (migration 019) : tests D'ABORD.

« Je tape une dimension dans la barre et je vois toutes les voiles
correspondantes. »

Ce fichier couvre trois niveaux :

1. ``TestAnalyseRequeteDimension`` (sans serveur) — normalisation côté requête :
   « 6,6 », « 6.60 », « 6,60 m », « 660 cm », « 6600 mm » convergent vers la
   même valeur ; les entiers nus (années, codes « 7792 », « 0701 ») ne sont
   JAMAIS des dimensions — sinon « spi sailonet 2026 » et « 7792-SO »
   basculeraient dans le filtre numérique et tomberaient à zéro résultat.

2. ``TestTokenisationReelle`` (postgres) — garde du test de tokenisation RÉEL
   publié (PostgreSQL 16.2, config ``seamtech_unaccent``) : la virgule coupe
   en deux lexèmes (``'6' <-> '60'``), le point garde un float unique
   (``'6.60'``), et les deux formes NE SE CROISENT JAMAIS — d'où l'exigence
   d'indexer les deux formes dans le texte pondéré (migration 019).

3. ``TestRechercheDimensionPostgres`` (postgres) — le parcours fonctionnel :
   valeur exacte, virgule/point, unités, tolérance ±0,5 % sur TOUTES les
   cotes (chemin numérique existant, jamais tsvector pour la valeur), cote
   nommée (« SLU 6,60 » → slu_m seul), mots + dimension (« spi 6,60 »),
   aucune régression des autres requêtes.

Le rappel 50/50 SYNTHÉTIQUE et 13/13 RÉEL sont gardés par leurs fichiers
d'origine (test_recherche_hybride.py / test_recherche_fonds_reel.py) ; ils
doivent rester verts après la migration 019.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import pytest

from seamtech_search.recherche import (
    COTES_AUTORISEES,
    TOLERANCE_DIMENSION,
    RequeteDimension,
    analyser_requete_dimension,
    rechercher_fiches,
)

# ---------------------------------------------------------------------------
# Partie 1 — normalisation côté requête (sans serveur)
# ---------------------------------------------------------------------------


class TestAnalyseRequeteDimension:
    """« 6,6 », « 6.60 », « 6,60 m », « 660 cm », « 6600 mm » → même valeur."""

    def test_decimal_virgule_et_point_convergent(self) -> None:
        for texte in ("6,60", "6.60", "6,6", "6.6"):
            analyse = analyser_requete_dimension(texte)
            assert analyse.valeur is not None, f"{texte!r} doit être reconnu comme dimension"
            assert float(analyse.valeur_convergente()) == pytest.approx(6.6), texte
            assert analyse.texte == "", f"{texte!r} est une requête numérique seule"

    def test_unites_longueur_convergent(self) -> None:
        attendu = pytest.approx(6.6)
        for texte in ("6,60 m", "660 cm", "6600 mm", "6.60m", "660cm", "6 600 mm"):
            analyse = analyser_requete_dimension(texte)
            assert analyse.valeur is not None, f"{texte!r} doit être reconnu comme dimension"
            assert float(analyse.valeur_convergente()) == attendu, texte
            assert analyse.texte == "", f"{texte!r} est une requête numérique seule"

    def test_valeur_convergente_unique_pour_toutes_les_formes(self) -> None:
        valeurs = {
            float(analyser_requete_dimension(t).valeur_convergente())
            for t in ("6,6", "6.60", "6,60 m", "660 cm", "6600 mm")
        }
        assert len(valeurs) == 1, f"les cinq formes doivent converger, obtenu {valeurs}"

    def test_cote_nommee_oriente_la_recherche(self) -> None:
        assert analyser_requete_dimension("SLU 6,60").cote == "slu_m"
        assert analyser_requete_dimension("slu 6,60").cote == "slu_m"
        assert analyser_requete_dimension("têtière 15.5").cote == "tetiere_cm"
        assert analyser_requete_dimension("tetiere 15.5").cote == "tetiere_cm"
        assert analyser_requete_dimension("poids 3,4").cote == "poids_kg"
        assert analyser_requete_dimension("spa 15.71").cote == "spa_m2"
        assert analyser_requete_dimension("guindant 6,60").cote == "slu_m"

    def test_entier_nu_avec_cote_nominee_est_une_dimension(self) -> None:
        # « tetiere 15 » : l'alias de cote désambiguïse l'entier — pas une année.
        analyse = analyser_requete_dimension("tetiere 15")
        assert analyse.valeur == 15.0
        assert analyse.cote == "tetiere_cm"
        assert analyser_requete_dimension("poids 3").valeur == 3.0

    def test_cote_nommee_retire_l_alias_du_texte_restant(self) -> None:
        analyse = analyser_requete_dimension("SLU 6,60")
        assert analyse.texte == ""
        analyse = analyser_requete_dimension("spi SLU 6,60")
        assert analyse.cote == "slu_m"
        assert analyse.texte == "spi"

    def test_spi_sans_cote_nominee_rest_avec_les_mots(self) -> None:
        # « spi 6,60 » : « spi » n'est PAS une cote (c'est le type de voile) —
        # il reste un mot porté par les sources textuelles, la valeur bascule
        # sur le chemin numérique (toutes cotes ±0,5 %).
        analyse = analyser_requete_dimension("spi 6,60")
        assert analyse.valeur is not None
        assert analyse.cote is None
        assert analyse.texte == "spi"

    def test_entiers_nus_ne_sont_jamais_des_dimensions(self) -> None:
        # Années, codes, quantités : jamais de filtre numérique implicite.
        for texte in ("2026", "7792", "spi sailonet 2026", "7792-SO", "0701-GV-001", "29er"):
            analyse = analyser_requete_dimension(texte)
            assert analyse.valeur is None, f"{texte!r} ne doit PAS basculer dans le filtre dimension"
            assert analyse.cote is None

    def test_mots_seuls_ou_alphanumeriques(self) -> None:
        for texte in ("grand voile", "monofilm k903", "monofime", "voile de portant"):
            analyse = analyser_requete_dimension(texte)
            assert analyse.valeur is None
            assert analyse.texte == texte

    def test_unite_attachee_sans_espace(self) -> None:
        analyse = analyser_requete_dimension("6.60m")
        assert analyse.valeur is not None
        assert analyse.unite == "m"

    def test_surface_et_poids(self) -> None:
        analyse = analyser_requete_dimension("15.71 m2")
        assert analyse.valeur == pytest.approx(15.71)
        analyse = analyser_requete_dimension("3,4 kg")
        assert analyse.valeur == pytest.approx(3.4)
        analyse = analyser_requete_dimension("3400 g")
        assert float(analyse.valeur_convergente()) == pytest.approx(3.4)

    def test_conversion_vers_unite_cote_nominee(self) -> None:
        # « tetiere 150 mm » → 15 cm dans l'unité métier de tetiere_cm.
        analyse = analyser_requete_dimension("tetiere 150 mm")
        assert analyse.cote == "tetiere_cm"
        assert float(analyse.valeur_pour_cote("tetiere_cm")) == pytest.approx(15.0)
        # « slu 660 cm » → 6,60 m dans l'unité métier de slu_m.
        analyse = analyser_requete_dimension("slu 660 cm")
        assert float(analyse.valeur_pour_cote("slu_m")) == pytest.approx(6.6)

    def test_tolerance_fixee_a_un_demi_pourcent(self) -> None:
        assert TOLERANCE_DIMENSION == 0.005

    def test_requete_dimension_immuable_et_sans_dimension(self) -> None:
        vide = RequeteDimension()
        assert vide.valeur is None and vide.cote is None and vide.texte == ""
        assert analyser_requete_dimension("").valeur is None

    def test_toutes_les_cotes_sont_couvertes(self) -> None:
        assert COTES_AUTORISEES == {
            "slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_cm", "poids_kg",
        }


# ---------------------------------------------------------------------------
# Partie 2 — garde du test de tokenisation RÉEL (PostgreSQL réel uniquement)
# ---------------------------------------------------------------------------


@pytest.mark.postgres
class TestTokenisationReelle:
    """Résultats expérimentaux publiés — NE PAS modifier sans refaire l'essai.

    PostgreSQL 16.2, config ``seamtech_unaccent`` (unaccent + simple), parser
    par défaut. Sorties brutes : voir
    ``docs/verite_terrain/RAPPORT_RECHERCHE_DIMENSION_2026-09-30.md``.
    """

    def _unaccent_pret(self, base: dict[str, Any]) -> str:
        index = base["index"]
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                config = index._postgres_ts_config(connexion)
                cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'unaccent'")
                if cursor.fetchone() is None:
                    pytest.skip("extension unaccent indisponible sur ce serveur")
        return config

    def test_websearch_virgule_coupe_en_deux_lexemes(self, base_recherche: dict[str, Any]) -> None:
        config = self._unaccent_pret(base_recherche)
        with base_recherche["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT websearch_to_tsquery(%s, '6,60')::text", (config,)
                )
                assert cursor.fetchone()[0] == "'6' <-> '60'"

    def test_websearch_point_garde_un_float_unique(self, base_recherche: dict[str, Any]) -> None:
        config = self._unaccent_pret(base_recherche)
        with base_recherche["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT websearch_to_tsquery(%s, '6.60')::text", (config,)
                )
                assert cursor.fetchone()[0] == "'6.60'"

    def test_les_deux_formes_ne_se_croisent_jamais(self, base_recherche: dict[str, Any]) -> None:
        config = self._unaccent_pret(base_recherche)
        with base_recherche["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT to_tsvector(%s, 'slu 6.60') @@ websearch_to_tsquery(%s, '6,60'),
                           to_tsvector(%s, 'slu 6,60') @@ websearch_to_tsquery(%s, '6.60'),
                           to_tsvector(%s, 'slu 6.60') @@ websearch_to_tsquery(%s, '6.60'),
                           to_tsvector(%s, 'slu 6,60') @@ websearch_to_tsquery(%s, '6,60')
                    """,
                    (config, config, config, config, config, config, config, config),
                )
                croisement_virgule, croisement_point, meme_point, meme_virgule = cursor.fetchone()
                assert croisement_virgule is False, "« 6,60 » ne doit PAS matcher un vecteur « 6.60 »"
                assert croisement_point is False, "« 6.60 » ne doit PAS matcher un vecteur « 6,60 »"
                assert meme_point is True and meme_virgule is True

    def test_double_forme_indexee_matche_les_deux_requetes(self, base_recherche: dict[str, Any]) -> None:
        config = self._unaccent_pret(base_recherche)
        with base_recherche["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT to_tsvector(%s, 'slu 6.60 6,60') @@ websearch_to_tsquery(%s, '6,60'),
                           to_tsvector(%s, 'slu 6.60 6,60') @@ websearch_to_tsquery(%s, '6.60'),
                           to_tsvector(%s, 'slu 6.60 6,60') @@ websearch_to_tsquery(%s, '6.6')
                    """,
                    (config, config, config, config, config, config),
                )
                virgule, point, variante = cursor.fetchone()
                assert virgule is True and point is True
                assert variante is False, "« 6.6 » ne matche ni « 6.60 » ni « 6,60 » — d'où la normalisation requête"


# ---------------------------------------------------------------------------
# Partie 3 — parcours fonctionnel (PostgreSQL réel)
# ---------------------------------------------------------------------------

# Cotes SYNTHÉTIQUES de la fixture (jamais confondues avec le fonds réel) :
# - 0701-GV-001 : slu_m 6,60 EXACT + poids 3,2 + têtière 15 cm ;
# - 0702-GV-003 : slu_m 6,62 (0,30 % — DANS la tolérance ±0,5 %) ;
# - 0801-GEN-001 : slu_m 6,55 (0,76 % — HORS tolérance) ;
# - 0812-SPI-001 : slu_m 7,15 (hors) mais poids_kg 6,60 (la valeur « tombe »
#   sur une AUTRE cote — prouve « toutes les cotes ») ;
# - 1001-GV-006 : slu_m 6,60, statut a_valider (badgée, cherchable depuis 018).
COTES_SEED: dict[str, dict[str, float]] = {
    "0701-GV-001": {"slu_m": 6.60, "poids_kg": 3.2, "tetiere_cm": 15.0},
    "0702-GV-003": {"slu_m": 6.62},
    "0801-GEN-001": {"slu_m": 6.55},
    "0812-SPI-001": {"slu_m": 7.15, "poids_kg": 6.60},
    "1001-GV-006": {"slu_m": 6.60},
}

# Résultat attendu pour la valeur 6,60 sur TOUTES les cotes ±0,5 % :
# 0701-GV-001 (slu 6,60), 0702-GV-003 (slu 6,62), 1001-GV-006 (slu 6,60),
# 0812-SPI-001 (poids 6,60) — jamais 0801-GEN-001 (6,55 hors tolérance).
ATTENDU_660_TOUTES = {"0701-GV-001", "0702-GV-003", "0812-SPI-001", "1001-GV-006"}

# « SLU 6,60 » : cote nommée → le poids 6,60 de 0812-SPI-001 ne compte PAS.
ATTENDU_660_SLU = {"0701-GV-001", "0702-GV-003", "1001-GV-006"}


@pytest.fixture()
def base_dimension(base_recherche: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Corpus Lot E + cotes SYNTHÉTIQUES + rafraîchissement complet (comme le
    backfill de la migration 019)."""
    index = base_recherche["index"]
    ids = base_recherche["fiches"]
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            for code, cotes in COTES_SEED.items():
                cursor.execute(
                    "INSERT INTO fiche_cotes (id_fiche, jeu) VALUES (%s, 'finie') "
                    "ON CONFLICT (id_fiche, jeu) DO NOTHING",
                    (ids[code],),
                )
                for nom, valeur in cotes.items():
                    cursor.execute(
                        f"UPDATE fiche_cotes SET {nom} = %s WHERE id_fiche = %s AND jeu = 'finie'",
                        (valeur, ids[code]),
                    )
            cursor.execute("SELECT rafraichir_texte_recherche_toutes()")
    yield base_recherche


def codes(reponse: dict[str, Any]) -> set[str]:
    return {r["code"] for r in reponse["resultats"]}


@pytest.mark.postgres
class TestRechercheDimensionPostgres:
    def test_valeur_exacte_virgule(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(base_dimension["index"], requete="6,60")
        assert codes(reponse) == ATTENDU_660_TOUTES

    def test_valeur_exacte_point(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(base_dimension["index"], requete="6.60")
        assert codes(reponse) == ATTENDU_660_TOUTES

    def test_variante_un_chiffre(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(base_dimension["index"], requete="6,6")
        assert codes(reponse) == ATTENDU_660_TOUTES

    def test_unites_convergent(self, base_dimension: dict[str, Any]) -> None:
        for requete in ("6,60 m", "660 cm", "6600 mm"):
            reponse = rechercher_fiches(base_dimension["index"], requete=requete)
            assert codes(reponse) == ATTENDU_660_TOUTES, requete

    def test_tolerance_pourcent_demi(self, base_dimension: dict[str, Any]) -> None:
        # 6,62 (0,30 %) est DANS ; 6,55 (0,76 %) est HORS.
        reponse = rechercher_fiches(base_dimension["index"], requete="6,60")
        assert "0702-GV-003" in codes(reponse)
        assert "0801-GEN-001" not in codes(reponse)

    def test_toutes_les_cotes(self, base_dimension: dict[str, Any]) -> None:
        # 0812-SPI-001 : slu 7,15 hors tolérance mais poids_kg 6,60 — trouvé.
        reponse = rechercher_fiches(base_dimension["index"], requete="6,60")
        assert "0812-SPI-001" in codes(reponse)

    def test_requete_numerique_seule_ne_passe_pas_par_tsvector(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(base_dimension["index"], requete="6,60")
        assert reponse["sources_actives"] == ["dimension"], (
            "la valeur doit passer par le chemin numérique (filtre de cote), pas par tsvector"
        )

    def test_cote_nommee_oriente_vers_la_cote(self, base_dimension: dict[str, Any]) -> None:
        for requete in ("SLU 6,60", "slu 6.60", "guindant 6,60"):
            reponse = rechercher_fiches(base_dimension["index"], requete=requete)
            assert codes(reponse) == ATTENDU_660_SLU, requete

    def test_poids_nomme_ne_cherche_que_le_poids(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(base_dimension["index"], requete="poids 6,60")
        assert codes(reponse) == {"0812-SPI-001"}, (
            "« poids 6,60 » ne doit trouver QUE le poids — pas les SLU à 6,60"
        )

    def test_mots_plus_dimension(self, base_dimension: dict[str, Any]) -> None:
        # « spi 6,60 » : le mot « spi » (type de voile, code) ET la valeur.
        reponse = rechercher_fiches(base_dimension["index"], requete="spi 6,60")
        assert codes(reponse) == {"0812-SPI-001"}
        # « grand voile 6,60 » : mots + valeur sur toutes les cotes.
        reponse = rechercher_fiches(base_dimension["index"], requete="grand voile 6,60")
        assert codes(reponse) == {"0701-GV-001", "0702-GV-003", "1001-GV-006"}

    def test_tetiere_unite_convertie(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(base_dimension["index"], requete="tetiere 15")
        assert codes(reponse) == {"0701-GV-001"}
        reponse = rechercher_fiches(base_dimension["index"], requete="têtière 150 mm")
        assert codes(reponse) == {"0701-GV-001"}

    def test_fiche_a_valider_badgee_toujours_trouvee(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(base_dimension["index"], requete="6,60")
        fiche = next(r for r in reponse["resultats"] if r["code"] == "1001-GV-006")
        assert fiche["statut"] == "a_valider"
        reponse = rechercher_fiches(base_dimension["index"], requete="6,60", inclure_a_valider=False)
        assert "1001-GV-006" not in codes(reponse)

    def test_aucune_conquete_des_requetes_annee_et_code(self, base_dimension: dict[str, Any]) -> None:
        # « spi sailonet 2026 »-like : une année ne doit JAMAIS devenir un
        # filtre dimension ; un code avec séparateurs reste un code.
        reponse = rechercher_fiches(base_dimension["index"], requete="0701-GV-001")
        assert "0701-GV-001" in codes(reponse)
        reponse = rechercher_fiches(base_dimension["index"], requete="spinnaker asymétrique sun fast 36")
        assert codes(reponse) == {"0812-SPI-001"}

    def test_texte_des_fiches_contient_les_deux_formes(self, base_dimension: dict[str, Any]) -> None:
        # Migration 019 : le texte pondéré porte « 6.60 » ET « 6,60 ».
        index = base_dimension["index"]
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT champs_texte FROM fiche WHERE code = %s", ("0701-GV-001",)
                )
                texte = cursor.fetchone()[0]
                assert "6.60" in texte, "forme pointe absente du texte de recherche"
                assert "6,60" in texte, "forme virgule absente du texte de recherche"
                cursor.execute(
                    "SELECT search_vector @@ to_tsquery('seamtech_unaccent', '6.60'), "
                    "search_vector @@ to_tsquery('seamtech_unaccent', '6 <-> 60') "
                    "FROM fiche WHERE code = %s",
                    ("0701-GV-001",),
                )
                point, virgule = cursor.fetchone()
                assert point is True and virgule is True

    def test_recherche_textuelle_sans_dimension_inchangee(self, base_dimension: dict[str, Any]) -> None:
        # Les mots restent trouvables ; l'ordre exact entre titres équivalents
        # peut bouger (les cotes allongent le vecteur B — dilution mesurée),
        # l'essentiel est que la fiche reste au premier rang utile.
        reponse = rechercher_fiches(base_dimension["index"], requete="grand voile")
        assert "0701-GV-001" in codes(reponse)
        assert reponse["resultats"][0]["code"] in {"0701-GV-001", "0701-GV-002", "0702-GV-003"}
        assert reponse["nb_resultats"] >= 5

    def test_filtre_dimension_existant_toujours_operationnel(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(
            base_dimension["index"],
            requete="",
            filtres={"cote": "slu_m", "min": 6.59, "max": 6.61},
        )
        assert codes(reponse) == {"0701-GV-001", "1001-GV-006"}
        reponse = rechercher_fiches(
            base_dimension["index"],
            requete="",
            filtres={"cote": "slu_m", "min": 6.5, "max": 6.7},
        )
        # Le filtre par plage EXISTANT reste exact : 6,55 est bien dans [6,5 ; 6,7].
        assert codes(reponse) == {"0701-GV-001", "0702-GV-003", "0801-GEN-001", "1001-GV-006"}

    def test_reponse_expose_le_contexte_dimension(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(base_dimension["index"], requete="SLU 6,60")
        assert reponse["dimension_active"]["cote"] == "slu_m"
        assert reponse["dimension_active"]["tolerance_pct"] == 0.5
        assert reponse["sources_actives"] == ["dimension"]
        reponse = rechercher_fiches(base_dimension["index"], requete="grand voile")
        assert reponse["dimension_active"] is None

    def test_pagination_dimension(self, base_dimension: dict[str, Any]) -> None:
        reponse = rechercher_fiches(base_dimension["index"], requete="6,60", limit=2, offset=0)
        assert reponse["nb_resultats"] == 4
        assert len(reponse["resultats"]) == 2
        assert reponse["has_more"] is True

    @pytest.mark.perf
    def test_perf_p95_recherche_dimension(self, base_dimension: dict[str, Any]) -> None:
        """CRITÈRE DE LATENCE Phase 3 : p95 < 100 ms sur le chemin NUMÉRIQUE.

        Mesuré hors instrumentation (marqueur perf désélectionné par la
        couverture). Publie p50/p95/max comme les autres mesures perf."""
        import statistics

        from tests.conftest import publier_mesure_perf, seuil_perf_p95_ms

        jeu = ("6,60", "6.60", "6,6", "660 cm", "SLU 6,60", "spi 6,60")
        durees_ms: list[float] = []
        for _passage in range(10):
            for requete in jeu:
                debut = time.perf_counter()
                reponse = rechercher_fiches(base_dimension["index"], requete=requete)
                durees_ms.append((time.perf_counter() - debut) * 1000.0)
                assert reponse["nb_resultats"] >= 1, f"{requete!r} doit trouver au moins une fiche"
        p50 = statistics.median(durees_ms)
        p95 = sorted(durees_ms)[max(0, int(len(durees_ms) * 0.95) - 1)]
        print(
            f"\n[perf Phase 1] recherche par dimension (6 formes × 10) : "
            f"p50 = {p50:.1f} ms, p95 = {p95:.1f} ms, max = {max(durees_ms):.1f} ms "
            f"(seuil p95 applicable : {seuil_perf_p95_ms():.0f} ms)"
        )
        publier_mesure_perf("recherche par dimension (Phase 1)", p50, p95, max(durees_ms), len(durees_ms))
        assert p95 < seuil_perf_p95_ms(), f"p95 {p95:.1f} ms ≥ {seuil_perf_p95_ms():.0f} ms"
