# Etapa 14 — Task Contract v2 y Memory Kernel

## Objetivo

Que cada tarea generada por un plan sea autosuficiente para un modelo local:
objetivo, alcance, criterios de aceptación, tests, dependencias, presupuesto de
tokens y memoria de lo ya intentado.

La meta no es llenar la ventana de 120k. La meta es que cada microtarea quepa en
una fracción segura de la ventana y que el progreso sobreviva a una compactación
o a un cambio de agente.

## Contrato de tarea

`harness_tasks` ahora persiste:

| Campo | Uso |
| --- | --- |
| `description` | Qué debe lograr la tarea, en lenguaje ejecutable. |
| `task_context` | Por qué existe y qué decisiones previas aplican. |
| `acceptance_criteria` | Lista verificable de aceptación. |
| `test_commands` | Comandos concretos de verificación. |
| `depends_on` | UUIDs de tareas que deben estar `MERGED`. |
| `allowed_symbols` | Símbolos AST permitidos, formato `archivo::simbolo`. |
| `estimated_input_tokens` | Estimación conservadora del contexto de entrada. |
| `max_input_tokens` | Techo duro para esa tarea. |
| `budget_status` | `OK` o `NEEDS_SPLIT`. |
| `handoff_summary` | Último resumen persistente de la tarea. |
| `context_manifest` | Perfil, presupuesto y estado del contrato. |
| `memory_epoch` | Número de checkpoints/handoffs de la tarea. |
| `task_kind` | `IMPLEMENT`, `VERIFY`, etc. |

El plan completo queda consultable en:

```http
GET /api/v1/plans/{plan_id}
GET /api/v1/tasks/{task_id}/contract
```

`TASK_ASSIGNED` incluye el contrato completo, no solo el título. El agente ya no
tiene que adivinar el requerimiento.

## Presupuesto por modelo

Nuevo catálogo `harness_model_profiles`:

| Perfil | Ventana | Entrada máx. | Salida | Reserva |
| --- | ---: | ---: | ---: | ---: |
| `local-32k` | 32.000 | 20.000 | 6.000 | 6.000 |
| `local-64k` | 64.000 | 40.000 | 8.000 | 16.000 |
| `local-120k` | 120.000 | 64.000 | 16.000 | 40.000 |
| `cloud-128k` | 128.000 | 72.000 | 16.000 | 40.000 |

Regla práctica para 120k: **24k–48k de entrada por microtarea**, máximo 64k.
No se llena la ventana; se deja espacio para razonamiento, tools y salida.

El perfil se elige al crear la sesión:

```http
POST /api/v1/portal/sessions

{
  "project_id": "...",
  "agent_tool": "opencode",
  "llm_provider": "local-llm",
  "max_context_tokens": 32000,
  "model_profile": "local-120k"
}
```

`max_context_tokens` se ajusta a la ventana del perfil. El perfil también se
puede indicar al proponer un plan.

## Context Pack v2

`POST /api/v1/context/pack` acepta:

- `task_id`: usa el contrato, `allowed_files`, `allowed_symbols` y handoff.
- `model_profile`: aplica el presupuesto del perfil.
- `token_budget`: hasta **64.000** tokens.
- `include_handoff`: incluye la memoria episódica de la tarea.

Devuelve `manifest` con tokens por sección:

```json
{
  "task_id": "...",
  "model_profile": "local-120k",
  "token_budget": 32000,
  "estimated_tokens_used": 697,
  "manifest": {
    "sections": {
      "contract": 376,
      "handoff": 161,
      "repo_quick_map": 89,
      "ast": 48,
      "documentation": 23
    }
  },
  "task_contract": {},
  "handoff": {}
}
```

La selección AST usa `allowed_symbols` cuando existen. Así un archivo enorme no
obliga a cargar todo el archivo: se carga el símbolo relevante y el agente puede
pedir más contexto bajo demanda.

## Memory Kernel

### Capas

| Capa | Contenido | Persistencia |
| --- | --- | --- |
| Working memory | Context Pack de la tarea actual | efímera |
| Episodic memory | checkpoints, handoffs, fallos, decisiones, tests | `harness_task_memory` |
| Semantic memory | documentos, RAG, mapa AST | PostgreSQL/pgvector |
| Procedural memory | skills y manuales | `harness_skills` |
| Plan memory | decisiones de plan | `harness_plan_memory` |

### Checkpoint

```http
POST /api/v1/tasks/{task_id}/checkpoint

{
  "summary": "Total implementado; falta validar redondeo",
  "decisions": ["usar round a 2 decimales"],
  "files": ["src/calc.py"],
  "tests": ["python -m compileall src/calc.py"],
  "next_steps": ["validar formato"],
  "reset_context": true
}
```

Persiste el checkpoint, incrementa `memory_epoch`, actualiza
`handoff_summary` y, si `reset_context=true`, limpia el contador de la sesión e
incrementa `context_compactions`.

### Resume

```http
POST /api/v1/tasks/{task_id}/resume
```

Devuelve contrato, dependencias, handoff y memoria. Es la entrada canónica para
retomar tras una compactación o cambiar de agente.

### Handoff automático

- Al hacer `submit`, se persiste un `HANDOFF` con resumen, archivos, tests y
  commits.
- Al fallar una barrera o un checkpoint, se persiste un `FAILURE`.
- `failed_attempts_memory` se mantiene para compatibilidad.

## Herramientas MCP

Se agregaron:

- `harness_get_task_contract`
- `harness_checkpoint_task`
- `harness_resume_task`
- `harness_report_context_usage`

El agente puede reportar tokens reales del runtime local. Si no los reporta,
Harness usa el presupuesto conservador del perfil y del contexto empaquetado.

## Dispatch con DAG

`dispatch_pending_tasks` no asigna una tarea hasta que sus `depends_on` estén
`MERGED`. Esto evita ejecutar validaciones antes de la implementación y mantiene
el orden del plan.

## Migración

`0016_task_contract_memory.py` agrega:

- contrato de tarea y métricas de memoria;
- `harness_task_memory` y `harness_plan_memory`;
- `harness_model_profiles` con los cuatro perfiles;
- `agent_sessions.model_profile`, `context_compactions`, `last_compacted_at`;
- `feature_consensus_plans.model_profile`, `plan_budget_tokens`, `plan_metrics`.

## Pruebas

`tests/e2e_stage14_task_contract_memory.py` cubre:

- plan con contrato rico y perfil 120k;
- plan default determinista;
- `GET /plans/{id}` y `GET /tasks/{id}/contract`;
- `NEEDS_SPLIT` cuando el techo es menor que la estimación;
- sesión `local-120k`;
- checkpoint, memoria, resume y handoff;
- Context Pack v2 con `manifest`, símbolos y documentación;
- fallo persistente y handoff de éxito.

Regresiones: `e2e_stage10`, `e2e_stage11`, `e2e_stage12` y
`e2e_stage13`.

## Límite

La estimación de tokens es conservadora pero no es un tokenizer exacto del
modelo local. Para precisión total, el agente debe reportar tokens reales o el
perfil debe declarar un tokenizer específico. La memoria episódica reduce la
pérdida por compactación, pero no sustituye la verificación con tests ni la
revisión humana.
