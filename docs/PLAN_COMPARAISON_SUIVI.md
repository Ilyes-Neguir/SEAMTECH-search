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
| **1** ⚠️ | — | **NON LIVRÉ.** Le Lot 1 « socle » du plan — re-extraction du corpus, contrôles géométriques obligatoires, anomalies `vraisemblance` (1.2) et `reference_discordante` (1.3) — n'existe pas dans cette branche. Voir « Le Lot 1 manquant » ci-dessous. |
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

## Le Lot 1 manquant — dit noir sur blanc

Le **Lot 1 « socle » n'a pas été livré.** Ce n'est pas une nuance de
présentation : c'est l'absence d'un lot entier, et la première version de ce
document l'a masquée en étiquetant « Lot 1 » l'écran de comparaison groupe, qui
appartient au Lot 6. Le renommage est corrigé ci-dessus ; l'absence, elle,
demeure.

Ce que le Lot 1 devait apporter, et qui est **absent** :

| Attendu | État mesuré dans la branche |
|---|---|
| Anomalies du corpus (`anomalies.py`) | **jamais modifié** — `git diff af31922..HEAD` vide |
| Gabarits (`gabarits.py`) | **jamais modifié** |
| Re-extraction du corpus | **aucune** |
| Contrôles géométriques rendus obligatoires | **aucun** |
| Anomalie `vraisemblance` (1.2) — bateau absent du référentiel signalé plutôt qu'enregistré | **0 occurrence** dans tout le dépôt |
| Anomalie `reference_discordante` (1.3) — référence dossier / fichier / document qui se contredisent | **0 occurrence** dans tout le dépôt |
| Table de correspondance validée | **aucune** |

Les seules lignes touchées dans `depot.py` et `extraction.py` (18 au total)
sont le passage des pages analysées vers la double vérification du Lot 2. Elles
ne constituent en rien une re-extraction ni un contrôle.

### Pourquoi c'est grave, et pas seulement une case non cochée

Le Lot 0 avait identifié **deux pièges réels** : un PDF appartenant à une autre
commande rangé dans un dossier, et un nom de fichier qui contredit la référence
du dossier. Le Lot 1.3 devait les attraper. Il n'existe pas — donc ces deux
pièges restent **non protégés pour toutes les voiles**.

La conséquence est directe : la comparaison multi-fiches et le mode cible
(Lots 5 et 6, validés par ailleurs) s'exécutent sur des données dont la
fiabilité de référence n'a pas été renforcée. Le principe n°2 du plan —
*« l'extraction avant la comparaison, sinon on reconstruit sur du faux »* —
n'est donc **pas respecté**. Le socle a été déclaré fait alors qu'il manque, et
c'est exactement le type d'erreur silencieuse que l'application est censée
interdire.

Une précision, pour ne pas entretenir la confusion que le document a créée : le
module `verification.py` du Lot 2 est une **double lecture des cotes
d'accessoires** (séquentielle puis géométrique). Ce n'est pas la vérification de
référence dossier/fichier/document du Lot 1.3, qui devait couvrir **tout le
corpus**. Écrire que son extension aux voiles « n'a été ni demandée ni prouvée »
était vrai du mécanisme de relecture des cotes, mais faux et trompeur dans le
contexte du Lot 1 : la vérification de référence, elle, était bel et bien
demandée.

**Ce lot reste à faire.** Il n'est pas remplacé, ni compensé, par quoi que ce
soit de livré ici.

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
  des références dossier / fichier / document) et qui **reste entièrement à
  faire** ;
* prétendre que le Lot 1 est livré — il ne l'est pas, cf. la section qui porte
  son nom.
