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

CREATE TABLE IF NOT EXISTS remediation_plans (
  id VARCHAR(64) PRIMARY KEY,
  incident_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(255) NOT NULL,
  cluster VARCHAR(255) NOT NULL,
  namespace VARCHAR(255) NOT NULL,
  deployment VARCHAR(255) NOT NULL,
  action_type VARCHAR(64) NOT NULL,
  status VARCHAR(64) NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS orchestration_jobs (
  id VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  idempotency_key VARCHAR(128),
  request_fingerprint VARCHAR(64) NOT NULL,
  status VARCHAR(24) NOT NULL,
  cluster VARCHAR(255) NOT NULL,
  incident_id VARCHAR(64) NOT NULL,
  actor VARCHAR(255) NOT NULL,
  payload JSONB NOT NULL,
  result JSONB,
  error TEXT,
  error_type VARCHAR(255),
  submitted_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  lease_owner VARCHAR(128),
  lease_expires_at TIMESTAMPTZ,
  progress_sequence INTEGER NOT NULL DEFAULT 0,
  CONSTRAINT orchestration_jobs_tenant_idem_uq UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS orchestration_jobs_claim_idx
  ON orchestration_jobs(status, lease_expires_at, submitted_at);
CREATE INDEX IF NOT EXISTS orchestration_jobs_tenant_created_idx
  ON orchestration_jobs(tenant_id, submitted_at);

CREATE TABLE IF NOT EXISTS orchestration_progress_events (
  job_id VARCHAR(64) NOT NULL REFERENCES orchestration_jobs(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  tenant_id VARCHAR(255) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (job_id, sequence)
);
CREATE INDEX IF NOT EXISTS orchestration_progress_events_created_idx
  ON orchestration_progress_events(job_id, sequence);

CREATE INDEX IF NOT EXISTS idx_remediation_tenant_created
  ON remediation_plans(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_remediation_incident
  ON remediation_plans(incident_id, tenant_id);

CREATE INDEX IF NOT EXISTS idx_investigations_alert_name ON investigations(alert_name);
CREATE INDEX IF NOT EXISTS idx_investigations_created_at ON investigations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_investigations_tenant ON investigations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_investigations_entity ON investigations(cluster, namespace, pod);
CREATE INDEX IF NOT EXISTS idx_audit_incident_tenant ON audit_events(incident_id, tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS investigations_embedding_hnsw
  ON investigations USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS alert_occurrences (
  id VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  cluster VARCHAR(255) NOT NULL,
  namespace VARCHAR(255),
  target_kind VARCHAR(32) NOT NULL,
  target VARCHAR(255) NOT NULL,
  group_key VARCHAR(64) NOT NULL,
  group_id VARCHAR(64) NOT NULL,
  alert_name VARCHAR(255) NOT NULL,
  severity VARCHAR(64) NOT NULL,
  fingerprint VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  starts_at TIMESTAMPTZ NOT NULL,
  ends_at TIMESTAMPTZ,
  received_at TIMESTAMPTZ NOT NULL,
  last_received_at TIMESTAMPTZ NOT NULL,
  notifications INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT uq_alert_occurrence_identity UNIQUE (tenant_id, cluster, fingerprint, starts_at)
);
CREATE INDEX IF NOT EXISTS idx_alert_occurrences_group ON alert_occurrences(tenant_id, group_key, starts_at);
CREATE INDEX IF NOT EXISTS idx_alert_occurrences_seen ON alert_occurrences(tenant_id, last_received_at);
CREATE INDEX IF NOT EXISTS ix_alert_occurrences_group_id ON alert_occurrences(group_id);
