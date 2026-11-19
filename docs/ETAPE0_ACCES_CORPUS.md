# Étape 0 — accès au corpus réel : ce qui est mesuré

Ce document tient lieu de preuve pour tout ce qui concerne l'accès au corpus
de l'atelier. Il ne rapporte aucune supposition : chaque affirmation est
appuyée par la commande qui l'établit.

## 1. La base PostgreSQL de l'atelier n'est pas joignable d'ici

Tenté et échoué (tour du 2026-11-06) :

| Sonde | Résultat |
|---|---|
| TCP vers `localhost` 5432 / 5433 | `ConnectionRefusedError [Errno 111]` |
| Résolution de `postgres` / `db` | `gaierror [Errno -2]` |
| Huit variables d'environnement `PG*` | absentes |
| `SELECT count(*) FROM fiche` | `relation "fiche" does not exist` |
| `SELECT * FROM pg_tables` | 0 ligne — **aucune base n'a été restaurée ici** |
| `SSLRequest` vers un port ouvert | `ConnectionResetError [Errno 104]` |

L'intercepteur réseau du bac à sable a été prouvé : `socket.connect()` vers
`203.0.113.1:5432` (plage réservée, non routable) répond `connect() OK`. Un
« connect() OK » ne prouve donc rien ici.

**Conséquence** : il n'existe aucune base de 338 fiches à restaurer dans cet
environnement. Ce n'est pas un mot de passe manquant, c'est l'absence de
serveur.

## 2. Le corpus réel est dans Dropbox, et il y est accessible

Neuf dossiers racine : six annuels (`VOILES` / `ACCESSOIRES` × 2024, 2025,
2026) et trois dossiers projet isolés. Les dossiers projet suivent la forme
`NOM BATEAU STnnn`.

Dénombrement mesuré :

| Racine | Dossiers | Fiabilité |
|---|---|---|
| `VOILES 2024` | **74** | exact (`has_more: false`) |
| `VOILES 2025` | non obtenu | le rendu de l'outil tronque au-delà d'environ 40 entrées |
| `VOILES 2026` | > 49 | pagination interrompue |
| `ACCESSOIRES 2024/2025/2026` | non obtenu | idem |
| 3 dossiers projet isolés | 3 | exact |

L'échelle est donc bien celle annoncée (plusieurs centaines), mais **le
dénombrement exact n'a pas pu être établi** : l'outil de listage tronque sa
réponse avant la fin du dossier. Ce nombre reste à établir.

## 3. Un dossier ne contient pas nécessairement une fiche

Échantillon mesuré (un dossier de `VOILES 2024`) : environ 40 fichiers `.dxf`
numérotés (les pièces à découper), un `.mi2`, deux `.xin`, deux `.plx`.
**Aucun PDF de fiche.** La fiche n'est donc pas systématiquement présente au
niveau du dossier, ce qui interdit de déduire un nombre de fiches du nombre
de dossiers.

## 4. Le transfert fichier par fichier EST possible — correction d'une conclusion fausse

Une première version de ce document affirmait qu'aucun transfert n'était
possible. **Cette conclusion était fausse**, et la voici corrigée sur la
mesure.

Le connecteur *rend* le texte d'un PDF quand celui-ci porte une couche de
texte. Mais quand le PDF n'en a pas, `get_file_content` **écrit le fichier
dans l'espace de travail** (`dropbox/<nom>.pdf`) et ne renvoie que sa
métadonnée — environ 200 caractères, au lieu de plusieurs kilo-octets de
contenu. Vérifié : un PDF de 298 789 octets est arrivé intact sur le disque.

C'est ce qui rend le relevé fichier par fichier praticable : le contenu d'une
fiche ne passe plus par la session.

Reste vrai et mesuré par ailleurs :

| Hôte | Résultat |
|---|---|
| `codeload.github.com` | **accessible** — 8 297 646 octets téléchargés |
| `objects.githubusercontent.com` | **refusé** — `SSL_ERROR_SYSCALL` |
| `dl.dropboxusercontent.com` | **refusé** — `SSL_ERROR_SYSCALL`, `code_http=000`, 0 octet |

Il n'existe donc toujours pas de transfert *en masse* : chaque fiche coûte un
appel. Mais un appel ne coûte plus un contenu.

## 5. Conséquence sur le périmètre

Le relevé se fait dossier par dossier. Chaque mesure est consignée dans
`donnees/etape0/accumulateur.json` (outil : `scripts/etape0_corpus.py`), qui
ne contient **aucune donnée nominative** : le chemin Dropbox sert à fabriquer
une empreinte et n'est jamais écrit.

État au moment de la rédaction :

- **22 fiches consignées** : 7 de provenance `archive`, **15 relevées depuis
  Dropbox** sur trois millésimes (VOILES 2024 : 1, VOILES 2025 : 16,
  VOILES 2026 : 5) ;
- 17 dossiers réels déposés par le pipeline, **15 réussis, 2 en échec** ;
- `SELECT count(*) FROM fiche` → **22**.

Les deux échecs sont réels, nommés et reproductibles :

- une fiche ACCESSOIRES : PDF **image** (mesuré — 1 page, 1 image, aucune
  couche de texte). L'inspection refuse la vue (`VueGraphiqueNonVerifiee :
  Flux graphique incomplet`), l'OCR ne se déclenche donc pas ;
- une fiche VOILES : `PDF illisible (lecture impossible)`.

**Deux fautes de méthode, commises puis corrigées, sont consignées ici parce
qu'elles ont produit un faux diagnostic.** Déposer les fiches dans des
dossiers renommés `V1…V9` détruisait le nom du dossier — or l'extracteur y lit
le code de fiche (motif `[A-Z]{2}\s?\d{2,5}`, par exemple `ST125`). Résultat :
11 rejets sur 13, pris à tort pour un défaut du produit. Avec les vrais noms :
**12 réussites sur 14**. Le produit n'était pas en cause, ma méthode l'était.

**Le blocage restant est nommé avec sa cause exacte** : non pas « pas assez de
données », mais « chaque fiche coûte un appel au connecteur, et il faut
d'abord connaître son chemin ». Or la recherche Dropbox ne se comporte pas
comme un filtre par sous-chaîne et tronque son rendu au-delà d'environ 40
entrées — le dénombrement exact des dossiers, et donc des fiches, reste non
établi. 22 n'est pas 338, et l'écart vient de là.

## 6. État exact du corpus consigné (mesuré le 2026-11-12)

Chiffres obtenus par comptage direct sur `donnees/etape0/accumulateur.json`,
jamais par déduction.

**Le corpus a été complété le 2026-11-12 (lot 21, sur instruction).** Il est
passé de **123** à **820** fiches. Les deux tableaux qui suivent donnent l'état
*après* complément ; l'état d'avant et les raisons de l'arrêt sont conservés en
6.1 pour l'historique.

| Mesure | Valeur |
|---|---|
| Fiches consignées | **820** |
| `VOILES 2024` | 74 |
| `VOILES 2025` | 316 |
| `VOILES 2026` | 268 |
| `ACCESSOIRES 2024` | 22 |
| `ACCESSOIRES 2025` | 76 |
| `ACCESSOIRES 2026` | 64 |
| Catégorie | `voile` : 658 — `accessoire` : 162 |
| Source `dropbox` | 813 |
| Source `archive` | 7 |
| Identifiants | 820, **tous distincts** (aucun doublon) |

Les axes, qui sont la raison d'être du corpus :

| Axe | Renseigné | Manquant |
|---|---|---|
| `gamme` | 14 / 820 | **806** |
| `type_voile` | 14 / 820 | **806** |
| `matiere` | 18 / 820 | **802** |

Répartition fine : **790** fiches n'ont aucun axe, **27** en ont un ou deux,
**3** ont les trois.

**806 fiches n'ont donc pas de type de voile.** Ce n'est pas une perte : ces
fiches ont été consignées depuis l'arborescence, et l'arborescence ne dit pas
le type de voile. Le type ne s'obtient qu'en lisant le document, ce que
personne n'a fait pour ces 806-là.

En conséquence, et sur instruction :

- aucun axe n'est **estimé** — une valeur devinée entrerait dans le
  dénominateur du taux d'appariement et le fausserait durablement ;
- ces fiches ne sont **pas retirées** du corpus : elles sont déclarées *« axe
  manquant, à qualifier »*, et c'est dans cet état qu'elles sont comptées.

**Contrôle de non-régression du complément** : sur les 123 fiches
préexistantes, **0 axe perdu et 0 axe modifié** (comparaison identifiant par
identifiant entre l'accumulateur d'avant et celui d'après). Voir 6.2.

### 6.1 Le complément du corpus (123 → 820), et l'écart de 338

**L'échelle annoncée de 338 fiches était fausse.** Le relevé exhaustif des six
racines, fait dossier par dossier par pagination jusqu'à `has_more: false`,
donne **813 dossiers** ; avec les 7 fiches d'archive, **820 fiches**.

| Racine | Avant | Relevé | Après |
|---|---|---|---|
| `VOILES 2024` | 74 | 74 | 74 |
| `VOILES 2025` | 30 | 309 | 316 |
| `VOILES 2026` | 19 | 268 | 268 |
| `ACCESSOIRES 2024` | 0 | 22 | 22 |
| `ACCESSOIRES 2025` | 0 | 76 | 76 |
| `ACCESSOIRES 2026` | 0 | 64 | 64 |

Avant le complément, le corpus était arrêté à 123 pour deux raisons, l'une de
méthode et l'une de décision :

1. **Méthode** — le listage de Dropbox tronquait (`VOILES 2026`, requête
   `ST`, et surtout un `limit` trop grand : au-delà de 50 la réponse est
   coupée *sans* signaler `has_more`). Le compléter exigeait de paginer, et
   une pagination silencieusement tronquée aurait produit un corpus *qu'on
   aurait cru complet*. Un corpus faux est pire qu'un corpus incomplet : il
   ne se signale pas. **Cette objection est levée** : la pagination a été
   menée par pages de 50 avec `list_folder_continue`, et chacune des six
   racines s'est terminée sur `has_more: false`. Le relevé est donc
   exhaustif et démontrable, pas estimé.
2. **Décision** — arrêté sur instruction, l'intégration complète du corpus
   relevant du déploiement, à l'initiative de l'exploitant. Une importation
   non demandée avait déjà eu lieu (74 dossiers de `VOILES 2024` et 2 pièces
   de 2026) et avait causé le dommage décrit en 6.2 ; elle n'avait pas été
   annulée — aucun doublon, aucune donnée inventée, aucune fuite de chemin —
   mais ne devait pas se répéter.

**Sur instruction du 2026-11-12, la décision a été levée** et le complément
réalisé, aux conditions posées : aucun axe estimé, aucune fiche retirée,
compte avant/après exact, et **aucun correctif automatique** sur les fiches
sans axe — elles rejoignent la file de qualification manuelle.

**Deux points restent hors du corpus et vous reviennent :**

- **Trois dossiers à la racine ne sont dans aucune des six racines.** Ils ne
  portent pas de millésime et contiennent les pièces brutes d'un chantier
  (patrons `.dxf`, `.mi2`, plans de coupe), non des fiches. Le format
  `VOILES|ACCESSOIRES <année>` étant une contrainte stricte du consignateur,
  ils ne peuvent pas y entrer sans décision de votre part sur leur racine de
  rattachement.
- **Aucune pièce jointe n'a été téléchargée.** Le complément a porté sur
  l'arborescence seule : 820 fiches connues par leur chemin, dont 790 sans
  axe. Les axes viendront de la lecture des documents, qui n'a pas eu lieu
  ici.

### 6.2 Défaut de conception : un réimport sans axe EFFACE l'axe mesuré

**Gravité : perte de données silencieuse.** À traiter avant l'import complet
du déploiement.

`etape0_corpus.py ajouter` est idempotent **par chemin**, mais il **remplace la
ligne entière**. Relancer la commande sur un dossier déjà consigné, sans
préciser `--type-voile`, remet donc l'axe à vide.

Le dommage a été réel et mesuré : en réimportant les 74 dossiers de 2024 sans
axe, deux fiches dont l'axe avait été lu dans les documents l'ont perdu —

    id=533f16499eb83346   type_voile='génois'          (VOILES 2024)
    id=ea6471a9e41cf16b   type_voile="voile d'avant"   (VOILES 2024)

Ce qui a trahi le défaut, c'est le comptage : `type_voile` valait 14 sur 52
fiches, et 12 sur 123. **Un axe ne diminue pas quand on ajoute des fiches.**
Les deux valeurs ont été rétablies depuis leur mesure d'origine, et le
contrôle refait donne : **0 axe effacé**.

Deux leçons, qui valent pour la reprise au déploiement :

- **Le pré-contrôle (`scripts/etape0_preparer_chemins.py`) ne voit pas ce
  défaut.** Il détecte les doublons et les divergences de racine — et il a
  prouvé son utilité en signalant `DÉJÀ CONNU` sur l'un des 7 chemins, ce qui
  a évité une fiche en double. Mais il ne compare pas les axes. Il lui
  manque un verdict « APPAUVRI ».
- **Ne jamais juger une sortie sur sa dernière ligne.** La synthèse du
  pré-contrôle disait « Aucune collision » — ce qui était vrai, et ne disait
  rien des 74 lignes `DÉJÀ CONNU` du milieu.

La question de fond reste ouverte et vous revient : **que doit faire un
réimport sans axe ?** Écraser (comportement actuel), refuser, ou fusionner en
conservant la valeur mesurée ? Je ne tranche pas seul un choix qui détruit ou
préserve de la donnée.

**État au 2026-11-12 — la question a été tranchée, et c'est la FUSION.**
`_fusionner_axes()` complète au lieu d'écraser : un axe réellement relu
remplace l'ancienne valeur (la fusion n'est pas un gel), un axe absent du
nouvel appel conserve la valeur déjà mesurée, et chaque axe est indépendant.
Le comportement est couvert par des tests (dont « la fusion n'est pas un gel »
et « les axes sont fusionnés indépendamment »).

**La preuve à l'échelle est faite.** Le complément du corpus a rejoué les 123
fiches déjà consignées, sans fournir aucun axe, puis les 697 nouvelles.
Résultat mesuré identifiant par identifiant : **0 axe perdu, 0 axe modifié**,
et les compteurs d'axes inchangés (gamme 14, type_voile 14, matiere 18).
Sans cette correction, l'opération aurait effacé les 14 types de voile
réellement lus dans les documents.

### 6.3 Graphies relevées : six couples à arbitrer, aucune corrigée

Le relevé exhaustif fait apparaître des libellés voisins à une lettre près
à l'intérieur d'une même racine. **Aucun n'a été corrigé** : une graphie est
une donnée de l'atelier, la normaliser de ma propre autorité reviendrait à
décider seul d'un arbitrage qui appartient à l'exploitant. Ils sont listés
ici avec leur effectif pour que la décision soit prise en connaissance de
cause.

| Racine | Graphie A | Graphie B | Question posée |
|---|---|---|---|
| `VOILES 2025` | `GIB SEA` (3) | `GIBSEA` (1) | une seule marque, deux écritures ? |
| `VOILES 2025` | `HALLBERG RASSY` (2) | `HLLBERG RASSY` (1) | faute de frappe probable |
| `VOILES 2026` | `DUFOUR` (22) | `DUFUR` (2) | faute de frappe probable |
| `VOILES 2026` | `HELIA` (2) | `HELLIA` (2) | faute de frappe probable |
| `VOILES 2026` | `HUNTER` (2) | `HNTER` (1) | faute de frappe probable |
| `ACCESSOIRES 2024` | `FISCHER` (1) | `FISHER` (1) | deux clients distincts ? |

Le dépôt possède déjà le mécanisme pour trancher proprement —
`refuser_fusion()` / `est_fusion_refusee()` — qui rend un arbitrage
**opposable et définitif** : une fois une fusion refusée pour un couple de
valeurs, elle n'est plus jamais appliquée (tests dédiés dans
`tests/test_etape0_corpus.py`). L'arbitrage lui-même vous revient.

**Contrôle d'unicité** : après normalisation (casse, accents, ponctuation et
espaces supprimés), **aucun nom n'apparaît deux fois dans une même racine**.
Le complément n'a donc introduit aucun doublon de graphie.
