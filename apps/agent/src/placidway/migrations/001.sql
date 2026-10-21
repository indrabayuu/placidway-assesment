CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS settings(key text PRIMARY KEY, value text NOT NULL);
CREATE TABLE IF NOT EXISTS source_versions(
    version uuid NOT NULL, source_id integer NOT NULL, url text NOT NULL, title text NOT NULL,
    content_hash text NOT NULL, diagnostics jsonb NOT NULL DEFAULT '[]', fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(version,source_id)
);
CREATE TABLE IF NOT EXISTS evidence(
    version uuid NOT NULL, id text NOT NULL, source_id integer NOT NULL, payload jsonb NOT NULL,
    PRIMARY KEY(version,id), FOREIGN KEY(version,source_id) REFERENCES source_versions(version,source_id)
);
CREATE TABLE IF NOT EXISTS chunks(
    version uuid NOT NULL, id text NOT NULL, evidence_id text NOT NULL, text text NOT NULL,
    embedding vector(384) NOT NULL, PRIMARY KEY(version,id),
    FOREIGN KEY(version,evidence_id) REFERENCES evidence(version,id)
);
CREATE TABLE IF NOT EXISTS price_facts(
    version uuid NOT NULL, id text NOT NULL, evidence_id text NOT NULL, payload jsonb NOT NULL,
    PRIMARY KEY(version,id), FOREIGN KEY(version,evidence_id) REFERENCES evidence(version,id)
);
CREATE TABLE IF NOT EXISTS ingestion_runs(
    id uuid PRIMARY KEY, status text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz, version uuid, error text, diagnostics jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS principals(
    id text PRIMARY KEY CHECK(id IN ('owner','client')), secret_hash text NOT NULL, role text NOT NULL, name text NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
    token_hash text PRIMARY KEY, principal_id text NOT NULL REFERENCES principals(id),
    expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS conversations(
    id uuid PRIMARY KEY, principal_id text NOT NULL REFERENCES principals(id), title text NOT NULL DEFAULT 'New conversation',
    response_language text NOT NULL DEFAULT 'en', purpose text NOT NULL DEFAULT 'chat',
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_owner_idx ON conversations(principal_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS runs(
    id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES conversations(id), client_message_id uuid NOT NULL,
    input text NOT NULL, response_language text NOT NULL, status text NOT NULL DEFAULT 'queued',
    created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, error text, result jsonb,
    UNIQUE(conversation_id,client_message_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_conversation ON runs(conversation_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS run_events(
    id bigserial PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs(id), event text NOT NULL, data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_events_replay_idx ON run_events(run_id,id);
INSERT INTO schema_migrations(version) VALUES(1) ON CONFLICT DO NOTHING;
