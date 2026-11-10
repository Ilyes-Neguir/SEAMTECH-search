# Rapport final — 10/11/2026

Branche `arena/765ebdcf-seamtech-search`, du commit `189723f` au commit `202d349`.

Ce rapport répond au mandat : **A, B, C et D doivent apparaître avec preuve,
pas avec « impossible ici ».** Chaque chiffre qui suit est assorti de la
commande qui le reproduit.

---

## 0. Résumé d'une phrase

L'Étape 0 est **prouvée sur 28 fiches réelles** et non sur les ~338 attendues ;
l'arbitrage A.1 est **rendu, encodé et appliqué** ; le calibrage B est **empêché
par un compte exact de zéro** ; la suite C est **de nouveau verte au complet,
2739 épreuves, zéro erreur** ; et la CI, **rouge depuis huit commits pour deux
causes distinctes que j'ai trouvées et corrigées**, est en cours de validation.

Verdict en section 8.

---

## 1. Étape 0 — la base, prouvée

La source de vérité est Dropbox. Le corpus a été déposé dans une base
PostgreSQL locale et le compte est prouvé, pas estimé.

```
SELECT count(*) FROM fiche  ->  28
```

| Répartition | Effectif |
|---|---|
| `VOILES 2024` | 1 |
| `VOILES 2025` | 21 |
| `VOILES 2026` | 6 |
| source `archive` | 7 |
| source `dropbox` | 21 |
| fiches avec `gamme` | 9 |
| fiches avec `type_voile` | 7 |
| fiches avec `matiere` | 13 |

Reproduire : `python /tmp/compter_verite_terrain.py` (ou déposer l'accumulateur
`donnees/etape0/accumulateur.json` puis compter).

**L'écart avec les 338 fiches est assumé et nommé.** La cause n'est pas un
refus d'y aller : chaque fiche coûte un appel au connecteur, et il faut
connaître son chemin à l'avance, or la recherche Dropbox tronque au-delà
d'environ 40 entrées. La méthode qui fonctionne est documentée dans
`docs/ETAPE0_ACCES_CORPUS.md` : partitionner par racine puis par famille de
nom jusqu'à obtenir `has_more: false`.

### 1.1 Deux incidents à ne pas effacer de l'histoire

**Une fuite de noms de bateaux, trois fois.** Des marques sont arrivées dans le
dépôt par trois chemins différents, et chacun a été colmaté :

1. la valeur de `gamme` elle-même, parce que l'extraction **déborde** et avale
   le nom du bateau voisin ;
2. le **motif** rédigé à la main pour justifier un arbitrage — l'assainisseur
   ne couvre pas le texte libre ;
3. une forme instable, parce qu'un marqueur par mot rendait la valeur
   dépendante du nombre de mots avalés.

Le contrôle est passé d'un script ad hoc — qui a échoué **sans empêcher le
commit** — à une **épreuve** qui relit les fichiers versionnés et bloque la CI.
Non-mutisme prouvé : réinjecter une marque dans `decisions.json` fait échouer
l'épreuve.

**Quatre réinitialisations d'environnement.** À chaque fois, diagnostic avant
action : `git ls-remote` pour vérifier que les commits étaient sur le distant,
sauvegarde de l'arbre, puis `reset --hard`. Rien n'a été perdu.

---

## 2. Partie A — les correspondances, arbitrées

### A.1 — la table, mesurée sur 28 fiches réelles

| axe | fiches | certaines | probables | écartées (refus) |
|---|---|---|---|---|
| `gamme` | 9 | 0 | 0 | **1** |
| `matiere` | 13 | **1** | 0 | 0 |
| `type_voile` | 7 | 0 | 0 | 0 |
| `categorie` | 28 | 0 | 0 | 0 |

Deux fusions se sont présentées. Les deux ont été tranchées par l'exploitante,
et les deux décisions sont **encodées de façon opposable** dans
`donnees/etape0/decisions.json` :

| axe | valeurs | décision | graphie retenue |
|---|---|---|---|
| `gamme` | `enrouleur` / `enrouleur <nom propre>` | **ne pas fusionner** | — |
| `gamme` | `enrouleur` / `enrouleur surface` | **fusionner** | `enrouleur surface` |

Une fusion autorisée qui ne dit pas quelle écriture gagne n'est pas applicable ;
c'est pourquoi « fusionner » emporte une graphie imposée, vérifiée contre les
deux valeurs et **ramenée à la forme réellement observée** sur les fiches — on
accepte « Enrouleur SURFACE », on consigne `enrouleur surface`, plutôt que
d'inventer une casse qui n'existe nulle part.

### A.2 — le garde-fou anti-réintroduction

Livré au commit `189723f`. Il s'est vérifié **en production**, pas seulement
dans les épreuves : la fusion refusée apparaît dans la colonne ÉCARTÉE de la
mesure, et non parmi les fusions applicables.

Un défaut grave a été trouvé et corrigé au passage : le garde-fou renvoyait
`False` alors que le refus était consigné, parce que sa clé de comparaison ne
repliait pas la ponctuation. Un refus saisi sur `10.80` ne retenait pas `10 80`.
Il est désormais branché sur **`cle_normalisation`, la clé de l'instrument 1.1**
— un garde-fou doit employer exactement la normalisation de ce qu'il surveille,
sinon il est muet.

### A.3 — **non exécuté**

La re-extraction avec 1.1→1.5 actifs et le tableau avant/après par famille
d'anomalie n'ont **pas** été faits. Ce n'est pas un oubli : après l'arbitrage
A.1, l'exploitante a demandé d'enchaîner sur B, C et D. A.3 reste donc ouvert,
et il est le prochain point du plan.

---

## 3. Partie B — le calibrage, et le compte exact

**Le compte qu'il fallait donner sans détour : 0 fiche de vérité terrain.**

```
fiches au total .......................... 28
fiches avec confiance renseignée ......... 0
champs extraits au total ................. 0
champs corrigés par un humain ............ 0
FICHES ARBITRÉES (statut = valide) ....... 0   <- l'assiette du calibrage
répartition par statut : importe 28
```

Il en faut 20. Il y en a 0, et **la cause n'est pas un manque de données mais un
manque de verdict** : le corpus est déposé, mais jamais passé devant un
approbateur. Calibrer, c'est comparer la confiance annoncée à ce qu'un humain a
corrigé ; aucun humain ne s'est prononcé.

`scripts/calibrer_seuils.py` se comporte correctement devant ce vide : il refuse
de proposer le moindre seuil pour les six familles, ne touche à aucun fichier,
et rend le code 1.

**Ce que B apporte quand même.** Le calcul était éprouvé ; les deux requêtes
SQL qui décident de l'assiette ne l'étaient pas. Cinq épreuves les couvrent
maintenant sur PostgreSQL réel. Deux régressions de ce projet n'ont été visibles
QUE sur PostgreSQL réel, jamais sur les doublures — c'est précisément cette
lecture qu'il fallait éprouver.

Une épreuve **muette** a été trouvée et réparée : le comptage et les
observations ont chacun leur requête, et l'épreuve n'en exerçait qu'une.

---

## 4. Partie C — parcours réel et non-régression

### 4.1 Navigation réelle dans l'application

Six épreuves parlent à l'ASGI comme le ferait un navigateur, avec derrière un
PostgreSQL 16 et les 28 fiches déposées dedans.

La preuve de connexion n'est pas molle : on n'accepte pas un « ok ». `/health`
doit rapporter **exactement** la version que le serveur déclare, lue par une
connexion indépendante dans l'épreuve elle-même. Une première assertion plus
faible s'est révélée muette — une version fabriquée passait. Corrigé.

### 4.2 Un constat mesuré, verrouillé

La navigation a fait apparaître ceci : les 28 fiches sont dans `fiche`, la vue
`v_fiche_recherche` les compte, et pourtant `/recherche` n'en rend aucune.
Causes mesurées :

- `fiche.search_vector` est NULL sur les 28 fiches ;
- `fiche.gamme`, `type_voile` et `matiere` sont NULL, alors que l'accumulateur
  porte 9 gammes, 7 types et 13 matières.

La raison n'est pas un défaut, c'est le **pipeline** : l'Étape 0 dépose des
extractions brutes dans `sail_extractions`, et la fiche n'est enrichie qu'à la
résolution. **Déposer ne rend donc pas retrouvable — c'est la résolution qui le
fait.** Ce n'était écrit nulle part. C'est maintenant énoncé par un test qui
**échouera** le jour où la résolution sera branchée, et ce sera une bonne
nouvelle.

### 4.3 La suite ne ment plus

La non-régression tournait à « 2739 passed, **48 errors** ». Ces erreurs
n'étaient pas des régressions mais des bancs qui **échouent sur un prérequis
absent au lieu d'être ignorés** : quatre fichiers fabriquaient leur base
PostgreSQL sans la garde, un cinquième faisait de même pour Redis.

Après correction :

```
2739 passed, 812 skipped, 3 xfailed, 0 failed, 0 error
```

Chaque correction est vérifiée dans les deux sens : ignoré sans le service,
et vert avec (59 épreuves rejouées sur PostgreSQL réel).

---

## 5. La CI — rouge depuis huit commits, deux causes, les deux corrigées

C'est le point le plus désagréable de ce rapport, et il est à ma charge.

Le dernier vert connu est `5f62d06`. Depuis `2f09dfb`, tous les commits
échouaient sur le job `backend`. **Je ne l'ai pas vu avant d'aller chercher
l'état de la CI au SHA exact** — c'est-à-dire au moment où je rédigeais ce
rapport, et pas avant. Je poussais des commits sans lancer `ruff`, alors que
c'est la première étape du job.

**Cause 1** — `Ruff lint check` (étape 9) : 8 erreurs, toutes de mon fait
(6 blocs d'imports non triés, 1 import devenu inutile), dans les cinq fichiers
où j'avais inséré `import os` ou `import re` au fil de l'eau.

**Cause 2, masquée par la première** — l'étape `coverage` (étape 22) n'avait
donc plus tourné depuis `2f09dfb`. Mon épreuve de parcours appelait
`AppConfig.load()`, qui lit `config/config.json` — fichier que `.gitignore`
exclut du dépôt, et le job backend ne définit pas `SEAMTECH_ROOT_PATHS` non
plus. `FileNotFoundError` dans une fixture **d'épreuve** : c'est toute la session
qui partait en erreur. L'épreuve construit maintenant sa configuration, comme
le font les autres tests du projet.

Une erreur en cachait une autre ; corriger la première a révélé la seconde.
Les deux sont vérifiées dans la condition exacte de la CI, `config.json` retiré
du disque pour la seconde.

**Ce que je ne peux pas prouver ici, et que je nomme :** `redis-server` n'est
pas installé sur ce banc et je ne peux pas l'installer. Les 6 épreuves de
`test_securite_banc_redis` sont ignorées, donc **non prouvées**. Elles le seront
sur un banc qui porte Redis.

---

## 6. Ce qui a été cassé volontairement, pour vérifier que les épreuves servent

Une épreuve qui ne peut pas échouer ne prouve rien. Chaque comportement ajouté
a été cassé à la main pour vérifier que son épreuve tombait.

| Cassure | Épreuve qui doit échouer | Résultat |
|---|---|---|
| garde-fou neutralisé | refus de fusion | échoue |
| symétrie retirée | refus symétrique | échoue |
| clé approximative | résistance à la ponctuation | échoue |
| assainisseur neutralisé | masquage du nom | échoue |
| assainisseur non branché | consignation assainie | échoue |
| idempotence retirée | assainissement stable | échoue |
| graphie imposée non appliquée | application de l'arbitrage | échoue |
| graphie non ramenée à l'observé | normalisation | échoue |
| version de base fabriquée | `/health` prouve la base | échoue |
| garde nominative retirée | accès aux extractions | échoue |
| marque réinjectée | confidentialité | échoue |
| requêtes SQL assouplies | vérité terrain (×3) | échouent |

Deux épreuves se sont révélées **muettes** et ont été réparées : l'assertion de
version de `/health`, et l'épreuve de vérité terrain qui n'exerçait qu'une des
deux requêtes.

---

## 7. Rollback

Chaque sous-point est un commit séparé ; le retour arrière se fait par lot.

| Objet | Commit |
|---|---|
| A.2 — garde-fou anti-réintroduction | `189723f` |
| Corpus, 22 → 28 fiches | `941fb3a` |
| Confidentialité — assainisseur | `fd948fd` |
| Confidentialité — forme stable | `ca2bc63` |
| A.1 — arbitrage « fusionner » | `8e4e2c4` |
| B — compte exact, requêtes SQL | `1910517` |
| C — parcours réel, suite propre | `c934fd4` |
| CI — erreurs ruff | `4672945` |
| CI — configuration construite | `202d349` |

---

## 8. Verdict

**« Partiellement prêt, avec deux trous nommés — et plus aucune réserve sur la CI. »**

Ce qui est **prouvé** :

- l'Étape 0 tient sur une base réelle, 28 fiches, compte vérifiable ;
- l'arbitrage A.1 est rendu, encodé, et **appliqué en production** par le
  garde-fou ;
- A.2 est livré et son garde-fou n'est pas muet ;

- B donne un compte exact — zéro — avec sa cause, plutôt qu'une estimation ;
- C est vert au complet : **2739 épreuves, 0 échec, 0 erreur**, avec six
  épreuves qui naviguent réellement dans l'application sur base réelle ;
- douze cassures volontaires ont vérifié que les épreuves servent, et deux
  épreuves muettes ont été trouvées et réparées.

Ce qui **n'est pas livré**, dit sans détour :

1. **Les ~338 fiches.** Il en manque 310. La cause est nommée (coût par appel,
   troncature de la recherche), la méthode est documentée, et l'exploitante a
   choisi de poursuivre le relevé dossier par dossier sur plusieurs sessions.
2. **A.3.** La re-extraction avec le tableau avant/après par famille d'anomalie
   n'a pas été faite. Reportée par décision explicite, c'est le prochain point.
3. **Le calibrage.** Zéro fiche de vérité terrain : il faut que des fiches
   passent devant un approbateur. Aucun seuil n'a été modifié, et c'est la
   bonne réponse.
4. **Le banc Redis.** Six épreuves ignorées, donc non prouvées.

La **réserve sur la CI est levée**. Le cycle s'est terminé au HEAD `202d349`
sur un vert complet — les treize jobs, dont les trois versions de Python et
l'étape `coverage` qui n'avait plus tourné depuis `2f09dfb` :

```
202d349  completed/success   <- HEAD, vert
4672945  completed/failure
c934fd4  completed/failure
```

C'est le premier vert depuis `5f62d06`, soit huit commits. Le vérifier :

```
gh api repos/Ilyes-Neguir/SEAMTECH-search/actions/workflows/ci.yml/runs \
  ?branch=arena/765ebdcf-seamtech-search --jq '.workflow_runs[] | "\(.head_sha[0:7]) \(.conclusion)"'
```

**En une phrase pour l'atelier :** le produit se comporte correctement sur ce
qu'on lui a donné à traiter, il refuse de calibrer faute de preuves plutôt que
d'inventer des seuils, et sa suite d'épreuves est redevenue honnête. Ce qui
manque n'est pas du code : ce sont des fiches, et des verdicts humains sur ces
fiches.
