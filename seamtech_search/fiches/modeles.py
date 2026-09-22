"""Structures de la fiche extraite (Lot B — plan v3.0 §5-6, §13).

Chaque valeur porte sa TRAÇABILÉ (:class:`ChampExtrait`) : d'où elle vient
(méthode), avec quelle confiance, et où dans le PDF (zone) — le contrat du
projet : « toute valeur extraite est enregistrée avec sa méthode, sa
confiance, sa zone dans le PDF et ses éventuelles corrections ».

Ces modèles décrivent le résultat de l'extraction ; l'écriture en base est
l'affaire de :func:`seamtech_search.fiches.extraction.ecrire_fiche` (tables
du Lot A). Un modèle ici ne contient jamais d'identifiant de base.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Zone(BaseModel):
    """Rectangle d'origine d'une valeur dans le PDF (points pdfplumber)."""

    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 0

    def en_dict(self) -> dict[str, float | int]:
        return {"x0": round(self.x0, 1), "y0": round(self.y0, 1), "x1": round(self.x1, 1), "y1": round(self.y1, 1), "page": self.page}


class ChampExtrait(BaseModel):
    """Une valeur lue, avec sa traçabilité complète (→ fiche_champ_extrait)."""

    champ: str  # clé de schéma : « fiche.code », « cotes.finie.slu_m », « materiau.epaisseur.3 »…
    valeur_brute: str | None = None  # tel que lu dans le PDF
    valeur_normalisee: str | None = None  # après conversion (unités, décimales)
    methode: str = "gabarit"  # gabarit | llm_local | humain | defaut (plan v3.0 §10.1)
    confiance: float = Field(default=0.0, ge=0.0, le=1.0)
    page: int = 0
    zone: Zone | None = None
    rang: int | None = None  # n° d'épaisseur, n° de jonction…
    table_cible: str | None = None
    colonne_cible: str | None = None


class Cotes(BaseModel):
    """Un jeu de cotes (« dessin » ou « finie ») → fiche_cotes."""

    jeu: str  # dessin | finie
    slu_m: float | None = None
    sle_m: float | None = None
    sf_m: float | None = None
    shw_m: float | None = None
    spa_m2: float | None = None
    tetiere_cm: float | None = None
    poids_kg: float | None = None
    champs: list[ChampExtrait] = Field(default_factory=list)


class Materiau(BaseModel):
    """Matériau de la fiche → fiche_materiau (rôle tissu_principal, cache_insignia, epaisseur)."""

    role: str
    niveau: int | None = None  # 1..10 pour les épaisseurs
    designation: str | None = None  # valeur brute : « Monofilm K903 »
    grammage_g_m2: float | None = None
    mesure_mm: float | None = None
    champs: list[ChampExtrait] = Field(default_factory=list)


class Galon(BaseModel):
    """Galon cousu → fiche_galon (bande guindant/chute/bordure)."""

    bande: str
    couleur: str | None = None
    largeur_mm: float | None = None
    matiere: str | None = None
    grammage_g_m2: float | None = None
    champs: list[ChampExtrait] = Field(default_factory=list)


class Jonction(BaseModel):
    """Jonction/couture → fiche_jonction (laizes, horizontale, verticale)."""

    nature: str  # laizes | horizontale | verticale
    ordre: int = 1
    description: str | None = None
    nb_zigzag: int | None = None
    nb_points: int | None = None
    espacement_mm: float | None = None
    surplus: str | None = None
    champs: list[ChampExtrait] = Field(default_factory=list)


class Finition(BaseModel):
    """Poste de finition → fiche_finition (amure, ecoute, drisse…)."""

    poste: str
    valeur_texte: str | None = None
    oeillet_type: str | None = None
    sangle: bool | None = None
    champs: list[ChampExtrait] = Field(default_factory=list)


class OptionFiche(BaseModel):
    """Option → fiche_option (velcro, retenue contre-écoute, anti-UV…)."""

    code: str
    valeur_bool: bool | None = None
    valeur_texte: str | None = None
    champs: list[ChampExtrait] = Field(default_factory=list)


class Renfort(BaseModel):
    """Renfort local → fiche_renfort (œillets, sangles de renfort…)."""

    repere: str | None = None
    quantite: int | None = None
    forme: str | None = None
    diametre_mm: float | None = None
    matiere: str | None = None
    description: str | None = None
    champs: list[ChampExtrait] = Field(default_factory=list)


class Anomalie(BaseModel):
    """Constat RG16, indépendant de la confiance → fiche_anomalie."""

    code: str  # cote_hors_plage | surface_incoherente | cotes_incoherentes | champ_manquant | unite_suspecte
    gravite: str = "moyenne"  # faible | moyenne | forte
    message: str = ""


class FicheExtraite(BaseModel):
    """Le résultat complet de l'extraction d'un PDF par un gabarit.

    Aucune valeur n'est ici « validée » : le statut d'arrivée en base est
    toujours ``a_valider`` (RG3 — l'humain valide, la machine propose).
    """

    code: str | None = None
    titre: str | None = None
    gamme: str | None = None
    type_voile_libelle: str | None = None
    client_nom: str | None = None
    client_chantier: str | None = None
    bateau_nom: str | None = None
    bateau_taille: str | None = None
    commande_numero: str | None = None
    atelier: str | None = None
    quantite: int = 1
    tissu_texte: str | None = None
    montage_type: str | None = None
    montage_fil: str | None = None
    logo: str | None = None
    notes: str | None = None
    dessinateur: str | None = None
    date_dessin: str | None = None  # ISO (AAAA-MM-JJ) après normalisation
    date_edition: str | None = None
    fichier_source: str | None = None
    cible_code: str | None = None
    cotes: list[Cotes] = Field(default_factory=list)
    materiaux: list[Materiau] = Field(default_factory=list)
    galons: list[Galon] = Field(default_factory=list)
    jonctions: list[Jonction] = Field(default_factory=list)
    finitions: list[Finition] = Field(default_factory=list)
    options: list[OptionFiche] = Field(default_factory=list)
    renforts: list[Renfort] = Field(default_factory=list)
    mesures_libres: list[ChampExtrait] = Field(default_factory=list)  # RG6 : filet de sécurité
    champs: list[ChampExtrait] = Field(default_factory=list)  # champs de tête (code, client…)
    anomalies: list[Anomalie] = Field(default_factory=list)
    gabarit_code: str | None = None
    gabarit_version: int | None = None
    fichier_pdf: str = ""

    @field_validator("quantite", mode="before")
    @classmethod
    def _quantite_entiere(cls, valeur: object) -> object:
        if isinstance(valeur, str) and valeur.strip().isdigit():
            return int(valeur.strip())
        return valeur

    def tous_les_champs(self) -> list[ChampExtrait]:
        """Tous les ChampExtrait de la fiche (traçabilité à écrire en base)."""
        tous = list(self.champs) + list(self.mesures_libres)
        for groupe in (self.cotes, self.materiaux, self.galons, self.jonctions, self.finitions, self.options, self.renforts):
            for element in groupe:
                tous.extend(element.champs)
        return tous

    def score_qualite(self) -> float | None:
        """Moyenne des confiances des champs lus (score_qualite en base)."""
        confiances = [champ.confiance for champ in self.tous_les_champs() if champ.valeur_normalisee is not None]
        if not confiances:
            return None
        return round(sum(confiances) / len(confiances), 3)
