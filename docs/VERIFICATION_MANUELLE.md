# Vérification manuelle de l'application

Ce document explique comment éprouver SEAMTECH **tel qu'il est servi**, et
pourquoi cela existe en plus des 3 200 épreuves automatisées.

## Pourquoi un second jeu d'épreuves

Les tests automatisés appellent l'application de l'intérieur (`TestClient`),
avec des bases que leurs fixtures ont elles-mêmes construites. Ils sont
indispensables, mais ils ne peuvent pas voir :

- le serveur réellement démarré, son routage et son ordre des middlewares ;
- l'authentification par jeton de service **puis** par session nominative ;
- l'ordre réel des opérations d'un utilisateur ;
- une base que l'opérateur a remplie lui-même.

Un import qui réussit dans un test peut très bien ne rien déposer dans une
vraie base. Seule une vérification par HTTP, contre un processus vivant, le
montre.

## Lancer l'application

Le point d'entrée est `seamtech_search.api.create_app`, qui attend une
configuration — il ne s'agit donc pas d'une fabrique sans argument.

```bash
export SEAMTECH_DATABASE_URL="postgresql://utilisateur:motdepasse@hote:5432/base"
export SEAMTECH_ROOT_PATHS="/chemin/vers/les/dossiers/a/importer"
export SEAMTECH_TOKEN="un-jeton-de-service"

python - <<'PY'
import os, pathlib, uvicorn
from seamtech_search.api import create_app
from seamtech_search.config import AppConfig

cfg = AppConfig(
    root_paths=[pathlib.Path(os.environ["SEAMTECH_ROOT_PATHS"])],
    database_path=pathlib.Path("/tmp/app-index.db"),
    database_url=os.environ["SEAMTECH_DATABASE_URL"],
    host="0.0.0.0", port=8000,
    allow_network_access=True,          # exigé dès que host n'est pas local
    auth_token=os.environ["SEAMTECH_TOKEN"],
    behind_tls_proxy=True,               # exigé avec un jeton hors TLS local
)
uvicorn.run(create_app(cfg), host="0.0.0.0", port=8000)
PY
```

Trois garde-fous de configuration se déclenchent dans l'ordre, et chacun dit
quoi faire — les retenir évite trois allers-retours :

| Situation | Message | Réponse |
|---|---|---|
| `host` non local | `non-local host requires allow_network_access=true` | ajouter `allow_network_access=True` |
| accès réseau sans jeton | `auth_token is required when allow_network_access is enabled` | fournir `auth_token` |
| jeton sans TLS | `token authentication over non-local host requires behind_tls_proxy=true` | passer derrière un proxy TLS (recommandé) |

## Lancer la vérification

```bash
python scripts/verification_manuelle.py \
    --base http://127.0.0.1:8000 \
    --jeton "$SEAMTECH_TOKEN"
```

Sortie : une ligne par épreuve, puis un bilan. **Le code de sortie vaut 1 si
une seule épreuve échoue**, ce qui permet de s'en servir comme porte :

```bash
python scripts/verification_manuelle.py --base ... --jeton ... || echo "non conforme"
```

## Ce qui est éprouvé

| Parcours | Ce qu'il constate |
|---|---|
| **Socle** | la recherche répond ; un compte est rendu ; un faux jeton est refusé (401) |
| **Dépôt** | les fiches déposées sont cherchables et rendent un extrait |
| **Plein texte** | un mot réellement présent dans le corpus est retrouvé |
| **Facettes** | les axes sont rendus ; filtrer par une valeur de facette la retrouve |
| **Fiche** | détail, champs, historique, pièces, anomalies |
| **Validation** | session nominative, puis `a_valider` → `valide` |
| **Conflit de révision** | valider avec une révision périmée est refusé (409) et ne valide pas |

### Deux pièges que ces épreuves ont déjà rencontrés

**Les anomalies n'ont pas de route dédiée.** Elles voyagent dans le détail de
la fiche (`GET /fiches/{code}` → clé `anomalies`), parce que c'est là que
l'approbateur en a besoin, au moment de décider. Un 404 sur
`/fiches/{code}/anomalies` n'est pas un défaut : c'est l'épreuve qui visait
une route inexistante.

**Valider exige une session nominative**, de rôle approbateur. Le jeton de
service ne suffit pas, et c'est voulu : une décision doit être attributable.
L'épreuve ouvre donc une session (`POST /auth/connexion`) puis transmet
`X-SEAMTECH-SESSION` et `X-SEAMTECH-SESSION-JETON`. Les identifiants viennent
de `SEAMTECH_VERIF_IDENTIFIANT` / `SEAMTECH_VERIF_MOT_DE_PASSE` s'ils sont
définis, sinon de ceux que l'amorçage e2e crée. **Aucun mot de passe réel
n'est écrit dans ce dépôt.**

## Ce que cette vérification ne prouve pas

Elle ne remplace pas la suite automatisée, elle la complète. En particulier :

- elle ne prouve pas les performances (pas de mesure p95 ici) ;
- elle ne prouve pas le rendu du navigateur (c'est le rôle de Playwright) ;
- **elle ne peut pas prouver le comportement lié à `unaccent` et `pg_trgm`**
  si le PostgreSQL employé est fourni par `pgserver`, qui est distribué sans
  les modules contrib. Sur un tel poste, `q=regate` ne trouve pas « Régate »
  alors qu'en production il la trouve. Ce n'est pas un défaut de
  l'application : c'est une limite du moteur employé pour l'essai.
