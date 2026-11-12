# Mise en service sur le poste Windows — feuille de route

**Pour qui** : la personne qui fera tourner SEAMTECH Search sur le poste de
l'atelier. Aucune connaissance préalable de Docker n'est supposée.

**Comment l'utiliser** : dans l'ordre, du haut vers le bas. Chaque étape dit
**où** taper, **quoi** taper, **pourquoi**, et **ce qu'on doit voir**. Un
signe ⛔ marque un point d'arrêt : on s'arrête, on relève ce qui est demandé,
et on me le transmet **avant** d'aller plus loin. Ce n'est pas de la
paperasserie : c'est ce qui évite de découvrir un problème trois étapes plus
tard.

**Règle qui ne se négocie pas** : l'archive réelle et les documents clients
n'entrent en jeu qu'à **l'étape 8**. Avant cela, on ne manipule que le jeu
d'essai fourni avec le dépôt (`sample_data`). Le poste n'est pas un banc de
découverte.

**Documents de référence** (pour aller plus loin, jamais pour remplacer ce
guide) : `docs/CHECKLIST_POSTE_WINDOWS.md` (prérequis matériels),
`docs/DEPLOYMENT.md` (installation détaillée, en anglais),
`docs/verite_terrain/RUNBOOK_RESTAURATION.md` (sauvegarde et restauration).

---

## Lire ceci avant l'étape 0 — une limite qui vient de Windows, pas de nous

⛔ **L'application ne fonctionne pas sous Python Windows.** Elle fonctionne
sur ce poste **uniquement par Docker**, parce que Docker fait tourner des
conteneurs Linux où les primitives nécessaires existent.

Le produit confine ses accès aux fichiers avec trois primitives que Windows ne
possède pas. Il ne s'agit pas d'un réglage, ni d'une lenteur : **il n'existe
aucun équivalent Windows**, pas même dégradé.

| Primitive | À quoi elle sert | Sous Windows |
|---|---|---|
| `dir_fd` + `O_NOFOLLOW` | parcourir un dossier sans jamais suivre un lien symbolique — c'est ce qui empêche un dépôt de sortir de l'arborescence autorisée | **inexistant** |
| `/proc/self/fd` | épingler le fichier servi le temps de la réponse, pour que ce qui est téléchargé soit exactement ce qui a été vérifié | **inexistant** |
| `fcntl.flock` | refuser deux imports concurrents du même dossier | **inexistant** |

**Ce que fait le produit quand la primitive manque : il refuse.** Il n'y a
volontairement **aucun mode dégradé** : un parcours de dossier « à peu près
sûr » laisserait croire qu'un dépôt est confiné alors qu'il ne l'est pas. Le
refus est explicite et nomme la capacité manquante — par exemple :

    Capacité de plateforme absente : scan_ancre_descripteur — scan d'un
    dossier ancré par descripteur (dir_fd + O_NOFOLLOW) : sans lui, l'import
    d'un dossier refuse au lieu de traverser des liens (plateforme : win32,
    os.name=nt).

Si vous voyez ce message, **la cause est que l'application tourne sous Python
Windows** et non dans Docker. Ce n'est ni un mot de passe, ni un chemin, ni
une panne : changer de configuration n'y fera rien.

**Vérification en une ligne**, à l'intérieur du conteneur (résultat attendu :
les quatre capacités à `oui`) :

```powershell
docker compose run --rm --no-deps web python -c "from seamtech_search.plateforme import resume; print(resume())"
```

attendu →

    scan_ancre_descripteur=oui ; procfs_fd=oui ; verrou_flock=oui ; processus_posix=oui

(`--no-deps` : on ne veut que la sonde, pas la pile entière. Si un `non`
apparaît, c'est que la commande n'a pas tourné dans le conteneur Linux —
revenir au paragraphe précédent.)

**Conséquence pratique** : tout passe par `docker compose`. N'installez pas
l'application en Python local sur le poste et ne lancez pas
`python -m seamtech_search...` hors conteneur.

---

## Étape 0 — Vérifier que le poste est prêt (lecture seule, sans risque)

**Où** : PowerShell, en tant qu'utilisateur normal.

```powershell
cd C:\SEAMTECH\SEAMTECH-search
powershell -ExecutionPolicy Bypass -File scripts\preflight_windows.ps1 |
  Tee-Object -FilePath data\rapports\preflight_windows.txt
```

**Pourquoi** : mesurer le poste sans rien y changer — le script n'installe
rien, ne télécharge rien, n'ouvre jamais `.env` et n'accède jamais à
l'archive.

**Ce qu'on doit voir** : `[OK]` sur chaque ligne, un bilan `poste prêt`, et un
code de sortie `0`.

⛔ **Point d'arrêt 0** — transmettre le fichier
`data\rapports\preflight_windows.txt` (il ne contient aucun secret). Si une
ligne dit `[PROBLEME]`, s'arrêter : le §4 de
`docs/CHECKLIST_POSTE_WINDOWS.md` dit quoi faire, et on en parle avant
d'installer quoi que ce soit.

---

## Étape 1 — Mettre le dépôt à jour

**Où** : PowerShell, dans `C:\SEAMTECH\SEAMTECH-search`.

```powershell
git status
git branch --show-current
```

**Pourquoi** : on ne met jamais à jour un dépôt sans savoir ce qu'il
contient. Si des fichiers sont modifiés, ils sont peut-être précieux.

**Ce qu'on doit voir** : `nothing to commit, working tree clean` et le nom de
la branche courante.

Si le arbre n'est **pas** propre, ou si la branche n'est pas `main` :
⛔ **s'arrêter et me transmettre les deux sorties.** On décidera ensemble.

Sinon :

```powershell
git fetch origin
git switch main
git pull --ff-only origin main
git log --oneline -1
```

**Ce qu'on doit voir** : `git pull` récupère les commits, et `git log`
affiche en première ligne **le SHA annoncé dans le message qui accompagne ce
guide**. Le noter : c'est la preuve que le poste exécute bien la version
qualifiée.

⛔ **Point d'arrêt 1** — transmettre la sortie de `git log --oneline -1`.

---

## Étape 2 — Renseigner les secrets (`.env`)

**Où** : PowerShell, même dossier.

```powershell
Copy-Item .env.example .env
notepad .env
```

**Pourquoi** : la pile refuse de démarrer avec des secrets d'exemple
(`change-me`, `minioadmin`…) — c'est voulu, un mot de passe connu n'est pas un
mot de passe.

Générer chaque secret **depuis PowerShell** (une ligne = un secret) :

```powershell
-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
```

À renseigner au minimum : `POSTGRES_PASSWORD`, `MINIO_ROOT_USER`,
`MINIO_ROOT_PASSWORD`, `REDIS_PASSWORD`, `SEAMTECH_AUTH_TOKEN`,
`SEAMTECH_UI_PASSWORD` (c'est le mot de passe tapé à la connexion),
`SEAMTECH_SESSION_SECRET`, `SEAMTECH_S3_SECRET_KEY`,
`SEAMTECH_BACKUP_SECRET_KEY`.

Trois précautions qui ont une raison :

* les secrets `POSTGRES_PASSWORD` et `REDIS_PASSWORD` voyagent **dans des URL** :
  les générer en hexadécimal (`-join (1..48 | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })`),
  jamais en base64 brut (un `/` dans une URL Redis change silencieusement de
  base) ;
* ne jamais réutiliser deux fois le même secret ;
* `.env` ne se committe jamais (il est ignoré par Git — le vérifier avec
  `git status`, il ne doit pas apparaître).

⛔ **Point d'arrêt 2** — transmettre uniquement : « `.env` rempli, `.env`
invisible dans `git status` ». **Jamais** le contenu du fichier, jamais un
secret.

---

## Étape 3 — Créer le stockage objet et les identités

**Où** : PowerShell, même dossier. Docker Desktop doit être lancé.

```powershell
docker compose up -d minio
bash scripts/provisionner_stockage.sh
```

**Pourquoi** : créer les deux buckets (documents et sauvegardes), activer le
versionnement, et créer **deux identités restreintes** — une pour
l'application (elle ne voit que le bucket documents), une pour la sauvegarde
(elle ne voit que le bucket sauvegardes). L'application ne tourne jamais avec
les identifiants administrateur. Le script est idempotent : le rejouer ne
casse rien, et c'est ainsi qu'on change un secret. Il n'affiche jamais de
secret.

**Ce qu'on doit voir** : le script annonce le bucket créé, les deux
utilisateurs créés, et confirme qu'aucun bucket n'est public.

⛔ **Point d'arrêt 3** — transmettre la sortie du script (sans les secrets).

---

## Étape 4 — Démarrer la pile et vérifier qu'elle est vivante

```powershell
docker compose up -d --build
docker compose ps
Invoke-WebRequest http://127.0.0.1:3000/api/health | Select-Object -Expand Content
```

**Pourquoi** : `up -d` ne veut pas dire « prêt ». Le `web` attend que
PostgreSQL, MinIO et Redis soient **sains** ; le `worker` (celui qui exécute
réellement les imports) attend les trois ; le `frontend` attend le `web`. On
regarde donc l'état réel.

**Ce qu'on doit voir** : dans `docker compose ps`, chaque service `healthy` ;
la commande `Invoke-WebRequest` renvoie un HTTP 200 ; et
`s3_credentials` vaut `dedie` dans la réponse (si elle affichait
`root_like`, les identifiants administrateur auraient été réutilisés — on
s'arrête et on me le dit).

Puis ouvrir **http://127.0.0.1:3000** dans Edge ou Chrome, et se connecter
avec `SEAMTECH_UI_PASSWORD`.

⛔ **Point d'arrêt 4** — transmettre la sortie de `docker compose ps` et la
réponse de `/api/health`, et confirmer « la page de connexion s'ouvre ».

---

## Étape 5 — Premier dépôt : le jeu d'essai, jamais l'archive

**Où** : l'interface web, et PowerShell pour préparer les fichiers.

```powershell
New-Item -ItemType Directory -Force data\DesignFiles | Out-Null
Copy-Item sample_data\CLIENT-123 data\DesignFiles\ -Recurse
Copy-Item sample_data\CLIENT-456 data\DesignFiles\ -Recurse
```

**Pourquoi** : `sample_data` contient quatre dossiers et six fichiers
**synthétiques** (aucune donnée réelle). C'est l'épreuve générale : elle doit
réussir avant qu'un seul document réel n'approche la machine.

Dans l'interface : déposer le dossier `data\DesignFiles` **en une seule fois**
par glisser-déposer (c'est le parcours qui doit fonctionner sur un gros
dossier complet — on ne fait pas un dossier à la fois).

**Ce qu'on doit voir** : l'import se termine sans erreur, et le compte de
fichiers déposés correspond à ce qui a été copié (4 fichiers pour les deux
dossiers ci-dessus). Un échec doit être **nommé** dans l'interface, jamais
silencieux.

⛔ **Point d'arrêt 5** — transmettre : le nombre de fichiers déposés, le
statut final de l'import, et le message exact si quelque chose a échoué.

---

## Étape 6 — Vérifier la recherche et le téléchargement

Dans l'interface : rechercher un mot qui figure dans les fichiers d'essai
(par exemple `PLAN-2026-001`, présent dans `plan-notes.txt`), ouvrir la pièce,
puis la télécharger.

**Ce qu'on doit voir** : le document est trouvé, la pièce s'ouvre, le
téléchargement aboutit.

⛔ **Point d'arrêt 6** — transmettre « recherche OK / téléchargement OK », ou
le message exact sinon.

---

## Étape 7 — Exercice de sauvegarde et de restauration (avant d'en avoir besoin)

**Pourquoi** : une sauvegarde qu'on n'a jamais restaurée n'est pas une
sauvegarde. On fait l'exercice maintenant, sur le jeu d'essai.

```powershell
# 1. la sauvegarde complète : base + références + données téléversées
docker compose run --rm sauvegarde python -m seamtech_search.sauvegarde sauver ^
  --archive /app/data/DesignFiles ^
  --data /app/data ^
  --avec-references ^
  --dossier /app/data/backups --retention 5

# 2. retrouver le nom du dernier manifeste (l'horodatage est dans le nom)
Get-ChildItem data\backups -Filter *.manifest.json | Sort-Object Name | Select-Object -Last 1 -ExpandProperty Name

# 3. le confronter à la base vivante
docker compose run --rm sauvegarde python -m seamtech_search.sauvegarde verifier ^
  --manifeste /app/data/backups/<le-nom-releve-au-2> --archive /app/data/DesignFiles
```

**Pourquoi** : `--archive` est la racine lue **en lecture seule** (jamais
modifiée), `--data` couvre les octets déposés depuis le navigateur (hors
archive), `--avec-references` copie les pièces réellement référencées. Une
sauvegarde qui omet `--data` n'est pas complète, et le manifeste le dit.

**Ce qu'on doit voir** : un manifeste écrit sous `data\backups`, et
`verifier` qui confirme la concordance (base + données + objets).

### ⛔ Ce que la sauvegarde AUTOMATIQUE ne couvre PAS — à lire avant tout

Le service `sauvegarde` tourne seul, toutes les
`SEAMTECH_BACKUP_INTERVAL_HEURES` (24 h par défaut). **Il ne fait pas la même
chose que la commande ci-dessus.** Son périmètre réel est :

    sauvegarde-auto: base corpus

C'est-à-dire :

| Ce qui est couvert automatiquement | Ce qui ne l'est PAS |
|---|---|
| la **base** PostgreSQL (dump + manifeste) | les **pièces jointes** des clients (objets du stockage MinIO/S3) |
| le **corpus mesuré** (`donnees/etape0`) | les **données déposées** depuis le navigateur (`./data`) |

**Les pièces jointes — c'est-à-dire les documents des clients — ne sont donc
pas dans la sauvegarde automatique.** Elles vivent dans le volume Docker de
MinIO, sur le disque de CE poste. Si ce disque meurt, les documents
disparaissent ; la base, elle, serait restaurée avec des références vers des
objets absents.

Cette ligne n'est pas une appréciation : elle est relue et vérifiée par un
test (`tests/test_sauvegarde_etat_reel.py`), qui échoue si ce guide promet
une couverture que le planificateur ne fait pas.

⛔ **Point d'arrêt 7 bis** — décision à prendre AVANT l'étape 8, avec moi :
faut-il que la sauvegarde automatique couvre aussi les pièces jointes ? Trois
options, et je ne tranche pas seul :

1. **Ajouter `--avec-references` et `--data` à l'automatisme** — protection
   complète, mais le volume à transférer chaque nuit devient celui des
   documents (temps, bande passante, coût du stockage hors-site) ;
2. **Garder l'automatisme tel quel, et rendre la sauvegarde complète
   obligatoire à fréquence définie** (hebdomadaire, par exemple) — compromis
   à décider avec l'atelier ;
3. **Ne rien changer** — assumer que les documents originaux restent dans
   l'archive de l'atelier, qui est alors la seule vraie sauvegarde. C'est
   défendable **si et seulement si** l'archive est elle-même sauvegardée
   ailleurs.

⛔ **Point d'arrêt 7** — transmettre la sortie de `verifier`. **Ne pas tenter
la restauration complète maintenant** : elle détruit la base courante avant
de la reconstruire. On la fera ensemble, au bon moment, avec la procédure de
`docs/verite_terrain/RUNBOOK_RESTAURATION.md`.

---

## Étape 8 — Et seulement maintenant, la vraie archive

Deux réglages, puis une montée en charge progressive :

1. dans `.env`, `SEAMTECH_IMPORT_ARCHIVE_HOST_PATH` = le dossier Windows de
   l'archive (ex. `D:\SEAMTECH\DesignFiles`). La composition le monte **en
   lecture seule** dans le conteneur : l'application ne peut ni le modifier ni
   le supprimer ;
2. ajouter `/archives` à `SEAMTECH_ROOT_PATHS` — sans cela, l'application
   répond « dossier hors des racines configurées » et rien ne peut être déposé
   ;
3. redémarrer : `docker compose up -d` ;
4. déposer **un seul petit dossier réel** d'abord, vérifier, puis un dossier
   plus gros, et seulement ensuite le volume visé (l'échelle « VOILES 2024 »
   est la cible, en un seul dépôt).

⛔ **Point d'arrêt 8** — après le premier petit dossier réel, transmettre le
compte de fichiers et le statut. On ne lance pas le gros volume avant d'avoir
validé le petit ensemble.

---

## Annexe — Arrêter, redémarrer, et quoi transmettre en cas de problème

```powershell
docker compose down                      # arrête la pile ; les DONNÉES RESTENT (volumes + ./data)
docker compose up -d                     # redémarre
docker compose logs --tail 100 web worker frontend postgres minio redis
docker compose ps
```

**Ce qui est conservé** : les volumes nommés (`postgres-data`, `minio-data`,
`redis-data`) et le dossier `./data` (dont `data\backups`). Une mise à jour de
code ne perd rien.

**En cas de problème**, transmettre :

1. l'étape en cours et la commande tapée ;
2. la sortie de `docker compose ps` ;
3. la sortie de `docker compose logs --tail 100 <service concerné>` ;
4. le message affiché à l'écran, **mot pour mot**.

Ne jamais transmettre `.env`, ni un mot de passe, ni un extrait de document
réel : un indice, un booléen, un compte ou un statut suffisent toujours à
comprendre.
