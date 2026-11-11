# 测试报告生成 Agent V1.0

输入版本名称 → Agent 自行规划取数顺序 → 获取禅道版本信息与缺陷明细 → 调用独立统计工具计算指标 → 输出精简版 Markdown 测试报告。

本项目以**学习单 Agent 开发全流程**为首要目标：LLM 规划、工具调用、短期记忆 State、循环控制器、终止条件、异常兜底各自独立成模块，Trace 逐轮可见。

需求文档见 `docs/测试报告生成Agent V1.0 需求文档.md`（配套样例 `docs/UniResearch V5.0.0 测试报告.docx`）。

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

1. 复制 `config/.env.example` 为 `config/.env`，填入**只读**账号与禅道地址（地址放 `.env`，不要写进 `settings.yaml`）：
   ```
   ZENTAO_BASE_URL=http://你的内网禅道地址/zentao
   ZENTAO_ACCOUNT=你的只读账号
   ZENTAO_PASSWORD=你的密码
   ```
   （或改用预置的长期只读 `ZENTAO_TOKEN`）
2. 在 `config/settings.yaml` 里把 `zentao.mode` 改为 `live`（`base_url` 由 `.env` 的 `ZENTAO_BASE_URL` 覆盖，无需改 yaml）。
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

> **实测确认的取数模型（已在内网禅道实例验证）**：
> - **版本 ≠ 产品**：禅道把「版本」建模为缺陷的 `openedBuild`（影响版本，如 `V5.0.0`），产品为 `UniResearch`（product）。`parse_version` 把「UniResearch V5.0.0」拆成产品关键字 `UniResearch` + 版本号 `V5.0.0`，先按产品名匹配 product，再按其缺陷的 `openedBuild` 过滤。
> - **跨版本带入（§10 已拍板：包含即计入）**：`openedBuild` 可为逗号多版本串（如 `V5.0.0,V5.1.0`），`build_matches` 按 token 精确（忽略大小写）判定是否含本版本，避免 `V5.0` 误配 `V5.0.0`。
> - **版本存在性判定（V1-002）**：该禅道 REST 无独立 `builds` 接口（`/products/{id}/builds` 返回 404，产品详情的 `builds` 仅为计数），故以「产品全部缺陷的 `openedBuild` 取值集合」作为版本存在性信号——指定了版本号但集合中不含它即判定「版本不存在」，产出失败说明版报告并终止（不编造、不重试）。局限：live 下「版本存在但零缺陷」无法与「不存在」区分，按不存在处理；零缺陷场景由 mock `empty_bugs` 覆盖。
> - **模块名（§10 已拍板：逐条补拉）**：列表接口 `/products/{id}/bugs` 的 `module` 为模块 ID；模块名从单条 `GET /bugs/{id}` 的 `moduleTitle` 补拉（`ZentaoRestClient.get_bug`，失败回退 ID）。
> - **数据完整性（关键）——必须 `status=all`**：禅道 `/products/{id}/bugs` 默认视图只返回未关闭缺陷（active/resolved），closed 全部缺失，会导致缺陷总数/修复覆盖率严重偏低（实测：默认视图相对 `status=all` 会漏掉大量已关闭缺陷）。`get_product_bugs` 已固定传 `status=all` 并以 `response.total` 取全分页。注意：`status=closed` 单独传返回 0、`browseType=all` 仍会漏计 closed，只有 `status=all` 能拿到含 closed 的全量。
> - **禅道状态映射（§10 已确认）**：`severity` 1–4、`status` active/resolved/closed、`resolution` fixed/postponed/willnotfix/bydesign… 均与配置默认一致，由 `statistics.status_bucket` 归类（已关闭优先，延期只认 postponed）。
> - **测试起止时间（已实算）**：取「影响版本恰好等于本版本」的缺陷 `openedDate`（ISO8601 UTC）最早/最晚，转北京时间 `YYYY-MM-DD`（`statistics.opened_date_range` + `_build_exact`，排除多值跨版本带入）；仅无可用日期才回退 `zentao.test_start`/`test_end` 手填占位。

### 一·b、多项目支持（产品自动识别 + 飞书按项目落点）

支持在同一套配置下为多个禅道产品生成报告并分别推送到各自知识库节点，**无需为每个项目改代码**：

- **产品识别不靠正则猜**：`get_version_info` 运行时拉一次禅道 `/products`，把每个**真实产品名**归一化（小写、去空格/引号）后，看你输入的那句话里是否包含它，命中多个取**最长**（最具体）者。禅道新增/改名产品，下次运行自动识别。产品清单每次运行现查、**不写死**；同一次运行内只拉一次 `/products`（客户端 `_products_cache` 缓存），`get_version_bugs` 直接复用上游解析结果不再重复取数。
- **版本号兼容中文前缀**：`extract_build` 取「含点号数字的非空格 token 连同紧邻前缀」，故 `V5.1.0`、`理工1.0.0`、`6.6.7.2` 都能正确抽出，`parse_version` 也不会再把中文前缀泄漏进产品关键字。
- **兜底默认产品**：整句里识别不到任何已有产品时回退 `zentao.default_product`（默认 `UniResearch`），可用 `.env` 的 `ZENTAO_DEFAULT_PRODUCT` 覆盖。
- **飞书按项目落点**：在容器节点 `feishu.wiki_projects_parent`（如「项目测试」）下，用产品名匹配**同名子节点**（如 `Uniresearch项目测试` / `一表通项目测试`）作为落点；匹配不到回退 `feishu.wiki_parent_node` 默认落点；两者皆无则记 `feishu_error` 但**不推翻已生成的报告**。
- **按版本号建子目录**：确定项目节点后，在其下按影响版本号（`build`，如 `V5.0.0` / `理工1.0.0`）找**同名子节点**：命中则复用，缺失则自动新建一个空白页作为版本目录，报告落到该目录之下（层级：项目节点 / 版本节点 / 报告）。无版本号或新建失败（如权限不足）时回退为直接落项目节点，不推翻报告。新建依赖 `wiki:node:create` 或 `wiki:wiki` 权限且对父节点有编辑权（应用为知识库管理员即满足）。

相关配置（`.env`，均 `.gitignore`）：

```powershell
ZENTAO_DEFAULT_PRODUCT=UniResearch            # 可选，识别不到产品时兜底
FEISHU_WIKI_PROJECTS_PARENT=<项目容器 node_token>   # 其下每个项目一个同名子节点
FEISHU_WIKI_PARENT_NODE=<兜底默认落点 node_token>
```

用法示例：

```powershell
python -m report_agent.main "帮我生成 一表通项目 理工1.0.0 的测试报告" --mode live --push-feishu
```

> 前提：知识库「项目容器」下每个项目的子节点标题包含对应产品名（当前 Uniresearch / 一表通 均如此）。若某新产品在禅道的产品名与知识库子节点标题差异过大导致匹配失败，会回退默认落点并如实记录，后续再考虑别名映射（本期不做手工别名表）。

### 一·c、测试人员（3.1）自动取数

报告 3.1 的岗位人员不再写死占位，而是从**当前版本**的缺陷数据如实聚合（严格按影响版本「恰好等于本版本」，排除跨版本带入）：

- **测试负责人 / 测试人员**：本版本缺陷的提交人 `openedBy` 去重。
- **软件工程师（开发、运维）**：本版本缺陷的解决人 `resolvedBy` 去重。
- **项目负责人**：从产品对象取，但需先在配置里指定字段名（`zentao.field_product_owner`，如 `PO`/`PM`）；**未配置时一律留【待补充】，不默认猜字段以免张冠李戴**。

禅道 REST 把 `openedBy/resolvedBy`（及产品负责人字段）返回为**用户对象**（含 `realname`/`account`/`id`），系统统一取 `realname` 作为显示名（无则退 `account`）。字段名可配置（`settings.yaml` 的 `zentao.field_opened_by` / `field_resolved_by` / `field_product_owner`）。跑 `python -m report_agent.main --probe` 会打印产品对象及其**疑似负责人字段**（值为用户对象或键名含 owner/PO/PM/RD 的项），据此把正确键名填进 `field_product_owner`（或 `.env` 的 `ZENTAO_FIELD_PRODUCT_OWNER`）。取不到任何人员时对应格标【待补充】。

### 一·d、缺陷清单（第 6 节）内嵌为飞书多维表格

报告末尾第 6 节把两类重点缺陷**直接内嵌展开在正文**（飞书文档内的多维表格块，形如原生报告里那张可筛选/排序的表；宽表在竖版 docx 里会被挤成一竖条、长文本无法完整显示，故改为多维表格）。默认形态为**文档内嵌块（C）**；由 `feishu.defect_bitable_embed` 控制：`true`=内嵌展开（默认），`false`=回退为正文内嵌宽表（**不再创建任何独立多维表格节点**）：

- **当前版本遗留未解决bug**（第一张表）：`status=active`（激活待解决）；
- **当前版本延期处理bug**（第二张表）：`resolution=postponed 且 status≠closed`（延期处理且未关闭）。

两表都取自与缺陷总数/状态分布同一批 scoped 缺陷（包含即计入），条数与 summary「待解决/延期」对齐。多维表格建在**报告同一个知识库版本目录**下（每版本一个快照），13 个字段：序号、Bug编号、所属模块、bug标题、优先级、功能类型、重现步骤、bug状态、由谁创建、创建日期、影响版本、指派给、解决方案。其中 `功能类型/bug状态/解决方案` 为**单选**字段（可点选筛选），**`重现步骤` 为多行文本、全文不截断**（这正是解决“文字多又不能裁”的关键）。

实现要点：

- **内嵌形态（C，默认）**：正文（`render_report`）只写计数与引导（不放链接、不放宽表）；真正的多维表格在 **`push_feishu` 导入并入库之后**逐组插入——**每组各插一个内嵌块**（先插一个三级标题块 `block_type=5` 作为表标题，再插一个多维表格块 `block_type=18`），拆出其 `app_token/table_id`，清理该默认表自带的 3 个垃圾列与 3 条空行（主列改名为「序号」、多余列删除、空行批量删除），补齐其余 12 列并灌记录。因单个内嵌块只渲染其绑定的那张表，故遗留未解决、延期处理各自独占一块（而非单块内多表，否则第二张表不显示）。顺序：①「6.1 当前版本遗留未解决bug」（激活）②「6.2 当前版本延期处理bug」（延期）。**全程非致命**——内嵌失败（网络/权限/未发布）时正文保留计数引导不阻断报告。
- **宽表回退（`defect_bitable_embed=false` 或无飞书/无缺陷）**：第 6 节直接放宽 Markdown 表（6.1/6.2）。**曾经的独立多维表格节点 + 正文链接形态（B）已从链路彻底移除**（`_export_defect_bitable`/`build_defect_bitable`/`create_bitable_node`/`resolve_report_parent` 均已删除），任何配置都不会再在知识库创建「缺陷清单」独立节点。

开关：`settings.yaml` 的 `feishu.defect_bitable`（默认 true，仅 `feishu.enabled` 时生效）与 `feishu.defect_bitable_embed`（默认 true=C 内嵌；false=正文宽表回退）。

> **live 前置**：内嵌形态（C）需 **`docx:document`**（向文档插入多维表格块）与 **`bitable:app`**（伞项，覆盖建/改/删字段、建/删/写记录；因需清理默认列与空行，细粒度 `base:field:create` 不够）。均须**发布版本**才生效。因多维表格由应用自己创建，应用即其所有者，不会再遇到“高级权限/添加文档应用”那道坎。飞书无“重命名数据表”的独立建表接口，默认表名用 `PATCH .../tables/{tbl}` 改。
>
> 飞书 `block_type=18` 内嵌多维表格块**只能建空表**（`view_type` 指定），不能引用已有多维表格；所以 C 是“先插空块、再用多维表格 API 灌数据”，而非把 B 的独立表内联。

> **live 实测（UniResearch V5.1.0、C 内嵌）**：推送后文档末尾含 2 个内嵌块（各带一个三级标题块），块1=「6.1 当前版本遗留未解决bug」15 条、块2=「6.2 当前版本延期处理bug」3 条，均恰好 13 列、主列已改名「序号」、默认垃圾列/空行已清零。优先级在禅道键名为 **`pri`**（非 `priority`，已作为默认）；由谁创建/指派给取 realname、影响版本为 `openedBuild` 原值。字段名可配置（`settings.yaml` 的 `field_title`/`field_priority`/`field_type`/`field_steps`/`field_assigned_to`）；列表接口不返回的字段从单条详情 `get_bug` 补拉。

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

---

## V1.0 交付物与收口状态（需求文档 §11）

| # | 交付物 | 位置 / 状态 |
|---|--------|-------------|
| 1 | 需求文档 | `docs/测试报告生成Agent V1.0 需求文档.md` ✅ |
| 2 | Agent 系统提示词 | `docs/Agent系统提示词说明.md` ✅ |
| 3 | 工具定义说明 | `docs/工具定义说明.md` ✅ |
| 4 | 测试用例集（V1-001~007 / U-001~008） | `tests/` ✅（`pytest 35 passed`） |
| 5 | 样例输出报告（V1-001 产物） | `docs/验收证据/V1-001_测试报告样例.md` ✅（**mock 脱敏样例**，不含真实业务数据） |
| 6 | 成功运行 Trace 样例 | `docs/验收证据/V1-001_Trace.json` ✅（完整链路；mock 脱敏） |
| 7 | 对照实验反例（V1-006） | `experiments/v1_006_comparison.py` ⏸ 脚手架已就绪，**反例产出需 `OPENAI_API_KEY`** |

> **待补 Key 方可闭环的两项**（本机未检测到 `OPENAI_API_KEY`）：真实 LLM 规划的 V1-001 跑批、V1-006「模型自行汇总 vs 独立统计」反例。二者均依赖真实 LLM 输出，依防幻觉原则**拒绝伪造**，故暂未产出。配置 Key 后：`python -m experiments.v1_006_comparison --runs 3`。
>
> **数据隐私**：验收证据 #5/#6 采用 mock 脱敏样例（结构等价、无真实缺陷数据）；公司真实禅道数据只存于本地 `output/`、`traces/`（均 `.gitignore`），不入版本控制、不推 GitHub。
