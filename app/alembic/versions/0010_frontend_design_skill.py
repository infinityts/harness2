"""frontend_design_skill

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-27
"""
import json
import uuid

from alembic import op
import sqlalchemy as sa

from src.skill_compiler import compile_standard_skill_md, estimate_tokens

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SKILL = {
    "slug": "frontend-design",
    "display_name": "Diseño frontend con identidad propia",
    "category": "frontend",
    "trigger_description": (
        "Usa esta habilidad cuando la tarea requiera diseñar o rediseñar una "
        "interfaz web con identidad visual propia, no una plantilla genérica."
    ),
    "objective": (
        "Definir y construir una interfaz web distintiva y coherente con el "
        "sujeto del brief, con tipografía, color, layout y movimiento "
        "intencionales. Adaptada de anthropics/skills/frontend-design."
    ),
    "workflow_steps": [
        "Identifica el sujeto, la audiencia y el trabajo principal del brief; si falta contexto, propone uno concreto y confírmalo.",
        "Escribe un plan compacto de tokens: 4-6 colores hex, una o dos tipografías con roles, layout con wireframe ASCII y tres principios de identidad.",
        "Revisa el plan contra el brief y elimina los clichés de IA (cream y serif con terracota, ácido verde, tarjetas idénticas, eyebrows en mayúsculas, flechas decorativas) salvo que el brief los pida.",
        "Implementa HTML semántico y CSS con una sola idea visual memorable, jerarquía tipográfica, contraste accesible, foco visible, responsive y reduced-motion.",
        "Critica el resultado con captura o inspección, recorta un accesorio decorativo y verifica responsive, teclado y consistencia de copy y acciones.",
    ],
    "guardrails_forbidden": [
        "Usar tarjetas idénticas, gradientes decorativos o iconos genéricos como sustituto de una idea de diseño.",
        "Usar tipografías, paletas o tratamientos por defecto que el brief no justifique.",
        "Sacrificar legibilidad, contraste, foco de teclado, responsive o reduced-motion por estética.",
        "Inventar el sujeto o el copy final si el brief no lo define; propón y confirma.",
    ],
    "output_criteria": (
        "Entrega el plan de tokens, el código funcional, una captura o evidencia "
        "visual y una nota de qué hace única la interfaz y qué se recortó."
    ),
}


def upgrade() -> None:
    compiled = compile_standard_skill_md(
        SKILL["slug"], SKILL["display_name"], SKILL["category"],
        SKILL["trigger_description"], SKILL["objective"],
        SKILL["workflow_steps"], SKILL["guardrails_forbidden"],
        SKILL["output_criteria"],
    )
    tokens = estimate_tokens(compiled)
    if tokens > 500:
        raise RuntimeError(f"frontend-design excede 500 tokens: {tokens}")
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
            "slug": SKILL["slug"],
            "display_name": SKILL["display_name"],
            "category": SKILL["category"],
            "trigger": SKILL["trigger_description"],
            "objective": SKILL["objective"],
            "steps": json.dumps(SKILL["workflow_steps"], ensure_ascii=False),
            "guardrails": json.dumps(SKILL["guardrails_forbidden"], ensure_ascii=False),
            "output": SKILL["output_criteria"],
            "compiled": compiled,
            "tokens": tokens,
        },
    )


def downgrade() -> None:
    op.execute("DELETE FROM harness_skills WHERE slug = 'frontend-design' AND is_official = true")
