"""Les journaux techniques ne contiennent pas les valeurs métier corrigées."""

import pytest

from seamtech_search.fiches import routes
from seamtech_search.fiches.modeles import ChampExtrait, FicheExtraite
from seamtech_search.fiches.persistance import ecrire_fiche
from tests.test_fiches_persistance import base_fiches  # noqa: F401

pytestmark = pytest.mark.postgres


def test_journal_technique_validation_sans_donnees_metier(base_fiches, caplog):  # noqa: F811
    index = base_fiches["index"]
    secret = "SECRET-SYNTHETIQUE-TEST"
    code = f"{secret}-CODE"
    acteur = f"{secret}-UTILISATEUR"
    fiche = FicheExtraite(code=code, titre=f"{secret}-AVANT")
    fiche.champs.append(ChampExtrait(champ="fiche.titre", valeur_brute=f"{secret}-BRUT",
                                    valeur_normalisee=fiche.titre, table_cible="fiche", colonne_cible="titre"))
    ecrire_fiche(index, fiche)
    with caplog.at_level("DEBUG", logger=routes.LOGGER.name):
        correction = routes.corriger_champ(index, code, "fiche.titre", f"{secret}-APRES", acteur,
                                            exiger_revision=False)
        assert correction["apres"] == f"{secret}-APRES"
        rejet = routes.rejeter_fiche(index, code, acteur, f"{secret}-MOTIF", exiger_revision=False)
        assert rejet["statut"] == "rejete"
        reouverture = routes.rouvrir_fiche(index, code, acteur, exiger_revision=False)
        assert reouverture["statut"] == "a_valider"
        validation = routes.valider_fiche(index, code, acteur, exiger_revision=False)
        assert validation["statut"] == "valide"
    assert secret not in caplog.text
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT commentaire FROM fiche_validation v JOIN fiche f ON f.id_fiche = v.id_fiche WHERE f.code = %s ORDER BY v.id_validation", (code,))
        commentaires = [row[0] for row in curseur.fetchall()]
    assert any(secret in (commentaire or "") for commentaire in commentaires)  # audit métier intact


def test_validation_groupee_sans_identifiants_dans_journaux(base_fiches, caplog):  # noqa: F811
    index = base_fiches["index"]
    secret = "SECRET-SYNTHETIQUE-LOT"
    fiche = FicheExtraite(code=secret + "-CODE")
    ecrire_fiche(index, fiche)
    with caplog.at_level("DEBUG", logger=routes.LOGGER.name):
        resultat = routes.valider_lot(index, [fiche.code], secret + "-ACTEUR",
                                      acquittement_humain=True, exiger_revision=False)
    assert resultat["nb_validees"] == 1
    assert secret not in caplog.text
