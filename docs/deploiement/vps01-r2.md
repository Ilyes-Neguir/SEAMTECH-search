# Runbook — VPS01 + stockage objet (cible R2 à décision)

**État:** guide de déploiement préparatoire. Le backend objet de production reste **MinIO** dans l'état courant. Cloudflare R2 est une option cible, **non activée** tant que la décision D-2 n'est pas rendue. Ce runbook ne modifie ni le code ni les paramètres effectifs.

## Architecture visée

- VPS01 : Docker Compose, frontend Next.js, API/worker SEAMTECH et PostgreSQL 16 ; accès applicatif uniquement via TLS.
- Stockage objet : MinIO reste la configuration actuelle. R2 remplacerait MinIO seulement après décision D-2, tests aller-retour et mesure de coûts/rétention.
- Sauvegardes : base PostgreSQL et objets sauvegardés hors du VPS01 (bucket distinct ou destination indépendante). Une copie sur le même disque n'est pas une sauvegarde hors site.

## Pré-requis opérateur

- Accès administrateur VPS01, domaine/DNS et terminaison TLS ; pare-feu limité à SSH administrateur et HTTPS public.
- Secrets générés et déposés hors Git : `SEAMTECH_AUTH_TOKEN`, mots de passe PostgreSQL/Redis/MinIO, compte UI, clé de session, clés S3 le cas échéant.
- Capacité et espace disque vérifiés pour PostgreSQL, journaux et fichiers temporaires ; alertes de disponibilité/disque configurées.
- Décision D-2 explicitement enregistrée avant toute configuration R2.

## Installation et TLS

1. Cloner le dépôt, préparer `.env` depuis `.env.example`, avec valeurs uniques. Ne jamais copier les secrets dans les commandes shell ou les journaux.
2. Définir les chemins d'archive persistants en lecture seule côté app (`SEAMTECH_ROOT_PATHS`) et vérifier les bind mounts.
3. Garder MinIO comme backend objet effectif. Ne renseigner les variables R2 (`SEAMTECH_STORAGE_BACKEND=s3`, endpoint, région `auto`, identifiants) que dans une future bascule décidée.
4. Démarrer la pile selon [`MISE_EN_SERVICE.md`](../verite_terrain/MISE_EN_SERVICE.md), vérifier les conteneurs healthy et les routes `/health` et `/ready` via le proxy.
5. Terminer TLS avec le reverse proxy validé du VPS01 ; activer `SEAMTECH_BEHIND_TLS_PROXY=true`, cookies Secure, HSTS et renouvellement du certificat. Ne pas exposer directement les ports internes.
6. Tester connexion nominative, autorisation/rôles, dépôt, recherche, lecture PDF, confirmation manuelle, génération d'artefacts et journal d'audit avec un échantillon synthétique.

## Sauvegardes / restauration

- Planifier des sauvegardes de PostgreSQL et du bucket objet vers une destination hors VPS01, chiffrées avec clés stockées séparément.
- Conserver une rétention convenue, surveiller les exécutions/échecs et vérifier régulièrement les empreintes et volumes.
- Faire un exercice de restauration sur une base et un bucket isolés ; valider migrations, recherche et lecture d'objets avant toute bascule.
- Procédure détaillée : [`RUNBOOK_RESTAURATION.md`](../verite_terrain/RUNBOOK_RESTAURATION.md). La CI `sauvegarde` prouve le mécanisme dans son environnement, pas le compte, le bucket ni le VPS01 du commanditaire.

## Retour arrière et limites

Le retour à l'état actuel consiste à conserver/rétablir MinIO et à restaurer PostgreSQL + objets depuis la sauvegarde vérifiée. Ne supprimer aucune donnée source pendant une bascule. Les adresses, certificats, volumes, politiques de sauvegarde, capacité VPS01, restauration sur l'infrastructure réelle et compatibilité R2 restent **NON MESURÉS** tant que le commanditaire n'a pas fourni l'accès et exécuté la recette.

Voir aussi [`RELEASE_CANDIDATE_CHECKLIST.md`](../RELEASE_CANDIDATE_CHECKLIST.md) et [`TLS.md`](../TLS.md).
