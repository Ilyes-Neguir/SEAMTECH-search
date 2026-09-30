# Banc d'échelle SYNTHÉTIQUE — run 36758180778

> Données synthétiques uniquement. Ne constitue pas une mesure réelle de VPS01, R2 ni du poste atelier.

- Run: https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36758180778
- Commit exécuté: `cf6f8cb29942050bdecebe53b75585421b187d0e`
- Base PostgreSQL jetable GitHub Actions; 10 000 fiches synthétiques; 50 mesures par scénario.
- Durée du job: 1 min 44 s; budget maximal demandé: moins de 20 min.
- Cible p95 indicative de ce micro-benchmark: 250 ms; distincte du critère produit réel.
- JSON: [`scale-bench-synthetique-36758180778.json`](scale-bench-synthetique-36758180778.json)

## Mesures (ms)

| Scénario | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| mot_simple | 79.6 | 84.72 | 105.52 | 105.52 |
| multi_mots | 101.09 | 110.47 | 119.08 | 119.08 |
| code | 13.27 | 14.79 | 15.71 | 15.71 |
| facette_dimension | 130.95 | 141.35 | 155.71 | 155.71 |
| filtres | 175.31 | 179.67 | 189.25 | 189.25 |
| suggestions | 9.08 | 9.55 | 10.65 | 10.65 |

## Avant / après — Phase 1 (recherche par dimension, migration 019)

Comparaison avec le dernier run AVANT la Phase 1 :
[`scale-bench-synthetique-36598278884.md`](scale-bench-synthetique-36598278884.md)
(run 36598278884, commit `887beba`, mesures des p95 ci-dessous). **SYNTHÉTIQUE — bruit de runner inclus.**

| Scénario | p50 avant | p95 avant | p50 après | p95 après | Δ p95 |
|---|---:|---:|---:|---:|---:|
| mot_simple | 81.13 | 84.81 | 79.6 | 84.72 | -0.09 |
| multi_mots | 107.86 | 109.83 | 101.09 | 110.47 | +0.64 |
| code | 11.7 | 12.43 | 13.27 | 14.79 | +2.36 |
| facette_dimension | 92.97 | 94.0 | 130.95 | 141.35 | +47.35 |
| filtres | 98.04 | 99.76 | 175.31 | 179.67 | +79.91 |
| suggestions | 8.47 | 8.76 | 9.08 | 9.55 | +0.79 |

Constat honnête : deux scénarios dérivent nettement (`facette_dimension` +47 ms,
`filtres` +80 ms), les quatre autres restent dans le bruit. Les six p95 restent
**sous la cible indicative de 250 ms** de ce micro-benchmark. La dérive n'est ni
infirmée ni expliquée ici — elle est tracée pour être suivie (données
synthétiques uniquement ; aucune latence réelle de poste atelier n'est mesurée
par ce banc).

## EXPLAIN ANALYZE / BUFFERS — trois requêtes SELECT observées les plus lentes

### Rang 1 — observation 171.857 ms

```text
Append  (cost=2471.21..9887.35 rows=795 width=68) (actual time=114.776..168.262 rows=9 loops=1)
  Buffers: shared hit=283245
  CTE correspondances
    ->  HashAggregate  (cost=1050.55..1150.58 rows=10003 width=8) (actual time=5.153..6.014 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=827
          ->  Append  (cost=0.00..1025.55 rows=10003 width=8) (actual time=0.049..3.387 rows=10000 loops=1)
                Buffers: shared hit=827
                ->  Seq Scan on fiche f_12  (cost=0.00..950.00 rows=10000 width=8) (actual time=0.049..2.712 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
                      Buffers: shared hit=825
                ->  Bitmap Heap Scan on chunk c_1  (cost=8.56..13.91 rows=2 width=8) (actual time=0.005..0.006 rows=0 loops=1)
                      Recheck Cond: (tsv @@ '''voile'''::tsquery)
                      Filter: (id_fiche IS NOT NULL)
                      Buffers: shared hit=2
                      ->  Bitmap Index Scan on idx_chunk_tsv  (cost=0.00..8.56 rows=2 width=0) (actual time=0.003..0.003 rows=0 loops=1)
                            Index Cond: (tsv @@ '''voile'''::tsquery)
                            Buffers: shared hit=2
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.002..0.002 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
  ->  GroupAggregate  (cost=1320.63..1320.87 rows=12 width=68) (actual time=31.295..31.297 rows=0 loops=1)
        Group Key: tv.libelle
        Buffers: shared hit=44435
        ->  Sort  (cost=1320.63..1320.66 rows=12 width=32) (actual time=31.294..31.296 rows=0 loops=1)
              Sort Key: tv.libelle
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=44435
              ->  Nested Loop  (cost=225.79..1320.41 rows=12 width=32) (actual time=31.284..31.287 rows=0 loops=1)
                    Join Filter: (f.id_fiche = f_1.id_fiche)
                    Buffers: shared hit=44435
                    ->  Nested Loop  (cost=225.51..1259.99 rows=12 width=48) (actual time=31.284..31.286 rows=0 loops=1)
                          Buffers: shared hit=44435
                          ->  Nested Loop  (cost=225.35..1236.57 rows=12 width=24) (actual time=9.746..30.581 rows=2858 loops=1)
                                Buffers: shared hit=44435
                                ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=9.733..10.757 rows=10000 loops=1)
                                      Group Key: correspondances.id_fiche
                                      Batches: 1  Memory Usage: 929kB
                                      Buffers: shared hit=827
                                      ->  CTE Scan on correspondances  (cost=0.00..200.06 rows=10003 width=8) (actual time=5.154..7.667 rows=10000 loops=1)
                                            Buffers: shared hit=827
                                ->  Index Scan using fiche_pkey on fiche f_1  (cost=0.29..5.04 rows=1 width=32) (actual time=0.002..0.002 rows=0 loops=10000)
                                      Index Cond: (id_fiche = correspondances.id_fiche)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND (gamme = 'Régate'::text) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 1
                                      Buffers: shared hit=41664
                          ->  Memoize  (cost=0.16..1.94 rows=1 width=40) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_1.id_type_voile
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using type_voile_pkey on type_voile tv  (cost=0.15..1.93 rows=1 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                                      Index Cond: (id_type_voile = f_1.id_type_voile)
                                      Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f  (cost=0.29..5.02 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1320.63..1320.87 rows=12 width=68) (actual time=23.995..23.997 rows=0 loops=1)
        Group Key: c.nom
        Buffers: shared hit=43608
        ->  Sort  (cost=1320.63..1320.66 rows=12 width=32) (actual time=23.995..23.996 rows=0 loops=1)
              Sort Key: c.nom
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=43608
              ->  Nested Loop  (cost=225.79..1320.41 rows=12 width=32) (actual time=23.992..23.994 rows=0 loops=1)
                    Join Filter: (f_2.id_fiche = f_3.id_fiche)
                    Buffers: shared hit=43608
                    ->  Nested Loop  (cost=225.51..1259.99 rows=12 width=48) (actual time=23.992..23.993 rows=0 loops=1)
                          Buffers: shared hit=43608
                          ->  Nested Loop  (cost=225.35..1236.57 rows=12 width=24) (actual time=2.569..23.301 rows=2858 loops=1)
                                Buffers: shared hit=43608
                                ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.561..3.592 rows=10000 loops=1)
                                      Group Key: correspondances_1.id_fiche
                                      Batches: 1  Memory Usage: 929kB
                                      ->  CTE Scan on correspondances correspondances_1  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.535 rows=10000 loops=1)
                                ->  Index Scan using fiche_pkey on fiche f_3  (cost=0.29..5.04 rows=1 width=32) (actual time=0.002..0.002 rows=0 loops=10000)
                                      Index Cond: (id_fiche = correspondances_1.id_fiche)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND (gamme = 'Régate'::text) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 1
                                      Buffers: shared hit=41664
                          ->  Memoize  (cost=0.16..1.94 rows=1 width=40) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_3.id_client
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using client_pkey on client c  (cost=0.15..1.93 rows=1 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                                      Index Cond: (id_client = f_3.id_client)
                                      Filter: ((nom IS NOT NULL) AND (nom <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f_2  (cost=0.29..5.02 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances_1.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1320.84..1321.14 rows=12 width=68) (actual time=23.894..23.895 rows=0 loops=1)
        Group Key: (((b.nom || ' '::text) || COALESCE(b.taille, ''::text)))
        Buffers: shared hit=43608
        ->  Sort  (cost=1320.84..1320.87 rows=12 width=32) (actual time=23.894..23.895 rows=0 loops=1)
              Sort Key: (((b.nom || ' '::text) || COALESCE(b.taille, ''::text)))
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=43608
              ->  Nested Loop  (cost=225.80..1320.62 rows=12 width=32) (actual time=23.891..23.892 rows=0 loops=1)
                    Join Filter: (f_4.id_fiche = f_5.id_fiche)
                    Buffers: shared hit=43608
                    ->  Nested Loop  (cost=225.51..1260.14 rows=12 width=80) (actual time=23.891..23.892 rows=0 loops=1)
                          Buffers: shared hit=43608
                          ->  Nested Loop  (cost=225.35..1236.57 rows=12 width=24) (actual time=2.456..23.208 rows=2858 loops=1)
                                Buffers: shared hit=43608
                                ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.449..3.465 rows=10000 loops=1)
                                      Group Key: correspondances_2.id_fiche
                                      Batches: 1  Memory Usage: 929kB
                                      ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.535 rows=10000 loops=1)
                                ->  Index Scan using fiche_pkey on fiche f_5  (cost=0.29..5.04 rows=1 width=32) (actual time=0.002..0.002 rows=0 loops=10000)
                                      Index Cond: (id_fiche = correspondances_2.id_fiche)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND (gamme = 'Régate'::text) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 1
                                      Buffers: shared hit=41664
                          ->  Memoize  (cost=0.16..1.95 rows=1 width=72) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_5.id_bateau
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using bateau_pkey on bateau b  (cost=0.15..1.94 rows=1 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                                      Index Cond: (id_bateau = f_5.id_bateau)
                                      Filter: ((((nom || ' '::text) || COALESCE(taille, ''::text)) IS NOT NULL) AND (((nom || ' '::text) || COALESCE(taille, ''::text)) <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f_4  (cost=0.29..5.02 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances_2.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1363.02..1363.24 rows=2 width=45) (actual time=35.591..35.921 rows=2 loops=1)
        Group Key: f_7.gamme
        Buffers: shared hit=64641
        ->  Sort  (cost=1363.02..1363.09 rows=25 width=9) (actual time=35.244..35.464 rows=5715 loops=1)
              Sort Key: f_7.gamme
              Sort Method: quicksort  Memory: 304kB
              Buffers: shared hit=64641
              ->  Nested Loop  (cost=225.64..1362.44 rows=25 width=9) (actual time=2.580..34.526 rows=5715 loops=1)
                    Join Filter: (f_6.id_fiche = f_7.id_fiche)
                    Buffers: shared hit=64641
                    ->  Nested Loop  (cost=225.35..1236.57 rows=25 width=25) (actual time=2.574..25.521 rows=5715 loops=1)
                          Buffers: shared hit=43608
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.567..3.682 rows=10000 loops=1)
                                Group Key: correspondances_3.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.542 rows=10000 loops=1)
                          ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..5.04 rows=1 width=41) (actual time=0.002..0.002 rows=1 loops=10000)
                                Index Cond: (id_fiche = correspondances_3.id_fiche)
                                Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                Rows Removed by Filter: 0
                                Buffers: shared hit=39720
                    ->  Index Only Scan using fiche_pkey on fiche f_6  (cost=0.29..5.02 rows=1 width=8) (actual time=0.001..0.001 rows=1 loops=5715)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Heap Fetches: 9603
                          Buffers: shared hit=21033
  ->  HashAggregate  (cost=2149.94..2163.17 rows=756 width=68) (actual time=27.492..27.496 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=37629
        ->  Hash Join  (cost=1262.85..2137.44 rows=2500 width=32) (actual time=5.302..26.565 rows=5000 loops=1)
              Hash Cond: (f_8.id_fiche = f_9.id_fiche)
              Buffers: shared hit=37629
              ->  Nested Loop  (cost=225.35..1074.32 rows=5000 width=16) (actual time=2.474..20.567 rows=10000 loops=1)
                    Buffers: shared hit=36804
                    ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.468..3.594 rows=10000 loops=1)
                          Group Key: correspondances_4.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.537 rows=10000 loops=1)
                    ->  Index Only Scan using fiche_pkey on fiche f_8  (cost=0.29..5.02 rows=1 width=8) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_4.id_fiche)
                          Heap Fetches: 16804
                          Buffers: shared hit=36804
              ->  Hash  (cost=975.00..975.00 rows=5000 width=36) (actual time=2.784..2.784 rows=5000 loops=1)
                    Buckets: 8192  Batches: 1  Memory Usage: 299kB
                    Buffers: shared hit=825
                    ->  Seq Scan on fiche f_9  (cost=0.00..975.00 rows=5000 width=36) (actual time=0.050..2.021 rows=5000 loops=1)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme = 'Régate'::text))
                          Rows Removed by Filter: 5000
                          Buffers: shared hit=825
  ->  GroupAggregate  (cost=1243.49..1243.51 rows=1 width=68) (actual time=25.646..25.648 rows=0 loops=1)
        Group Key: m.nom
        Buffers: shared hit=49324
        ->  Sort  (cost=1243.49..1243.50 rows=1 width=40) (actual time=25.646..25.647 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=49324
              ->  Nested Loop  (cost=225.94..1243.48 rows=1 width=40) (actual time=25.632..25.633 rows=0 loops=1)
                    Buffers: shared hit=49324
                    ->  Nested Loop  (cost=225.79..1243.23 rows=1 width=16) (actual time=25.631..25.632 rows=0 loops=1)
                          Join Filter: (f_10.id_fiche = f_11.id_fiche)
                          Buffers: shared hit=49324
                          ->  Nested Loop  (cost=225.50..1239.50 rows=1 width=32) (actual time=25.631..25.632 rows=0 loops=1)
                                Join Filter: (fm.id_fiche = f_11.id_fiche)
                                Buffers: shared hit=49324
                                ->  Nested Loop  (cost=225.35..1236.57 rows=12 width=16) (actual time=2.531..24.131 rows=2858 loops=1)
                                      Buffers: shared hit=43608
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.521..3.662 rows=10000 loops=1)
                                            Group Key: correspondances_5.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            ->  CTE Scan on correspondances correspondances_5  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.547 rows=10000 loops=1)
                                      ->  Index Scan using fiche_pkey on fiche f_11  (cost=0.29..5.04 rows=1 width=32) (actual time=0.002..0.002 rows=0 loops=10000)
                                            Index Cond: (id_fiche = correspondances_5.id_fiche)
                                            Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND (gamme = 'Régate'::text) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                            Rows Removed by Filter: 1
                                            Buffers: shared hit=41664
                                ->  Index Scan using fiche_materiau_id_fiche_role_niveau_key on fiche_materiau fm  (cost=0.15..0.21 rows=3 width=16) (actual time=0.000..0.000 rows=0 loops=2858)
                                      Index Cond: (id_fiche = correspondances_5.id_fiche)
                                      Buffers: shared hit=5716
                          ->  Index Only Scan using fiche_pkey on fiche f_10  (cost=0.29..3.72 rows=1 width=8) (never executed)
                                Index Cond: (id_fiche = fm.id_fiche)
                                Heap Fetches: 0
                    ->  Index Scan using materiau_pkey on materiau m  (cost=0.15..0.25 rows=1 width=40) (never executed)
                          Index Cond: (id_materiau = fm.id_materiau)
Planning:
  Buffers: shared hit=140
Planning Time: 3.440 ms
Execution Time: 168.730 ms
```

### Rang 2 — observation 85.747 ms

```text
Append  (cost=3143.05..11746.34 rows=1413 width=68) (actual time=37.637..67.165 rows=9 loops=1)
  Buffers: shared hit=102925
  CTE correspondances
    ->  HashAggregate  (cost=1050.55..1150.58 rows=10003 width=8) (actual time=5.879..7.046 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=827
          ->  Append  (cost=0.00..1025.55 rows=10003 width=8) (actual time=0.075..3.935 rows=10000 loops=1)
                Buffers: shared hit=827
                ->  Seq Scan on fiche f_12  (cost=0.00..950.00 rows=10000 width=8) (actual time=0.075..3.253 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
                      Buffers: shared hit=825
                ->  Bitmap Heap Scan on chunk c_1  (cost=8.56..13.91 rows=2 width=8) (actual time=0.012..0.012 rows=0 loops=1)
                      Recheck Cond: (tsv @@ '''voile'''::tsquery)
                      Filter: (id_fiche IS NOT NULL)
                      Buffers: shared hit=2
                      ->  Bitmap Index Scan on idx_chunk_tsv  (cost=0.00..8.56 rows=2 width=0) (actual time=0.007..0.007 rows=0 loops=1)
                            Index Cond: (tsv @@ '''voile'''::tsquery)
                            Buffers: shared hit=2
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.006..0.006 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
  ->  HashAggregate  (cost=1992.46..1994.96 rows=200 width=68) (actual time=0.020..0.024 rows=0 loops=1)
        Group Key: tv.libelle
        Batches: 1  Memory Usage: 40kB
        ->  Nested Loop  (cost=464.25..1987.53 rows=986 width=32) (actual time=0.019..0.023 rows=0 loops=1)
              Join Filter: (f.id_fiche = cd.id_fiche)
              ->  Hash Join  (cost=463.96..1329.08 rows=986 width=56) (actual time=0.019..0.022 rows=0 loops=1)
                    Hash Cond: (f_1.id_type_voile = tv.id_type_voile)
                    ->  Hash Join  (cost=441.78..1304.28 rows=996 width=32) (never executed)
                          Hash Cond: (f_1.id_fiche = cd.id_fiche)
                          ->  Nested Loop  (cost=225.35..1074.73 rows=5000 width=24) (never executed)
                                ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                      Group Key: correspondances.id_fiche
                                      ->  CTE Scan on correspondances  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                                ->  Index Scan using fiche_pkey on fiche f_1  (cost=0.29..5.03 rows=1 width=32) (never executed)
                                      Index Cond: (id_fiche = correspondances.id_fiche)
                                      Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                          ->  Hash  (cost=191.54..191.54 rows=1991 width=8) (never executed)
                                ->  Bitmap Heap Scan on fiche_cotes cd  (cost=52.69..191.54 rows=1991 width=8) (never executed)
                                      Recheck Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                                      Filter: (jeu = 'finie'::text)
                                      ->  Bitmap Index Scan on idx_fiche_cotes_slu_m  (cost=0.00..52.19 rows=1991 width=0) (never executed)
                                            Index Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                    ->  Hash  (cost=16.12..16.12 rows=485 width=40) (actual time=0.002..0.002 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on type_voile tv  (cost=0.00..16.12 rows=485 width=40) (actual time=0.001..0.002 rows=0 loops=1)
                                Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
              ->  Index Only Scan using fiche_pkey on fiche f  (cost=0.29..0.66 rows=1 width=8) (never executed)
                    Index Cond: (id_fiche = f_1.id_fiche)
                    Heap Fetches: 0
  ->  HashAggregate  (cost=1988.74..1991.24 rows=200 width=68) (actual time=0.016..0.022 rows=0 loops=1)
        Group Key: c.nom
        Batches: 1  Memory Usage: 40kB
        ->  Nested Loop  (cost=460.52..1983.81 rows=986 width=32) (actual time=0.015..0.020 rows=0 loops=1)
              Join Filter: (f_2.id_fiche = cd_1.id_fiche)
              ->  Hash Join  (cost=460.24..1325.36 rows=986 width=56) (actual time=0.014..0.020 rows=0 loops=1)
                    Hash Cond: (f_3.id_client = c.id_client)
                    ->  Hash Join  (cost=441.78..1304.28 rows=996 width=32) (never executed)
                          Hash Cond: (f_3.id_fiche = cd_1.id_fiche)
                          ->  Nested Loop  (cost=225.35..1074.73 rows=5000 width=24) (never executed)
                                ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                      Group Key: correspondances_1.id_fiche
                                      ->  CTE Scan on correspondances correspondances_1  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                                ->  Index Scan using fiche_pkey on fiche f_3  (cost=0.29..5.03 rows=1 width=32) (never executed)
                                      Index Cond: (id_fiche = correspondances_1.id_fiche)
                                      Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                          ->  Hash  (cost=191.54..191.54 rows=1991 width=8) (never executed)
                                ->  Bitmap Heap Scan on fiche_cotes cd_1  (cost=52.69..191.54 rows=1991 width=8) (never executed)
                                      Recheck Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                                      Filter: (jeu = 'finie'::text)
                                      ->  Bitmap Index Scan on idx_fiche_cotes_slu_m  (cost=0.00..52.19 rows=1991 width=0) (never executed)
                                            Index Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                    ->  Hash  (cost=14.25..14.25 rows=337 width=40) (actual time=0.001..0.005 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on client c  (cost=0.00..14.25 rows=337 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                                Filter: ((nom IS NOT NULL) AND (nom <> ''::text))
              ->  Index Only Scan using fiche_pkey on fiche f_2  (cost=0.29..0.66 rows=1 width=8) (never executed)
                    Index Cond: (id_fiche = f_3.id_fiche)
                    Heap Fetches: 0
  ->  HashAggregate  (cost=2004.75..2008.25 rows=200 width=68) (actual time=0.014..0.016 rows=0 loops=1)
        Group Key: ((b.nom || ' '::text) || COALESCE(b.taille, ''::text))
        Batches: 1  Memory Usage: 40kB
        ->  Nested Loop  (cost=472.29..1999.83 rows=985 width=32) (actual time=0.013..0.015 rows=0 loops=1)
              Join Filter: (f_4.id_fiche = cd_2.id_fiche)
              ->  Hash Join  (cost=472.00..1337.12 rows=985 width=88) (actual time=0.013..0.014 rows=0 loops=1)
                    Hash Cond: (f_5.id_bateau = b.id_bateau)
                    ->  Hash Join  (cost=441.78..1304.28 rows=996 width=32) (never executed)
                          Hash Cond: (f_5.id_fiche = cd_2.id_fiche)
                          ->  Nested Loop  (cost=225.35..1074.73 rows=5000 width=24) (never executed)
                                ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                      Group Key: correspondances_2.id_fiche
                                      ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                                ->  Index Scan using fiche_pkey on fiche f_5  (cost=0.29..5.03 rows=1 width=32) (never executed)
                                      Index Cond: (id_fiche = correspondances_2.id_fiche)
                                      Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                          ->  Hash  (cost=191.54..191.54 rows=1991 width=8) (never executed)
                                ->  Bitmap Heap Scan on fiche_cotes cd_2  (cost=52.69..191.54 rows=1991 width=8) (never executed)
                                      Recheck Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                                      Filter: (jeu = 'finie'::text)
                                      ->  Bitmap Index Scan on idx_fiche_cotes_slu_m  (cost=0.00..52.19 rows=1991 width=0) (never executed)
                                            Index Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                    ->  Hash  (cost=23.05..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on bateau b  (cost=0.00..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                                Filter: ((((nom || ' '::text) || COALESCE(taille, ''::text)) IS NOT NULL) AND (((nom || ' '::text) || COALESCE(taille, ''::text)) <> ''::text))
              ->  Index Only Scan using fiche_pkey on fiche f_4  (cost=0.29..0.66 rows=1 width=8) (never executed)
                    Index Cond: (id_fiche = f_5.id_fiche)
                    Heap Fetches: 0
  ->  HashAggregate  (cost=1974.81..1974.83 rows=2 width=45) (actual time=37.587..37.589 rows=2 loops=1)
        Group Key: f_7.gamme
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=51876
        ->  Nested Loop  (cost=442.06..1969.83 rows=996 width=9) (actual time=11.795..37.189 rows=2020 loops=1)
              Join Filter: (f_6.id_fiche = cd_3.id_fiche)
              Buffers: shared hit=51876
              ->  Hash Join  (cost=441.78..1304.70 rows=996 width=33) (actual time=11.789..34.048 rows=2020 loops=1)
                    Hash Cond: (f_7.id_fiche = cd_3.id_fiche)
                    Buffers: shared hit=44488
                    ->  Nested Loop  (cost=225.35..1075.15 rows=5000 width=25) (actual time=11.080..31.977 rows=10000 loops=1)
                          Buffers: shared hit=44435
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=11.066..12.257 rows=10000 loops=1)
                                Group Key: correspondances_3.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                Buffers: shared hit=827
                                ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.06 rows=10003 width=8) (actual time=5.880..8.854 rows=10000 loops=1)
                                      Buffers: shared hit=827
                          ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..5.03 rows=1 width=41) (actual time=0.001..0.001 rows=1 loops=10000)
                                Index Cond: (id_fiche = correspondances_3.id_fiche)
                                Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text))
                                Buffers: shared hit=36804
                    ->  Hash  (cost=191.54..191.54 rows=1991 width=8) (actual time=0.692..0.693 rows=2020 loops=1)
                          Buckets: 2048  Batches: 1  Memory Usage: 95kB
                          Buffers: shared hit=53
                          ->  Bitmap Heap Scan on fiche_cotes cd_3  (cost=52.69..191.54 rows=1991 width=8) (actual time=0.172..0.455 rows=2020 loops=1)
                                Recheck Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                                Filter: (jeu = 'finie'::text)
                                Heap Blocks: exact=42
                                Buffers: shared hit=53
                                ->  Bitmap Index Scan on idx_fiche_cotes_slu_m  (cost=0.00..52.19 rows=1991 width=0) (actual time=0.163..0.163 rows=2020 loops=1)
                                      Index Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                                      Buffers: shared hit=11
              ->  Index Only Scan using fiche_pkey on fiche f_6  (cost=0.29..0.66 rows=1 width=8) (actual time=0.001..0.001 rows=1 loops=2020)
                    Index Cond: (id_fiche = f_7.id_fiche)
                    Heap Fetches: 3348
                    Buffers: shared hit=7388
  ->  HashAggregate  (cost=1979.37..1992.60 rows=756 width=68) (actual time=29.491..29.495 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=51049
        ->  Nested Loop  (cost=442.06..1974.39 rows=996 width=32) (actual time=3.209..29.092 rows=2020 loops=1)
              Join Filter: (f_8.id_fiche = cd_4.id_fiche)
              Buffers: shared hit=51049
              ->  Hash Join  (cost=441.78..1304.28 rows=996 width=28) (actual time=3.198..25.228 rows=2020 loops=1)
                    Hash Cond: (f_9.id_fiche = cd_4.id_fiche)
                    Buffers: shared hit=43661
                    ->  Nested Loop  (cost=225.35..1074.73 rows=5000 width=20) (actual time=2.545..23.247 rows=10000 loops=1)
                          Buffers: shared hit=43608
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.537..3.752 rows=10000 loops=1)
                                Group Key: correspondances_4.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.546 rows=10000 loops=1)
                          ->  Index Scan using fiche_pkey on fiche f_9  (cost=0.29..5.03 rows=1 width=36) (actual time=0.001..0.001 rows=1 loops=10000)
                                Index Cond: (id_fiche = correspondances_4.id_fiche)
                                Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])))
                                Buffers: shared hit=36804
                    ->  Hash  (cost=191.54..191.54 rows=1991 width=8) (actual time=0.632..0.632 rows=2020 loops=1)
                          Buckets: 2048  Batches: 1  Memory Usage: 95kB
                          Buffers: shared hit=53
                          ->  Bitmap Heap Scan on fiche_cotes cd_4  (cost=52.69..191.54 rows=1991 width=8) (actual time=0.162..0.429 rows=2020 loops=1)
                                Recheck Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                                Filter: (jeu = 'finie'::text)
                                Heap Blocks: exact=42
                                Buffers: shared hit=53
                                ->  Bitmap Index Scan on idx_fiche_cotes_slu_m  (cost=0.00..52.19 rows=1991 width=0) (actual time=0.151..0.151 rows=2020 loops=1)
                                      Index Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                                      Buffers: shared hit=11
              ->  Index Only Scan using fiche_pkey on fiche f_8  (cost=0.29..0.66 rows=1 width=8) (actual time=0.001..0.001 rows=1 loops=2020)
                    Index Cond: (id_fiche = f_9.id_fiche)
                    Heap Fetches: 3348
                    Buffers: shared hit=7388
  ->  GroupAggregate  (cost=625.70..626.80 rows=55 width=68) (actual time=0.012..0.014 rows=0 loops=1)
        Group Key: m.nom
        ->  Sort  (cost=625.70..625.83 rows=55 width=40) (actual time=0.012..0.014 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              ->  Nested Loop  (cost=442.14..624.11 rows=55 width=40) (actual time=0.006..0.008 rows=0 loops=1)
                    ->  Nested Loop  (cost=441.99..610.49 rows=55 width=16) (actual time=0.006..0.007 rows=0 loops=1)
                          Join Filter: (f_11.id_fiche = cd_5.id_fiche)
                          ->  Nested Loop  (cost=441.70..573.63 rows=55 width=40) (actual time=0.005..0.007 rows=0 loops=1)
                                ->  Hash Join  (cost=441.42..459.53 rows=55 width=32) (actual time=0.005..0.006 rows=0 loops=1)
                                      Hash Cond: (fm.id_fiche = cd_5.id_fiche)
                                      ->  Seq Scan on fiche_materiau fm  (cost=0.00..15.50 rows=550 width=16) (actual time=0.004..0.004 rows=0 loops=1)
                                      ->  Hash  (cost=428.97..428.97 rows=996 width=16) (never executed)
                                            ->  Hash Join  (cost=282.26..428.97 rows=996 width=16) (never executed)
                                                  Hash Cond: (cd_5.id_fiche = correspondances_5.id_fiche)
                                                  ->  Bitmap Heap Scan on fiche_cotes cd_5  (cost=52.69..191.54 rows=1991 width=8) (never executed)
                                                        Recheck Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                                                        Filter: (jeu = 'finie'::text)
                                                        ->  Bitmap Index Scan on idx_fiche_cotes_slu_m  (cost=0.00..52.19 rows=1991 width=0) (never executed)
                                                              Index Cond: ((slu_m >= 5.5) AND (slu_m <= 6.5))
                                                  ->  Hash  (cost=227.07..227.07 rows=200 width=8) (never executed)
                                                        ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                                              Group Key: correspondances_5.id_fiche
                                                              ->  CTE Scan on correspondances correspondances_5  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                                ->  Index Only Scan using fiche_pkey on fiche f_10  (cost=0.29..2.07 rows=1 width=8) (never executed)
                                      Index Cond: (id_fiche = cd_5.id_fiche)
                                      Heap Fetches: 0
                          ->  Index Scan using fiche_pkey on fiche f_11  (cost=0.29..0.66 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = f_10.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Index Scan using materiau_pkey on materiau m  (cost=0.15..0.25 rows=1 width=40) (never executed)
                          Index Cond: (id_materiau = fm.id_materiau)
Planning:
  Buffers: shared hit=309
Planning Time: 6.253 ms
Execution Time: 68.061 ms
```

### Rang 3 — observation 78.36 ms

```text
Append  (cost=3361.80..13556.32 rows=1633 width=68) (actual time=38.174..68.669 rows=9 loops=1)
  Buffers: shared hit=89691
  CTE correspondances
    ->  HashAggregate  (cost=1052.02..1152.04 rows=10002 width=8) (actual time=8.523..9.448 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=825
          ->  Append  (cost=0.00..1027.01 rows=10002 width=8) (actual time=0.063..6.154 rows=10000 loops=1)
                Buffers: shared hit=825
                ->  Seq Scan on fiche f_12  (cost=0.00..950.00 rows=10000 width=8) (actual time=0.062..5.373 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
                      Buffers: shared hit=825
                ->  Seq Scan on chunk c_1  (cost=0.00..15.38 rows=1 width=8) (actual time=0.004..0.004 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (tsv @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.001..0.001 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
  ->  HashAggregate  (cost=2209.77..2212.27 rows=200 width=68) (actual time=0.007..0.009 rows=0 loops=1)
        Group Key: tv.libelle
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1159.40..2185.02 rows=4949 width=32) (actual time=0.006..0.008 rows=0 loops=1)
              Hash Cond: (f_1.id_type_voile = tv.id_type_voile)
              ->  Hash Join  (cost=1137.21..2149.71 rows=5000 width=8) (never executed)
                    Hash Cond: (f.id_fiche = f_1.id_fiche)
                    ->  Seq Scan on fiche f  (cost=0.00..925.00 rows=10000 width=8) (never executed)
                    ->  Hash  (cost=1074.71..1074.71 rows=5000 width=24) (never executed)
                          ->  Nested Loop  (cost=225.33..1074.71 rows=5000 width=24) (never executed)
                                ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                      Group Key: correspondances.id_fiche
                                      ->  CTE Scan on correspondances  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                                ->  Index Scan using fiche_pkey on fiche f_1  (cost=0.29..5.03 rows=1 width=32) (never executed)
                                      Index Cond: (id_fiche = correspondances.id_fiche)
                                      Filter: (statut = ANY ('{valide,a_valider}'::text[]))
              ->  Hash  (cost=16.12..16.12 rows=485 width=40) (actual time=0.003..0.003 rows=0 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 8kB
                    ->  Seq Scan on type_voile tv  (cost=0.00..16.12 rows=485 width=40) (actual time=0.002..0.002 rows=0 loops=1)
                          Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
  ->  HashAggregate  (cost=2206.08..2208.58 rows=200 width=68) (actual time=0.005..0.007 rows=0 loops=1)
        Group Key: c.nom
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1155.67..2181.30 rows=4956 width=32) (actual time=0.004..0.006 rows=0 loops=1)
              Hash Cond: (f_3.id_client = c.id_client)
              ->  Hash Join  (cost=1137.21..2149.71 rows=5000 width=8) (never executed)
                    Hash Cond: (f_2.id_fiche = f_3.id_fiche)
                    ->  Seq Scan on fiche f_2  (cost=0.00..925.00 rows=10000 width=8) (never executed)
                    ->  Hash  (cost=1074.71..1074.71 rows=5000 width=24) (never executed)
                          ->  Nested Loop  (cost=225.33..1074.71 rows=5000 width=24) (never executed)
                                ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                      Group Key: correspondances_1.id_fiche
                                      ->  CTE Scan on correspondances correspondances_1  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                                ->  Index Scan using fiche_pkey on fiche f_3  (cost=0.29..5.03 rows=1 width=32) (never executed)
                                      Index Cond: (id_fiche = correspondances_1.id_fiche)
                                      Filter: (statut = ANY ('{valide,a_valider}'::text[]))
              ->  Hash  (cost=14.25..14.25 rows=337 width=40) (actual time=0.001..0.002 rows=0 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 8kB
                    ->  Seq Scan on client c  (cost=0.00..14.25 rows=337 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                          Filter: ((nom IS NOT NULL) AND (nom <> ''::text))
  ->  HashAggregate  (cost=2242.54..2246.04 rows=200 width=68) (actual time=0.004..0.006 rows=0 loops=1)
        Group Key: ((b.nom || ' '::text) || COALESCE(b.taille, ''::text))
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1167.44..2217.80 rows=4948 width=32) (actual time=0.003..0.005 rows=0 loops=1)
              Hash Cond: (f_5.id_bateau = b.id_bateau)
              ->  Hash Join  (cost=1137.21..2149.71 rows=5000 width=8) (never executed)
                    Hash Cond: (f_4.id_fiche = f_5.id_fiche)
                    ->  Seq Scan on fiche f_4  (cost=0.00..925.00 rows=10000 width=8) (never executed)
                    ->  Hash  (cost=1074.71..1074.71 rows=5000 width=24) (never executed)
                          ->  Nested Loop  (cost=225.33..1074.71 rows=5000 width=24) (never executed)
                                ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                      Group Key: correspondances_2.id_fiche
                                      ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                                ->  Index Scan using fiche_pkey on fiche f_5  (cost=0.29..5.03 rows=1 width=32) (never executed)
                                      Index Cond: (id_fiche = correspondances_2.id_fiche)
                                      Filter: (statut = ANY ('{valide,a_valider}'::text[]))
              ->  Hash  (cost=23.05..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 8kB
                    ->  Seq Scan on bateau b  (cost=0.00..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                          Filter: ((((nom || ' '::text) || COALESCE(taille, ''::text)) IS NOT NULL) AND (((nom || ' '::text) || COALESCE(taille, ''::text)) <> ''::text))
  ->  HashAggregate  (cost=2175.13..2175.15 rows=2 width=45) (actual time=38.158..38.160 rows=2 loops=1)
        Group Key: f_7.gamme
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=45258
        ->  Hash Join  (cost=1137.63..2150.13 rows=5000 width=9) (actual time=34.067..36.729 rows=10000 loops=1)
              Hash Cond: (f_6.id_fiche = f_7.id_fiche)
              Buffers: shared hit=45258
              ->  Seq Scan on fiche f_6  (cost=0.00..925.00 rows=10000 width=8) (actual time=0.082..0.991 rows=10000 loops=1)
                    Buffers: shared hit=825
              ->  Hash  (cost=1075.13..1075.13 rows=5000 width=25) (actual time=33.977..33.978 rows=10000 loops=1)
                    Buckets: 16384 (originally 8192)  Batches: 1 (originally 1)  Memory Usage: 714kB
                    Buffers: shared hit=44433
                    ->  Nested Loop  (cost=225.33..1075.13 rows=5000 width=25) (actual time=12.640..32.366 rows=10000 loops=1)
                          Buffers: shared hit=44433
                          ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (actual time=12.629..13.660 rows=10000 loops=1)
                                Group Key: correspondances_3.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                Buffers: shared hit=825
                                ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.04 rows=10002 width=8) (actual time=8.524..10.949 rows=10000 loops=1)
                                      Buffers: shared hit=825
                          ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..5.03 rows=1 width=41) (actual time=0.001..0.001 rows=1 loops=10000)
                                Index Cond: (id_fiche = correspondances_3.id_fiche)
                                Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text))
                                Buffers: shared hit=36804
  ->  HashAggregate  (cost=2199.71..2212.94 rows=756 width=68) (actual time=30.453..30.456 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=44433
        ->  Hash Join  (cost=1137.21..2174.71 rows=5000 width=32) (actual time=23.489..28.973 rows=10000 loops=1)
              Hash Cond: (f_8.id_fiche = f_9.id_fiche)
              Buffers: shared hit=44433
              ->  Seq Scan on fiche f_8  (cost=0.00..925.00 rows=10000 width=8) (actual time=0.045..1.181 rows=10000 loops=1)
                    Buffers: shared hit=825
              ->  Hash  (cost=1074.71..1074.71 rows=5000 width=20) (actual time=23.431..23.432 rows=10000 loops=1)
                    Buckets: 16384 (originally 8192)  Batches: 1 (originally 1)  Memory Usage: 675kB
                    Buffers: shared hit=43608
                    ->  Nested Loop  (cost=225.33..1074.71 rows=5000 width=20) (actual time=2.301..21.811 rows=10000 loops=1)
                          Buffers: shared hit=43608
                          ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (actual time=2.294..3.333 rows=10000 loops=1)
                                Group Key: correspondances_4.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.04 rows=10002 width=8) (actual time=0.000..0.555 rows=10000 loops=1)
                          ->  Index Scan using fiche_pkey on fiche f_9  (cost=0.29..5.03 rows=1 width=36) (actual time=0.001..0.001 rows=1 loops=10000)
                                Index Cond: (id_fiche = correspondances_4.id_fiche)
                                Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])))
                                Buffers: shared hit=36804
  ->  GroupAggregate  (cost=1335.64..1341.14 rows=275 width=68) (actual time=0.021..0.023 rows=0 loops=1)
        Group Key: m.nom
        ->  Sort  (cost=1335.64..1336.32 rows=275 width=40) (actual time=0.020..0.022 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              ->  Hash Join  (cost=252.49..1324.49 rows=275 width=40) (actual time=0.010..0.012 rows=0 loops=1)
                    Hash Cond: (fm.id_materiau = m.id_materiau)
                    ->  Nested Loop  (cost=230.11..1301.40 rows=275 width=16) (never executed)
                          ->  Nested Loop  (cost=229.83..1121.19 rows=275 width=32) (never executed)
                                Join Filter: (f_11.id_fiche = correspondances_5.id_fiche)
                                ->  Hash Join  (cost=229.54..252.61 rows=275 width=24) (never executed)
                                      Hash Cond: (fm.id_fiche = correspondances_5.id_fiche)
                                      ->  Seq Scan on fiche_materiau fm  (cost=0.00..15.50 rows=550 width=16) (never executed)
                                      ->  Hash  (cost=227.04..227.04 rows=200 width=8) (never executed)
                                            ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                                  Group Key: correspondances_5.id_fiche
                                                  ->  CTE Scan on correspondances correspondances_5  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                                ->  Index Scan using fiche_pkey on fiche f_11  (cost=0.29..3.72 rows=1 width=32) (never executed)
                                      Index Cond: (id_fiche = fm.id_fiche)
                                      Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                          ->  Index Only Scan using fiche_pkey on fiche f_10  (cost=0.29..0.66 rows=1 width=8) (never executed)
                                Index Cond: (id_fiche = f_11.id_fiche)
                                Heap Fetches: 0
                    ->  Hash  (cost=15.50..15.50 rows=550 width=40) (actual time=0.005..0.005 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on materiau m  (cost=0.00..15.50 rows=550 width=40) (actual time=0.004..0.004 rows=0 loops=1)
Planning:
  Buffers: shared hit=135
Planning Time: 5.054 ms
Execution Time: 69.002 ms
```
