# Agent 系统提示词说明（交付物 #2）

> 对应需求文档 §4 F4、§5 处理流程、§9 Trace 规格。本文件记录 Agent 规划环节的提示词全文与两种规划模式，便于复现与评审。

## 1. 双模规划器概览

规划器负责「读当前 State → 决定下一步动作」，提供两种实现（`report_agent/agent/planner.py`）：

| 模式 | 类 | 触发条件 | 说明 |
|------|----|----------|------|
| 确定性规划 | `DeterministicPlanner` | 无 `OPENAI_API_KEY`，或 `use_llm_planner=false`，或 LLM 解析失败回退 | 按主链路固定顺序脚本化规划，**离线即可跑通全链路**；亦作为 V1-006 对照实验基线 |
| LLM 规划 | `LLMPlanner` | 配置了可用 LLM 且 `use_llm_planner=true` | 让模型在工具集中自主选下一步；输出 JSON 动作，解析失败自动回退确定性规划 |

选择逻辑见 `build_planner()`：仅当 `use_llm_planner and llm_client.available` 才用 `LLMPlanner`，否则 `DeterministicPlanner`。

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

## 4. LLM 规划器提示词全文

`LLMPlanner._build_prompt(state)` 组装的完整 prompt（无独立 system 角色，全部并入 user 消息）：

```text
你是测试报告生成 Agent 的规划器。根据当前状态选择下一步动作，只输出 JSON。
可用工具：{tool_specs}          # 由 ToolRegistry.specs() 注入，见《工具定义说明》
当前状态：{state_snapshot}       # state.snapshot()：已完成的取数/统计摘要，不含大对象全量
主链路顺序：get_version_info → get_version_bugs → compute_statistics → render_report → finish。
取数失败则 finish。输出格式：{"thought": "...", "type": "tool|finish", "tool": "工具名", "args": {}}
只输出 JSON，不要多余文字。
```

动作解析（`_parse`）：容错截取首个 `{` 到末个 `}`，`type` 限 `tool|finish`，非法即回退确定性规划。

## 5. 防幻觉硬约束

- **统计数值一律不交给模型**：LLM 只做「选下一步」的规划，绝不参与汇总计算；所有指标由 `compute_statistics` 纯函数产出（§4 F3、§7 契约）。
- **依赖数据由循环注入**：`compute_statistics` 的 `version_info/bugs`、`render_report` 的 `stats_obj` 均由 `loop._exec_tool` 注入，prompt/LLM 无需搬运大对象（降低注入面与幻觉空间）。
- **失败即如实终止**：取数失败/版本不存在/步数超限都走 `render_failure_report`/`render_step_limit_report`，报告不含任何虚构数字。

## 6. 版本名抽取

`extract_version_name` 用正则 `VERSION_PATTERN`（`([A-Za-z\u4e00-\u9fa5]+\s*)?[Vv]?\d+(?:\.\d+)+`）从自然语言里取版本串，支持任意段数（如 `V6.6.7.2`）；抽不到返回 `None` 触发澄清。
