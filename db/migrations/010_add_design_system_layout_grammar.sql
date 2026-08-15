-- Store the region grammar read from a template's own pages: rails, ground
-- splits, band rows, card rows and banners, with fractional geometry. Without
-- it the grammar is read at upload, used for one run, and lost - so every
-- later deck from the same template composes into placeholder boxes again.
ALTER TABLE design_system_bundles ADD COLUMN IF NOT EXISTS layout_grammar JSONB NOT NULL DEFAULT '{}';
