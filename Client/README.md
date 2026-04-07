# Distributed Client

基于 DESIGN.md 设计的分布式手机自动化客户端，采用 TDD 开发模式。

## 项目结构

```
Distributed/Client/
├── src/
│   ├── adapters/          # 设备适配器 (ADB/HDC/WDA)
│   ├── polling/           # 设备轮询模块
│   ├── state/             # 状态机模块
│   ├── network/           # WebSocket 网络层
│   ├── executor/          # 任务执行器
│   ├── screenshot/        # 截图管理
│   └── logging/           # 日志模块
├── tests/
│   ├── unit/              # 单元测试
│   └── integration/       # 集成测试
├── TDD_DEVELOPMENT.md     # TDD 开发流程文档
└── DESIGN.md              # 设计文档
```

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行单元测试
pytest tests/unit/ -v

# 带覆盖率
pytest tests/ --cov=src --cov-report=html

# TDD 模式（监视文件变化）
pytest tests/ --watch
```

## TDD 开发流程

详见 [TDD_DEVELOPMENT.md](TDD_DEVELOPMENT.md)

## 支持的平台

- Android (通过 ADB)
- HarmonyOS (通过 HDC)
- iOS (通过 WebDriverAgent)

## 核心模块

### 设备适配器 (Adapters)

| 适配器 | 状态 | 说明 |
|--------|------|------|
| ADBAdapter | ✅ 已实现 | Android 设备适配器 |
| HDCAdapter | 🔨 开发中 | HarmonyOS 设备适配器 |
| WDAAdapter | 🔨 开发中 | iOS 设备适配器 |

### 状态机 (State)

- `DeviceStatus`: IDLE / BUSY / OFFLINE
- `ClientStatus`: ONLINE / OFFLINE / CONNECTING

### 消息协议 (Messages)

支持 DESIGN.md 中定义的所有消息类型：

- Task / Batch Task
- Interrupt / Batch Interrupt
- ACK / Batch ACK
- Task Update / Task Result
- Device Status
- Error / Heartbeat

## 开发指南

### 添加新测试

1. 在 `tests/unit/` 下创建测试文件
2. 遵循命名规范: `test_*.py`
3. 运行测试确保失败
4. 实现代码让测试通过
5. 重构优化

### 代码规范

```bash
# Lint
ruff check src/

# Format
ruff format src/

# Type check
mypy src/
```

## 参考资料

- [DESIGN.md](DESIGN.md) - 详细设计文档
- [TDD_DEVELOPMENT.md](TDD_DEVELOPMENT.md) - TDD 开发流程
- [phone_agent/](../../phone_agent/) - 参考实现
