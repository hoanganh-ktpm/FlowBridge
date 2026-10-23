"""Single-enterprise Core P0 schema; business data and credentials stay outside Core."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class CoreBase(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


CORE_TABLE_PRIORITIES = {
    "principals": "P0",
    "departments": "P0",
    "department_memberships": "P0",
    "applications": "P0",
    "data_sources": "P0",
    "data_products": "P0",
    "data_contract_versions": "P0",
    "access_requests": "P0",
    "access_grants": "P0",
    "publication_jobs": "P0",
    "audit_events": "P0",
    "core_command_receipts": "P0",
}


principals = sa.Table(
    "principals",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("issuer", sa.Text, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("display_name", sa.Text, nullable=True),
    sa.Column("service_client_id", sa.Text, nullable=True),
    sa.Column(
        "application_id",
        sa.Uuid,
        sa.ForeignKey(
            "public.applications.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_principal_application",
        ),
        nullable=True,
    ),
    sa.Column("platform_role", sa.Text, nullable=False, server_default="NONE"),
    sa.Column("status", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "created_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "updated_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    sa.CheckConstraint("kind IN ('human', 'service')", name="kind"),
    sa.CheckConstraint("platform_role IN ('NONE', 'PLATFORM_ADMIN')", name="platform_role"),
    sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="status"),
    sa.UniqueConstraint("issuer", "subject"),
    sa.CheckConstraint(
        "(kind = 'human' AND service_client_id IS NULL AND application_id IS NULL) OR (kind = 'service' AND service_client_id IS NOT NULL AND btrim(service_client_id) <> '')",
        name="identity_binding",
    ),
    sa.CheckConstraint("btrim(issuer) <> ''", name="issuer_nonblank"),
    sa.CheckConstraint("btrim(subject) <> ''", name="subject_nonblank"),
    sa.CheckConstraint("btrim(kind) <> ''", name="kind_nonblank"),
    sa.CheckConstraint("btrim(platform_role) <> ''", name="platform_role_nonblank"),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    sa.CheckConstraint("revision >= 1", name="revision"),
    schema="public",
)
sa.Index(
    "uq_principals_service_client",
    principals.c.issuer,
    principals.c.service_client_id,
    unique=True,
    postgresql_where=sa.text("kind = 'service'"),
)


departments = sa.Table(
    "departments",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("code", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("keycloak_group_ref", sa.Text, nullable=True),
    sa.Column("status", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "created_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "updated_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    sa.UniqueConstraint("code"),
    sa.CheckConstraint("code = lower(btrim(code)) AND code <> ''", name="code_normalized"),
    sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="status"),
    sa.CheckConstraint("btrim(code) <> ''", name="code_nonblank"),
    sa.CheckConstraint("btrim(name) <> ''", name="name_nonblank"),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    sa.CheckConstraint("revision >= 1", name="revision"),
    schema="public",
)


department_memberships = sa.Table(
    "department_memberships",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "principal_id",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "department_id",
        sa.Uuid,
        sa.ForeignKey("public.departments.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("membership_role", sa.Text, nullable=False, server_default="STAFF"),
    sa.Column("status", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "created_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "updated_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    sa.UniqueConstraint("principal_id", "department_id"),
    sa.CheckConstraint("membership_role IN ('STAFF', 'OWNER')", name="membership_role"),
    sa.CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="status"),
    sa.CheckConstraint("btrim(membership_role) <> ''", name="membership_role_nonblank"),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    sa.CheckConstraint("revision >= 1", name="revision"),
    schema="public",
)
sa.Index(
    "ix_membership_department_role",
    department_memberships.c.department_id,
    department_memberships.c.membership_role,
    department_memberships.c.status,
)


applications = sa.Table(
    "applications",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "department_id",
        sa.Uuid,
        sa.ForeignKey("public.departments.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("app_key", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("business_api_base_url", sa.Text, nullable=True),
    sa.Column("status", sa.Text, nullable=False, server_default="DRAFT"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "created_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "updated_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    sa.UniqueConstraint("app_key"),
    sa.CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'DISABLED')", name="status"),
    sa.CheckConstraint("btrim(app_key) <> ''", name="app_key_nonblank"),
    sa.CheckConstraint("btrim(name) <> ''", name="name_nonblank"),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    sa.CheckConstraint("revision >= 1", name="revision"),
    schema="public",
)
sa.Index("ix_application_department_status", applications.c.department_id, applications.c.status)


data_sources = sa.Table(
    "data_sources",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "application_id",
        sa.Uuid,
        sa.ForeignKey("public.applications.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("source_key", sa.Text, nullable=False),
    sa.Column("db_type", sa.Text, nullable=False, server_default="POSTGRESQL"),
    sa.Column("host", sa.Text, nullable=False),
    sa.Column("port", sa.Integer, nullable=False, server_default=sa.text("5432")),
    sa.Column("database_name", sa.Text, nullable=False),
    sa.Column("db_user", sa.Text, nullable=False),
    sa.Column("secret_ref", sa.Text, nullable=False),
    sa.Column("ssl_mode", sa.Text, nullable=False, server_default="verify-full"),
    sa.Column("hasura_source_name", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="DRAFT"),
    sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "created_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "updated_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    sa.UniqueConstraint("source_key"),
    sa.UniqueConstraint("hasura_source_name"),
    sa.CheckConstraint("db_type IN ('POSTGRESQL')", name="db_type"),
    sa.CheckConstraint("ssl_mode IN ('require', 'verify-ca', 'verify-full')", name="ssl_mode"),
    sa.CheckConstraint(
        "status IN ('DRAFT', 'VALIDATING', 'CONNECTED', 'FAILED', 'DISABLED')", name="status"
    ),
    sa.CheckConstraint("port BETWEEN 1 AND 65535", name="port"),
    sa.CheckConstraint("btrim(source_key) <> ''", name="source_key_nonblank"),
    sa.CheckConstraint("btrim(db_type) <> ''", name="db_type_nonblank"),
    sa.CheckConstraint("btrim(host) <> ''", name="host_nonblank"),
    sa.CheckConstraint("btrim(database_name) <> ''", name="database_name_nonblank"),
    sa.CheckConstraint("btrim(db_user) <> ''", name="db_user_nonblank"),
    sa.CheckConstraint("btrim(secret_ref) <> ''", name="secret_ref_nonblank"),
    sa.CheckConstraint("btrim(ssl_mode) <> ''", name="ssl_mode_nonblank"),
    sa.CheckConstraint("btrim(hasura_source_name) <> ''", name="hasura_source_name_nonblank"),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    sa.CheckConstraint("revision >= 1", name="revision"),
    schema="public",
)
sa.Index("ix_source_application_status", data_sources.c.application_id, data_sources.c.status)


data_products = sa.Table(
    "data_products",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "data_source_id",
        sa.Uuid,
        sa.ForeignKey("public.data_sources.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("product_key", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("schema_name", sa.Text, nullable=False),
    sa.Column("object_name", sa.Text, nullable=False),
    sa.Column("object_type", sa.Text, nullable=False),
    sa.Column("classification", sa.Text, nullable=False, server_default="INTERNAL"),
    sa.Column(
        "active_contract_id",
        sa.Uuid,
        nullable=True,
    ),
    sa.Column("status", sa.Text, nullable=False, server_default="DRAFT"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "created_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "updated_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    sa.UniqueConstraint("product_key"),
    sa.UniqueConstraint("data_source_id", "schema_name", "object_name"),
    sa.CheckConstraint(
        "status IN ('DRAFT', 'READY', 'PROVISIONING', 'ACTIVE', 'ERROR', 'DISABLED')", name="status"
    ),
    sa.CheckConstraint(
        "classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL')", name="classification"
    ),
    sa.CheckConstraint("object_type IN ('TABLE', 'VIEW')", name="object_type"),
    sa.CheckConstraint(
        "status <> 'ACTIVE' OR active_contract_id IS NOT NULL", name="active_contract"
    ),
    sa.ForeignKeyConstraint(
        ["active_contract_id", "id"],
        ["public.data_contract_versions.id", "public.data_contract_versions.data_product_id"],
        ondelete="RESTRICT",
        use_alter=True,
        name="fk_product_active_contract",
    ),
    sa.CheckConstraint("btrim(product_key) <> ''", name="product_key_nonblank"),
    sa.CheckConstraint("btrim(name) <> ''", name="name_nonblank"),
    sa.CheckConstraint("btrim(schema_name) <> ''", name="schema_name_nonblank"),
    sa.CheckConstraint("btrim(object_name) <> ''", name="object_name_nonblank"),
    sa.CheckConstraint("btrim(object_type) <> ''", name="object_type_nonblank"),
    sa.CheckConstraint("btrim(classification) <> ''", name="classification_nonblank"),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    sa.CheckConstraint("revision >= 1", name="revision"),
    schema="public",
)


data_contract_versions = sa.Table(
    "data_contract_versions",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "data_product_id",
        sa.Uuid,
        sa.ForeignKey("public.data_products.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("version", sa.Text, nullable=False),
    sa.Column("contract_json", JSONB, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="DRAFT"),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "published_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "created_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "updated_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    sa.UniqueConstraint("data_product_id", "version"),
    sa.UniqueConstraint("id", "data_product_id"),
    sa.CheckConstraint("status IN ('DRAFT', 'PUBLISHED', 'RETIRED')", name="status"),
    sa.CheckConstraint(
        "status NOT IN ('PUBLISHED','RETIRED') OR (published_at IS NOT NULL AND published_by IS NOT NULL)",
        name="published_evidence",
    ),
    sa.CheckConstraint(
        "version ~ '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'", name="semantic_version"
    ),
    sa.CheckConstraint("btrim(version) <> ''", name="version_nonblank"),
    sa.CheckConstraint(
        "contract_json IS NULL OR jsonb_typeof(contract_json) = 'object'",
        name="contract_json_object",
    ),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    sa.CheckConstraint("revision >= 1", name="revision"),
    schema="public",
)


access_requests = sa.Table(
    "access_requests",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "data_product_id",
        sa.Uuid,
        sa.ForeignKey("public.data_products.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "contract_version_id",
        sa.Uuid,
        nullable=False,
    ),
    sa.Column(
        "requested_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "consumer_department_id",
        sa.Uuid,
        sa.ForeignKey("public.departments.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "consumer_principal_id",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("requested_scope_json", JSONB, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="PENDING"),
    sa.Column(
        "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "decided_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("decision_note", sa.Text, nullable=True),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "created_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "updated_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.ForeignKeyConstraint(
        ["contract_version_id", "data_product_id"],
        ["public.data_contract_versions.id", "public.data_contract_versions.data_product_id"],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "consumer_department_id IS NOT NULL AND consumer_principal_id IS NULL", name="consumer_p0"
    ),
    sa.CheckConstraint(
        "requested_scope_json ?& ARRAY['action','columns','row_scope'] AND requested_scope_json->>'action' = 'SELECT' AND jsonb_typeof(requested_scope_json->'columns') = 'array' AND jsonb_array_length(requested_scope_json->'columns') BETWEEN 1 AND 100 AND requested_scope_json->'row_scope' = '{\"kind\":\"ALL\"}'::jsonb",
        name="select_scope",
    ),
    sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')", name="status"),
    sa.CheckConstraint(
        "status NOT IN ('APPROVED','REJECTED') OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)",
        name="decision",
    ),
    sa.CheckConstraint(
        "requested_scope_json IS NULL OR jsonb_typeof(requested_scope_json) = 'object'",
        name="requested_scope_json_object",
    ),
    sa.CheckConstraint("btrim(reason) <> ''", name="reason_nonblank"),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    sa.CheckConstraint("revision >= 1", name="revision"),
    schema="public",
)


access_grants = sa.Table(
    "access_grants",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "data_product_id",
        sa.Uuid,
        sa.ForeignKey("public.data_products.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "contract_version_id",
        sa.Uuid,
        nullable=False,
    ),
    sa.Column(
        "access_request_id",
        sa.Uuid,
        sa.ForeignKey("public.access_requests.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "consumer_department_id",
        sa.Uuid,
        sa.ForeignKey("public.departments.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "consumer_principal_id",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("scope_json", JSONB, nullable=False),
    sa.Column("enforcement_role", sa.Text, nullable=False),
    sa.Column("desired_revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    sa.Column("applied_revision", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("status", sa.Text, nullable=False, server_default="PENDING_APPLY"),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "granted_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "revoked_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(
        ["contract_version_id", "data_product_id"],
        ["public.data_contract_versions.id", "public.data_contract_versions.data_product_id"],
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "consumer_department_id IS NOT NULL AND consumer_principal_id IS NULL", name="consumer_p0"
    ),
    sa.CheckConstraint(
        "scope_json ?& ARRAY['action','columns','row_scope'] AND scope_json->>'action' = 'SELECT' AND jsonb_typeof(scope_json->'columns') = 'array' AND jsonb_array_length(scope_json->'columns') BETWEEN 1 AND 100 AND scope_json->'row_scope' = '{\"kind\":\"ALL\"}'::jsonb",
        name="select_scope",
    ),
    sa.UniqueConstraint("access_request_id"),
    sa.CheckConstraint(
        "status IN ('PENDING_APPLY', 'ACTIVE', 'REVOKING', 'REVOKED', 'EXPIRED', 'ERROR')",
        name="status",
    ),
    sa.CheckConstraint(
        "desired_revision >= 1 AND applied_revision >= 0 AND applied_revision <= desired_revision",
        name="revision_bounds",
    ),
    sa.CheckConstraint(
        "status <> 'ACTIVE' OR applied_revision = desired_revision", name="active_applied"
    ),
    sa.CheckConstraint("(revoked_by IS NULL) = (revoked_at IS NULL)", name="revocation"),
    sa.CheckConstraint(
        "scope_json IS NULL OR jsonb_typeof(scope_json) = 'object'", name="scope_json_object"
    ),
    sa.CheckConstraint("btrim(enforcement_role) <> ''", name="enforcement_role_nonblank"),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    schema="public",
)
sa.Index(
    "uq_live_department_grant",
    access_grants.c.data_product_id,
    access_grants.c.consumer_department_id,
    unique=True,
    postgresql_where=sa.text("status NOT IN ('REVOKED','EXPIRED')"),
)


publication_jobs = sa.Table(
    "publication_jobs",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("job_type", sa.Text, nullable=False),
    sa.Column(
        "data_source_id",
        sa.Uuid,
        sa.ForeignKey("public.data_sources.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "data_product_id",
        sa.Uuid,
        sa.ForeignKey("public.data_products.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "access_grant_id",
        sa.Uuid,
        sa.ForeignKey("public.access_grants.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("target_revision", sa.BigInteger, nullable=False),
    sa.Column("idempotency_key", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="QUEUED"),
    sa.Column("attempt_count", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("max_attempts", sa.Integer, nullable=False, server_default=sa.text("5")),
    sa.Column(
        "next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("locked_by", sa.Text, nullable=True),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_error_code", sa.Text, nullable=True),
    sa.Column("last_error_message", sa.Text, nullable=True),
    sa.Column(
        "requested_by",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("correlation_id", sa.Text, nullable=False),
    sa.CheckConstraint(
        "job_type IN ('CONNECT_SOURCE', 'PUBLISH_PRODUCT', 'APPLY_GRANT', 'REVOKE_GRANT', 'DISABLE_PRODUCT')",
        name="job_type",
    ),
    sa.CheckConstraint(
        "status IN ('QUEUED', 'RUNNING', 'RETRY', 'SUCCEEDED', 'FAILED')", name="status"
    ),
    sa.UniqueConstraint("idempotency_key"),
    sa.CheckConstraint(
        "(job_type = 'CONNECT_SOURCE' AND data_source_id IS NOT NULL AND data_product_id IS NULL AND access_grant_id IS NULL) OR (job_type IN ('PUBLISH_PRODUCT','DISABLE_PRODUCT') AND data_source_id IS NULL AND data_product_id IS NOT NULL AND access_grant_id IS NULL) OR (job_type IN ('APPLY_GRANT','REVOKE_GRANT') AND data_source_id IS NULL AND data_product_id IS NULL AND access_grant_id IS NOT NULL)",
        name="target",
    ),
    sa.CheckConstraint(
        "target_revision >= 1 AND max_attempts >= 1 AND attempt_count BETWEEN 0 AND max_attempts",
        name="attempts",
    ),
    sa.CheckConstraint("(locked_by IS NULL) = (lease_expires_at IS NULL)", name="lease_pair"),
    sa.CheckConstraint(
        "status <> 'RUNNING' OR (locked_by IS NOT NULL AND lease_expires_at IS NOT NULL)",
        name="running_lease",
    ),
    sa.CheckConstraint("btrim(job_type) <> ''", name="job_type_nonblank"),
    sa.CheckConstraint("btrim(idempotency_key) <> ''", name="idempotency_key_nonblank"),
    sa.CheckConstraint("btrim(status) <> ''", name="status_nonblank"),
    sa.CheckConstraint("btrim(correlation_id) <> ''", name="correlation_id_nonblank"),
    schema="public",
)
sa.Index("ix_job_due", publication_jobs.c.status, publication_jobs.c.next_attempt_at)
sa.Index("ix_job_lease", publication_jobs.c.status, publication_jobs.c.lease_expires_at)
sa.Index(
    "uq_job_source_revision",
    publication_jobs.c.job_type,
    publication_jobs.c.data_source_id,
    publication_jobs.c.target_revision,
    unique=True,
    postgresql_where=sa.text("data_source_id IS NOT NULL"),
)
sa.Index(
    "uq_job_product_revision",
    publication_jobs.c.job_type,
    publication_jobs.c.data_product_id,
    publication_jobs.c.target_revision,
    unique=True,
    postgresql_where=sa.text("data_product_id IS NOT NULL"),
)
sa.Index(
    "uq_job_grant_revision",
    publication_jobs.c.job_type,
    publication_jobs.c.access_grant_id,
    publication_jobs.c.target_revision,
    unique=True,
    postgresql_where=sa.text("access_grant_id IS NOT NULL"),
)


audit_events = sa.Table(
    "audit_events",
    CoreBase.metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("event_key", sa.Text, nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "actor_principal_id",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("actor_kind", sa.Text, nullable=False),
    sa.Column(
        "initiator_principal_id",
        sa.Uuid,
        sa.ForeignKey("public.principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("resource_type", sa.Text, nullable=False),
    sa.Column("resource_ref", sa.Text, nullable=False),
    sa.Column("outcome", sa.Text, nullable=False),
    sa.Column("request_id", sa.Text, nullable=False),
    sa.Column("workflow_run_ref", sa.Text, nullable=True),
    sa.Column("diff_json", JSONB, nullable=True),
    sa.Column("error_code", sa.Text, nullable=True),
    sa.Column("content_hash", sa.Text, nullable=True),
    sa.CheckConstraint("actor_kind IN ('human', 'service', 'system')", name="actor_kind"),
    sa.CheckConstraint("outcome IN ('SUCCESS', 'FAILURE', 'DENIED')", name="outcome"),
    sa.UniqueConstraint("event_key"),
    sa.CheckConstraint(
        "(actor_kind = 'system' AND actor_principal_id IS NULL) OR (actor_kind IN ('human','service') AND actor_principal_id IS NOT NULL)",
        name="actor",
    ),
    sa.CheckConstraint("btrim(actor_kind) <> ''", name="actor_kind_nonblank"),
    sa.CheckConstraint("btrim(action) <> ''", name="action_nonblank"),
    sa.CheckConstraint("btrim(resource_type) <> ''", name="resource_type_nonblank"),
    sa.CheckConstraint("btrim(resource_ref) <> ''", name="resource_ref_nonblank"),
    sa.CheckConstraint("btrim(outcome) <> ''", name="outcome_nonblank"),
    sa.CheckConstraint("btrim(request_id) <> ''", name="request_id_nonblank"),
    sa.CheckConstraint(
        "diff_json IS NULL OR jsonb_typeof(diff_json) = 'object'", name="diff_json_object"
    ),
    sa.CheckConstraint(
        "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"
    ),
    schema="public",
)
sa.Index("ix_audit_request", audit_events.c.request_id)
sa.Index(
    "ix_audit_resource_time",
    audit_events.c.resource_type,
    audit_events.c.resource_ref,
    audit_events.c.occurred_at,
)


core_command_receipts = sa.Table(
    "core_command_receipts",
    CoreBase.metadata,
    sa.Column("namespace", sa.Text, primary_key=True),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("status", sa.Integer, nullable=False),
    sa.Column("body", JSONB, nullable=False),
    sa.Column("location", sa.Text, nullable=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status BETWEEN 100 AND 599", name="status"),
    sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint"),
    sa.CheckConstraint("expires_at >= created_at + interval '7 days'", name="retention"),
    sa.CheckConstraint("btrim(namespace) <> ''", name="namespace_nonblank"),
    sa.CheckConstraint("btrim(fingerprint) <> ''", name="fingerprint_nonblank"),
    sa.CheckConstraint("body IS NULL OR jsonb_typeof(body) = 'object'", name="body_object"),
    schema="public",
)
sa.Index("ix_receipt_expiry", core_command_receipts.c.expires_at)
