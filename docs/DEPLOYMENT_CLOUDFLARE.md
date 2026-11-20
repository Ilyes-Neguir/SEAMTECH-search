# SEAMTECH Search — déploiement derrière Cloudflare (internet public)

Ce document décrit le déploiement de la stack Docker existante **inchangée**
(postgres, redis, minio, web, worker, sauvegarde, frontend) sur un hôte
conteneur, exposée sur internet **via Cloudflare**. Aucune réécriture
d'architecture : la composition reste celle de `docs/DEPLOYMENT.md`, et
Cloudflare apporte le domaine, le certificat TLS, le WAF et le CDN.

> Le backend (FastAPI + PostgreSQL + Redis + worker) ne peut pas tourner sur
> Cloudflare Workers (Python, système de fichiers persistant, base
> relationnelle). Cloudflare est donc la **façade** ; la charge reste sur
> l'hôte conteneur.

## Architecture

```
Navigateur ──HTTPS──► Cloudflare (DNS, TLS, WAF, CDN)
                          │  Cloudflare Tunnel (sortant, aucun port ouvert)
                          ▼
                   Hôte conteneur (docker-compose)
                          ├─ frontend (Next.js, 127.0.0.1:3000)
                          ├─ web      (FastAPI, 127.0.0.1:8000, non exposé)
                          ├─ worker   (file durable)
                          ├─ postgres (pgvector, 127.0.0.1:5433)
                          ├─ redis    (127.0.0.1:6379)
                          └─ minio    (S3, 127.0.0.1:9000)
```

Seul le **frontend** est exposé (via le Tunnel). Le navigateur ne parle
jamais directement au backend : les routes `/api/*` de Next.js proxifient
vers FastAPI côté serveur avec le jeton de service, qui n'atteint jamais le
navigateur. Aucun CORS n'est requis.

## Pourquoi Cloudflare Tunnel (et pas un port public)

`cloudflared` ouvre une connexion **sortante** vers Cloudflare et y publie
le service local. Avantages pour cette application :

- **aucun port ouvert** sur l'hôte (les ports restent sur `127.0.0.1`, comme
  la composition les lie par défaut) ;
- pas d'adresse IP publique à protéger ;
- le TLS est terminé chez Cloudflare, l'origine reste en clair **en local
  uniquement** — exactement le modèle `SEAMTECH_BEHIND_TLS_PROXY=true` que
  l'application exige pour l'authentification par jeton sur hôte non-local.

> ⚠️ **Réserve de sécurité (revue externe 2026-11-20)** : le frontend fait
> confiance à l'en-tête `cf-connecting-ip` pour identifier le client
> (`clientIdentifier` dans `frontend/lib/auth.ts`), ce qui fonde le
> verrouillage des tentatives de connexion. C'est sûr **uniquement** si le
> frontend n'est joignable que par le tunnel Cloudflare : si un port est
> exposé en direct sur internet, un attaquant peut forger cet en-tête à
> chaque essai et contourner le verrou. **Ne jamais exposer le port du
> frontend en direct — tunnel seul.**

## Prérequis

- Un hôte (VPS ou machine) avec Docker et Docker Compose, capable de faire
  tourner les six services.
- Un domaine dont le DNS est chez Cloudflare.
- `cloudflared` installé sur l'hôte (ou en conteneur).

## 1. Démarrer la stack (inchangée)

Suivre `docs/DEPLOYMENT.md` : `.env` renseigné, identités de stockage
provisionnées (`scripts/provisionner_stockage.sh`), puis :

```bash
docker compose up -d
docker compose ps   # les six services doivent être « running (healthy) »
```

## 2. Variables d'environnement de production

Dans `.env`, en plus de celles de `docs/DEPLOYMENT.md` :

| Variable | Valeur | Pourquoi |
|---|---|---|
| `SEAMTECH_BEHIND_TLS_PROXY` | `true` | L'auth par jeton sur hôte non-local l'exige (garde de démarrage). |
| `SEAMTECH_ALLOW_NETWORK_ACCESS` | `true` | Le service écoute au-delà du loopback (via le Tunnel). |
| `SEAMTECH_ALLOWED_HOSTS` | `app.example.com` | Validation de l'en-tête Host (anti-injection). Remplacer par le vrai domaine. |
| `SEAMTECH_AUTH_TOKEN` | secret long et unique | Protège l'API. |
| `SEAMTECH_SESSION_SECRET` | secret long et unique | Signe les cookies de session. |

> `SEAMTECH_ALLOWED_HOSTS` : liste séparée par des virgules des domaines
> publics autorisés. Le loopback reste toujours accepté (sondes de santé).
> Sans cette variable, aucun contrôle d'hôte n'est appliqué — à renseigner
> en production.

## 3. Exposer le frontend via Cloudflare Tunnel

```bash
# Authentification et création du tunnel (une fois)
cloudflared tunnel login
cloudflared tunnel create seamtech

# Configuration : publier le frontend local
cat > ~/.cloudflared/config.yml <<'YAML'
tunnel: seamtech
credentials-file: ~/.cloudflared/<id>.json

ingress:
  - hostname: app.example.com
    service: http://127.0.0.1:3000
  - service: http_status:404
YAML

# Enregistrement DNS (crée le CNAME chez Cloudflare)
cloudflared tunnel route dns seamtech app.example.com

# Démarrage (à mettre en service persistant : systemd, Docker, etc.)
cloudflared tunnel run seamtech
```

Le port du frontend est celui publié par la composition (`frontend`, voir
`docker-compose.yml`). Le backend (`web`, 8000) n'est **pas** publié : il
n'est joignable que depuis le réseau Docker et le frontend.

## 4. WAF Cloudflare (recommandé)

Dans le tableau de bord Cloudflare, pour la zone du domaine :

- **Managed Ruleset** : activer le *Cloudflare Managed Ruleset* et le
  *Cloudflare OWASP Core Ruleset* (mode *Block* ou *Challenge* selon le
  trafic légitime).
- **Rate limiting** : une règle sur `/api/*` (ex. 600 requêtes / minute / IP)
  en complément du limiteur applicatif déjà en place côté API.
- **Bot Fight Mode** : activé (gratuit) pour les bots triviaux.

L'application pose déjà ses propres protections (limiteur de débit Redis,
en-têtes de sécurité, HSTS) : le WAF est une couche supplémentaire, pas un
substitut.

## 5. Vérification

```bash
# L'application répond en HTTPS avec les en-têtes de sécurité
curl -sI https://app.example.com/ | grep -iE "strict-transport|content-security|x-frame"

# Le backend refuse sans jeton (401) et ne révèle rien
curl -s https://app.example.com/api/...   # via le front, jamais en direct

# Un hôte non déclaré est rejeté (validation d'hôte)
curl -s -H "Host: evil.example.com" https://app.example.com/ -o /dev/null -w '%{http_code}\n'
```

## 6. Sauvegarde et reprise

Inchangées : le service `sauvegarde` de la composition planifie la
sauvegarde quotidienne (base + données + objets, hors-site si un client S3
est configuré). Voir `docs/DEPLOYMENT.md` § *Backups and recovery* et la
vérification § *Verifying that backups actually happen*.

## 7. Mises à jour

```bash
docker compose pull && docker compose up -d
```

Les volumes nommés (`postgres-data`, `minio-data`, `redis-data`) survivent à
la recréation des conteneurs. Le répertoire `data/` contient les fichiers de
travail locaux.

## Notes et limites

- **MinIO reste le stockage supporté** pour cette version (voir
  `docs/DEPLOYMENT.md`). Cloudflare R2 (compatible S3) est une option
  ultérieure, non requise pour ce déploiement.
- **Aucune donnée client n'est exposée** : la purge des données du
  commanditaire (vague 1) est indépendante de ce déploiement.
- Si l'hôte conteneur est derrière un NAT sans IP publique, Cloudflare
  Tunnel est la seule voie requise (connexion sortante uniquement).
