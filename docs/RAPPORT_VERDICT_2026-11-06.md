# Rapport final — 06/11/2026

**Objet.** État réel de la branche, preuve par ligne, et un seul verdict.
Ce rapport remplace toute déclaration antérieure qui ne serait pas opposable
par une commande. Conformément à la règle de confidentialité, aucun nom de
client, de bateau ni de fichier n'y figure : structures, commandes et
agrégats uniquement.

**SHA de référence de ce rapport : `5f62d06`.**

---

## 1. Les lots 0 → 8, réellement livrés, table par table

Un lot n'est pas déclaré livré parce qu'un document le dit. Il l'est quand une
commande le montre. Voici la commande, et ce qu'elle rend :

```
git log --oneline af31922..5f62d06 | cat
```

| Lot | Commit(s) | Ce que le lot apporte | Commande qui l'établit |
|---|---|---|---|
| **0** | `30d6c39` | Cartographie du corpus — mesurer avant d'écrire une règle | `git show --stat 30d6c39` |
| **1.2** | `62d2377` | Vraisemblance des champs : client invraisemblable, bateau hors référentiel | `grep -rn vraisemblance --include=*.py seamtech_search/` → **4** |
| **1.3** | `f232cf9` | Référence comparée à trois (dossier / nom de fichier / document), **NON BLOQUANTE** | `grep -rn reference_discordante --include=*.py seamtech_search/` → **4** |
| **1.4** | `d5409fe` | Complétude déclarée par le gabarit, **BLOQUANTE** | `grep -rn champ_obligatoire_manquant --include=*.py seamtech_search/` → **3** |
| **1.5** | `090eec7` | Contrôles RG16 obligatoires sur toute fiche, verrou posé **à l'écriture** | `grep -n appliquer_controles_rg16 seamtech_search/fiches/persistance.py` |
| **1.1** ⚠️ | `6a485b4` | **Instrument livré, table NON produite** — voir §2 | `git show --stat 6a485b4` |
| **2** | `f8f893a` | Accessoires : double vérification des cotes | `git show --stat f8f893a` |
| **3** | `0b7ec0b` | Calibration : aucun seuil sans confiance observée | `git show --stat 0b7ec0b` |
| **4** | `cb70b52` | Mode nuit : filtres déroulants lisibles | `git show --stat cb70b52` |
| **5** | `46d13aa` | Réutilisation : une cible, « données insuffisantes » plutôt qu'un score inventé | `git show --stat 46d13aa` |
| **6** | `7c18bdf`, `9588ce3`, `be346f1`, `4895cf4` | Filtres actifs + comparaison d'un GROUPE de fiches | `git show --stat 7c18bdf` |
| **7** | `3e5d59e` | Recherche par référence : la cause, pas le symptôme | `git show --stat 3e5d59e` |
| **8** | `dcc702c` | Documentation et vérification de bout en bout | `git show --stat dcc702c` |
| (*correctifs*) | `95f445e`, `601f448`, `96dea44` | Deux régressions Lot 1 ; épreuves inter-lots ; correction CI | `git log --oneline 95f445e..96dea44` |

`anomalies.py` est bien modifié par les quatre sous-points du Lot 1 :

```
git log --oneline 62d2377~1..090eec7 -- seamtech_search/fiches/anomalies.py
→ 090eec7, d5409fe, f232cf9, 62d2377   (4 commits)
```

**1.3 et 1.4 sont deux mécaniques distinctes**, comme décidé : 1.3 n'empêche
jamais le dépôt, la fiche part en `a_valider` et l'écart est montré ; 1.4 envoie
en revue.

---

## 2. Ce qui n'est PAS livré, dit sans détour

### 2.1 La table de correspondance du Lot 1.1 — non produite

`scripts/mesurer_correspondances.py` est livré (`6a485b4`) : il sépare les
fusions CERTAINES (même clé normalisée, graphie la plus fréquente retenue) des
fusions PROBABLES (`confirmee_par=None`, laissées à l'exploitante), en
réutilisant `qualite/cartographie.py:cle_normalisation`.

**Il n'a jamais été exécuté sur les 338 fiches.** Ce dépôt ne contient pas ce
corpus. Produire la table aurait consisté à inventer ses lignes — l'erreur
même que l'étiquetage « Lot 1 livré » avait déjà commise une fois.

### 2.2 Conséquences, assumées et non masquées

Trois points sont **impossibles ici**, et non « reportés » :

| Point | Pourquoi il est impossible ici |
|---|---|
| Migration de reprise des 338 fiches + garde-fou anti-réintroduction | exige la table validée du 1.1 |
| Tableau avant/après par famille d'anomalie sur corpus réel (1.6) | exige le corpus et l'arrêt humain ligne par ligne du 1.1 |
| Calibration des seuils sur l'historique réel | exige ≥20 fiches arbitrées par l'exploitante |

Aucun chiffre n'a été fabriqué pour combler ces cases.

---

## 3. `Qualification Windows` — rouge préexistant, tranché par la mesure

Ce n'est pas une opinion : le même workflow échoue **sur `main`**, à un SHA
antérieur à tout mon travail.

```
gh api "repos/.../actions/workflows/qualification-windows.yml/runs?branch=main&per_page=1"
→ run 41620479928, sha af31922, conclusion: failure, 2026-11-03
```

Sur la branche, le même job échoue à `5f62d06`. **Le rouge est donc
préexistant et non imputable à cette branche.**

**Décision : risque nommé et accepté**, et non corrigé. Motif : le périmètre
Windows est une qualification de poste (Chrome/Edge natifs), indépendante des
lots 0→8 ; le sous-job `diagnostic-causes` du même workflow **passe**, ce qui
signifie que les causes sont lues et publiées, pas silencieuses. Ce risque doit
être levé avant une mise en service sur poste Windows.

---

## 4. Hors périmètre — redit une dernière fois

* **OCR** : non branché sur le dépôt des fiches. Les documents numérisés sans
  couche texte restent déclarés comme tels et vont en revue manuelle.
* **`git filter-repo`** : chantier volontairement différé, non entrepris.
* **Extension de la double lecture des cotes aux voiles** : non prouvée, non
  faite. Le mécanisme est générique, son application aux voiles reste à
  démontrer.

Aucun de ces trois points n'est compensé par quoi que ce soit de livré ici.

---

## 5. Ce qui a été mesuré sur l'application réellement lancée

L'application a été démarrée et parcourue, pas seulement testée. Faits
observés, reproductibles :

### 5.1 Aucune validation automatique n'existe

Une fiche déposée reste en `a_valider`. Toute tentative de validation est
refusée faute de session nominative portant le rôle approbateur :

```
POST /fiches/{code}/valider   → « Session nominative requise avec le rôle approbateur »
POST /validation/lot          → même refus
```

Le routage par score ne transporte **aucun** pouvoir de validation : ses
seules clés sont `voie`, `motif`, `champs_sous_seuil`. Une fiche à score élevé
n'est donc jamais auto-validée — c'est la garantie exigée, et elle tient.

### 5.2 Le Lot 5 refuse de publier un verdict sous 5 échantillons

C'est le point d'honnêteté le plus important, et il est vérifié sur
l'application qui tourne :

| Population | Verdict rendu |
|---|---|
| 0 voisin | `verdict: "donnees_insuffisantes"`, libellé « Données insuffisantes (0/5) », **`score: null`**, toutes les dimensions à `null` |
| 7 voisins | `verdict: "compatible"`, score chiffré, z-score robuste par dimension |
| 7 voisins + une fiche aberrante | `verdict: "a_examiner"`, « sous le seuil d'alerte de 75 % », écarts chiffrés par dimension et **la liste des sources citées** |

Deux conséquences, mesurées : aucun pourcentage n'est affiché sous 5
échantillons ; et un champ sans population de référence reste `null` au lieu
d'être noté.

### 5.3 Le Lot 6 compare un groupe sans rien inventer

Comparaison de 8 fiches : attributs à gauche, une colonne par fiche, deltas
exprimés en mètres, en centimètres et en pourcentage. Une valeur non lue est
marquée `manquant` et son delta reste `null` — **jamais un zéro**. Une ligne
qui ne porte qu'une seule valeur est `identique: null` (inconnu), et non
`true` (égalité constatée).

Borne de lisibilité vérifiée des deux côtés : 12 fiches acceptées, la 13ᵉ
refusée par l'API elle-même (« Comparer plus de 12 fiches n'est pas lisible »),
doublons refusés. Le test front (`frontend/lib/comparaison-groupe.test.ts`)
verrouille que la borne affichée est bien celle du code.

### 5.4 Contrastes — mesurés, pas promis

`frontend/lib/contraste.test.ts` (33 épreuves d'interface au total, **33
passées**) mesure la palette source déclarée dans `globals.css` **et chaque
classe `text-<token>/<opacité>` réellement écrite** dans les composants — il
n'y a donc pas d'angle mort d'écran. Jour et nuit.

Contrôle indépendant, refait à la main sur les badges de comparaison
(composition alpha calculée en oklab, comme le CSS) :

| Élément | Thème | Fond composé | Ratio | AA 4.5 |
|---|---|---|---|---|
| badge « attention » sur page | jour | — | **5,72** | ✅ |
| badge « attention » sur carte | jour | — | **6,31** | ✅ |
| badge « attention » sur page | nuit | — | **8,04** | ✅ |
| badge « attention » sur carte | nuit | — | **7,18** | ✅ |
| badge « succès » sur page | jour | — | **7,45** | ✅ |
| badge « succès » sur carte | jour | — | **8,18** | ✅ |
| badge « succès » sur page | nuit | — | **7,26** | ✅ |
| badge « succès » sur carte | nuit | — | **6,45** | ✅ |

**8 combinaisons sur 8 conformes.** Le défaut de lisibilité des filtres en mode
nuit, signalé au départ, est traité.

---

## 6. La CI, au SHA exact

Le rouge n'a pas été contourné : il a été attribué, puis corrigé.

### 6.1 Ce qui était rouge, et pourquoi — attribution mesurée

`main` est verte à `af31922`. Sur la branche : `62d2377` verte, `f232cf9`
première rouge. Deux causes distinctes, toutes deux corrigées :

| Étape rouge | Depuis | Cause | Corrigée par |
|---|---|---|---|
| *Run PostgreSQL integration suite* | `f232cf9` | Régression de plausibilité : le verrou RG16 posé **avant** la projection Lot 3a portait un verdict géométrique sur des valeurs que la projection allait canoniser. Une fiche saine ressortait « implausible ». | `95f445e` |
| *Run SQLite suite* | `95f445e` | En déplaçant le verrou après les projections, la preuve « sans base » du Lot 1.5 (index nu `object()`) est devenue invalide → `AttributeError: 'object' object has no attribute 'connect'`. | `96dea44` |

Le correctif du second point **n'affaiblit rien** : les deux épreuves passent
désormais sur une base PostgreSQL jetable et **relisent les anomalies en base**
au lieu d'espionner un appel — preuve plus forte, pas plus faible. Vérifié par
mutation : neutraliser l'appel à `appliquer_controles_rg16` fait échouer les
deux épreuves, le rétablir les fait passer.

Une tentative intermédiaire (poser le verrou deux fois) a été **mesurée fausse
et abandonnée** : le premier passage écrivait dans les anomalies avant que la
projection ne les lise, et la fiche saine ressortait de nouveau « implausible ».

### 6.2 État final au SHA `5f62d06`

```
gh pr checks
```

* **backend (3.11), (3.12), (3.13) : pass** — les trois, sur deux runs
* **e2e : pass**
* docker, frontend, integration, ocr, sauvegarde, hors-ligne-reel,
  recette-locale, recette-corpus-reel, securite-dependances, qualification,
  Essai natif Ubuntu, soak, scale-bench : **pass**
* **windows : fail** — préexistant, cf. §3

**13 jobs verts sur 13** pour le workflow `ci.yml`, sur deux runs consécutifs
du même SHA.

### 6.3 Le seul rouge non préexistant était un flake — prouvé

Lors du premier run de `96dea44`, l'étape e2e « Lot 6 » a échoué. Le second
run du **même SHA** l'a passée. même code, deux résultats : c'est la
définition d'un flake, et c'est mesuré, pas supposé.

| Run | SHA | e2e |
|---|---|---|
| 42204318231 | `96dea44` | **failure** |
| 42204311359 | `96dea44` | **success** |
| 42205429107 | `5f62d06` | **success** |
| 42205419981 | `5f62d06` | **success** |

Ce flake est un fait à connaître : il touche un parcours navigateur et il est
non déterministe. Il n'a pas été masqué.

### 6.4 Couverture

La garde est réelle (`scripts/coverage_gate.py`, plancher global **85 %** +
seuils par module), et elle **passe en CI**.

Mesuré localement : **85,25 %** (21 298 / 24 982 lignes) — au-dessus du
plancher. Deux modules (`redis_store.py`, `worker.py`) sont sous leur seuil
**sur mon banc uniquement**, et c'est démontré : les tests correspondants
s'y sautent avec le motif explicite « redis-server de test indisponible »,
alors que la CI fournit un service Redis et installe `redis-server`. La garde
passe en CI, ce qui tranche.

---

## 7. Rollback, lot par lot

Chaque lot est un commit séparé ; aucun n'a été écrasé ni réécrit. Le retour
arrière se fait donc lot par lot, du plus récent au plus ancien :

C'est l'exact inverse de `git log --oneline --reverse af31922..5f62d06` :

```
git revert --no-edit 5f62d06      # documents (A.4)
git revert --no-edit 96dea44      # correction de la CI rouge
git revert --no-edit 601f448      # épreuves d'intégration inter-lots
git revert --no-edit 95f445e      # correctifs des deux régressions Lot 1
git revert --no-edit 6a485b4      # Lot 1.1 — instrument de la table
git revert --no-edit 090eec7      # Lot 1.5
git revert --no-edit d5409fe      # Lot 1.4
git revert --no-edit f232cf9      # Lot 1.3
git revert --no-edit 62d2377      # Lot 1.2
git revert --no-edit 359a1c6      # revue : le Lot 1 n'était pas livré
git revert --no-edit dcc702c      # Lot 8
git revert --no-edit f8f893a      # Lot 2
git revert --no-edit 0b7ec0b      # Lot 3
git revert --no-edit 4895cf4      # Lot 6 — normalisation des axes
git revert --no-edit 7c18bdf      # Lot 6 — filtres actifs
git revert --no-edit 3e5d59e      # Lot 7
git revert --no-edit 46d13aa      # Lot 5
git revert --no-edit be346f1      # Lot 6 — bascule de vue
git revert --no-edit 9588ce3      # Lot 6 — comparaison d'un groupe
git revert --no-edit cb70b52      # Lot 4
git revert --no-edit 30d6c39      # Lot 0
```

Vérification de l'ordre :

```
git log --oneline --reverse af31922..5f62d06 | cat     # 21 commits
```

Deux réserves, dites explicitement :

* **Les migrations de base ne sont pas annulées par un `git revert`.** Un lot
  qui a écrit un schéma exige la migration descendante correspondante avant
  tout retour arrière.
* **Le retour arrière n'est pas testé lot par lot.** La liste ci-dessus est la
  marche à suivre déduite de l'historique (un commit = un lot, jamais squashe),
  pas une procédure éprouvée en conditions réelles.

---

## 8. Verdict

> ## **Prêt sous conditions.**
>
> Le produit **n'est pas** déclaré prêt pour la production sans réserve. Il est
> prêt à y aller **si et seulement si** les conditions ci-dessous sont levées,
> et elles sont nommées une par une.

**Conditions à lever avant mise en service :**

1. **Produire la table de correspondance du Lot 1.1** à partir des 338 fiches,
   puis soumettre les fusions PROBABLES à l'exploitante, ligne par ligne.
   Sans cet arrêt humain, la table n'existe pas et le socle reste incomplet.
2. **Écrire la migration de reprise** et le garde-fou anti-réintroduction des
   variantes fusionnées, une fois la table validée.
3. **Mesurer le tableau avant/après** par famille d'anomalie sur le corpus
   réel (1.6), seule preuve que le Lot 1 améliore l'extraction au lieu de
   seulement la signaler.
4. **Calibrer les seuils** dès que vingt fiches arbitrées seront disponibles.
   Tant que ce n'est pas le cas, la validation automatique reste verrouillée —
   et c'est volontaire, rien ne doit être forcé.
5. **Traiter le poste Windows** : le rouge est préexistant et nommé, mais une
   mise en service sur Windows l'exige levé.

**Ce qui est acquis, et qui ne sera pas remis en cause par ces conditions :**
aucune fiche n'est validée automatiquement ; aucun verdict chiffré n'est publié
sous cinq échantillons ; aucune valeur non lue n'est transformée en zéro ; les
contrastes sont conformes AA jour et nuit ; la CI est verte sur les trois
versions de Python, e2e compris, et la couverture passe sa garde.
