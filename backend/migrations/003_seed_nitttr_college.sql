-- 003_seed_nitttr_college.sql — NON-DESTRUCTIVE, idempotent (Plan #26).
--
-- Why: colleges was created empty by 001, and campus_import.py never populated
-- it, leaving blocks.college_id NULL. The real source of institution data is
-- Frontend/src/lib/collegeConfig.ts (verified against https://nitttrc.ac.in/
-- on 2026-09-21). This deployment is a single-institute system (NITTTR Chennai).

INSERT INTO colleges (college_id, college_name, created_at, updated_at)
VALUES (
    'NITTTR-CHENNAI',
    'National Institute of Technical Teachers Training and Research',
    NOW(),
    NOW()
)
ON CONFLICT (college_id) DO NOTHING;

-- Link orphaned blocks to the single college of this deployment.
-- Guarded: only when exactly one college exists, so this stays correct if the
-- database ever becomes multi-college (then blocks must be assigned explicitly).
DO $$
BEGIN
    IF (SELECT COUNT(*) FROM colleges) = 1 THEN
        UPDATE blocks
        SET college_id = (SELECT college_id FROM colleges LIMIT 1),
            updated_at = NOW()
        WHERE college_id IS NULL;
    END IF;
END $$;
