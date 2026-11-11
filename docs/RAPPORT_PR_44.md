# Rapport détaillé — PR #44

**« Comparaison, réutilisation, et une cote relue deux fois (lots 0 → 8) »**

Ouverte le 2026-11-05, base `main`, **toujours ouverte** (état `UNSTABLE`).
Branche `arena/765ebdcf-seamtech-search`, base de fusion `af31922`, HEAD `c0b07f8`.

Périmètre : **53 commits, 67 fichiers, +11 845 / −32 lignes.**

Ce rapport dit ce qui est **prouvé** et ce qui **manque**, séparément. Une ligne
dans « ce qui marche » renvoie toujours à une commande reproductible ; une ligne
dans « ce qui manque » dit pourquoi et ce qu'il faudrait pour la lever.

---

## 0. Incident de début de session — à connaître

En ouvrant cette session, la branche locale était **revenue à `af31922`**, la
base de fusion : les 53 commits avaient disparu de l'historique local, et
l'intégralité du travail se présentait comme 29 fichiers modifiés et 44 fichiers
non suivis. Le `reflog` montrait un **clone neuf** (`clone:` puis `checkout:`).

Diagnostic avant toute action :

- la branche distante était intacte à `c0b07f8` ;
- la PR #44 pointait bien sur `c0b07f8` avec ses 53 commits ;
- les 38 fichiersdifférant entre l'arbre local et `c0b07f8` ont été comparés
  par empreinte : **38 sur 38 identiques à l'octet**, aucun orphelin.

Restauration sans perte : `git reset --hard c0b07f8`. **Rien n'a été perdu.**
Le signaler ici parce que, sans la vérification par empreinte, un
`reset --hard` aurait pu écraser du travail.

---

## 1. Ce que contient la PR, par chantier

| Chantier | Commits | Ce qui est livré |
|---|---|---|
| **Lots 0 → 8** comparaison et réutilisation | 13 | Lot 0 cartographie ; comparaison de groupe ; réutilisation ; recherche par référence ; filtres actifs ; normalisation des axes ; calibration des seuils ; accessoires relus deux fois ; Lot 8 compte rendu ; mode nuit |
| **Lot 1 « socle »** | 11 | 1.2 vraisemblance, 1.3 référence comparée à trois, 1.4 complétude par gabarit, 1.5 contrôles RG16 obligatoires, puis trois correctifs et l'intégration inter-lots |
| **Étape 0 — corpus réel** | 17 | outil d'accumulation Dropbox fichier par fichier ; 19 → 22 → 28 → 34 → 39 → 41 → **46 fiches** ; deux correctifs de confidentialité |
| **A / B / C / D** | 8 | A.1 arbitrage encodé, A.2 garde-fou, A.3 apport des instruments mesuré, B calibrage, C parcours réel, D rapport |
| **Mise en production et finitions** | 4 | corpus rendu retrouvable ; correctifs CI ; filtre `gamme` ; audit ECC |

Répartition des 67 fichiers : 23 `tests/`, 16 `seamtech_search/`, 6 composants
et pages, 6 `docs/`, 5 `scripts/`, 5 `frontend/lib/`, 3 `frontend/e2e/`,
2 `donnees/`, 1 `CHANGELOG.md`.

---

## 2. CE QUI MARCHE — prouvé

### 2.1 La CI est verte au HEAD, sur toute la largeur

Au HEAD exact `c0b07f8`, **les 13 jobs du workflow sont au vert**, sur deux runs
distincts, plus cinq contrôles additionnels :

```
backend (3.11)  backend (3.12)  backend (3.13)   e2e   frontend   docker
ocr   integration   recette-locale   recette-corpus-reel
sauvegarde   securite-dependances   hors-ligne-reel
— et en sus : soak, qualification, diagnostic-causes,
  scale-bench (10 000 fiches synthétiques), Essai natif Ubuntu 24.04
```

Vérifier : `gh api repos/Ilyes-Neguir/SEAMTECH-search/commits/c0b07f8/check-runs`

**Un seul contrôle est rouge : `windows`.** Il échoue **aussi sur `main`**
(`af31922` → `completed/failure`) : c'est un état préexistant, pas une
régression de cette PR. Les 369 échecs sont tous nommés dans
`ci/ecarts-windows.txt` (437 lignes, **fichier préexistant sur `main`**), et la
CI le dit elle-même : « limite de plateforme connue, pas une régression ».

### 2.2 L'E2E est vert au premier essai, sans flaky

Les annotations du job `e2e` au HEAD :

```
e2e-lot6   3 parcours Lot 6 verts AU PREMIER ESSAI
e2e-lot5   3 parcours verts au premier essai
e2e-lot4b  3 parcours verts au premier essai
e2e-lot3b  3 parcours verts au premier essai
e2e-lot1   7 parcours verts au premier essai
e2e-lot7   4 parcours verts au premier essai
e2e-auth-nominatif       34 tests verts au premier essai
e2e-scan-ocr-live         4 scénarios verts au premier essai en PostgreSQL
accessibilite            82 écrans scannés, 0 violation WCAG 2.1 A/AA
mesure-phase1     parcours complet de la fiche 7792-SO : 993 ms (critère < 120 s)
```

« Au premier essai » est la mention qui compte : le run précédent annonçait
« 2 passés, **1 flaky** ». Ce flaky n'a pas été masqué — il a mené à un défaut
applicatif réel (section 2.4).

### 2.3 La vérification manuelle passe intégralement

`python scripts/verification_manuelle.py --base http://127.0.0.1:8000 --jeton …`
→ **24 épreuves, 24 OK, 0 ÉCHEC**, contre un serveur réellement lancé :
socle, dépôt, plein texte, facettes, fiche, validation, conflit de révision.
Procédure reproductible : `docs/VERIFICATION_MANUELLE.md`.

### 2.4 Un défaut applicatif trouvé et corrigé, que la CI verte laissait passer

C'est le résultat le plus utile de la session. En cherchant la cause du job
`e2e` rouge, la cause racine s'est révélée être un défaut **visible par
l'utilisateur** : la file des extractions pouvait **revenir non filtrée** sous
ses yeux. Deux requêtes se croisaient, et la plus ancienne écrasait la plus
récente, annulant la recherche. `chargerDetail` avait une garde de génération,
`chargerFile` non. Corrigé en deux commits (`b290b3e`, `e74afcd`), le second
ajoutant l'annulation de la requête devenue inutile, sur recommandation de la
liste de contrôle ECC `react-reviewer`.

### 2.5 Le filtre `gamme` est corrigé et mesuré

Il ne retrouvait qu'une fiche sur sept. Mesuré après correction sur serveur
vivant : `gamme='Medium Régate'` → 2 fiches, `gamme='Medium'` → 2 fiches,
`gamme='Medium Croisière'` → non retenue. Le périmètre est **borné au seul axe
`gamme`** : une épreuve existante impose l'égalité stricte sur `type_voile`,
`client`, `bateau` et `categorie`.

### 2.6 A, B, C, D sont rendus

- **A.1** — arbitrage rendu, encodé dans `donnees/etape0/decisions.json`,
  appliqué en production par le garde-fou.
- **A.2** — garde-fou anti-réintroduction livré, et **non muet** : un défaut
  grave a été trouvé (la clé de comparaison ne repliait pas la ponctuation, un
  refus saisi sur `10.80` ne retenait pas `10 80`). Branché sur
  `cle_normalisation`.
- **A.3** — **clos avec mesure** (`2e6e84e`) : les mêmes PDF déposés deux fois
  dans deux bases neuves, la seule différence étant les instruments actifs ;
  l'écart AVANT/APRÈS **est** l'apport des instruments. Mesuré sur 15 fiches
  réelles.
- **B** — le compte exact est **0 fiche de vérité terrain**, et l'outil refuse
  de proposer le moindre seuil. C'est la bonne réponse : il manque des verdicts
  humains, pas des données.
- **C** — parcours réel dans l'application sur base réelle ; suite revenue
  honnête ; **2 739 épreuves vertes, 0 erreur** à ce stade.
- **D** — rapport rendu, CI verte au HEAD, verdict explicite.

### 2.7 Le banc Redis n'est plus un trou

Le rapport de verdict signalait « six épreuves ignorées, donc non prouvées ».
**Ce trou est fermé** : Redis est désormais un service réel en CI
(`redis:7-alpine` avec sonde de santé), avec une étape dédiée « file durable —
Redis réel + PostgreSQL réel » et un garde-fou qui exige
**« passed > 0 et skipped == 0 »**. Cette étape tourne dans le job `backend`,
vert sur les trois versions de Python.

### 2.8 L'audit de production : 86/100

`docs/AUDIT_PRODUCTION.md`, établi avec la compétence ECC `production-audit`.
Chaque constat est une commande reproductible. Points saillants : aucun secret
consigné ; limitation de débit éprouvée jusqu'au 429 ; `/health` rend les
extensions PostgreSQL manquantes **visibles** au lieu de les laisser silencieuses
; aucune surface de paiement ; 32 migrations suivies ; retour arrière documenté
lot par lot.

---

## 3. CE QUI MANQUE

### 3.1 Le corpus : 46 fiches sur ~338 — **292 manquantes**

Le manque principal, et il n'est pas logiciel. L'outil ingère, indexe et
restitue correctement ce qu'on lui donne ; il n'a simplement reçu que 14 % du
fonds. **Le relevé est suspendu sur décision métier** (mandat `bd`), pas oublié.
Conséquence : c'est un feu vert de mise en service **technique**, pas un feu
vert d'ouverture aux utilisateurs.

### 3.2 Le Lot 1.1 : la table de correspondance n'est pas écrite

Ce qui est livré est l'**instrument** qui la produirait
(`scripts/mesurer_correspondances.py`). Il n'a **jamais tourné sur les 338
fiches**, parce que ce dépôt ne contient pas ce corpus. La table n'est donc pas
écrite, ni la migration de reprise correspondante. Ces deux points **exigent le
corpus** : ils ne sont pas « reportés », ils sont bloqués par 3.1.

### 3.3 Le calibrage : zéro fiche de vérité terrain

Aucun seuil n'a été modifié, et c'est la bonne réponse — l'outil refuse de
calibrer faute de preuves plutôt que d'inventer des seuils. Pour lever ce point,
il faut que **des fiches passent devant un approbateur** : c'est une action
humaine, pas du code.

### 3.4 Windows : 369 échecs de plateforme

Préexistant sur `main`, tous nommés dans `ci/ecarts-windows.txt`. La plateforme
Windows n'est donc **pas** couverte au même niveau que Linux. Ce n'est pas une
régression de cette PR, mais c'est un trou réel si l'atelier travaille sous
Windows — et c'est ce qui met la PR en état `UNSTABLE`.

### 3.5 Deux prérequis d'exploitation à poser par l'opérateur

`unaccent` et `pg_trgm` doivent être installés sur le PostgreSQL de production
(`CREATE EXTENSION`). Sans eux l'application ne casse pas : elle dégrade, avec
la conséquence écrite dans le journal **et rendue visible par `/health`**. C'est
donc un prérequis connu, pas un piège — mais il est à la charge de l'opérateur.

### 3.6 La suite locale : 42 échecs environnementaux, sans rapport avec le produit

Sur ce bac à sable, 42 épreuves échouent parce que le PostgreSQL fourni par
`pgserver` est distribué **sans les modules contrib** (`unaccent`, `pg_trgm`).
Ils sont verts en CI, où les modules sont présents. À ne pas lire comme un
défaut du livrable.

### 3.7 La PR n'est pas fusionnée

Elle est ouverte depuis le 2026-11-05 et en état `UNSTABLE` (cause : 3.4).

---

## 4. Tableau récapitulatif

| Élément | État | Preuve / cause |
|---|---|---|
| CI, les 13 jobs du workflow | **VERT** | `check-runs` au HEAD `c0b07f8` |
| Contrôle `windows` | **ROUGE** | 369 échecs, **préexistants sur `main`** |
| E2E, tous lots | **VERT au 1ᵉʳ essai** | annotations du job `e2e` |
| Accessibilité | **0 violation** | 82 écrans, WCAG 2.1 A/AA |
| Vérification manuelle | **24 / 24** | `scripts/verification_manuelle.py` |
| Suite automatisée | **VERTE en CI** | 2 739 épreuves, 0 erreur (stade C) |
| Filtre `gamme` | **CORRIGÉ** | `Medium` → 2 fiches, mesuré |
| File des extractions | **CORRIGÉ** | défaut applicatif, `b290b3e` + `e74afcd` |
| A.1 / A.2 / A.3 | **RENDUS** | dont A.3 mesuré sur 15 fiches |
| B calibrage | **COMPTE EXACT 0** | refuse d'inventer des seuils |
| C / D | **RENDUS** | parcours réel, CI verte |
| Lot 1.2 / 1.3 / 1.4 / 1.5 | **LIVRÉS** | — |
| Banc Redis | **TROU FERMÉ** | service réel + garde-fou « skipped == 0 » |
| Audit de production | **86 / 100** | `docs/AUDIT_PRODUCTION.md` |
| Corpus | **46 / ~338** | suspendu par décision métier |
| Lot 1.1 (la table) | **NON ÉCRITE** | exige le corpus |
| Calibrage des seuils | **BLOQUÉ** | exige des verdicts humains |
| `unaccent` / `pg_trgm` | **À INSTALLER** | prérequis d'exploitation |
| Fusion de la PR | **EN ATTENTE** | état `UNSTABLE` (Windows) |

---

## 5. En une phrase

Le produit se comporte correctement sur ce qu'on lui a donné à traiter, sa
suite d'épreuves est honnête et sa CI est verte sur toute la largeur — et l'écart
le plus important n'est pas dans le code : **il faut 292 fiches de plus et des
verdicts humains sur les fiches déjà présentes.**
