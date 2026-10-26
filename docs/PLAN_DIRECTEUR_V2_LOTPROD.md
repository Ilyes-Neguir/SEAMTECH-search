# Plan directeur v2 — Lot PROD : mise en production (audit ECC)

> Branche : `arena/82958474-seamtech-search` · RED : `1059d74` · GREEN : `64812a1` → prouvé : `f8bf885`
> · Méthode : skill ECC **`production-audit`** (preuves locales, aucun envoi
> externe), correctifs prouvés par la boucle `verification-loop`.
> Préalable livré le même jour : fusion des Lots 7+8 dans `main`
> (PR #39, commit de fusion `fdbff54`, 17/17 checks).

## Verdict

**Audit de production : 90/100 — solide (« Strong »)**, contre 78/100
« lançable avec réserves » avant correctifs. Les deux risques qui plafonnaient
le score — secrets d'exemple acceptés au démarrage et sauvegardes jamais
déclenchées — sont corrigés et prouvés ; il ne reste que des risques
documentés et assumés pour un déploiement local d'atelier.

## Surface de production inspectée (preuves)

- **Déploiement** : `docker-compose.yml` (postgres pgvector, MinIO épinglé par
  release, Redis, web, worker, frontend, + `sauvegarde` ajouté par ce lot),
  healthchecks réels partout où `service_healthy` est attendu, ports publiés
  en loopback, secrets obligatoires (`${VAR:?}`), identités de stockage au
  moindre privilège (application ≠ sauvegarde ≠ root) — le tout verrouillé par
  `tests/test_compose_hardening.py`, `test_compose_web_boot.py`,
  `test_compose_partage_worker.py`.
- **Démarrage** : migrations fail-fast (jamais de service sur schéma
  semi-migré), dégradation explicite là où elle est survivable.
- **Sondes** : `/live`, `/ready` (base), `/health` (file durable, workers,
  lettres mortes, jobs, disque, état des sauvegardes, versioning du stockage,
  **version applicative** depuis ce lot), `/metrics`.
- **Auth** : comptes nominatifs + verrouillage, sessions Redis fail-closed,
  cookies httpOnly/SameSite + CSRF double-submit, compte de secours explicite,
  journal d'audit, limitation de débit.
- **CI** : 4 workflows / 13 jobs (matrices Python, e2e navigateur réel sur
  PostgreSQL, OCR réel, sauvegarde/restauration, hors-ligne prouvé, corpus
  réel, sécurité des dépendances, perf), seuil de couverture 85 %.
- **Docs d'exploitation** : `DEPLOYMENT.md` (installation, mises à jour,
  sauvegardes, rollback par lot, dépannage), `TLS.md`,
  `RELEASE_CANDIDATE_CHECKLIST.md`, `CHECKLIST_POSTE_WINDOWS.md`.

## Blockers trouvés → corrigés (RED `1059d74` : 20/20 échecs → GREEN 20/20)

1. **Les secrets d'exemple démarraient en production.** Copier `.env.example`
   (valeurs `change-me` publiques) satisfaisait compose (non-vide) et aucun
   code ne rejetait ces valeurs. Correctif : `exiger_secrets_production`
   (config.py) aux points d'entrée déployés (`serve`, worker, planificateur) —
   exemples et secrets < 8 caractères refusés pour `auth_token`,
   `s3_secret_key` et les mots de passe des URL base/Redis, messages nommant
   les champs sans jamais fuiter une valeur ; côté Next, un mot de passe UI ou
   un secret de session d'exemple vaut « non configuré » (écran fail-closed,
   jamais une clé de signature publique). Prouvé en direct : backend refuse
   de démarrer, login répond 503 avec `change-me`. `create_app` volontairement
   inchangé (les tests y utilisent des jetons courts) ; jetons de recette CI
   (`test-token`, `e2e-service-token`) explicitement préservés par test.
2. **Aucune sauvegarde n'était jamais déclenchée.** La machinerie (Lot H.1 :
   dump, manifeste, rétention, hors-site, restauration vérifiée en CI)
   attendait un opérateur. Correctif : service compose `sauvegarde` +
   `seamtech_search/sauvegarde_planifiee.py` — vérification horaire, sauvegarde
   dès 24 h d'âge (paramétrable), rétention 5, identité hors-site dédiée,
   boucle qui survit aux échecs ; l'état reste celui que `/health` publie.

## Correctifs à forte valeur

3. **Version identifiable** : `__version__ = "1.0.0"` (le plan directeur v2
   est livré et fusionné), exposée par `/health` et FastAPI depuis une source
   unique — en incident, « quelle version tourne ? » ne demande plus de shell.
4. **En-têtes de sécurité HTTP globaux** : CSP `self` (stricte en production,
   assouplie pour HMR en dev seulement), `nosniff`, `X-Frame-Options: DENY`,
   `Referrer-Policy: same-origin`, `Permissions-Policy` — vérifiés par `curl`
   sur le serveur de production construit, et par la suite e2e complète.

## Risques résiduels documentés et acceptés

- **Images `pgvector:pg16` / `redis:7-alpine`** : épinglées au majeur (le test
  de durcissement interdit `latest` ; MinIO est épinglé par release). Le
  pinning par digest exigerait une cadence de mise à jour manuelle que
  l'atelier ne suit pas ; la procédure de mise à jour de `DEPLOYMENT.md`
  (sauvegarde → relevé de version de schéma → déploiement → vérification)
  couvre le risque pour un déploiement mono-poste/LAN.
- **`/ready` ne sonde que la base** : Redis/S3 absents dégradent des fonctions
  sans rendre l'API « not ready » — choix assumé, `/health` donne le détail
  et les healthchecks compose gardent l'ordre de démarrage.
- **Compte de secours partagé** : conservé par décision d'exploitation
  (documenté, journalisé, désactivable en ne le configurant pas) — les comptes
  nominatifs restent la voie normale.
- **Mono-nœud** : compose sur une machine ; la reprise = restauration de
  sauvegarde prouvée, pas de haute disponibilité. Cohérent avec le 100 % local.
- **Aucun tag/release Git** : la version 1.0.0 est dans le code et `/health` ;
  la pose du tag `v1.0.0` et la release GitHub sont un acte de publication —
  proposées ci-dessous, à valider par la commanditaire.

## Preuves du lot

- Local : contrat 20/20, suite Python **1078 passés** (442 live ignorés),
  `ruff` ✓, `tsc` ✓, build de production ✓, en-têtes `curl` ✓, refus de
  démarrage et fail-closed `change-me` en direct ✓.
- **CI au SHA exact `f8bf885` — 4/4 verts** : CI `40375447362`, Qualification
  native `40375447227`, Qualification reserves `40375447419`, Perf comparaison
  `40375447340`. Le premier passage (`64812a1`) était vert partout sauf
  `backend (3.13)` : un test du worker réel utilisait le mot de passe Redis
  « motdepasse » — littéralement un secret d'exemple — et mourait sur le
  nouveau garde-fou avant le contrôle Redis visé. Fixture rendue réaliste
  (la politique, elle, n'a pas bougé), test forcé et vert en local.

## Prochaine action proposée

1. Fusionner ce lot dans `main` (PR, `--merge`, sur autorisation).
2. Poser le tag **`v1.0.0`** sur le commit de fusion et publier la release
   GitHub correspondante (notes = ce rapport + Lots 7/8).
3. Déployer sur le poste atelier en suivant `DEPLOYMENT.md` +
   `CHECKLIST_POSTE_WINDOWS.md` (le service `sauvegarde` protège dès le
   premier jour), puis session de calibration accessoires sur échantillons.
