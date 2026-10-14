# Performance notes — dashboards, reports, audit (Phase 7)

_Measured 2026-10-07 15:12 UTC by `backend/scripts/seed_perf.py` (`seed` → `bench --runs 40` → `clean`) against Postgres 16 in the throwaway `acms-test` container (Docker, localhost:5433)._

Plan requirement (phase-7 task 2): *each report answers **< 300 ms** on **10k assets**, checked with `EXPLAIN ANALYZE`.*

## Dataset

| table | rows |
|---|---:|
| `acs` | 10,000 |
| `maintenance_work_orders` | 8,000 |
| `audit_logs` | 40,000 |
| `rooms` | 480 |
| `blocks` | 8 |
| `acs (all)` | 10,020 |
| `maintenance_work_orders (all)` | 8,000 |
| `audit_logs (all)` | 40,002 |

Seeded synthetic campus: 8 blocks × 6 floors × 10 rooms = 480 rooms,
ACs spread evenly over them, service dates spread ±45…60 days around today,
work orders spread over the last 365 days, audit rows over the last 30 days.
Everything carries a `PERF-` prefix so `scripts/seed_perf.py clean` removes
exactly what `seed` created.

## Result: p50 / p95 through the real HTTP stack

Measured with `app.test_client()` — full request path including auth,
permission check, SQL, envelope and JSON encoding (no network hop).

| endpoint | p50 (ms) | p95 (ms) | max (ms) | < 300 ms? |
|---|---:|---:|---:|:--:|
| `dashboard/summary (admin)` | 67.3 | 81.5 | 145.8 | ✅ |
| `reports/assets-by-block` | 14.1 | 19.9 | 27.8 | ✅ |
| `reports/assets-by-block (block + 1y)` | 33.5 | 46.1 | 51.6 | ✅ |
| `reports/assets-by-type` | 12.2 | 20.3 | 25.3 | ✅ |
| `reports/capacity` | 12.9 | 23.4 | 25.1 | ✅ |
| `reports/due-for-maintenance` | 37.3 | 53.6 | 61.4 | ✅ |
| `reports/maintenance-trend` | 117.6 | 180.7 | 221.0 | ✅ |
| `reports/maintenance-trend (60d + block)` | 54.6 | 88.6 | 104.0 | ✅ |
| `audit (page 1)` | 17.9 | 28.7 | 44.4 | ✅ |
| `audit (entity_type + date range)` | 28.2 | 43.9 | 60.0 | ✅ |
| `audit (actor + action filter)` | 33.0 | 44.8 | 81.6 | ✅ |

## EXPLAIN ANALYZE — the report queries

Built by the same `build_*_query` helpers the endpoints call, so this is
the production SQL, not a copy.

| report query | planning (ms) | execution (ms) | seq scan on a large table? |
|---|---:|---:|:--:|
| `assets-by-block` | 1.1 | 11.0 | ⚠️ yes |
| `assets-by-block (installation window)` | 0.7 | 1.0 | no |
| `assets-by-type` | 0.4 | 3.6 | ⚠️ yes |
| `capacity` | 0.2 | 9.0 | ⚠️ yes |
| `due-for-maintenance` | 1.2 | 7.4 | no |
| `maintenance-trend (rows)` | 0.2 | 0.9 | ⚠️ yes |
| `maintenance-trend (stats)` | 0.2 | 3.8 | ⚠️ yes |
| `maintenance-trend (priority)` | 0.2 | 2.7 | ⚠️ yes |

"Large" = `acs`, `audit_logs`, `maintenance_work_orders` — a seq scan on
the 480-row `rooms`/`floors`/`blocks` side tables is not counted. ⚠️ marks a
full pass over one of the three big tables, which at 10k/40k/8k rows is
still the cheapest correct plan (see below); it becomes a problem past
~100k rows, which is where the date/cursor indexes start doing the work.

### Raw plans

<details><summary><code>assets-by-block</code></summary>

```
Sort  (cost=496.19..496.20 rows=1 width=72) (actual time=10.910..10.916 rows=9 loops=1)
  Sort Key: (count(*)) DESC, (COALESCE(NULLIF(b.block_name, ''::text), b.block_id, 'Unassigned'::text))
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=287
  ->  HashAggregate  (cost=496.17..496.18 rows=1 width=72) (actual time=10.815..10.823 rows=9 loops=1)
        Group Key: COALESCE(NULLIF(b.block_name, ''::text), b.block_id, 'Unassigned'::text)
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=287
        ->  Hash Left Join  (cost=19.97..458.59 rows=5010 width=37) (actual time=0.357..8.634 rows=9620 loops=1)
              Hash Cond: (f.block_id = b.block_id)
              Buffers: shared hit=287
              ->  Hash Left Join  (cost=18.95..425.70 rows=5010 width=17) (actual time=0.319..6.609 rows=9620 loops=1)
                    Hash Cond: (r.floor_id = f.floor_id)
                    Buffers: shared hit=286
                    ->  Hash Left Join  (cost=16.84..409.31 rows=5010 width=20) (actual time=0.279..4.811 rows=9620 loops=1)
                          Hash Cond: (a.room_id = r.room_id)
                          Buffers: shared hit=285
                          ->  Seq Scan on acs a  (cost=0.00..379.20 rows=5010 width=24) (actual time=0.019..2.612 rows=9620 loops=1)
                                Filter: COALESCE(is_active, true)
                                Rows Removed by Filter: 400
                                Buffers: shared hit=279
                          ->  Hash  (cost=10.82..10.82 rows=482 width=33) (actual time=0.250..0.251 rows=482 loops=1)
                                Buckets: 1024  Batches: 1  Memory Usage: 40kB
                                Buffers: shared hit=6
                                ->  Seq Scan on rooms r  (cost=0.00..10.82 rows=482 width=33) (actual time=0.006..0.123 rows=482 loops=1)
                                      Buffers: shared hit=6
                    ->  Hash  (cost=1.49..1.49 rows=49 width=27) (actual time=0.032..0.033 rows=49 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 11kB
                          Buffers: shared hit=1
                          ->  Seq Scan on floors f  (cost=0.00..1.49 rows=49 width=27) (actual time=0.008..0.015 rows=49 loops=1)
                                Buffers: shared hit=1
              ->  Hash  (cost=1.01..1.01 rows=1 width=40) (actual time=0.017..0.018 rows=9 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=1
                    ->  Seq Scan on blocks b  (cost=0.00..1.01 rows=1 width=40) (actual time=0.011..0.012 rows=9 loops=1)
                          Buffers: shared hit=1
Planning:
  Buffers: shared hit=29
Planning Time: 1.130 ms
Execution Time: 10.988 ms
```

</details>

<details><summary><code>assets-by-block (installation window)</code></summary>

```
Sort  (cost=341.45..341.45 rows=1 width=72) (actual time=0.977..0.981 rows=8 loops=1)
  Sort Key: (count(*)) DESC, (COALESCE(NULLIF(b.block_name, ''::text), b.block_id, 'Unassigned'::text))
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=211
  ->  HashAggregate  (cost=341.42..341.44 rows=1 width=72) (actual time=0.968..0.973 rows=8 loops=1)
        Group Key: COALESCE(NULLIF(b.block_name, ''::text), b.block_id, 'Unassigned'::text)
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=211
        ->  Nested Loop Left Join  (cost=35.14..339.97 rows=193 width=37) (actual time=0.240..0.871 rows=369 loops=1)
              Join Filter: (f.block_id = b.block_id)
              Rows Removed by Join Filter: 1687
              Buffers: shared hit=211
              ->  Hash Left Join  (cost=35.14..335.58 rows=193 width=17) (actual time=0.232..0.614 rows=369 loops=1)
                    Hash Cond: (r.floor_id = f.floor_id)
                    Buffers: shared hit=210
                    ->  Hash Left Join  (cost=33.04..332.93 rows=193 width=20) (actual time=0.216..0.540 rows=369 loops=1)
                          Hash Cond: (a.room_id = r.room_id)
                          Buffers: shared hit=209
                          ->  Bitmap Heap Scan on acs a  (cost=16.19..315.58 rows=193 width=24) (actual time=0.114..0.361 rows=369 loops=1)
                                Recheck Cond: ((installation_date >= '2026-04-10'::date) AND (installation_date <= '2026-10-07'::date))
                                Filter: COALESCE(is_active, true)
                                Rows Removed by Filter: 16
                                Heap Blocks: exact=200
                                Buffers: shared hit=203
                                ->  Bitmap Index Scan on idx_acs_installation_date  (cost=0.00..16.14 rows=386 width=0) (actual time=0.088..0.089 rows=385 loops=1)
                                      Index Cond: ((installation_date >= '2026-04-10'::date) AND (installation_date <= '2026-10-07'::date))
                                      Buffers: shared hit=3
                          ->  Hash  (cost=10.82..10.82 rows=482 width=33) (actual time=0.098..0.099 rows=482 loops=1)
                                Buckets: 1024  Batches: 1  Memory Usage: 40kB
                                Buffers: shared hit=6
                                ->  Seq Scan on rooms r  (cost=0.00..10.82 rows=482 width=33) (actual time=0.005..0.041 rows=482 loops=1)
                                      Buffers: shared hit=6
                    ->  Hash  (cost=1.49..1.49 rows=49 width=27) (actual time=0.013..0.013 rows=49 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 11kB
                          Buffers: shared hit=1
                          ->  Seq Scan on floors f  (cost=0.00..1.49 rows=49 width=27) (actual time=0.003..0.006 rows=49 loops=1)
                                Buffers: shared hit=1
              ->  Materialize  (cost=0.00..1.01 rows=1 width=40) (actual time=0.000..0.000 rows=6 loops=369)
                    Buffers: shared hit=1
                    ->  Seq Scan on blocks b  (cost=0.00..1.01 rows=1 width=40) (actual time=0.003..0.004 rows=9 loops=1)
                          Buffers: shared hit=1
Planning:
  Buffers: shared hit=29
Planning Time: 0.674 ms
Execution Time: 1.034 ms
```

</details>

<details><summary><code>assets-by-type</code></summary>

```
Sort  (cost=416.90..416.91 rows=5 width=40) (actual time=3.583..3.585 rows=6 loops=1)
  Sort Key: (count(*)) DESC, (COALESCE(NULLIF(a.ac_type, ''::text), 'Other'::text))
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=279
  ->  HashAggregate  (cost=416.77..416.84 rows=5 width=40) (actual time=3.566..3.568 rows=6 loops=1)
        Group Key: COALESCE(NULLIF(a.ac_type, ''::text), 'Other'::text)
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=279
        ->  Seq Scan on acs a  (cost=0.00..391.72 rows=5010 width=32) (actual time=0.017..2.204 rows=9620 loops=1)
              Filter: COALESCE(is_active, true)
              Rows Removed by Filter: 400
              Buffers: shared hit=279
Planning:
  Buffers: shared hit=3
Planning Time: 0.385 ms
Execution Time: 3.627 ms
```

</details>

<details><summary><code>capacity</code></summary>

```
Sort  (cost=416.96..416.98 rows=7 width=16) (actual time=8.911..8.914 rows=7 loops=1)
  Sort Key: ((a.ac_capacity_ton)::double precision)
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=279
  ->  HashAggregate  (cost=416.77..416.86 rows=7 width=16) (actual time=8.897..8.901 rows=7 loops=1)
        Group Key: (a.ac_capacity_ton)::double precision
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=279
        ->  Seq Scan on acs a  (cost=0.00..391.72 rows=5010 width=8) (actual time=0.024..6.588 rows=9619 loops=1)
              Filter: (COALESCE(is_active, true) AND (ac_capacity_ton IS NOT NULL))
              Rows Removed by Filter: 401
              Buffers: shared hit=279
Planning Time: 0.242 ms
Execution Time: 8.962 ms
```

</details>

<details><summary><code>due-for-maintenance</code></summary>

```
Limit  (cost=495.47..496.72 rows=500 width=118) (actual time=7.289..7.350 rows=500 loops=1)
  Buffers: shared hit=293
  ->  Sort  (cost=495.47..499.12 rows=1461 width=118) (actual time=7.286..7.318 rows=500 loops=1)
        Sort Key: a.next_service_date, a.ac_id
        Sort Method: top-N heapsort  Memory: 175kB
        Buffers: shared hit=293
        ->  Hash Left Join  (cost=89.83..422.67 rows=1461 width=118) (actual time=0.619..4.031 rows=3897 loops=1)
              Hash Cond: (f.block_id = b.block_id)
              Buffers: shared hit=293
              ->  Hash Left Join  (cost=88.81..412.36 rows=1461 width=98) (actual time=0.608..3.301 rows=3897 loops=1)
                    Hash Cond: (r.floor_id = f.floor_id)
                    Buffers: shared hit=292
                    ->  Hash Left Join  (cost=86.71..406.09 rows=1461 width=93) (actual time=0.572..2.597 rows=3897 loops=1)
                          Hash Cond: (a.room_id = r.room_id)
                          Buffers: shared hit=291
                          ->  Bitmap Heap Scan on acs a  (cost=69.86..385.37 rows=1461 width=88) (actual time=0.417..1.704 rows=3897 loops=1)
                                Recheck Cond: ((next_service_date IS NOT NULL) AND (next_service_date >= '2026-10-07'::date))
                                Filter: COALESCE(is_active, true)
                                Rows Removed by Filter: 219
                                Heap Blocks: exact=279
                                Buffers: shared hit=285
                                ->  Bitmap Index Scan on idx_acs_next_service  (cost=0.00..69.50 rows=2921 width=0) (actual time=0.325..0.325 rows=4116 loops=1)
                                      Index Cond: ((next_service_date IS NOT NULL) AND (next_service_date >= '2026-10-07'::date))
                                      Buffers: shared hit=6
                          ->  Hash  (cost=10.82..10.82 rows=482 width=42) (actual time=0.147..0.148 rows=482 loops=1)
                                Buckets: 1024  Batches: 1  Memory Usage: 44kB
                                Buffers: shared hit=6
                                ->  Seq Scan on rooms r  (cost=0.00..10.82 rows=482 width=42) (actual time=0.008..0.049 rows=482 loops=1)
                                      Buffers: shared hit=6
                    ->  Hash  (cost=1.49..1.49 rows=49 width=35) (actual time=0.033..0.034 rows=49 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 12kB
                          Buffers: shared hit=1
                          ->  Seq Scan on floors f  (cost=0.00..1.49 rows=49 width=35) (actual time=0.002..0.007 rows=49 loops=1)
                                Buffers: shared hit=1
              ->  Hash  (cost=1.01..1.01 rows=1 width=40) (actual time=0.007..0.008 rows=9 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=1
                    ->  Seq Scan on blocks b  (cost=0.00..1.01 rows=1 width=40) (actual time=0.004..0.005 rows=9 loops=1)
                          Buffers: shared hit=1
Planning:
  Buffers: shared hit=49
Planning Time: 1.208 ms
Execution Time: 7.429 ms
```

</details>

<details><summary><code>maintenance-trend (rows)</code></summary>

```
Seq Scan on maintenance_work_orders w  (cost=0.00..299.63 rows=2653 width=18) (actual time=0.011..0.798 rows=3970 loops=1)
  Filter: ((COALESCE(scheduled_date, (completed_at)::date) IS NOT NULL) AND (COALESCE(scheduled_date, (completed_at)::date) >= '2026-04-10'::date))
  Rows Removed by Filter: 4030
  Buffers: shared hit=153
Planning Time: 0.173 ms
Execution Time: 0.935 ms
```

</details>

<details><summary><code>maintenance-trend (stats)</code></summary>

```
Aggregate  (cost=579.71..579.72 rows=1 width=104) (actual time=3.763..3.764 rows=1 loops=1)
  Buffers: shared hit=153
  ->  Seq Scan on maintenance_work_orders w  (cost=0.00..273.00 rows=2667 width=22) (actual time=0.007..0.817 rows=3970 loops=1)
        Filter: (COALESCE(scheduled_date, (completed_at)::date) >= '2026-04-10'::date)
        Rows Removed by Filter: 4030
        Buffers: shared hit=153
Planning Time: 0.209 ms
Execution Time: 3.796 ms
```

</details>

<details><summary><code>maintenance-trend (priority)</code></summary>

```
HashAggregate  (cost=293.00..293.05 rows=4 width=40) (actual time=2.640..2.642 rows=4 loops=1)
  Group Key: COALESCE(NULLIF(w.priority, ''::text), 'MEDIUM'::text)
  Batches: 1  Memory Usage: 24kB
  Buffers: shared hit=153
  ->  Seq Scan on maintenance_work_orders w  (cost=0.00..279.67 rows=2667 width=32) (actual time=0.015..1.661 rows=3970 loops=1)
        Filter: (COALESCE(scheduled_date, (completed_at)::date) >= '2026-04-10'::date)
        Rows Removed by Filter: 4030
        Buffers: shared hit=153
Planning Time: 0.247 ms
Execution Time: 2.682 ms
```

</details>

## Why this stays fast

- **`acs` (10k rows)** — the report queries are grouped aggregates over a
  single table plus three LEFT JOINs on primary keys (`rooms.room_id`,
  `floors.floor_id`, `blocks.block_id`). 10k rows fit in memory, so a seq
  scan + hash join is *the* right plan; an index would be slower here.
- **`?block=` filter** narrows to one block before the GROUP BY, which is
  what the block-scoped variants rely on.
- **`?from/?to` on `installation_date`** — `idx_acs_installation_date`
  (012) covers the range, which had no index at all before Phase 7. A narrow
  window now plans a bitmap index scan instead of reading the whole table.
- **`?from/?to` on `next_service_date`** — `idx_acs_next_service` (002).
- **`maintenance_trend`** buckets `COALESCE(scheduled_date, completed_at::date)`;
  the `?from/?to` window bounds the scan to ≤ 24 months of work orders, and
  `idx_wo_scheduled` (002) covers `maintenance_work_orders.scheduled_date`.
- **`audit_logs` (40k+ rows)** — `idx_audit_created` (002) plus
  `idx_audit_actor` / `idx_audit_action` (011) cover the three access paths;
  keyset (cursor) pagination walks `(created_at DESC, id DESC)` so page N
  costs the same as page 1 instead of `OFFSET`'s O(N) scan.

## Reproducing

```
cd backend
$env:TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5433/postgres"
python scripts/seed_perf.py seed --assets 10000 --work-orders 8000 --audit 40000
python scripts/seed_perf.py bench --runs 40 --out ../docs/perf.md
python scripts/seed_perf.py clean
```

`seed` is deterministic (`random.Random(20261007)`) and idempotent — it
always starts by clearing previous `PERF-` rows.
