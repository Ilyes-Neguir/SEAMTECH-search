"""Lot 2 — double vérification de l'extraction accessoire.

Mesuré sur le corpus réel de l'atelier. Seules des STRUCTURES et des
comptages sont consignés ici : aucun nom de client, de bateau ni de fichier,
aucune valeur chiffrée issue d'un document réel.

Ce que la mesure a établi
-------------------------

Trois natures de documents cohabitent dans les dossiers d'accessoires, et
elles ne se traitent pas de la même façon :

* **formulaire** — fiche de prise de cotes imprimée. Des libellés suivis
  d'une mesure dont l'unité est explicite et souvent COLLÉE au nombre
  (« 3.60m », « 12mm »). Deux familles observées (fourreau de GSE, lazy bag
  / sac de pont), chacune avec son propre vocabulaire de cotes.
* **plan** — planche de coupe ou taud : des nombres NUS, sans unité, sans
  libellé. Aucune cote n'en est déduite (une unité devinée serait une cote
  fabriquée).
* **illisible** — document numérisé sans couche texte. L'extraction n'a rien
  à y lire : le dépôt le déclare déjà en revue manuelle, l'OCR du projet
  n'est pas branché ici.

Deux pièges mesurés, tous deux traités ci-dessous :

* **casse et accents incohérents** — le même formulaire porte
  « Hauteur du point d'écoute » et « longueur point decoute ». La
  normalisation plie casse, accents, apostrophes et ponctuation ;
  l'astérisque imprimé (« Longueur du lazy* ») est retiré.
* **deux cotes sur une même ligne** — « Longueur du gousset de latte 3.22m -
  Latte jonc 12mm ». Seule la mesure de TÊTE du libellé est retenue :
  prendre la seconde la ferait projeter en « largeur » une cote de latte.

Ce qu'est la double vérification
--------------------------------

Une valeur n'est promue « vérifiée » que si deux lectures INDÉPENDANTES
donnent le même résultat :

1. **lecture séquentielle** — le libellé puis la valeur, sur la ligne
   reconstruite par le moteur d'extraction ;
2. **lecture géométrique** — la mesure la plus proche à DROITE du libellé,
   bandes verticales superposées, sans reconstruire aucune ligne.

S'y ajoute un contrôle d'unité : l'unité lue doit être celle que le
vocabulaire annonce pour ce libellé (un diamètre se lit en mm). Faute de
seconde lecture possible, rien n'est promu — « non vérifiable » n'est jamais
« vérifié ». Un désaccord est une anomalie FORTE qui renvoie le dossier à
l'approbateur : il n'est jamais résolu en silence (RG6).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from seamtech_search.fiches.extraction import Mot, PageAnalysee
from seamtech_search.fiches.modeles import Zone

__all__ = [
    "MesureNommee",
    "NatureDocument",
    "RapportVerification",
    "StatutVerification",
    "VerificationMesure",
    "agreger_verifications",
    "classifier_nature_document",
    "mesures_nommees",
    "verifier_document",
    "verifier_mesure",
]

NatureDocument = Literal["formulaire", "plan", "illisible", "indetermine"]
StatutVerification = Literal["accord", "divergence", "unite_inattendue", "non_verifiable"]

#: Vocabulaire des cotes nommées, mesuré sur les formulaires réels.
#: (libellé normalisé, libellé canonique, unité annoncée par le formulaire)
_VOCABULAIRE: tuple[tuple[str, str, str], ...] = (
    # Fourreau de GSE — quatre cotes, toutes en mètres.
    ("longueur du fourreau", "longueur_fourreau", "m"),
    ("hauteur du fourreau", "hauteur_fourreau", "m"),
    ("longueur point d ecoute", "longueur_point_ecoute", "m"),
    ("longueur point decoute", "longueur_point_ecoute", "m"),
    ("hauteur du point d ecoute", "hauteur_point_ecoute", "m"),
    ("hauteur du point decoute", "hauteur_point_ecoute", "m"),
    # Lazy bag / sac de pont.
    ("longueur du lazy", "longueur_lazy", "m"),
    ("hauteur du lazy", "hauteur_lazy", "m"),
    ("hauteur du capot", "hauteur_capot", "m"),
    ("longueur du gousset de latte", "longueur_gousset_latte", "m"),
    ("longueur de la ralingue", "longueur_ralingue", "m"),
    ("diametre de ralingue", "diametre_ralingue", "mm"),
    ("longueur pour facturation", "longueur_facturation", "m"),
    ("longueur du sac", "longueur_sac", "m"),
    ("mesure e", "mesure_e", "m"),
)

#: Le libellé le plus long gagne : « longueur du gousset de latte » ne doit
#: jamais être tronqué en « longueur du… » par une entrée plus courte.
_VOCABULAIRE_TRIEE: tuple[tuple[str, str, str], ...] = tuple(
    sorted(_VOCABULAIRE, key=lambda entree: len(entree[0].split()), reverse=True)
)

_EN_METRES = {"mm": 0.001, "cm": 0.01, "m": 1.0}

# « 3.60m » (unité collée) et « 12mm » — la forme réelle des formulaires.
_RE_JETON_MESURE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(mm|cm|m)$", re.IGNORECASE)
_RE_NOMBRE_SEUL = re.compile(r"^(\d+(?:[.,]\d+)?)$")
_RE_UNITE_SEULE = re.compile(r"^(mm|cm|m)$", re.IGNORECASE)
# Un nombre suivi d'une unité, où qu'il soit : signature d'un formulaire.
_RE_UNITE_EXPLICITE = re.compile(r"\d\s*(?:mm|cm|m)\b", re.IGNORECASE)
# Un nombre NU (aucune unité) : signature d'un plan.
_RE_NOMBRE_NU = re.compile(r"(?<![\w,.])\d+(?:[.,]\d+)?(?![\w])")

#: Deux mots sont sur la même bande si leurs hauteurs se recouvrent à
#: :data:`_TOLERANCE_BANDE` près — tolérance nécessaire car les libellés et
#: leurs valeurs ne partagent pas toujours la même ligne de base imprimée.
_TOLERANCE_BANDE = 3.0


@dataclass(frozen=True)
class MesureNommee:
    """Une cote lue derrière un libellé connu, avec son unité explicite."""

    libelle_brut: str
    libelle_canonique: str
    #: Mots normalisés du libellé — sert à le retrouver dans les mots de la page.
    libelle_mots: tuple[str, ...]
    unite_attendue: str
    valeur_texte: str
    unite_lue: str
    valeur_m: float | None
    page: int

    @property
    def valeur_lue(self) -> str:
        """La mesure telle que le formulaire l'imprime (valeur + unité)."""
        return f"{self.valeur_texte}{self.unite_lue}"

    @property
    def cle(self) -> tuple[float | None, str] | None:
        """Clé de comparaison insensible à la casse et à la virgule décimale."""
        try:
            return (round(float(self.valeur_texte.replace(",", ".")), 6), self.unite_lue.lower())
        except ValueError:
            return None


@dataclass(frozen=True)
class VerificationMesure:
    """Résultat de la relecture indépendante d'une cote."""

    mesure: MesureNommee
    statut: StatutVerification
    seconde_lecture: str | None = None
    unite_conforme: bool = True
    motif: str = ""


@dataclass
class RapportVerification:
    """Ce que la relecture a donné pour un document entier."""

    nature: NatureDocument
    mesures: list[MesureNommee] = field(default_factory=list)
    verifications: list[VerificationMesure] = field(default_factory=list)

    @property
    def verifiees(self) -> list[MesureNommee]:
        """Les cotes dont les deux lectures concordent — les seules promues."""
        return [v.mesure for v in self.verifications if v.statut == "accord"]

    @property
    def divergentes(self) -> list[VerificationMesure]:
        return [v for v in self.verifications if v.statut == "divergence"]

    @property
    def unites_inattendues(self) -> list[VerificationMesure]:
        return [v for v in self.verifications if v.statut == "unite_inattendue"]

    @property
    def non_verifiables(self) -> list[VerificationMesure]:
        return [v for v in self.verifications if v.statut == "non_verifiable"]

    @property
    def bloquant(self) -> bool:
        """Vrai si le dossier doit repartir vers l'approbateur."""
        return bool(self.divergentes or self.unites_inattendues)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _cle_libelle(valeur: str) -> str:
    """Casse, accents, apostrophes, astérisques et ponctuation retirés.

    « Hauteur du point d'écoute », « longueur point decoute » et
    « Longueur du lazy* » doivent donner la même clé comparable.
    """
    decompose = unicodedata.normalize("NFKD", str(valeur))
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    bas = sans_accents.lower().replace("’", " ")
    bas = re.sub(r"[^a-z0-9]+", " ", bas)
    return re.sub(r"\s+", " ", bas).strip()


def _en_metres(valeur_texte: str, unite: str) -> float | None:
    """Convertit une mesure vers le mètre. ``None`` si c'est impossible.

    Aucune unité n'est jamais supposée : sans unité reconnue, il n'y a rien
    à convertir (RG6 — une cote devinée est une cote fausse).
    """
    try:
        nombre = float(str(valeur_texte).replace(",", "."))
    except (TypeError, ValueError):
        return None
    facteur = _EN_METRES.get(str(unite).lower())
    if facteur is None or nombre <= 0:
        return None
    return round(nombre * facteur, 6)


# ---------------------------------------------------------------------------
# Lecture séquentielle (libellé → valeur sur la ligne reconstruite)
# ---------------------------------------------------------------------------


def _lire_mesure(jetons: list[str], debut: int) -> tuple[str, str, int] | None:
    """Lit la mesure SITUÉE JUSTE APRÈS le libellé.

    Retourne ``(valeur, unite, jetons consommés)``. Deux formes réelles :
    « 3.60m » (un seul jeton, unité collée) et « 12 mm » (deux jetons).

    Un nombre NU n'est jamais retenu : sans unité, il n'y a pas de mesure.
    """
    if debut >= len(jetons):
        return None
    tete = jetons[debut].strip()
    colle = _RE_JETON_MESURE.match(tete)
    if colle is not None:
        return colle.group(1), colle.group(2).lower(), 1
    if _RE_NOMBRE_SEUL.match(tete) and debut + 1 < len(jetons):
        unite = _RE_UNITE_SEULE.match(jetons[debut + 1].strip())
        if unite is not None:
            return tete, unite.group(1).lower(), 2
    return None


def mesures_nommees(texte: str, *, page: int = 0) -> list[MesureNommee]:
    """Toutes les cotes d'un texte dont le libellé est connu.

    Une seule mesure est retenue par libellé : celle de TÊTE. Une ligne qui
    porte deux cotes (« 3.22m - Latte jonc 12mm ») ne produit donc pas de
    seconde cote orpheline rangée n'importe comment.
    """
    resultats: list[MesureNommee] = []
    for ligne in str(texte or "").splitlines():
        jetons = ligne.split()
        if not jetons:
            continue
        # Un jeton peut porter PLUSIEURS mots normalisés : « d'ecoute » donne
        # « d ecoute », « 4.5m » donne « 4 5m ». Le libellé est donc cherché
        # au niveau du MOT, puis ramené à ses jetons d'origine — sans quoi
        # « Hauteur du point d'ecoute » (apostrophe) restait illisible alors
        # que « longueur point decoute » (sans apostrophe) était lu.
        cles: list[str] = []
        rattachement: list[int] = []
        for rang, jeton in enumerate(jetons):
            for mot in _cle_libelle(jeton).split():
                cles.append(mot)
                rattachement.append(rang)
        position = 0
        while position < len(cles):
            trouve = None
            for libelle, canonique, unite_attendue in _VOCABULAIRE_TRIEE:
                attendus = tuple(libelle.split())
                fin_mot = position + len(attendus)
                if fin_mot > len(cles) or tuple(cles[position:fin_mot]) != attendus:
                    continue
                debut_jeton = rattachement[position]
                fin_jeton = rattachement[fin_mot - 1] + 1
                lue = _lire_mesure(jetons, fin_jeton)
                if lue is None:
                    continue
                trouve = (debut_jeton, fin_jeton, canonique, unite_attendue, lue, attendus)
                break
            if trouve is None:
                position += 1
                continue
            debut_jeton, fin_jeton, canonique, unite_attendue, lue, attendus = trouve
            valeur_texte, unite_lue, consommes = lue
            resultats.append(
                MesureNommee(
                    libelle_brut=" ".join(jetons[debut_jeton:fin_jeton]),
                    libelle_canonique=canonique,
                    libelle_mots=attendus,
                    unite_attendue=unite_attendue,
                    valeur_texte=valeur_texte,
                    unite_lue=unite_lue,
                    valeur_m=_en_metres(valeur_texte, unite_lue),
                    page=page,
                )
            )
            # Reprendre après la mesure : « … 3.22m - Latte jonc 12mm » peut
            # porter un autre libellé plus loin sur la même ligne.
            apres = fin_jeton + consommes
            while position < len(cles) and rattachement[position] < apres:
                position += 1
    return resultats


# ---------------------------------------------------------------------------
# Nature du document
# ---------------------------------------------------------------------------


def classifier_nature_document(texte: str) -> NatureDocument:
    """Dit ce qu'un document EST avant toute extraction.

    * ``illisible`` — aucune couche texte : revue manuelle, rien à lire ;
    * ``formulaire`` — au moins une cote nommée : extractable ;
    * ``plan`` — des nombres nus, aucune unité, aucun libellé : non
      extractable en l'état (deviner l'unité fabriquerait une cote) ;
    * ``indetermine`` — du texte, mais rien de reconnu : aucune prétention.
    """
    contenu = str(texte or "").strip()
    if not contenu:
        return "illisible"
    if mesures_nommees(contenu):
        return "formulaire"
    if _RE_UNITE_EXPLICITE.search(contenu):
        return "indetermine"
    if len(_RE_NOMBRE_NU.findall(contenu)) >= 2:
        return "plan"
    return "indetermine"


# ---------------------------------------------------------------------------
# Lecture géométrique (seconde lecture, indépendante de la reconstruction
# des lignes)
# ---------------------------------------------------------------------------


def _zone_du_libelle(mots: list[Mot], libelle_mots: tuple[str, ...], page: int) -> Zone | None:
    """Boîte englobante du libellé sur la page, ou ``None`` s'il est absent."""
    if not libelle_mots:
        return None
    page_mots = sorted(
        (mot for mot in mots if mot.page == page),
        key=lambda mot: (round(mot.haut, 1), mot.x0),
    )
    # Même aplatissement que :func:`mesures_nommees` : un jeton « d'ecoute »
    # compte pour les deux mots « d » et « ecoute ».
    cles: list[str] = []
    rattachement: list[int] = []
    for rang, mot in enumerate(page_mots):
        for terme in _cle_libelle(mot.texte).split():
            cles.append(terme)
            rattachement.append(rang)
    attendus = list(libelle_mots)
    if len(attendus) > len(cles):
        return None
    for index in range(len(cles) - len(attendus) + 1):
        if cles[index : index + len(attendus)] != attendus:
            continue
        rangs = sorted({rattachement[index + decalage] for decalage in range(len(attendus))})
        groupe = [page_mots[rang] for rang in rangs]
        return Zone(
            x0=min(mot.x0 for mot in groupe),
            y0=min(mot.haut for mot in groupe),
            x1=max(mot.x1 for mot in groupe),
            y1=max(mot.bas for mot in groupe),
            page=page,
        )
    return None


def _mesure_a_droite(mots: list[Mot], zone: Zone, page: int) -> tuple[str, str] | None:
    """Première mesure à droite du libellé, bandes verticales superposées."""
    droite = sorted(
        (
            mot
            for mot in mots
            if mot.page == page
            and mot.x0 >= zone.x1 - 0.5
            and mot.haut <= zone.y1 + _TOLERANCE_BANDE
            and mot.bas >= zone.y0 - _TOLERANCE_BANDE
        ),
        key=lambda mot: (mot.x0, mot.haut),
    )
    for index, mot in enumerate(droite):
        texte = mot.texte.strip()
        colle = _RE_JETON_MESURE.match(texte)
        if colle is not None:
            return colle.group(1), colle.group(2).lower()
        if _RE_NOMBRE_SEUL.match(texte) and index + 1 < len(droite):
            unite = _RE_UNITE_SEULE.match(droite[index + 1].texte.strip())
            if unite is not None:
                return texte, unite.group(1).lower()
    return None


def verifier_mesure(mesure: MesureNommee, mots: list[Mot]) -> VerificationMesure:
    """Confronte la lecture séquentielle à la lecture géométrique.

    Sans mots exploitables, ou sans mesure à droite du libellé, la seconde
    lecture est impossible : la cote reste « non vérifiable ». Elle n'est
    jamais promue pour autant — l'absence de preuve n'est pas une preuve.
    """
    if not mots:
        return VerificationMesure(
            mesure=mesure,
            statut="non_verifiable",
            motif="aucun mot transmis : seconde lecture impossible",
        )
    zone = _zone_du_libelle(mots, mesure.libelle_mots, mesure.page)
    if zone is None:
        return VerificationMesure(
            mesure=mesure,
            statut="non_verifiable",
            motif="libellé introuvable dans les mots de la page",
        )
    seconde = _mesure_a_droite(mots, zone, mesure.page)
    if seconde is None:
        return VerificationMesure(
            mesure=mesure,
            statut="non_verifiable",
            motif="aucune mesure à droite du libellé sur la même bande",
        )
    valeur_seconde, unite_seconde = seconde
    lecture = f"{valeur_seconde}{unite_seconde}"
    quantite_seconde = _en_metres(valeur_seconde, unite_seconde)

    # 1) Désaccord de QUANTITÉ : les deux lectures ne mesurent pas la même
    #    chose. C'est le cas le plus grave — la cote est mise en doute.
    if quantite_seconde is None or mesure.valeur_m is None or quantite_seconde != mesure.valeur_m:
        return VerificationMesure(
            mesure=mesure,
            statut="divergence",
            seconde_lecture=lecture,
            unite_conforme=False,
            motif=(f"lectures différentes : « {mesure.valeur_lue} » par libellé, « {lecture} » par proximité"),
        )

    # 2) Unité non conforme à celle que le formulaire annonce pour ce libellé.
    #    La quantité est la même, mais un diamètre lu en mètres doit être
    #    signalé : la conversion aurait pu masquer une erreur de repérage.
    if mesure.unite_lue.lower() != mesure.unite_attendue.lower():
        return VerificationMesure(
            mesure=mesure,
            statut="unite_inattendue",
            seconde_lecture=lecture,
            unite_conforme=False,
            motif=(
                f"unité lue « {mesure.unite_lue} » alors que « {mesure.libelle_brut} » "
                f"annonce « {mesure.unite_attendue} »"
            ),
        )

    # 3) Même quantité mais les deux lectures n'impriment pas la même unité :
    #    le document et la relecture ne laissent pas la même trace.
    if unite_seconde.lower() != mesure.unite_lue.lower():
        return VerificationMesure(
            mesure=mesure,
            statut="unite_inattendue",
            seconde_lecture=lecture,
            unite_conforme=False,
            motif=(
                f"même quantité mais unités différentes : « {mesure.unite_lue} » par "
                f"libellé, « {unite_seconde} » par proximité"
            ),
        )

    return VerificationMesure(
        mesure=mesure,
        statut="accord",
        seconde_lecture=lecture,
        unite_conforme=True,
        motif=f"deux lectures concordantes : « {mesure.valeur_lue} »",
    )


# ---------------------------------------------------------------------------
# Vérification d'un document entier
# ---------------------------------------------------------------------------


def verifier_document(pages: list[PageAnalysee]) -> RapportVerification:
    """Classe le document puis relit chacune de ses cotes nommées.

    Les pages ne sont analysées qu'une fois : la classification et la
    relecture partagent la même lecture, seul le CHEMIN de lecture change.
    """
    if not pages:
        return RapportVerification(nature="illisible")
    texte = "\n".join(" ".join(mot.texte for mot in ligne.mots) for page in pages for ligne in page.lignes)
    nature = classifier_nature_document(texte)
    mesures: list[MesureNommee] = []
    for page in pages:
        texte_page = "\n".join(" ".join(mot.texte for mot in ligne.mots) for ligne in page.lignes)
        mesures.extend(mesures_nommees(texte_page, page=page.numero))
    mots = [mot for page in pages for ligne in page.lignes for mot in ligne.mots]
    verifications = [verifier_mesure(mesure, mots) for mesure in mesures]
    return RapportVerification(nature=nature, mesures=mesures, verifications=verifications)


def agreger_verifications(rapports: list[RapportVerification]) -> dict[str, int]:
    """Compte par nature et par statut — le rapport d'un lot de dossiers.

    Dossier par dossier, jamais d'import global : cet agrégat additionne des
    rapports déjà produits séparément, il ne relit rien.
    """
    compte: dict[str, int] = {
        "documents": len(rapports),
        "formulaire": 0,
        "plan": 0,
        "illisible": 0,
        "indetermine": 0,
        "mesures": 0,
        "accord": 0,
        "divergence": 0,
        "unite_inattendue": 0,
        "non_verifiable": 0,
        "dossiers_bloquants": 0,
    }
    for rapport in rapports:
        compte[rapport.nature] = compte.get(rapport.nature, 0) + 1
        compte["mesures"] += len(rapport.mesures)
        for verification in rapport.verifications:
            compte[verification.statut] = compte.get(verification.statut, 0) + 1
        if rapport.bloquant:
            compte["dossiers_bloquants"] += 1
    return compte
