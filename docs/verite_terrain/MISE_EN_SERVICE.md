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
#   SEAMTECH_UI_PASSWORD     (compte de SECOURS de l'interface — voir ci-dessous)
#   SEAMTECH_SESSION_SECRET  (secret de session — chaîne longue aléatoire)
# Les secrets qui finissent dans une URL de connexion (POSTGRES_PASSWORD,
# REDIS_PASSWORD — compose construit SEAMTECH_DATABASE_URL / SEAMTECH_REDIS_URL
# avec eux) doivent être URL-safe : « openssl rand -hex 24 ». Un base64 brut peut
# contenir « / », qui casse l'URL (côté Redis, il devient le sélecteur de base) ;
# l'application refuse alors de démarrer avec un message explicite.

# 3. Démarrage
docker compose up -d --build

# 4. LOT L.2 — créer les COMPTES NOMINATIFS avant de travailler.
#    Tant qu'aucun compte n'existe, seul le compte de secours (mot de passe
#    partagé SEAMTECH_UI_PASSWORD, identifiant « secours ») permet d'entrer —
#    c'est voulu, pour ne jamais se verrouiller dehors. Mais ses validations
#    sont toutes attribuées à « secours » : créez les comptes réels, puis
#    videz SEAMTECH_UI_PASSWORD et redémarrez le conteneur frontend.
docker compose exec web python -m seamtech_search.comptes.cli creer \
    --identifiant imrane --nom "I. N." --role administrateur
#    (mot de passe lu sur STDIN ; jamais en argument — cf. docs/API.md)
docker compose exec web python -m seamtech_search.comptes.cli lister
```

Statut des comptes, à tout moment : `… comptes.cli lister` (rôle, actif, dernière
connexion) ; `… sessions --identifiant imrane` montre les sessions ouvertes et
`… revoquer-session --id-session N` en ferme une immédiatement. La déconnexion
depuis l'interface ferme la session EN BASE (`revoque_le`) : un cookie copié ne
vaut plus rien après coup — c'est vérifié par les tests et par l'e2e live.

Sortie attendue : `docker compose ps` montre les **six** services — `postgres`,
`minio`, `redis`, `web` (API), **`worker`** (imports, service séparé) et
`frontend` — en `running (healthy)` après la montée (les healthchecks de compose
font foi ; le job CI `integration` attend exactement cet état). Le provisionnement
du stockage (section 1, étape 2) est **vérifié AVANT tout usage normal** : un dépôt
réel avant vérification peut échouer à mi-parcours.

```bash
# 4. Santé — l'unique route sans authentification
curl -s http://127.0.0.1:3000/api/health
# attendu : un payload JSON de vivacité (statut, sans comptage d'archive)

# 5. Santé API (avec le token)
curl -s -H "X-SEAMTECH-TOKEN: <SEAMTECH_AUTH_TOKEN>" http://127.0.0.1:8000/health
```

```bash
# 6. Dépôt d'un dossier réel puis fiche visible
#    Interface : http://127.0.0.1:3000 (connexion avec l'identifiant + mot de
#    passe d'un compte nominatif ; identifiant vide = compte de secours),
#    écran Dépôt → choisir un dossier de fiches PDF → attendre la fin du
#    traitement → la fiche apparaît dans la file de validation.
#    (CLI équivalent : voir docs/API.md — `cli depot`.)
```

Sortie attendue : la fiche entre en statut `a_valider` (RG3 : jamais validée
automatiquement), ses champs extraits sont affichés, son PDF est rendu à
droite. C'est exactement ce que le job CI `integration` rejoue avec la vraie
fiche 7792-SO.

## 2 bis. Ordre recommandé de mise en service (revue du 2026-10-07)

L'ordre compte : chaque étape suppose la précédente FAITE et VÉRIFIÉE.

1. **Fermer les points d'ingénierie et figer la release.** `index --rebuild` est
   sûr (E-40 corrigé, prouvé sur PostgreSQL réel) ; la CI est verte sur le commit
   retenu, et le **commit de release est FIGÉ : `889cfc4`** (CI 13/13 jobs, push
   `37697083753` + pull request `37697089852`, dont 7 scénarios de concurrence au
   premier essai sans aucun rattrapage). Après ce gel, on ne fait plus
   entrer de fonctionnalité sans rapport : les commits suivants éventuels sont
   documentaires et doivent être présentés comme tels.
2. **Confirmer la conception.** Serveur d'atelier local (PC/VM) ou VPS distant —
   un VPS fait dépendre l'atelier de la liaison Internet ; **MinIO** comme
   fournisseur objet (R2 optionnel, non requis) ; **destination de sauvegarde
   indépendante** (pas le même disque, pas le même compte) ; **perte de données
   acceptable** et **durée de reprise** écrites ; qui maintient comptes, mises à
   jour, sauvegardes et alertes.
3. **Provisionner SÉCURISÉ — avant toute donnée réelle et avant les workers** :
   secrets (URL-safe) hors dépôt, volumes et permissions, **archive montée en
   lecture seule**, identités applicative et de sauvegarde **restreintes à leur
   bucket**, exposition réseau limitée (loopback + TLS pour les postes), puis
   vérifier les **six services** et les **migrations** attendues de la release
   (constante `VERSION_SCHEMA_METIER`, liste finissant à `021_revision_fiche`).
4. **Pilote réduit représentatif** (dossier réel + cas difficiles : scans anciens,
   doublons, chemins accentués, gros PDF) : chaque fichier comptabilisé, résultats
   de recherche attendus, téléchargement des originaux et des rapports depuis un
   AUTRE poste, validation par les ouvriers — et refus CLAIR d'une validation
   périmée ou d'une édition concurrente. Le **processus de fabrication existant
   reste la référence** : le logiciel ne le remplace pas.
5. **Prouver la reprise dans un environnement ISOLÉ** : connexion et permissions,
   projets et historique de validation, recherche, originaux + rapports, contrôle
   d'intégrité, sort des jobs en attente. **Un dump restauré seul ne prouve pas que
   l'archive est récupérée.**
6. **Augmenter la taille de l'archive** puis **acceptation en atelier** : mesurer
   (durées d'import, latences de recherche, mémoire), et conclure par un **go/no-go
   explicite** avec les limites écrites noir sur blanc.

> La **calibration ML** ne bloque PAS cette mise en service : elle bloque seulement
> la promesse d'une **confiance calibrée** affichée à l'opérateur (les fiches
> validées nécessaires n'existent pas encore).

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
- [ ] `docker compose ps` : **6** services `healthy`
- [ ] `curl http://127.0.0.1:3000/api/health` répond (depuis PowerShell : `Invoke-WebRequest http://127.0.0.1:3000/api/health`)
- [ ] un dossier réel déposé via l'écran Dépôt produit une fiche `a_valider` visible
- [ ] une sauvegarde `python -m seamtech_search.sauvegarde sauver …` écrit dump + manifeste

Chaque case cochée = une ligne de ce qui EST prouvé sur le poste cible ;
reporter le résultat dans ce document (ou « échoué + sortie brute »).
