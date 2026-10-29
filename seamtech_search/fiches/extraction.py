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

# Forme d'un code d'affaire d'accessoire, mesurée sur les formulaires réels :
# « ST64 », « ST105 », « ST96 », « ST 074b », « ST 256b ». Un code porte au
# plus quatre lettres, au plus cinq chiffres, au plus une lettre de version.
_RE_CODE_ACCESSOIRE = re.compile(r"^[A-Za-z]{0,4}\s?\d{1,5}[A-Za-z]?$")

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
    # Mesuré le 2026-10-29 sur `/WESTERLY PENTLAND ST660B/ST 660b_ffab.pdf` :
    # « Artimon classique semi-latté, gamme Ocean, pour … ». L'artimon est une
    # voile d'interface (mât d'artimon) ; sans cette entrée la fiche sortait
    # avec `type_voile_libelle = None` alors que la désignation était lue.
    ("artimon", "Artimon", "interface"),
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


@dataclass(frozen=True)
class CandidatFiche:
    """Un PDF analysé une seule fois : gabarit détecté, score et extraction.

    Le scanner de dossier compare plusieurs PDF d'un même dossier sans
    rouvrir chaque fichier pour la détection puis pour l'extraction.
    """

    gabarit: GabaritDef
    score_detection: int
    fiche: FicheExtraite


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


#: Guillemets et apostrophes typographiques retirés en tête/queue d'un mot
#: AVANT comparaison d'ancre. Un libellé d'export tableur arrive souvent
#: encadré de guillemets (« "Grand-voile" »), et l'ancre du gabarit
#: (« grand-voile ») ne le reconnaissait alors jamais : la règle entière était
#: silencieusement ignorée, code de fiche compris.
_GUILLEMETS = "\"'«»“”‘’"


def _jeton_comparable(mot_normalise: str) -> str:
    """Mot normalisé, sans guillemets ni astérisque collés.

    L'astérisque est un repère typographique des formulaires imprimés
    (« Longueur du lazy* », « Hauteur du capot* », « Longueur du sac* ») : il ne
    fait pas partie du mot. Sans ce retrait, une ancre « longueur du lazy » ne
    trouvait PAS la ligne réelle « longueur du lazy* 3.32 m » et la lecture
    retombait sur la ligne suivante de la liste des ancres — « longueur du
    gousset de latte 3.22m » — soit la longueur du GOUSSET présentée comme
    longueur du Lazy Bag (mesuré sur `/ACCESSOIRES 2024/LAZY BAG 2P ST64/LB 2
    parties.pdf` : 3,22 m au lieu de 3,32 m).
    """
    return mot_normalise.strip(_GUILLEMETS).strip("*")


def _indice_ancre(ligne: Ligne, ancre_norm: str) -> int | None:
    """Premier mot de la ligne où commence la séquence d'ancres (ou None)."""
    jetons = ancre_norm.split()
    mots_norm = [_jeton_comparable(mot.normalise) for mot in ligne.mots]
    for depart in range(len(mots_norm) - len(jetons) + 1):
        if mots_norm[depart : depart + len(jetons)] == jetons:
            return depart
    return None


def _sauter_annotation_parenthesee(mots: list[Mot], debut: int) -> int:
    """Déplace le début de la valeur après une annotation ENTRE PARENTHÈSES
    collée au libellé, quand cette annotation porte les deux-points.

    Mesuré le 2026-10-29 sur `/WESTERLY PENTLAND ST660B/ST 660b_ffab.pdf`
    (idem `/YACHET CLASSIC ST508/ST 508_ffab.pdf`) : la ligne imprimée est

        ``Tissu(s) (Pas de carbon): Newport 9.45 AP Blanc - White  Quantité : 1``

    et le PDF ne livre pas de mot « : » isolé : les deux-points sont soudés au
    mot ``carbon):``. Sans ce saut, la lecture s'arrêtait au changement de
    colonne juste après la parenthèse et le tissu principal valait
    ``(Pas de carbon):`` — un libellé de formulaire présenté comme matière.

    Le saut est STRICT : il ne s'applique que si le groupe parenthésé se ferme
    sur un mot terminé par ``):`` (ou suivi d'un mot ``:``). Une parenthèse qui
    EST la valeur (« Type gousset (voile) ») n'est donc jamais avalée.
    """
    if debut >= len(mots) or not mots[debut].texte.startswith("("):
        return debut
    for position in range(debut, len(mots)):
        texte = mots[position].texte
        if texte.endswith("):"):
            return position + 1
        if texte.endswith(")") and position + 1 < len(mots) and mots[position + 1].texte == ":":
            return position + 2
        if not texte.endswith(")") and ")" not in texte and position - debut > 8:
            return debut  # parenthèse non refermée à portée raisonnable
    return debut


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
    fin_ancre = _sauter_annotation_parenthesee(mots, fin_ancre)
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


def _traiter_code_accessoire(
    fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict | None = None
) -> None:
    """Code d'une fiche d'accessoire : les DEUX formes imprimées mesurées.

    Formulaire « fourreau de gse » / « Sac de Pont » : ``Commande ST96`` /
    ``Commande ST 074b`` — le code suit le libellé. Formulaire « Lazy Bag » :
    le libellé ``Commande`` est imprimé SEUL (valeur vide) et le code occupe la
    ligne NUE juste sous le titre (``ST64``, ``ST105``). Aucune autre ligne
    n'est prise pour un code : une valeur qui n'a pas la forme d'un code
    (date, nom de bateau, phrase) est refusée plutôt que tronquée.
    """
    pages = (contexte or {}).get("pages") or []
    for page in pages[:2]:
        lignes = [ligne for ligne in page.lignes if ligne.mots]
        for indice, ligne in enumerate(lignes):
            texte = " ".join(mot.texte for mot in ligne.mots).strip()
            if texte.lower().startswith("commande"):
                valeur = texte[len("commande") :].strip(" :;-–")
                if valeur and _RE_CODE_ACCESSOIRE.match(valeur):
                    _poser_code_accessoire(fiche, base, valeur, ligne.mots[1:])
                    return
                continue
            if not _RE_CODE_ACCESSOIRE.match(texte):
                continue
            precedente = lignes[indice - 1].normalise if indice else ""
            if precedente.startswith("fiche de ") and not precedente.startswith("fiche de fabrication"):
                _poser_code_accessoire(fiche, base, texte, ligne.mots)
                return


def _poser_code_accessoire(
    fiche: FicheExtraite, base: ChampExtrait, valeur: str, mots: list[Mot]
) -> None:
    code = _normaliser_code_ffab(valeur)
    base.valeur_brute = valeur
    base.valeur_normalisee = code
    base.confiance = max(base.confiance, CONFIANCE_CERTAIN)
    base.zone = _zone_des_mots(mots)
    if fiche.code is None:
        fiche.code = code


def _traiter_titre_accessoire(
    fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict | None = None
) -> None:
    """« Fiche de Lazy Bag » → type d'accessoire « Lazy Bag ».

    Le titre EST l'ancre : la valeur n'est pas à droite du libellé mais après
    « Fiche de ». « Fiche de fabrication … » (fiches de voiles) est exclu
    explicitement : ce gabarit ne doit jamais lire un titre de voile.
    """
    pages = (contexte or {}).get("pages") or []
    for page in pages[:2]:
        for ligne in page.lignes:
            normalise = ligne.normalise
            if not normalise.startswith("fiche de ") or normalise.startswith("fiche de fabrication"):
                continue
            mots_titre = ligne.mots[2:]
            valeur = " ".join(mot.texte for mot in mots_titre).strip()
            if not valeur:
                continue
            valeur = valeur[0].upper() + valeur[1:]
            base.valeur_brute = valeur
            base.valeur_normalisee = valeur
            base.confiance = max(base.confiance, CONFIANCE_CERTAIN)
            base.zone = _zone_des_mots(mots_titre)
            if fiche.titre is None:
                fiche.titre = valeur
            return


def _traiter_dimension_nommee(
    fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict | None = None
) -> None:
    """Ne garde que la MESURE de tête d'une ligne de dimension nommée.

    Mesuré sur les formulaires réels : « Longueur du gousset de latte 3.22m -
    Latte jonc 12mm avec manchons et embouts ». La valeur brute contient DEUX
    mesures (3,22 m puis 12 mm) ; la projection d'accessoire range la seconde
    en « largeur_m » — soit une cote de largeur inventée. On ne conserve donc
    que ``<nombre> <unité>`` de tête ; sans unité explicite, rien n'est lu.
    """
    trouve = re.match(r"\s*(\d+(?:[.,]\d+)?\s*(?:mm|cm|m)\b)", brut, re.I)
    if trouve is None:
        return
    valeur = re.sub(r"\s+", " ", trouve.group(1)).strip()
    base.valeur_brute = brut
    base.valeur_normalisee = valeur
    base.confiance = max(base.confiance, CONFIANCE_CERTAIN)


def _traiter_logo_accessoire(
    fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict | None = None
) -> None:
    """« Logo Pas de logo » / « Logo sans » / « SANS LOGO » → ``sans``.

    Trois formes imprimées mesurées ; « avec logo » n'a pas été observé, il
    n'est donc pas deviné — toute autre forme laisse le champ vide (RG6).
    """
    candidats = [brut]
    # « Observations/Notes SANS LOGO » (mesuré sur les deux fiches Lazy Bag) :
    # le mot « logo » termine la ligne, aucun mot de valeur ne le suit. On
    # relit alors les lignes de la page au lieu de laisser le champ vide.
    for page in ((contexte or {}).get("pages") or [])[:2]:
        for ligne in page.lignes:
            if "logo" in ligne.normalise:
                candidats.append(" ".join(mot.texte for mot in ligne.mots))
    for candidat in candidats:
        normalise = normaliser_terme(candidat)
        if "sans" in normalise or re.search(r"\bpas de\b", normalise):
            base.valeur_brute = candidat.strip() or base.valeur_brute
            base.valeur_normalisee = "sans"
            base.confiance = max(base.confiance, CONFIANCE_CERTAIN)
            fiche.logo = fiche.logo or "sans"
            return


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
    # Forme FFab : « Tissu(s) (précision imprimée) : matière… » — la
    # parenthèse appartient au libellé, pas à la désignation du matériau.
    valeur = re.sub(r"^\([^)]*\)\s*:\s*", "", valeur)
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
    # Aucun nom de fichier avec extension n'a été lu : ne pas laisser la
    # trace générique de la règle prétendre le contraire.
    if base in fiche.champs:
        fiche.champs.remove(base)


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
            # Une seule ligne fiche_galon par bande (UNIQUE id_fiche+bande) :
            # les bandes de force multiples d'un même bord se rattachent à la
            # même ligne — leurs traces distinctes (galon.<bande>.<type>)
            # portent le détail, jamais perdu.
            galon = next((g for g in fiche.galons if g.bande == bande), None)
            if galon is None:
                galon = Galon(bande=bande)  # largeur/grammage/couleur : absents sans unité explicite
                fiche.galons.append(galon)
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
        trouve_fil = re.search(r"\bfil\s+v?(\d+)\b", texte, re.I)
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


# ---------------------------------------------------------------------------
# Traitements « FFab grand-voile » (Lot 10). La famille observée en
# validation réelle a la parenté Portant (ligne de titre complète, ligne
# Fichier) mais une grille « Cotes (tissu) » sans libellé de rangée. Le code
# n'est jamais cherché par un motif libre dans tout le document : il doit
# terminer la ligne de titre ou être corroboré par la ligne Fichier du PDF.
# ---------------------------------------------------------------------------

#: Référence imprimée par l'atelier. Suffixe admis : une lettre SEULE
#: (« ST 660b », « ST449A ») ou une lettre suivie de 1-2 chiffres de révision
#: (« ST 508V2 », mesuré sur `/YACHET CLASSIC ST508/ST 508V2_ffab.pdf`).
#: Sans ce second chiffre, le `\b` qui suit échouait sur le « V » de « V2 » :
#: la fiche V2 sortait SANS CODE (donc « non viable ») alors que le document
#: porte « … ST 508V2 » et « Fichier ST 508V2 (commande ST 508V2) ».
_MOTIF_CODE_FFAB = r"(?:[A-Za-z]{1,4}\s+\d{2,5}[A-Za-z]?(?:\d{1,2})?|\d{3,5}-[A-Za-z]{2,4})"
_RE_CODE_FFAB_FIN = re.compile(rf"(?P<code>{_MOTIF_CODE_FFAB})\s*$")
_RE_CODE_FFAB_FICHIER = re.compile(rf"\bFichier\s+(?P<code>{_MOTIF_CODE_FFAB})(?:\.(?:xlsm|xlsx|pdf))?\b", re.I)
_RE_CODE_FFAB_COMMANDE = re.compile(rf"\(\s*commande\s+(?P<code>{_MOTIF_CODE_FFAB})\s*\)", re.I)
_RE_COMMANDE_TITRE_FFAB = re.compile(r"\(\s*commande\s+#\s*([^)]+?)\s*\)", re.I)
#: Référence d'affaire entre parenthèses en fin de ligne de titre, forme
#: « (ST75 - TOAOWEUYQ) » / « (AB 1234 - XYZ) » : ce n'est ni le client ni un
#: détail de chantier, c'est le numéro de commande externe. Sans cette règle,
#: la parenthèse restait collée au nom du client et le référentiel bateau ne
#: pouvait plus rattacher la fiche.
_RE_REFERENCE_TITRE_FFAB = re.compile(
    r"\(\s*(?P<prefixe>[A-Za-z]{1,4}\s*\d{2,5})\s*-\s*(?P<suffixe>[A-Za-z0-9]{3,})\s*\)\s*$"
)


def _normaliser_code_ffab(code: str) -> str:
    return re.sub(r"\s+", " ", code.strip().upper())


def _code_corroboré_ligne_fichier(pages: list[PageAnalysee]) -> tuple[str | None, list[Mot], int]:
    """Code lu sur une ligne « Fichier … (commande …) » du document.

    Si les deux mentions existent, elles doivent concorder. À défaut, aucun
    code n'est proposé par cette voie : mieux vaut une reprise complète
    qu'une référence choisie arbitrairement entre deux lectures.
    """
    for page in pages[:2]:
        for ligne in page.lignes:
            texte = " ".join(mot.texte for mot in ligne.mots)
            if not re.search(r"\bfichier\b", texte, re.I):
                continue
            dans_fichier = _RE_CODE_FFAB_FICHIER.search(texte)
            dans_commande = _RE_CODE_FFAB_COMMANDE.search(texte)
            code_fichier = _normaliser_code_ffab(dans_fichier.group("code")) if dans_fichier else None
            code_commande = _normaliser_code_ffab(dans_commande.group("code")) if dans_commande else None
            if code_fichier and code_commande and code_fichier != code_commande:
                continue
            code = code_fichier or code_commande
            if code:
                return code, _localiser_sequence(ligne.mots, normaliser_terme(code).split()) or list(ligne.mots), page.numero
    return None, [], 0


def _reprendre_code_ffab_depuis_ligne_fichier(
    fiche: FicheExtraite, pages: list[PageAnalysee]
) -> bool:
    """Pose le code FFAB lu dans le document quand le titre n'a pas pu
    être décomposé.

    La source reste la ligne « Fichier … » du PDF (jamais le nom du
    fichier PDF) et la valeur reçoit sa propre trace localisée. Les
    autres champs du titre restent absents : ils seront relus par
    l'approbateur, jamais inventés pour rendre la fiche présentable.
    """
    if fiche.code:
        return False
    code, mots_code, page_code = _code_corroboré_ligne_fichier(pages)
    if not code:
        return False
    fiche.code = code
    fiche.champs.append(
        ChampExtrait(
            champ="fiche.code",
            valeur_brute=code,
            valeur_normalisee=code,
            methode="gabarit",
            confiance=CONFIANCE_CERTAIN,
            page=page_code,
            zone=_zone_des_mots(mots_code),
            table_cible="fiche",
            colonne_cible="code",
        )
    )
    return True


def _indice_sequence_mots(mots: list[Mot], jetons: list[str]) -> int | None:
    """Premier indice où les mots normalisés forment exactement la séquence."""
    if not jetons or len(mots) < len(jetons):
        return None
    normes = [mot.normalise for mot in mots]
    for depart in range(len(normes) - len(jetons) + 1):
        if normes[depart : depart + len(jetons)] == jetons:
            return depart
    return None


#: Bornes de la fenêtre multi-lignes du titre FFab. Un export tableur met
#: l'en-tête (« Fiche de fabrication "Grand-voile" »), la ligne d'atelier et
#: la ligne de désignation dans des CELLULES distinctes : chaque cellule est
#: une ligne physique. Quatre lignes ne suffisaient donc pas dès qu'une
#: cellule supplémentaire (tissu, quantité) s'intercalait, et le titre
#: n'était jamais analysé — d'où des fiches sans client ni bateau.
_FENETRE_TITRE_FFAB_LIGNES = 8
_FENETRE_TITRE_FFAB_ECART_PT = 60.0

#: Familles de voiles servant à ROGNER une désignation polluée par une
#: cellule voisine lue sur la même ligne physique (la désignation commence
#: au dernier de ces mots, jamais avant).
_FAMILLES_VOILE_TITRE = re.compile(
    r"\b(grand[-\s]?voiles?|voiles?\s+d['’]?avants?|voiles?\s+avants?|genois|g[eé]nois|spis?|spinnakers?|gennakers?|"
    r"code[-\s]?0|trinquettes?|solents?|tourmentins?|asym[eé]triques?|sym[eé]triques?)\b",
    re.I,
)


def _rogner_designation(designation: str) -> str:
    """Retire le bruit lu avant le nom de la voile (« Fichier …, dessiné par… »).

    Une fenêtre multi-lignes peut préfixer la désignation par une cellule
    voisine (« Fichier ZZ 075a dessiné par… »). La désignation réelle
    commence au mot de famille de voile : on repart de sa DERNIÈRE
    occurrence, jamais d'une invention de valeur.
    """
    occurrences = list(_FAMILLES_VOILE_TITRE.finditer(designation))
    if not occurrences:
        return designation.strip(" ,;")
    return designation[occurrences[-1].start() :].strip(" ,;")


def _sequences_titre_ffab(pages: list[PageAnalysee]) -> list[tuple[list[Mot], int]]:
    """Séquences candidates pour le titre FFAB.

    Les exports tableur morcellent parfois un titre logiquement unique
    en plusieurs lignes physiques (retour à la ligne de cellule, fontes
    ou blocs voisins). On essaie donc d'abord chaque ligne seule — le
    comportement historique — puis des fenêtres de deux à huit lignes
    physiquement voisines, ramenées au début de « Fiche de fabrication ».
    Une fenêtre n'est candidate que si les repères du titre apparaissent
    dans l'ordre ; la décomposition stricte décide ensuite.
    """
    sequences: list[tuple[list[Mot], int]] = []
    for page in pages[:2]:
        for ligne in page.lignes:
            normalise = ligne.normalise
            if "fiche de fabrication" in normalise and "grand-voile" in normalise and " pour " in normalise:
                sequences.append((list(ligne.mots), page.numero))

    for page in pages[:2]:
        lignes = [ligne for ligne in page.lignes if ligne.mots]
        for debut in range(len(lignes)):
            mots: list[Mot] = []
            for fin in range(debut, min(len(lignes), debut + _FENETRE_TITRE_FFAB_LIGNES)):
                if fin > debut:
                    haut_precedent = min(mot.haut for mot in lignes[fin - 1].mots)
                    haut_courant = min(mot.haut for mot in lignes[fin].mots)
                    if haut_courant - haut_precedent > _FENETRE_TITRE_FFAB_ECART_PT:
                        break
                mots.extend(lignes[fin].mots)
                if fin == debut:
                    continue
                depart_titre = _indice_sequence_mots(mots, ["fiche", "de", "fabrication"])
                if depart_titre is None:
                    continue
                sequence = list(mots[depart_titre:])
                normalise = " ".join(mot.normalise for mot in sequence)
                # Défaut mesuré le 30/10/2026 : cette voie exigeait le mot
                # « grand-voile ». Une fiche de voile d'avant (« Fiche de
                # fabrication "Voile d'avant" » puis « Génois … pour X de Y »)
                # n'en contient aucun : la fenêtre n'était jamais candidate,
                # donc client et bateau restaient vides. La seule exigence qui
                # reste est celle du CONTRAT de décomposition (« … pour … de
                # … ») ; la décomposition stricte décide ensuite et n'invente
                # rien quand elle échoue (RG6).
                if " pour " not in normalise:
                    continue
                sequences.append((sequence, page.numero))
                break

    # Troisième voie — mise en page en CELLULES où l'en-tête
    # « Fiche de fabrication » n'est pas physiquement voisin du titre (titre
    # déporté sur une autre ligne du tableau) : toute ligne qui porte à la
    # fois un mot de famille de voile, « pour » et « de » est candidate. La
    # désignation est ensuite rognée sur le mot de famille, donc une cellule
    # voisine lue sur la même ligne ne peut pas polluer la valeur.
    for page in pages[:2]:
        for ligne in page.lignes:
            normalise = ligne.normalise
            if " pour " not in normalise or " de " not in normalise:
                continue
            if _FAMILLES_VOILE_TITRE.search(normalise) is None:
                continue
            sequences.append((list(ligne.mots), page.numero))
    return sequences


def _analyser_titre_ffab(valeur: str) -> tuple[str | None, str, str, str] | None:
    """Décompose un titre FFAB en catégorie, désignation, bateau et reste.

    La désignation est ROGNÉE sur le dernier mot de famille de voile : dans
    un export tableur, la ligne physique porte parfois une cellule voisine
    avant le titre (« … Fichier ZZ 075a dessiné par … Grand-voile … »), et
    cette cellule ne doit jamais devenir la désignation.
    """
    correspondance = re.search(
        r"^\s*fiche\s+de\s+fabrication\s+[\"«](?P<categorie>[^\"»]+)[\"»]\s+"
        r"(?P<designation>.+?),?\s+pour\s+(?P<bateau>.+?)\s+de\s+(?P<reste>.+?)\s*$",
        valeur,
        flags=re.I,
    )
    if correspondance:
        return (
            correspondance.group("categorie").strip(),
            _rogner_designation(correspondance.group("designation")),
            correspondance.group("bateau").strip(),
            correspondance.group("reste").strip(),
        )

    repli = re.search(
        r"^(?P<designation>.+?)\s+pour\s+(?P<bateau>.+?)\s+de\s+(?P<reste>.+?)\s*$",
        valeur,
        flags=re.I,
    )
    if repli is None:
        return None
    designation = re.sub(r"^\s*fiche\s+de\s+fabrication\s*", "", repli.group("designation"), flags=re.I)
    designation = re.sub(r"^[\"«][^\"»]+[\"»]\s*", "", designation).strip(" ,")
    designation = _rogner_designation(designation)
    if not designation:
        return None
    return (
        None,
        designation,
        repli.group("bateau").strip(),
        repli.group("reste").strip(),
    )


def _traiter_ligne_titre_ffab(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Ligne de titre FFab complète : catégorie, désignation, bateau,
    client, numéro de commande externe et code de fiche en fin de ligne."""
    pages: list[PageAnalysee] = contexte.get("pages") or []
    analyse_titre: tuple[str | None, str, str, str] | None = None
    mots: list[Mot] = []
    valeur = ""
    page_titre = 0
    sequences_vues: set[tuple[int, str]] = set()
    for mots_candidats, page_candidate in _sequences_titre_ffab(pages):
        valeur_candidate = " ".join(mot.texte for mot in mots_candidats).strip()
        cle_sequence = (page_candidate, valeur_candidate)
        if cle_sequence in sequences_vues:
            continue
        sequences_vues.add(cle_sequence)
        analyse = _analyser_titre_ffab(valeur_candidate)
        if analyse is None:
            continue
        analyse_titre = analyse
        mots = list(mots_candidats)
        valeur = valeur_candidate
        page_titre = page_candidate
        break

    if analyse_titre is None:
        # Le titre n'est pas décomposable sans deviner : on retire la
        # trace générique de la règle et l'on conserve uniquement le
        # code corroboré par la ligne « Fichier » du document, s'il
        # existe. La fiche reste à relire, mais elle n'est plus perdue
        # au dépôt pour la seule absence d'une ligne physique unique.
        if base in fiche.champs:
            fiche.champs.remove(base)
        _reprendre_code_ffab_depuis_ligne_fichier(fiche, pages)
        return

    categorie, designation, bateau_brut, reste_client = analyse_titre

    commande: str | None = None
    trouve_commande = _RE_COMMANDE_TITRE_FFAB.search(reste_client)
    if trouve_commande:
        commande = trouve_commande.group(1).strip() or None
        reste_client = (
            reste_client[: trouve_commande.start()] + " " + reste_client[trouve_commande.end() :]
        ).strip()
    if commande is None:
        # Référence d'affaire en fin de ligne (« (ST75 - TOAOWEUYQ) ») :
        # commande externe, jamais un morceau du nom du client.
        trouve_reference = _RE_REFERENCE_TITRE_FFAB.search(reste_client)
        if trouve_reference:
            commande = (
                f"{_normaliser_code_ffab(trouve_reference.group('prefixe'))} - "
                f"{trouve_reference.group('suffixe').upper()}"
            )
            reste_client = reste_client[: trouve_reference.start()].strip(" ,;")

    code_fichier, mots_code_fichier, page_code_fichier = _code_corroboré_ligne_fichier(pages)
    code: str | None = None
    mots_code: list[Mot] = []
    page_code = page_titre
    client_brut = reste_client
    trouve_code = _RE_CODE_FFAB_FIN.search(reste_client)
    if trouve_code:
        candidat = _normaliser_code_ffab(trouve_code.group("code"))
        client_brut = reste_client[: trouve_code.start()].strip()
        if code_fichier is None or code_fichier == candidat:
            code = candidat
            mots_code = _localiser_sequence(mots, normaliser_terme(code).split())
    elif code_fichier:
        code = code_fichier
        mots_code = mots_code_fichier
        page_code = page_code_fichier

    if fiche.titre is None:
        # Le titre est la CATÉGORIE lue dans l'en-tête (« Grand-voile »,
        # « Voile d'avant »…). À défaut, celui de la famille reconnue dans la
        # désignation ; jamais un « Grand-voile » codé en dur, qui affichait un
        # génois comme une grand-voile.
        famille = _FAMILLES_VOILE_TITRE.search(designation or "")
        fiche.titre = categorie or (famille.group(1) if famille else None) or "Voile"
    if fiche.code is None and code:
        fiche.code = code
    if fiche.commande_numero is None and commande:
        fiche.commande_numero = commande
    if fiche.bateau_nom is None and bateau_brut:
        nom, taille = norm.separer_nom_et_detail(bateau_brut)
        fiche.bateau_nom = nom or bateau_brut
        fiche.bateau_taille = taille
    if fiche.client_nom is None and client_brut:
        nom, chantier = norm.separer_nom_et_detail(client_brut)
        fiche.client_nom = nom or client_brut
        fiche.client_chantier = chantier
    if designation and fiche.type_voile_libelle is None:
        _traiter_designation(fiche, designation, base)

    base.champ = "fiche.designation"
    base.valeur_brute = valeur
    base.page = page_titre
    base.zone = _zone_des_mots(mots)
    sous_traces: list[ChampExtrait] = []
    if fiche.titre:
        sous_traces.append(
            _reprendre_base(
                fiche,
                base,
                champ="fiche.titre",
                valeur_brute=valeur,
                valeur_normalisee=fiche.titre,
                confiance=CONFIANCE_CERTAIN,
                page=page_titre,
                zone=_zone_des_mots(_localiser_sequence(mots, normaliser_terme(fiche.titre).split()) or mots),
                table_cible="fiche",
                colonne_cible="titre",
            )
        )
    if fiche.code:
        sous_traces.append(
            _reprendre_base(
                fiche,
                base,
                champ="fiche.code",
                valeur_brute=fiche.code,
                valeur_normalisee=fiche.code,
                confiance=CONFIANCE_CERTAIN,
                page=page_code,
                zone=_zone_des_mots(mots_code or mots),
                table_cible="fiche",
                colonne_cible="code",
            )
        )
    if fiche.commande_numero:
        sous_traces.append(
            _reprendre_base(
                fiche,
                base,
                champ="fiche.commande",
                valeur_brute=fiche.commande_numero,
                valeur_normalisee=fiche.commande_numero,
                confiance=CONFIANCE_CERTAIN,
                page=page_titre,
                zone=_zone_des_mots(mots),
                table_cible="fiche",
                colonne_cible="commande_numero",
            )
        )
    if fiche.client_nom:
        sous_traces.append(
            _reprendre_base(
                fiche,
                base,
                champ="fiche.client",
                valeur_brute=client_brut,
                valeur_normalisee=(
                    f"{fiche.client_nom} | {fiche.client_chantier}" if fiche.client_chantier else fiche.client_nom
                ),
                confiance=CONFIANCE_CERTAIN,
                page=page_titre,
                zone=_zone_des_mots(_localiser_sequence(mots, normaliser_terme(client_brut).split()) or mots),
                table_cible="fiche",
                colonne_cible="client",
            )
        )
    if fiche.bateau_nom:
        sous_traces.append(
            _reprendre_base(
                fiche,
                base,
                champ="fiche.bateau",
                valeur_brute=bateau_brut,
                valeur_normalisee=(
                    f"{fiche.bateau_nom} | {fiche.bateau_taille}" if fiche.bateau_taille else fiche.bateau_nom
                ),
                confiance=CONFIANCE_CERTAIN,
                page=page_titre,
                zone=_zone_des_mots(_localiser_sequence(mots, normaliser_terme(bateau_brut).split()) or mots),
                table_cible="fiche",
                colonne_cible="bateau",
            )
        )
    if base not in fiche.champs:
        fiche.champs.append(base)
    fiche.champs.extend(sous_traces)


_COTES_FFAB_CONNUES: dict[str, str | None] = {
    "guindant": "slu_m",
    "bordure": "sf_m",
    "chute": "sle_m",
    "tetiere": "tetiere_cm",
    "surface": "spa_m2",
    "poids": "poids_kg",
    "shw": "shw_m",
    "mhw": None,
    "mtw": None,
    "muw": None,
}
_UNites_cote_FFAB = {"m", "m²", "m2", "kg", "cm", "mm"}


def _jeton_cote_ffab(mot: Mot) -> str:
    return re.sub(r"[^a-z0-9²]", "", mot.normalise)


def _tetiere_en_cm(brut: str) -> float | None:
    valeur = norm.extraire_decimal(brut)
    if valeur is None:
        return None
    if re.search(r"\bmm\b", brut, re.I):
        return round(valeur / 10.0, 3)
    if re.search(r"\bcm\b", brut, re.I):
        return valeur
    if re.search(r"\bm\b(?!m)", brut, re.I):
        return round(valeur * 100.0, 3)
    return None  # sans unité explicite, aucune conversion n'est inventée


def _groupes_valeurs_ffab(ligne: Ligne) -> list[list[Mot]]:
    groupes: list[list[Mot]] = []
    indice = 0
    while indice < len(ligne.mots):
        mot = ligne.mots[indice]
        if re.search(r"\d", mot.texte) and norm.extraire_decimal(mot.texte) is not None:
            groupe = [mot]
            if indice + 1 < len(ligne.mots) and _jeton_cote_ffab(ligne.mots[indice + 1]) in _UNites_cote_FFAB:
                groupe.append(ligne.mots[indice + 1])
                indice += 1
            groupes.append(groupe)
        indice += 1
    return groupes


def _traiter_cotes_ffab(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """Grille « Cotes (tissu) » : en-têtes sur une ligne, valeurs alignées
    sur la suivante. L'ordre fait foi quand les comptes concordent ; sinon
    l'alignement horizontal départage. MHW/MTW/MUW n'ont pas de colonne du
    schéma : ils sont conservés en mesures libres tracées (RG6)."""
    pages: list[PageAnalysee] = contexte.get("pages") or []
    if base in fiche.champs:
        fiche.champs.remove(base)

    for page in pages:
        for indice_ligne, ligne in enumerate(page.lignes):
            etiquettes: list[tuple[str, list[Mot]]] = []
            vus: set[str] = set()
            indice_mot = 0
            while indice_mot < len(ligne.mots):
                jeton = _jeton_cote_ffab(ligne.mots[indice_mot])
                if jeton in _COTES_FFAB_CONNUES and jeton not in vus:
                    mots_etiquette = [ligne.mots[indice_mot]]
                    if (
                        jeton == "poids"
                        and indice_mot + 1 < len(ligne.mots)
                        and _jeton_cote_ffab(ligne.mots[indice_mot + 1]) == "tissu"
                    ):
                        mots_etiquette.append(ligne.mots[indice_mot + 1])
                        indice_mot += 1
                    etiquettes.append((jeton, mots_etiquette))
                    vus.add(jeton)
                indice_mot += 1
            if len(etiquettes) < 4 or "guindant" not in vus or not ({"bordure", "chute"} & vus):
                continue

            valeurs: list[list[Mot]] = []
            for suivante in page.lignes[indice_ligne + 1 : indice_ligne + 5]:
                if not suivante.mots or not ligne.mots:
                    continue
                if suivante.mots[0].haut - ligne.mots[0].haut > 70:
                    break
                candidate = _groupes_valeurs_ffab(suivante)
                if len(candidate) >= min(4, len(etiquettes)):
                    valeurs = candidate
                    break
            if not valeurs:
                continue

            paires: list[tuple[str, list[Mot]]] = []
            lecture_exacte = len(valeurs) == len(etiquettes)
            if lecture_exacte:
                paires = [(etiquettes[i][0], valeurs[i]) for i in range(len(etiquettes))]
            else:
                utilisees: set[int] = set()
                centres = [
                    (min(mot.x0 for mot in mots) + max(mot.x1 for mot in mots)) / 2
                    for _cle, mots in etiquettes
                ]
                for groupe in valeurs:
                    centre = (min(mot.x0 for mot in groupe) + max(mot.x1 for mot in groupe)) / 2
                    choix = min(
                        (abs(centre - centres[i]), i)
                        for i in range(len(etiquettes))
                        if i not in utilisees
                    )
                    if choix[0] <= 55:
                        utilisees.add(choix[1])
                        paires.append((etiquettes[choix[1]][0], groupe))
                if len(paires) < 4:
                    paires = [(etiquettes[i][0], valeurs[i]) for i in range(min(len(etiquettes), len(valeurs)))]

            confiance = CONFIANCE_CERTAIN if lecture_exacte else 0.85
            for cle, mots_valeur in paires:
                brut_cote = " ".join(mot.texte for mot in mots_valeur).strip()
                if not brut_cote or brut_cote == "~":
                    continue
                colonne = _COTES_FFAB_CONNUES[cle]
                if colonne is None:
                    decimal = norm.extraire_decimal(brut_cote)
                    if decimal is None or any(m.champ == f"libre.{cle}" for m in fiche.mesures_libres):
                        continue
                    fiche.mesures_libres.append(
                        ChampExtrait(
                            champ=f"libre.{cle}",
                            valeur_brute=f"{cle.upper()} {brut_cote}",
                            valeur_normalisee=str(decimal),
                            methode="gabarit",
                            confiance=confiance,
                            page=page.numero,
                            zone=_zone_des_mots(mots_valeur),
                        )
                    )
                    continue
                cible = f"cotes.finie.{colonne}"
                if _cote_deja_lue(fiche, cible):
                    continue
                if colonne == "tetiere_cm":
                    valeur = _tetiere_en_cm(brut_cote)
                    if valeur is None:
                        if not any(m.champ == "libre.tetiere" for m in fiche.mesures_libres):
                            decimal = norm.extraire_decimal(brut_cote)
                            fiche.mesures_libres.append(
                                ChampExtrait(
                                    champ="libre.tetiere",
                                    valeur_brute=f"Têtière {brut_cote}",
                                    valeur_normalisee=None if decimal is None else str(decimal),
                                    methode="gabarit",
                                    confiance=0.7,
                                    page=page.numero,
                                    zone=_zone_des_mots(mots_valeur),
                                )
                            )
                        continue
                elif colonne in {"spa_m2", "poids_kg"}:
                    valeur = norm.extraire_decimal(brut_cote)
                else:
                    valeur = norm.cote_en_metres(brut_cote)
                if valeur is None:
                    continue
                champ = ChampExtrait(
                    champ=cible,
                    valeur_brute=brut_cote,
                    valeur_normalisee=str(valeur),
                    methode="gabarit",
                    confiance=confiance,
                    page=page.numero,
                    zone=_zone_des_mots(mots_valeur),
                    table_cible="fiche_cotes",
                    colonne_cible=colonne,
                )
                _ranger_cote(fiche, cible, valeur, champ)
            return


def _traiter_lattes_ffab(fiche: FicheExtraite, brut: str, base: ChampExtrait, contexte: dict) -> None:
    """« 4 lattes » : le nombre précède l'ancre, la ligne est donc relue."""
    pages: list[PageAnalysee] = contexte.get("pages") or []
    if base in fiche.champs:
        fiche.champs.remove(base)
    if any(mesure.champ == "libre.nombre_lattes" for mesure in fiche.mesures_libres):
        return
    for page in pages:
        for ligne in page.lignes:
            trouve = re.search(r"\b(\d{1,2})\s+lattes\b", ligne.normalise)
            if trouve:
                fiche.mesures_libres.append(
                    ChampExtrait(
                        champ="libre.nombre_lattes",
                        valeur_brute=" ".join(mot.texte for mot in ligne.mots),
                        valeur_normalisee=trouve.group(1),
                        methode="gabarit",
                        confiance=0.85,
                        page=page.numero,
                        zone=_zone_des_mots(ligne.mots),
                    )
                )
                return


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
    # Format FFab grand-voile (Lot 10).
    "ligne_titre_ffab": lambda fiche, brut, base, contexte: _traiter_ligne_titre_ffab(fiche, brut, base, contexte),
    "cotes_ffab": lambda fiche, brut, base, contexte: _traiter_cotes_ffab(fiche, brut, base, contexte),
    "lattes_ffab": lambda fiche, brut, base, contexte: _traiter_lattes_ffab(fiche, brut, base, contexte),
    # Formulaires accessoires réels (Lot CAL-2, 2026-10-29).
    "titre_accessoire": lambda fiche, brut, base, contexte: _traiter_titre_accessoire(fiche, brut, base, contexte),
    "code_accessoire": lambda fiche, brut, base, contexte: _traiter_code_accessoire(fiche, brut, base, contexte),
    "dimension_nommee": lambda fiche, brut, base, contexte: _traiter_dimension_nommee(fiche, brut, base, contexte),
    "logo_accessoire": lambda fiche, brut, base, contexte: _traiter_logo_accessoire(fiche, brut, base, contexte),
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


def _porte_entete_fiche_fabrication(pages: list[PageAnalysee]) -> bool:
    """Le document porte-t-il l'en-tête « fiche de fabrication » (toute catégorie) ?

    Sert d'autorisation de relecture pour la règle de titre FFab, dont l'ancre
    historique (« grand-voile ») n'existe pas sur une fiche de voile d'avant.
    """
    for page in pages[:2]:
        for ligne in page.lignes:
            if "fiche de fabrication" in ligne.normalise:
                return True
    return False


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
        # Défaut mesuré le 30/10/2026 : la règle de titre FFab n'a qu'une ancre
        # (« grand-voile »). Sur une fiche de VOILE D'AVANT — en-tête
        # « Fiche de fabrication "Voile d'avant" », puis « Génois … pour X de
        # Y » — le mot « grand-voile » n'existe nulle part : la règle n'était
        # jamais exécutée, donc titre, client et bateau restaient vides. C'est
        # ce qui donnait « ça a marché une fois et pas les deux autres ».
        # On autorise donc la relecture de page dès que le document porte
        # l'en-tête « fiche de fabrication » : le traitement relit les pages et
        # n'invente rien si la décomposition stricte échoue (RG6).
        relecture_page = ancre_trouvee or (
            traitement == "ligne_titre_ffab" and _porte_entete_fiche_fabrication(pages)
        )
        # Traitements « l'ancre EST la valeur » : sur les formulaires
        # d'accessoires réels, le type est porté par le titre lui-même
        # (« Fiche de Lazy Bag ») et le code par la ligne nue suivante ou par
        # « Commande ». Aucun mot de valeur n'est donc à droite de l'ancre :
        # sans cette autorisation, la règle s'arrêtait avant le traitement et
        # la fiche sortait sans titre ni code. Le traitement relit les pages et
        # n'invente rien si la forme attendue est absente (RG6).
        if traitement in {"designation", "ligne_titre_ffab", "titre_accessoire", "code_accessoire", "logo_accessoire"} and relecture_page and regle.ancres:
            # Le gabarit génois pose le type porté par l'ancre, le titre FFAB
            # peut être morcelé juste après « grand-voile », et les deux
            # traitements accessoires ci-dessus lisent la page entière.
            _executer_regle_traitement(fiche, regle, "", 0, [], pages=pages)
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


def _executer_regle_traitement(
    fiche: FicheExtraite,
    regle,
    brut: str,
    numero_page: int,
    mots_valeur: list[Mot],
    pages: list[PageAnalysee] | None = None,
) -> None:
    """Dispatch d'un traitement sur une valeur déjà bornée (utilisé aussi par
    les voies où l'ancre seule autorise une relecture de page)."""
    _construire_et_ranger(fiche, regle, brut, numero_page, mots_valeur, pages=pages)


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


def _type_imprime(texte: str | None) -> str | None:
    """Type de voile reconnu dans un texte court (titre imprimé), ou None."""
    if not texte:
        return None
    normalise = norm.sans_accents(texte).strip()
    for motif, libelle, _famille in TYPES_VOILE:
        if normalise.startswith(norm.sans_accents(motif)):
            return libelle
    return None


def _aligner_titre_sur_type(fiche: FicheExtraite) -> None:
    """Quand l'en-tête imprimé et la ligne de désignation nomment DEUX types
    différents, c'est la désignation qui décrit la voile : le titre la suit.

    Mesuré le 2026-10-29 sur `/WESTERLY PENTLAND ST660B/ST 660b_ffab.pdf` :

        ``Fiche de fabrication "Grand-voile"``          ← en-tête du FORMULAIRE
        ``Artimon classique semi-latté, gamme Ocean, pour Westerly Pentland 32 …``

    L'en-tête est un libellé de gabarit d'impression, pas la voile fabriquée :
    la fiche sortait avec ``titre = Grand-voile`` et ``type = Artimon`` — deux
    champs qui se contredisent dans l'écran de validation. La valeur imprimée
    reste dans ``valeur_brute`` (traçabilité intacte).

    Aucun effet quand le titre n'est pas un type connu (« Voile d'avant »,
    « Fiche de fabrication ») : rien n'est deviné.
    """
    type_du_titre = _type_imprime(fiche.titre)
    if type_du_titre is None or fiche.type_voile_libelle in (None, type_du_titre):
        return
    fiche.titre = fiche.type_voile_libelle
    for champ in fiche.champs:
        if champ.champ == "fiche.titre":
            champ.valeur_normalisee = fiche.titre
            champ.methode = "gabarit_designation"


def _extraire_pages(
    pages: list[PageAnalysee],
    chemin: Path,
    gabarit: GabaritDef,
    gabarits: list[GabaritDef] | None,
    vocabulaire_libres: set[str] | None = None,
) -> FicheExtraite:
    """Exécute un gabarit sur des pages déjà analysées (aucune réouverture)."""
    fiche = FicheExtraite(fichier_pdf=str(chemin))
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
    _aligner_titre_sur_type(fiche)
    fiche.anomalies = evaluer_anomalies(fiche)
    return fiche


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
    if gabarit_code is not None:
        if not gabarits:
            raise ValueError("gabarit_code exigé : fournir aussi la liste des gabarits.")
        gabarit = next((g for g in gabarits if g.code == gabarit_code), None)
        if gabarit is None:
            raise GabaritInconnu(f"Gabarit « {gabarit_code} » absent du registre.")
    else:
        gabarit = detecter_gabarit(texte, gabarits or [])
    return _extraire_pages(pages, chemin, gabarit, gabarits, vocabulaire_libres)


def analyser_candidat_fiche(
    chemin: Path,
    gabarits: list[GabaritDef],
    vocabulaire_libres: set[str] | None = None,
) -> CandidatFiche | None:
    """Analyse un PDF une seule fois pour le scanner multi-PDF.

    Retourne ``None`` quand aucun gabarit n'a d'ancre présente. Les erreurs
    de lecture PDF sont laissées à l'appelant, qui les transforme en raison
    explicite du dossier sans interrompre le lot.
    """
    pages = analyser_pdf(chemin)
    texte = texte_normalise(pages)
    scores = [(gabarit.score_detection(texte), indice, gabarit) for indice, gabarit in enumerate(gabarits)]
    if not scores:
        return None
    score, _indice, gabarit = max(scores, key=lambda item: (item[0], -item[1]))
    if score <= 0:
        return None
    fiche = _extraire_pages(pages, chemin, gabarit, gabarits, vocabulaire_libres)
    return CandidatFiche(gabarit=gabarit, score_detection=score, fiche=fiche)


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
