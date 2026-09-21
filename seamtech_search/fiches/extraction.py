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
    ("génois", "Génois", "interface"),
    ("genoa", "Génois", "interface"),
    ("grand-voile", "Grand-voile", "interface"),
    ("grand voile", "Grand-voile", "interface"),
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


def _mots_valeur(ligne: Ligne, debut_ancre: int, ancres_stop: list[str]) -> tuple[list[Mot], bool]:
    """Mots de la valeur : après le « : » qui suit l'ancre, jusqu'au prochain
    libellé (ancres_stop) ou à une rupture de colonne marquée.

    Retourne (mots, borne_naturelle) : la borne est « naturelle » quand la
    lecture s'est arrêtée en fin de ligne ou sur un changement de colonne
    (valeur intégralement délimitée) ; elle est « tronquée » quand un libellé
    stop a coupé la valeur (ambiguïté résiduelle → confiance réduite)."""
    mots = ligne.mots
    # saute l'ancre puis cherche les deux-points qui la suivent
    deux_points = None
    for position in range(debut_ancre + 1, len(mots)):
        if mots[position].texte == ":":
            deux_points = position
            break
        if mots[position].texte not in ("/", "-") and position - debut_ancre > 2:
            break
    if deux_points is None:
        # pas de « label : valeur » : la valeur est le reste de la ligne
        # (cas du titre « Fiche de fabrication "Voile de portant" »)
        debut = debut_ancre + 1
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
    if brut.strip():
        fiche.commande_numero = brut.strip()
        base.valeur_normalisee = fiche.commande_numero
        base.confiance = max(base.confiance, CONFIANCE_CERTAIN)


def _traiter_dessinateur(fiche: FicheExtraite, brut: str, base: ChampExtrait) -> None:
    """« Yann · 6 mars 2026 » → dessinateur + date ISO."""
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
    valeur = re.sub(r",?\s*non-?sangl\w*", "", droite, flags=re.I).strip().rstrip(",;").strip()
    if not valeur:
        valeur = gauche.strip()
    oeillet = None
    sangle = None
    trouve = re.search(r"œillet\s+(\S+)", droite, re.I)
    if trouve:
        oeillet = trouve.group(1).rstrip(",;")
    if re.search(r"non-?sangl", droite, re.I):
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


TRAITEMENTS = {
    "titre": lambda fiche, brut, base, contexte: _traiter_titre(fiche, brut, base),
    "designation": lambda fiche, brut, base, contexte: _traiter_designation(fiche, brut, base, contexte.get("fallback")),
    "support": lambda fiche, brut, base, contexte: _traiter_support(fiche, brut, base),
    "client": lambda fiche, brut, base, contexte: _traiter_client(fiche, brut, base),
    "commande": lambda fiche, brut, base, contexte: _traiter_commande(fiche, brut, base),
    "dessinateur": lambda fiche, brut, base, contexte: _traiter_dessinateur(fiche, brut, base),
    "fichier_source": lambda fiche, brut, base, contexte: _traiter_fichier_source(fiche, brut, base),
    "montage": lambda fiche, brut, base, contexte: _traiter_montage(fiche, brut, base),
    "galons": lambda fiche, brut, base, contexte: _traiter_galons(fiche, brut, base, contexte["ancre"]),
    "jonctions": lambda fiche, brut, base, contexte: _traiter_jonctions(fiche, brut, base, contexte["ancre"]),
    "finitions": lambda fiche, brut, base, contexte: _traiter_finitions(fiche, brut, base),
    "options": lambda fiche, brut, base, contexte: _traiter_options(fiche, brut, base),
    "epaisseurs": lambda fiche, brut, base, contexte: _traiter_epaisseurs(fiche, brut, base),
    "tissu_principal": lambda fiche, brut, base, contexte: _traiter_tissu_principal(fiche, brut, base, contexte),
    "notes": lambda fiche, brut, base, contexte: _traiter_notes(fiche, brut, base),
}


# ---------------------------------------------------------------------------
# Exécution des règles du gabarit
# ---------------------------------------------------------------------------


def _executer_regle(fiche: FicheExtraite, regle, pages: list[PageAnalysee]) -> None:
    """Lit un champ selon sa règle ; ne lève jamais : un champ non trouvé est
    journalisé avec sa conséquence (absent du rapport, à relire)."""
    cible = regle.cible
    traitement = regle.traitement
    brut: str | None = None
    mots_valeur: list[Mot] = []
    numero_page = 0
    borne_naturelle = True
    if cible.startswith("cotes."):
        brut, mots_valeur, borne_naturelle = _lire_cote(pages, regle.ancres)
    else:
        for ancre in regle.ancres:
            trouve = _trouver_ligne(pages, normaliser_terme(ancre))
            if trouve is None:
                continue
            page, ligne, indice = trouve
            mots_valeur, borne_naturelle = _mots_valeur(ligne, indice, list(regle.stop) + list(regle.ancres))
            brut = " ".join(mot.texte for mot in mots_valeur).strip() if mots_valeur else ""
            if brut:
                break
            numero_page = page.numero
            if brut:
                break
    if brut is None or brut.strip() == "":
        if traitement == "designation" and regle.ancres:
            _executer_regle_traitement(fiche, regle, "", 0, [])
        else:
            LOGGER.debug("Champ non lu : %s (ancres %s) — absent du rapport, à relire.", cible, regle.ancres)
        return
    _construire_et_ranger(fiche, regle, brut.strip(), numero_page, mots_valeur, borne_naturelle)


def _construire_et_ranger(
    fiche: FicheExtraite,
    regle,
    brut: str,
    numero_page: int,
    mots_valeur: list[Mot],
    borne_naturelle: bool = True,
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
    fiche.champs.append(champ)
    if cible.startswith("cotes."):
        _ranger_cote(fiche, cible, normalise, champ)
        return
    if traitement is not None:
        fonction = TRAITEMENTS.get(traitement)
        if fonction is None:
            LOGGER.warning("Traitement inconnu « %s » (champ %s) : valeur conservée en tête de fiche.", traitement, cible)
            return
        contexte = {"ancre": normaliser_terme(regle.ancres[0]), "fallback": regle.ancres[0]}
        fonction(fiche, brut, champ, contexte)
        return
    if cible.startswith("fiche."):
        # champ simple de tête (code, atelier, quantité, tissu…)
        attribut = cible.split(".", 1)[1]
        if normalise is not None and hasattr(fiche, attribut):
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
