# Statuts de clôture A–D

Les quatre points **ne sont pas tous corrigés**. PR #37 **non fusionnée**.
Aucun PV humain. Recette historique intacte.

## Identité

| | |
|---|---|
| Départ | `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09` |
| Lot 1 | `be0f6d2e170145a8491ebf065ef2f464fd639a63` |
| Lot 2 | `3e4beb5e702b6e33bcbc7d5f9c2e663f2f682320` |
| Lot 3 (ce tour) | commit suivant sur la même branche |
| Recette historique | préservée |

## A — Recherche

**Statut : CORRIGÉ ET TESTÉ** sous périmètre sandbox PostgreSQL 16.2 +
chemins HTTP, **EN ATTENTE DE VALIDATION HUMAINE** (revue indépendante /
fonds réel).

Exécuté ici : parité IDs+ordre, 022→023, mutation UPDATE, filtres, pagination,
arabe non translittéré, 800 et 2000 fiches, GET /recherche + /search +
suggestions. Régression dédup ligatures **corrigée**.

Pas un SLA atelier. Plan Seq Scan à 2000 lignes documenté.

## B — Sauvegarde

**Statut : PARTIEL.**

- PG + fichiers locaux : **CORRIGÉ ET TESTÉ** dans ce sandbox (aller-retour
  sources coupées).
- Objets MinIO : test écrit, **PRÉPARÉ NON EXÉCUTÉ** ici (pas de binaire MinIO
  dans le sandbox). Sera exécuté par le job CI `sauvegarde` (MinIO officiel
  déjà vert sur PR #37) et le workflow `qualification-reserves.yml`.

## C — OCR

**Statut : PARTIEL.**

- Sandbox local : **BLOQUÉ** (pas de binaire tesseract ; CDN Releases fermé).
- Job CI historique `ocr` : tesseract 5.3.4 **réel**, taux propre 1.000,
  dégradé 0.955 (annotations du run 38399629077). Parcours
  import→index→recherche : **PRÉPARÉ NON EXÉCUTÉ** (job ocr sans PostgreSQL).
  Workflow complémentaire ajoute PG + tesseract.

## D — Déploiement de référence

**Statut : PRÉPARÉ NON EXÉCUTÉ** sur une machine vierge. Le sandbox actuel
réutilise pgserver/Redis déjà là — ce n'est pas une install propre.
Windows/Docker : non extrapolés.

**EN ATTENTE DE VALIDATION HUMAINE** pour l'acceptation métier.
