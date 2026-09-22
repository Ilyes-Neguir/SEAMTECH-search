# Mise en service — UN chemin validé (Lot H.1)

Statut au 22/09/2026 : le chemin retenu et PROUVÉ est `docker compose`
(rejoué à chaque CI par le job `integration` : démarrage, santé, dépôt, fiche
visible). Les scripts PowerShell (`scripts/*.ps1`) restent fournis comme
REPLI documenté (section 4) — ils n'ont pas pu être exécutés depuis
l'environnement de développement (Linux) : ce qui n'est pas testé est dit
explicitement ci-dessous.

## 1. Chemin unique validé : docker compose (reproductible depuis un clone vierge)

Prérequis : Docker + Docker Compose v2, git. (Mesuré en CI sur ubuntu-latest ;
sur Windows : Docker Desktop — voir section 4.)

```bash
# 1. Récupérer le code
git clone https://github.com/Ilyes-Neguir/SEAMTECH-search.git
cd SEAMTECH-search

# 2. Configuration — trois secrets OBLIGATOIRES (compose refuse de démarrer sans eux)
cp .env.example .env
# puis éditer .env et fixer :
#   SEAMTECH_AUTH_TOKEN      (token API — chaîne longue aléatoire)
#   SEAMTECH_UI_PASSWORD     (mot de passe opérateur de l'interface)
#   SEAMTECH_SESSION_SECRET  (secret de session — chaîne longue aléatoire)

# 3. Démarrage
docker compose up -d --build
```

Sortie attendue : `docker compose ps` montre `postgres`, `minio`, `redis`,
`web`, `frontend` en `running (healthy)` après la montée (les healthchecks de
compose font foi ; le job CI `integration` attend exactement cet état).

```bash
# 4. Santé — l'unique route sans authentification
curl -s http://127.0.0.1:3000/api/health
# attendu : un payload JSON de vivacité (statut, sans comptage d'archive)

# 5. Santé API (avec le token)
curl -s -H "X-SEAMTECH-TOKEN: <SEAMTECH_AUTH_TOKEN>" http://127.0.0.1:8000/health
```

```bash
# 6. Dépôt d'un dossier réel puis fiche visible
#    Interface : http://127.0.0.1:3000 (connexion avec SEAMTECH_UI_PASSWORD),
#    écran Dépôt → choisir un dossier de fiches PDF → attendre la fin du
#    traitement → la fiche apparaît dans la file de validation.
#    (CLI équivalent : voir docs/API.md — `cli depot`.)
```

Sortie attendue : la fiche entre en statut `a_valider` (RG3 : jamais validée
automatiquement), ses champs extraits sont affichés, son PDF est rendu à
droite. C'est exactement ce que le job CI `integration` rejoue avec la vraie
fiche 7792-SO.

## 2. Contrôle quotidien (une commande)

```bash
curl -f -s http://127.0.0.1:3000/api/health > /dev/null && echo "service vivant" || echo "service MORT"
```

Si « service MORT » : QUE_FAIRE_SI.md §1.

## 3. Ce qui est prouvé et ce qui ne l'est pas

| Élément | Prouvé par | Statut |
|---|---|---|
| `docker compose up -d --build` depuis un clone vierge | job CI `integration` (chaque run) | ✅ prouvé |
| santé web + frontend + dépôt + fiche visible | job CI `integration` + e2e | ✅ prouvé |
| sauvegarde hors-site + restauration après destruction | job CI `sauvegarde` (Lot H.1) | ✅ prouvé |
| rotation des journaux bornée | `logging` json-file 10 Mo × 5 dans docker-compose.yml | ✅ configuré, observé en CI |
| exécution sur Windows / PowerShell | — | ❌ **non testé** (environnement de développement Linux) |
| montée de version Docker Desktop sous Windows | — | ❌ non testé |

## 4. Repli Windows (documenté, NON testé — checklist à exécuter par le commanditaire)

Les scripts `scripts/backup_postgres.ps1`, `restore_postgres.ps1`,
`backup_sqlite.ps1`, `restore_sqlite.ps1` sont conservés mais MINIMAUX
(pg_dump local sans manifeste ni hors-site) : sur Windows comme ailleurs, la
sauvegarde de référence est `python -m seamtech_search.sauvegarde` (portable,
éprouvée en CI). Checklist Windows à cocher par le commanditaire :

- [ ] Docker Desktop installé ; `docker compose version` répond
- [ ] `git clone` + `cp .env.example .env` + les 3 secrets fixés
- [ ] `docker compose up -d --build` termine sans erreur
- [ ] `docker compose ps` : 5 services `healthy`
- [ ] `curl http://127.0.0.1:3000/api/health` répond (depuis PowerShell : `Invoke-WebRequest http://127.0.0.1:3000/api/health`)
- [ ] un dossier réel déposé via l'écran Dépôt produit une fiche `a_valider` visible
- [ ] une sauvegarde `python -m seamtech_search.sauvegarde sauver …` écrit dump + manifeste

Chaque case cochée = une ligne de ce qui EST prouvé sur le poste cible ;
reporter le résultat dans ce document (ou « échoué + sortie brute »).
