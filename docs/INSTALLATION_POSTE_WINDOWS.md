# Installer SEAMTECH Search sur un poste Windows — « propre »

**Objet** : une seule copie du dépôt sur le poste, une commande d'installation,
et une icône sur le bureau. Aucun droit administrateur, aucune donnée perdue en
cas de réinstallation.

Ce guide couvre le lancement **local** : moteur Python et interface Next.js sur
le poste, services PostgreSQL / MinIO / Redis dans Docker. La pile **100 %
Docker** reste documentée dans
[`verite_terrain/MISE_EN_SERVICE.md`](verite_terrain/MISE_EN_SERVICE.md) et
[`deploiement/poste-atelier.md`](deploiement/poste-atelier.md).

---

## 1. Ce qu'il faut sur le poste (une seule fois, avant l'installation)

| Élément | Version | Pour quoi |
|---|---|---|
| Windows 10 22H2 / Windows 11 | 64 bits | Docker Desktop, onnxruntime |
| Mémoire | 8 Go minimum (16 Go conseillés) | OCR + embeddings locaux |
| Disque | 20 Go libres minimum sur le disque du projet | dépendances, index, images |
| **Docker Desktop** | récent, moteur démarré | PostgreSQL, MinIO, Redis |
| **Node.js** | 20 LTS | construction de l'interface |
| **pnpm** | 9.15.x (`npm install -g pnpm@9.15.9`) | lancement de l'interface |
| **Python** | 3.11 (3.10 minimum) | moteur de recherche |
| **Git for Windows** | récent | fournit `bash`, sert à construire l'image MinIO locale |

Le script d'installation **mesure** le poste et dit précisément ce qui manque
(lignes `!!`). Il n'installe jamais rien à votre place et ne demande aucun droit
administrateur.

---

## 2. Une seule copie du dépôt

Copiez le dépôt **en entier** (tous les sous-dossiers : `scripts\`, `frontend\`,
`seamtech_search\`, `config\`, `sample_data\`, …) dans un dossier sans espace
problématique, par exemple :

```text
C:\SEAMTECH-search
```

Gardez **une seule** copie : les données (base PostgreSQL, fichiers déposés)
vivent dans cette copie et dans les volumes Docker. Une deuxième copie du dossier
ne partage rien avec la première.

---

## 3. Installation : une seule commande

Dans le dossier copié, **clic droit** sur :

```text
Installer SEAMTECH Search.cmd  →  Exécuter
```

(Équivalent en ligne de commande :
`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\installer_poste_windows.ps1`)

Laissez la fenêtre ouverte : le premier lancement construit l'interface, cela
prend plusieurs minutes.

### Ce que fait le script, dans l'ordre

1. vérifie que le dossier contient bien une copie **complète** du dépôt ;
2. mesure le poste (Windows, RAM, disque, Docker, Node.js, pnpm, Python, bash) ;
3. prépare les services de données : `.env` avec 7 secrets locaux aléatoires
   (jamais dans Git), image MinIO locale (les registres ne la distribuent plus),
   conteneurs `postgres` / `minio` / `redis` ;
4. prépare le poste : environnement Python `.venv\` + dépendances, puis
   `config\config.json` (dossier à indexer, MinIO, Redis) — jamais dans Git ;
5. **lance l'application pour de vrai** et vérifie qu'elle répond ;
6. propose de créer le **premier compte nominatif** (mot de passe masqué, jamais
   affiché, jamais écrit, jamais passé en argument de ligne de commande) ;
7. dépose l'**icône** : raccourci `SEAMTECH Search` sur le Bureau et dans le
   menu Démarrer, plus `Arreter SEAMTECH Search` dans le menu Démarrer ;
8. affiche un récapitulatif (adresse, connexion, arrêt, sauvegarde, journaux).

### Les deux questions posées

| Question | Réponse par défaut | Signification |
|---|---|---|
| Dossier(s) à indexer | `sample_data` du projet | dossier(s) dont les PDF sont recherchables ; plusieurs dossiers séparés par `;` |
| Créer le premier compte ? | Oui | identifiant + mot de passe pour se connecter à l'interface |

### Ce qui est créé sur le poste

| Chemin | Contenu |
|---|---|
| `.env` | secrets locaux (PostgreSQL, MinIO, Redis, session) — hors Git |
| `config\config.json` | dossier indexé, MinIO, Redis — hors Git |
| `.venv\` | environnement Python du projet |
| `data\` | index, journaux (`data\logs\lancement-*.log`), identifiants de processus (`data\pids\`) — hors Git |
| Bureau + menu Démarrer | raccourcis « SEAMTECH Search » et « Arreter SEAMTECH Search » |

### Options

```powershell
scripts\installer_poste_windows.ps1 -DemarrageAutomatique   # démarre à l'ouverture de session
scripts\installer_poste_windows.ps1 -SansCompte             # ne pas créer de compte maintenant
scripts\installer_poste_windows.ps1 -SansLancement          # installer sans lancer l'application
scripts\installer_poste_windows.ps1 -Oui                    # aucune question (réponses par défaut ;
                                                      # aucun compte créé : un mot de passe ne se devine pas)
```

---

## 4. Utiliser l'icône

Double-clic sur l'icône **SEAMTECH Search** du bureau :

- si l'application ne tourne pas, elle démarre (moteur, interface) puis le
  navigateur s'ouvre sur `http://127.0.0.1:3000` ;
- si elle tourne déjà, le navigateur s'ouvre directement.

Connexion : l'identifiant et le mot de passe créés à l'installation. Sans compte
nominatif, seul le **compte de secours** entre — identifiant `secours`, mot de
passe `SEAMTECH_UI_PASSWORD` dans le fichier `.env` du projet.

Créer d'autres comptes (ou gérer les sessions) :

```powershell
.venv\Scripts\python -m seamtech_search.comptes.cli creer --identifiant imrane --nom "I. N." --role administrateur
.venv\Scripts\python -m seamtech_search.comptes.cli lister
.venv\Scripts\python -m seamtech_search.comptes.cli desactiver --identifiant imrane
```

Le mot de passe est toujours lu sur l'entrée standard, jamais en argument.

---

## 5. Arrêter proprement

Menu Démarrer → **SEAMTECH Search** → *Arreter SEAMTECH Search*.

Arrête l'interface et le moteur (jamais les données). Les conteneurs Docker de
données peuvent rester arrêtés aussi :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\arreter_seamtech.ps1 -AvecServices
```

Aucune donnée n'est supprimée : ni `docker compose down -v`, ni vidage de
volume, ni suppression de fichier du projet. Le double-clic suivant retrouve
tout.

---

## 6. Sauvegarde

```powershell
powershell -File scripts\backup_postgres.ps1 -DatabaseUrl postgresql://seamtech:<POSTGRES_PASSWORD du .env>@127.0.0.1:5433/seamtech_search
```

Le fichier `.dump` est écrit dans `data\backups\` (hors Git). Restauration :

```powershell
powershell -File scripts\restore_postgres.ps1 -BackupFile data\backups\<fichier>.dump -DatabaseUrl <même url>
```

La recette locale prouve l'aller-retour sauvegarde → restauration à chaque
exécution (`scripts\recette_locale.ps1`).

---

## 7. Réinstaller, mettre à jour, désinstaller

- **Réinstaller / mettre à jour** : recopier le dépôt (ou `git pull`) puis
  relancer `Installer SEAMTECH Search.cmd`. `.env`, `config\config.json`, les
  volumes Docker et `data\` sont réutilisés : aucune donnée n'est perdue, les
  raccourcis sont réécrits.
- **Désinstaller** : supprimer les raccourcis (Bureau + menu Démarrer), puis
  `docker compose down` **sans** `-v`, puis supprimer le dossier du projet.

---

## 8. Dépannage

| Symptôme | Cause probable | Piste |
|---|---|---|
| Ligne `!!` « Docker » | Docker Desktop arrêté ou absent | lancer Docker Desktop, puis relancer l'installation |
| Ligne `!!` « pnpm » | pnpm non installé | `npm install -g pnpm@9.15.9` après Node.js |
| Ligne `!!` « bash (Git) » | Git for Windows absent | l'installer (l'image MinIO locale se construit avec `bash`) |
| Ligne `!!` « copie du depot » | copie incomplète | recopier le dépôt en entier |
| « l'interface ne repond pas » | premier build échoué | lire `data\logs\lancement-*.log` |
| L'icône ne répond pas / message d'erreur | échec au démarrage | la bulle affiche le détail ; journal dans `data\logs\` |
| Port 3000 déjà pris | un autre service écoute | l'application bascule automatiquement sur 3001 |
| Écran de connexion refusé | aucun compte, secours vide | créer un compte (§4) ou renseigner `SEAMTECH_UI_PASSWORD` dans `.env` |

---

## 9. Ce que l'installation ne fait pas

- elle n'installe **pas** Docker Desktop, Node.js, pnpm, Python ou Git (elle dit
  ce qui manque et s'arrête) ;
- elle ne modifie **rien** hors du dossier du projet, du Bureau et du menu
  Démarrer ;
- elle ne touche **pas** aux dossiers à indexer (lecture seule) ;
- elle ne publie **rien** sur le réseau : tout reste sur `127.0.0.1`.
