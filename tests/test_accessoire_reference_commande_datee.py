"""Fiche d'accessoire à référence de commande DATÉE (« Commande 170124CS »).

Mesure du 2026-10-29 sur un document client réel de l'archive :
`/ACCESSOIRES 2024/LAZY BAG 2 PARTIES DUFOUR 385/LB FINI.pdf`. Ce formulaire
est **entièrement lisible** — titre « Lazy Bag », client « SEAMTECH PRO
(CLASS SAILS) », bateau « DUFOUR 385 », gousset de latte 3.96 m, ralingue
2.55 m, coulisseaux A031 (x3), lazy 4.1 m — mais il était **refusé au dépôt** :

    aucun code de fiche lisible : ni le PDF, ni son nom « LB FINI.pdf », ni le
    nom du dossier « LAZY BAG 2 PARTIES DUFOUR 385 » ne portent de référence

La cause n'est pas le document, c'est le motif : il n'acceptait que la forme
« lettres puis chiffres » (« ST64 », « ST 074b »). Or cette fiche imprime sa
référence de commande dans l'autre sens : `Commande 170124CS` — six chiffres
puis deux lettres. Le formulaire était donc le seul des six mesurés à ne
pouvoir entrer dans l'application.

Deux garde-fous sont vérifiés ici :

* une valeur qui n'est PAS une référence (une date « 01-02-2024 », une mesure
  « 884.68 », une phrase) reste refusée : élargir le motif ne doit pas ouvrir
  la porte à n'importe quelle ligne ;
* les références déjà lues (« ST96 », « ST 074b », « ST 256b », « ST105 ») sont
  inchangées.

Les PDF sont synthétiques : mise en page et libellés mesurés, valeurs inventées.
"""

from __future__ import annotations

from reportlab.pdfgen import canvas

from seamtech_search.fiches.extraction import extraire_fiche
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES

PAYSAGE = (792.0, 612.0)


def _formulaire_lazy_bag(chemin, ligne_commande: str):
    """Fiche de Lazy Bag, au format paysage mesuré sur les formulaires réels."""
    pdf = canvas.Canvas(str(chemin), pagesize=PAYSAGE)
    pdf.setFont("Helvetica", 9)
    lignes = [
        "SEAMTECH PRO",
        "LAZY BAG 2 PARTIES DUFOUR 385",
        "Fiche de Lazy Bag",
        "01-02-2024",
        "Bateau DUFOUR 385",
        ligne_commande,
        "Client (réf.) SEAMTECH PRO (CLASS SAILS)",
        "Longueur pour Facturation 5.12m",
        "Mesure \"E\" 4.3m",
        "MARINE BLUE 5031 Longueur du gousset de latte 3.96m - Latte jonc 12mm avec manchons et embouts plats Contrôle",
        "Logo SO",
        "Couleur MARINE BLUE 5031",
        "Diametre de ralingue* 8 mm",
        "Longueur de la ralingue* 2.55m",
        "Type des coulisseaux A031 (x3)",
        "Longueur du lazy* 4.1m",
        "Hauteur du lazy* 0.38m",
        "Observations/Notes",
    ]
    for indice, texte in enumerate(lignes):
        pdf.drawString(30.0, 560.0 - indice * 22.0, texte)
    pdf.save()
    return chemin


def test_reference_de_commande_datee_est_lue(tmp_path) -> None:
    """Mesure réelle : « Commande 170124CS » doit donner le code 170124CS."""
    pdf = _formulaire_lazy_bag(tmp_path / "LB FINI.pdf", "Commande 170124CS")
    fiche = extraire_fiche(pdf, gabarits=list(GABARITS_EMBARQUES))
    assert fiche.code == "170124CS"
    assert fiche.titre == "Lazy Bag"
    assert fiche.client_nom == "SEAMTECH PRO"


def test_formes_de_reference_deja_lues_inchangees(tmp_path) -> None:
    """Élargir le motif ne doit rien changer aux références « ST… »."""
    for attendu in ("ST64", "ST 074b", "ST 256b", "ST105", "ST96"):
        pdf = _formulaire_lazy_bag(tmp_path / f"{attendu.strip()}.pdf", f"Commande {attendu}")
        fiche = extraire_fiche(pdf, gabarits=list(GABARITS_EMBARQUES))
        assert fiche.code == attendu.upper().replace("  ", " ")


def test_une_date_n_est_pas_une_reference(tmp_path) -> None:
    """« Commande 01-02-2024 » n'est pas un code : rien ne doit être inventé."""
    pdf = _formulaire_lazy_bag(tmp_path / "date.pdf", "Commande 01-02-2024")
    fiche = extraire_fiche(pdf, gabarits=list(GABARITS_EMBARQUES))
    assert fiche.code is None


def test_une_mesure_n_est_pas_une_reference(tmp_path) -> None:
    """« Commande 884.68 » (un montant) n'est pas un code."""
    pdf = _formulaire_lazy_bag(tmp_path / "montant.pdf", "Commande 884.68")
    fiche = extraire_fiche(pdf, gabarits=list(GABARITS_EMBARQUES))
    assert fiche.code is None
