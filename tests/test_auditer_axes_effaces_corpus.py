"""Épreuves de l'audit des axes effacés, sur des historiques fabriqués.

L'audit lit treize états réels de l'accumulateur. Une épreuve ne peut donc pas
reposer sur le dépôt : elle fabriquerait son propre historique git. Le cœur du
module — :func:`analyser_historique` — est une fonction pure qui prend une
liste d'états ; c'est elle qui est éprouvée ici, ce qui rend chaque cas
reproductible par n'importe qui, sans dépendre du passé du dépôt.

Le cas réel est couvert par ailleurs : les trois effacements, les deux
rebasements de sel, et l'unique valeur en souffrance.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import auditer_axes_effaces_corpus as audit


def _acc(sel: str, *fiches: dict) -> dict:
    """Un accumulateur complet, tel que le dépôt en versionne."""
    from scripts.etape0_corpus import VERSION_ACCUMULATEUR

    return {"version": VERSION_ACCUMULATEUR, "sel": sel, "fiches": [dict(f) for f in fiches]}


def _fiche(id_fiche: str, racine: str = "VOILES 2026", **axes) -> dict:
    fiche = {
        "id": id_fiche,
        "racine": racine,
        "famille": "voiles",
        "annee": 2026,
        "source": "dropbox",
        "categorie": "voile",
        "gamme": None,
        "type_voile": None,
        "matiere": None,
    }
    fiche.update(axes)
    return fiche


# ---------------------------------------------------------------------------
# Ce que l'audit doit voir
# ---------------------------------------------------------------------------


def test_un_effacement_est_detecte_et_localise() -> None:
    """Un axe qui passe d'une valeur à vide est un effacement, daté et cité."""
    etats = [
        ("aaaaaaa", _acc("sel-1", _fiche("id-1", matiere="Dacron"))),
        ("bbbbbbb", _acc("sel-1", _fiche("id-1"))),
    ]
    rapport = audit.analyser_historique(etats)

    assert len(rapport["effacements"]) == 1
    eff = rapport["effacements"][0]
    assert eff["id"] == "id-1"
    assert eff["axe"] == "matiere"
    assert eff["avant"] == "Dacron"
    assert eff["sha"] == "bbbbbbb", "l'effacement est daté du commit qui l'a commis"
    assert eff["repare"] is False
    assert len(rapport["en_souffrance"]) == 1


def test_un_effacement_repare_plus_tard_nest_plus_en_souffrance() -> None:
    """C'est le cas des deux victimes du §6.2 : effacées en 4088774, réparées en f54cf93."""
    etats = [
        ("aaaaaaa", _acc("sel-1", _fiche("id-1", type_voile="génois"))),
        ("bbbbbbb", _acc("sel-1", _fiche("id-1"))),
        ("ccccccc", _acc("sel-1", _fiche("id-1", type_voile="génois"))),
    ]
    rapport = audit.analyser_historique(etats)

    assert len(rapport["effacements"]) == 1
    assert rapport["effacements"][0]["repare"] is True
    assert rapport["en_souffrance"] == [], "une valeur rétablie ne reste pas en souffrance"
    assert len(rapport["restaurations"]) == 1


def test_un_effacement_toujours_vide_reste_en_souffrance() -> None:
    """C'est le cas de la troisième victime, perdue en c52e722 et jamais rétablie."""
    etats = [
        ("aaaaaaa", _acc("sel-1", _fiche("id-1", matiere="fibercom hybrid cool grey"))),
        ("bbbbbbb", _acc("sel-1", _fiche("id-1"))),
        ("ccccccc", _acc("sel-1", _fiche("id-1"))),
    ]
    rapport = audit.analyser_historique(etats)

    assert len(rapport["en_souffrance"]) == 1
    assert rapport["en_souffrance"][0]["avant"] == "fibercom hybrid cool grey"


# ---------------------------------------------------------------------------
# Ce que l'audit ne doit PAS voir — les faux positifs
# ---------------------------------------------------------------------------


def test_un_changement_de_sel_nest_pas_une_perte() -> None:
    """Le sel a changé deux fois : les identifiants sont rebâtis, rien n'est perdu.

    C'est le piège de cet audit. Sans cette distinction, les 26 fiches dont
    l'identifiant a changé aux commits 92352b7 et 0fb7622 passeraient pour des
    fiches disparues, et l'audit annoncerait un désastre imaginaire.
    """
    etats = [
        ("aaaaaaa", _acc("sel-A", _fiche("id-1", type_voile="génois"))),
        ("bbbbbbb", _acc("sel-B", _fiche("id-2", type_voile="génois"))),
    ]
    rapport = audit.analyser_historique(etats)

    assert len(rapport["rebasements"]) == 1
    assert rapport["rebasements"][0]["sel_avant"] == "sel-A"
    assert rapport["rebasements"][0]["sel_apres"] == "sel-B"
    assert rapport["effacements"] == [], "un rebasement n'efface rien"
    assert rapport["en_souffrance"] == []


def test_une_fiche_qui_quitte_vraiment_le_corpus_est_signalee() -> None:
    """À sel constant, une fiche qui disparaît est une perte réelle."""
    etats = [
        ("aaaaaaa", _acc("sel-1", _fiche("id-1"), _fiche("id-2"))),
        ("bbbbbbb", _acc("sel-1", _fiche("id-1"))),
    ]
    rapport = audit.analyser_historique(etats)

    disparitions = [e for e in rapport["effacements"] if e["nature"] == "fiche_disparue"]
    assert len(disparitions) == 1
    assert disparitions[0]["id"] == "id-2"
    assert rapport["rebasements"] == []


def test_une_croissance_saine_ne_signale_rien() -> None:
    """Le vrai négatif : ajouter des fiches ne doit jamais rien déclencher."""
    etats = [
        ("aaaaaaa", _acc("sel-1", _fiche("id-1", type_voile="génois"))),
        ("bbbbbbb", _acc("sel-1", _fiche("id-1", type_voile="génois"), _fiche("id-2"))),
        ("ccccccc", _acc("sel-1", _fiche("id-1", type_voile="génois"), _fiche("id-2", matiere="Dacron"))),
    ]
    rapport = audit.analyser_historique(etats)

    assert rapport["effacements"] == []
    assert rapport["en_souffrance"] == []
    assert rapport["total_etats"] == 3


def test_historique_vide_ne_fabrique_rien() -> None:
    """Pas d'état, pas de conclusion — pas d'invention non plus."""
    rapport = audit.analyser_historique([])
    assert rapport["total_etats"] == 0
    assert rapport["effacements"] == []
    assert rapport["en_souffrance"] == []


# ---------------------------------------------------------------------------
# La réparation : elle refuse de deviner
# ---------------------------------------------------------------------------


def _reparer_avec_etats(tmp_path: Path, etats: list[tuple[str, dict]], **kwargs) -> dict:
    """Applique ``reparer`` sur des états fabriqués, sans toucher au dépôt."""
    chemin = tmp_path / "acc.json"
    acc = json.loads(json.dumps(etats[-1][1]))  # copie : l'état courant
    chemin.write_text(json.dumps(acc, ensure_ascii=False), encoding="utf-8")

    original = audit.lire_etats_git
    audit.lire_etats_git = lambda _chemin: etats
    try:
        return audit.reparer(chemin, **kwargs)
    finally:
        audit.lire_etats_git = original


def test_reparer_restaure_la_valeur_et_laisse_une_trace(tmp_path: Path, monkeypatch) -> None:
    """La valeur est RELUE dans l'état cité, jamais devinée."""
    journal = tmp_path / "reparations.json"
    monkeypatch.setattr(audit, "JOURNAL_REPARATIONS", journal)

    entree = _reparer_avec_etats(
        tmp_path,
        [
            ("aaaaaaa", _acc("sel-1", _fiche("id-1", matiere="fibercom hybrid cool grey"))),
            ("bbbbbbb", _acc("sel-1", _fiche("id-1"))),
        ],
        id_fiche="id-1",
        axe="matiere",
        source="aaaaaaa",
        operateur="patron",
        motif="valeur relue dans l'état antérieur",
    )

    assert entree["avant"] is None
    assert entree["apres"] == "fibercom hybrid cool grey"
    assert entree["source_sha"] == "aaaaaaa"
    assert entree["operateur"] == "patron"
    # La trace est écrite et relisible.
    assert len(json.loads(journal.read_text(encoding="utf-8"))["reparations"]) == 1


def test_reparer_refuse_une_source_qui_ne_porte_pas_la_valeur(tmp_path) -> None:
    """On ne restaure pas depuis un état vide : ce serait deviner."""
    with pytest.raises(ValueError, match="ne porte PAS de valeur"):
        _reparer_avec_etats(
            tmp_path,
            [
                ("aaaaaaa", _acc("sel-1", _fiche("id-1"))),
                ("bbbbbbb", _acc("sel-1", _fiche("id-1"))),
            ],
            id_fiche="id-1",
            axe="matiere",
            source="aaaaaaa",
        )


def test_reparer_refuse_un_etat_inconnu(tmp_path) -> None:
    with pytest.raises(ValueError, match="n'est pas un état connu"):
        _reparer_avec_etats(
            tmp_path,
            [("aaaaaaa", _acc("sel-1", _fiche("id-1", matiere="Dacron")))],
            id_fiche="id-1",
            axe="matiere",
            source="zzzzzzz",
        )


def test_reparer_refuse_decraser_une_valeur_presente(tmp_path) -> None:
    """Réparer n'est pas réécrire : un axe déjà renseigné n'est pas touché."""
    with pytest.raises(ValueError, match="porte déjà"):
        _reparer_avec_etats(
            tmp_path,
            [
                ("aaaaaaa", _acc("sel-1", _fiche("id-1", matiere="Dacron"))),
                ("bbbbbbb", _acc("sel-1", _fiche("id-1", matiere="Mylar"))),
            ],
            id_fiche="id-1",
            axe="matiere",
            source="aaaaaaa",
        )


def test_larbre_de_travail_compte_comme_etat_courant(tmp_path: Path) -> None:
    """Le présent, c'est l'arbre de travail — pas le dernier commit.

    Bug réel du Lot 20 : l'audit ne lisait que les commits. Une réparation
    appliquée mais non encore versionnée restait signalée « en souffrance »,
    ce qui est faux et découragerait de réparer quoi que ce soit.

    On fabrique ici un dépôt git jetable : c'est le seul moyen de couvrir la
    fonction qui lit réellement l'historique.
    """
    import subprocess

    depot = tmp_path / "depot"
    depot.mkdir()
    (depot / "donnees").mkdir()
    cible = depot / "donnees" / "acc.json"

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=depot,
            check=True,
            capture_output=True,
            env={
                **{
                    "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t",
                    "HOME": str(tmp_path),
                    "PATH": "/usr/bin:/bin",
                }
            },
        )

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    cible.write_text(json.dumps(_acc("sel-1", _fiche("id-1", matiere="Dacron")), ensure_ascii=False), encoding="utf-8")
    git("add", "donnees/acc.json")
    git("commit", "-q", "-m", "premier etat")
    # Deuxième état versionné : la matière a disparu.
    cible.write_text(json.dumps(_acc("sel-1", _fiche("id-1")), ensure_ascii=False), encoding="utf-8")
    git("commit", "-q", "-am", "effacement")

    original = audit.RACINE_DEPOT
    audit.RACINE_DEPOT = depot
    try:
        # Réparé dans l'arbre de travail, sans commit.
        cible.write_text(
            json.dumps(_acc("sel-1", _fiche("id-1", matiere="Dacron")), ensure_ascii=False), encoding="utf-8"
        )
        etats = audit.lire_etats_git(cible)
        assert etats[-1][0] == audit.ETAT_ARBRE, "l'arbre de travail est le dernier état"
        assert len(etats) == 3, "deux commits + l'arbre de travail"

        rapport = audit.analyser_historique(etats)
        assert len(rapport["effacements"]) == 1, "l'effacement est bien vu"
        assert rapport["en_souffrance"] == [], (
            "une réparation appliquée dans l'arbre de travail doit être reconnue, même sans commit"
        )
    finally:
        audit.RACINE_DEPOT = original
