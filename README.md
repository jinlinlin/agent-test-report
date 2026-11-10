# 测试报告生成 Agent V1.0

输入版本名称 → Agent 自行规划取数顺序 → 获取禅道版本信息与缺陷明细 → 调用独立统计工具计算指标 → 输出精简版 Markdown 测试报告。

本项目以**学习单 Agent 开发全流程**为首要目标：LLM 规划、工具调用、短期记忆 State、循环控制器、终止条件、异常兜底各自独立成模块，Trace 逐轮可见。

需求文档见 `docs/测试报告生成Agent V1.0 需求文档.docx`（及同名 `.md` 修订版）。

---

## 目录结构

```
agent-test-report/
├── config/
│   ├── settings.yaml          # 模型/最大步数/mock开关/禅道配置
│   └── .env.example           # OPENAI_API_KEY / ZENTAO_TOKEN（只读）
├── report_agent/
│   ├── main.py                # CLI 入口：组装并运行 Agent，落盘报告与 Trace
│   ├── config.py              # 配置加载（yaml + env + 点路径 overrides）
│   ├── agent/                 # Agent 核心（ReAct 最小循环）
│   │   ├── state.py           #   短期记忆 State
│   │   ├── planner.py         #   规划器：确定性 + LLM 双模式，含版本名抽取
│   │   ├── termination.py     #   终止条件判定
│   │   └── loop.py            #   主循环：规划→工具调用→观察→更新→终止
│   ├── llm/client.py          # OpenAI 兼容客户端（懒加载，无 Key 自动降级）
│   ├── tools/                 # 工具层
│   │   ├── base.py            #   Tool 基类 + 注册表 + ToolResult
│   │   ├── zentao_version.py  #   F1 版本信息工具
│   │   ├── zentao_bugs.py     #   F2 缺陷明细工具
│   │   ├── statistics.py      #   F3 独立统计工具（纯函数，禁止 LLM 汇总）
│   │   ├── render_report.py   #   F4 报告渲染工具
│   │   └── mock_zentao.py     #   F6 mock 数据源 + 故障注入
│   ├── rules/release_engine.py#   F8 发布建议规则引擎（确定性判定）
│   ├── report/                #   报告层
│   │   ├── templates.py       #     固定文案 + 占位符
│   │   └── renderer.py        #     Markdown 渲染（数据→模板，含失败/超限版）
│   ├── schemas/contracts.py   #   第 7 章统计工具输出 JSON 契约
│   └── trace/tracer.py        #   Trace 逐轮记录与落盘
├── tests/
│   ├── unit/                  # 统计工具 + 规则引擎单元测试（U-xxx）
│   └── e2e/                   # 端到端场景测试（V1-001~V1-007）
├── traces/                    # Trace 输出（运行后生成）
├── output/                    # Markdown 报告输出（运行后生成）
├── conftest.py                # pytest 路径配置
└── requirements.txt
```

### 组件与需求映射

| 需求组件 | 代码位置 |
|----------|----------|
| LLM 规划推理 | `agent/planner.py`（LLMPlanner） |
| 工具调用 | `tools/` + `agent/loop.py::_exec_tool` |
| 短期记忆 State | `agent/state.py` |
| 循环控制器 + 终止条件 | `agent/loop.py` + `agent/termination.py` + `config.agent.max_steps` |
| 异常兜底 | `tools/base.py::ToolResult` + `loop.py::_apply_result` + `renderer.render_failure_report` |
| 独立统计（F3） | `tools/statistics.py` |
| 发布准入规则（F8） | `rules/release_engine.py` |
| Trace 可观测 | `trace/tracer.py` |

---

## 快速开始

```powershell
# 1. 安装依赖（Python 3.10+）
pip install -r requirements.txt

# 2. 离线跑通 V1-001（默认 mock 数据源，无需 API Key）
python -m report_agent.main "帮我生成 UniResearch V5.0.0 的测试报告"
#   → output/UniResearch_V5.0.0.md 与 traces/UniResearch_V5.0.0.json

# 3. 异常场景（故障注入）
python -m report_agent.main "UniResearch V9.9.9" --fault not_found   # V1-002 版本不存在
python -m report_agent.main "UniResearch V5.0.0" --fault timeout     # V1-003 超时
python -m report_agent.main "UniResearch V5.0.0" --fault empty_bugs  # V1-004 缺陷为空
python -m report_agent.main "UniResearch V5.0.0" --max-steps 2       # V1-005 步数超限
python -m report_agent.main "帮我生成测试报告"                        # V1-007 缺版本名→澄清

# 4. 运行测试
pytest -q
```

## 接入真实禅道 / 真实 LLM

### 一、接真实禅道（RESTful API，账号密码换 token）

1. 复制 `config/.env.example` 为 `config/.env`，填入**只读**账号：
   ```
   ZENTAO_ACCOUNT=你的只读账号
   ZENTAO_PASSWORD=你的密码
   ```
   （或改用预置的长期只读 `ZENTAO_TOKEN`）
2. 在 `config/settings.yaml` 里把 `zentao.base_url` 改成你们的禅道地址，`zentao.mode` 改为 `live`。
3. **先跑自检**，确认禅道支持 `/api.php/v1` 并打印真实字段名：
   ```powershell
   python -m report_agent.main --probe "UniResearch V5.0.0"
   ```
   - 若报 `404 / 非 JSON`：说明该禅道未开启 RESTful API，需改用传统 `m/f` 接口或数据库只读直连（另做适配）。
   - 若成功：对照打印出的 `product 字段名` 与 `bug 字段名`，核对 `settings.yaml` 的 `field_module / field_severity / field_status / field_resolution / field_activated` 是否一致，不一致就改配置（**无需改代码**）。
4. 正式生成：
   ```powershell
   python -m report_agent.main "帮我生成 UniResearch V5.0.0 的测试报告" --mode live
   ```

> **实测确认的取数模型（已接入 192.168.2.208 禅道验证）**：
> - **版本 ≠ 产品**：禅道把「版本」建模为缺陷的 `openedBuild`（影响版本，如 `V5.0.0`），产品为 `UniResearch`（product）。`parse_version` 把「UniResearch V5.0.0」拆成产品关键字 `UniResearch` + 版本号 `V5.0.0`，先按产品名匹配 product，再按其缺陷的 `openedBuild` 过滤。
> - **跨版本带入（§10 已拍板：包含即计入）**：`openedBuild` 可为逗号多版本串（如 `V5.0.0,V5.1.0`），`build_matches` 按 token 精确（忽略大小写）判定是否含本版本，避免 `V5.0` 误配 `V5.0.0`。
> - **模块名（§10 已拍板：逐条补拉）**：列表接口 `/products/{id}/bugs` 的 `module` 为模块 ID；模块名从单条 `GET /bugs/{id}` 的 `moduleTitle` 补拉（`ZentaoRestClient.get_bug`，失败回退 ID）。
> - **数据完整性（关键）——必须 `status=all`**：禅道 `/products/{id}/bugs` 默认视图只返回未关闭缺陷（active/resolved），closed 全部缺失，会导致缺陷总数/修复覆盖率严重偏低（实测 V5.1.0 默认 50 vs `status=all` 193）。`get_product_bugs` 已固定传 `status=all` 并以 `response.total` 取全分页。注意：`status=closed` 单独传返回 0、`browseType=all` 仍返回 240，只有 `status=all` 能拿到含 closed 的全量。
> - **禅道状态映射（§10 已确认）**：`severity` 1–4、`status` active/resolved/closed、`resolution` fixed/postponed/willnotfix/bydesign… 均与配置默认一致，由 `statistics.status_bucket` 归类（已关闭优先，延期只认 postponed）。
> - **测试起止时间（§10：维持占位）**：禅道 REST 无干净的产品版本日期字段，`zentao.test_start`/`test_end` 手填；留空则报告标注「待补充」，仅作软性缺口不阻断发布结论。

### 二、接真实 LLM 规划

1. 在 `config/.env` 填 `OPENAI_API_KEY`（OpenAI 兼容端点在 `settings.yaml` 的 `llm.base_url` 配置）。
2. `llm.use_llm_planner: true` 且存在 API Key 时，规划器自动切换为 LLM 模式；否则回退确定性规划，保证链路可离线复现。

## 发布准入标准（F8）

分子 = 激活(active/待解决)缺陷数，分母 = 缺陷总数，占比两位小数：

- P1 致命：激活数 = 0
- P2 严重：激活占比 ≤ 5%
- P3 一般：激活占比 ≤ 30%
- P4 建议：不限制

四项全满足 → 可发布；任一不满足 → 暂缓/阻断，并在报告结论中列出命中依据。
