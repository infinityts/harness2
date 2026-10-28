"""native_document_rag

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-28
"""
import json
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

from src.skill_compiler import compile_standard_skill_md, estimate_tokens

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

DOC_TYPES = ("README", "ADR", "ARCHITECTURE", "API_SPEC", "RUNBOOK", "REQUIREMENTS", "CHANGELOG", "OTHER")

RAG_SKILL = {
    "slug": "rag-docs-curator",
    "display_name": "Curador de documentación RAG",
    "category": "documents",
    "trigger_description": (
        "Usa esta habilidad cuando la tarea requiera inventariar, limpiar, "
        "ingestar o actualizar documentación canónica en el RAG del proyecto."
    ),
    "objective": (
        "Mantener la documentación del proyecto canónica, sin secretos, "
        "deduplicada e indexada con trazabilidad por fuente."
    ),
    "workflow_steps": [
        "Inventaria README, docs, ADRs, OpenAPI, runbooks y requisitos; identifica fuentes faltantes.",
        "Limpia secretos y PII, y clasifica cada fuente con source_uri estable y doc_type correcto.",
        "Calcula el checksum del contenido limpio; si no cambió, no re-vectorices ni dupliques.",
        "Ingesta o reemplaza el documento con sus chunks y embeddings, verificando el project_id.",
        "Verifica con búsqueda e inventario y registra evidencia, checksum y documentación faltante.",
    ],
    "guardrails_forbidden": [
        "Ingestar secretos, tokens, contraseñas, URLs con credenciales o PII.",
        "Inventar documentación o mezclar contenido de otro proyecto.",
        "Duplicar una fuente con otro source_uri o re-vectorizar si el checksum no cambió.",
        "Escribir en el RAG sin rol Documenter, Redactor o Admin.",
    ],
    "output_criteria": (
        "Entrega fuentes ingestadas/actualizadas/omitidas, checksums, consultas "
        "de verificación y una lista de huecos documentales."
    ),
}


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("is_headless_worker", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_sessions_headless_project "
        "ON agent_sessions(project_id) "
        "WHERE is_headless_worker = true AND status <> 'REVOKED'"
    )

    op.create_table(
        "harness_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_uri", sa.String(500), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("content_checksum", sa.String(64), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_by_session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "source_uri", name="uq_project_doc_source"),
        sa.CheckConstraint("doc_type IN " + str(DOC_TYPES), name="ck_documents_doc_type"),
    )
    op.create_index("idx_documents_project", "harness_documents", ["project_id"])
    op.create_index("idx_documents_type", "harness_documents", ["doc_type"])

    op.create_table(
        "harness_document_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_heading", sa.String(255), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
    )
    op.create_index("idx_document_chunks_project", "harness_document_chunks", ["project_id"])
    op.execute(
        "CREATE INDEX idx_document_chunks_hnsw ON harness_document_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    compiled = compile_standard_skill_md(
        RAG_SKILL["slug"], RAG_SKILL["display_name"], RAG_SKILL["category"],
        RAG_SKILL["trigger_description"], RAG_SKILL["objective"],
        RAG_SKILL["workflow_steps"], RAG_SKILL["guardrails_forbidden"],
        RAG_SKILL["output_criteria"],
    )
    tokens = estimate_tokens(compiled)
    if tokens > 500:
        raise RuntimeError(f"rag-docs-curator excede 500 tokens: {tokens}")
    op.get_bind().execute(
        sa.text("""
            INSERT INTO harness_skills
                (id, slug, display_name, category, trigger_description, objective,
                 workflow_steps, guardrails_forbidden, output_criteria, compiled_skill_md,
                 created_by_user_id, owner_user_id, is_official, status, visibility,
                 version, compiled_tokens)
            VALUES
                (CAST(:id AS UUID), :slug, :display_name, :category, :trigger, :objective,
                 CAST(:steps AS JSONB), CAST(:guardrails AS JSONB), :output, :compiled,
                 NULL, NULL, true, 'PUBLISHED', 'OFFICIAL', 1, :tokens)
            ON CONFLICT (slug) DO NOTHING
        """),
        {
            "id": str(uuid.uuid4()),
            "slug": RAG_SKILL["slug"],
            "display_name": RAG_SKILL["display_name"],
            "category": RAG_SKILL["category"],
            "trigger": RAG_SKILL["trigger_description"],
            "objective": RAG_SKILL["objective"],
            "steps": json.dumps(RAG_SKILL["workflow_steps"], ensure_ascii=False),
            "guardrails": json.dumps(RAG_SKILL["guardrails_forbidden"], ensure_ascii=False),
            "output": RAG_SKILL["output_criteria"],
            "compiled": compiled,
            "tokens": tokens,
        },
    )


def downgrade() -> None:
    op.execute("DELETE FROM harness_skills WHERE slug = 'rag-docs-curator' AND is_official = true")
    op.execute("DROP INDEX IF EXISTS idx_document_chunks_hnsw")
    op.drop_index("idx_document_chunks_project", table_name="harness_document_chunks")
    op.drop_table("harness_document_chunks")
    op.drop_index("idx_documents_type", table_name="harness_documents")
    op.drop_index("idx_documents_project", table_name="harness_documents")
    op.drop_table("harness_documents")
    op.execute("DROP INDEX IF EXISTS uq_agent_sessions_headless_project")
    op.drop_column("agent_sessions", "is_headless_worker")
