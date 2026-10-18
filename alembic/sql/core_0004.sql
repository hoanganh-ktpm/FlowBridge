-- SPDX-License-Identifier: GPL-3.0-or-later
-- Durable admin operations; canonical Markdown remains in core.document_versions.
SET LOCAL lock_timeout = '5s';
CREATE TABLE core.knowledge_files (
  document_id uuid PRIMARY KEY REFERENCES core.documents ON DELETE CASCADE,
  filename text NOT NULL UNIQUE,
  CHECK (filename ~ '^[^/\\]+\.md$' AND length(filename) BETWEEN 4 AND 160)
);
-- Adopt the existing single-document-per-supplier identity. No content is copied
-- from the repository; existing DB versions are already the canonical source.
INSERT INTO core.knowledge_files(document_id,filename)
SELECT d.document_id,
  CASE WHEN v.metadata->>'supplier_code' ~ '^[A-Za-z0-9_-]{1,150}$'
       THEN (v.metadata->>'supplier_code')||'.md'
       ELSE d.document_id::text||'.md' END
FROM core.documents d
LEFT JOIN LATERAL (SELECT metadata FROM core.document_versions
  WHERE document_id=d.document_id ORDER BY version_no DESC LIMIT 1) v ON true
WHERE d.document_kind='supplier_review' AND d.subject_type='supplier' AND d.deleted_at IS NULL;
CREATE TABLE core.knowledge_jobs (
  job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('publish','reindex','delete','withdraw')),
  expected_revision integer NOT NULL CHECK (expected_revision >= 0),
  actor_user_id uuid NOT NULL REFERENCES public.users(user_id),
  actor_identity jsonb NOT NULL,
  idempotency_key text NOT NULL,
  request_hash text NOT NULL,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','processing','ready','failed')),
  stage text NOT NULL DEFAULT 'pending',
  lease_id uuid,
  lease_until timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  result jsonb,
  error_code text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (actor_user_id,idempotency_key)
);
CREATE UNIQUE INDEX knowledge_jobs_one_active ON core.knowledge_jobs(document_id)
  WHERE status IN ('pending','processing');
CREATE INDEX knowledge_jobs_poll ON core.knowledge_jobs(status,lease_until,created_at);
ALTER TABLE core.knowledge_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.knowledge_jobs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.knowledge_files,core.knowledge_jobs FROM PUBLIC;
DO $$ DECLARE r text; BEGIN
  FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=r) THEN
      EXECUTE format('REVOKE ALL ON core.knowledge_files,core.knowledge_jobs FROM %I',r);
    END IF;
  END LOOP;
END $$;

-- Reports/candidates are the persisted citation owners. Match IDs, version IDs or
-- chunk IDs even in older reference layouts; a false positive safely blocks purge.
CREATE FUNCTION core.knowledge_is_referenced(target uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, core, public AS $$
  WITH ids AS (
    SELECT target::text AS id
    UNION ALL SELECT version_id::text FROM core.document_versions WHERE document_id=target
    UNION ALL SELECT chunk_id::text FROM core.document_chunks c
      JOIN core.document_versions v USING(version_id) WHERE v.document_id=target
  ), evidence AS (
    SELECT report_data::text AS body FROM public.risk_analysis_reports
    UNION ALL SELECT evidence_refs::text FROM public.analysis_supplier_candidates
      WHERE evidence_refs IS NOT NULL
  ) SELECT EXISTS(SELECT 1 FROM evidence,ids WHERE strpos(body,id)>0)
$$;

-- Fence report creation against concurrent purge. Existing evidence is untouched.
CREATE FUNCTION core.guard_knowledge_citations() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog,core,public AS $$
DECLARE body text; ref record;
BEGIN
  body := CASE WHEN TG_TABLE_NAME='risk_analysis_reports' THEN (to_jsonb(NEW)->'report_data')::text
               ELSE (to_jsonb(NEW)->'evidence_refs')::text END;
  -- The same lock order is used by the manager's purge transaction.
  FOR ref IN SELECT document_id,deleted_at FROM core.documents
    WHERE strpos(body,document_id::text)>0
       OR document_id IN (SELECT v.document_id FROM core.document_versions v
          WHERE strpos(body,v.version_id::text)>0)
    ORDER BY document_id FOR SHARE
  LOOP
    IF ref.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Knowledge source deleted'; END IF;
  END LOOP;
  -- Core references must resolve; a purge cannot be followed by a stale writer.
  FOR ref IN SELECT value #>> '{}' AS id FROM jsonb_path_query(
      CASE WHEN TG_TABLE_NAME='risk_analysis_reports' THEN to_jsonb(NEW)->'report_data'
           ELSE COALESCE(to_jsonb(NEW)->'evidence_refs','[]'::jsonb) END,
      '$.**.document_id') AS value
  LOOP
    IF body LIKE '%core-document://%' AND NOT EXISTS (
      SELECT 1 FROM core.documents WHERE document_id::text=ref.id AND deleted_at IS NULL
    ) THEN RAISE EXCEPTION 'Knowledge source unavailable'; END IF;
  END LOOP;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_report_knowledge_citations BEFORE INSERT OR UPDATE OF report_data
  ON public.risk_analysis_reports FOR EACH ROW EXECUTE FUNCTION core.guard_knowledge_citations();
CREATE TRIGGER trg_candidate_knowledge_citations BEFORE INSERT OR UPDATE OF evidence_refs
  ON public.analysis_supplier_candidates FOR EACH ROW EXECUTE FUNCTION core.guard_knowledge_citations();

-- Published versions still cannot be edited. Purge is only allowed after the
-- manager tombstones and locks a document with no persisted business citations.
CREATE OR REPLACE FUNCTION core.guard_published_document_versions()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, core AS $$
DECLARE stamp timestamptz := clock_timestamp();
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.status<>'draft' AND NOT (
      EXISTS(SELECT 1 FROM core.documents WHERE document_id=OLD.document_id
        AND deleted_at IS NOT NULL)
      AND NOT core.knowledge_is_referenced(OLD.document_id)
    ) THEN RAISE EXCEPTION 'Published evidence is retained'; END IF;
    RETURN OLD;
  END IF;
  IF TG_OP='UPDATE' AND OLD.status IN ('published','withdrawn') THEN
    IF (to_jsonb(NEW)-ARRAY['status','withdrawn_at']) IS DISTINCT FROM
       (to_jsonb(OLD)-ARRAY['status','withdrawn_at']) THEN
      RAISE EXCEPTION 'Published source versions are immutable';
    END IF;
    IF NEW.status=OLD.status AND NEW.withdrawn_at IS NOT DISTINCT FROM OLD.withdrawn_at
      THEN RETURN NEW; END IF;
    IF OLD.status='published' AND NEW.status='withdrawn' THEN
      NEW.withdrawn_at:=stamp; RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Invalid source status transition';
  END IF;
  IF NEW.status='withdrawn' THEN RAISE EXCEPTION 'Publish before withdrawal'; END IF;
  IF NEW.status='published' THEN
    NEW.published_at:=stamp; NEW.known_from:=stamp; NEW.withdrawn_at:=NULL;
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION core.knowledge_is_referenced(uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION core.guard_knowledge_citations() FROM PUBLIC;
