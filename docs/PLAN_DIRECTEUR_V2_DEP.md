# Plan directeur v2 — DEP : déploiement sur le serveur d'entreprise (hors ligne)

Branche : `arena/b02ed560-seamtech-search`. Ce document est la procédure
**mécanique** du jour J : tout ce qui pouvait être préparé et prouvé en amont
l'a été dans les Lots 0a → 6 (rapports `PLAN_DIRECTEUR_V2_LOT*.md`). Le
serveur cible n'a **pas besoin d'Internet** : le code et les images arrivent
par support amovible, les traitements sont 100 % locaux (PostgreSQL +
pg_trgm/pgvector, Redis, MinIO, Tesseract/Poppler — aucune API d'IA externe
ne voit jamais un PDF d'entreprise).

## 0. Dimensionnement (à vérifier AVANT le transfert)

Disque du serveur ≥ **3 × (volume du corpus) + 20 Go** :

- 1 × l'archive d'origine (montée en lecture seule, jamais écrite — RG13) ;
- 1 × les copies stockées (bucket MinIO + rendus) ;
- 1 × l'espace de travail (OCR, temporaires, quarantaine, sauvegardes) ;
- \+ 20 Go pour PostgreSQL/index, les images Docker et la croissance.

RAM ≥ 8 Go et ≥ 2 cœurs pour démarrer (à confirmer par la mesure du pilote,
voir `docs/RELEASE_CANDIDATE_CHECKLIST.md` §1). Ports : seul le terminateur
TLS du LAN est exposé ; tout le reste reste en loopback (voir
`docs/DEPLOYMENT.md`, « Office LAN access »).

## 1. Préparer le paquet (poste connecté — PAS le PC du commanditaire)

```bash
git clone https://github.com/Ilyes-Neguir/SEAMTECH-search && cd SEAMTECH-search
git checkout <SHA-de-livraison>
bash scripts/preparer_transfert_hors_ligne.sh preparer /chemin/support/seamtech-dep
bash scripts/preparer_transfert_hors_ligne.sh verifier /chemin/support/seamtech-dep
```

Le script refuse un arbre Git non propre (le paquet fige un SHA commité),
construit les images applicatives, tire/construit les images
d'infrastructure (MinIO via `scripts/construire_image_minio.sh`), exporte
`seamtech-<sha>.bundle` + `images-<sha>.tar` + `MANIFESTE.txt` +
`SHA256SUMS`. **Aucun secret n'est transporté** : `.env` se crée sur le
serveur (§3).

## 2. Préflight de l'archive réelle (lecture seule stricte)

Sur le serveur, AVANT toute ingestion — la source ne doit jamais être
écrite, déplacée ni renommée par nos outils :

```bash
python3 scripts/preflight_archive.py preflight \
  --source /srv/archive-client --travail /srv/seamtech/travail \
  --sortie /srv/seamtech/rapports
python3 scripts/inventaire_archive.py /srv/archive-client \
  --sortie /srv/seamtech/rapports/inventaire
```

L'inventaire (`inventaire.json`, CSV par fichier, CSV doublons) donne le
volume réel (recontrôler le §0), la part de PDF scannés, et surtout la
**dédup SHA-256 avant OCR** : les doublons exacts (même empreinte) ne seront
traités qu'une fois — sur un fonds de voiles renommées/copiées, c'est
l'économie majeure de temps d'ingestion.

## 3. Installer sur le serveur

```bash
bash scripts/preparer_transfert_hors_ligne.sh verifier /media/support/seamtech-dep
bash scripts/preparer_transfert_hors_ligne.sh charger  /media/support/seamtech-dep
git clone /media/support/seamtech-dep/seamtech-*.bundle seamtech-search
cd seamtech-search
cp .env.example .env   # remplir TOUS les secrets (voir RELEASE_CANDIDATE_CHECKLIST.md §2)
docker compose up -d   # AUCUN --build : les images viennent du paquet
```

Contrôles : `/health` vert (dont `s3_credentials: dedie`), ouverture de
l'URL LAN en HTTPS via le terminateur TLS, connexion avec le compte de
secours, recherche vide fonctionnelle.

## 4. Comptes et fermeture du compte de secours

Créer immédiatement les comptes nominatifs (écran Comptes, rôle admin
technique) : au moins **deux approbateurs** (l'auto-approbation est interdite
par défaut), les utilisateurs de l'atelier, les lectures. Puis **vider
`SEAMTECH_UI_PASSWORD`** dans `.env` et redémarrer le service web : le compte
de secours (admin d'urgence partagé, sans identité nominative ni session
révocable) ne doit pas survivre à la mise en service.

## 5. Ingestion du fonds par vagues

Ne jamais lancer le fonds complet d'un bloc. Ordre de vague recommandé
(du plus utile au plus ancien) :

1. **Vague pilote** : un dossier client récent complet (quelques centaines de
   fiches). Mesurer : temps d'ingestion/fiche, taux d'extraction complète par
   famille (écran Qualité), occupation disque réelle → confirmer le §0.
2. **Vagues par année/famille**, dépôt dossier par dossier (écran Nouveau
   dossier) ou par racines `SEAMTECH_ROOT_PATHS`. Suivi dans l'écran Dossiers
   et la file durable (Redis) : un arrêt (panne, nuit) se **reprend** sans
   retraitement — la reprise est un comportement testé (file persistante,
   idempotence par empreinte), pas un espoir.
3. Après chaque vague : résolution bateau en lot (écran Extractions), puis
   corrections approbateur sur les ambiguïtés — c'est ce qui alimente
   Comparaison (4b) et Confiance (5).

Montée en charge : les workers d'ingestion/OCR se règlent par la
configuration Compose (répliques) — augmenter par paliers en surveillant
CPU/RAM pendant le pilote, jamais sur le fonds complet d'emblée.

## 6. Calibration des gabarits sur le fonds réel

État mesuré avant DEP (fiches réelles transmises en conversation, jamais
versionnées) : le gabarit spi extrait à 100 % ; génois/GV/jade sont partiels.
Sur le serveur, exécuter l'échantillon d'or **localement** (outil du Lot 3a :
`rapport_extraction_or.py`) sur 10–30 fiches par famille choisies dans le
fonds, corriger via l'écran Extractions (les motifs appris du Lot 3b
accélèrent les vagues suivantes) et ne promouvoir un gabarit que lorsque le
taux exact par champ est mesuré. Aucune vérité issue d'une fiche client ne
rentre dans Git — ni ici ni ailleurs.

## 7. Corpus Dropbox `VOILES 2024.zip` (1 205 608 885 octets)

Déposé par le commanditaire le 2026-10-26. Inaccessible depuis
l'environnement de développement (connecteur ≤ 100 Mio/fichier, CDN Dropbox
hors liste blanche réseau de la sandbox). Deux voies, au choix :

- **Calibration rapide** : extraire le ZIP dans l'interface web Dropbox
  (clic droit → Extraire) ; l'agent récupère alors des PDF **unitaires** via
  le connecteur pour la mesure de calibration §6 (traitement éphémère local,
  agrégats uniquement, jamais dans Git).
- **Production** : décompresser l'archive directement sur le serveur
  d'entreprise et l'ajouter aux racines du §2 — c'est la voie normale pour
  1,12 Gio et au-delà.

## 8. Sauvegarde, rollback, exploitation

- Sauvegarde PostgreSQL planifiée (`docs/DEPLOYMENT.md` + runbook de
  restauration) ; tester une restauration AVANT la première vague.
- Mise à jour : nouveau paquet (§1) au nouveau SHA, sauvegarde PG,
  `docker compose up -d` (migrations additives appliquées au démarrage),
  contrôles du §3. Rollback : paquet au SHA précédent ; si une migration a
  eu lieu, décider explicitement (les tables ajoutées sont inertes pour
  l'ancienne version, voir les rapports de lots).
- Le dépôt GitHub reste **public** par décision du commanditaire (dérogation
  RG13 consignée) : ne jamais y pousser de document client, d'extrait
  nominatif ni de secret. Le transfert hors ligne (§1) rend de toute façon
  le serveur indépendant de GitHub.

## 9. Critères de fin de DEP (à cocher sur le serveur)

- [ ] Paquet vérifié (SHA256SUMS + bundle verify) et images chargées.
- [ ] Preflight + inventaire de l'archive réelle produits, disque recontrôlé.
- [ ] `/health` vert en HTTPS sur le LAN ; comptes nominatifs créés
      (≥ 2 approbateurs) ; `SEAMTECH_UI_PASSWORD` vidé.
- [ ] Vague pilote ingérée ; temps par fiche, taux d'extraction par famille
      et occupation disque consignés.
- [ ] Sauvegarde testée par restauration réelle.
- [ ] Parcours 3 clics rejoué à la main sur le serveur (chercher → demander
      → approuver → télécharger) avec les comptes réels.
- [ ] Chrono humain « ouverture → validation < 2 min » mesuré en conditions
      réelles (le chrono machine CI est < 1 s ; la lecture humaine est la
      part restant à confirmer sur place).
