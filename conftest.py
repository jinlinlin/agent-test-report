"""pytest 全局配置：把仓库根目录加入 sys.path，并给飞书传输层装上"测试基线硬闸"。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_live_feishu_transport(monkeypatch):
    """禁止任何测试打真实飞书接口，把"忘记关开关"从静默联网变成即时降级。

    背景：config/settings.yaml 已把 scope/doc_index/test_env 三项接地默认开启（2026-11-19 拍板），
    而它们只要遇到 `feishu.enabled=True` 的配置就会去读知识库。实测漏关时整套测试从 16s 涨到 63s
    且结果依赖网络与租户余额——属于"测试静默联网"的经典复发形态，故在此集中设闸，而不是指望
    每个测试文件各自维护一份 override 清单。

    被拦的方法一律抛 FeishuError，正好命中各读取封装既有的降级分支（返回空/None → 报告占位），
    因此不改变任何被测语义。测试若自己 monkeypatch 了这些方法（或整个 FeishuClient/上层工具），
    测试体内的 patch 晚于本 fixture 写入，照常生效。
    """
    from report_agent.tools.feishu_client import FeishuClient, FeishuError

    def _blocked(self, *args, **kwargs):
        raise FeishuError("测试基线禁止真实飞书请求（请在测试内 stub 所需方法）", "error")

    for name in ("_get", "_post", "_send", "upload_bytes", "download_file"):
        monkeypatch.setattr(FeishuClient, name, _blocked, raising=False)
