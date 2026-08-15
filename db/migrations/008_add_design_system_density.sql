-- Store the density budget measured from a template's own rendered pages
-- (words and blocks per content page, the grid shape, words per block), so a
-- deck generated from an uploaded template is written to that template's
-- density rather than to a fixed four-bullet default.
ALTER TABLE design_system_bundles ADD COLUMN IF NOT EXISTS density JSONB NOT NULL DEFAULT '{}';
