# P5 — Un lot borné traité à fond, et ce qu'il apprend sur les 817

**Date** : 13/11/2026. **Lot** : la racine `ACCESSOIRES 2024`, en entier.
**Compte avant / après** : **22 dossiers examinés — 0 axe consigné.**

Un lot qui se termine sur un zéro peut être un échec ou un résultat. Celui-ci
est un résultat, et voici pourquoi.

---

## 1. Le lot, et pourquoi celui-là

La racine la plus petite et complète du corpus : **22 dossiers**, soit 100 %
d'une racine — pas un échantillon choisi pour réussir. Les 22 fiches
correspondantes ont été retrouvées dans le corpus par recalcul de leur
identifiant (`sha256(sel|chemin)`, le sel étant celui du corpus) : **22/22**,
toutes sans le moindre axe renseigné.

Le couplage entre un dossier et sa fiche fonctionne donc. C'est un préalable
que rien ne garantissait : l'identifiant est une empreinte à sens unique, et
rien n'assurait qu'on puisse remonter du dossier à la fiche. On le peut, en
recalculant l'empreinte depuis le chemin exact — `/<RACINE>/<NOM>`, avec la
racine en majuscules.

## 2. Ce que contiennent réellement les documents

9 dossiers sur 22 contiennent un PDF lisible ; les 13 autres ne contiennent
que des pièces de coupe (`.dxf`) et des fichiers de machines, qu'aucun lecteur
de texte ne peut interpréter.

Les 8 PDF lus en entier sont **deux types de documents**, et ni l'un ni
l'autre ne porte les axes demandés :

| Type | Ce qu'il porte | Ce qu'il ne porte pas |
|---|---|---|
| **Fiche de fabrication** (5 lues) | bateau, n° de commande, client, contrôle, logo, **couleur**, et les cotes de l'ouvrage | ni tissu, ni gamme |
| **Plan « FINI »** (3 lus) | des cotes, et rien d'autre — 9 à 13 caractères de texte par document | tout le reste |

Les fiches de fabrication sont propres et structurées : on y lit sans
ambiguité la couleur et les dimensions. Le champ **tissu est absent** — il
n'est pas vide, il n'existe pas sur ce modèle de document.

## 3. La vérification qui change la portée du constat

Huit documents ne prouvent pas une règle. Le corpus entier l'établit :

| Racine | Fiches | Celles qui portent une **matière** | une **gamme** |
|---|---|---|---|
| ACCESSOIRES 2024 | 22 | **0** | **0** |
| ACCESSOIRES 2025 | 76 | **0** | **0** |
| ACCESSOIRES 2026 | 64 | **0** | **0** |
| VOILES 2024 | 74 | **0** | **0** |
| VOILES 2025 | 313 | 11 | 9 |
| VOILES 2026 | 268 | 7 | 5 |

La matière et la gamme n'apparaissent **que** dans `VOILES 2025` et
`VOILES 2026` — 18 et 14 fiches sur 820. Jamais dans les accessoires, jamais
dans VOILES 2024.

Autrement dit : **236 fiches (29 % du corpus) ne peuvent pas produire ces deux
axes**, parce que leurs documents ne les contiennent pas. Elles ne sont pas
« à qualifier » : elles sont complètes au sens de leurs documents.

## 4. Conséquence — la file de 817 n'est pas homogène

La file annonce 817 fiches à qualifier. Ce lot montre qu'elle mélange deux
choses qu'il faut séparer :

* **les fiches dont le document porte l'information** (`VOILES 2025`,
  `VOILES 2026`) — la qualification manuelle y a un sens, et c'est là qu'on a
  déjà 18 matières et 14 gammes de renseignées ;
* **les fiches dont le document ne la porte pas** (les 236) — les y chercher
  plus longtemps ne produira rien, et les y inscrire reviendrait à inventer.

**Recommandation** : resserrer la file sur `VOILES 2025` et `VOILES 2026`, et
sortir les 236 autres du décompte — non pas les abandonner, mais cesser de les
compter comme un retard.

## 5. Ce qui n'a PAS été fait

* **Aucune valeur consignée.** Pas une seule. Consigner quoi que ce soit sur
  ces 22 dossiers aurait été une invention, et le mandat est explicite
  là-dessus.
* **Aucun nom.** Ni de client, ni de bateau, ni de fichier. Les dossiers ne
  sont désignés nulle part dans ce document, y compris ceux dont le nom
  contient un nom de chantier — on en parle par leur type et leur nombre.
* **Les PDF lus ont été supprimés** de l'espace de travail après lecture.
  Aucun document d'atelier ne subsiste dans le dépôt, et rien n'y a été
  déposé.

## 6. Le point d'arrêt

Le lot est **terminé**, pas interrompu : 22 dossiers sur 22 examinés, 8
documents lus en entier, un résultat démontré et une recommandation. Le
suivant (`ACCESSOIRES 2025`, 76 dossiers) n'a pas été commencé — il
reproduirait le même constat, les chiffres du §3 le disent déjà.
