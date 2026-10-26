-- Idempotent bootstrap, also run explicitly on existing local volumes.
\set ON_ERROR_STOP on
\getenv postgres_password POSTGRES_PASSWORD
\getenv sales_password SALES_PASSWORD
\getenv warehouse_password WAREHOUSE_PASSWORD
\getenv logistics_password LOGISTICS_PASSWORD
\getenv core_password CORE_PASSWORD
\getenv metadata_password METADATA_PASSWORD
\getenv reader_password READER_PASSWORD
SELECT format('CREATE ROLE %I LOGIN', role_name)
FROM unnest(ARRAY['sales_owner','warehouse_owner','logistics_owner','core_owner',
                 'hasura_owner','sales_reader','warehouse_reader','logistics_reader']) AS role_name
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) \gexec
ALTER ROLE flowbridge_admin PASSWORD :'postgres_password';
ALTER ROLE sales_owner PASSWORD :'sales_password';
ALTER ROLE warehouse_owner PASSWORD :'warehouse_password';
ALTER ROLE logistics_owner PASSWORD :'logistics_password';
ALTER ROLE core_owner PASSWORD :'core_password';
ALTER ROLE hasura_owner PASSWORD :'metadata_password';
ALTER ROLE sales_reader PASSWORD :'reader_password';
ALTER ROLE warehouse_reader PASSWORD :'reader_password';
ALTER ROLE logistics_reader PASSWORD :'reader_password';
SELECT format('CREATE DATABASE %I OWNER %I', db, owner_role)
FROM (VALUES ('sales_db','sales_owner'),('warehouse_db','warehouse_owner'),
             ('logistics_db','logistics_owner'),('dxos_core_db','core_owner'),
             ('hasura_metadata_db','hasura_owner')) AS targets(db,owner_role)
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname=db) \gexec
REVOKE ALL ON DATABASE sales_db, warehouse_db, logistics_db, dxos_core_db, hasura_metadata_db FROM PUBLIC;
GRANT CONNECT ON DATABASE sales_db TO sales_reader;
GRANT CONNECT ON DATABASE warehouse_db TO warehouse_reader;
GRANT CONNECT ON DATABASE logistics_db TO logistics_reader;
-- Readers receive SELECT on approved product views explicitly after migrations.
-- No blanket table grants or default SELECT privileges.
