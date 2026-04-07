# Distributed PhoneAgent（精简版）

## Client

作为设备执行代理，负责：
任务分发 → 执行 → 日志/截图回传

### Dispatcher（WebSocket 常驻）

接收 Server 下发的 `{device_id, action, version}`：

1. 校验 device_id

   * 不存在 → 尝试发现 + `check_capability`
   * 仍失败 → 返回 `40001`

2. version 去重

   * 已执行过 → 直接返回 ack，不重复执行

3. 执行任务

   * 成功 → 返回 screenshot + log
   * 执行异常 → 返回 `40002`

4. 接收即返回 ack（/ack）

---

### CheckStatus（设备扫描与上报）

常驻线程：

1. 每 3s 扫描设备（Android / Harmony / iOS）
2. 维护 `available_device_list`
3. 若设备状态变化 → POST `/device/status`

定期上报：

* 每 10 min 全量上报（仅在线设备，yaml 可配置）

---

## Server

以 **device 为单位维护 Context**

Context内容：

* system_prompt（不裁剪）
* user_prompt（不裁剪）
* history（裁剪）
* observation（保存最新 + 存库）

---

### Dispatcher（服务端）

负责：

* 接收 Observation
* 写入对应 DeviceSession
* 更新状态为 `WAIT_FOR_PUSH`

---

### ThreadPool

* 默认：4 核心线程 / 8 最大线程
* 处理状态为 `WAIT_FOR_PUSH` 的 Context

---

## Status Design
Session 采用三态模型（WAIT_FOR_PUSH / WAIT_OBSERVATION / FINISHED），用于驱动 ReAct 循环；Device 采用四态模型（OK / BUSY / OFFLINE / ERROR），用于约束任务调度。两者解耦，通过 action 与 observation 进行状态联动

---

## AgentLoop（ReAct 状态机）

### 1. 推理阶段

输入：

* system_prompt
* user_prompt
* history（裁剪）
* 最新 observation

调用第三方 Agent：

* 最多 3 次
* 单次超时 10s
* 超时 → `RemoteAPICallingTimeoutException`

---

### 2. 结果分支

#### （1）任务完成

→ `MISSION_FINISHED`

#### （2）返回 action

进入 ActionParse

---

### 3. ActionParse

1. 解析 action
2. 校验是否符合设备指令集

失败：

* 进入 Self-Reconstruct-Action
* 附：

  * 错误 action
  * 提示信息
  * 可选 action 列表
* 最多 3 次
* 失败 → `ActionParseExceedException`

---

### 4. 设备状态检查

查询 `DeviceStatusTable`：

* 非 ok → `DeviceUnExpectedStatusException`

---

### 5. 下发任务

1. 生成 version（DB 持久化）
2. 发送 action
3. 等待 ack：

* 重试 3 次（15s 间隔）
* 失败 → `DeviceNotAckException`

---

### 6. 等待执行结果

进入 `WAIT_OBSERVATION`

* 超时 → `DeviceObserveTimeoutException`
* 返回 error → `DeviceObserveErrorException`
* 成功 → 写入 observation → `WAIT_FOR_PUSH`

---

## Device-Session Manager

### 生命周期

* Server 启动：初始化 SessionManager（空容器）
* Device 首次出现：懒加载创建 DeviceSession

---

### 新任务开始

调用 `start_new_task`：

* 清空 history
* 清空 observation
* 写入 user_prompt
* 保留 system_prompt
* 状态 → `WAIT_FOR_PUSH / PENDING`

---

### Observation 写入

Dispatcher 调用：

1. 追加 observation（log / screenshot / task_id）
2. 提取 log → 转为 tool 内容写入 history
3. 状态 → `WAIT_FOR_PUSH`

---

### AgentLoop 读取

仅处理 `WAIT_FOR_PUSH`：

构造输入：

* system_prompt
* user_prompt
* history（裁剪）
* 最新 observation

---

### Action 写回

* 写入 history（assistant）
* 状态 → `WAIT_OBSERVATION`

---

### 状态流转

```
WAIT_FOR_PUSH → 推理 → WAIT_OBSERVATION → 写入Observation → WAIT_FOR_PUSH
```

---

### Task 标识

* current_task_id（当前执行）
* last_task_id（最近完成）

用于：

* 幂等校验
* 调试

---

### 上下文裁剪

* 保留：

  * system_prompt
  * user_prompt
* history：限制 token / 条数
* observation：仅最新一条参与推理

---

### 模块约束

* 不发网络请求
* 不参与调度
* 仅：

  * Dispatcher 写
  * AgentLoop 读

---

## API
*详情参照docs/API.md*

---

## 状态码

* 20001：成功
* 40001：设备不存在
* 40002：执行失败