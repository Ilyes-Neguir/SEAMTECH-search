"""Le script de preuve réelle doit NOMMER ce qui manque, jamais échouer en silence.

Pourquoi un test pour un script qu'on n'exécute pas ici : la pire fin pour une
preuve qu'on ne peut pas faire, c'est qu'elle pourrisse sans que personne ne le
voie. Le jour où l'atelier aura le fournisseur sous la main, le script doit
encore refuser correctement ce qu'il ne peut pas prouver — et ce refus est la
seule partie qu'on peut éprouver sans fournisseur.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
SCRIPT = RACINE / "scripts" / "preuve_restauration_reelle.py"


def test_sans_pre_requis_le_script_nomme_ce_qui_manque_et_refuse() -> None:
    """Un échec obscur vaudrait un mensonge : le script doit dire lesquels."""
    environnement = {
        cle: valeur
        for cle, valeur in os.environ.items()
        if not cle.startswith("SEAMTECH_")
    }
    environnement["PYTHONPATH"] = str(RACINE)
    # Un répertoire vide comme répertoire courant : aucune config applicative
    # ne peut être trouvée, ce qui est le cas le plus défavorable.
    resultat = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=environnement,
        cwd=str(RACINE),
        check=False,
    )
    assert resultat.returncode == 2, resultat.stdout
    for attendu in (
        "SEAMTECH_DATABASE_URL",
        "SEAMTECH_RESTORE_DATABASE_URL",
        "s3_endpoint_url",
        "SEAMTECH_BACKUP_ACCESS_KEY",
    ):
        assert attendu in resultat.stderr, (
            f"le refus ne nomme pas « {attendu} » : sans lui, celui qui exécute "
            "le script sur site ne sait pas quoi fournir"
        )


def test_la_base_cible_doit_etre_inexistante() -> None:
    """Restaurer sur une base existante est une destruction, pas une reprise.

    Éprouvé sans PostgreSQL : ce qui compte ici, c'est que la GARDE existe et
    qu'elle est câblée dans ``main``, pas qu'elle sache interroger un serveur.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert "_base_existe" in source
    assert 'REFUS : la base cible existe déjà' in source


@pytest.mark.postgres
def test_base_existe_est_fausse_pour_une_base_inexistante() -> None:
    """Sous PostgreSQL réel, la garde ne doit pas bloquer une base neuve."""
    url = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    sys.path.insert(0, str(RACINE))
    from scripts.preuve_restauration_reelle import _base_existe  # type: ignore[import-not-found]

    assert _base_existe(url.rsplit("/", 1)[0] + "/base_qui_nexiste_pas_du_tout") is False
