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

## 4. Aucun transfert en masse n'est possible vers l'environnement

Mesuré, pas supposé :

| Hôte | Résultat |
|---|---|
| `codeload.github.com` | **accessible** — 8 297 646 octets téléchargés |
| `github.com` (archive) | **accessible** — redirige vers codeload |
| `objects.githubusercontent.com` | **refusé** — `SSL_ERROR_SYSCALL` |
| `dl.dropboxusercontent.com` | **refusé** — `SSL_ERROR_SYSCALL`, `code_http=000`, 0 octet |

Le connecteur Dropbox lit un fichier à la fois et rend son **contenu**, sans
jamais l'écrire dans l'environnement de travail. Aucun outil du connecteur ne
fait le trajet Dropbox → espace de travail.

**Conséquence** : la seule voie de transfert en masse passerait par un dépôt
GitHub. Elle a été proposée et écartée par décision explicite.

## 5. Conséquence sur le périmètre

Le relevé se fait dossier par dossier. Chaque mesure est consignée dans
`donnees/etape0/accumulateur.json` (outil : `scripts/etape0_corpus.py`), qui
ne contient **aucune donnée nominative** : le chemin Dropbox sert à fabriquer
une empreinte et n'est jamais écrit.

État au moment de la rédaction :

- 7 fiches consignées, toutes de provenance `archive` (extraction réelle des
  7 archives locales), aucune encore relevée depuis Dropbox ;
- `SELECT count(*) FROM fiche` → **7**, très loin des ~338 attendus ;
- le dénombrement réel des dossiers Dropbox reste à établir.

**Le blocage est nommé ici avec sa cause exacte** : non pas « pas assez de
données », mais « le corpus est accessible à la lecture unitaire seulement, et
chaque lecture coûte une place finie dans la session ; à ce rythme, plusieurs
centaines de dossiers excèdent ce qu'une session peut absorber ». La levée
passe par un transfert en masse, dont la seule voie mesurée comme praticable
est un dépôt GitHub.
