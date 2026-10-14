-- supabase_auth_trigger.sql — Phase 1.3
-- NOT run by `python -m app.migrate` (the `auth` schema only exists on the
-- Supabase side). Run this by hand in the Supabase SQL editor.
--
-- Purpose: every new Supabase Auth signup gets a `profiles` row with role
-- VIEWER and status APPROVED (automatic onboarding — no routine approver
-- step). The role is NEVER taken from signup metadata; only a non-privileged
-- requested role (TECHNICIAN/VIEWER) is recorded for a later elevation
-- decision, anything else is dropped to NULL.
--
-- The Flask fallback `POST /api/v1/auth/register-profile` provides the same
-- guarantee when triggers cannot be used (both paths are idempotent and the
-- registration outcome is identical).

CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id, email, name, role, status, approved_at, requested_role)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'name', NEW.raw_user_meta_data->>'full_name'),
        'VIEWER',          -- role is assigned by policy/admins, never by the client
        'APPROVED',        -- automatic onboarding: verified identity, least privilege
        NOW(),
        CASE WHEN NULLIF(NEW.raw_user_meta_data->>'requested_role', '') IN ('TECHNICIAN', 'VIEWER')
             THEN NEW.raw_user_meta_data->>'requested_role'
        END
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();
