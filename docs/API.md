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
| `GET /recherche?q=&type_voile=&client=&bateau=&matiere=&gamme=&annee=&annee_min=&annee_max=&cote=&min=&max=&cote_min=&cote_max=&tri=&page=&limit=&offset=&inclure_a_valider=` | Recherche hybride : lexical tsvector pondéré A/B/C + trigrammes (volet dégradable) + texte des PDF (chunks/documents) + vecteurs (dormants, activables par injection `encode_requete`), fusionnés par **RRF k=60**. Par défaut : fiches `valide` seulement. Réponse : `{requete, nb_resultats, resultats:[{code, titre, type_voile, client, bateau, gamme, statut, annee, extrait, score, sources}], facettes:{groupe:[{valeur, effectif}]}, facettes_cotes:{cote:{unite, min, max, effectif, intervalles:[{min,max,effectif,label}] }}, cote_active, cotes_unites, tri, page, sources_actives, sans_resultat, duree_ms}`. **Les 7 cotes d'un résultat (`slu_m`, `sle_m`, `sf_m`, `shw_m`, `spa_m2`, `tetiere_cm`, `poids_kg`) ne sont présentes QUE si un filtre dimension ou un tri par cote est demandé — voir « Chargement conditionnel des cotes » ci-dessous.** |
| `GET /recherche/suggestions?prefix=&limite=` | Suggestions au fil de la frappe : **valeurs réellement présentes seulement** (référentiels, codes, gammes) par préfixe, complétées par tolérance aux fautes trigrammes sur les référentiels si le préfixe ne donne rien. Réponse : `{prefixe, suggestions:[{nature, valeur}]}`. |
| `GET /recherche/journal?jours=&limite_top=&limite_sans=` | Exploitation du journal : agrégé depuis `recherche_log` réelle. Retour : `{periode_jours, total_recherches, total_sans_resultat, top_requetes:[{requete, nb_occurrences, nb_sans_resultat, dernier}], sans_resultat:[{requete, nb_occurrences, dernier, exemple_filtres}]}`. Période paramétrable en jours. |

**Comportements (Lot J)** :

- **Facette DIMENSION (cotes)** : 7 cotes explicites depuis `fiche_cotes` (vue
  `fiche_cotes` migration 014) — `slu_m`, `sle_m`, `sf_m`, `shw_m`, `spa_m2`,
  `tetiere_cm`, `poids_kg`. Paramètres API : `cote=<nom>&min=&max=` (alias
  `cote_min`/`cote_max`). Unités métier : `slu_m`/`sle_m`/`sf_m`/`shw_m` en
  mètres (m), `spa_m2` en m², `tetiere_cm` en cm, `poids_kg` en kg — aucune
  conversion : la valeur stockée est dans son unité métier. `facettes_cotes`
  retourne pour chaque cote `{unite, min, max, effectif, intervalles[]}` où
  `intervalles` est calculé depuis les données réelles filtrées (hors filtre
  dimension courant) avec répartition quantile approximative (≤5 intervalles).
  `cote_active` = cote filtrée ou `slu_m` par défaut. `facettes["dimension"]`
  = intervalles de la cote active (compatibilité UI). Chaque facette (y compris
  dimension) compte sans son propre filtre, comme les autres facettes.
  **Sous réserve du chargement conditionnel ci-dessous : par défaut
  `effectif = 0` et `intervalles = []`, `facettes["dimension"]` est vide.**

- **Chargement conditionnel des cotes (correctif 0.4, audit Lot K)** : les 7
  cotes d'un résultat **et** les valeurs de `facettes_cotes` sont chargées
  **si et seulement si** :
  1. un **filtre dimension est actif** — `cote=<nom>` accompagné d'au moins une
     borne (`min`/`max`, ou leurs alias `cote_min`/`cote_max`) ; ou
  2. un **tri par cote est demandé** — `tri` commençant par une des 7 cotes
     (`slu_m_asc`, `slu_m_desc`, …, `poids_kg_asc`, `poids_kg_desc`).

  Sinon — c'est-à-dire dans tous les autres cas, dont le défaut
  `tri=pertinence` sans filtre dimension — **les résultats ne portent AUCUNE
  des 7 clés de cote** (`r["slu_m"]` lève `KeyError`, ce n'est pas `null` : un
  code qui fait `r.get("slu_m")` verra `None`, indistinguable d'une cote non
  extraite) et **les 7 clés de `facettes_cotes` existent mais sont vides** :
  `{"unite": …, "min": null, "max": null, "effectif": 0, "intervalles": []}`
  (les `unite` restent renseignées, pour que l'UI connaisse l'unité à
  afficher). `facettes["dimension"]` est alors `[]`.

  **Raison** : la latence. Charger systématiquement les 7 cotes imposait un
  `JOIN fiche_cotes` sur chaque recherche (fusion complète + page) et 7
  requêtes de facettes : p95 mesuré 54 ms puis, après 7→1 requête, toujours
  au-dessus du seuil d'environnement CI (< 50 ms), avec alerte `perf-derive`.
  Le correctif du Lot J (commit `d0a8aac`) rend le coût proportionnel au
  besoin réel : le chemin par défaut (`tri=pertinence`, aucun filtre
  dimension) n'ouvre plus `fiche_cotes` du tout.

  **Comment obtenir les cotes** : ajouter un filtre dimension
  (`?cote=slu_m&min=6.5&max=6.7`) ou un tri par cote (`?tri=slu_m_asc`). C'est
  la seule façon documentée ; il n'existe pas de paramètre « charge tout ».
  Le contrat est figé par le test
  `tests/test_recherche_dimension_tri.py::test_contrat_cotes_conditionnelles_documente`.

- **Tri** : paramètre `tri` parmi 18 valeurs (`pertinence` par défaut,
  `date_asc/desc` sur année, `code_asc/desc`, `slu_m_asc/desc`, `sle_m_asc/desc`,
  `sf_m_asc/desc`, `shw_m_asc/desc`, `spa_m2_asc/desc`, `tetiere_cm_asc/desc`,
  `poids_kg_asc/desc`). Tri appliqué sur la fusion complète AVANT pagination :
  les détails des cotes sont récupérés sur `PROFONDEUR_SOURCES` ids puis triés,
  puis paginés.

- **Pagination partageable** : `page` (1-indexed) converti en `offset = (page-1)*limit`
  côté API. L'UI synchronise `q + filtres + cote/min/max + tri + page` dans
  `URLSearchParams`, restaurés au chargement. Bouton « Copier le lien » copie
  l'URL courante. F5 et nouvel onglet conservent l'état (aucun état caché).

- **Journal exploité** : route `GET /recherche/journal` agrège la table réelle
  `recherche_log`. Paramètres `jours` (période), `limite_top`, `limite_sans`.
  CLI `python -m seamtech_search.journal_recherche --jours 30 --json` ou
  `recherche-log` (entry point). Fonctions testables `rapport_journal`,
  `top_requetes`, `recherches_sans_resultat` sur jeu injecté (test automatisé).

**Comportements (Lot E)** :

- **Facettes à compteurs** : chaque axe (type de voile, client, bateau, matière,
  gamme, année) compte les résultats filtrés par le texte et par les AUTRES
  filtres — jamais par son propre filtre (comportement standard d'un moteur
  généraliste : on peut changer son choix sans perdre les autres valeurs).
- **Codes** : les séparateurs `-_/` sont normalisés en espaces côté requête ET
  côté index — `0701-GV-001` se trouve tel quel, le tiret n'est jamais lu comme
  une exclusion.
- **Tolérance aux fautes** : filet trigrammes sur les requêtes d'un seul mot
  (`monofime` → Monofilm) ; seuil 0,30 ; indexable (GIN trigrammes, migration 007).
  Limite connue : la comparaison trigrammes est littérale — un mot fautif SANS
  ses accents (`genios`) n'est pas rattrapé ; la requête accentuée correcte
  (`génois`) passe par le lexical. Plier les accents exigerait un index
  d'expression `unaccent()` (fonction STABLE, non IMMUTABLE) : hors périmètre
  Lot E, documenté pour la suite.
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

## Assistant sourcé (Lot I, plan v3.0 §11 / Phase 4)

Questions en français, réponses **EXTRACTIF** construites depuis la base
(aucun LLM génératif, aucun torch, aucun modèle téléchargé — décision
matérielle actée) : chaque valeur affirmée porte une citation vérifiable ;
sans source, l'assistant refuse explicitement.

| Route | Rôle |
|---|---|
| `POST /assistant` | Corps JSON : `{"question": str (1..500), "inclure_a_valider": bool=false}`. Réponse : `{question, etat, reponse, citations:[{code_fiche, champ, libelle, table_cible, colonne_cible, valeur, valeur_normalisee, page, zone, lien}], interpretations:[{lecture, reponse, citations}], pistes:[str], duree_ms}`. États : `ok` (réponse + ≥ 1 citation), `ambigu` (lectures listées, chacune sourcée), `sans_source` (refus explicite, citations vides), `occupe` (une analyse à la fois — poste mono-cœur), `indisponible` (503, hors PostgreSQL). `lien` pointe `/dossier/<code>?champ=<champ>` : l'écran Fiche surligne la zone PDF de la citation. |
| `GET /assistant/etat` | `{etat: ok|occupe, postgres: bool}` — disponibilité pour le panneau. |

**Comportements** :

- **Aucune réponse sans citation** : une valeur n'est rendue que si elle est
  lue dans `fiche*`/référentiels ; le refus (« je ne trouve pas dans les
  fiches ») n'affirme rien et propose au besoin des pistes de requête
  (clairement étiquetées « pas des réponses »).
- **Champ « non applicable » (RG5)** : une valeur consignée `~`/« non
  applicable » est rendue comme telle (sans objet), jamais convertie en
  chiffre.
- **Comptages** : chaque fiche comptée est citée ; un comptage à zéro est dit
  tel quel (aucune valeur affirmée).
- **Journal « comme les recherches »** : chaque question est écrite dans
  `recherche_log` (`filtres->>'canal' = 'assistant'`, `nb_resultats` = nombre
  de citations) — mesure de l'usage réel.
- **Sans PostgreSQL** : 503 `{"etat": "indisponible"}` (décision §17.1).

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
