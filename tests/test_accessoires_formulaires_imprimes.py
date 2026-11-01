"""Formulaires d'accessoires IMPRIMÉS — calibration sur documents réels.

Mesure du 2026-10-29 (Dropbox de l'atelier, ouvert et lu par pdfplumber ; aucun
document client n'est versionné ici) : **trois** formulaires imprimés
coexistent dans les dossiers d'accessoires, tous préfixés « Fiche de … » :

=====================  ================  ====================================
formulaire             format mesuré     où le code est imprimé
=====================  ================  ====================================
Fiche de Lazy Bag      792×612 paysage   ligne NUE sous le titre (« ST64 »);
                                          « Commande » est imprimé SANS valeur
Fiche de fourreau      792×612 paysage   après « Commande » (« Commande ST96 »)
  de gse
Fiche de Sac de Pont   595×842 portrait  après « Commande » (« Commande ST 074b »)
=====================  ================  ====================================

Défauts PROUVÉS avant correctif, avec ``extraire_fiche`` sur ces documents :

* la fiche Lazy Bag sortait en ``FICHE_GV_FULLBATTEN_V1`` — la ligne « Type des
  coulisseaux » porte l'ancre ``coulisseaux`` de la grand-voile lattée, et à
  score égal la GV passait avant les accessoires. Résultat : titre, code,
  client et dimensions TOUS vides ;
* la fiche « fourreau de gse » n'était reconnue par AUCUN gabarit ;
* la fiche « Sac de Pont » n'était lue que par la v1 (code seul : ni type, ni
  bateau, ni couleur) ;
* sur la fiche Lazy Bag, la ligne « Longueur du gousset de latte 3.22m - Latte
  jonc 12mm … » livrait DEUX mesures : la projection rangeait la seconde en
  ``largeur_m`` — une cote de largeur inventée (3,22 m puis 12 mm).

Les PDF ci-dessous sont SYNTHÉTIQUES : ils reproduisent la mise en page et les
libellés génériques mesurés, avec des valeurs inventées.
"""

from __future__ import annotations

import pytest
from reportlab.pdfgen import canvas

from seamtech_search.accessoires import construire_extraction_accessoire
from seamtech_search.fiches.extraction import ExtractionImpossible, extraire_fiche
from seamtech_search.fiches.gabarits import (
    CODE_GABARIT_ACCESSOIRE,
    CODE_GABARIT_ACCESSOIRE_IMPRIME,
    GABARIT_ACCESSOIRE_IMPRIME,
    GABARIT_ACCESSOIRE_V2,
    GABARIT_GV_FULLBATTEN,
    GABARITS_EMBARQUES,
    GabaritInconnu,
    detecter_gabarit,
)
from seamtech_search.lexique import normaliser_terme

PAYSAGE = (792.0, 612.0)  # format des deux fiches Lazy Bag / fourreau mesurées
PORTRAIT = (595.0, 842.0)  # format de la fiche Sac de Pont mesurée


def _ecrire(chemin, lignes: list[str], format_page=PAYSAGE):
    largeur, hauteur = format_page
    pdf = canvas.Canvas(str(chemin), pagesize=(largeur, hauteur))
    pdf.setFont("Helvetica", 9)
    y = hauteur - 60
    for ligne in lignes:
        pdf.drawString(40, y, ligne)
        y -= 22
    pdf.save()
    return chemin


# --- Réplique mesurée n° 1 : « Fiche de Lazy Bag » (code nu sous le titre) ----
LIGNES_LAZY_BAG = [
    "Fiche de Lazy Bag",
    "ST64",
    "Commande",
    "Client (réf.) SEAMTECH PRO",
    "Contrôle",
    "Longueur du gousset de latte 3.22m - Latte jonc 12mm avec manchons et embouts",
    "Diametre de ralingue* 8 mm",
    "Longueur de la ralingue* 1.77 m",
    "Type des coulisseaux A031 (X3)",
    "Longueur du lazy* 3.32 m",
    "Hauteur du lazy* 0.29m",
    "Hauteur du capot* 0.84m",
    "Observations/Notes SANS LOGO",
]

# --- Réplique mesurée n° 2 : « Fiche de fourreau de gse » (Commande ST96) -----
LIGNES_FOURREAU = [
    "Fiche de fourreau de gse",
    "Commande ST96",
    "Client (réf.) SEAMTECH PRO",
    "Contrôle",
    "Longueur du fourreau 10.50 m",
    "Hauteur du fourreau 0.62 m",
    "longueur point decoute 0.85m",
    "Hauteur du point d'ecoute 0.42 m",
    "Observations/Notes",
]

# --- Réplique mesurée n° 3 : « Fiche de Sac de Pont » (A4 portrait) ----------
LIGNES_SAC_DE_PONT = [
    "Fiche de Sac de Pont",
    "04/10/2024",
    "Bateau First 325",
    "Commande ST 074b",
    "Client (réf.) Voilerie VBO (ST74 - SAOODXJBQ) Contrôle",
    "Logo Pas de logo",
    "Couleur Captain Navy 5057",
    "Longueur du sac* 3,19 m",
    "Hauteur avant* 1,14 m",
    "Observations/Notes",
]

# --- Contre-épreuve : formulaire de PRISE DE COTES (famille v2, doit rester) --
LIGNES_PRISE_DE_COTES = [
    "Réservé pour le CLIENT",
    "Type de bateau : Voilier Fictif 42",
    "Type de LazyBag",
    "Nombre d'ancrages",
    "Type de grand-voile",
    "FullBatten",
    "Couleurs Sunbrella",
    "Les housses de bôme sont fabriquées en tissu acrylique teinté masse",
]

# --- Contre-épreuve : fiche de VOILE (ne doit jamais devenir accessoire) -----
LIGNES_VOILE = [
    'Fiche de fabrication "Grand-voile"',
    "Grand-voile classique semi-lattée, gamme Ocean, pour Bateau Demo (43') "
    "de Client Demo (Commande #999 DEMO1234) ZZ 999",
    "Découpe et fabrication Atelier démo",
    "Guindant à coulisseaux/chariots:",
    "Cotes (tissu):",
]


@pytest.fixture(scope="module")
def fiche_lazy_bag(tmp_path_factory):
    return extraire_fiche(
        _ecrire(tmp_path_factory.mktemp("acc") / "lazy-bag.pdf", LIGNES_LAZY_BAG),
        gabarits=list(GABARITS_EMBARQUES),
    )


@pytest.fixture(scope="module")
def fiche_fourreau(tmp_path_factory):
    return extraire_fiche(
        _ecrire(tmp_path_factory.mktemp("acc") / "fourreau.pdf", LIGNES_FOURREAU),
        gabarits=list(GABARITS_EMBARQUES),
    )


@pytest.fixture(scope="module")
def fiche_sac_de_pont(tmp_path_factory):
    return extraire_fiche(
        _ecrire(
            tmp_path_factory.mktemp("acc") / "sac-de-pont.pdf",
            LIGNES_SAC_DE_PONT,
            PORTRAIT,
        ),
        gabarits=list(GABARITS_EMBARQUES),
    )


# ---------------------------------------------------------------------------
# 1. Détection : le formulaire imprimé gagne, y compris contre la GV lattée
# ---------------------------------------------------------------------------


def test_la_famille_imprimee_est_au_registre() -> None:
    assert CODE_GABARIT_ACCESSOIRE_IMPRIME == "FICHE_ACCESSOIRE_IMPRIME_V1"
    assert GABARIT_ACCESSOIRE_IMPRIME in GABARITS_EMBARQUES
    # La prise de cotes (v2) reste active, avec son propre code.
    assert GABARIT_ACCESSOIRE_V2 in GABARITS_EMBARQUES
    assert CODE_GABARIT_ACCESSOIRE != CODE_GABARIT_ACCESSOIRE_IMPRIME


@pytest.mark.parametrize(
    "lignes",
    [LIGNES_LAZY_BAG, LIGNES_FOURREAU, LIGNES_SAC_DE_PONT],
    ids=["lazy-bag", "fourreau-de-gse", "sac-de-pont"],
)
def test_formulaire_imprime_detecte(lignes: list[str]) -> None:
    texte = normaliser_terme("\n".join(lignes))
    assert GABARIT_ACCESSOIRE_IMPRIME.score_detection(texte) >= 1
    gagne = detecter_gabarit(texte, list(GABARITS_EMBARQUES))
    assert gagne.code == CODE_GABARIT_ACCESSOIRE_IMPRIME


def test_fiche_lazy_bag_n_est_plus_volee_par_la_grand_voile() -> None:
    """Cause mesurée : « Type des coulisseaux » porte l'ancre `coulisseaux`."""
    texte = normaliser_terme("\n".join(LIGNES_LAZY_BAG))
    assert GABARIT_GV_FULLBATTEN.score_detection(texte) >= 1
    assert GABARIT_ACCESSOIRE_IMPRIME.score_detection(texte) > GABARIT_GV_FULLBATTEN.score_detection(texte)


def test_fiche_voile_ne_devient_jamais_accessoire() -> None:
    texte = normaliser_terme("\n".join(LIGNES_VOILE))
    assert GABARIT_ACCESSOIRE_IMPRIME.score_detection(texte) == 0
    assert detecter_gabarit(texte, list(GABARITS_EMBARQUES)).code != CODE_GABARIT_ACCESSOIRE_IMPRIME


def test_prise_de_cotes_reste_sur_sa_famille() -> None:
    """La famille imprimée ne doit pas voler le formulaire de prise de cotes."""
    texte = normaliser_terme("\n".join(LIGNES_PRISE_DE_COTES))
    assert GABARIT_ACCESSOIRE_IMPRIME.score_detection(texte) == 0
    assert detecter_gabarit(texte, list(GABARITS_EMBARQUES)).code == CODE_GABARIT_ACCESSOIRE


# ---------------------------------------------------------------------------
# 2. Lecture des champs mesurés sur les documents réels
# ---------------------------------------------------------------------------


def test_lazy_bag_champs_reels(fiche_lazy_bag) -> None:
    fiche = fiche_lazy_bag
    assert fiche.gabarit_code == CODE_GABARIT_ACCESSOIRE_IMPRIME
    assert fiche.titre == "Lazy Bag"  # le titre EST le type d'accessoire
    assert fiche.code == "ST64"  # ligne nue sous le titre
    assert fiche.client_nom == "SEAMTECH PRO"  # « Client (réf.) … »
    assert fiche.logo == "sans"  # « Observations/Notes SANS LOGO »
    traces = {champ.champ: champ for champ in fiche.tous_les_champs()}
    assert traces["accessoire.type"].valeur_normalisee == "Lazy Bag"


def test_lazy_bag_ne_fabrique_aucune_largeur(fiche_lazy_bag) -> None:
    """UNE seule mesure, et la BONNE : la longueur du Lazy Bag (3,32 m).

    Défaut mesuré sur `/ACCESSOIRES 2024/LAZY BAG 2P ST64/LB 2 parties.pdf` :
    l'astérisque de « Longueur du lazy* 3.32 m » empêchait l'ancre de trouver
    la ligne ; la lecture retombait sur « Longueur du gousset de latte 3.22m »
    et annonçait 3,22 m — la longueur du GOUSSET, pas celle du Lazy Bag.
    """
    extraction = construire_extraction_accessoire(fiche_lazy_bag)
    assert extraction.type_accessoire == "Lazy Bag"
    assert extraction.longueur_m == pytest.approx(3.32)  # « Longueur du lazy* »
    assert extraction.largeur_m is None  # 12 mm n'est PAS une largeur
    assert extraction.hauteur_m is None
    assert extraction.dimensions_texte == "3.32 m"


def test_asterisque_imprime_ne_cache_plus_la_ligne(fiche_lazy_bag, tmp_path) -> None:
    """L'astérisque des formulaires n'est pas un caractère du libellé.

    Deux lectures comparées sur la MÊME mise en page : avec la ligne
    « Longueur du lazy* 3.32 m » la longueur vaut 3,32 m ; sans elle, la seule
    dimension longueur restante est celle du gousset (3,22 m). Si l'astérisque
    cachait la ligne, les deux lectures donneraient 3,22 m.
    """
    extraction = construire_extraction_accessoire(fiche_lazy_bag)
    assert extraction.longueur_m == pytest.approx(3.32)

    sans_lazy = [
        ligne for ligne in LIGNES_LAZY_BAG if not ligne.startswith("Longueur du lazy")
    ]
    fiche = extraire_fiche(
        _ecrire(tmp_path / "lazy-sans-ligne-lazy.pdf", sans_lazy),
        gabarits=list(GABARITS_EMBARQUES),
    )
    assert construire_extraction_accessoire(fiche).longueur_m == pytest.approx(3.22)


def test_fourreau_champs_reels(fiche_fourreau) -> None:
    fiche = fiche_fourreau
    assert fiche.gabarit_code == CODE_GABARIT_ACCESSOIRE_IMPRIME
    assert fiche.titre == "Fourreau de gse"
    assert fiche.code == "ST96"  # « Commande ST96 »
    assert fiche.client_nom == "SEAMTECH PRO"
    extraction = construire_extraction_accessoire(fiche)
    assert extraction.type_accessoire == "Fourreau de gse"
    assert extraction.longueur_m == pytest.approx(10.5)


def test_sac_de_pont_champs_reels(fiche_sac_de_pont) -> None:
    fiche = fiche_sac_de_pont
    assert fiche.gabarit_code == CODE_GABARIT_ACCESSOIRE_IMPRIME
    assert fiche.titre == "Sac de Pont"
    assert fiche.code == "ST 074B"
    # « Contrôle » est un en-tête imprimé sur la même ligne : il ne finit pas
    # dans le nom du client, et la référence d'affaire reste séparée.
    assert fiche.client_nom == "Voilerie VBO"
    assert fiche.client_chantier == "ST74 - SAOODXJBQ"
    extraction = construire_extraction_accessoire(fiche)
    assert extraction.type_accessoire == "Sac de Pont"
    assert extraction.modele_bateau_brut == "First 325"
    assert extraction.couleur == "Captain Navy 5057"
    assert extraction.longueur_m == pytest.approx(3.19)  # « 3,19 m »


def test_sac_de_pont_sans_couleur_ni_bateau_ne_devine_rien(tmp_path) -> None:
    """Variante sans les lignes Bateau/Couleur : rien n'est inventé (RG6)."""
    lignes = [ligne for ligne in LIGNES_SAC_DE_PONT if not ligne.startswith(("Bateau", "Couleur"))]
    fiche = extraire_fiche(
        _ecrire(tmp_path / "sac-minimal.pdf", lignes, PORTRAIT),
        gabarits=list(GABARITS_EMBARQUES),
    )
    assert fiche.code == "ST 074B"
    assert fiche.bateau_nom is None
    extraction = construire_extraction_accessoire(fiche)
    assert extraction.modele_bateau_brut is None
    assert extraction.couleur is None


# ---------------------------------------------------------------------------
# 3. Documents sans fiche : plan du traceur, dossier réel
# ---------------------------------------------------------------------------


def test_plan_sans_texte_n_est_pas_une_fiche(tmp_path) -> None:
    """Mesuré : `TAUD DE GV.pdf`, `ST298.pdf`, `ST40 FINI.pdf` n'ont AUCUNE
    couche texte exploitable (0 à 13 caractères extraits)."""
    pdf = canvas.Canvas(str(tmp_path / "plan.pdf"), pagesize=PAYSAGE)
    pdf.setFillGray(0.0)
    pdf.rect(100, 100, 300, 300, stroke=1, fill=0)  # dessin, pas de texte
    pdf.save()
    with pytest.raises(ExtractionImpossible):
        extraire_fiche(tmp_path / "plan.pdf", gabarits=list(GABARITS_EMBARQUES))


def test_plan_avec_cotes_nues_n_est_pas_reconnu(tmp_path) -> None:
    """Cotes nues d'un plan (« 20 68 03 317 ») : aucun gabarit, aucun champ."""
    chemin = _ecrire(tmp_path / "plan-cote.pdf", ["20", "68", "03", "317"])
    with pytest.raises(GabaritInconnu):
        extraire_fiche(chemin, gabarits=list(GABARITS_EMBARQUES))


def test_dossier_reel_type_scanne_la_fiche_et_liste_les_pieces(tmp_path) -> None:
    """Forme réelle d'un dossier : 1 fiche PDF + PLX/DXF/XIN + plan PDF.

    La fiche doit être choisie (et non le plan), et les fichiers techniques
    doivent être conservés comme pièces — jamais jeter un fichier du dossier.
    """
    from seamtech_search.fiches.depot import scanner_dossier

    dossier = tmp_path / "ST64"
    dossier.mkdir()
    _ecrire(dossier / "LB 2 parties.pdf", LIGNES_LAZY_BAG)
    _ecrire(dossier / "DESSIN ST64.dxf", ["0", "SECTION"])
    (dossier / "CAPTAIN NAVY ST64.PLX").write_bytes(b"\x00PLX")
    (dossier / "DESSIN ST641.xin").write_text("<xin/>", encoding="utf-8")
    plan = canvas.Canvas(str(dossier / "plan.pdf"), pagesize=PAYSAGE)
    plan.rect(50, 50, 200, 200, stroke=1, fill=0)
    plan.save()

    scan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert scan.pdf_fiche is not None and scan.pdf_fiche.name == "LB 2 parties.pdf"
    assert scan.fiche_extraite is not None
    assert scan.fiche_extraite.code == "ST64"
    assert scan.fiche_extraite.titre == "Lazy Bag"
    noms_pieces = {piece.chemin.name for piece in scan.pieces}
    assert noms_pieces == {"CAPTAIN NAVY ST64.PLX", "DESSIN ST64.dxf", "DESSIN ST641.xin", "plan.pdf"}
