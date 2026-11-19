# Clôture avant GO — état consolidé · 13/11/2026

**Un seul document, celui-ci.** Les rapports détaillés restent la référence
pour chaque sujet ; celui-ci donne l'état final, la liste de lecture, et les
deux décisions qui n'appartiennent qu'à vous.

## État d'avancement

| Preuve | État | Où le vérifier |
|---|---|---|
| **P2 — Windows** | **clos** | § ci-dessous |
| **P3 — sauvegarde / restauration objet** | **PROUVÉ, 16/16 jalons** | § ci-dessous |
| **P5 — corpus (lot borné)** | **clos, et il dément une hypothèse** | § ci-dessous |
| **Défaut de l'artefact déployé** | **trouvé et corrigé** | § ci-dessous |
| **Décisions P4** | ⏳ **en attente du commanditaire** | § « Les deux décisions » |

**Rien n'est fusionné vers `main`.** Une seule PR ouverte, et elle le reste.

---

## §1 — P3 · Sauvegarde et restauration d'un objet · **PROUVÉ**

Tourné pour de vrai, dans l'artefact déployé : vrais conteneurs Linux, vrai
PostgreSQL 16, vrai MinIO, fiche déposée par les propres commandes du produit.

**SHA exact : `0297eafdcb8d289d813aae32481ace216277f51b`**
**PR : <https://github.com/Ilyes-Neguir/SEAMTECH-search/pull/44>** — `OPEN`,
`MERGEABLE`, `mergedAt: null`, 151 commits d'avance sur `main`, rien de fondu.

Les seize jalons, tels que la CI les a publiés (checks `132261672506` et
`132261655479`), non reconstitués depuis le code :

```
services=ok ; session nominative=ok ; objet servi avant=ok ;
appariement pièce ↔ objet=ok ; alias de stockage=ok ; sauvegarde complète=ok ;
bundle vérifié=ok ; seconde copie retirée=ok ; objet supprimé=ok ;
objet disparu=ok ; base cible=ok ; restauration=ok ; octets restaurés=ok ;
protection globale=ok ; réinjection=ok ; objet resservi=ok
preuve-PROUVÉ :: objet supprimé du stockage puis resservi, octets identiques
```

Runs `43534981467` et `43534976364` : les deux `completed / success`, **13 jobs
verts** chacun. Suite de tests : **2 951 réussites**, 832 ignorés, 3 échecs
attendus.

Le scénario, dans l'ordre du sinistre : on sauvegarde → le partage source
tombe → l'objet reste et suffit à servir → l'objet tombe à son tour → on
restaure dans une base distincte → on réinjecte → l'application ressert les
mêmes octets → **le produit déclare lui-même la protection globale prouvée**
(dernier jalon, et le seul qui ne vienne pas de notre mesure).

**Ce que cette preuve ne couvre pas**, et qu'il faut garder en tête : le
sinistre qui emporte *à la fois* le stockage et le bundle ; une restauration
à taille industrielle ; et le poste Windows — aucun runner GitHub ne fait
tourner des conteneurs Linux sous Windows.

## §2 — Défaut réel de l'artefact déployé · **trouvé, corrigé**

**L'image applicative savait sauvegarder, mais pas restaurer.**

Elle installait le client PostgreSQL de Debian (majeure 17) alors que la
composition sert `pgvector/pgvector:pg16`. Un client plus récent **exporte**
sans broncher, puis **réimporte** en écrivant des réglages que le serveur
ignore — `SET transaction_timeout`, refusé par PostgreSQL 16. La sauvegarde
réussissait, la restauration échouait, et **rien ne le signalait avant le jour
du sinistre**. C'est le pire défaut possible pour une sauvegarde : il ne se
voit qu'au moment où l'on en a besoin.

Le Dockerfile affirmait déjà le contraire, noir sur blanc : *« Le client
Debian (majeure >= serveur 16) sait dumper le serveur pgvector/pg16 »*. Il
sait exporter. Il ne savait pas réimporter. **La rassurance était dans le
commentaire, le défaut dans l'image.**

Corrigé (`edd9aeb`) : majeure épinglée, dépôt PGDG, et **échec fermé à la
construction** — une image qui ne peut pas prouver la majeure de son client ne
se construit pas. Un test interdit le retour en arrière.

C'est exactement ce que devait attraper l'exécution réelle : ce défaut
n'existait que dans l'artefact assemblé, aucun test unitaire ne pouvait le
voir.

## §3 — P2 · Windows · **clos, et il dément ma propre piste**

Le banc Windows réel a rendu **393 échecs, tous nommés**, avec cause et
contrepartie : des capacités de fichiers que Windows n'a pas
(`flock`, lecture confinée par descripteur, `O_NOFOLLOW`), et que le produit
refuse nommément plutôt que de les simuler à moitié.

Ce n'était pas un défaut du produit : c'était une mesure prise sur une
configuration que le guide déclare non supportée. La **Voie B** a été retenue
— qualifier ce qui est réellement déployé (Docker, conteneurs Linux) plutôt
que du Python Windows natif.

J'avais construit un simulateur partant d'une hypothèse fausse ; il a prédit
4 échecs là où le runner en a 393, pour d'autres raisons. Les 4 étaient des
**faux positifs**, démontrés deux fois. Le simulateur est annoté comme tel :
il suggère, il ne conclut pas. L'autorité est le runner.

L'instrument de qualification du poste existe et est vert :
`scripts/recette_locale.ps1` démarre la pile et vérifie **20 contrôles**, dont
la sauvegarde/restauration et la persistance.

## §4 — P5 · Corpus, lot borné · **clos, résultat utile**

Une racine traitée **en entier** (22 dossiers), pas un échantillon choisi pour
réussir. Les 22 fiches ont été retrouvées par recalcul de leur identifiant :
le couplage dossier ↔ fiche fonctionne, ce que rien ne garantissait.

Le résultat qui compte : **236 fiches (29 % du corpus) ne peuvent pas produire
les axes « matière » et « gamme »**, parce que leurs documents ne les
contiennent pas — le champ tissu **n'existe pas** sur ce modèle de fiche de
fabrication. Chercher ces axes sur ces fiches serait chercher éternellement.

**Aucune valeur n'a été consignée** — le mandat l'interdit. Aucun nom. Les PDF
lus ont été supprimés après lecture.

---

## §5 — Les deux décisions · ⏳ **EN ATTENTE DU COMMANDITAIRE**

Rien n'a été tranché, rien n'a été appliqué. **Les 820 fiches du corpus sont
intactes.** Documents de référence : `docs/DECISIONS_EN_ATTENTE_2026-11-13.md`.

### Décision 1 — Trois dossiers sans millésime

La racine contient 9 dossiers : les 6 attendus (`VOILES` et `ACCESSOIRES`
2024, 2025, 2026) et **3 hors format**. Le format `VOILES|ACCESSOIRES <année>`
étant une contrainte stricte, ces trois-là ne peuvent entrer sans qu'on leur
attribue une racine. Ils sont connus mais non importés.

Ils sont désignés par **identifiant stable** — les dix premiers caractères du
SHA-256 du nom en minuscules — jamais par leur nom, parce que ce sont des noms
de bateaux et qu'un nom de client ou de bateau n'entre ni dans un dépôt, ni
dans un rapport.

| # | Identifiant | Structure | Ce que ça suggère |
|---|---|---|---|
| **A** | `ed8db84ba2` | arborescent : un sous-dossier d'accessoire, puis **trois sous-dossiers de taille** ; `.dxf` `.plx` `.pdf` `.xin` `.svg` `.jpg` ; dates 2020-09 → 2026-02 mélangées | un **accessoire** décliné en plusieurs tailles — pas une voile |
| **B** | `0bf5bdf8d0` | plat, laizes numérotées, `.dxf` seul, tous déposés le 2026-10-26 | une **voile** |
| **C** | `5d3d10c783` | idem | une **voile** |

⚠️ **Réserve** : pour B et C, 2026-10-26 est la date de **dépôt**, pas celle de
conception. Attribuer 2026 sur cette seule base serait une supposition
présentée comme un fait.

**À décider, pour chacun :** la racine (`VOILES <année>` ou `ACCESSOIRES
<année>`), l'année, **et pour A seulement** si les trois sous-dossiers de
taille font trois fiches distinctes ou une seule déclinée — la réponse change
le compte.

### Décision 2 — Six paires de graphies à une lettre près

Une graphie est une donnée de l'atelier : ce n'est pas une faute de frappe
qu'on corrige, c'est un **arbitrage** qu'on rend. Le corriger de soi-même
reviendrait à décider à votre place quelle orthographe est la bonne.

| Graphie A | Graphie B | Nature de l'écart |
|---|---|---|
| `DUFOUR` | `DUFUR` | une lettre manquante |
| `HUNTER` | `HNTER` | une lettre manquante |
| `HELIA` | `HELLIA` | consonne doublée |
| `HALLBERG RASSY` | `HLLBERG RASSY` | une lettre manquante |
| `GIB SEA` | `GIBSEA` | espace |
| `FISCHER` | `FISHER` | consonne manquante |

**À décider, pour chaque couple :** fusionner ou non, et si oui **laquelle des
deux graphies est la bonne**. Six décisions binaires, dont six choix
d'orthographe. Règle rapide : la graphie qui figure sur les documents
officiels du chantier est la bonne ; quand les deux apparaissent dans la
nature, c'est peut-être qu'elles désignent deux choses différentes — et c'est
alors un refus de fusion.

L'instrument existe déjà (refuser / autoriser + graphie retenue), avec le
garde-fou anti-réintroduction. **Rien n'a été appliqué.** Si rien n'est
décidé : les deux graphies coexistent, les recherches rendent deux familles au
lieu d'une, on perd de la justesse — personne ne perd de données.

---

## §6 — Liste de lecture pour le patron, dans l'ordre

Cinq documents, dans cet ordre. Le premier suffit à décider ; les autres
servent à vérifier.

| Ordre | Document | Pourquoi | Durée |
|---|---|---|---|
| **1** | **celui-ci** | l'état final et ce qui reste à votre charge | 10 min |
| **2** | `docs/DECISIONS_EN_ATTENTE_2026-11-13.md` | **les deux décisions** — c'est le seul document qui demande une action de votre part | 10 min |
| **3** | `docs/RAPPORT_PREUVE_REELLE_OBJET_2026-11-13.md` | la preuve de sauvegarde/restauration : le verdict, les 16 jalons, le défaut de l'image | 15 min |
| 4 | `docs/RAPPORT_VERDICT_2026-11-13.md` | le verdict du lot 22 dans son ensemble (Windows, corpus, méthode) | 20 min |
| 5 | `docs/CHECKLIST_POSTE_WINDOWS.md` | la procédure à jouer sur le poste de l'atelier | à garder sous la main |

Pour aller plus loin : `docs/QUALIFICATION_DEPLOIEMENT_2026-11-13.md`
(qualification du déploiement) et
`docs/verite_terrain/QUALIFICATION_LOT_BORNE_2026-11-13.md` (le relevé brut du
corpus, 22 dossiers).

### Avant de dire GO — la liste courte

1. **Rendre les deux décisions** (§5). Rien ne bloque côté code : dès la
   décision rendue, l'import et la fusion suivent.
2. **Jouer une fois la procédure sur le poste de l'atelier** — Docker Desktop
   lancé : `.\scripts\recette_locale.ps1`. C'est la seule qualification
   qu'aucune machine de développement ni aucun runner ne peut faire à votre
   place, parce qu'un runner GitHub Windows standard ne fait pas tourner de
   conteneurs Linux.
3. **Lire le §2** et décider si le défaut de l'image, désormais corrigé et
   verrouillé par un test, appelle une vérification supplémentaire de votre
   part avant la mise en service.

GO recommandé **une fois les points 1 et 2 faits**.

## §7 — Ce qui reste à la charge du commanditaire, et à personne d'autre

1. Rendre la décision 1 : pour les dossiers `ed8db84ba2`, `0bf5bdf8d0` et
   `5d3d10c783` — voile ou accessoire, et quelle année.
2. Rendre la décision 2 : pour les six paires de graphies — garder les deux
   orthographes, ou n'en garder une, et laquelle.
3. Lancer la procédure une fois sur le poste de l'atelier et me transmettre le
   compte rendu.
4. Dire GO — aucune fusion ne sera faite sans votre accord explicite.

## §8 — Transparence

* **Dix incidents de persistance** de l'environnement de travail depuis le
  début de la mission (le dixième pendant la rédaction de ce document :
  l'arbre local était retombé au commit de base). Aucun n'a coûté de travail :
  tout est poussé à chaque étape, et la récupération est une routine
  (`fetch` + `reset --hard`). **Aucun n'a affecté le dépôt distant, la PR, ni
  les preuves** — elles sont toutes vérifiables au SHA cité.
* Les journaux bruts de ce dépôt sont servis par un hôte inaccessible depuis
  l'environnement de travail. C'est la raison d'être des annotations publiées
  par les scripts eux-mêmes, et cela explique la forme de la preuve : elle
  publie son verdict et ses jalons au lieu de les écrire dans un journal.
* **Aucune fusion vers `main`.** La branche `arena/765ebdcf-seamtech-search`
  porte 151 commits et reste ouverte.

---

*Document écrit au SHA `2312f62327fd1b28e167d7d195fb8f23061035af`, qui
contient le commit prouvé `0297eafdcb8d289d813aae32481ace216277f51b`.*
