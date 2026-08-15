-- Store the layout survey of a template's own pages: which pages look alike,
-- which one represents each group, and which were shown to the vision model.
-- Without it the survey is computed at upload, used for one extraction, and
-- lost - so nothing downstream can say which archetypes the template has.
ALTER TABLE design_system_bundles ADD COLUMN IF NOT EXISTS page_survey JSONB NOT NULL DEFAULT '{}';
