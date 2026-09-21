# API — routes fiche et gabarits (Lot B / B.2, plan v3.0 §17.11)

Ces routes complètent l'API existante (documents, recherche, imports). Elles sont
**PostgreSQL uniquement** (tables `fiche*`/`gabarit`, migrations 006-009 — décision
de couche §17.1) : sans `database_url`, elles répondent **503** avec l'explication.
Authentification : mêmes règles que le reste de l'API (en-tête `X-SEAMTECH-TOKEN`
quand `SEAMTECH_AUTH_TOKEN` est configuré).

## Recherche hybride des fiches (Lot E, plan v3.0 §10 / §17.6)

La recherche « comme Google » des fiches validées. La route Phase 0 `GET /search`
(recherche fichiers) reste intacte ; `/recherche` est la ressource fiches.

| Route | Rôle |
|---|---|
| `GET /recherche?q=&type_voile=&client=&bateau=&matiere=&gamme=&annee=&annee_min=&annee_max=&inclure_a_valider=&limit=&offset=` | Recherche hybride : lexical tsvector pondéré A/B/C + trigrammes (volet dégradable) + texte des PDF (chunks/documents) + vecteurs (dormants, activables par injection `encode_requete`), fusionnés par **RRF k=60**. Par défaut : fiches `valide` seulement. Réponse : `{requete, nb_resultats, resultats:[{code, titre, type_voile, client, bateau, gamme, statut, annee, extrait, score, sources}], facettes:{groupe:[{valeur, effectif}]}, sources_actives, sans_resultat, duree_ms}`. |
| `GET /recherche/suggestions?prefix=&limite=` | Suggestions au fil de la frappe : **valeurs réellement présentes seulement** (référentiels, codes, gammes) par préfixe, complétées par tolérance aux fautes trigrammes sur les référentiels si le préfixe ne donne rien. Réponse : `{prefixe, suggestions:[{nature, valeur}]}`. |

**Comportements** :

- **Facettes à compteurs** : chaque axe (type de voile, client, bateau, matière,
  gamme, année) compte les résultats filtrés par le texte et par les AUTRES
  filtres — jamais par son propre filtre (comportement standard d'un moteur
  généraliste : on peut changer son choix sans perdre les autres valeurs).
- **Codes** : les séparateurs `-_/` sont normalisés en espaces côté requête ET
  côté index — `0701-GV-001` se trouve tel quel, le tiret n'est jamais lu comme
  une exclusion.
- **Tolérance aux fautes** : filet trigrammes sur les requêtes d'un seul mot
  (`monofime` → Monofilm) ; seuil 0,30 ; indexable (GIN trigrammes, migration 007).
- **Synonymes** : table `synonyme` (terme → canonique), rafraîchie toutes les 30 s.
- **Journal** : TOUTES les recherches sont écrites dans `recherche_log` ;
  `nb_resultats = 0` marque la recherche sans résultat (index partiel, migration
  012) — critère de sortie Phase 3, matière première de l'amélioration du lexique.
- **Métriques** : `GET /metriques` expose `recherche_requests` et
  `recherche_sans_resultat`.
- **Vecteurs dormants** : la source vectorielle ne s'active que si un appelant
  injecte `encode_requete` (aucun modèle embarqué, aucun appel réseau — §17.2).
- **Sans PostgreSQL** : 503 propre avec l'explication (comme les autres routes
  fiche), jamais de bascule silencieuse SQLite.

Aucune de ces routes n'écrit de fiche : elles lisent, détectent et publient des gabarits.

## Traçabilité

| Route | Rôle |
|---|---|
| `GET /fiches/{code}/champs` | Toutes les lignes `fiche_champ_extrait` de la fiche : `champ`, `rang`, `table_cible`, `colonne_cible`, `valeur_brute`, `valeur_normalisee`, `methode`, `confiance`, `page`, `zone` (bbox PDF), `version_gabarit`, `corrige`, `corrige_par`, `corrige_le`. 404 si la fiche est inconnue. C'est la forme que consommera l'écran Fiche (lot D). |

## Registre de gabarits

| Route | Rôle |
|---|---|
| `GET /gabarits` | Dernière version de chaque gabarit : `code`, `version`, `description`, `ancres_detection`, `actif`, `nb_fiches`. |
| `GET /gabarits/{code}/versions` | Toutes les versions d'un code (404 si inconnu). Une version publiée reste toujours lisible. |
| `POST /gabarits/{code}/versions` | Publie une **nouvelle** version (max+1). Corps JSON : `{"description": str, "ancres_detection": [str, …], "regles": {…}}`. 201 → `{"code", "version", "nb_ancres", "nb_regles"}`. Jamais destructif : les versions précédentes passent `actif=false` (consultables), la nouvelle devient la seule active. 422 si ancres vides ou règles absentes. |
| `POST /gabarits/detecter` | Détection seule sur un PDF envoyé en multipart (champ `fichier`), **sans aucune écriture**. 200 → `{"detecte": true, "gabarit", "version", "score", "ancres_trouvees", "scores", "pages"}` ou `{"detecte": false, "voie": "reprise_complete", "scores", "detail"}` — une non-détection est un résultat, pas une erreur. 422 si le contenu n'est pas un PDF, 413 au-delà de 20 Mo. |

## Ingestion par dossier complet et lots (Lot C)

| Route | Rôle |
|---|---|
| `POST /imports/dossier` | Porte A (plan §9.1) : corps `{"dossier": "/chemin/du/dossier"}`. Reconnaît la fiche (meilleur `score_detection`), l'extrait via le moteur du lot B, rattache les autres PDF du dossier comme pièces jointes, écrit **fiche + pièces en une transaction** → fiche `a_valider` (jamais valide, RG3). Idempotent (clé = chemin normalisé + SHA-256 : rejouer = `deja_traite`, 0 nouveau). Un refus est un **résultat** avec raison (« aucun PDF dans le dossier », « gabarit inconnu (scores…) »), jamais une erreur 500 : il est tracé dans son propre lot suivi. 201. |
| `POST /imports/dossier/lot` | Porte A bis : lot de dossiers en tâche de fond (thread in-process, **sans Redis** ; `X-SEAMTECH-BACKGROUND: false` → synchrone). Corps `{"racine": "/archive", "interrompre_apres": N}` (option). 202 → `{"id_lot", "nb_dossiers"}`. Reprise après interruption : rappeler la même route (les dossiers `traite` sont sautés par idempotence, les `echec` retentés). |
| `GET /lots` | Liste des lots : `id_lot`, `racine`, `statut` (`en_cours`/`termine`/`interrompu`), compteurs (`nb_dossiers`, `nb_traites`, `nb_echecs`), `progression_pct`. |
| `GET /lots/{id_lot}` | État complet : progression, **dossiers avec leur statut et leur raison d'échec** (« fiche non reconnue », « gabarit inconnu (scores de détection : …) », « PDF illisible », « déjà traité (lot #N) ») et **fichiers restants**. 404 si lot inconnu. |

**Écart documenté (assumé)** : la consultation des lots se fait sous `GET /lots` et
`GET /lots/{id_lot}` — le chemin `GET /imports/{id}` est déjà pris par l'import unitaire
de la Phase 0 (porte B) et sert un autre contrat. Le suivi de lot est une ressource
propre (`lot_import`/`lot_dossier`), distincte du job d'import unitaire.

**Archive en lecture seule (RG13)** : le dépôt ne modifie JAMAIS l'archive source —
empreintes SHA-256 de l'arbre avant/après testées égales (`TestArchiveIntacteRG13`).



```bash
curl -H "X-SEAMTECH-TOKEN: $TOKEN" http://localhost:8000/fiches/7792-SO/champs
curl -H "X-SEAMTECH-TOKEN: $TOKEN" http://localhost:8000/gabarits
curl -H "X-SEAMTECH-TOKEN: $TOKEN" -H "X-SEAMTECH-TOKEN: $TOKEN" \
     -F "fichier=@fiche.pdf" http://localhost:8000/gabarits/detecter
```

## Extraction (hors HTTP, lot B)

`python -m seamtech_search.fiches.cli extraire CHEMIN.pdf` — rapport champ par champ
(valeur, confiance, page, zone) ; `ecrire` (transaction, statut `a_valider`), `init`,
`banc` (non-régression `gabarit_test`). Détection isolée : `POST /gabarits/detecter` (lot B.2) ;
l'écran d'extraction interactive arrive avec le lot D. Dépôt d'un dossier : `deposer DOSSIER`
et lots : `lot RACINE [--interrompre-apres N]` (reprise = rappeler).
