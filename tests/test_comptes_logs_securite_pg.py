"""Pas de noms de comptes dans les journaux techniques ; audit métier intact."""
import pytest
from fastapi import HTTPException

from seamtech_search.comptes import comptes
from tests.test_fiches_persistance import base_fiches  # noqa: F401

pytestmark = pytest.mark.postgres


def test_journaux_auth_et_gestion_comptes_sans_identifiant(base_fiches, caplog):  # noqa: F811
    index = base_fiches["index"]
    identifiant = "SECRET-SYNTHETIQUE-IDENTIFIANT"
    with caplog.at_level("DEBUG", logger=comptes.LOGGER.name):
        compte = comptes.creer_utilisateur(index, identifiant, "Nom synthétique", "motdepasse-synthese", role="lecture")
        with pytest.raises(HTTPException) as echec:
            comptes.verifier_identifiants(index, identifiant, "incorrect")
        assert echec.value.status_code == 401
        assert comptes.verifier_identifiants(index, identifiant, "motdepasse-synthese")["identifiant"] == identifiant
        comptes.changer_role_utilisateur(index, identifiant, "utilisateur")
        comptes.reinitialiser_mot_de_passe(index, identifiant, "motdepasse-temporaire")
        comptes.changer_mot_de_passe(index, compte["id_utilisateur"], "motdepasse-temporaire", "motdepasse-nouveau")
        comptes.desactiver_utilisateur(index, identifiant)
    assert identifiant not in caplog.text
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT COUNT(*) FROM audit_log WHERE actor = %s", (identifiant,))
        assert curseur.fetchone()[0] >= 2
