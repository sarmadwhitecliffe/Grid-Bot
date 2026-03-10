# Grid Bot Agent Workflow Reference

This document describes how to invoke and coordinate the Grid Bot development agents for different types of work.

---

## Agent Roles & Responsibilities

| Agent | Model | Role | When to Use |
|-------|-------|------|-------------|
| **Orchestrator** | Claude Sonnet 4.5 | Breaks down requests into phases; delegates to specialist agents; never writes code | Multi-phase or complex tasks spanning multiple files/layers |
| **Planner** | Claude Haiku 4.5 | Researches Grid Bot conventions, phase plans, and codebase; creates ordered steps; identifies edge cases | Any feature or phase work—consult Planner FIRST |
| **Coder** | Claude Haiku 4.5 | Implements code per Planner's step-by-step plan; follows mandatory Grid Bot principles | Actual code implementation (always after Planner) |
| **Designer** | Gemini 3.1 Pro | Dormant for Phase 1–5; activates if UI dashboard is built post-Phase 5 | Future UI work only |
| **TDD Red** | Claude Haiku 4.5 | Write failing tests first from GitHub issue requirements before implementation exists | Start of any new feature or bug fix to define expected behavior |
| **TDD Green** | Claude Haiku 4.5 | Implement minimal code to satisfy issue requirements and make tests pass quickly | After TDD Red phase; focus on getting tests green |
| **TDD Refactor** | Claude Haiku 4.5 | Improve code quality, security, and design while keeping tests green | After TDD Green phase; enhance without breaking tests |
| **Context7-Expert** | Claude Sonnet 4.5 | Up-to-date library documentation via MCP; verify current ccxt, pydantic, pytest-asyncio APIs | When library usage is uncertain; before implementing with external deps |
| **Critical-thinking** | Gemini 3 Pro | Challenge assumptions, ask "why", identify edge cases and potential pitfalls | Before major architectural decisions or new feature planning |
| **Prompt Engineer** | Claude Sonnet 4.5 | Analyze and improve prompts using systematic framework and best practices | When creating/refining agent prompts or workflow templates |
| **Memory Updater** | Claude Haiku 4.5 | Updates Memory Bank after each phase completion milestone | After phase completion; on memory refresh requests |
| **Janitor** | Claude Haiku 4.5 | Code cleanup, tech debt removal, dead code elimination, dependency audit | Phase completion, before PR submission, periodic maintenance |
| **Security Auditor** | Claude Haiku 4.5 | Pre-deployment security gates; scan for API keys, validate testnet, audit secrets management | Before every commit (quick), before phase completion (deep), before deployment (critical) |
| **Async Sheriff** | Claude Haiku 4.5 | Validate async/await patterns, rate limiting, retry logic; prevent async deadlocks | After implementing async modules; before phase completion; when debugging async issues |
| **Performance Optimizer** | Claude Haiku 4.5 | Profile hot paths, optimize async loops, and reduce memory overhead | Performance tuning, latency regressions, pre-deployment profiling |
| **Documentation Specialist** | Claude Sonnet 4.5 | Produce API docs, user guides, diagrams, and runbooks | Documentation updates, onboarding, operational readiness |
| **DevOps Engineer** | Claude Haiku 4.5 | Dockerization, CI/CD, monitoring stack, and deployment strategies | Infrastructure setup, pipeline work, production readiness |
| **Data Analyst** | GPT-4.1 | Analyze backtests, performance metrics, and risk statistics | Reporting, parameter analysis, and performance review |
| **4.1 Beast Mode** | GPT-4.1 | Autonomous complex problem solving with extensive internet research; iterative debugging | Complex integrations, obscure async patterns, new exchange research |

---

## Workflow: Task Execution

### For Simple Tasks (Single Feature/File)

```
1. Planner: Research and create implementation plan
2. TDD Red: Write failing tests for the feature
3. TDD Green: Implement minimal code to pass tests
4. TDD Refactor: Improve quality while keeping tests green
5. Janitor (optional): Cleanup before commit
```

### For Complex Tasks (Multi-Phase, Multiple Layers)

```
1. Orchestrator: Breaks down into phases
   ↓
2. Critical-thinking: Review plan for edge cases
   ↓
3. Planner: Create detailed step-by-step plan
   ↓
4. For each implementation step:
   - TDD Red: Write failing test
   - Context7-Expert (if needed): Verify library APIs
   - TDD Green: Implement to pass test
   - TDD Refactor: Improve code quality
   ↓
5. Janitor: Final cleanup and tech debt removal
6. Memory Updater: Refresh Memory Bank after phase completion
```

### Example: Implement Phase 1

```
User: "Implement Grid Bot Phase 1"
  ↓
Orchestrator: Breaks into sub-phases (Config → Exchange → Price Feed → Tests)
  ↓
Critical-thinking: "Have you considered rate limit edge cases? What about cache corruption?"
  ↓
Planner: Reads plan/feature-grid-bot-phase1-1.md, outputs 7 ordered steps
  ↓
Orchestrator: Parses steps into parallel-safe phases
  
  Phase 1a: Config files
    TDD Red: Write test for settings validation
    TDD Green: Implement GridBotSettings with Pydantic
    TDD Refactor: Add validation rules, improve error messages
  
  Phase 1b: Exchange client (depends on config)
    TDD Red: Write test for async order placement (mocked)
    Context7-Expert: Check latest ccxt.async_support API
    TDD Green: Implement ExchangeClient.place_limit_order
    TDD Refactor: Add retry logic, structured logging
  
  Phase 1c: Price feed (depends on exchange)
    TDD Red: Write test for OHLCV caching
    TDD Green: Implement PriceFeed.get_ohlcv_dataframe
    TDD Refactor: Add cache freshness check, Parquet optimization
  
  Phase 1d: Tests (depends on all above)
    - Run full test suite
    - Janitor: Remove unused imports, optimize fixtures
  ↓
All phases complete → Orchestrator reports "Phase 1 ready"
```

---

## Key Conventions (Always Reference)

### Files & Locations

- **Phase Plans**: `plan/feature-grid-bot-phase{1-5}-1.md`
- **Conventions**: `.github/copilot-instructions.md`
- **Agent Specs**: `.github/agents/` (orchestrator, planner, coder, designer)
- **Config**: `config/grid_config.yaml` + `config/settings.py`
- **Source**: `src/` (exchange, data, strategy, oms, risk, persistence, monitoring)
- **Tests**: `tests/` (mirrors `src/` layout)
- **State**: `data/state/grid_state.json` (git-ignored)
- **Logs**: `logs/`

### Mandatory Grid Bot Principles

- **Async-first**: All exchange IO via `ccxt.async_support`
- **Rate limiting**: `enableRateLimit=True` on every ccxt instance
- **Retry logic**: Exponential backoff `[1, 2, 5]` seconds; catch only `NetworkError` & `RequestTimeout`
- **No hardcoded values**: All config in YAML or `.env`
- **Pure strategy**: regime_detector & grid_calculator are stateless functions
- **Data models**: Pydantic or dataclass DTOs at inter-layer boundaries
- **Testing**: `pytest` + `pytest-asyncio`; mock exchange calls with `pytest-mock`
- **Structured logging**: Context at layer boundaries (order IDs, prices, regime state)
- **Persistence**: Atomic writes to `data/state/grid_state.json`

---

## Red Flags & Escalation

| Situation | Action |
|-----------|--------|
| Task involves multiple phases and unclear dependencies | Use Orchestrator to parse into parallel-safe phases first |
| Unsure which files need to be created or modified | Always consult Planner before calling Coder |
| Library behavior is uncertain (ccxt, pydantic, etc.) | Instruct Coder to use #context7 MCP to verify docs |
| Test layout or structure unclear | Planner should define test file locations in plan |
| State persistence or crash recovery needed | Planner must plan atomic writes and recovery logic |
| Async/await patterns feel complex | Planner should identify all async boundaries upfront |

---

## Phase Gates & Dependencies

```
Phase 1: Config + Exchange + Data
         (independent core setup)
         ↓
Phase 2: Strategy Core (depends on Phase 1 Data)
         ├─ Regime Detector
         └─ Grid Calculator
         ↓
Phase 3: Execution Engine (depends on Phase 2 Strategy)
         ├─ Order Manager
         ├─ Fill Handler
         └─ Risk Manager
         ↓
Phase 4: Persistence + Monitoring (depends on Phase 3)
         ├─ State Store
         ├─ Alerting
         └─ Main Loop
         ↓
Phase 5: Backtesting (depends on Phases 1–4)
         ├─ Grid Backtester
         ├─ Test Suite
         └─ Verification Report
```

---

## Templates & Task Dispatch

### Dispatch to Planner

```
"Create an implementation plan for [FEATURE/PHASE].

Reference:
- plan/feature-grid-bot-phase[N]-1.md (if applicable)
- .github/copilot-instructions.md (for Grid Bot conventions)

Output ordered steps with:
- File paths
- Data models (DTOs, Enums)
- Dependencies
- Edge cases (async, errors, retries)
- Assumptions & open questions"
```

### Dispatch to Coder

```
"Implement the following steps (from Planner):

Step 1: [description]
  Files: [list]
  
Step 2: [description]
  Files: [list]

Verify:
- All tests pass
- No hardcoded values (check config/grid_config.yaml)
- Async calls wrapped properly
- Structured logging at layer boundaries
- Code follows Grid Bot principles"
```

---

## Contact & Questions

- See `.github/copilot-instructions.md` for architecture overview
- See `plan/feature-grid-bot-master-1.md` for 5-phase master plan
- See agent files in `.github/agents/` for detailed agent specs

---

## Supporting Prompts & Instructions

### Prompts (AI-Optimized Execution Templates)

Store specialized execution prompts in `.github/prompts/`. Each prompt has a trigger and primary agent:

| Prompt | Primary Agent | Trigger | Purpose |
|--------|--------------|---------|---------|
| `create-implementation-plan.prompt.md` | Planner | Feature/phase work | Break down requirements into ordered steps |
| `create-specification.prompt.md` | Planner | New feature requirements | Formalize component interfaces and acceptance criteria |
| `tdd-workflow.prompt.md` | TDD Red/Green/Refactor | Test-driven development | Structured TDD process from issue to passing tests |
| `run-security-audit.prompt.md` | Security Auditor | Pre-commit, pre-deployment | Scan for hardcoded secrets, API leaks, testnet violations |
| `validate-async-patterns.prompt.md` | Async Sheriff | Post-async implementation | Verify rate limiting, deadlock prevention, retry logic |
| `memory-merger.prompt.md` | Memory Updater | Phase completion | Consolidate mature domain memories into instruction files |
| `remember.prompt.md` | Memory Keeper | After lessons learned | Capture hard-won insights and debugging discoveries |
| `create-readme.prompt.md` | Documentation Specialist | Phase completion, user onboarding | Generate user guides, architecture diagrams, runbooks |
| `create-github-action-workflow-specification.prompt.md` | DevOps Engineer | CI/CD improvements | Design and spec new GitHub Actions workflows |
| `create-github-issue-feature-from-specification.prompt.md` | (Orchestrator/User) | Issue standardization | Template for well-formed GitHub issue creation |
| `create-github-pull-request-from-specification.prompt.md` | (Orchestrator/User) | PR documentation | Template for PR description and changelog |
| `create-agentsmd.prompt.md` | Prompt Engineer | New agent creation | Define new agent specifications (reserved) |

### Instructions (Coding & Documentation Standards)

Store mandatory standards in `.github/instructions/`:
- **memory-bank.instructions.md**: Memory Bank governance and structure (all agents must reference)
- **memory.instructions.md**: Grid Bot project memory, patterns, hard-won lessons
- **python.instructions.md**: Mandatory for all Grid Bot code (Phases 1–5)
- **markdown.instructions.md**: Mandatory for all documentation and plan files

All agents must reference these standards before producing code or documentation.
