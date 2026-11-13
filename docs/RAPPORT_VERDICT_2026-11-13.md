# Rapport de verdict — Lot 22, 13/11/2026

Mandat : sauvegarde complète hebdomadaire, fermeture du diagnostic Windows,
preuve de restauration réelle, remontée des décisions en attente, et reprise
de la tâche de fond sur un lot borné.

**Verdict : PAS DE GO.** Deux preuves manquent, et elles ne sont pas
cosmétiques. Le détail est au §7, les deux verrous au §8.

---

## §1 — P1 Sauvegarde complète hebdomadaire · **faite**

Livrée et éprouvée au tour précédent (commit `300aa55`, 13 épreuves vertes).
Rien de nouveau à signaler, rien de régressé depuis.

## §2 — P2 Diagnostic Windows · **clos, et il dément ma propre piste**

C'était la priorité que j'attendais : le verdict du banc Windows réel.

### Le résultat

| Suite | Échecs | Réussites | Verdict |
|---|---|---|---|
| sans service | 355 | 2 331 | **vert** |
| PostgreSQL + Redis | 38 | 550 | **vert** |

Un vert **opposable**, pas un vert de complaisance : le verdict ne passe que
si *chaque* échec est nommé dans `ci/ecarts-windows.txt` avec sa cause et sa
contrepartie. 393 échecs, tous nommés. Le mécanisme a fonctionné comme prévu.

### Les causes, par ordre d'importance

```
verrou_flock (fcntl.flock)                    156
lecture confinée du bundle indisponible        53
scan_ancre_descripteur (dir_fd + O_NOFOLLOW)   39
module fcntl absent                            16
autres causes                                 ~29
```

C'est un diagnostic propre : des **capacités de fichiers** que Windows n'a pas,
et que le produit refuse nommément plutôt que de les simuler à moitié.

### Ma piste était la mauvaise — dit franchement

J'avais construit un simulateur de l'absence des primitives *processus*
(`fork`, `SIGKILL`, `killpg`), partant de l'idée que c'était la cause. Il a
prédit 4 échecs. Le runner en a 393, pour des raisons de fichiers.

Et les 4 étaient des **faux positifs**, démontrés : aucun n'est nommé dans
`ci/ecarts-windows.txt`, or le verdict exige que tout échec réel y figure.
Ces 4 tests passent donc sur Windows. Le simulateur retirait des primitives
que le produit n'appelle pas dans ces chemins-là.

**Le simulateur est annoté comme tel.** Il suggère des pistes, il ne conclut
rien : l'autorité est le runner Windows.

Un seul défaut certain en est sorti, et il est réel : un
`monkeypatch.setattr` sans `raising=False`, corrigé. Un défaut
d'échafaudage — mais ce n'était pas un échec Windows, et je ne l'ai pas
présenté comme tel.

### ⚠️ Incident : mon outil a mis le job au rouge

Le commit P1 est passé au rouge sur Windows **après coup**. Ce n'était pas une
régression du produit : le verdict de la suite était **vert**, et seule mon
étape de diagnostic était rouge. GitHub ajoute `exit $LASTEXITCODE` à toute
étape `pwsh` ; le code du dernier pytest — qui échoue sur Windows, c'est
attendu et nommé — devenait celui de l'étape.

Corrigé (`exit 0` : un diagnostic ne prononce pas de verdict). Documenté ici
plutôt que réparé en silence, comme à chaque incident.

Un deuxième défaut du même outil, corrigé au passage : il publiait des
notices **vides**, parce que GitHub tronque un message d'annotation au
premier saut de ligne, et que le plafond de 50 annotations par job était
atteint avant lui. L'inventaire détaillé part désormais dans un **job séparé**,
sur une seule ligne.

## §3 — P3 Preuve de restauration réelle · **NON FAITE**

La consigne était : le trajet réel avec preuve, **ou** un constat honnête de
ce qui manque — jamais une preuve simulée présentée comme réelle. C'est le
second cas.

**Mesuré, pas supposé :**

```
api.github.com     → HTTP 200           (référence : passe)
s3.amazonaws.com   → échec TLS (EOF)    (bloqué)
```

Le réseau de l'environnement est fermé à une liste blanche. Aucun fournisseur
objet n'est joignable. Et aucune identité de production n'y est présente — la
demander ici serait une faute, pas une étape.

**Ce qui reste vrai, et qu'il ne faut pas perdre** : l'émulateur qualifie le
*mécanisme*, pas le *fournisseur*. L'ETag multipart, la disponibilité d'un
checksum plein objet, la sémantique du versioning et la conservation des
octets dans le temps se décident **chez eux**, pas dans notre code.

Livré à la place (`docs/verite_terrain/PREUVE_RESTAURATION_REELLE.md`) : une
commande prête à courir sur site, qui refuse de démarrer en **nommant** ce qui
lui manque ; trois épreuves qui la vérifient (3 passed) ; et la liste exacte
des accès à réunir.

## §4 — P4 Décisions remontées · **rien n'a été tranché**

`docs/DECISIONS_EN_ATTENTE_2026-11-13.md`. **Aucune année attribuée. Aucune
fusion faite.** Les 820 fiches sont intactes.

**Trois dossiers sans millésime**, désignés par un identifiant stable et non
par leur nom (ce sont des noms de bateaux) :

| Id | Structure | Ce que ça suggère |
|---|---|---|
| `ed8db84ba2` | arborescent, sous-dossiers par taille, extensions variées, dates 2020→2026 | un **accessoire** |
| `0bf5bdf8d0` | plat, laizes numérotées, `.dxf`, déposés le 2026-10-26 | une **voile** |
| `5d3d10c783` | idem | une **voile** |

⚠️ **Réserve** : pour les deux derniers, 2026-10-26 est la date de *dépôt*,
pas celle de conception. Attribuer 2026 sur cette seule base serait une
supposition présentée comme un fait.

**Six paires de graphies** exposées, avec l'instrument d'arbitrage déjà
existant (refuser / autoriser + graphie retenue) — prêt à recevoir la
décision, pas appliqué par défaut.

## §5 — P5 Lot borné · **22 dossiers, 0 axe, et ce n'est pas un échec**

`docs/verite_terrain/QUALIFICATION_LOT_BORNE_2026-11-13.md`.

Une racine traitée **en entier** (22 dossiers), pas un échantillon choisi pour
réussir. Les 22 fiches ont été retrouvées par recalcul de leur identifiant —
le couplage dossier ↔ fiche fonctionne, ce que rien ne garantissait.

8 documents lus en entier : des **fiches de fabrication** et des plans de
cotes. Ils portent bateau, commande, couleur, dimensions. **Le champ tissu
n'existe pas sur ce modèle de document.**

Le corpus entier le confirme, et c'est le résultat utile :

| Racine | Fiches | Celles avec une matière | avec une gamme |
|---|---|---|---|
| ACCESSOIRES 2024 / 2025 / 2026 | 162 | **0** | **0** |
| VOILES 2024 | 74 | **0** | **0** |
| VOILES 2025 | 313 | 11 | 9 |
| VOILES 2026 | 268 | 7 | 5 |

**236 fiches (29 % du corpus) ne peuvent pas produire ces deux axes**, parce
que leurs documents ne les contiennent pas. La file de 817 mélange deux
populations qu'il faut séparer : celles où la qualification a un sens
(VOILES 2025 et 2026), et celles où on chercherait éternellement.

**Aucune valeur consignée** — le mandat l'interdit. **Aucun nom. Les PDF lus
ont été supprimés** après lecture.

## §6 — Ce que j'ai changé, fichier par fichier

| Fichier | État |
|---|---|
| `scripts/preuve_restauration_reelle.py` | nouveau — la commande à courir sur site |
| `tests/test_preuve_restauration_reelle.py` | nouveau — 3 passed |
| `docs/verite_terrain/PREUVE_RESTAURATION_REELLE.md` | nouveau — le constat P3 |
| `docs/DECISIONS_EN_ATTENTE_2026-11-13.md` | nouveau — les deux décisions P4 |
| `docs/verite_terrain/QUALIFICATION_LOT_BORNE_2026-11-13.md` | nouveau — le lot P5 |
| `.github/workflows/qualification-windows.yml` | modifié — job de diagnostic dédié, `exit 0` |
| `ci/plugin_simulation_processus_windows.py` | modifié — annoté : produit des faux positifs |
| `tests/test_extraction_logs_securite.py` | modifié — `raising=False` (défaut certain) |

Aucune donnée du corpus n'a été modifiée. Aucun document d'atelier ne
subsiste dans le dépôt.

## §7 — Verdict

**Je ne propose pas de GO.**

La règle posée est simple : jamais de GO plein tant qu'une preuve manque. Deux
manquent, et aucune n'est cosmétique :

1. **La restauration n'a jamais été prouvée contre le fournisseur objet réel.**
   Tout le reste — corpus, recherche, sauvegarde — repose sur l'idée qu'on
   peut revenir en arrière. C'est le seul point dont la défaillance est
   irréversible, et c'est le seul qu'on n'a pas pu exercer ici.
2. **Windows ne porte pas les capacités de fichiers du produit** (393 échecs
   nommés). Le déploiement Windows se fait en Docker avec des conteneurs
   Linux — décision de déploiement, pas un pis-aller. Elle doit être tenue,
   et elle n'a pas été revérifiée sur le poste réel.

## §8 — Les deux verrous, et comment les lever

**Verrou 1 — la preuve de restauration.** À faire sur site, une fois, par
quelqu'un qui a les accès. Ce qu'il faut réunir :

* une base source et une base cible **distinctes** (`SEAMTECH_DATABASE_URL`,
  `SEAMTECH_RESTORE_DATABASE_URL`) ;
* l'accès au fournisseur objet (`s3_endpoint_url`, `s3_bucket`, clés) ;
* une identité de sauvegarde **dédiée** (`SEAMTECH_BACKUP_ACCESS_KEY` /
  `SECRET_KEY`), qui ne soit pas l'identité de service.

Puis : `python scripts/preuve_restauration_reelle.py`. Elle refuse de
démarrer en nommant ce qui manque, et elle rend `PROUVÉ` ou `NON PROUVÉ`.
Une dizaine de minutes. **Un `PROUVÉ` prouve un aller-retour, pas la
durabilité** — la conservation dans le temps ne se prouve qu'en y revenant.

**Verrou 2 — les deux décisions de l'atelier** (§4) : la racine et l'année des
trois dossiers, et le sort des six paires de graphies. Rien à écrire côté
code : dès la décision rendue, l'import suit.

Tant que ces deux verrous ne sont pas levés, le projet est **prêt pour la
consultation et la qualification, pas déclarable comme garanti**.

## §9 — Ce que je n'ai pas fait

* Je n'ai pas attribué d'année aux trois dossiers, ni fusionné la moindre
  graphie. Ces décisions vous appartiennent.
* Je n'ai pas consigné le moindre axe en P5 : les documents ne les portent
  pas, et en inventer aurait été la pire chose à faire.
* Je n'ai pas poursuivi les curseurs Dropbox `has_more` laissés ouverts : les
  indices relevés suffisaient à instruire les deux décisions, et le reste
  n'aurait rien changé à ce qu'il faut trancher.
* Je n'ai rien fusionné vers `main`. La PR #44 reste ouverte.
