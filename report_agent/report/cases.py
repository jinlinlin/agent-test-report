"""V2-4 测试范围接地：从「测试用例表」解析，聚合为 2.1 测试范围。

背景：飞书思维笔记无内容读取 API，测试范围只能取用例表。用例表有两种载体，本模块两种都支持：
- `obj_type=file`（上传的 .xlsx）→ drive 下载字节 → openpyxl 出行 → `extract_scope_from_cases`；
- `obj_type=sheet`（飞书原生电子表格）→ sheets 取值接口出行矩阵 → `extract_scope_from_rows`。
两条入口共用同一套解析主体：按**表头名**定位列（与列顺序无关），用例表通常含「模块分类」列。

2.1 语义（用户口径，参考既有报告风格）：
- **模块 = 菜单目录**：取「模块分类」的一级段（如 `AI对话组件`）；
- **描述 = 这个菜单里测了什么**：只取层级路径的**二级功能点**（如 `模型选择、对话模式切换`），
  去重保序、以「、」连接；「模块分类」无二级时退用「用例名称」的二级段（如名称 `研究探索-页面重构-…`
  → 取 `页面重构`），避免把整条多级路径堆进描述。

红线：程序定事实——只读列、按模块聚合，绝不臆造模块/功能名；解析不出模块列/无数据行一律返回 None
（交上层回退占位）。

若用例表把「模块分类」填成扁平名（无层级），一级聚合会产出几十个菜单；此时可由**人工确认的分组映射**
（`scope.module_groups`，只存组名与成员模块名）把细模块归并到一级菜单，见 `apply_module_groups`：
归并只改名与相加，成员名逐字引用，不生成任何新名称。
"""
from __future__ import annotations

from io import BytesIO
from typing import Dict, List, Optional

# 模块路径分级分隔符（一级模块取首段）；不同团队可能用 - / ／ — 等。
_SEPARATORS = ("-", "－", "—", "/", "／", "\\", ">", "»", "→")

# 执行结果分桶口径（用例表「执行结果」列枚举，程序定事实、只按字面归类，不臆测）：
# 判定顺序互斥、自上而下——先判未执行（空值），再判通过/失败/阻塞，其余非空一律归「其他」，
# 绝不把无法识别的值并进通过或失败（守「不编造」红线）。大小写不敏感（统一 lower 比对）。
_EXEC_NOTRUN = {"", "未执行", "not run", "notrun", "n/a", "na", "-", "—"}
_EXEC_PASSED = {"通过", "成功", "测试通过", "正常", "pass", "passed"}
_EXEC_FAILED = {"失败", "不通过", "未通过", "fail", "failed"}
_EXEC_BLOCKED = {"阻塞", "blocked"}
_EXEC_KEYS = ("passed", "failed", "blocked", "not_run", "other")


def _exec_bucket(value) -> str:
    """执行结果原值 → 分桶键（passed/failed/blocked/not_run/other）；未识别的非空值归 other。"""
    s = str(value or "").strip().lower()
    if s in _EXEC_NOTRUN:
        return "not_run"
    if s in _EXEC_PASSED:
        return "passed"
    if s in _EXEC_FAILED:
        return "failed"
    if s in _EXEC_BLOCKED:
        return "blocked"
    return "other"


def _pct(numerator: int, denominator: int) -> str:
    """百分比字符串（两位小数，与 statistics._pct 同口径）；分母 ≤0 返回 0.00%。

    本模块自带而非从 tools.statistics 导入，避免 report 层反向依赖 tools 层造成循环。"""
    if denominator <= 0:
        return "0.00%"
    return f"{round(numerator / denominator * 100, 2):.2f}%"


def _first_sep_index(s: str) -> int:
    idx = -1
    for sep in _SEPARATORS:
        i = s.find(sep)
        if i != -1 and (idx == -1 or i < idx):
            idx = i
    return idx


def _top_segment(path: str) -> str:
    """取模块路径的一级段（首个分隔符之前），去空白。无分隔符则原样返回。"""
    s = str(path or "").strip()
    i = _first_sep_index(s)
    return (s[:i] if i != -1 else s).strip()


def _strip_leading_sep(rest: str) -> str:
    while rest and (rest[0] in _SEPARATORS or rest[0].isspace()):
        rest = rest[1:]
    return rest


def _second_segment(path: str) -> str:
    """取层级路径的二级功能点（首个分隔符与下一个分隔符之间），去空白/前导分隔符。无二级则空串。"""
    s = str(path or "").strip()
    i = _first_sep_index(s)
    if i == -1:
        return ""
    rest = _strip_leading_sep(s[i + 1:])
    j = _first_sep_index(rest)
    return (rest[:j] if j != -1 else rest).strip()


def extract_scope_from_rows(
    rows: List[object],
    module_col: str = "模块分类",
    name_col: str = "用例名称",
    group_level: str = "top",
    exec_col: str = "执行结果",
) -> Optional[List[Dict[str, str]]]:
    """解析行矩阵（含表头行），返回 [{"module","desc","case_count", 执行分桶计数...}]；不可信返回 None。

    xlsx（openpyxl 出 tuple 行）与原生电子表格（sheets 接口出 list 行）两条数据源共用本入口。
    module 为菜单目录（一级），desc 为该菜单测到的功能点（去重、保序、以「、」连接）。
    module_col/name_col：模块列/用名列表头（模糊包含匹配，兼容写法与列顺序差异）。
    group_level："top"=按一级菜单聚合（默认）；"full"=按完整模块路径逐条列出。
    exec_col：执行结果列表头（命中则按 _exec_bucket 逐模块聚合 passed/failed/blocked/not_run/other，
        并标 exec_available=True；未命中则各分桶计 0、exec_available=False，上层据此如实不输出通过率，不臆造）。
    """
    if not rows:
        return None

    def _find_col(header, needle: str) -> int:
        if not needle:
            return -1
        for ci, cell in enumerate(header):
            if needle in str(cell or "").strip():
                return ci
        return -1

    # 定位表头行（前 10 行内含 module_col 者）与模块列、用名列下标
    header_idx, mod_pos = -1, -1
    for ri, row in enumerate(rows[:10]):
        ci = _find_col(row, str(module_col or "").strip())
        if ci >= 0:
            header_idx, mod_pos = ri, ci
            break
    if mod_pos < 0:
        return None
    name_pos = _find_col(rows[header_idx], str(name_col or "").strip())
    exec_pos = _find_col(rows[header_idx], str(exec_col or "").strip())
    exec_available = exec_pos >= 0

    # 聚合：按菜单模块保序收集功能点（描述）与计数，并逐模块累计执行结果分桶
    order: List[str] = []
    details: Dict[str, List[str]] = {}
    counts: Dict[str, int] = {}
    exec_stats: Dict[str, Dict[str, int]] = {}

    def _record(module: str, detail: str, bucket: str = "") -> None:
        if module not in counts:
            counts[module] = 0
            details[module] = []
            exec_stats[module] = {k: 0 for k in _EXEC_KEYS}
            order.append(module)
        counts[module] += 1
        if detail and detail not in details[module]:
            details[module].append(detail)
        if bucket:
            exec_stats[module][bucket] += 1

    for row in rows[header_idx + 1:]:
        mod_val = str(row[mod_pos] or "").strip() if mod_pos < len(row) else ""
        if not mod_val:
            continue
        name_val = str(row[name_pos] or "").strip() if 0 <= name_pos < len(row) else ""
        if group_level == "full":
            module, detail = mod_val, name_val
        else:
            module = _top_segment(mod_val)
            # 描述只取二级功能点：优先「模块分类」二级，退而用「用例名称」二级，再退整条名称
            detail = _second_segment(mod_val) or _second_segment(name_val) or name_val
        if not module:
            continue
        bucket = ""
        if exec_available and exec_pos < len(row):
            bucket = _exec_bucket(row[exec_pos])
        _record(module, detail, bucket)

    if not order:
        return None

    return [
        {
            "module": m,
            "desc": "、".join(details[m]),
            "case_count": counts[m],
            "exec_available": exec_available,
            **exec_stats[m],
        }
        for m in order
    ]


def extract_scope_from_cases(
    content: bytes,
    module_col: str = "模块分类",
    name_col: str = "用例名称",
    group_level: str = "top",
    exec_col: str = "执行结果",
) -> Optional[List[Dict[str, str]]]:
    """解析 xlsx 字节流（薄壳：openpyxl 读首个 sheet 的行 → 转调 extract_scope_from_rows）。

    非 xlsx / 损坏 / 缺 openpyxl 一律返回 None，交上层回退占位；口径与行矩阵入口完全一致。
    """
    if not content:
        return None
    try:
        import openpyxl
    except ImportError:  # pragma: no cover
        return None
    try:
        wb = openpyxl.load_workbook(BytesIO(content), data_only=True)
    except Exception:  # 非 xlsx / 损坏 → 交上层回退
        return None
    ws = wb.worksheets[0] if wb.worksheets else None
    if ws is None:
        return None
    return extract_scope_from_rows(list(ws.iter_rows(values_only=True)), module_col=module_col,
                                   name_col=name_col, group_level=group_level, exec_col=exec_col)


def _as_count(value) -> int:
    """用例数转整数：非数字（缺失/异常）一律按 0，绝不抛断归并。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def apply_module_groups(scope, groups) -> Optional[List[Dict[str, str]]]:
    """按人工确认的「一级菜单 → 成员模块」映射归并细粒度模块（只归并、不发明）。

    scope：`extract_scope_from_*` 的产出；groups：`[(组名, (成员模块名, ...)), ...]`。
    规则：模块名去空白后与成员**全等**才归并（不做模糊匹配，宁可不归并也不归错）；
    同一成员被多个组声明时只归首个组；一个模块也没命中的组整行不出现（不编造空组）；
    **未命中任何组的模块原样保留**并附在组行之后（绝不丢数据）。
    组行描述=已归并成员模块名逐字以「、」连接（按用例表出现顺序，不是映射声明顺序）；
    case_count=已归并模块的用例数合计（只供排序/对账，是否上表由渲染层定）。
    groups 为空/None 或 scope 为空时原样返回，行为与归并上线前逐字一致。
    """
    if not scope or not groups:
        return scope
    buckets = []
    for item in groups:
        try:
            name, members = item
        except (TypeError, ValueError):
            continue
        clean = {str(m).strip() for m in (members or ()) if str(m).strip()}
        if str(name or "").strip() and clean:
            buckets.append((str(name).strip(), clean))
    if not buckets:
        return scope

    out, used = [], set()
    for name, members in buckets:
        hits = [r for r in scope
                if str(r.get("module", "")).strip() in members
                and str(r.get("module", "")).strip() not in used]
        if not hits:
            continue
        used.update(str(r.get("module", "")).strip() for r in hits)
        out.append({
            "module": name,
            "desc": "、".join(str(r.get("module", "")).strip() for r in hits),
            "case_count": sum(_as_count(r.get("case_count")) for r in hits),
            # 执行分桶随归并相加（只相加不重算）；成员只要有一行带执行结果就归为可用
            "exec_available": any(bool(r.get("exec_available")) for r in hits),
            **{k: sum(_as_count(r.get(k)) for r in hits) for k in _EXEC_KEYS},
        })
    out.extend(r for r in scope if str(r.get("module", "")).strip() not in used)
    return out or scope


def summarize_case_exec(scope: Optional[List[Dict[str, object]]]) -> Optional[Dict[str, object]]:
    """把按模块的用例聚合行汇总为「用例执行概况」（供 3.3 与 2.1 用例数列使用）。

    只把已有分桶相加并算两个百分比，不发明任何新事实（渲染层不做计算、只搬运本函数产出的数值）：
    - 已执行 executed = passed+failed+blocked+other（未识别非空值计入已执行但归 other，不并进通过）；
    - 执行率 = 已执行 ÷ 用例总数；通过率 = 通过 ÷ 已执行（与缺陷修复覆盖率同为两位小数）；
    - exec_available=False（用表无「执行结果」列或全空）时仍返回总数与分模块用例数，但通过率置空由上层如实标注。
    scope 为空返回 None。
    """
    if not scope:
        return None
    total = 0
    agg = {k: 0 for k in _EXEC_KEYS}
    exec_available = False
    by_module: List[Dict[str, object]] = []
    for r in scope:
        cc = _as_count(r.get("case_count"))
        total += cc
        row_exec = bool(r.get("exec_available"))
        exec_available = exec_available or row_exec
        buckets = {k: _as_count(r.get(k)) for k in _EXEC_KEYS}
        for k in _EXEC_KEYS:
            agg[k] += buckets[k]
        executed = buckets["passed"] + buckets["failed"] + buckets["blocked"] + buckets["other"]
        by_module.append({
            "module": str(r.get("module", "")).strip(),
            "case_count": cc,
            "executed": executed,
            **buckets,
            "pass_rate": _pct(buckets["passed"], executed) if (row_exec and executed) else "",
        })
    executed = agg["passed"] + agg["failed"] + agg["blocked"] + agg["other"]
    return {
        "exec_available": exec_available,
        "total": total,
        "executed": executed,
        "passed": agg["passed"],
        "failed": agg["failed"],
        "blocked": agg["blocked"],
        "not_run": agg["not_run"],
        "other": agg["other"],
        "exec_rate": _pct(executed, total),
        "pass_rate": _pct(agg["passed"], executed) if exec_available else "",
        "by_module": by_module,
    }

