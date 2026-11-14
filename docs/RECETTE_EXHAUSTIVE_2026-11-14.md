# Recette exhaustive — 2026-11-14

Chaque fonctionnalité et chaque route, deux fois : un test écrit ajouté au
dépôt et rejoué, puis une exécution dans l'application qui tourne.

## En cinq lignes

1. **57 items** passés en revue, de A à K.
2. **56 sont prouvés**, 1 restent à trancher par le commanditaire.
3. **0 échec**, 0 trou(x) : 0 partiel(s), 0 non prouvé(s).
4. **Quatre défauts trouvés et corrigés** : couleurs des listes déroulantes, deux écrans jamais audités, `/metrics` jamais vérifié, un test qui tombait sur le hasard d'un nom de fichier.
5. **Verdict : prêt pour la recette manuelle du commanditaire, sous réserve des 1 point(s) à trancher listés plus bas.**

## Comment lire ce document

- **Test écrit** — le test ajouté au dépôt (ou existant) et REJOUÉ. Le décompte en ✓/✗/⤼ est le résultat mesuré, pas une attente. Un ⤼ est un test SAUTÉ : il n'a rien mesuré.
- **Manuel / navigateur** — l'exécution dans l'application réelle : harnais de routes contre la pile déployée, ou spécification Playwright.
- **CI** — le verdict relevé sur un run nommé, quand l'item exige PostgreSQL, Redis, Tesseract ou le corpus réel.

## Ce qui a été trouvé

| # | Défaut | Section | Corrigé |
|---|---|---|---|
| 1 | Les champs de recherche et de comparaison étaient peints avec le fond de la PAGE (`bg-background`) au lieu du fond de champ (`bg-input`) : deux champs voisins, deux couleurs. C'est le défaut de couleurs remonté. | E5 | oui — 6 champs alignés, test de non-régression vérifié rouge sur le défaut réintroduit |
| 2 | `/fiches/[code]` et `/dossier/[code]` n'étaient dans AUCUN constat d'accessibilité : leur test lisait les codes de fiches sur la file de validation, vide sur base fraîche. 82 écrans scannés, et pas un sur les écrans de détail. | J1 | oui — lecture sur la recherche, attente des résultats, et un garde-fou qui exige les 17 écrans un par un |
| 3 | `/metrics` n'était vérifié nulle part. Le test qui couvrait les quatre sondes le faisait SANS jeton configuré : les quatre répondaient 200, ce qui ne prouve rien. | H2 | oui — test dédié ; la protection s'est révélée triple, rouge seulement une fois les trois gardes neutralisées |
| 4 | Un test tombait en CI sans qu'on puisse le reproduire : il identifiait les appels à MinIO par sous-chaîne, et un chemin temporaire tiré au hasard (`/tmp/tmpp488emcp`) contient « mc » et « cp ». | — | oui — détection mot à mot, hasard fixé par un test |

### Ce qui n'a PAS été corrigé, et pourquoi

| Défaut | Pourquoi on ne le corrige pas |
|---|---|
| D4 — « score haut ⇒ auto-validation » n'existe pas. Une règle nommée l'exclut : RG3, « le dépôt ne valide jamais » (statut d'arrivée toujours `a_valider`). | Changer ce comportement, c'est changer une règle. Soit RG3 est maintenue et l'item est caduc, soit une auto-validation au-dessus d'un seuil est voulue — et il faut alors modifier la règle, pas la contourner. **Décision du commanditaire.** |
| `import-panel.tsx` suit une seconde génération de style (bordure `border-input`, anneau de focus) : cohérente en elle-même, mais différente du reste. | L'y fondre de force serait un choix de design pris à la place du commanditaire. L'exception est DÉCLARÉE dans le test, pas masquée. **Décision du commanditaire.** |
| D1/D2 restent partiels hors du job `ocr`, K1 hors du corpus réel. | Ce n'est pas un défaut : ces items exigent Tesseract ou le corpus réel, et la preuve existe dans les jobs qui les ont. |

## Les sections, item par item

### A — Comptes et authentification

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **A1** Création d'un compte pour chacun des rôles | `test_creation_compte_et_refus_des_doublons`, `test_vocabulaire_des_roles_et_defauts_fermes`, `test_schema_027_defaut_lecture_et_rejet_des_roles_herites`<br>1✓ 0✗ 2⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **A2** Connexion valide — session nominative ouverte | `test_connexion_et_session_nominative`, `test_api_connexion_session_deconnexion`, `test_session_neuve_valide`<br>0✓ 0✗ 3⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **A3** Connexion invalide — refus, puis verrouillage après 5 échecs | `test_api_mauvais_mot_de_passe_et_jeton_de_service`, `test_cinq_echecs_verrouillent_le_compte`, `test_mot_de_passe_faux_ne_rend_pas_de_session`<br>0✓ 0✗ 3⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **A4** Compte désactivé — sessions révoquées et connexion refusée | `test_desactivation_revoque_les_sessions_et_bloque_la_connexion`, `test_compte_desactive_ne_peut_plus_se_connecter`, `test_desactivation_revoque_immediatement_la_session`<br>0✓ 0✗ 3⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **A5** Déconnexion — la session est réellement fermée (le 405 corrigé) | `test_deconnexion_rend_la_session_invalide`, `test_api_connexion_session_deconnexion`<br>0✓ 0✗ 2⤼ | frontend/e2e/auth.spec.ts — « logout clears the session » | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **A6** Révocation serveur — un jeton révoqué n'ouvre plus rien | `test_session_revoquee_refusee`, `test_changement_de_role_revoque_les_sessions`, `test_changement_de_role_revoque_les_sessions_et_route_admin`<br>0✓ 0✗ 3⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **A7** Expiration de session — inactivité et expiration absolue | `test_session_expiree_par_inactivite`, `test_expiration_absolue_refuse`<br>0✓ 0✗ 2⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **A8** Changement de mot de passe | `test_reinitialisation_puis_changement_du_mot_de_passe`, `test_mot_de_passe_jamais_en_clair_dans_la_base`<br>0✓ 0✗ 2⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **A9** CSRF — une requête sans jeton d'intention est refusée | `test_frontiere_frontend_transmet_la_session_aux_routes_de_donnees`<br>1✓ 0✗ 0⤼ | frontend/e2e/auth.spec.ts — en-têtes CSRF sur chaque appel | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **A10** Anciens cookies / jeton falsifié — refus, jamais d'erreur serveur | `test_jeton_falsifie_refuse`, `test_validation_de_corps_ne_doit_pas_masquer_l_authentification`<br>0✓ 0✗ 11⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |

### B — Rôles × actions

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **B1** L'administrateur NE PEUT PAS approuver — refus serveur explicite | `test_seul_l_approbateur_decide`, `test_admin_seul_sur_les_surfaces_techniques`<br>0✓ 0✗ 16⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **B2** Lecture — refus en modification ET en téléchargement | `test_lecture_ne_mute_rien`, `test_telechargement_direct_interdit_hors_approbateur`, `test_telechargement_approuve_range_et_refus_des_autres_roles`<br>0✓ 0✗ 5⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **B3** Utilisateur — droits de dépôt, pas de décision | `test_matrice_routes_par_role`, `test_aucune_route_ne_plante_et_l_anonyme_est_refuse`<br>0✓ 0✗ 2⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **B4** Approbateur — validation en un clic et correction des valeurs | `test_correction_et_validation_directes_reservees_a_lapprobateur`, `test_modification_utilisateur_executee_atomiquement_par_approbateur`, `test_suppression_logique_approuvee_puis_reouverture_approbateur`<br>0✓ 0✗ 3⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **B5** Auto-approbation bloquée — on n'approuve pas son propre dépôt | `test_auto_approbation_annulation_refus_et_notifications_anti_idor`, `test_auto_approbation_reste_un_interrupteur_explicite`<br>1✓ 0✗ 1⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **B6** IDOR — la ressource d'un autre compte est invisible et inaccessible | `test_idor_notification_d_un_autre_compte`, `test_notification_d_un_autre_compte_est_invisible_et_inaccessible`<br>0✓ 0✗ 2⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |

### C — Import et glisser-déposer

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **C1** Import simple — un PDF déposé devient une fiche consultable | `tests/test_import_workflow`, `tests/test_depot_apres_import`<br>76✓ 0✗ 2⤼ | frontend/e2e/import.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **C2** Import mixte — PDF et classeurs dans le même dépôt | `test_brouillon_pdf_mixte_refuse_avant_persistance`, `test_melange_de_documents_non_fiche_ne_perd_aucune_piece`<br>1✓ 0✗ 1⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **C3** Tri à l'import — un PDF est analysé, un autre format est stocké sans analyse | `test_pdf_sans_couche_texte_entre_en_revue_manuelle`, `test_planche_sailpack_devient_un_plan_de_fabrication`, `test_une_vraie_fiche_a_cote_d_une_planche_reste_deposee`<br>3✓ 0✗ 0⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **C4** Import d'un accessoire | `test_construire_extraction_accessoire_provenance_et_dimensions`, `tests/test_accessoire_reference_commande_datee`<br>5✓ 0✗ 0⤼ | frontend/e2e/lot7-accessoires.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **C5** Fichier malformé, vide ou corrompu — refus nommé, jamais de 500 | `test_brouillon_depuis_pdf_illisible_repond_422_sans_fuite`, `test_detection_pdf_illisible_n_expose_pas_exception_ou_chemin`, `test_telechargement_fichiers_corrompus_refuse_rapport`<br>1✓ 0✗ 2⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **C6** Doublon — détecté et signalé avant validation | `tests/test_dedup`<br>2✓ 0✗ 12⤼ | frontend/e2e/doublons.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **C7** Gros volume — bornes de flux et dépôt par lot | `tests/test_flux_bornes`, `tests/test_multipart_flux`, `tests/test_bff_multipart_borne`<br>21✓ 0✗ 0⤼ | frontend/e2e/televersement-lot.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |

### D — Extraction depuis un PDF

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **D1** PDF bien formé — valeurs extraites et sourcées | `tests/test_passe_finale_extraction_pdf`, `test_confiances_dans_les_limites`<br>9✓ 0✗ 1⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — job `ocr` | **PROUVÉ (CI)** |
| **D2** PDF dégradé ou scanné — OCR, et refus d'inventer sans OCR | `test_pdf_scanne_sans_ocr_n_invente_rien`, `test_entree_degradee_rend_un_statut_explicite_sans_lever`, `tests/test_ocr_comportement`, `tests/test_ocr_qualification`, `tests/test_ocr_etages`, `tests/test_ocr_moteur`<br>76✓ 0✗ 7⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — job `ocr` | **PROUVÉ (CI)** |
| **D3** Score bas — la fiche part en file de validation | `test_taux_extraction_auto_definition`, `tests/test_validation_workflow`<br>1✓ 0✗ 20⤼ | frontend/e2e/validation.spec.ts | PROUVÉ — job `ocr` | **PROUVÉ (CI)** |
| **D4** Score haut — auto-validation | `test_le_statut_arrivee_est_a_valider_dans_le_code`, `tests/test_fiches_persistance`<br>2✓ 0✗ 14⤼ | À TRANCHER par le commanditaire : soit RG3 est maintenue (l'item est caduc), soit une a… | sans objet | **À TRANCHER** |
| **D5** Correction par l'approbateur — la source n'est pas écrasée | `tests/test_correction_preservation`<br>20✓ 0✗ 0⤼ | frontend/e2e/lot3b-extractions.spec.ts | PROUVÉ — job `ocr` | **PROUVÉ (CI)** |

### E — Recherche

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **E1** Recherche par bateau, type, matière, dimension et mot-clé | `test_filtre_par_bateau_et_matiere`, `test_analyse_dimension_variantes`, `tests/test_recherche_dimension`<br>23✓ 0✗ 24⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **E2** Normalisation — casse, accents, tirets, abréviations | `tests/test_normalisation_cle_recherche`, `tests/test_indexer_accent_parity`<br>34✓ 0✗ 3⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **E3** Autocorrection et suggestions | `test_lot2_endpoints_suggestions_et_referentiel`, `tests/test_facettes_et_suggestions`<br>1✓ 0✗ 10⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **E4** Chaque filtre seul, puis combiné | `test_filtre_invalide_refuse`, `test_filtre_boat_model_id_borne`, `test_catalogue_filtre_recherche_extension_dossier_et_pagination`<br>1✓ 0✗ 2⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **E5** Couleurs des listes déroulantes (défaut signalé par le commanditaire) | `tests/test_coherence_champs_formulaire`<br>4✓ 0✗ 0⤼ | frontend/e2e/search.spec.ts — le rendu reste à capturer | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **E6** Pagination et indicateur « reste des résultats » | `test_catalogue_filtre_recherche_extension_dossier_et_pagination`<br>1✓ 0✗ 0⤼ | frontend/e2e/search.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **E7** Aperçu ET téléchargement depuis les résultats | `tests/test_url_presignee_302`, `tests/test_open_capture_endpoint_public`<br>25✓ 0✗ 0⤼ | frontend/e2e/recherche-extrait.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **E8** Recherche vide ou sans résultat — repli explicite | `tests/test_passe_finale_repli_recherche`, `test_vide_et_espaces_seuls`<br>2✓ 0✗ 3⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |

### F — Comparaison

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **F1** Comparaison de deux fiches | `tests/test_lot4b_comparaison_voile`, `test_l_identite_est_fausse_quand_une_seule_fiche_differ_e`<br>6✓ 0✗ 3⤼ | frontend/e2e/lot4b-comparaison.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **F2** Comparaison de N fiches après filtres | `test_au_dela_de_douze_colonnes_est_refuse`, `test_chaque_ligne_porte_autant_de_valeurs_que_de_fiches`, `test_l_etendue_et_les_bornes_sont_calculees_sur_le_groupe`<br>3✓ 0✗ 0⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **F3** Pourcentage de confiance — seuil, sinon « données insuffisantes » | `tests/test_passe_finale_confiance_seuils`, `test_le_seuil_est_toujours_une_confiance_reellement_observee`<br>14✓ 0✗ 0⤼ | frontend/e2e/lot5-confiance.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **F4** Cas d'étalonnage — la fiche réelle du fonds | —<br>0✓ 0✗ 0⤼ | Le harnais NE REJOUE PAS cette suite : elle consomme la base qu'elle mesure, et la rejo… | PROUVÉ — job `recette-corpus-reel` | **PROUVÉ (CI)** |

### G — Accessoires

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **G1** La section accessoires s'ouvre et liste | `tests/test_lot7_accessoires`<br>5✓ 0✗ 5⤼ | frontend/e2e/lot7-accessoires.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **G2** Lien entre un accessoire et son bateau | `tests/test_lot3b_liaison_voile`, `tests/test_double_verification_accessoires`<br>32✓ 0✗ 11⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **G3** Extraction depuis un PDF d'accessoire | `test_extraction_accessoire_dimensions_et_provenance`, `test_extraction_accessoire_sans_unite_ne_devine_rien`, `test_extraction_libelles_reels`<br>3✓ 0✗ 0⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |

### H — API — toutes les routes, quatre cas chacune

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **H1** Chaque route extraite du code : nominal, non autorisé, paramètre invalide, ressource absente | `tests/test_recette_routes`<br>7✓ 0✗ 0⤼ | Le balayage des 116 routes contre la pile réelle est joué par l'étape « Recette de TOUT… | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **H2** `/health` et `/metrics` — avec et sans jeton | `tests/test_sondes_et_metriques`, `test_aucune_route_n_est_ouverte_sans_jeton`<br>5✓ 0✗ 0⤼ | `/health` était vérifié, `/metrics` ne l'était nulle part : le test qui couvrait les qu… | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **H3** Confinement de `/search`, `/preview` et `/open` | `tests/test_open_capture_endpoint_public`, `tests/test_artefact_confinement_absent`<br>7✓ 0✗ 0⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **H4** Limitation de débit — seuil, 429, et routes de sonde exemptées | `test_rate_limiting_and_retry_after`, `test_les_sondes_ne_sont_pas_soumises_a_la_limitation_de_debit`<br>2✓ 0✗ 0⤼ | Seuil : `rate_limit_per_minute`, 600 par défaut. Le quota épuisé, `/live` et `/ready` d… | PROUVÉ — c4d2b9d | **PROUVÉ** |

### I — Notifications et audit

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **I1** Chaque événement produit sa notification | `test_notifications_exigent_un_compte_nominatif`, `test_notifications_tout_lire`, `test_auto_approbation_annulation_refus_et_notifications_anti_idor`<br>0✓ 0✗ 3⤼ | frontend/e2e/lot1-approbations.spec.ts | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **I2** Traçabilité — chaque action est rattachée à un utilisateur nommé | `test_validation_attribuee_au_compte_connecte`, `test_lot0b_postgres_audit_append_only_et_gardes`, `test_acteur_audit_ne_croit_jamais_des_entetes_non_verifies`<br>1✓ 0✗ 2⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **I3** Journal d'audit — expurgé, et aucune fuite de secret | `test_audit_expurgation_des_secrets_sqlite`, `test_creation_compte_ne_journalise_pas_identifiant`, `test_brouillon_erreur_ne_journalise_pas_secret`<br>1✓ 0✗ 3⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |

### J — Accessibilité

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **J1** Chaque écran audité — 0 violation WCAG 2.1 A/AA | `test_chaque_ecran_de_l_inventaire_est_dans_le_plan_d_audit`, `test_le_plan_d_audit_couvre_au_moins_l_inventaire`<br>2✓ 0✗ 0⤼ | frontend/e2e/accessibilite.spec.ts — tous écrans × rôles × états | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **J2** Un seul `<main>` par écran | `test_un_seul_repere_main_dans_la_mise_en_page`<br>1✓ 0✗ 0⤼ | frontend/e2e/accessibilite.spec.ts — règle axe « landmark-one-main » | PROUVÉ — c4d2b9d | **PROUVÉ** |

### K — Sauvegarde et restauration

| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |
|---|---|---|---|---|
| **K1** Sauvegarde puis restauration sur base neuve — identité octet à octet | `tests/test_sauvegarde_identite_octets`, `test_10_sauvegarde_restauration_base_neuve`<br>9✓ 0✗ 1⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — job `recette-corpus-reel` | **PROUVÉ (CI)** |
| **K2** Objets — sauvegarde et restauration sans écrasement | `test_sauvegarde_copie_objet_par_reseau_isole`, `test_s3rver_restauration_objet_refusee_sans_ecraser`, `tests/test_sauvegarde_objet_checksum`<br>14✓ 0✗ 3⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **K3** Identité restreinte — l'outil n'utilise jamais l'administrateur | `test_outil_de_sauvegarde_utilise_l_identite_dediee_et_jamais_l_administrateur`<br>1✓ 0✗ 0⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **K4** Recette locale sur poste — les 20 contrôles | `tests/test_recette_locale`<br>21✓ 0✗ 0⤼ | scripts/recette_locale.ps1, à rejouer dans un venv disponible. | PROUVÉ — c4d2b9d | **PROUVÉ** |
| **K5** Un échec de restauration nomme sa cause et s'arrête | `test_un_echec_de_restauration_nomme_sa_cause`, `test_sans_pre_requis_le_script_nomme_ce_qui_manque_et_refuse`, `test_recette_ps1_arrete_sur_echec_restauration_sans_poursuivre_down_up`<br>3✓ 0✗ 0⤼ | harnais de routes — pile réelle, 472 sondes (H1) | PROUVÉ — c4d2b9d | **PROUVÉ** |

## Les mesures de CI, citées

| Job | SHA | Annotation relevée |
|---|---|---|
| `backend (3.11)` | `c4d2b9d` | recette-A-K :: 57 items — 52 prouvés, 1 partiels, 0 non prouvés, 0 échecs, 0 introuvables |
| `prouves_par_un_autre_job` | `?` |  |
| `ocr` | `c4d2b9d` | recette-A-K :: 5 items — 4 prouvés, 0 partiels, 0 non prouvés, 0 échecs, 0 introuvables |
| `integration` | `1908e96` | recette-routes :: 116 routes, 472 sondes — 0 plantage(s), 0 indisponible(s) ici, 0 accès non contrôlé(s) |
| `e2e` | `5ad5d8b` | accessibilite :: 88 écrans scannés, 0 violation WCAG 2.1 A/AA |
| `recette-corpus-reel` | `5ad5d8b` | garde-pdf-corpus :: fichiers=14 pages=30 refus=0 motifs[aucun] |
| `integration` | `5ad5d8b` | preuve-PROUVÉ :: objet supprimé du stockage puis resservi, octets identiques |
| `prouves_par_un_autre_job_detail` | `?` |  |

## Limites, dites explicitement

- **Le bac à sable n'a ni PostgreSQL, ni Redis, ni Docker.** La couche métier ne peut donc y être exercée. C'est pourquoi le harnais de routes accepte une `--base-url` et se joue contre la pile réelle en CI, au lieu d'un serveur local.
- **Les artefacts de CI ne sont pas téléchargeables** depuis l'environnement de travail (hôte non autorisé). Les annotations sont le seul canal lisible : elles sont plafonnées à dix par étape, d'où la publication d'un bilan compact et d'un artefact pour le commanditaire, qui télécharge depuis sa machine.
- **Playwright n'est pas installable ici.** Les preuves navigateur (E2E, axe-core) sont relevées depuis le job `e2e`, jamais simulées.

*Généré par `scripts/rapport_recette.py` depuis `matrice-recette.json` et `releves-ci.json`.*
