# Runbook — poste atelier (Compose local)

**Cible :** poste local ou petit réseau d'atelier, un opérateur. Ce runbook décrit l'usage du Compose documenté ; il ne remplace pas la procédure de mise en service.

## Démarrage initial

1. Installer Docker Desktop (Windows/macOS) ou Docker Engine + Compose (Linux), vérifier l'espace disque et l'accès à l'archive de travail.
2. Cloner le dépôt dans un répertoire de travail contrôlé et copier `.env.example` vers `.env` ; générer des secrets uniques. Garder `.env` hors Git et hors partage.
3. Configurer `SEAMTECH_ROOT_PATHS` vers l'archive voulue. La source reste en lecture seule : le montage applicatif doit rester `:ro` (RG13).
4. Conserver MinIO comme stockage objet local. R2/VPS ne font pas partie de ce scénario.
5. Démarrer avec `docker compose up -d --build`, attendre les contrôles de santé, puis ouvrir l'URL locale affichée par la configuration. Ne pas publier les ports sur le LAN sans TLS et décision sécurité.
6. Se connecter avec le compte UI, vérifier la recherche et l'écran Validation.

## Usage courant

- Déposer/indexer un dossier depuis l'interface prévue ; surveiller l'état du lot, les erreurs et l'espace temporaire.
- Rechercher par texte/code, contrôler le badge **Non vérifiée**, ouvrir le PDF, confirmer/corriger/rejeter avec le clavier et un motif traçable.
- Une suggestion, un score ou une extraction n'est jamais une validation : l'opérateur décide fiche par fiche (RG3). Ne pas utiliser la validation groupée tant que le verrou de calibration retourne 409.
- Assistant et endpoints ML sont actifs par défaut. Pour un poste à un opérateur, les masquer avec `SEAMTECH_OPTIONAL_FEATURES_ENABLED=false`; recherche et validation restent disponibles.
- Ne pas modifier ni déplacer les fichiers de l'archive depuis SEAMTECH. Pour arrêter : `docker compose down` (ne pas ajouter `-v` sauf destruction planifiée).

## Sauvegarde, mise à jour et incidents

- Les volumes Compose protègent contre une simple recréation de conteneur, pas contre la panne du disque. Planifier une copie hors du poste et tester la restauration : [`RUNBOOK_RESTAURATION.md`](../verite_terrain/RUNBOOK_RESTAURATION.md).
- Avant mise à jour : sauvegarder, noter le SHA déployé, lire le changelog, puis vérifier l'état de santé et un parcours de recherche/validation.
- En cas de source inaccessible, arrêter le traitement et demander une vérification des droits/chemins ; ne jamais basculer en écriture ni contourner RG13/RG14.

## Documents de référence

- Procédure opérateur complète : [`MISE_EN_SERVICE.md`](../verite_terrain/MISE_EN_SERVICE.md).
- Liste de vérification release : [`RELEASE_CANDIDATE_CHECKLIST.md`](../RELEASE_CANDIDATE_CHECKLIST.md).
- Guide d'archive et OCR : [`OCR_ETAGES.md`](../OCR_ETAGES.md).
- Guide de validation humaine du corpus : [`GUIDE_VALIDATION_HUMAINE_CORPUS.md`](../verite_terrain/GUIDE_VALIDATION_HUMAINE_CORPUS.md).
