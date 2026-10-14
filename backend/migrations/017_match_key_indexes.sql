-- 017_match_key_indexes.sql — comparison-layer normalization support (Option A).
-- Run AFTER 016_location_source_ids_and_nitttrc_alias.sql. Additive and safe to re-run.
--
-- What this is: btree indexes over the SQL form of the shared match key —
--   REGEXP_REPLACE(LOWER(<name>), '[^a-z0-9]+', '', 'g')
-- (see app/services/location_sql.py::fold_sql). Lowering comes FIRST: the
-- class ``[^a-z0-9]`` would otherwise eat uppercase letters. The Python
-- matcher (location_resolver.normalize) additionally folds non-ASCII via
-- NFKD; the SQL form is exact for ASCII input, which covers the domain's
-- campus and manufacturer data. Direction of any residual difference is
-- safe: two names equal under the SQL fold are always equal under
-- normalize(), so a fold-backed duplicate check can never reject names the
-- matcher treats as distinct — it only misses accent-only variants
-- (reported as AMBIGUOUS by the resolver, never silently merged).
--
-- What this is NOT: no stored value is rewritten, no uniqueness is added
-- here (that is 018, after the remediation report), and the resolver itself
-- is untouched (it already normalizes in memory). These indexes back the
-- campus CRUD scoped duplicate checks and any future equality predicate on
-- the match key.
--
-- Rollback: DROP INDEX IF EXISTS <name below>; the application runs without
-- them (statement budgets and behavior are unchanged either way).

CREATE INDEX IF NOT EXISTS idx_blocks_match_key
    ON blocks (college_id, (REGEXP_REPLACE(LOWER(block_name), '[^a-z0-9]+', '', 'g')));
CREATE INDEX IF NOT EXISTS idx_floors_match_key
    ON floors (block_id, (REGEXP_REPLACE(LOWER(floor_name), '[^a-z0-9]+', '', 'g')));
CREATE INDEX IF NOT EXISTS idx_rooms_match_key
    ON rooms (floor_id, (REGEXP_REPLACE(LOWER(room_name), '[^a-z0-9]+', '', 'g')));
CREATE INDEX IF NOT EXISTS idx_departments_match_key
    ON departments ((REGEXP_REPLACE(LOWER(department_name), '[^a-z0-9]+', '', 'g')));
CREATE INDEX IF NOT EXISTS idx_colleges_match_key
    ON colleges ((REGEXP_REPLACE(LOWER(college_name), '[^a-z0-9]+', '', 'g')));

-- Trigram index for the manufacturer substring filters (`ac_make_company`
-- ILIKE '%…%'), which no btree can serve. Same guard idiom as 007: when
-- pg_trgm is unavailable the filters still work, just without the index.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pg_trgm not available, skipping manufacturer trigram index: %', SQLERRM;
    END;
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        EXECUTE $idx$
            CREATE INDEX IF NOT EXISTS idx_acs_make_trgm ON acs USING gin
                (ac_make_company gin_trgm_ops)
        $idx$;
    END IF;
END $$;
