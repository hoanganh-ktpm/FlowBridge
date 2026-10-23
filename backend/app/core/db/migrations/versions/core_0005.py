"""Add governed relationships and reuse publication jobs without resetting metadata."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "core_0005"
down_revision = "core_0004"
branch_labels = None
depends_on = None

OLD_TYPES = "'CONNECT_SOURCE','PUBLISH_PRODUCT','APPLY_GRANT','REVOKE_GRANT','DISABLE_PRODUCT'"
NEW_TYPES = OLD_TYPES + ",'PUBLISH_RELATIONSHIP','DISABLE_RELATIONSHIP'"
OLD_TARGET = "(job_type = 'CONNECT_SOURCE' AND data_source_id IS NOT NULL AND data_product_id IS NULL AND access_grant_id IS NULL) OR (job_type IN ('PUBLISH_PRODUCT','DISABLE_PRODUCT') AND data_source_id IS NULL AND data_product_id IS NOT NULL AND access_grant_id IS NULL) OR (job_type IN ('APPLY_GRANT','REVOKE_GRANT') AND data_source_id IS NULL AND data_product_id IS NULL AND access_grant_id IS NOT NULL)"


def upgrade():
    op.create_table(
        "data_product_relationships",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("source_product_id", sa.Uuid, sa.ForeignKey("public.data_products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_product_id", sa.Uuid, sa.ForeignKey("public.data_products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_contract_id", sa.Uuid, nullable=False),
        sa.Column("target_contract_id", sa.Uuid, nullable=False),
        sa.Column("relationship_type", sa.Text, nullable=False),
        sa.Column("field_mapping", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="DRAFT"),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("applied_revision", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("last_error_code", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("public.principals.id", ondelete="RESTRICT")),
        sa.Column("updated_by", sa.Uuid, sa.ForeignKey("public.principals.id", ondelete="RESTRICT")),
        sa.ForeignKeyConstraint(["source_contract_id", "source_product_id"], ["public.data_contract_versions.id", "public.data_contract_versions.data_product_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_contract_id", "target_product_id"], ["public.data_contract_versions.id", "public.data_contract_versions.data_product_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_product_id", "name"),
        sa.CheckConstraint("name ~ '^[a-z_][a-z0-9_]{0,62}$' AND left(name, 2) <> '__'", name="name"),
        sa.CheckConstraint("relationship_type IN ('object','array')", name="relationship_type"),
        sa.CheckConstraint("jsonb_typeof(field_mapping) = 'object' AND field_mapping <> '{}'::jsonb", name="mapping"),
        sa.CheckConstraint("status IN ('DRAFT','PROVISIONING','ACTIVE','DISABLING','DISABLED','ERROR')", name="status"),
        sa.CheckConstraint("revision >= 1 AND applied_revision BETWEEN 0 AND revision", name="revision"),
        sa.CheckConstraint("status <> 'ACTIVE' OR applied_revision = revision", name="active_applied"),
        schema="public",
    )
    op.create_index("ix_relationship_target", "data_product_relationships", ["target_product_id"], schema="public")
    op.add_column("publication_jobs", sa.Column("relationship_id", sa.Uuid, nullable=True), schema="public")
    op.create_foreign_key("fk_publication_jobs_relationship_id_data_product_relationships", "publication_jobs", "data_product_relationships", ["relationship_id"], ["id"], source_schema="public", referent_schema="public", ondelete="RESTRICT")
    op.drop_constraint("ck_publication_jobs_job_type", "publication_jobs", schema="public", type_="check")
    op.create_check_constraint("job_type", "publication_jobs", "job_type IN (" + NEW_TYPES + ")", schema="public")
    op.drop_constraint("ck_publication_jobs_target", "publication_jobs", schema="public", type_="check")
    op.create_check_constraint("target", "publication_jobs", "((" + OLD_TARGET + ") AND relationship_id IS NULL) OR (job_type IN ('PUBLISH_RELATIONSHIP','DISABLE_RELATIONSHIP') AND relationship_id IS NOT NULL AND data_source_id IS NULL AND data_product_id IS NULL AND access_grant_id IS NULL)", schema="public")
    op.create_index("uq_job_relationship_revision", "publication_jobs", ["job_type", "relationship_id", "target_revision"], unique=True, schema="public", postgresql_where=sa.text("relationship_id IS NOT NULL"))


def downgrade():
    schema = op.get_context().opts.get("dxos_schema", "public")
    quoted = op.get_context().dialect.identifier_preparer.quote_schema(schema)
    jobs = quoted + ".publication_jobs"
    relationships = quoted + ".data_product_relationships"
    op.execute("LOCK TABLE " + jobs + ", " + relationships + " IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM " + jobs + " WHERE relationship_id IS NOT NULL) OR EXISTS (SELECT 1 FROM " + relationships + ") THEN RAISE EXCEPTION 'Remove relationship resources through an approved retention procedure before downgrade'; END IF; END $$")
    op.drop_index("uq_job_relationship_revision", table_name="publication_jobs", schema="public")
    op.drop_constraint("ck_publication_jobs_target", "publication_jobs", schema="public", type_="check")
    op.drop_constraint("ck_publication_jobs_job_type", "publication_jobs", schema="public", type_="check")
    op.create_check_constraint("target", "publication_jobs", OLD_TARGET, schema="public")
    op.create_check_constraint("job_type", "publication_jobs", "job_type IN (" + OLD_TYPES + ")", schema="public")
    op.drop_column("publication_jobs", "relationship_id", schema="public")
    op.drop_table("data_product_relationships", schema="public")
