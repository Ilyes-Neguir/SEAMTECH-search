# Harnais de recette (scripts réellement exécutés)

Ces scripts sont ceux qui ont **produit** les fichiers de `PREUVES/` et `LOGS/`. Ils sont
versionnés avec le livrable pour que les mesures soient reproductibles et vérifiables :
aucune valeur de résultat n'est saisie à la main, la matrice est construite à partir des
fichiers de preuve (`construire_matrice.py`).

| Script | Rôle |
| --- | --- |
| `start_redis.sh`, `start_s3.sh`, `start_api.sh`, `start_worker.sh`, `start_frontend.sh` | démarrage des services de la pile native (remplaçant `docker-compose.yml`) |
| `preparer_config.py`, `verifier_config_resolue.py` | préparation puis contrôle de la configuration résolue (0 écart avec `rv/recette.env`) |
| `construire_corpus.py` | construction du corpus de recette (copies anonymisées + fixtures + synthétiques) et du manifeste SHA-256 |
| `verifier_integrite_corpus.py` | contrôle pré/post d'intégrité du corpus (453 fichiers) |
| `recette_api.py` | recette API : 92 appels, sections A→H et §7, journal `CONTROLE|id|PASS|détail` |
| `recette_navigateur.cjs` | recette navigateur (Chromium réel piloté par CDP) : 29 contrôles, captures dans `PREUVES/navigateur/` |
| `e2e.config.cjs`, `lancer_e2e_depot.sh` | lancement de la suite e2e du dépôt sur la pile réelle |
| `lancer_recette_verif.sh` | lancement du script de recette officiel du dépôt (`scripts/recette_verif.py`) |
| `essai_panne_stockage_import.py` | essai ciblé : import pendant une panne du stockage objet |
| `pannes_et_reprises.py` | §8/§9 : pannes provoquées, reprises observées, sauvegarde/restauration (23 contrôles) |
| `reparer_copies_locales.py`, `provisionner_stockage.py` | réparation des copies locales rétablies, provisionnement des buckets |
| `rassembler_preuves.py` | copie des preuves et journaux dans ce livrable, masquage des secrets |
| `relever_environnement.py` | relevé machine/versions pour `ENVIRONNEMENT.md` |
| `extraire_navigateur.mjs` | extraction du navigateur Chromium utilisé par la recette navigateur |

Les identifiants sont lus dans `rv/recette.env` (généré pour la recette, non versionné) et dans
`rv/secrets/` : ces scripts ne contiennent **aucun** mot de passe, jeton ou clé en clair (par
exemple `lancer_e2e_depot.sh` lit `PGPASSWORD` de l'environnement au lieu de l'écrire en dur).
