# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Distributed phone automation system for controlling multiple mobile devices (Android, HarmonyOS, iOS) via a centralized server and web dashboard. Uses a ReAct (Reason-Act-Observe) loop powered by Zhipu AI's AutoGLM model for autonomous device control.

## Architecture

Three independent components communicate via REST + WebSocket:

- **Server/** — FastAPI backend (Python 3.10+). Manages devices, tasks, and the ReAct scheduler. Uses SQLAlchemy for persistence, structlog for logging, Redis for caching, and Pydantic Settings for configuration.
- **Client/** — Edge device agent (Python 3.10+). Polls devices, executes actions, and reports observations back to the server via WebSocket. Platform adapters: ADB (Android), HDC (HarmonyOS), WDA (iOS).
- **Web/** — React 19 + TypeScript frontend. Ant Design UI, Zustand state management, Socket.io for real-time updates. Built with Vite.

### Key flows

1. Client registers device via WebSocket → Server tracks device state (Idle/Busy/Offline)
2. User creates task via Web UI → Server assigns task to idle device
3. Server's ReAct scheduler calls AutoGLM API for reasoning → sends action to Client
4. Client executes action on device, takes screenshot, posts observation back via `POST /observe/result`
5. Loop continues until task completes or fails

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
python main.py --server ws://localhost:8080 --log-level INFO   # run client
pytest tests/                                                  # run tests
pytest tests/unit/ -v                                          # unit tests only
pytest tests/ --cov=src --cov-report=html                     # with coverage
ruff check src/ && ruff format src/                           # lint + format
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
```

## Code Style

- **Server**: black (line-length 88) + isort (black profile). pytest with `asyncio_mode = "auto"`.
- **Client**: ruff (line-length 100, target py310). Coverage excludes tests dir.
- **Web**: ESLint with react-hooks and react-refresh plugins. Tailwind CSS 4 + Ant Design 6.

## Configuration

- Server config via `Server/.env` (DATABASE_URL, JWT_SECRET, PHONE_AGENT_API_KEY, etc.) loaded by Pydantic Settings
- Web config via `Web/.env` (VITE_API_URL pointing to server)
- Server storage config in `Server/storage.yaml`

## Design Documentation

Detailed design docs live alongside each component:
- `docs/MAIN.md` — system-level architecture and device status design
- `docs/API.md` — REST API specifications
- `docs/Startup_Guide.md` — deployment instructions
- `Server/DESIGN.md` / `Server/architecture.md` — server internals, class diagrams
- `Client/DESIGN.md` / `Client/TDD_DEVELOPMENT.md` — client state machine, TDD workflow
- `Web/DESIGN.md` — frontend UI design, Agent window, batch processing

## 开发管理
- **大测试-仿人类测试：**所有大修改必须进行Web页面的修改，使用Playwright模仿人类行为进行网页端的web测试，截图，调用understand_image工具进行截图分析，分析截图中的元素是否符合要求

**对于Agent会话窗口相关的测试，不要在第三方agent进行回复之前（如果测试目的是需要它回复）就下定结论，应该等它正常回复才进行后一步的处理**

- **小测试-用户通知：**小修改必须告诉我如何进行的，以及具体的修改逻辑
- **全程进程管理：**测试过程中应该保持所有Server , Client , Web进程运行，如果进行了代码修改，应该进行主动重启操作加载新的验证。并且保持唯一性，不允许同时多个进程同时出现
- **重启规定：**对任何部分的代码进行了任何程度的修改，都应该进行对老进程的关闭与新进程的重启，再测试

## 用户辅助
- **实机辅助：**如果需要用户手机辅助检测，暂时阻塞，给出选项，询问用户是否已经插上（或拔出手机），等待用户操作进行进一步判断