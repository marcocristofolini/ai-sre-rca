CREATE TABLE IF NOT EXISTS investigations (
  id VARCHAR(64) PRIMARY KEY,
  status VARCHAR(32) NOT NULL,
  title TEXT NOT NULL,
  alert_name VARCHAR(255) NOT NULL,
  cluster VARCHAR(255) NOT NULL,
  namespace VARCHAR(255),
  pod VARCHAR(255),
  root_cause TEXT NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_investigations_alert_name ON investigations(alert_name);
CREATE INDEX IF NOT EXISTS idx_investigations_created_at ON investigations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_investigations_entity ON investigations(cluster, namespace, pod);
