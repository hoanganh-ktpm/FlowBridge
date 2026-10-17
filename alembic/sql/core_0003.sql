-- SPDX-License-Identifier: GPL-3.0-or-later
-- FlowBridge; redistribution/modification under GNU GPL v3 or later.
-- Provided without warranty. See LICENSE.
-- Additive Core store. Existing compatible Core tables are adopted, never dropped.
SET LOCAL lock_timeout = '5s';
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
DO $$ BEGIN
  IF (SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
      WHERE e.extname='vector') <> 'extensions' THEN
    RAISE EXCEPTION 'vector must be installed in extensions; review schema before migration';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core.documents (
  document_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_kind text NOT NULL CHECK (btrim(document_kind) <> ''),
  subject_type text NOT NULL CHECK (btrim(subject_type) <> ''),
  subject_id text NOT NULL CHECK (btrim(subject_id) <> ''),
  title text NOT NULL CHECK (btrim(title) <> ''),
  scope_type text NOT NULL CHECK (scope_type IN ('global','department')),
  department_id uuid,
  owner_subject text NOT NULL CHECK (btrim(owner_subject) <> ''),
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  CONSTRAINT core_documents_scope CHECK (
    (scope_type='global' AND department_id IS NULL) OR
    (scope_type='department' AND department_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS documents_supplier_review_live_uq
  ON core.documents(document_kind,subject_type,subject_id)
  WHERE document_kind='supplier_review' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS documents_acl_idx ON core.documents(scope_type,department_id)
  WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS core.document_versions (
  version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES core.documents(document_id) ON DELETE RESTRICT,
  version_no integer NOT NULL CHECK (version_no > 0),
  title_snapshot text NOT NULL CHECK (btrim(title_snapshot) <> ''),
  content_md text NOT NULL,
  checksum_sha256 char(64) NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','withdrawn')),
  metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata)='object'),
  created_by_subject text NOT NULL CHECK (btrim(created_by_subject) <> ''),
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  known_from timestamptz,
  withdrawn_at timestamptz,
  CONSTRAINT document_versions_document_no_uq UNIQUE (document_id,version_no)
);
CREATE INDEX IF NOT EXISTS document_versions_lookup_idx
  ON core.document_versions(document_id,status,known_from DESC);

CREATE TABLE IF NOT EXISTS core.document_chunks (
  chunk_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id uuid NOT NULL REFERENCES core.document_versions(version_id) ON DELETE RESTRICT,
  chunk_no integer NOT NULL CHECK (chunk_no >= 0),
  content text NOT NULL CHECK (length(content)>0),
  heading_path text[],
  source_start_byte integer NOT NULL CHECK (source_start_byte>=0),
  source_end_byte integer NOT NULL CHECK (source_end_byte>source_start_byte),
  source_checksum char(64) NOT NULL CHECK (source_checksum ~ '^[0-9a-f]{64}$'),
  chunk_checksum char(64) NOT NULL CHECK (chunk_checksum ~ '^[0-9a-f]{64}$'),
  embedding extensions.vector(1024) NOT NULL,
  embedding_profile text NOT NULL CHECK (btrim(embedding_profile)<>''),
  index_generation text NOT NULL CHECK (btrim(index_generation)<>''),
  search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig,content)) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT document_chunks_version_generation_no_uq
    UNIQUE (version_id,index_generation,chunk_no)
);
CREATE INDEX IF NOT EXISTS document_chunks_lookup_idx
  ON core.document_chunks(version_id,embedding_profile,index_generation);
CREATE INDEX IF NOT EXISTS document_chunks_search_gin_idx
  ON core.document_chunks USING gin(search_vector);

-- Also validate adopted tables instead of assuming CREATE IF NOT EXISTS upgraded them.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
    WHERE conrelid='core.document_versions'::regclass AND conname='core_versions_state_v1') THEN
    ALTER TABLE core.document_versions ADD CONSTRAINT core_versions_state_v1 CHECK (
      (status='draft' AND published_at IS NULL AND known_from IS NULL AND withdrawn_at IS NULL)
      OR (status='published' AND published_at IS NOT NULL AND known_from IS NOT NULL
          AND withdrawn_at IS NULL AND published_at<=known_from
          AND created_at<=published_at AND btrim(content_md)<>'')
      OR (status='withdrawn' AND published_at IS NOT NULL AND known_from IS NOT NULL
          AND withdrawn_at IS NOT NULL AND created_at<=published_at
          AND published_at<=known_from AND known_from<=withdrawn_at));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
    WHERE conrelid='core.document_versions'::regclass AND conname='core_versions_hash_v1') THEN
    ALTER TABLE core.document_versions ADD CONSTRAINT core_versions_hash_v1 CHECK (
      checksum_sha256=encode(sha256(convert_to(content_md,'UTF8')),'hex'));
  END IF;
END $$;

CREATE OR REPLACE FUNCTION core.guard_published_document_versions()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, core AS $$
DECLARE stamp timestamptz := clock_timestamp();
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.status<>'draft' THEN RAISE EXCEPTION 'Published source versions cannot be deleted'; END IF;
    RETURN OLD;
  END IF;
  IF TG_OP='UPDATE' AND OLD.status IN ('published','withdrawn') THEN
    IF (to_jsonb(NEW)-ARRAY['status','withdrawn_at']) IS DISTINCT FROM
       (to_jsonb(OLD)-ARRAY['status','withdrawn_at']) THEN
      RAISE EXCEPTION 'Published source versions are immutable';
    END IF;
    IF NEW.status=OLD.status AND NEW.withdrawn_at IS NOT DISTINCT FROM OLD.withdrawn_at THEN
      RETURN NEW;
    END IF;
    IF OLD.status='published' AND NEW.status='withdrawn' THEN
      NEW.withdrawn_at:=stamp; RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Invalid source status transition';
  END IF;
  IF NEW.status='withdrawn' THEN RAISE EXCEPTION 'Publish a draft before withdrawal'; END IF;
  IF NEW.status='published' THEN
    -- Online publication cannot backdate knowledge. Historical imports require a separate audit.
    NEW.published_at:=stamp; NEW.known_from:=stamp; NEW.withdrawn_at:=NULL;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_guard_published_document_versions ON core.document_versions;
CREATE TRIGGER trg_guard_published_document_versions
  BEFORE INSERT OR UPDATE OR DELETE ON core.document_versions FOR EACH ROW
  EXECUTE FUNCTION core.guard_published_document_versions();

CREATE OR REPLACE FUNCTION core.guard_document_chunk_source()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, core, extensions AS $$
DECLARE source core.document_versions; raw bytea;
BEGIN
  SELECT * INTO STRICT source FROM core.document_versions WHERE version_id=NEW.version_id;
  raw:=convert_to(source.content_md,'UTF8');
  IF source.status<>'published' OR NEW.source_checksum<>source.checksum_sha256
    OR NEW.source_start_byte<0 OR NEW.source_end_byte<=NEW.source_start_byte
    OR NEW.source_end_byte>octet_length(raw)
    OR NEW.content<>convert_from(substring(raw FROM NEW.source_start_byte+1
       FOR NEW.source_end_byte-NEW.source_start_byte),'UTF8')
    OR NEW.chunk_checksum<>encode(sha256(convert_to(NEW.content,'UTF8')),'hex')
    OR abs(extensions.vector_norm(NEW.embedding)-1.0)>0.0001 THEN
    RAISE EXCEPTION 'Chunk does not match its immutable source or normalized embedding';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_guard_document_chunk_source ON core.document_chunks;
CREATE TRIGGER trg_guard_document_chunk_source BEFORE INSERT OR UPDATE ON core.document_chunks
  FOR EACH ROW EXECUTE FUNCTION core.guard_document_chunk_source();

ALTER TABLE core.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.document_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.document_chunks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON SCHEMA core FROM PUBLIC;
REVOKE ALL ON core.documents, core.document_versions, core.document_chunks FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION core.guard_published_document_versions() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION core.guard_document_chunk_source() FROM PUBLIC;
DO $$ DECLARE role_name text; BEGIN
  FOREACH role_name IN ARRAY ARRAY['anon','authenticated'] LOOP
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
      EXECUTE format('REVOKE ALL ON SCHEMA core FROM %I',role_name);
      EXECUTE format('REVOKE ALL ON core.documents, core.document_versions, core.document_chunks FROM %I',role_name);
    END IF;
  END LOOP;
END $$;
-- No client-facing policies: backend ACL is mandatory; no browser service-role key.
