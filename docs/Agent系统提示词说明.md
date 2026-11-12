# Agent 系统提示词说明（交付物 #2）

> 对应需求文档 §4 F4、§5 处理流程、§9 Trace 规格。本文件记录 Agent 规划环节的提示词全文与两种规划模式，便于复现与评审。

## 1. 双模规划器概览

规划器负责「读当前 State → 决定下一步动作」，提供两种实现（`report_agent/agent/planner.py`）：

| 模式 | 类 | 触发条件 | 说明 |
|------|----|----------|------|
| 确定性规划 | `DeterministicPlanner` | 无 `OPENAI_API_KEY`，或 `use_native_fc=false`，或原生 FC 失败回退 | 按主链路固定顺序脚本化规划，**离线即可跑通全链路**；亦作为 V1-006 对照实验基线 |
| 原生 FC 规划 | `NativeFCPlanner` | 配置了可用 LLM 且 `use_native_fc=true` | 使用原生 Function Calling 协议（tools 参数 + tool_calls 解析 + role:tool 回传），模型自主决定调用顺序；失败自动回退确定性规划 |

选择逻辑见 `build_planner()`：仅当 `use_native_fc and llm_client.available` 才用 `NativeFCPlanner`，否则 `DeterministicPlanner`。

## 2. 主链路动作顺序（两种模式共同遵守）

```
识别版本名 → get_version_info → get_version_bugs → compute_statistics → render_report → finish
```

- 缺版本名：返回 `clarify`（最多澄清 `agent.clarify_max_times` 次，仍缺失则终止，不编造版本号）。
- `get_version_info` 返回 `fetch_status != ok`：直接 `finish` → 失败说明版报告。
- 取数失败：立即终止、不循环重试、不编造数字。

## 3. 确定性规划器思考文案（逐动作）

`DeterministicPlanner` 每步产出的 `thought`（写入 Trace 的 `llm_reasoning` 轮）：

| 判定条件 | 动作 | thought 文案 |
|----------|------|--------------|
| `version_info is None` | `get_version_info` | 第一步：获取版本基础信息（描述、测试起止时间） |
| `fetch_status != ok` | `finish` | 版本信息获取失败，终止并输出失败说明版报告 |
| `bugs is None` | `get_version_bugs` | 第二步：获取该版本全部缺陷明细 |
| `stats is None` | `compute_statistics` | 第三步：调用独立统计工具计算指标 |
| `report_markdown is None` | `render_report` | 第四步：按章节结构渲染 Markdown 报告 |
| 以上都有 | `finish` | 报告已生成，结束 |

## 4. 原生 FC 规划器机制

`NativeFCPlanner` 不使用单一 prompt 拼接，而是维护多轮消息历史：

1. **初始化**：`_init_messages()` 设置 system（角色描述 + 目标）+ user（版本名）
2. **调用模型**：`llm.complete_with_tools(messages, tools=[...])` 其中 tools 为 `ToolRegistry.specs()` 转为 OpenAI 格式
3. **解析响应**：`msg.tool_calls[0].function.name/arguments` 结构化字段，不需要手动解析 JSON
4. **回传观察**：工具执行后 `_append_tool_observation()` 以 `{"role":"tool", "tool_call_id":..., "content":...}` 追加到历史
5. **循环**：模型看到 observation 后自主决定下一个工具或输出最终回答

注意：**prompt 中不包含执行顺序提示**，模型从工具描述和已有 observation 自行推断。

回退机制：`next_action()` 内任何异常（API 失败、网络超时、余额不足等）均自动回退到 `DeterministicPlanner`，保证主链路不中断。

## 5. 防幻觉硬约束

- **统计数值一律不交给模型**：LLM 只做「选下一步」的规划，绝不参与汇总计算；所有指标由 `compute_statistics` 纯函数产出（§4 F3、§7 契约）。
- **依赖数据由循环注入**：`compute_statistics` 的 `version_info/bugs`、`render_report` 的 `stats_obj` 均由 `loop._exec_tool` 注入，prompt/LLM 无需搬运大对象（降低注入面与幻觉空间）。
- **失败即如实终止**：取数失败/版本不存在/步数超限都走 `render_failure_report`/`render_step_limit_report`，报告不含任何虚构数字。

## 6. 版本名抽取

`extract_version_name` 用正则 `VERSION_PATTERN`（`([A-Za-z\u4e00-\u9fa5]+\s*)?[Vv]?\d+(?:\.\d+)+`）从自然语言里取版本串，支持任意段数（如 `V6.6.7.2`）；抽不到返回 `None` 触发澄清。
