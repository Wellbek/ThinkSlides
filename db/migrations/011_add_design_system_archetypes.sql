-- Store the named archetypes read from a template's pages and the chapter it
-- repeats them in. A template like an analyst report is one chapter run once
-- per subject; without this the pattern is found at upload and lost, and every
-- deck from that template is planned slide by slide from scratch.
ALTER TABLE design_system_bundles ADD COLUMN IF NOT EXISTS archetypes JSONB NOT NULL DEFAULT '{}';
