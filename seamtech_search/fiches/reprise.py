"""Fiche de REPRISE : ne rien perdre d'un dossier réel qui n'a pas de fiche.

Constat mesuré sur `VOILES 2024` (24 dossiers réels, 27 PDF) : **21 dossiers sur
24 étaient refusés** au dépôt, non pas à cause d'un bug, mais parce qu'ils ne
contiennent pas de « fiche de fabrication ». Trois familles, toutes mesurées :

1. **Plan de fabrication SailPack** (BSG) — `ST 076.pdf`, `ST 092.pdf`,
   `ST 097.pdf`, `First38MSL_CC.pdf`, `GuepardRFG_CC.pdf`, `280224VA.pdf`,
   `ST 088.pdf`… Ces PDF **portent des données réelles** : `Date:`, `Sail:`,
   `Designer:`, `Boat:`, `Company:`, `Customer:`, `Filename:`, `Order Ref.:`,
   plus une zone `Panel layout` ou `Measurement Check List` avec la surface et
   les cotes `Geoluff` / `Geoleech` / `Geofoot` (= guindant / chute / bordure).
2. **Formulaire de prise de cotes** — `CavailleGV.pdf`, `GSE Oceanis411.pdf`,
   `MerianGvTanton43.pdf`… Aucune cote de fabrication (c'est un formulaire à
   remplir), mais le dossier existe et doit entrer dans l'application.
3. **PDF sans couche texte** — scan ou plan image (`boivent.pdf`,
   `inter Durand.pdf`, `WOSSA FICHE DE COTES.pdf`…). Aucun texte : sans OCR
   (binaire absent sur le poste d'essai), rien ne peut être lu — mais le
   document doit rester archivé et relu par un humain.

Principe
--------
**Rien n'est inventé.** La fiche de reprise porte :

- ce que le document imprime réellement (SailPack) avec sa provenance ;
- à défaut, la référence lue dans le NOM DU DOSSIER (traçée `nom_dossier`,
  confiance 0,55 — jamais présentée comme une lecture du document) ;
- un titre qui dit ce qu'est le document (« Plan de fabrication »,
  « Prise de cotes », « Document scanné ») — jamais « Fiche de fabrication » ;
- une anomalie explicite qui demande la saisie manuelle.

Le dépôt écrit donc le dossier en `a_valider` (RG3 : l'humain valide) au lieu
de le refuser, et les PDF restent attachés en pièces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from seamtech_search.fiches.extraction import ExtractionImpossible, analyser_pdf
from seamtech_search.fiches.modeles import Anomalie, ChampExtrait, Cotes, FicheExtraite

CODE_GABARIT_REPRISE = "FICHE_REPRISE_V1"
VERSION_GABARIT_REPRISE = 1

TITRE_PLAN = "Plan de fabrication"
TITRE_COTES = "Prise de cotes"
TITRE_SCAN = "Document scanné"

#: Traduction des codes de voile imprimés par SailPack — chaque entrée est
#: PROUVÉE par un document du corpus, jamais devinée :
#: - ``Gvsl`` : dossier `/VOILES 2024/CAVAILLE ST76` — la prise de cotes
#:   « CavailleGV.pdf » (grand-voile) est la même affaire que `ST 076.pak`.
#: - ``Gse``  : dossier `/VOILES 2024/FANTASIA ST63` — « GoleniaGseFantasia.pdf »,
#:   et les formulaires d'accessoires impriment « fourreau de gse » pour une
#:   housse de génois.
#: - ``MSL``  : la feuille SailPack imprime elle-même
#:   « Measurement rules Ref.: mainsails ».
#: - ``RFG``  : la feuille SailPack imprime elle-même
#:   « Measurement rules Ref.: headsails ».
#: Aucun autre code n'est traduit : « Smd », « Gk »… restent bruts, avec une
#: anomalie qui demande la précision — on ne devine pas un type de voile.
TRADUCTION_SAIL = {
    "GVSL": "Grand-voile",
    "GSE": "Génois",
    "MSL": "Grand-voile",
    "RFG": "Voile d'avant",
    # Le type est imprimé EN TOUTES LETTRES sur certaines planches : c'est une
    # lecture, pas une traduction d'abréviation.
    "GENNAKER": "Gennaker",
    "SPI": "Spi",
}

# Les étiquettes SailPack sont imprimées sur une ligne ou sur deux selon le
# moteur d'export : « Company: Seamtech Customer: X » ou « Company: Seamtech »
# puis « Customer: X ». Les motifs tolèrent donc le saut de ligne (re.S borné
# par l'étiquette suivante) — mesuré sur `ST 088.pdf` et `GuepardRFG_CC.pdf`.
_RE_DATE_SAIL = re.compile(
    r"Date\s*:\s*(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+Sail\s*:\s*(?P<sail>[^\s\n]+)", re.I | re.S
)
_RE_DESIGNER_BATEAU = re.compile(
    r"Designer\s*:\s*(?P<designer>[^\n]*?)\s+Boat\s*:\s*(?P<bateau>[^\n]*)", re.I | re.S
)
_RE_COMPANY_CLIENT = re.compile(
    r"Company\s*:\s*(?P<company>[^\n]*?)\s*\n?\s*Customer\s*:\s*(?P<client>[^\n]*)", re.I | re.S
)
_RE_FICHIER = re.compile(r"Filename\s*:\s*(?P<fichier>[^\s\n]+)", re.I)
_RE_ORDER_REF = re.compile(r"Order Ref\.?\s*:\s*(?P<code>[^\n]*)", re.I)
_RE_REGLES = re.compile(r"Measurement rules Ref\.?\s*:\s*(?P<regles>[A-Za-z]+)", re.I)
_RE_SURFACE = re.compile(r"(?:True area|Area)\s*=\s*(?P<surface>\d+(?:[.,]\d+)?)", re.I)
_RE_COTE = re.compile(
    r"\[?_*\s*(?P<label>Geo\s*luff|Geo\s*leech|Geo\s*foot|LL_*JL)\s*\]?\s*=\s*(?P<valeur>\d+(?:[.,]\d+)?)",
    re.I,
)
_RE_REFERENCE_CLE = re.compile(r"\s*/\s*(?P<reference>.+)$")
#: Une valeur ne doit jamais être l'ÉTIQUETTE SUIVANTE : mesuré sur
#: `GuepardRFG_CC.pdf`, où « Company: » et « Customer: » sont vides et où le
#: texte qui suit est « Filename: GuepardRFG_CC.pak ».
_RE_ETIQUETTE_SUIVANTE = re.compile(r"^(?:Company|Customer|Filename|Designer|Boat|Order Ref\.?|Date)\s*:", re.I)
_RE_NUMERO_COMMANDE = re.compile(r"(?:Commande\s*:?\s*)?#?\s*(?P<numero>[A-Z]{0,3}\s?\d[\w-]*)", re.I)


@dataclass(frozen=True)
class LectureSailpack:
    """Ce qu'une planche SailPack imprime réellement (aucune valeur inventée)."""

    date: str | None
    sail: str | None
    designer: str | None
    bateau: str | None
    company: str | None
    client: str | None
    reference: str | None
    code: str | None
    surface_m2: float | None
    guindant_m: float | None
    chute_m: float | None
    bordure_m: float | None
    regles: str | None

    @property
    def est_complete(self) -> bool:
        return bool(self.code or self.bateau or self.client)


def _lignes(chemin: Path) -> list[str]:
    """Lignes de texte du PDF (page par page), ou liste vide si illisible."""
    try:
        pages = analyser_pdf(chemin)
    except ExtractionImpossible:
        return []
    lignes: list[str] = []
    for page in pages:
        for ligne in page.lignes:
            texte = " ".join(mot.texte for mot in ligne.mots).strip()
            if texte:
                lignes.append(texte)
    return lignes


def _valeur_propre(brut: str | None) -> str | None:
    """Valeur d'étiquette utilisable, ou ``None`` (jamais l'étiquette suivante)."""
    if not brut:
        return None
    valeur = brut.strip(" :,-")
    if not valeur or _RE_ETIQUETTE_SUIVANTE.match(valeur):
        return None
    return valeur


def _numero_commande(brut: str | None) -> str | None:
    """« Commande #88 KKXBLOTZO » → « 88 » ; « Commande:ST92 - MLQIIEWQA » → « ST92 »."""
    if not brut:
        return None
    trouve = _RE_NUMERO_COMMANDE.search(brut.strip())
    if trouve is None:
        return brut.strip() or None
    return trouve.group("numero").strip().upper()


def _nombre(brut: str | None) -> float | None:
    if not brut:
        return None
    try:
        return float(brut.replace(",", "."))
    except ValueError:
        return None


def lire_sailpack(chemin: Path) -> LectureSailpack | None:
    """Lit une planche SailPack, ou ``None`` si le PDF n'en est pas une.

    Aucune exception n'est propagée : un PDF illisible ou sans texte n'est
    simplement pas une planche SailPack.
    """
    lignes = _lignes(chemin)
    if not lignes:
        return None
    joint = "\n".join(lignes)
    reference_commande = _RE_ORDER_REF.search(joint)
    # Le pied de page est imprimé ESPACÉ (« S a i l P a c k V3.0 ») sur une
    # partie des exports : on compare donc le texte sans espaces.
    sans_espaces = re.sub(r"\s+", "", joint).casefold()
    if "sailpack" not in sans_espaces and not (
        reference_commande is not None and re.search(r"Sail\s*:", joint, re.I)
    ):
        return None

    date = sail = designer = bateau = company = client = reference = code = regles = None
    surface = guindant = chute = bordure = None
    if (trouve := _RE_DATE_SAIL.search(joint)) is not None:
        date = trouve.group("date").strip() or None
        sail = trouve.group("sail").strip() or None
    if (trouve := _RE_DESIGNER_BATEAU.search(joint)) is not None:
        designer = trouve.group("designer").strip() or None
        bateau = trouve.group("bateau").strip() or None
    if (trouve := _RE_COMPANY_CLIENT.search(joint)) is not None:
        company = _valeur_propre(trouve.group("company"))
        brut_client = trouve.group("client").strip()
        if brut_client:
            decoupe = _RE_REFERENCE_CLE.search(brut_client)
            if decoupe is not None:
                client = _valeur_propre(brut_client[: decoupe.start()])
                reference = _numero_commande(decoupe.group("reference"))
            else:
                client = _valeur_propre(brut_client)
    if reference_commande is not None:
        candidat = reference_commande.group("code").strip()
        if re.search(r"\d", candidat):
            code = candidat
    if (trouve := _RE_REGLES.search(joint)) is not None:
        candidat_regles = trouve.group("regles").strip().casefold()
        if candidat_regles in {"mainsails", "headsails"}:
            regles = candidat_regles
    for ligne in lignes:
        if (trouve := _RE_SURFACE.search(ligne)) is not None:
            surface = surface if surface is not None else _nombre(trouve.group("surface"))
        for trouve in _RE_COTE.finditer(ligne):
            label = re.sub(r"[^a-z]", "", trouve.group("label").casefold())
            valeur = _nombre(trouve.group("valeur"))
            if valeur is None:
                continue
            if label in {"geoluff", "lljl"} and guindant is None:
                guindant = valeur
            elif label == "geoleech" and chute is None:
                chute = valeur
            elif label == "geofoot" and bordure is None:
                bordure = valeur

    return LectureSailpack(
        date=date,
        sail=sail,
        designer=designer,
        bateau=bateau,
        company=company,
        client=client,
        reference=reference,
        code=code,
        surface_m2=surface,
        guindant_m=guindant,
        chute_m=chute,
        bordure_m=bordure,
        regles=regles,
    )


def nom_sans_code(nom_dossier: str, code: str | None) -> str | None:
    """« CAVAILLE ST76 » + « ST76 » → « Cavaille » (nom du bateau du dossier)."""
    if not code:
        return None
    motif = re.escape(code).replace(r"\ ", r"\s*")
    reste = re.sub(motif, " ", nom_dossier, flags=re.I)
    reste = re.sub(r"\s{2,}", " ", reste).strip(" -_")
    return reste.title() if reste else None


def _champ(champ: str, valeur: str | None, methode: str, confiance: float, colonne: str) -> ChampExtrait:
    return ChampExtrait(
        champ=champ,
        valeur_brute=valeur,
        valeur_normalisee=valeur,
        methode=methode,
        confiance=confiance,
        page=0,
        zone=None,
        table_cible="fiche",
        colonne_cible=colonne,
    )


def construire_fiche_de_reprise(
    dossier: Path,
    pdfs: list[Path],
    classements: dict[str, str],
    code_dossier: str | None,
    code_fichier: str | None = None,
) -> FicheExtraite | None:
    """Fiche de reprise d'un dossier SANS fiche de fabrication, ou ``None``.

    ``None`` signifie « aucune référence lisible nulle part » : le dossier est
    alors réellement impossible à nommer, et le refus reste la bonne réponse —
    on n'invente pas un code.
    """
    lecture = next((lire for lire in (lire_sailpack(pdf) for pdf in pdfs) if lire is not None), None)
    # Priorité : ce que le DOCUMENT imprime, puis le NOM DU DOSSIER (c'est
    # l'affaire), puis seulement le nom du fichier — mesuré sur
    # `/VOILES 2024/NS44 ST88`, où « NS44 GV.pdf » donne « NS44 » (le nom du
    # bateau) alors que le dossier porte la référence « ST88 ».
    code = (lecture.code if lecture is not None and lecture.code else None) or code_dossier or code_fichier
    if not code and lecture is None:
        return None

    fiche = FicheExtraite(fichier_pdf=str(pdfs[0] if pdfs else dossier))
    fiche.gabarit_code = CODE_GABARIT_REPRISE
    # La persistance résout ``id_gabarit`` sur le COUPLE (code, version) :
    # sans version, la fiche gardait une provenance vide à l'écran.
    fiche.gabarit_version = VERSION_GABARIT_REPRISE
    methode_code = "document" if lecture is not None and lecture.code else (
        "nom_dossier" if code_dossier else "nom_fichier"
    )
    confiance_code = {"document": 0.9, "nom_fichier": 0.7, "nom_dossier": 0.55}[methode_code]
    if code:
        fiche.code = code
        fiche.champs.append(_champ("fiche.code", code, methode_code, confiance_code, "code"))

    if lecture is not None:
        fiche.titre = TITRE_PLAN
        if lecture.sail:
            libelle = TRADUCTION_SAIL.get(lecture.sail.upper())
            fiche.type_voile_libelle = libelle
            fiche.gamme = None if libelle else lecture.sail
        if lecture.bateau:
            fiche.bateau_nom = lecture.bateau
            fiche.champs.append(_champ("fiche.bateau", lecture.bateau, "document", 0.9, "nom"))
        if lecture.client:
            fiche.client_nom = lecture.client
            fiche.champs.append(_champ("fiche.client", lecture.client, "document", 0.9, "nom"))
        if lecture.reference:
            fiche.commande_numero = lecture.reference
            fiche.champs.append(
                _champ("fiche.commande", lecture.reference, "document", 0.8, "commande_numero")
            )
        if lecture.date:
            jour, mois, annee = lecture.date.split("/")
            fiche.date_dessin = f"{annee}-{mois.zfill(2)}-{jour.zfill(2)}"
            fiche.champs.append(_champ("fiche.date", fiche.date_dessin, "document", 0.9, "date_dessin"))
        if lecture.designer:
            fiche.dessinateur = lecture.designer
        if lecture.surface_m2 is not None or lecture.guindant_m is not None:
            fiche.cotes.append(
                Cotes(
                    jeu="finie",
                    slu_m=lecture.guindant_m,
                    sle_m=lecture.chute_m,
                    sf_m=lecture.bordure_m,
                    spa_m2=lecture.surface_m2,
                )
            )
        if lecture.sail and TRADUCTION_SAIL.get(lecture.sail.upper()) is None:
            fiche.anomalies.append(
                Anomalie(
                    code="type_voile_non_traduit",
                    gravite="moyenne",
                    message=(
                        f"Le plan imprime le code de voile « {lecture.sail} » : aucun document du "
                        "corpus ne prouve sa traduction — type à préciser à la main, il n'est pas deviné."
                    ),
                )
            )
    else:
        famille = next((classe for classe in classements.values()), None)
        if famille and famille.startswith("formulaire"):
            fiche.titre = TITRE_COTES
        elif famille and famille.startswith("PDF sans"):
            fiche.titre = TITRE_SCAN
        else:
            fiche.titre = TITRE_SCAN
        bateau = nom_sans_code(dossier.name, code)
        if bateau:
            fiche.bateau_nom = bateau
            fiche.champs.append(_champ("fiche.bateau", bateau, "nom_dossier", 0.5, "nom"))

    manquants = [
        libelle
        for libelle, valeur in (
            ("bateau", fiche.bateau_nom),
            ("client", fiche.client_nom),
            ("cotes de fabrication", fiche.cotes),
        )
        if not valeur
    ]
    fiche.anomalies.append(
        Anomalie(
            code="fiche_de_fabrication_absente",
            gravite="haute",
            message=(
                "Ce dossier ne contient pas de fiche de fabrication (« …ffab.pdf ») : la fiche "
                "a été créée depuis les documents réellement présents pour ne rien perdre. "
                + (f"À compléter à la main : {', '.join(manquants)}. " if manquants else "")
                + "Les PDF du dossier restent attachés en pièces."
            ),
        )
    )
    return fiche
