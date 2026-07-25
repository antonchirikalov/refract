# SPEC — движок декларативных агентных пайплайнов (v0.1, rev.2)

> Назначение: единственный источник правды для генерации кода в Claude Code CLI.
> Читатель — LLM-имплементатор. Всё, что не определено здесь, — решение имплементатора,
> но оно НЕ должно нарушать инварианты (§2) и ограничения v0.1 (§16).
> rev.2: исправлены находки аудита (изоляция ревизий, node-ledger, именование портов,
> архитектура opencode-адаптера, перечень кодов ошибок и др.).
> Рабочее имя: `refract` (пакет `refract`, CLI `refract`) — заменяемо глобальным rename.

---

## 1. Обзор

Система исполняет декларативные пайплайны из LLM-агентов. Граф описан в `pipeline.yaml`,
исполняется универсальным движком (никакой кодогенерации). Агенты — пакеты со строгим
контрактом входов/выходов (типизированные порты). Все hand-off'ы — файлы на диске.
Каждый запуск (run) — изолированный, воспроизводимый, резюмируемый.

```
UI (фаза 2)  ←REST/WS→  Engine (Python)
                          ├─ Graph loader + Validator
                          ├─ Scheduler (asyncio)
                          ├─ State ledger (nodes + steps) + Events
                          ├─ Artifact store + Gates
                          └─ AgentRuntime (интерфейс)
                               ├─ OpencodeRuntime (opencode)
                               └─ MockRuntime (тесты)
```

Спека полностью покрывает фазы 0–1 (движок + CLI) и фиксирует контракты фаз 2–3.

**spectra** — референс-проект (https://github.com/antonchirikalov/spectra): его три пайплайна
(Extract, Discovery, Solution Design) и агенты служат приёмочными сценариями миграции (§17);
его CLI-вывод — образец для `refract status`/heartbeat.

## 2. Инварианты (нарушение любого = баг)

- **I1. Изоляция шага.** Агент видит только workdir своего шага: входы в `input/`, выход в `output/`. В промпт не попадает ни один путь вне workdir. Всё, что нужно агенту из других шагов (включая предыдущие раунды loop), движок материализует в его `input/`.
- **I2. Иммутабельность завершённого.** Каталог шага в статусе `done`/`reused` не модифицируется. Повторное исполнение шага (retry, force, новый attempt) архивирует прежнее состояние в `attempts/<n>/` внутри каталога шага (§10.2) — история не теряется.
- **I3. Атомарный леджер.** `state.json` пишется только движком, только атомарно (`tmp` + `os.replace`), одной записью на изменение.
- **I4. Управление — только через типизированные артефакты.** Решения движка (цикл, выбор, ожидание человека) принимаются ТОЛЬКО чтением JSON-артефактов типов `verdict@v1` / `selection@v1` / `question@v1`. Парсинг маркеров из свободного текста запрещён.
- **I5. Контракт — источник правды.** Секции промпта про входы/выходы генерируются движком из `agent.yaml`; в `prompt.md` автора их нет.
- **I6. Множественность — знание движка.** Агент всегда «одна задача за запуск». Fan-out делают map-ноды; агент не может produces коллекцию. (Consumes коллекцию — может, это fan-in чтением: §6.)
- **I7. UI/CLI — проекции.** Всё отображаемое выводится из `state.json` + `events.jsonl`.
- **I8. Минимизация секретов (уровень рана).** В окружение раннера попадают только ключи провайдеров моделей, разрешённых в снапшоте рана, и токены MCP из needs задействованных агентов. В папку проекта, артефакты и промпты секреты не попадают никогда.
- **I9. Отладочный след (agent-шаги).** Для каждого шага с раннером сохраняются `prompt.md`, `raw.txt`, `agent.events.jsonl` (каждой попытки — через `attempts/`). Builtin-шаги сохраняют только свои выходы и запись в леджере.
- **I10. Дисциплина фаз.** Функциональность будущих фаз (§17) не реализуется раньше времени. Текущая фаза — в `PROGRESS.md`.

## 3. Техстек, packaging, конвенции

Python **3.11+**, менеджер — `uv`.

`pyproject.toml` (обязательная основа):

```toml
[project]
name = "refract"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "pydantic>=2.7", "pyyaml>=6", "jsonschema>=4.18", "httpx>=0.27",
  "typer>=0.12", "jinja2>=3.1",
]
[project.optional-dependencies]
api = ["fastapi>=0.111", "uvicorn>=0.30"]        # фаза 2
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.5", "mypy>=1.10"]
[project.scripts]
refract = "refract.cli:app"
[tool.pytest.ini_options]
asyncio_mode = "auto"
[tool.mypy]
strict = true
```

Конвенции:
- Все форматы файлов из спеки — pydantic-модели в `refract/models/`; ad-hoc парсинг YAML/JSON запрещён.
- Планировщик — asyncio; CLI (typer — синхронный) вызывает `asyncio.run(...)` на границе.
- JSON Schema: `jsonschema.Draft202012Validator` явно. Встроенные схемы — package data `refract/schemas/`.
- Тесты: pytest + pytest-asyncio, без сети и реальных LLM (только MockRuntime), без sleep-синхронизации.
- Windows first-class: `pathlib` только; UTF-8 явно во всех open/subprocess; символьные ссылки — через хелпер `link_or_copy()` (symlink, при неудаче copy) — единственная точка линковки в кодовой базе.
- Логи: stdlib `logging` в stderr; машинное — только events.jsonl (единственный writer — asyncio-задача с очередью, она же присваивает `seq`).
- Идентификаторы/код/комментарии — английский. Репозиторий — git; коммит на фичу; перед нетривиальным коммитом — субагент `code-reviewer`.

## 4. Структура репозитория

```
refract/
  pyproject.toml
  PROGRESS.md                # чеклист фаз §17; создаётся при bootstrap, ведётся постоянно
  SPEC.md  CLAUDE.md
  .claude/agents/            # spec-auditor, test-engineer, code-reviewer
  refract/
    models/                  # pydantic всех форматов
    registry.py              # реестр типов артефактов (+ инжекция встроенных типов)
    graph.py                 # загрузка/валидация pipeline.yaml, топосортировка
    scheduler.py             # исполнитель
    steps.py                 # жизненный цикл одного шага
    metanodes.py             # loop / select
    state.py                 # леджер, resume, reuse
    artifacts.py             # материализация входов/выходов, коллекции, гейты
    prompt.py                # сборка промпта
    templates/               # jinja2-шаблоны движка (inputs, outputs, revision, gate_feedback)
    schemas/                 # встроенные JSON-схемы (verdict, selection, question, answer)
    runtime/  base.py opencode.py mock.py
    builtins/ __init__.py scanner.py     # реестр builtin-нод + реализации
    security.py  events.py  cli.py
    api/                     # фаза 2
  library/
    types/artifact_types.yaml
    types/schemas/           # все пользовательские JSON-схемы (единственное место)
    agents/<name>/
    templates/extract.yaml discovery.yaml solution_design.yaml
  examples/demo-project/     # смоук: project.yaml + pipelines/demo.yaml + input/ (2 txt);
                             # использует агента library/agents/demo_writer (агенты резолвятся только из library)
  tests/
```

`PROGRESS.md` bootstrap-содержимое: таблица фаз §17 со статусами + чеклист секций спеки
текущей фазы. Обновляется в каждом коммите, закрывающем пункт.

## 5. Реестр типов артефактов

`library/types/artifact_types.yaml`:

```yaml
version: "0.1"
types:
  source@v1:        { kind: any }
  extract@v1:       { kind: file, format: json, schema: extract.schema.json }
  requirements@v1:
    kind: file
    format: markdown
    rules:
      - { rule: regex, pattern: "^# Requirements:", flags: "m" }
      - { rule: regex, pattern: "FR-\\d+" }
  design_doc@v1:    { kind: file, format: markdown, rules: [{ rule: min_length, value: 2000 }] }
  discovery_report@v1: { kind: file, format: markdown }
```

Поля записи: `kind ∈ {file, dir, any}`; `format ∈ {json, markdown, text}` (только для file;
закрытый набор v0.1); `schema` — имя файла в `library/types/schemas/` (только для json);
`rules` — список из закрытого набора: `regex {pattern, flags?}` (по содержимому),
`min_length {value}` (символов) (только для file); `inline: bool` (default false) —
разрешение инлайнить содержимое в промпт при размере < 4 KB.

> CHANGED (2026-07-20): `rules` явно ограничены `kind=file` (правила применяются к
> содержимому; для `dir`/`any` бессмысленны). Согласует SPEC с моделью
> `ArtifactTypeDef` (аудит §5). Инвариант поведения не меняется.

**Встроенные управляющие типы** — НЕ в файле реестра: движок инжектит их при загрузке
(`verdict@v1`, `selection@v1`, `question@v1`, `answer@v1`; kind file, format json, inline true,
схемы из `refract/schemas/`). Объявление этих имён в пользовательском реестре →
ошибка `E_RESERVED_TYPE`.

Встроенные схемы:

```json
// verdict@v1
{ "type":"object", "required":["verdict"], "properties":{
  "verdict":{"enum":["approved","revise"]},
  "issues":{"type":"array","items":{"type":"object","required":["note"],
    "properties":{"section":{"type":"string"},"note":{"type":"string"}}}} } }
// selection@v1
{ "type":"object", "required":["winner"], "properties":{
  "winner":{"type":"string"}, "rationale":{"type":"string"} } }
// question@v1
{ "type":"object", "required":["question"], "properties":{
  "question":{"type":"string"}, "context":{"type":"string"},
  "options":{"type":"array","items":{"type":"string"}} } }
// answer@v1
{ "type":"object", "required":["answer"], "properties":{"answer":{"type":"string"}} }
```

**Коллекции.** `collection<X>` — конструктор типов (не запись реестра): каталог с манифестом
`_collection.json`:

```json
{ "type": "collection<extract@v1>",
  "items": [
    { "slug": "rfp-doc", "source": "rfp.pdf", "source_hash": "sha256:...",
      "status": "ok", "path": "rfp-doc/", "error": null }
  ],
  "stats": { "total": 2, "ok": 1, "failed": 1 } }
```

`path` — каталог элемента относительно каталога коллекции; внутри — payload элемента
в стандартном именовании §10.4. `status ∈ {ok, failed}`; failed-элементы присутствуют
в items и stats (payload может отсутствовать).

**Slugify** (одна функция `slugify(s)`): lowercase, `[^a-z0-9]+ → -`, обрезка `-` по краям,
коллизии → суффикс `-2`, `-3`. Слаг модели: `slugify(provider) + "_" + slugify(model_id)`
(пример: `kimi_kimi-k3`).

**Совместимость рёбер.** `T → T` (точное имя с версией). `collection<X>` соединим:
(а) со входом `collection<X>`; (б) со входом `X` — только через `map:` (§8). Иное — `E_TYPE_MISMATCH`.

## 6. Пакет агента

```
library/agents/source_processor/
  agent.yaml
  prompt.md            # системный промпт; чистый текст, без jinja
  revision_hint.md     # опционально: добавка к revision-промпту в loop
```

(Схемы артефактов агентские пакеты НЕ содержат — все схемы в `library/types/schemas/`.)

```yaml
name: source_processor        # [a-z_][a-z0-9_]*, уникально в библиотеке
version: 1                    # ссылка из графа: source_processor@1
description: "Extracts requirements JSON from one source document"
consumes:
  - { port: source, type: source@v1 }            # тип может быть collection<X>
produces:
  - { port: extract, type: extract@v1 }
  - { port: clarification, type: question@v1, optional: true }   # HITL, фаза 3
needs: [read, vision, "mcp:pdf-reader"]
defaults: { timeout_s: 3600 }
```

Правила:
- `consumes`: типы — из реестра или `collection<X>`.
- `produces`: ровно один не-optional порт («основной выход»). Он МОЖЕТ быть управляющего типа — так устроены критики (`verdict@v1`) и селекторы (`selection@v1`). Collection-тип в produces запрещён (`E_AGENT_PRODUCES_COLLECTION`, I6).
- optional-портов — не более одного, только `question@v1` (v0.1).
- Capabilities (закрытый набор): `read, edit, vision, bash, webfetch, mcp:<server>`.

## 7. Проект и конфигурация приложения

```
my-project/
  project.yaml
  pipelines/*.yaml
  input/
  runs/run_<YYYYMMDD_HHMMSS>/
```

```yaml
# project.yaml
version: "0.1"
name: "Atlas RFP"
input: ./input
defaults: { model: kimi/kimi-k3 }
# capability confirmation policy (phase 3, §16.10) — optional
confirm: [bash]            # explicit capabilities that require human approval
confirm_tier: dangerous    # and/or every capability at/above this tier
```

> CHANGED (2026-07-25): added optional `confirm` / `confirm_tier` to `project.yaml`.
> SPEC §17 phase 3 named "тиры capabilities, подтверждения" without mechanics; the
> design is specified here and in §16.10. `confirm` lists capability names; `confirm_tier`
> is a threshold in the risk order `safe < moderate < dangerous` (see §16.10).

Конфиг приложения `~/.refract/`:

```yaml
# providers.yaml — ключ провайдера = префикс модели до первого "/"
providers:
  # встроенный в opencode провайдер — достаточно ключа
  openai: { api_key_env: OPENAI_API_KEY, max_concurrent: 4, models: [gpt-5.6] }
  # OpenAI-совместимый провайдер (Kimi/Moonshot) — нужны npm + base_url + каталог
  kimi:                       # Kimi For Coding plan; base_url зависит от типа ключа
    api_key_env: MOONSHOT_API_KEY
    max_concurrent: 4
    npm: "@ai-sdk/openai-compatible"
    base_url: "https://api.kimi.com/coding/v1"   # sk-kimi-… ключ (coding-план)
    models: [k3, k3-256k, kimi-for-coding, kimi-for-coding-highspeed]
library_path: /path/to/refract/library

# mcp.yaml
servers:
  pdf-reader: { command: ["npx", "-y", "@mcp/pdf-reader"], env: {} }
  tavily:     { url: "https://...", token_env: TAVILY_API_KEY }
```

Строка модели: `provider/model-id`; `provider` обязан быть ключом `providers`;
провайдер «доступен», если env-переменная непуста.

> CHANGED (2026-07-25): `ProviderConfig` расширен опциональными `npm` / `base_url` /
> `models`. Встроенным провайдерам opencode (openai) хватает `api_key_env`; кастомным /
> OpenAI-совместимым (Kimi через Moonshot) нужны `npm` (пакет ai-sdk) и `base_url`, а
> `models` — каталог model-id этого провайдера (нужен opencode для OpenAI-совместимых и
> служит меню, из которого ноды назначают `model:`). Адаптер прокидывает их в
> per-step `opencode.json` (§12); секреты — только через `{env:VAR}` (I8).

**Приоритет модели** для agent-шага: (1) оверрайды запуска (`--model-for KEY=MODEL`,
KEY = `node_id` | `node_id.body` | `node_id.critic` | `node_id.selector`) →
(2) `model:` в ноде/суб-блоке → (3) `defaults.model` проекта. Если после этого модель
не разрешилась или провайдер недоступен — `E_MODEL_UNRESOLVED` / `E_PROVIDER_UNAVAILABLE`.
Исключение: `model: "@<select>.winner_model"` валидируется иначе (§8.1, скалярный биндинг).

## 8. pipeline.yaml

```yaml
version: "0.1"
name: extract
nodes:
  - id: scan
    type: builtin/scanner
    params: { exclude: ["plan", ".git", "outputs", "__pycache__"] }

  - id: extract
    type: agent
    agent: source_processor@1
    map: scan.sources
    params: { workers: 3, gate_retries: 2, on_item_failure: skip, min_ok: 1 }

  - id: refine
    type: loop
    params: { max_rounds: 5, on_max_rounds: pass }
    body:   { agent: requirements_writer@1, inputs: { extracts: extract.extract } }
    critic: { agent: requirements_critic@1, inputs: { doc: "@body", extracts: extract.extract } }
    outputs: { doc: "@body" }
```

Solution-design-паттерн:

```yaml
  - id: design
    type: agent
    agent: solution_designer@1
    inputs: { requirements: refine.doc }
    map_over: { models: ["kimi/kimi-k3", "openai/gpt-5.6"] }

  - id: choose
    type: select
    candidates: design.design_doc
    selector: { agent: solution_design_selector@1 }
    params: { fallback: first_ok }

  - id: sd_refine
    type: loop
    params: { max_rounds: 3 }
    body:
      agent: solution_designer@1
      model: "@choose.winner_model"
      inputs: { requirements: refine.doc, draft: choose.out }
    critic: { agent: solution_design_critic@1, inputs: { doc: "@body" } }
    outputs: { doc: "@body" }
```

### 8.1. Грамматика ссылок

- `inputs: {<localPort>: <nodeId>.<outPort>}` — ребро данных.
- `map: <nodeId>.<outPort>` — источник обязан быть `collection<X>`, произведённой НЕ map/map_over-нодой (v0.1, `E_NESTED_MAP`). Элемент биндится на единственный consumes-порт агента с типом `X`; если таких портов ≠ 1 — `E_MAP_PORT_AMBIGUOUS`. Остальные порты — через `inputs:`. `map` и `map_over` взаимоисключающи (`E_MAP_CONFLICT`).
- `map_over: {models: [...]}` — fan-out по моделям; выход — `collection<тип основного порта>`, слаг = слаг модели. Провайдеры всех моделей валидируются.
- Внутри loop: `@body` ≡ `@body.<основной порт>` — выход body текущего раунда. `loop.outputs` значения — только `@body[.<port>]`; тип выхода = тип порта body; после завершения резолвится в выход ПОСЛЕДНЕГО исполненного раунда. Ссылки снаружи внутрь loop запрещены.
- Скалярный биндинг: только `model: "@<selectId>.winner_model"`. `winner_model` существует, только если candidates произведены `map_over.models` (иначе `E_BINDING_ILLEGAL`). Биндинг создаёт зависимость планирования (нода ждёт select).

### 8.2. Параметры ноды (дефолты)

`workers` (3; только с map/map_over), `gate_retries` (2), `infra_retries` (2),
`timeout_s` (defaults агента → 3600), `on_item_failure: skip|fail` (skip), `min_ok` (1),
`model` (§7), `cache` (false; при true — warning `W_CACHE_UNSUPPORTED`, игнор).
Loop: `max_rounds` (3), `on_max_rounds: pass|fail` (pass). Select: `fallback: first_ok|fail`
(first_ok). Параметры loop/select-ноды (`gate_retries`, `timeout_s`, `infra_retries`)
наследуются суб-шагами; суб-блоки (`body:`, `critic:`, `selector:`) могут переопределять
`model` и `params`.

### 8.3. Валидация графа

Ошибки собираются списком `{code, node_id?, message}`. Коды (закрытый enum):

```
E_YAML  E_SCHEMA  E_DUP_NODE_ID  E_UNKNOWN_NODE_REF  E_UNKNOWN_PORT  E_UNKNOWN_AGENT
E_UNKNOWN_TYPE  E_RESERVED_TYPE  E_TYPE_MISMATCH  E_INPUT_MISSING  E_CYCLE
E_MODEL_UNRESOLVED  E_PROVIDER_UNAVAILABLE  E_MAP_CONFLICT  E_MAP_PORT_AMBIGUOUS
E_NESTED_MAP  E_LOOP_SHAPE  E_BINDING_ILLEGAL  E_AGENT_PRODUCES_COLLECTION  E_HITL_SHAPE
W_CACHE_UNSUPPORTED  W_SECURITY
```

Порядок проверок: pydantic-схема → id/ссылки/существование агентов, builtin'ов, типов →
совместимость рёбер и map-правила → все не-optional входы подключены → ацикличность
(loop — атомарная вершина) → разрешимость моделей (§7; scalar-binding пропускается,
вместо него проверяются провайдеры `map_over.models` соответствующего select-источника) →
ограничения §16 → security warnings (`W_SECURITY`: нода, чей вход достижим от
builtin/scanner, имеет `bash`/`webfetch`/`mcp:*`). Warnings не блокируют.

## 9. Run: каталог, леджер, события

```
runs/run_20260719_101500/
  snapshot/
    pipeline.yaml            # копия
    resolved.yaml            # см. ниже
    agents/<name>@<ver>/     # ПОЛНЫЕ копии задействованных пакетов агентов
    agents.lock.json         # {"source_processor@1": "sha256:..."} — hash пакета
  state.json
  events.jsonl
  steps/<node_id>/...        # §10
  .active.lock               # pid; наличие = ран активен (энфорс «один активный ран»)
```

`resolved.yaml` — pipeline.yaml, где у каждой ноды/суб-блока проставлены эффективные
`model` и все params (дефолты заполнены). Исполнение и resume читают ТОЛЬКО snapshot.
Hash пакета: sha256 отсортированных строк `"<relpath>:<sha256(file)>"`.

**Step ID** и каталоги:

| Вид | step_id | каталог |
|---|---|---|
| обычная agent/builtin нода `write` | `write` | `steps/write/main/` |
| map-элемент | `extract:rfp-doc` | `steps/extract/rfp-doc/` |
| loop body / critic, раунд n | `refine.body:r2` / `refine.critic:r2` | `steps/refine/body_r2/`, `steps/refine/critic_r2/` |
| селектор | `choose.selector` | `steps/choose/selector/` |

**state.json** — ДВА уровня: шаги и ноды:

```json
{ "run_id": "run_20260719_101500", "status": "running",
  "pipeline": "extract", "created_at": "...", "finished_at": null,
  "reuse_from": null, "force_nodes": [],
  "nodes": {
    "scan":    { "status": "done",    "error": null },
    "extract": { "status": "running", "error": null },
    "refine":  { "status": "pending", "error": null },
    "choose":  { "status": "pending", "error": null, "winner": null, "winner_model": null }
  },
  "steps": {
    "extract:rfp-doc": { "node": "extract", "status": "done", "outcome": "ok",
      "tries": 1, "started_at": "...", "finished_at": "...", "error": null }
  } }
```

Статусы шага: `pending → running → done | failed | waiting_human | cancelled`, плюс `reused`.
Outcome шага: `ok | failed_validation | failed_agent | failed_infra | timeout`. Триггеры:
`failed_validation` — гейт исчерпал `gate_retries`+1 попыток; `failed_infra` — раннер
исчерпал `infra_retries` (StepResult.completed=False); `timeout` — превышен `timeout_s`;
`failed_agent` — раннер сообщил ошибку исполнения агента, либо (фазы 0–2) в выходе
валидный `question.json` («interactive not supported yet»).

Статусы ноды: `pending → running → done | failed | skipped`, плюс `reused`
(и `waiting_human` — фаза 3). Нода `done` только после сборки её выходов (для map —
после записи `_collection.json`); это делает сборку идемпотентной при resume: если все
шаги done, а нода нет — пересобрать. `skipped` — нода, недостижимая из-за failed выше.
Ошибки нод — `{status: failed, error}`, без outcome-таксономии.

Крэш-восстановление при загрузке леджера: `running → pending` (шаги и ноды).

Статусы рана: `created → validating → running → completed | failed | cancelled`;
`running ⇄ paused`; `waiting_human` — фаза 3. Терминальность: нет исполняемых и running
шагов → `completed`, если нет failed-нод, иначе `failed` (недостижимые ноды — `skipped`).
Ctrl+C/`cancel`: graceful — новые шаги не стартуют, in-flight ждём `min(30s, остаток timeout)`,
затем kill; их шаги → `cancelled`; ран → `cancelled`.

**events.jsonl** (append-only; писатель один):

```json
{"seq": 41, "ts": "2026-07-19T10:15:22Z", "type": "step_state_changed",
 "step_id": "extract:rfp-doc", "payload": {"from": "running", "to": "done", "outcome": "ok"}}
```

Типы и payload: `run_state_changed {from,to}`; `step_state_changed {from,to,outcome?}`;
`node_state_changed {node_id,from,to}`; `heartbeat {step_id,elapsed_s}`;
`tool_call {step_id,tool,summary}` (если раннер отдаёт); `log {level,message}`;
`question {step_id, question}` (фаза 3). WS фазы 2: `?from_seq=` → replay + live.

## 10. Исполнение

### 10.1. Материализация входов

Все входы — по подкаталогу на порт: `input/<port>/`.

- Одиночный артефакт kind=file: `input/<port>/<port>.<ext>` (§10.4).
- kind=dir|any: содержимое в `input/<port>/` (файл — под своим именем; каталог — содержимое).
- Коллекция: `input/<port>/_collection.json` + подкаталоги элементов `<slug>/` (payload по §10.4). Несколько коллекций на разных портах не конфликтуют.
- Map-элемент: в `input/<port>/` — payload элемента + `_item.json` `{slug, source, source_hash}`.
- Материализация — `link_or_copy()`, целёвое дерево делается read-only на best-effort.

### 10.2. Жизненный цикл шага (steps.py — единственная реализация)

1. Создать каталог шага; материализовать входы (I1).
2. Собрать промпт (§11) → `prompt.md`.
3. `runtime.run_step(StepSpec)` с таймаутом.
   Инфра-ошибка (completed=False) → backoff-ретрай (base 2s, ×2, jitter, max 60s) до `infra_retries`; счётчик отдельный от гейта.
4. HITL-проверка (фаза 3; в фазах 0–2 наличие валидного `question.json` → `failed/failed_agent`).
5. **Гейт**: для каждого не-optional produces-порта: файл/каталог существует **и непуст** (dir — хотя бы один элемент, file — ненулевой размер); json → парс + схема; rules — все. Провал → `gate_report.json`; попыток < `gate_retries`+1 → **архивировать попытку** и повторить с добавкой `gate_feedback` (§11); иначе `failed/failed_validation`.

> CHANGED (2026-07-24): гейт для `dir`/`any` требует непустоту, не только
> существование каталога. Без этого агент, «успешно» ничего не создавший (напр.
> illustrator, у которого упал image-бэкенд), проходил как `ok`. Выявлено живым
> прогоном illustrator → paperbanana.
6. Успех → `done/ok`; запись в леджер (I3); событие.

**Архив попытки**: перед каждым повторным запуском (гейт-ретрай, resume `--retry-failed`,
`--force-step`) текущие `prompt.md, raw.txt, agent.events.jsonl, output/, gate_report.json`
перемещаются в `attempts/<n>/` (n = номер завершённой попытки); `output/` создаётся заново.
Это согласует ретраи с I2/I9.

### 10.3. Map, loop, select

**Map**: разворачивается в шаги по items входной коллекции со status=ok; failed-элементы
входа НЕ исполняются, но копируются в выходную коллекцию со status=failed (учёт в stats).
Параллелизм: `min(workers, свободные слоты провайдера)` (семафор per-provider из
`providers.yaml.max_concurrent`). Завершение: собрать `_collection.json` выхода в
`steps/<node>/_out/<port>/` (элементы — link_or_copy из output шагов); `ok < min_ok`
или (`on_item_failure: fail` и есть failed) → нода failed.

**Loop**: `body:r1 → critic:r1 → [body:r2 …]`. Перед body r≥2 движок материализует
в `input/` шага: `input/_previous/<port>.<ext>` — выход body r-1, `input/_verdict/verdict.json` —
вердикт r-1 (I1: только относительные пути). Промпт-добавка `revision` (§11). Критик
обязан выдать `verdict@v1` (гейт). `approved` → выходы ноды = выход body последнего раунда;
`revise` на `max_rounds` → `pass` (warning-событие, взять последнюю версию) или `fail`.
Номер раунда выводится из леджера. Сборка выхода ноды: `steps/<loopId>/_out/<outName>.<ext>` —
link_or_copy выхода body последнего раунда (идемпотентна при resume, как у map).
Тип основного порта critic обязан быть `verdict@v1` — проверяется валидатором (`E_LOOP_SHAPE`).

**Select**: единственный выходной порт всегда называется `out`, тип = `X` при candidates
`collection<X>`. Тип основного порта селектора обязан быть `selection@v1` (`E_TYPE_MISMATCH`).
Вход-коллекция; 0 ok-элементов → нода failed (error="no ok candidates").
1 ok-элемент → селектор не исполняется; узел-шаг не создаётся; нода done, выход = элемент.
Иначе шаг `<id>.selector`; `selection.winner` обязан совпасть с одним из ok-slug (иначе
это failed_validation селектора, идёт в гейт-ретраи); после исчерпания ретраев —
`fallback: first_ok` (первый ok по порядку items; warning-событие) или нода failed.
Выход ноды: `steps/<id>/_out/out.<ext>` — link_or_copy артефакта победителя;
экспорты `winner`, `winner_model` — в node-записи леджера: `{"winner": "kimi_kimi-k3", "winner_model": "kimi/kimi-k3"}`.

### 10.4. Именование артефактов по портам

| kind/format | путь |
|---|---|
| file/json | `<dir>/<port>.json` |
| file/markdown | `<dir>/<port>.md` |
| file/text | `<dir>/<port>.txt` |
| dir, any | `<dir>/<port>/` (содержимое внутри) |

`<dir>` = `output/` шага или каталог элемента коллекции. Агенту путь сообщается в промпте
(генерируется из контракта, I5).

### 10.5. Планировщик, resume, reuse

Нода готова, когда все ноды-источники её входов (включая binding-зависимости) в
`done|reused`. Готовые исполняются конкурентно.

**Resume** (`refract resume <run_dir>`): загрузить snapshot + леджер; `running→pending`;
продолжить. `--retry-failed`: `failed→pending` (шаги). `--force-step <step_id>`: шаг →
pending (с архивацией §10.2); его нода и все зависимые ноды вниз → pending
(их done-шаги остаются, но выходные сборки нод пересобираются).

**Reuse / rerun-from-node** (`refract rerun <project> --from NODE [--reuse RUN|last]`):
новый ран. Множество перевычисления R = `{NODE} ∪ descendants(NODE)` ∪ ноды, чей вход
изменился транзитивно. Builtin-ноды исполняются всегда. Map-элементы диффятся по
`(slug, source_hash)` против reuse-рана: изменённые/новые исполняются, прочие — шаги
`reused` (артефакты link_or_copy из старого рана). Нода вне R и без изменённых элементов →
все шаги reused, нода reused. `force_nodes` в state.json хранит NODE-список (node id;
не путать со step id у `--force-step`).

## 11. Сборка промпта

Конкатенация (jinja2-шаблоны движка `refract/templates/`):

1. `prompt.md` агента (system-часть, если раннер различает; иначе префикс).
2. **Inputs-секция** (из контракта, I5): на порт — относительный путь §10.1; коллекция — инлайн `_collection.json` при items ≤ 50, иначе stats + первые 50 + путь; управляющие типы и `inline: true`-типы < 4 KB — инлайн содержимого; прочее содержимое НЕ инлайнится.
3. **Outputs-секция**: на produces-порт — путь §10.4 + человекочитаемая выжимка схемы/правил (генерируется из реестра).
4. Контекстные добавки: `revision` (пути `input/_previous/…`, инлайн вердикта, `revision_hint.md` если есть), `gate_feedback` (содержимое gate_report.json), `clarification` (фаза 3).

## 12. AgentRuntime

```python
@dataclass
class StepSpec:
    step_id: str
    agent_dir: Path            # пакет из snapshot/agents/
    model: str                 # "provider/model-id"
    workdir: Path
    prompt: str                # task-промпт (§11 п.2-4); system - в пакете
    system_prompt: str         # содержимое prompt.md
    needs: list[str]
    timeout_s: int

@dataclass
class StepResult:
    completed: bool            # False = инфра-ошибка (ретраить)
    agent_error: str | None    # completed=True, но агент упал → failed_agent
    usage: dict | None

class AgentRuntime(Protocol):
    async def run_step(self, spec: StepSpec,
                       on_event: Callable[[dict], None]) -> StepResult: ...
    async def close(self) -> None: ...
```

Ответственность за след (I9): **адаптер** пишет `raw.txt` и `agent.events.jsonl` в workdir;
движок пишет `prompt.md`. Результат агента движок оценивает по файлам `output/` (гейт),
не по StepResult.

**Секреты (I8)**: раннер получает env один раз на ран — объединение ключей провайдеров
всех моделей из `resolved.yaml` + MCP-токены из needs задействованных агентов. Per-step
сужение невозможно с shared-сервером и НЕ требуется (I8 сформулирован на уровне рана).

**OpencodeRuntime**: пиновая версия opencode фиксируется константой
`OPENCODE_PINNED_VERSION` и проверяется при старте (`opencode --version`, warning при
расхождении). Компиляция пакета на шаг: `<workdir>/<AGENTS_SUBDIR>/<name>.md`
(AGENTS_SUBDIR — константа адаптера, определяемая пиновой версией: `.opencode/agent/`
или `.opencode/agents/`) с frontmatter {model, tools из маппинга needs} и телом =
system_prompt; `<workdir>/opencode.json` — провайдер модели + MCP из needs (из
`~/.refract/mcp.yaml`). Требование I1 — файловый доступ агента ограничен workdir —
обязанность адаптера; допустимые стратегии: per-session directory (если пиновая версия
поддерживает) или отдельный serve-процесс на шаг с cwd=workdir. Выбор — деталь адаптера
за интерфейсом; спека требует только I1 + heartbeat-события каждые ~10 с в `on_event` +
авто-approve permissions. Интеграция с реальным opencode тестами не покрывается (§18) —
ручной смоук-рецепт обязателен в `docs/opencode-smoke.md`.

**MockRuntime**: сценарий `dict[pattern, list[ScriptedResponse]]`, pattern — fnmatch по
step_id. `ScriptedResponse`: файлы для записи в `output/` | `completed=False` |
`agent_error`. Повторные вызовы шага берут следующий элемент списка. Пишет стабовый
`raw.txt` и минимальный `agent.events.jsonl`.

## 13. Builtin-ноды

Реестр в коде: `refract/builtins/__init__.py`:
`BUILTINS: dict[str, BuiltinDef]`, `BuiltinDef = {params_model: type[BaseModel], produces: [{port, type}], run: async fn}`.
Валидатор берёт порты отсюда.

`builtin/scanner`: `produces: [{port: sources, type: collection<source@v1>}]`.
Params: `exclude: list[str]` (точное имя, только верхний уровень input-папки),
`input: str|None` (оверрайд пути). Каждый файл верхнего уровня и каждая подпапка →
элемент source@v1 (подпапка целиком = один элемент). `source_hash`: файл — sha256
содержимого; папка — sha256 отсортированных пар `(relpath, sha256(file))` (mtime НЕ
участвует). Исполняется в `steps/scan/main/` без раннера; детерминирован.

> CHANGED (2026-07-25): элементы верхнего уровня, чьё имя начинается с `.`, скэннер
> пропускает (в дополнение к `exclude`). Причина — боевой прогон Extract: `refract init`
> оставлял `input/.gitkeep`, скэннер делал из него источник, и map тратил LLM-вызов на
> пустой файл. Dot-элементы (`.gitkeep`, `.DS_Store`, `Thumbs.db`-класс артефактов
> тулинга) источниками не бывают. `init` больше не создаёт `.gitkeep` (§14: «пустой
> `input/`»).

## 14. CLI (фазы 0–1)

```
refract init     <project_dir> --template NAME [--name NAME] [--model PROVIDER/ID] [--force] [--input PATH]
refract templates
refract validate <project_dir> [--pipeline NAME]
refract run      <project_dir> [--pipeline NAME] [--model-for KEY=MODEL]... [--workers-for NODE=N]...
refract status   <run_dir>
refract resume   <run_dir> [--retry-failed] [--force-step STEP_ID]
refract rerun    <project_dir> --from NODE_ID [--reuse RUN_ID|last]   # default: last
refract answer   <run_dir> <step_id> <text>
refract agents   list
refract catalog  [--json]                                            # §19.1 (фаза 4)
```

> CHANGED (2026-07-25): added `refract init` / `refract templates` (authoring
> ergonomics) and documented `refract answer` (§16.10/HITL). `init` copies a
> `<library>/templates/<name>.yaml` into `<project>/pipelines/` and writes a
> minimal `project.yaml` + empty `input/`; it is pure scaffolding (no validation),
> refusing to overwrite an existing `project.yaml` without `--force`. `templates`
> lists the stems under `<library>/templates/`.
>
> CHANGED (2026-07-25): `init --input PATH` — папка с документами может лежать где
> угодно и НЕ копируется (`project.yaml: input:` как есть); без флага проект получает
> свой пустой `input/`. Шаблоны резолвятся из двух источников — `<library>/templates/`
> и `<refract_home>/templates/` (пользовательские, SPEC-UI §5); `templates` печатает
> источник каждого.

`--pipeline` обязателен, если в `pipelines/` больше одного файла. Активный ран — по
`.active.lock` (pid жив → отказ старта нового рана в проекте). Прогресс в stdout —
heartbeat-строки в стиле spectra.

## 15. REST/WS API (фаза 2 — контракт)

```
GET/POST /api/projects
GET  /api/projects/{id}/pipelines ; GET/PUT /api/projects/{id}/pipelines/{name}   # PUT при активном ране → 409
GET  /api/catalog                              # каталог блоков, §19 (фаза 4)
POST /api/projects/{id}/pipelines/{name}/validate
POST /api/projects/{id}/runs {pipeline, overrides?, reuse_from?, force?}
GET  /api/runs/{run_id}                        # state.json
GET  /api/runs/{run_id}/steps/{step_id}/artifacts[/{path}]
POST /api/runs/{run_id}/cancel | /pause | /resume
POST /api/runs/{run_id}/answers {step_id, answer}          # фаза 3
GET  /api/models ; GET /api/fs/browse?path=
WS   /api/runs/{run_id}/events?from_seq=N
```

## 16. Ограничения v0.1 (энфорсятся валидатором либо рантаймом, как указано)

1. Один активный ран на проект (`.active.lock`); правка pipeline при активном ране → 409/ошибка.
2. Control flow — только `loop` и `select`; условных рёбер нет.
3. `loop.body` / `loop.critic` — ровно по одному агенту (`E_LOOP_SHAPE`); вложенные loop/map запрещены.
4. `map:` не может ссылаться на коллекцию, произведённую map/map_over-нодой (`E_NESTED_MAP`); один map/map_over на ноду.
5. Скалярный биндинг — только `@<select>.winner_model` в `model:` (`E_BINDING_ILLEGAL`).
6. `cache: true` → `W_CACHE_UNSUPPORTED`, игнор.
7. produces с collection-типом → `E_AGENT_PRODUCES_COLLECTION`.
8. Управляющие типы — встроенные, неизменяемые (`E_RESERVED_TYPE`).
9. HITL-порт — максимум один, тип question@v1 (`E_HITL_SHAPE`); исполнение — фаза 3, до неё `question.json` в выходе → `failed/failed_agent`.

### 16.10 Подтверждение sensitive capabilities (фаза 3)

> CHANGED (2026-07-25): раздел добавлен. §17 фаза 3 назвала «тиры capabilities,
> подтверждения» без механики — механика специфицирована здесь.

**Тиры риска.** Каждая capability имеет тир в порядке `safe < moderate < dangerous`:
`read`, `vision` → safe; `edit`, `webfetch`, `mcp:<server>` → moderate; `bash` → dangerous.
Неизвестная capability → moderate.

**Политика.** `project.yaml` может задавать `confirm` (явный список capabilities) и/или
`confirm_tier` (порог). Confirm-набор рана = `confirm` ∪ {все capabilities на/выше
`confirm_tier`, которые нужны используемым в пайплайне агентам}.

**Механика (переиспользует HITL-паузу).** Это pre-execution гейт на уровне ноды, ДО
материализации входов, поэтому статус `waiting_human` выставляется напрямую, а не через
per-attempt lifecycle шага (§10.2). Для plain agent-ноды, чей агент нуждается в
capability из confirm-набора:
- при первом запуске движок пишет `steps/<node>/main/confirm/request.json`
  (`{node, agent, capabilities}`), выставляет шаг/ноду в `waiting_human`, эмитит событие
  `question`, и возвращает управление;
- `refract answer <run> <node> <text>` (или API `/answers`) интерпретирует ответ:
  утвердительный (`approve`/`yes`/`ok`/`да`/…) → `confirm/decision.json`
  `{approved: true, answer}`, иначе `{approved: false, answer}`;
- на resume: если `decision.approved` — гейт пропускается и агент исполняется один раз
  (идемпотентно, `confirm/` не архивируется в `attempts/`); если `approved: false` —
  нода становится `failed`. Решение движка берётся из явного булева `approved`, а не из
  наличия/парсинга текста (совместимо с I4).

Подтверждение реализовано только для plain agent-нод. Confirm-требующий агент внутри
`map`/`map_over`/`loop`/`select` → `NotImplementedError` (политика НЕ обходится молча).

## 17. Фазировка и критерии приёмки

**Фаза 0**: pyproject+scaffolding, PROGRESS.md, models, registry (+builtin-типы), graph+
валидатор (ВСЕ коды §8.3), scheduler (без loop/select), map (+агрегация, семафоры
провайдеров), scanner, steps (полный §10.2: гейт-ретраи с фидбэком, attempts, вся
outcome-таксономия), state (nodes+steps, resume, force-step), snapshot, prompt, runtime
base+mock+opencode-компиляция, CLI (validate/run/status/resume), examples/demo-project,
миграция source_processor + requirements_writer.
✅ Критерий: Extract без критика end-to-end на MockRuntime (golden-тест) и вручную на
реальном opencode; крэш-тест (обрыв во время map) → resume доводит; все тесты §18
фазы 0 зелёные; mypy/ruff чистые.

**Фаза 1**: metanodes (loop/select), map_over.models, winner_model-биндинг, rerun/reuse,
`refract rerun`, миграция остальных агентов spectra, три шаблона в library/templates,
docs/opencode-smoke.md.
✅ Критерий: три пайплайна spectra end-to-end; loop-тест revise→revise→approved и обе
ветки on_max_rounds; select-тест fallback и invalid winner; reuse-тест «добавили файл —
пересчитан только он и низ графа».

**Фаза 2**: api/ + WS; фронт — отдельная UI-спека. **Фаза 3**: HITL, тиры capabilities,
подтверждения (механика — §16.10). **Фаза 4**: каталог для билдер-LLM +
безопасная запись пайплайна — механика в §19 (патч-операции рассмотрены и
отклонены там же).

> DESIGN NOTE (2026-07-23, не реализовано): discovery-источник. Новый архетип входной
> ноды (условно `type: discover`) — второй легальный производитель `collection<X>` «из
> ничего», рядом со `builtin/scanner`. В отличие от scanner (детерминированный builtin без
> LLM над файлами `input/`), discovery принимает тему/бриф как обычный артефакт и backed
> рантаймом (LLM + MCP из `needs`, напр. tavily): сам ищет источники в сети и собирает
> `collection<source@v1>`. I6 не нарушается — коллекцию порождает движковая нода-источник,
> а не ординарный agent-исполнитель (как и у scanner). Весь низ графа (map → loop → select →
> гейты) не меняется: контракт выхода — та же типизированная коллекция, discover просто
> встаёт на место scan. Открытые вопросы для проработки: (1) reuse без детерминизма —
> discover либо всегда переисполняется (как builtins), диффя низ по хэшу скачанного
> контента, либо набор «замораживается» в снапшот; (2) детерминированные `slug` и
> `source_hash` для сетевых находок (из URL / по содержимому); (3) сборка выходной коллекции
> и обход agent-гейта «один основной порт» (как у map/scanner). Затрагивает reuse-семантику
> и гейт — отдельная фаза, не Фаза 0–1.

## 18. Обязательные тесты (минимум; все — MockRuntime, без сети)

- `test_registry`: rules regex/min_length; inline-флаг; E_RESERVED_TYPE; неизвестный тип.
- `test_graph_validation`: по тесту на КАЖДЫЙ код §8.3, включая E_MAP_PORT_AMBIGUOUS, E_NESTED_MAP, E_BINDING_ILLEGAL и W_SECURITY.
- `test_scheduler`: топопорядок; конкурентность; семафор провайдера; binding-зависимость select→loop; skipped-ноды при failed.
- `test_map`: fan-out; on_item_failure/min_ok; failed-элементы входа попадают в выходную коллекцию; сборка _out; идемпотентная пересборка при resume.
- `test_step`: материализация входов (file/dir/collection/map-элемент с _item.json); гейт-провал → attempts/1 + gate_feedback → успех; таймаут; infra-ретраи отдельным счётчиком.
- `test_loop`: revise×2→approved; max_rounds pass и fail; невалидный verdict; материализация _previous/_verdict; вывод раунда из леджера.
- `test_select`: 1 кандидат насквозь (без шага селектора); победитель; winner мимо slug → гейт-ретрай → fallback; 0 ok → failed; winner_model в леджере.
- `test_state`: атомарность (крэш между write); running→pending (шаги и ноды); resume; force-step с архивацией; терминальность рана с failed/skipped.
- `test_reuse`: rerun-from-node; дифф map по source_hash; транзитивная инвалидация.
- `test_prompt`: генерация inputs/outputs из контракта; коллекция >50; инлайн-лимиты; revision и gate_feedback добавки.
- `test_opencode_compile`: пакет → agent-md + opencode.json (только генерация файлов, без запуска opencode).
- `test_cli`: validate exit codes; run на demo-project (MockRuntime через DI); status-таблица; lock активного рана.
- E2E golden: scanner→map(2 файла)→writer→loop(critic: revise→approved) на MockRuntime; полная проверка дерева run-каталога, state.json и events.jsonl.

## 19. Каталог и патч-операции графа (фаза 4)

> CHANGED (2026-07-25): раздел добавлен. §17 фаза 4 назвала «патч-операции графа,
> каталог для билдер-LLM» и вынесла механику «вне этой спеки» — механика
> специфицирована здесь. Причина: UI-редактор пайплайна (и билдер-LLM за ним) не
> может править `pipeline.yaml` целиком через `PUT` — он затирает чужие правки,
> теряет комментарии и не даёт валидировать НАМЕРЕНИЕ, только результат.

### 19.1. Каталог блоков

`GET /api/catalog`, `refract catalog [--json]` — один машинно-читаемый документ
«из чего можно собрать пайплайн». Собирается из уже существующих источников
(реестр типов §5, пакеты агентов §6, `BUILTINS` §13, модели мета-нод §8.1) — это
проекция, НЕ новый формат данных на диске.

```json
{
  "version": "0.1",
  "artifact_types": [{"id": "extract@v1", "kind": "file", "format": "json",
                      "inline": false, "rules": 2, "builtin": false}],
  "agents": [{"ref": "source_processor@1", "description": "...",
              "consumes": [{"port": "source", "type": "source@v1", "optional": false}],
              "produces": [{"port": "extract", "type": "extract@v1"}],
              "needs": ["read", "edit", "mcp:pdf-reader"], "max_tier": "moderate",
              "timeout_s": 1800}],
  "builtins": [{"type": "builtin/scanner", "produces": [...], "params_schema": {...}}],
  "node_kinds": [{"kind": "loop", "params_schema": {...},
                  "blocks": {"body": "agent", "critic": "agent"},
                  "required": ["body", "critic", "outputs"]}],
  "templates": ["extract", "discovery", "solution_design"],
  "constraints": [{"code": "E_NESTED_MAP",
                   "rule": "map: не может ссылаться на выход map/map_over-ноды"}]
}
```

`constraints` — проекция §16: каждый пункт назван КОДОМ валидатора, который он
вызовет. Билдер-LLM получает и правила, и словарь ошибок, на которых учится.
Каталог не содержит секретов и путей (I8): провайдеры и модели отдаёт `GET /api/models`.

### 19.2. Редактирование пайплайна: перезапись + верификация

> CHANGED (2026-07-25): §19 изначально специфицировал словарь патч-операций
> (`add_node`/`set_input`/…). Решение отменено ДО реализации: для UI-редактора он
> не даёт ничего, чего не даёт полная перезапись с верификацией. Разбор: (1)
> одновременные правки закрываются хэшом прочитанного файла, а не дельтами; (2)
> комментарии сохраняет клиент, который держит исходный текст — round-trip на
> сервере нужен только для дельт; (3) валидация «намерения» полностью покрыта
> валидацией результата — коды §8.3 и так структурные; (4) дельты выигрывают на
> больших файлах, а пайплайн — ~40 строк, LLM пишет его целиком надёжнее, чем
> собирает патч. Пятнадцать операций и round-trip YAML — сложность без выгоды.
> Вместе с ними отменена зависимость `ruamel.yaml`.

`PUT /api/projects/{id}/pipelines/{name}` — единственный способ записи. Требования:

1. **Валидация перед коммитом.** Тело валидируется полным валидатором §8.3.
   Блокирующие ошибки → файл НЕ пишется, ответ `409` с полным отчётом.
   `?allow_invalid=true` — сохранить черновик как есть (UI-«сохранить и продолжить
   править»); отчёт возвращается всё равно.
2. **Атомарность.** Запись только `tmp` + `os.replace` — как леджер (I3). Обрыв
   посреди записи не оставляет обрезанный `pipeline.yaml`.
3. **Оптимистичная блокировка.** Клиент передаёт `?base_hash=<sha256>` файла,
   который прочитал; несовпадение → `409 stale` (кто-то записал раньше). Без
   `base_hash` проверка не делается — CLI и скрипты не обязаны её знать.
4. **Активный ран** в проекте → `409` (§16.1), как и сейчас.
5. Ответ всегда: `{name, committed: bool, errors: [...], warnings: [...], hash}` —
   `hash` нового содержимого, чтобы клиент сразу имел свежий `base_hash`.

`POST .../validate` остаётся отдельно: проверить, не записывая (dry-run редактора).

### 19.3. Обязательные тесты фазы 4

- `test_catalog`: полнота (каждый агент/builtin/тип библиотеки присутствует); наличие
  `constraints` с кодами §8.3; отсутствие секретов и абсолютных путей.
- `test_pipeline_write`: невалидное тело → не записано + отчёт; `allow_invalid`
  записывает; атомарность (файл цел при сбое); `base_hash` mismatch → 409;
  активный ран → 409; успешный PUT возвращает свежий `hash`.
