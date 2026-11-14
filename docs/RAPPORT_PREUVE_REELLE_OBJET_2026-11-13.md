# Preuve RÉELLE — objet perdu puis restauré · 13/11/2026

**Verdict : PROUVÉ.**

La chaîne a tourné pour de vrai, dans l'artefact déployé : vrais conteneurs
Linux, vrai PostgreSQL 16, vrai MinIO, vraie fiche déposée par les propres
commandes du produit, vraie suppression de l'objet, vraie restauration.

```
SHA      0297eafdcb8d289d813aae32481ace216277f51b
Branche  arena/765ebdcf-seamtech-search  (PR #44, OPEN, MERGEABLE, rien de fusionné)
Job      integration — étape 14 « Preuve RÉELLE — objet perdu puis restauré »
Runs     43534981467 et 43534976364 — les deux completed / success, 13 jobs verts
```

---

## §1 — Les seize jalons, tels que la CI les a publiés

Copiés tels quels de l'annotation `preuve-JALONS` (checks `132261672506` et
`132261655479`) — pas reconstitués depuis le code :

```
services=ok ; session nominative=ok ; objet servi avant=ok ;
appariement pièce ↔ objet=ok ; alias de stockage=ok ; sauvegarde complète=ok ;
bundle vérifié=ok ; seconde copie retirée=ok ; objet supprimé=ok ;
objet disparu=ok ; base cible=ok ; restauration=ok ; octets restaurés=ok ;
protection globale=ok ; réinjection=ok ; objet resservi=ok
```

et le verdict, publié par le script lui-même :

```
preuve-PROUVÉ :: objet supprimé du stockage puis resservi, octets identiques
amorcage-OK   :: code_fiche=7792-SO — décor prêt
```

Le scénario, dans l'ordre du sinistre : on sauvegarde, le partage source
tombe, l'objet reste et suffit à servir, puis l'objet tombe à son tour, on
restaure dans une base distincte, on réinjecte, l'application ressert les
mêmes octets — et le produit déclare lui-même la protection globale prouvée.

## §2 — Ce qui a été prouvé, et ce que cela ne dit pas

Prouvé :

- l'objet supprimé du seau rend la pièce **inservable** (perte réelle, pas
  supposée) ;
- l'objet seul **suffit** à servir la pièce une fois la copie locale retirée ;
- la sauvegarde complète du produit **contient** cet objet, et son SHA-256 est
  conforme après restauration ;
- la restauration coordonnée rend des **octets identiques** dans une base
  cible distincte — jamais par-dessus la source ;
- le produit pose `complet: true` sur le manifeste, ce qu'il ne fait
  qu'après un contrôle composant par composant.

Non prouvé, et volontairement hors de portée : le sinistre où l'on perd **à la
fois** le seau et le bundle ; la restauration d'un corpus de taille
industrielle ; et le poste Windows de l'exploitante — aucun runner GitHub ne
fait tourner des conteneurs Linux sous Windows, donc `recette_locale.ps1`
reste la seule qualification qui vous appartienne (§7 du rapport de verdict).

## §3 — Le défaut trouvé dans l'artefact déployé

**L'image applicative sauvait, mais ne savait pas restaurer.**

`Dockerfile` installait le `postgresql-client` de Debian — majeure 17 —
alors que la composition sert `pgvector/pgvector:pg16`. Un client plus
récent **dump** sans broncher, puis **restore** en écrivant des réglages que
le serveur ignore : `SET transaction_timeout`, refusé par PostgreSQL 16 par
`ERROR: unrecognized configuration parameter`.

La sauvegarde réussissait. La restauration échouait. Et rien ne le signalait
avant le jour du sinistre — c'est le pire défaut possible pour une sauvegarde,
parce qu'il ne se voit qu'au moment où l'on en a besoin.

Le Dockerfile affirmait déjà le contraire, noir sur blanc :

> `postgresql-client` (OBS-04) … Le client Debian (majeure **>=** serveur 16)
> sait dumper le serveur pgvector/pg16 de la composition.

Il sait dumper. Il ne sait pas restaurer. **La rassurance était dans le
commentaire, le défaut était dans l'image.** La leçon n'est pas qu'un
commentaire était faux : c'est qu'une rassurance non éprouvée vaut moins
que pas de commentaire, parce qu'elle dissuade de vérifier.

Correction (`edd9aeb`) :

1. majeure épinglée (`ARG PG_MAJEURE=16`) et paquet pris au dépôt PGDG, seul
   à servir un client 16 quand Debian n'en fournit plus ;
2. **échec fermé à la construction** — `pg_dump --version` et
   `pg_restore --version` sont confrontés à la majeure attendue. Sans ces
   assertions, un dépôt injoignable laisserait l'image avec le client Debian
   par défaut, celui-là même qui casse la restauration, sans rien dire ;
3. un test statique interdit le retour en arrière
   (`tests/test_image_client_postgresql.py`) : la majeure du client doit être
   **celle du serveur**, ni plus ni moins.

## §4 — Les cinq autres trous bouchés en route

Chacun a coûté un cycle CI, chacun était invisible depuis le code seul.

| # | Trou | Ce qui le rendait invisible |
|---|---|---|
| 1 | **Racine servie non déclarée.** `charger_document` refuse tout chemin hors des racines déclarées (403), **avant** même de regarder si un objet existe. Le service web démarrait avec `/data/SEAMTECH/DesignFiles`, répertoire absent du conteneur. | Une pièce inservable ressemble à une pièce perdue. |
| 2 | **La seconde copie.** Quand l'objet manque, l'application retombe sur le fichier local, en silence (un avertissement au journal). | Supprimer l'objet ne prouvait rien : la pièce continuait d'être servie. |
| 3 | **Le nom du manifeste est fabriqué** par la commande (`<dump>.manifest.json`), pas `manifeste.json`. | La vérification mourait d'une `FileNotFoundError` muette. |
| 4 | **`complet` est l'aboutissement, pas le préalable.** Il est faux par construction sur un bundle frais (`sauvegarde.py:41`) et n'est posé qu'après une restauration contrôlée. | L'exiger avant la restauration condamnait la preuve à exiger son propre résultat. |
| 5 | **Le détail d'une étape coupait sa propre queue.** Vingt-trois `[:300]` tronquaient la tête avant `_aplatir` : l'exception, seule ligne utile, disparaissait avant d'être aplatie. | Cinq cycles passés à deviner une cause déjà écrite, mais coupée juste avant. |

S'y ajoutent, des tours précédents : l'appariement pièce ↔ objet lu en base
(`object_key` n'est jamais exposé par l'API), la base cible distincte, la
sauvegarde avant la suppression, la session nominative humaine, et l'alias
`mc` qui n'existe pas par défaut.

## §5 — Ce que la méthode doit à ces échecs

Quatre règles, payées cher, désormais dans le code :

1. **Le script annonce son verdict lui-même**, en flux non tamponné. Une étape
   bash qui capture la sortie dans une variable perd la raison si elle meurt.
2. **Une seule annotation de résultat par étape.** GitHub plafonne à dix ;
   au-delà, les suivantes sont évincées **silencieusement**, ce qui fait
   croire à une panne une étape qui a réussi. Trois cycles perdus à
   instrumenter un succès.
3. **Le détail garde la tête et la queue.** Une trace Python finit par
   l'exception.
4. **Ne jamais exiger d'un drapeau ce qu'il ne promet pas.** Lire le code
   avant d'écrire l'assertion : `complet` dit « la protection est prouvée »,
   pas « le bundle est fini ».

Et une règle de confidentialité tenue jusqu'au bout : pour lire la cause de
l'échec de `pg_restore`, que le produit masque à dessein, le diagnostic
**rejoue** la commande sur une base jetable et **expurge** l'hôte, le port et
l'identifiant avant publication. Reprendre le vrai `stderr` aurait affaibli
une protection explicite ; un test exige qu'aucun identifiant n'y survive.

## §6 — État des lieux

| | |
|---|---|
| Suite de tests | **2 951 passed**, 832 skipped, 3 xfailed |
| Jobs CI au SHA ci-dessus | 13/13 verts, deux runs |
| PR #44 | OPEN, MERGEABLE, **rien fusionné** |
| Fichiers produits | `scripts/preuve_objet_perdu.py`, `scripts/amorcer_preuve_objet.py`, `tests/test_preuve_objet_perdu.py`, `tests/test_amorcer_preuve_objet.py`, `tests/test_image_client_postgresql.py`, `.github/workflows/ci.yml`, `Dockerfile` |
| Fichiers modifiés pour la preuve | aucun fichier du produit, hors `Dockerfile` (le défaut du §3) |

## §7 — Ce que je recommande

La preuve est faite, la CI est verte, rien n'est fusionné.

**Ne pas fusionner tant que l'atelier n'a pas tranché** les deux décisions
P4 restées ouvertes (racine et millésime des trois dossiers sans année, sort
des six paires de graphies) — elles n'ont rien à voir avec cette preuve, mais
la branche les porte.

**Le défaut du §3 mérite une lecture de votre part** : il touchait
l'artefact déployé tel qu'il aurait été livré, et seule l'exécution réelle
pouvait le révéler. C'est précisément ce que la Voie B devait attraper.

**Reste une qualification que personne ne peut faire à votre place** : une
exécution sur le poste de l'atelier, Docker Desktop lancé —

```powershell
.\scripts\recette_locale.ps1
```

Aucun runner, aucune machine de développement ne remplace ce passage-là.

---

*Rapport écrit au SHA `0297eafdcb8d289d813aae32481ace216277f51b`. Toute
affirmation ci-dessus est vérifiable à ce SHA, et à lui seul.*
