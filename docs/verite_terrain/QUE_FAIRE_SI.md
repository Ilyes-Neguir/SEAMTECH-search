# Que faire si… — exploitation courante (Lot H.1)

Une page, six situations. Chaque section : symptôme → diagnostic en une
commande → action. Si rien ne résout : la restauration complète est
documentée dans RUNBOOK_RESTAURATION.md.

## 1. Le service est tombé (l'interface ne répond plus)

```bash
curl -f -s http://127.0.0.1:3000/api/health || echo MORT
docker compose ps          # quels services ne sont PAS « healthy » ?
docker compose logs --tail 50 <service en défaut>
```

- Conteneur arrêté seul : `docker compose up -d` le relance (les données sont
  sur volumes, rien n'est perdu).
- Tout est tombé : `docker compose up -d --build`, puis re-contrôler
  `/api/health`.
- Le service repart mais la recherche répond mal → section 4.

## 2. La base est muette (l'API répond mais les données ne viennent pas)

```bash
docker compose exec postgres pg_isready -U seamtech
docker compose exec web python -c "import os, urllib.request; print(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/health', headers={'X-SEAMTECH-TOKEN': os.environ['SEAMTECH_AUTH_TOKEN']}), timeout=5).read())"
```

- `pg_isready` échoue : `docker compose restart postgres` ; vérifier le volume
  (`docker volume ls` — le volume `postgres-data` doit exister).
- Le volume a disparu ou la base est corrompue : restauration depuis la
  dernière sauvegarde hors-site — RUNBOOK_RESTAURATION.md §2. C'est le
  scénario PROUVÉ en CI (base détruite → reconstruite à l'identique).

## 3. Le disque est plein (erreur 507 à l'import)

Le garde-fou est volontaire : sous `SEAMTECH_MIN_FREE_BYTES`, l'import
REFUSE d'écrire (HTTP 507) plutôt que de remplir le disque — comportement
testé (`tests/test_chaos.py`), aucune écriture partielle.

```bash
df -h .                     # combien reste-t-il ?
docker system df            # la part Docker (images, volumes orphelins)
```

- Purger ce qui est jetable : `docker system prune` (JAMAIS `--volumes` —
  les volumes contiennent la base et l'objet MinIO).
- Les dumps locaux dans `data/backups` peuvent être purgés : seul le bucket
  fait foi (RUNBOOK_RESTAURATION.md §1).
- Relever `SEAMTECH_MIN_FREE_BYTES` si le disque est structurellement petit.

## 4. La recherche renvoie vide

```bash
# une requête connue doit répondre (la vraie fiche 7792-SO si importée) :
curl -s -H "X-SEAMTECH-TOKEN: <token>" "http://127.0.0.1:8000/api/search?q=7792-SO"
```

- Aucune fiche `valide` en base : c'est normal — la portée par défaut est
  `valide` ; les fiches attendent la validation humaine (RG3). Valider une
  fiche la rend cherchable.
- Des fiches validées mais rien ne remonte : vérifier l'index (`documents` /
  `search_vector`) via le runbook de recherche — `JEU_REQUETES_REELLES.md`
  donne les requêtes de contrôle et leurs attendus exacts.
- Le jeu attendu manque : le dépôt n'est peut-être pas allé au bout —
  vérifier la file de validation et les journaux du service `web`.

## 5. Une fiche est bloquée à `a_valider`

- État NORMAL le long du parcours : `a_valider` est le statut d'arrivée (RG3) ;
  la fiche attend un humain (interface → file de validation).
- Si la validation échoue en interface : noter le message EXACT + la console
  du navigateur, vérifier `/api/health`, réessayer ; si l'échec persiste,
  comparer avec le banc `validate_extraction` (tests) et joindre la sortie
  brute au rapport d'incident.
- Le chrono de validation attendu : < 120 s par fiche
  (MESURE_VALIDATION_2MIN.md) — au-delà, c'est un symptôme de performance,
  pas un blocage.

## 6. Le hors-site (R2 / bucket) est muet

```bash
python - << 'PY'
from seamtech_search.config import AppConfig
from seamtech_search.storage import S3StorageClient
c = S3StorageClient(config=AppConfig.load())
print("bucket présent :", c.object_exists is not None)
print("objets sauvegarde :", len(c.list_keys("backups/seamtech-search-")))
PY
```

- Erreur de connexion : vérifier `SEAMTECH_S3_ENDPOINT_URL` / clés ; pour R2,
  la résolution DNS et l'accès sortant du poste.
- Le bucket répond mais la dernière sauvegarde est ancienne : le CRON de
  sauvegarde n'est pas en place chez le commanditaire — exécuter
  manuellement `python -m seamtech_search.sauvegarde sauver …` et planifier.
- La re-lecture d'un dump échoue (empreinte) : ne pas restaurer cette
  sauvegarde, prendre la précédente — RUNBOOK_RESTAURATION.md §3.
- Rappel : l'archive n'est JAMAIS dans le bucket (RG13) — le bucket contient
  les dumps + manifestes + documents de la pile ; l'archive vit sur son
  disque, son état est prouvé par l'inventaire du manifeste.
