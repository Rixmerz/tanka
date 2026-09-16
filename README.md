# Tanka — de Haiku a un asistente delegable y seguro

Tanka es un **harness para Claude Code** (plugin + workspace limpio + launcher) que convierte Claude Haiku en un asistente personal capaz de redactar y enviar correos, contestar mensajes, clasificar bandejas y usar servidores MCP **sin** los fallos típicos del modelo pequeño: loops, acciones sin confirmar, resultados inventados, reglas olvidadas y degradación por exceso de tools.

No modifica el modelo: controla el entorno. Las reglas importantes viven en hooks que deniegan o piden confirmación, no en el prompt.

- Investigación (documentación oficial, issues, foros): [`docs/research/`](docs/research/)
- Arquitectura y mapa limitación → mitigación: [`docs/architecture.md`](docs/architecture.md)

## Qué incluye

| Pieza | Ruta | Función |
|---|---|---|
| Plugin `tanka` | `plugin/` | Hooks (política, validación de envíos, anti-loop, objetivo, cierre verificado), skills (`setup`, `plan`, `draft`, `triage`, `status`), agente `tanka-verifier`, output style con el rol fijo. |
| Plantilla de workspace | `workspace-template/` | Directorio limpio con `.tanka/` (persona, política, MCP, objetivos) y `.claude/settings.json` restrictivo. |
| Launcher | `bin/tanka` | `init`, `start`, `run`, `doctor`, `test`. Arranca Claude Code aislado: sin settings de usuario, sin MCP ajenos, sin otros plugins. |
| Tests | `tests/` | 38 tests de hooks con entradas simuladas + servidor MCP falso para pruebas end-to-end. |

## Requisitos

- Claude Code ≥ 2.1 (`claude --version`), con sesión iniciada.
- `python3` (los hooks no usan dependencias externas).
- Linux/macOS/WSL. Bash para el launcher.

## Instalación rápida

```bash
git clone https://github.com/Rixmerz/tanka
export PATH="$PWD/tanka/bin:$PATH"

tanka init ~/tanka-workspace        # crea el directorio limpio
tanka start ~/tanka-workspace       # sesión interactiva con Haiku aislado
```

Dentro de la sesión:

```
/tanka:setup Kira                   # nombre, personalidad, idioma, firma…
/tanka:status                       # qué hay habilitado y qué está permitido
/tanka:plan encárgate de …          # objetivo con criterios de hecho antes de actuar
/tanka:draft responde a Ana …       # borrador → verificación → confirmación → envío
/tanka:triage revisa mi bandeja     # clasificación con rúbrica y confianza
```

La primera vez que arranques en el workspace, acepta el diálogo de confianza del directorio: sin él, Claude Code ignora las reglas `permissions.allow` del proyecto.

### Habilitar solo lo necesario

1. **MCP**: edita `~/tanka-workspace/.tanka/mcp.json` y añade únicamente los servidores de la tarea (ver `mcp.example.json`). El launcher usa `--strict-mcp-config`, así que nada más se carga. Con Haiku, más de ~3 servidores o ~15 tools degrada la elección de tool; el `SessionStart` avisa.
2. **Skills propias**: copia solo skills de asistencia (redacción, procesos, clasificación) a `~/tanka-workspace/.claude/skills/<nombre>/SKILL.md`. Las de `~/.claude/skills` no se cargan.
3. **Plugins**: ninguno salvo Tanka. `--setting-sources project,local` deja fuera los plugins habilitados en tu usuario.

### Persona y formato

`.tanka/persona.json` (o `/tanka:setup`): `name`, `user_name`, `language`, `tone`, `personality`, `output_format`, `signature`, `timezone`, `notes`. El rol de asistente y los invariantes de seguridad no se editan desde ahí: están en `plugin/output-styles/tanka.md` y en los hooks.

### Política

`.tanka/policy.json` sobreescribe parcialmente los valores por defecto de `plugin/scripts/tanka_common.py`:

```json
{
  "decisions": { "read": "allow", "draft": "allow", "modify": "ask", "send": "ask", "destructive": "deny", "unknown": "ask" },
  "send_validation": { "recipient_allowlist": ["@miempresa\\.com$"], "internal_domains": ["miempresa.com"] },
  "loop_guard": { "max_identical_calls_per_turn": 2, "max_calls_per_turn": 25, "max_consecutive_failures": 3 }
}
```

Los tools MCP se clasifican por nombre (`mcp__<servidor>__<tool>`): `delete|trash|remove…` → destructivo (siempre denegado), `send|reply|forward|post|share…` → envío (validado y con confirmación), `label|archive|update|move…` → modificación (confirmación), `get|list|search|read…` → lectura (libre). Lo que no encaja es `unknown` → confirmación. Ajusta `tool_classes` si tu servidor usa otros nombres.

### Tareas delegadas sin humano

```bash
tanka run "Clasifica los correos no leídos de hoy y deja borradores para los urgentes" \
  ~/tanka-workspace --objective triage-inbox --max-turns 20 --budget 0.50
```

Modo fail-closed: todo lo que pediría confirmación se deniega, `may_send` se fuerza a `false`, y hay tope de turnos y de gasto. Puede leer, clasificar y dejar borradores; nunca enviar ni borrar.

## Cómo mitiga cada limitación de Haiku

| Limitación observada | Mecanismo |
|---|---|
| Repite la misma llamada aunque el tool le diga que ya está hecha | `PreToolUse` deniega la 3ª llamada idéntica del turno; tope de acciones por turno/sesión; 3 fallos seguidos → parar |
| Inventa parámetros (destinatario, fecha) en vez de preguntar | Validación de envíos: placeholders, cuerpo mínimo, secretos, destinatarios bloqueados/allowlist |
| Afirma «enviado» sin haber llamado al tool | `Stop` hook bloquea el cierre sin `tool_result` exitoso de esa clase |
| Olvida reglas en conversaciones largas | Reglas duras reinyectadas cada turno; objetivo persistido en fichero; output style forzado |
| Se degrada con muchos tools / 200K sin compaction | Workspace limpio, `--strict-mcp-config`, aviso >3 servidores, `MAX_MCP_OUTPUT_TOKENS` bajo |
| Actúa de más (commits, envíos, borrados) | `send`/`modify` → confirmación; `destructive` → denegado; `may_send` por objetivo |
| Obedece instrucciones incrustadas en correos | Contenido externo = datos (rol) y ninguna acción irreversible sin humano |

## Desarrollo

```bash
tanka test                          # 38 tests de hooks
claude plugin validate plugin --strict
tanka doctor ~/tanka-workspace
```

Prueba end-to-end con un buzón simulado (`tests/fake_mcp_server.py`): ver `docs/architecture.md` § Modo delegado y `tests/README.md`.

## Instalar como plugin (alternativa al launcher)

```bash
claude plugin marketplace add Rixmerz/tanka
claude plugin install tanka@tanka --scope project
```

Sin el launcher solo actúa la segunda capa de aislamiento (settings del proyecto); los MCP y plugins de tu usuario seguirán cargándose.

## Limitaciones conocidas

- No hace a Haiku más capaz en planificación abierta. `TANKA_MODEL=sonnet tanka start` cambia el modelo sin tocar nada más.
- La clasificación por nombre de tool es heurística; revisa `tool_classes` para servidores con nombres opacos.
- Las skills no pueden usar inyección dinámica con `` !`comando` `` porque Bash está denegado; usan Read.
