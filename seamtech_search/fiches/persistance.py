"""Écriture en base d'une fiche extraite (Lot B — plan v3.0 §10, §13, RG3/RG11).

Règles appliquées ici, toutes testées :
- **Transaction unique** : fiche, cotes, matériaux, galons, jonctions,
  finitions, options, renforts, champs extraits, mesures libres et anomalies
  sont écrits dans UNE transaction — tout ou rien.
- **RG3** : le statut d'arrivée est TOUJOURS ``a_valider`` ; aucun chemin de
  ce module ne peut écrire ``valide`` (c'est l'affaire de l'opérateur).
- **RG11 (idempotence)** : rejouer une extraction sur un PDF déjà importé ne
  duplique rien — la fiche ``a_valider`` est remplacée à l'identique près ;
  et n'écrase JAMAIS une fiche déjà validée par un humain.
- **Traçabilité** : chaque valeur arrive dans ``fiche_champ_extrait`` avec
  méthode, confiance, page, zone et version du gabarit.
- **Routage** : consommation des seuils ``config/seuils_confiance.json``
  (§10.3) — voie proposée = passage direct / relecture ciblée / reprise
  complète. Les seuils restent des points de départ à calibrer (Tâche 3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from seamtech_search.fiches import normalisation as norm
from seamtech_search.fiches.modeles import FicheExtraite

LOGGER = logging.getLogger("seamtech_search.fiches.persistance")

CHEMIN_SEUILS_DEFAUT = Path("config/seuils_confiance.json")
SEUIL_GABARIT_TEST = 0.90

# SQL écrits ici validés par pglast dans tests/test_fiches_sql_grammar.py.
_SQL_EXISTE_FICHE = "SELECT id_fiche, statut FROM fiche WHERE code = %s"
_SQL_LIRE_STATUT = "SELECT statut FROM fiche WHERE id_fiche = %s"
_SQL_SUPPRIMER_FICHE = "DELETE FROM fiche WHERE id_fiche = %s"
_SQL_ID_GABARIT = "SELECT id_gabarit FROM gabarit WHERE code = %s AND version = %s"
_SQL_CLIENT = "SELECT id_client FROM client WHERE nom = %s AND chantier IS NOT DISTINCT FROM %s"
_SQL_CLIENT_INS = "INSERT INTO client (nom, chantier) VALUES (%s, %s) RETURNING id_client"
_SQL_BATEAU = "SELECT id_bateau FROM bateau WHERE nom = %s AND taille IS NOT DISTINCT FROM %s"
_SQL_BATEAU_INS = "INSERT INTO bateau (nom, taille) VALUES (%s, %s) RETURNING id_bateau"
_SQL_TYPE_VOILE = "SELECT id_type_voile FROM type_voile WHERE code = %s"
_SQL_TYPE_VOILE_INS = "INSERT INTO type_voile (code, libelle, famille) VALUES (%s, %s, %s) RETURNING id_type_voile"
_SQL_MATERIAU = "SELECT id_materiau FROM materiau WHERE nom = %s"
_SQL_MATERIAU_INS = "INSERT INTO materiau (nom, grammage_g_m2) VALUES (%s, %s) RETURNING id_materiau"
_SQL_COMMANDE = "SELECT id_commande FROM commande WHERE numero = %s"
_SQL_COMMANDE_INS = "INSERT INTO commande (numero, id_client, quantite) VALUES (%s, %s, %s) RETURNING id_commande"
_SQL_FICHE_INS = (
    "INSERT INTO fiche (code, titre, id_type_voile, gamme, atelier, id_bateau, id_client, id_commande, "
    "quantite, tissu_texte, montage_type, montage_fil, notes, dessinateur, date_dessin, date_edition, "
    "fichier_source, id_gabarit, statut, score_qualite) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id_fiche"
)
_SQL_COTES_INS = (
    "INSERT INTO fiche_cotes (id_fiche, jeu, slu_m, sle_m, sf_m, shw_m, spa_m2, tetiere_cm, poids_kg) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_MATERIAU_FICHE_INS = (
    "INSERT INTO fiche_materiau (id_fiche, role, niveau, id_materiau, designation_texte, grammage_g_m2, mesure_mm) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
_SQL_GALON_INS = (
    "INSERT INTO fiche_galon (id_fiche, bande, couleur, largeur_mm, matiere, grammage_g_m2) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)
_SQL_JONCTION_INS = (
    "INSERT INTO fiche_jonction (id_fiche, nature, ordre, description, nb_zigzag, nb_points, espacement_mm, surplus) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_FINITION_INS = (
    "INSERT INTO fiche_finition (id_fiche, poste, valeur_texte, oeillet_type, sangle) VALUES (%s, %s, %s, %s, %s)"
)
_SQL_OPTION_INS = "INSERT INTO fiche_option (id_fiche, code, valeur_bool, valeur_texte) VALUES (%s, %s, %s, %s)"
_SQL_RENFORT_INS = (
    "INSERT INTO fiche_renfort (id_fiche, repere, quantite, forme, diametre_mm, matiere, description) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
_SQL_MESURE_LIBRE_INS = (
    "INSERT INTO fiche_mesure_libre (id_fiche, code, libelle, valeur_num, valeur_texte) VALUES (%s, %s, %s, %s, %s)"
)
_SQL_CHAMP_INS = (
    "INSERT INTO fiche_champ_extrait (id_fiche, champ, rang, table_cible, colonne_cible, valeur_brute, "
    "valeur_normalisee, methode, confiance, page, zone, version_gabarit) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_ANOMALIE_INS = "INSERT INTO fiche_anomalie (id_fiche, code, gravite, message) VALUES (%s, %s, %s, %s)"
_SQL_GABARIT_TEST_INS = (
    "INSERT INTO gabarit_test (code_gabarit, version_gabarit, nom_fichier, empreinte_pdf, attendu, notes) "
    "VALUES (%s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (code_gabarit, version_gabarit, nom_fichier) DO UPDATE "
    "SET empreinte_pdf = EXCLUDED.empreinte_pdf, attendu = EXCLUDED.attendu, notes = EXCLUDED.notes"
)
_SQL_GABARIT_TEST_LIRE = (
    "SELECT attendu FROM gabarit_test WHERE code_gabarit = %s AND nom_fichier = %s"
)

# NB « parité SQLite » : ce module n'écrit QUE via PostgreSQL (§17.1, décision
# de couche actée au Lot A). Les tables fiche_* n'existent pas côté SQLite et
# aucun chemin de secours SQLite ne sera ajouté.


def charger_seuils(chemin: Path | str | None = None) -> dict[str, float]:
    """Charge les seuils de passage direct par famille (SEAMTECH_SEUILS_CONFIANCE).

    Retourne un dictionnaire {famille: seuil}. Fichier absent → valeurs par
    défaut du §10.3, avec avertissement : ce sont des points de départ.
    """
    chemin_resolu = Path(chemin or os.environ.get("SEAMTECH_SEUILS_CONFIANCE") or CHEMIN_SEUILS_DEFAUT)
    familles = {"structurels": 0.98, "cotes": 0.95, "materiaux": 0.85, "finitions_options": 0.8, "notes_libres": 0.5}
    if not chemin_resolu.is_file():
        LOGGER.warning(
            "Seuils de confiance absents (%s) — défauts du §10.3 utilisés (conséquence : routage indicatif, à calibrer).",
            chemin_resolu,
        )
        return familles
    donnees = json.loads(chemin_resolu.read_text(encoding="utf-8"))
    for famille, valeurs in (donnees.get("familles") or {}).items():
        seuil = valeurs.get("seuil_passage_direct")
        if isinstance(seuil, (int, float)):
            familles[famille] = float(seuil)
    return familles


def famille_du_champ(champ: str) -> str:
    """Famille de routage d'un champ (noms du fichier seuils_confiance.json)."""
    if champ.startswith("cotes."):
        return "cotes"
    if champ.startswith(("materiau.", "galon.", "jonction.")):
        return "materiaux"
    if champ.startswith(("finition.", "option.", "options")) or champ in ("fiche.montage", "fiche.dessinateur", "fiche.fichier_source"):
        return "finitions_options"
    if champ.startswith("libre.") or champ.startswith("fiche.notes"):
        return "notes_libres"
    return "structurels"


def routage(fiche: FicheExtraite, seuils: dict[str, float] | None = None) -> dict[str, Any]:
    """Voie de traitement proposée (§10.3) — les seuils sont CONSOMMÉS ici.

    - ``passage_direct`` : gabarit reconnu, aucune anomalie RG16, tous les
      champs lus ≥ seuil de leur famille ;
    - ``relecture_ciblee`` : seuls les champs sous leur seuil sont listés ;
    - ``reprise_complete`` : gabarit inconnu ou plus de la moitié des champs
      structurels attendus absents.
    """
    seuils = seuils or charger_seuils()
    if fiche.gabarit_code is None:
        return {"voie": "reprise_complete", "motif": "gabarit inconnu", "champs_sous_seuil": []}
    sous_seuil = [
        {"champ": champ.champ, "confiance": champ.confiance, "seuil": seuils.get(famille_du_champ(champ.champ), 0.9)}
        for champ in fiche.tous_les_champs()
        if champ.valeur_normalisee is not None and champ.confiance < seuils.get(famille_du_champ(champ.champ), 0.9)
    ]
    codes_attendus = {"fiche.code", "fiche.client", "cotes.finie.slu_m", "cotes.finie.sf_m"}
    lus = {champ.champ for champ in fiche.champs}
    manquants = len([cible for cible in codes_attendus if cible not in lus])
    if fiche.anomalies:
        return {
            "voie": "relecture_ciblee",
            "motif": f"{len(fiche.anomalies)} anomalie(s) RG16",
            "champs_sous_seuil": sous_seuil,
        }
    if manquants >= 3:
        return {
            "voie": "reprise_complete",
            "motif": f"{manquants} champs structurels absents",
            "champs_sous_seuil": sous_seuil,
        }
    if sous_seuil:
        return {"voie": "relecture_ciblee", "motif": f"{len(sous_seuil)} champ(s) sous le seuil", "champs_sous_seuil": sous_seuil}
    return {"voie": "passage_direct", "motif": "tous les champs au-dessus de leur seuil, aucune anomalie", "champs_sous_seuil": []}


def _resoudre(
    cursor: Any,
    selection: str,
    insertion: str,
    parametres_recherche: tuple,
    parametres_insertion: tuple | None = None,
) -> int:
    """Trouve l'identifiant du référentiel ou le crée (recherche-ou-création).

    Les paramètres de recherche et d'insertion diffèrent quand la colonne de
    recherche est plus étroite que la ligne à créer (ex. matériau : nom seul).
    """
    cursor.execute(selection, parametres_recherche)
    ligne = cursor.fetchone()
    if ligne is not None:
        return int(ligne[0])
    cursor.execute(insertion, parametres_insertion if parametres_insertion is not None else parametres_recherche)
    return int(cursor.fetchone()[0])


def _id_type_voile(cursor: Any, fiche: FicheExtraite) -> int | None:
    if not fiche.type_voile_libelle:
        return None
    code = re_sub_code(fiche.type_voile_libelle)
    famille = next(
        (fam for motif, fam in _TYPES_VOILE_SIMPLE if norm.sans_accents(fiche.type_voile_libelle) == motif),
        None,
    )
    cursor.execute(_SQL_TYPE_VOILE, (code,))
    ligne = cursor.fetchone()
    if ligne is not None:
        return int(ligne[0])
    cursor.execute(_SQL_TYPE_VOILE_INS, (code, fiche.type_voile_libelle, famille))
    return int(cursor.fetchone()[0])


_TYPES_VOILE_SIMPLE = (
    ("spi asymetrique", "portant"),
    ("spi symetrique", "portant"),
    ("spi", "portant"),
    ("genois", "interface"),
    ("grand-voile", "interface"),
    ("trinquette", "interface"),
    ("solent", "interface"),
    ("code 0", "portant"),
    ("staysail", "portant"),
)


def re_sub_code(texte: str) -> str:
    """« Spi Asymétrique » → « spi_asymetrique » (code de référentiel)."""
    return re.sub(r"[^a-z0-9]+", "_", norm.sans_accents(texte)).strip("_")


def ecrire_fiche(index: Any, fiche: FicheExtraite) -> tuple[int, str]:
    """Écrit la fiche et ses dépendances en UNE transaction PostgreSQL.

    Retourne (id_fiche, action) avec action parmi :
    - ``creee`` : première écriture ;
    - ``remplacee`` : une version ``a_valider`` existait → remplacée (RG11) ;
    - ``conservee_validee`` : fiche déjà VALIDÉE → rien n'est touché (RG11).

    Lève ``ValueError`` si la fiche n'a pas de code (une fiche sans référence
    ne peut pas être posée en base : reprise complète d'abord).
    """
    if not fiche.code:
        raise ValueError("Fiche sans code : écriture refusée (reprise complète requise avant stockage).")
    action = "creee"
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_EXISTE_FICHE, (fiche.code,))
            existante = cursor.fetchone()
            if existante is not None:
                id_existante, statut = int(existante[0]), existante[1]
                if statut == "valide":
                    LOGGER.warning(
                        "Fiche %s déjà VALIDÉE (id %d) : ré-extraction ignorée — aucune donnée validée n'est écrasée (RG11).",
                        fiche.code,
                        id_existante,
                    )
                    return id_existante, "conservee_validee"
                cursor.execute(_SQL_SUPPRIMER_FICHE, (id_existante,))  # cascades : enfants + champs extraits
                action = "remplacee"
                LOGGER.info("Fiche %s (a_valider) remplacée par la nouvelle extraction (RG11).", fiche.code)

            id_type_voile = _id_type_voile(cursor, fiche)
            id_client = None
            if fiche.client_nom:
                id_client = _resoudre(cursor, _SQL_CLIENT, _SQL_CLIENT_INS, (fiche.client_nom, fiche.client_chantier))
            id_bateau = None
            if fiche.bateau_nom:
                id_bateau = _resoudre(cursor, _SQL_BATEAU, _SQL_BATEAU_INS, (fiche.bateau_nom, fiche.bateau_taille))
            id_commande = None
            if fiche.commande_numero:
                cursor.execute(_SQL_COMMANDE, (fiche.commande_numero,))
                ligne = cursor.fetchone()
                if ligne is None:
                    cursor.execute(_SQL_COMMANDE_INS, (fiche.commande_numero, id_client, fiche.quantite))
                    id_commande = int(cursor.fetchone()[0])
                else:
                    id_commande = int(ligne[0])

            id_gabarit = None
            if fiche.gabarit_code:
                cursor.execute(_SQL_ID_GABARIT, (fiche.gabarit_code, fiche.gabarit_version))
                ligne = cursor.fetchone()
                id_gabarit = int(ligne[0]) if ligne else None

            cursor.execute(
                _SQL_FICHE_INS,
                (
                    fiche.code,
                    fiche.titre,
                    id_type_voile,
                    fiche.gamme,
                    fiche.atelier,
                    id_bateau,
                    id_client,
                    id_commande,
                    fiche.quantite,
                    fiche.tissu_texte,
                    fiche.montage_type,
                    fiche.montage_fil,
                    fiche.notes,
                    fiche.dessinateur,
                    fiche.date_dessin,
                    fiche.date_edition,
                    fiche.fichier_source,
                    id_gabarit,
                    "a_valider",  # RG3 : jamais « valide » à l'arrivée
                    fiche.score_qualite(),
                ),
            )
            id_fiche = int(cursor.fetchone()[0])

            for cotes in fiche.cotes:
                cursor.execute(
                    _SQL_COTES_INS,
                    (id_fiche, cotes.jeu, cotes.slu_m, cotes.sle_m, cotes.sf_m, cotes.shw_m, cotes.spa_m2, cotes.tetiere_cm, cotes.poids_kg),
                )
            for materiau in fiche.materiaux:
                id_materiau = None
                if materiau.designation:
                    id_materiau = _resoudre(
                        cursor,
                        _SQL_MATERIAU,
                        _SQL_MATERIAU_INS,
                        (materiau.designation,),
                        (materiau.designation, materiau.grammage_g_m2),
                    )
                cursor.execute(
                    _SQL_MATERIAU_FICHE_INS,
                    (id_fiche, materiau.role, materiau.niveau, id_materiau, materiau.designation, materiau.grammage_g_m2, materiau.mesure_mm),
                )
            for galon in fiche.galons:
                cursor.execute(_SQL_GALON_INS, (id_fiche, galon.bande, galon.couleur, galon.largeur_mm, galon.matiere, galon.grammage_g_m2))
            for jonction in fiche.jonctions:
                cursor.execute(
                    _SQL_JONCTION_INS,
                    (id_fiche, jonction.nature, jonction.ordre, jonction.description, jonction.nb_zigzag, jonction.nb_points, jonction.espacement_mm, jonction.surplus),
                )
            for finition in fiche.finitions:
                cursor.execute(_SQL_FINITION_INS, (id_fiche, finition.poste, finition.valeur_texte, finition.oeillet_type, finition.sangle))
            for option in fiche.options:
                cursor.execute(_SQL_OPTION_INS, (id_fiche, option.code, option.valeur_bool, option.valeur_texte))
            for renfort in fiche.renforts:
                cursor.execute(_SQL_RENFORT_INS, (id_fiche, renfort.repere, renfort.quantite, renfort.forme, renfort.diametre_mm, renfort.matiere, renfort.description))
            for libre in fiche.mesures_libres:
                curseur_libelle = libre.valeur_brute.split(":", 1)
                libelle = curseur_libelle[0].strip() if len(curseur_libelle) > 1 else None
                cursor.execute(
                    _SQL_MESURE_LIBRE_INS,
                    (
                        id_fiche,
                        libre.champ.removeprefix("libre."),
                        libelle,
                        None if libre.valeur_normalisee is None else float(libre.valeur_normalisee),
                        libre.valeur_brute,
                    ),
                )
            for champ in fiche.tous_les_champs():
                if champ.valeur_normalisee is None and champ.valeur_brute is None:
                    continue
                cursor.execute(
                    _SQL_CHAMP_INS,
                    (
                        id_fiche,
                        champ.champ,
                        champ.rang,
                        champ.table_cible,
                        champ.colonne_cible,
                        champ.valeur_brute,
                        champ.valeur_normalisee,
                        champ.methode,
                        champ.confiance,
                        champ.page,
                        json.dumps(champ.zone.en_dict()) if champ.zone else None,
                        fiche.gabarit_version,
                    ),
                )
            for anomalie in fiche.anomalies:
                cursor.execute(_SQL_ANOMALIE_INS, (id_fiche, anomalie.code, anomalie.gravite, anomalie.message))
    LOGGER.info("Fiche %s écrite (%s, id %d, statut a_valider).", fiche.code, action, id_fiche)
    return id_fiche, action


# ---------------------------------------------------------------------------
# Vérité terrain du gabarit de référence (7792-SO) — non-régression (§13)
# ---------------------------------------------------------------------------

VERITE_7792: dict[str, object] = {
    "fiche.code": "7792-SO",
    "fiche.titre": "Voile de portant",
    "fiche.quantite": 1,
    "client": "Sailonet",
    "bateau": "29er",
    "type_voile": "Spi Asymétrique",
    "gamme": "Medium Régate",
    "cotes.finie.slu_m": 6.6,
    "cotes.finie.sle_m": 5.5,
    "cotes.finie.sf_m": 3.08,
    "cotes.finie.shw_m": 3.14,
    "cotes.finie.spa_m2": 15.71,
    "cotes.finie.tetiere_cm": 3.0,
    "cotes.finie.poids_kg": 0.7,
    "materiau.epaisseur.1": "Monofilm K903",
    "materiau.epaisseur.1.mesure_mm": 190.0,
    "galon.guindant": "Bleu",
    "galon.chute": "Rouge · Blanc",
    "jonction.laizes.nb_zigzag": 1,
    "jonction.laizes.nb_points": 6,
    "jonction.laizes.espacement_mm": 15.0,
    "jonction.horizontale.nb_zigzag": 2,
    "jonction.horizontale.nb_points": 6,
    "jonction.horizontale.espacement_mm": 30.0,
    "finition.amure": "Œillet SR12",
    "option.velcro_anti_deroulement": False,
    "option.protection_anti_uv": False,
    "renfort.1.quantite": 2,
    "renfort.1.diametre_mm": 200.0,
    "date_dessin": "2026-03-06",
    "date_edition": "2026-03-06",
}


def _valeur_extraite(fiche: FicheExtraite, cible: str) -> object:
    """Lit dans la fiche extraite la valeur visée par une clé de vérité."""
    if cible == "client":
        return fiche.client_nom
    if cible == "bateau":
        return fiche.bateau_nom
    if cible == "type_voile":
        return fiche.type_voile_libelle
    if cible == "gamme":
        return fiche.gamme
    if cible == "date_dessin":
        return fiche.date_dessin
    if cible == "date_edition":
        return fiche.date_edition
    if cible.startswith("cotes."):
        _, jeu, colonne = cible.split(".")
        jeu_cotes = next((c for c in fiche.cotes if c.jeu == jeu), None)
        return getattr(jeu_cotes, colonne, None) if jeu_cotes else None
    morceaux = cible.split(".")
    if morceaux[0] == "materiau" and len(morceaux) >= 3 and morceaux[1] == "epaisseur":
        niveau = int(morceaux[2])
        attribut = morceaux[3] if len(morceaux) == 4 else "designation"
        materiau = next((m for m in fiche.materiaux if m.role == "epaisseur" and m.niveau == niveau), None)
        return getattr(materiau, attribut, None) if materiau else None
    if morceaux[0] == "galon":
        bande = morceaux[1]
        attribut = morceaux[2] if len(morceaux) == 3 else "couleur"
        galon = next((g for g in fiche.galons if g.bande == bande), None)
        return getattr(galon, attribut, None) if galon else None
    if morceaux[0] == "jonction":
        nature = morceaux[1]
        attribut = morceaux[2] if len(morceaux) == 3 else "description"
        jonction = next((j for j in fiche.jonctions if j.nature == nature), None)
        return getattr(jonction, attribut, None) if jonction else None
    if morceaux[0] == "finition":
        finition = next((f for f in fiche.finitions if f.poste == morceaux[1]), None)
        return getattr(finition, "valeur_texte", None) if finition else None
    if morceaux[0] == "option":
        option = next((o for o in fiche.options if o.code == morceaux[1]), None)
        return option.valeur_bool if option else None
    if morceaux[0] == "renfort":
        renfort = fiche.renforts[int(morceaux[1]) - 1] if len(fiche.renforts) >= int(morceaux[1]) else None
        attribut = morceaux[2] if len(morceaux) == 3 else "quantite"
        return getattr(renfort, attribut, None) if renfort else None
    if cible.startswith("fiche."):
        return getattr(fiche, cible.split(".", 1)[1], None)
    return None


def evaluer_verite(fiche: FicheExtraite, attendu: dict[str, object]) -> tuple[float, list[dict[str, object]]]:
    """Taux de champs corrects + détail des écarts (banc gabarit_test).

    Nombres comparés à 1 % relatif près (impressions PDF), textes comparés
    après normalisation accents/casse/espaces.
    """
    corrects = 0
    ecarts: list[dict[str, object]] = []
    for cible, voulu in attendu.items():
        lu = _valeur_extraite(fiche, cible)
        if isinstance(voulu, (int, float)) and not isinstance(voulu, bool):
            ok = lu is not None and isinstance(lu, (int, float)) and abs(float(lu) - float(voulu)) <= max(0.005, abs(float(voulu)) * 0.01)
        elif isinstance(voulu, bool):
            ok = lu is voulu
        else:
            ok = lu is not None and norm.sans_accents(str(lu)) == norm.sans_accents(str(voulu))
        if ok:
            corrects += 1
        else:
            ecarts.append({"champ": cible, "attendu": voulu, "lu": lu})
    return round(corrects / len(attendu), 4) if attendu else 0.0, ecarts


def initialiser_verite_7792(index: Any, chemin_pdf: Path | None = None) -> None:
    """Enregistre la vérité 7792 dans ``gabarit_test`` (non-régression du gabarit)."""
    empreinte = None
    if chemin_pdf is not None and Path(chemin_pdf).is_file():
        empreinte = hashlib.sha256(Path(chemin_pdf).read_bytes()).hexdigest()
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                _SQL_GABARIT_TEST_INS,
                (
                    "FICHE_PORTANT_V1",
                    1,
                    "fiche-7792-SO_ffab.pdf",
                    empreinte,
                    json.dumps(VERITE_7792, ensure_ascii=False),
                    "Vérité §13 du plan v3.0 — à étendre avec les fiches réelles (Tâche 3).",
                ),
            )


def verifier_non_regression(index: Any, fiche: FicheExtraite, nom_fichier: str = "fiche-7792-SO_ffab.pdf") -> dict[str, Any]:
    """Compare une fiche extraite à la vérité enregistrée (banc gabarit_test)."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_GABARIT_TEST_LIRE, (fiche.gabarit_code, nom_fichier))
            ligne = cursor.fetchone()
    if ligne is None:
        raise ValueError(f"Aucune vérité enregistrée pour {fiche.gabarit_code}/{nom_fichier}.")
    attendu = ligne[0] if isinstance(ligne[0], dict) else json.loads(ligne[0])
    seuil = SEUIL_GABARIT_TEST
    taux, ecarts = evaluer_verite(fiche, attendu)
    return {"taux": taux, "seuil": seuil, "conforme": taux >= seuil, "ecarts": ecarts, "total": len(attendu)}
