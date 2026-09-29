# Banc d’échelle SYNTHÉTIQUE — run 36593833893

> Données synthétiques uniquement. Ce résultat ne constitue pas une mesure réelle de VPS01, R2 ni du poste atelier.

- Run: https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36593833893
- Commit: `9794e474051f73afec54238d204b45ae6238ff04`
- Base jetable GitHub Actions PostgreSQL; 10 000 fiches synthétiques; 50 mesures par scénario.
- Durée job observée: 53 s; budget maximal demandé: moins de 20 min.
- Cible p95 de ce micro-benchmark: 250 ms indicative (distincte du critère produit réel).
- JSON: [`scale-bench-synthetique-36593833893.json`](scale-bench-synthetique-36593833893.json)

## Mesures (ms)

| Scénario | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| mot_simple | 81.88 | 90.46 | 96.79 | 96.79 |
| multi_mots | 101.87 | 104.61 | 104.89 | 104.89 |
| code | 12.36 | 13.21 | 13.53 | 13.53 |
| facette_dimension | 95.13 | 98.84 | 111.07 | 111.07 |
| filtres | 97.3 | 100.03 | 101.7 | 101.7 |
| suggestions | 8.89 | 9.21 | 9.48 | 9.48 |

## EXPLAIN ANALYZE / BUFFERS — trois requêtes SELECT observées les plus lentes

### Rang 1 — observation 84.901 ms

```text
Append  (cost=2228.24..9162.03 rows=795 width=68) (actual time=68.438..91.249 rows=9 loops=1)
  Buffers: shared hit=75166
  CTE correspondances
    ->  HashAggregate  (cost=906.55..1006.58 rows=10003 width=8) (actual time=4.598..5.498 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=683
          ->  Append  (cost=0.00..881.55 rows=10003 width=8) (actual time=0.037..2.941 rows=10000 loops=1)
                Buffers: shared hit=683
                ->  Seq Scan on fiche f_12  (cost=0.00..806.00 rows=10000 width=8) (actual time=0.036..2.261 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
                      Buffers: shared hit=681
                ->  Bitmap Heap Scan on chunk c_1  (cost=8.56..13.91 rows=2 width=8) (actual time=0.004..0.004 rows=0 loops=1)
                      Recheck Cond: (tsv @@ '''voile'''::tsquery)
                      Filter: (id_fiche IS NOT NULL)
                      Buffers: shared hit=2
                      ->  Bitmap Index Scan on idx_chunk_tsv  (cost=0.00..8.56 rows=2 width=0) (actual time=0.003..0.003 rows=0 loops=1)
                            Index Cond: (tsv @@ '''voile'''::tsquery)
                            Buffers: shared hit=2
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.002..0.002 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
  ->  GroupAggregate  (cost=1221.66..1221.90 rows=12 width=68) (actual time=14.517..14.520 rows=0 loops=1)
        Group Key: tv.libelle
        Buffers: shared hit=1374
        ->  Sort  (cost=1221.66..1221.69 rows=12 width=32) (actual time=14.516..14.519 rows=0 loops=1)
              Sort Key: tv.libelle
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=1374
              ->  Nested Loop  (cost=307.80..1221.44 rows=12 width=32) (actual time=14.514..14.516 rows=0 loops=1)
                    Join Filter: (f.id_fiche = f_1.id_fiche)
                    Buffers: shared hit=1374
                    ->  Nested Loop  (cost=307.52..1161.98 rows=12 width=48) (actual time=14.514..14.516 rows=0 loops=1)
                          Buffers: shared hit=1374
                          ->  Hash Join  (cost=307.36..1138.56 rows=12 width=24) (actual time=11.092..13.925 rows=2858 loops=1)
                                Hash Cond: (f_1.id_fiche = correspondances.id_fiche)
                                Buffers: shared hit=1374
                                ->  Bitmap Heap Scan on fiche f_1  (cost=77.79..908.79 rows=25 width=32) (actual time=0.376..2.710 rows=2858 loops=1)
                                      Recheck Cond: (gamme = 'Régate'::text)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 2142
                                      Heap Blocks: exact=681
                                      Buffers: shared hit=691
                                      ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.201..0.201 rows=10000 loops=1)
                                            Index Cond: (gamme = 'Régate'::text)
                                            Buffers: shared hit=10
                                ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=10.712..10.712 rows=10000 loops=1)
                                      Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                      Buffers: shared hit=683
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=8.702..9.555 rows=10000 loops=1)
                                            Group Key: correspondances.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            Buffers: shared hit=683
                                            ->  CTE Scan on correspondances  (cost=0.00..200.06 rows=10003 width=8) (actual time=4.599..6.961 rows=10000 loops=1)
                                                  Buffers: shared hit=683
                          ->  Memoize  (cost=0.16..1.94 rows=1 width=40) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_1.id_type_voile
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using type_voile_pkey on type_voile tv  (cost=0.15..1.93 rows=1 width=40) (actual time=0.002..0.002 rows=0 loops=1)
                                      Index Cond: (id_type_voile = f_1.id_type_voile)
                                      Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f  (cost=0.29..4.94 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1221.66..1221.90 rows=12 width=68) (actual time=8.354..8.357 rows=0 loops=1)
        Group Key: c.nom
        Buffers: shared hit=691
        ->  Sort  (cost=1221.66..1221.69 rows=12 width=32) (actual time=8.354..8.356 rows=0 loops=1)
              Sort Key: c.nom
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=691
              ->  Nested Loop  (cost=307.80..1221.44 rows=12 width=32) (actual time=8.337..8.339 rows=0 loops=1)
                    Join Filter: (f_2.id_fiche = f_3.id_fiche)
                    Buffers: shared hit=691
                    ->  Nested Loop  (cost=307.52..1161.98 rows=12 width=48) (actual time=8.337..8.339 rows=0 loops=1)
                          Buffers: shared hit=691
                          ->  Hash Join  (cost=307.36..1138.56 rows=12 width=24) (actual time=4.997..7.760 rows=2858 loops=1)
                                Hash Cond: (f_3.id_fiche = correspondances_1.id_fiche)
                                Buffers: shared hit=691
                                ->  Bitmap Heap Scan on fiche f_3  (cost=77.79..908.79 rows=25 width=32) (actual time=0.382..2.668 rows=2858 loops=1)
                                      Recheck Cond: (gamme = 'Régate'::text)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 2142
                                      Heap Blocks: exact=681
                                      Buffers: shared hit=691
                                      ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.216..0.216 rows=10000 loops=1)
                                            Index Cond: (gamme = 'Régate'::text)
                                            Buffers: shared hit=10
                                ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=4.601..4.602 rows=10000 loops=1)
                                      Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.557..3.427 rows=10000 loops=1)
                                            Group Key: correspondances_1.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            ->  CTE Scan on correspondances correspondances_1  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.548 rows=10000 loops=1)
                          ->  Memoize  (cost=0.16..1.94 rows=1 width=40) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_3.id_client
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using client_pkey on client c  (cost=0.15..1.93 rows=1 width=40) (actual time=0.001..0.001 rows=0 loops=1)
                                      Index Cond: (id_client = f_3.id_client)
                                      Filter: ((nom IS NOT NULL) AND (nom <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f_2  (cost=0.29..4.94 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances_1.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1221.87..1222.17 rows=12 width=68) (actual time=8.249..8.251 rows=0 loops=1)
        Group Key: (((b.nom || ' '::text) || COALESCE(b.taille, ''::text)))
        Buffers: shared hit=691
        ->  Sort  (cost=1221.87..1221.90 rows=12 width=32) (actual time=8.249..8.250 rows=0 loops=1)
              Sort Key: (((b.nom || ' '::text) || COALESCE(b.taille, ''::text)))
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=691
              ->  Nested Loop  (cost=307.80..1221.65 rows=12 width=32) (actual time=8.242..8.244 rows=0 loops=1)
                    Join Filter: (f_4.id_fiche = f_5.id_fiche)
                    Buffers: shared hit=691
                    ->  Nested Loop  (cost=307.52..1162.13 rows=12 width=80) (actual time=8.242..8.243 rows=0 loops=1)
                          Buffers: shared hit=691
                          ->  Hash Join  (cost=307.36..1138.56 rows=12 width=24) (actual time=4.903..7.659 rows=2858 loops=1)
                                Hash Cond: (f_5.id_fiche = correspondances_2.id_fiche)
                                Buffers: shared hit=691
                                ->  Bitmap Heap Scan on fiche f_5  (cost=77.79..908.79 rows=25 width=32) (actual time=0.374..2.631 rows=2858 loops=1)
                                      Recheck Cond: (gamme = 'Régate'::text)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 2142
                                      Heap Blocks: exact=681
                                      Buffers: shared hit=691
                                      ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.213..0.213 rows=10000 loops=1)
                                            Index Cond: (gamme = 'Régate'::text)
                                            Buffers: shared hit=10
                                ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=4.519..4.520 rows=10000 loops=1)
                                      Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.504..3.354 rows=10000 loops=1)
                                            Group Key: correspondances_2.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.556 rows=10000 loops=1)
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
  ->  GroupAggregate  (cost=1345.02..1345.24 rows=2 width=45) (actual time=37.317..37.642 rows=2 loops=1)
        Group Key: f_7.gamme
        Buffers: shared hit=64641
        ->  Sort  (cost=1345.02..1345.09 rows=25 width=9) (actual time=36.995..37.218 rows=5715 loops=1)
              Sort Key: f_7.gamme
              Sort Method: quicksort  Memory: 304kB
              Buffers: shared hit=64641
              ->  Nested Loop  (cost=225.64..1344.44 rows=25 width=9) (actual time=2.740..36.259 rows=5715 loops=1)
                    Join Filter: (f_6.id_fiche = f_7.id_fiche)
                    Buffers: shared hit=64641
                    ->  Nested Loop  (cost=225.35..1220.57 rows=25 width=25) (actual time=2.735..26.779 rows=5715 loops=1)
                          Buffers: shared hit=43608
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.722..3.878 rows=10000 loops=1)
                                Group Key: correspondances_3.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.596 rows=10000 loops=1)
                          ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..4.96 rows=1 width=41) (actual time=0.002..0.002 rows=1 loops=10000)
                                Index Cond: (id_fiche = correspondances_3.id_fiche)
                                Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                Rows Removed by Filter: 0
                                Buffers: shared hit=39720
                    ->  Index Only Scan using fiche_pkey on fiche f_6  (cost=0.29..4.94 rows=1 width=8) (actual time=0.001..0.001 rows=1 loops=5715)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Heap Fetches: 9603
                          Buffers: shared hit=21033
  ->  HashAggregate  (cost=1981.69..1994.92 rows=756 width=68) (actual time=13.252..13.256 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=1362
        ->  Hash Join  (cost=1123.07..1969.19 rows=2500 width=32) (actual time=7.306..12.488 rows=5000 loops=1)
              Hash Cond: (f_8.id_fiche = f_9.id_fiche)
              Buffers: shared hit=1362
              ->  Hash Join  (cost=229.57..1050.07 rows=5000 width=16) (actual time=4.566..7.246 rows=10000 loops=1)
                    Hash Cond: (f_8.id_fiche = correspondances_4.id_fiche)
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_8  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.039..0.916 rows=10000 loops=1)
                          Buffers: shared hit=681
                    ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=4.522..4.523 rows=10000 loops=1)
                          Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.454..3.302 rows=10000 loops=1)
                                Group Key: correspondances_4.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.536 rows=10000 loops=1)
              ->  Hash  (cost=831.00..831.00 rows=5000 width=36) (actual time=2.704..2.704 rows=5000 loops=1)
                    Buckets: 8192  Batches: 1  Memory Usage: 299kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_9  (cost=0.00..831.00 rows=5000 width=36) (actual time=0.041..1.947 rows=5000 loops=1)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme = 'Régate'::text))
                          Rows Removed by Filter: 5000
                          Buffers: shared hit=681
  ->  GroupAggregate  (cost=1145.33..1145.35 rows=1 width=68) (actual time=9.218..9.220 rows=0 loops=1)
        Group Key: m.nom
        Buffers: shared hit=6407
        ->  Sort  (cost=1145.33..1145.33 rows=1 width=40) (actual time=9.217..9.219 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=6407
              ->  Nested Loop  (cost=307.94..1145.32 rows=1 width=40) (actual time=9.210..9.211 rows=0 loops=1)
                    Buffers: shared hit=6407
                    ->  Nested Loop  (cost=307.79..1145.07 rows=1 width=16) (actual time=9.209..9.211 rows=0 loops=1)
                          Join Filter: (f_10.id_fiche = f_11.id_fiche)
                          Buffers: shared hit=6407
                          ->  Nested Loop  (cost=307.51..1141.49 rows=1 width=32) (actual time=9.209..9.210 rows=0 loops=1)
                                Join Filter: (fm.id_fiche = f_11.id_fiche)
                                Buffers: shared hit=6407
                                ->  Hash Join  (cost=307.36..1138.56 rows=12 width=16) (actual time=4.960..7.821 rows=2858 loops=1)
                                      Hash Cond: (f_11.id_fiche = correspondances_5.id_fiche)
                                      Buffers: shared hit=691
                                      ->  Bitmap Heap Scan on fiche f_11  (cost=77.79..908.79 rows=25 width=32) (actual time=0.388..2.750 rows=2858 loops=1)
                                            Recheck Cond: (gamme = 'Régate'::text)
                                            Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                            Rows Removed by Filter: 2142
                                            Heap Blocks: exact=681
                                            Buffers: shared hit=691
                                            ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.218..0.219 rows=10000 loops=1)
                                                  Index Cond: (gamme = 'Régate'::text)
                                                  Buffers: shared hit=10
                                      ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=4.561..4.561 rows=10000 loops=1)
                                            Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                            ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.518..3.407 rows=10000 loops=1)
                                                  Group Key: correspondances_5.id_fiche
                                                  Batches: 1  Memory Usage: 929kB
                                                  ->  CTE Scan on correspondances correspondances_5  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.001..0.537 rows=10000 loops=1)
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
Planning Time: 3.435 ms
Execution Time: 91.478 ms
```

### Rang 2 — observation 75.055 ms

```text
Append  (cost=3047.03..12513.76 rows=1633 width=68) (actual time=35.666..67.108 rows=9 loops=1)
  Buffers: shared hit=89261
  CTE correspondances
    ->  HashAggregate  (cost=906.55..1006.58 rows=10003 width=8) (actual time=5.032..5.901 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=683
          ->  Append  (cost=0.00..881.55 rows=10003 width=8) (actual time=0.067..3.207 rows=10000 loops=1)
                Buffers: shared hit=683
                ->  Seq Scan on fiche f_12  (cost=0.00..806.00 rows=10000 width=8) (actual time=0.066..2.525 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
                      Buffers: shared hit=681
                ->  Bitmap Heap Scan on chunk c_1  (cost=8.56..13.91 rows=2 width=8) (actual time=0.006..0.006 rows=0 loops=1)
                      Recheck Cond: (tsv @@ '''voile'''::tsquery)
                      Filter: (id_fiche IS NOT NULL)
                      Buffers: shared hit=2
                      ->  Bitmap Index Scan on idx_chunk_tsv  (cost=0.00..8.56 rows=2 width=0) (actual time=0.005..0.005 rows=0 loops=1)
                            Index Cond: (tsv @@ '''voile'''::tsquery)
                            Buffers: shared hit=2
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.003..0.003 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
  ->  HashAggregate  (cost=2040.45..2042.95 rows=200 width=68) (actual time=0.015..0.017 rows=0 loops=1)
        Group Key: tv.libelle
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1153.54..2015.70 rows=4949 width=32) (actual time=0.014..0.016 rows=0 loops=1)
              Hash Cond: (f_1.id_fiche = f.id_fiche)
              ->  Hash Join  (cost=247.54..1096.71 rows=4949 width=48) (actual time=0.013..0.015 rows=0 loops=1)
                    Hash Cond: (f_1.id_type_voile = tv.id_type_voile)
                    ->  Nested Loop  (cost=225.35..1061.40 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                Group Key: correspondances.id_fiche
                                ->  CTE Scan on correspondances  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_1  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=16.12..16.12 rows=485 width=40) (actual time=0.002..0.002 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on type_voile tv  (cost=0.00..16.12 rows=485 width=40) (actual time=0.002..0.002 rows=0 loops=1)
                                Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2036.78..2039.28 rows=200 width=68) (actual time=0.017..0.018 rows=0 loops=1)
        Group Key: c.nom
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1149.82..2012.00 rows=4956 width=32) (actual time=0.016..0.018 rows=0 loops=1)
              Hash Cond: (f_3.id_fiche = f_2.id_fiche)
              ->  Hash Join  (cost=243.81..1092.99 rows=4956 width=48) (actual time=0.016..0.017 rows=0 loops=1)
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
  ->  HashAggregate  (cost=2073.22..2076.72 rows=200 width=68) (actual time=0.014..0.016 rows=0 loops=1)
        Group Key: ((b.nom || ' '::text) || COALESCE(b.taille, ''::text))
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1161.58..2048.48 rows=4948 width=32) (actual time=0.014..0.015 rows=0 loops=1)
              Hash Cond: (f_5.id_fiche = f_4.id_fiche)
              ->  Hash Join  (cost=255.58..1104.75 rows=4948 width=80) (actual time=0.013..0.014 rows=0 loops=1)
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
  ->  HashAggregate  (cost=2005.94..2005.97 rows=2 width=45) (actual time=35.619..35.620 rows=2 loops=1)
        Group Key: f_7.gamme
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=44972
        ->  Hash Join  (cost=1131.35..1980.94 rows=5000 width=9) (actual time=11.972..33.910 rows=10000 loops=1)
              Hash Cond: (f_7.id_fiche = f_6.id_fiche)
              Buffers: shared hit=44972
              ->  Nested Loop  (cost=225.35..1061.82 rows=5000 width=25) (actual time=9.843..29.977 rows=10000 loops=1)
                    Buffers: shared hit=44291
                    ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=9.829..10.898 rows=10000 loops=1)
                          Group Key: correspondances_3.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          Buffers: shared hit=683
                          ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.06 rows=10003 width=8) (actual time=5.033..7.681 rows=10000 loops=1)
                                Buffers: shared hit=683
                    ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..4.95 rows=1 width=41) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=2.121..2.121 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_6  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.057..0.916 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  HashAggregate  (cost=2030.53..2043.76 rows=756 width=68) (actual time=31.396..31.399 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=44289
        ->  Hash Join  (cost=1131.35..2005.53 rows=5000 width=32) (actual time=4.767..29.671 rows=10000 loops=1)
              Hash Cond: (f_9.id_fiche = f_8.id_fiche)
              Buffers: shared hit=44289
              ->  Nested Loop  (cost=225.35..1061.40 rows=5000 width=20) (actual time=2.643..22.738 rows=10000 loops=1)
                    Buffers: shared hit=43608
                    ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.637..3.650 rows=10000 loops=1)
                          Group Key: correspondances_4.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.000..0.566 rows=10000 loops=1)
                    ->  Index Scan using fiche_pkey on fiche f_9  (cost=0.29..4.95 rows=1 width=36) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_4.id_fiche)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=2.111..2.111 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_8  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.041..0.909 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  GroupAggregate  (cost=1284.84..1290.34 rows=275 width=68) (actual time=0.032..0.033 rows=0 loops=1)
        Group Key: m.nom
        ->  Sort  (cost=1284.84..1285.53 rows=275 width=40) (actual time=0.032..0.033 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              ->  Hash Join  (cost=252.51..1273.70 rows=275 width=40) (actual time=0.021..0.022 rows=0 loops=1)
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
                    ->  Hash  (cost=15.50..15.50 rows=550 width=40) (actual time=0.003..0.003 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on materiau m  (cost=0.00..15.50 rows=550 width=40) (actual time=0.003..0.003 rows=0 loops=1)
Planning:
  Buffers: shared hit=135
Planning Time: 4.149 ms
Execution Time: 67.439 ms
```

### Rang 3 — observation 64.528 ms

```text
Append  (cost=3048.46..12515.08 rows=1633 width=68) (actual time=36.267..68.559 rows=9 loops=1)
  Buffers: shared hit=89259
  CTE correspondances
    ->  HashAggregate  (cost=908.01..1008.03 rows=10002 width=8) (actual time=5.436..6.459 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=681
          ->  Append  (cost=0.00..883.01 rows=10002 width=8) (actual time=0.056..3.726 rows=10000 loops=1)
                Buffers: shared hit=681
                ->  Seq Scan on fiche f_12  (cost=0.00..806.00 rows=10000 width=8) (actual time=0.056..3.063 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
                      Buffers: shared hit=681
                ->  Seq Scan on chunk c_1  (cost=0.00..15.38 rows=1 width=8) (actual time=0.003..0.003 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (tsv @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.002..0.002 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
  ->  HashAggregate  (cost=2040.43..2042.93 rows=200 width=68) (actual time=0.007..0.010 rows=0 loops=1)
        Group Key: tv.libelle
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1153.52..2015.68 rows=4949 width=32) (actual time=0.006..0.009 rows=0 loops=1)
              Hash Cond: (f_1.id_fiche = f.id_fiche)
              ->  Hash Join  (cost=247.52..1096.69 rows=4949 width=48) (actual time=0.006..0.008 rows=0 loops=1)
                    Hash Cond: (f_1.id_type_voile = tv.id_type_voile)
                    ->  Nested Loop  (cost=225.33..1061.38 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                Group Key: correspondances.id_fiche
                                ->  CTE Scan on correspondances  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_1  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=16.12..16.12 rows=485 width=40) (actual time=0.002..0.003 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on type_voile tv  (cost=0.00..16.12 rows=485 width=40) (actual time=0.002..0.002 rows=0 loops=1)
                                Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2036.76..2039.26 rows=200 width=68) (actual time=0.005..0.006 rows=0 loops=1)
        Group Key: c.nom
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1149.79..2011.98 rows=4956 width=32) (actual time=0.004..0.006 rows=0 loops=1)
              Hash Cond: (f_3.id_fiche = f_2.id_fiche)
              ->  Hash Join  (cost=243.79..1092.97 rows=4956 width=48) (actual time=0.004..0.005 rows=0 loops=1)
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
  ->  HashAggregate  (cost=2073.20..2076.70 rows=200 width=68) (actual time=0.005..0.007 rows=0 loops=1)
        Group Key: ((b.nom || ' '::text) || COALESCE(b.taille, ''::text))
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1161.56..2048.46 rows=4948 width=32) (actual time=0.004..0.007 rows=0 loops=1)
              Hash Cond: (f_5.id_fiche = f_4.id_fiche)
              ->  Hash Join  (cost=255.56..1104.73 rows=4948 width=80) (actual time=0.004..0.005 rows=0 loops=1)
                    Hash Cond: (f_5.id_bateau = b.id_bateau)
                    ->  Nested Loop  (cost=225.33..1061.38 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                Group Key: correspondances_2.id_fiche
                                ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_5  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances_2.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=23.05..23.05 rows=574 width=72) (actual time=0.001..0.002 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on bateau b  (cost=0.00..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                                Filter: ((((nom || ' '::text) || COALESCE(taille, ''::text)) IS NOT NULL) AND (((nom || ' '::text) || COALESCE(taille, ''::text)) <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f_4  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2005.92..2005.94 rows=2 width=45) (actual time=36.250..36.252 rows=2 loops=1)
        Group Key: f_7.gamme
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=44970
        ->  Hash Join  (cost=1131.33..1980.92 rows=5000 width=9) (actual time=11.678..34.465 rows=10000 loops=1)
              Hash Cond: (f_7.id_fiche = f_6.id_fiche)
              Buffers: shared hit=44970
              ->  Nested Loop  (cost=225.33..1061.79 rows=5000 width=25) (actual time=9.673..30.497 rows=10000 loops=1)
                    Buffers: shared hit=44289
                    ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (actual time=9.662..10.961 rows=10000 loops=1)
                          Group Key: correspondances_3.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          Buffers: shared hit=681
                          ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.04 rows=10002 width=8) (actual time=5.437..8.005 rows=10000 loops=1)
                                Buffers: shared hit=681
                    ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..4.95 rows=1 width=41) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=1.998..1.998 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_6  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.054..0.968 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  HashAggregate  (cost=2030.50..2043.73 rows=756 width=68) (actual time=32.246..32.253 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=44289
        ->  Hash Join  (cost=1131.33..2005.50 rows=5000 width=32) (actual time=4.237..30.370 rows=10000 loops=1)
              Hash Cond: (f_9.id_fiche = f_8.id_fiche)
              Buffers: shared hit=44289
              ->  Nested Loop  (cost=225.33..1061.38 rows=5000 width=20) (actual time=2.272..23.254 rows=10000 loops=1)
                    Buffers: shared hit=43608
                    ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (actual time=2.265..3.423 rows=10000 loops=1)
                          Group Key: correspondances_4.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.04 rows=10002 width=8) (actual time=0.000..0.562 rows=10000 loops=1)
                    ->  Index Scan using fiche_pkey on fiche f_9  (cost=0.29..4.95 rows=1 width=36) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_4.id_fiche)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=1.954..1.955 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_8  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.045..0.890 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  GroupAggregate  (cost=1284.82..1290.32 rows=275 width=68) (actual time=0.021..0.023 rows=0 loops=1)
        Group Key: m.nom
        ->  Sort  (cost=1284.82..1285.51 rows=275 width=40) (actual time=0.021..0.023 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              ->  Hash Join  (cost=252.49..1273.68 rows=275 width=40) (actual time=0.009..0.011 rows=0 loops=1)
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
                    ->  Hash  (cost=15.50..15.50 rows=550 width=40) (actual time=0.003..0.004 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on materiau m  (cost=0.00..15.50 rows=550 width=40) (actual time=0.003..0.003 rows=0 loops=1)
Planning:
  Buffers: shared hit=135
Planning Time: 3.300 ms
Execution Time: 68.815 ms
```
