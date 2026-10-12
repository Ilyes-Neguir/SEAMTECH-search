# Journaux d'exécution de la recette

Ces journaux sont ceux produits par les processus réellement lancés sur la machine de
recette. Politique de troncature : les fichiers sont copiés **intégralement** sauf
`worker.log` (plusieurs dizaines de Mo) et les journaux de compilation, dont seules les
dernières lignes sont conservées — la liste exacte et le nombre de lignes figurent dans
`MANIFESTE_LOGS.json`.

Contenu :

| Fichier | Ce qu'il prouve |
| --- | --- |
| `recette_verif.log` | sortie du script de recette officiel du dépôt (19 contrôles, `controles_echoues\|aucun`) |
| `api.log`, `worker.log` | exécution de l'API et du worker de recette (migrations, dépôts, jobs) |
| `api-e2e.log`, `frontend-e2e.log`, `e2e_depot_passe4.log` … `e2e_depot_passe9.log` | passes de la suite e2e du dépôt (base PostgreSQL jetable, navigateur réel) |
| `pannes_final.log` | run définitif §8/§9 du 2026-10-12 (23/23 PASS) |
| `recette_verif_relance.log` | relance du script officiel : séquelles de l'anomalie A-07 (copies locales manquantes), avant réparation |
| `redis.log`, `s3.log`, `postgres.log`, `frontend.log` | démarrage et vie des services de la pile |
| `build_redis.log`, `initdb.log` | construction de l'environnement |

Aucun secret n'est présent dans ces journaux : les URL contenant des mots de passe sont
masquées à l'écriture (`***`) et les fichiers d'identifiants ne sont pas copiés.
