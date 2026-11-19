"""La supervision ne confond pas une copie partielle avec une protection prouvée."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.etat_exploitation import etat_sauvegarde
from seamtech_search.sauvegarde_planifiee import sauvegarde_due


@pytest.mark.parametrize("qualification", [None, False])
def test_manifeste_reel_partiel_ne_se_dit_pas_ok(tmp_path: Path, monkeypatch, qualification):
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    manifeste = {"date_iso": datetime.now(timezone.utc).isoformat(), "complet": True}
    if qualification is not None:
        manifeste["protection_globale_prouvee"] = qualification
    (tmp_path / "sauvegarde.manifest.json").write_text(json.dumps(manifeste))
    etat = etat_sauvegarde(config)
    assert etat["age_heures"] is not None
    assert etat["statut"] == "non_qualifiee"
    assert etat["protection_globale_prouvee"] is False
    assert not sauvegarde_due(config, 24)  # pas de tempête de dumps tant que la copie est récente


def test_planificateur_ne_signale_pas_reussite_globale_pour_copie_partielle(tmp_path: Path, monkeypatch, caplog):
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    resultat = {"complet": True, "protection_globale_prouvee": False}
    assert not executer_si_du(config, intervalle_heures=24, fonction_sauver=lambda: resultat)
    assert "non qualifiée" in caplog.text


def test_planificateur_reel_transmet_la_qualification(tmp_path: Path, monkeypatch, caplog):
    from seamtech_search import sauvegarde
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
    monkeypatch.setattr(sauvegarde, "sauver", lambda *args, **kwargs: {
        "complet": True, "protection_globale_prouvee": False,
    })
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    assert not executer_si_du(config, intervalle_heures=24)
    assert "non qualifiée" in caplog.text


def test_health_refuse_manifeste_contradictoire(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    (tmp_path / "sauvegarde.manifest.json").write_text(json.dumps({
        "date_iso": datetime.now(timezone.utc).isoformat(), "complet": False,
        "protection_globale_prouvee": True,
    }))
    etat = etat_sauvegarde(config)
    assert etat["statut"] == "non_qualifiee"
    assert not etat["protection_globale_prouvee"]


def test_planificateur_refuse_manifeste_contradictoire(tmp_path: Path, monkeypatch):
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    assert not executer_si_du(config, intervalle_heures=24, fonction_sauver=lambda: {
        "complet": False, "protection_globale_prouvee": True,
    })


def test_date_future_ne_masque_pas_une_sauvegarde_due(tmp_path: Path, monkeypatch):
    from datetime import timedelta

    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    (tmp_path / "sauvegarde.manifest.json").write_text(json.dumps({
        "date_iso": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "complet": False, "protection_globale_prouvee": False,
    }))
    etat = etat_sauvegarde(config)
    assert etat["age_heures"] is None
    assert etat["statut"] == "age_inconnu"
    assert sauvegarde_due(config, 24)


@pytest.mark.parametrize('resultat', [None, False, True, [], 'ok', {'complet': True}, {'protection_globale_prouvee': True}])
def test_planificateur_refuse_tout_resultat_sans_qualification_explicite(tmp_path, monkeypatch, resultat, caplog):
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    assert not executer_si_du(config, intervalle_heures=24, fonction_sauver=lambda: resultat)
    assert 'non qualifiée' in caplog.text


@pytest.mark.parametrize('manifeste', [None, [], 'texte', 42, {'dump': []}, {'dump': 'invalide'}, {'dump': 42}])
def test_health_et_planificateur_refusent_manifestes_json_malformes(tmp_path: Path, monkeypatch, manifeste):
    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    (tmp_path / 'sauvegarde.manifest.json').write_text(json.dumps(manifeste))
    etat = etat_sauvegarde(config)
    assert etat['statut'] == 'illisible'
    assert etat['age_heures'] is None
    assert sauvegarde_due(config, 24)


def test_health_et_planificateur_refusent_manifeste_non_utf8(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    (tmp_path / 'sauvegarde.manifest.json').write_bytes(b'\xff\xfe')
    etat = etat_sauvegarde(config)
    assert etat['statut'] == 'illisible'
    assert etat['age_heures'] is None
    assert sauvegarde_due(config, 24)


def test_planificateur_ne_journalise_pas_secrets_des_exceptions(tmp_path: Path, monkeypatch, caplog):
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')

    def echec():
        raise RuntimeError('jeton-synthetique-secret')

    assert not executer_si_du(config, intervalle_heures=24, fonction_sauver=echec)
    assert 'jeton-synthetique-secret' not in caplog.text
    assert 'RuntimeError' in caplog.text


@pytest.mark.parametrize('nom', ['SEAMTECH_BACKUP_INTERVAL_HEURES', 'SEAMTECH_BACKUP_VERIFICATION_MINUTES'])
@pytest.mark.parametrize('valeur', ['nan', 'inf', '-inf', '0', '-1', 'invalide'])
def test_planificateur_refuse_intervalles_invalides_avant_boucle(tmp_path: Path, monkeypatch, nom, valeur):
    from seamtech_search import sauvegarde_planifiee

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    monkeypatch.setattr(sauvegarde_planifiee.AppConfig, 'load', lambda *_args: config)
    monkeypatch.setattr(sauvegarde_planifiee, 'default_config_path', lambda: tmp_path / 'config.json')
    monkeypatch.setattr(sauvegarde_planifiee, 'exiger_secrets_production', lambda *_args: None)
    monkeypatch.setattr(sauvegarde_planifiee, 'executer_si_du', lambda *_args, **_kwargs: pytest.fail('boucle démarrée'))
    monkeypatch.setenv(nom, valeur)
    assert sauvegarde_planifiee.main([]) == 2


@pytest.mark.parametrize('intervalle', [float('nan'), float('inf'), 0, -1])
def test_sauvegarde_due_ne_masque_pas_copie_avec_intervalle_invalide(tmp_path: Path, monkeypatch, intervalle):
    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    (tmp_path / 'sauvegarde.manifest.json').write_text(json.dumps({
        'date_iso': datetime.now(timezone.utc).isoformat(), 'dump': {},
        'complet': False, 'protection_globale_prouvee': False,
    }))
    assert sauvegarde_due(config, intervalle)


# ---------------------------------------------------------------------------
# Le guide de mise en service ne doit pas promettre une couverture que le
# planificateur ne fait pas.
# ---------------------------------------------------------------------------
#
# Constaté au Lot 21 : le guide affirmait que le service `sauvegarde` « fait
# déjà ce travail », en parlant de la sauvegarde COMPLÈTE (base + données +
# objets). Or le planificateur appelle `sauvegarde.sauver()` sans
# `inclure_references_base` ni `racines_data` : les pièces jointes des clients
# — les documents eux-mêmes — ne sont dans aucune sauvegarde automatique. Une
# exploitante qui lit le guide croit ses documents protégés ; ils ne le sont
# pas. C'est exactement le défaut que cette épreuve interdit de reproduire.

GUIDE_MISE_EN_SERVICE = Path(__file__).resolve().parents[1] / "docs" / "GUIDE_MISE_EN_SERVICE_POSTE.md"

#: Vocabulaire admis entre « sauvegarde-auto: » et la fin de ligne.
PERIMETRE_CONNU = {"base", "corpus", "references", "donnees"}


def _perimetre_annonce() -> set[str]:
    """Ce que le GUIDE promet, lu dans le guide — pas dans le code."""
    texte = GUIDE_MISE_EN_SERVICE.read_text(encoding="utf-8")
    lignes = re.findall(r"^\s*sauvegarde-auto:\s*([a-z_ ]+)$", texte, re.MULTILINE)
    assert len(lignes) == 1, (
        "le guide doit porter exactement une ligne « sauvegarde-auto: … » ; "
        f"trouvé {len(lignes)}. C'est elle qui rend le périmètre vérifiable."
    )
    annonce = set(lignes[0].split())
    inconnus = annonce - PERIMETRE_CONNU
    assert not inconnus, f"terme(s) de périmètre inconnu(s) : {sorted(inconnus)}"
    return annonce


def _perimetre_reel(tmp_path: Path, monkeypatch) -> set[str]:
    """Ce que le planificateur couvre VRAIMENT, mesuré en l'exécutant.

    Rien n'est déduit d'une relecture du code : on exécute un passage avec des
    espions. `sauvegarde.sauver` et `sauvegarde_corpus.sauvegarder` sont
    remplacés, tout le reste — l'ordonnancement, la fraîcheur, le refus d'une
    copie non qualifiée — est le code de production.
    """
    from seamtech_search import sauvegarde, sauvegarde_corpus
    from seamtech_search import sauvegarde_planifiee as planificateur

    vu: set[str] = set()

    def sauver_espion(base_url, racines_archive, dossier_local, client_s3=None, retention=5, **kwargs):
        if base_url:
            vu.add("base")
        if kwargs.get("inclure_references_base"):
            vu.add("references")
        if kwargs.get("racines_data"):
            vu.add("donnees")
        return {"complet": True, "protection_globale_prouvee": True}

    def corpus_espion(client, *, dossier=None):
        vu.add("corpus")
        return {"nb_fichiers": 0, "horodatage": "20260101T000000Z"}

    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(sauvegarde, "sauver", sauver_espion)
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: object())
    monkeypatch.setattr(sauvegarde_corpus, "sauvegarder", corpus_espion)
    # Le corpus vit dans le dépôt, pas dans tmp_path : cibler le dossier du
    # test pour que « tenté » ne dépende pas de la présence du corpus réel.
    monkeypatch.setattr(sauvegarde_corpus, "dossier_corpus", lambda: tmp_path)
    # Fraîcheur lue sur le stockage : on force « dû » pour observer la
    # TENTATIVE de sauvegarde du corpus, qui est ce que l'épreuve mesure.
    monkeypatch.setattr(sauvegarde_corpus, "verifier", lambda *a, **kw: {"protege": False, "motif": "test"})
    monkeypatch.setattr(planificateur, "dossier_sauvegardes", lambda config: tmp_path)

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "db",
        database_url="postgresql://synthetique",
    )
    planificateur.executer_si_du(config, intervalle_heures=24, client_corpus=object())
    return vu


def test_le_guide_n_annonce_pas_plus_que_le_planificateur(tmp_path: Path, monkeypatch):
    """Le guide ne promet rien que l'automatisme ne fasse.

    Sens unique volontaire : promettre moins que ce qui est fait n'est pas un
    défaut grave (on reste protégé), promettre plus l'est (on se croit
    protégé). L'égalité est exigée quand même, pour qu'un élargissement du
    périmètre mette le guide à jour au lieu de le laisser sous-décrire.
    """
    annonce = _perimetre_annonce()
    reel = _perimetre_reel(tmp_path, monkeypatch)
    assert annonce <= reel, (
        f"le guide promet {sorted(annonce - reel)} que la sauvegarde "
        f"automatique ne fait pas (périmètre réel : {sorted(reel)}). "
        "Une exploitante croirait ses documents protégés."
    )
    assert annonce == reel, (
        f"périmètre réel {sorted(reel)} plus large que celui annoncé "
        f"{sorted(annonce)} : mettre le guide à jour."
    )


def test_le_guide_annonce_le_perimetre_de_la_sauvegarde_automatique():
    """L'épreuve précédente ne prouve rien si le guide n'a plus la ligne.

    Une épreuve qui ne peut pas échouer ne prouve rien : sans cette garde,
    supprimer la ligne « sauvegarde-auto: » du guide ferait passer
    l'autre épreuve (ensemble vide inclus dans n'importe quoi).
    """
    assert _perimetre_annonce(), "le guide n'annonce aucun périmètre"
