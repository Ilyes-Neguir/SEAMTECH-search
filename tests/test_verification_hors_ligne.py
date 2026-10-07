"""Garde réseau de la vérification hors ligne (revue du 2026-10-07, constat n° 3).

Le script ``scripts/verifier_hors_ligne.py`` prétend exécuter les parcours
essentiels SANS réseau externe. Une prétention de ce genre ne vaut que si l'on
prouve que le garde BLOQUE réellement : ces tests exercent ses décisions sur des
connexions réelles (loopback accepté, adresse publique refusée, nom non déclaré
refusé), et vérifient que le mode diagnostic, lui, compte sans bloquer.

Le script est chargé par chemin : il n'est jamais importé par le service (RG14 —
exception documentée dans ``tests/test_garde_fous_preparation.py``).
"""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
import types
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "scripts" / "verifier_hors_ligne.py"


def _module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("verifier_hors_ligne_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["verifier_hors_ligne_test"] = module
    spec.loader.exec_module(module)
    return module


def test_le_script_existe_et_ne_se_lance_pas_sans_mode() -> None:
    """Aucun mode demandé ⇒ argparse refuse : rien ne s'exécute par défaut."""
    assert SCRIPT.exists(), "scripts/verifier_hors_ligne.py manquant"
    resultat = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
    assert resultat.returncode == 2, (resultat.returncode, resultat.stderr[-400:])
    assert "--inventaire" in resultat.stderr and "--executer" in resultat.stderr


def test_le_garde_bloque_une_adresse_publique_et_laisse_passer_le_loopback() -> None:
    """Cœur de la preuve : sans le blocage, « aucun appel externe » ne veut rien dire."""
    module = _module()
    garde = module.GardeReseau(bloquer=True)
    ecoute = socket.socket()
    ecoute.bind(("127.0.0.1", 0))
    ecoute.listen(1)
    port = ecoute.getsockname()[1]
    garde.installer()
    try:
        # Loopback : autorisé, la connexion aboutit réellement.
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
        assert garde.tentatives == []
        # Adresse PUBLIQUE : refusée, et la tentative est NOMMÉE.
        with pytest.raises(module.EgressExterne) as erreur:
            socket.create_connection(("8.8.8.8", 53), timeout=1)
        assert "8.8.8.8:53" in str(erreur.value)
        assert garde.tentatives == ["8.8.8.8:53"]
        # connect_ex passe par le même contrôle.
        sonde = socket.socket()
        try:
            with pytest.raises(module.EgressExterne):
                sonde.connect_ex(("1.1.1.1", 443))
        finally:
            sonde.close()
        assert "1.1.1.1:443" in garde.tentatives
    finally:
        garde.retirer()
        ecoute.close()


def test_le_mode_diagnostic_compte_sans_bloquer() -> None:
    """``--autoriser-externe`` sert à MONTRER ce qui sortirait, sans le bloquer."""
    module = _module()
    garde = module.GardeReseau(bloquer=False)
    # Le contrôle lui-même ne lève pas en diagnostic : il CLASSE.
    # 203.0.113.0/24 (comme 192.0.2.0/24) est classé « privé » par ipaddress :
    # on prend une adresse réellement globale pour éprouver le classement.
    garde._verifier(("1.1.1.1", 443))
    assert garde.tentatives == ["1.1.1.1:443"]
    # Et une connexion réelle vers une adresse publique n'est pas bloquée par
    # le garde (elle échoue ou aboutit selon le réseau ambiant — peu importe :
    # ce qui compte est qu'EgressExterne ne soit PAS levée).
    garde.installer()
    sonde = socket.socket()
    sonde.settimeout(1)
    try:
        try:
            sonde.connect(("1.1.1.1", 443))
        except module.EgressExterne as erreur:  # pragma: no cover - échec net
            pytest.fail(f"le mode diagnostic a bloqué : {erreur}")
        except OSError:
            pass  # réseau ambiant : sans importance pour ce test
        assert "1.1.1.1:443" in garde.tentatives
    finally:
        garde.retirer()
        sonde.close()


def test_un_nom_non_declare_est_refuse_un_alias_declare_est_accepte() -> None:
    """Le garde ne résout pas les noms : un nom public ne doit pas passer en douce."""
    module = _module()
    garde = module.GardeReseau(bloquer=True)
    assert garde._est_local(("huggingface.co", 443)) is False
    assert garde._est_local(("minio", 9000)) is False
    assert garde._est_local(("127.0.0.1", 9000)) is True
    assert garde._est_local(("10.0.0.7", 5432)) is True  # réseau privé de l'atelier
    avec_alias = module.GardeReseau(bloquer=True, hotes_autorises={"minio"})
    assert avec_alias._est_local(("minio", 9000)) is True


def test_les_points_de_terminaison_sont_classes_local_ou_public() -> None:
    """L'inventaire doit savoir dire qu'une URL pointe un nom PUBLIC."""
    module = _module()
    local, detail = module._adresse_locale("postgresql://seamtech@127.0.0.1:5433/base")
    assert local is True and detail == "127.0.0.1"
    local, detail = module._adresse_locale("redis://cache.exemple.tn:6379/0")
    assert local is False and "cache.exemple.tn" in detail
    local, detail = module._adresse_locale(None)
    assert local is True and detail == "non configuré"
    local, _ = module._adresse_locale("http://minio:9000")
    assert local is False, "un nom (alias compose) doit être déclaré explicitement, pas supposé local"
