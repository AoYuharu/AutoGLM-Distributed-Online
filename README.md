# Open-AutoGLM Distributed

一个面向多设备手机自动化的分布式系统，包含 **Server / Client / Web** 三个独立组件，通过 **REST + WebSocket** 协同工作，用于驱动 Android、HarmonyOS、iOS 等设备执行任务，并在 Web 界面中实时展示执行过程。

## 项目组成

### 1. Server
- 基于 FastAPI 的后端服务
- 负责设备管理、任务调度、ReAct 运行时、消息分发、状态同步
- 对 Web 提供 HTTP API 与 WebSocket 实时消息
- 对 Client 提供任务下发、ACK / observe_result 接收与状态管理

### 2. Client
- 部署在设备接入侧的执行代理
- 负责轮询并发现设备
- 负责执行服务端下发的动作命令
- 通过 WebSocket 接收动作，通过 HTTP 上报设备状态与观察结果
- 支持 ADB / HDC / WDA 三类设备接入

### 3. Web
- 基于 React + TypeScript + Vite 的前端控制台
- 负责展示设备列表、任务状态、实时阶段流、Agent 会话窗口
- 支持通过 WebSocket 接收 Server 推送的实时事件

---

## 当前主要功能

### 设备与任务
- 多设备接入与在线状态管理
- 设备状态同步与离线检测
- 单任务执行与批量任务执行
- 任务中断、恢复与状态更新

### ReAct 执行链路
- Reason → Act → Observe 的服务端调度流程
- ACK-first 执行确认机制
- observe_result 回传与结果处理
- 默认 observe 错误重试控制
- ACK / Observe 超时控制

### Web 实时界面
- AgentWindow 实时阶段流展示
- 设备卡片与批量任务视图
- WebSocket 控制台连接与订阅
- REST + WebSocket 双通道协同

### 配置能力
- Server 与 Web 共用一个 YAML 配置文件
- Client 使用独立 YAML 配置文件
- Client 支持 `--config` 指定配置文件，并支持 CLI 覆盖部分 YAML 配置
- Web 在 Node 侧读取共享 YAML，再注入浏览器运行时配置

---

## YAML 配置文件位置

### 1. Server 与 Web 共用
```text
config/server-web.yaml
```

当前主要用于：
- Server 监听地址与端口
- Server 对外公开 HTTP / WebSocket 地址
- ReAct 超时与重试参数
- Web 开发服务器地址与端口
- Web mock server 地址与端口

### 2. Client 独立配置
```text
config/client.yaml
```

当前主要用于：
- Client 连接的 Server 地址
- 日志级别
- 轮询间隔
- WebSocket 重连参数
- HTTP 超时与 observe 重试参数
- ADB / HDC / WDA 平台参数

---

## 目录结构

```text
Distributed/
├── Client/                  # 设备侧执行代理
├── Server/                  # 后端服务
├── Web/                     # 前端控制台
├── config/                  # 统一 YAML 配置
│   ├── client.yaml
│   └── server-web.yaml
├── docs/                    # 设计与分析文档
└── README.md
```

---

## 环境要求

### Python
- Python 3.10+

### Node.js
- 建议 Node.js 18+

### 设备工具
按需要安装：
- Android：`adb`
- HarmonyOS：`hdc`
- iOS：`WebDriverAgent` 对应运行环境

---

## 启动方式

建议启动顺序：
1. 启动 Server
2. 启动 Client
3. 启动 Web

### 1. 启动 Server

```bash
cd Server
pip install -e ".[dev]"
python -m src.main
```

默认情况下，Server 会读取：
```text
config/server-web.yaml
```

### 2. 启动 Client

```bash
cd Client
pip install -e ".[dev]"
python main.py --config "../config/client.yaml" --server "ws://localhost:8000"
```

说明：
- `--config` 用于显式指定 Client YAML 配置文件
- `--server` 会覆盖 YAML 中的服务端 WebSocket 地址

如需更详细日志：

```bash
python main.py --config "../config/client.yaml" --server "ws://localhost:8000" --log-level DEBUG
```

### 3. 启动 Web

```bash
cd Web
npm install
npm run dev
```

启动后默认访问：

```text
http://localhost:5173
```

Web 会读取：
```text
config/server-web.yaml
```

---

## 常用开发命令

### Server
```bash
cd Server
pytest tests/
```

### Client
```bash
cd Client
pytest tests/
ruff check src/
ruff format src/
```

### Web
```bash
cd Web
npm run build
npm run lint
```

---

## 配置优先级

### Server
优先级大致为：
```text
初始化参数 > 环境变量 > .env > config/server-web.yaml > 代码默认值
```

### Client
优先级大致为：
```text
CLI 参数 > config/client.yaml > 代码默认值
```

### Web
优先级大致为：
```text
config/server-web.yaml > 浏览器注入配置 > 默认值
```

---

## 典型访问地址

如果你使用当前默认配置：

- Server HTTP: `http://localhost:8000`
- Server WebSocket: `ws://localhost:8000`
- Web: `http://localhost:5173`

---

## 说明

本仓库当前已经接入统一的 YAML 配置入口：
- `config/server-web.yaml`
- `config/client.yaml`

其中：
- Server 与 Web 共用 `server-web.yaml`
- Client 使用 `client.yaml`

如果你修改了 Server / Client / Web 任一端代码，建议关闭旧进程后重新启动对应服务，再进行联调验证。
