"""Moteur d'extraction piloté par gabarit (Lot B — plan v3.0 §10, §13).

Chaîne de lecture d'un PDF de fiche technique :
1. analyse pdfplumber (mots, lignes reconstruites, tableaux réglés) ;
2. détection du gabarit par ancres normalisées (:mod:`.gabarits`) ;
3. exécution des règles du gabarit : chaque champ lu produit un
   :class:`~.modeles.ChampExtrait` (valeur brute, valeur normalisée, méthode,
   confiance, page, **zone** dans le PDF) ;
4. filet de sécurité RG6 : les paires « libellé : valeur » non consommées
   dont le libellé appartient au vocabulaire connu sont conservées en
   mesures libres — rien n'est jeté silencieusement ;
5. contrôles RG16 (:mod:`.anomalies`), indépendants de la confiance.

L'écriture en base (transactionnelle, statut ``a_valider``) est dans
:mod:`.persistance` ; ce module ne touche jamais à la base.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from seamtech_search.fiches import normalisation as norm
from seamtech_search.fiches.anomalies import evaluer_anomalies
from seamtech_search.fiches.gabarits import GabaritDef, GabaritInconnu, detecter_gabarit
from seamtech_search.fiches.modeles import (
    ChampExtrait,
    Cotes,
    FicheExtraite,
    Finition,
    Galon,
    Jonction,
    Materiau,
    OptionFiche,
    Renfort,
    Zone,
)
from seamtech_search.lexique import normaliser_terme

LOGGER = logging.getLogger("seamtech_search.fiches.extraction")

TOLERANCE_LIGNE_PT = 2.5  # écart vertical maximal entre deux mots d'une même ligne
GAP_COLONNE_PT = 14.0  # écart horizontal marquant un changement de colonne (bordure de valeur)

# Familles de type de voile connues (valeur → (libellé canonique, famille)).
TYPES_VOILE: tuple[tuple[str, str, str], ...] = (
    ("spi asymétrique", "Spi Asymétrique", "portant"),
    ("spi symétrique", "Spi Symétrique", "portant"),
    ("spi", "Spi", "portant"),
    ("gennaker", "Gennaker", "portant"),
    ("génois", "Génois", "interface"),
    ("genoa", "Génois", "interface"),
    ("grand-voile", "Grand-voile", "interface"),
    ("grand voile", "Grand-voile", "interface"),
    ("gv", "Grand-voile", "interface"),
    ("trinquette", "Trinquette", "interface"),
    ("solent", "Solent", "interface"),
    ("code 0", "Code 0", "portant"),
    ("staysail", "Staysail", "portant"),
)


class ExtractionImpossible(RuntimeError):
    """Le PDF ne peut pas être lu (fichier absent, illisible, vide)."""


# ---------------------------------------------------------------------------
# Analyse du PDF : mots, lignes, tableaux
# ---------------------------------------------------------------------------


@dataclass
class Mot:
    texte: str
    normalise: str
    x0: float
    x1: float
    haut: float  # « top » pdfplumber
    bas: float  # « bottom » pdfplumber
    page: int


@dataclass
class Ligne:
    mots: list[Mot] = field(default_factory=list)
    page: int = 0

    @property
    def normalise(self) -> str:
        return " ".join(mot.normalise for mot in self.mots)


@dataclass
class PageAnalysee:
    numero: int  # index 0-based
    lignes: list[Ligne] = field(default_factory=list)
    tableaux: list[list[list[str | None]]] = field(default_factory=list)


def analyser_pdf(chemin: Path) -> list[PageAnalysee]:
    """Lit le PDF et reconstruit lignes + tableaux par page."""
    if not chemin.is_file():
        raise ExtractionImpossible(f"Fichier introuvable : {chemin}")
    try:
        with pdfplumber.open(str(chemin)) as pdf:
            pages: list[PageAnalysee] = []
            for numero, page in enumerate(pdf.pages):
                pages.append(_analyser_page(page, numero))
    except ExtractionImpossible:
        raise
    except Exception as erreur:  # pdfplumber lève des erreurs variées selon le PDF
        raise ExtractionImpossible(f"PDF illisible ({chemin}) : {erreur}") from erreur
    if not any(page.lignes or page.tableaux for page in pages):
        raise ExtractionImpossible(f"PDF sans texte lisible : {chemin}")
    return pages


def _analyser_page(page: object, numero: int) -> PageAnalysee:
    analysee = PageAnalysee(numero=numero)
    mots_bruts = sorted(page.extract_words(), key=lambda mot: (mot["top"], mot["x0"]))
    courante = Ligne(page=numero)
    for brut in mots_bruts:
        mot = Mot(
            texte=brut["text"],
            normalise=normaliser_terme(brut["text"]),
            x0=brut["x0"],
            x1=brut["x1"],
            haut=brut["top"],
            bas=brut["bottom"],
            page=numero,
        )
        if courante.mots and mot.haut - courante.mots[-1].haut > TOLERANCE_LIGNE_PT:
            analysee.lignes.append(courante)
            courante = Ligne(page=numero)
        courante.mots.append(mot)
    if courante.mots:
        analysee.lignes.append(courante)
    # Les mots d'une ligne se lisent de gauche à droite : les fontes de la
    # page donnent des « top » légèrement différents sur une même ligne
    # visuelle (le code 7792-SO en gras, les exposants…), donc l'ordre brut
    # (top, x0) de pdfplumber n'est pas l'ordre de lecture — on re-trie.
    for ligne in analysee.lignes:
        ligne.mots.sort(key=lambda mot: mot.x0)
    analysee.tableaux = page.extract_tables() or []
    return analysee


def texte_normalise(pages: list[PageAnalysee]) -> str:
    """Texte complet normalisé (détection du gabarit)."""
    morceaux = []
    for page in pages:
        for ligne in page.lignes:
            morceaux.append(ligne.normalise)
        for tableau in page.tableaux:
            for rangee in tableau:
                morceaux.append(" ".join(normaliser_terme(cellule) for cellule in rangee if cellule))
    return "\n".join(morceaux)


# ---------------------------------------------------------------------------
# Localisation d'une valeur dans une ligne / un tableau
# ---------------------------------------------------------------------------


def _indice_ancre(ligne: Ligne, ancre_norm: str) -> int | None:
    """Premier mot de la ligne où commence la séquence d'ancres (ou None)."""
    jetons = ancre_norm.split()
    mots_norm = [mot.normalise for mot in ligne.mots]
    for depart in range(len(mots_norm) - len(jetons) + 1):
        if mots_norm[depart : depart + len(jetons)] == jetons:
            return depart
    return None


def _mots_valeur(
    ligne: Ligne, debut_ancre: int, ancres_stop: list[str], nb_mots_ancre: int = 1
) -> tuple[list[Mot], bool]:
    """Mots de la valeur : après le « : » qui suit l'ancre, jusqu'au prochain
    libellé (ancres_stop) ou à une rupture de colonne marquée.

    ``nb_mots_ancre`` donne la longueur de l'ancre en mots : la valeur
    commence après TOUS les mots de l'ancre (une ancre « dessiné par » ne
    doit pas laisser « par » dans la valeur).

    Retourne (mots, borne_naturelle) : la borne est « naturelle » quand la
    lecture s'est arrêtée en fin de ligne ou sur un changement de colonne
    (valeur intégralement délimitée) ; elle est « tronquée » quand un libellé
    stop a coupé la valeur (ambiguïté résiduelle → confiance réduite)."""
    mots = ligne.mots
    fin_ancre = debut_ancre + nb_mots_ancre
    # saute l'ancre puis cherche les deux-points qui la suivent
    deux_points = None
    for position in range(fin_ancre, len(mots)):
        if mots[position].texte == ":":
            deux_points = position
            break
        if mots[position].texte not in ("/", "-") and position - fin_ancre > 1:
            break
    if deux_points is None:
        # pas de « label : valeur » : la valeur est le reste de la ligne
        # (cas du titre « Fiche de fabrication "Voile de portant" »)
        debut = fin_ancre
        for curseur in range(debut, len(mots)):
            if curseur > debut and mots[curseur].x0 - mots[curseur - 1].x1 > GAP_COLONNE_PT:
                return mots[debut:curseur], True
            reste = " ".join(m.normalise for m in mots[curseur : curseur + 3])
            if any(reste.startswith(normaliser_terme(stop)) for stop in ancres_stop):
                return mots[debut:curseur], False
        return mots[debut:], True
    fin = len(mots)
    borne_naturelle = True
    for curseur in range(deux_points + 1, len(mots)):
        mot = mots[curseur]
        if curseur > deux_points + 1 and mot.x0 - mots[curseur - 1].x1 > GAP_COLONNE_PT:
            fin = curseur  # changement de colonne : la valeur est finie
            break
        reste = " ".join(m.normalise for m in mots[curseur : curseur + 3])
        if any(reste.startswith(normaliser_terme(stop)) for stop in ancres_stop):
            fin = curseur
            borne_naturelle = False  # troncature par libellé : ambiguïté résiduelle
            break
    return mots[deux_points + 1 : fin], borne_naturelle


def _zone_des_mots(mots: list[Mot]) -> Zone | None:
    if not mots:
        return None
    return Zone(
        x0=min(mot.x0 for mot in mots),
        x1=max(mot.x1 for mot in mots),
        y0=min(mot.haut for mot in mots),
        y1=max(mot.bas for mot in mots),
        page=mots[0].page,
    )


def _trouver_ligne(pages: list[PageAnalysee], ancre_norm: str) -> tuple[PageAnalysee, Ligne, int] | None:
    """Première ligne (page, ligne, position d'ancre) contenant l'ancre."""
    for page in pages:
        for ligne in page.lignes:
            indice = _indice_ancre(ligne, ancre_norm)
            if indice is not None:
                return page, ligne, indice
    return None


def _valeur_de_tableau(pages: list[PageAnalysee], ancres: list[str]) -> tuple[str, list[Mot]] | None:
    """Valeur lue dans un tableau réglé : ligne dont la 1re cellule porte
    l'ancre, valeur = première cellule suivante non vide."""
    ancres_norm = [normaliser_terme(ancre) for ancre in ancres]
    for page in pages:
        for tableau in page.tableaux:
            for rangee in tableau:
                cellules = [cellule for cellule in rangee]
                if not cellules or cellules[0] is None:
                    continue
                etiquette = normaliser_terme(cellules[0])
                for ancre in ancres_norm:
                    if etiquette == ancre or etiquette.startswith(ancre):
                        for valeur in cellules[1:]:
                            if valeur and valeur.strip():
                                brut = valeur.strip()
                                return brut, _localiser_mots(page, brut)
    return None


def _localiser_mots(page: PageAnalysee, valeur: str) -> list[Mot]:
    """Retrouve les mots d'une valeur (tableau) pour en produire la zone."""
    jetons = [normaliser_terme(jeton) for jeton in valeur.split()]
    for ligne in page.lignes:
        normes = [mot.normalise for mot in ligne.mots]
        for depart in range(len(normes) - len(jetons) + 1):
            if normes[depart : depart + len(jetons)] == jetons:
                return ligne.mots[depart : depart + len(jetons)]
    return []


# ---------------------------------------------------------------------------
# Conversion d'une valeur brute selon le type déclaré par la règle
# ---------------------------------------------------------------------------

def compter_par_palier(fiche: FicheExtraite) -> dict[str, int]:
    """Comptes PAR PALIER de l'échelle ORDINALE de confiance (docs/CONTROLES_RG16.md).

    Le tableau de bord qualité (lot E) affichera ces comptes — JAMAIS une
    « confiance moyenne » : moyenner des paliers de décision n'a aucun sens et
    produirait un chiffre faux pour le commanditaire.
    """
    comptes = {"certain": 0, "lu": 0, "decompose": 0, "partiel": 0}
    for champ in fiche.tous_les_champs():
        if champ.valeur_normalisee is None:
            continue
        if champ.confiance >= CONFIANCE_CERTAIN:
            comptes["certain"] += 1
        elif champ.confiance >= CONFIANCE_LUE:
            comptes["lu"] += 1
        elif champ.confiance >= 0.85:
            comptes["decompose"] += 1
        else:
            comptes["partiel"] += 1
    return comptes


def _consommation_totale(type_declare: str, brut: str) -> bool:
    """Vrai quand la chaîne brute EST le format attendu, en entier (fullmatch)
    — pas une extraction partielle au sein d'une chaîne plus riche."""
    motif = _MOTIFS_CONSOMMATION.get(type_declare)
    return motif is not None and motif.match(brut.strip()) is not None


CONVERTISSEURS: dict[str, type] = {
    "texte": lambda brut: brut,
    "entier": norm.extraire_entier,
    "decimal": norm.extraire_decimal,
    "decimal_m": norm.cote_en_metres,
    "decimal_mm": norm.mesure_en_mm,
    "grammage": norm.grammage_g_m2,
    "date_fr": norm.date_fr_vers_iso,
    "booleen": norm.booleen_fr,
}


# Échelle de confiance (docs/CONTROLES_RG16.md) :
#   0,99 — lecture déterministe : ancre exacte, valeur intégralement bornée
#          (fin de ligne, changement de colonne ou cellule de tableau) ET
#          format intégralement consommé par le convertisseur du type ;
#   0,90 — lecture correcte mais ambigüité résiduelle (troncature par libellé
#          stop, extraction partielle du format) ;
#   0,85 — sous-valeurs de décompositions structurées (galons, jonctions,
#          épaisseurs, finitions, options) ;
#   0,70 et 0,50-0,60 — reconnaissons partielles, présences douteuses.
# Le palier 0,99 doit rester AU-DESSUS du seuil structurel le plus strict
# (0,98 dans config/seuils_confiance.json) : sans cela la voie « passage
# direct » serait morte par construction (§17.14 vise ≥ 50 % en Phase 2).
CONFIANCE_CERTAIN = 0.99
CONFIANCE_LUE = 0.90

_MOTIFS_CONSOMMATION: dict[str, re.Pattern[str]] = {
    "decimal_m": re.compile(r"^-?\d+(?:[.,]\d+)?\s*(?:m[²23]?|cm|mm|kg)?\s*$", re.I),
    "decimal_mm": re.compile(r"^-?\d+(?:[.,]\d+)?\s*(?:mm|cm|m)?\s*$", re.I),
    "decimal": re.compile(r"^-?\d+(?:[.,]\d+)?\s*(?:m[²23]?|cm|mm|kg|g)?\s*$", re.I),
    "entier": re.compile(r"^\s*\d+\s*$"),
    "grammage": re.compile(r"^\s*\d+(?:[.,]\d+)?\s*(?:g|gr)\s*/\s*m\s*[²2^]?\s*$", re.I),
    "date_fr": re.compile(r"^\s*\d{1,2}\s+[a-zéûôà]+\s+\d{4}\s*$", re.I),
    "booleen": re.compile(r"^\s*(?:oui|non)\s*$", re.I),
}


def _confiance_type(type_declare: str, brut: str, normalise: object) -> float:
    """Confiance selon la qualité du format : format attendu = 0,9 ;
    valeur présente mais non convertible dans le type attendu = 0,5."""
    if normalise is None or normalise == "":
        return 0.5
    if type_declare == "texte" or type_declare == "date_fr":
        return CONFIANCE_LUE
    attendu = CONVERTISSEURS.get(type_declare)
    if attendu is not None and attendu(brut) is not None:
        return CONFIANCE_LUE
    return 0.5


# ---------------------------------------------------------------------------
# Traitements structurés (décompositions propres au vocabulaire des fiches)
# ---------------------------------------------------------------------------


def _traiter_titre(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    trouve = re.search(r"[«\"](.+?)[»\"]", brut)
    if trouve:
        fiche.titre = trouve.group(1).strip()
        base.valeur_normalisee = fiche.titre
    elif brut.strip():
        fiche.titre = brut.strip()
        base.valeur_normalisee = fiche.titre
    if fiche.titre:
        base.confiance = max(base.confiance, CONFIANCE_CERTAIN)


def _traiter_designation(fiche: FicheExtraite, brut: str, base: ChampExtrait, fallback: str | None = None) -> None:
    """« Spi Asymétrique Medium Régate » → type_voile + gamme."""
    valeur = brut.strip()
    if not valeur and fallback:
        valeur = fallback  # variante génois : l'ancre du titre EST le type
        base.valeur_brute = None  # rien n'a été lu : seul le type est posé
    if not valeur:
        return
    normalise = norm.sans_accents(valeur)
    for motif, libelle, famille in TYPES_VOILE:
        if normalise.startswith(norm.sans_accents(motif)):
            fiche.type_voile_libelle = libelle
            reste = valeur[len(motif) :].strip(" -,–")
            if reste:
                fiche.gamme = reste
            base.valeur_normalisee = f"{libelle} | {reste}" if reste else libelle
            # type reconnu parmi les motifs connus : lecture déterministe
            base.confiance = max(base.confiance, CONFIANCE_CERTAIN)
            return
    fiche.gamme = fiche.gamme or valeur
    base.valeur_normalisee = valeur
    base.confiance = max(base.confiance, 0.7)


def _traiter_support(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    nom, taille = norm.separer_nom_et_detail(brut)
    fiche.bateau_nom = nom
    fiche.bateau_taille = taille
    if nom:
        base.valeur_normalisee = f"{nom} | {taille}" if taille else nom
        base.confiance = max(base.confiance, CONFIANCE_CERTAIN)


def _traiter_client(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    nom, chantier = norm.separer_nom_et_detail(brut)
    fiche.client_nom = nom
    fiche.client_chantier = chantier
    if nom:
        base.valeur_normalisee = f"{nom} | {chantier}" if chantier else nom
        base.confiance = max(base.confiance, CONFIANCE_CERTAIN)


def _traiter_commande(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    valeur = brut.strip().strip("()[]")
    if valeur:
        fiche.commande_numero = valeur
        base.valeur_normalisee = valeur
        base.confiance = max(base.confiance, CONFIANCE_CERTAIN)


def _traiter_dessinateur(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    """« Yann · 6 mars 2026 » → dessinateur + date ISO.

    Deux formes réelles : la reconstruction sépare par un tiret ; le document
    client écrit « dessiné par Yann le 6 mars 2026 » (ancre « dessiné par »,
    valeur « Yann le 6 mars 2026 »)."""
    en_le = re.match(r"^\s*(?P<nom>[A-Za-zÀ-ÿ-]+)\s+le\s+(?P<date>.+)$", brut.strip())
    if en_le:
        fiche.dessinateur = en_le.group("nom").strip()
        base.valeur_normalisee = fiche.dessinateur
        date_iso = norm.date_fr_vers_iso(en_le.group("date"))
        if date_iso:
            fiche.date_dessin = date_iso
            base.valeur_normalisee += f" | {date_iso}"
        base.confiance = max(base.confiance, 0.85)
        return
    morceaux = norm.scinder_sur_tirets(brut)
    if not morceaux:
        return
    fiche.dessinateur = morceaux[0].strip()
    base.valeur_normalisee = fiche.dessinateur
    for morceau in morceaux[1:]:
        date_iso = norm.date_fr_vers_iso(morceau)
        if date_iso:
            fiche.date_dessin = date_iso
            base.valeur_normalisee += f" | {date_iso}"
            break
    base.confiance = max(base.confiance, 0.85)


def _traiter_fichier_source(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    """« 7792-SO.xlsm édité le 06/03/2026 » → fichier + date d'édition."""
    trouve = re.match(r"\s*(\S+\.(?:xlsm|xlsx|pdf|dwg|dxf))", brut, re.I)
    if not trouve:
        return
    fiche.fichier_source = trouve.group(1)
    base.valeur_normalisee = fiche.fichier_source
    date_iso = norm.date_fr_vers_iso(brut)
    if date_iso:
        fiche.date_edition = date_iso
        base.valeur_normalisee += f" | {date_iso}"
    base.confiance = max(base.confiance, 0.9)


def _traiter_montage(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    """« Collé/Cousu · V46 » → montage_type + montage_fil."""
    morceaux = norm.scinder_sur_tirets(brut)
    if not morceaux:
        return
    fiche.montage_type = morceaux[0].strip()
    base.valeur_normalisee = fiche.montage_type
    for morceau in morceaux[1:]:
        trouve = re.search(r"fil\s+([A-Za-z]?\d+[a-z]?)", morceau, re.I)
        if trouve:
            fiche.montage_fil = trouve.group(1)
        else:
            fiche.montage_fil = morceau.strip()
    if fiche.montage_fil:
        base.valeur_normalisee += f" | fil {fiche.montage_fil}"
    base.confiance = max(base.confiance, 0.9)


def _traiter_montage_fil(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    """« Montage en Fil: V46 » du document réel : le fil seul, lu après le
    libellé — le type de montage (Collé/Cousu) est lu par la règle montage et
    n'est jamais écrasé."""
    trouve = re.search(r"fil\s*[: ]\s*([A-Za-z]?\d+[a-z]?)", brut, re.I)
    fil = trouve.group(1) if trouve else brut.strip()
    if fil and fiche.montage_fil is None:
        fiche.montage_fil = fil
        base.valeur_normalisee = fil
        base.confiance = max(base.confiance, 0.9)


def _reprendre_base(fiche: FicheExtraite, base: ChampExtrait, **mises_a_jour) -> ChampExtrait:
    """Retire la trace de tête de fiche.champs et la rend pour rangement dans
    la ligne enfant — une valeur écrite n'apparaît qu'une fois en base."""
    if base in fiche.champs:
        fiche.champs.remove(base)
    return base.model_copy(update=mises_a_jour)


def _traiter_galons(fiche: FicheExtraite, brut: str, base: ChampExtrait, ancle_norm: str) -> None:
    """« Bleu · 50 mm · Nylon 65 g/m2 » → fiche_galon (bande lue dans l'ancre)."""
    bande = "guindant"
    if "chute" in ancle_norm:
        bande = "chute"
    elif "bordure" in ancle_norm:
        bande = "bordure"
    galon = next((g for g in fiche.galons if g.bande == bande), None)
    if galon is None:
        galon = Galon(bande=bande)
        fiche.galons.append(galon)
    morceaux = norm.scinder_sur_tirets(brut)
    couleurs: list[str] = []
    for morceau in morceaux:
        largeur = norm.extraire_decimal(morceau) if re.search(r"\bmm\b", morceau, re.I) else None
        grammage = norm.grammage_g_m2(morceau)
        if largeur is not None:
            galon.largeur_mm = norm.vers_mm(largeur, "mm")
            base.valeur_normalisee = f"{base.valeur_normalisee} | {galon.largeur_mm} mm" if base.valeur_normalisee else f"{galon.largeur_mm} mm"
        elif grammage is not None:
            galon.grammage_g_m2 = grammage
            trouve = re.match(r"\s*([A-Za-zÀ-ÿ-]+)", morceau)
            if trouve:
                galon.matiere = trouve.group(1)
            base.valeur_normalisee = f"{base.valeur_normalisee} | {morceau.strip()}" if base.valeur_normalisee else morceau.strip()
        else:
            couleurs.append(morceau)
    if couleurs:
        galon.couleur = " · ".join(couleurs)
        base.valeur_normalisee = f"{galon.couleur} | {base.valeur_normalisee}" if base.valeur_normalisee else galon.couleur
    base.confiance = max(base.confiance, 0.85)
    galon.champs.append(_reprendre_base(fiche, base, table_cible="fiche_galon", colonne_cible=bande))


def _traiter_jonctions(fiche: FicheExtraite, brut: str, base: ChampExtrait, ancle_norm: str) -> None:
    """« 2 zigzag 6 tps 30 mm » → fiche_jonction (nature lue dans l'ancre)."""
    nature = "laizes"
    ordre = 1
    if "surplus" in ancle_norm:
        nature = "surplus"
        nb_zigzag = nb_points = None
        espacement = None
        description = brut.strip() or None
    else:
        nb_zigzag, nb_points, espacement, description = norm.decomposer_jonction(brut)
        if "laize" in ancle_norm:
            nature = "laizes"
        elif "horizontale" in ancle_norm:
            nature = "horizontale"
        elif "verticale" in ancle_norm:
            nature = "verticale"
            trouve = re.search(r"\b(\d+)\b", brut)
            ordre = int(trouve.group(1)) if trouve else 1
        else:
            nature = "laizes"
            ordre = 1
    existante = next((j for j in fiche.jonctions if j.nature == nature and j.ordre == ordre), None)
    if existante is None:
        existante = Jonction(
            nature=nature,
            ordre=ordre,
            nb_zigzag=nb_zigzag,
            nb_points=nb_points,
            espacement_mm=espacement,
            description=description,
        )
        fiche.jonctions.append(existante)
    else:
        existante.nb_zigzag = existante.nb_zigzag or nb_zigzag
        existante.nb_points = existante.nb_points or nb_points
        existante.espacement_mm = existante.espacement_mm or espacement
        existante.description = existante.description or description
    if nature == "surplus":
        existante.surplus = brut.strip() or None
    base.confiance = max(base.confiance, 0.85)
    existante.champs.append(_reprendre_base(fiche, base, table_cible="fiche_jonction", colonne_cible=f"{nature}[{ordre}]"))


def _traiter_finitions(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    """« amure/écoute/drisse — Œillet SR12, non-sanglé » → fiche_finition."""
    gauche, _, droite = brut.partition("—")
    postes = [normaliser_terme(poste) for poste in re.split(r"[/,]", gauche) if poste.strip()]
    valeur = re.sub(r",?\s*non[\s-]*sangl\w*", "", droite, flags=re.I).strip().rstrip(",;").strip()
    if not valeur:
        valeur = gauche.strip()
    oeillet = None
    sangle = None
    trouve = re.search(r"œillet\s+(\S+)", droite, re.I)
    if trouve:
        oeillet = trouve.group(1).rstrip(",;")
    if re.search(r"non[\s-]*sangl", droite, re.I):
        sangle = False
    elif re.search(r"sangl", droite, re.I):
        sangle = True
    if not postes:
        postes = ["finition"]
    base.confiance = max(base.confiance, 0.85)
    for rang, poste in enumerate(postes, start=1):
        finition = next((f for f in fiche.finitions if f.poste == poste), None)
        if finition is None:
            finition = Finition(poste=poste, valeur_texte=valeur or None, oeillet_type=oeillet, sangle=sangle)
            fiche.finitions.append(finition)
        finition.champs.append(_reprendre_base(fiche, base, rang=rang, table_cible="fiche_finition", colonne_cible=poste))


def _traiter_options(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    """« Velcro anti-déroulement : Non · Protection anti-UV : Non » → fiche_option.

    « ~ » signifie « sans objet » (RG5) : l'option est enregistrée sans
    valeur booléenne, avec son texte — jamais inventée à faux.
    """
    morceaux = norm.scinder_sur_tirets(brut)
    for morceau in morceaux:
        libelle, _, valeur = morceau.partition(":")
        libelle = libelle.strip()
        valeur = valeur.strip()
        if not libelle:
            continue
        code = re.sub(r"[^a-z0-9]+", "_", norm.sans_accents(libelle)).strip("_")
        option = OptionFiche(code=code, valeur_bool=norm.booleen_fr(valeur), valeur_texte=valeur or None)
        fiche.options.append(option)
        trace = _reprendre_base(
            fiche,
            base,
            rang=len(fiche.options),
            colonne_cible=code,
            valeur_brute=morceau,
            valeur_normalisee=str(option.valeur_bool) if option.valeur_bool is not None else valeur,
            table_cible="fiche_option",
        )
        trace.confiance = 0.85
        option.champs.append(trace)
    base.confiance = max(base.confiance, 0.85)


def _traiter_epaisseurs(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    """« Monofilm K903 · 190 mm ; 270 g/m2 ; 220 mm ; ~ » (niveaux 01→10).

    Chaque morceau séparé par « ; » est l'épaisseur d'un niveau consécutif ;
    un morceau « ~ » est un niveau vide (RG5, conservé en texte). Un morceau
    peut porter désignation + mesure (« Monofilm K903 · 190 mm »).
    """
    morceaux = [morceau.strip() for morceau in brut.split(";")]
    for index, morceau in enumerate(morceaux, start=1):
        if not morceau or morceau == "~":
            continue
        designation = None
        mesure = None
        grammage = None
        trouve = re.search(r"(\d+(?:[.,]\d+)?)\s*mm\b", morceau, re.I)
        if trouve:
            mesure = norm.vers_mm(float(trouve.group(1).replace(",", ".")), "mm")
            designation = morceau[: trouve.start()].rstrip(" ·").strip() or None
            reste = morceau[trouve.end() :].strip(" ·")
            if reste:
                grammage = norm.grammage_g_m2(reste) or grammage
        if grammage is None:
            grammage = norm.grammage_g_m2(morceau)
            if grammage is not None:
                # le grammage se détache de la désignation (« Nylon 270 g/m2 »)
                designation = re.sub(r"\d+(?:[.,]\d+)?\s*(?:g|gr)\s*/\s*m\s*[²2^]?", "", designation or "").strip(" ·") or None
        if designation is None and grammage is None and mesure is None:
            designation = morceau  # aucun motif reconnu : le texte brut est conservé
        materiau = Materiau(
            role="epaisseur",
            niveau=index,
            designation=designation or None,
            grammage_g_m2=grammage,
            mesure_mm=mesure,
        )
        trace = _reprendre_base(
            fiche,
            base,
            rang=index,
            valeur_brute=morceau,
            valeur_normalisee=designation or None,
            table_cible="fiche_materiau",
            colonne_cible=f"epaisseur_{index:02d}",
        )
        trace.confiance = 0.85
        materiau.champs.append(trace)
        fiche.materiaux.append(materiau)
    base.confiance = max(base.confiance, 0.85)


def _traiter_tissu_principal(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """« Tissu : Dacron 260 » → fiche_materiau (rôle tissu_principal) + fiche.tissu_texte.

    Réservé aux gabarits dont l'ancre désigne VRAIMENT le tissu principal
    (génois) ; la fiche 7792 garde « Tissu(s) » en texte libre — son contenu
    (« Voir avec JFC suivant stock ») n'est pas un matériau.
    """
    valeur = brut.strip()
    if not valeur:
        return
    fiche.tissu_texte = valeur
    base.valeur_normalisee = valeur
    base.confiance = max(base.confiance, 0.9)
    materiau = Materiau(role="tissu_principal", designation=valeur, grammage_g_m2=norm.grammage_g_m2(valeur))
    materiau.champs.append(_reprendre_base(fiche, base, table_cible="fiche_materiau", colonne_cible="tissu_principal"))
    fiche.materiaux.append(materiau)


def _traiter_notes(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    """Notes libres + extraction des renforts qu'elles décrivent (RG16-safe)."""
    fiche.notes = brut.strip() or None
    if fiche.notes:
        base.valeur_normalisee = fiche.notes
        base.confiance = max(base.confiance, 0.85)
    for trouve in re.finditer(
        r"(\d+)\s*[x×]\s*(œillets?|oeillets?|sangles?|renforts?)\s*(?:n[°o]\s*(\d+|[A-Za-z0-9]+))?\s*(?:[ØøØ]\s*(\d+(?:[.,]\d+)?)\s*mm)?\s*(.*)",
        brut,
        re.I,
    ):
        quantite, forme, repere, diametre, matiere = trouve.groups()
        renfort = Renfort(
            repere=f"n°{repere}" if repere else None,
            quantite=int(quantite),
            forme=normaliser_terme(forme),
            diametre_mm=norm.vers_mm(float(diametre.replace(",", ".")), "mm") if diametre else None,
            matiere=matiere.strip() or None,
            description=trouve.group(0).strip(),
        )
        trace = _reprendre_base(
            fiche,
            base,
            champ="renforts",
            rang=len(fiche.renforts) + 1,
            valeur_brute=trouve.group(0).strip(),
            valeur_normalisee=f"{quantite}x {renfort.forme}" + (f" Ø{renfort.diametre_mm}mm" if renfort.diametre_mm else ""),
            table_cible="fiche_renfort",
        )
        trace.confiance = 0.85
        renfort.champs.append(trace)
        fiche.renforts.append(renfort)


# ---------------------------------------------------------------------------
# Traitements « document réel » (Tâche 2 — vraie fiche 7792-SO, 842×595,
# grille tracée). Ces traitements lisent la PAGE (contexte["pages"]) :
# ligne de titre unique, grille de cotes à colonnes alignées, blocs
# épaisseurs / galons / finitions en zones. Rien n'est complété ni deviné :
# une valeur absente reste absente (RG6).
# ---------------------------------------------------------------------------

_RE_CODE_FICHE = re.compile(r"\b(\d{3,5}-[A-Za-z]{2,4})\b")
_ECAP_GAUCHE_PT = 130.0  # colonne des postes de finition (bord gauche réel)
_ECAP_VALEUR_PT = 330.0  # fin de la colonne des valeurs de finition
_TOL_COLONNE_PT = 9.0  # tolérance d'alignement d'une valeur sous son en-tête
_GAP_GROUPE_PT = 10.0  # écart séparant deux groupes de l'en-tête de cotes
# Ligne de valeurs d'un galon : l'écart interne (largeur « 50 mm » puis
# matière « Nylon ») monte à ~35 pt ; la section voisine (épaisseurs) est à
# des centaines de points. Une rupture de section est donc bien au-delà.
_GAP_SECTION_GALON_PT = 60.0


def _localiser_sequence(mots: list[Mot], jetons: list[str]) -> list[Mot]:
    """Sous-séquence consécutive de mots dont les formes normalisées sont
    ``jetons`` — sert à donner une zone exacte à une sous-valeur de la ligne
    de titre (jamais de zone inventée)."""
    normes = [mot.normalise for mot in mots]
    for depart in range(len(normes) - len(jetons) + 1):
        if normes[depart : depart + len(jetons)] == jetons:
            return mots[depart : depart + len(jetons)]
    return []


def _traiter_ligne_titre_portant(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Ligne de titre RÉELLE d'une fiche portant — quatre champs structurels
    sur une seule ligne, plus le code à droite :

        Spi Asymétrique Medium Régate pour 29er (15') de Sailonet (Cruette) 7792-SO

    → désignation (type + gamme), bateau, client, code. L'ancre de la règle
    fait partie de la désignation ; chaque sous-valeur reçoit sa propre trace
    (champ « fiche.* ») avec sa zone, pour le rapport et le routage.

    La ligne est relue ENTIÈRE depuis la page : le code est rejeté à droite
    au-delà d'une rupture de colonne que la lecture de règle ne franchit
    pas, et c'est pourtant une valeur lue de la même ligne."""
    ancre_norm = normaliser_terme(contexte.get("ancre") or "")
    mots: list[Mot] = []
    for page in contexte.get("pages") or []:
        for ligne in page.lignes:
            if _indice_ancre(ligne, ancre_norm) is not None:
                mots = list(ligne.mots)
                break
        if mots:
            break
    if not mots:
        mots = list(contexte.get("mots") or [])
    valeur = " ".join(mot.texte for mot in mots).strip()
    if not valeur:
        return

    if fiche.code is None:
        codes = _RE_CODE_FICHE.findall(valeur)
        if codes:
            fiche.code = codes[-1].upper()

    correspondance = re.search(
        r"\bpour\s+(?P<bateau>.+?)\s+de\s+(?P<client>.+?)(?:\s+\d{3,5}-[A-Za-z]{2,4})?\s*$",
        valeur,
        flags=re.I,
    )
    designation = valeur
    if correspondance:
        designation = valeur[: correspondance.start()].strip()
        if fiche.bateau_nom is None:
            bateau_brut = correspondance.group("bateau").strip()
            nom, taille = norm.separer_nom_et_detail(bateau_brut)
            fiche.bateau_nom = nom or bateau_brut
            fiche.bateau_taille = taille
        if fiche.client_nom is None:
            client_brut = correspondance.group("client").strip()
            nom, chantier = norm.separer_nom_et_detail(client_brut)
            fiche.client_nom = nom or client_brut
            fiche.client_chantier = chantier

    if designation and fiche.type_voile_libelle is None:
        _traiter_designation(fiche, designation, base)

    # Traces : la base (règle) devient la trace désignation ; code, bateau et
    # client reçoivent chacun leur ChampExtrait nommé « fiche.* » (nécessaire
    # au routage et au rapport champ par champ).
    base.champ = "fiche.designation"
    base.valeur_brute = valeur
    base.zone = _zone_des_mots(mots)
    sous_traces = []
    if fiche.code is not None:
        zone_mots = _localiser_sequence(mots, normaliser_terme(fiche.code).split())
        sous_traces.append(
            _reprendre_base(
                fiche, base, champ="fiche.code",
                valeur_brute=fiche.code, valeur_normalisee=fiche.code,
                confiance=CONFIANCE_CERTAIN, zone=_zone_des_mots(zone_mots),
                table_cible="fiche", colonne_cible="code",
            )
        )
    if fiche.client_nom is not None:
        zone_mots = _localiser_sequence(mots, normaliser_terme(correspondance.group("client")).split()) if correspondance else []
        sous_traces.append(
            _reprendre_base(
                fiche, base, champ="fiche.client",
                valeur_brute=correspondance.group("client") if correspondance else fiche.client_nom,
                valeur_normalisee=f"{fiche.client_nom} | {fiche.client_chantier}" if fiche.client_chantier else fiche.client_nom,
                confiance=CONFIANCE_CERTAIN, zone=_zone_des_mots(zone_mots),
                table_cible="fiche", colonne_cible="client",
            )
        )
    if fiche.bateau_nom is not None:
        zone_mots = _localiser_sequence(mots, normaliser_terme(correspondance.group("bateau")).split()) if correspondance else []
        sous_traces.append(
            _reprendre_base(
                fiche, base, champ="fiche.bateau",
                valeur_brute=correspondance.group("bateau") if correspondance else fiche.bateau_nom,
                valeur_normalisee=f"{fiche.bateau_nom} | {fiche.bateau_taille}" if fiche.bateau_taille else fiche.bateau_nom,
                confiance=CONFIANCE_CERTAIN, zone=_zone_des_mots(zone_mots),
                table_cible="fiche", colonne_cible="bateau",
            )
        )
    # La base a été retirée par _reprendre_base à la première sous-trace :
    # elle revient comme trace de désignation (une valeur lue = une trace).
    fiche.champs.append(base)
    for trace in sous_traces:
        fiche.champs.append(trace)


def _traiter_grille_cotes(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Grille de cotes du document réel : l'en-tête porte les colonnes
    (Guindant (SLU) … Poids (tissu)) ; les valeurs sont les mots de MÊME
    COLONNE sur les lignes « Mesures Dessin » / « Mesures Finies » — les deux
    jeux du schéma fiche_cotes. La grille tracée (rects) borne la zone de
    lecture ; l'alignement en colonnes fait le reste, sans coordonnées dures.

    « ~ » = sans objet (RG5) : la cote reste absente, jamais complétée."""
    pages = contexte.get("pages") or []
    ancre_norm = normaliser_terme(contexte.get("ancre") or "guindant slu")

    en_tete = None
    for page in pages:
        for ligne in page.lignes:
            if _indice_ancre(ligne, ancre_norm) is not None:
                en_tete = (page, ligne)
                break
        if en_tete:
            break
    if en_tete is None:
        return
    page, ligne_en_tete = en_tete

    # Colonnes : groupes de mots de l'en-tête séparés par un écart marqué.
    groupes: list[list[Mot]] = [[ligne_en_tete.mots[0]]]
    for mot in ligne_en_tete.mots[1:]:
        if mot.x0 - groupes[-1][-1].x1 > _GAP_GROUPE_PT:
            groupes.append([mot])
        else:
            groupes[-1].append(mot)
    colonnes: list[tuple[str, float, float]] = []
    for groupe in groupes:
        etiquette = re.sub(
            r"\s+", " ", " ".join(mot.normalise for mot in groupe).replace("(", " ").replace(")", " ")
        ).strip()
        colonnes.append((etiquette, groupe[0].x0, groupe[-1].x1))

    def _colonne_pour(mot: Mot) -> int | None:
        centre = (mot.x0 + mot.x1) / 2
        for indice, (_etiquette, x0, x1) in enumerate(colonnes):
            if x0 - _TOL_COLONNE_PT <= centre <= x1 + _TOL_COLONNE_PT:
                return indice
        return None

    # Lignes de valeurs : sous l'en-tête, tant que la grille court (écart
    # vertical raisonnable), lignes commençant par « mesures dessin/finies ».
    y_en_tete = ligne_en_tete.mots[0].haut
    lectures: list[tuple[str, list[Mot]]] = []
    for ligne in page.lignes:
        if not ligne.mots or ligne.mots[0].haut <= y_en_tete:
            continue
        if ligne.mots[0].haut - y_en_tete > 45:
            break
        tete_ligne = " ".join(mot.normalise for mot in ligne.mots[:2])
        if tete_ligne.startswith("mesures dessin"):
            jeu = "dessin"
        elif tete_ligne.startswith("mesures finies"):
            jeu = "finie"
        else:
            continue
        lectures.append((jeu, ligne))

    colonnes_cibles = {
        "guindant slu": ("slu_m", "decimal_m"),
        "chute sle": ("sle_m", "decimal_m"),
        "bordure sf": ("sf_m", "decimal_m"),
        "shw": ("shw_m", "decimal_m"),
        "surface spa": ("spa_m2", "decimal_m"),
        "tetiere": ("tetiere_cm", "decimal"),
        "poids": ("poids_kg", "decimal"),
    }
    cible_par_colonne: list[tuple[str, str] | None] = []
    for etiquette, _x0, _x1 in colonnes:
        trouve = None
        for prefixe, cible_type in colonnes_cibles.items():
            if etiquette.startswith(prefixe):
                trouve = cible_type
                break
        cible_par_colonne.append(trouve)

    for jeu, ligne in lectures:
        par_colonne: dict[int, list[Mot]] = {}
        for mot in ligne.mots[2:]:
            indice = _colonne_pour(mot)
            if indice is not None and cible_par_colonne[indice] is not None:
                par_colonne.setdefault(indice, []).append(mot)
        for indice, mots_valeur in par_colonne.items():
            colonne, type_cote = cible_par_colonne[indice]
            brut_cote = " ".join(mot.texte for mot in mots_valeur).strip()
            if not brut_cote or brut_cote == "~":
                continue  # sans objet : conservé tel quel, jamais complété
            convertisseur = CONVERTISSEURS.get(type_cote)
            valeur = convertisseur(brut_cote) if convertisseur else None
            if valeur is None:
                continue
            cible = f"cotes.{jeu}.{colonne}"
            if _cote_deja_lue(fiche, cible):
                continue
            champ = ChampExtrait(
                champ=cible,
                valeur_brute=brut_cote,
                valeur_normalisee=str(valeur),
                methode="gabarit",
                confiance=CONFIANCE_CERTAIN,  # cellule bornée par la grille tracée
                page=page.numero,
                zone=_zone_des_mots(mots_valeur),
                table_cible="fiche_cotes",
                colonne_cible=colonne,
            )
            _ranger_cote(fiche, cible, valeur, champ)
    # La règle a produit ses propres traces ; la base de tête ne sert plus.
    if base in fiche.champs:
        fiche.champs.remove(base)


def _traiter_epaisseurs_grille(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Bloc « Épaisseur NN » du document réel : une entrée par niveau
    (01→10). Sur la page réelle, le bloc vit dans une colonne à droite et
    partage ses lignes avec d'autres sections (galons à gauche) : la séquence
    « Epaisseur NN » est donc cherchée N'IMPORTE OÙ dans la ligne, à condition
    d'ouvrir son propre bloc (écart marqué avec le mot précédent). La valeur
    du niveau suit, dans l'ordre : désignation éventuelle, grammage éventuel,
    mesure en mm. Un niveau « ~ » est vide : aucun matériau n'est créé pour
    lui (RG5), et rien n'est deviné (RG6)."""
    if fiche.materiaux:
        return  # déjà lus par une autre règle du gabarit
    pages = contexte.get("pages") or []
    for page in pages:
        for ligne in page.lignes:
            mots = ligne.mots
            for indice, mot in enumerate(mots[:-1]):
                if mot.normalise != "epaisseur":
                    continue
                if indice > 0 and mot.x0 - mots[indice - 1].x1 <= _GAP_GROUPE_PT:
                    continue  # « epaisseur » collé au mot précédent : pas un bloc
                numero = mots[indice + 1]
                if not re.fullmatch(r"\d{1,2}", numero.normalise):
                    continue
                niveau = int(numero.normalise)
                reste_mots = mots[indice + 2 :]
                reste = " ".join(m.texte for m in reste_mots).strip()
                if not reste or set(reste) <= {"~", " "}:
                    break  # niveau vide : sans objet, aucun matériau (RG5)
                if any(m.niveau == niveau for m in fiche.materiaux):
                    break
                designation = None
                mesure = None
                trouve_mm = re.search(r"(\d+(?:[.,]\d+)?)\s*mm\b", reste, re.I)
                if trouve_mm:
                    mesure = norm.vers_mm(float(trouve_mm.group(1).replace(",", ".")), "mm")
                    candidat = reste[: trouve_mm.start()].strip()
                    grammage_seul = norm.grammage_g_m2(candidat)
                    if candidat and grammage_seul is None and not re.fullmatch(r"[\d.,\s~]+", candidat):
                        designation = candidat
                grammage = norm.grammage_g_m2(reste)
                if designation is None and grammage is None and mesure is None:
                    designation = reste  # aucun motif reconnu : le texte est conservé
                materiau = Materiau(
                    role="epaisseur",
                    niveau=niveau,
                    designation=designation,
                    grammage_g_m2=grammage,
                    mesure_mm=mesure,
                )
                trace = _reprendre_base(
                    fiche, base,
                    champ=f"materiau.epaisseur_{niveau:02d}",
                    rang=niveau,
                    valeur_brute=f"Epaisseur {numero.texte} {reste}".strip(),
                    valeur_normalisee=designation,
                    page=page.numero,
                    zone=_zone_des_mots(mots[indice:]),
                    table_cible="fiche_materiau",
                    colonne_cible=f"epaisseur_{niveau:02d}",
                )
                trace.confiance = 0.85
                materiau.champs.append(trace)
                fiche.materiaux.append(materiau)
                break
    if base in fiche.champs:
        fiche.champs.remove(base)


def _traiter_galons_grille(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Galons du document réel : sections bornées par les bandes
    (Guindant / Chute / Bordure) ; chaque « Galon - <couleur> » est suivi de
    sa ligne de valeurs (« 50 mm Nylon 65 gr/m² »). Une bande « Libre » n'a
    pas de galon : rien n'est créé (RG6)."""
    if fiche.galons:
        return
    pages = contexte.get("pages") or []
    bande_courante: str | None = None
    galon_courant: Galon | None = None
    trace_courante: ChampExtrait | None = None

    def _bloc_depuis(mots: list[Mot], rupture_pt: float) -> list[Mot]:
        """Mots du premier bloc : la lecture s'arrête à la première rupture
        d'espacement supérieure à ``rupture_pt`` ou au premier « ~ » (sans
        objet) — les autres sections de la ligne (épaisseurs, cibles…) ne
        sont pas du galon."""
        bloc: list[Mot] = []
        precedent: Mot | None = None
        for mot in mots:
            if mot.texte == "~":
                break
            if precedent is not None and mot.x0 - precedent.x1 > rupture_pt:
                break
            bloc.append(mot)
            precedent = mot
        return bloc

    for page in pages:
        for ligne in page.lignes:
            if not ligne.mots:
                continue
            premier = ligne.mots[0].normalise
            if premier in ("guindant", "chute", "bordure"):
                bande_courante = premier
                continue
            if premier == "galon" and bande_courante:
                # La couleur colle au tiret ; l'en-tête de colonne « Grammage »
                # qui suit est une autre section (écart de groupe marqué).
                mots_couleur = _bloc_depuis(ligne.mots[1:], _GAP_GROUPE_PT)
                couleur = " ".join(mot.texte for mot in mots_couleur).lstrip("- ").strip()
                galon_courant = Galon(bande=bande_courante, couleur=couleur or None)
                trace_courante = _reprendre_base(
                    fiche, base,
                    champ=f"galon.{bande_courante}",
                    valeur_brute=" ".join(mot.texte for mot in mots_couleur),
                    page=page.numero,
                    zone=_zone_des_mots(ligne.mots),
                    table_cible="fiche_galon",
                    colonne_cible=bande_courante,
                )
                continue
            if galon_courant is not None and re.match(r"^\d", premier):
                mots_valeur = _bloc_depuis(ligne.mots, _GAP_SECTION_GALON_PT)
                texte = " ".join(mot.texte for mot in mots_valeur)
                normalisee: list[str] = []
                largeur = norm.extraire_decimal(texte) if re.search(r"\bmm\b", texte, re.I) else None
                if largeur is not None:
                    galon_courant.largeur_mm = norm.vers_mm(largeur, "mm")
                    normalisee.append(f"{galon_courant.largeur_mm} mm")
                grammage = norm.grammage_g_m2(texte)
                if grammage is not None:
                    galon_courant.grammage_g_m2 = grammage
                    normalisee.append(f"{grammage} g/m²")
                if galon_courant.matiere is None:
                    trouve = re.search(r"\bmm\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ-]*)", texte, re.I)
                    if trouve:
                        galon_courant.matiere = trouve.group(1)
                if trace_courante is not None:
                    trace_courante.valeur_normalisee = " | ".join(normalisee) or None
                    trace_courante.confiance = 0.85
                    galon_courant.champs.append(trace_courante)
                fiche.galons.append(galon_courant)
                galon_courant = None
                trace_courante = None
    if base in fiche.champs:
        fiche.champs.remove(base)


_POSTES_CONNUS: tuple[str, ...] = ("amure", "ecoute", "drisse")


def _traiter_finitions_grille(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Finitions du document réel : les postes (Amure / Écoute / Drisse)
    forment une colonne à gauche ; chaque poste est apparié à la valeur la
    plus proche en y dans la colonne voisine (« Œillet SR12, non-sanglé »).
    Un poste sans valeur à portée reste sans valeur — jamais devinée."""
    if fiche.finitions:
        return
    pages = contexte.get("pages") or []
    for page in pages:
        label_y = None
        for ligne in page.lignes:
            if ligne.mots and ligne.mots[0].normalise == "finition":
                label_y = ligne.mots[0].haut
                break
        if label_y is None:
            continue
        postes: list[tuple[str, Mot]] = []
        valeurs: list[tuple[float, str, list[Mot]]] = []
        for ligne in page.lignes:
            for mot in ligne.mots:
                if mot.haut <= label_y:
                    continue
                if mot.x0 >= _ECAP_GAUCHE_PT:
                    break
                if normaliser_terme(mot.texte) in _POSTES_CONNUS:
                    postes.append((normaliser_terme(mot.texte), mot))
        for ligne in page.lignes:
            mots_valeur = [
                mot for mot in ligne.mots
                if mot.haut > label_y and _ECAP_GAUCHE_PT <= mot.x0 <= _ECAP_VALEUR_PT
            ]
            texte = " ".join(mot.texte for mot in mots_valeur)
            if mots_valeur and re.search(r"œillet|sangl|cosse|mousqueton|poulie", texte, re.I):
                valeurs.append((mots_valeur[0].haut, texte, mots_valeur))
        if not postes:
            continue
        for poste, mot_poste in postes:
            meilleure = min(valeurs, key=lambda candidat: abs(candidat[0] - mot_poste.haut), default=None)
            valeur_texte = None
            oeillet = None
            sangle = None
            mots_valeur: list[Mot] = []
            if meilleure is not None and abs(meilleure[0] - mot_poste.haut) <= 25:
                valeur_texte = meilleure[1]
                mots_valeur = meilleure[2]
                trouve = re.search(r"œillet\s+(\S+)", valeur_texte, re.I)
                if trouve:
                    oeillet = trouve.group(1).rstrip(",;")
                if re.search(r"non[\s-]*sangl", valeur_texte, re.I):
                    sangle = False
                elif re.search(r"sangl", valeur_texte, re.I):
                    sangle = True
                valeur_texte = re.sub(r",?\s*non[\s-]*sangl\w*", "", valeur_texte, flags=re.I).strip().rstrip(",;").strip()
            finition = Finition(poste=poste, valeur_texte=valeur_texte or None, oeillet_type=oeillet, sangle=sangle)
            trace = _reprendre_base(
                fiche, base,
                champ=f"finition.{poste}",
                valeur_brute=valeur_texte or None,
                valeur_normalisee=valeur_texte or None,
                page=page.numero,
                zone=_zone_des_mots(mots_valeur) or _zone_des_mots([mot_poste]),
                table_cible="fiche_finition",
                colonne_cible=poste,
            )
            trace.confiance = 0.85
            finition.champs.append(trace)
            fiche.finitions.append(finition)
    if base in fiche.champs:
        fiche.champs.remove(base)


def _traiter_jonctions_grille(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Jonctions du document réel : une ligne d'en-tête porte les colonnes
    (« Laizes », « Jonctions horizontales », « Jonction verticale 1 »…) et la
    ligne immédiatement en dessous porte les valeurs de chaque colonne
    (« 1 zigzag 6 tps 15mm »). Une colonne « ~ » est sans objet : aucune
    jonction n'est créée pour elle (RG5). La ligne « Surplus » se lit de la
    même manière (« ~ » conservé tel quel)."""
    if fiche.jonctions:
        return
    pages = contexte.get("pages") or []
    for page in pages:
        en_tete = None
        for ligne in page.lignes:
            if any(mot.normalise == "laizes" for mot in ligne.mots):
                en_tete = ligne
                break
        if en_tete is None:
            continue
        indice = next(i for i, mot in enumerate(en_tete.mots) if mot.normalise == "laizes")
        groupes: list[list[Mot]] = [[en_tete.mots[indice]]]
        for mot in en_tete.mots[indice + 1 :]:
            if mot.x0 - groupes[-1][-1].x1 > _GAP_GROUPE_PT:
                groupes.append([mot])
            else:
                groupes[-1].append(mot)
        ligne_valeur = None
        for ligne in page.lignes:
            if not ligne.mots or ligne.mots[0].haut <= en_tete.mots[0].haut:
                continue
            if any("zigzag" in mot.normalise for mot in ligne.mots):
                ligne_valeur = ligne
                break
        if ligne_valeur is None:
            continue
        # Valeurs en blocs (écarts de groupe), puis chaque bloc rejoint sa
        # colonne d'en-tête la plus proche — la valeur « laizes » déborde à
        # gauche de son en-tête, un containment strict la perdrait.
        blocs: list[list[Mot]] = []
        for mot in ligne_valeur.mots:
            if blocs and mot.x0 - blocs[-1][-1].x1 <= _GAP_GROUPE_PT:
                blocs[-1].append(mot)
            else:
                blocs.append([mot])
        valeur_par_colonne: dict[int, list[Mot]] = {}
        for bloc in blocs:
            centre_bloc = (bloc[0].x0 + bloc[-1].x1) / 2
            meilleure, distance = None, None
            for indice, groupe in enumerate(groupes):
                centre_colonne = (groupe[0].x0 + groupe[-1].x1) / 2
                ecart = abs(centre_bloc - centre_colonne)
                if distance is None or ecart < distance:
                    meilleure, distance = indice, ecart
            if meilleure is not None and distance is not None and distance <= 90.0:
                valeur_par_colonne[meilleure] = bloc

        for indice, groupe in enumerate(groupes):
            etiquette = " ".join(mot.normalise for mot in groupe)
            mots_colonne = valeur_par_colonne.get(indice, [])
            brut_colonne = " ".join(mot.texte for mot in mots_colonne).strip()
            if not brut_colonne or brut_colonne == "~":
                continue  # sans objet : jamais de jonction inventée (RG5/RG6)
            if "laizes" in etiquette:
                nature, ordre = "laizes", 1
            elif "horizontale" in etiquette:
                nature, ordre = "horizontale", 1
            elif "verticale" in etiquette:
                nature = "verticale"
                numero = re.search(r"\b(\d+)\b", etiquette)
                ordre = int(numero.group(1)) if numero else len([j for j in fiche.jonctions if j.nature == "verticale"]) + 1
            else:
                continue
            nb_zigzag, nb_points, espacement, description = norm.decomposer_jonction(brut_colonne)
            jonction = Jonction(
                nature=nature, ordre=ordre, nb_zigzag=nb_zigzag,
                nb_points=nb_points, espacement_mm=espacement, description=description,
            )
            trace = _reprendre_base(
                fiche, base,
                champ=f"jonction.{nature}",
                rang=ordre,
                valeur_brute=brut_colonne,
                valeur_normalisee=description,
                page=page.numero,
                zone=_zone_des_mots(mots_colonne),
                table_cible="fiche_jonction",
                colonne_cible=f"{nature}[{ordre}]",
            )
            trace.confiance = 0.85
            jonction.champs.append(trace)
            fiche.jonctions.append(jonction)
        # Ligne « Surplus » : le libellé puis le premier bloc de valeur
        # (« ~ » conservé tel quel — RG5).
        for ligne in page.lignes:
            if not ligne.mots or ligne.mots[0].normalise != "surplus":
                continue
            bloc: list[Mot] = []
            for mot in ligne.mots[1:]:
                if bloc and mot.x0 - bloc[-1].x1 > _GAP_GROUPE_PT:
                    break
                bloc.append(mot)
            valeur = " ".join(mot.texte for mot in bloc).strip() or None
            surplus = Jonction(nature="surplus", surplus=valeur, description=valeur)
            trace = _reprendre_base(
                fiche, base,
                champ="jonction.surplus",
                valeur_brute=valeur,
                valeur_normalisee=valeur,
                page=page.numero,
                zone=_zone_des_mots(bloc or ligne.mots),
                table_cible="fiche_jonction",
                colonne_cible="surplus",
            )
            trace.confiance = 0.85
            surplus.champs.append(trace)
            fiche.jonctions.append(surplus)
            break
        break
    if base in fiche.champs:
        fiche.champs.remove(base)


# Options du document réel : colonne centrale sous le bloc finitions
# (« Emmagasineur ~ », « Sac Pas de sac. »…). La zone est délimitée par la
# ligne « Finition » et la colonne des valeurs de finition à gauche.
_X_OPTIONS_DROITE_PT = 560.0


def _traiter_options_grille(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Options du document réel — colonne centrale sous le bloc finitions :
    chaque ligne porte un libellé d'option puis sa valeur (« Emmagasineur ~ »,
    « Sac Pas de sac. »). « ~ » = sans objet : valeur booléenne inconnue,
    jamais devinée (RG5/RG6). Le bloc s'arrête à la ligne « Notes ».

    Ce traitement complète (sans les remplacer) les règles d'options v1 :
    le document réel imprime ses options en DEUX endroits — cette colonne
    centrale et le bloc « Velcro / Retenue / Protection » — et les deux
    sont lus, sans doublon par code."""
    pages = contexte.get("pages") or []
    codes_presents = {option.code for option in fiche.options}
    for page in pages:
        label_y = None
        for ligne in page.lignes:
            if ligne.mots and ligne.mots[0].normalise == "finition":
                label_y = ligne.mots[0].haut
                break
        if label_y is None:
            continue
        for ligne in page.lignes:
            if not ligne.mots or ligne.mots[0].haut <= label_y:
                continue
            if ligne.mots[0].normalise == "notes":
                break  # fin du bloc options
            mots_zone = [
                mot for mot in ligne.mots
                if _ECAP_VALEUR_PT < mot.x0 < _X_OPTIONS_DROITE_PT
            ]
            if not mots_zone:
                continue
            libelle: list[Mot] = []
            valeur_mots: list[Mot] = []
            for mot in mots_zone:
                if not valeur_mots and (not libelle or mot.x0 - libelle[-1].x1 <= GAP_COLONNE_PT):
                    libelle.append(mot)
                else:
                    valeur_mots.append(mot)
            valeur = " ".join(mot.texte for mot in valeur_mots).strip()
            if valeur == "~":
                valeur = ""
            nom = re.sub(
                r"[^a-z0-9]+", "_", norm.sans_accents(" ".join(mot.texte for mot in libelle))
            ).strip("_")
            if not nom or nom in codes_presents:
                continue
            option = OptionFiche(
                code=nom,
                valeur_bool=True if valeur.lower() in ("oui", "true", "1") else False if valeur.lower() in ("non", "false", "0") else None,
                valeur_texte=valeur or None,
            )
            trace = _reprendre_base(
                fiche, base,
                champ=f"option.{nom}",
                valeur_brute=valeur or "~",
                valeur_normalisee=valeur or None,
                page=page.numero,
                zone=_zone_des_mots(mots_zone),
                table_cible="fiche_option",
                colonne_cible=nom,
            )
            trace.confiance = 0.85
            option.champs.append(trace)
            fiche.options.append(option)
            codes_presents.add(nom)
        break
    if base in fiche.champs:
        fiche.champs.remove(base)


# Codes d'options canoniques (schéma fiche_option) : le libellé réel peut
# porter un détail (« au point d'écoute », « WeatherMax-65 ») — le code reste
# celui du schéma quand le libellé commence par la formule canonique, et le
# libellé intégral demeure dans la trace (valeur_brute). Pas de code inventé
# au-delà : un libellé inconnu reçoit sa normalisation mécanique.
_OPTIONS_CANONIQUES: tuple[tuple[str, str], ...] = (
    ("velcro anti-deroulement", "velcro_anti_deroulement"),
    ("protection anti-uv", "protection_anti_uv"),
    ("retenue de contre ecoute", "retenue_contre_ecoute"),
    ("retenue contre ecoute", "retenue_contre_ecoute"),
)


def _code_option(libelle_norm: str) -> str:
    for prefixe, code in _OPTIONS_CANONIQUES:
        if libelle_norm.startswith(prefixe):
            return code
    return re.sub(r"[^a-z0-9]+", "_", libelle_norm).strip("_")


def _traiter_options_lignes(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Bloc « Velcro anti-deroulement … Non » du document réel : une option
    par ligne, la valeur (Oui/Non) rejetée à droite après une rupture de
    colonne ; tout ce qui précède est le libellé, lu intégralement (le détail
    « au point d'écoute », « WeatherMax-65 » fait partie de la lecture)."""
    pages = contexte.get("pages") or []
    codes_presents = {option.code for option in fiche.options}
    for page in pages:
        for ligne in page.lignes:
            if not ligne.mots or ligne.mots[0].normalise not in ("velcro", "retenue", "protection"):
                continue
            libelle = list(ligne.mots)
            valeur = ""
            precedent: Mot | None = None
            for indice, mot in enumerate(ligne.mots):
                if precedent is not None and mot.x0 - precedent.x1 > GAP_COLONNE_PT and (
                    mot.texte.lower() in ("oui", "non") or indice == len(ligne.mots) - 1
                ):
                    libelle = ligne.mots[:indice]
                    valeur = mot.texte
                    break
                precedent = mot
            libelle_norm = norm.sans_accents(" ".join(mot.texte for mot in libelle)).lower()
            nom = _code_option(libelle_norm)
            if not nom or nom in codes_presents:
                continue
            option = OptionFiche(
                code=nom,
                valeur_bool=norm.booleen_fr(valeur),
                valeur_texte=valeur or None,
            )
            trace = _reprendre_base(
                fiche, base,
                champ=f"option.{nom}",
                valeur_brute=" ".join(mot.texte for mot in ligne.mots),
                valeur_normalisee=str(option.valeur_bool) if option.valeur_bool is not None else valeur or None,
                page=page.numero,
                zone=_zone_des_mots(ligne.mots),
                table_cible="fiche_option",
                colonne_cible=nom,
            )
            trace.confiance = 0.85
            option.champs.append(trace)
            fiche.options.append(option)
            codes_presents.add(nom)
    if base in fiche.champs:
        fiche.champs.remove(base)


def _traiter_renforts_note(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Renforts du document réel, imprimés dans la note :
    « 2 x œillets n°1 … Ø 200mm en Nylon + 1 dacron Ø 150 + 1 dacron 110mm
    pour avaleur. » — un renfort par segment séparé par « + ». Ce qui n'est
    pas lu (forme non nommée, diamètre absent) reste None : jamais complété."""
    if fiche.renforts:
        return
    pages = contexte.get("pages") or []
    for page in pages:
        for ligne in page.lignes:
            # La note forme un bloc ; un mot très décalé à droite (« Logo
            # SailOnet ») est sur la même ligne pdfplumber mais n'en fait pas
            # partie : on coupe à la rupture de section.
            bloc: list[Mot] = []
            for mot in ligne.mots:
                if bloc and mot.x0 - bloc[-1].x1 > _GAP_SECTION_GALON_PT:
                    break
                bloc.append(mot)
            texte = " ".join(mot.texte for mot in bloc)
            if "renfort" not in norm.sans_accents(texte).lower():
                continue
            segments = [segment.strip(" .") for segment in texte.split("+")]
            for segment in segments:
                if not segment.strip():
                    continue
                quantite = None
                trouve = re.match(r"(\d+)\s*(?:x\b|×)?", segment, re.I)
                if trouve:
                    quantite = int(trouve.group(1))
                forme = None
                if re.search(r"\b(?:œ|oe)illets?\b", segment, re.I):
                    forme = "œillets"
                elif re.search(r"\bdacron\b", segment, re.I):
                    forme = "dacron"
                diametre = None
                trouve = re.search(r"[ØO]\s*(\d+(?:[.,]\d+)?)\s*mm\b", segment, re.I)
                if trouve is None:
                    trouve = re.search(r"(\d+(?:[.,]\d+)?)\s*mm\b", segment, re.I)
                if trouve:
                    diametre = float(trouve.group(1).replace(",", "."))
                matiere = None
                trouve = re.search(r"\ben\s+([A-Za-zÀ-ÿ-]+)", segment)
                if trouve:
                    matiere = trouve.group(1)
                renfort = Renfort(
                    quantite=quantite, forme=forme, diametre_mm=diametre,
                    matiere=matiere, description=segment.strip(),
                )
                trace = _reprendre_base(
                    fiche, base,
                    champ="renfort.note",
                    rang=len(fiche.renforts) + 1,
                    valeur_brute=segment.strip(),
                    valeur_normalisee=segment.strip(),
                    page=page.numero,
                    zone=_zone_des_mots(bloc),
                    table_cible="fiche_renfort",
                    colonne_cible=f"renfort_{len(fiche.renforts) + 1}",
                )
                trace.confiance = 0.85
                renfort.champs.append(trace)
                fiche.renforts.append(renfort)
            break
        if fiche.renforts:
            break
    if base in fiche.champs:
        fiche.champs.remove(base)


def _traiter_fichier_edite(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Ligne « Fichier 7792-SO.xlsm édité le 06/03/2026 » du document réel :
    le nom de fichier porte son extension (sinon ce n'est pas une lecture)."""
    base.champ = "fiche.fichier_source"
    if fiche.fichier_source:
        return
    pages = contexte.get("pages") or []
    for page in pages:
        for ligne in page.lignes:
            texte = " ".join(mot.texte for mot in ligne.mots)
            trouve = re.search(r"(\S+\.(?:xlsm|xlsx|pdf|dwg|dxf))", texte, re.I)
            if not trouve:
                continue
            fiche.fichier_source = trouve.group(1)
            base.valeur_normalisee = fiche.fichier_source
            date_iso = norm.date_fr_vers_iso(texte)
            if date_iso:
                fiche.date_edition = date_iso
                base.valeur_normalisee += f" | {date_iso}"
            base.confiance = max(base.confiance, 0.9)
            return


# ---------------------------------------------------------------------------
# Traitements « format atelier » (corpus réel 2026-09-28 — gabarits
# FICHE_JADE_V1 / FICHE_GV_FULLBATTEN_V1). Ces traitements lisent la PAGE
# (contexte["pages"]) : en-tête en colonnes CLIENT/BATEAU/TYPE DE VOILE,
# ligne N° Commande (référence à 6 chiffres + initiales, parfois collée au
# libellé), ligne DATE avec Surface, cotes des trois bords en décimal
# français (valeur pouvant être collée au libellé : « GUINDANT9,53 »),
# bandes de force (BDF) par bord, points d'ancrage, coutures zigzag,
# renforts au-dessus du planning atelier. Rien n'est complété ni deviné :
# une zone absente reste absente (RG6), un nombre sans unité explicite
# n'est jamais converti (RG16 honnête).
# ---------------------------------------------------------------------------

# Bord → colonne de cote du schéma (SLU = guindant, SLE = chute, SF = bordure).
_BORDS_JADE: tuple[tuple[str, str], ...] = (
    ("guindant", "slu_m"),
    ("chute", "sle_m"),
    ("bordure", "sf_m"),
)
# Limite haute de la zone « matière » sous la ligne DATE (en points) : la
# colonne matière des fiches réelles vit dans les ~15 pt sous la date ; les
# sections suivantes (RIS, LATTES…) commencent plus bas.
_DELTA_TISSU_PT = 15.0
# Bord droit de la colonne centrale (bateau/matière) : au-delà commencent la
# colonne Expédition et le bord droit de la page.
_X_COLONNE_CENTRALE_PT = 340.0
# Préfixes des lignes du planning atelier (bas de page) : tout ce qui suit
# appartient à l'ordonnancement, pas à la fiche métier.
_PREFIXES_PLANNING = ("preparation", "prepa", "coupe", "montage machine", "temps total", "renforts + uv")


def _ligne_contenant(pages: list[PageAnalysee], predicat) -> tuple[PageAnalysee, Ligne] | None:
    """Première ligne (page, ligne) satisfaisant le prédicat."""
    for page in pages:
        for ligne in page.lignes:
            if ligne.mots and predicat(ligne):
                return page, ligne
    return None


def _trace_fiche(
    champ: str,
    valeur_brute: str | None,
    valeur_normalisee: str | None,
    confiance: float,
    page: int,
    zone: Zone | None,
    colonne: str,
) -> ChampExtrait:
    """ChampExtrait de tête (table ``fiche``) — traces des traitements atelier."""
    return ChampExtrait(
        champ=champ,
        valeur_brute=valeur_brute,
        valeur_normalisee=valeur_normalisee,
        methode="gabarit",
        confiance=confiance,
        page=page,
        zone=zone,
        table_cible="fiche",
        colonne_cible=colonne,
    )


def _designation_jade(fiche: FicheExtraite, valeur: str) -> tuple[str | None, str | None, float]:
    """« Gennaker C0 » / « GV Fullbatten » / « GSE » → (type, gamme, confiance).

    Un type connu du lexique ouvre type + gamme (lecture déterministe) ; un
    acronyme inconnu (ex. « GSE ») est conservé comme type TEL QUEL, sans
    gamme inventée, à confiance réduite.
    """
    normalise = norm.sans_accents(valeur).lower().strip()
    for motif, libelle, _famille in TYPES_VOILE:
        if normalise.startswith(norm.sans_accents(motif).lower()):
            reste = valeur[len(motif) :].strip(" -,–")
            return libelle, (reste or None), CONFIANCE_CERTAIN
    return valeur.strip() or None, None, 0.7


def _traiter_entete_jade(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """En-tête du format atelier : titre, N° Commande, colonnes CLIENT /
    BATEAU / TYPE DE VOILE, DATE, Surface. Chaque sous-valeur reçoit sa
    propre trace avec sa zone (le bateau peut être reporté sur la ligne
    suivante quand il déborde de sa colonne — lu dans la colonne, jamais
    dans le texte voisin)."""
    pages = contexte.get("pages") or []
    if base in fiche.champs:
        fiche.champs.remove(base)
    for page in pages:
        lignes = [ligne for ligne in page.lignes if ligne.mots]
        # --- Titre : « FICHE DE FABRICATION » -----------------------------
        titre = _ligne_contenant([page], lambda ligne: ligne.mots[0].normalise == "fiche" and "fabrication" in ligne.normalise)
        if titre is not None and fiche.titre is None:
            ligne_titre = titre[1]
            texte_titre = " ".join(mot.texte for mot in ligne_titre.mots)
            fiche.titre = texte_titre
            fiche.champs.append(
                _trace_fiche("fiche.titre", texte_titre, texte_titre, CONFIANCE_CERTAIN, page.numero, _zone_des_mots(ligne_titre.mots), "titre")
            )
        # --- N° Commande : référence à 6 chiffres + initiales -------------
        commande = _ligne_contenant(
            [page], lambda ligne: any("commande" in mot.normalise for mot in ligne.mots) and ligne.mots[0].haut < 70
        )
        if commande is not None and fiche.code is None:
            ligne_commande = commande[1]
            texte = " ".join(mot.texte for mot in ligne_commande.mots)
            trouve = re.search(r"(\d{6})\s*([A-Za-z]{2,3})\b", texte)
            if trouve:
                fiche.code = f"{trouve.group(1)} {trouve.group(2)}"
                mots_zone = [
                    mot
                    for mot in ligne_commande.mots
                    if trouve.group(1) in mot.texte or mot.texte == trouve.group(2)
                ]
                fiche.champs.append(
                    _trace_fiche(
                        "fiche.code", trouve.group(0), fiche.code, CONFIANCE_CERTAIN,
                        page.numero, _zone_des_mots(mots_zone) or _zone_des_mots(ligne_commande.mots), "code",
                    )
                )
        # --- Ligne d'en-tête : CLIENT … BATEAU … TYPE DE VOILE … -----------
        entete = None
        for ligne in lignes:
            normes = [mot.normalise for mot in ligne.mots]
            if "client" in normes and "bateau" in normes and "type" in normes and "voile" in normes:
                entete = ligne
                break
        if entete is not None:
            mots = entete.mots
            index_client = next(i for i, mot in enumerate(mots) if mot.normalise == "client")
            index_bateau = next(i for i, mot in enumerate(mots) if mot.normalise == "bateau")
            index_type = next(i for i, mot in enumerate(mots) if mot.normalise == "type")
            # client : entre les libellés CLIENT et BATEAU
            if fiche.client_nom is None:
                mots_client = mots[index_client + 1 : index_bateau]
                texte_client = " ".join(mot.texte for mot in mots_client).strip()
                if texte_client:
                    fiche.client_nom = texte_client
                    fiche.champs.append(
                        _trace_fiche(
                            "fiche.client", texte_client, texte_client, CONFIANCE_CERTAIN,
                            page.numero, _zone_des_mots(mots_client), "client",
                        )
                    )
            # bateau : entre BATEAU et TYPE, sinon la ligne suivante DANS la
            # colonne bateau (x du libellé BATEAU à x du libellé TYPE).
            if fiche.bateau_nom is None:
                mots_bateau = mots[index_bateau + 1 : index_type]
                if not mots_bateau:
                    x_min = mots[index_bateau].x1
                    x_max = mots[index_type].x0
                    y_entete = mots[index_bateau].haut
                    for ligne in lignes:
                        if not ligne.mots or ligne.mots[0].haut <= y_entete or ligne.mots[0].haut - y_entete > 6:
                            continue
                        mots_bateau = [mot for mot in ligne.mots if x_min < mot.x0 < x_max]
                        break
                texte_bateau = " ".join(mot.texte for mot in mots_bateau).strip()
                if texte_bateau:
                    nom, taille = norm.separer_nom_et_detail(texte_bateau)
                    fiche.bateau_nom = nom or texte_bateau
                    fiche.bateau_taille = taille
                    fiche.champs.append(
                        _trace_fiche(
                            "fiche.bateau", texte_bateau,
                            f"{fiche.bateau_nom} | {taille}" if taille else fiche.bateau_nom,
                            CONFIANCE_CERTAIN, page.numero, _zone_des_mots(mots_bateau), "bateau",
                        )
                    )
            # type de voile : après la séquence TYPE DE VOILE, jusqu'en fin de ligne
            if fiche.type_voile_libelle is None and index_type + 2 < len(mots):
                if mots[index_type + 1].normalise == "de" and mots[index_type + 2].normalise == "voile":
                    mots_type = mots[index_type + 3 :]
                    texte_type = " ".join(mot.texte for mot in mots_type).strip()
                    if texte_type:
                        libelle, gamme, confiance = _designation_jade(fiche, texte_type)
                        fiche.type_voile_libelle = libelle
                        fiche.gamme = gamme
                        fiche.champs.append(
                            _trace_fiche(
                                "fiche.designation", texte_type,
                                f"{libelle} | {gamme}" if gamme else libelle,
                                confiance, page.numero, _zone_des_mots(mots_type), "designation",
                            )
                        )
        # --- Ligne DATE : date + surface -----------------------------------
        ligne_date = _ligne_contenant([page], lambda ligne: ligne.mots[0].normalise == "date")
        if ligne_date is not None:
            ligne = ligne_date[1]
            texte = " ".join(mot.texte for mot in ligne.mots)
            if fiche.date_edition is None:
                date_iso = norm.date_fr_vers_iso(texte)
                if date_iso is not None:
                    fiche.date_edition = date_iso
                    trouve = re.search(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{4}", texte)
                    mots_date = [mot for mot in ligne.mots if trouve and trouve.group(0) in mot.texte]
                    fiche.champs.append(
                        _trace_fiche(
                            "fiche.date_edition", trouve.group(0) if trouve else texte, date_iso,
                            CONFIANCE_CERTAIN, page.numero, _zone_des_mots(mots_date), "date_edition",
                        )
                    )
            if not _cote_deja_lue(fiche, "cotes.finie.spa_m2"):
                trouve_surface = re.search(r"(?:surface|surf)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*m", texte, re.I)
                if trouve_surface:
                    valeur = norm.cote_en_metres(trouve_surface.group(0))
                    if valeur is not None:
                        index_surface = next(
                            (i for i, mot in enumerate(ligne.mots) if mot.normalise in ("surface", "surf")), None
                        )
                        mots_surface = ligne.mots[index_surface + 1 :] if index_surface is not None else []
                        champ = ChampExtrait(
                            champ="cotes.finie.spa_m2",
                            valeur_brute=trouve_surface.group(0),
                            valeur_normalisee=str(valeur),
                            methode="gabarit",
                            confiance=CONFIANCE_CERTAIN,
                            page=page.numero,
                            zone=_zone_des_mots(mots_surface),
                            table_cible="fiche_cotes",
                            colonne_cible="spa_m2",
                        )
                        _ranger_cote(fiche, "cotes.finie.spa_m2", valeur, champ)


def _traiter_bords_jade(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Cotes des trois bords : la ligne « GUINDANT9,53 » (valeur collée au
    libellé), « CHUTE 11,2 » ou « BORDURE 5,37 à la corde ». Le qualificatif
    (« à la corde ») reste dans la valeur brute ; seule la valeur numérique
    est normalisée en mètres."""
    pages = contexte.get("pages") or []
    if base in fiche.champs:
        fiche.champs.remove(base)
    for page in pages:
        for ligne in page.lignes:
            if not ligne.mots:
                continue
            tete = ligne.mots[0].normalise
            for bord, colonne in _BORDS_JADE:
                if not tete.startswith(bord):
                    continue
                cible = f"cotes.finie.{colonne}"
                if _cote_deja_lue(fiche, cible):
                    break
                texte = " ".join(mot.texte for mot in ligne.mots)
                trouve = re.match(rf"^{bord}\s*(\d+(?:[.,]\d+)?)", texte, re.I)
                if trouve is None:
                    break  # ligne « Guindant : voir tracé » → pas une cote lisible
                valeur = norm.cote_en_metres(trouve.group(1))
                if valeur is None:
                    break
                champ = ChampExtrait(
                    champ=cible,
                    valeur_brute=texte,
                    valeur_normalisee=str(valeur),
                    methode="gabarit",
                    confiance=CONFIANCE_CERTAIN,
                    page=page.numero,
                    zone=_zone_des_mots(ligne.mots),
                    table_cible="fiche_cotes",
                    colonne_cible=colonne,
                )
                _ranger_cote(fiche, cible, valeur, champ)
                break


def _traiter_tissu_jade(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Matière : bande de mots sous/autour de la ligne DATE, colonne centrale
    (x < 340). Sur les fiches réelles, les mots matière se partagent la ligne
    DATE (reconstruction par tolérance verticale) ou la suivent à ~10 pt.
    Sont exclus : les libellés et valeurs de DATE/Surface/Expédition, la
    diagonale (« D 7,45 NP ») et les rapports numériques (« 344/394 »).
    Absente → aucun matériau (jamais inventée)."""
    if fiche.materiaux:
        return
    pages = contexte.get("pages") or []
    if base in fiche.champs:
        fiche.champs.remove(base)
    for page in pages:
        datee = _ligne_contenant([page], lambda ligne: ligne.mots[0].normalise == "date")
        if datee is None:
            continue
        ligne_date = datee[1]
        y_date = ligne_date.mots[0].haut
        # Tout ce qui est à droite du libellé Surface SUR la ligne DATE
        # appartient au bloc surface/expédition, jamais à la matière.
        mot_surface = next((m for m in ligne_date.mots if m.normalise in ("surface", "surf")), None)
        x_surface = mot_surface.x0 - 2 if mot_surface is not None else None
        bande = [
            mot
            for mot in ligne_date.mots
            if mot.x0 < _X_COLONNE_CENTRALE_PT and (x_surface is None or mot.x0 < x_surface)
        ]
        for ligne in page.lignes:
            if not ligne.mots or ligne is ligne_date:
                continue
            y = ligne.mots[0].haut
            if y <= y_date - 1 or y - y_date > _DELTA_TISSU_PT:
                continue
            bande.extend(mot for mot in ligne.mots if mot.x0 < _X_COLONNE_CENTRALE_PT)
        mots_zone: list[Mot] = []
        tries = sorted(bande, key=lambda m: (m.haut, m.x0))
        exclus: set[int] = set()
        # Diagonale « D 7,45 NP » : le trio consécutif est exclu en bloc.
        for indice, mot in enumerate(tries):
            if mot.normalise == "d" and indice + 2 < len(tries):
                if re.fullmatch(r"\d+(?:[.,]\d+)?", tries[indice + 1].texte) and tries[indice + 2].normalise == "np":
                    exclus |= {indice, indice + 1, indice + 2}
        for indice, mot in enumerate(tries):
            if indice in exclus:
                continue
            normalise = mot.normalise
            if normalise in ("date", "surface", "surf") or normalise.startswith("expedition"):
                continue
            if re.fullmatch(r"\d{1,2}[.,]\d{1,2}[.,]\d{2,4}", mot.texte):  # valeur de date
                continue
            if re.fullmatch(r"\d{3,4}/\d{3,4}", mot.texte):  # rapport de mesures, pas une matière
                continue
            if normalise == "np" and any(i in exclus for i in (indice - 2, indice - 1)):
                continue
            mots_zone.append(mot)
        if not mots_zone:
            continue
        designation = " ".join(mot.texte for mot in mots_zone).strip()
        if not designation:
            continue
        fiche.tissu_texte = designation
        materiau = Materiau(role="tissu_principal", designation=designation, grammage_g_m2=norm.grammage_g_m2(designation))
        trace = _trace_fiche(
            "materiau.tissu_principal", designation, designation, 0.85,
            page.numero, _zone_des_mots(mots_zone), "tissu_principal",
        )
        materiau.champs.append(trace)
        fiche.materiaux.append(materiau)
        break


def _traiter_galons_bdf(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Bandes de force (« BDF a plat / pliée / décalée … ») rattachées au
    bord en cours (section GUINDANT / CHUTE / BORDURE). Le texte intégral de
    la ligne est proposé tel quel : sans unité explicite, aucune largeur ni
    grammage n'est converti (jamais inventé)."""
    if fiche.galons:
        return
    pages = contexte.get("pages") or []
    if base in fiche.champs:
        fiche.champs.remove(base)
    for page in pages:
        bande: str | None = None
        for ligne in page.lignes:
            if not ligne.mots:
                continue
            tete = ligne.mots[0].normalise
            bord_vu = next((bord for bord, _colonne in _BORDS_JADE if tete.startswith(bord)), None)
            if bord_vu is not None:
                bande = bord_vu
                continue
            if bande is None or not tete.startswith("bdf"):
                continue
            texte = " ".join(mot.texte for mot in ligne.mots).strip()
            trouve_type = re.match(r"bdf\s+(a plat|pliée|pliee|décalée|decalee)", ligne.normalise)
            type_bdf = normaliser_terme(trouve_type.group(1)).replace(" ", "_") if trouve_type else "bdf"
            galon = Galon(bande=bande)  # largeur/grammage/couleur : absents sans unité explicite
            trace = ChampExtrait(
                champ=f"galon.{bande}.{type_bdf}",
                valeur_brute=texte,
                valeur_normalisee=texte,
                methode="gabarit",
                confiance=0.85,
                page=page.numero,
                zone=_zone_des_mots(ligne.mots),
                table_cible="fiche_galon",
                colonne_cible=f"{bande}.{type_bdf}",
            )
            galon.champs.append(trace)
            fiche.galons.append(galon)


def _traiter_finitions_jade(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Points d'ancrage : lignes AMURE / ECOUTE / DRISSE / RIS [CHUTE|GT] /
    TÊTIÈRE sous le bloc « POINT D'ANCRAGE ». La valeur est le reste de la
    ligne ; œillet et sangle sont décomposés quand ils y figurent. Les
    lignes de continuation (« Barrée », « Surliures main »…) ne créent rien."""
    if fiche.finitions:
        return
    pages = contexte.get("pages") or []
    if base in fiche.champs:
        fiche.champs.remove(base)
    postes = ("amure", "ecoute", "drisse", "ris", "tetiere")
    for page in pages:
        ancrage = _ligne_contenant(
            [page], lambda ligne: ligne.mots[0].normalise == "point" and "ancrage" in ligne.normalise
        )
        if ancrage is None:
            continue
        y_ancrage = ancrage[1].mots[0].haut
        for ligne in page.lignes:
            if not ligne.mots or ligne.mots[0].haut <= y_ancrage:
                continue
            mots = ligne.mots
            indice = 0
            # GV : « 3 ris RIS CHUTE … » — le quantificateur précède le libellé.
            if re.fullmatch(r"\d+", mots[0].normalise) and len(mots) > 1 and mots[1].normalise == "ris":
                indice = 2
            if indice >= len(mots):
                continue
            tete = mots[indice].normalise
            if tete not in postes:
                continue
            poste = tete
            consomme = indice + 1
            if tete == "ris" and consomme < len(mots) and mots[consomme].normalise in ("chute", "gt"):
                poste = f"ris {mots[consomme].normalise}"
                consomme += 1
            valeur_mots = mots[consomme:]
            valeur = " ".join(mot.texte for mot in valeur_mots).strip()
            oeillet = None
            trouve = re.search(r"[ŒOe]{1,2}illet\s+(\S+)", valeur, re.I)
            if trouve:
                oeillet = trouve.group(1).rstrip(",;.")
            sangle = None
            if re.search(r"non[\s-]*sangl", valeur, re.I):
                sangle = False
                valeur = re.sub(r",?\s*non[\s-]*sangl\w*", "", valeur, flags=re.I).strip().rstrip(",;").strip()
            elif re.search(r"sangl", valeur, re.I):
                sangle = True
            finition = Finition(poste=poste, valeur_texte=valeur or None, oeillet_type=oeillet, sangle=sangle)
            poste_norm = poste.replace(" ", "_")
            trace = ChampExtrait(
                champ=f"finition.{poste_norm}",
                valeur_brute=" ".join(mot.texte for mot in mots),
                valeur_normalisee=valeur or None,
                methode="gabarit",
                confiance=0.85,
                page=page.numero,
                zone=_zone_des_mots(mots),
                table_cible="fiche_finition",
                colonne_cible=poste_norm,
            )
            finition.champs.append(trace)
            fiche.finitions.append(finition)


def _traiter_montage_jade(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Coutures : la ligne portant « zigzag » / « zig zag » donne le montage
    (points zigzag, fil). La ligne est relue ENTIÈRE depuis la page : le
    nombre de points précède le mot zigzag, hors de la valeur bornée."""
    pages = contexte.get("pages") or []
    for page in pages:
        ligne_montage = _ligne_contenant([page], lambda ligne: "zigzag" in ligne.normalise or ("zig" in [m.normalise for m in ligne.mots] and "zag" in [m.normalise for m in ligne.mots]))
        if ligne_montage is None:
            continue
        texte = " ".join(mot.texte for mot in ligne_montage[1].mots)
        # Le nombre de points précède parfois « zigzag » (fiches GSE :
        # « 2 Zig Zag 6 temps ») mais peut être omis (fiches GV :
        # « zigzag 6temps. ») — il reste facultatif, jamais inventé.
        trouve = re.search(r"(?:(\d+)\s*)?zig\s*zag\w*\s*(?:en\s*)?(\d+)\s*t(?:emps|ps)", texte, re.I)
        if trouve is None:
            continue
        fiche.montage_type = f"{trouve.group(1) or ''} zigzag {trouve.group(2)} temps".strip()
        base.champ = "fiche.montage_type"
        base.valeur_brute = texte
        base.valeur_normalisee = fiche.montage_type
        base.confiance = 0.85
        base.zone = _zone_des_mots(ligne_montage[1].mots)
        trouve_fil = re.search(r"\bfil\s+(\d+)\b", texte, re.I)
        if trouve_fil and fiche.montage_fil is None:
            fiche.montage_fil = trouve_fil.group(1)
            base.valeur_normalisee += f" | fil {fiche.montage_fil}"
        if base not in fiche.champs:
            fiche.champs.append(base)
        return


def _traiter_renforts_jade(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Renforts : la ligne RENFORTS au-dessus du planning atelier (les
    libellés « RENFORTS + UV » du planning ne sont pas des renforts)."""
    if fiche.renforts:
        return
    pages = contexte.get("pages") or []
    if base in fiche.champs:
        fiche.champs.remove(base)
    for page in pages:
        y_planning: float | None = None
        for ligne in page.lignes:
            if not ligne.mots:
                continue
            tete = " ".join(mot.normalise for mot in ligne.mots[:2])
            if any(tete.startswith(prefixe) or ligne.mots[0].normalise.startswith(prefixe) for prefixe in _PREFIXES_PLANNING):
                y_planning = ligne.mots[0].haut
                break
        for ligne in page.lignes:
            if not ligne.mots or not ligne.mots[0].normalise.startswith("renfort"):
                continue
            if y_planning is not None and ligne.mots[0].haut >= y_planning:
                continue  # libellé du planning atelier, pas un renfort
            texte = " ".join(mot.texte for mot in ligne.mots).strip()
            valeur = " ".join(mot.texte for mot in ligne.mots[1:]).strip()
            if not valeur:
                continue
            quantite = None
            forme = None
            trouve = re.search(r"(\d+)\s*[x×]?\s*(œillets?|oeillets?|sangles?|renforts?)", valeur, re.I)
            if trouve:
                quantite = int(trouve.group(1))
                forme = normaliser_terme(trouve.group(2))
            renfort = Renfort(quantite=quantite, forme=forme, description=valeur)
            trace = ChampExtrait(
                champ="renforts.ligne",
                rang=len(fiche.renforts) + 1,
                valeur_brute=texte,
                valeur_normalisee=valeur,
                methode="gabarit",
                confiance=0.85,
                page=page.numero,
                zone=_zone_des_mots(ligne.mots),
                table_cible="fiche_renfort",
                colonne_cible=f"renfort_{len(fiche.renforts) + 1}",
            )
            renfort.champs.append(trace)
            fiche.renforts.append(renfort)


def _traiter_libres_gv(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Sections propres à la GV fullbatten, conservées en mesures libres
    (aucune table dédiée dans le schéma — jamais forcées ailleurs) : prises
    de ris (« Ris 1 = 4,98 long »), positions de goussets (« L7 = L4 = 4,59 »),
    lattes, numéro de voile, bôme."""
    pages = contexte.get("pages") or []
    if base in fiche.champs:
        fiche.champs.remove(base)
    for page in pages:
        for ligne in page.lignes:
            if not ligne.mots:
                continue
            texte = " ".join(mot.texte for mot in ligne.mots).strip()
            normalise = ligne.normalise
            for cle, valeur in re.findall(r"ris\s+(\d)\s*=\s*(\d+(?:[.,]\d+)?)", normalise):
                fiche.mesures_libres.append(
                    ChampExtrait(
                        champ=f"libre.ris_{cle}",
                        valeur_brute=f"Ris {cle} = {valeur} long",
                        valeur_normalisee=str(norm.extraire_decimal(valeur)),
                        methode="gabarit",
                        confiance=0.7,
                        page=page.numero,
                        zone=_zone_des_mots(ligne.mots),
                    )
                )
            for cle, cle_paire, valeur in re.findall(r"\bl(\d)\s*=\s*(?:(l\d)\s*=\s*)?(\d+(?:[.,]\d+)?)", normalise):
                # « L7 = L4 = 4,59 » : les DEUX positions valent 4,59 —
                # deux mesures libres, jamais une seule (RG6 : rien de perdu).
                for cle_gousset in (cle, cle_paire[1] if cle_paire else ""):
                    if not cle_gousset:
                        continue
                    fiche.mesures_libres.append(
                        ChampExtrait(
                            champ=f"libre.gousset_l{cle_gousset}",
                            valeur_brute=f"L{cle_gousset} = {valeur}",
                            valeur_normalisee=str(norm.extraire_decimal(valeur)),
                            methode="gabarit",
                            confiance=0.7,
                            page=page.numero,
                            zone=_zone_des_mots(ligne.mots),
                        )
                    )
            if ligne.mots[0].normalise == "lattes":
                fiche.mesures_libres.append(
                    ChampExtrait(
                        champ="libre.lattes",
                        valeur_brute=texte,
                        valeur_normalisee=texte,
                        methode="gabarit",
                        confiance=0.7,
                        page=page.numero,
                        zone=_zone_des_mots(ligne.mots),
                    )
                )
            if normalise.startswith("numero voile"):
                fiche.mesures_libres.append(
                    ChampExtrait(
                        champ="libre.numero_voile",
                        valeur_brute=texte,
                        valeur_normalisee=" ".join(mot.texte for mot in ligne.mots[2:]),
                        methode="gabarit",
                        confiance=0.7,
                        page=page.numero,
                        zone=_zone_des_mots(ligne.mots[2:]),
                    )
                )
            if ligne.mots[0].normalise.startswith("bome"):
                fiche.mesures_libres.append(
                    ChampExtrait(
                        champ="libre.bome",
                        valeur_brute=texte,
                        valeur_normalisee=texte,
                        methode="gabarit",
                        confiance=0.7,
                        page=page.numero,
                        zone=_zone_des_mots(ligne.mots),
                    )
                )


TRAITEMENTS = {
    "titre": lambda fiche, brut, base, contexte: _traiter_titre(fiche, brut, base),
    "ligne_titre_portant": lambda fiche, brut, base, contexte: _traiter_ligne_titre_portant(fiche, brut, base, contexte),
    "grille_cotes": lambda fiche, brut, base, contexte: _traiter_grille_cotes(fiche, brut, base, contexte),
    "epaisseurs_grille": lambda fiche, brut, base, contexte: _traiter_epaisseurs_grille(fiche, brut, base, contexte),
    "galons_grille": lambda fiche, brut, base, contexte: _traiter_galons_grille(fiche, brut, base, contexte),
    "finitions_grille": lambda fiche, brut, base, contexte: _traiter_finitions_grille(fiche, brut, base, contexte),
    "jonctions_grille": lambda fiche, brut, base, contexte: _traiter_jonctions_grille(fiche, brut, base, contexte),
    "options_grille": lambda fiche, brut, base, contexte: _traiter_options_grille(fiche, brut, base, contexte),
    "options_lignes": lambda fiche, brut, base, contexte: _traiter_options_lignes(fiche, brut, base, contexte),
    "renforts_note": lambda fiche, brut, base, contexte: _traiter_renforts_note(fiche, brut, base, contexte),
    "fichier_edite": lambda fiche, brut, base, contexte: _traiter_fichier_edite(fiche, brut, base, contexte),
    "designation": lambda fiche, brut, base, contexte: _traiter_designation(fiche, brut, base, contexte.get("fallback")),
    "support": lambda fiche, brut, base, contexte: _traiter_support(fiche, brut, base),
    "client": lambda fiche, brut, base, contexte: _traiter_client(fiche, brut, base),
    "commande": lambda fiche, brut, base, contexte: _traiter_commande(fiche, brut, base),
    "dessinateur": lambda fiche, brut, base, contexte: _traiter_dessinateur(fiche, brut, base),
    "fichier_source": lambda fiche, brut, base, contexte: _traiter_fichier_source(fiche, brut, base),
    "montage": lambda fiche, brut, base, contexte: _traiter_montage(fiche, brut, base),
    "montage_fil": lambda fiche, brut, base, contexte: _traiter_montage_fil(fiche, brut, base),
    "galons": lambda fiche, brut, base, contexte: _traiter_galons(fiche, brut, base, contexte["ancre"]),
    "jonctions": lambda fiche, brut, base, contexte: _traiter_jonctions(fiche, brut, base, contexte["ancre"]),
    "finitions": lambda fiche, brut, base, contexte: _traiter_finitions(fiche, brut, base),
    "options": lambda fiche, brut, base, contexte: _traiter_options(fiche, brut, base),
    "epaisseurs": lambda fiche, brut, base, contexte: _traiter_epaisseurs(fiche, brut, base),
    "tissu_principal": lambda fiche, brut, base, contexte: _traiter_tissu_principal(fiche, brut, base, contexte),
    "notes": lambda fiche, brut, base, contexte: _traiter_notes(fiche, brut, base),
    # Format atelier (corpus réel 2026-09-28 — FICHE_JADE_V1 / FICHE_GV_FULLBATTEN_V1).
    "entete_jade": lambda fiche, brut, base, contexte: _traiter_entete_jade(fiche, brut, base, contexte),
    "bords_jade": lambda fiche, brut, base, contexte: _traiter_bords_jade(fiche, brut, base, contexte),
    "tissu_jade": lambda fiche, brut, base, contexte: _traiter_tissu_jade(fiche, brut, base, contexte),
    "galons_bdf": lambda fiche, brut, base, contexte: _traiter_galons_bdf(fiche, brut, base, contexte),
    "finitions_jade": lambda fiche, brut, base, contexte: _traiter_finitions_jade(fiche, brut, base, contexte),
    "montage_jade": lambda fiche, brut, base, contexte: _traiter_montage_jade(fiche, brut, base, contexte),
    "renforts_jade": lambda fiche, brut, base, contexte: _traiter_renforts_jade(fiche, brut, base, contexte),
    "libres_gv": lambda fiche, brut, base, contexte: _traiter_libres_gv(fiche, brut, base, contexte),
}


# ---------------------------------------------------------------------------
# Exécution des règles du gabarit
# ---------------------------------------------------------------------------


def _cote_deja_lue(fiche: FicheExtraite, cible: str) -> bool:
    """Une cote déjà remplie n'est jamais ré-écrite : les règles v1 (lignes
    « label : valeur ») et v2 (grille à colonnes) coexistent ainsi sans
    conflit, quel que soit l'ordre d'exécution du gabarit."""
    try:
        _, jeu, colonne = cible.split(".")
    except ValueError:
        return False
    cotes = next((c for c in fiche.cotes if c.jeu == jeu), None)
    return cotes is not None and getattr(cotes, colonne, None) is not None


# Groupes de décomposition : quand une règle antérieure a déjà rempli le
# groupe (ex. la grille du document réel avant la décomposition v1), la
# règle suivante s'efface — « premier lu gagne », jamais de doublon.
_GROUPE_DU_TRAITEMENT: dict[str, str] = {
    "galons": "galons",
    "galons_grille": "galons",
    "finitions": "finitions",
    "finitions_grille": "finitions",
    "epaisseurs": "materiaux",
    "epaisseurs_grille": "materiaux",
    # options : pas de garde de groupe — le document réel imprime ses options
    # en deux blocs distincts (colonne centrale + velcro/retenue/anti-UV) et
    # chaque traitement se dédoublonne par code, jamais par groupe entier.
    "jonctions": "jonctions",
    "jonctions_grille": "jonctions",
    "renforts_note": "renforts",
    # Format atelier : garde d'idempotence par groupe (une seule exécution).
    "galons_bdf": "galons",
    "finitions_jade": "finitions",
    "renforts_jade": "renforts",
}


def _executer_regle(fiche: FicheExtraite, regle, pages: list[PageAnalysee]) -> None:
    """Lit un champ selon sa règle ; ne lève jamais : un champ non trouvé est
    journalisé avec sa conséquence (absent du rapport, à relire)."""
    cible = regle.cible
    traitement = regle.traitement
    groupe = _GROUPE_DU_TRAITEMENT.get(traitement or "")
    if groupe and getattr(fiche, groupe):
        return
    brut: str | None = None
    mots_valeur: list[Mot] = []
    numero_page = 0
    borne_naturelle = True
    ancre_trouvee = False
    if cible.startswith("cotes.") and traitement is None:
        if _cote_deja_lue(fiche, cible):
            return
        brut, mots_valeur, borne_naturelle = _lire_cote(pages, regle.ancres)
    else:
        for ancre in regle.ancres:
            trouve = _trouver_ligne(pages, normaliser_terme(ancre))
            if trouve is None:
                continue
            ancre_trouvee = True
            page, ligne, indice = trouve
            nb_mots_ancre = len(normaliser_terme(ancre).split())
            mots_valeur, borne_naturelle = _mots_valeur(
                ligne, indice, list(regle.stop) + list(regle.ancres), nb_mots_ancre
            )
            brut = " ".join(mot.texte for mot in mots_valeur).strip() if mots_valeur else ""
            if brut:
                numero_page = page.numero
                break
    if brut is None or brut.strip() == "":
        if traitement == "designation" and ancre_trouvee and regle.ancres:
            # Voie dégradée du gabarit génois : l'ancre du titre EST le type
            # (« Voile de génois » → type Génois). Elle n'est ouverte que si
            # l'ancre a réellement été lue — sinon le libellé d'ancre d'une
            # fiche qui n'en contient pas deviendrait une valeur (RG6).
            _executer_regle_traitement(fiche, regle, "", 0, [])
        else:
            LOGGER.debug("Champ non lu : %s (ancres %s) — absent du rapport, à relire.", cible, regle.ancres)
        return
    _construire_et_ranger(fiche, regle, brut.strip(), numero_page, mots_valeur, borne_naturelle, pages)


def _construire_et_ranger(
    fiche: FicheExtraite,
    regle,
    brut: str,
    numero_page: int,
    mots_valeur: list[Mot],
    borne_naturelle: bool = True,
    pages: list[PageAnalysee] | None = None,
) -> None:
    """Construit le ChampExtrait d'une lecture réussie puis range la valeur
    (cote, traitement structuré, ou champ simple de tête)."""
    cible = regle.cible
    traitement = regle.traitement
    normalise: object = brut
    if cible.startswith("cotes.") or (traitement is None and regle.type in CONVERTISSEURS):
        convertisseur = CONVERTISSEURS.get(regle.type)
        if convertisseur is not None:
            normalise = convertisseur(brut)
    confiance = _confiance_type(regle.type if traitement is None else "texte", brut, normalise)
    if confiance == CONFIANCE_LUE and borne_naturelle and (
        regle.type == "texte" or _consommation_totale(regle.type, brut)
    ):
        confiance = CONFIANCE_CERTAIN  # lecture déterministe, sans ambiguïté
    champ = ChampExtrait(
        champ=cible,
        valeur_brute=brut or None,
        valeur_normalisee=None if normalise is None else str(normalise),
        methode="gabarit",
        confiance=confiance,
        page=numero_page,
        zone=_zone_des_mots(mots_valeur),
        table_cible="fiche_cotes" if cible.startswith("cotes.") else "fiche",
        colonne_cible=cible.split(".")[-1],
    )
    if cible.startswith("cotes."):
        if traitement is not None:
            # Cote visée par un traitement page-aware (grille du document
            # réel) : le traitement lit la page et pose ses propres traces.
            fiche.champs.append(champ)
            fonction = TRAITEMENTS.get(traitement)
            if fonction is None:
                LOGGER.warning("Traitement inconnu « %s » (cote %s).", traitement, cible)
                return
            contexte = {
                "ancre": normaliser_terme(regle.ancres[0]),
                "fallback": regle.ancres[0],
                "pages": pages,
                "mots": list(mots_valeur),
            }
            fonction(fiche, brut, champ, contexte)
            return
        if normalise is None:
            # L'ancre a été trouvée mais la cellule n'est pas une cote
            # (en-tête de colonne relu, « ~ »…) : rien n'est lu, aucune trace
            # fantôme à confiance dégradée n'est émise (RG6).
            LOGGER.debug("Cote non convertible : %s (lu « %s ») — absente du rapport.", cible, brut)
            return
        fiche.champs.append(champ)
        _ranger_cote(fiche, cible, normalise, champ)
        return
    fiche.champs.append(champ)
    if traitement is not None:
        fonction = TRAITEMENTS.get(traitement)
        if fonction is None:
            LOGGER.warning("Traitement inconnu « %s » (champ %s) : valeur conservée en tête de fiche.", traitement, cible)
            return
        contexte = {
            "ancre": normaliser_terme(regle.ancres[0]),
            "fallback": regle.ancres[0],
            "pages": pages,
            "mots": list(mots_valeur),
        }
        fonction(fiche, brut, champ, contexte)
        return
    if cible.startswith("fiche."):
        # champ simple de tête (code, atelier, quantité, tissu…)
        attribut = cible.split(".", 1)[1]
        if normalise is not None and hasattr(fiche, attribut):
            # « premier lu gagne » : quand deux règles visent le même attribut
            # (v1 lignes « label : valeur » + v2 document réel), la première
            # lecture effective n'est jamais écrasée par la seconde.
            if getattr(fiche, attribut) is None:
                setattr(fiche, attribut, normalise)
                champ.valeur_normalisee = str(normalise)


def _executer_regle_traitement(fiche: FicheExtraite, regle, brut: str, numero_page: int, mots_valeur: list[Mot]) -> None:
    """Dispatch d'un traitement sur une valeur déjà bornée (utilisé aussi par
    la voie dégradée « désignation posée par l'ancre » du gabarit génois)."""
    _construire_et_ranger(fiche, regle, brut, numero_page, mots_valeur)


def _lire_cote(pages: list[PageAnalysee], ancres: list[str]) -> tuple[str | None, list[Mot], bool]:
    """Cote nommée : d'abord dans les tableaux réglés (cellule entière, borne
    toujours naturelle), sinon en ligne (borne du _mots_valeur)."""
    trouve = _valeur_de_tableau(pages, ancres)
    if trouve is not None:
        brut, mots = trouve
        return brut, mots, True
    for ancre in ancres:
        position = _trouver_ligne(pages, normaliser_terme(ancre))
        if position is None:
            continue
        page, ligne, indice = position
        mots, naturelle = _mots_valeur(ligne, indice, [])
        if mots:
            return " ".join(mot.texte for mot in mots), mots, naturelle
    return None, [], True


def _ranger_cote(fiche: FicheExtraite, cible: str, valeur: object, champ: ChampExtrait) -> None:
    """Range une cote nommée ; le JEU vient de la cible (« cotes.dessin.* » ou
    « cotes.finie.* ») — le gabarit 7792 ne lit que « finies », la fiche
    n'imprimant que le jeu « Cotes — Mesures Finies »."""
    _, jeu, colonne = cible.split(".")
    cotes = next((c for c in fiche.cotes if c.jeu == jeu), None)
    if cotes is None:
        cotes = Cotes(jeu=jeu)
        fiche.cotes.append(cotes)
    setattr(cotes, colonne, valeur)
    champ.valeur_normalisee = None if valeur is None else str(valeur)
    cotes.champs.append(_reprendre_base(fiche, champ))


def _collecter_libres(fiche: FicheExtraite, pages: list[PageAnalysee], ancres_consommees: set[str], vocabulaire: set[str]) -> None:
    """RG6 : paires « libellé : valeur » non consommées dont le libellé touche
    le vocabulaire connu → mesures libres (capées pour éviter les vidanges)."""
    for page in pages:
        for ligne in page.lignes:
            for indice, mot in enumerate(ligne.mots[:-1]):
                if mot.texte != ":" or indice == 0:
                    continue
                libelle = " ".join(m.texte for m in ligne.mots[max(0, indice - 3) : indice])
                valeur_mots, _borne = _mots_valeur(ligne, indice - 1, [])
                valeur = " ".join(m.texte for m in valeur_mots).strip()
                lib_norm = normaliser_terme(libelle)
                if not valeur or not lib_norm:
                    continue
                deja = any(lib_norm in normaliser_terme(ancre) or normaliser_terme(ancre) in lib_norm for ancre in ancres_consommees)
                connu = any(mot_cle in lib_norm for mot_cle in vocabulaire if len(mot_cle) >= 4)
                if deja or not connu:
                    continue
                decimal = norm.extraire_decimal(valeur)
                fiche.mesures_libres.append(
                    ChampExtrait(
                        champ=f"libre.{re.sub(r'[^a-z0-9]+', '_', lib_norm).strip('_')}",
                        valeur_brute=f"{libelle} : {valeur}",
                        valeur_normalisee=None if decimal is None else str(decimal),
                        methode="gabarit",
                        confiance=0.6,
                        page=page.numero,
                        zone=_zone_des_mots(valeur_mots),
                    )
                )


def extraire_fiche(
    chemin: Path,
    gabarits: list[GabaritDef] | None = None,
    gabarit_code: str | None = None,
    vocabulaire_libres: set[str] | None = None,
) -> FicheExtraite:
    """Extrait une fiche du PDF par gabarit ; lève GabaritInconnu si besoin.

    ``vocabulaire_libres`` : mots-clés autorisant la conservation RG6 (défaut :
    ancres de tous les gabarits). L'appelant peut y ajouter les termes du
    lexique du dépôt (:mod:`seamtech_search.detection_fiches`).
    """
    pages = analyser_pdf(chemin)
    texte = texte_normalise(pages)
    fiche = FicheExtraite(fichier_pdf=str(chemin))
    if gabarit_code is not None:
        if not gabarits:
            raise ValueError("gabarit_code exigé : fournir aussi la liste des gabarits.")
        gabarit = next((g for g in gabarits if g.code == gabarit_code), None)
        if gabarit is None:
            raise GabaritInconnu(f"Gabarit « {gabarit_code} » absent du registre.")
    else:
        gabarit = detecter_gabarit(texte, gabarits or [])
    fiche.gabarit_code = gabarit.code
    fiche.gabarit_version = gabarit.version
    consommees: set[str] = set()
    for regle in gabarit.champs:
        consommees.update(regle.ancres)
        try:
            _executer_regle(fiche, regle, pages)
        except Exception:
            LOGGER.exception("Échec de la règle %s — champ ignoré, à relire (conséquence : champ absent).", regle.cible)
    _collecter_libres(fiche, pages, consommees, vocabulaire_libres or {ancre for g in (gabarits or [gabarit]) for ancre in g.ancres_detection} | {ancre for r in gabarit.champs for ancre in r.ancres})
    fiche.anomalies = evaluer_anomalies(fiche)
    return fiche


def extraire_avec_filet(chemin: Path, gabarits: list[GabaritDef], vocabulaire_libres: set[str] | None = None) -> FicheExtraite:
    """Extraction avec voie dégradée RG6 : gabarit inconnu → mesures libres.

    La fiche dégradée ne prétend rien structurer : pas de gabarit, pas de
    statut optimiste — l'écriture en base la rangera explicitement en
    reprise complète (à relire intégralement).
    """
    try:
        return extraire_fiche(chemin, gabarits=gabarits, vocabulaire_libres=vocabulaire_libres)
    except GabaritInconnu as erreur:
        LOGGER.warning("%s (conséquence : fiche conservée en mesures libres, voie « reprise complète » §10.3).", erreur)
        pages = analyser_pdf(chemin)
        from seamtech_search.fiches.modeles import Anomalie

        fiche = FicheExtraite(fichier_pdf=str(chemin))
        vocabulaire = vocabulaire_libres
        if vocabulaire is None:
            # RG6 : on retient au moins tout le vocabulaire connu du registre
            vocabulaire = {ancre for gabarit in gabarits for ancre in gabarit.ancres_detection}
            vocabulaire |= {ancre for gabarit in gabarits for regle in gabarit.champs for ancre in regle.ancres}
        _collecter_libres(fiche, pages, set(), vocabulaire)
        fiche.anomalies.append(Anomalie(code="gabarit_inconnu", gravite="forte", message=str(erreur)))
        return fiche
