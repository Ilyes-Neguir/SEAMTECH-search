"""Lot 2 — double vérification de l'extraction accessoire.

Le contrat est 100 % SYNTHÉTIQUE. Les libellés reprennent la STRUCTURE
imprimée des formulaires de l'atelier (casse et accents incohérents, unité
collée au nombre, astérisque, apostrophe, deux cotes sur une même ligne) ;
les valeurs sont inventées. Aucun document client, aucun nom de bateau ni de
fichier, aucune cote réelle n'entre dans ce dépôt.

Ce que ces épreuves verrouillent :

* un document est classé AVANT d'être lu (formulaire / plan / illisible) ;
* une cote n'est promue « vérifiée » que si deux lectures INDÉPENDANTES
  concordent — la lecture par libellé et la lecture par proximité ;
* sans seconde lecture possible, rien n'est promu (ni vérifié, ni discrédité) ;
* un désaccord n'est JAMAIS résolu en silence : il devient une anomalie
  FORTE qui renvoie le dossier à l'approbateur ;
* chaque cote est projetée SOUS SON PROPRE LIBELLÉ au lieu d'être perdue ou
  rangée de travers ;
* sans pages transmises, le comportement est exactement celui d'avant le
  Lot 2 — aucune régression sur les appelants existants.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from seamtech_search.accessoires import (
    CODE_GABARIT_ACCESSOIRE,
    ChampAccessoire,
    construire_extraction_accessoire,
)
from seamtech_search.fiches.extraction import (
    Ligne,
    Mot,
    PageAnalysee,
    analyser_pdf,
    normaliser_terme,
)
from seamtech_search.fiches.modeles import ChampExtrait, FicheExtraite
from seamtech_search.fiches.verification import (
    agreger_verifications,
    classifier_nature_document,
    mesures_nommees,
    verifier_document,
    verifier_mesure,
)

# ---------------------------------------------------------------------------
# Structures synthétiques
# ---------------------------------------------------------------------------

# Fiche « fourreau de GSE » : casse et accents incohérents, unité tantôt
# collée tantôt séparée, apostrophe absente sur une ligne et présente sur
# l'autre — exactement ce que porte le formulaire réel.
LIGNES_FOURREAU = [
    "Fiche de fourreau de gse",
    "Commande ST900",
    "Contrôle",
    "Longueur du fourreau 12.50 m",
    "Hauteur du fourreau 0.80m",
    "longueur point decoute 3 m",
    "Hauteur du point d'ecoute 1.5m",
    "Observations/Notes",
    "SANS LOGO",
]

# Fiche « Lazy Bag » : astérisques imprimés et DEUX cotes sur une même ligne.
LIGNES_LAZY_BAG = [
    "Fiche de Lazy Bag",
    "Commande 123456CS",
    "Bateau Voilier Fictif 385",
    'Mesure "E" 4.5m',
    "Longueur pour Facturation 4.20m",
    "Diametre de ralingue* 10 mm",
    "Longueur de la ralingue* 4.10m",
    "Longueur du lazy* 3.60m",
    "Hauteur du lazy* 1.10m",
    "Hauteur du capot* 0.35m",
    "Longueur du gousset de latte 3.22m - Latte jonc 12mm avec manchons",
]

# Plan : des cotes nues, sans unité ni libellé — rien à en déduire.
LIGNES_PLAN = ["TAUD DE GV", "3.42", "185", "464"]


def _ecrire_pdf(chemin: Path, lignes: list[str]) -> Path:
    """PDF texte natif synthétique — une ligne imprimée par ligne logique."""
    from reportlab.pdfgen import canvas

    toile = canvas.Canvas(str(chemin))
    y = 780
    for ligne in lignes:
        toile.drawString(60, y, ligne)
        y -= 24
    toile.save()
    return chemin


def _pages(lignes: list[str], tmp_path: Path, nom: str = "fiche.pdf") -> list[PageAnalysee]:
    return analyser_pdf(_ecrire_pdf(tmp_path / nom, lignes))


def _fiche(**remplacements: Any) -> FicheExtraite:
    champs = [
        ChampExtrait(
            champ="fiche.code",
            valeur_brute="LOT2-VERIF-1",
            valeur_normalisee="LOT2-VERIF-1",
            confiance=0.99,
            page=1,
        ),
        ChampExtrait(
            champ="accessoire.type",
            valeur_brute="Fourreau de GSE",
            valeur_normalisee="Fourreau de GSE",
            confiance=0.95,
            page=1,
        ),
    ]
    donnees: dict[str, Any] = {
        "code": "LOT2-VERIF-1",
        "gabarit_code": CODE_GABARIT_ACCESSOIRE,
        "gabarit_version": 2,
        "champs": champs,
    }
    donnees.update(remplacements)
    return FicheExtraite(**donnees)


@dataclass
class _MotFactice:
    """Mot minimal pour construire une page sans PDF."""

    texte: str
    x0: float
    x1: float
    haut: float = 100.0
    bas: float = 110.0
    page: int = 0


def _mot(fictif: _MotFactice) -> Mot:
    return Mot(
        texte=fictif.texte,
        normalise=normaliser_terme(fictif.texte),
        x0=fictif.x0,
        x1=fictif.x1,
        haut=fictif.haut,
        bas=fictif.bas,
        page=fictif.page,
    )


# ---------------------------------------------------------------------------
# 1. Nature du document : on classe avant de lire
# ---------------------------------------------------------------------------


def test_formulaire_reconnu_par_une_cote_nommee() -> None:
    assert classifier_nature_document("\n".join(LIGNES_FOURREAU)) == "formulaire"
    assert classifier_nature_document("\n".join(LIGNES_LAZY_BAG)) == "formulaire"


def test_plan_sans_unite_est_un_plan_et_non_un_formulaire() -> None:
    # Des nombres nus : deviner l'unité fabriquerait une cote.
    assert classifier_nature_document("\n".join(LIGNES_PLAN)) == "plan"


def test_document_sans_texte_est_illisible() -> None:
    assert classifier_nature_document("") == "illisible"
    assert classifier_nature_document("   \n  \n") == "illisible"


def test_texte_libre_reste_indetermine() -> None:
    # Du texte, mais aucune cote nommée : aucune prétention.
    assert classifier_nature_document("Bonjour, ceci est une note libre.") == "indetermine"


def test_pages_vides_sont_illisibles() -> None:
    rapport = verifier_document([])
    assert rapport.nature == "illisible"
    assert rapport.mesures == []
    assert not rapport.bloquant


# ---------------------------------------------------------------------------
# 2. Lecture des cotes nommées
# ---------------------------------------------------------------------------


def test_cotes_nommees_malgre_casse_accents_et_apostrophe() -> None:
    mesures = mesures_nommees("\n".join(LIGNES_FOURREAU))
    canoniques = [mesure.libelle_canonique for mesure in mesures]
    # « Longueur du fourreau », « Hauteur du fourreau », « longueur point
    # decoute » (sans apostrophe) et « Hauteur du point d'ecoute » (avec
    # apostrophe) doivent tous être lus : la normalisation les égalise.
    assert canoniques == [
        "longueur_fourreau",
        "hauteur_fourreau",
        "longueur_point_ecoute",
        "hauteur_point_ecoute",
    ]


def test_unite_collee_ou_separee_donne_la_meme_mesure() -> None:
    mesures = mesures_nommees("\n".join(LIGNES_FOURREAU))
    par_canonique = {mesure.libelle_canonique: mesure for mesure in mesures}
    # « 12.50 m » (séparé) et « 0.80m » (collé) : même lecture, en mètres.
    assert par_canonique["longueur_fourreau"].valeur_lue == "12.50m"
    assert par_canonique["longueur_fourreau"].valeur_m == pytest.approx(12.5)
    assert par_canonique["hauteur_fourreau"].valeur_lue == "0.80m"
    assert par_canonique["hauteur_fourreau"].valeur_m == pytest.approx(0.8)


def test_deux_cotes_sur_une_ligne_ne_produit_pas_de_cote_orpheline() -> None:
    mesures = mesures_nommees("\n".join(LIGNES_LAZY_BAG))
    gousset = [m for m in mesures if m.libelle_canonique == "longueur_gousset_latte"]
    # « … 3.22m - Latte jonc 12mm … » : seule la mesure DE TÊTE est retenue.
    # Prendre la seconde la ferait projeter en largeur une cote de latte.
    assert len(gousset) == 1
    assert gousset[0].valeur_lue == "3.22m"
    assert gousset[0].valeur_m == pytest.approx(3.22)


def test_asterisque_imprime_ne_bloque_pas_la_lecture() -> None:
    mesures = mesures_nommees("\n".join(LIGNES_LAZY_BAG))
    assert "longueur_lazy" in [m.libelle_canonique for m in mesures]
    assert "hauteur_capot" in [m.libelle_canonique for m in mesures]


def test_asterisque_et_unite_attendue_du_diametre() -> None:
    mesures = mesures_nommees("\n".join(LIGNES_LAZY_BAG))
    diametre = next(m for m in mesures if m.libelle_canonique == "diametre_ralingue")
    assert diametre.unite_lue == "mm"
    assert diametre.unite_attendue == "mm"
    assert diametre.valeur_m == pytest.approx(0.01)


def test_cote_sans_unite_n_est_jamais_lue() -> None:
    # Un nombre nu n'est pas une mesure : deviner l'unité est interdit.
    assert mesures_nommees("Longueur du fourreau 12.50") == []


def test_libelle_inconnu_ne_produit_rien() -> None:
    assert mesures_nommees("Longueur du mat 12.50m") == []


# ---------------------------------------------------------------------------
# 3. Double vérification : les trois issues
# ---------------------------------------------------------------------------


def test_relecture_concordante_promulgue_la_cote(tmp_path: Path) -> None:
    pages = _pages(LIGNES_FOURREAU, tmp_path)
    rapport = verifier_document(pages)
    assert rapport.nature == "formulaire"
    assert len(rapport.verifications) == 4
    # Deux chemins indépendants, même résultat : les quatre cotes sont promues.
    assert [m.libelle_canonique for m in rapport.verifiees] == [
        "longueur_fourreau",
        "hauteur_fourreau",
        "longueur_point_ecoute",
        "hauteur_point_ecoute",
    ]
    assert rapport.divergentes == []
    assert rapport.unites_inattendues == []
    assert not rapport.bloquant


def test_deux_lectures_differentes_font_divergence() -> None:
    mesure = mesures_nommees("Longueur du lazy 9.99m")[0]
    mots = [
        _mot(_MotFactice("Longueur", 10, 60)),
        _mot(_MotFactice("du", 62, 74)),
        _mot(_MotFactice("lazy", 76, 100)),
        _mot(_MotFactice("3.60m", 105, 140)),
        _mot(_MotFactice("Hauteur", 150, 200)),
        _mot(_MotFactice("du", 202, 214)),
        _mot(_MotFactice("lazy", 216, 240)),
        _mot(_MotFactice("1.10m", 245, 275)),
    ]
    verification = verifier_mesure(mesure, mots)
    assert verification.statut == "divergence"
    assert verification.seconde_lecture == "3.60m"
    assert "9.99m" in verification.motif and "3.60m" in verification.motif


def test_unite_lue_differente_de_celle_annoncee() -> None:
    mesure = mesures_nommees("Diametre de ralingue 3.60m")[0]
    mots = [
        _mot(_MotFactice("Diametre", 10, 60)),
        _mot(_MotFactice("de", 62, 74)),
        _mot(_MotFactice("ralingue", 76, 130)),
        _mot(_MotFactice("3.60m", 135, 175)),
    ]
    verification = verifier_mesure(mesure, mots)
    assert verification.statut == "unite_inattendue"
    assert verification.unite_conforme is False
    assert "mm" in verification.motif


def test_sans_seconde_lecture_rien_nest_promu() -> None:
    mesure = mesures_nommees("Longueur du lazy 3.60m")[0]
    # Aucun mot transmis : la relecture est impossible.
    assert verifier_mesure(mesure, []).statut == "non_verifiable"
    # Libellé absent de la page.
    assert verifier_mesure(mesure, [_mot(_MotFactice("Autre", 10, 40))]).statut == "non_verifiable"
    # Aucune mesure à droite du libellé.
    vide = [
        _mot(_MotFactice("Longueur", 10, 60)),
        _mot(_MotFactice("du", 62, 74)),
        _mot(_MotFactice("lazy", 76, 100)),
    ]
    assert verifier_mesure(mesure, vide).statut == "non_verifiable"


def test_non_verifiable_n_est_jamais_un_accord() -> None:
    """L'absence de preuve n'est pas une preuve d'exactitude."""
    mesure = mesures_nommees("Longueur du lazy 3.60m")[0]
    verification = verifier_mesure(mesure, [])
    assert verification.statut == "non_verifiable"
    assert {"accord": True, "divergence": False, "unite_inattendue": False}.get(verification.statut) is None


# ---------------------------------------------------------------------------
# 4. Projection accessoire : ce que l'approbateur voit
# ---------------------------------------------------------------------------


def test_sans_pages_le_comportement_est_inchange() -> None:
    """Aucune régression : pas de pages, pas de Lot 2."""
    extraction = construire_extraction_accessoire(_fiche())
    assert [c.nom for c in extraction.champs if c.nom.startswith("mesure.")] == []
    assert all(c.verifie is None for c in extraction.champs)
    assert [a.code for a in extraction.anomalies] == []


def test_cotes_projetees_sous_leur_propre_libelle(tmp_path: Path) -> None:
    pages = _pages(LIGNES_FOURREAU, tmp_path)
    extraction = construire_extraction_accessoire(_fiche(), pages=pages)
    noms = [c.nom for c in extraction.champs if c.nom.startswith("mesure.")]
    # Les quatre cotes du formulaire, chacune sous SON libellé — pas de
    # « largeur » inventée pour une hauteur de fourreau.
    assert noms == [
        "mesure.longueur_fourreau",
        "mesure.hauteur_fourreau",
        "mesure.longueur_point_ecoute",
        "mesure.hauteur_point_ecoute",
    ]
    assert all(c.verifie is True for c in extraction.champs if c.nom.startswith("mesure."))
    assert all(c.preuve_verification for c in extraction.champs if c.nom.startswith("mesure."))


def test_valeur_canonique_portee_par_la_cote(tmp_path: Path) -> None:
    pages = _pages(LIGNES_FOURREAU, tmp_path)
    extraction = construire_extraction_accessoire(_fiche(), pages=pages)
    longueur = next(c for c in extraction.champs if c.nom == "mesure.longueur_fourreau")
    assert longueur.valeur_canonique == pytest.approx(12.5)
    assert longueur.unite_canonique == "m"
    assert longueur.unite_brute == "m"
    assert longueur.methode == "double_verification"


def test_divergence_devient_une_anomalie_forte_et_bloque() -> None:
    """Deux lectures qui ne concordent pas : anomalie FORTE, jamais résolue.

    La page ci-dessous simule une reconstruction de lignes fautive (deux
    colonnes inversées) : la ligne reconstruite attribue 3.60 au libellé
    « Longueur du lazy », alors que la mesure la plus proche à droite de CE
    libellé vaut 9.99. Le désaccord est signalé, pas tranché.
    """
    longueur = [
        _mot(_MotFactice("Longueur", 10, 60)),
        _mot(_MotFactice("du", 62, 74)),
        _mot(_MotFactice("lazy", 76, 100)),
    ]
    hauteur = [
        _mot(_MotFactice("Hauteur", 150, 200)),
        _mot(_MotFactice("du", 202, 214)),
        _mot(_MotFactice("lazy", 216, 240)),
    ]
    cote_gauche = _mot(_MotFactice("9.99m", 105, 140))
    cote_droite = _mot(_MotFactice("3.60m", 245, 280))
    # Regroupement fautif : chaque libellé reçoit la cote de l'AUTRE.
    pages = [
        PageAnalysee(
            numero=0,
            lignes=[
                Ligne(mots=longueur + [cote_droite], page=0),
                Ligne(mots=hauteur + [cote_gauche], page=0),
            ],
        )
    ]
    extraction = construire_extraction_accessoire(_fiche(), pages=pages)
    codes = [a.code for a in extraction.anomalies]
    assert "divergence_relecture" in codes
    anomalie = next(a for a in extraction.anomalies if a.code == "divergence_relecture")
    assert anomalie.gravite == "forte"
    # Aucune des deux cotes n'est promue.
    cotes = [c for c in extraction.champs if c.nom.startswith("mesure.")]
    assert len(cotes) == 2
    assert all(c.verifie is False for c in cotes)
    assert all(c.preuve_verification for c in cotes)
    # Un désaccord est bloquant : le dossier repart à vérifier.
    assert extraction.statut_plausibilite == "a_verifier"


def test_cote_sur_une_autre_bande_est_non_verifiable() -> None:
    """La cote est lue par libellé mais pas retrouvée à droite : non promue.

    Cas réel : une valeur renvoyée à la ligne, que le moteur regroupe pourtant
    dans la même ligne logique. La relecture géométrique ne la retrouve pas
    sur la bande du libellé — elle ne prétend donc pas l'avoir confirmée.
    """
    libelle = [
        _mot(_MotFactice("Longueur", 10, 60)),
        _mot(_MotFactice("du", 62, 74)),
        _mot(_MotFactice("fourreau", 76, 130)),
    ]
    # Même ligne logique, mais bande verticale distincte (valeur renvoyée).
    cote = _mot(_MotFactice("12.50m", 135, 175, haut=130.0, bas=140.0))
    pages = [PageAnalysee(numero=0, lignes=[Ligne(mots=libelle + [cote], page=0)])]
    rapport = verifier_document(pages)
    assert len(rapport.mesures) == 1
    assert rapport.verifications[0].statut == "non_verifiable"
    assert rapport.verifiees == []
    assert not rapport.bloquant

    extraction = construire_extraction_accessoire(_fiche(), pages=pages)
    cotes = [c for c in extraction.champs if c.nom.startswith("mesure.")]
    assert len(cotes) == 1
    # Ni promue ni discréditée : l'absence de preuve n'est pas une preuve.
    assert cotes[0].verifie is None
    assert cotes[0].valeur_canonique == pytest.approx(12.5)


def test_un_plan_ajoute_une_anomalie_sans_inventer_de_cote(tmp_path: Path) -> None:
    pages = _pages(LIGNES_PLAN, tmp_path, nom="plan.pdf")
    extraction = construire_extraction_accessoire(_fiche(), pages=pages)
    codes = [a.code for a in extraction.anomalies]
    assert "document_plan_sans_libelle" in codes
    # Aucune cote n'est déduite de nombres nus.
    assert [c.nom for c in extraction.champs if c.nom.startswith("mesure.")] == []


def test_page_sans_texte_est_signalee() -> None:
    """Un document sans couche texte ne peut pas être vérifié : on le dit."""
    pages = [PageAnalysee(numero=0, lignes=[Ligne(mots=[], page=0)])]
    extraction = construire_extraction_accessoire(_fiche(), pages=pages)
    assert "document_sans_texte" in [a.code for a in extraction.anomalies]
    assert [c.nom for c in extraction.champs if c.nom.startswith("mesure.")] == []


def test_verification_est_idempotente(tmp_path: Path) -> None:
    """Dossier par dossier : relire deux fois donne strictement la même chose."""
    pages = _pages(LIGNES_LAZY_BAG, tmp_path, nom="lazy.pdf")
    premier = verifier_document(pages)
    second = verifier_document(pages)
    assert [v.statut for v in premier.verifications] == [v.statut for v in second.verifications]
    assert [m.valeur_lue for m in premier.mesures] == [m.valeur_lue for m in second.mesures]


# ---------------------------------------------------------------------------
# 5. Échelle : le rapport d'un lot de dossiers
# ---------------------------------------------------------------------------


def test_agregat_compte_un_lot_de_dossiers(tmp_path: Path) -> None:
    rapports = [
        verifier_document(_pages(LIGNES_FOURREAU, tmp_path, "a.pdf")),
        verifier_document(_pages(LIGNES_LAZY_BAG, tmp_path, "b.pdf")),
        verifier_document(_pages(LIGNES_PLAN, tmp_path, "c.pdf")),
        verifier_document([]),
    ]
    compte = agreger_verifications(rapports)
    assert compte["documents"] == 4
    assert compte["formulaire"] == 2
    assert compte["plan"] == 1
    assert compte["illisible"] == 1
    assert compte["mesures"] == 4 + 8
    assert compte["accord"] == 4 + 8
    assert compte["dossiers_bloquants"] == 0


def test_agregat_sur_aucun_dossier() -> None:
    compte = agreger_verifications([])
    assert compte["documents"] == 0
    assert compte["mesures"] == 0


def test_champ_accessoire_expose_la_preuve() -> None:
    """Le modèle porte la trace de la relecture (contrat de l'écran)."""
    champ = ChampAccessoire(
        nom="mesure.longueur_fourreau",
        verifie=True,
        preuve_verification="deux lectures concordantes : « 12.50m »",
    )
    assert champ.verifie is True
    assert "12.50m" in (champ.preuve_verification or "")
    neutre = ChampAccessoire(nom="mesure.hauteur_capot")
    assert neutre.verifie is None
    assert neutre.preuve_verification is None
