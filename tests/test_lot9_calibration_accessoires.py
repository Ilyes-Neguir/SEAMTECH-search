"""Lot CAL — calibration du gabarit accessoire sur les formulaires réels.

La mesure (Dropbox, hors Git, structures et comptes seulement) a établi les
formes réelles des documents accessoires de l'atelier : fiche de prise de
cotes Lazy Bag imprimée (« Réservé pour le CLIENT », « Type de bateau »,
« Type de LazyBag », « Nombre d'ancrages », « Couleurs Sunbrella », cases à
cocher, valeurs manuscrites), plan de coupe CAD (« <PRODUIT> ST### »,
matière/couleur en gros caractères, dimensions SANS libellé), croquis libre
« pour devis » entièrement manuscrit, impression de commande boutique.

Le contrat ci-dessous est 100 % SYNTHÉTIQUE : les textes reprennent la
STRUCTURE imprimée des formulaires (libellés), jamais une valeur cliente.
Il épingle, pour la version 2 du gabarit `FICHE_ACCESSOIRE_V1` :

* détection renforcée (lazybag en un mot, sac de pont, cagnard, ancres du
  formulaire) SANS voler une fiche voile — le formulaire Lazy Bag contient
  « FullBatten » et « coulisseau », et doit rester accessoire ;
* lecture par les libellés réels (« N° de commande », « Type de bateau »,
  « Couleurs Sunbrella »…) vers les MÊMES cibles que la v1 (aucune cible
  nouvelle : pas de migration, la projection Lot 7 est inchangée) ;
* l'ancre générique « type » ne doit JAMAIS lire « Type de bateau : … »
  comme le type de l'accessoire ;
* rien n'est inventé : un en-tête sans valeur sur sa ligne (cases à cocher)
  et des nombres sans libellé (plan de coupe, croquis) ne produisent aucun
  champ — l'approbateur reste seul décideur ;
* la v1 publiée reste intacte (RG11) : la v2 s'enregistre devant elle et
  seule la version maximale reste active en base.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.accessoires import (
    construire_extraction_accessoire,
    gabarit_accessoire,
    gabarit_accessoire_v1,
)
from seamtech_search.fiches.depot import scanner_dossier
from seamtech_search.fiches.extraction import extraire_fiche, normaliser_terme
from seamtech_search.fiches.gabarits import (
    CODE_GABARIT_ACCESSOIRE,
    GABARIT_ACCESSOIRE_V1,
    GABARIT_ACCESSOIRE_V2,
    GABARIT_GV_FULLBATTEN,
    GABARITS_EMBARQUES,
    detecter_gabarit,
)


def _gabarits_actifs() -> list:
    """Équivalent « base » des gabarits embarqués : par code, seule la
    version la plus récente est active (initialiser_gabarits)."""
    actifs: dict[str, object] = {}
    for gabarit in GABARITS_EMBARQUES:
        courant = actifs.get(gabarit.code)
        if courant is None or gabarit.version > courant.version:  # type: ignore[attr-defined]
            actifs[gabarit.code] = gabarit
    return list(actifs.values())


def _ecrire_pdf(chemin: Path, lignes: list[str]) -> Path:
    """PDF texte natif synthétique (reportlab) — une ligne par drawString."""
    from reportlab.pdfgen import canvas

    toile = canvas.Canvas(str(chemin))
    y = 780
    for ligne in lignes:
        toile.drawString(60, y, ligne)
        y -= 24
    toile.save()
    return chemin


# Structure imprimée de la fiche de prise de cotes Lazy Bag (libellés seuls,
# valeurs inventées). Les cases à cocher sont des lignes sans valeur à droite
# de l'en-tête : le moteur ne doit rien en déduire.
LIGNES_PRISE_DE_COTES = [
    "Réservé pour le CLIENT",
    "Type de bateau : Voilier Fictif 42",
    "Type de LazyBag",
    "1 partie",
    "2 parties",
    "Multicoque",
    "Nombre d'ancrages",
    "3 ancrages",
    "4 ancrages",
    "Type de grand-voile",
    "Classique",
    "FullBatten",
    "Bordure de la Grand-voile",
    "Couleurs Sunbrella",
    "Les housses de bôme sont fabriquées en tissu acrylique teinté masse",
    "par le coulisseau d'écoute ou l'anneau, capotes de roof et biminis",
]

LIGNES_FICHE_COMPLETE = [
    "FICHE ACCESSOIRE - CALIBRATION SYNTHETIQUE",
    "N° de commande : CAL-2026-001",
    "Type de bateau : Voilier Fictif 42",
    "Type : Housse de protection fictive",
    "Matière : Acrylique fictif",
    "Couleurs Sunbrella : bleu fictif",
    "Dimensions : 120 cm x 45 cm",
    "Quantité : 3",
]

# Plan libre / croquis : aucun libellé, des nombres nus — rien à structurer.
LIGNES_CROQUIS_LIBRE = [
    "CAGNARD",
    "1.99",
    "0.57",
    "X2",
    "Sunbrella bleu fictif",
    "Ourlet sur les 4 côtés",
]

# Impression de commande boutique : texte de gestion, aucun libellé métier
# d'atelier — le scanner ne doit pas la choisir comme fiche.
LIGNES_COMMANDE_BOUTIQUE = [
    "Commande confirmee",
    "Client : Personne Fictive",
    "Date : 01/01/2026",
    "Total : 100 EUR",
]


# ---------------------------------------------------------------------------
# Registre : v2 devant v1, v1 intacte, mêmes cibles (pas de migration)
# ---------------------------------------------------------------------------


def test_v2_est_le_gabarit_accessoire_courant() -> None:
    courant = gabarit_accessoire()
    assert courant.code == CODE_GABARIT_ACCESSOIRE == "FICHE_ACCESSOIRE_V1"
    assert courant.version == 2
    assert courant is GABARIT_ACCESSOIRE_V2


def test_v1_publiee_reste_intacte() -> None:
    v1 = gabarit_accessoire_v1()
    assert v1 is GABARIT_ACCESSOIRE_V1
    assert v1.version == 1
    # La v1 générique n'a pas été réécrite : les ancres calibrées sont en v2.
    ancres_v1 = {normaliser_terme(a) for a in v1.ancres_detection}
    assert "cagnard" not in ancres_v1
    assert "lazybag" not in ancres_v1
    assert "Lazy Bag" in v1.ancres_detection


def test_v2_devant_v1_dans_le_registre_embarque() -> None:
    ordre = list(GABARITS_EMBARQUES)
    assert GABARIT_ACCESSOIRE_V2 in ordre
    assert GABARIT_ACCESSOIRE_V1 in ordre
    assert ordre.index(GABARIT_ACCESSOIRE_V2) < ordre.index(GABARIT_ACCESSOIRE_V1)
    versions = {
        gabarit.version
        for gabarit in GABARITS_EMBARQUES
        if gabarit.code == CODE_GABARIT_ACCESSOIRE
    }
    assert versions == {1, 2}


def test_v2_ne_change_aucune_cible_de_projection() -> None:
    """Invariant Lot CAL : mêmes cibles que la v1 → la projection Lot 7 et
    le schéma (migration 031) restent valides sans aucune migration."""
    cibles_v1 = {regle.cible for regle in GABARIT_ACCESSOIRE_V1.champs}
    cibles_v2 = {regle.cible for regle in GABARIT_ACCESSOIRE_V2.champs}
    assert cibles_v2 == cibles_v1
    assert cibles_v2 == {
        "fiche.code",
        "accessoire.type",
        "accessoire.matiere",
        "accessoire.couleur",
        "accessoire.dimensions",
        "accessoire.quantite",
        "accessoire.modele_bateau",
    }


# ---------------------------------------------------------------------------
# Détection : le formulaire Lazy Bag gagne malgré « FullBatten/coulisseau » ;
# une vraie GV reste voile.
# ---------------------------------------------------------------------------


def test_detection_lazybag_en_un_mot() -> None:
    texte = normaliser_terme("fiche lazybag pour housse de bôme")
    assert GABARIT_ACCESSOIRE_V2.score_detection(texte) >= 2
    # Delta de calibration : la v1 ne connaissait que « Lazy Bag » en 2 mots.
    texte_solo = normaliser_terme("lazybag")
    assert GABARIT_ACCESSOIRE_V2.score_detection(texte_solo) >= 1
    assert GABARIT_ACCESSOIRE_V1.score_detection(texte_solo) == 0


def test_detection_familles_reelles() -> None:
    for texte in (
        "sac de pont avec fermetures",
        "cagnard de cockpit",
        "fiche de prise de cotes",
    ):
        assert GABARIT_ACCESSOIRE_V2.score_detection(normaliser_terme(texte)) >= 1, texte


def test_formulaire_prise_de_cotes_reste_accessoire() -> None:
    texte = normaliser_terme("\n".join(LIGNES_PRISE_DE_COTES))
    score_accessoire = GABARIT_ACCESSOIRE_V2.score_detection(texte)
    score_gv = GABARIT_GV_FULLBATTEN.score_detection(texte)
    # Le formulaire cite « FullBatten » (case) : la GV marque, mais perd.
    assert score_gv >= 1
    assert score_accessoire > score_gv
    gagne = detecter_gabarit(texte, _gabarits_actifs())
    assert gagne.code == CODE_GABARIT_ACCESSOIRE
    assert gagne.version == 2


def test_vraie_grand_voile_jamais_accessoire() -> None:
    texte = normaliser_terme(
        "GRAND-VOILE fullbatten lattes ris 1 ris 2 coulisseaux goussets ragage"
    )
    assert GABARIT_ACCESSOIRE_V2.score_detection(texte) == 0
    gagne = detecter_gabarit(texte, _gabarits_actifs())
    assert gagne.code != CODE_GABARIT_ACCESSOIRE


# ---------------------------------------------------------------------------
# Extraction sur PDFs synthétiques : libellés réels, gardes anti-vol,
# aucun champ inventé.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fiche_prise_de_cotes(tmp_path_factory: pytest.TempPathFactory):
    chemin = _ecrire_pdf(
        tmp_path_factory.mktemp("cal") / "prise-de-cotes.pdf",
        LIGNES_PRISE_DE_COTES,
    )
    return extraire_fiche(chemin, gabarits=_gabarits_actifs())


def test_extraction_prise_de_cotes(fiche_prise_de_cotes) -> None:
    fiche = fiche_prise_de_cotes
    assert fiche.gabarit_code == CODE_GABARIT_ACCESSOIRE
    assert fiche.gabarit_version == 2
    traces = {champ.champ: champ for champ in fiche.tous_les_champs()}
    # « Type de bateau » (libellé réel du formulaire) → modèle bateau.
    assert traces["accessoire.modele_bateau"].valeur_brute == "Voilier Fictif 42"
    # « Type de LazyBag » est un EN-TÊTE de cases à cocher, sans valeur sur
    # sa ligne : le type n'est PAS inventé (la case cochée est illisible
    # par un moteur libellé→valeur ; l'approbateur décidera).
    assert "accessoire.type" not in traces
    # « Couleurs Sunbrella » est un en-tête de nuancier : idem.
    assert "accessoire.couleur" not in traces
    # Aucun libellé de commande imprimé sur cette variante du formulaire.
    assert fiche.code is None


@pytest.fixture(scope="module")
def fiche_complete(tmp_path_factory: pytest.TempPathFactory):
    chemin = _ecrire_pdf(
        tmp_path_factory.mktemp("cal") / "fiche-complete.pdf",
        LIGNES_FICHE_COMPLETE,
    )
    return extraire_fiche(chemin, gabarits=_gabarits_actifs())


def test_extraction_libelles_reels(fiche_complete) -> None:
    fiche = fiche_complete
    assert fiche.gabarit_code == CODE_GABARIT_ACCESSOIRE
    assert fiche.gabarit_version == 2
    # « N° de commande » (libellé réel) → code de fiche.
    assert fiche.code == "CAL-2026-001"
    traces = {champ.champ: champ for champ in fiche.tous_les_champs()}
    # Garde anti-vol : « Type : » (générique gardé) lit le type d'accessoire,
    # jamais la ligne « Type de bateau : … » placée AVANT dans le document.
    assert traces["accessoire.type"].valeur_brute == "Housse de protection fictive"
    assert traces["accessoire.modele_bateau"].valeur_brute == "Voilier Fictif 42"
    assert traces["accessoire.matiere"].valeur_brute == "Acrylique fictif"
    # « Couleurs Sunbrella : … » (libellé du nuancier) → couleur.
    assert traces["accessoire.couleur"].valeur_brute == "bleu fictif"
    assert traces["accessoire.dimensions"].valeur_brute == "120 cm x 45 cm"
    assert traces["accessoire.quantite"].valeur_normalisee == "3"


def test_projection_fiche_complete(fiche_complete) -> None:
    extraction = construire_extraction_accessoire(fiche_complete)
    assert extraction.code_fiche == "CAL-2026-001"
    assert extraction.gabarit_code == CODE_GABARIT_ACCESSOIRE
    assert extraction.gabarit_version == 2
    assert extraction.type_accessoire == "Housse de protection fictive"
    assert extraction.matiere == "Acrylique fictif"
    assert extraction.couleur == "bleu fictif"
    assert extraction.longueur_m == pytest.approx(1.2)
    assert extraction.largeur_m == pytest.approx(0.45)
    assert extraction.quantite == 3
    assert extraction.modele_bateau_brut == "Voilier Fictif 42"
    assert extraction.statut_extraction == "complete"


def test_croquis_libre_aucun_champ_invente(tmp_path: Path) -> None:
    chemin = _ecrire_pdf(tmp_path / "croquis.pdf", LIGNES_CROQUIS_LIBRE)
    fiche = extraire_fiche(chemin, gabarits=_gabarits_actifs())
    assert fiche.gabarit_code == CODE_GABARIT_ACCESSOIRE  # « cagnard » détecte
    assert fiche.code is None
    champs = {champ.champ for champ in fiche.tous_les_champs()}
    assert not {c for c in champs if c.startswith("accessoire.")}
    extraction = construire_extraction_accessoire(fiche)
    assert extraction.type_accessoire is None
    assert extraction.couleur is None
    assert extraction.longueur_m is None
    assert extraction.quantite is None
    assert extraction.statut_extraction == "a_reprendre"


# ---------------------------------------------------------------------------
# Scanner de dossier : dans un dossier avec deux PDFs, la fiche de prise de
# cotes gagne contre l'impression de commande boutique.
# ---------------------------------------------------------------------------


def test_scanner_choisit_la_fiche_contre_la_commande_boutique(
    tmp_path: Path,
) -> None:
    dossier = tmp_path / "DOSSIER-CAL"
    dossier.mkdir()
    _ecrire_pdf(dossier / "a-prise-de-cotes.pdf", LIGNES_PRISE_DE_COTES)
    _ecrire_pdf(dossier / "b-commande.pdf", LIGNES_COMMANDE_BOUTIQUE)
    plan = scanner_dossier(dossier, _gabarits_actifs())
    assert plan.accepte
    assert plan.pdf_fiche is not None
    assert plan.pdf_fiche.name == "a-prise-de-cotes.pdf"
    assert plan.gabarit_code == CODE_GABARIT_ACCESSOIRE
    pieces = {piece.chemin.name for piece in plan.pieces}
    assert pieces == {"b-commande.pdf"}
