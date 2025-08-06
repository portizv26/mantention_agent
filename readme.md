# AI Agent Chatbot – Workflow Guide

This **README** explains, end‑to‑end, how the refactored `AgentChat` class orchestrates language‑model reasoning, SQL data retrieval, and image generation to answer user questions. If you plan to extend or debug the agent, start here.

---

## 1  Bird’s‑Eye View

```
User (ES) ──► AgentChat.execute ──► LLM services ──►                   
                             │                  │                      
                             ▼                  ▼                      
                      DataService (SQL)   ImageService (OpenAI CI)     
                             │                  │                      
                             └───────► FileManager ◄───────────────────┘
                                             │                         
                                             ▼                         
                                artefacts (csv, png, code)             
```

* **AgentChat** is the façade; every Streamlit call arrives at `execute()`.
* **LLM** is a thin wrapper around OpenAI. It performs: translation, classification, action planning, and text generation.
* **DataService** and **ImageService** sit inside `AgentChat` as private helpers. They retrieve data and create visualisations respectively.
* **FileManager** persists every artefact under `chat_docs/<run‑id>/` so that the UI can offer downloads or re‑use cached data.
* **Artefacts** (dataclass) hold in‑memory references to the latest SQL query, `DataFrame`, image bytes, and Python code.

---

## 2  Execution Timeline

| #  | Phase                                          | Key Method(s)                                                      | Purpose                                                                                                                                                                                                          |
| -- | ---------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1  | **Translation**                                | `_translate`                                                       | Convert Spanish user text ► English for standardised prompting.                                                                                                                                                  |
| 2  | **Request Normalisation**                      | `_to_request`                                                      | Reduce the raw utterance to a concise, context‑free request.                                                                                                                                                     |
| 3  | **Classification**                             | `_classify` (`messageClassification`)                              | Decide whether the request is *on‑topic* and whether prior context is *sufficient*.                                                                                                                              |
| 4  | **Routing Decision**                           | `_get_actions` (`actionsRequired`)                                 | Ask the LLM what work is needed: new SQL, new image, both, or none.                                                                                                                                              |
| 5a | **Bad flow** (if off‑topic or missing context) | `_bad_flow`                                                        | Politely refuse or request clarification.                                                                                                                                                                        |
| 5b | **Good flow**                                  | `_good_flow`                                                       | Dispatch to one of three branches:<br>• `_answer_only_branch` – reuse cached data & image<br>• `_image_only_branch` – create image from cached data<br>• `_data_refresh_branch` – run new SQL (+ optional image) |
| 6  | **Answer Drafting**                            | `_create_final_answer`                                             | Synthesise human‑readable output combining request + data (and previous answer if any).                                                                                                                          |
| 7  | **Summarisation & Context Update**             | `_record_artefacts` → `_summarise_interaction` → `_update_context` | Store artefacts, produce a one‑sentence memory, and fold it into rolling context.                                                                                                                                |
| 8  | **Back‑translation**                           | `_translate`                                                       | Return the final answer in Spanish.                                                                                                                                                                              |

> **Retry logic:** The SQL path (`_supervised_sql`) attempts up to **3** iterations, each time feeding the failed query back to the LLM for refinement.

---

## 3  Component Deep‑Dive

### 3.1  LLM Wrapper (`LLM`)

| Method   | Model         | Responsibility                                     |
| -------- | ------------- | -------------------------------------------------- |
| `chat`   | `gpt‑4o‑mini` | Free‑form text generation.                         |
| `struct` | `o3‑mini`     | Constrained responses parsed into Pydantic models. |

All OpenAI calls are isolated here to simplify mocking in unit tests.

### 3.2  FileManager

A small utility that generates sequential filenames (`data_0.csv`, `image_1.png`, …) inside the per‑session folder. It is the single point of truth for **all** disk writes.

### 3.3  Artefacts Dataclass

```python
@dataclass
class Artefacts:
    sql_query: str | None = None
    data: pd.DataFrame | None = None
    data_file: Path | None = None
    image_file: Path | None = None
    code_file: Path | None = None
    answer: str | None = None
```

Eliminates fragile `dict` key access patterns and makes type‑checking pleasant.

### 3.4  SQL Retrieval Workflow

1. **Question Simplification** – LLM rephrases complex request into a single SQL‑answerable question.
2. **Query Generation** – LLM proposes a `SELECT‑only` SQL statement.
3. **Execution** – Pandas runs the query on the provided `sqlite3.Connection`.
4. **Validation** – Empty `DataFrame` triggers another loop (up to 3). Success persists CSV via `FileManager`.

### 3.5  Image Generation Workflow

1. CSV sample is **uploaded** to the OpenAI Assistant tool.
2. LLM converts the user request into clear plotting instructions.
3. Assistant runs code interpreter to generate a `.png`.
4. Both the **image bytes** and the **Python code** are downloaded and stored.

### 3.6  Context Maintenance

After every turn the agent writes a *compressed* summary (≲ 30 tokens) which replaces the previous context. This keeps the system prompt short while remembering the important facts.

---

## 4  Directory & File Layout

```
chat_docs/
└── 20250805T110212/       # run‑id (YYYYMMDDTHHMMSS)
    ├── data_0.csv         # most recent DataFrame
    ├── image_0.png        # latest chart
    ├── code_0.py          # Python code that built the chart
    └── ...                # subsequent artefacts
```

You can safely clean these folders; they are only a cache.

---

## 5  Configuration Knobs

| Variable              | Default       | Purpose                                   |
| --------------------- | ------------- | ----------------------------------------- |
| `OPENAI_MODEL_CHAT`   | `gpt‑4o‑mini` | Free‑form chat tasks.                     |
| `OPENAI_MODEL_STRUCT` | `o3‑mini`     | Structured outputs.                       |
| `MAX_SQL_RETRIES`     | `3`           | Loops for improving failed SQL.           |
| `HEAD_ROWS`           | `5`           | Lines of CSV included in plotting prompt. |
| `CHAT_DOCS_DIR`       | `chat_docs/`  | Root folder for artefacts.                |

Change them either in `config.py` or via environment variables.

---

## 6  Error Handling & Logging

* All operations use the standard **`logging`** module (INFO for control flow, WARNING for recoverable issues, ERROR for unrecoverable failures).
* SQL errors are caught; the offending query is fed back to the LLM for refinement.
* Image generation failures raise `RuntimeError` → surfaced to the UI.

---

## 7  Extending the Agent

* **Add a new modality** (e.g. PDF generation) by creating another service that implements the *plan → execute → persist* pattern, then integrate it inside `_good_flow()` similarly to the image path.
* **Swap vector DB or SQL engine** by replacing `sqlite3.Connection` with any object that exposes `pd.read_sql_query` capability.
* **Experiment with prompts** – all templates live in `prompts/default_prompts.py` so tweaking doesn’t touch the core code.

---

## 8  Sequence Diagram (text‑based)

```
User ─► AgentChat.execute
       ├─► _translate (ES→EN)
       ├─► _to_request
       ├─► _classify ─► LLM (struct)
       │        │
       │        └─► messageClassification
       ├─► _bad_flow  (if needed) ─► LLM.chat
       │
       └─► _good_flow
             ├─► _get_actions ─► LLM (struct)
             ├─► branch dispatch
             │      ├─► _supervised_sql (0–3×)
             │      │      ├─► _single_sql_round
             │      │      │      ├─► LLM.chat (simple question)
             │      │      │      ├─► LLM.chat (sql query)
             │      │      │      └─► pandas.read_sql_query
             │      └─► _run_python_image (optional)
             │             ├─► file upload (OpenAI)
             │             ├─► LLM.chat (instructions)
             │             └─► Assistant code interpreter
             ├─► _create_final_answer ─► LLM.chat
             ├─► _record_artefacts
             ├─► _summarise_interaction ─► LLM.chat
             └─► _update_context ─► LLM.chat
```

---

