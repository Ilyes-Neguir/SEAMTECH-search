# Imports durables — file Redis, worker séparé, reprise

Statut : **implémenté et mesuré** (2026-10-06). Ce document dit ce qui est
garanti, ce qui ne l'est PAS, et comment l'exploiter. Il est la référence citée
par la migration `020_file_durable` et par `seamtech_search/worker.py`.

## 1. Le problème corrigé

Avant cette version, un import déposé depuis le navigateur était exécuté par un
**fil d'arrière-plan du processus web**. Conséquences mesurées :

| Situation | Avant | Maintenant |
|---|---|---|
| `docker compose restart web` pendant un import | import tué, tâche disparue de la mémoire, `/imports/{id}` restait **running** pour toujours | l'import continue : il est exécuté par le service `worker`, un autre processus |
| `docker compose restart redis` | les tâches acceptées mais non exécutées étaient perdues (l'API avait répondu 202) | AOF + volume : les tâches sont toujours là ; les claims survivent (test `test_redemarrage_de_redis_conserve_la_tache_acceptee`) |
| worker tué (SIGKILL) en plein import | la tâche restait dans `seamtech:processing` **pour toujours** | la revendication expire, un worker reprend la tâche (test `test_worker_tue_en_plein_import_est_repris_sans_perte_ni_doublon`) |
| lot multi-dossiers interrompu | lot bloqué « en_cours », aucun état terminal, aucune reprise | reprise là où c'était arrêté, dossiers déjà traités non refaits (test `test_lot_interrompu_reprend_sans_refaire_les_dossiers_traites`) |
| Redis injoignable | l'import était accepté (202) puis perdu avec le processus | en production (`SEAMTECH_REQUIRE_DURABLE_QUEUE=true`) : **503 explicite, aucun job créé** ; en développement : 202 avec `durability: "process_memory"` et un avertissement visible |

## 2. Ce qui est promis — et ce qui ne l'est pas

**Promis :**

1. **Rien n'est perdu en silence.** Une tâche est soit exécutée, soit dans la
   file, soit en lettre morte AVEC une raison lisible
   (`/health` → `queue.dead_letters`, `import_jobs.failure_reason`).
2. **La base est le registre de vérité.** `import_jobs` porte l'état durable :
   `status`, `attempts` (combien de fois), `claimed_by` (quel worker),
   `heartbeat_at` (depuis quand), `failure_reason` (pourquoi), `durability`
   (durable / process_memory / sync). Redis ne fait que transporter.
3. **L'acceptation dit la vérité.** Un job n'est marqué `durable` que s'il est
   réellement dans la file Redis. Il n'est JAMAIS écrit « accepté durablement »
   pour un job qui ne vit qu'en mémoire.
4. **La redélivrance est sûre.** Un job déjà `completed` n'est pas réexécuté
   (garde d'idempotence), et le dépôt de dossier s'appuie sur
   `lot_dossier.cle_idempotence` : le même contenu ne crée pas une seconde fiche.
5. **L'annulation traverse les processus.** `POST /imports/{id}/cancel` et
   `POST /lots/{id}/annuler` posent un drapeau Redis lu par le worker ; un lot
   s'arrête **entre deux dossiers**, les restants restent `en_attente`
   (reprenables), aucun dossier n'est laissé « en_cours ».

**PAS promis (et il ne faut pas le documenter comme tel) :**

* **Pas d'« exactly once ».** Redis ne le permet pas. Un job peut être exécuté
  deux fois (worker mort après écriture, avant acquittement) : c'est
  l'**idempotence** qui protège, pas une garantie de livraison unique.
* **Pas de reprise instantanée.** La reprise d'une tâche abandonnée attend
  l'expiration de la revendication (`SEAMTECH_TASK_CLAIM_TTL_SECONDS`, 300 s par
  défaut ; 15 s minimum). Un worker mort est donc détecté en quelques minutes,
  pas en quelques secondes.
* **Pas de cluster.** Un seul Redis. Perdre le serveur Redis sans son volume
  (`redis-data`) perd les tâches acceptées non encore exécutées : la base les
  signale alors comme orphelines (`reconcilier_file`) au lieu de les laisser
  bloquées — mais l'import doit être relancé.
* **Pas de priorité ni de limite de concurrence par utilisateur.** Une seule
  file `imports`, traitée dans l'ordre.

## 3. Anatomie d'une tâche

```
POST /imports (ou /lots)
   │  1. CONTRÔLE  file_durable_disponible() → Redis configuré ET joignable ?
   │               non + require_durable_queue → 503, AUCUN job créé
   │               non + développement          → 202 « process_memory »
   │  2. ÉCRITURE  create_job(..., durability="durable")   ← base = vérité
   │  3. REMISE   enqueue_task("imports", charge)
   ▼
seamtech:queue:imports ──BLMOVE──▶ seamtech:processing:imports
        │                                   │
        │                            seamtech:claim:imports:<job_id>  (TTL)
        │                                   │
        │                            import_jobs.claimed_by + heartbeat_at
        ▼
   worker (seamtech_search.worker_service, processus séparé)
        │  succès  → ack : task hors processing, job completed
        │  échec   → seamtech:retry:imports (backoff 2**n) → nouvelle tentative
        │  épuisé  → seamtech:deadletter:imports + failure_reason en base
        ▼
   /health → queue{queue, processing, retry, deadletter, workers_vivants}
             jobs{par_statut, echecs, en_cours, recoverables}
```

Un lot multi-dossiers est une tâche comme une autre (`{"kind": "lot",
"id_lot": N}`) : le worker appelle `executer_lot`, qui ne traite que les lignes
`lot_dossier` en `en_attente` et marque le lot `relance` avec son `worker_id`.

## 4. Exploitation

```bash
# Le worker tourne-t-il et voit-il sa file ?
docker compose exec -T worker python -m seamtech_search.worker_service --verifier

# Ce que /health expose
docker compose exec -T web python -c \
  "import json,urllib.request,os;r=urllib.request.Request('http://127.0.0.1:8000/health',headers={'X-SEAMTECH-TOKEN':os.environ['SEAMTECH_AUTH_TOKEN']});print(json.dumps(json.load(urllib.request.urlopen(r))['queue'],indent=2))"

# Relancer une tâche en lettre morte : elle est en base avec sa raison
docker compose exec -T postgres psql -U seamtech -d seamtech_search -c \
  "SELECT id, status, attempts, claimed_by, failure_reason FROM import_jobs \
   WHERE status = 'failed' ORDER BY updated_at DESC LIMIT 20;"
```

Réglages (variables d'environnement, valeurs par défaut) :

| Variable | Défaut | Effet |
|---|---|---|
| `SEAMTECH_REQUIRE_DURABLE_QUEUE` | `false` (code) / `true` (compose) | refuse (503) un import quand la file durable est indisponible |
| `SEAMTECH_WEB_WORKER_ENABLED` | `true` (code) / `false` (compose) | le processus web consomme-t-il la file ? En production : non |
| `SEAMTECH_TASK_CLAIM_TTL_SECONDS` | `300` (min 15) | durée avant qu'une tâche d'un worker mort soit reprise |
| `SEAMTECH_MAX_TASK_ATTEMPTS` | `3` (1–20) | au-delà : lettre morte avec raison |
| `queue_reclaim_interval_seconds` | `30` (min 1) | fréquence de reprise des tâches orphelines |

## 5. Ce que les tests prouvent (et où il tournent)

| Test | Ce qui est mesuré | Fichier |
|---|---|---|
| `test_worker_tue_en_plein_import_est_repris_sans_perte_ni_doublon` | vrai processus worker tué par SIGKILL, claim expiré, second worker, 0 doublon d'index | `tests/test_worker_process.py` |
| `test_redemarrage_de_redis_conserve_la_tache_acceptee` | serveur Redis réel tué puis relancé, AOF : tâche et claim toujours là | `tests/test_file_durable_postgres.py` |
| `test_lot_interrompu_reprend_sans_refaire_les_dossiers_traites` | 3 dossiers, interruption, reprise, comptage de fiches, redélivrance | idem |
| `test_tentatives_epuisees_lettre_morte_avec_raison` | épuisement → lettre morte + raison visible | `tests/test_file_durable.py` |
| `test_file_exigee_mais_indisponible_refuse_le_job` | 503, aucun job créé, aucune promesse fausse | idem |
| `test_base_anterieure_a_la_020_demarre_puis_se_migre` | une base d'avant la 020 démarre et se migre | `tests/test_file_durable_postgres.py` |

CI : étape dédiée `pytest -m "redis_queue"` avec un garde-fou
`passed > 0` ET `skipped == 0` — un job vert dont tous les tests auraient été
sautés est refusé (`.github/workflows/ci.yml`).

## 6. Limites connues, à traiter plus tard

* Le worker s'arrête proprement sur SIGTERM mais **ne préempte pas** un import
  en cours : un `docker compose stop worker` attend la fin de la tâche. Un
  `kill -9` laisse la reprise au worker suivant (TTL du claim).
* Une seule file : un import géant retarde les suivants (pas de priorité, pas
  de pool parallèle). À l'échelle de l'atelier (3 postes), c'est acceptable ;
  c'est mesuré, pas supposé.
* La reprise suppose que l'archive est encore là. Si un dossier a été déplacé
  pendant l'interruption, la tâche échoue avec une raison explicite
  (chemin introuvable) — elle n'est pas « réparée » automatiquement.
