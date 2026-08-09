-- Store table/chart styling tokens (chart color sequence, axis/grid colors,
-- table header/band colors, font) derived once during design-system
-- extraction, so generated tables/charts can be styled from the template's
-- own brand colors instead of a generic default.
ALTER TABLE design_system_bundles ADD COLUMN IF NOT EXISTS data_viz JSONB NOT NULL DEFAULT '{}';
