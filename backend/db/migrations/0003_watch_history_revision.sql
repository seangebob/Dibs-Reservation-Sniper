-- The live repository's fencing revision orders projection writes across
-- workers. -1 marks pre-revision rows and legacy callers.
ALTER TABLE watch_history ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT -1;
