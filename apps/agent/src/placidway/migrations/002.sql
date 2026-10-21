ALTER TABLE source_versions ADD COLUMN IF NOT EXISTS page_type text NOT NULL DEFAULT 'unknown';
ALTER TABLE source_versions ADD COLUMN IF NOT EXISTS extractor_version text NOT NULL DEFAULT '1';
ALTER TABLE source_versions ADD COLUMN IF NOT EXISTS index_version text NOT NULL DEFAULT 'legacy';
INSERT INTO schema_migrations(version) VALUES(2) ON CONFLICT DO NOTHING;
