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
| `GET /recherche?q=&type_voile=&client=&bateau=&boat_model_id=&matiere=&gamme=&annee=&annee_min=&annee_max=&cote=&min=&max=&cote_min=&cote_max=&tri=&page=&limit=&offset=&inclure_a_valider=` | Recherche hybride : lexical tsvector pondéré A/B/C + trigrammes (volet dégradable) + texte des PDF (chunks/documents) + vecteurs (dormants, activables par injection `encode_requete`), fusionnés par **RRF k=60** ; depuis la migration 019, une **dimension détectée dans `q`** bascule sur le chemin numérique (voir « Recherche par dimension (Phase 1) » ci-dessous). Par défaut : fiches `valide` seulement. Réponse : `{requete, modeles_bateaux_detectes:[{id_modele, nom_canonique, cle_normalisee, type_correspondance, rang, score, alias}], nb_resultats, resultats:[{code, titre, type_voile, client, bateau, id_modele_bateau, modele_bateau, modele_bateau_cle, gamme, statut, annee, extrait, extrait_document:{texte, segments:[{texte, correspondance}], source, libelle_source, correspondance_requete}|null, score, sources}], facettes:{groupe:[{valeur, effectif}]}, facettes_cotes:{cote:{unite, min, max, effectif, intervalles:[{min,max,effectif,label}] }}, cote_active, cotes_unites, dimension_active, tri, page, sources_actives, sans_resultat, duree_ms}`. **Les 7 cotes d'un résultat (`slu_m`, `sle_m`, `sf_m`, `shw_m`, `spa_m2`, `tetiere_cm`, `poids_kg`) ne sont présentes QUE si un filtre dimension, une recherche par dimension ou un tri par cote est demandé — voir « Chargement conditionnel des cotes » ci-dessous.** |
| `GET /search/suggestions?prefix=&limit=` | Route canonique Lot 2 des suggestions au fil de la frappe : **valeurs réellement présentes seulement**, classées **exact > alias/synonyme > préfixe > flou (pg_trgm)**. Réponse : `{prefixe, suggestions:[{nature, valeur, type_correspondance, rang, score, alias?, id_modele_bateau?}]}`. Le proxy navigateur correspondant est `GET /api/search/suggestions`. |
| `GET /recherche/suggestions?prefix=&limit=` | Alias historique strictement équivalent à `/search/suggestions` (compatibilité) ; le proxy `/api/recherche/suggestions` est conservé. |
| `GET /referentiel/bateaux?inclure_fusionnes=` | Référentiel canonique Lot 2 : modèles finaux, alias et compteurs réels de bateaux/fiches. Lecture derrière le jeton de service. Réponse : `{modeles:[{id_modele, nom_canonique, cle_normalisee, source, actif, id_modele_fusion, alias:[{alias, cle_normalisee, source}], nb_bateaux, nb_fiches}]}`. |
| `POST /referentiel/bateaux/{id_modele}/alias` | Ajoute un alias (`{"alias": "…"}`), administrateur humain uniquement. Un alias déjà affecté à un autre modèle final répond **409** ; modèle inconnu **404**. |
| `POST /referentiel/bateaux/{id_modele}/fusion` | Fusion explicite et historisée vers `{"id_modele_cible": …, "motif": "…"}` ; administrateur humain uniquement. Aucune fusion n’est jamais inférée par la normalisation. |
| `POST /referentiel/bateaux/{id_modele}/defusion` | Annule la dernière fusion (ou `{"id_historique": …}` si fourni) depuis l’instantané historisé ; administrateur humain uniquement. Seules les lignes encore rattachées à la cible enregistrée sont restaurées. |
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
     (`slu_m_asc`, `slu_m_desc`, …, `poids_kg_asc`, `poids_kg_desc`) ; ou
  3. une **recherche par dimension est détectée dans `q`** (Phase 1,
     migration 019) — la réponse porte alors `dimension_active` non nul et les
     cotes des résultats sont affichées (c'est le but : « je vois toutes les
     voiles correspondantes » avec leurs valeurs).

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

- **Recherche par dimension (Phase 1, migration 019)** : la requête `q` est
  scindée en (dimension éventuelle + mots restants). Une dimension est un
  nombre **décimal** (« 6,60 » / « 6.60 ») ou muni d'une **unité** (« 660 cm »,
  « 6600 mm », « 6,60 m »), éventuellement suivi d'une **cote nommée**
  (`slu`, `sle`, `sf`, `shw`, `spa`, `tetiere`/`têtière`, `poids`, `guindant`).
  Frontière documentée : un **entier nu** (« 2026 », « 7792 ») n'est JAMAIS une
  dimension — années et codes restent des mots (« spi sailonet 2026 », « 7792-SO »
  ne basculent pas dans le filtre numérique), sauf si une cote est nommée
  (« tetiere 15 »).
  - **Normalisation** : « 6,6 », « 6.60 », « 6,60 m », « 660 cm », « 6600 mm »
    convergent vers la même valeur (6.6).
  - **Requête numérique seule** (« 6,60 ») : la valeur est cherchée dans
    **toutes les 7 cotes** avec tolérance **±0,5 %**, **par le chemin numérique
    existant** (bornes sur les colonnes de `v_fiche_recherche`, même mécanique
    que `cote=&min=&max=`) — `sources_actives == ["dimension"]`, aucun tsvector.
    Résultats ordonnés par écart à la valeur (les plus proches d'abord).
  - **Cote nommée** (« SLU 6,60 », « poids 6,60 ») : la valeur est cherchée
    **uniquement dans cette cote**, convertie dans son unité métier
    (« tetiere 150 mm » → 15 cm ; « slu 660 cm » → 6,60 m).
  - **Mots + dimension** (« spi 6,60 ») : les mots portent les sources
    textuelles, la valeur sert de **filtre** (toutes les cotes ou la cote
    nommée). Rappel : « spi » n'est PAS une cote — c'est un type de voile.
  - **`dimension_active`** dans la réponse : `{valeur, unite, cote,
    tolerance_pct: 0.5, texte_restant}` ou `null` si aucune dimension détectée.
  - Le texte de recherche pondéré des fiches (poids B) porte les 7 cotes dans
    les DEUX formes « 6.60 » et « 6,60 » (migration 019) — `websearch_to_tsquery`
    traite « 6,60 » en `'6' <-> '60'` et « 6.60 » en float unique `'6.60'`, les
    deux formes ne se croisent jamais (test de tokenisation RÉEL gardé par
    `tests/test_recherche_dimension.py::TestTokenisationReelle`).

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
  (`monofime` → Monofilm) ; seuil 0,30 ; indexable (GIN trigrammes, migration 007,
  clés normalisées migration 023).
- **Casse et diacritiques** : pour tous les champs réellement recherchés, les
  variantes d'un même terme (casse, accents latin, NFC/NFD, ligatures œ/æ/ß)
  produisent le **même ensemble d'identifiants**, dans le **même ordre**
  déterministe. La normalisation porte sur une *clé* (`seamtech_texte_recherche`
  côté SQL, `normaliser_cle_recherche` côté Python) : les textes affichés et
  les fichiers d'origine ne sont pas réécrits. Ce n'est **pas** une recherche
  par sous-chaîne, ni un changement des seuils de faute, ni une translittération
  arabe/cyrillique. Les filtres exacts (`type_voile=…`) et les codes/dimensions
  restent distincts de cette règle.
- **Ligatures** : équivalences retenues — `œ`/`Œ` ↔ `oe`, `æ`/`Æ` ↔ `ae`,
  `ß` ↔ `ss`. Un simple retrait d'accents ne les couvre pas.
- **Limite** : une faute *et* un accent manquant (`genios` pour « Génois »)
  reste une question de **seuil de trigrammes**, pas de normalisation. La
  normalisation garantit seulement que `GENOIS` et `GÉNOIS` votent pareil.
- **Synonymes** : `search_synonyms` (Lot 2, normalisés/versionnés) fusionné
  avec la table historique `synonyme`, cette dernière gardant priorité en cas
  de même terme ; cache de 30 s. Dans la recherche soumise, le texte tapé et
  sa forme canonique votent en **OU** dans les sources textuelles : remplacer
  aveuglément `spi` par `spinnaker` ferait perdre les fiches dont le libellé
  réel est « Spi Asymétrique ».
- **Extrait du document (Lot 4a, sans migration)** : chaque résultat de la
  page porte `extrait_document` — un aperçu d'au plus 360 caractères centré
  sur le passage pertinent et reconstruit par PostgreSQL (`ts_headline`). La
  provenance retenue est, après pertinence, un document réellement extrait
  rattaché directement, par le chemin du PDF source ou par une pièce jointe ;
  puis un chunk documentaire ; puis les champs de la fiche. Les chunks
  `resume_champs` (résumés générés pour les vecteurs) sont exclus afin de ne
  jamais les présenter comme du texte source. `segments` distingue le texte
  ordinaire des correspondances (`correspondance=true`) sans HTML : le
  navigateur affiche ces segments comme des nœuds de texte et des éléments
  `<mark>`. Quand aucun texte documentaire n'existe, `source="fiche"` rend
  l'origine explicite. L'ancien champ `extrait` reste inchangé pour les
  clients existants.
- **Référentiel bateaux (migration 026)** : `boat_models`,
  `boat_model_aliases`, `boat_model_merge_history`, `search_synonyms` et le
  lien nullable `bateau.boat_model_id`. L’identité dérive de `bateau.nom` ;
  `bateau.taille` est un détail et le libellé complet `nom + taille` est un
  alias. La clé plie casse/accents/ponctuation et frontières lettre-chiffre,
  protège le séparateur décimal et ne développe que les abréviations
  ponctuées connues (`Bav.`, `ben.`, `duf.`, `jan.`). Ainsi `BAVARIA 45`,
  `Bavaria-45` et `Bav. 45` sont un modèle, mais `45`, `45.1` et `45 CC`
  restent trois modèles. Un suffixe décimal appartenant à un modèle
  exact/alias gagne sur l’analyseur de dimensions. Le filtre `bateau=` est
  résolu en exact/alias vers le modèle final ; `boat_model_id=` filtre
  directement ce modèle.
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

## Déduplication avant validation (Lot L.1, plan v3.0 §17.4)

**Détection PROPOSITIVE — à lire avant toute intégration.** Aucune de ces routes ne
supprime, ne fusionne ni ne change le `statut` d'une fiche. Elles constatent et
enregistrent des **liens** (`fiche_lien`), la décision reste humaine (RG3). Le
paramètre `appliquer` vaut **`false` par défaut** partout : sans lui, rien n'est écrit.

| Route | Rôle |
|---|---|
| `GET /fiches/doublons` | Tous les liens de doublon connus (`?type=doublon_exact` ou `doublon_probable` pour filtrer) : `id_lien`, `type`, `score`, codes source/cible, statuts, `cree_le`. |
| `POST /fiches/doublons/scan` | Lance une détection **sans écrire** (`dry_run` implicite) et rend `{groupes_exacts, liens_exacts, liens_probables, liens_crees, liens_deja_presents, fiches_concernees, trigrammes_disponibles}`. Corps optionnel : `{"seuil": 0.55, "appliquer": true}` — `appliquer=true` est le SEUL chemin qui écrit des liens, jamais une fusion. Idempotent : rejouer ne duplique aucun lien (`ON CONFLICT DO NOTHING`). |
| `GET /fiches/{code}/doublons` | Liens d'UNE fiche, dans les deux sens (source ou cible). 404 si le code est inconnu. C'est ce que consomme le bandeau de `/validation`. |
| `POST /validation/doublons` | Liens pour une LISTE de codes (une seule requête pour tout l'écran de validation). Corps `{"codes": ["0701-GV-001", …]}` → `{"par_code": {code: [lien, …]}}` ; chaque code demandé est présent, même sans lien. |

**Doublon exact** = même empreinte SHA-256 de pièce jointe portée par plusieurs fiches
(`GROUP BY empreinte_sha256 HAVING count(DISTINCT id_fiche) > 1`). **Doublon probable** =
titres similaires (pg_trgm) ; sans l'extension, repli déterministe documenté (titre
normalisé identique ET même client/bateau/gamme/année), jamais une erreur.

**CLI** : `python -m seamtech_search.dedup.cli scan` (alias installé : `dedup-scan`) —
simulation par défaut, `--appliquer` pour écrire les liens, `--seuil`, `--json`,
`--database-url`. PostgreSQL requis (503 sinon).

## Comptes nominatifs et « qui a validé quoi » (Lot L.2, plan v3.0 §10.1)

Deux chemins, jamais un seul :

```
navigateur ──cookie signé (HMAC)──► Next.js /api/* ──X-SEAMTECH-TOKEN──► API Python
```

Le navigateur ne détient **jamais** `SEAMTECH_AUTH_TOKEN` : le proxy serveur le porte, et
c'est ce qui rend les en-têtes d'attribution dignes de foi.

| Route | Rôle |
|---|---|
| `POST /auth/connexion` | Corps `{"identifiant", "mot_de_passe"}`. Vérifie `utilisateur.empreinte_mot_de_passe` (scrypt) et ouvre une session : `{"ok", "id_utilisateur", "identifiant", "nom", "role", "id_session", "jeton_session", "expire_le"}`. 401 si les identifiants sont faux (réponse identique pour un compte inexistant), 429 si 5 tentatives ont échoué en 5 minutes (`Retry-After` renseigné). |
| `POST /auth/deconnexion` | Corps `{"id_session", "jeton_session"}` ; exige le JETON (un `id_session` seul est devinable). Pose `revoque_le` et supprime la clé Redis : le cookie, même encore signé, ne vaut plus rien. Idempotent. 401 si le jeton ne correspond pas. |
| `POST /auth/deconnexion-tout` | « Déconnecter tous mes appareils » : en-têtes de session vivante exigés, puis révocation PostgreSQL + Redis de **toutes** les sessions du compte, y compris l'appelante. |
| `GET /auth/session` | En-têtes `X-SEAMTECH-SESSION` + `X-SEAMTECH-SESSION-JETON` → identité de la session. 401 si la session est expirée (absolue 8 h ou inactive 30 min par défaut), révoquée, absente du registre Redis, ou si le compte a été désactivé ; 503 si Redis exigé est injoignable (fail-closed). **Jamais** d'empreinte, de sel ni de jeton dans la réponse. |
| `POST /auth/mot-de-passe` | Changement par l'intéressé : `{"mot_de_passe_actuel", "nouveau_mot_de_passe"}`. Éteint `doit_changer_mot_de_passe` et révoque toutes les sessions du compte. 401 si l'actuel est faux. |
| `GET /auth/utilisateurs` | Liste des comptes : `identifiant`, `nom`, `role`, `actif`, dates, `mot_de_passe_defini` (booléen) — jamais l'empreinte. **403 si la session n'est pas `administrateur`**, 401 sans session. |
| `POST /auth/utilisateurs` | Crée un compte (`identifiant`, `nom`, `role`, `mot_de_passe`). 403 pour un opérateur, 409 si l'identifiant existe, 422 si rôle inconnu. |
| `POST /auth/utilisateurs/{identifiant}/desactiver` | `actif=false` ET révocation des sessions ouvertes (désactiver sans révoquer laisserait un cookie travailler). 404 si inconnu. **Aucune suppression** de compte ni d'historique. |
| `POST /auth/utilisateurs/{identifiant}/mot-de-passe` | Réinitialise (changement imposé à la prochaine connexion) et révoque les sessions. |

**Politique de session (Lot 0a).** PostgreSQL `session_ui` reste l'historique
durable ; Redis est le registre vivant consulté à chaque validation.
`SEAMTECH_SESSION_HOURS` borne la durée absolue (défaut `8`),
`SEAMTECH_SESSION_IDLE_MINUTES` l'inactivité (défaut `30`), et
`SEAMTECH_REQUIRE_REDIS_SESSIONS=true` rend Redis obligatoire en production.
Le jeton est régénéré à chaque connexion ; seuls les hachages SHA-256 sont
stockés. Le front ajoute un CSRF double-submit (cookie `seamtech_csrf` +
en-tête `X-CSRF-Token`) sur toutes les mutations navigateur authentifiées.

**Frontière de confiance (à ne pas déplacer).** Le proxy Next.js pose
`X-SEAMTECH-UTILISATEUR` et `X-SEAMTECH-ROLE` depuis le cookie signé. Côté Python ces
en-têtes ne sont honorés **que si `X-SEAMTECH-TOKEN` est valide sur la même requête** :
une requête qui les porte sans le jeton de service reçoit **401** et la fiche n'est pas
attribuée. Les routes de validation (`POST /fiches/{code}/corriger|valider|rejeter|rouvrir`,
`POST /validation/lot`) prennent l'identité de cette session ; le champ `utilisateur` du
corps de requête n'est plus qu'un **repli d'outillage** (scripts, tests), jamais
prioritaire. L'écran ne l'envoie plus : une seule source de vérité.

**Concurrence entre postes (verrou optimiste, migration `021_revision_fiche`).** Une
fiche porte un numéro de `revision` (entier ≥ 1), publié par `GET /fiches/{code}` et
incrémenté par toute écriture qui change son contenu (correction d'un champ, validation,
rejet, réouverture). Le corps de `POST /fiches/{code}/corriger` DOIT porter `revision` :
si un autre poste a enregistré entre l'ouverture et l'enregistrement, la correction est
**refusée (409)** avec `{code: "conflit_revision", revision_actuelle, valeur_actuelle,
corrige_par, corrige_le, regle}` — la valeur du collègue est conservée, rien de ce poste
n'est écrit (pas même la ligne « utilisateur » de l'auteur).

Sans `revision`, la correction est **refusée (428)** : « la révision n'a pas pu être lue
côté poste » ne doit jamais devenir « écriture non protégée ». Le seul moyen d'ouvrir un
client ancien est de l'ASSUMER (`SEAMTECH_REQUIRE_REVISION=false`, jamais sur un poste
d'atelier) ; l'écriture est alors non protégée et journalisée comme telle.

`GET /fiches/{code}/etat` publie **statut + révision + champs dans UNE réponse** (un seul
instantané : la révision renvoyée est toujours celle des valeurs renvoyées). L'écran de
validation ouvre et recharge par cette route — deux requêtes séparées pouvaient apparier
des valeurs périmées avec une révision à jour, et laisser passer une correction fondée sur
un état jamais lu.

**Les décisions sont liées à la révision revue.** `POST /fiches/{code}/valider|rejeter|rouvrir`
et `POST /validation/lot` (`revisions: {code: n}`) vérifient statut ET révision dans le même
`UPDATE` : si la fiche a changé depuis l'affichage, la décision est **refusée (409)**
(`{code: "conflit_decision", decision, revision_envoyee, revision_actuelle, statut_actuel}`)
et **aucune ligne n'est ajoutée au journal** — l'opérateur doit recharger et décider à
nouveau. En lot, une fiche modifiée depuis la sélection est IGNORÉE avec sa raison
(`ignorees[].raison`), les autres sont validées. Le front (écran Validation) envoie la
révision, affiche le conflit avec la valeur du collègue (ou le statut réel pour une
décision) et propose de recharger la fiche ; sans révision lue, il DÉSACTIVE les boutons
d'écriture.

**Vérrouillage des connexions** : le compteur d'échecs vit dans la table `audit_log`
existante (`action='connexion_refusee'`), pas dans une table dédiée — la mission fixe
32 tables métier après L.2 et une connexion refusée EST un événement d'audit. Conséquence
documentée : `SELECT … FROM audit_log WHERE actor='<identifiant>' AND action='connexion_refusee'`
liste les tentatives. Une connexion réussie (`action='connexion'`) remet le compteur à zéro ;
aucune purge automatique n'est faite (on ne supprime jamais de données).

**CLI** : `python -m seamtech_search.comptes.cli` (alias installé : `comptes`) —
`creer --identifiant --nom --role operateur|administrateur`, `lister`, `desactiver`,
`reinitialiser-mot-de-passe`, `sessions --identifiant`, `revoquer-session --id-session`,
`verifier --identifiant`. Le mot de passe est **toujours lu sur STDIN, jamais en argument**
(un mot de passe en argument finit dans l'historique du shell et dans `ps`).

**Compte de secours (limite assumée)** : `SEAMTECH_UI_PASSWORD`, identifiant `secours`,
rôle `administrateur`. Il n'existe PAS en base : sa session n'est donc ni révocable ni
attribuable (`X-SEAMTECH-UTILISATEUR: secours`), et il sert uniquement à créer les premiers
comptes nominatifs puis à dépanner. À désactiver (`SEAMTECH_UI_PASSWORD` vide) une fois les
comptes nominatifs en place — dans ce cas le front refuse toute connexion et l'application
n'est accessible que par les comptes nominatifs.

**Sécurité** : empreintes `scrypt$n=16384$r=8$p=1$<sel_b64>$<hash_b64>` (`hashlib.scrypt`,
sel de 16 octets via `secrets`), vérification `hmac.compare_digest`, **bibliothèque standard
uniquement** (aucune dépendance nouvelle). Les jetons de session ne sont stockés que par leur
empreinte SHA-256 — un dump de la base ne permet pas de rejouer une session.

## Journal d'audit canonique et surfaces sensibles (Lot 0b)

La migration `025_audit_logs` ajoute `audit_logs`, journal PostgreSQL
**append-only** (trigger refusant `UPDATE`/`DELETE`) et chaîné par empreinte
SHA-256 : chaque ligne porte l'empreinte de la précédente, un `request_id`,
l'acteur structuré (`actor_type`, `id_utilisateur`, `identifiant`, `role`,
`id_session`), la ressource (`resource_type` + `resource_id`), le code HTTP et
des `details` expurgés (aucun mot de passe, jeton, cookie ou secret). La
migration `024_sessions_redis` est le marqueur sans DDL du Lot 0a. La table
historique `audit_log` reste écrite pour compatibilité (SQLite et verrouillage
des connexions) ; en PostgreSQL, `GET /audit` lit `audit_logs` dès qu'il
contient des événements, en conservant les clés historiques (`id`, `timestamp`,
`actor`, `action`, `resource`, `status`, `details`) enrichies des nouveaux
champs.

Le middleware journalise dans ce journal toutes les mutations, tous les refus
HTTP et les lectures sensibles (octets de pièces/artefacts, audit, maintenance,
administration, journal de recherche), sans corps ni query string. Les actions
de compte (connexion, déconnexions, création, désactivation, changements de mot
de passe) sont aussi des événements explicites.

**Gardes PostgreSQL du Lot 0b** (le mode SQLite conserve le contrat
d'outillage derrière le jeton de service, faute de comptes nominatifs) :

| Surface | Exigence en PostgreSQL |
|---|---|
| `GET /audit`, `GET /metrics`, `GET /preview`, `/maintenance/*`, `/ml/*`, `GET /recherche/journal` | session humaine `administrateur` vérifiée (ou compte `secours` derrière le jeton de service) ; service seul ⇒ 401, opérateur ⇒ 403 |
| `GET|HEAD /pieces/{id}/apercu`, `/pieces/{id}/telecharger`, `GET /imports/{id}/artifacts/{artifact}` | session humaine vivante ; l'objet est ensuite relu par ID et confiné aux racines/staging/rapports |
| corrections/validations de fiches | si les en-têtes de session sont présents, l'identité est revérifiée et relue en base ; un en-tête utilisateur/rôle incompatible ⇒ 401 |
| `POST /fiches/doublons/scan` avec `appliquer=true` | session humaine vivante (le dry-run reste un comptage) |
| `POST /referentiel/bateaux/{id}/*` (Lot 2) | session humaine `administrateur` vérifiée (ou `secours` derrière le jeton de service) ; service seul ⇒ 401, opérateur ⇒ 403. `GET /referentiel/bateaux` reste une lecture derrière le jeton de service |
| `POST /imports/dossier`, `POST /imports/dossier/lot` | dossier/racine obligatoirement sous une racine configurée ou le staging, sinon 403 |

`POST /open` reste l'endpoint historique des scripts serveur de qualification :
le navigateur ne l'utilise plus (`/api/open` = 410, pièces par ID). En
PostgreSQL, il exige désormais en plus que le chemin désigne un document
**catalogué** : un fichier simplement présent sous une racine mais absent de
`documents` répond 404.

Tous les proxys Next de données transmettent désormais `backendHeaders()`
(jeton de service + identité/session recalculées depuis le cookie signé), pas
le seul jeton de service. Les exceptions restent les routes d'authentification
elles-mêmes et `/api/health`.

## Tableau de bord qualité (Lot K.1 + L + M)

`GET /qualite/tableau-de-bord` — 10 indicateurs, chacun avec sa `definition`, son `unite` et
sa `periode` (exigence Lot K), tous en `GROUP BY` côté SQL :

| Indicateur | Contenu |
|---|---|
| `taux_extraction_auto` | Part des champs extraits sans correction humaine. |
| `taux_correction_par_champ` | Taux de correction par champ, trié décroissant. |
| `temps_validation` | Durée de validation : médiane et p95. |
| `anomalies_frequentes` | Anomalies les plus fréquentes par code. |
| `volume_par_statut` | Nombre de fiches par statut. |
| `usage_recherches` | Usage réel de la recherche. |
| `lots` | Lots d'ingestion : dossiers, réussites, échecs. |
| `doublons_detectes` | Lot L.1 — `groupes_exacts`, `liens_exacts`, `liens_probables`, `doublons_vus_avant_validation` (fiches **non `valide`** déjà engagées dans un lien : le chiffre du lot — combien de doublons ont été vus AVANT validation). Un doublon vu n'est pas un doublon traité. |
| `taux_par_utilisateur` | Lot L.2 — `actions_total`, `actions_attribuees`, `actions_sans_utilisateur`, `part_attribuee`, `par_utilisateur` (identifiant, nom, rôle, actions), `comptes_actifs_sans_action`. Les actions sans utilisateur sont l'héritage d'avant L.2 : elles ne sont **jamais** réattribuées a posteriori. |
| `ocr_etage3` | Lot M — `total_pages`, `pages_ocerisees`, `fichiers_scannes`, `pages_ignorees_texte_natif`, `echecs`, `taille_texte_produit`, `duree_totale_s`, `debit_moyen_pages_par_minute`, `confiance_moyenne`. Source : table de staging `ocr_etage3` (une ligne = une page). Rappel plan v3.0 : OCR seulement là où c'est utile — le texte des fiches est déjà dans le PDF. |

**Règle de maintenance** : tout nouvel indicateur arrive avec (a) sa clé dans
`tests/test_qualite_tableau.py::CLES_TABLEAU_DE_BORD`, (b) un test de valeurs, (c) sa ligne
dans ce tableau. Le test de forme compare l'**ensemble exact** des clés : un indicateur
ajouté sans être déclaré fait échouer la CI.

## OCR par étages — Lot M (préparation Lot G)

> « OCR seulement là où c'est utile — le texte des fiches est déjà dans le PDF » (plan v3.0 §4 bis)

### CLI

`python3 -m seamtech_search.ocr.cli` (alias installé : `ocr-nuit`) — trois commandes :

| Commande | Rôle |
|---|---|
| `inventaire --dossier <chemin> [--seuil 20] [--json]` | Étage 1 + étage 3 : inventaire rapide (fichiers, extensions, tailles, par année/dossier) + inventaire des scans (fichiers_scannes, fichiers_texte_natif, pages_a_oceriser, estimation_duree_s). Phase d'inventaire obligatoire avant traitement (exigence plan). |
| `nuit --dossier <chemin> --limite N --budget-minutes B [--seuil 20] [--langue fra] [--resolution 300] [--tesseract-command tesseract] [--pages 0,1] [--database-url URL] [--travail-dir DIR] [--json]` | Exécution nocturne : verrou (un seul run à la fois, second sort code 2 proprement), état reprenable par empreinte SHA-256 (jamais mtime), budget de temps respecté à la minute, arrêt propre (état écrit, verrou libéré), compte rendu JSON + texte (fichiers examinés, pages océrisées, pages ignorées texte natif, échecs avec motif, durée totale, débit pages/min, taille texte produit, moteur+version). Toute sortie dans répertoire travail déclaré `SEAMTECH_OCR_TRAVAIL_DIR` (défaut `data/ocr_travail`), jamais dans dossier source (RG13). Aucun appel réseau (RG14). |
| `rapport --depuis <YYYY-MM-DD> [--json]` | Liste les comptes rendus précédents depuis une date, dans travail_dir/rapports/. |

Variable d'environnement : `SEAMTECH_OCR_TRAVAIL_DIR` (répertoire hors archive pour verrou, état, rapports).

### Table de staging

`ocr_etage3` (migration 017) — staging du texte OCR par page, jamais dans `chunk` ni `document` (promotion = Lot G) :

| Colonne | Type | Description |
|---|---|---|
| `fichier_source` | TEXT | Chemin source (traçabilité humaine) |
| `empreinte_sha256` | TEXT | Empreinte SHA-256 fichier source (idempotence) |
| `page` | INTEGER | Numéro page 0-based |
| `texte_ocr` | TEXT | Texte produit par OCR |
| `confiance` | REAL | Confiance moyenne tesseract (0-100) ou NULL |
| `moteur` | TEXT | Moteur (tesseract) |
| `version_moteur` | TEXT | Version binaire (ex tesseract 5.3.0) |
| `duree_s` | REAL | Durée OCR page |
| `page_ocerisee` | BOOLEAN | True si OCR a produit du texte |
| `motif` | TEXT | Raison non-OCR (texte natif présent, tesseract absent, etc.) |
| `horodatage` | TIMESTAMPTZ | Date insertion |

Index : empreinte, fichier, page_ocerisee, horodatage.

### Planification

Documentée dans `docs/OCR_ETAGES.md` (Windows `schtasks`, Linux `cron`), non exécutée. Précautions : budget, verrou, répertoire travail, journal.

### Règle d'étage (inviolable)

- PDF à texte natif : ZÉRO page océrisée, texte natif jamais remplacé (fonction pure `doit_oceriser_page`)
- PDF scanné : pages sans texte SONT océrisées
- Fichier déjà traité (même empreinte) : jamais retraité (idempotence)
- Image isolée (png/jpg/tif) : traitée comme scan d'une page
- Seuil paramétrable, défaut 20 justifié (voir `docs/OCR_ETAGES.md`)

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
