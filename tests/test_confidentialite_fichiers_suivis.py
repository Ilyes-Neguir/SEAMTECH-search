"""Les noms des fichiers suivis par git — l'angle mort bouché.

Constat à l'origine de ce fichier : le garde-fou existant
(:func:`tests.test_etape0_corpus.test_aucun_fichier_consigne_ne_nomme_un_bateau`)
lit le CONTENU de deux fichiers JSON. Il ne regarde jamais le NOM des fichiers
que git suit. Sept archives de 4,7 Mo, dont le nom porte un nom de bateau,
étaient donc consignées depuis des mois sans que rien ne bronche.

Ces épreuves bouchent l'angle mort **sans jamais transcrire un nom**. Les
exemptions sont désignées par un MOTIF d'export, pas par une liste de noms :
écrire ici « BAVARIA 32… » serait précisément la fuite que l'épreuve interdit.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]

#: Extensions qui, suivies par git, sont presque toujours des dépôts de
#: documents réels et non des pièces d'épreuve.
EXTENSIONS_SENSIBLES = frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2"})

#: Seules ces pièces de jeu d'essai ont le droit d'être des PDF suivis.
#: Hors de ces dossiers, un PDF suivi est un document métier.
DOSSIERS_PIECES_ADMISES = ("tests/fixtures/", "frontend/e2e/live-fixtures/")

#: ``sample_data/`` tient des fiches d'exemple — dont au moins une réelle
#: (celle que le plan de mise en production nomme). Conserver ce dossier est
#: une décision de l'exploitante, au même titre que les sept archives : la
#: purger casserait les parcours de démonstration, et les fiches qu'il porte
#: ne sont pas des pièces d'épreuve au sens strict.
#:
#: L'exemption porte sur l'EMPLACEMENT, jamais sur les noms, pour la même
#: raison que les archives : transcrire ici le nom d'un bateau serait la fuite
#: que l'on prétend colmater. Et comme pour les archives, le COMpte est figé :
#: un quatrième PDF dans ce dossier fait échouer l'épreuve.
NOMBRE_PDF_SAMPLE_DATA_ACCEPTES = 3

#: Archives réelles acceptées comme risque — décision de l'exploitante.
#:
#: Désignées par le motif de l'export qui les a produites (horodatage
#: Dropbox du 2026-09-28), jamais par leur nom : une exemption nominative
#: transcrirait dans ce dépôt les noms de bateaux que l'épreuve a pour
#: fonction d'en tenir dehors. Le motif couvre exactement les sept et rien
#: d'autre — l'épreuve :func:`test_l_exemption_ne_couvre_que_les_sept_connues`
#: le vérifie.
#:
#: RISQUE ACCEPTÉ : ces archives contiennent des documents clients réels. Les
#: purger réécrirait l'historique de ``main`` (opération destructive et
#: irréversible). L'exploitante a choisi de les conserver. La contrepartie est
#: ce garde-fou : il n'en interdit pas l'existence, il interdit leur
#: RÉINTRODUCTION ailleurs, et toute huitième archive.
MOTIF_ARCHIVES_ACCEPTEES = re.compile(r"-20260928T1823\d{2}Z-1-001\.zip$", re.IGNORECASE)

#: Combien d'archives l'exemption doit couvrir. Figé : si quelqu'un élargit le
#: motif ``MOTIF_ARCHIVES_ACCEPTEES`` pour y faire entrer une nouvelle archive,
#: ce compte ne ment pas et l'épreuve échoue.
NOMBRE_ARCHIVES_ACCEPTEES = 7


def _fichiers_suivis() -> list[str]:
    """Les fichiers réellement suivis par git, pas ceux du disque.

    On interroge git et non le système de fichiers : un fichier présent sur
    le disque mais non suivi ne sera pas poussé, alors qu'un fichier absent du
    disque mais suivi sera restauré au prochain ``checkout``. C'est l'index qui
    fait foi.
    """
    resultat = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=RACINE,
        capture_output=True,
        check=True,
    )
    return [chemin for chemin in resultat.stdout.decode("utf-8").split("\0") if chemin]


@pytest.fixture(scope="module")
def fichiers_suivis() -> list[str]:
    return _fichiers_suivis()


def test_aucune_archive_n_est_consignee_hors_exemption(fichiers_suivis: list[str]) -> None:
    """Toute archive suivie par git, hors les sept acceptées, est une fuite."""
    fautives = [
        chemin
        for chemin in fichiers_suivis
        if Path(chemin).suffix.lower() in EXTENSIONS_SENSIBLES
        and not MOTIF_ARCHIVES_ACCEPTEES.search(chemin)
    ]
    assert fautives == [], (
        "archives suivies par git hors exemption : "
        f"{fautives}. Une archive contient des documents clients ; elle n'a rien "
        "à faire dans l'index. Voir le module pour la décision de risque accepté."
    )


def test_l_exemption_ne_couvre_que_les_sept_connues(fichiers_suivis: list[str]) -> None:
    """Le motif d'exemption ne doit pas servir d'autoroute.

    Sept archives sont acceptées, pas une de plus. Si une huitième se présente,
    soit elle correspond au motif — et ce test casse, volontairement — soit
    elle n'y correspond pas, et le précédent test casse. Aucune issue.
    """
    couvertes = [
        chemin for chemin in fichiers_suivis if MOTIF_ARCHIVES_ACCEPTEES.search(chemin)
    ]
    assert len(couvertes) == NOMBRE_ARCHIVES_ACCEPTEES, (
        f"{len(couvertes)} archive(s) couverte(s) par l'exemption, "
        f"{NOMBRE_ARCHIVES_ACCEPTEES} attendue(s) : {couvertes}. "
        "Élargir une exemption n'est pas la resserrer ; toute archive "
        "supplémentaire exige une décision, pas un motif plus large."
    )


def test_aucun_pdf_metier_n_est_consigne(fichiers_suivis: list[str]) -> None:
    """Un PDF suivi hors pièces d'épreuve et hors ``sample_data/`` est une fuite."""
    fautifs = [
        chemin
        for chemin in fichiers_suivis
        if Path(chemin).suffix.lower() == ".pdf"
        and not any(chemin.startswith(dossier) for dossier in DOSSIERS_PIECES_ADMISES)
        and not chemin.startswith("sample_data/")
    ]
    assert fautifs == [], (
        f"PDF suivis hors pièces d'épreuve : {fautifs}. "
        "Un PDF qui n'est pas une pièce de test est une fiche métier."
    )


def test_le_jeu_d_exemples_ne_gonfle_pas(fichiers_suivis: list[str]) -> None:
    """``sample_data/`` est une exemption ACCEPTÉE, pas une autorisation d'y verser.

    Trois PDF, pas un de plus. Ajouter une fiche réelle dans ce dossier est
    exactement le geste que la décision de risque accepté n'a pas couvert.
    """
    comptes = [
        chemin for chemin in fichiers_suivis if chemin.startswith("sample_data/") and chemin.endswith(".pdf")
    ]
    assert len(comptes) == NOMBRE_PDF_SAMPLE_DATA_ACCEPTES, (
        f"{len(comptes)} PDF dans sample_data/, {NOMBRE_PDF_SAMPLE_DATA_ACCEPTES} attendus : "
        f"{comptes}. Y verser une fiche de plus demande une décision, pas un "
        "dépôt discret."
    )


def test_la_regle_lit_git_et_non_le_disque(tmp_path: Path) -> None:
    """La source de vérité est l'INDEX, pas le système de fichiers.

    Preuve directe : on dépose sur le disque une archive qui n'est PAS suivie
    par git. Une règle assise sur le disque la verrait et échouerait ; une
    règle assise sur l'index l'ignore, et c'est ce qu'on veut — ce qui compte
    est ce qui serait POUSSÉ, pas ce qui traîne sur un poste.

    L'inverse est tout aussi vrai et c'est ainsi que la fuite est passée
    inaperçue : les sept archives pouvaient être absentes du disque tout en
    restant consignées, prêtes à reparaître au prochain ``checkout``.
    """
    import os

    temoin = RACINE / "temoin_non_suivi_20260928T182399Z-1-001.zip"
    try:
        temoin.write_bytes(b"archive temoin, jamais ajoutee a l'index")
        suivis = _fichiers_suivis()
        assert suivis, "git ls-files n'a rien rendu : l'épreuve ne vaut plus rien"
        assert temoin.name not in suivis, (
            "l'archive témoin est sur le disque et pourtant listée comme suivie : "
            "la règle lit le système de fichiers au lieu de l'index git."
        )
        assert temoin.exists(), "le témoin doit exister pour que la preuve ait un sens"
    finally:
        if temoin.exists():
            os.remove(temoin)

    # Et les archives acceptées, elles, sont bien vues par la règle.
    assert any(MOTIF_ARCHIVES_ACCEPTEES.search(c) for c in suivis), (
        "les sept archives acceptées ne sont plus suivies : l'exemption est "
        "devenue sans objet, il faut alors la retirer plutôt que la laisser "
        "couvrir une autoroute."
    )
