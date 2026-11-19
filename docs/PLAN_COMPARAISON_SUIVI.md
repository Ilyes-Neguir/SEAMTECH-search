# Suivi d'exécution — plan « comparaison et réutilisation »

**Objet :** exécution complète du plan validé (lots 0 → 8, le lot 2 accessoires
mené sur données réelles), avec les preuves de chaque lot et l'état honnête de
ce qui reste ouvert.

- **Branche :** `arena/765ebdcf-seamtech-search`
- **Base :** `af31922` (jamais `main` directement)
- **Période :** 2026-11-04 → 2026-11-05
- **Méthode :** ECC appliqué en entier — *créer* le lot, *évaluer* le résultat
  par des épreuves automatiques, *corriger* sur la mesure, jamais sur une
  intuition. Aucun lot n'a été déclaré terminé sur une impression.

## Méthode appliquée à chaque lot

1. **Mesurer avant d'écrire.** Chaque lot commence par une lecture du réel :
   le corpus (lot 0), les documents d'accessoires (lot 2), les fiches arbitrées
   en base (lot 3). Deux hypothèses d'audit ont été réfutées par la mesure
   avant d'être mises en code.
2. **Contrat d'épreuves d'abord.** Le comportement attendu est écrit en	test
   avant ou avec l'implémentation ; une épreuve qui échoue produit un correctif
   ciblé, jamais un assouplissement de l'assertion.
3. **Aucune valeur inventée.** Unité absente = non convertie. Modèle ambigu =
   décision humaine. Preuves insuffisantes = aucun seuil publié.
4. **Preuve opposable.** Suite locale, frontend, et CI au SHA exact. Un
   workflow rouge est un fait, pas une gêne : il est documenté ci-dessous.

## Les lots tels qu'exécutés

| Lot | Commit | Ce que le lot apporte |
|---|---|---|
| **0** | `30d6c39` | Cartographie du corpus — mesurer la qualité réelle avant d'écrire une règle (`seamtech_search/qualite/cartographie.py`, 5 épreuves) |
| **1.2** | `62d2377` | Vraisemblance des champs avant écriture : `client_invraisemblable`, `bateau_hors_referentiel` (4 occurrences de `vraisemblance` mesurées dans `seamtech_search/`) |
| **1.3** | `f232cf9` | La référence comparée à trois (dossier / nom de fichier / document) — anomalie `reference_discordante`, **NON BLOQUANTE** et volontairement distincte de 1.4 |
| **1.4** | `d5409fe` | Complétude par gabarit : les champs que le GABARIT déclare obligatoires, jamais une liste devinée — anomalie `champ_obligatoire_manquant`, **BLOQUANTE** |
| **1.5** | `090eec7` | Les contrôles RG16 deviennent OBLIGATOIRES sur toute fiche : le verrou est posé à l'ÉCRITURE, seule voie par où passe nécessairement une fiche |
| **1.1** ⚠️ | `6a485b4` | **Instrument livré, table NON produite.** `scripts/mesurer_correspondances.py` mesure les fusions CERTAINES et PROBABLES ; il n'a **jamais été exécuté sur les 338 fiches**, faute de corpus dans ce dépôt. Voir « Le Lot 1.1 » ci-dessous. |
| (*correctifs*) | `95f445e`, `601f448`, `96dea44` | Deux régressions Lot 1 détectées sur base PostgreSQL réelle ; épreuves d'intégration inter-lots ; correction de la CI rouge |
| (*comparaison groupe*) | `9588ce3`, `be346f1`, `4895cf4` | Écran de comparaison d'un GROUPE de fiches : attributs à gauche, une colonne par fiche, bascule de vue, normalisation des axes (nettoyer ≠ fusionner). **Ces commits figuraient sous l'étiquette « Lot 1 » dans la première version de ce document : c'était un mauvais étiquetage.** Dans la numérotation du plan, ils relèvent du Lot 6. |
| **2** | `f8f893a` | Accessoires : double vérification des cotes, construite sur les documents réels |
| **3** | `0b7ec0b` | Calibration des seuils : la preuve avant la décision (jamais un seuil sans confiance observée) |
| **4** | `cb70b52` | Mode nuit : les filtres déroulants redeviennent lisibles |
| **5** | `46d13aa` | Réutilisation : une cible, et une réponse même avec trois fiches |
| **6** | `7c18bdf` | Filtres actifs : un libellé lisible, un seul bouton pour tout effacer |
| **7** | `3e5d59e` | Recherche par référence : la cause trouvée, pas le symptôme |
| **8** | présent document | Documentation et vérification de bout en bout |

Le lot 2 a été mené en parallèle, comme prévu au plan, parce qu'il exigeait
l'ouverture de documents réels et ne pouvait pas attendre la fin de la chaîne.

## Le Lot 1 — ce qui est livré, et ce qui ne l'est pas

Ce document a d'abord déclaré le Lot 1 « non livré ». **Cette affirmation est
devenue fausse** : les sous-points 1.2 à 1.5 sont livrés, commités et éprouvés.
Elle est remplacée ci-dessous par l'état mesuré. Le principe ne change pas :
jamais une étiquette, toujours une preuve opposable.

### Livré — vérifiable par commande

| Attendu | État mesuré | Commande qui l'établit |
|---|---|---|
| Anomalies du corpus (`anomalies.py`) | **modifié** par les Lots 1.2 à 1.5 | `git log --oneline 62d2377~1..090eec7 -- seamtech_search/fiches/anomalies.py` → 4 commits (`62d2377`, `f232cf9`, `d5409fe`, `090eec7`) |
| Anomalie 1.2 — client invraisemblable, bateau hors référentiel | **4 occurrences** de `vraisemblance` | `grep -rn vraisemblance --include=*.py seamtech_search/` |
| Anomalie `reference_discordante` (1.3) | **4 occurrences** ; les TROIS valeurs sont montrées côte à côte | `grep -rn reference_discordante --include=*.py seamtech_search/` |
| Anomalie `champ_obligatoire_manquant` (1.4) | **3 occurrences** ; les attendus viennent du GABARIT | `grep -rn champ_obligatoire_manquant --include=*.py seamtech_search/` |
| Contrôles géométriques rendus obligatoires (1.5) | verrou posé **à l'écriture**, dans `_ecrire_fiche_dans` | `grep -n appliquer_controles_rg16 seamtech_search/fiches/persistance.py` |

Les deux mécaniques sont **distinctes et non confondues**, conformément à la
décision du commanditaire : 1.3 est **NON BLOQUANT** (le dossier entre en base,
la fiche part en `a_valider`, l'écart est montré) ; 1.4 est **BLOQUANT** (revue).

### NON livré — le Lot 1.1, et pourquoi

La **table de correspondance** (1.1) n'existe pas. Ce qui est livré à `6a485b4`
est l'**instrument** qui la produirait — `scripts/mesurer_correspondances.py`,
qui sépare les fusions CERTAINES (même clé normalisée, graphie la plus fréquente
retenue) des fusions PROBABLES (`confirmee_par=None`, laissées à la décision de
l'exploitante), en réutilisant `qualite/cartographie.py:cle_normalisation`.

L'instrument **n'a jamais été exécuté sur les 338 fiches** : ce dépôt ne
contient pas ce corpus (il contient 6 fichiers d'exemple sous `sample_data/`).
Produire la table supposerait d'inventer ses lignes. Ce n'est pas une nuance :
une table inventée serait précisément l'erreur que ce document a déjà commise
une fois avec une étiquette.

**Conséquence assumée, et non masquée :** la migration de reprise des 338
fiches et le garde-fou anti-réintroduction des variantes fusionnées (A.2) ne
sont pas écrits ; le tableau avant/après par famille d'anomalie sur le corpus
réel (A.3 = 1.6) n'est pas mesuré. Ces deux points sont **impossibles ici**, et
non « reportés » : ils exigent le corpus.

### Ce que le Lot 1 change, concrètement

Le Lot 0 avait identifié **deux pièges réels** : un PDF appartenant à une autre
commande rangé dans un dossier, et un nom de fichier qui contredit la référence
du dossier. Le Lot 1.3 les attrape désormais — les trois écritures de la
référence sont comparées et l'écart est montré au lieu d'être résolu en douce
par un repli sur la source la plus commode.

Une précision, pour ne pas entretenir la confusion que la première version du
document a créée : le module `verification.py` du Lot 2 est une **double lecture
des cotes d'accessoires** (séquentielle puis géométrique). Ce n'est pas la
vérification de référence dossier/fichier/document du Lot 1.3.

## Lot 2 — ce que les documents réels ont appris

Mesure effectuée sur l'arborescence d'accessoires de l'atelier. **Seules des
structures et des comptages sont consignés ici et dans le code :** aucun nom de
client, de bateau ni de fichier, aucune cote réelle. Aucun exemplaire de
document n'a été conservé dans le dépôt.

Trois natures de documents cohabitent, et elles ne se traitent pas de la même
façon :

| Nature | Signature mesurée | Conséquence |
|---|---|---|
| **Formulaire** (fourreau de GSE, lazy bag) | libellés suivis d'une mesure à unité explicite, souvent **collée** au nombre ; casse et accents incohérents d'une ligne à l'autre ; astérisques imprimés | extractable, à condition de normaliser avant de lire |
| **Plan** (taud, planche de coupe) | nombres **nus**, sans unité ni libellé | aucune cote déduite — deviner l'unité fabrique une cote |
| **Numérisé** (millésimes récents) | aucune couche texte | déclaré illisible ; revue manuelle, jamais une lecture inventée |

Deux pièges mesurés ont dicté le design :

* une ligne peut porter **deux cotes** (« … 3.22m - Latte jonc 12mm … »). En
  retenir la seconde la faisait projeter en « largeur » une cote de latte ;
* le **nom du dossier n'est pas une source d'identité fiable** — au moins un
  document porte une référence différente de celle de son dossier.

La double vérification qui en résulte (`seamtech_search/fiches/verification.py`)
ne promet une cote que si deux lectures **indépendantes** concordent : la
lecture séquentielle (libellé → valeur sur la ligne reconstruite) et la lecture
géométrique (mesure la plus proche à droite, bandes superposées, sans
reconstruire aucune ligne). Sans seconde lecture possible, rien n'est promu :
l'absence de preuve n'est pas une preuve d'exactitude. Un désaccord devient une
anomalie **forte** qui renvoie le dossier à l'approbateur — jamais résolue en
silence.

L'échelle est traitée sans import global : les pages sont analysées une seule
fois par le scanner de dossier, transmises à l'écriture, et la relecture est
**idempotente** — relire le même dossier donne strictement le même rapport.
Sans pages transmises, le comportement est exactement celui d'avant le lot :
aucune régression, verrouillée par une épreuve dédiée.

## Preuves

### Locales (ce banc)

| Vérification | Résultat |
|---|---|
| `ruff check .` | **propre** |
| Suite sans PostgreSQL (`-m "not s3 and not perf and not postgres and not redis_queue and not ocr_suite"`) | **2500 passed, 47 skipped, 882 deselected, 3 xfailed, 0 échec** |
| Frontend — `tsc --noEmit` | **propre** |
| Frontend — tests unitaires (`pnpm run test:unite`) | **33 passed, 0 échec** |
| Frontend — `pnpm run build` (Next.js 16.4.3) | **OK** |

### PostgreSQL local — et pourquoi ses 37 échecs ne sont pas des régressions

Un banc PostgreSQL local a été levé (`pgserver`, PostgreSQL 16.2) pour ne pas
se contenter des 882 tests désélectionnés. Résultat brut : **3127 passed,
37 failed, 174 skipped, 30 errors**.

Ce banc est **réduit par construction** : il ne fournit que `plpgsql` et
`vector` — ni `unaccent`, ni `pg_trgm`, ni les binaires `psql`/`pg_dump`, ni
Redis. Chaque échec a été ramené à une cause, sans en excepter un seul :

* `text search configuration "seamtech_unaccent" does not exist` → `unaccent`
  absent (recherche insensible aux accents) ;
* `function similarity(unknown, unknown) does not exist` → `pg_trgm` absent
  (recherche tolérante aux fautes, doublons « probables », résolution
  « ambiguë » des liaisons) ;
* les 30 **erreurs** viennent de fichiers qui lèvent leur propre cluster
  (`test_migration_rollback`), appellent `psql`/`pg_dump` (`test_sauvegarde_*`),
  ou exigent Redis (`test_securite_banc_redis`).

Le produit **prévoit** cette dégradation et la fait sonner :
`tests/test_indexer_accent_parity.py` échoue bruyamment dans ce cas, par
conception (« la contrepartie est ce test, qui échoue bruyamment si une CI
complète empruntait le chemin dégradé »). L'alarme a fonctionné.

La preuve opposable est donc la CI, qui tourne sur un PostgreSQL complet.

### CI au SHA `f8f893a` (lot 2)

| Workflow | Run | Résultat |
|---|---|---|
| CI | `42055269418` | **13/13 jobs `success`** |
| Qualification native | `42055269315` | `success` |
| Qualification réservés | `42055269397` | `success` |
| Qualification navigateurs Windows | `42055269806` | `success` |
| Endurance | `42055269333` | `success` |
| Perf comparaison | `42055269489` | `success` |
| **Qualification Windows** | `42055269322` | **échec — antérieur à la session, voir ci-dessous** |

Chiffres publiés par la CI elle-même (annotations du job `backend (3.12)`) :

* suite PostgreSQL : `pytest -m "postgres and not perf and not sauvegarde"` →
  **535 passed, 0 skipped** ;
* file durable Redis : `pytest -m "redis_queue"` → **140 passed, 0 skipped** ;
* couverture : **88,15 %** pour un plancher de 85 %.

### Frontend en CI

Le job `frontend` (CI `42055269418`) refait `tsc --noEmit`, le build et les
épreuves navigateur : `success`.

## État honnête : Qualification Windows est rouge, et c'est antérieur

**Ce n'est pas une régression de cette session.** Le workflow
`Qualification Windows` est en échec sur `main` au commit de base `af31922`
(run du 2026-11-03), donc **avant le premier commit du plan**. Il est resté
rouge à chaque commit de la session.

Ce que l'on sait précisément, sans pouvoir lire le journal depuis ce banc
(le téléchargement des journaux et des artefacts est bloqué par la politique
réseau de l'environnement) :

* l'étape fautive est la **n° 21, « Suite PostgreSQL + Redis sur Windows —
  verdict »** ; toutes les autres étapes du job passent, y compris
  l'installation de PostgreSQL 16 et de Redis 7 ;
* l'étape « Suite sans service sur Windows — verdict » **passe** : ses 369
  échecs sont tous nommés dans `ci/ecarts-windows.txt` (283 lignes actives),
  limite de plateforme assumée et documentée ;
* le verdict échoue donc sur des échecs **non nommés** dans ce fichier — ce qui
  est exactement le comportement voulu par `ci/verdict-windows.ps1` (« un échec
  qui ne correspond à aucune ligne fait échouer le job : c'est la garantie
  qu'un écart ajouté ici est un écart assumé, jamais un oubli »).

Hypothèse à vérifier sur le runner, et non ici : le PostgreSQL 16 installé par
`choco install postgresql16` ne fournit peut-être pas `postgresql-contrib`. La
mesure locale l'atteste — sur un banc sans `unaccent` ni `pg_trgm`, ce sont
précisément les tests de recherche accentuée, de trigrammes et de doublons
probables qui échouent, et **aucun d'eux n'est nommé** dans
`ci/ecarts-windows.txt`.

**Prochaine action recommandée** (hors périmètre de ce plan, à décider) : lire
le journal de l'étape 21 sur un poste qui y a accès, puis soit nommer les
écarts dans `ci/ecarts-windows.txt` **avec leur raison exacte et leur
contrepartie**, soit installer `postgresql-contrib` sur le banc Windows. En
l'absence de cette lecture, ajouter des lignes d'écart reviendrait à faire taire
une alarme sans savoir ce qu'elle dit — ce que le dépôt interdit explicitement.

## Réserve de confidentialité

Aucun document client ni extrait nominatif n'entre dans ce dépôt. Le lot 2 a
ouvert des documents réels de l'atelier : seules des **structures, des motifs
et des comptages** en ont été tirés, et **aucun exemplaire** n'a été conservé
(suppression immédiate après lecture, vérifiée avant chaque commit). Les
épreuves du lot 2 sont 100 % synthétiques : elles reprennent la structure
imprimée des formulaires, jamais une valeur de l'atelier.

## Déploiement et rollback

Le lot 2 ne touche **ni le schéma, ni une migration** : les deux nouveaux
champs (`verifie`, `preuve_verification`) vivent dans la colonne JSONB
`champs` de `accessory_extractions`.

1. Déployer backend + frontend du même commit.
2. Aucune reprise de données n'est nécessaire : les fiches déjà extraites
   gardent leurs valeurs ; la double vérification s'applique aux dépôts
   suivants.
3. **Rollback :** `git revert f8f893a` puis redéployer. Sans les pages
   transmises, la projection accessoire est exactement celle d'avant le lot —
   la réversibilité est donc totale et sans migration.

## Périmètre non prétendu

Ce document clôt le plan « comparaison et réutilisation ». Il ne prétend pas :

* corriger `Qualification Windows` (antérieur, diagnostiqué mais non réparé —
  voir ci-dessus) ;
* brancher l'OCR sur le dépôt des fiches : les documents numérisés restent
  déclarés « sans couche texte » et vont en revue manuelle ;
* traiter le chantier `git filter-repo`, volontairement différé ;
* étendre la **double lecture des cotes** (`verification.py`) aux fiches de
  **voiles** : elle est aujourd'hui câblée sur la famille accessoire. Le
  mécanisme est générique, son application aux voiles n'a pas été prouvée.
  Cela ne préjuge en rien du Lot 1.3, qui est d'une autre nature (concordance
  des références dossier / fichier / document) et qui **est livré** par
  `f232cf9` ;
* prétendre que la table de correspondance du Lot 1.1 est produite — elle ne
  l'est pas : seul l'instrument de mesure est livré, cf. la section qui porte
  son nom.
