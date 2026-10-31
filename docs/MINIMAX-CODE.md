# MiniMax Code en Harness2

## Objetivo

Integrar **MiniMax Code CLI (`mcode`)** como un agente más del ecosistema Harness2:
sesión aislada por proyecto, contrato de tarea, Context Pack, splitter, memoria,
War Room y auditoría.

MiniMax Code no reemplaza el control plane. Harness2 sigue siendo la autoridad de:

- proyecto y sesión;
- tarea y contrato;
- contexto y presupuesto;
- locks, worktrees y Git Guardian;
- memoria episódica y checkpoints;
- auditoría.

## Instalación

```bash
npm install -g @minimax-ai/code
mcode --version
```

Autenticación con cuenta MiniMax:

```bash
mcode login --region global
```

O con API key:

```bash
export MCODE_PROVIDER_API_KEY="<MINIMAX_API_KEY>"
mcode provider set-minimax-key
```

MiniMax Code soporta proveedores compatibles con OpenAI y Anthropic. La API key
de MiniMax se administra en `mcode`; Harness2 no la almacena.

## MCP

En Harness2:

1. Crear una sesión con herramienta **MiniMax Code**.
2. Abrir **Configuración** de la sesión.
3. Copiar **MiniMax Code MCP JSON (.mcp.json)**.
4. Guardarlo como `.mcp.json` en la raíz del proyecto.

El bloque tiene esta forma:

```json
{
  "mcpServers": {
    "harness": {
      "command": "python",
      "args": ["harness_mcp_bridge.py"],
      "env": {
        "HARNESS_URL": "https://...",
        "HARNESS_SESSION_ID": "...",
        "HARNESS_SESSION_KEY": "...",
        "HARNESS_PROJECT_SLUG": "..."
      }
    }
  }
}
```

Descargar el bridge si no existe:

```bash
curl -fsSL <HARNESS_URL>/api/v1/client/bridge.py -o harness_mcp_bridge.py
```

### Windows: usar ruta absoluta

MiniMax Code en Windows resuelve `args` de un servidor MCP stdio contra el
**home del usuario**, no contra el directorio del workspace. Si el bridge está en:

```text
D:\minimax\projects\desktop-organizer\harness_mcp_bridge.py
```

el `.mcp.json` debe usar:

```json
"args": [
  "D:/minimax/projects/desktop-organizer/harness_mcp_bridge.py"
]
```

No uses `"harness_mcp_bridge.py"` relativo: el runtime intentará abrirlo desde
`C:\Users\<usuario>\harness_mcp_bridge.py` y fallará.

Verificar:

```bash
mcode mcp list --human
mcode mcp tools harness
```

La sesión de Harness permanece `ONLINE` solo mientras `mcode` mantenga el
proceso MCP/bridge vivo. Una prueba one-shot (`mcode exec` o un `tools/list`
aislado) puede terminar y el watchdog marcará la sesión `OFFLINE`.

### Modo presence

Si MiniMax Code arranca el servidor MCP solo durante un turno y lo cierra
después, la sesión quedará `OFFLINE` entre turnos. Para mantener presencia
mientras trabajas, abre otra terminal en Windows y ejecuta:

```powershell
$env:HARNESS_URL="http://11.0.0.3:8444"
$env:HARNESS_SESSION_ID="<session_id>"
$env:HARNESS_SESSION_KEY="<session_key>"
python "D:\minimax\projects\desktop-organizer\harness_mcp_bridge.py" --presence
```

`--presence` abre el WebSocket y mantiene el heartbeat, pero no abre el
transporte MCP stdio. La sesión queda `ONLINE` para que Harness pueda asignarle
tareas. Cuando abras `mcode`, su bridge MCP se conecta y recoge la tarea activa.

Recomendaciones:

- inicia primero `--presence` y luego `mcode`;
- no reinicies el proceso presence mientras `mcode` esté conectado, porque puede
  tomar la conexión activa;
- si usas `mcode exec` one-shot, la tarea se ejecuta y luego la sesión puede
  volver a `OFFLINE` si no queda presencia.

## Ejecución

Dentro del repositorio del proyecto:

```bash
mcode
```

O en modo headless:

```bash
mcode exec "Implementa la tarea activa de Harness y ejecuta sus tests"
```

El agente puede usar las tools MCP de Harness:

- `harness_get_active_contract_and_task`
- `harness_get_task_contract`
- `harness_request_context_pack`
- `harness_validate_symbols`
- `harness_submit_task_changes`
- `harness_checkpoint_task`
- `harness_resume_task`
- `harness_split_task`
- `harness_report_checkpoint_failure`
- `harness_rag_*`

## Perfil de modelo

Migración `0018` crea el perfil:

```text
name: minimax-m3.1-flash
context_window: 1.000.000
max_input: 128.000
max_output: 32.000
reserved: 840.000
tokenizer: heurístico conservador
```

El perfil se usa para el presupuesto de tareas y el Context Pack. El modelo real
de MiniMax Code se selecciona en `mcode` (`/model` o `provider add`).

Si se desea tokenizer exacto/calibrado, se puede configurar más adelante con la
API de tokenizers; el estándar MiniMax no expone `/tokenize`, por lo que la
calibración se haría con `usage` de la API compatible.

## Flujo de control

```text
Harness2
  ├─ sesión MiniMax Code (proyecto aislado)
  ├─ TASK_ASSIGNED con contrato
  ├─ Context Pack por símbolos
  ├─ splitter si excede presupuesto
  ├─ checkpoint/resume
  └─ Git Guardian + War Room

MiniMax Code
  ├─ mcode TUI/headless
  ├─ MCP bridge
  └─ proveedor MiniMax o compatible
```

## Límites

- Harness2 no controla la cuenta ni el consumo de MiniMax; eso queda en `mcode`.
- La sesión de Harness controla el proyecto, la tarea, el contexto y la evidencia.
- Para aislamiento estricto, `.mcp.json` debe apuntar al `HARNESS_SESSION_ID` de la
  sesión correcta.
- Si se usa otra cuenta/proveedor, el contrato y la memoria siguen siendo de
  Harness2, pero el modelo y el costo son del proveedor configurado en `mcode`.
