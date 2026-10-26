# Plan directeur v2 — Lot 3a : extraction structurée des voiles

Statut : **livré et prouvé** le 2026-10-25 sur la branche
`arena/b02ed560-seamtech-search`, au SHA d'implémentation
`be3a6fa1cf98666426182cc12937007744c39000`.

## Objectif du plan

Le Lot 3a transforme une fiche de fabrication PDF lue par les gabarits
déterministes existants en une **extraction de voile normalisée, traçable et
requêtable** : dimensions canoniques, matériaux, provenance champ par champ,
confiance, page/zone dans le PDF et contrôles de plausibilité géométrique.

Il ne résout pas encore le bateau vers le référentiel canonique :
`modele_bateau_brut` reste le libellé lu dans le PDF. La liaison au modèle,
les statuts de résolution, l'écran de correction côte à côte et la boucle
d'apprentissage local appartiennent au Lot 3b.

## Écart réel mesuré avant implémentation

L'audit de départ a constaté un socle déjà solide, à prolonger plutôt qu'à
remplacer :

- `FicheExtraite` et `ChampExtrait` portaient déjà valeur brute, valeur
  normalisée, méthode, confiance, page, zone, rang et cible ;
- `fiche_champ_extrait`, les tables filles typées et l'écriture
  transactionnelle existaient, avec les protections RG3/RG11 ;
- les conversions mm/cm → m, les contrôles RG16 et la vérité terrain
  7792-SO à 74 cibles existaient ;
- en revanche, il n'existait ni table `sail_extractions`, ni modèle de voile
  normalisé indépendant des tables filles, ni rapport d'or mesurant cette
  projection normalisée.

La voie OCR générique reste séparée du moteur de gabarits. Le Lot 3a ne
prétend donc pas extraire structurellement un PDF uniquement scanné : il
traite les PDF à texte natif reconnus par gabarit. Un gabarit inconnu passe
par le filet historique : mesures libres conservées, extraction
`a_reprendre`, aucune cote structurée inventée.

## Contrat livré

### Modèle normalisé

`seamtech_search.extractions_voile.ExtractionVoile` expose notamment :

- les liens optionnels `id_fiche` et `id_document`, le code de fiche, le
  seul nom de fichier source (jamais son chemin d'archive complet) et
  l'empreinte SHA-256 du PDF ;
- le gabarit et sa version, la version d'extracteur `lot3a-v1` et la méthode
  principale ;
- `type_voile`, `gamme`, `designation_brute`,
  `modele_bateau_brut` et `taille_bateau_brute` ;
- `slu_m`, `sle_m`, `sf_m`, `shw_m`, `spa_m2`, `tetiere_m` et `poids_kg` ;
- les matériaux structurés (rôle, niveau, désignation, grammage, mesure) ;
- la provenance de chaque champ : valeurs brute, normalisée et canonique,
  unités brute/canonique, méthode, confiance dans `[0, 1]`, page, zone,
  rang, table/colonne cibles, état et avertissements ;
- les anomalies, le score qualité, la confiance moyenne et deux statuts
  séparés : complétude (`complete`, `partielle`, `a_reprendre`) et
  plausibilité (`plausible`, `a_verifier`, `implausible`, `indeterminee`).

Il n'y a volontairement **aucun `boat_model_id`** dans ce modèle ni dans la
table 028.

### Unités : explicite ou rien

- Une unité explicitement lue fait foi pour la canonisation :
  `6600 mm → 6,6 m`, `550 cm → 5,5 m`, `157100 cm² → 15,71 m²`,
  `700 g → 0,7 kg`, `30 mm → 0,03 m` pour la têtière.
- Une valeur nue utilise l'unité portée par son champ typé historique
  (`_m`, `_m2`, `_cm`, `_kg`), sans autre interprétation.
- Une valeur nue hors plage qui « pourrait » être en mm/cm n'est jamais
  rescalée par devinette : elle reste visible, reçoit `unite_suspecte` et
  rend l'extraction implausible.
- Une unité incompatible (`kg` pour une longueur) ne devient pas des
  mètres ; une dimension négative ne fait pas échouer l'extraction : elle
  est signalée et exclue de la colonne canonique positive.

### Plausibilité

Les contrôles RG16 existants sont réévalués sur les dimensions canoniques :
plages physiques, ordre des cotes et cohérence de surface des portants avec
le facteur toléré figé `[0,55 ; 1,30]`. Le Lot 3a ajoute les constats de
complétude et d'unité (`modele_bateau_manquant`, `type_voile_manquant`,
`cote_manquante`, `materiau_manquant`, `unite_suspecte`,
`unite_incompatible`, `unite_corrigée`). Une confiance élevée ne rend jamais
une géométrie incohérente « plausible ».

### Base de données — migration 028

La migration `028_sail_extractions` ajoute uniquement la table
`sail_extractions` et ses index. Elle ne convertit ni ne réécrit aucune
donnée existante.

Garanties portées par la base :

- clé d'idempotence unique ;
- liens `fiche`/`documents` en `ON DELETE SET NULL`, afin de conserver la
  preuve d'extraction si un lien disparaît ;
- empreinte SHA-256 contrainte à 64 hexadécimaux ;
- dimensions strictement positives lorsqu'elles sont présentes ;
- `materiaux`, `champs` et `anomalies` en tableaux JSONB ;
- scores bornés à `[0, 1]` et vocabulaires de statuts fermés ;
- index par fiche, document, empreinte, code et file « à reprendre /
  à vérifier ».

### Écritures et idempotence

- `ecrire_fiche()` upsert l'extraction Lot 3a dans la **même transaction**
  que la fiche et ses tables filles. Une fiche validée préservée par RG11 ou
  une ré-extraction refusée à cause de corrections humaines ne remplace pas
  l'extraction normalisée en silence.
- Le dépôt d'un dossier lie ensuite, toujours dans sa transaction,
  l'extraction au document PDF principal et à son empreinte SHA-256.
- Rejouer la même extraction met à jour la même ligne. La résolution de clé
  reconnaît une ligne déjà créée par document, fiche ou empreinte afin de ne
  pas créer de doublon lorsqu'un lien arrive dans un second temps.
- La migration ne backfille pas les fiches existantes : les nouvelles
  écritures/dépôts remplissent la table, et l'outil CLI peut enregistrer
  explicitement des PDF déjà connus (voir ci-dessous).

### Rapport CLI d'or

`scripts/rapport_extraction_or.py` est 100 % local :

```bash
# Banc par défaut, lecture seule
python scripts/rapport_extraction_or.py --format json

# Extraction d'un PDF sans attendu : aucun taux n'est fabriqué
python scripts/rapport_extraction_or.py --pdf chemin/fiche.pdf --format texte

# Échantillons d'entreprise décrits par un manifeste JSON local
python scripts/rapport_extraction_or.py --manifeste manifeste-or.json --format json

# Enregistrement explicite (jamais par défaut) ; lie par code si la fiche
# existe déjà, sans créer ni réécrire de fiche
python scripts/rapport_extraction_or.py --manifeste manifeste-or.json \
  --enregistrer --database-url 'postgresql://…'
```

Codes de retour : `0` conforme ou extraction sans attendu réussie, `1` sous
le seuil, `2` erreur d'entrée ou d'extraction. Le seuil par défaut du banc
versionné est 100 %.

Mesure du banc par défaut :

- 7792-SO, champs Lot 3a : **17/17** ;
- 7792-SO, vérité historique de fiche : **74/74** ;
- génois synthétique, champs Lot 3a : **11/11** ;
- total publié : **102/102, soit 100 %**.

## Commits du lot

- `2fcac3f` — contrat de test RED du Lot 3a ;
- `e190978` — migration 028, modèle normalisé, conversions, persistance,
  dépôt et rapport CLI ;
- `be3a6fa` — durcissement : aucune unité absente/incompatible devinée,
  valeurs négatives signalées sans crash, fixture PostgreSQL compatible
  avec la borne historique `fiche_cotes.spa_m2 NUMERIC(8,3)`.

Le premier passage CI de `e190978` a d'ailleurs échoué exactement sur ce
dépassement numérique PostgreSQL réel ; il a été corrigé dans `be3a6fa`, pas
masqué. Les preuves ci-dessous portent sur `be3a6fa`.

## Preuves au SHA exact

Workflows GitHub Actions terminés avec succès pour
`be3a6fa1cf98666426182cc12937007744c39000` :

- CI `40225963741` : **13/13 jobs verts** (backend Python 3.11/3.12/3.13,
  frontend, e2e, intégration Docker, OCR, sauvegarde, hors-ligne réel,
  sécurité des dépendances et recettes) ;
- Qualification native `40225963627` : succès ;
- Qualification réservés `40225963613` : succès ;
- Perf comparaison `40225963568` : succès.

Mesures publiées par la CI :

- PostgreSQL réel : **300 tests passés, 0 skipped** (297 après le Lot 1,
  +3 épreuves Lot 3a : écriture de fiche idempotente, enregistrement autonome
  et liaison document, dépôt complet) ;
- file durable Redis réelle : **89 tests passés, 0 skipped** ;
- couverture backend : **85,90 %**, plancher inchangé à 85 % ;
- navigateur : Lot 1 **7/7 au premier essai**, parcours canonisés **8/8**,
  authentification **34/34**, doublons **2/2**, scan OCR **4/4** ;
- parcours machine de la vraie fiche 7792-SO : 858 ms (temps machine
  uniquement, pas un chronométrage humain) ;
- latences publiées sans instrumentation : plus grand p95 des annotations
  backend à **51,2 ms** (1 500 fiches), sous le critère produit de 100 ms ;
  l'annotation de « dérive possible » au-delà de 50 ms est non bloquante et
  reste à surveiller, sans être présentée comme un dépassement du critère.

Vérifications locales avant la CI : 14 tests Lot 3a purs/CLI verts, puis
222 tests adjacents (extraction, gabarits, dépôt, persistance, migrations,
validation d'extraction) verts hors marqueurs de services réels. Le bac à
sable ne fournit ni PostgreSQL ni Docker : les épreuves de service réel
ci-dessus sont celles de GitHub Actions.

## Déploiement et rollback

### Déploiement

1. Sauvegarder PostgreSQL avant montée de version et conserver la sauvegarde
   hors du poste applicatif.
2. Déployer le backend du commit Lot 3a ; la migration 028 s'applique au
   démarrage sur PostgreSQL. Aucun changement frontend n'est requis par ce
   lot.
3. Vérifier `/health` (`schema_metier_a_jour`), la présence de
   `028_sail_extractions` dans `schema_migrations` et la table
   `sail_extractions`. Un compte initial de 0 ligne est normal : la
   migration ne backfille pas.
4. Exécuter le rapport d'or en lecture seule et exiger 100 % au seuil par
   défaut.
5. Déposer ou réécrire une fiche d'essai `a_valider`, puis contrôler une
   ligne `sail_extractions` liée à la fiche ; après dépôt, elle doit aussi
   porter `id_document` et l'empreinte SHA-256 du PDF.
6. Pour les PDF déjà archivés, n'utiliser `--enregistrer` qu'avec le PDF
   source exact de la fiche. L'outil lie par code existant mais n'est ni un
   mécanisme de correction humaine ni un contournement de RG11.

### Rollback

La migration 028 est additive et ne convertit aucune donnée :

1. arrêter les écritures et sauvegarder l'état post-Lot 3a ;
2. redéployer le backend pré-Lot 3a `b534723` en laissant
   `sail_extractions` en place : l'ancien code l'ignore et continue de lire
   les fiches historiques ;
3. ne pas supprimer la table ni sa ligne `schema_migrations` pendant un
   rollback de code : ce sont des preuves d'extraction réutilisables lors du
   retour en avant ;
4. si un état de base strictement antérieur est exigé par le métier,
   restaurer la sauvegarde prise avant déploiement, puis redéployer
   `b534723`, en assumant explicitement la perte des écritures postérieures
   à cette sauvegarde. À défaut, corriger en avant.

## Sécurité et confidentialité

- Aucune nouvelle route HTTP, aucun écran et aucun service externe ne sont
  ajoutés par le Lot 3a ; le traitement reste local (pdfplumber + gabarits).
- `sail_extractions` et les rapports ne stockent pas le chemin complet
  d'archive : seul le nom de fichier et l'empreinte sont conservés.
- Les valeurs brutes peuvent néanmoins contenir les libellés déjà présents
  dans les PDF (bateau, désignation, matériaux). Un rapport produit sur des
  documents d'entreprise doit rester local et ne doit pas être commité.
- Aucun nouveau document client ni extrait nominatif n'a été ajouté au
  dépôt : les épreuves réutilisent le 7792-SO déjà versionné et des données
  synthétiques.

Réserve inchangée : le dépôt GitHub reste public sur décision explicite du
donneur d'ordre, contrairement à l'exigence du plan (dépôt strictement
privé, documents clients / Loi 2004-63). Cette dérogation reste portée sous
sa responsabilité ; elle ne doit pas être interprétée comme une autorisation
d'ajouter de nouveaux documents clients.

## Périmètre non prétendu

Ce document clôt uniquement le **Lot 3a**. Restent à livrer et à prouver :
3b (résolution du modèle brut vers le référentiel, écran de correction côte à
côte et boucle d'apprentissage locale), 4b (comparaison bloc par bloc sur
valeurs vérifiées), 5 (confiance de réemploi), 6 (cohérence UI et recette
complète) et DEP (préparation maximale du déploiement hors ligne). Aucune
extraction structurée de PDF uniquement scanné n'est prétendue ici. Aucun
merge vers `main` n'est effectué sans autorisation explicite.
