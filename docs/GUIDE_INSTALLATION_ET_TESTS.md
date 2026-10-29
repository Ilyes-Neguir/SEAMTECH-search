# SEAMTECH Search — Guide d'installation et de test, pas à pas

Public : le poste de l'atelier (Windows 10 22H2 / Windows 11, 64 bits) et, à l'identique,
un poste Linux. Aucune connaissance de développement requise pour les phases 1 à 6.

État du logiciel au moment de ce guide : `main` = commit **`9009676`** (fusion de la PR #42).
Les chiffres de référence cités plus bas ont tous été mesurés sur ce commit (voir
`docs/RAPPORT_PASSE_FINALE_2026-10-29.md` §2). Ce guide **ne remplace pas** la mesure :
à chaque étape, le résultat attendu est donné pour que vous puissiez le comparer vous-même.

> Règle absolue : **un document client ne se versionne pas**. `data/` et `.env` sont dans
> `.gitignore` — ne les forcez jamais (`git add -f`) et ne les copiez jamais dans un commit,
> un ticket ou une capture d'écran.

---

## Phase 0 — Ce qu'il faut sur le poste

| Élément | Exigence | Comment vérifier |
|---|---|---|
| Système | Windows 10 22H2 ou Windows 11, 64 bits (ou Linux récent) | `winver` |
| RAM | 8 Go minimum, 16 Go confortable | Gestionnaire des tâches |
| Disque libre | 50 Go (images Docker + archives + sauvegardes) | Explorateur |
| Docker | Docker Desktop avec backend **WSL 2** démarré | `docker info` sans erreur |
| Docker Compose | version 2 (`docker compose`, pas `docker-compose`) | `docker compose version` |
| Git | client Git pour Windows | `git --version` |
| Ports libres | **3000, 8000, 5433, 6379, 9000, 9001** | `netstat -ano \| findstr ":3000 "` |

Détail complet (checklist imprimable, actions correctives par symptôme) :
`docs/CHECKLIST_POSTE_WINDOWS.md`.

---

## Phase 1 — Récupérer le logiciel

```powershell
cd C:\
git clone <adresse-du-dépôt> seamtech-search
cd seamtech-search
git log -1 --oneline        # doit afficher 9009676 ...
```

Le poste n'a **pas** besoin d'Internet après la première construction des images
(le script de recette construit une image MinIO locale car les registres publics ne
servent plus cette image).

---

## Phase 2 — Diagnostic du poste (lecture seule, 30 secondes)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\preflight_windows.ps1
```

* **`[OK]` partout, code de sortie 0** → passez à la phase 3.
* **`[!!]` / code 1** → le script nomme l'élément manquant (Docker arrêté, ports occupés,
  RAM, place disque, pare-feu). Corrigez avant d'aller plus loin ; relancez le script.
* Le script ne lit aucun secret, n'installe rien, n'ouvre pas l'archive réelle.

Conservez la trace :
```powershell
powershell -File scripts\preflight_windows.ps1 | Tee-Object -FilePath data\rapports\preflight_windows.txt
```

---

## Phase 3 — Première mise en service : la recette locale (une seule commande)

```powershell
cd C:\seamtech-search
.\scripts\recette_locale.ps1
```

Durée : 10 à 25 min selon la machine (construction des images la première fois).
Le script est **idempotent** : en cas d'échec, on corrige, on relance, il reprend.

Ce qu'il fait, dans l'ordre :

1. vérifie Docker, Compose v2, Git, et que les 6 ports sont libres ;
2. génère un `.env` avec des **secrets aléatoires** (jamais versionné) ;
3. construit l'image MinIO locale et provisionne les buckets ;
4. `docker compose up -d --build` de la pile complète ;
5. attend `/live`, `/ready`, `/health` et l'interface (HTTP 200) ;
6. crée le compte nominatif de recette et dépose 7 archives de démonstration ;
7. suit les lots d'import, ouvre les fiches à valider, valide, cherche
   (texte, dimension « 6,60 », filtres/facettes, suggestions), télécharge un PDF présigné
   avec zone surlignée, rejoue l'import (idempotence) ;
8. fait une **sauvegarde puis une restauration** réelle de la base ;
9. fait un `down`/`up` et vérifie la persistance ;
10. écrit un rapport **`CONTROLE|<nom>|PASS\|FAIL|<détail>`** ligne par ligne.

**Résultat attendu :** chaque ligne `PASS`, code de sortie **0**, et deux fichiers écrits :

```
data\backups\rapport-recette-<horodatage>.txt      <- le rapport complet
data\backups\recette-verif-sortie.txt              <- la sortie du vérificateur
```

Code de sortie 1 = au moins un `FAIL` : la ligne `FAIL` nomme le contrôle (ex.
`stockage-presigne`, `recherche-dimension`, `sauvegarde-restauration`). Envoyez ce fichier ;
ne « réparez » rien à l'aveugle.

Déposer aussi vos propres dossiers (facultatif, copiés en lecture seule dans la zone de
travail, **l'archive source n'est jamais modifiée — RG13**) :

```powershell
.\scripts\recette_locale.ps1 -CheminsSupp "D:\Voiles 2025", "D:\Dossier été"
```

Équivalent Linux : `bash scripts/recette_locale.sh` (même vérificateur, mêmes contrôles).

---

## Phase 4 — Se connecter

1. Ouvrez **http://127.0.0.1:3000** (l'interface ; l'API est sur `127.0.0.1:8000`).
2. La page `/login` demande un identifiant et un mot de passe.
3. Compte nominatif de recette : identifiant **`recette`**, mot de passe = valeur de
   `RECETTE_MOT_DE_PASSE` dans `.env`. Ce compte est créé avec obligation de changer le
   mot de passe : le changement vous est demandé à la première connexion (c'est normal).
4. Ne cherchez pas le compte « secours » : il n'existe que si `SEAMTECH_UI_PASSWORD` est
   renseigné, et il est partagé donc non traçable. **Une fois les comptes nominatifs créés,
   laissez `SEAMTECH_UI_PASSWORD` vide dans `.env`** (l'interface refuse alors ce chemin).

### Créer les vrais comptes, avec leur rôle

Rôles : `admin`, `utilisateur`, `approbateur`, `lecture`.

```powershell
docker compose exec web python -m seamtech_search.comptes.cli creer --identifiant cecile --nom "C. B." --role approbateur
# le mot de passe est demandé sur l'entrée standard, jamais en argument (invisible dans `ps`)
docker compose exec web python -m seamtech_search.comptes.cli lister
docker compose exec web python -m seamtech_search.comptes.cli changer-role --identifiant cecile --role utilisateur
docker compose exec web python -m seamtech_search.comptes.cli sessions --identifiant cecile
docker compose exec web python -m seamtech_search.comptes.cli revoquer-session --id-session 12
docker compose exec web python -m seamtech_search.comptes.cli desactiver --identifiant cecile
```

Sans `--role`, le compte est créé en **`lecture`** (défaut fermé, volontaire).
`changer-role` et `desactiver` **révoquent immédiatement** les sessions du compte :
l'utilisateur est éjecté à la requête suivante, sans attendre l'expiration.

---

## Phase 5 — Usage quotidien

| Action | Commande |
|---|---|
| Démarrer | double-clic sur **`SEAMTECH Search.cmd`** (ou `.\SEAMTECH Search.cmd`) |
| Vérifier | `docker compose ps` → `web`, `worker`, `frontend`, `postgres`, `minio`, `redis` **running** (les services applicatifs `healthy`) |
| Arrêter (données conservées) | `docker compose down` |
| Arrêter **et tout effacer** | `docker compose down -v` ⚠️ volumes `postgres-data`, `minio-data`, `redis-data` supprimés → perte définitive sans sauvegarde |
| Journaux | `docker compose logs -f web worker` |

`docker compose down` ne détruit **rien** : les documents sont dans le stockage objet
(MinIO), l'index et les comptes dans PostgreSQL, les deux sur volumes nommés.

---

## Phase 6 — Sauvegarde et restauration

```powershell
# Sauvegarde (dump custom, restaurable)
docker compose exec -T postgres pg_dump -U seamtech --format=custom seamtech_search > data\backups\2026-10-29.dump

# Variante avec le script fourni (nécessite pg_dump dans le PATH et l'URL de base)
.\scripts\backup_postgres.ps1 -DatabaseUrl "postgresql://seamtech:<motdepasse>@127.0.0.1:5433/seamtech_search"

# Restauration (écrase l'état courant de la base)
.\scripts\restore_postgres.ps1 -BackupFile data\backups\2026-10-29.dump -DatabaseUrl "postgresql://seamtech:<motdepasse>@127.0.0.1:5433/seamtech_search"
```

`<motdepasse>` = `POSTGRES_PASSWORD` dans `.env`. Une sauvegarde **non restaurée n'est pas
une sauvegarde** : faites l'essai de restauration une fois sur le poste, sur une base de test,
et notez la date dans le registre d'exploitation. (L'archive documentaire, elle, reste la
source de vérité : RG13.)

---

## Phase 7 — Vérifications automatiques, à refaire sur le poste

Les suites de tests ne sont **pas** dans l'image applicative (volontairement : pas de pytest
en production). On utilise un environnement Python local, uniquement pour tester.

### 7.1 Préparer l'environnement de test (une fois)

```powershell
cd C:\seamtech-search
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
```

Linux : `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt`.

### 7.2 Suite principale, sans base (SQLite) — la plus rapide

```powershell
.\.venv\Scripts\pytest -q -rf -m "not postgres and not s3 and not perf and not redis_queue and not ocr_suite"
```

**Attendu (mesuré) : `1128 passed`, ~27 s, aucun échec.** C'est la même commande que la CI.

### 7.3 Suite PostgreSQL (la pile Docker doit tourner)

```powershell
$env:SEAMTECH_TEST_DATABASE_URL = "postgresql://seamtech:<POSTGRES_PASSWORD>@127.0.0.1:5433/seamtech_search"
.\.venv\Scripts\pytest -m "postgres and not perf and not sauvegarde and not redis_queue and not ocr_suite" -q -rf
```

**Attendu (mesuré) : `418 passed, 3 skipped`.** Les 3 `skipped` sont normaux et nommés :
poids du modèle E5 absents (facultatif hors-ligne) et 2 tests de sauvegarde qui exigent
MinIO/stockage objet réel. Tout autre `skip` ou un `xfail` inattendu doit être signalé.

### 7.4 Qualité de code

```powershell
.\.venv\Scripts\ruff check .            # attendu : All checks passed!
cd frontend
pnpm install --frozen-lockfile
pnpm exec tsc --noEmit                  # attendu : aucune sortie
pnpm exec node --experimental-strip-types --test lib/*.test.ts   # attendu : 10 tests, 10 ok
pnpm build                              # attendu : code de sortie 0
```

### 7.5 Tests d'interface (Playwright) — nécessite Internet une fois

```powershell
cd frontend
pnpm exec playwright install chromium
$env:CI = "1"
pnpm exec playwright test                # mode SQLite jetable
```

**Attendu (mesuré) : `49 passed`, 0 échec, ~1 min.**
Pour la version « pile réelle » (vraie base PG + Redis + OCR), voir la CI (étape `e2e`,
**112 passed**). Les deux modes recréent leur propre base jetable à chaque exécution.

### 7.6 Sécurité des dépendances

```powershell
.\.venv\Scripts\pip install pip-audit
.\.venv\Scripts\pip-audit -r requirements.txt -r requirements-dev.txt -r requirements-local.txt
cd frontend ; pnpm audit --prod --audit-level=high
```

**Attendu : aucune vulnérabilité connue.**

### 7.7 Le contrôle d'intégration qui compte le plus

La **phase 3** (`recette_locale.ps1`) est le test d'intégration de bout en bout : c'est le seul
qui prouve, sur *votre* machine, que dépôt → import → fiches → validation → recherche →
téléchargement → sauvegarde → restauration fonctionnent ensemble. Refaites-la après toute
mise à jour du logiciel, et archivez le `rapport-recette-*.txt` correspondant.

---

## Phase 8 — Recette manuelle (à faire une fois, puis après chaque montée de version)

Cochez chaque ligne. La colonne « attendu » décrit le comportement **correct** ; tout écart
est un défaut à signaler avec la capture et l'heure.

### 8.1 Dépôt de dossiers
| Essai | Attendu |
|---|---|
| Glisser un dossier normal dans `/nouveau` | lot créé, progression visible, aucune erreur |
| Fichier `.pdf` + `.docx` + `.jpg` dans le même dossier | tous indexés ; `.pdf` classé **Analysé**, le reste **Stockable** |
| Nom avec accents / espaces / apostrophe (`Fiche été N°2.pdf`) | accepté, nom affiché intact |
| Nom très long (>255 caractères) | refusé **proprement** (message, pas de plantage) |
| Fichier vide (0 octet) | accepté ou refusé, mais **jamais** un lot bloqué en « en cours » |
| Fichier corrompu / ZIP invalide | mis en quarantaine, visible dans l'interface, **jamais** supprimé |
| Extension inconnue (`.xyz`) | classé Stockable, non analysé, aucune erreur |
| Dossier vide | lot terminé sans erreur |
| Arborescence très profonde (10+ niveaux), dossier avec symlink | traité, ou refusé explicitement — jamais de blocage |
| Rejouer le même dépôt | aucun doublon de projet/document (idempotence) |

### 8.2 Fiches et extraction PDF
| Essai | Attendu |
|---|---|
| Ouvrir une fiche `.pdf` correcte | champs du gabarit remplis, zones surlignées sur l'aperçu |
| PDF protégé par mot de passe | marqué non analysable, motif affiché, pas de plantage |
| PDF scanné (image seule) | OCR tenté ; si le texte est illisible → fiche à valider manuellement, motif explicite |
| PDF tourné 90° / 180° / 270° | orientation détectée, extraction identique au PDF droit |
| PDF malformé (tronqué) | erreur enregistrée sur la pièce, lot non bloqué |

### 8.3 Écran de correction (approbateur)
| Essai | Attendu |
|---|---|
| Valeur extraite fausse → corriger → enregistrer | nouvelle valeur persistée, révision incrémentée, ancienne valeur dans l'historique |
| Valider la fiche | **un seul clic**, effet immédiat, la fiche quitte « à valider » |
| Refuser la fiche | motif obligatoire, la fiche revient en correction avec le motif visible |
| Deux postes corrigent la même fiche | le second reçoit un conflit (409) explicite, **rien n'est écrasé en silence** |

### 8.4 Recherche « à la Google »
Dans `/recherche`, chaque essai doit trouver la fiche attendue (ou ne rien proposer) :
| Requête | Attendu |
|---|---|
| dimension `6,60` / `6.60` / `660` | même résultat |
| matière (`lattée`, `lattee`, sans accent) | même résultat |
| nom de bateau, même partiel | résultat |
| abréviation connue | résultat via synonyme/référentiel |
| trait d'union (`genois-gibsea`) | résultat |
| MAJUSCULES / minuscules | identique |
| mot partiel (`gib`) | suggestions proposées |
| requête vide | page d'accueil de recherche, pas d'erreur 500 |
| requête absurde (`zzzzzz`) | « aucun résultat », pas d'erreur |

### 8.5 Comparaison de voiles du même modèle (`/comparaison`)
Bloc par bloc : chaque bloc affiche la valeur des deux voiles, et signale clairement les
**données manquantes** de l'une ou l'autre (jamais une case vide sans explication, jamais une
valeur inventée). Choisir deux modèles différents doit être **refusé** avec un message.

### 8.6 Indice de confiance (`/confiance`)
| Cas | Attendu |
|---|---|
| Voile strictement identique à une référence | confiance haute, références listées |
| Écart de quelques centimètres | confiance plus basse, écart chiffré affiché |
| Modèle hétérogène | avertissement explicite |
| Moins de 5 voiles comparables | **« données insuffisantes »**, aucune note chiffrée |
| Accord < 75 % | **aucun verdict automatique**, mention « à valider par un approbateur » |

### 8.7 Accessoires
Accessoire rattaché à un bateau : le lien apparaît sur la fiche bateau et sur l'accessoire ;
l'accessoire apparaît dans le classement **Accessoire** (sous-type), pas dans Stockable/Analysé.
Un PDF d'accessoire se dépose et s'extrait comme une fiche normale.

### 8.8 Rôles et droits — **essayez ce qui doit être refusé**
| Compte (rôle) | Doit pouvoir | **Doit être refusé** (vérifier le refus, pas seulement l'absence de bouton) |
|---|---|---|
| `admin` | gérer comptes, rôles, journaux, référentiels | **approuver une fiche** (séparation des tâches) |
| `lecture` | consulter, rechercher | modifier une fiche, déposer un dossier, **télécharger un document** |
| `utilisateur` | modifier, déposer, télécharger | approuver une fiche, gérer les comptes |
| `approbateur` | approuver en un clic, immédiatement | gérer les comptes, modifier les référentiels |

Testez aussi **par l'API** (le refus doit venir du serveur, pas de l'interface) :
un `403`/`401` est un refus correct ; un `422` ou un `200` ne prouve rien.

### 8.9 Session et sécurité
| Essai | Attendu |
|---|---|
| Se déconnecter | retour à `/login`, l'ancien cookie ne donne plus accès |
| Attendre l'expiration (`SEAMTECH_SESSION_HOURS=8`, inactivité `SEAMTECH_SESSION_IDLE_MINUTES=30`) | déconnexion, aucune action fantôme |
| Faire révoquer sa session par l'admin | éjection immédiate |
| Modifier le cookie à la main | refus, pas de fuite d'information |
| Ouvrir l'URL d'une fiche d'un autre compte / d'un identifiant inexistant | `403`/`404`, jamais les données d'autrui (IDOR) |
| Formulaire sans jeton CSRF / depuis un autre site | action refusée |

### 8.10 Notifications (`/notifications`)
Un dépôt terminé, une fiche à valider, une approbation et un refus produisent chacun une
notification ; marquer comme lu la fait passer en « lu » et l'état persiste après
rechargement et après reconnexion.

---

## Phase 9 — En cas de problème

| Symptôme | Cause probable | Action |
|---|---|---|
| `Set POSTGRES_PASSWORD in the environment` | `.env` absent | relancer `scripts\recette_locale.ps1` (il génère `.env`) |
| « password ... is not encoded » au démarrage | secret contenant `/` ou `+` | régénérer avec `openssl rand -hex 24` (hexadécimal = toujours sûr en URL) |
| Port déjà utilisé | autre service sur 3000/5433/… | `netstat -ano \| findstr ":3000 "` puis arrêter le coupable |
| `docker info` échoue | Docker Desktop non démarré / backend non-WSL2 | démarrer Docker Desktop, attendre « Engine running » |
| La recette échoue au provisioning MinIO | image MinIO absente, pas d'Internet | relancer une fois connecté (l'image est construite localement) |
| 503 en tentant de se connecter avec l'identifiant `secours` | `SEAMTECH_UI_PASSWORD` vide : le compte de secours est volontairement désactivé | se connecter avec un compte nominatif, ou en créer un (phase 4) |
| Lots bloqués « en attente » | worker arrêté | `docker compose ps` puis `docker compose logs worker` |
| Import marqué `upload_incomplete` | vérification stockage échouée | voir `data/quarantine/`, ne rien supprimer, signaler avec le journal |

---

## Phase 10 — Ce que ce poste prouve, et ce qu'il ne prouve pas

**Prouvé par ce guide, sur votre machine :** installation reproductible, pile complète
fonctionnelle, import et classement, extraction PDF, recherche, validation, rôles, sessions,
sauvegarde **et restauration** de la base, idempotence du rejeu.

**Non prouvé par une exécution locale** — à qualifier en conditions réelles, avec un procès-verbal :

1. tenue en charge réelle (100 000 fichiers / 20 Go) : la recette utilise 7 archives de démonstration ;
2. endurance multi-jours (fuites, dérive disque) : nécessite un fonctionnement continu surveillé ;
3. coupure de courant **pendant** une migration ou une sauvegarde ;
4. restauration d'archive d'un exercice antérieur (RG13) sur le poste de production ;
5. qualité de l'OCR natif (`tesseract` système) sur vos scans réels — la CI utilise un OCR simulé ;
6. stockage objet réellement distant (MinIO d'atelier / R2 / S3) et ses latences ;
7. navigateurs natifs du parc (Chrome/Edge à jour) au-delà de ceux testés ici ;
8. PostgreSQL de production avec un rôle **non superutilisateur** et ses politiques de sauvegarde ;
9. les identités de sauvegarde et la rotation des secrets sur la durée.

Pour chacun de ces points, la mention correcte est
**« NON PROUVÉ EN LOCAL — nécessite qualification en conditions réelles »**, jamais « PRÊT ».

---

## Récapitulatif ultra-court

```
preflight_windows.ps1          -> [OK] partout, 0
recette_locale.ps1             -> tous PASS, 0
http://127.0.0.1:3000          -> connexion compte nominatif
pytest (SQLite)                -> 1128 passed
pytest (PostgreSQL)            -> 418 passed, 3 skipped (normaux)
tsc / unit / build             -> 0 erreur, 10 ok, 0
playwright                     -> 49 passed
pip-audit / pnpm audit         -> rien
phase 8 (manuelle)             -> tout coché, y compris les refus
sauvegarde + restauration      -> faite et datée
```
