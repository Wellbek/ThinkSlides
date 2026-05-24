-- 004: Prepare for flexible organisation membership
--
-- 1. Allow organisations without a slug (auto-created personal workspaces,
--    dev orgs; slug will come from Azure Entra ID / SSO later).
-- 2. Allow users without an organisation (future: invite flow, SSO onboarding).
-- 3. Enforce email uniqueness globally instead of per-org.

-- Make slug optional on organisations
ALTER TABLE organisations ALTER COLUMN slug DROP NOT NULL;

-- Make organisation_id optional on users
ALTER TABLE users ALTER COLUMN organisation_id DROP NOT NULL;

-- Replace per-org email uniqueness with global email uniqueness
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_organisation_id_email_key;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'users_email_key'
  ) THEN
    ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
  END IF;
END$$;
