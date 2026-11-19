"""飞书应用机器人外壳单测（全离线：runner 与 client 均注入假件，不打任何网络）。

覆盖三条硬约束：白名单默认全拒、事件入口不阻塞（重活进线程池）、失败如实回原因不伪造数字。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from report_agent.bot.app import BotService, extract_text, format_result, serve
from report_agent.config import load_config

CHAT = "oc_testgroup"


class FakeClient:
    """记录发送调用的假飞书客户端（reply 优先于 send，与 _say 的实现口径一致）。"""

    def __init__(self, fail=False):
        self.replies = []
        self.sends = []
        self.fail = fail

    def reply_message(self, message_id, msg_type, content):
        if self.fail:
            raise RuntimeError("im:message 权限未发布")
        self.replies.append((message_id, msg_type, content.get("text", "")))
        return "om_reply"

    def send_message(self, receive_id, msg_type, content, receive_id_type="chat_id"):
        if self.fail:
            raise RuntimeError("im:message 权限未发布")
        self.sends.append((receive_id, msg_type, content.get("text", "")))
        return "om_send"


def make_event(text="帮我生成 UniResearch V5.1.0 的测试报告", *, msg_type="text",
               chat_id=CHAT, message_id="om_origin", open_id="ou_user"):
    msg = SimpleNamespace(chat_id=chat_id, message_id=message_id, msg_type=msg_type,
                          content=json.dumps({"text": text}) if msg_type == "text" else "{}")
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id=open_id))
    return SimpleNamespace(event=SimpleNamespace(message=msg, sender=sender))


def make_service(*, allowed_chats=(CHAT,), allowed_users=(), runner=None, client=None):
    cfg = load_config(**{"bot.allowed_chats": tuple(allowed_chats),
                         "bot.allowed_users": tuple(allowed_users),
                         "bot.push": True})
    calls = []

    def _default_runner(user_input, **kwargs):
        calls.append((user_input, kwargs))
        return {"status": "finished", "display_name": "UniResearch V5.1.0",
                "version_name": "UniResearch V5.1.0", "report_path": "output/x.md",
                "trace_path": "traces/x.json", "report_markdown": "# 报告",
                "feishu_url": "https://feishu/wiki/abc", "feishu_error": "",
                "fail_reason": "", "summary": {"total": 194, "closed": 143}}

    svc = BotService(cfg, runner=runner or _default_runner, client=client or FakeClient())
    svc.calls = calls
    return svc


# ---------- extract_text ----------
def test_extract_text_plain():
    assert extract_text("text", json.dumps({"text": " 出 V5.1.0 的报告 "})) == "出 V5.1.0 的报告"


def test_extract_text_keeps_at_placeholder_for_upstream():
    # 群 @ 带来的 @_user_1 占位符原样交上层：版本名解析本就不依赖它，不做静默清洗
    raw = json.dumps({"text": "@_user_1 UniResearch V5.1.0"})
    assert extract_text("text", raw) == "@_user_1 UniResearch V5.1.0"


def test_extract_text_non_text_type_returns_empty():
    assert extract_text("post", json.dumps({"text": "x"})) == ""
    assert extract_text("image", "{}") == ""


def test_extract_text_bad_payload_returns_empty():
    assert extract_text("text", "不是 JSON") == ""
    assert extract_text("text", "") == ""
    assert extract_text("text", json.dumps(["a"])) == ""      # 合法 JSON 但不是 dict
    assert extract_text("text", json.dumps({"content": "x"})) == ""


# ---------- format_result ----------
def test_format_result_finished_with_url_shows_numbers_and_advice():
    res = {"status": "finished", "display_name": "UniResearch V5.1.0",
           "feishu_url": "https://feishu/wiki/abc", "report_path": None,
           "summary": {"total": 194, "closed": 143, "pending_resolve": 15,
                       "pending_accept": 33, "postponed": 3, "fix_coverage": "73.71%",
                       "activated_rate": "15.46%", "release_advice": "可按计划发布。"}}
    out = format_result(res)
    assert "✅" in out and "https://feishu/wiki/abc" in out
    assert "194" in out and "143" in out and "73.71%" in out and "可按计划发布。" in out


def test_format_result_finished_without_url_falls_back_to_local_path():
    res = {"status": "finished", "display_name": "V1", "feishu_url": None,
           "feishu_error": "缺少 FEISHU_APP_ID", "report_path": "output/V1_测试报告.md",
           "summary": {}}
    out = format_result(res)
    assert "output/V1_测试报告.md" in out and "缺少 FEISHU_APP_ID" in out
    assert "缺陷总数" not in out          # 无 summary 就不出数字行，绝不补造


def test_format_result_clarify_asks_for_full_version():
    out = format_result({"status": "clarify", "version_name": None})
    assert "没能确定版本" in out and "UniResearch V5.1.0" in out


def test_format_result_failed_reports_reason_and_explains_report():
    res = {"status": "failed", "display_name": "V9.9.9", "fail_reason": "版本不存在",
           "report_markdown": "# 失败说明", "report_path": "output/V9.9.9_测试报告.md"}
    out = format_result(res)
    assert "❌" in out and "版本不存在" in out and "失败说明版报告" in out


def test_format_result_step_limit():
    assert "步数超限" in format_result({"status": "step_limit", "display_name": "V1"})


# ---------- 白名单 ----------
def test_allow_denies_everything_when_both_lists_empty():
    svc = make_service(allowed_chats=(), allowed_users=())
    assert svc.allow(CHAT, "ou_user") is False


def test_allow_chat_hit_or_user_hit():
    svc = make_service(allowed_chats=(CHAT,), allowed_users=())
    assert svc.allow(CHAT, "ou_other") is True
    assert svc.allow("oc_other", "ou_user") is False
    svc2 = make_service(allowed_chats=(), allowed_users=("ou_user",))
    assert svc2.allow("oc_other", "ou_user") is True


# ---------- 事件路由 ----------
def test_on_message_rejected_calls_nothing():
    svc = make_service(allowed_chats=("oc_other",))
    svc.on_message(make_event())
    svc.shutdown()
    assert svc.calls == []
    assert svc.client.replies == [] and svc.client.sends == []


def test_on_message_happy_path_receipt_then_result():
    svc = make_service()
    svc.on_message(make_event(text="UniResearch V5.1.0 出报告"))
    svc.shutdown()                                   # 等线程池排空，断言才确定
    assert svc.calls and svc.calls[0][0] == "UniResearch V5.1.0 出报告"
    assert svc.calls[0][1]["push"] is True           # 机器人默认推飞书
    assert len(svc.client.replies) == 2
    assert "正在生成" in svc.client.replies[0][2]
    assert "✅" in svc.client.replies[1][2] and "https://feishu/wiki/abc" in svc.client.replies[1][2]


def test_on_message_non_text_replies_hint_and_skips_runner():
    svc = make_service()
    svc.on_message(make_event(msg_type="image"))
    svc.shutdown()
    assert svc.calls == []
    assert len(svc.client.replies) == 1 and "暂只支持文本" in svc.client.replies[0][2]


def test_on_message_runner_exception_still_replies():
    def boom(user_input, **kwargs):
        raise RuntimeError("禅道连不上")

    svc = make_service(runner=boom)
    svc.on_message(make_event())
    svc.shutdown()
    texts = [t for _, _, t in svc.client.replies]
    assert any("生成失败" in t and "禅道连不上" in t for t in texts)


def test_on_message_send_failure_does_not_raise():
    # 回消息失败（如权限未发版）只记日志，不能把异常抛回飞书事件循环
    svc = make_service(client=FakeClient(fail=True))
    svc.on_message(make_event())
    svc.shutdown()


def test_on_message_bad_event_shape_is_ignored():
    svc = make_service()
    svc.on_message(SimpleNamespace())                # 缺 event/message
    svc.shutdown()
    assert svc.calls == []


# ---------- serve 前置校验 ----------
def test_serve_refuses_without_credentials(capsys):
    cfg = load_config(**{"feishu.app_id": "", "feishu.app_secret": "",
                         "bot.allowed_chats": (CHAT,)})
    assert serve(cfg) == 2
    assert "FEISHU_APP_ID" in capsys.readouterr().out


def test_serve_refuses_with_empty_whitelist(capsys):
    cfg = load_config(**{"feishu.app_id": "cli_x", "feishu.app_secret": "secret",
                         "bot.allowed_chats": (), "bot.allowed_users": ()})
    assert serve(cfg) == 2
    assert "白名单为空" in capsys.readouterr().out
