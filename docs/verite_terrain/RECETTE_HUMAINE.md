# Recette humaine « validation < 2 minutes » — prête à exécuter (Lot H.1)

Statut au 22/09/2026 : cette recette est exécutable SANS le développeur, sur
tout poste où la mise en service (MISE_EN_SERVICE.md) est passée. Elle remplit
le tableau vide de MESURE_VALIDATION_2MIN.md §2.

Ce qui est mesuré ici : **lecture + décision humaines** (relire 48 champs face
au PDF, corriger, valider). La part MACHINE est déjà mesurée et publiée en CI
: 665 ms puis 656 ms (runs 35715024408 / 35715783739, annotation
`mesure-phase1`) — elle borne le temps de rendu/traitement, pas le temps de
lecture.

## Le jeu de 3 fiches (déjà dans le dépôt — rien à télécharger)

| # | Fiche | Fichier | Cas couvert |
|---|---|---|---|
| 1 | 7792-SO (la vraie fiche, 48 champs) | `sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf` | relecture normale d'une fiche riche |
| 2 | Génois (fiche synthétique) | `sample_data/CLIENT-GENOA/fiche-genois.pdf` | **champs incertains** : peu de texte lisible, plusieurs champs seront vides ou de faible confiance — décision humaine : compléter ou laisser vide |
| 3 | Fiche technique (synthétique) | `sample_data/CLIENT-123/fiche-technique.pdf` | deuxième relecture de confirmation |

Cas transverses exercés par la recette :
- **cote à corriger** : à l'étape 3, corriger volontairement UN champ de la
  fiche 1 avant de valider (le champ corrigé est verrouillé — RG11 ; c'est le
  comportement attendu).
- **doublon de code** : à l'étape 5, redéposer le dossier `CLIENT-7792-SO` —
  observer ce que fait le système (signalement / statut) et le noter ; le
  doublon ne doit jamais écraser une fiche validée.

## Fiche de recette imprimable (6 étapes)

Préparer : un chrono (téléphone suffit), cette page imprimée, un stylo.

- [ ] **Étape 1 — Le service vit.** `docker compose ps` : 5 services healthy ;
      `http://127.0.0.1:3000/api/health` répond. Sinon : MISE_EN_SERVICE.md.
- [ ] **Étape 2 — Dépôt.** Interface `http://127.0.0.1:3000` → connexion →
      écran Dépôt → déposer le dossier `sample_data` (les 3 fiches ci-dessus).
      Attendre la fin du traitement ; les fiches arrivent dans la file de
      validation (statut `a_valider`).
- [ ] **Étape 3 — Validation chronométrée (3 fiches).** Pour CHAQUE fiche :
      ouvrir la fiche → démarrer le chrono → relire chaque champ en le
      comparant au PDF affiché à droite → corriger UN champ de la fiche 1
      (cote à corriger) → valider → ARRÊTER le chrono. Noter dans le tableau.
- [ ] **Étape 4 — Recherche.** Dans la recherche, taper `7792-SO` : la fiche
      validée remonte en tête (les fiches `valide` seulement sont cherchables).
- [ ] **Étape 5 — Doublon.** Redéposer le dossier `CLIENT-7792-SO`. Noter ce
      que le système affiche ; vérifier que la fiche validée n'a PAS été
      écrasée (rechercher `7792-SO` : elle est toujours là, inchangée).
- [ ] **Étape 6 — Publication.** Recopier les 3 durées dans le tableau de
      MESURE_VALIDATION_2MIN.md §2 (ou les transmettre telles quelles).

### Tableau de mesure (à recopier dans MESURE_VALIDATION_2MIN.md)

| # | Fiche | Durée (s) | Champs corrigés | Opérateur | Date |
|---|-------|-----------|-----------------|-----------|------|
| 1 | 7792-SO | ……… | ……… | ……… | ……… |
| 2 | GENOA | ……… | ……… | ……… | ……… |
| 3 | 123 | ……… | ……… | ……… | ……… |

**Règle de succès : les 3 durées < 120 s** (critère de sortie Phase 1). Tant
qu'une case est vide, écrire « non mesuré » — jamais d'estimation.

## Ce que la recette ne mesure PAS (et pourquoi)

- La part machine (déjà en CI : 665/656 ms) — la recette la suppose acquise ;
  si l'étape 3 « dépasse » à cause d'une page qui ne charge pas, c'est un
  incident service (QUE_FAIRE_SI.md), pas une mesure.
- La qualité d'extraction (déjà mesurée : banc 31/31, `validate_extraction`
  6/6 sur la vraie fiche — EMPREINTES.md).
- Les fiches réelles du commanditaire (20-30 attendues) : cette recette
  utilise les fiches du dépôt pour débloquer la mesure SANS attendre ; elle
  sera rejouée sur le fonds réel quand l'accès archive sera fourni (Lot G).
