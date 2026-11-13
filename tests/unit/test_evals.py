# -*- coding: utf-8 -*-
"""V2-8 回归评测集门禁（接入 pytest，杜绝「忘记跑评测」）。

需求文档 §4/B8：V2 任何功能合入前必须通过金标准全绿。将评测集作为一条 pytest
用例接入全量回归，使「忘了单独跑 evals」不再可能绕过门禁。全离线 mock、无副作用。
"""
from evals.run_eval import run_all


def test_evals_golden_all_pass():
    results = run_all()
    failed = [r for r in results if not r["passed"]]
    detail = "\n".join(f"{r['id']}:\n" + "\n".join(r["diffs"]) for r in failed)
    assert not failed, f"金标准评测集 {len(failed)}/{len(results)} 未通过：\n{detail}"
