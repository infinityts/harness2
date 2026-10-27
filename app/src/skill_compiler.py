"""Estandar SKILL.md y generador guiado de skills.

La base de datos es la fuente de verdad. `compiled_skill_md` es el artefacto
precompilado que se inyecta bajo demanda; no se cargan todos los manuales en el
contexto del agente.
"""
import json
import os
import re
import unicodedata
import urllib.request
from typing import Any, Dict, List, Optional

SKILL_CATEGORIES = {
    "backend": "Backend / APIs",
    "frontend": "Frontend / UI",
    "database": "Base de datos",
    "documents": "Lectura de documentos",
    "writing": "Redaccion / Documentacion",
    "qa": "QA / Testing",
    "devops": "DevOps / Infraestructura",
    "security": "Seguridad",
    "general": "General",
}

MAX_COMPILED_TOKENS = 500
MAX_STEPS = 5
MAX_GUARDRAILS = 8


def estimate_tokens(text: str) -> int:
    """Estimacion conservadora para controlar el presupuesto de contexto."""
    return max(1, (len(text or "") + 3) // 4)


def slugify(value: str, max_length: int = 80) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug[:max_length].strip("-") or "skill")


def _clean_item(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", text).strip()


def compile_standard_skill_md(
    slug: str,
    display_name: str,
    category: str,
    trigger_description: str,
    objective: str,
    workflow_steps: List[str],
    guardrails_forbidden: List[str],
    output_criteria: str,
) -> str:
    steps = [_clean_item(step) for step in workflow_steps if _clean_item(step)]
    guardrails = [_clean_item(rule) for rule in guardrails_forbidden if _clean_item(rule)]
    steps_text = "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, start=1))
    guardrails_text = "\n".join(f"- NEVER: {rule}" for rule in guardrails)
    return f"""---
skill: {slug}
name: {display_name}
category: {category}
when_to_use: {trigger_description}
---
#SKILL: {display_name}
## 1. Objective
{objective}

## 2. Mandatory Steps
{steps_text}

## 3. Strict Guardrails (DO NOT)
{guardrails_text}

## 4. Verification & Output Format
{output_criteria}
"""


def validate_skill_fields(
    display_name: str,
    category: str,
    trigger_description: str,
    objective: str,
    workflow_steps: List[str],
    guardrails_forbidden: List[str],
    output_criteria: str,
) -> Optional[str]:
    if not (2 <= len(display_name.strip()) <= 150):
        return "display_name debe tener entre 2 y 150 caracteres"
    if category not in SKILL_CATEGORIES:
        return f"category invalida: {category}"
    if not (10 <= len(trigger_description.strip()) <= 300):
        return "trigger_description debe tener entre 10 y 300 caracteres"
    if not (10 <= len(objective.strip()) <= 1000):
        return "objective debe tener entre 10 y 1000 caracteres"
    steps = [str(s).strip() for s in workflow_steps if str(s).strip()]
    if not (3 <= len(steps) <= MAX_STEPS):
        return f"workflow_steps debe tener entre 3 y {MAX_STEPS} pasos"
    if any(len(s) > 400 for s in steps):
        return "cada paso debe tener maximo 400 caracteres"
    guardrails = [str(g).strip() for g in guardrails_forbidden if str(g).strip()]
    if not (1 <= len(guardrails) <= MAX_GUARDRAILS):
        return f"guardrails_forbidden debe tener entre 1 y {MAX_GUARDRAILS} reglas"
    if any(len(g) > 400 for g in guardrails):
        return "cada guardrail debe tener maximo 400 caracteres"
    if not (10 <= len(output_criteria.strip()) <= 1000):
        return "output_criteria debe tener entre 10 y 1000 caracteres"
    preview = compile_standard_skill_md(
        "preview", display_name, category, trigger_description, objective,
        steps, guardrails, output_criteria,
    )
    if estimate_tokens(preview) > MAX_COMPILED_TOKENS:
        return f"el manual compilado supera {MAX_COMPILED_TOKENS} tokens estimados"
    return None


_CATEGORY_STEPS = {
    "backend": [
        "Lee los contratos, modelos y pruebas existentes antes de cambiar codigo.",
        "Implementa el cambio minimo con tipado, validacion de entrada y manejo explicito de errores.",
        "Agrega o actualiza pruebas para el camino feliz y los errores esperados.",
        "Ejecuta la suite relevante y documenta el resultado.",
    ],
    "frontend": [
        "Identifica la pantalla, el estado y los componentes reutilizables existentes.",
        "Implementa la interfaz con HTML semantico y estilos consistentes con el portal.",
        "Agrega breakpoints para 16 pulgadas, tablet y movil; usa objetivos tactiles de al menos 44px.",
        "Verifica accesibilidad, foco, teclado y ausencia de overflow.",
    ],
    "database": [
        "Revisa el esquema actual y define la migracion necesaria.",
        "Crea la revision Alembic con upgrade y downgrade reversibles.",
        "Ejecuta alembic upgrade head en un entorno controlado y valida el esquema.",
        "Documenta el cambio y las consideraciones de despliegue.",
    ],
    "documents": [
        "Identifica las secciones y el tipo de documento antes de extraer datos.",
        "Extrae solo informacion presente en el texto y cita la seccion de origen.",
        "Si un dato no aparece, escribe NO_ESPECIFICADO; no inventes cifras ni hechos.",
        "Revisa que fechas, montos y nombres coincidan exactamente con el documento.",
    ],
    "writing": [
        "Define la audiencia, el objetivo y el formato de entrega.",
        "Estructura el contenido en resumen, desarrollo y siguiente accion.",
        "Redacta con frases directas, sin relleno ni tecnicismos innecesarios.",
        "Revisa consistencia, ortografia y que cada afirmacion tenga evidencia.",
    ],
    "qa": [
        "Identifica el comportamiento esperado y los riesgos principales.",
        "Disena casos felices, errores, limites y regresiones.",
        "Ejecuta las pruebas y registra evidencia reproducible.",
        "Reporta hallazgos con severidad, pasos y resultado esperado.",
    ],
    "devops": [
        "Revisa el entorno, los recursos y las dependencias del cambio.",
        "Aplica el cambio de forma reversible y con respaldo cuando corresponda.",
        "Verifica salud, logs y rollback antes de declarar exito.",
        "Documenta comandos, variables y criterios de recuperacion.",
    ],
    "security": [
        "Identifica activos, actores y superficies de ataque relevantes.",
        "Aplica el principio de menor privilegio y valida toda entrada externa.",
        "Verifica que no se expongan secretos en codigo, logs o respuestas.",
        "Registra hallazgos con evidencia y una recomendacion concreta.",
    ],
    "general": [
        "Aclara el objetivo y el resultado esperado antes de empezar.",
        "Descompone el trabajo en pasos verificables y ejecutalos en orden.",
        "Revisa el resultado contra los criterios de exito.",
        "Reporta supuestos, limitaciones y siguiente accion.",
    ],
}

_CATEGORY_GUARDRAILS = {
    "backend": ["No cambies contratos publicos sin versionar.", "No silencies excepciones sin registrarlas."],
    "frontend": ["No uses botones menores a 44px en movil.", "No rompas el layout de escritorio."],
    "database": ["Nunca ejecutes DDL manual fuera de Alembic.", "No elimines columnas sin plan de migracion de datos."],
    "documents": ["No inventes fechas, cifras, nombres ni clausulas.", "No omitas el origen de un dato extraido."],
    "writing": ["No inventes hechos ni fuentes.", "No uses relleno para aumentar la extension."],
    "qa": ["No declares una prueba exitosa sin evidencia.", "No ocultes fallos conocidos."],
    "devops": ["No expongas secretos en logs o repositorios.", "No apliques cambios destructivos sin respaldo."],
    "security": ["No almacenes secretos en texto plano.", "No confies en entrada no validada."],
    "general": ["No inventes informacion.", "No declares completado algo que no verificaste."],
}

_CATEGORY_OUTPUT = {
    "backend": "Entrega el cambio, las pruebas ejecutadas y cualquier riesgo o migracion pendiente.",
    "frontend": "Entrega una interfaz responsive, accesible y verificada en escritorio y movil.",
    "database": "Entrega la revision Alembic, el resultado de upgrade/downgrade y el plan de rollback.",
    "documents": "Entrega una tabla o lista con los datos extraidos y NO_ESPECIFICADO cuando falte informacion.",
    "writing": "Entrega el texto final en el formato solicitado, con resumen y siguiente accion.",
    "qa": "Entrega casos ejecutados, evidencia, severidad y recomendacion.",
    "devops": "Entrega comandos, verificaciones de salud y plan de rollback.",
    "security": "Entrega hallazgos priorizados, evidencia y mitigacion.",
    "general": "Entrega el resultado en el formato solicitado y una verificacion concreta.",
}


def deterministic_skill_draft(raw_idea: str, category: str = "general") -> Dict[str, Any]:
    idea = " ".join((raw_idea or "").split()).strip()
    if len(idea) < 10:
        idea = "Ejecutar una tarea de forma verificable y segura"
    category = category if category in SKILL_CATEGORIES else "general"
    slug = slugify(idea, max_length=60)
    if len(slug) < 3:
        slug = f"{category}-skill"
    if len(slug.split("-")) <= 2 and not slug.endswith(("-skill", "-helper", "-workflow")):
        slug = f"{slug}-skill"
    display_name = idea[:120].strip().capitalize()
    if len(display_name) < 5:
        display_name = f"Habilidad {category}"
    trigger = f"Usa esta habilidad cuando la tarea requiera: {idea[:200]}"
    objective = f"Lograr de forma verificable: {idea[:400]}"
    steps = list(_CATEGORY_STEPS[category])
    guardrails = list(_CATEGORY_GUARDRAILS[category])
    output = _CATEGORY_OUTPUT[category]
    compiled = compile_standard_skill_md(
        slug, display_name, category, trigger, objective,
        steps, guardrails, output,
    )
    return {
        "slug": slug,
        "display_name": display_name,
        "category": category,
        "trigger_description": trigger,
        "objective": objective,
        "workflow_steps": steps,
        "guardrails_forbidden": guardrails,
        "output_criteria": output,
        "compiled_skill_md": compiled,
        "compiled_tokens": estimate_tokens(compiled),
        "provider": "deterministic",
    }


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _ai_skill_draft(raw_idea: str, category: str) -> Optional[Dict[str, Any]]:
    provider = os.getenv("SKILL_DRAFT_PROVIDER", "none").strip().lower()
    if provider in ("", "none", "disabled"):
        return None
    prompt = (
        "Genera una skill estructurada en JSON con las claves slug, display_name, "
        "category, trigger_description, objective, workflow_steps (3-5 strings), "
        "guardrails_forbidden (1-8 strings) y output_criteria. "
        f"Categoria: {category}. Idea: {raw_idea}. "
        "Responde solo JSON valido, sin markdown."
    )
    try:
        if provider == "ollama":
            base = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
            model = os.getenv("SKILL_DRAFT_MODEL", "llama3.2")
            payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
            req = urllib.request.Request(
                base + "/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        elif provider in ("openai", "openai-compatible"):
            base = os.getenv("SKILL_DRAFT_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            model = os.getenv("SKILL_DRAFT_MODEL", "gpt-4o-mini")
            api_key = os.getenv("SKILL_DRAFT_API_KEY", "")
            if not api_key:
                return None
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }).encode()
            req = urllib.request.Request(
                base + "/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
        else:
            return None
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
        if provider == "ollama":
            content = raw.get("response", "")
        else:
            content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        data = _extract_json_object(content)
        if not data:
            return None
        steps = [str(x) for x in data.get("workflow_steps", [])][:MAX_STEPS]
        guardrails = [str(x) for x in data.get("guardrails_forbidden", [])][:MAX_GUARDRAILS]
        display = str(data.get("display_name") or raw_idea)[:150]
        error = validate_skill_fields(
            display, category, str(data.get("trigger_description") or "")[:300],
            str(data.get("objective") or "")[:1000], steps, guardrails,
            str(data.get("output_criteria") or "")[:1000],
        )
        if error:
            return None
        slug = slugify(str(data.get("slug") or display), 80)
        compiled = compile_standard_skill_md(
            slug, display, category, str(data.get("trigger_description") or ""),
            str(data.get("objective") or ""), steps, guardrails,
            str(data.get("output_criteria") or ""),
        )
        return {
            "slug": slug,
            "display_name": display,
            "category": category,
            "trigger_description": str(data.get("trigger_description") or ""),
            "objective": str(data.get("objective") or ""),
            "workflow_steps": steps,
            "guardrails_forbidden": guardrails,
            "output_criteria": str(data.get("output_criteria") or ""),
            "compiled_skill_md": compiled,
            "compiled_tokens": estimate_tokens(compiled),
            "provider": provider,
        }
    except Exception:
        return None


def draft_skill(raw_idea: str, category: str = "general") -> Dict[str, Any]:
    return _ai_skill_draft(raw_idea, category) or deterministic_skill_draft(raw_idea, category)
