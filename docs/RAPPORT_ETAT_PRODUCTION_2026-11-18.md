# Rapport d'état — SEAMTECH Search (production / Cloudflare)

**Date :** 2026-11-18 · **Branche :** `arena/765ebdcf-seamtech-search` (PR #44 ouverte)
**HEAD vérifié :** `4d05d89f1ac873c05e077fb4a1ad3bde1145d9bb`
**CI à ce SHA :** 45 check-runs terminés — **43 success, 2 skipped, 0 failure,
0 cancelled** (run CI : https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/44487327735).
Les 2 `skipped` sont `recette-corpus-reel`, sauté par conception (corpus réel
purgé, repli synthétique utilisé).

Ce rapport dit, point par point : **ce qui est fait**, **ce qui manque**, et
**ce qu'il reste à faire** pour une mise en production derrière Cloudflare.
Règle appliquée : rien n'est déclaré terminé sans preuve (commande + résultat).

---

## 1. Ce qui a été fait

### 1.1 Vague 1 — sécurité / données (bloquant production)

| Item | Statut | Preuve |
|---|---|---|
| Purge (a) — fixtures synthétiques + recalcul des tests | ✅ Fait | Commits poussés ; suite 3021 verts |
| Purge (b) — épreuve à blanc sur clone jetable + rapport chiffré | ✅ Fait | Pack 16,72 → 9,21 Mo ; 0 donnée client dans l'historique ; suite verte sur clone purgé |
| Sauvegarde quotidienne — test déclenchement réel + artefact valide | ✅ Fait | `test_un_passage_planifie_ecrit_un_dump_et_un_manifeste_reels` ; job CI `sauvegarde` vert |
| Provisionnement Git Bash | ✅ Fait | `c69cebf` (`MSYS_NO_PATHCONV=1`) |
| Guide de mise en service étapes 4/5/7 | ✅ Fait | `c69cebf` |

### 1.2 Étape 2 — CI verte

Trois rouges, tous de mon fait, corrigés et prouvés verts : comparaison de
performance (alias par motif), audit exigeant le document réel purgé
(contrôle inversé), build Docker (curl réessayé). CI entièrement verte à
`4d05d89` : 43 success, 2 skipped attendus, 0 échec.

### 1.3 Production Cloudflare (choix : conteneurs + Cloudflare devant, internet public)

- **Durcissement internet public** (`e5eb9de`, 4 tests dédiés) : HSTS (front +
  API), en-têtes de sécurité API (y compris 401/429), validation d'hôte
  (`SEAMTECH_ALLOWED_HOSTS`).
- **Guide de déploiement Cloudflare** (`fd3f170`, `docs/DEPLOYMENT_CLOUDFLARE.md`) :
  Cloudflare Tunnel (aucun port ouvert), variables de production, WAF,
  vérifications.
- **Posture préexistante** (déjà en place) : aucun secret en dur, comparaison
  de jeton en temps constant, cookies de session signés et révocables,
  rate limiting Redis, limites d'upload, jetons jamais journalisés en clair,
  garde `behind_tls_proxy` obligatoire hors loopback, endpoints de santé.

### 1.4 Vérifications globales

- Suite complète locale : **3021 passed** (+4 tests de durcissement).
- `ruff check .` : **All checks passed**.
- Dépôt réel intact : aucune réécriture, aucun `push --force`.

---

## 2. Ce qui manque (non fait)

| # | Élément | Pourquoi ce n'est pas fait |
|---|---|---|
| M1 | **`push --force` réel de la purge** | **Volontairement en attente** : décision explicite de l'utilisateur (« attendu, pas maintenant »). Commande exacte prête dans le rapport d'épreuve à blanc. |
| M2 | **Vagues 2 à 5 du plan** | Non entamées ; détail non disponible dans cette session. |
| M3 | ~~Job CI `backend (3.13)`~~ **RÉSOLU** | À `fd3f170`, le job avait été annulé après ~2 h bloqué sur `apt-get` (infra). Au SHA vérifié `4d05d89`, il est **success** : 0 annulation. |
| M4 | **Déploiement réel chez Cloudflare** | Le guide est rédigé, mais aucune infrastructure n'a été provisionnée (domaine, tunnel, hôte). Action de l'exploitant. |
| M5 | **Fichier local non suivi** `docs/RECETTE_EXHAUSTIVE_2026-11-18.md` | Artefact local, sans effet sur les tests. Non commité. |

---

## 3. Ce qu'il reste à faire pour la production

### 3.1 Action de l'exploitant (déploiement)

1. **Hôte conteneur** : `docker compose up -d` (voir `docs/DEPLOYMENT_CLOUDFLARE.md`).
2. **`.env` de production** : `SEAMTECH_BEHIND_TLS_PROXY=true`,
   `SEAMTECH_ALLOW_NETWORK_ACCESS=true`,
   `SEAMTECH_ALLOWED_HOSTS=<vrai-domaine>`, secrets longs et uniques.
3. **Cloudflare Tunnel** : `cloudflared tunnel run seamtech` (domaine + DNS).
4. **WAF** : Managed Ruleset + OWASP Core Ruleset + rate limiting sur `/api/*`.
5. **Vérifications** : `curl -sI https://<domaine>/` (HSTS/CSP), refus sans
   jeton (401), rejet d'hôte non déclaré.

### 3.2 Décision en attente (utilisateur)

- **Déclencher le `push --force` de la purge** sur mot explicite et distinct.
  Effets assumés : réécriture de `main` (624+ commits), clones existants
  invalides, PR #44 à recréer, SHAs cités caducs.

### 3.3 Hygiène (recommandé, non bloquant)

- Décider du sort de `docs/RECETTE_EXHAUSTIVE_2026-11-18.md` (committer ou supprimer).
- Si les vagues 2 à 5 sont requises : en redonner le contenu pour les traiter.

---

## 4. Verdict

**L'application est prête pour un déploiement Cloudflare** (conteneurs +
façade Cloudflare, internet public) : code durci et prouvé, guide rédigé,
sauvegarde prouvée, **CI entièrement verte à `4d05d89`** (43 success,
2 skipped attendus, 0 échec).

**Le seul geste irréversible restant est le `push --force` de la purge**, qui
attend votre décision explicite. Aucune fusion vers `main` n'a été faite.
