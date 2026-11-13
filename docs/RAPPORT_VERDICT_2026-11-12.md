# Rapport de clôture — Lot 21 (12/11/2026)

Branche `arena/765ebdcf-seamtech-search`. Mandat : **P0 sauvegarde hors-git du
corpus → P1 compléter le corpus → P2 repli Windows → P3 clôture**, dans cet
ordre, avec ECC à chaque étape et CI au SHA exact.

Ce rapport dit ce qui est prouvé, ce qui ne l'est pas, et ce qui vous revient.
Chaque chiffre est assorti de la commande qui le reproduit.

---

## 0. Résumé d'une phrase

**Le corpus est complet et mesuré — 820 fiches, pas 338 —, il est sauvegardé
hors-site et restaurable à l'identique, la limite Windows est écrite dans le
guide ; mais un constat fait au passage remet en cause la mise en service :
les pièces jointes des clients ne sont dans aucune sauvegarde automatique, et
le guide affirmait le contraire.** Le workflow de qualification Windows, lui,
reste rouge sur une étape de diagnostic — antérieurement à ce lot, et en
s'améliorant par ailleurs (section 6.1).

Verdict en section 8.

---

## 1. Priorité 0 — sauvegarde hors-git du corpus : **livrée**

| Livrable | État | Preuve |
|---|---|---|
| Sauvegarde quotidienne automatique du corpus vers le stockage hors-site | **fait** | service compose `sauvegarde`, `SEAMTECH_CORPUS_DIR` + `_sauver_corpus()` au même rythme que la base |
| Restauration prouvée par empreinte | **fait** | `scripts/sauvegarder_corpus.py restaurer` → `intégrité : verifiee` |
| Visibilité `/health` et vérificateur | **fait** | clé `corpus` dans `/health` (jamais `True` en cas de doute), `scripts/verifier_sauvegarde_quotidienne.py` |
| Montage compose en lecture seule | **fait** | `./donnees/etape0:/app/donnees/etape0:ro` |

**Trajet complet rejoué sur le corpus complet (820 fiches)**, contre un
émulateur S3 isolé sur loopback (s3rver — ni MinIO ni R2 : c'est le mécanisme
qui est qualifié, pas le fournisseur) :

```
sauvegarder  → corpus sauvegardé (3 fichier(s)) — hors-site   code 0
verifier     → {"protege": true, "dernier": "20261112T222113Z"}   code 0
restaurer    → intégrité : verifiee                                code 0

accumulateur.json      206 414 o   IDENTIQUE (sha256)
decisions.json           1 030 o   IDENTIQUE
reparations_axes.json      422 o   IDENTIQUE
fiches restaurées : 820 — sans aucun axe : 790
```

Les trois fichiers du corpus sont couverts (`FICHIERS_CORPUS`), pas seulement
les deux premiers.

### 1.1 Un incident, documenté plutôt qu'effacé

La CI de `db70fdf` est sortie **rouge** sur deux causes distinctes :

1. `backend (3.11)`, étape « Run SQLite suite » — trois assertions de
   `tests/test_selection_ci.py` : une épreuve marquée `s3` ne figurait pas
   dans l'inventaire. **C'était un garde-fou du dépôt qui avait raison.**
   Corrigé dans `0bd099a`.
2. `recette-locale` — `failed to resolve source metadata for
   docker.io/docker/dockerfile:1 : 502 Bad Gateway`. **Incident
   d'infrastructure, pas de code.** Il ne s'est pas reproduit : le job est
   vert depuis, sans que je touche à la composition.

---

## 2. Priorité 1 — le corpus complété : **820 fiches, et 338 était faux**

**Le premier résultat de cette priorité est une correction.** L'échelle
annoncée de 338 fiches n'a jamais été mesurée par personne. Le relevé
exhaustif, dossier par dossier, donne **813 dossiers** ; avec les 7 fiches
d'archive, **820 fiches**.

| Racine | Avant | Relevé | Après |
|---|---|---|---|
| `VOILES 2024` | 74 | 74 | **74** |
| `VOILES 2025` | 30 | 309 | **316** |
| `VOILES 2026` | 19 | 268 | **268** |
| `ACCESSOIRES 2024` | 0 | 22 | **22** |
| `ACCESSOIRES 2025` | 0 | 76 | **76** |
| `ACCESSOIRES 2026` | 0 | 64 | **64** |
| **Total** | **123** | **813** | **820** |

**Lire la colonne « Relevé » :** 813 est ce que Dropbox a rendu. La colonne
« Après » y ajoute **7 fiches d'archive**, toutes en `VOILES 2025` — d'où 309
relevées mais 316 comptées sur cette racine, et 820 au total. Ce n'est pas une
approximation : le décompte par source se lit d'une commande.

```
820 fiches — VOILES 2024 74 · VOILES 2025 316 (dont 7 d'archive) · VOILES 2026 268
           · ACCESSOIRES 2024 22 · 2025 76 · 2026 64
```

Reproduire : `.venv/bin/python scripts/etape0_corpus.py stats`.

### 2.1 Méthode — pourquoi cette fois c'est démontrable

L'objection qui avait fait arrêter le complément était juste : Dropbox tronque
au-delà de `limit=50`, et **silencieusement** (la réponse est coupée sans
poser `has_more`). Un relevé ainsi tronqué aurait produit un corpus *qu'on
aurait cru complet*.

La pagination a donc été menée par pages de 50 avec `list_folder_continue`, et
**chacune des six racines s'est terminée sur `has_more: false`**. Le relevé
est exhaustif et démontrable, et il n'a pas été complété de moi-même : chaque
nom provient d'une réponse du connecteur.

### 2.2 Ce que le complément apporte, et ce qu'il n'apporte pas

Les 697 fiches nouvelles sont connues par leur chemin et n'ont **aucun axe** :
l'arborescence ne dit ni la gamme, ni le type de voile, ni la matière. Elles
rejoignent la file de qualification manuelle — aucun axe n'a été estimé,
aucune fiche retirée.

| Mesure | Valeur |
|---|---|
| Fiches consignées | 820 |
| Identifiants | 820, **tous distincts** (aucun doublon) |
| `gamme` / `type_voile` renseignés | 14 / 820 |
| `matiere` renseignée | 18 / 820 |
| Fiches **sans aucun axe** | 790 |
| Fiches à qualifier (au moins un axe manquant) | **817** |

Reproduire : `scripts/file_qualification.py montrer --limite 0`.

### 2.3 Non-régression, mesurée et pas affirmée

Les 123 fiches préexistantes ont été rejouées **sans fournir aucun axe**.
Comparaison identifiant par identifiant entre l'accumulateur d'avant et celui
d'après :

```
axes perdus ou modifiés sur les 123 fiches préexistantes : 0
nouvelles fiches : 697
total avant/après : 123 → 820
```

Sans la correction du Lot 19 (`_fusionner_axes`), cette opération aurait
effacé les 14 types de voile réellement lus dans les documents. Le défaut du
§6.2 est ainsi éprouvé à l'échelle, pas seulement en laboratoire.

### 2.4 Deux points qui vous reviennent

- **Trois dossiers à la racine ne sont dans aucune des six racines.** Ils ne
  portent pas de millésime et contiennent les pièces brutes d'un chantier
  (patrons `.dxf`, `.mi2`, plans de coupe), non des fiches. Le format
  `VOILES|ACCESSOIRES <année>` étant une contrainte stricte, ils ne peuvent
  entrer sans que vous choisissiez leur rattachement.
- **Six couples de graphies voisines** ont été relevés et **laissés tels
  quels** : `DUFOUR`/`DUFUR`, `HUNTER`/`HNTER`, `HELIA`/`HELLIA`,
  `HALLBERG`/`HLLBERG RASSY`, `GIB SEA`/`GIBSEA`, `FISCHER`/`FISHER`. Une
  graphie est une donnée de l'atelier ; le dépôt possède l'instrument pour
  trancher (`refuser_fusion` / `autoriser_fusion`), l'arbitrage lui-même est
  le vôtre. Détail en §6.3 de `docs/ETAPE0_ACCES_CORPUS.md`.

**Aucune pièce jointe n'a été téléchargée.** Le complément a porté sur
l'arborescence seule.

---

## 3. Priorité 2 — repli Windows : **limite documentée, et un constat grave**

### 3.1 La limite, écrite dans le guide et pas en commentaire

`docs/GUIDE_MISE_EN_SERVICE_POSTE.md` s'ouvre désormais sur « Lire ceci avant
l'étape 0 ». Elle dit que l'application ne fonctionne **pas** sous Python
Windows mais uniquement par Docker, pourquoi (`dir_fd` + `O_NOFOLLOW`,
`/proc/self/fd`, `fcntl.flock` n'ont aucun équivalent), ce que fait le produit
(**il refuse**, sans mode dégradé), le message exact rencontré, et la commande
de vérification. `docs/CHECKLIST_POSTE_WINDOWS.md` y renvoie.

**Aucun repli affaibli n'a été écrit.** Le module `plateforme.py` refusait
déjà proprement ; il manquait que l'exploitante le sache avant l'incident.

Le message cité dans le guide a été recopié d'une **exécution réelle**
(`os.name` et `sys.platform` forcés), pas réécrit de mémoire.

### 3.2 ⛔ Le constat : les pièces jointes ne sont dans aucune sauvegarde automatique

C'est le point qui vous est adressé, et il est plus grave que de la
documentation.

Le guide disait du service `sauvegarde` : « il fait déjà ce travail », en
parlant de la sauvegarde complète (base + données + objets). Mesuré en
exécutant le planificateur avec des espions :

```
sauver(base, racines_archive, dossier, client_s3=…, retention=…)
  inclure_references_base → absent, donc False
  racines_data           → absent, donc None
  client_documents_s3    → absent, donc None
```

**Le périmètre réel de la sauvegarde automatique est `base` + `corpus`.**
Les pièces jointes des clients — les documents eux-mêmes — vivent dans le
volume Docker de MinIO, **sur le disque de ce poste**, et ne sont dans aucune
copie automatique. Si ce disque meurt, les documents disparaissent ; la base,
elle, serait restaurée avec des références vers des objets absents.

**Ce point est signalé plutôt que corrigé.** Élargir le périmètre de la
sauvegarde est une décision d'exploitation (volume transféré chaque nuit, coût
du stockage hors-site, fréquence), pas un correctif à prendre seul. Le guide
expose les trois options et renvoie la décision par un point d'arrêt
**avant l'étape 8** — c'est-à-dire avant que l'archive réelle n'entre en jeu.

### 3.3 Deux épreuves qui empêchent de reproduire ces deux défauts

| Épreuve | Ce qu'elle verrouille |
|---|---|
| `test_le_guide_n_annonce_pas_plus_que_le_planificateur` | le guide porte une ligne lisible par une machine (`sauvegarde-auto: base corpus`) ; le test exécute le planificateur et exige que l'annonce égale le réel |
| `test_le_guide_de_mise_en_service_annonce_la_limite_windows` | le guide doit nommer `dir_fd`, `/proc/self/fd`, `flock`, dire qu'il n'existe aucun mode dégradé, et donner la commande de vérification |

---

## 4. Priorité 3 — contrôle de bout en bout

### 4.1 Rejoué sur le corpus complet

| Contrôle | Résultat |
|---|---|
| Trajet complet `sauvegarder → verifier → restaurer` sur les 820 fiches | **identique octet à octet**, 3 fichiers, `intégrité : verifiee` |
| File de qualification sur le corpus complet | **817 fiches** à qualifier, réparties par racine sans doublon |
| Unicité des identifiants après complément | 820 / 820 distincts |
| Axes des 123 fiches préexistantes | **0 perdu, 0 modifié** |

### 4.2 Le dossier réel du patron, 7792-SO, en test final

`tests/test_extraction_fiche_reference.py` : **17 épreuves passées**, contre
la vérité terrain de 74 valeurs vérifiées littéralement sur le PDF réel
(`sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf`, 166 990 o).

### 4.3 Contrôle d'accès par rôle, sur PostgreSQL réel

Banc local PostgreSQL 16.2 (serveur réel, pas une simulation) :

| Fichier d'épreuves | Résultat |
|---|---|
| `tests/test_lot1_roles_approbations.py` | **11 passed** — dont `test_auto_approbation_annulation_refus_et_notifications_anti_idor` et la séparation demandeur / approbateur |
| `tests/test_matrice_roles_routes.py` | **9 passed** |
| `tests/test_passe_finale_matrice_roles.py` | **36 passed** |
| **Total** | **56 épreuves passées** |

Aucune auto-approbation, aucune élévation par rôle hérité, refus des rôles non
reconnus : ces comportements sont prouvés par exécution, pas par relecture.

### 4.4 La suite d'épreuves

| Configuration | Résultat |
|---|---|
| Suite complète sans PostgreSQL (socle SQLite) | **2876 passed, 830 skipped, 3 xfailed, 0 failed** |
| Suite complète avec PostgreSQL local | 3407 passed, 39 failed, 27 errors — **aucun dans un fichier touché par ce lot** |

Les 39 échecs et 27 erreurs de la configuration PostgreSQL viennent du banc
local, pas du code : le PostgreSQL embarqué utilisé ici ne fournit ni
`unaccent` ni `pg_trgm` (vérifié : `extension "unaccent" is not available`),
et 27 épreuves démarrent **leur propre** instance PostgreSQL, ce qu'un bac
sans binaire système ne permet pas. **Ce banc n'est donc pas équivalent à la
CI**, et je ne prétends pas l'être : l'autorité est la CI, où ces mêmes
épreuves tournent sur `pgvector/pgvector:pg16` avec les extensions.

---

## 5. Ce qui a été cassé volontairement

Une épreuve qui ne peut pas échouer ne prouve rien. Les trois épreuves
ajoutées par ce lot ont été vérifiées en les faisant **échouer** :

| Épreuve | Cassure injectée | Réaction |
|---|---|---|
| unicité des identifiants | doublon d'`id` dans l'accumulateur | échec nommant les identifiants en double |
| périmètre annoncé = périmètre réel | guide passé à `sauvegarde-auto: base corpus references donnees` | `AssertionError : le guide promet ['donnees', 'references'] …` |
| limite Windows annoncée | `/proc/self/fd` retiré du guide | `AssertionError : le guide ne parle pas de '/proc/self/fd' …` |

---

## 6. La CI au SHA exact

Relevée après achèvement (`status == completed`), jamais annoncée à l'avance :

| SHA | Objet | CI |
|---|---|---|
| `db70fdf` | P0 — sauvegarde hors-git du corpus | 🔴 rouge (2 causes, section 1.1) |
| `0bd099a` | P0 suite — inventaire de sélection | 🟢 **verte** |
| `2b750c6` | P1 — corpus 123 → 820 | 🟢 **verte** |
| `019ec6f` | P2 — limite Windows et guide honnête | 🟢 **verte** |
| `5199062` | P3 — rapport de clôture | 🟢 **verte** |
| `18bb0e9` | P3 — le workflow Windows, dit | 🟢 **verte** |
| `dba1e07` | P3 — 316 n'est pas une approximation de 309 | 🟢 **verte** |
| `c882629` | P3 — fermer la table de CI | 🟢 **verte** |

Les deux runs déclenchés par SHA sont verts dans les huit cas (deux workflows
portent le nom « CI ») ; relevés `completed/success`, aucun job en échec.

**Aucun SHA de ce lot n'a été poussé sans que sa CI soit relevée jusqu'à
`completed`.** Huit commits, huit relevés ; le seul rouge est `db70fdf`, dont
les deux causes sont écrites au §1.1. Le tableau s'arrête au dernier commit du
lot : tout commit postérieur appartient au lot suivant et portera sa propre
ligne.

Reproduire :

```
gh api repos/Ilyes-Neguir/SEAMTECH-search/actions/runs?head_sha=<sha complet> \
  --jq '.workflow_runs[]|select(.name=="CI")|"\(.id) \(.status) \(.conclusion)"'
```

### 6.1 Le workflow « Qualification Windows » est rouge — **et il l'était déjà**

Je ne l'avais pas dit plus haut, le voici. Un second workflow porte sur
Windows, et il est rouge au SHA `5199062`. Ce n'est pas une régression de ce
lot ; c'est un état antérieur, et il a plutôt empiré en sens inverse. Ce que
les étapes disent :

| Étape | `af31922f` (base `main`, 03/11) | `5199062` (ce lot) |
|---|---|---|
| Suite PostgreSQL + Redis — **verdict** | 🔴 échec | 🟢 **succès** |
| Diagnostic — fichiers à processus POSIX | 🔴 échec | 🔴 échec |

Autrement dit : sur la base `main` dont ce lot est parti, il y avait **deux**
étapes rouges ; il en reste **une**. Le verdict — l'étape qui décide si les 37
échecs Windows sont *nommés* dans `ci/ecarts-windows.txt` ou s'ils révèlent
une régression — passe au vert : les 37 échecs sont tous rattachés à une cause
écrite (`scan_ancre_descripteur`, `Lecture confinée du bundle indisponible sur
cette plateforme`, `procfs_fd`, `No module named 'fcntl'`).

Ce qui reste rouge est l'étape de **diagnostic** (16 étapes sur 19) : elle
exécute un par un cinq fichiers d'épreuves qui manipulent des processus POSIX
(`fork`, `SIGKILL`, `killpg`, `multiprocessing`) — des notions que Windows n'a
pas. Quatre des cinq rendent `rc=1` :

```
diag-test_restauration_confinement              → rc=1
diag-test_televersement_creation_idempotente    → rc=1
diag-test_relais_file_base                      → rc=0
diag-test_lot10_televersement_lot               → rc=1
diag-test_extraction_logs_securite              → rc=1
```

Le commentaire du workflow le dit lui-même : cette étape instruit, elle ne
prononce pas de verdict — « dès que la cause est connue, chaque fichier est
soit adapté à Windows, soit écarté NOMMÉMENT avec sa raison. » Ce travail
n'est pas fait. Je ne l'ai pas fait ici : il n'est dans aucune des quatre
priorités du lot 21, et le faire correctement demande d'adapter cinq fichiers
d'épreuves à la sémantique `TerminateProcess`, ce qui est un lot en soi.

**Un fichier d'échec Windows est bien touché par la branche** — mais pas par
ce lot : `tests/test_sauvegarde_snapshot_pg.py` a été modifié pour la dernière
fois au lot 19 (`0bdb90c`) et au lot 17 (`8ac7752`). Vérifié :

```
git log --oneline af31922f..HEAD -- tests/test_sauvegarde_snapshot_pg.py
```

---

## 7. Dette hors périmètre — listée, pas traitée

1. **Année 3 de l'archive.** Les racines couvrent 2024, 2025, 2026. Une
   quatrième année n'est ni détectée ni réclamée nulle part : elle apparaîtra
   dans Dropbox sans que rien ne le signale.
2. **Purge des données clients dans l'historique git.** L'historique du dépôt
   contient des traces qu'on ne peut plus effacer sans réécriture. Une décision
   explicite est requise ; ce n'est pas un correctif.
3. **Détection des accessoires.** Les 162 fiches `ACCESSOIRES` sont comptées
   mais aucune détection automatique ne les distingue des voiles autrement que
   par leur racine.
4. **Épreuve de restauration réelle (R2/S3).** Déjà signalée comme dépendante
   des données réelles ; toujours non prouvée sur le fournisseur de
   production.
5. **Banc Redis.** Épreuves ignorées localement, donc non prouvées ici.
6. **L'étape de diagnostic POSIX du workflow Windows.** Cinq fichiers
   d'épreuves à adapter (`fork`, `SIGKILL`, `killpg`, `multiprocessing`) ou à
   écarter nommément. Antérieur au lot 21 ; voir section 6.1.

---

## 8. Verdict

### **GO PARTIEL** — sous une condition qui n'est pas de mon ressort.

**Ce qui est prêt :**

- le corpus est complet, exhaustif et démontrable : **820 fiches**, relevées
  jusqu'à `has_more: false`, sans doublon ;
- il est sauvegardé hors-site et **restauré à l'identique**, trajet complet
  rejoué sur les 820 fiches ;
- le complément **n'a rien détruit** : 0 axe perdu sur 123, compteurs d'axes
  inchangés ;
- le contrôle d'accès par rôle, l'absence d'auto-approbation et la séparation
  demandeur / approbateur sont **prouvés par exécution sur PostgreSQL réel** ;
- le dossier réel du patron, 7792-SO, passe ses 17 épreuves contre 74 valeurs
  de vérité terrain ;
- la limite Windows est écrite là où l'exploitante la lira ;
- la suite SQLite est intégralement verte (2876 épreuves, 0 échec) et la CI
  est verte aux huit SHA livrés ;
- le workflow Windows n'a **pas régressé** : son verdict passe du rouge au vert
  par rapport à la base `main`, et ce qui reste rouge est une étape de
  diagnostic qui l'était déjà (section 6.1).

**Ce qui n'est pas prêt, et pourquoi ce n'est pas un GO :**

⛔ **Les pièces jointes des clients ne sont dans aucune sauvegarde
automatique**, et le guide affirmait le contraire jusqu'à ce lot. Le poste de
production Windows est le seul point de stockage des documents, et la
sauvegarde automatique n'en emporte aucune copie. Tant que cette décision
n'est pas prise, **l'étape 8 du guide — l'entrée de l'archive réelle — ne doit
pas être franchie.**

**Les trois options**, reprises dans le guide :

1. ajouter `--avec-references` et `--data` à l'automatisme (protection
   complète ; coût : volume nocturne, bande passante, stockage hors-site) ;
2. garder l'automatisme tel quel et rendre la sauvegarde complète obligatoire
   à fréquence définie ;
3. ne rien changer, **si et seulement si** l'archive de l'atelier est
   elle-même sauvegardée ailleurs — elle devient alors la seule vraie copie.

**En une phrase pour l'atelier :** le produit fait ce qu'il doit, sur le
corpus complet, et il le prouve ; ce qui manque n'est pas du code, c'est une
décision sur ce qui doit partir en sauvegarde chaque nuit.
