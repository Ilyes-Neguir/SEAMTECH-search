# Deux décisions en attente — à rendre par l'atelier, pas par l'agent

**Date** : 13/11/2026 — Lot 22, priorité 4.
**Règle appliquée** : remonter, ne pas trancher. **Aucune** fusion de graphie
n'a été faite. **Aucune** année n'a été attribuée. Ce document décrit, il ne
décide pas.

---

## Pourquoi les noms ne figurent pas ici

Les trois dossiers concernés portent des noms de bateaux. Un nom de client ou
de bateau n'entre ni dans un dépôt, ni dans un rapport, ni dans une
conversation — c'est une règle posée une fois pour toutes et elle ne se
négocie pas au moment où elle devient commode d'y déroger.

Chaque dossier est donc désigné par un **identifiant stable** : les dix
premiers caractères du SHA-256 de son nom en minuscules. Il ne révèle rien et
il permet le rapprochement. Pour retrouver lequel est lequel, chez vous :

```python
import hashlib
for nom in ("<nom du dossier>",):
    print(hashlib.sha256(nom.lower().encode()).hexdigest()[:10])
```

ou, depuis Dropbox, lister la racine et comparer.

---

## Décision 1 — Trois dossiers sans millésime

### Le fait

La racine Dropbox contient **9 dossiers** : les **6 racines attendues**
(`VOILES` et `ACCESSOIRES` 2024, 2025, 2026) et **3 dossiers hors format**.
Le format `VOILES|ACCESSOIRES <année>` étant une contrainte stricte du
consignateur, ces trois-là ne peuvent pas entrer dans le corpus sans qu'on
leur attribue une racine. Ils sont donc **connus mais non importés** : 820
fiches, et eux trois en dehors.

### Les indices, dossier par dossier

| # | Identifiant | Structure | Extensions | Dates des fichiers | Ce que ça suggère |
|---|---|---|---|---|---|
| **A** | `ed8db84ba2` | **arborescent** : un sous-dossier d'accessoire, puis **trois sous-dossiers de taille** | `.dxf` `.plx` `.pdf` `.xin` `.svg` `.jpg` | 2020-09 → 2026-02, **mélangées** | un **accessoire** (sac), décliné en plusieurs tailles — pas une voile |
| **B** | `0bf5bdf8d0` | **plat** : fichiers numérotés au même niveau | `.dxf` uniquement *(sur l'échantillon relevé)* | tous déposés le **2026-10-26** | un jeu de **laizes numérotées** — donc une **voile** |
| **C** | `5d3d10c783` | **plat** : fichiers numérotés au même niveau | `.dxf` uniquement *(sur l'échantillon relevé)* | tous déposés le **2026-10-26** | idem — une **voile** |

**Lecture des dates, et la réserve qu'elle impose** : B et C portent la date
de **dépôt** (2026-10-26), pas la date de conception. Elle ne prouve donc pas
que ces voiles sont de 2026 — elle prouve seulement qu'elles ont été versées
ce jour-là. Attribuer 2026 sur cette seule base serait une supposition
présentée comme un fait. C'est à vous de la confirmer.

Le dossier A, lui, est **sans ambiguïté de nature** (un sac, pas une voile)
mais **sans millésime du tout** : ses fichiers vont de 2020 à 2026.

### Ce qu'il faut décider

Pour chacun des trois :

1. **la racine** — `VOILES <année>` ou `ACCESSOIRES <année>` ;
2. **l'année** — sans quoi rien n'entre ;
3. **et pour A seulement** : les trois sous-dossiers de taille sont-ils trois
   fiches distinctes, ou une seule fiche déclinée ? Le corpus consigne une
   fiche par dossier ; la réponse change le compte.

### Ce qui est prêt

Rien n'est à écrire côté code. Le relevé est déjà fait et la commande d'import
existe (`scripts/etape0_corpus.py`). Dès la décision rendue, l'import suit.

---

## Décision 2 — Six paires de graphies à une lettre près

### Le fait

Le relevé exhaustif a fait apparaître **six couples** de graphies voisines
dans les noms de chantiers. Une graphie est une donnée de l'atelier : ce
n'est pas une faute de frappe qu'on corrige, c'est un **arbitrage** qu'on
rend. Le corriger de soi-même reviendrait à décider à la place de l'atelier
quelle graphie est la bonne.

| Graphie A | Graphie B | Nature probable de l'écart |
|---|---|---|
| `DUFOUR` | `DUFUR` | une lettre manquante |
| `HUNTER` | `HNTER` | une lettre manquante |
| `HELIA` | `HELLIA` | consonne doublée |
| `HALLBERG RASSY` | `HLLBERG RASSY` | une lettre manquante |
| `GIB SEA` | `GIBSEA` | espace |
| `FISCHER` | `FISHER` | consonne manquante |

### L'instrument est prêt, la décision ne l'est pas

Le dépôt possède déjà le mécanisme d'arbitrage — **le même** que celui qui a
servi à l'arbitrage « enrouleur » des lots A.1 et A.2. Deux verbes, et rien
d'autre :

* `refuser_fusion` — les deux graphies restent deux entrées distinctes ;
* `autoriser_fusion` + **graphie retenue** — les deux se rejoignent sous une
  seule, et c'est celle-là qui fera foi partout.

Le garde-fou anti-réintroduction est en place : une fusion refusée le reste,
et une fusion autorisée impose la graphie retenue — on ne peut pas obtenir
silencieusement l'inverse de ce qui a été décidé.

### Ce qu'il faut décider

Pour chaque couple : **fusionner ou non**, et si oui, **laquelle des deux
graphies est la bonne**. Six décisions binaires, dont six choix d'orthographe.

Une façon rapide de trancher : la graphie qui figure sur les documents
officiels du chantier est la bonne. Quand les deux apparaissent dans la
nature, c'est qu'elles désignent peut-être deux choses différentes — et
c'est alors un `refuser_fusion`.

### Ce qui se passe si rien n'est décidé

Rien de grave, mais rien de propre : les deux graphies coexistent dans le
corpus, les recherches par chantier rendent deux familles au lieu d'une, et
les comparaisons de fiches du même chantier peuvent manquer des
rapprochements. Personne ne perd de données ; on perd de la justesse.

---

## Récapitulatif — qui décide quoi

| # | Objet | Décideur | État |
|---|---|---|---|
| 1 | 3 dossiers sans millésime | l'atelier (racine + année + découpage pour A) | ⏳ **en attente** |
| 2 | 6 paires de graphies | l'atelier (fusion + graphie retenue) | ⏳ **en attente** |

**Aucune action n'a été prise sur l'un ou l'autre.** Les 820 fiches du corpus
sont intactes, aucun axe n'a été modifié, aucune fiche n'a été créée ni
retirée pour ces deux sujets.
