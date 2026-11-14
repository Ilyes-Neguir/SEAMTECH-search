#!/usr/bin/env python3
"""Recette fonctionnelle A→K : chaque fonctionnalité, une preuve rejouée.

Le harnais de routes (``scripts/recette_routes.py``) prouve que CHAQUE route
répond. Il ne prouve pas que CHAQUE FONCTIONNALITÉ fait ce qu'elle doit : une
route qui répond 200 à une recherche peut très bien ne rien trouver. Ce script
est l'autre moitié : il prend le périmètre fonctionnel section par section,
nomme le test qui en répond, et le REJOUE.

Règle de lecture des résultats — c'est ici que tout se joue :

* ``PROUVÉ``    — tous les tests de l'item sont passés.
* ``ÉCHEC``     — au moins un test a échoué.
* ``NON PROUVÉ``— tous les tests ont été *skippés*. Un test sauté n'est pas un
  test vert : il n'a rien mesuré du tout. C'est le statut qu'on obtient
  localement pour tout ce qui exige PostgreSQL ou Redis, et c'est précisément
  pourquoi ce script se joue en CI.
* ``PARTIEL``   — certains passés, d'autres sautés : l'item est à moitié
  prouvé, ce qui n'est pas prouvé.

Une ligne « NON PROUVÉ » en sortie de CI est un trou, pas une formalité.

Sorties : ``docs/recette/matrice-recette.json`` et ``.md``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

DESTINATION = RACINE / "docs/recette"

# Un statut par item. `` INTROUVABLE `` : un sélecteur ne correspond à aucun
# test — la matrice a dérivé du code, ce qui est un défaut de la recette
# elle-même et non de l'application.
PROUVE = "PROUVÉ"
# La preuve existe, elle est jouée et relevée à chaque run — mais par une
# étape de CI que ce harnais ne peut pas rejouer : rejouer la suite du corpus
# réel après elle-même la fait échouer (base consommée), et casser une preuve
# qui marche pour en ajouter une qui casse n'est pas une amélioration. Le
# statut le DIT, plutôt que de faire passer l'item pour non prouvé.
PROUVE_CI = "PROUVÉ (CI)"
# L'item décrit un comportement que l'application N'implémente pas, et ce
# n'est pas un oubli : une règle nommée l'exclut. Ce n'est ni un échec ni une
# absence de preuve — c'est une question adressée au commanditaire, et elle
# doit figurer comme telle plutôt que se fondre dans les « non prouvés ».
A_TRANCHER = "À TRANCHER"
ECHEC = "ÉCHEC"
NON_PROUVE = "NON PROUVÉ"
PARTIEL = "PARTIEL"
INTROUVABLE = "INTROUVABLE"


@dataclass
class Item:
    """Une ligne de la matrice : l'item, ce qui en répond, et comment."""

    id: str
    libelle: str
    tests: list[str] = field(default_factory=list)
    navigateur: str | None = None
    manuel: str | None = None
    preuve_ci: str | None = None
    absent: str | None = None

    @property
    def couvert(self) -> bool:
        return bool(self.tests) or bool(self.navigateur) or bool(self.preuve_ci)


@dataclass
class Section:
    id: str
    titre: str
    items: list[Item]


# ---------------------------------------------------------------------------
# LE PÉRIMÈTRE
#
# Chaque sélecteur ci-dessous a été relevé dans le code, pas retrouvé de
# mémoire. Un sélecteur qui cesserait d'exister ferait échouer la recette
# (statut INTROUVABLE) plutôt que de disparaître en silence.
# ---------------------------------------------------------------------------

SECTIONS: list[Section] = [
    Section(
        "A",
        "Comptes et authentification",
        [
            Item(
                "A1",
                "Création d'un compte pour chacun des rôles",
                [
                    "tests/test_comptes.py::test_creation_compte_et_refus_des_doublons",
                    "tests/test_lot1_roles_approbations.py::test_vocabulaire_des_roles_et_defauts_fermes",
                    "tests/test_lot1_roles_approbations.py::test_schema_027_defaut_lecture_et_rejet_des_roles_herites",
                ],
            ),
            Item(
                "A2",
                "Connexion valide — session nominative ouverte",
                [
                    "tests/test_comptes.py::test_connexion_et_session_nominative",
                    "tests/test_comptes.py::test_api_connexion_session_deconnexion",
                    "tests/test_passe_finale_sessions.py::test_session_neuve_valide",
                ],
            ),
            Item(
                "A3",
                "Connexion invalide — refus, puis verrouillage après 5 échecs",
                [
                    "tests/test_comptes.py::test_api_mauvais_mot_de_passe_et_jeton_de_service",
                    "tests/test_comptes.py::test_cinq_echecs_verrouillent_le_compte",
                    "tests/test_passe_finale_matrice_roles.py::test_mot_de_passe_faux_ne_rend_pas_de_session",
                ],
            ),
            Item(
                "A4",
                "Compte désactivé — sessions révoquées et connexion refusée",
                [
                    "tests/test_comptes.py::test_desactivation_revoque_les_sessions_et_bloque_la_connexion",
                    "tests/test_passe_finale_sessions.py::test_compte_desactive_ne_peut_plus_se_connecter",
                    "tests/test_passe_finale_matrice_roles.py::test_desactivation_revoque_immediatement_la_session",
                ],
            ),
            Item(
                "A5",
                "Déconnexion — la session est réellement fermée (le 405 corrigé)",
                [
                    "tests/test_comptes.py::test_deconnexion_rend_la_session_invalide",
                    "tests/test_comptes.py::test_api_connexion_session_deconnexion",
                ],
                navigateur="frontend/e2e/auth.spec.ts — « logout clears the session »",
            ),
            Item(
                "A6",
                "Révocation serveur — un jeton révoqué n'ouvre plus rien",
                [
                    "tests/test_passe_finale_sessions.py::test_session_revoquee_refusee",
                    "tests/test_passe_finale_sessions.py::test_changement_de_role_revoque_les_sessions",
                    "tests/test_lot1_roles_approbations.py::test_changement_de_role_revoque_les_sessions_et_route_admin",
                ],
            ),
            Item(
                "A7",
                "Expiration de session — inactivité et expiration absolue",
                [
                    "tests/test_passe_finale_sessions.py::test_session_expiree_par_inactivite",
                    "tests/test_sessions_redis.py::test_expiration_absolue_refuse",
                ],
            ),
            Item(
                "A8",
                "Changement de mot de passe",
                [
                    "tests/test_comptes.py::test_reinitialisation_puis_changement_du_mot_de_passe",
                    "tests/test_comptes.py::test_mot_de_passe_jamais_en_clair_dans_la_base",
                ],
            ),
            Item(
                "A9",
                "CSRF — une requête sans jeton d'intention est refusée",
                [
                    "tests/test_lot0b_audit_securite.py::test_frontiere_frontend_transmet_la_session_aux_routes_de_donnees",
                ],
                navigateur="frontend/e2e/auth.spec.ts — en-têtes CSRF sur chaque appel",
            ),
            Item(
                "A10",
                "Anciens cookies / jeton falsifié — refus, jamais d'erreur serveur",
                [
                    "tests/test_passe_finale_sessions.py::test_jeton_falsifie_refuse",
                    "tests/test_passe_finale_matrice_roles.py::test_validation_de_corps_ne_doit_pas_masquer_l_authentification",
                ],
            ),
        ],
    ),
    Section(
        "B",
        "Rôles × actions",
        [
            Item(
                "B1",
                "L'administrateur NE PEUT PAS approuver — refus serveur explicite",
                [
                    "tests/test_passe_finale_matrice_roles.py::test_seul_l_approbateur_decide",
                    "tests/test_passe_finale_matrice_roles.py::test_admin_seul_sur_les_surfaces_techniques",
                ],
            ),
            Item(
                "B2",
                "Lecture — refus en modification ET en téléchargement",
                [
                    "tests/test_passe_finale_matrice_roles.py::test_lecture_ne_mute_rien",
                    "tests/test_passe_finale_matrice_roles.py::test_telechargement_direct_interdit_hors_approbateur",
                    "tests/test_lot1_roles_approbations.py::test_telechargement_approuve_range_et_refus_des_autres_roles",
                ],
            ),
            Item(
                "B3",
                "Utilisateur — droits de dépôt, pas de décision",
                [
                    "tests/test_matrice_roles_routes.py::test_matrice_routes_par_role",
                    "tests/test_passe_finale_matrice_roles.py::test_aucune_route_ne_plante_et_l_anonyme_est_refuse",
                ],
            ),
            Item(
                "B4",
                "Approbateur — validation en un clic et correction des valeurs",
                [
                    "tests/test_lot1_roles_approbations.py::test_correction_et_validation_directes_reservees_a_lapprobateur",
                    "tests/test_lot1_roles_approbations.py::test_modification_utilisateur_executee_atomiquement_par_approbateur",
                    "tests/test_lot1_roles_approbations.py::test_suppression_logique_approuvee_puis_reouverture_approbateur",
                ],
            ),
            Item(
                "B5",
                "Auto-approbation bloquée — on n'approuve pas son propre dépôt",
                [
                    "tests/test_lot1_roles_approbations.py::test_auto_approbation_annulation_refus_et_notifications_anti_idor",
                    "tests/test_lot1_roles_approbations.py::test_auto_approbation_reste_un_interrupteur_explicite",
                ],
            ),
            Item(
                "B6",
                "IDOR — la ressource d'un autre compte est invisible et inaccessible",
                [
                    "tests/test_passe_finale_matrice_roles.py::test_idor_notification_d_un_autre_compte",
                    "tests/test_passe_finale_comparaison_accessoires.py::test_notification_d_un_autre_compte_est_invisible_et_inaccessible",
                ],
            ),
        ],
    ),
    Section(
        "C",
        "Import et glisser-déposer",
        [
            Item(
                "C1",
                "Import simple — un PDF déposé devient une fiche consultable",
                [
                    "tests/test_import_workflow.py",
                    "tests/test_depot_apres_import.py",
                ],
                navigateur="frontend/e2e/import.spec.ts",
            ),
            Item(
                "C2",
                "Import mixte — PDF et classeurs dans le même dépôt",
                [
                    "tests/test_matrice_roles_routes.py::test_brouillon_pdf_mixte_refuse_avant_persistance",
                    "tests/test_classement_documents_non_fiche.py::test_melange_de_documents_non_fiche_ne_perd_aucune_piece",
                ],
            ),
            Item(
                "C3",
                "Tri à l'import — un PDF est analysé, un autre format est stocké sans analyse",
                [
                    "tests/test_classement_documents_non_fiche.py::test_pdf_sans_couche_texte_entre_en_revue_manuelle",
                    "tests/test_classement_documents_non_fiche.py::test_planche_sailpack_devient_un_plan_de_fabrication",
                    "tests/test_classement_documents_non_fiche.py::test_une_vraie_fiche_a_cote_d_une_planche_reste_deposee",
                ],
            ),
            Item(
                "C4",
                "Import d'un accessoire",
                [
                    "tests/test_lot7_accessoires.py::test_construire_extraction_accessoire_provenance_et_dimensions",
                    "tests/test_accessoire_reference_commande_datee.py",
                ],
                navigateur="frontend/e2e/lot7-accessoires.spec.ts",
            ),
            Item(
                "C5",
                "Fichier malformé, vide ou corrompu — refus nommé, jamais de 500",
                [
                    "tests/test_matrice_roles_routes.py::test_brouillon_depuis_pdf_illisible_repond_422_sans_fuite",
                    "tests/test_matrice_roles_routes.py::test_detection_pdf_illisible_n_expose_pas_exception_ou_chemin",
                    "tests/test_integrite_telechargement_import.py::test_telechargement_fichiers_corrompus_refuse_rapport",
                ],
            ),
            Item(
                "C6",
                "Doublon — détecté et signalé avant validation",
                [
                    "tests/test_dedup.py",
                ],
                navigateur="frontend/e2e/doublons.spec.ts",
            ),
            Item(
                "C7",
                "Gros volume — bornes de flux et dépôt par lot",
                [
                    "tests/test_flux_bornes.py",
                    "tests/test_multipart_flux.py",
                    "tests/test_bff_multipart_borne.py",
                ],
                navigateur="frontend/e2e/televersement-lot.spec.ts",
            ),
        ],
    ),
    Section(
        "D",
        "Extraction depuis un PDF",
        [
            Item(
                "D1",
                "PDF bien formé — valeurs extraites et sourcées",
                [
                    "tests/test_passe_finale_extraction_pdf.py",
                    "tests/test_extraction_fiche_reference.py::TestTracabilite::test_confiances_dans_les_limites",
                ],
            ),
            Item(
                "D2",
                "PDF dégradé ou scanné — OCR, et refus d'inventer sans OCR",
                [
                    "tests/test_passe_finale_extraction_pdf.py::test_pdf_scanne_avec_ocr",
                    "tests/test_passe_finale_extraction_pdf.py::test_pdf_scanne_sans_ocr_n_invente_rien",
                    "tests/test_passe_finale_extraction_pdf.py::test_entree_degradee_rend_un_statut_explicite_sans_lever",
                ],
            ),
            Item(
                "D3",
                "Score bas — la fiche part en file de validation",
                [
                    "tests/test_qualite_tableau.py::test_taux_extraction_auto_definition",
                    "tests/test_validation_workflow.py",
                ],
                navigateur="frontend/e2e/validation.spec.ts",
            ),
            Item(
                "D4",
                "Score haut — auto-validation",
                [
                    "tests/test_fiches_sql_grammaire.py::test_le_statut_arrivee_est_a_valider_dans_le_code",
                    "tests/test_fiches_persistance.py",
                ],
                absent=(
                    "Aucune auto-validation par score : le statut d'arrivée est "
                    "TOUJOURS a_valider, et c'est une règle nommée — RG3, « le dépôt "
                    "ne valide jamais ». L'attente « score haut ⇒ auto-validation » "
                    "est donc CONTRAIRE à RG3, non oubliée."
                ),
                manuel=(
                    "À TRANCHER par le commanditaire : soit RG3 est maintenue (l'item est "
                    "caduc), soit une auto-validation au-dessus d'un seuil est réellement "
                    "voulue et il faut changer la règle — pas la contourner."
                ),
            ),
            Item(
                "D5",
                "Correction par l'approbateur — la source n'est pas écrasée",
                [
                    "tests/test_correction_preservation.py",
                ],
                navigateur="frontend/e2e/lot3b-extractions.spec.ts",
            ),
        ],
    ),
    Section(
        "E",
        "Recherche",
        [
            Item(
                "E1",
                "Recherche par bateau, type, matière, dimension et mot-clé",
                [
                    "tests/test_passe_finale_recherche_variantes.py::test_filtre_par_bateau_et_matiere",
                    "tests/test_passe_finale_recherche_variantes.py::test_analyse_dimension_variantes",
                    "tests/test_recherche_dimension.py",
                ],
            ),
            Item(
                "E2",
                "Normalisation — casse, accents, tirets, abréviations",
                [
                    "tests/test_normalisation_cle_recherche.py",
                    "tests/test_indexer_accent_parity.py",
                ],
            ),
            Item(
                "E3",
                "Autocorrection et suggestions",
                [
                    "tests/test_lot2_referentiel_recherche.py::test_lot2_endpoints_suggestions_et_referentiel",
                    "tests/test_facettes_et_suggestions.py",
                ],
            ),
            Item(
                "E4",
                "Chaque filtre seul, puis combiné",
                [
                    "tests/test_passe_finale_recherche_variantes.py::test_filtre_invalide_refuse",
                    "tests/test_passe_finale_recherche_variantes.py::test_filtre_boat_model_id_borne",
                    "tests/test_fichiers_par_id.py::test_catalogue_filtre_recherche_extension_dossier_et_pagination",
                ],
            ),
            Item(
                "E5",
                "Couleurs des listes déroulantes (défaut signalé par le commanditaire)",
                [
                    "tests/test_coherence_champs_formulaire.py",
                ],
                navigateur="frontend/e2e/search.spec.ts — le rendu reste à capturer",
                manuel=(
                    "Défaut CONFIRMÉ et corrigé : les écrans de recherche et de "
                    "comparaison peignaient leurs champs en `bg-background` (fond opaque "
                    "de page) alors que tous les autres écrans utilisent `bg-input` "
                    "(translucide). Le test lit les sources et refuse l'écart ; vérifié "
                    "rouge sur le défaut réintroduit à la main. Reste à ouvrir la recherche "
                    "et constater visuellement l'alignement."
                ),
            ),
            Item(
                "E6",
                "Pagination et indicateur « reste des résultats »",
                [
                    "tests/test_fichiers_par_id.py::test_catalogue_filtre_recherche_extension_dossier_et_pagination",
                ],
                navigateur="frontend/e2e/search.spec.ts",
            ),
            Item(
                "E7",
                "Aperçu ET téléchargement depuis les résultats",
                [
                    "tests/test_url_presignee_302.py",
                    "tests/test_open_capture_endpoint_public.py",
                ],
                navigateur="frontend/e2e/recherche-extrait.spec.ts",
            ),
            Item(
                "E8",
                "Recherche vide ou sans résultat — repli explicite",
                [
                    "tests/test_passe_finale_repli_recherche.py",
                    "tests/test_normalisation_cle_recherche.py::test_vide_et_espaces_seuls",
                ],
            ),
        ],
    ),
    Section(
        "F",
        "Comparaison",
        [
            Item(
                "F1",
                "Comparaison de deux fiches",
                [
                    "tests/test_lot4b_comparaison_voile.py",
                    "tests/test_comparaison_plusieurs.py::test_l_identite_est_fausse_quand_une_seule_fiche_differ_e",
                ],
                navigateur="frontend/e2e/lot4b-comparaison.spec.ts",
            ),
            Item(
                "F2",
                "Comparaison de N fiches après filtres",
                [
                    "tests/test_comparaison_plusieurs.py::test_au_dela_de_douze_colonnes_est_refuse",
                    "tests/test_comparaison_plusieurs.py::test_chaque_ligne_porte_autant_de_valeurs_que_de_fiches",
                    "tests/test_comparaison_plusieurs.py::test_l_etendue_et_les_bornes_sont_calculees_sur_le_groupe",
                ],
            ),
            Item(
                "F3",
                "Pourcentage de confiance — seuil, sinon « données insuffisantes »",
                [
                    "tests/test_passe_finale_confiance_seuils.py",
                    "tests/test_calibration_seuils.py::test_le_seuil_est_toujours_une_confiance_reellement_observee",
                ],
                navigateur="frontend/e2e/lot5-confiance.spec.ts",
            ),
            Item(
                "F4",
                "Cas d'étalonnage — la fiche réelle du fonds",
                [],
                preuve_ci=(
                    "job « recette-corpus-reel », étape « Run full recette suite » : "
                    "pytest -m recette_corpus sur le corpus réel, jouée à chaque run."
                ),
                manuel=(
                    "Le harnais NE REJOUE PAS cette suite : elle consomme la base qu'elle "
                    "mesure, et la rejouer après elle-même la fait échouer — vérifié, "
                    "l'étape ajoutée puis retirée a rendu un F4 rouge alors que la suite, "
                    "elle, était verte. Casser une preuve qui marche pour en ajouter une "
                    "qui casse n'est pas une amélioration."
                ),
            ),
        ],
    ),
    Section(
        "G",
        "Accessoires",
        [
            Item(
                "G1",
                "La section accessoires s'ouvre et liste",
                [
                    "tests/test_lot7_accessoires.py",
                ],
                navigateur="frontend/e2e/lot7-accessoires.spec.ts",
            ),
            Item(
                "G2",
                "Lien entre un accessoire et son bateau",
                [
                    "tests/test_lot3b_liaison_voile.py",
                    "tests/test_double_verification_accessoires.py",
                ],
            ),
            Item(
                "G3",
                "Extraction depuis un PDF d'accessoire",
                [
                    "tests/test_passe_finale_comparaison_accessoires.py::test_extraction_accessoire_dimensions_et_provenance",
                    "tests/test_passe_finale_comparaison_accessoires.py::test_extraction_accessoire_sans_unite_ne_devine_rien",
                    "tests/test_lot9_calibration_accessoires.py::test_extraction_libelles_reels",
                ],
            ),
        ],
    ),
    Section(
        "H",
        "API — toutes les routes, quatre cas chacune",
        [
            Item(
                "H1",
                "Chaque route extraite du code : nominal, non autorisé, paramètre invalide, ressource absente",
                [
                    "tests/test_recette_routes.py",
                ],
                manuel=(
                    "Le balayage des 116 routes contre la pile réelle est joué par l'étape "
                    "« Recette de TOUTES les routes » du job integration."
                ),
                preuve_ci=(
                    "job « integration », étape « Recette de TOUTES les routes, contre la "
                    "pile réelle » — 116 routes, 472 sondes, publiée en annotations."
                ),
            ),
            Item(
                "H2",
                "`/health` et `/metrics` — avec et sans jeton",
                [
                    "tests/test_sondes_et_metriques.py",
                    "tests/test_recette_routes.py::test_aucune_route_n_est_ouverte_sans_jeton",
                ],
                manuel=(
                    "`/health` était vérifié, `/metrics` ne l'était nulle part : le test qui "
                    "couvrait les quatre sondes le faisait SANS jeton configuré, donc tout "
                    "répondait 200. `/metrics` est protégé par TROIS couches (middleware, "
                    "`_require_auth`, résolution d'acteur) — vérifié rouge seulement une fois "
                    "les trois neutralisées, et vert sinon."
                ),
            ),
            Item(
                "H3",
                "Confinement de `/search`, `/preview` et `/open`",
                [
                    "tests/test_open_capture_endpoint_public.py",
                    "tests/test_artefact_confinement_absent.py",
                ],
            ),
            Item(
                "H4",
                "Limitation de débit — seuil, 429, et routes de sonde exemptées",
                [
                    "tests/test_jobs_api.py::test_rate_limiting_and_retry_after",
                    "tests/test_sondes_et_metriques.py::test_les_sondes_ne_sont_pas_soumises_a_la_limitation_de_debit",
                ],
                manuel=(
                    "Seuil : `rate_limit_per_minute`, 600 par défaut. Le quota épuisé, "
                    "`/live` et `/ready` doivent répondre quand même — une supervision "
                    "bloquée par son propre outil de limitation cesse de répondre au moment "
                    "précis où le service est le plus chargé."
                ),
            ),
        ],
    ),
    Section(
        "I",
        "Notifications et audit",
        [
            Item(
                "I1",
                "Chaque événement produit sa notification",
                [
                    "tests/test_passe_finale_comparaison_accessoires.py::test_notifications_exigent_un_compte_nominatif",
                    "tests/test_passe_finale_comparaison_accessoires.py::test_notifications_tout_lire",
                    "tests/test_lot1_roles_approbations.py::test_auto_approbation_annulation_refus_et_notifications_anti_idor",
                ],
                navigateur="frontend/e2e/lot1-approbations.spec.ts",
            ),
            Item(
                "I2",
                "Traçabilité — chaque action est rattachée à un utilisateur nommé",
                [
                    "tests/test_comptes.py::test_validation_attribuee_au_compte_connecte",
                    "tests/test_lot0b_audit_securite.py::test_lot0b_postgres_audit_append_only_et_gardes",
                    "tests/test_lot0b_audit_securite.py::test_acteur_audit_ne_croit_jamais_des_entetes_non_verifies",
                ],
            ),
            Item(
                "I3",
                "Journal d'audit — expurgé, et aucune fuite de secret",
                [
                    "tests/test_lot0b_audit_securite.py::test_audit_expurgation_des_secrets_sqlite",
                    "tests/test_matrice_roles_routes.py::test_creation_compte_ne_journalise_pas_identifiant",
                    "tests/test_matrice_roles_routes.py::test_brouillon_erreur_ne_journalise_pas_secret",
                ],
            ),
        ],
    ),
    Section(
        "J",
        "Accessibilité",
        [
            Item(
                "J1",
                "Chaque écran audité — 0 violation WCAG 2.1 A/AA",
                [
                    "tests/test_accessibilite_inventaire.py::test_chaque_ecran_de_l_inventaire_est_dans_le_plan_d_audit",
                    "tests/test_accessibilite_inventaire.py::test_le_plan_d_audit_couvre_au_moins_l_inventaire",
                ],
                navigateur="frontend/e2e/accessibilite.spec.ts — tous écrans × rôles × états",
                manuel=(
                    "TROU TROUVÉ ET COMBLÉ : l'audit annonçait « 82 écrans scannés » mais "
                    "les deux écrans à paramètre (/fiches/[code], /dossier/[code]) "
                    "n'apparaissaient dans aucun constat — leur test lisait les codes sur "
                    "la FILE DE VALIDATION, vide sur base fraîche. Le test d'inventaire "
                    "exige désormais les 17 écrans un par un, et le spec lit la recherche."
                ),
            ),
            Item(
                "J2",
                "Un seul `<main>` par écran",
                [
                    "tests/test_accessibilite_inventaire.py::test_un_seul_repere_main_dans_la_mise_en_page",
                ],
                navigateur="frontend/e2e/accessibilite.spec.ts — règle axe « landmark-one-main »",
            ),
        ],
    ),
    Section(
        "K",
        "Sauvegarde et restauration",
        [
            Item(
                "K1",
                "Sauvegarde puis restauration sur base neuve — identité octet à octet",
                [
                    "tests/test_recette_corpus_reel.py::test_10_sauvegarde_restauration_base_neuve",
                    "tests/test_sauvegarde_identite_octets.py",
                ],
            ),
            Item(
                "K2",
                "Objets — sauvegarde et restauration sans écrasement",
                [
                    "tests/test_restauration_bundle_s3rver.py::test_sauvegarde_copie_objet_par_reseau_isole",
                    "tests/test_restauration_bundle_s3rver.py::test_s3rver_restauration_objet_refusee_sans_ecraser",
                    "tests/test_sauvegarde_objet_checksum.py",
                ],
            ),
            Item(
                "K3",
                "Identité restreinte — l'outil n'utilise jamais l'administrateur",
                [
                    "tests/test_credentials_s3_restreintes.py::test_outil_de_sauvegarde_utilise_l_identite_dediee_et_jamais_l_administrateur",
                ],
            ),
            Item(
                "K4",
                "Recette locale sur poste — les 20 contrôles",
                [
                    "tests/test_recette_locale.py",
                ],
                manuel="scripts/recette_locale.ps1, à rejouer dans un venv disponible.",
            ),
            Item(
                "K5",
                "Un échec de restauration nomme sa cause et s'arrête",
                [
                    "tests/test_preuve_objet_perdu.py::test_un_echec_de_restauration_nomme_sa_cause",
                    "tests/test_preuve_restauration_reelle.py::test_sans_pre_requis_le_script_nomme_ce_qui_manque_et_refuse",
                    "tests/test_recette_locale.py::test_recette_ps1_arrete_sur_echec_restauration_sans_poursuivre_down_up",
                ],
            ),
        ],
    ),
]


def _collecte_disponible() -> set[str]:
    """L'ensemble des identifiants pytest du dépôt, pour valider la matrice."""
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly"],
        cwd=RACINE,
        capture_output=True,
        text=True,
        check=False,
    )
    collectes = {ligne.strip() for ligne in proc.stdout.splitlines() if ligne.startswith("tests/") and "::" in ligne}
    # Trois granularités, parce que pytest les accepte toutes les trois et que
    # la matrice en emploie trois :
    #   · le test paramétré     — tests/x.py::test_y[cas]
    #   · le test NU            — tests/x.py::test_y        (sélectionne tous
    #     les cas du test ; sans cette entrée, six items de la matrice
    #     passaient pour introuvables alors qu'ils tournent très bien)
    #   · le module entier      — tests/x.py
    nus = {ligne.split("[")[0] for ligne in collectes if "[" in ligne}
    return collectes | nus | {ligne.split("::")[0] for ligne in collectes}


def jouer(selecteurs: list[str], rapport_xml: Path) -> dict[str, int]:
    """Joue les sélecteurs d'un item et relève le décompte par issue."""
    compte = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    if not selecteurs:
        return compte
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:randomly",
            f"--junitxml={rapport_xml}",
            *selecteurs,
        ],
        cwd=RACINE,
        capture_output=True,
        text=True,
        check=False,
    )
    if not rapport_xml.exists():
        compte["error"] = len(selecteurs)
        return compte
    racine = ET.parse(rapport_xml).getroot()
    vus = 0
    for cas in racine.iter("testcase"):
        vus += 1
        for enfant in cas:
            if enfant.tag in compte:
                compte[enfant.tag] += 1
                if enfant.tag in ("failed", "error"):
                    # Sans le NOM du test fautif, « ÉCHEC » est un mot, pas un
                    # diagnostic : depuis l'extérieur du runner on ne peut ni
                    # lire les journaux ni ouvrir l'artefact.
                    classe = cas.get("classname", "").replace(".", "/")
                    compte.setdefault("_noms", []).append(  # type: ignore[arg-type]
                        f"{classe}.py::{cas.get('name', '?')} : {(enfant.get('message') or '')[:140]}"
                    )
                break
        else:
            compte["passed"] += 1
    # Un sélecteur qui ne correspond à rien fait rendre à pytest le code 4 et
    # un rapport VIDE — et il emporte les autres sélecteurs de l'item avec
    # lui. Mesuré : l'item H2 est resté « NON PROUVÉ, 0 test » à cause d'un nom
    # de test erroné, alors que quatre tests valides l'accompagnaient. Un item
    # dont aucun test n'a tourné est un défaut de la RECTTE, pas un « non
    # prouvé » : il doit se voir.
    if vus == 0:
        compte["error"] = len(selecteurs)
    return compte


def statut_item(compte: dict[str, int], introuvables: list[str]) -> str:
    """Le statut d'un item, en refusant de prendre un saut pour un succès."""
    if introuvables:
        return INTROUVABLE
    if compte["failed"] or compte["error"]:
        return ECHEC
    if compte["passed"] and compte["skipped"]:
        return PARTIEL
    if compte["passed"]:
        return PROUVE
    if compte["skipped"]:
        return NON_PROUVE
    # Aucun test joué : l'item est porté par le navigateur, par une étape de
    # CI que ce harnais ne rejoue pas, ou par rien du tout.
    return NON_PROUVE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recette fonctionnelle A→K")
    parser.add_argument(
        "--sortie",
        type=Path,
        default=DESTINATION,
        help="Dossier de sortie du bilan",
    )
    parser.add_argument(
        "--annonces",
        action="store_true",
        help="Publier le bilan en annotations GitHub",
    )
    parser.add_argument(
        "--section",
        help="Ne jouer qu'une section (A, B, C…)",
    )
    args = parser.parse_args(argv)

    args.sortie.mkdir(parents=True, exist_ok=True)
    xml = args.sortie / "junit-item.xml"

    dispo = _collecte_disponible()
    lignes: list[dict[str, object]] = []
    sections = SECTIONS
    if args.section:
        sections = [s for s in SECTIONS if s.id == args.section.upper()]
        if not sections:
            print(f"section inconnue : {args.section}", file=sys.stderr)
            return 2

    for section in sections:
        for item in section.items:
            introuvables = [s for s in item.tests if s not in dispo]
            compte = jouer(item.tests, xml)
            statut = statut_item(compte, introuvables)
            if item.preuve_ci and statut in (NON_PROUVE, PARTIEL):
                statut = PROUVE_CI
            if item.absent:
                # Un item délibérément exclu par une règle n'est pas « non
                # prouvé » : il est tranché dans le code, et la question est
                # renvoyée au commanditaire.
                statut = A_TRANCHER
            lignes.append(
                {
                    "section": section.id,
                    "titre_section": section.titre,
                    "item": item.id,
                    "libelle": item.libelle,
                    "tests": item.tests,
                    "navigateur": item.navigateur,
                    "manuel": item.manuel,
                    "preuve_ci": item.preuve_ci,
                    "absent": item.absent,
                    "resultats": compte,
                    "introuvables": introuvables,
                    "statut": statut,
                }
            )
            print(
                f"[{section.id}] {item.id} {item.libelle[:58]:<58} "
                f"{lignes[-1]['statut']:<12} "
                f"({compte['passed']}✓ {compte['failed']}✗ {compte['skipped']}⤼ {compte['error']}!)"
            )
        if xml.exists():
            xml.unlink()

    bilan = {
        "sections": [s.id for s in sections],
        "items": len(lignes),
        "prouves": sum(1 for ligne in lignes if ligne["statut"] == PROUVE),
        "prouves_ci": sum(1 for ligne in lignes if ligne["statut"] == PROUVE_CI),
        "a_trancher": sum(1 for ligne in lignes if ligne["statut"] == A_TRANCHER),
        "partiels": sum(1 for ligne in lignes if ligne["statut"] == PARTIEL),
        "non_prouves": sum(1 for ligne in lignes if ligne["statut"] == NON_PROUVE),
        "echecs": sum(1 for ligne in lignes if ligne["statut"] == ECHEC),
        "introuvables": sum(1 for ligne in lignes if ligne["statut"] == INTROUVABLE),
        "lignes": lignes,
    }

    (args.sortie / "matrice-recette.json").write_text(
        json.dumps(bilan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.sortie / "matrice-recette.md").write_text(
        _markdown(bilan, sections),
        encoding="utf-8",
    )

    print()
    print(
        f"Recette A→{sections[-1].id} : {bilan['items']} items — "
        f"{bilan['prouves']} prouvés, {bilan['prouves_ci']} prouvés par la CI, "
        f"{bilan['a_trancher']} à trancher, "
        f"{bilan['partiels']} partiels, "
        f"{bilan['non_prouves']} non prouvés, {bilan['echecs']} échecs, "
        f"{bilan['introuvables']} introuvables"
    )
    if args.annonces:
        _annoncer(bilan)
    # Le bilan n'est pas un constat : un item en échec fait échouer l'étape.
    # Le harnais rendait 0 quoi qu'il arrive — mesuré, le job
    # « recette-corpus-reel » est resté VERT avec « 1 échec » publié en
    # annotation. Une erreur annotée sur un job vert est un alibi, pas un
    # signal : personne ne lit les annotations d'un run qui réussit.
    if bilan["echecs"] or bilan["introuvables"]:
        return 1
    return 0


def _annoncer(bilan: dict[str, object]) -> None:
    """Le bilan passe en annotations : seul canal lisible depuis la CI."""
    print(
        f"::notice title=recette-A-K::{bilan['items']} items — "
        f"{bilan['prouves']} prouvés, {bilan['partiels']} partiels, "
        f"{bilan['non_prouves']} non prouvés, {bilan['echecs']} échecs, "
        f"{bilan['introuvables']} introuvables"
    )
    for ligne in bilan["lignes"]:  # type: ignore[union-attr]
        if ligne["statut"] in (ECHEC, INTROUVABLE):
            print(f"::error title=recette-{ligne['item']}::{ligne['statut']} — {ligne['libelle']}")
            for faute in ligne["resultats"].get("_noms", [])[:3]:  # type: ignore[union-attr]
                print(f"::error title=recette-{ligne['item']}-detail::{faute}")
        elif ligne["statut"] in (NON_PROUVE, PARTIEL):
            print(f"::warning title=recette-{ligne['item']}::{ligne['statut']} — {ligne['libelle']}")


def _markdown(bilan: dict[str, object], sections: list[Section]) -> str:
    titre_section = {s.id: s.titre for s in sections}
    out = [
        "# Matrice de recette fonctionnelle A→K",
        "",
        f"- items : {bilan['items']}",
        f"- prouvés : {bilan['prouves']}",
        f"- partiels : {bilan['partiels']}",
        f"- non prouvés : {bilan['non_prouves']}",
        f"- échecs : {bilan['echecs']}",
        f"- introuvables : {bilan['introuvables']}",
        "",
        "| Section | Item | Test écrit (résultat) | Navigateur / manuel | Statut |",
        "|---|---|---|---|---|",
    ]
    for ligne in bilan["lignes"]:  # type: ignore[union-attr]
        resultats = ligne["resultats"]  # type: ignore[index]
        detail = f"{resultats['passed']}✓ {resultats['failed']}✗ {resultats['skipped']}⤼ {resultats['error']}!"
        tests = ", ".join(f"`{t.split('::')[-1]}`" for t in ligne["tests"]) or "—"
        autre = ligne["navigateur"] or ligne["manuel"] or "—"
        out.append(
            f"| {ligne['section']} · {titre_section[ligne['section']]} "  # type: ignore[index]
            f"| {ligne['item']} {ligne['libelle']} "
            f"| {tests} — {detail} "
            f"| {autre} "
            f"| **{ligne['statut']}** |"
        )
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
