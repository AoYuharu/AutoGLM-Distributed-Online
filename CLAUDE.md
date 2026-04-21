# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

Distributed phone automation system for controlling multiple mobile devices (Android, HarmonyOS, iOS) via a centralized server and web dashboard. Uses a ReAct (Reason-Act-Observe) loop powered by Zhipu AI's AutoGLM model for autonomous device control.

## Architecture

Three independent components communicate via REST + WebSocket:

- **Server/** — FastAPI backend (Python 3.10+). Manages devices, tasks, and the ReAct scheduler. Uses SQLAlchemy for persistence, structlog for logging, and Pydantic Settings for configuration. Default port: `8000`.
- **Client/** — Edge device agent (Python 3.10+). Polls devices, executes actions, and reports observations back to the server via WebSocket and HTTP. Platform adapters: ADB (Android), HDC (HarmonyOS), WDA (iOS).
- **Web/** — React 19 + TypeScript frontend. Ant Design UI, Zustand state management, native WebSocket for real-time updates. Built with Vite. Dev server at `http://localhost:5173`.

### Key flows

1. Client registers device via WebSocket → Server tracks device state (Idle/Busy/Offline)
2. User creates task via Web UI → Server assigns task to idle device
3. Server's ReAct scheduler calls AutoGLM API for reasoning → sends action to Client
4. Client executes action on device, takes screenshot, posts observation back via `POST /observe/result`
5. Loop continues until task completes or fails

### Canonical realtime protocol

The Server → Web realtime pipeline uses three message types:

- **`task_created`** — New task started on a device
- **`agent_progress`** — Fine-grained stage stream per step
- **`agent_step`** — Per-step summary / completion
- **`agent_status`** — Task-level terminal status
- **`phase_confirmed`** — User confirmed/rejected a cautious-mode phase

Canonical `agent_progress` stages in order (one step):

```
reason_complete    →  reason reasoning parsed, action ready
action_dispatched  →  action sent to client device
waiting_ack        →  waiting for client device ACK
ack_received       →  client device ACKed
waiting_observe    →  waiting for observation result
observe_received   →  observation result received (may include screenshot)
```

Timeout/error stages: `ack_timeout`, `observe_timeout`, `ack_rejected`

Progress identity key: `${task_id}:${step_number}:${stage}` — this is critical. Stage must be in the key or different stages within the same step overwrite each other.

## Development Commands

### Server

```bash
cd Server/
pip install -e ".[dev]"
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000    # run server
pytest tests/                                                  # run tests
pytest tests/ --cov=src --cov-report=html                     # with coverage
black src/ && isort src/                                       # format
mypy src/                                                      # type check
```

### Client

```bash
cd Client/
pip install -e ".[dev]"
python main.py --server ws://localhost:8000 --log-level INFO   # run client (WebSocket to Server)
pytest tests/                                                  # run tests
pytest tests/unit/ -v                                          # unit tests only
pytest tests/ --cov=src --cov-report=html                     # with coverage
ruff check src/ && ruff format src/                            # lint + format
mypy src/                                                      # type check
```

### Web

```bash
cd Web/
npm install
npm run dev       # dev server at http://localhost:5173
npm run build     # production build (tsc + vite)
npm run lint      # eslint
npm run preview   # preview production build

# Playwright E2E (requires mock-server running separately):
cd Web/mock-server && node mock-server.cjs &
npx playwright test tests/e2e/
```

## Code Style

- **Server**: black (line-length 88) + isort (black profile). pytest with `asyncio_mode = "auto"`.
- **Client**: ruff (line-length 100, target py310). Coverage excludes tests dir.
- **Web**: ESLint with react-hooks and react-refresh plugins. Tailwind CSS 4 + Ant Design 6. TypeScript strict mode.

## Configuration

- Server config via `Server/.env` (`DATABASE_URL`, `JWT_SECRET`, `PHONE_AGENT_API_KEY`, etc.) loaded by Pydantic Settings
- Web config via `Web/.env` (`VITE_API_URL` pointing to server, currently `http://localhost:8000`)
- Server storage config in `Server/storage.yaml`

## Critical Files

### Server

| File | Purpose |
|------|---------|
| `src/services/websocket.py` | `WebSocketHub` — manages all WebSocket connections (devices + web consoles). Canonical broadcast methods: `broadcast_agent_progress()`, `broadcast_agent_step()`, `broadcast_agent_status()`. Also handles `broadcast_task_update()` legacy adapter. |
| `src/services/action_router.py` | Routes parsed actions to client devices, tracks pending round state, handles ACK/observe timeouts. Key: `execute_action()`, `handle_observe_result()`, progress stage broadcasting. |
| `src/services/react_scheduler.py` | ReAct thread-pool scheduler. Key: `execute_reason()` (AI model call), `_parse_action()`, task ownership, requeue logic. |
| `src/api/tasks.py` | Task creation via WebSocket `/ws/console`, observe HTTP endpoint (`POST /observe/result`), session/chat restore. Bootstrap observe guard: `step_number == 0` skips canonical pending-round router. |
| `src/network/message_types.py` | All protocol message definitions (action_cmd, ack, observe_result, etc.) |
| `src/schemas/schemas.py` | Pydantic request/response schemas including rich replay metadata fields |
| `src/api/devices.py` | Device registration and status management |

### Client

| File | Purpose |
|------|---------|
| `main.py` | `DistributedClient` entry point. WebSocket connection to Server, HTTP reporting, platform adapter orchestration. |
| `src/network/websocket.py` | Receives `action_cmd` from Server, sends `ack` back |
| `src/network/http_client.py` | Reports `device_status` and `observe_result` via HTTP |
| `src/adapters/adb_adapter.py` | Android device control via ADB |
| `src/adapters/hdc_adapter.py` | HarmonyOS device control via HDC |
| `src/adapters/wda_adapter.py` | iOS device control via WDA |

### Web

| File | Purpose |
|------|---------|
| `src/stores/agentStore.ts` | Zustand store — session state, WebSocket message handling, progress/conversation normalization, restore from session+chat API. **This file is the center of the realtime UI pipeline.** Key: `_handleAgentProgress()`, `_handleAgentStep()`, `_handleAgentStatus()`, progress key design. Contains a large legacy helper layer (prefixed `normalize*`) that is intentionally retained for build compatibility but not actively used in the current timeline path. |
| `src/services/wsConsole.ts` | WebSocket console client. Manages connection lifecycle, subscribes to device events, dispatches messages to store callbacks. Key: `sendConfirmPhase()`, `sendCreateTask()`, `sendInterruptTask()`. |
| `src/services/agentApi.ts` | REST API client for tasks, devices, session, chat history. Key types: `DeviceSessionSnapshot`, `ChatHistoryMessage`. Session restore includes `chat_history` merge. |
| `src/components/agent/AgentWindow.tsx` | Agent conversation UI. Renders progress blocks, action confirmation buttons, current screenshot. Reads `agentStore` state. |
| `src/components/log/LogPanel.tsx` | Device log viewer with timeline filtering, screenshot preview, download buttons for JSON/raw logs. |

## Design Documentation

- `docs/MAIN.md` — system-level architecture and device status design
- `docs/API.md` — REST API specifications
- `docs/Startup_Guide.md` — deployment instructions
- `Server/DESIGN.md` / `Server/architecture.md` — server internals
- `Client/DESIGN.md` / `Client/TDD_DEVELOPMENT.md` — client state machine, TDD workflow
- `Web/DESIGN.md` — frontend UI design, Agent window, batch processing

---

## 开发管理

### 大测试 — 仿人类 E2E 测试

所有涉及 UI / Agent 会话窗口的大修改**必须**进行 Playwright E2E 测试，步骤：

1. 启动所有必要进程（Server、Client、Web）
2. 用 Playwright 打开浏览器，模拟人类操作
3. 执行任务，观察 AgentWindow 的实时反馈是否按阶段依次显示（reason_complete → action_dispatched → waiting_ack → ack_received → waiting_observe → observe_received）
4. **截图**，必要时调用 `understand_image` 工具分析截图中的元素是否符合预期
5. 验证截图是否在 `observe_received` 到达后**立即刷新**

**注意**：对于 Agent 会话窗口相关的测试，**不要在第三方 agent 进行回复之前（如果测试目的是需要它回复）就下定结论**，应该等它正常回复后才进行下一步处理。

### 小测试 — 用户通知

小修改**必须**告诉用户：
- 修改了哪个文件的哪几行
- 修改的核心逻辑是什么
- 是否需要重启进程

### 全程进程管理

测试过程中应保持所有 Server、Client、Web 进程运行。如果对任何部分进行了代码修改，**必须**先关闭老进程再重启新进程加载新代码，再验证。**不允许同时多个同名进程并存**。

### 重启规定

对任何部分的代码进行了**任何程度**的修改，都应该进行对老进程的关闭与新进程的重启，再测试。不要因为"只是改了一个注释"就跳过重启——以防修改了实际逻辑但遗漏了重启。

### 实机辅助

如果需要用户手机辅助检测，暂时阻塞，给出选项，询问用户是否已经插上（或拔出手机），等待用户操作再进行进一步判断。

---

## Technical Notes & Known Issues

### 协议与状态设计

1. **`confirm_phase` 绑定较弱**：当前 `wsConsole.ts` 的 `sendConfirmPhase(deviceId, approved)` 只发送 `device_id + approved`，不携带 `task_id` 或 `phase`。如果存在延迟确认或积压的待确认项，协议无法唯一绑定到正确的任务/阶段。这是历史遗留的兼容性设计，后续应增强为显式携带 `task_id` 和 `phase`。

2. **Bootstrap observe 不进 canonical 路由**：`tasks.py` 中 `step_number == 0` 的 observe 结果被明确跳过，不进入 canonical pending-round router。这是正确的设计，确保初始化截图不会触发动作执行逻辑。

3. **Session/Chat 恢复路径已增强**：`/devices/{id}/session` 现在返回 richer metadata（`step_number`, `phase`, `stage`, `progress_message` 等），`agentApi.ts` 会将其与 `chat_history` 合并后重建 timeline 节点。这是最近修复的关键恢复路径。

4. **Progress key 必须包含 stage**：当前的 `getProgressKey(taskId, stepNumber, stage)` 是正确的设计。如果 stage 被省略，同一步的多个进度事件会互相覆盖，导致用户只能看到最后一个 stage。这是之前的历史 bug，已在最新代码中修复。

5. **Step 0 不是 falsy**：`normalizeProgressStepNumber()` 等函数使用 `?? 0` 而非 `|| 0`，确保 `step_number = 0` 的 bootstrap 阶段不会被丢弃。

6. **`sendCommand()` 不再在 task_created 到达前提前标记为"未运行"**：最近修复后，`sendCommand()` 在 HTTP 响应后立即将 `isRunning` 设为 `true`，避免短暂的状态不一致窗口。

### Mock Server 的稳定性

`Web/mock-server/mock-server.cjs` 在 `create_task` 分支中应复用同一个 `taskId` 变量，而不是每条延迟消息都用 `Date.now()` 生成新 ID。之前的实现有这个 bug，已修复。

### 代码质量遗留

- `Web/src/stores/agentStore.ts` 中存在大量历史遗留的 normalize wrapper helper（`normalize*` 前缀），它们是上一轮重构的残留，当前不在活跃的 timeline 处理路径中使用。这些函数通过 `agentStoreLegacyCompatRegistry` 导出以满足 TypeScript 的 no-unused-vars 检查，但**不应在新的代码中继续依赖或调用它们**。
- `Web/src/services/agentApi.ts` 中存在少数死兼容路径（如 `getTaskDetail()` 硬返回 `null`），它们在真实场景下不会造成运行时崩溃，但会静默降级为更薄的快照。

### Playwright E2E 结构

- `Web/tests/e2e/log-panel.spec.ts` — 验证日志面板的 timeline 合并、截图预览、搜索过滤、下载按钮
- `Web/tests/e2e/agent-api-fixes.spec.ts` — 验证 store API 修复（confirmPhase、resume/continueTask 异常、废弃方法删除等）。注意：此文件内部通过直接操作 Zustand store 来测试，WebSocket 被 `route.abort()` 屏蔽，所以是 store 单元测试而非真正的端到端
- `Web/tests/e2e/agent-full.spec.ts` — 完整的 Agent 窗口功能测试，但部分用例仍为空断言或 store 内检，未充分覆盖流式阶段顺序
- `Web/mock-server/mock-server.cjs` — HTTP + WebSocket mock 服务器，模拟 canonical 事件流。**不稳定**：依赖全局 `ws` npm 包，不在本项目的 package.json 中

### AI 模型行为

- 模型在推理阶段偶尔会"多走几步"，即在任务目标已达成后仍然继续点击同一区域。建议后续优化 `finish` 判断规则。

### 归档文件

- 设备归档文件（`history.json`, `react_records.jsonl`）按设备累计，不是"单次运行独立视图"。如需分析单次运行，需要按时间戳过滤。
- 偶尔会出现 `Error: bad size width` 日志，但不阻断主链路，来源在设备能力检查附近。

---

## Process Checklist for Any Change

Before committing any change, verify:

1. **Build**: `npm run build` passes (Web), or relevant Python tests pass (Server/Client)
2. **Linting**: `npm run lint` (Web), `ruff check` (Client), `black --check` (Server)
3. **Process restart**: All affected processes stopped and restarted after code change
4. **Smoke test**: Relevant unit/test file passes
5. **UI sanity**: If changing AgentWindow, wsConsole, or agentStore, at minimum confirm the page loads without console errors

For large changes that affect the realtime pipeline:
1. Verify `agent_progress` stages are still routed correctly through the store
2. Verify screenshot updates fire on `observe_received` (check `_handleAgentProgress` calls `shouldSetScreenshot`)
3. Verify progress key includes `stage` (grep for `getProgressKey.*stage`)
4. Run Playwright E2E against the actual app if the change touches store normalization or UI rendering
