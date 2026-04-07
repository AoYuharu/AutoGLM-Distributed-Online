很好，这一步非常关键。你现在已经不是在“设计”，而是在进入**验证系统可靠性**阶段了。

我给你一套**可以直接执行的测试要求（偏工程验收标准）**，覆盖你系统最容易出问题的点，而且不需要额外复杂工具。

---

# 一、测试目标（先明确标准）

这套测试的核心验证三件事：

1. **不会重复执行 action（幂等性）**
2. **状态不会错乱（Session / Device）**
3. **网络异常下系统仍可恢复**

---

# 二、基础正确性测试（必须先过）

## 测试1：单任务正常流程

步骤：

1. 启动 server + client
2. 发起一个简单任务（如点击操作）
3. 观察完整链路：

```text
WAIT_FOR_PUSH → WAIT_OBSERVATION → WAIT_FOR_PUSH → FINISHED
```

验证点：

* 只发送一次 action
* 只收到一次 observation
* history 顺序正确
* device 状态：OK → BUSY → OK

---

## 测试2：多轮 ReAct

步骤：

1. 构造一个需要 2~3 步的任务（如打开App → 点击按钮）
2. 观察多轮循环

验证点：

* 每轮都有：

  * action → observation → 下一轮
* history 累积正确
* 没有跳轮 / 丢轮

---

# 三、幂等性测试（重点）

## 测试3：重复 observation（手动模拟）

步骤：

1. client 正常执行一次任务
2. **手动重复发送同一个 observation（完全相同 payload）**

验证点：

* server 不重复写入 history
* session 不重复推进
* 状态不变化（仍然 WAIT_FOR_PUSH）

---

## 测试4：重复 action（模拟网络抖动）

步骤：

1. server 发送 action
2. **手动让 client 再执行一次同一个 action（相同 task_id / version）**

验证点：

* client 不重复执行（关键）
* 或执行但只返回一次结果（取决你实现）
* server 不产生两条 observation

---

# 四、网络异常测试（最重要）

## 测试5：observation 丢响应（模拟HTTP失败）

步骤：

1. client 发送 observation
2. **在 server 返回前强制断开连接（或直接丢弃响应）**
3. client 重发 observation

验证点：

* server 只处理一次
* 不重复写 history
* session 正常推进

---

## 测试6：ack 丢失（WebSocket异常）

步骤：

1. server 发送 action
2. client 收到但**不返回 ack**
3. server 触发重试

验证点：

* client 不重复执行 action（关键）
* 最终只产生一个 observation

---

## 测试7：observation 延迟

步骤：

1. client 执行 action
2. **延迟 30s 再发送 observation**

验证点：

* server 不提前错误结束（或正确触发 timeout）
* 延迟到达后：

  * 能正确接入 session（不丢）

---

# 五、状态一致性测试

## 测试8：device 状态切换

步骤：

1. 任务执行过程中：

   * 手动将 device 标记为 offline

验证点：

* AgentLoop 不再下发 action
* 触发 DeviceUnExpectedStatusException

---

## 测试9：busy 冲突

步骤：

1. 在 device 还未返回 observation 时
2. 强行让 server 再发一个 action

验证点：

* 被拒绝（device != OK）
* 不会并发执行两个任务

---

# 六、边界测试（容易忽略）

## 测试10：大日志

步骤：

1. 构造一个 observation：

   * log 非常长（例如 1MB）

验证点：

* history 被裁剪
* 不影响后续推理
* 不导致内存暴涨

---

## 测试11：最大 step 限制

步骤：

1. 构造一个“无法完成”的任务（让 LLM一直输出错误 action）

验证点：

* 在 max_step 后：

  * session → FINISHED
  * 不再循环

---

# 七、恢复能力测试（进阶但很重要）

## 测试12：server 重启

步骤：

1. 正在执行任务（WAIT_OBSERVATION）
2. 重启 server
3. client 发送 observation

验证点：

* session 能恢复（或至少不崩）
* 不出现脏状态

---

# 八、并发测试（简单版）

## 测试13：多设备

步骤：

1. 同时连接 2~3 个设备
2. 分别执行任务

验证点：

* session 完全隔离
* 不串数据
* 不串 action

---

# 九、验收标准（你可以写进文档）

通过标准：

* ✔ 所有测试中 **无重复执行 action**
* ✔ session 状态始终符合状态机
* ✔ 无 observation 错配
* ✔ 网络异常下系统可恢复
* ✔ 无死循环（max_step 生效）

---

# 十、一句话总结这套测试的本质

> 这不是在测“功能对不对”，
> 而是在测：**系统在“出问题时还能不能保持正确”**

---

如果你下一步想更进一步，我可以帮你做一个：

👉 **自动化测试脚本设计（mock client + mock network）**

那你可以一键跑完所有这些测试，而不是手动测。
