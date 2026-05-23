-- 004: Fix registration and prepare for flexible org membership
--
-- 1. Allow users without an organisation (future: invite flow, SSO onboarding).
-- 2. Enforce email uniqueness globally instead of per-org.
-- 3. Do NOT touch organisations.slug — it stays NOT NULL; the handler
--    will auto-generate slugs for personal workspaces and dev orgs.

-- Make organisation_id optional on users (future: users can exist before joining an org)
ALTER TABLE users ALTER COLUMN organisation_id DROP NOT NULL;

-- Replace per-org email uniqueness with global email uniqueness
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_organisation_id_email_key;
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);