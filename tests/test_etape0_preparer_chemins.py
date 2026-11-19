"""Le pré-contrôle d'import : prouver avant d'écrire, jamais après.

L'enjeu, rappelé parce qu'il n'est pas intuitif : une fiche est identifiée par
``sha256(sel | chemin)`` et **le chemin n'est jamais conservé**. Un chemin
erroné ne peut donc pas être corrigé après coup, et le même dossier importé
plus tard sous son vrai chemin produirait un SECOND identifiant — deux fiches
pour une seule, indétectables puisque le corpus ne garde aucune empreinte de
contenu. C'est une erreur irrécupérable qui fausse le dénominateur de A.1.

Ces épreuves vérifient que le pré-contrôle attrape ces cas AVANT l'écriture.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import etape0_preparer_chemins as prep
from scripts.etape0_corpus import ajouter_fiche, identifiant, nouvel_accumulateur


def _accumulateur(tmp_path: Path, sel: str = "sel-de-test") -> Path:
    """Un accumulateur valide, avec un sel FIXE pour rendre les ids reproductibles."""
    chemin = tmp_path / "acc.json"
    acc = nouvel_accumulateur()
    acc["sel"] = sel
    chemin.write_text(json.dumps(acc))
    return chemin


def test_chemins_neufs_sont_annonces_comme_nouveaux(tmp_path: Path, capsys) -> None:
    acc = _accumulateur(tmp_path)
    liste = tmp_path / "chemins.txt"
    liste.write_text("/VOILES 2026/DOSSIER A\n/VOILES 2026/DOSSIER B\n")

    assert prep.main(["--fichier", str(acc), str(liste)]) == 0
    sortie = capsys.readouterr().out
    assert sortie.count("NOUVEAU") == 2
    assert "Aucune collision" in sortie


def test_deux_chemins_identiques_seraient_double_comptes(tmp_path: Path, capsys) -> None:
    """Le cœur du risque : deux lignes pour le même dossier."""
    acc = _accumulateur(tmp_path)
    liste = tmp_path / "chemins.txt"
    liste.write_text("/VOILES 2026/DOSSIER A\n/VOILES 2026/DOSSIER A\n")

    assert prep.main(["--fichier", str(acc), str(liste)]) == 1
    assert "COLLISION INTERNE" in capsys.readouterr().out


def test_un_chemin_deja_importe_est_reconnu_et_non_recompte(tmp_path: Path, capsys) -> None:
    """Reprendre un relevé interrompu ne doit pas créer de doublon.

    C'est la propriété que l'import promet (idempotence par chemin) : le
    pré-contrôle doit la rendre visible avant l'écriture.
    """
    acc = _accumulateur(tmp_path)
    donnees = json.loads(acc.read_text())
    donnees, cree = ajouter_fiche(
        donnees, chemin="/VOILES 2026/DOSSIER A", racine="VOILES 2026", source="dropbox"
    )
    assert cree is True
    acc.write_text(json.dumps(donnees))

    liste = tmp_path / "chemins.txt"
    liste.write_text("/VOILES 2026/DOSSIER A\n")

    assert prep.main(["--fichier", str(acc), str(liste)]) == 0
    assert "DÉJÀ CONNU" in capsys.readouterr().out


def test_racine_divergente_sur_un_chemin_connu_est_signalee(tmp_path: Path, capsys) -> None:
    """Le même dossier déclaré sous une autre année : incohérence, pas un ajout."""
    acc = _accumulateur(tmp_path)
    donnees = json.loads(acc.read_text())
    donnees, _ = ajouter_fiche(
        donnees, chemin="/VOILES 2026/DOSSIER A", racine="VOILES 2026", source="dropbox"
    )
    acc.write_text(json.dumps(donnees))

    liste = tmp_path / "chemins.txt"
    liste.write_text("/VOILES 2026/DOSSIER A|VOILES 2024\n")

    assert prep.main(["--fichier", str(acc), str(liste)]) == 1
    assert "DIVERGENCE" in capsys.readouterr().out


def test_lignes_vides_et_commentaires_sont_ignores(tmp_path: Path, capsys) -> None:
    acc = _accumulateur(tmp_path)
    liste = tmp_path / "chemins.txt"
    liste.write_text("# un commentaire\n\n/VOILES 2026/DOSSIER A\n\n")

    assert prep.main(["--fichier", str(acc), str(liste)]) == 0
    assert "Chemins proposés : 1" in capsys.readouterr().out


def test_fichier_de_chemins_absent_est_une_erreur(tmp_path: Path) -> None:
    assert prep.main(["--fichier", str(_accumulateur(tmp_path)), str(tmp_path / "nul.txt")]) == 1


def test_l_identifiant_depend_du_chemin_complet() -> None:
    """Deux dossiers distincts ne doivent jamais partager un identifiant."""
    assert identifiant("sel", "/VOILES 2026/A") != identifiant("sel", "/VOILES 2026/B")
    # Et le même chemin reste stable : c'est ce qui rend l'import rejouable.
    assert identifiant("sel", "/VOILES 2026/A") == identifiant("sel", "/VOILES 2026/A")
