# Audit de production

Établi avec la compétence ECC `production-audit` (`.agents/skills/production-audit/SKILL.md`),
par preuves locales uniquement : aucun dépôt de données chez un tiers, aucun
scanneur externe, aucun code distant exécuté.

**Verdict : 86/100 — solide, avec un corpus incomplet comme seule réserve
sérieuse.** Deux réserves pèsent sur le score : le corpus n'est rempli qu'à
46 fiches sur ~338, et la recherche insensible aux accents dépend d'extensions
PostgreSQL que l'opérateur doit installer lui-même.

Le plafond de 84 prévu par la compétence (« si la CI n'est pas verte ou si le
parcours critique n'a pas été éprouvé de bout en bout ») **ne s'applique pas** :
la CI est verte au HEAD et le parcours critique est mesuré. Le plafond de 69 ne
s'applique pas davantage : authentification présente, aucune surface de
paiement, migrations sûres, aucun secret consigné, retour arrière documenté.

---

## Surface examinée

HEAD audité : `e74afcd`. Branche `arena/765ebdcf-seamtech-search`.

## Ce qui a été mesuré, et comment

Chaque ligne ci-dessous est une commande reproductible, pas une appréciation.

| Constat | Preuve |
|---|---|
| CI verte au HEAD, 13 jobs sur 13 | `gh api …/actions/runs/42922261618/jobs` → `13 success`, `e2e` compris |
| E2E sans flaky | annotations du job `e2e` : « 3 parcours Lot 6 verts **au premier essai** » (c'était « 2 passés, 1 flaky » avant correction) |
| Parcours critique mesuré | « mesure-phase1 : parcours machine complet de la vraie fiche 7792-SO … 993 ms » (critère < 120 000 ms) |
| Accessibilité | « accessibilite : 82 écrans scannés, 0 violation WCAG 2.1 A/AA » |
| Vérification manuelle | `python scripts/verification_manuelle.py` → **24 épreuves, 24 OK, 0 ÉCHEC** contre un serveur vivant |
| Contrôle de santé | `GET /health` → 200, état des dépendances réellement interrogées |
| Limitation de débit | 700 requêtes sur `/` → 200 puis **429** ; premier refus à la 201ᵉ (seuil 600/min) |
| Aucun secret consigné | recherche de motifs `password|secret|token|api_key` sur `.py/.ts/.tsx/.yml/.md` → 0 résultat hors documentation |
| Schéma rejouable | 249 occurrences de `CREATE TABLE IF NOT EXISTS` ; 32 migrations suivies |
| Journaux sans jeton en clair | `actor_fingerprint` empreinte en SHA-256 tronqué (`audit.py:64`) ; éprouvé (`test_jobs_indexer_coverage.py:217`) |

## Le point de contrôle santé est meilleur que la moyenne

`GET /health` ne se contente pas de répondre `ok`. Il rend :

```
schema_migrations         32 migrations énumérées
schema_metier_a_jour      True
schema_application_a_jour True
extensions                {'vector': True, 'pg_trgm': False, 'unaccent': False, 'applicables': True}
disk_free_bytes / disk_total_bytes
redis_connected, s3_configured, backup, jobs, upload_dead_letters
```

Deux détails valent d'être relevés, parce qu'ils sont rares :

- **les extensions manquantes sont visibles**, pas silencieuses. L'opérateur
  voit `unaccent: False` au lieu de découvrir six mois plus tard que
  « regate » ne trouvait pas « Régate » ;
- **la sauvegarde dit sa propre limite** : « Une copie sur le même serveur
  n'est PAS une sauvegarde de reprise : configurer le bucket distant ». Un
  point de contrôle santé qui ment par omission est pire qu'un point de
  contrôle qui s'avoue insuffisant.

`/live` et `/ready` sont dispensés de limitation de débit (bon : une sonde ne
doit pas s'auto-bloquer), `/metrics` **ne l'est pas** et exige le jeton —
vérifié, il rend 401 sans lui.

## Sécurité et authentification — aucun manquement bloquant

- Authentification **appliquée côté serveur** : jeton de service pour l'accès,
  puis **session nominative** pour toute décision (valider une fiche exige un
  approbateur identifié ; le jeton de service seul est refusé, vérifié en 401).
  Une décision est attributable, ce qui est la bonne conception.
- **Aucun secret consigné** : la recherche couvre les fichiers de code, de
  configuration et de documentation. Les deux seules concordances sont un
  exemple pédagogique dans une compétence ECC et un nom de couleur.
- **Limitation de débit active**, et pas seulement définie : elle a été
  éprouvée jusqu'au refus. Elle possède en outre un repli en mémoire quand
  Redis n'est pas configuré (`api.py:427`) — donc pas de protection qui
  disparaît discrètement en l'absence de Redis.
- **Aucun middleware CORS** : pas de joker `*` à risque.
- **Aucune surface de paiement ni de webhook** dans l'application : la lentille
  correspondante de la compétence ne s'applique pas.

## Intégrité des données

Schéma créé en `CREATE TABLE IF NOT EXISTS` (249 occurrences) : rejouable sans
effet de bord. Les 32 migrations sont énumérées et l'état d'à-jour est rendu
par `/health`. Le retour arrière est documenté lot par lot dans
`docs/DEPLOYMENT.md` (« Rollback is a code revert plus redeploy », avec ce qui
doit être conservé et ce qui peut être laissé).

## Les réserves — ce qui empêche de mettre 95

**1. Le corpus n'est rempli qu'à 46 fiches sur ~338.** C'est la réserve
principale. Ce n'est pas un défaut du code : l'outil ingère, indexe et restitue
correctement ce qu'on lui donne. Mais un service de recherche n'a de valeur que
par ce qu'il contient, et là il contient 14 % du fonds attendu. Le relevé du
reste est **suspendu sur décision métier**, pas oublié. À ne pas lire comme un
feu vert de mise en service : c'est un feu vert de mise en service *technique*.

**2. `unaccent` et `pg_trgm` sont des prérequis d'exploitation.** Sans eux
l'application ne casse pas — elle dégrade, avec la conséquence écrite dans le
journal (`indexer.py:1091`) et rendue visible par `/health`. Mais la recherche
insensible aux accents et la tolérance aux fautes de frappe dépendent d'un
`CREATE EXTENSION` que l'opérateur doit exécuter. C'est documenté
(`docs/DEPLOYMENT.md`), ce qui en fait un prérequis connu et non un piège —
d'où une retenue modeste plutôt qu'un blocage.

**3. Réserves d'environnement, non du code.** Sur le poste d'essai, S3, Redis
et la sauvegarde distante ne sont pas configurés (`s3_configured: False`,
`redis_connected: null`) et le PostgreSQL local est fourni par `pgserver`,
distribué sans les modules contrib. Ces trois points expliquent aussi les
42 échecs environnementaux de la suite locale, qui sont verts en CI où les
modules sont présents. Ils relèvent de l'installation, pas du livrable.

## Correction trouvée par cet audit

L'audit n'a pas seulement constaté : il a découvert un défaut que la CI verte
avait laissé passer. En suivant la piste du job `e2e` rouge, la cause racine
s'est révélée être un **défaut applicatif visible par l'utilisateur** : la file
des extractions pouvait **revenir non filtrée** sous ses yeux, parce que deux
requêtes se croisaient et que la plus ancienne écrasait la plus récente. Voir
les commits `b290b3e` et `e74afcd`. C'est précisément le genre de défaut qu'une
CI verte ne voit pas et qu'un parcours réel révèle.

## Recommandation

**Mettre en service sur le plan technique.** Le code est prêt.

Avant l'ouverture aux utilisateurs : installer `unaccent` et `pg_trgm` sur le
PostgreSQL de production, configurer la sauvegarde distante (le point de
contrôle santé le redemande tant qu'il ne l'est pas), puis reprendre le relevé
du corpus au-delà des 46 fiches.

## Compétences et listes ECC employées

- `production-audit` — cadre et barème de cet audit ;
- `.agents/agents/react-reviewer.md` — appliquée au composant
  `extractions-app.tsx` ; c'est sa rubrique HIGH « fetch without
  AbortController » qui a conduit au commit `e74afcd` ;
- `.agents/skills/e2e-testing` — stratégie anti-flaky, et confirmation qu'il ne
  fallait **pas** recourir aux motifs d'« isolement » (`test.fixme`,
  `test.skip`) qu'elle documente : masquer un flaky aurait fait taire
  exactement le signal qui a permis de trouver le défaut ;
- `tdd-workflow` — cycle rouge → vert → mutation appliqué au correctif
  `gamme` (commit `5f186d1`).

Comme le prévoit `.agents/README.md`, Arena ne pouvant pas invoquer de
sous-agents ECC, ces listes ont été appliquées en rôle par l'agent lui-même.
