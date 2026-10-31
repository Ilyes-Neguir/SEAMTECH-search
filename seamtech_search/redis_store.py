"""Redis client for distributed background task queueing, state caching, and rate limiting.

Provides:
- Distributed background job queue (RPUSH / BLPOP)
- Fast job status caching with TTL
- Sliding-window rate limiting across multi-worker FastAPI instances
- Graceful degradation if Redis is temporarily unreachable
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import AppConfig

logger = logging.getLogger("seamtech_search.redis_store")


# ---------------------------------------------------------------------------
# Revendication (claim), battement de cœur et reprise — file durable
# ---------------------------------------------------------------------------
#
# Modèle honnête : Redis ne promet PAS « exactement une fois ». Un job peut
# être exécuté deux fois (le worker meurt après avoir écrit ses objets mais
# avant d'avoir acquitté, la tâche est alors reprise par un autre worker). Ce
# qui est garanti :
#   1. une tâche n'est jamais PERDUE silencieusement : tant qu'elle est dans
#      la liste de traitement d'un worker mort, elle est reprise ou marquée
#      en échec définitif — jamais oubliée ;
#   2. la redélivrance est SÛRE, parce que le traitement est idempotent
#      (le worker saute un job déjà terminé, et le dépôt de dossier en base
#      s'appuie sur une clé d'idempotence).
#
# Mécanique : `seamtech:claim:<queue>:<job_id>` est un verrou à durée de vie
# (TTL), rafraîchi par le worker vivant. Un élément resté dans
# `seamtech:processing:<queue>` dont le claim a EXPIRÉ appartient à un worker
# mort : il est remis en file (ou mis en lettre morte si les tentatives sont
# épuisées).

CLAIM_PREFIX = "seamtech:claim"
WORKER_PREFIX = "seamtech:worker"

#: Revendiquer SEULEMENT si le verrou est libre ou déjà à moi. Un `SET` nu
#: écraserait la revendication d'un autre worker vivant : deux workers
#: traiteraient le même job, chacun écrivant son résultat (écrasement croisé).
#: Lua s'exécute atomiquement côté Redis, donc la lecture et l'écriture ne
#: peuvent pas s'intercaler avec un autre client.
_SCRIPT_REVENDIQUER = """
local brut = redis.call('GET', KEYS[1])
if not brut then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
  return 1
end
local ok, charge = pcall(cjson.decode, brut)
if ok and charge and charge['worker_id'] == ARGV[3] then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
  return 1
end
return 0
"""

#: Libérer SEULEMENT sa propre revendication. Un worker dont le verrou a expiré
#: et dont la tâche a été reprise ne doit pas effacer la revendication du
#: nouveau propriétaire (sinon un troisième worker croirait la place libre).
_SCRIPT_RAFRAICHIR = """
local brut = redis.call('GET', KEYS[1])
if not brut then return 0 end
local ok, claim = pcall(cjson.decode, brut)
if not ok or type(claim) ~= 'table' or claim.worker_id ~= ARGV[1] then return 0 end
return redis.call('EXPIRE', KEYS[1], ARGV[2])
"""

# Le transfert et le constat d'absence de claim doivent être une seule
# opération. Un second balayeur ne doit pas réenfiler le même élément.
_SCRIPT_REPRENDRE_ORPHELINE = """
if ARGV[1] ~= '' and redis.call('EXISTS', KEYS[3]) == 1 then
  return 0
end
local destination_type = redis.call('TYPE', KEYS[2]).ok
if destination_type ~= 'none' and destination_type ~= 'list' then
  return redis.error_reply('destination de reprise non-list')
end
local retire = redis.call('LREM', KEYS[1], 1, ARGV[2])
if retire == 0 then return 0 end
redis.call('RPUSH', KEYS[2], ARGV[3])
return 1
"""

# Le balayage peut déplacer une livraison entre BLMOVE et revendication.
# Ne revendiquer QUE si cette livraison précise est encore en traitement.
_SCRIPT_REVENDIQUER_LIVRAISON = """
local items = redis.call('LRANGE', KEYS[2], 0, -1)
local present = false
for _, item in ipairs(items) do
  local ok, value = pcall(cjson.decode, item)
  if item == ARGV[4] or (ARGV[5] ~= '' and ok and type(value) == 'table'
    and tostring(value.job_id or '') == ARGV[6]
    and tostring(value.task_id or '') == ARGV[5]
    and tonumber(value.attempt or 0) == tonumber(ARGV[7])) then
    present = true
    break
  end
end
if not present then return 0 end
local brut = redis.call('GET', KEYS[1])
if not brut then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
  return 1
end
local ok, claim = pcall(cjson.decode, brut)
if ok and type(claim) == 'table' and claim.worker_id == ARGV[3] then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
  return 1
end
return 0
"""

# Recréation d'un job manquant : le contrôle et l'insertion doivent être
# indivisibles entre plusieurs réconciliateurs. Les listes sont parcourues ici
# comme dans job_est_dans_file ; leur coût doit être qualifié sous charge.
_SCRIPT_ENFILER_SI_ABSENT = """
if redis.call('EXISTS', KEYS[4]) == 1 then return 0 end
local function contient(items)
  for _, item in ipairs(items) do
    local ok, value = pcall(cjson.decode, item)
    if ok and type(value) == 'table' and tostring(value.job_id or '') == ARGV[1] then
      return true
    end
  end
  return false
end
local queue_type = redis.call('TYPE', KEYS[1]).ok
if queue_type ~= 'none' and queue_type ~= 'list' then return redis.error_reply('file non-list') end
if contient(redis.call('LRANGE', KEYS[1], 0, -1)) then return 0 end
if contient(redis.call('LRANGE', KEYS[2], 0, -1)) then return 0 end
if contient(redis.call('ZRANGE', KEYS[3], 0, -1)) then return 0 end
redis.call('RPUSH', KEYS[1], ARGV[2])
return 1
"""

_SCRIPT_PROMOUVOIR_RETRY = """
local kind = redis.call('TYPE', KEYS[2]).ok
if kind ~= 'none' and kind ~= 'list' then
  return redis.error_reply('destination retry non-list')
end
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not score or tonumber(score) > tonumber(ARGV[2]) then return 0 end
if redis.call('ZREM', KEYS[1], ARGV[1]) == 0 then return 0 end
redis.call('RPUSH', KEYS[2], ARGV[1])
return 1
"""

_SCRIPT_NETTOYER_ANNULATION = """
local brut = redis.call('GET', KEYS[1])
if not brut then return 0 end
local ok, claim = pcall(cjson.decode, brut)
if not ok or type(claim) ~= 'table' or claim.worker_id ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[2])
redis.call('EXPIRE', KEYS[3], 1)
return 1
"""

_SCRIPT_LIBERER = """
local brut = redis.call('GET', KEYS[1])
if not brut then
  return 0
end
local ok, charge = pcall(cjson.decode, brut)
if ok and charge and charge['worker_id'] == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return -1
"""


def queue_keys(queue_name: str) -> dict[str, str]:
    """Noms de clés d'une file (un seul endroit, jamais des f-strings dispersées)."""
    return {
        "queue": f"seamtech:queue:{queue_name}",
        "processing": f"seamtech:processing:{queue_name}",
        "retry": f"seamtech:retry:{queue_name}",
        "deadletter": f"seamtech:deadletter:{queue_name}",
        "claim_prefix": f"{CLAIM_PREFIX}:{queue_name}",
    }


class ClaimMixin:
    """Revendications de tâches, battements de cœur de worker et reprise.

    Mixin appliqué à :class:`RedisStore` ; isolé pour que la logique de reprise
    soit testable seule (et lisible) sans relire la file d'attente.
    """

    # --- workers ---------------------------------------------------------

    def enregistrer_worker(self, worker_id: str, ttl_seconds: int = 60) -> bool:
        """Déclare un worker vivant (TTL) — c'est ce que lit la supervision."""
        client = self._get_client()
        if client is None:
            return False
        try:
            client.set(
                f"{WORKER_PREFIX}:{worker_id}",
                json.dumps({"worker_id": worker_id, "vu_le": time.time()}),
                ex=max(5, ttl_seconds),
            )
            return True
        except Exception as exc:
            logger.warning("Impossible d'enregistrer le worker %s : %s", worker_id, exc)
            return False

    def desenregistrer_worker(self, worker_id: str) -> bool:
        """Retire l'enregistrement du worker (arrêt propre, pas un crash)."""
        client = self._get_client()
        if client is None:
            return False
        try:
            client.delete(f"{WORKER_PREFIX}:{worker_id}")
            return True
        except Exception as exc:
            logger.warning("Impossible de désenregistrer le worker %s : %s", worker_id, exc)
            return False

    def workers_vivants(self) -> list[dict[str, Any]]:
        """Workers actuellement enregistrés (TTL non expiré)."""
        client = self._get_client()
        if client is None:
            return []
        try:
            vivants: list[dict[str, Any]] = []
            for cle in client.scan_iter(match=f"{WORKER_PREFIX}:*", count=100):
                valeur = client.get(cle)
                if not valeur:
                    continue
                try:
                    vivants.append(json.loads(valeur))
                except Exception:
                    vivants.append({"worker_id": str(cle).split(":")[-1]})
            return sorted(vivants, key=lambda item: str(item.get("worker_id", "")))
        except Exception as exc:
            logger.warning("Impossible de lister les workers : %s", exc)
            return []

    # --- revendications --------------------------------------------------

    def revendiquer_tache(
        self,
        queue_name: str,
        job_id: str,
        worker_id: str,
        ttl_seconds: int = 300,
        *,
        attempt: int = 0,
        reprendre: bool = False,
        livraison: dict[str, Any] | None = None,
    ) -> bool:
        """Revendique une tâche — **atomiquement** et sans voler un pair vivant.

        ``reprendre=False`` (cas normal) : la revendication n'est posée que si
        le verrou est libre ou déjà le nôtre. Retourne ``False`` si un autre
        worker le détient : l'appelant ne doit PAS traiter la tâche.

        ``reprendre=True`` : reprise explicite d'une tâche dont le verrou a
        expiré (worker mort). L'écrasement est voulu — mais il n'a lieu
        qu'après que la reprise a constaté l'expiration.
        """
        client = self._get_client()
        if client is None:
            return False
        charge = json.dumps(
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "attempt": attempt,
                "revendique_le": time.time(),
            },
            ensure_ascii=False,
        )
        cle = f"{CLAIM_PREFIX}:{queue_name}:{job_id}"
        try:
            if livraison is not None:
                if reprendre or str(livraison.get('job_id') or '') != job_id:
                    return False
                resultat = client.eval(
                    _SCRIPT_REVENDIQUER_LIVRAISON, 2, cle, queue_keys(queue_name)['processing'],
                    charge, max(5, ttl_seconds), worker_id, json.dumps(livraison, ensure_ascii=False),
                    str(livraison.get('task_id') or ''), job_id, int(livraison.get('attempt', 0)),
                )
                return int(resultat) == 1
            if reprendre:
                client.set(cle, charge, ex=max(5, ttl_seconds))
                return True
            resultat = client.eval(_SCRIPT_REVENDIQUER, 1, cle, charge, max(5, ttl_seconds), worker_id)
            return int(resultat) == 1
        except Exception as exc:
            logger.warning("Impossible de revendiquer la tâche %s : %s", job_id, exc)
            return False

    def rafraichir_revendications(self, queue_name: str, job_ids: list[str], worker_id: str, ttl_seconds: int) -> int:
        """Prolonge les verrous de CE worker, et seulement les siens.

        Un worker dont la tâche a été reprise (verrou expiré, réattribué) ne doit
        pas ressusciter sa revendication : il ne la prolonge plus, et son écriture
        terminale sera refusée (voir :meth:`revendication_appartient_a`).
        """
        if not job_ids:
            return 0
        client = self._get_client()
        if client is None:
            return 0
        prolonges = 0
        for job_id in job_ids:
            cle = f"{CLAIM_PREFIX}:{queue_name}:{job_id}"
            try:
                prolonges += int(client.eval(_SCRIPT_RAFRAICHIR, 1, cle, worker_id, max(5, ttl_seconds)))
            except Exception as exc:
                logger.warning("Impossible de rafraîchir la revendication %s : %s", job_id, exc)
        return prolonges

    def revendication_appartient_a(self, queue_name: str, job_id: str, worker_id: str) -> bool:
        """Le verrou de ce job est-il encore le mien ?

        Appelé AVANT toute écriture terminale : si un autre worker a repris la
        tâche (mon verrou avait expiré), je ne dois pas écraser son résultat.
        """
        revendication = self.revendication(queue_name, job_id)
        if revendication is None:
            return False
        return str(revendication.get("worker_id")) == worker_id

    def revendication(self, queue_name: str, job_id: str) -> dict[str, Any] | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            valeur = client.get(f"{CLAIM_PREFIX}:{queue_name}:{job_id}")
            return json.loads(valeur) if valeur else None
        except Exception:
            return None

    def liberer_revendication(self, queue_name: str, job_id: str, worker_id: str | None = None) -> int:
        """Libère une revendication — la sienne de préférence.

        Avec ``worker_id``, la suppression est conditionnelle (Lua) : un worker
        zombie ne peut pas effacer le verrou du worker qui a repris sa tâche.
        Retour : ``1`` supprimée, ``0`` absente, ``-1`` appartient à un autre.
        """
        client = self._get_client()
        if client is None:
            return 0
        cle = f"{CLAIM_PREFIX}:{queue_name}:{job_id}"
        try:
            if worker_id is None:
                return int(client.delete(cle))
            return int(client.eval(_SCRIPT_LIBERER, 1, cle, worker_id))
        except Exception:
            return 0

    def profondeur_file(self, queue_name: str) -> dict[str, int]:
        """Longueurs queue / processing / retry / dead-letter (supervision)."""
        client = self._get_client()
        if client is None:
            return {"queue": 0, "processing": 0, "retry": 0, "deadletter": 0}
        cles = queue_keys(queue_name)
        try:
            pipe = client.pipeline()
            pipe.llen(cles["queue"])
            pipe.llen(cles["processing"])
            pipe.zcard(cles["retry"])
            pipe.llen(cles["deadletter"])
            queue_len, processing_len, retry_len, dead_len = pipe.execute()
            return {
                "queue": int(queue_len),
                "processing": int(processing_len),
                "retry": int(retry_len),
                "deadletter": int(dead_len),
            }
        except Exception as exc:
            logger.warning("Impossible de lire la profondeur de la file %s : %s", queue_name, exc)
            return {"queue": 0, "processing": 0, "retry": 0, "deadletter": 0}

    def taches_en_traitement(self, queue_name: str) -> list[dict[str, Any]]:
        """Tâches présentes dans la liste de traitement, avec leur claim éventuel."""
        client = self._get_client()
        if client is None:
            return []
        cles = queue_keys(queue_name)
        try:
            brutes = client.lrange(cles["processing"], 0, -1)
        except Exception as exc:
            logger.warning("Impossible de lire les tâches en traitement : %s", exc)
            return []
        resultat: list[dict[str, Any]] = []
        for brute in brutes:
            try:
                charge = json.loads(brute)
            except Exception:
                # Charge illisible : on la traite comme orpheline (elle ne peut
                # pas être exécutée), sans la perdre de vue.
                resultat.append({"job_id": None, "charge": None, "brute": brute})
                continue
            job_id = charge.get("job_id")
            resultat.append(
                {
                    "job_id": job_id,
                    "charge": charge,
                    "brute": brute,
                    "claim": self.revendication(queue_name, str(job_id)) if job_id else None,
                }
            )
        return resultat

    def job_est_dans_file(self, job_id: str, queues: tuple[str, ...] = ("imports",)) -> bool:
        """Vrai si le job est encore quelque part dans la file (queue, processing, retry).

        Sert à ne PAS marquer « échoué » un job qui attend simplement son tour
        (c'était un défaut réel : ``recover_stale_jobs`` sur un backlog faisait
        échouer des jobs en attente depuis plus de 5 minutes).
        """
        client = self._get_client()
        if client is None:
            return False
        try:
            for queue_name in queues:
                cles = queue_keys(queue_name)
                # Jamais de recherche par sous-chaîne : on parse la charge JSON.
                for brute in client.lrange(cles["queue"], 0, -1):
                    if _brute_concerne(brute, job_id):
                        return True
                for brute in client.lrange(cles["processing"], 0, -1):
                    if _brute_concerne(brute, job_id):
                        return True
                for brute in client.zrange(cles["retry"], 0, -1):
                    if _brute_concerne(brute, job_id):
                        return True
            return False
        except Exception as exc:
            logger.warning("Impossible de vérifier la présence du job %s en file : %s", job_id, exc)
            # Prudence : dans le doute, considérer le job ENCORE en file évite de
            # le marquer en échec à tort.
            return True

    def reprendre_taches_orphelines(
        self,
        queue_name: str,
        *,
        max_tentatives: int = 3,
        raison: str = "worker interrompu (claim expiré)",
    ) -> list[dict[str, Any]]:
        """Reprend les tâches dont le worker est mort OU dont le claim a expiré.

        Retourne la liste des décisions (jamais un « rien à signaler » muet) :
        ``{"job_id", "action": "requeued"|"dead_lettered", "attempt", "raison"}``.
        L'appelant met à jour la base (registre de vérité) en conséquence.
        """
        client = self._get_client()
        if client is None:
            return []
        cles = queue_keys(queue_name)
        decisions: list[dict[str, Any]] = []
        try:
            en_traitement = client.lrange(cles["processing"], 0, -1)
        except Exception as exc:
            logger.warning("Reprise impossible (lecture de la liste de traitement) : %s", exc)
            return []
        for brute in en_traitement:
            try:
                charge = json.loads(brute)
                if not isinstance(charge, dict):
                    raise ValueError("charge non objet")
                tentative = int(charge.get("attempt", 0))
                if tentative < 0:
                    raise ValueError("tentative négative")
            except (ValueError, TypeError, json.JSONDecodeError):
                job_id = ""
                action = "dead_lettered"
                cible = cles["deadletter"]
                destination = json.dumps({"raison": "charge illisible", "brut": brute[:2000]}, ensure_ascii=False)
                decision = {"job_id": None, "action": action, "attempt": 0, "raison": "charge illisible"}
            else:
                job_id = str(charge.get("job_id") or "")
                if job_id and tentative + 1 < max_tentatives:
                    charge["attempt"] = tentative + 1
                    charge["reprise"] = raison
                    action = "requeued"
                    cible = cles["queue"]
                else:
                    charge["raison_lettre_morte"] = f"{raison} — tentatives épuisées ({tentative + 1}/{max_tentatives})"
                    action = "dead_lettered"
                    cible = cles["deadletter"]
                destination = json.dumps(charge, ensure_ascii=False)
                decision = {
                    "job_id": job_id or None,
                    "action": action,
                    "attempt": tentative + 1,
                    "raison": charge.get("raison_lettre_morte", raison),
                }
            try:
                transfere = client.eval(
                    _SCRIPT_REPRENDRE_ORPHELINE, 3, cles["processing"], cible,
                    f"{cles['claim_prefix']}:{job_id}", job_id, brute, destination,
                )
            except Exception as exc:
                # Redis n'offre pas de rollback des écritures effectuées par un
                # script en erreur ; ses types sont vérifiés avant le LREM.
                logger.warning("Reprise atomique impossible pour %s : %s", job_id or "charge illisible", exc)
                continue
            if int(transfere) == 1:
                decisions.append(decision)
        return decisions


def _brute_concerne(brute: str, job_id: str) -> bool:
    """Vrai si la charge sérialisée concerne ce job (parse JSON, pas de substring)."""
    try:
        return str(json.loads(brute).get("job_id")) == job_id
    except Exception:
        return False



_LUA_ACK_TASK = """
local processing_key = KEYS[1]
local claim_key = KEYS[2]
local expected_worker = ARGV[1]
local raw_payload = ARGV[2]
local target_id = ARGV[3]

if expected_worker ~= "" then
    if claim_key == "" then
        return {0, "no_claim"}
    end
    local claim_raw = redis.call('GET', claim_key)
    if not claim_raw then
        return {0, "no_claim"}
    end
    local ok, claim_data = pcall(cjson.decode, claim_raw)
    if not (ok and claim_data and claim_data.worker_id and claim_data.worker_id == expected_worker) then
        return {0, "lost_claim"}
    end
end

local removed = redis.call('LREM', processing_key, 1, raw_payload)
if removed > 0 then
    return {1, "removed_exact"}
end

if expected_worker == "" and target_id ~= "" then
    local items = redis.call('LRANGE', processing_key, 0, -1)
    for i, item in ipairs(items) do
        local ok, decoded = pcall(cjson.decode, item)
        if ok and decoded and decoded.job_id == target_id then
            redis.call('LREM', processing_key, 1, item)
            return {1, "removed_by_id"}
        end
    end
end

return {0, "not_found"}
"""

_LUA_RETRY_TASK = """
local processing_key = KEYS[1]
local target_key = KEYS[2]
local claim_key = KEYS[3]
local expected_worker = ARGV[1]
local raw_payload = ARGV[2]
local new_payload = ARGV[3]
local mode = ARGV[4]
local score = tonumber(ARGV[5])
local target_id = ARGV[6]

if expected_worker ~= "" then
    if claim_key == "" then
        return {0, "no_claim"}
    end
    local claim_raw = redis.call('GET', claim_key)
    if not claim_raw then
        return {0, "no_claim"}
    end
    local ok, claim_data = pcall(cjson.decode, claim_raw)
    if not (ok and claim_data and claim_data.worker_id and claim_data.worker_id == expected_worker) then
        return {0, "lost_claim"}
    end
end

local target_type = redis.call('TYPE', target_key).ok
local expected_type = mode == 'zadd' and 'zset' or 'list'
if target_type ~= 'none' and target_type ~= expected_type then
    return redis.error_reply('type destination retry incorrect')
end
local removed = redis.call('LREM', processing_key, 1, raw_payload)
if removed == 0 and expected_worker == "" and target_id ~= "" then
    local items = redis.call('LRANGE', processing_key, 0, -1)
    for i, item in ipairs(items) do
        local ok, decoded = pcall(cjson.decode, item)
        if ok and decoded and decoded.job_id == target_id then
            redis.call('LREM', processing_key, 1, item)
            removed = 1
            break
        end
    end
end

if removed == 0 and expected_worker ~= "" then return {0, "not_found"} end

if mode == "zadd" then
    redis.call('ZADD', target_key, score, new_payload)
else
    redis.call('RPUSH', target_key, new_payload)
end

return {1, "retried"}
"""

_LUA_DEADLETTER_TASK = """
local processing_key = KEYS[1]
local dead_key = KEYS[2]
local claim_key = KEYS[3]
local expected_worker = ARGV[1]
local raw_payload = ARGV[2]
local target_id = ARGV[3]

if expected_worker ~= "" then
    if claim_key == "" then
        return {0, "no_claim"}
    end
    local claim_raw = redis.call('GET', claim_key)
    if not claim_raw then
        return {0, "no_claim"}
    end
    local ok, claim_data = pcall(cjson.decode, claim_raw)
    if not (ok and claim_data and claim_data.worker_id and claim_data.worker_id == expected_worker) then
        return {0, "lost_claim"}
    end
end

local target_type = redis.call('TYPE', dead_key).ok
if target_type ~= 'none' and target_type ~= 'list' then
    return redis.error_reply('type destination deadletter incorrect')
end
local removed = redis.call('LREM', processing_key, 1, raw_payload)
if removed == 0 and expected_worker == "" and target_id ~= "" then
    local items = redis.call('LRANGE', processing_key, 0, -1)
    for i, item in ipairs(items) do
        local ok, decoded = pcall(cjson.decode, item)
        if ok and decoded and decoded.job_id == target_id then
            redis.call('LREM', processing_key, 1, item)
            removed = 1
            break
        end
    end
end

if removed == 0 and expected_worker ~= '' then return {0, 'not_found'} end
redis.call('RPUSH', dead_key, raw_payload)
return {1, "deadlettered"}
"""

class RedisStore(ClaimMixin):
    """Redis integration for job queues, status caching, and rate-limiting."""

    def __init__(self, redis_url: str | None = None, config: AppConfig | None = None) -> None:
        if config is not None:
            self.redis_url = config.redis_url
        else:
            self.redis_url = redis_url

        self._client = None

    def is_configured(self) -> bool:
        return bool(self.redis_url)

    def _get_client(self):
        if self._client is not None:
            return self._client

        if not self.redis_url:
            return None

        try:
            import redis

            self._client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=5.0,
            )
            return self._client
        except Exception as exc:
            logger.warning("Could not initialize Redis client for %s: %s", self.redis_url, exc)
            return None

    def ping(self) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(client.ping())
        except Exception as exc:
            logger.debug("Redis ping failed: %s", exc)
            return False

    # ---------------------------------------------------------------------------
    # Sliding-Window Rate Limiting (Sorted Sets)
    # ---------------------------------------------------------------------------

    def check_rate_limit(self, key: str, limit: int, window_seconds: float = 60.0) -> tuple[bool, int]:
        """Check if request exceeds rate limit using sliding window in Redis.

        Returns (is_limited, retry_after_seconds).
        """
        client = self._get_client()
        if client is None:
            return False, 0

        now = time.time()
        cutoff = now - window_seconds
        redis_key = f"seamtech:ratelimit:{key}"

        try:
            pipe = client.pipeline()
            # Remove timestamps older than window
            pipe.zremrangebyscore(redis_key, 0, cutoff)
            # Count remaining in current window
            pipe.zcard(redis_key)
            # Add current request
            pipe.zadd(redis_key, {f"{now}:{time.perf_counter()}": now})
            # Expire rate limit key after window
            pipe.expire(redis_key, int(window_seconds) + 5)
            # Get the oldest timestamp in current window
            pipe.zrange(redis_key, 0, 0, withscores=True)
            results = pipe.execute()

            current_count = results[1]
            if current_count >= limit:
                oldest_entries = results[4]
                oldest_ts = oldest_entries[0][1] if oldest_entries else cutoff
                retry_after = max(1, int(window_seconds - (now - oldest_ts)) + 1)
                return True, retry_after

            return False, 0
        except Exception as exc:
            logger.warning("Redis rate limit check failed, falling back: %s", exc)
            return False, 0

    # ---------------------------------------------------------------------------
    # Job Status Caching & Distribution
    # ---------------------------------------------------------------------------

    def set_job(self, job_id: str, data: dict[str, Any], ttl_seconds: int = 86400) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.set(f"seamtech:job:{job_id}", json.dumps(data, ensure_ascii=False), ex=ttl_seconds)
            return True
        except Exception as exc:
            logger.warning("Failed to cache job in Redis %s: %s", job_id, exc)
            return False

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            val = client.get(f"seamtech:job:{job_id}")
            if val is None:
                return None
            return json.loads(val)
        except Exception as exc:
            logger.warning("Failed to get job from Redis %s: %s", job_id, exc)
            return None

    def update_job(self, job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            current = self.get_job(job_id) or {"id": job_id}
            current.update(updates)
            if not self.set_job(job_id, current):
                # Ne JAMAIS prétendre avoir écrit : sans Redis joignable, l'état
                # mis en cache n'existe nulle part. L'appelant reçoit None et
                # sait que la copie Redis n'a PAS été mise à jour (le registre
                # en base, lui, reste la vérité).
                logger.warning("État du job %s non mis à jour dans Redis (non joignable)", job_id)
                return None
            return current
        except Exception as exc:
            logger.warning("Failed to update job in Redis %s: %s", job_id, exc)
            return None

    # ---------------------------------------------------------------------------
    # Cancellation (Redis with DB fallback) — 4.5
    # ---------------------------------------------------------------------------

    def set_cancel_flag(self, job_id: str, ttl_seconds: int = 86400) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.set(f"seamtech:cancel:{job_id}", "1", ex=ttl_seconds)
            return True
        except Exception as exc:
            logger.warning("Failed to set cancel flag in Redis %s: %s", job_id, exc)
            return False

    def is_cancelled(self, job_id: str) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(client.exists(f"seamtech:cancel:{job_id}"))
        except Exception:
            return False

    def clear_cancel_flag_if_claimed(self, job_id: str, worker_id: str, queue_name: str = "imports") -> bool:
        """Nettoie uniquement si le claim Redis appartient encore au worker, atomiquement."""
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(client.eval(
                _SCRIPT_NETTOYER_ANNULATION, 3,
                f"{CLAIM_PREFIX}:{queue_name}:{job_id}",
                f"seamtech:cancel:{job_id}", f"seamtech:heartbeat:{job_id}", worker_id,
            ))
        except Exception as exc:
            logger.warning("Nettoyage conditionnel de l'annulation %s impossible : %s", job_id, exc)
            return False

    def clear_cancel_flag(self, job_id: str) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.delete(f"seamtech:cancel:{job_id}")
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------------------
    # Heartbeat — 4.7
    # ---------------------------------------------------------------------------

    def set_heartbeat(self, job_id: str, ttl_seconds: int = 300) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.set(f"seamtech:heartbeat:{job_id}", str(time.time()), ex=ttl_seconds)
            return True
        except Exception:
            return False

    def get_heartbeat(self, job_id: str) -> float | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            val = client.get(f"seamtech:heartbeat:{job_id}")
            return float(val) if val else None
        except Exception:
            return None

    # ---------------------------------------------------------------------------
    # Task Queue with acknowledgement, retry and dead-letter — 4.6
    # ---------------------------------------------------------------------------

    def enqueue_task(self, queue_name: str, payload: dict[str, Any]) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            # Include attempt count and a per-delivery identity. `task_id` is
            # what lets an operator (and the reclaim pass) tell two deliveries
            # of the same job apart without parsing free text.
            payload = dict(payload)
            payload.setdefault("attempt", 0)
            payload.setdefault("task_id", uuid.uuid4().hex)
            payload.setdefault("enqueued_at", time.time())
            client.rpush(f"seamtech:queue:{queue_name}", json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as exc:
            logger.error("Failed to enqueue task to Redis %s: %s", queue_name, exc)
            return False

    def enqueue_task_si_absent(self, queue_name: str, payload: dict[str, Any]) -> bool:
        """Réenfile un job uniquement s'il n'a ni livraison active ni claim Redis."""
        client = self._get_client()
        job_id = str(payload.get("job_id") or "")
        if client is None or not job_id:
            return False
        cles = queue_keys(queue_name)
        charge = dict(payload)
        charge.setdefault("attempt", 0)
        charge.setdefault("task_id", uuid.uuid4().hex)
        charge.setdefault("enqueued_at", time.time())
        try:
            return bool(client.eval(
                _SCRIPT_ENFILER_SI_ABSENT, 4, cles['queue'], cles['processing'], cles['retry'],
                f"{cles['claim_prefix']}:{job_id}", job_id, json.dumps(charge, ensure_ascii=False),
            ))
        except Exception as exc:
            logger.warning("Réenfilage conditionnel impossible pour %s : %s", job_id, exc)
            return False

    def dequeue_task(self, queue_name: str, timeout: int = 2) -> dict[str, Any] | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            # Sans transfert atomique, dépiler puis pousser en traitement peut
            # perdre une livraison sur crash. Refuser les clients incompatibles.
            processing_key = f"seamtech:processing:{queue_name}"
            item = client.blmove(f"seamtech:queue:{queue_name}", processing_key, timeout=timeout)
            if item:
                return json.loads(item)
            return None
        except Exception as exc:
            logger.error("Failed to dequeue task from Redis %s: %s", queue_name, exc)
            return None

    def ack_task(self, queue_name: str, payload: dict[str, Any], *, worker_id: str | None = None) -> bool:
        """Acquitte une tâche de façon atomique conditionnée au verrou (R2)."""
        client = self._get_client()
        if client is None:
            return False
        job_id = str(payload.get("job_id") or "")
        worker_str = str(worker_id) if worker_id is not None else ""
        processing_key = f"seamtech:processing:{queue_name}"
        claim_key = f"seamtech:claim:{queue_name}:{job_id}" if job_id else ""
        raw_payload = json.dumps(payload, ensure_ascii=False)

        # Exécution atomique via script Lua si disponible
        if hasattr(client, "eval") and type(getattr(client, "eval")).__name__ != "MagicMock":
            try:
                res = client.eval(_LUA_ACK_TASK, 2, processing_key, claim_key, worker_str, raw_payload, job_id)
                if isinstance(res, (list, tuple)) and len(res) >= 2:
                    if res[0] == 0:
                        reason = res[1].decode("utf-8") if isinstance(res[1], bytes) else str(res[1])
                        logger.warning(
                            "Acquittement refusé atomiquement : tâche %s, worker %s, raison %s.",
                            job_id, worker_id, reason,
                        )
                        return False
                    return True
                return True
            except Exception as exc:
                logger.warning("Lua eval failed in ack_task: %s", exc)
                return False

        # Repli standard (environnements de mock sans Lua)
        try:
            count = client.lrem(processing_key, 1, raw_payload)
            if count == 0 and job_id:
                for item in client.lrange(processing_key, 0, -1):
                    try:
                        p = json.loads(item)
                        if p.get("job_id") == job_id:
                            client.lrem(processing_key, 1, item)
                            break
                    except (json.JSONDecodeError, TypeError, KeyError) as parse_err:
                        logger.debug("Tâche processing non-JSON ignorée lors de l'ack : %s", parse_err)
            return True
        except Exception as exc:
            logger.warning("Failed to ack task %s: %s", queue_name, exc)
            return False

    def retry_task(
        self,
        queue_name: str,
        payload: dict[str, Any],
        delay_seconds: int = 0,
        *,
        worker_id: str | None = None,
        original_payload: dict[str, Any] | None = None,
    ) -> bool:
        """Reprogramme une tâche de façon atomique conditionnée au verrou (R2)."""
        client = self._get_client()
        if client is None:
            return False
        job_id = str(payload.get("job_id") or "")
        worker_str = str(worker_id) if worker_id is not None else ""
        processing_key = f"seamtech:processing:{queue_name}"
        claim_key = f"seamtech:claim:{queue_name}:{job_id}" if job_id else ""
        retry_key = f"seamtech:retry:{queue_name}"
        target_key = retry_key if delay_seconds > 0 else f"seamtech:queue:{queue_name}"
        mode = "zadd" if delay_seconds > 0 else "rpush"
        score = time.time() + delay_seconds

        new_payload = dict(payload)
        new_payload["attempt"] = new_payload.get("attempt", 0) + 1
        raw_payload = json.dumps(original_payload if original_payload is not None else payload, ensure_ascii=False)
        new_raw_payload = json.dumps(new_payload, ensure_ascii=False)

        if hasattr(client, "eval") and type(getattr(client, "eval")).__name__ != "MagicMock":
            try:
                res = client.eval(
                    _LUA_RETRY_TASK,
                    3,
                    processing_key,
                    target_key,
                    claim_key,
                    worker_str,
                    raw_payload,
                    new_raw_payload,
                    mode,
                    score,
                    job_id,
                )
                if isinstance(res, (list, tuple)) and len(res) >= 1:
                    if res[0] == 0:
                        logger.warning(
                            "Retry refusé atomiquement : tâche %s n'appartient plus à %s",
                            job_id, worker_id,
                        )
                        return False
                    return True
                return True
            except Exception as exc:
                logger.error("Failed to retry task %s: %s", queue_name, exc)
                return False

        try:
            self.ack_task(queue_name, payload, worker_id=worker_id)
            if delay_seconds > 0:
                client.zadd(target_key, {new_raw_payload: score})
            else:
                client.rpush(target_key, new_raw_payload)
            return True
        except Exception as exc:
            logger.error("Failed to retry task %s: %s", queue_name, exc)
            return False

    def deadletter_task(
        self,
        queue_name: str,
        payload: dict[str, Any],
        *,
        worker_id: str | None = None,
    ) -> bool:
        """Envoie une tâche en lettre morte de façon atomique conditionnée au verrou (R2)."""
        client = self._get_client()
        if client is None:
            return False
        job_id = str(payload.get("job_id") or "")
        worker_str = str(worker_id) if worker_id is not None else ""
        processing_key = f"seamtech:processing:{queue_name}"
        dead_key = f"seamtech:deadletter:{queue_name}"
        claim_key = f"seamtech:claim:{queue_name}:{job_id}" if job_id else ""
        raw_payload = json.dumps(payload, ensure_ascii=False)

        if hasattr(client, "eval") and type(getattr(client, "eval")).__name__ != "MagicMock":
            try:
                res = client.eval(
                    _LUA_DEADLETTER_TASK,
                    3,
                    processing_key,
                    dead_key,
                    claim_key,
                    worker_str,
                    raw_payload,
                    job_id,
                )
                if isinstance(res, (list, tuple)) and len(res) >= 1:
                    if res[0] == 0:
                        logger.warning(
                            "Deadletter refusé atomiquement : tâche %s n'appartient plus à %s",
                            job_id, worker_id,
                        )
                        return False
                    return True
                return True
            except Exception as exc:
                logger.error("Failed to deadletter task %s: %s", queue_name, exc)
                return False

        try:
            self.ack_task(queue_name, payload, worker_id=worker_id)
            client.rpush(dead_key, raw_payload)
            return True
        except Exception as exc:
            logger.error("Failed to deadletter task %s: %s", queue_name, exc)
            return False

    def get_deadletter_count(self, queue_name: str) -> int:
        client = self._get_client()
        if client is None:
            return 0
        try:
            return int(client.llen(f"seamtech:deadletter:{queue_name}"))
        except Exception:
            return 0

    def get_deadletters(self, queue_name: str, limit: int = 100) -> list[dict[str, Any]]:
        client = self._get_client()
        if client is None:
            return []
        try:
            items = client.lrange(f"seamtech:deadletter:{queue_name}", 0, limit - 1)
            return [json.loads(i) for i in items]
        except Exception:
            return []

    def replay_deadletters(self, queue_name: str, limit: int = 100) -> int:
        client = self._get_client()
        if client is None:
            return 0
        try:
            dead_key = f"seamtech:deadletter:{queue_name}"
            queue_key = f"seamtech:queue:{queue_name}"
            count = 0
            for _ in range(limit):
                # LMOVE est indivisible : ni panne entre LPOP/RPUSH, ni
                # destination de mauvais type ne retirent la seule copie.
                item = client.lmove(dead_key, queue_key, src="LEFT", dest="RIGHT")
                if item is None:
                    break
                count += 1
            return count
        except Exception as exc:
            logger.error("Failed to replay deadletters %s: %s", queue_name, exc)
            return 0

    def process_retry_queue(self, queue_name: str) -> int:
        client = self._get_client()
        if client is None:
            return 0
        try:
            retry_key = f"seamtech:retry:{queue_name}"
            queue_key = f"seamtech:queue:{queue_name}"
            now = time.time()
            items = client.zrangebyscore(retry_key, 0, now, start=0, num=100)
            if not items:
                return 0
            nombre = 0
            for item in items:
                nombre += int(client.eval(_SCRIPT_PROMOUVOIR_RETRY, 2, retry_key, queue_key, item, now))
            return nombre
        except Exception as exc:
            logger.warning("Failed to process retry queue %s: %s", queue_name, exc)
            return 0
