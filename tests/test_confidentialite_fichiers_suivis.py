"""Les noms des fichiers suivis par git — l'angle mort bouché.

Constat à l'origine de ce fichier : le garde-fou existant
(:func:`tests.test_etape0_corpus.test_aucun_fichier_consigne_ne_nomme_un_bateau`)
lit le CONTENU de deux fichiers JSON. Il ne regarde jamais le NOM des fichiers
que git suit. Sept archives de 4,7 Mo, dont le nom porte un nom de bateau,
étaient donc consignées depuis des mois sans que rien ne bronche.

Ces épreuves bouchent l'angle mort **sans jamais transcrire un nom**. Les
exemptions sont désignées par un MOTIF d'export, pas par une liste de noms :
écrire ici « BAVARIA 32… » serait précisément la fuite que l'épreuve interdit.

 Les sept archives ont depuis été PURGÉES du dépôt (décision de l'exploitante,
 cf. ``docs/PURGE_DONNEES_CLIENTS_2026-11-17.md``). L'exemption qui les
 tolérait est donc retirée — la laisser en place aurait laissé ouverte
 l'autoroute même qu'elle prétendait borner. La règle devient ce qu'elle
 n'aurait jamais dû cesser d'être : **aucune archive suivie, sans exception**.
 Le motif de l'export purgé demeure, mais il sert désormais à interdire leur
 RÉINTRODUCTION, non à les autoriser.
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

#: ``sample_data/`` tient des fiches d'exemple — désormais TOUTES
#: synthétiques : la fiche de référence y est régénérée par
#: ``scripts/generer_fiche_reference_synthetique.py``. Conserver ce dossier
#: est une décision de l'exploitante : le purger casserait les parcours de
#: démonstration, et les fiches qu'il porte ne sont pas des pièces d'épreuve
#: au sens strict.
#:
#: L'exemption porte sur l'EMPLACEMENT, jamais sur les noms, pour la même
#: raison que les archives : transcrire ici le nom d'un bateau serait la fuite
#: que l'on prétend colmater. Et comme pour les archives, le COMpte est figé :
#: un PDF de plus dans ce dossier fait échouer l'épreuve.
#:
#: Le compte est passé de 3 à 6 : aux trois PDF d'exemple s'ajoutent les
#: trois affaires du corpus de repli de la recette locale
#: (sample_data/recette/), rendues nécessaires par la purge des archives du
#: corpus réel. Toutes synthétiques, toutes régénérées par un générateur
#: versionné. Sept PDF, en revanche, serait une venue d'ailleurs.
NOMBRE_PDF_SAMPLE_DATA_ACCEPTES = 6

#: Motif de l'export qui avait produit les sept archives (horodatage
#: Dropbox du 2026-09-28). Elles ont été purgées : ce motif ne désigne plus
#: une exemption, il sert à empêcher leur RÉINTRODUCTION — une archive qui y
#: correspondrait est refusée nommément, et non traitée comme une venue
#: d'ailleurs. Désigné par le motif et jamais par les noms : transcrire ici
#: un nom de bateau serait la fuite que l'on prétend colmater.
MOTIF_EXPORT_PURGE = re.compile(r"-20260928T1823\d{2}Z-1-001\.zip$", re.IGNORECASE)


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


def test_aucune_archive_n_est_consignee(fichiers_suivis: list[str]) -> None:
    """Toute archive suivie par git est une fuite — il n'y a plus d'exemption.

    Les sept archives tolérées jusqu'ici ont été purgées. La tolérance les
    accompagnait : elle disparaît avec elles. Une archive contient des
    documents clients ; aucune n'a rien à faire dans l'index.
    """
    fautives = [
        chemin
        for chemin in fichiers_suivis
        if Path(chemin).suffix.lower() in EXTENSIONS_SENSIBLES
    ]
    assert fautives == [], (
        f"archives suivies par git : {fautives}. Le dépôt n'en tolère PLUS "
        "aucune depuis la purge. Voir le module pour la décision."
    )


def test_aucune_archive_de_l_export_purge_ne_revient(fichiers_suivis: list[str]) -> None:
    """L'export purgé ne doit pas se réintroduire par une autre voie.

    Le motif de l'export du 2026-09-28 désignait les sept archives retirées.
    Il ne les autorise plus : il les RECONNAÎT, pour qu'une réintroduction —
    même rapportée sous un autre chemin — soit nommée pour ce qu'elle est,
    au lieu de passer pour une archive ordinaire.
    """
    revenues = [c for c in fichiers_suivis if MOTIF_EXPORT_PURGE.search(c)]
    assert revenues == [], (
        f"archives de l'export purgé de nouveau suivies : {revenues}. Elles ont "
        "été retirées du dépôt sur décision de l'exploitante ; les remettre "
        "n'est pas un oubli, c'est une décision à reprendre explicitement."
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

    Six PDF, pas un de plus. Ajouter une fiche — RÉELLE surtout — dans ce
    dossier est exactement le geste que la décision de risque accepté n'a
    jamais couvert ; la fiche de référence qui s'y trouve est synthétique et
    régénérée par un générateur versionné.
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

    # Et la règle ne tolère plus aucune archive, exemption comprise : le motif
    # de l'export purgé ne doit plus rien couvrir du tout.
    assert not any(MOTIF_EXPORT_PURGE.search(c) for c in suivis), (
        "une archive de l'export purgé est de nouveau suivie : l'exemption a "
        "été retirée avec les archives qu'elle couvrait, la faire reparaître "
        "rouvrirait l'autoroute."
    )
