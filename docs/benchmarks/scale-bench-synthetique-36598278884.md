# Banc d’échelle SYNTHÉTIQUE — run 36598278884

> Données synthétiques uniquement. Ne constitue pas une mesure réelle de VPS01, R2 ni du poste atelier.

- Run: https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36598278884
- Commit exécuté: `887beba5b5d2e36ce99e405d99a5e23b2e5a53b9`
- Base PostgreSQL jetable GitHub Actions; 10 000 fiches synthétiques; 50 mesures par scénario.
- Durée du job: 1 min 11 s; budget maximal demandé: moins de 20 min.
- Cible p95 indicative de ce micro-benchmark: 250 ms; distincte du critère produit réel.
- JSON: [`scale-bench-synthetique-36598278884.json`](scale-bench-synthetique-36598278884.json)

## Mesures (ms)

| Scénario | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| mot_simple | 81.13 | 84.81 | 88.42 | 88.42 |
| multi_mots | 107.86 | 109.83 | 133.43 | 133.43 |
| code | 11.7 | 12.43 | 12.57 | 12.57 |
| facette_dimension | 92.97 | 94.0 | 95.18 | 95.18 |
| filtres | 98.04 | 99.76 | 111.7 | 111.7 |
| suggestions | 8.47 | 8.76 | 8.82 | 8.82 |

## EXPLAIN ANALYZE / BUFFERS — trois requêtes SELECT observées les plus lentes

### Rang 1 — observation 91.79 ms

```text
Append  (cost=2228.24..9162.03 rows=795 width=68) (actual time=67.701..90.641 rows=9 loops=1)
  Buffers: shared hit=75166
  CTE correspondances
    ->  HashAggregate  (cost=906.55..1006.58 rows=10003 width=8) (actual time=4.826..5.730 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=683
          ->  Append  (cost=0.00..881.55 rows=10003 width=8) (actual time=0.036..3.132 rows=10000 loops=1)
                Buffers: shared hit=683
                ->  Seq Scan on fiche f_12  (cost=0.00..806.00 rows=10000 width=8) (actual time=0.036..2.558 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
                      Buffers: shared hit=681
                ->  Bitmap Heap Scan on chunk c_1  (cost=8.56..13.91 rows=2 width=8) (actual time=0.005..0.006 rows=0 loops=1)
                      Recheck Cond: (tsv @@ '''voile'''::tsquery)
                      Filter: (id_fiche IS NOT NULL)
                      Buffers: shared hit=2
                      ->  Bitmap Index Scan on idx_chunk_tsv  (cost=0.00..8.56 rows=2 width=0) (actual time=0.004..0.005 rows=0 loops=1)
                            Index Cond: (tsv @@ '''voile'''::tsquery)
                            Buffers: shared hit=2
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.003..0.003 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
  ->  GroupAggregate  (cost=1221.66..1221.90 rows=12 width=68) (actual time=14.721..14.726 rows=0 loops=1)
        Group Key: tv.libelle
        Buffers: shared hit=1374
        ->  Sort  (cost=1221.66..1221.69 rows=12 width=32) (actual time=14.720..14.725 rows=0 loops=1)
              Sort Key: tv.libelle
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=1374
              ->  Nested Loop  (cost=307.80..1221.44 rows=12 width=32) (actual time=14.716..14.721 rows=0 loops=1)
                    Join Filter: (f.id_fiche = f_1.id_fiche)
                    Buffers: shared hit=1374
                    ->  Nested Loop  (cost=307.52..1161.98 rows=12 width=48) (actual time=14.716..14.720 rows=0 loops=1)
                          Buffers: shared hit=1374
                          ->  Hash Join  (cost=307.36..1138.56 rows=12 width=24) (actual time=11.221..14.152 rows=2858 loops=1)
                                Hash Cond: (f_1.id_fiche = correspondances.id_fiche)
                                Buffers: shared hit=1374
                                ->  Bitmap Heap Scan on fiche f_1  (cost=77.79..908.79 rows=25 width=32) (actual time=0.418..2.917 rows=2858 loops=1)
                                      Recheck Cond: (gamme = 'Régate'::text)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 2142
                                      Heap Blocks: exact=681
                                      Buffers: shared hit=691
                                      ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.195..0.195 rows=10000 loops=1)
                                            Index Cond: (gamme = 'Régate'::text)
                                            Buffers: shared hit=10
                                ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=10.797..10.799 rows=10000 loops=1)
                                      Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                      Buffers: shared hit=683
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=8.987..9.745 rows=10000 loops=1)
                                            Group Key: correspondances.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            Buffers: shared hit=683
                                            ->  CTE Scan on correspondances  (cost=0.00..200.06 rows=10003 width=8) (actual time=4.828..7.291 rows=10000 loops=1)
                                                  Buffers: shared hit=683
                          ->  Memoize  (cost=0.16..1.94 rows=1 width=40) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_1.id_type_voile
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using type_voile_pkey on type_voile tv  (cost=0.15..1.93 rows=1 width=40) (actual time=0.003..0.003 rows=0 loops=1)
                                      Index Cond: (id_type_voile = f_1.id_type_voile)
                                      Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f  (cost=0.29..4.94 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1221.66..1221.90 rows=12 width=68) (actual time=8.257..8.262 rows=0 loops=1)
        Group Key: c.nom
        Buffers: shared hit=691
        ->  Sort  (cost=1221.66..1221.69 rows=12 width=32) (actual time=8.255..8.259 rows=0 loops=1)
              Sort Key: c.nom
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=691
              ->  Nested Loop  (cost=307.80..1221.44 rows=12 width=32) (actual time=8.244..8.248 rows=0 loops=1)
                    Join Filter: (f_2.id_fiche = f_3.id_fiche)
                    Buffers: shared hit=691
                    ->  Nested Loop  (cost=307.52..1161.98 rows=12 width=48) (actual time=8.243..8.247 rows=0 loops=1)
                          Buffers: shared hit=691
                          ->  Hash Join  (cost=307.36..1138.56 rows=12 width=24) (actual time=4.707..7.675 rows=2858 loops=1)
                                Hash Cond: (f_3.id_fiche = correspondances_1.id_fiche)
                                Buffers: shared hit=691
                                ->  Bitmap Heap Scan on fiche f_3  (cost=77.79..908.79 rows=25 width=32) (actual time=0.452..2.947 rows=2858 loops=1)
                                      Recheck Cond: (gamme = 'Régate'::text)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 2142
                                      Heap Blocks: exact=681
                                      Buffers: shared hit=691
                                      ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.231..0.231 rows=10000 loops=1)
                                            Index Cond: (gamme = 'Régate'::text)
                                            Buffers: shared hit=10
                                ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=4.249..4.250 rows=10000 loops=1)
                                      Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.412..3.173 rows=10000 loops=1)
                                            Group Key: correspondances_1.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            ->  CTE Scan on correspondances correspondances_1  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.001..0.519 rows=10000 loops=1)
                          ->  Memoize  (cost=0.16..1.94 rows=1 width=40) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_3.id_client
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using client_pkey on client c  (cost=0.15..1.93 rows=1 width=40) (actual time=0.003..0.003 rows=0 loops=1)
                                      Index Cond: (id_client = f_3.id_client)
                                      Filter: ((nom IS NOT NULL) AND (nom <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f_2  (cost=0.29..4.94 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances_1.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1221.87..1222.17 rows=12 width=68) (actual time=8.225..8.230 rows=0 loops=1)
        Group Key: (((b.nom || ' '::text) || COALESCE(b.taille, ''::text)))
        Buffers: shared hit=691
        ->  Sort  (cost=1221.87..1221.90 rows=12 width=32) (actual time=8.224..8.228 rows=0 loops=1)
              Sort Key: (((b.nom || ' '::text) || COALESCE(b.taille, ''::text)))
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=691
              ->  Nested Loop  (cost=307.80..1221.65 rows=12 width=32) (actual time=8.214..8.218 rows=0 loops=1)
                    Join Filter: (f_4.id_fiche = f_5.id_fiche)
                    Buffers: shared hit=691
                    ->  Nested Loop  (cost=307.52..1162.13 rows=12 width=80) (actual time=8.213..8.216 rows=0 loops=1)
                          Buffers: shared hit=691
                          ->  Hash Join  (cost=307.36..1138.56 rows=12 width=24) (actual time=4.693..7.659 rows=2858 loops=1)
                                Hash Cond: (f_5.id_fiche = correspondances_2.id_fiche)
                                Buffers: shared hit=691
                                ->  Bitmap Heap Scan on fiche f_5  (cost=77.79..908.79 rows=25 width=32) (actual time=0.432..2.938 rows=2858 loops=1)
                                      Recheck Cond: (gamme = 'Régate'::text)
                                      Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                      Rows Removed by Filter: 2142
                                      Heap Blocks: exact=681
                                      Buffers: shared hit=691
                                      ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.216..0.217 rows=10000 loops=1)
                                            Index Cond: (gamme = 'Régate'::text)
                                            Buffers: shared hit=10
                                ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=4.247..4.248 rows=10000 loops=1)
                                      Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                      ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.391..3.162 rows=10000 loops=1)
                                            Group Key: correspondances_2.id_fiche
                                            Batches: 1  Memory Usage: 929kB
                                            ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.001..0.526 rows=10000 loops=1)
                          ->  Memoize  (cost=0.16..1.95 rows=1 width=72) (actual time=0.000..0.000 rows=0 loops=2858)
                                Cache Key: f_5.id_bateau
                                Cache Mode: logical
                                Hits: 2857  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                ->  Index Scan using bateau_pkey on bateau b  (cost=0.15..1.94 rows=1 width=72) (actual time=0.002..0.002 rows=0 loops=1)
                                      Index Cond: (id_bateau = f_5.id_bateau)
                                      Filter: ((((nom || ' '::text) || COALESCE(taille, ''::text)) IS NOT NULL) AND (((nom || ' '::text) || COALESCE(taille, ''::text)) <> ''::text))
                    ->  Index Only Scan using fiche_pkey on fiche f_4  (cost=0.29..4.94 rows=1 width=8) (never executed)
                          Index Cond: (id_fiche = correspondances_2.id_fiche)
                          Heap Fetches: 0
  ->  GroupAggregate  (cost=1345.02..1345.24 rows=2 width=45) (actual time=36.495..36.782 rows=2 loops=1)
        Group Key: f_7.gamme
        Buffers: shared hit=64641
        ->  Sort  (cost=1345.02..1345.09 rows=25 width=9) (actual time=36.209..36.393 rows=5715 loops=1)
              Sort Key: f_7.gamme
              Sort Method: quicksort  Memory: 304kB
              Buffers: shared hit=64641
              ->  Nested Loop  (cost=225.64..1344.44 rows=25 width=9) (actual time=2.393..35.462 rows=5715 loops=1)
                    Join Filter: (f_6.id_fiche = f_7.id_fiche)
                    Buffers: shared hit=64641
                    ->  Nested Loop  (cost=225.35..1220.57 rows=25 width=25) (actual time=2.387..26.203 rows=5715 loops=1)
                          Buffers: shared hit=43608
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.373..3.388 rows=10000 loops=1)
                                Group Key: correspondances_3.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.001..0.517 rows=10000 loops=1)
                          ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..4.96 rows=1 width=41) (actual time=0.002..0.002 rows=1 loops=10000)
                                Index Cond: (id_fiche = correspondances_3.id_fiche)
                                Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                Rows Removed by Filter: 0
                                Buffers: shared hit=39720
                    ->  Index Only Scan using fiche_pkey on fiche f_6  (cost=0.29..4.94 rows=1 width=8) (actual time=0.001..0.001 rows=1 loops=5715)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Heap Fetches: 9603
                          Buffers: shared hit=21033
  ->  HashAggregate  (cost=1981.69..1994.92 rows=756 width=68) (actual time=13.426..13.433 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=1362
        ->  Hash Join  (cost=1123.07..1969.19 rows=2500 width=32) (actual time=7.534..12.762 rows=5000 loops=1)
              Hash Cond: (f_8.id_fiche = f_9.id_fiche)
              Buffers: shared hit=1362
              ->  Hash Join  (cost=229.57..1050.07 rows=5000 width=16) (actual time=4.432..7.425 rows=10000 loops=1)
                    Hash Cond: (f_8.id_fiche = correspondances_4.id_fiche)
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_8  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.058..1.183 rows=10000 loops=1)
                          Buffers: shared hit=681
                    ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=4.365..4.366 rows=10000 loops=1)
                          Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.398..3.233 rows=10000 loops=1)
                                Group Key: correspondances_4.id_fiche
                                Batches: 1  Memory Usage: 929kB
                                ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.001..0.532 rows=10000 loops=1)
              ->  Hash  (cost=831.00..831.00 rows=5000 width=36) (actual time=3.062..3.063 rows=5000 loops=1)
                    Buckets: 8192  Batches: 1  Memory Usage: 299kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_9  (cost=0.00..831.00 rows=5000 width=36) (actual time=0.072..2.347 rows=5000 loops=1)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme = 'Régate'::text))
                          Rows Removed by Filter: 5000
                          Buffers: shared hit=681
  ->  GroupAggregate  (cost=1145.33..1145.35 rows=1 width=68) (actual time=9.190..9.193 rows=0 loops=1)
        Group Key: m.nom
        Buffers: shared hit=6407
        ->  Sort  (cost=1145.33..1145.33 rows=1 width=40) (actual time=9.188..9.191 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=6407
              ->  Nested Loop  (cost=307.94..1145.32 rows=1 width=40) (actual time=9.173..9.176 rows=0 loops=1)
                    Buffers: shared hit=6407
                    ->  Nested Loop  (cost=307.79..1145.07 rows=1 width=16) (actual time=9.172..9.174 rows=0 loops=1)
                          Join Filter: (f_10.id_fiche = f_11.id_fiche)
                          Buffers: shared hit=6407
                          ->  Nested Loop  (cost=307.51..1141.49 rows=1 width=32) (actual time=9.172..9.173 rows=0 loops=1)
                                Join Filter: (fm.id_fiche = f_11.id_fiche)
                                Buffers: shared hit=6407
                                ->  Hash Join  (cost=307.36..1138.56 rows=12 width=16) (actual time=4.698..7.780 rows=2858 loops=1)
                                      Hash Cond: (f_11.id_fiche = correspondances_5.id_fiche)
                                      Buffers: shared hit=691
                                      ->  Bitmap Heap Scan on fiche f_11  (cost=77.79..908.79 rows=25 width=32) (actual time=0.434..3.033 rows=2858 loops=1)
                                            Recheck Cond: (gamme = 'Régate'::text)
                                            Filter: ((statut = ANY ('{valide,a_valider}'::text[])) AND ((EXTRACT(year FROM date_edition))::integer >= 2022) AND ((EXTRACT(year FROM date_edition))::integer <= 2025))
                                            Rows Removed by Filter: 2142
                                            Heap Blocks: exact=681
                                            Buffers: shared hit=691
                                            ->  Bitmap Index Scan on idx_fiche_gamme  (cost=0.00..77.78 rows=5000 width=0) (actual time=0.224..0.225 rows=10000 loops=1)
                                                  Index Cond: (gamme = 'Régate'::text)
                                                  Buffers: shared hit=10
                                      ->  Hash  (cost=227.07..227.07 rows=200 width=8) (actual time=4.250..4.251 rows=10000 loops=1)
                                            Buckets: 16384 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 519kB
                                            ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=2.405..3.173 rows=10000 loops=1)
                                                  Group Key: correspondances_5.id_fiche
                                                  Batches: 1  Memory Usage: 929kB
                                                  ->  CTE Scan on correspondances correspondances_5  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.002..0.526 rows=10000 loops=1)
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
Planning Time: 3.302 ms
Execution Time: 90.960 ms
```

### Rang 2 — observation 87.612 ms

```text
Append  (cost=3048.46..12515.08 rows=1633 width=68) (actual time=36.814..69.117 rows=9 loops=1)
  Buffers: shared hit=89259
  CTE correspondances
    ->  HashAggregate  (cost=908.01..1008.03 rows=10002 width=8) (actual time=5.381..6.375 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=681
          ->  Append  (cost=0.00..883.01 rows=10002 width=8) (actual time=0.104..3.618 rows=10000 loops=1)
                Buffers: shared hit=681
                ->  Seq Scan on fiche f_12  (cost=0.00..806.00 rows=10000 width=8) (actual time=0.104..3.101 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
                      Buffers: shared hit=681
                ->  Seq Scan on chunk c_1  (cost=0.00..15.38 rows=1 width=8) (actual time=0.003..0.003 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (tsv @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.002..0.002 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'' & ''croisiere'' & ''synthetique'''::tsquery))
  ->  HashAggregate  (cost=2040.43..2042.93 rows=200 width=68) (actual time=0.036..0.039 rows=0 loops=1)
        Group Key: tv.libelle
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1153.52..2015.68 rows=4949 width=32) (actual time=0.034..0.036 rows=0 loops=1)
              Hash Cond: (f_1.id_fiche = f.id_fiche)
              ->  Hash Join  (cost=247.52..1096.69 rows=4949 width=48) (actual time=0.033..0.035 rows=0 loops=1)
                    Hash Cond: (f_1.id_type_voile = tv.id_type_voile)
                    ->  Nested Loop  (cost=225.33..1061.38 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (never executed)
                                Group Key: correspondances.id_fiche
                                ->  CTE Scan on correspondances  (cost=0.00..200.04 rows=10002 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_1  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=16.12..16.12 rows=485 width=40) (actual time=0.003..0.004 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on type_voile tv  (cost=0.00..16.12 rows=485 width=40) (actual time=0.003..0.003 rows=0 loops=1)
                                Filter: ((libelle IS NOT NULL) AND (libelle <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2036.76..2039.26 rows=200 width=68) (actual time=0.017..0.019 rows=0 loops=1)
        Group Key: c.nom
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1149.79..2011.98 rows=4956 width=32) (actual time=0.016..0.018 rows=0 loops=1)
              Hash Cond: (f_3.id_fiche = f_2.id_fiche)
              ->  Hash Join  (cost=243.79..1092.97 rows=4956 width=48) (actual time=0.016..0.017 rows=0 loops=1)
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
  ->  HashAggregate  (cost=2073.20..2076.70 rows=200 width=68) (actual time=0.012..0.015 rows=0 loops=1)
        Group Key: ((b.nom || ' '::text) || COALESCE(b.taille, ''::text))
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1161.56..2048.46 rows=4948 width=32) (actual time=0.011..0.014 rows=0 loops=1)
              Hash Cond: (f_5.id_fiche = f_4.id_fiche)
              ->  Hash Join  (cost=255.56..1104.73 rows=4948 width=80) (actual time=0.011..0.013 rows=0 loops=1)
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
  ->  HashAggregate  (cost=2005.92..2005.94 rows=2 width=45) (actual time=36.748..36.751 rows=2 loops=1)
        Group Key: f_7.gamme
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=44970
        ->  Hash Join  (cost=1131.33..1980.92 rows=5000 width=9) (actual time=12.503..34.913 rows=10000 loops=1)
              Hash Cond: (f_7.id_fiche = f_6.id_fiche)
              Buffers: shared hit=44970
              ->  Nested Loop  (cost=225.33..1061.79 rows=5000 width=25) (actual time=10.228..30.715 rows=10000 loops=1)
                    Buffers: shared hit=44289
                    ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (actual time=10.210..11.264 rows=10000 loops=1)
                          Group Key: correspondances_3.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          Buffers: shared hit=681
                          ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.04 rows=10002 width=8) (actual time=5.384..8.146 rows=10000 loops=1)
                                Buffers: shared hit=681
                    ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..4.95 rows=1 width=41) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=2.262..2.262 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_6  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.105..1.195 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  HashAggregate  (cost=2030.50..2043.73 rows=756 width=68) (actual time=32.223..32.228 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=44289
        ->  Hash Join  (cost=1131.33..2005.50 rows=5000 width=32) (actual time=4.712..30.298 rows=10000 loops=1)
              Hash Cond: (f_9.id_fiche = f_8.id_fiche)
              Buffers: shared hit=44289
              ->  Nested Loop  (cost=225.33..1061.38 rows=5000 width=20) (actual time=2.476..23.390 rows=10000 loops=1)
                    Buffers: shared hit=43608
                    ->  HashAggregate  (cost=225.04..227.04 rows=200 width=8) (actual time=2.464..3.658 rows=10000 loops=1)
                          Group Key: correspondances_4.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.04 rows=10002 width=8) (actual time=0.001..0.532 rows=10000 loops=1)
                    ->  Index Scan using fiche_pkey on fiche f_9  (cost=0.29..4.95 rows=1 width=36) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_4.id_fiche)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=2.217..2.217 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_8  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.070..1.099 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  GroupAggregate  (cost=1284.82..1290.32 rows=275 width=68) (actual time=0.055..0.057 rows=0 loops=1)
        Group Key: m.nom
        ->  Sort  (cost=1284.82..1285.51 rows=275 width=40) (actual time=0.054..0.055 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              ->  Hash Join  (cost=252.49..1273.68 rows=275 width=40) (actual time=0.035..0.036 rows=0 loops=1)
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
                    ->  Hash  (cost=15.50..15.50 rows=550 width=40) (actual time=0.005..0.006 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on materiau m  (cost=0.00..15.50 rows=550 width=40) (actual time=0.005..0.005 rows=0 loops=1)
Planning:
  Buffers: shared hit=135
Planning Time: 4.112 ms
Execution Time: 69.471 ms
```

### Rang 3 — observation 67.133 ms

```text
Append  (cost=3047.03..12513.76 rows=1633 width=68) (actual time=35.827..68.657 rows=9 loops=1)
  Buffers: shared hit=89261
  CTE correspondances
    ->  HashAggregate  (cost=906.55..1006.58 rows=10003 width=8) (actual time=4.803..5.726 rows=10000 loops=1)
          Group Key: f_12.id_fiche
          Batches: 1  Memory Usage: 913kB
          Buffers: shared hit=683
          ->  Append  (cost=0.00..881.55 rows=10003 width=8) (actual time=0.074..3.135 rows=10000 loops=1)
                Buffers: shared hit=683
                ->  Seq Scan on fiche f_12  (cost=0.00..806.00 rows=10000 width=8) (actual time=0.074..2.581 rows=10000 loops=1)
                      Filter: ((search_vector IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
                      Buffers: shared hit=681
                ->  Bitmap Heap Scan on chunk c_1  (cost=8.56..13.91 rows=2 width=8) (actual time=0.007..0.008 rows=0 loops=1)
                      Recheck Cond: (tsv @@ '''voile'''::tsquery)
                      Filter: (id_fiche IS NOT NULL)
                      Buffers: shared hit=2
                      ->  Bitmap Index Scan on idx_chunk_tsv  (cost=0.00..8.56 rows=2 width=0) (actual time=0.005..0.006 rows=0 loops=1)
                            Index Cond: (tsv @@ '''voile'''::tsquery)
                            Buffers: shared hit=2
                ->  Seq Scan on documents d  (cost=0.00..11.62 rows=1 width=8) (actual time=0.002..0.003 rows=0 loops=1)
                      Filter: ((id_fiche IS NOT NULL) AND (search_vector @@ '''voile'''::tsquery))
  ->  HashAggregate  (cost=2040.45..2042.95 rows=200 width=68) (actual time=0.008..0.011 rows=0 loops=1)
        Group Key: tv.libelle
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1153.54..2015.70 rows=4949 width=32) (actual time=0.007..0.010 rows=0 loops=1)
              Hash Cond: (f_1.id_fiche = f.id_fiche)
              ->  Hash Join  (cost=247.54..1096.71 rows=4949 width=48) (actual time=0.006..0.008 rows=0 loops=1)
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
  ->  HashAggregate  (cost=2036.78..2039.28 rows=200 width=68) (actual time=0.005..0.007 rows=0 loops=1)
        Group Key: c.nom
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1149.82..2012.00 rows=4956 width=32) (actual time=0.004..0.006 rows=0 loops=1)
              Hash Cond: (f_3.id_fiche = f_2.id_fiche)
              ->  Hash Join  (cost=243.81..1092.99 rows=4956 width=48) (actual time=0.004..0.006 rows=0 loops=1)
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
  ->  HashAggregate  (cost=2073.22..2076.72 rows=200 width=68) (actual time=0.003..0.006 rows=0 loops=1)
        Group Key: ((b.nom || ' '::text) || COALESCE(b.taille, ''::text))
        Batches: 1  Memory Usage: 40kB
        ->  Hash Join  (cost=1161.58..2048.48 rows=4948 width=32) (actual time=0.003..0.005 rows=0 loops=1)
              Hash Cond: (f_5.id_fiche = f_4.id_fiche)
              ->  Hash Join  (cost=255.58..1104.75 rows=4948 width=80) (actual time=0.003..0.004 rows=0 loops=1)
                    Hash Cond: (f_5.id_bateau = b.id_bateau)
                    ->  Nested Loop  (cost=225.35..1061.40 rows=5000 width=24) (never executed)
                          ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (never executed)
                                Group Key: correspondances_2.id_fiche
                                ->  CTE Scan on correspondances correspondances_2  (cost=0.00..200.06 rows=10003 width=8) (never executed)
                          ->  Index Scan using fiche_pkey on fiche f_5  (cost=0.29..4.95 rows=1 width=32) (never executed)
                                Index Cond: (id_fiche = correspondances_2.id_fiche)
                                Filter: (statut = ANY ('{valide,a_valider}'::text[]))
                    ->  Hash  (cost=23.05..23.05 rows=574 width=72) (actual time=0.001..0.002 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on bateau b  (cost=0.00..23.05 rows=574 width=72) (actual time=0.001..0.001 rows=0 loops=1)
                                Filter: ((((nom || ' '::text) || COALESCE(taille, ''::text)) IS NOT NULL) AND (((nom || ' '::text) || COALESCE(taille, ''::text)) <> ''::text))
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (never executed)
                    ->  Seq Scan on fiche f_4  (cost=0.00..781.00 rows=10000 width=8) (never executed)
  ->  HashAggregate  (cost=2005.94..2005.97 rows=2 width=45) (actual time=35.809..35.812 rows=2 loops=1)
        Group Key: f_7.gamme
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=44972
        ->  Hash Join  (cost=1131.35..1980.94 rows=5000 width=9) (actual time=11.057..33.832 rows=10000 loops=1)
              Hash Cond: (f_7.id_fiche = f_6.id_fiche)
              Buffers: shared hit=44972
              ->  Nested Loop  (cost=225.35..1061.82 rows=5000 width=25) (actual time=8.929..29.766 rows=10000 loops=1)
                    Buffers: shared hit=44291
                    ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=8.913..10.026 rows=10000 loops=1)
                          Group Key: correspondances_3.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          Buffers: shared hit=683
                          ->  CTE Scan on correspondances correspondances_3  (cost=0.00..200.06 rows=10003 width=8) (actual time=4.805..7.287 rows=10000 loops=1)
                                Buffers: shared hit=683
                    ->  Index Scan using fiche_pkey on fiche f_7  (cost=0.29..4.95 rows=1 width=41) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_3.id_fiche)
                          Filter: ((gamme IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])) AND (gamme <> ''::text))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=2.113..2.114 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_6  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.080..1.148 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  HashAggregate  (cost=2030.53..2043.76 rows=756 width=68) (actual time=32.780..32.786 rows=7 loops=1)
        Group Key: to_char((f_9.date_edition)::timestamp with time zone, 'YYYY'::text)
        Batches: 1  Memory Usage: 49kB
        Buffers: shared hit=44289
        ->  Hash Join  (cost=1131.35..2005.53 rows=5000 width=32) (actual time=5.688..30.791 rows=10000 loops=1)
              Hash Cond: (f_9.id_fiche = f_8.id_fiche)
              Buffers: shared hit=44289
              ->  Nested Loop  (cost=225.35..1061.40 rows=5000 width=20) (actual time=3.589..24.317 rows=10000 loops=1)
                    Buffers: shared hit=43608
                    ->  HashAggregate  (cost=225.07..227.07 rows=200 width=8) (actual time=3.569..4.757 rows=10000 loops=1)
                          Group Key: correspondances_4.id_fiche
                          Batches: 1  Memory Usage: 929kB
                          ->  CTE Scan on correspondances correspondances_4  (cost=0.00..200.06 rows=10003 width=8) (actual time=0.001..0.905 rows=10000 loops=1)
                    ->  Index Scan using fiche_pkey on fiche f_9  (cost=0.29..4.95 rows=1 width=36) (actual time=0.001..0.001 rows=1 loops=10000)
                          Index Cond: (id_fiche = correspondances_4.id_fiche)
                          Filter: ((date_edition IS NOT NULL) AND (statut = ANY ('{valide,a_valider}'::text[])))
                          Buffers: shared hit=36804
              ->  Hash  (cost=781.00..781.00 rows=10000 width=8) (actual time=2.075..2.076 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 519kB
                    Buffers: shared hit=681
                    ->  Seq Scan on fiche f_8  (cost=0.00..781.00 rows=10000 width=8) (actual time=0.071..1.087 rows=10000 loops=1)
                          Buffers: shared hit=681
  ->  GroupAggregate  (cost=1284.84..1290.34 rows=275 width=68) (actual time=0.024..0.025 rows=0 loops=1)
        Group Key: m.nom
        ->  Sort  (cost=1284.84..1285.53 rows=275 width=40) (actual time=0.023..0.024 rows=0 loops=1)
              Sort Key: m.nom, f_11.id_fiche
              Sort Method: quicksort  Memory: 25kB
              ->  Hash Join  (cost=252.51..1273.70 rows=275 width=40) (actual time=0.012..0.013 rows=0 loops=1)
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
                    ->  Hash  (cost=15.50..15.50 rows=550 width=40) (actual time=0.006..0.006 rows=0 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 8kB
                          ->  Seq Scan on materiau m  (cost=0.00..15.50 rows=550 width=40) (actual time=0.006..0.006 rows=0 loops=1)
Planning:
  Buffers: shared hit=135
Planning Time: 3.299 ms
Execution Time: 68.976 ms
```

Toutes les données de ce rapport sont **SYNTHÉTIQUES**; aucun benchmark de VPS01/R2 ou mesure opérateur n’est impliqué.
