# 验收证据（交付物 #5 / #6）

对应需求文档 §8「本期交付证据为 V1-001 的完整 Trace 与一份样例报告各一份」、§9 Trace 规格、§11 交付物 #5/#6。

| 文件 | 说明 |
|------|------|
| `V1-001_测试报告样例.md` | V1-001「正常版本、缺陷数据完整」场景产出的 Markdown 报告。**为 mock 脱敏样例**（UniResearch V5.0.0，24 条合成缺陷），结构、章节、数值口径与真实产出完全一致，但**不含任何公司真实缺陷数据**。 |
| `V1-001_Trace.json` | 同一次 mock 运行的逐轮 Trace，含完整链路：规划推理 → `get_version_info` → `get_version_bugs` → `compute_statistics` → `render_report` → 终止原因，满足 §9「Trace 可见完整链路」判定清单；无 token/凭据。 |

## 说明

- **为何用 mock 而非 live**：真实禅道跑出的报告含公司实际缺陷数量、模块、状态、日期等业务数据。为避免把生产数据写入 git 历史（尤其推送到 GitHub 后难以彻底抹除），本目录留存的是**结构等价、无真实数据**的 mock 脱敏样例。真实 live 报告只在本地 `output/`、`traces/`（均已 `.gitignore`）生成，不入版本控制。
- **规划模式**：本机未配置 `OPENAI_API_KEY`，`build_planner` 自动降级为 `DeterministicPlanner`。**真实 LLM 规划的 V1-001 跑批**与 **V1-006 对照实验反例**（交付物 #7）待补 Key 后生成（见 `experiments/v1_006_comparison.py`，无 Key 时拒绝伪造、不产出）。
- **验证方式**：`python -m report_agent.main "帮我生成 UniResearch V5.0.0 的测试报告" --mode mock` 可离线复现本报告与 Trace。
