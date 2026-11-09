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
