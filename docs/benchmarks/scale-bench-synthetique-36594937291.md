# Banc d’échelle SYNTHÉTIQUE — run 36594937291

> Données synthétiques uniquement. Ne constitue pas une mesure réelle de VPS01, R2 ni du poste atelier.

- Run: https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36594937291
- Commit exécuté: `984e2dbb3379aa0080d41bdca9f2d9dae673c992`
- Base jetable GitHub Actions PostgreSQL; 10 000 fiches synthétiques; 50 mesures par scénario.
- Durée du job: 1 min 5 s; budget maximal demandé: moins de 20 min.
- Cible p95 de ce micro-benchmark: 250 ms indicative; distincte du critère produit réel.
- JSON: [`scale-bench-synthetique-36594937291.json`](scale-bench-synthetique-36594937291.json)

## Mesures (ms)

| Scénario | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| mot_simple | 42.59 | 48.11 | 48.26 | 48.26 |
| multi_mots | 52.83 | 66.45 | 86.05 | 86.05 |
| code | 6.7 | 6.83 | 15.48 | 15.48 |
| facette_dimension | 50.71 | 62.05 | 129.36 | 129.36 |
| filtres | 53.44 | 80.11 | 150.62 | 150.62 |
| suggestions | 4.28 | 4.58 | 4.67 | 4.67 |

## EXPLAIN ANALYZE / BUFFERS — trois requêtes SELECT observées les plus lentes

### Rang 1 — observation 55.008 ms

```text
Append  (cost=3048.46..12515.08 rows=1633 width=68) (actual time=19.628..37.022 rows=9 loops=1)
  Buffers: shared hit=89259
  CTE correspondances
    ->  HashAggregate  (cost=908.01..1008.03 rows=10002 width=8) (actual time=2.762..3.414 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=681
          ->  Append  (cost=0.00..883.01 rows=10002 width=8) (actual time=0.027..1.923 rows=10000 loops=1)
                Buffers: shared hit=681
                ->  Seq Scan on fiche f_12  (cost=0.00..806.00 rows=10000 width=8) (actual time=0.027..1.435 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
                      Buffers: shared hit=681
                ->  Seq Scan on chunk c_1  (cost=0.00..15.38 rows=1 width=8) (actual time=0.002..0.002 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (tsv @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.002..0.002 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
  ->  HashAggregate  (cost=2040.43..2042.93 rows=200 width=68) (actual time=0.004..0.007 rows=0 loops=1)
        Group Key: tv.libelle
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1153.52..2015.68 rows=4949 width=32) (actual time=0.003..0.005 rows=0 loops=1)
              Hash Cond: (f_1.id_fiche = f.id_fiche)
              ->  Hash Join  (cost=247.52..1096.69 rows=4949 width=48) (actual time=0.003..0.005 rows=0 loops=1)
                    Hash Cond: (f_1.id_type_voile = tv.id_type_voile)
                    ->  Nested Loop  (cost=225.33..1061.38 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                Group Key: correspondances.id_fiche
                                ->  CTE Scan on correspondances  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_1  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=16.12..16.12 rows=485 width=40) (actual time=0.002..0.002 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on type_voile tv  (cost=0.00..16.12 rows=485 width=40) (actual time=0.001..0.002 rows=0 loops=1)
                                Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2036.76..2039.26 rows=200 width=68) (actual time=0.002..0.004 rows=0 loops=1)
        Group Key: c.nom
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1149.79..2011.98 rows=4956 width=32) (actual time=0.002..0.004 rows=0 loops=1)
              Hash Cond: (f_3.id_fiche = f_2.id_fiche)
              ->  Hash Join  (cost=243.79..1092.97 rows=4956 width=48) (actual time=0.002..0.003 rows=0 loops=1)
                    Hash Cond: (f_3.id_client = c.id_client)
                    ->  Nested Loop  (cost=225.33..1061.38 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                Group Key: correspondances_1.id_fiche
                                ->  CTE Scan on correspondances correspondances_1  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_3  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances_1.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=14.25..14.25 rows=337 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on client c  (cost=0.00..14.25 rows=337 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                                Filter: ((nom IS NOT NULL) AND (nom <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f_2  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2073.20..2076.70 rows=200 width=68) (actual time=0.002..0.003 rows=0 loops=1)
        Group Key: ((b.nom || ' '::text) || COALESCE(b.taille, ''::text))
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1161.56..2048.46 rows=4948 width=32) (actual time=0.002..0.003 rows=0 loops=1)
              Hash Cond: (f_5.id_fiche = f_4.id_fiche)
              ->  Hash Join  (cost=255.56..1104.73 rows=4948 width=80) (actual time=0.002..0.002 rows=0 loops=1)
                    Hash Cond: (f_5.id_bateau = b.id_bateau)
                    ->  Nested Loop  (cost=225.33..1061.38 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                Group Key: correspondances_2.id_fiche
                                ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_5  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances_2.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=23.05..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on bateau b  (cost=0.00..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                                Filter: ((((nom || ' '::text) || COALESCE(taille, ''::text)) IS NOT NULL) AND (((nom || ' '::text) || COALESCE(taille, ''::text)) <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f_4  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2005.92..2005.94 rows=2 width=45) (actual time=19.619..19.620 rows=2 loops=1)
        Group Key: f_7.gamme
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=44970
        ->  Hash Join  (cost=1131.33..1980.92 rows=5000 width=9) (actual time=6.460..18.652 rows=10000 loops=1)
              Hash Cond: (f_7.id_fiche = f_6.id_fiche)
              Buffers: shared hit=44970
              ->  Nested Loop  (cost=225.33..1061.79 rows=5000 width=25) (actual time=5.290..16.449 rows=10000 loops=1)
                    Buffers: shared hit=44289
                    ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (actual time=5.282..5.858 rows=10000 loops=1)
                          Group Key: correspondances_3.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          Buffers: shared hit=681
                          ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.04 rows=10002 width=8) (actual time=2.763..4.342 rows=10000 loops=1)
                                Buffers: shared hit=681
                    ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..4.95 rows=1 width=41) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=1.166..1.166 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_6  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.023..0.514 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  HashAggregate  (cost=2030.50..2043.73 rows=756 width=68) (actual time=17.360..17.363 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=44289
        ->  Hash Join  (cost=1131.33..2005.50 rows=5000 width=32) (actual time=2.668..16.440 rows=10000 loops=1)
              Hash Cond: (f_9.id_fiche = f_8.id_fiche)
              Buffers: shared hit=44289
              ->  Nested Loop  (cost=225.33..1061.38 rows=5000 width=20) (actual time=1.487..12.628 rows=10000 loops=1)
                    Buffers: shared hit=43608
                    ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (actual time=1.484..2.062 rows=10000 loops=1)
                          Group Key: correspondances_4.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.04 rows=10002 width=8) (actual time=0.000..0.333 rows=10000 loops=1)
                    ->  Index Scan using fiche_pkey on fiche f_9  (cost=0.29..4.95 rows=1 width=36) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_4.id_fiche)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=1.172..1.172 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_8  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.022..0.519 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  GroupAggregate  (cost=1284.82..1290.32 rows=275 width=68) (actual time=0.019..0.020 rows=0 loops=1)
        Group Key: m.nom
        ->  Sort  (cost=1284.82..1285.51 rows=275 width=40) (actual time=0.019..0.020 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              ->  Hash Join  (cost=252.49..1273.68 rows=275 width=40) (actual time=0.013..0.013 rows=0 loops=1)
                    Hash Cond: (fm.id_materiau = m.id_materiau)
                    ->  Nested Loop  (cost=230.11..1250.58 rows=275 width=16) (never executed)
                          ->  Nested Loop  (cost=229.83..1086.21 rows=275 width=32) (never executed)
                                Join Filter: (f_11.id_fiche = correspondances_5.id_fiche)
                                ->  Hash Join  (cost=229.54..252.61 rows=275 width=24) (never executed)
                                      Hash Cond: (fm.id_fiche = correspondances_5.id_fiche)
                                      ->  Seq Scan on fiche_materiau fm  (cost=0.00..15.50 rows=550 width=16) (never executed)
                                      ->  Hash  (cost=227.04..227.04 rows=200 width=8) (never executed)
                                            ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                                  Group Key: correspondances_5.id_fiche
                                                  ->  CTE Scan on correspondances correspondances_5  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                                ->  Index Scan using fiche_pkey on fiche f_11  (cost=0.29..3.57 rows=1 width=32) (never executed)
                                      Index Cond: (id_fiche = fm.id_fiche)
                                      Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                          ->  Index Only Scan using fiche_pkey on fiche f_10  (cost=0.29..0.60 rows=1 width=8) (never executed)
                                Index Cond: (id_fiche = f_11.id_fiche)
                                Heap Fetches: 0
                    ->  Hash  (cost=15.50..15.50 rows=550 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on materiau m  (cost=0.00..15.50 rows=550 width=40) (actual time=0.001..0.001 rows=0 loops=1)
Planning:
  Buffers: shared hit=135
Planning Time: 1.845 ms
Execution Time: 37.169 ms
```

### Rang 2 — observation 52.286 ms

```text
Append  (cost=2228.24..9162.03 rows=795 width=68) (actual time=37.033..50.012 rows=9 loops=1)
  Buffers: shared hit=75166
  CTE correspondances
    ->  HashAggregate  (cost=906.55..1006.58 rows=10003 width=8) (actual time=2.531..3.051 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=683
          ->  Append  (cost=0.00..881.55 rows=10003 width=8) (actual time=0.019..1.720 rows=10000 loops=1)
                Buffers: shared hit=683
                ->  Seq Scan on fiche f_12  (cost=0.00..806.00 rows=10000 width=8) (actual time=0.019..1.238 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
                      Buffers: shared hit=681
                ->  Bitmap Heap Scan on chunk c_1  (cost=8.56..13.91 rows=2 width=8) (actual time=0.004..0.004 rows=0 loops=1)
                      Recheck Cond: (tsv @@ '''voile'''::tsquery)
                      Filter: (id_fiche IS NOT NULL)
                      Buffers: shared hit=2
                      ->  Bitmap Index Scan on idx_chunk_tsv  (cost=0.00..8.56 rows=2 width=0) (actual time=0.003..0.003 rows=0 loops=1)
                            Index Cond: (tsv @@ '''voile'''::tsquery)
                            Buffers: shared hit=2
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.001..0.001 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
  ->  GroupAggregate  (cost=1221.66..1221.90 rows=12 width=68) (actual time=7.969..7.971 rows=0 loops=1)
        Group Key: tv.libelle
        Buffers: shared hit=1374
        ->  Sort  (cost=1221.66..1221.69 rows=12 width=32) (actual time=7.968..7.971 rows=0 loops=1)
              Sort Key: tv.libelle
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=1374
              ->  Nested Loop  (cost=307.80..1221.44 rows=12 width=32) (actual time=7.966..7.969 rows=0 loops=1)
                    Join Filter: (f.id_fiche = f_1.id_fiche)
                    Buffers: shared hit=1374
                    ->  Nested Loop  (cost=307.52..1161.98 rows=12 width=48) (actual time=7.966..7.968 rows=0 loops=1)
                          Buffers: shared hit=1374
                          ->  Hash Join  (cost=307.36..1138.56 rows=12 width=24) (actual time=6.150..7.630 rows=2858 loops=1)
                                Hash Cond: (f_1.id_fiche = correspondances.id_fiche)
                                Buffers: shared hit=1374
                                ->  Bitmap Heap Scan on fiche f_1  (cost=77.79..908.79 rows=25 width=32) (actual time=0.242..1.442 rows=2858 loops=1)
                                      Recheck Cond: (gamme = 'Régate'::text)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 2142
                                      Heap Blocks: exact=681
                                      Buffers: shared hit=691
                                      ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.127..0.127 rows=10000 loops=1)
                                            Index Cond: (gamme = 'Régate'::text)
                                            Buffers: shared hit=10
                                ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=5.905..5.906 rows=10000 loops=1)
                                      Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                      Buffers: shared hit=683
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=4.863..5.333 rows=10000 loops=1)
                                            Group Key: correspondances.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            Buffers: shared hit=683
                                            ->  CTE Scan on correspondances  (cost=0.00..200.06 rows=10003 width=8) (actual time=2.532..3.927 rows=10000 loops=1)
                                                  Buffers: shared hit=683
                          ->  Memoize  (cost=0.16..1.94 rows=1 width=40) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_1.id_type_voile
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using type_voile_pkey on type_voile tv  (cost=0.15..1.93 rows=1 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                                      Index Cond: (id_type_voile = f_1.id_type_voile)
                                      Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f  (cost=0.29..4.94 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1221.66..1221.90 rows=12 width=68) (actual time=4.317..4.318 rows=0 loops=1)
        Group Key: c.nom
        Buffers: shared hit=691
        ->  Sort  (cost=1221.66..1221.69 rows=12 width=32) (actual time=4.317..4.318 rows=0 loops=1)
              Sort Key: c.nom
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=691
              ->  Nested Loop  (cost=307.80..1221.44 rows=12 width=32) (actual time=4.316..4.317 rows=0 loops=1)
                    Join Filter: (f_2.id_fiche = f_3.id_fiche)
                    Buffers: shared hit=691
                    ->  Nested Loop  (cost=307.52..1161.98 rows=12 width=48) (actual time=4.316..4.317 rows=0 loops=1)
                          Buffers: shared hit=691
                          ->  Hash Join  (cost=307.36..1138.56 rows=12 width=24) (actual time=2.492..3.969 rows=2858 loops=1)
                                Hash Cond: (f_3.id_fiche = correspondances_1.id_fiche)
                                Buffers: shared hit=691
                                ->  Bitmap Heap Scan on fiche f_3  (cost=77.79..908.79 rows=25 width=32) (actual time=0.220..1.419 rows=2858 loops=1)
                                      Recheck Cond: (gamme = 'Régate'::text)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 2142
                                      Heap Blocks: exact=681
                                      Buffers: shared hit=691
                                      ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.113..0.114 rows=10000 loops=1)
                                            Index Cond: (gamme = 'Régate'::text)
                                            Buffers: shared hit=10
                                ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=2.270..2.270 rows=10000 loops=1)
                                      Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=1.214..1.692 rows=10000 loops=1)
                                            Group Key: correspondances_1.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            ->  CTE Scan on correspondances correspondances_1  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.331 rows=10000 loops=1)
                          ->  Memoize  (cost=0.16..1.94 rows=1 width=40) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_3.id_client
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using client_pkey on client c  (cost=0.15..1.93 rows=1 width=40) (actual time=0.000..0.001 rows=0 loops=1)
                                      Index Cond: (id_client = f_3.id_client)
                                      Filter: ((nom IS NOT NULL) AND (nom <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f_2  (cost=0.29..4.94 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances_1.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1221.87..1222.17 rows=12 width=68) (actual time=4.664..4.665 rows=0 loops=1)
        Group Key: (((b.nom || ' '::text) || COALESCE(b.taille, ''::text)))
        Buffers: shared hit=691
        ->  Sort  (cost=1221.87..1221.90 rows=12 width=32) (actual time=4.664..4.665 rows=0 loops=1)
              Sort Key: (((b.nom || ' '::text) || COALESCE(b.taille, ''::text)))
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=691
              ->  Nested Loop  (cost=307.80..1221.65 rows=12 width=32) (actual time=4.663..4.664 rows=0 loops=1)
                    Join Filter: (f_4.id_fiche = f_5.id_fiche)
                    Buffers: shared hit=691
                    ->  Nested Loop  (cost=307.52..1162.13 rows=12 width=80) (actual time=4.663..4.664 rows=0 loops=1)
                          Buffers: shared hit=691
                          ->  Hash Join  (cost=307.36..1138.56 rows=12 width=24) (actual time=2.859..4.318 rows=2858 loops=1)
                                Hash Cond: (f_5.id_fiche = correspondances_2.id_fiche)
                                Buffers: shared hit=691
                                ->  Bitmap Heap Scan on fiche f_5  (cost=77.79..908.79 rows=25 width=32) (actual time=0.223..1.396 rows=2858 loops=1)
                                      Recheck Cond: (gamme = 'Régate'::text)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 2142
                                      Heap Blocks: exact=681
                                      Buffers: shared hit=691
                                      ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.125..0.125 rows=10000 loops=1)
                                            Index Cond: (gamme = 'Régate'::text)
                                            Buffers: shared hit=10
                                ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=2.631..2.631 rows=10000 loops=1)
                                      Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=1.485..1.961 rows=10000 loops=1)
                                            Group Key: correspondances_2.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.342 rows=10000 loops=1)
                          ->  Memoize  (cost=0.16..1.95 rows=1 width=72) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_5.id_bateau
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using bateau_pkey on bateau b  (cost=0.15..1.94 rows=1 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                                      Index Cond: (id_bateau = f_5.id_bateau)
                                      Filter: ((((nom || ' '::text) || COALESCE(taille, ''::text)) IS NOT NULL) AND (((nom || ' '::text) || COALESCE(taille, ''::text)) <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f_4  (cost=0.29..4.94 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances_2.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1345.02..1345.24 rows=2 width=45) (actual time=20.082..20.302 rows=2 loops=1)
        Group Key: f_7.gamme
        Buffers: shared hit=64641
        ->  Sort  (cost=1345.02..1345.09 rows=25 width=9) (actual time=19.877..20.032 rows=5715 loops=1)
              Sort Key: f_7.gamme
              Sort Method: quicksort  Memory: 304kB
              Buffers: shared hit=64641
              ->  Nested Loop  (cost=225.64..1344.44 rows=25 width=9) (actual time=1.493..19.460 rows=5715 loops=1)
                    Join Filter: (f_6.id_fiche = f_7.id_fiche)
                    Buffers: shared hit=64641
                    ->  Nested Loop  (cost=225.35..1220.57 rows=25 width=25) (actual time=1.489..14.309 rows=5715 loops=1)
                          Buffers: shared hit=43608
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=1.484..2.096 rows=10000 loops=1)
                                Group Key: correspondances_3.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.381 rows=10000 loops=1)
                          ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..4.96 rows=1 width=41) (actual time=0.001..0.001 rows=1 loops=10000)
                                Index Cond: (id_fiche = correspondances_3.id_fiche)
                                Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                Rows Removed by Filter: 0
                                Buffers: shared hit=39720
                    ->  Index Only Scan using fiche_pkey on fiche f_6  (cost=0.29..4.94 rows=1 width=8) (actual time=0.001..0.001 rows=1 loops=5715)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Heap Fetches: 9603
                          Buffers: shared hit=21033
  ->  HashAggregate  (cost=1981.69..1994.92 rows=756 width=68) (actual time=7.522..7.524 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=1362
        ->  Hash Join  (cost=1123.07..1969.19 rows=2500 width=32) (actual time=4.165..7.121 rows=5000 loops=1)
              Hash Cond: (f_8.id_fiche = f_9.id_fiche)
              Buffers: shared hit=1362
              ->  Hash Join  (cost=229.57..1050.07 rows=5000 width=16) (actual time=2.683..4.207 rows=10000 loops=1)
                    Hash Cond: (f_8.id_fiche = correspondances_4.id_fiche)
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_8  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.023..0.539 rows=10000 loops=1)
                          Buffers: shared hit=681
                    ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=2.656..2.657 rows=10000 loops=1)
                          Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=1.447..1.928 rows=10000 loops=1)
                                Group Key: correspondances_4.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.001..0.333 rows=10000 loops=1)
              ->  Hash  (cost=831.00..831.00 rows=5000 width=36) (actual time=1.453..1.453 rows=5000 loops=1)
                    Buckets: 8192  Batches: 1  Memory Usage: 299kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_9  (cost=0.00..831.00 rows=5000 width=36) (actual time=0.023..1.043 rows=5000 loops=1)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme = 'Régate'::text))
                          Rows Removed by Filter: 5000
                          Buffers: shared hit=681
  ->  GroupAggregate  (cost=1145.33..1145.35 rows=1 width=68) (actual time=5.225..5.227 rows=0 loops=1)
        Group Key: m.nom
        Buffers: shared hit=6407
        ->  Sort  (cost=1145.33..1145.33 rows=1 width=40) (actual time=5.225..5.226 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=6407
              ->  Nested Loop  (cost=307.94..1145.32 rows=1 width=40) (actual time=5.220..5.221 rows=0 loops=1)
                    Buffers: shared hit=6407
                    ->  Nested Loop  (cost=307.79..1145.07 rows=1 width=16) (actual time=5.219..5.220 rows=0 loops=1)
                          Join Filter: (f_10.id_fiche = f_11.id_fiche)
                          Buffers: shared hit=6407
                          ->  Nested Loop  (cost=307.51..1141.49 rows=1 width=32) (actual time=5.219..5.220 rows=0 loops=1)
                                Join Filter: (fm.id_fiche = f_11.id_fiche)
                                Buffers: shared hit=6407
                                ->  Hash Join  (cost=307.36..1138.56 rows=12 width=16) (actual time=2.876..4.415 rows=2858 loops=1)
                                      Hash Cond: (f_11.id_fiche = correspondances_5.id_fiche)
                                      Buffers: shared hit=691
                                      ->  Bitmap Heap Scan on fiche f_11  (cost=77.79..908.79 rows=25 width=32) (actual time=0.242..1.485 rows=2858 loops=1)
                                            Recheck Cond: (gamme = 'Régate'::text)
                                            Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                            Rows Removed by Filter: 2142
                                            Heap Blocks: exact=681
                                            Buffers: shared hit=691
                                            ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.138..0.138 rows=10000 loops=1)
                                                  Index Cond: (gamme = 'Régate'::text)
                                                  Buffers: shared hit=10
                                      ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=2.627..2.627 rows=10000 loops=1)
                                            Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                            ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=1.462..1.941 rows=10000 loops=1)
                                                  Group Key: correspondances_5.id_fiche
                                                  Batches: 1  Memory Usage: 929kB
                                                  ->  CTE Scan on correspondances correspondances_5  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.333 rows=10000 loops=1)
                                ->  Index Scan using fiche_materiau_id_fiche_role_niveau_key on fiche_materiau fm  (cost=0.15..0.21 rows=3 width=16) (actual time=0.000..0.000 rows=0 loops=2858)
                                      Index Cond: (id_fiche = correspondances_5.id_fiche)
                                      Buffers: shared hit=5716
                          ->  Index Only Scan using fiche_pkey on fiche f_10  (cost=0.29..3.57 rows=1 width=8) (never executed)
                                Index Cond: (id_fiche = fm.id_fiche)
                                Heap Fetches: 0
                    ->  Index Scan using materiau_pkey on materiau m  (cost=0.15..0.25 rows=1 width=40) (never executed)
                          Index Cond: (id_materiau = fm.id_materiau)
Planning:
  Buffers: shared hit=140
Planning Time: 2.351 ms
Execution Time: 50.161 ms
```

### Rang 3 — observation 35.281 ms

```text
Append  (cost=3047.03..12513.76 rows=1633 width=68) (actual time=22.167..39.661 rows=9 loops=1)
  Buffers: shared hit=89261
  CTE correspondances
    ->  HashAggregate  (cost=906.55..1006.58 rows=10003 width=8) (actual time=4.272..4.941 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=683
          ->  Append  (cost=0.00..881.55 rows=10003 width=8) (actual time=0.046..2.863 rows=10000 loops=1)
                Buffers: shared hit=683
                ->  Seq Scan on fiche f_12  (cost=0.00..806.00 rows=10000 width=8) (actual time=0.045..2.307 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
                      Buffers: shared hit=681
                ->  Bitmap Heap Scan on chunk c_1  (cost=8.56..13.91 rows=2 width=8) (actual time=0.006..0.006 rows=0 loops=1)
                      Recheck Cond: (tsv @@ '''voile'''::tsquery)
                      Filter: (id_fiche IS NOT NULL)
                      Buffers: shared hit=2
                      ->  Bitmap Index Scan on idx_chunk_tsv  (cost=0.00..8.56 rows=2 width=0) (actual time=0.006..0.006 rows=0 loops=1)
                            Index Cond: (tsv @@ '''voile'''::tsquery)
                            Buffers: shared hit=2
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.001..0.001 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
  ->  HashAggregate  (cost=2040.45..2042.95 rows=200 width=68) (actual time=0.013..0.014 rows=0 loops=1)
        Group Key: tv.libelle
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1153.54..2015.70 rows=4949 width=32) (actual time=0.012..0.013 rows=0 loops=1)
              Hash Cond: (f_1.id_fiche = f.id_fiche)
              ->  Hash Join  (cost=247.54..1096.71 rows=4949 width=48) (actual time=0.012..0.012 rows=0 loops=1)
                    Hash Cond: (f_1.id_type_voile = tv.id_type_voile)
                    ->  Nested Loop  (cost=225.35..1061.40 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                Group Key: correspondances.id_fiche
                                ->  CTE Scan on correspondances  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_1  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=16.12..16.12 rows=485 width=40) (actual time=0.001..0.002 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on type_voile tv  (cost=0.00..16.12 rows=485 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                                Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2036.78..2039.28 rows=200 width=68) (actual time=0.016..0.018 rows=0 loops=1)
        Group Key: c.nom
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1149.82..2012.00 rows=4956 width=32) (actual time=0.015..0.018 rows=0 loops=1)
              Hash Cond: (f_3.id_fiche = f_2.id_fiche)
              ->  Hash Join  (cost=243.81..1092.99 rows=4956 width=48) (actual time=0.015..0.016 rows=0 loops=1)
                    Hash Cond: (f_3.id_client = c.id_client)
                    ->  Nested Loop  (cost=225.35..1061.40 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                Group Key: correspondances_1.id_fiche
                                ->  CTE Scan on correspondances correspondances_1  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_3  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances_1.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=14.25..14.25 rows=337 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on client c  (cost=0.00..14.25 rows=337 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                                Filter: ((nom IS NOT NULL) AND (nom <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f_2  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2073.22..2076.72 rows=200 width=68) (actual time=0.012..0.014 rows=0 loops=1)
        Group Key: ((b.nom || ' '::text) || COALESCE(b.taille, ''::text))
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1161.58..2048.48 rows=4948 width=32) (actual time=0.012..0.013 rows=0 loops=1)
              Hash Cond: (f_5.id_fiche = f_4.id_fiche)
              ->  Hash Join  (cost=255.58..1104.75 rows=4948 width=80) (actual time=0.012..0.013 rows=0 loops=1)
                    Hash Cond: (f_5.id_bateau = b.id_bateau)
                    ->  Nested Loop  (cost=225.35..1061.40 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                Group Key: correspondances_2.id_fiche
                                ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_5  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances_2.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=23.05..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on bateau b  (cost=0.00..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                                Filter: ((((nom || ' '::text) || COALESCE(taille, ''::text)) IS NOT NULL) AND (((nom || ' '::text) || COALESCE(taille, ''::text)) <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f_4  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2005.94..2005.97 rows=2 width=45) (actual time=22.125..22.126 rows=2 loops=1)
        Group Key: f_7.gamme
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=44972
        ->  Hash Join  (cost=1131.35..1980.94 rows=5000 width=9) (actual time=8.897..21.156 rows=10000 loops=1)
              Hash Cond: (f_7.id_fiche = f_6.id_fiche)
              Buffers: shared hit=44972
              ->  Nested Loop  (cost=225.35..1061.82 rows=5000 width=25) (actual time=7.727..18.957 rows=10000 loops=1)
                    Buffers: shared hit=44291
                    ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=7.721..8.268 rows=10000 loops=1)
                          Group Key: correspondances_3.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          Buffers: shared hit=683
                          ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.06 rows=10003 width=8) (actual time=4.273..6.211 rows=10000 loops=1)
                                Buffers: shared hit=683
                    ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..4.95 rows=1 width=41) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=1.165..1.165 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_6  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.025..0.532 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  HashAggregate  (cost=2030.53..2043.76 rows=756 width=68) (actual time=17.464..17.466 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=44289
        ->  Hash Join  (cost=1131.35..2005.53 rows=5000 width=32) (actual time=2.683..16.547 rows=10000 loops=1)
              Hash Cond: (f_9.id_fiche = f_8.id_fiche)
              Buffers: shared hit=44289
              ->  Nested Loop  (cost=225.35..1061.40 rows=5000 width=20) (actual time=1.499..12.752 rows=10000 loops=1)
                    Buffers: shared hit=43608
                    ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=1.495..2.071 rows=10000 loops=1)
                          Group Key: correspondances_4.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.346 rows=10000 loops=1)
                    ->  Index Scan using fiche_pkey on fiche f_9  (cost=0.29..4.95 rows=1 width=36) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_4.id_fiche)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=1.177..1.177 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_8  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.022..0.514 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  GroupAggregate  (cost=1284.84..1290.34 rows=275 width=68) (actual time=0.019..0.020 rows=0 loops=1)
        Group Key: m.nom
        ->  Sort  (cost=1284.84..1285.53 rows=275 width=40) (actual time=0.019..0.020 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              ->  Hash Join  (cost=252.51..1273.70 rows=275 width=40) (actual time=0.014..0.014 rows=0 loops=1)
                    Hash Cond: (fm.id_materiau = m.id_materiau)
                    ->  Nested Loop  (cost=230.14..1250.60 rows=275 width=16) (never executed)
                          ->  Nested Loop  (cost=229.85..1086.24 rows=275 width=32) (never executed)
                                Join Filter: (f_11.id_fiche = correspondances_5.id_fiche)
                                ->  Hash Join  (cost=229.57..252.63 rows=275 width=24) (never executed)
                                      Hash Cond: (fm.id_fiche = correspondances_5.id_fiche)
                                      ->  Seq Scan on fiche_materiau fm  (cost=0.00..15.50 rows=550 width=16) (never executed)
                                      ->  Hash  (cost=227.07..227.07 rows=200 width=8) (never executed)
                                            ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                                  Group Key: correspondances_5.id_fiche
                                                  ->  CTE Scan on correspondances correspondances_5  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                                ->  Index Scan using fiche_pkey on fiche f_11  (cost=0.29..3.57 rows=1 width=32) (never executed)
                                      Index Cond: (id_fiche = fm.id_fiche)
                                      Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                          ->  Index Only Scan using fiche_pkey on fiche f_10  (cost=0.29..0.60 rows=1 width=8) (never executed)
                                Index Cond: (id_fiche = f_11.id_fiche)
                                Heap Fetches: 0
                    ->  Hash  (cost=15.50..15.50 rows=550 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on materiau m  (cost=0.00..15.50 rows=550 width=40) (actual time=0.001..0.001 rows=0 loops=1)
Planning:
  Buffers: shared hit=135
Planning Time: 2.650 ms
Execution Time: 39.932 ms
```
