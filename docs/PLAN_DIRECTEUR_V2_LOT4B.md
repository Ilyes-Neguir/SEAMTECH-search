# Plan directeur v2 — Lot 4b : Fonction A, comparaison de voiles bloc par bloc

Branche : `arena/b02ed560-seamtech-search`. Base : Lot 3b livré et prouvé à
`21de51b2f7f1e2ba5cf50c359a47e2e7459abf77`. Implémentation Lot 4b prouvée au
SHA `97f6b8560efa8472ab8443e82e159e172f1ad832` par les quatre workflows
listés ci-dessous. `main` n'a pas été touchée. Aucune migration (lecture
seule), comme prévu au plan.

## Objectif du plan

Comparer deux voiles bloc par bloc sur les valeurs **vérifiées** : mêmes
extractions normalisées du Lot 3a, résolues vers le modèle de bateau
canonique au Lot 3b, corrections d'approbateur incluses (les colonnes
canoniques portent déjà la valeur corrigée). Écarts par dimension en unité,
en cm pour les longueurs et en % par rapport à la voile de référence A.
Aucun verdict de réutilisation : c'est la Fonction B (Lot 5).

## Mesure préalable sur fiches réelles (hors Git)

Le commanditaire a transmis 9 fiches PDF d'entreprise le 2026-10-25. Elles
ont été mesurées **localement, sans qu'aucun extrait n'entre dans Git** (le
dépôt est public sur sa décision) :

- les gabarits existants reconnaissent ces mises en page (génois, GV
  fullbatten, jade) mais n'en extraient qu'une partie des cotes (génois :
  la SLU seule sur ces exemplaires ; GV/jade : extractions « à reprendre » ;
  modèle de bateau non parsé) ;
- le spi 7792-SO de référence, lui, reste extrait à 100 % sur son gabarit.

Conséquences assumées pour ce lot : la comparaison est conçue honnête avec
des données partielles (manquants affichés, chevauchement averti), et
l'amélioration des gabarits génois/GV/jade est un chantier séparé — alimenté
par les corrections d'approbateur du Lot 3b et, à terme, par un échantillon
d'or d'entreprise exécuté localement avec l'outil du Lot 3a (jamais commité).

## Contrat livré

### Service `seamtech_search/comparaison_voile.py`

- `comparer_extractions(a, b)` (pur) : blocs identification, dimensions
  (SLU/SLE/SF/SHW/SPA/têtière/poids ; `delta` B−A, `delta_cm` pour les
  longueurs, `delta_pct` / A), matériaux appariés par rôle+niveau
  (grammage Δ%), qualité (complétude, plausibilité RG16, scores, liaison,
  nombre de corrections). Synthèse factuelle : dimensions communes,
  identiques, écart % maximal et son champ — **aucune moyenne**, aucun
  pourcentage de confiance déguisé.
- Honnêteté même-modèle : `comparable` exige le même `boat_model_id`
  canonique ET le même type de voile normalisé (accents/casse/tirets pliés).
  Modèles différents, non liés, types différents, moins de 2 dimensions
  communes ou voile implausible ⇒ avertissements explicites, comparaison
  affichable mais jamais présentée comme « même modèle ».
- `candidats_comparaison` : uniquement même `boat_model_id` + même type,
  triés par dimensions communes décroissantes ; une source non liée rend
  `raison = "non_liee"` au lieu de ressemblances de libellés.
- `comparaison_entre` : enrichit des noms canoniques de modèles et des
  comptes de corrections (la correction approbateur est la valeur comparée).

### API (lecture seule, session nominative — tout rôle)

- `GET /comparaison-voiles?a=<id>&b=<id>` (relais navigateur
  `/api/comparaison-voiles`, correspondant au `GET /api/compare/sails` du
  plan, adapté aux conventions du dépôt) ; `a == b` ⇒ 422 ; inconnu ⇒ 404 ;
  jeton de service seul ⇒ 401.
- `GET /comparaison-voiles/candidats?id_extraction=<id>`.

### Écran `/comparaison`

Sélecteurs A (référence) et B (candidats même modèle+type d'abord, puis
toutes les extractions), liens profonds `?a=&b=`, badge comparable/indicative,
avertissements, tableau des dimensions avec Δ cm et Δ % colorés (seuils
d'affichage 1 %/5 %, étiquetés comme tels), blocs identification/matériaux/
qualité. Lien « Comparer… » depuis le détail d'extraction du Lot 3b.
Navigation visible pour tout compte nominatif ; le compte de secours est
refusé (pas d'identité nominative).

## Commits du lot

- `fb51ce6` — contrat RED (`tests/test_lot4b_comparaison_voile.py`) ;
- `670a778` — service + API de comparaison ;
- `5edc777` — écran de comparaison, relais Next.js, navigation, lien depuis
  l'écran Extractions ;
- `97f6b85` — parcours navigateur live + garde de comptage CI.

## Preuves au SHA exact `97f6b85`

- CI `40241173904` : 13/13 jobs en succès. Suite PostgreSQL réelle :
  **314 passés / 0 sauté** (311 au Lot 3b → +3 : candidats même modèle+type,
  comparaison enrichie — la correction SF d'approbateur est bien la valeur
  comparée, Δ SLU +30,0 cm — rôles API 401/200/422/404). Redis 89/0.
  Couverture globale 86,05 % (plancher 85 %).
- Parcours navigateur (premier essai) : Lot 4b 3/3 — écart SLU réel du seed
  relu (+160 cm entre 7792-SO et le génois Sun 2000), badge « indicative »
  et avertissement « modèles DIFFÉRENTS », lecture seule par lien profond
  pour le rôle lecture, secours refusé ; Lot 3b 3/3, Lot 1 7/7, parcours
  8/8, OCR 4/4 ; parcours complet 7792-SO : 727 ms.
- Qualification native `40241173918`, Qualification réservés `40241173903`,
  Perf comparaison `40241173932` : succès au même SHA.
- Les contrats purs (Δ cm/%, matériaux, avertissements, refus
  auto-comparaison) tournent sans base dans la suite standard.

## Déploiement et rollback

- Aucune migration : redéployer backend + frontend suffit. Vérification :
  ouvrir `/comparaison`, comparer deux voiles connues, contrôler un écart à
  la main ; vérifier qu'une voile non liée n'annonce aucun candidat.
- Rollback : retour code au Lot 3b (`21de51b`). Aucune donnée n'a été
  écrite par ce lot ; rien à nettoyer en base.

## Sécurité et confidentialité

- Lecture seule sous session nominative vérifiée côté backend (jeton de
  service seul refusé, secours refusé à l'écran comme à l'API).
- Les 9 fiches d'entreprise reçues pour calibration n'ont produit aucun
  artefact versionné : ni PDF, ni extrait, ni vérité terrain dans Git. Le
  dépôt reste public sur décision du commanditaire (dérogation RG13) ; pour
  envoyer d'autres dossiers : pièces jointes dans cette conversation
  (traitement local éphémère) ou, pour le fonds complet, exécution locale
  des outils sur le poste/serveur d'entreprise (chantier DEP).

## Périmètre non prétendu

- Pas d'indice de confiance de réutilisation (Fonction B, Lot 5) : les
  couleurs d'écart sont une aide de lecture, pas un verdict.
- Pas d'amélioration des gabarits génois/GV/jade dans ce lot : la mesure
  ci-dessus la recommande comme chantier dédié, avec vérités terrain
  locales et non commitées.
- La comparaison porte sur les extractions persistées ; comparer un PDF
  « à la volée » sans dépôt n'est pas dans le périmètre (le dépôt crée
  l'extraction, puis on compare).
