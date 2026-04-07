# API 精确定义（收敛版）

## 1️⃣ POST `/device/status`

### 作用

客户端**上报当前在线设备全集**（只包含在线设备）

### 调用时机

* 设备状态变化时（增 / 减 / 状态变更）
* 或定期全量上报（默认 10min）

---

### 请求体

```json
{
  "timestamp": 1666125234,
  "available_device_list": [
    {
      "device_name":"设备名称",
      "device_id": "xxx",
      "device_type": "android | ios | harmony",
      "status": "ok | busy | offline"
    }
  ]
}
```

---

### 语义约束

1. `available_device_list`

   * 表示“当前客户端视角下所有在线设备”
   * **不包含离线设备**
2. Server 处理逻辑

   * 直接覆盖该客户端的设备视图（不是增量合并）
   * 更新内存中的 `DeviceStatusTable`
3. `status` 含义

   * ok：可接任务
   * busy：执行中
   * offline：仅用于过渡状态（理论上不应出现在列表中）

---

---

## 2️⃣ POST `/observe/result`

### 作用

客户端回传一次 action 执行结果（Observation）

---

### 调用时机

* 每次 action 执行结束（成功或失败都必须调用一次）

---

### 请求体

```json
{
  "device_id": "xxx",
  "version": 1,
  "timestamp": 1666125234,
  "log": [
    "...",
    "..."
  ],
  "error": null,
  "screenshot": "base64_or_url",
  "code": 20001
}
```

---

### 字段约束

1. `device_id + version`

   * 唯一标识一次 ReAct 执行轮次
   * 必须与 Server 下发一致
2. `log`

   * 按执行顺序排列
   * 不允许为空数组（至少一条）
3. `error`

   * 成功时必须为 `null`
   * 失败时必须包含错误描述（字符串）
4. `code`

   * 20001：执行成功
   * 40002：执行失败
5. `screenshot`

   * 当前执行结束时的最终屏幕状态
   * 不允许为空（即使失败）

---

### 语义约束

* **该接口必须“恰好调用一次”对应一个 version**
* Server 以 `(device_id, version)` 做幂等控制：

  * 重复请求 → 覆盖 or 忽略（由实现决定，但不得重复推进状态机）

---

---

## 3️⃣ WebSocket `/ack`

### 作用

客户端确认“已接收到 action”

---

### 发送时机

* 收到 `/actionSend` 后立即发送（**不等待执行**）

---

### 消息体

```json
{
  "device_id": "xxx",
  "version": 1,
  "timestamp": 1666125234
}
```

---

### 语义约束

1. ack 表示：

   * 已接收任务
   * 不代表执行成功
2. 必须满足：

   * 一个 `/actionSend` → **最多一个 ack**
3. version 去重：

   * 若重复收到同 version action：

     * 不执行
     * 直接返回 ack

---

---

## 4️⃣ WebSocket `/actionSend`

### 作用

Server 向 Client 下发执行指令

---

### 发送时机

* AgentLoop 生成合法 action 后

---

### 消息体

```json
{
  "device_id": "xxx",
  "version": 1,
  "timestamp": 1666125234,
  "action": "xxx"
}
```

---

### 字段约束

1. `version`

   * 单设备单调递增
   * 唯一标识当前 ReAct 轮次
2. `action`

   * 必须已经通过 ActionParse 校验
   * 必须属于该设备支持的指令集

---

### 语义约束

1. Server 发送后进入：

   * `WAIT_ACK`
2. Client 行为：

   * 收到后立即 ack
   * 再执行 action
3. 重试机制（Server侧）

   * 未收到 ack：

     * 重试 3 次
     * 每次间隔 15s

---

---

# ✅ 四个接口的“闭环关系”（核心）

```id=
actionSend → ack → 执行 → observe/result
```

严格保证：

* 每个 `version`：

  * 1 次 actionSend
  * ≤1 次 ack
  * =1 次 observe/result
