# Plan directeur v2 — Lot 5 : Fonction B, confiance de réutilisation d'une voile

Branche : `arena/b02ed560-seamtech-search`. Base : Lot 4b livré et prouvé à
`98fce87`. Implémentation Lot 5 prouvée au SHA
`921669b9d80d58a03fef40fb6511a1b5e5ec41fc` par les quatre workflows listés
ci-dessous. `main` n'a pas été touchée. Migration **030**
(`evaluations_confiance`), comme prévu au plan.

## Objectif du plan

Répondre honnêtement à : « cette voile vérifiée ressemble-t-elle aux voiles
déjà connues pour ce modèle de bateau et ce type de voile ? » par un **indice
de cohérence robuste** (médiane / MAD / IQR), jamais une probabilité ni une
moyenne. Les valeurs comparées sont les valeurs vérifiées (extractions du
Lot 3a, résolution bateau du Lot 3b, corrections d'approbateur déjà portées
par les colonnes canoniques).

## Règles d'honnêteté (figées, testées, portées aussi par la base)

- **N < 5 échantillons (`CONFIANCE_MIN_ECHANTILLONS`) → aucun pourcentage.**
  Verdict `donnees_insuffisantes`, affiché « Données insuffisantes (N/5) ».
- **Population hétérogène → aucun score.** Une dimension est hétérogène si
  IQR/médiane > 7,5 % ; si au moins la moitié des dimensions communes le
  sont, verdict `heterogene`, score `NULL` — mais les statistiques
  complètes restent affichées : c'est le score qui est refusé, pas
  l'information.
- **Score robuste par dimension** : `100·exp(−z²/2)`, `z = |x − médiane|/σ̂`,
  `σ̂ = IQR/1,349` (sinon `1,4826·MAD` ; σ̂ = 0 ⇒ 100 si x = médiane, sinon
  décroissance relative bornée). Une dimension n'est notée qu'avec n ≥ 3.
  **Le score global est la médiane des scores par dimension** — jamais une
  moyenne, et il exige au moins 2 dimensions notées.
- **Seuil d'alerte `CONFIANCE_SEUIL_ALERTE = 75`** : au-dessus `compatible`,
  en dessous `a_examiner` — le % s'affiche avec sa justification, sans
  verdict positif.
- **Justification obligatoire dès qu'un chiffre existe** : N, et par
  dimension médiane/Q1/Q3/IQR/MAD/min/max, valeur de la candidate, écart %
  à la médiane, z robuste, score ; concordance matériaux (part de la
  population au même rôle/niveau/désignation) ; fiches sources de la
  population. La migration 030 porte la cohérence en contrainte :
  `score IS NULL ⇔ verdict ∈ {donnees_insuffisantes, heterogene}`.

## Contrat livré

### Service `seamtech_search/confiance_reutilisation.py`

- `statistiques(valeurs)` : médiane, quartiles par interpolation linéaire
  (type 7), IQR, MAD, min/max.
- `evaluer_population(candidate, population)` (pur) : verdict + justification
  complète selon les règles ci-dessus.
- Population en base : même `boat_model_id` canonique, même type de voile
  normalisé (accents/casse/tirets pliés, comme au Lot 4b), plausibilité non
  `implausible`, au moins une dimension présente ; **la candidate est exclue
  de son propre échantillon** (N=5 quand 6 voiles liées existent).
- `evaluer_extraction` persiste l'évaluation (qui, quand, verdict, score,
  justification) ; candidate non liée ⇒ `ConfianceRefusee` (aucune
  population n'existe) ; extraction inconnue ⇒ `ExtractionIntrouvable`.
- `lister_evaluations` (historique) et `population_par_modele` (vue complète
  candidate incluse, lecture seule, sans persistance).

### API (`confiance_reutilisation_routes.py`)

- `POST /confiance-reutilisation/evaluer` — **approbateur uniquement**,
  persisté et audité (`confiance_reutilisation.evaluer`) ; jeton de service
  seul ⇒ 401 ; rôle lecture ⇒ 403 ; inconnue ⇒ 404 ; non liée ⇒ 422.
- `GET /confiance-reutilisation/evaluations?id_extraction=` et
  `GET /confiance-reutilisation/population?boat_model_id=&type_voile=` —
  session nominative (tout rôle).

### Écran `/confiance`

Sélection d'une candidate (lien profond `?id=`), population du modèle/type
(N, médiane/IQR/min-max), bouton « Évaluer la confiance » (approbateur),
verdict honnête (jamais de % sous N=5 ni sur population hétérogène), tableau
de justification par dimension, concordance matériaux, fiches sources,
historique des évaluations persistées. Navigation « Confiance » visible pour
tout compte nominatif ; le compte de secours est refusé.

## Corpus Dropbox du commanditaire (état au 2026-10-26)

Le commanditaire a déposé `VOILES 2024.zip` (1 205 608 885 octets) sur son
Dropbox pour nourrir la calibration des gabarits et prouver l'ingestion de
dossiers en conditions réelles. Accès mesuré depuis l'environnement de
développement : le connecteur refuse les fichiers > 100 Mio et le CDN
Dropbox n'est pas joignable depuis la sandbox — le ZIP monolithique est donc
**inaccessible en l'état**. Action demandée au commanditaire : **extraire le
ZIP dans l'interface web Dropbox** (ou déposer l'arborescence décompressée) ;
les PDF unitaires (≪ 100 Mio) seront alors récupérables un par un via le
connecteur, traités **localement et de façon éphémère** (jamais dans Git, le
dépôt est public sur sa décision), avec restitution d'agrégats uniquement
(aucun nom de client ni référence de commande). L'ingestion de production du
fonds complet reste l'étape DEP sur le serveur d'entreprise.

## Commits du lot

- `2dbe348` — contrat RED (`tests/test_lot5_confiance_reutilisation.py`) ;
- `85b5833` — migration 030, service robuste, API, gardes de schéma alignées ;
- `921669b` — écran `/confiance`, relais Next.js, navigation, seed de
  population live + parcours navigateur et sa garde CI.

## Preuves au SHA exact `921669b`

- CI `40249015850` : 13/13 jobs en succès. Suite PostgreSQL réelle :
  **317 passés / 0 sauté** (314 au Lot 4b → +3 : évaluation persistée relue
  avec candidate exclue de sa population, refus sans modèle lié, rôles API
  401/403/200/404 + population complète médiane relue). Redis 89/0.
  Couverture globale 86,11 % (plancher 85 %).
- Parcours navigateur (premier essai) : Lot 5 3/3 — l'approbateur évalue la
  vraie fiche 7792-SO contre les cinq voiles de référence semées du même
  modèle/type : verdict « Cohérente avec la population », justification par
  dimension et 5 fiches sources affichées, évaluation persistée relue dans
  l'historique (N=5) ; lecture consulte sans bouton d'évaluation ; secours
  refusé. Lot 4b 3/3, Lot 3b 3/3, Lot 1 7/7, parcours 8/8, OCR 4/4 ;
  parcours complet 7792-SO : 909 ms.
- Qualification native `40249015834`, Qualification réservés `40249016199`,
  Perf comparaison `40249015823` : succès au même SHA.
- Les contrats purs (statistiques médiane/MAD/IQR, insuffisance, hétérogénéité,
  score à la médiane, justification sources/matériaux) tournent sans base
  dans la suite standard.

## Déploiement et rollback

- Migration 030 additive (une table + index) : redéployer backend +
  frontend ; la migration s'applique au démarrage. Vérification : ouvrir
  `/confiance`, évaluer une voile liée disposant d'au moins 5 consœurs du
  même modèle/type, contrôler une médiane à la main ; vérifier qu'avec moins
  de 5 consœurs l'écran affiche « Données insuffisantes (N/5) » sans aucun %.
- Rollback code : retour au Lot 4b (`98fce87`). La table
  `evaluations_confiance` reste alors en base mais invisible pour l'ancienne
  version (aucun impact) ; restaurer une sauvegarde pré-déploiement ne se
  fait que sur décision métier explicite, en acceptant la perte des
  évaluations consignées depuis — sinon corriger en avant.

## Sécurité et confidentialité

- Évaluer est une décision métier : approbateur uniquement, côté relais
  Next.js comme côté backend (session nominative revérifiée, jeton de
  service seul refusé), action auditée et persistée nominativement.
- Aucun document client n'a été ajouté à Git. Le corpus Dropbox, quand il
  sera extractible, ne produira que des mesures éphémères locales et des
  agrégats anonymisés (règle inchangée malgré le dépôt public, dérogation
  RG13 du commanditaire).

## Périmètre non prétendu

- Le score est un **indice de cohérence dimensionnelle**, pas une
  certification de réutilisation : l'état physique de la voile (usure,
  stock) reste hors périmètre, comme au plan.
- La qualité du verdict dépend des liaisons bateau du Lot 3b et des valeurs
  vérifiées : les gabarits génois/GV/jade actuels n'extraient les fiches
  réelles que partiellement (mesure du Lot 4b) — leur calibration sur le
  corpus Dropbox, dès extraction par le commanditaire, est le chantier qui
  rendra la population réelle exploitable à grande échelle.
- Reste au plan : Lot 6 (cohérence UI globale + recette multi-rôles
  « 3 clics »), puis DEP (préparation du déploiement hors ligne sur le
  serveur d'entreprise, ingestion du fonds complet).
