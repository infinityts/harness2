# Etapa 15 — Splitter por símbolos y tokenizer multi-runtime

## Objetivo

Convertir una tarea `NEEDS_SPLIT` en varias tareas hijas autocontenidas, cada
una con su propio presupuesto y contrato, y contar tokens con el runtime real
cuando esté disponible.

## Splitter

`POST /api/v1/tasks/{task_id}/split`

```json
{
  "max_input_tokens": 3000
}
```

El splitter:

1. Lee `allowed_symbols` y sus snippets AST.
2. Cuenta tokens por símbolo con el tokenizer del perfil.
3. Reserva tokens para contrato, aceptación, tests y documentación.
4. Agrupa símbolos en slices que quepan en el presupuesto.
5. Crea tareas hijas con:
   - `parent_task_id`
   - `split_index` y `split_count`
   - `split_reason = BUDGET_EXCEEDED`
   - `allowed_symbols` del slice
   - aceptación y tests heredados
   - `estimated_input_tokens` y `max_input_tokens`
6. Marca el padre como `SPLIT`.
7. Encadena los slices secuencialmente para evitar locks del mismo archivo.
8. Registra memoria `SPLIT` en el padre y `SPLIT_INHERIT` en los hijos.

También se puede disparar desde el plan:

```json
{
  "auto_split": true,
  "model_profile": "local-120k"
}
```

## Linaje

Migración `0017` agrega a `harness_tasks`:

- `parent_task_id`
- `split_index`
- `split_count`
- `split_reason`
- `split_status` (`NONE`, `PARENT`, `CHILD`)

`GET /api/v1/tasks/{task_id}/split-tree` devuelve el árbol completo.

## Dispatch

`dispatch_pending_tasks` excluye:

- `budget_status = NEEDS_SPLIT`
- `budget_status = SPLIT`
- `split_status = PARENT`

Así una tarea que no cabe no se asigna por accidente.

## Tokenizer Registry

El control plane no carga pesos. Habla con el runtime o lee un tokenizer JSON.

| Runtime | Estrategia | Precisión |
| --- | --- | --- |
| llama.cpp | `POST /tokenize` | Exacta |
| vLLM / servidores con `/tokenize` | `POST /tokenize` | Exacta |
| LM Studio | `/v1/tokenize` si existe; si no, calibración | Calibrada |
| Strata / OpenAI-compatible | `/tokenize` o `/v1/tokenize` si existe; si no, `usage.prompt_tokens` | Calibrada |
| Ollama | `/api/tokenize` si existe; si no, `prompt_eval_count` | Calibrada |
| tokenizer.json | `tokenizers.Tokenizer.from_file` | Exacta |
| Fallback | `chars_per_token` conservador | Heurística |

### Configurar un perfil

```bash
curl -X POST http://127.0.0.1:8444/api/v1/model-profiles/local-120k/tokenizer/config \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "tokenizer_kind": "openai_compatible",
    "tokenizer_url": "http://host.containers.internal:8080",
    "tokenizer_model": "strata-local",
    "tokenizer_timeout_ms": 3000
  }'
```

Desde el contenedor de Harness, `127.0.0.1` apunta al propio contenedor. Para
llegar a un runtime del host usar:

- `http://host.containers.internal:<puerto>`
- o la IP del host, por ejemplo `http://11.0.0.3:<puerto>`

### Health

```http
GET /api/v1/model-profiles/{name}/tokenizer/health
```

### Calibrar

```http
POST /api/v1/model-profiles/{name}/tokenizer/calibrate
```

Para OpenAI-compatible usa `/v1/chat/completions` y `usage.prompt_tokens`. Para
Ollama usa `/api/generate` y `prompt_eval_count`.

### Contar

```http
POST /api/v1/tokenizer/count

{
  "profile": "local-120k",
  "text": "texto a tokenizar"
}
```

Respuesta:

```json
{
  "tokens": 123,
  "source": "http:openai_compatible",
  "status": "CALIBRATED"
}
```

## Migración

`0017_task_split_lineage_tokenizers.py` agrega:

- linaje de split en `harness_tasks`;
- campos de tokenizer en `harness_model_profiles`;
- tabla `harness_tokenizer_samples` para auditoría de calibración.

## Herramientas MCP

- `harness_split_task`

## Pruebas

`tests/e2e_stage15_split_tokenizer.py` cubre:

- tokenizer exacto llama.cpp con mock HTTP;
- calibración OpenAI-compatible/Strata con mock `/v1/chat/completions`;
- tarea `NEEDS_SPLIT`;
- dispatch bloqueado antes del split;
- split por símbolos con presupuesto y linaje;
- dispatch del primer slice y bloqueo del segundo por DAG;
- auto-split desde el plan;
- `split-tree`, contrato y memoria.

Regresiones: `e2e_stage10`, `e2e_stage14`, `e2e_stage13`.

## Límite

- OpenAI-compatible estándar no expone tokenización exacta; se calibra con
  `usage.prompt_tokens`. Si el runtime expone `/tokenize`, se usa exacto.
- Si un solo símbolo excede el presupuesto, el splitter devuelve
  `SYMBOL_TOO_LARGE`; el siguiente paso sería dividir por rangos de líneas.
- Los slices del mismo archivo se ejecutan secuencialmente para respetar locks.
- La calibración debe repetirse si cambia el modelo o el tokenizer del runtime.
