# Démarrage local — SEAMTECH Search (1 page, 5 commandes)

Poste Windows + Docker Desktop (commanditaire) — Linux en équivalent.
Prérequis : Docker Desktop démarré, Git (Git for Windows suffit), Node.js +
pnpm pour le lanceur quotidien, Python 3.11+ pour les tests.

**Première installation sur le poste (une seule fois)** : double-clic sur
`Installer SEAMTECH Search.cmd`. Le script mesure le poste, prépare `.env`,
`.venv`, `config\config.json`, lance l'application une première fois pour de
vrai, puis dépose l'icône « SEAMTECH Search » sur le bureau et dans le menu
Démarrer. Détails, options et dépannage : `docs/INSTALLATION_POSTE_WINDOWS.md`.

## Les 5 commandes

| # | Windows (PowerShell) | Linux | Ce qui se passe |
|---|---|---|---|
| 1 | `.\scripts\recette_locale.ps1` | `bash scripts/recette_locale.sh` | **Premier lancement / preuve complète** : génère `.env` (7 secrets aléatoires, jamais dans Git), construit l'image MinIO locale (les registres sont morts), démarre la pile, dépose les 7 ZIP du dépôt, vérifie 20 contrôles (dépôt, lots, validation, recherche dimension `6,60`, PDF présigné, rejeu, sauvegarde/restauration, persistance) → **rapport PASS/FAIL + code sortie 0**. Rejouable à volonté. |
| 2 | `.\SEAMTECH Search.cmd` | `docker compose up -d --build` puis ouvrir `http://127.0.0.1:3000` | **Démarrage quotidien** : services PostgreSQL/MinIO/Redis dans Docker, application + interface locales, navigateur ouvert. Connexion : compte nominatif (ex. `recette`, mot de passe dans `.env` → `RECETTE_MOT_DE_PASSE`). |
| 3 | `docker compose ps` | idem | État de la pile : les 5 services doivent être `running`/`healthy`. |
| 4 | `docker compose down` | idem | Arrêt propre — **les données restent** (volumes nommés) : `docker compose up -d` les retrouve. |
| 5 | `.\scripts\backup_postgres.ps1 -DatabaseUrl postgresql://seamtech:<motdepasse>@127.0.0.1:5433/seamtech_search` | `docker compose exec -T postgres pg_dump -U seamtech --format=custom seamtech_search > data/backups/$(date +%F).dump` | **Sauvegarde** dans `data/backups/` (hors Git) ; retour arrière : `.\scripts\restore_postgres.ps1 -BackupFile <fichier> -DatabaseUrl <url>` (la recette prouve l'aller-retour à chaque exécution). |

Le mot de passe PostgreSQL est dans `.env` (`POSTGRES_PASSWORD`) — jamais
commité (`.gitignore`).

## Ce qu'on voit quand tout marche

- `http://127.0.0.1:3000` — connexion nominative puis l'accueil (Nouveau,
  Dossiers, Recherche, Validation, Fichiers…).
- `http://127.0.0.1:9001` — console MinIO (identifiants `.env`).
- Le rapport de la recette : ligne par contrôle `PASS`/`FAIL`, plus
  `data/backups/rapport-recette-*.txt`.

## En cas de pépin

| Symptôme | Cause | Piste |
|---|---|---|
| `docker compose up` refuse une variable | `.env` absent ou incomplet | relancer la commande 1 (elle complète `.env`) |
| `image ... not found` sur MinIO | image jamais construite | commande 1 (construit depuis les sources archivées) |
| Un contrôle FAIL au rapport | détail sur la ligne FAIL | relire le détail, `docker compose logs web frontend` |
| Un port déjà occupé | un autre service écoute (3000/8000/5433/6379/9000/9001) | la recette le dit : libérer le port |
| Chemins avec espaces/accents | — | gérés : les scripts les citent entre guillemets |
| L'icône du bureau ne répond pas | échec au démarrage (Docker arrêté, pnpm absent…) | le message affiche la cause ; détail dans `data\logs\lancement-*.log` |

Vérifications développeurs : `pytest -m "not postgres and not s3 and not perf"`
(puis `-m postgres` avec un PostgreSQL), `pnpm build` dans `frontend/`.
Détails : `scripts/recette_locale.ps1` (rapport), `docs/API.md`,
`docs/verite_terrain/DEMO_BOSS.md` (démonstration 10 minutes).
