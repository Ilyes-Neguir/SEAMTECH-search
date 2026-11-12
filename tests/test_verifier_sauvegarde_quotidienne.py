"""Lot 13.2 — la vérification de la sauvegarde quotidienne doit être opposable.

Deux niveaux, volontairement :

- des épreuves de DÉCISION sur des états fabriqués (rapides, exhaustives) ;
- une épreuve d'INTÉGRATION qui passe par le vrai ``etat_sauvegarde`` avec un
  manifeste réellement écrit sur le disque, pour prouver que le vérificateur
  lit bien la même source que ``/health`` et non une variante parallèle qui
  pourrait dériver.

L'épreuve centrale est :func:`test_copie_locale_seule_n_est_pas_une_protection`.
``etat_sauvegarde`` renvoie ``"ok"`` pour une copie locale — l'avertissement
est posé après le calcul du statut — donc une supervision branchée sur
``statut == "ok"`` resterait VERTE dans le scénario qui compte le plus : la
machine meurt, la sauvegarde meurt avec elle.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import verifier_sauvegarde_quotidienne as v
from seamtech_search.config import AppConfig
from seamtech_search.etat_exploitation import etat_sauvegarde


def _etat(**surcharges) -> dict:
    """Un état sain, complet et hors-site, que l'on dégrade à volonté."""
    etat = {
        "statut": "ok",
        "message": "Dernière sauvegarde il y a 1.0 h.",
        "derniere": "2026-11-11T10:00:00+00:00",
        "age_heures": 1.0,
        "copie_distante": "s3://sauvegardes/2026-11-11.dump",
        "manifeste": "/data/backups/sauvegarde.manifest.json",
        "protection_globale_prouvee": True,
    }
    etat.update(surcharges)
    return etat


# --------------------------------------------------------------------------
# Décision
# --------------------------------------------------------------------------


def test_copie_locale_seule_n_est_pas_une_protection() -> None:
    """L'épreuve centrale : « ok » ne suffit pas s'il n'y a rien hors-site."""
    sain, raisons = v.evaluer(_etat(copie_distante=None), autoriser_locale=False)
    assert sain is False, (
        "une copie locale seule est déclarée saine. /health dit « ok » dans ce "
        "cas ; c'est précisément le piège que ce vérificateur doit fermer."
    )
    assert any("hors-site" in r for r in raisons)


def test_une_copie_complete_et_hors_site_est_saine() -> None:
    sain, raisons = v.evaluer(_etat(), autoriser_locale=False)
    assert sain is True, f"refusée à tort : {raisons}"
    assert raisons == []


@pytest.mark.parametrize(
    "statut",
    ["jamais", "illisible", "age_inconnu", "non_qualifiee", "ancienne"],
)
def test_tout_statut_degrade_est_un_echec(statut: str) -> None:
    """Aucun statut dégradé ne doit passer, même avec une copie hors-site."""
    sain, _ = v.evaluer(_etat(statut=statut), autoriser_locale=False)
    assert sain is False, f"le statut « {statut} » a été accepté"


def test_chaque_statut_degrade_porte_un_remede_actionnable() -> None:
    """Un constat sans remède ne se répare pas : le message doit dire quoi faire."""
    for statut in ("jamais", "illisible", "age_inconnu", "non_qualifiee", "ancienne"):
        assert statut in v.REMEDE, f"« {statut} » constaté sans remède"
        assert len(v.REMEDE[statut]) > 40, f"remède de « {statut} » trop vague pour agir"


def test_autoriser_locale_assume_le_risque_mais_ne_soigne_pas_le_statut() -> None:
    """L'échappatoire ne couvre QUE la localité — pas l'ancienneté ni une copie partielle."""
    sain, raisons = v.evaluer(_etat(copie_distante=None), autoriser_locale=True)
    assert sain is True, "l'option n'a pas l'effet annoncé"
    assert any("AVERTISSEMENT" in r for r in raisons), "le risque doit rester écrit"

    # Une copie locale ET ancienne reste un échec : le risque accepté porte sur
    # la localité, il ne guérit pas une sauvegarde qui ne tourne plus.
    sain, _ = v.evaluer(_etat(copie_distante=None, statut="ancienne"), autoriser_locale=True)
    assert sain is False, "--autoriser-locale a masqué une sauvegarde trop ancienne"


def test_le_rapport_ne_se_contredit_pas_sur_la_localisation(tmp_path: Path, monkeypatch, capsys) -> None:
    """Un outil opposable ne dit pas « hors-site » et « locale » dans le même rapport.

    Défaut réellement rencontré en écrivant : l'en-tête affichait
    « copie récente, complète et hors-site » tandis que l'avertissement
    disait « LOCALE uniquement ». Une sortie qui se contredit n'est pas
    opposable : on ne sait plus laquelle des deux lignes croire.

    On passe par le vrai ``main``, avec un vrai fichier de configuration et un
    vrai manifeste : c'est le seul moyen de voir la sortie telle que
    l'opérateur la lira.
    """
    config = _config(tmp_path, monkeypatch)
    _ecrire_manifeste(tmp_path)  # locale : aucune clé S3
    fichier_config = tmp_path / "config.json"
    fichier_config.write_text(
        json.dumps(
            {
                "root_paths": [str(tmp_path)],
                "database_path": str(tmp_path / "search.db"),
                "database_url": config.database_url,
            }
        )
    )

    assert v.main(["--config", str(fichier_config), "--autoriser-locale"]) == 0
    sortie = capsys.readouterr().out
    # Le champ « hors-site : AUCUN » du constat est légitime et doit rester.
    # Ce qui ne doit pas apparaître, c'est le VERDICT qui affirme une
    # protection complète : c'est lui qu'on croit en lisant vite.
    assert "PROTECTION PROUVÉE" not in sortie, "le verdict affirme une protection prouvée alors que la copie est locale"
    assert "PROTECTION PARTIELLE" in sortie
    assert "LOCALE" in sortie, "le risque accepté doit rester écrit"


# --------------------------------------------------------------------------
# Intégration : le vérificateur lit la MÊME source que /health
# --------------------------------------------------------------------------


def _config(tmp_path: Path, monkeypatch) -> AppConfig:
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    return AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "db",
        database_url="postgresql://synthetique",
    )


def _ecrire_manifeste(tmp_path: Path, **champs) -> None:
    manifeste = {
        "date_iso": datetime.now(timezone.utc).isoformat(),
        "complet": True,
        "protection_globale_prouvee": True,
    }
    manifeste.update(champs)
    (tmp_path / "sauvegarde.manifest.json").write_text(json.dumps(manifeste))


def test_integration_copie_recente_mais_locale_est_un_echec(tmp_path: Path, monkeypatch) -> None:
    """Preuve de bout en bout : manifeste réel sur disque, aucune clé S3."""
    config = _config(tmp_path, monkeypatch)
    _ecrire_manifeste(tmp_path)  # ni « dump » ni clé S3 : copie locale

    etat = etat_sauvegarde(config)
    assert etat["statut"] == "ok", "hypothèse de départ vérifiée : /health dit « ok » pour une copie locale"

    sain, raisons = v.evaluer(etat, autoriser_locale=False)
    assert sain is False, "le vérificateur a suivi /health dans son angle mort au lieu de le corriger"
    assert any("hors-site" in r for r in raisons)


def test_integration_copie_hors_site_est_acceptee(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, monkeypatch)
    _ecrire_manifeste(tmp_path, dump={"cle_s3": "s3://sauvegardes/dump.dump"})

    etat = etat_sauvegarde(config)
    sain, raisons = v.evaluer(etat, autoriser_locale=False)
    assert sain is True, f"refusée à tort : {etat} / {raisons}"


def test_integration_copie_trop_ancienne_est_un_echec(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, monkeypatch)
    vieux = datetime.now(timezone.utc) - timedelta(hours=200)
    _ecrire_manifeste(tmp_path, date_iso=vieux.isoformat(), dump={"cle_s3": "s3://s/d.dump"})

    etat = etat_sauvegarde(config)
    assert etat["statut"] == "ancienne"
    sain, _ = v.evaluer(etat, autoriser_locale=False)
    assert sain is False


def test_integration_aucune_sauvegarde_jamais_produite(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, monkeypatch)  # dossier vide

    etat = etat_sauvegarde(config)
    assert etat["statut"] == "jamais"
    sain, raisons = v.evaluer(etat, autoriser_locale=False)
    assert sain is False
    assert any("service compose" in r for r in raisons), "le remède doit nommer le service"


# --------------------------------------------------------------------------
# Lot 21 — le corpus, vérifié par la même commande
# --------------------------------------------------------------------------


def test_corpus_absent_est_sans_objet(tmp_path: Path, monkeypatch):
    """Un déploiement sans corpus n'est ni pénalisé ni dispensé d'être informé."""
    monkeypatch.setenv("SEAMTECH_CORPUS_DIR", str(tmp_path / "inexistant"))
    applicable, etat, raisons = v.evaluer_corpus(seuil_heures=24, client=None)
    assert applicable is None
    assert etat["applicable"] is False
    assert raisons == []


def test_corpus_present_sans_stockage_n_est_pas_protege(tmp_path: Path, monkeypatch):
    """Le cas réel du Lot 21 : un corpus sur un disque et nulle part ailleurs."""
    corpus = tmp_path / "etape0"
    corpus.mkdir()
    (corpus / "accumulateur.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SEAMTECH_CORPUS_DIR", str(corpus))

    applicable, etat, raisons = v.evaluer_corpus(seuil_heures=24, client=None)
    assert applicable is False
    assert etat["protege"] is False
    assert len(raisons) == 1
    assert "corpus non protégé" in raisons[0]


def test_corpus_protege_est_prouve(tmp_path: Path, monkeypatch):
    from seamtech_search.sauvegarde_corpus import sauvegarder

    corpus = tmp_path / "etape0"
    corpus.mkdir()
    (corpus / "accumulateur.json").write_text('{"fiches": []}', encoding="utf-8")
    monkeypatch.setenv("SEAMTECH_CORPUS_DIR", str(corpus))

    class ClientFactice:
        def __init__(self):
            self.objets: dict[str, bytes] = {}

        def upload_bytes(self, data: bytes, remote_key: str, **_):
            self.objets[remote_key] = data
            return remote_key

        def list_keys(self, prefix: str) -> list[str]:
            return sorted(cle for cle in self.objets if cle.startswith(prefix))

    client = ClientFactice()
    sauvegarder(client, dossier=corpus)

    applicable, etat, raisons = v.evaluer_corpus(seuil_heures=24, client=client)
    assert applicable is True
    assert etat["protege"] is True
    assert raisons == []


def test_corpus_illisible_n_est_jamais_protege(tmp_path: Path, monkeypatch):
    """Une lecture impossible rend « inconnu », donc échec — jamais « protégé »."""
    corpus = tmp_path / "etape0"
    corpus.mkdir()
    monkeypatch.setenv("SEAMTECH_CORPUS_DIR", str(corpus))

    class ClientEnPanne:
        def list_keys(self, prefix: str) -> list[str]:
            raise RuntimeError("endpoint injoignable")

    applicable, etat, raisons = v.evaluer_corpus(seuil_heures=24, client=ClientEnPanne())
    # Une lecture impossible n'est JAMAIS « protégé » : l'état est soit faux
    # (non prouvé), soit nul (inconnu) — dans les deux cas, échec.
    assert applicable is False
    assert etat["protege"] is not True
    assert "corpus" in raisons[0]
