CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS investigations (
  id VARCHAR(64) PRIMARY KEY,
  status VARCHAR(32) NOT NULL,
  tenant_id VARCHAR(255) NOT NULL DEFAULT 'default',
  title TEXT NOT NULL,
  alert_name VARCHAR(255) NOT NULL,
  cluster VARCHAR(255) NOT NULL,
  namespace VARCHAR(255),
  pod VARCHAR(255),
  root_cause TEXT NOT NULL,
  fingerprint_text TEXT NOT NULL,
  embedding vector(128) NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
  id VARCHAR(64) PRIMARY KEY,
  incident_id VARCHAR(64),
  tenant_id VARCHAR(255) NOT NULL,
  actor VARCHAR(255) NOT NULL,
  action VARCHAR(255) NOT NULL,
  detail JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_investigations_alert_name ON investigations(alert_name);
CREATE INDEX IF NOT EXISTS idx_investigations_created_at ON investigations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_investigations_tenant ON investigations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_investigations_entity ON investigations(cluster, namespace, pod);
CREATE INDEX IF NOT EXISTS idx_audit_incident_tenant ON audit_events(incident_id, tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS investigations_embedding_hnsw
  ON investigations USING hnsw (embedding vector_cosine_ops);
