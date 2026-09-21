# API — routes fiche et gabarits (Lot B / B.2, plan v3.0 §17.11)

Ces routes complètent l'API existante (documents, recherche, imports). Elles sont
**PostgreSQL uniquement** (tables `fiche*`/`gabarit`, migrations 006-009 — décision
de couche §17.1) : sans `database_url`, elles répondent **503** avec l'explication.
Authentification : mêmes règles que le reste de l'API (en-tête `X-SEAMTECH-TOKEN`
quand `SEAMTECH_AUTH_TOKEN` est configuré).

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

## Exemples

```bash
curl -H "X-SEAMTECH-TOKEN: $TOKEN" http://localhost:8000/fiches/7792-SO/champs
curl -H "X-SEAMTECH-TOKEN: $TOKEN" http://localhost:8000/gabarits
curl -H "X-SEAMTECH-TOKEN: $TOKEN" -H "X-SEAMTECH-TOKEN: $TOKEN" \
     -F "fichier=@fiche.pdf" http://localhost:8000/gabarits/detecter
```

## Extraction (hors HTTP, lot B)

`python -m seamtech_search.fiches.cli extraire CHEMIN.pdf` — rapport champ par champ
(valeur, confiance, page, zone) ; `ecrire` (transaction, statut `a_valider`), `init`,
`banc` (non-régression `gabarit_test`). La route HTTP d'extraction arrive avec le lot C.
