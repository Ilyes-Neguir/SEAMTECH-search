"""Épreuves de la file de qualification manuelle.

Les 109 fiches sans axe ne sont pas un bug : personne n'a ouvert leurs
documents, et aucune machine ne peut le faire à la place d'un humain. Ce que ce
lot livre, c'est le dispositif qui rend ce travail possible et traçable.

Deux exigences traversent toutes ces épreuves :

* **rien n'est déduit** — une valeur devinée entrerait dans le dénominateur du
  taux d'appariement et le fausserait durablement ;
* **toute saisie est tracée** — qui, quand, quelle valeur, et la valeur
  d'avant : la même exigence que pour les auto-validations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import file_qualification as fq
from scripts.etape0_corpus import VERSION_ACCUMULATEUR, ajouter_fiche, nouvel_accumulateur


def _corpus(tmp_path: Path, *fiches: dict) -> Path:
    """Un corpus jetable, sans toucher à celui du dépôt."""
    acc = nouvel_accumulateur()
    for fiche in fiches:
        ajouter_fiche(acc, **fiche)
    chemin = tmp_path / "acc.json"
    chemin.write_text(json.dumps(acc, ensure_ascii=False), encoding="utf-8")
    return chemin


def _complete() -> dict:
    return {
        "chemin": "/VOILES 2026/COMPLETE",
        "racine": "VOILES 2026",
        "source": "dropbox",
        "gamme": "C0",
        "type_voile": "génois",
        "matiere": "Dacron",
    }


def _vide(nom: str = "VIDE") -> dict:
    return {"chemin": f"/VOILES 2024/{nom}", "racine": "VOILES 2024", "source": "dropbox"}


# ---------------------------------------------------------------------------
# La file
# ---------------------------------------------------------------------------


def test_la_file_liste_les_fiches_incompletes(tmp_path: Path) -> None:
    """La fiche complète n'entre pas dans la file ; les autres si."""
    chemin = _corpus(tmp_path, _complete(), _vide("A"), _vide("B"))
    from scripts.etape0_corpus import charger_accumulateur, fiches_a_qualifier

    acc = charger_accumulateur(chemin)
    file_attente = fiches_a_qualifier(acc)

    assert len(file_attente) == 2, "la fiche complète ne doit pas être dans la file"
    assert all("type_voile" in f["axes_manquants"] for f in file_attente)


def test_la_file_annonce_ou_trouver_sans_jamais_donner_le_chemin(tmp_path: Path) -> None:
    """Pas de lien direct vers le PDF : le chemin n'est pas stocké.

    C'est une limite assumée, pas un oubli — un chemin nomme un client et un
    bateau. L'épreuve garantit qu'aucun chemin ne fuite dans l'affichage.
    """
    chemin = _corpus(tmp_path, _vide("DOSSIER DUN CLIENT"))
    from scripts.etape0_corpus import charger_accumulateur, fiches_a_qualifier

    fiches = fiches_a_qualifier(charger_accumulateur(chemin))

    affiche = fq.formater_file(fiches)
    assert "VOILES 2024" in affiche, "la racine guide la recherche"
    assert "DOSSIER DUN CLIENT" not in affiche, "le nom du dossier ne doit pas apparaître"
    assert "/VOILES" not in affiche, "aucun chemin ne doit fuiter"
    assert "confidentialité" in affiche, "la raison est dite, pas tue"


def test_file_vide_quand_tout_est_qualifie(tmp_path: Path) -> None:
    """Le vrai négatif : une file vide se dit, elle ne se tait pas."""
    chemin = _corpus(tmp_path, _complete())
    from scripts.etape0_corpus import charger_accumulateur, fiches_a_qualifier

    assert fiches_a_qualifier(charger_accumulateur(chemin)) == []
    assert "vide" in fq.formater_file([]).lower()


# ---------------------------------------------------------------------------
# La saisie et sa trace
# ---------------------------------------------------------------------------


def test_une_saisie_est_appliquee_et_tracée(tmp_path: Path) -> None:
    """Qui, quand, quelle valeur — et la valeur d'avant."""
    chemin = _corpus(tmp_path, _vide("A"))
    journal = tmp_path / "qualifications.json"
    from scripts.etape0_corpus import charger_accumulateur

    id_fiche = charger_accumulateur(chemin)["fiches"][0]["id"]

    entree = fq.saisir(
        id_fiche=id_fiche,
        axe="type_voile",
        valeur="Génois",
        operateur="lea",
        motif="lu sur le devis",
        chemin_corpus=chemin,
        chemin_journal=journal,
    )

    assert entree["avant"] is None
    assert entree["apres"] == "Génois"
    assert entree["operateur"] == "lea"
    assert entree["motif"] == "lu sur le devis"
    assert entree["horodatage"], "le moment est consigné"

    # La valeur est appliquée au corpus…
    from scripts.etape0_corpus import charger_accumulateur as relire

    assert relire(chemin)["fiches"][0]["type_voile"] == "Génois"
    # …et la trace est relisible.
    consigne = json.loads(journal.read_text(encoding="utf-8"))["saisies"]
    assert len(consigne) == 1
    assert consigne[0]["id"] == id_fiche


def test_une_saisie_ulterieure_est_ajoutee_au_journal(tmp_path: Path) -> None:
    """Le journal s'allonge : il ne remplace pas la saisie précédente."""
    chemin = _corpus(tmp_path, _vide("A"))
    journal = tmp_path / "qualifications.json"
    from scripts.etape0_corpus import charger_accumulateur

    id_fiche = charger_accumulateur(chemin)["fiches"][0]["id"]

    for axe, valeur in (("type_voile", "Génois"), ("matiere", "Dacron")):
        fq.saisir(
            id_fiche=id_fiche, axe=axe, valeur=valeur, operateur="lea", chemin_corpus=chemin, chemin_journal=journal
        )

    consigne = json.loads(journal.read_text(encoding="utf-8"))["saisies"]
    assert len(consigne) == 2
    assert [s["axe"] for s in consigne] == ["type_voile", "matiere"]


def test_saisir_refuse_un_operateur_anonyme(tmp_path: Path) -> None:
    """Une saisie sans auteur n'est pas une trace."""
    chemin = _corpus(tmp_path, _vide("A"))
    from scripts.etape0_corpus import charger_accumulateur

    id_fiche = charger_accumulateur(chemin)["fiches"][0]["id"]
    with pytest.raises(ValueError, match="opérateur"):
        fq.saisir(
            id_fiche=id_fiche,
            axe="type_voile",
            valeur="Génois",
            operateur="   ",
            chemin_corpus=chemin,
            chemin_journal=tmp_path / "j.json",
        )


def test_saisir_refuse_une_valeur_vide(tmp_path: Path) -> None:
    """On ne consigne pas un axe vide : « je ne sais pas » se dit en ne saisissant rien."""
    chemin = _corpus(tmp_path, _vide("A"))
    from scripts.etape0_corpus import charger_accumulateur

    id_fiche = charger_accumulateur(chemin)["fiches"][0]["id"]
    with pytest.raises(ValueError, match="Valeur vide"):
        fq.saisir(
            id_fiche=id_fiche,
            axe="type_voile",
            valeur="   ",
            operateur="lea",
            chemin_corpus=chemin,
            chemin_journal=tmp_path / "j.json",
        )


def test_saisir_refuse_une_saisie_sans_effet(tmp_path: Path) -> None:
    """Répéter la même valeur n'écrit rien au journal : pas de trace pour du vent."""
    chemin = _corpus(tmp_path, _vide("A"))
    journal = tmp_path / "qualifications.json"
    from scripts.etape0_corpus import charger_accumulateur

    id_fiche = charger_accumulateur(chemin)["fiches"][0]["id"]

    fq.saisir(
        id_fiche=id_fiche,
        axe="type_voile",
        valeur="Génois",
        operateur="lea",
        chemin_corpus=chemin,
        chemin_journal=journal,
    )
    with pytest.raises(ValueError, match="déjà"):
        fq.saisir(
            id_fiche=id_fiche,
            axe="type_voile",
            valeur="Génois",
            operateur="lea",
            chemin_corpus=chemin,
            chemin_journal=journal,
        )

    assert len(json.loads(journal.read_text(encoding="utf-8"))["saisies"]) == 1


def test_saisir_refuse_une_fiche_inexistante(tmp_path: Path) -> None:
    chemin = _corpus(tmp_path, _vide("A"))
    with pytest.raises(ValueError, match="Aucune fiche"):
        fq.saisir(
            id_fiche="inconnue",
            axe="type_voile",
            valeur="Génois",
            operateur="lea",
            chemin_corpus=chemin,
            chemin_journal=tmp_path / "j.json",
        )


def test_saisir_refuse_un_axe_qui_nest_pas_un_axe(tmp_path: Path) -> None:
    chemin = _corpus(tmp_path, _vide("A"))
    with pytest.raises(ValueError, match="Axe inattendu"):
        fq.saisir(
            id_fiche="x",
            axe="couleur",
            valeur="rouge",
            operateur="lea",
            chemin_corpus=chemin,
            chemin_journal=tmp_path / "j.json",
        )


def test_le_journal_se_relit_par_fiche(tmp_path: Path) -> None:
    """Relire l'historique d'une fiche : c'est ce qui rend le travail vérifiable."""
    chemin = _corpus(tmp_path, _vide("A"), _vide("B"))
    journal = tmp_path / "qualifications.json"
    from scripts.etape0_corpus import charger_accumulateur

    ids = [f["id"] for f in charger_accumulateur(chemin)["fiches"]]

    fq.saisir(
        id_fiche=ids[0],
        axe="type_voile",
        valeur="Génois",
        operateur="lea",
        chemin_corpus=chemin,
        chemin_journal=journal,
    )

    assert len(fq.charger_journal(journal)["saisies"]) == 1
    assert "Génois" in fq.formater_journal(fq.charger_journal(journal)["saisies"])
    assert fq.formater_journal([]) == "Aucune saisie consignée."


def test_le_corpus_reste_lisible_apres_saisie(tmp_path: Path) -> None:
    """La sauvegarde doit garder une version compatible avec le lecteur."""
    chemin = _corpus(tmp_path, _vide("A"))
    from scripts.etape0_corpus import charger_accumulateur

    id_fiche = charger_accumulateur(chemin)["fiches"][0]["id"]
    fq.saisir(
        id_fiche=id_fiche,
        axe="gamme",
        valeur="C3",
        operateur="lea",
        chemin_corpus=chemin,
        chemin_journal=tmp_path / "j.json",
    )

    acc = charger_accumulateur(chemin)
    assert acc["version"] == VERSION_ACCUMULATEUR, "la version reste reconnue"
    assert len(acc["fiches"]) == 1, "aucune fiche n'est dupliquée"


def test_les_options_sont_acceptées_avant_et_apres_la_sous_commande(tmp_path: Path, capsys) -> None:
    """RÉGRESSION : « --corpus » après la sous-commande était rejeté.

    L'opérateur écrit naturellement « saisir --id … --corpus … ». Imposer
    l'ordre le surprenait : « unrecognized arguments ». Les options communes
    sont donc reprises par chaque sous-commande, en ``SUPPRESS`` pour que la
    valeur par défaut de la sous-commande n'écrase pas celle donnée avant elle.
    """
    chemin = _corpus(tmp_path, _vide("A"))

    code_avant = fq.main(["--corpus", str(chemin), "montrer", "--limite", "1"])
    premier = capsys.readouterr().out
    code_apres = fq.main(["montrer", "--corpus", str(chemin), "--limite", "1"])
    second = capsys.readouterr().out

    assert code_avant == code_apres == 1, "les deux ordres rendent le même verdict"
    assert "1 fiche(s) à qualifier" in premier
    assert "1 fiche(s) à qualifier" in second
