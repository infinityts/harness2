"""skills_catalog_standard

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27
"""
import json
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from src.skill_compiler import (
    compile_standard_skill_md,
    estimate_tokens,
    slugify,
)

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

CATEGORIES = ("backend", "frontend", "database", "documents", "writing", "qa", "devops", "security", "general")

OFFICIAL_SKILLS = [
    {
        "slug": "python",
        "display_name": "Python limpio y validado",
        "category": "backend",
        "trigger_description": "Usa esta habilidad cuando la tarea requiera escribir, modificar o revisar codigo Python.",
        "objective": "Producir codigo Python tipado, legible y verificable, con validacion de entrada y manejo explicito de errores.",
        "workflow_steps": [
            "Lee los modelos, contratos y pruebas existentes antes de tocar el codigo.",
            "Implementa el cambio minimo con type hints, validacion de datos y excepciones claras.",
            "Agrega pruebas del camino feliz, errores y limites relevantes.",
            "Ejecuta la suite y confirma que no se rompieron contratos existentes.",
        ],
        "guardrails_forbidden": [
            "No uses eval, exec ni imports dinamicos con entrada externa.",
            "No silencies excepciones con pass sin registrar el fallo.",
            "No cambies la firma publica de una funcion sin actualizar a sus consumidores.",
        ],
        "output_criteria": "Entrega el diff, las pruebas ejecutadas y una nota breve de riesgos o migraciones pendientes.",
    },
    {
        "slug": "fastapi",
        "display_name": "APIs FastAPI seguras",
        "category": "backend",
        "trigger_description": "Usa esta habilidad cuando la tarea requiera crear o modificar endpoints REST o WebSocket con FastAPI.",
        "objective": "Implementar endpoints seguros, validados con Pydantic y con codigos HTTP correctos.",
        "workflow_steps": [
            "Define el contrato de entrada y salida con modelos Pydantic.",
            "Aplica autenticacion, autorizacion y validacion antes de ejecutar logica de negocio.",
            "Implementa el endpoint con manejo explicito de errores y transacciones.",
            "Agrega pruebas HTTP para exito, validacion, permisos y errores.",
        ],
        "guardrails_forbidden": [
            "No devuelvas trazas internas ni secretos al cliente.",
            "No aceptes campos sin validar en operaciones de escritura.",
            "No uses 200 para errores de negocio.",
        ],
        "output_criteria": "Entrega el endpoint, ejemplos de request/response, pruebas y codigos HTTP utilizados.",
    },
    {
        "slug": "ui-responsive",
        "display_name": "UI responsive y accesible",
        "category": "frontend",
        "trigger_description": "Usa esta habilidad cuando la tarea requiera construir o ajustar interfaces web para escritorio, tablet o movil.",
        "objective": "Crear interfaces adaptativas, legibles y tactiles para pantallas de 16 pulgadas y dispositivos desde 7 pulgadas.",
        "workflow_steps": [
            "Identifica los breakpoints y el contenido prioritario de cada pantalla.",
            "Implementa el layout con CSS Grid o Flexbox y evita anchos fijos.",
            "Agrega estados de foco, hover y objetivos tactiles de al menos 44px.",
            "Verifica overflow, contraste, teclado y lectura en viewport movil.",
        ],
        "guardrails_forbidden": [
            "No ocultes acciones criticas solo por falta de espacio.",
            "No uses texto menor a 12px en contenido operativo.",
            "No dependas de hover para acciones esenciales.",
        ],
        "output_criteria": "Entrega capturas o evidencia de escritorio y movil, breakpoints usados y accesibilidad basica verificada.",
    },
    {
        "slug": "db-alembic",
        "display_name": "Migraciones Alembic",
        "category": "database",
        "trigger_description": "Usa esta habilidad cuando la tarea requiera cambiar el esquema o los datos persistentes de PostgreSQL.",
        "objective": "Evolucionar el esquema exclusivamente mediante migraciones Alembic reversibles y verificadas.",
        "workflow_steps": [
            "Revisa el head actual y el modelo de datos afectado.",
            "Crea una revision Alembic con upgrade y downgrade reversibles.",
            "Ejecuta alembic upgrade head y valida tablas, columnas, indices y constraints.",
            "Documenta el plan de rollback y cualquier backfill de datos.",
        ],
        "guardrails_forbidden": [
            "Nunca ejecutes DDL manual fuera de una migracion.",
            "No elimines columnas o tablas sin plan de migracion o respaldo.",
            "No modifiques una revision ya aplicada en produccion.",
        ],
        "output_criteria": "Entrega el archivo de migracion, el resultado de upgrade/downgrade y la verificacion del esquema.",
    },
    {
        "slug": "doc-analyzer",
        "display_name": "Analisis fiel de documentos",
        "category": "documents",
        "trigger_description": "Usa esta habilidad cuando la tarea requiera leer contratos, facturas, actas o documentos y extraer datos clave.",
        "objective": "Extraer informacion verificable de documentos sin inventar cifras, fechas, nombres ni clausulas.",
        "workflow_steps": [
            "Identifica el tipo de documento, sus secciones y el alcance solicitado.",
            "Extrae solo datos presentes y cita la seccion o pagina de origen.",
            "Marca NO_ESPECIFICADO cuando un dato no aparezca en el texto.",
            "Verifica fechas, montos y nombres contra el documento original.",
        ],
        "guardrails_forbidden": [
            "No inventes datos faltantes ni completes con supuestos.",
            "No omitas la fuente de una cifra o clausula.",
            "No interpretes una clausula legal sin marcarla como interpretacion.",
        ],
        "output_criteria": "Entrega una tabla con campo, valor, fuente y NO_ESPECIFICADO cuando corresponda.",
    },
    {
        "slug": "tech-writer",
        "display_name": "Redaccion tecnica ejecutiva",
        "category": "writing",
        "trigger_description": "Usa esta habilidad cuando la tarea requiera documentar incidentes, decisiones, guias o resumenes para audiencias tecnicas y ejecutivas.",
        "objective": "Producir documentacion clara, directa y estructurada, con resumen ejecutivo y acciones concretas.",
        "workflow_steps": [
            "Define audiencia, decision esperada y nivel de detalle.",
            "Estructura el contenido en resumen, contexto, hallazgos y siguiente accion.",
            "Redacta frases cortas y elimina relleno o tecnicismos innecesarios.",
            "Verifica consistencia, ortografia y que cada afirmacion tenga evidencia.",
        ],
        "guardrails_forbidden": [
            "No inventes hechos, fechas ni responsables.",
            "No presentes una hipotesis como causa confirmada.",
            "No ocultes riesgos o limitaciones relevantes.",
        ],
        "output_criteria": "Entrega el documento final en Markdown, con titulo, resumen ejecutivo y acciones verificables.",
    },
]


def _skill_id(conn, slug: str) -> str:
    value = conn.execute(
        sa.text("SELECT id::text FROM harness_skills WHERE slug = :slug"),
        {"slug": slug},
    ).scalar_one_or_none()
    if value:
        return str(value)
    skill_id = str(uuid.uuid4())
    # Skill legacy creada a partir de allowed_skills historicos.
    compiled = compile_standard_skill_md(
        slug, slug.replace("-", " ").title(), "general",
        f"Usa esta habilidad cuando la tarea requiera {slug.replace('-', ' ')}.",
        f"Ejecutar de forma verificable la habilidad {slug}.",
        [
            "Aclara el objetivo y el resultado esperado.",
            "Ejecuta el trabajo siguiendo las convenciones del proyecto.",
            "Verifica el resultado antes de declararlo completado.",
        ],
        ["No inventes informacion.", "No declares completado algo que no verificaste."],
        "Entrega el resultado y la evidencia de verificacion.",
    )
    conn.execute(
        sa.text("""
            INSERT INTO harness_skills
                (id, slug, display_name, category, trigger_description, objective,
                 workflow_steps, guardrails_forbidden, output_criteria, compiled_skill_md,
                 created_by_user_id, owner_user_id, is_official, status, visibility,
                 version, compiled_tokens)
            VALUES
                (CAST(:id AS UUID), :slug, :name, 'general', :trigger, :objective,
                 CAST(:steps AS JSONB), CAST(:guardrails AS JSONB), :output, :compiled,
                 NULL, NULL, false, 'PUBLISHED', 'PRIVATE', 1, :tokens)
        """),
        {
            "id": skill_id, "slug": slug, "name": slug.replace("-", " ").title(),
            "trigger": f"Usa esta habilidad cuando la tarea requiera {slug.replace('-', ' ')}.",
            "objective": f"Ejecutar de forma verificable la habilidad {slug}.",
            "steps": json.dumps([
                "Aclara el objetivo y el resultado esperado.",
                "Ejecuta el trabajo siguiendo las convenciones del proyecto.",
                "Verifica el resultado antes de declararlo completado.",
            ]),
            "guardrails": json.dumps(["No inventes informacion.", "No declares completado algo que no verificaste."]),
            "output": "Entrega el resultado y la evidencia de verificacion.",
            "compiled": compiled,
            "tokens": estimate_tokens(compiled),
        }
    )
    return skill_id


def upgrade() -> None:
    op.create_table(
        "harness_skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("display_name", sa.String(150), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("trigger_description", sa.String(300), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("workflow_steps", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("guardrails_forbidden", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("output_criteria", sa.Text(), nullable=False),
        sa.Column("compiled_skill_md", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_official", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default="PUBLISHED"),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="PRIVATE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("compiled_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"category IN {CATEGORIES}", name="ck_skills_category"),
        sa.CheckConstraint("status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')", name="ck_skills_status"),
        sa.CheckConstraint("visibility IN ('OFFICIAL', 'SHARED', 'PRIVATE')", name="ck_skills_visibility"),
        sa.CheckConstraint("is_official = false OR visibility = 'OFFICIAL'", name="ck_skills_official_visibility"),
    )
    op.create_index("idx_skills_category", "harness_skills", ["category"])
    op.create_index("idx_skills_owner", "harness_skills", ["owner_user_id"])
    op.create_index("idx_skills_status", "harness_skills", ["status"])

    op.create_table(
        "user_skill_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("can_create_skills", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("can_clone_skills", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("can_edit_own_skills", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("can_grant_skills", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "skill_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("can_use", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("can_edit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("can_clone", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("can_grant", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("skill_id", "user_id", name="uq_skill_permission_user"),
    )
    op.create_index("idx_skill_permissions_user", "skill_permissions", ["user_id"])

    op.create_table(
        "project_skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "skill_id", name="uq_project_skill"),
    )
    op.create_index("idx_project_skills_project", "project_skills", ["project_id"])

    op.create_table(
        "membership_skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("harness_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "user_id", "skill_id", name="uq_membership_skill"),
    )
    op.create_index("idx_membership_skills_user", "membership_skills", ["user_id"])
    op.create_index("idx_membership_skills_project", "membership_skills", ["project_id"])

    conn = op.get_bind()

    # Seeds oficiales.
    for skill in OFFICIAL_SKILLS:
        compiled = compile_standard_skill_md(
            skill["slug"], skill["display_name"], skill["category"],
            skill["trigger_description"], skill["objective"],
            skill["workflow_steps"], skill["guardrails_forbidden"],
            skill["output_criteria"],
        )
        conn.execute(
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
                "id": str(uuid.uuid4()), "slug": skill["slug"],
                "display_name": skill["display_name"], "category": skill["category"],
                "trigger": skill["trigger_description"], "objective": skill["objective"],
                "steps": json.dumps(skill["workflow_steps"]),
                "guardrails": json.dumps(skill["guardrails_forbidden"]),
                "output": skill["output_criteria"], "compiled": compiled,
                "tokens": estimate_tokens(compiled),
            }
        )

    # Backfill de allowed_skills historicos a project_skills y membership_skills.
    legacy_rows = conn.execute(
        sa.text("""
            SELECT project_id::text, user_id::text, allowed_skills
            FROM project_memberships
            WHERE allowed_skills IS NOT NULL AND allowed_skills <> '[]'::jsonb
        """)
    ).fetchall()
    for project_id, user_id, allowed_skills in legacy_rows:
        raw_slugs = allowed_skills if isinstance(allowed_skills, list) else []
        for raw in raw_slugs:
            slug = slugify(str(raw), 100)
            if not slug:
                continue
            skill_id = _skill_id(conn, slug)
            conn.execute(
                sa.text("""
                    INSERT INTO project_skills (id, project_id, skill_id, assigned_by_user_id)
                    VALUES (CAST(:id AS UUID), CAST(:pid AS UUID), CAST(:sid AS UUID), NULL)
                    ON CONFLICT (project_id, skill_id) DO NOTHING
                """),
                {"id": str(uuid.uuid4()), "pid": project_id, "sid": skill_id},
            )
            conn.execute(
                sa.text("""
                    INSERT INTO membership_skills (id, project_id, user_id, skill_id, assigned_by_user_id)
                    VALUES (CAST(:id AS UUID), CAST(:pid AS UUID), CAST(:uid AS UUID), CAST(:sid AS UUID), NULL)
                    ON CONFLICT (project_id, user_id, skill_id) DO NOTHING
                """),
                {"id": str(uuid.uuid4()), "pid": project_id, "uid": user_id, "sid": skill_id},
            )

    # Capacidades por defecto para ingenieros existentes (los ADMIN tienen bypass).
    conn.execute(
        sa.text("""
            INSERT INTO user_skill_capabilities
                (id, user_id, can_create_skills, can_clone_skills, can_edit_own_skills, can_grant_skills)
            SELECT CAST(uuid_generate_v4() AS UUID), u.id, false, false, true, false
            FROM harness_users u
            WHERE u.role <> 'ADMIN'
            ON CONFLICT (user_id) DO NOTHING
        """)
    )


def downgrade() -> None:
    op.drop_index("idx_membership_skills_project", table_name="membership_skills")
    op.drop_index("idx_membership_skills_user", table_name="membership_skills")
    op.drop_table("membership_skills")
    op.drop_index("idx_project_skills_project", table_name="project_skills")
    op.drop_table("project_skills")
    op.drop_index("idx_skill_permissions_user", table_name="skill_permissions")
    op.drop_table("skill_permissions")
    op.drop_table("user_skill_capabilities")
    op.drop_index("idx_skills_status", table_name="harness_skills")
    op.drop_index("idx_skills_owner", table_name="harness_skills")
    op.drop_index("idx_skills_category", table_name="harness_skills")
    op.drop_table("harness_skills")
