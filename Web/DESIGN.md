# Distributed Web 前端设计文档

## 目录

- [概述](#概述)
- [页面结构](#页面结构)
- [组件设计](#组件设计)
- [单设备 Agent 窗口](#单设备-agent-窗口)
- [谨慎模式](#谨慎模式)
- [日志系统](#日志系统)
- [状态机管理](#状态机管理)
- [批处理功能](#批处理功能)
- [技术实现](#技术实现)
- [组件清单](#组件清单)
- [消息协议](#消息协议)

---

## 概述

### 设计目标

Web 前端是分布式手机自动化控制系统的用户界面，提供：
1. **多设备监控** - 实时查看所有连接设备的状态
2. **单设备控制** - 通过自然语言与单个设备的 Agent 交互
3. **任务管理** - 创建、监控和管理自动化任务
4. **日志审计** - 查看设备历史操作日志
5. **批处理** - 批量下发任务到多个设备

### 用户角色

| 角色 | 权限 |
|------|------|
| 管理员 | 所有功能，包括设备管理、用户管理 |
| 操作员 | 设备监控、任务下发、日志查看 |
| 访客 | 仅查看设备状态 |

---

## 页面结构

### 整体布局

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Header: Logo + 系统名称 + 用户信息 + 设置                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  Toolbar: 刷新 + 连接状态 + 批量操作入口                                     │
├───────────────┬─────────────────────────────────────────────────────────────┤
│               │                                                             │
│   设备列表     │                    主工作区                                  │
│   (Sidebar)   │   ┌─────────────────────────────────────────────────────┐ │
│               │   │                                                     │ │
│  ┌─────────┐ │   │    设备监控视图 / Agent 交互窗口 / 批处理视图         │ │
│  │Device 1 │ │   │                                                     │ │
│  │[Log][→] │ │   │                                                     │ │
│  └─────────┘ │   │                                                     │ │
│  ┌─────────┐ │   │                                                     │ │
│  │Device 2 │ │   │                                                     │ │
│  │[Log][→] │ │   │                                                     │ │
│  └─────────┘ │   └─────────────────────────────────────────────────────┘ │
│               │                                                             │
│  ┌─────────┐ │   ┌─────────────────────────────────────────────────────┐ │
│  │Device 3 │ │   │              实时日志面板 (可折叠)                     │ │
│  │[Log][→] │ │   └─────────────────────────────────────────────────────┘ │
│  └─────────┘ │                                                             │
│               │                                                             │
├───────────────┴─────────────────────────────────────────────────────────────┤
│  Footer: 连接状态 + 版本信息                                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 主视图模式

1. **设备监控视图** (默认)
   - 设备卡片网格展示
   - 实时状态指示
   - 快速操作入口

2. **单设备 Agent 窗口**
   - 全屏/分屏模式
   - Agent 交互界面
   - 实时截图预览

3. **批处理视图**
   - 设备选择区
   - 任务配置区
   - 预览/执行区

---

## 组件设计

### 1. 设备卡片 (DeviceCard)

```
┌─────────────────────────────────────────┐
│  ●                                    ☐  │  ← 状态指示 + 复选框
│  ┌───────┐   vivo V2324HA              │
│  │  📱   │   10AE551838000D7           │
│  └───────┘   Android 16                │
│              状态: 空闲                  │
│  ┌───────┬───────┬───────┐            │
│  │  Log  │ Agent │  ⋮   │            │  ← 操作按钮
│  └───────┴───────┴───────┘            │
└─────────────────────────────────────────┘
```

| 状态 | 指示灯颜色 | 说明 |
|------|-----------|------|
| `idle` | 绿色 (#22c55e) | 空闲，可接收任务 |
| `busy` | 黄色 (#eab308) | 忙碌，执行中 |
| `offline` | 灰色 (#6b7280) | 离线 |
| `error` | 红色 (#ef4444) | 异常 |

### 2. Agent 交互组件 (AgentChat)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Agent 交互窗口 - vivo V2324HA                                      [全屏]  │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────┬──────────────────────────────────────┐  │
│  │                              │  Reason:                              │  │
│  │      [实时截图预览]           │  用户想要打开微信并搜索附近的人...    │  │
│  │      1920 x 1080            │                                      │  │
│  │                              │  ─────────────────────────────────   │  │
│  │      点击可放大               │  Act:                                │  │
│  │                              │  tap(x=500, y=300)                   │  │
│  │                              │  [✓ 确认] [✗ 拒绝] [▶ 跳过]          │  │
│  │                              │                                      │  │
│  │                              │  ─────────────────────────────────   │  │
│  │                              │  Observation:                        │  │
│  │                              │  当前屏幕显示微信首页                 │  │
│  └──────────────────────────────┴──────────────────────────────────────┘  │
│                                                                             │
│  模式: (○) 谨慎模式  (●) 非谨慎模式                                          │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ 输入自然语言命令...                                            [发送] │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  进度: ████████░░░░░░░░░░░░░  8/100 步骤                    ⏱ 00:45        │
│                                                                             │
│  历史步骤:                                                                  │
│  ├─ 1. ✓ launch(app="微信")         成功                                 │
│  ├─ 2. ✓ tap(x=500, y=300)          成功                                 │
│  ├─ 3. ✓ type(text="附近的人")      成功                                 │
│  ├─ 4. ⟳ tap(x=500, y=400)         执行中...                            │
│  └─ 5. ○ wait()                    待执行                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3. 谨慎模式确认对话框 (ActionConfirmDialog)

```
┌─────────────────────────────────────────┐
│           确认执行动作                    │
├─────────────────────────────────────────┤
│                                         │
│  即将执行: tap                           │
│  参数: x=500, y=300                      │
│                                         │
│  截图预览:                               │
│  ┌─────────────────────────────────┐   │
│  │        [标注点击位置]            │   │
│  │              ●                  │   │
│  └─────────────────────────────────┘   │
│                                         │
│  Reason:                                │
│  需要点击搜索按钮来触发搜索功能...        │
│                                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │  ✓ 确认  │  │  ✗ 拒绝  │  │  ▶ 跳过 │  │
│  └─────────┘  └─────────┘  └─────────┘  │
│                                         │
│  [x] 本次会话记住我的选择                 │
│                                         │
└─────────────────────────────────────────┘
```

### 4. 日志面板 (LogPanel)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  日志 - vivo V2324HA                                           [导出] [清空] │
├─────────────────────────────────────────────────────────────────────────────┤
│  筛选: [全部 ▼]  时间: [今天 ▼]  搜索: [_______________]                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ 10:30:05.123  📱 设备连接     10AE551838000D7 已上线                  │  │
│  │ 10:30:06.456  📋 任务开始     task_001 打开微信                       │  │
│  │ 10:30:07.234  🎯 动作执行     tap (500, 300)                         │  │
│  │ 10:30:07.890  ✅ 动作成功     耗时: 150ms                            │  │
│  │ 10:30:08.456  📸 截图上传     step_1.png                             │  │
│  │ 10:30:12.234  🎯 动作执行     type("附近的人")                        │  │
│  │ 10:30:13.890  ⚠️  动作失败    超时 60s                               │  │
│  │ 10:30:15.000  🔄 重试        重新执行 type                           │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  上报日志到服务端                                                           │
│  [选择文件...] 已选择: device_logs_20240315.json           [上传]            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5. 批处理视图 (BatchTaskView)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  批处理任务                                            [+ 新建批处理]      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────────────┐  ┌────────────────────────────────────┐  │
│  │     可用设备 (拖拽选中)       │  │         任务配置                   │  │
│  │                              │  │                                     │  │
│  │  ┌─────┐ ┌─────┐ ┌─────┐    │  │  任务描述:                         │  │
│  │  │ D1 │ │ D2 │ │ D3 │    │  │  ┌─────────────────────────────────┐ │  │
│  │  │ ☐   │ │ ☑   │ │ ☐   │    │  │ 打开微信搜索附近的人              │ │  │
│  │  └─────┘ └─────┘ └─────┘    │  │ └─────────────────────────────────┘ │  │
│  │                              │  │                                     │  │
│  │  ┌─────┐ ┌─────┐ ┌─────┐    │  │  模式: (○) 谨慎  (●) 非谨慎          │  │
│  │  │ D4 │ │ D5 │ │ D6 │    │  │                                     │  │
│  │  │ ☑   │ │ ☐   │ │ ☑   │    │  │ 设备数量: 3 台                     │  │
│  │  └─────┘ └─────┘ └─────┘    │  │ 预计耗时: ~15 分钟                   │  │
│  │                              │  │                                     │  │
│  │  已选择: 3 台                │  │  ┌─────────────────────────────────┐ │  │
│  │  [清除选择]                 │  │  │        [▶ 开始执行]              │ │  │
│  └──────────────────────────────┘  └────────────────────────────────────┘  │
│                                                                             │
│  执行进度:                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  D1: ████████████████████ 完成                                        │  │
│  │  D2: ████████████░░░░░░░░ 50% (tap)                                  │  │
│  │  D3: ░░░░░░░░░░░░░░░░░░░░  等待中                                      │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 单设备 Agent 窗口

### 入口方式

1. **设备卡片按钮** - 点击 "Agent" 按钮进入
2. **双击设备卡片** - 直接进入 Agent 窗口
3. **右键菜单** - "打开 Agent 窗口"

### Agent 执行过程展示

每个 Agent 循环包含三个阶段，UI 需要完整展示：

```typescript
interface AgentStep {
  phase: 'reasoning' | 'action' | 'observation';
  reasoning?: {
    thought: string;           // 思考过程
    plan: string;              // 执行计划
    confidence: number;        // 置信度 0-1
  };
  action?: {
    type: ActionType;          // tap, type, swipe, launch, etc.
    params: Record<string, any>;
    description: string;       // 自然语言描述
  };
  observation?: {
    screenshot: string;        // 截图 URL
    elements: ScreenElement[]; // 识别到的元素
    app: string;               // 当前应用
    description: string;       // 场景描述
  };
}
```

### 界面分区

| 区域 | 占比 | 内容 |
|------|------|------|
| 截图预览 | 40% | 实时手机屏幕，可交互标注 |
| 过程面板 | 40% | Reason / Act / Observation |
| 历史步骤 | 20% | 滚动历史记录 |

### 交互操作

- **截图交互**: 点击截图可放大，标注点击/输入位置
- **步骤追溯**: 点击历史步骤可回看当时截图
- **中断任务**: 可随时中断正在执行的任务
- **模式切换**: 实时切换谨慎/非谨慎模式

---

## 谨慎模式

### 模式说明

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| **谨慎模式** | 每步动作需用户确认 | 关键操作、测试阶段 |
| **非谨慎模式** | 自动执行所有动作 | 批量操作、熟悉流程后 |

### 谨慎模式流程

```
用户发送命令
     │
     ▼
┌─────────┐
│Reasoning│ ←──────┐
└────┬────┘       │
     │ 思考完成    │
     ▼            │
┌─────────┐       │ 拒绝
│  Act    │ ──────┼────→ 记录拒绝原因，继续思考
└────┬────┘       │
     │ 等待确认    │
     ▼            │
┌─────────┐       │
│ 确认?   │ ──────┼────→ 拒绝
└────┬────┘       │
     │ 确认       │
     ▼            │
┌─────────┐       │
│ 执行动作│       │ 跳过
└────┬────┘ ──────┘
     │
     ▼
┌─────────────┐
│ Observation │
└──────┬──────┘
       │
       ▼
   循环继续
```

### 快捷操作

| 操作 | 效果 |
|------|------|
| 确认 (✓) | 执行当前动作，继续下一步 |
| 拒绝 (✗) | 跳过当前动作，让 Agent 重新思考 |
| 跳过 (▶) | 跳过当前动作，继续下一步 |
| 全部确认 | 谨慎模式临时切换为非谨慎，执行完当前任务 |

### 记住选择

用户可勾选 "本次会话记住我的选择"，将选择应用到后续所有动作：
- 全部确认 / 全部拒绝 / 全部跳过

---

## 日志系统

### 日志类型

| 类型 | 图标 | 颜色 | 说明 |
|------|------|------|------|
| 设备连接 | 📱 | 蓝色 | 设备上线/下线 |
| 任务开始 | 📋 | 蓝色 | 任务创建 |
| 任务完成 | ✅ | 绿色 | 任务成功结束 |
| 任务失败 | ❌ | 红色 | 任务执行失败 |
| 动作执行 | 🎯 | 蓝色 | 动作开始执行 |
| 动作成功 | ✓ | 绿色 | 动作执行成功 |
| 动作失败 | ⚠️ | 黄色 | 动作执行失败 |
| 截图上传 | 📸 | 灰色 | 截图生成 |
| 系统消息 | ℹ️ | 蓝色 | 系统通知 |

### 日志数据结构

```typescript
interface LogEntry {
  id: string;
  timestamp: string;           // ISO 8601
  device_id: string;
  task_id?: string;
  type: LogType;
  level: 'info' | 'warning' | 'error' | 'success';
  message: string;
  details?: Record<string, any>; // 扩展信息
  screenshot_url?: string;      // 相关截图
}

interface LogUploadRequest {
  device_id: string;
  logs: LogEntry[];
  client_info: {
    version: string;
    platform: string;
  };
}
```

### 日志上报流程

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   客户端    │     │   前端      │     │   服务端    │
│  (Device)  │     │   (Web)    │     │  (Server)  │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                  │                   │
       │ 1. 本地日志       │                   │
       │ 存储              │                   │
       │                  │                   │
       │ 2. 日志上报       │                   │
       │ ───────────────► │                   │
       │                  │ 3. 格式化          │
       │                  │ ───────────────►  │
       │                  │                   │ 4. 存储
       │                  │                   │ 到数据库
       │                  │                   │
       │                  │ 5. 确认收到        │
       │ ◄─────────────── │ ◄───────────────  │
       │                  │                   │
```

### 日志导出

支持导出格式：
- **JSON** - 完整数据，便于程序处理
- **CSV** - 表格格式，便于分析
- **TXT** - 纯文本，便于阅读

---

## 状态机管理

### 设备状态流转

```
                    ┌─────────────────────┐
                    │                     │
                    │     ┌───────┐       │
              ┌─────┴────►│ idle  │◄──────┘
              │           └───┬───┘
              │               │
              │               │ 任务开始
              │               ▼
              │           ┌───────┐
    任务失败  │           │ busy  │
    或中断    │           └───┬───┘
              │               │
              │               │ 任务结束
              │               ▼
              │           ┌───────┐
              └──────────►│ idle  │
                          └───┘

    ┌───────────┐                    ┌───────────┐
    │  offline  │◄──────断开────────►│  online   │
    └───────────┘                    └───────────┘
```

### 命令发送限制

| 设备状态 | 命令发送 | 提示信息 |
|----------|----------|----------|
| `idle` | ✅ 允许 | - |
| `busy` | ❌ 禁止 | "设备正在执行任务，请等待完成后重试" |
| `offline` | ❌ 禁止 | "设备已离线，无法发送命令" |
| `error` | ⚠️ 警告后可发送 | "设备状态异常，是否继续？" |

### UI 反馈

```typescript
// 命令发送前的状态检查
function canSendCommand(device: Device): { allowed: boolean; reason?: string } {
  switch (device.status) {
    case 'idle':
      return { allowed: true };
    case 'busy':
      return { allowed: false, reason: '设备正在执行任务，请等待完成后重试' };
    case 'offline':
      return { allowed: false, reason: '设备已离线，无法发送命令' };
    case 'error':
      return { allowed: true, reason: '设备状态异常，请注意操作安全' };
  }
}

// 命令输入框禁用状态
<input
  disabled={!canSendCommand(device).allowed}
  placeholder={canSendCommand(device).reason || '输入命令...'}
/>
```

---

## 批处理功能

### 多选方式

#### 方式一：复选框选择（简单）

```
┌─────────────────────────────────────────┐
│  设备列表                      [全选]   │
├─────────────────────────────────────────┤
│  ☐  Device 1 - vivo XFold3             │
│  ☑  Device 2 - 小米 13                 │
│  ☑  Device 3 - Mate 60 Pro             │
│  ☐  Device 4 - OPPO Find X7            │
│  ☑  Device 5 - 三星 S24 Ultra          │
├─────────────────────────────────────────┤
│  已选择: 3 台设备            [清除]     │
│  [下一步: 配置任务]                     │
└─────────────────────────────────────────┘
```

#### 方式二：拖拽分区（高级）

```
┌─────────────────────────────────────────────────────────────────────┐
│                          拖拽分配区域                                 │
├───────────────────────┬─────────────────────────────────────────────┤
│                       │                                             │
│    可用设备            │              已选设备                       │
│    (拖拽到右侧)        │              (可拖拽排序)                   │
│                       │                                             │
│   ┌─────┐  ┌─────┐   │   ┌─────┐  ┌─────┐  ┌─────┐               │
│   │ D1  │  │ D2  │   │   │ D3  │  │ D4  │  │ D5  │               │
│   └─────┘  └─────┘   │   └─────┘  └─────┘  └─────┘               │
│                       │                                             │
│   ┌─────┐  ┌─────┐   │              Task A ─────────►              │
│   │ D6  │  │ D7  │   │              ┌─────────────────┐            │
│   └─────┘  └─────┘   │              │ 打开微信        │            │
│                       │              └─────────────────┘            │
│                       │                                             │
│                       │              Task B ─────────►              │
│                       │              ┌─────────────────┐            │
│                       │              │ 打开设置        │            │
│                       │              └─────────────────┘            │
│                       │                                             │
└───────────────────────┴─────────────────────────────────────────────┘
```

### 批处理配置

```typescript
interface BatchTaskConfig {
  mode: 'parallel' | 'sequential';
  tasks: Array<{
    name: string;
    instruction: string;
    devices: string[];           // 设备 ID 列表
    mode: 'cautious' | 'normal';
    max_steps?: number;
  }>;
  settings: {
    stop_on_error: boolean;      // 单设备失败是否停止全部
    continue_on_timeout: boolean; // 超时后是否继续
    notify_on_complete: boolean; // 全部完成是否通知
  };
}
```

### 执行监控

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  批处理执行中 - 2/5 设备完成                                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Device 1 (vivo XFold3)                    ████████████░░░  80%      │   │
│  │  任务: 打开微信搜索附近的人                                         │   │
│  │  状态: 执行中... 步骤 8/10                                         │   │
│  │  [查看详情] [中断]                                                  │   │
│  ├─────────────────────────────────────────────────────────────────────┤   │
│  │  Device 2 (小米 13)                         ████████████████ 完成    │   │
│  │  任务: 打开微信搜索附近的人                                         │   │
│  │  状态: ✅ 成功完成 (12步，耗时 2分30秒)                             │   │
│  │  [查看详情] [查看截图]                                              │   │
│  ├─────────────────────────────────────────────────────────────────────┤   │
│  │  Device 3 (Mate 60 Pro)                    ░░░░░░░░░░░░░░░░  待执行 │   │
│  │  任务: 打开微信搜索附近的人                                         │   │
│  │  状态: ⏳ 等待设备空闲                                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  [全部暂停] [全部恢复] [导出结果]                                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 技术实现

### 前端技术栈

| 组件 | 技术 | 版本 |
|------|------|------|
| 框架 | React | 18.x |
| UI 库 | Ant Design | 5.x |
| 状态管理 | Zustand | 4.x |
| 实时通信 | Socket.IO Client | 4.x |
| 构建工具 | Vite | 5.x |
| 样式 | Tailwind CSS | 3.x |

### 目录结构

```
Distributed/Web/
├── public/
├── src/
│   ├── components/
│   │   ├── common/           # 通用组件
│   │   │   ├── Header.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   └── StatusIndicator.tsx
│   │   ├── device/           # 设备相关
│   │   │   ├── DeviceCard.tsx
│   │   │   ├── DeviceList.tsx
│   │   │   └── DeviceStatus.tsx
│   │   ├── agent/            # Agent 交互
│   │   │   ├── AgentChat.tsx
│   │   │   ├── AgentScreenshot.tsx
│   │   │   ├── AgentReasoning.tsx
│   │   │   ├── AgentAction.tsx
│   │   │   └── ActionConfirmDialog.tsx
│   │   ├── batch/            # 批处理
│   │   │   ├── BatchTaskView.tsx
│   │   │   ├── DeviceSelector.tsx
│   │   │   └── BatchProgress.tsx
│   │   └── log/              # 日志
│   │       ├── LogPanel.tsx
│   │       ├── LogEntry.tsx
│   │       └── LogUpload.tsx
│   ├── hooks/
│   │   ├── useWebSocket.ts
│   │   ├── useDevice.ts
│   │   └── useAgent.ts
│   ├── stores/
│   │   ├── deviceStore.ts
│   │   ├── agentStore.ts
│   │   └── batchStore.ts
│   ├── services/
│   │   ├── api.ts
│   │   └── websocket.ts
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── AgentWindow.tsx
│   │   └── BatchTask.tsx
│   ├── App.tsx
│   └── main.tsx
├── package.json
├── vite.config.ts
├── tailwind.config.js
└── tsconfig.json
```

### 状态管理 (Zustand)

```typescript
// deviceStore.ts
interface DeviceState {
  devices: Record<string, Device>;
  selectedDevices: Set<string>;

  // Actions
  updateDevice: (deviceId: string, data: Partial<Device>) => void;
  selectDevice: (deviceId: string) => void;
  deselectDevice: (deviceId: string) => void;
  toggleDevice: (deviceId: string) => void;
  selectAll: () => void;
  deselectAll: () => void;
}

// agentStore.ts
interface AgentState {
  // 当前 Agent 会话
  currentDeviceId: string | null;
  mode: 'cautious' | 'normal';

  // 执行状态
  isRunning: boolean;
  currentStep: AgentStep | null;
  history: AgentStep[];

  // 待确认动作队列
  pendingActions: AgentStep[];

  // Actions
  sendCommand: (command: string) => void;
  confirmAction: (actionId: string) => void;
  rejectAction: (actionId: string, reason?: string) => void;
  skipAction: (actionId: string) => void;
  setMode: (mode: 'cautious' | 'normal') => void;
  interrupt: () => void;
}
```

### WebSocket 消息处理

```typescript
// services/websocket.ts
class WebSocketService {
  private socket: Socket | null = null;

  connect(token: string) {
    this.socket = io('/api/ws', {
      auth: { token },
      transports: ['websocket'],
    });

    this.socket.on('connect', () => {
      console.log('WebSocket connected');
    });

    // 设备状态更新
    this.socket.on('device_status_changed', (data) => {
      deviceStore.getState().updateDevice(data.device_id, {
        status: data.status,
        ...data.data,
      });
    });

    // Agent 步骤更新
    this.socket.on('agent_step', (data) => {
      agentStore.getState().appendStep(data);
    });

    // 任务结果
    this.socket.on('task_result', (data) => {
      agentStore.getState().setResult(data);
    });
  }
}
```

---

## 组件清单

### 通用组件

| 组件名 | 说明 | Props |
|--------|------|-------|
| `StatusIndicator` | 状态指示灯 | `status: DeviceStatus`, `size?: 'sm' \| 'md' \| 'lg'` |
| `ConfirmDialog` | 确认对话框 | `title`, `message`, `onConfirm`, `onCancel` |
| `LoadingSpinner` | 加载动画 | `size?: number`, `color?: string` |
| `Toast` | 提示消息 | `type: 'success' \| 'error' \| 'warning' \| 'info'`, `message` |

### 设备组件

| 组件名 | 说明 | Props |
|--------|------|-------|
| `DeviceCard` | 设备卡片 | `device: Device`, `onClick`, `onLogClick`, `onAgentClick` |
| `DeviceList` | 设备列表 | `devices: Device[]`, `selectedIds`, `onSelect` |
| `DeviceStatus` | 设备状态标签 | `status: DeviceStatus` |
| `DeviceFilter` | 设备筛选器 | `onFilterChange` |

### Agent 组件

| 组件名 | 说明 | Props |
|--------|------|-------|
| `AgentChat` | Agent 聊天主组件 | `deviceId`, `initialMode` |
| `AgentScreenshot` | 截图预览 | `url`, `annotations?`, `onAnnotationAdd` |
| `AgentReasoning` | 思考过程展示 | `step: AgentStep['reasoning']` |
| `AgentAction` | 动作展示 | `step: AgentStep['action']`, `onConfirm`, `onReject`, `onSkip` |
| `ActionConfirmDialog` | 动作确认弹窗 | `action`, `onConfirm`, `onReject`, `onSkip` |
| `ModeSwitch` | 模式切换 | `mode`, `onChange` |
| `StepHistory` | 步骤历史 | `steps: AgentStep[]`, `currentStep` |

### 批处理组件

| 组件名 | 说明 | Props |
|--------|------|-------|
| `BatchTaskView` | 批处理主视图 | - |
| `DeviceSelector` | 设备选择器 | `devices`, `selectedIds`, `onChange` |
| `DraggableDevice` | 可拖拽设备 | `device`, `onDragStart`, `onDragEnd` |
| `DropZone` | 拖放区域 | `onDrop`, `acceptedTypes` |
| `BatchConfig` | 批处理配置 | `config`, `onChange` |
| `BatchProgress` | 批处理进度 | `tasks`, `onInterrupt`, `onExport` |

### 日志组件

| 组件名 | 说明 | Props |
|--------|------|-------|
| `LogPanel` | 日志面板 | `deviceId`, `filters`, `onExport` |
| `LogEntry` | 单条日志 | `entry: LogEntry` |
| `LogFilter` | 日志筛选 | `filters`, `onChange` |
| `LogUpload` | 日志上传 | `deviceId`, `onUpload` |
| `LogTimeline` | 日志时间线 | `entries` |

---

## 消息协议

### WebSocket 消息

#### 客户端 → 服务端

```typescript
// 订阅设备
interface SubscribeMessage {
  type: 'subscribe';
  subscriptions: Array<{
    type: 'device' | 'task';
    id: string;
  }>;
}

// Agent 命令
interface AgentCommandMessage {
  type: 'agent_command';
  device_id: string;
  command: string;
  mode: 'cautious' | 'normal';
}

// 动作确认 (谨慎模式)
interface ActionConfirmMessage {
  type: 'action_confirm';
  task_id: string;
  step_id: string;
  action: 'confirm' | 'reject' | 'skip';
  reason?: string;  // reject 时可选
}

// 日志上报
interface LogUploadMessage {
  type: 'log_upload';
  device_id: string;
  logs: LogEntry[];
}
```

#### 服务端 → 客户端

```typescript
// Agent 思考过程
interface AgentReasoningMessage {
  type: 'agent_reasoning';
  task_id: string;
  device_id: string;
  step: {
    id: string;
    reasoning: {
      thought: string;
      plan: string;
      confidence: number;
    };
  };
}

// Agent 待确认动作 (谨慎模式)
interface AgentPendingActionMessage {
  type: 'agent_pending_action';
  task_id: string;
  device_id: string;
  step: {
    id: string;
    action: {
      type: string;
      params: Record<string, any>;
      description: string;
    };
    reasoning: {
      thought: string;
      confidence: number;
    };
    screenshot_url?: string;
  };
}

// Agent 执行结果
interface AgentActionResultMessage {
  type: 'agent_action_result';
  task_id: string;
  device_id: string;
  step_id: string;
  result: 'success' | 'failure';
  observation?: {
    screenshot_url?: string;
    description?: string;
    app?: string;
  };
  error?: {
    code: string;
    message: string;
  };
}

// 任务完成
interface TaskCompleteMessage {
  type: 'task_complete';
  task_id: string;
  device_id: string;
  status: 'completed' | 'failed' | 'interrupted';
  result: {
    finish_message: string;
    total_steps: number;
    duration_seconds: number;
    final_screenshot?: string;
  };
}
```

### REST API

```typescript
// 获取设备列表
GET /api/v1/devices

// 获取设备详情
GET /api/v1/devices/:device_id

// 获取设备日志
GET /api/v1/logs/:device_id
Query: {
  start_time?: string;
  end_time?: string;
  level?: 'info' | 'warning' | 'error';
  task_id?: string;
}

// 上报日志
POST /api/v1/logs/:device_id/upload
Body: {
  logs: LogEntry[];
}

// 创建任务
POST /api/v1/tasks
Body: {
  device_id: string;
  instruction: string;
  mode: 'cautious' | 'normal';
  max_steps?: number;
}

// 确认/拒绝动作
POST /api/v1/tasks/:task_id/steps/:step_id/decision
Body: {
  action: 'confirm' | 'reject' | 'skip';
  reason?: string;
}

// 中断任务
POST /api/v1/tasks/:task_id/interrupt

// 批量创建任务
POST /api/v1/tasks/batch
Body: {
  tasks: Array<{
    device_id: string;
    instruction: string;
    mode: 'cautious' | 'normal';
  }>;
}
```

---

## 附录

### A. 错误处理

| 错误码 | 错误信息 | 处理方式 |
|--------|----------|----------|
| `E001` | 设备不在线 | 提示用户，禁用发送按钮 |
| `E002` | 设备忙碌中 | 提示用户等待或中断当前任务 |
| `E003` | 任务创建失败 | 显示错误信息，可重试 |
| `E004` | 动作执行失败 | 显示失败原因，更新截图 |
| `E005` | WebSocket 断开 | 自动重连，显示连接状态 |
| `E006` | 日志上传失败 | 保存本地，可手动重传 |

### B. 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl + Enter` | 发送命令 |
| `Ctrl + C` | 中断当前任务 |
| `Ctrl + M` | 切换谨慎/非谨慎模式 |
| `Ctrl + L` | 打开日志面板 |
| `Ctrl + B` | 打开批处理视图 |
| `Escape` | 关闭当前弹窗/窗口 |

### C. 响应式设计

| 断点 | 布局 |
|------|------|
| `< 768px` | 单列布局，底部 Tab 导航 |
| `768px - 1024px` | 两列布局，Sidebar 可折叠 |
| `> 1024px` | 三列布局，完整视图 |
