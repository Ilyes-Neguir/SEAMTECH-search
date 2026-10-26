# Plan directeur v2 — Lot 3b : résolution du modèle de bateau, écran de correction et apprentissage local

Branche : `arena/b02ed560-seamtech-search`. Base : Lot 3a livré et prouvé à
`480ecea75003d44d812ea6bcc8013883d5d036f7`. Implémentation Lot 3b prouvée au
SHA `52a61feae7b00da66741f9f5d18f85683b865dda` par les quatre workflows listés
ci-dessous. `main` n'a pas été touchée.

## Objectif du plan

Le Lot 3a a livré des extractions de voiles normalisées dont le modèle de
bateau restait un libellé BRUT (`modele_bateau_brut`), délibérément non
résolu. Le Lot 3b le résout vers le référentiel canonique unique du Lot 2
(`boat_models`), ouvre l'écran de correction côte à côte de l'approbateur
(PDF + valeurs + provenance) et met en place la boucle d'apprentissage locale
: chaque décision humaine (liaison, correction récurrente) améliore les
suggestions et les liaisons automatiques suivantes, sans jamais écrire une
donnée métier à la place d'un humain. Tout reste 100 % local (PostgreSQL,
pg_trgm, référentiel du Lot 2) ; aucune API externe n'est appelée.

## Écart réel mesuré avant implémentation

Audit du code au SHA `480ecea` :

- aucune migration `029`, aucun `boat_model_id` sur `sail_extractions`,
  aucune table de corrections d'extractions : le Lot 3b partait d'une ardoise
  propre ;
- le référentiel du Lot 2 exposait déjà toutes les primitives nécessaires :
  résolution exacte/alias sans préfixe (`resoudre_modele_depuis_texte`),
  classement exact > alias > préfixe > flou (`rechercher_modeles`), création
  de modèle et d'alias avec conflit explicite (`assurer_modele`,
  `assurer_alias`) ;
- un écran de correction côte à côte existait déjà pour les FICHES
  (`/validation`, PDF + zones surlignées) ; il manquait son pendant pour les
  EXTRACTIONS (liaison bateau, dimensions canoniques, provenance Lot 3a) ;
- le noyau de correction fiche (`corriger_champ_sous_curseur`, révision
  optimiste + propagation canonique + verrou RG11) était réutilisable tel
  quel pour propager une cote corrigée depuis l'extraction.

## Contrat livré

### Schéma — migration `029_extraction_liaison_corrections`

- `sail_extractions` reçoit `boat_model_id` (FK `boat_models`),
  `statut_liaison` (`non_resolu` / `lie_automatique` / `lie_humain` /
  `ambigu` / `sans_modele`), `methode_liaison` (`cle_exacte` / `alias_exact`
  / `fiche_bateau` / `humain` / `humain_nouveau_modele`), `lie_le`, `lie_par`
  (FK `utilisateur`). Une contrainte de cohérence portée par la base interdit
  tout état intermédiaire : un statut « lié » exige un modèle, un statut non
  lié l'interdit.
- `extraction_corrections` : historique des corrections d'approbateur
  (champ, valeur avant/après JSONB, auteur, motif, propagation `aucune` /
  `fiche`). La provenance machine (`sail_extractions.champs`) n'est jamais
  réécrite : la colonne canonique porte la valeur courante, l'historique
  porte la décision humaine.
- `extraction_motifs_appris` : motifs de correction récurrents (gabarit,
  champ, valeur lue → valeur corrigée, occurrences). Ils ne servent qu'à
  SUGGÉRER à l'approbateur (« déjà corrigé en X, N fois ») ; ils
  n'appliquent jamais seuls une valeur.
- La migration elle-même ne résout aucune extraction existante : elle marque
  seulement `sans_modele` celles sans libellé lu. 43 tables métier, 49 tables
  publiques.

### Résolution — déterministe, jamais devinée

`seamtech_search/liaison_voile.py` :

- liaison automatique uniquement sur preuve exacte : clé normalisée d'un
  modèle, alias exact déjà appris, ou bateau de la fiche déjà lié dont le
  libellé (nom, nom + taille) correspond exactement ;
- désaccord entre la clé exacte et le bateau de la fiche, ou simples
  candidats préfixe/flous ⇒ `ambigu` avec les candidats présentés ; aucun
  candidat ⇒ `non_resolu`. « Bavaria 4 » ne lie ni 45 ni 45.1 ; une faute de
  frappe (« Bavria 45 ») ne lie rien ;
- la résolution tourne dans la même transaction que l'écriture d'une
  extraction (dépôt, ré-extraction) et en lot borné à la demande ; une
  liaison décidée par un approbateur (`lie_humain`) n'est jamais revisitée
  par l'automatisme ni écrasée par une ré-extraction (esprit RG11).

### Décision humaine et apprentissage

- `POST /extractions-voile/{id}/lier` (approbateur seul) : rattacher à un
  modèle existant OU créer le modèle canonique — jamais les deux, jamais
  aucun. Le libellé brut devient alors un alias appris (source
  `apprentissage_lot3b`) : la prochaine extraction portant le même libellé
  se lie toute seule en `alias_exact`. Un libellé déjà alias d'un autre
  modèle est refusé en 409, sans vol d'étiquette, transaction annulée.
- `POST /extractions-voile/{id}/corriger` (approbateur seul) : 11 champs
  corrigibles (type, gamme, modèle/taille bruts, 7 dimensions canoniques).
  Valeurs validées strictement (nombres simples > 0 en unité canonique,
  aucun rescaling deviné d'une saisie humaine). La plausibilité RG16 est
  recalculée après chaque correction de cote. Une cote qui a une trace dans
  la fiche est propagée dans la même transaction par le noyau existant
  (révision obligatoire : 428 sans révision, 409 si périmée, verrou RG11
  inchangé). Corriger le libellé de bateau rouvre explicitement la
  résolution. Chaque correction alimente le motif appris correspondant.
- Lecture (`GET /extractions-voile`, `GET /extractions-voile/{id}`) ouverte
  à tout compte nominatif ; toute mutation exige une session nominative
  d'approbateur (jeton de service seul : 401 ; autre rôle : 403). Chaque
  mutation est journalisée (`audit_logs`) en plus de l'historique métier.

### Écran approbateur — `/extractions`

File filtrable par statut de liaison et texte libre, bouton « Résoudre le
lot » (preuves exactes uniquement), détail côte à côte : panneau de liaison
(libellé brut, modèle lié + méthode, candidats à un clic, création de
modèle), valeurs avec provenance (valeur brute, confiance, page) et zone
surlignée dans le PDF source à droite, motif de correction journalisé,
suggestions de motifs appris, anomalies, historique des corrections. Lien de
navigation et écran réservés à l'approbateur nominatif ; les autres rôles
voient un refus explicite. La garde reste double (relais Next.js + backend).

## Commits du lot

- `4d2776a` — contrat RED (`tests/test_lot3b_liaison_voile.py`), confirmé
  rouge avant implémentation ;
- `3af69a5` — migration 029, service de liaison/corrections, routes API,
  gardes de schéma alignées ;
- `41a297a` — écran approbateur côte à côte, relais Next.js, navigation ;
- `3d8ae9a` — parcours navigateur live + garde de comptage CI ;
- `52a61fe` — alignement d'un contrat Lot 3a devenu caduc (la colonne
  `boat_model_id` existe depuis la 029) et garde « live » du nouveau
  parcours (sauté hors PostgreSQL, comme le Lot 1).

## Preuves au SHA exact `52a61fe`

- CI `40235516711` : 13/13 jobs en succès. Suite PostgreSQL réelle :
  **311 passés / 0 sautés** (300 au Lot 3a → +11 tests Lot 3b : résolution
  exacte/ambiguë, voie fiche et conflit, apprentissage d'alias de bout en
  bout, création de modèle, conflit d'alias 409, préservation RG11 à la
  ré-extraction, lot et idempotence, correction + plausibilité + motif,
  propagation fiche avec révision 428/409/200, rôles API 401/403/404/200).
  Redis 89/0. Couverture globale 85,95 % (plancher 85 %).
- Parcours navigateur (PostgreSQL réel, premier essai) : Lot 3b 3/3
  (approbateur : file → PDF → correction du libellé → création+liaison avec
  alias appris → cote corrigée relue dans l'historique ; lecture et
  utilisateur refusés), Lot 1 7/7, parcours canonisés 8/8, OCR 4/4, auth
  34/34, doublons 2/2, validation/badge/dimension/extrait 12/12 ; parcours
  complet de la vraie fiche 7792-SO : 930 ms.
- Qualification native `40235516673`, Qualification réservés `40235516638`,
  Perf comparaison `40235516654` : succès au même SHA. La Perf signale comme
  aux lots précédents un avertissement de dérive d'environnement CI
  (p95 mi-échelle ≈ 51 ms), non bloquant et sans rapport avec ce lot.
- Local (bac à sable sans PostgreSQL) : suite complète hors PostgreSQL —
  1000 passés, seul échec connu et bénin : `test_recette_locale.py` sur les
  fins de ligne CRLF d'un script PowerShell, artefact de ce poste (le
  fichier versionné est correct ; la CI le vérifie vert).

## Déploiement et rollback

- Montée de version : redéployer backend + frontend ; la migration 029
  s'applique au démarrage de l'API (PostgreSQL). Aucun remplissage n'est
  nécessaire : les extractions existantes restent `non_resolu`/`sans_modele`
  jusqu'à un « Résoudre le lot » explicite ou une nouvelle écriture.
  Vérification d'acceptation : ouvrir `/extractions` en approbateur,
  résoudre le lot (seules les preuves exactes lient), vérifier qu'aucune
  liaison `ambigu` n'a été tranchée sans décision.
- Rollback code : revenir au backend+frontend d'avant Lot 3b (`480ecea`)
  après sauvegarde PostgreSQL. Les colonnes et tables 029 sont additives et
  ignorées par l'ancien code (ses écritures laissent le statut par défaut
  `non_resolu`, cohérent avec la contrainte). Les alias appris vivent dans
  le référentiel du Lot 2 et restent valides ; les corrections historisées
  restent en base, invisibles pour l'ancien code.
- Rollback de données : ne restaurer une sauvegarde d'avant la montée que
  sur décision métier explicite — cela perdrait les liaisons et corrections
  décidées entre-temps par les approbateurs. En cas de doute : corriger en
  avant, pas restaurer.

## Sécurité et confidentialité

- Mutations réservées à l'approbateur nominatif (session réelle vérifiée
  côté backend ; le jeton de service seul ne lie ni ne corrige rien) ; CSRF
  double-submit inchangé via les relais Next.js ; audit de chaque mutation
  (`audit_logs`) en plus de l'historique `extraction_corrections` et des
  colonnes `lie_par`/`lie_le`.
- Aucune donnée client nouvelle n'entre dans Git : les tests utilisent le
  corpus déjà versionné et des libellés synthétiques. Le dépôt GitHub reste
  public sur décision explicite du commanditaire (dérogation RG13 déjà
  consignée aux lots précédents) : ne jamais y déposer de rapport généré
  depuis des PDF d'entreprise.

## Périmètre non prétendu

- La comparaison de voiles bloc par bloc (Fonction A, Lot 4b) et l'indice de
  confiance de réutilisation (Fonction B, Lot 5) ne sont pas encore livrés :
  ils consommeront ces extractions liées et corrigées.
- Les motifs appris suggèrent, ils ne corrigent pas : aucune valeur n'est
  appliquée sans décision d'approbateur, et c'est assumé.
- La correction du libellé brut ne réécrit ni le bateau de la fiche ni le
  PDF : elle corrige l'extraction et rouvre la résolution ; la fiche garde
  son propre circuit de validation (écran `/validation`).
