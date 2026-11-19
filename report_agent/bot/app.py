"""飞书应用机器人外壳：长连接收消息 → 白名单校验 → 后台线程跑 generate_report → 回消息。

本包不触碰 Agent 内核，只是 `main.generate_report()` 的又一个调用方（与 CLI、定时任务同级）。
三条硬约束（均来自官方要求或本项目实测，改动前请先读懂）：

1) **事件处理必须 3 秒内返回**：live 跑一次报告是数十秒量级，所以 handler 只做
   「校验 + 回执 + 入队」，重活全部交线程池。把生成写进 handler 会被飞书判超时并重推事件，
   造成同一份报告被反复生成。
2) **白名单默认为空 = 全部拒绝**：机器人以应用身份取数与写文档，放行等于把内网禅道读权限
   和知识库写权限交给所有能发消息的人。
3) **失败一律如实回原因，不伪造数字**（与 push_feishu「不推翻已生成报告」同口径）。
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from ..config import Config, load_config
from ..main import generate_report
from ..tools.feishu_client import FeishuClient

log = logging.getLogger("report_agent.bot")

HELP_TEXT = "用法示例：帮我生成 UniResearch V5.1.0 的测试报告"


def extract_text(msg_type: str, content: str) -> str:
    """从事件消息体取纯文本。

    content 是 JSON 字符串（text 类型形如 `{"text":"..."}`，群聊里 @机器人 会带上
    `@_user_1` 这类占位符）。非 text（富文本/图片/卡片等）一律返回空串，由上层回「暂不支持」——
    不去猜富文本结构，避免把占位符当版本名喂进解析。
    """
    if msg_type != "text":
        return ""
    try:
        obj = json.loads(content) if content else {}
    except (TypeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    return str(obj.get("text", "")).strip()


def format_result(res: Dict[str, Any]) -> str:
    """把 generate_report 的返回渲染成一条群消息（纯函数，便于单测）。

    只报已有事实：summary 缺失（早退场景）就不出数字行，绝不补造。
    """
    status = res.get("status") or ""
    name = res.get("display_name") or res.get("version_name") or "报告"

    if status == "finished":
        lines = [f"✅ {name} 测试报告已生成"]
        url = res.get("feishu_url")
        if url:
            lines.append(f"📄 飞书知识库：{url}")
        else:
            lines.append(f"📄 本地报告：{res.get('report_path')}")
            if res.get("feishu_error"):
                lines.append(f"（飞书推送未成功：{res['feishu_error']}）")
        s = res.get("summary") or {}
        if s:
            lines.append(f"缺陷总数 {s.get('total')} ｜ 已关闭 {s.get('closed')} ｜ "
                         f"待解决 {s.get('pending_resolve')} ｜ 待验收 {s.get('pending_accept')} ｜ "
                         f"延期 {s.get('postponed')}")
            lines.append(f"修复覆盖率 {s.get('fix_coverage')} ｜ 激活率 {s.get('activated_rate')}")
            if s.get("release_advice"):
                lines.append(f"发布建议：{s['release_advice']}")
        return "\n".join(lines)

    if status == "clarify":
        return f"❓ 没能确定版本，请补全项目与版本号。{HELP_TEXT}"

    if status == "step_limit":
        return f"⚠️ {name} 执行步数超限，未能完成，请缩小范围或让维护人查看 Trace。"

    reason = res.get("fail_reason") or res.get("feishu_error") or "未知原因"
    lines = [f"❌ {name} 生成失败：{reason}"]
    if res.get("report_markdown"):
        lines.append(f"（失败说明版报告：{res.get('report_path')}）")
    return "\n".join(lines)


class BotService:
    """事件路由 + 任务派发。runner / client 均可注入，单测里不打任何网络。"""

    def __init__(self, cfg: Optional[Config] = None,
                 runner: Optional[Callable[..., Dict[str, Any]]] = None,
                 client: Any = None) -> None:
        self.cfg = cfg or load_config()
        self.runner = runner or generate_report
        self.client = client if client is not None else FeishuClient(self.cfg.feishu)
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(self.cfg.bot.max_workers or 1)))

    # ---- 白名单 ----
    def allow(self, chat_id: str, user_id: str) -> bool:
        """群或用户任一命中即放行；两份白名单都空则全部拒绝（不默认放行）。"""
        b = self.cfg.bot
        if not (b.allowed_chats or b.allowed_users):
            return False
        if chat_id and chat_id in tuple(b.allowed_chats):
            return True
        return bool(user_id) and user_id in tuple(b.allowed_users)

    # ---- 事件入口（必须 3 秒内返回）----
    def on_message(self, data: Any) -> None:
        try:
            msg = data.event.message
            sender = data.event.sender
        except AttributeError as exc:
            log.warning("事件结构不符合预期，忽略：%s", exc)
            return
        chat_id = str(getattr(msg, "chat_id", "") or "")
        message_id = str(getattr(msg, "message_id", "") or "")
        user_id = str(getattr(getattr(sender, "sender_id", None), "open_id", "") or "")

        if not self.allow(chat_id, user_id):
            log.info("拒绝白名单外请求 chat=%s user=%s", chat_id, user_id)
            return                      # 静默：不回、不跑，也不暴露机器人存在

        text = extract_text(str(getattr(msg, "msg_type", "text") or "text"),
                            str(getattr(msg, "content", "") or ""))
        if not text:
            self._say(message_id, chat_id, f"暂只支持文本消息。{HELP_TEXT}")
            return

        self._say(message_id, chat_id, f"收到，正在生成「{text}」的报告，预计 30~60 秒…")
        self._pool.submit(self._work, text, message_id, chat_id)

    # ---- 后台线程：真正跑 Agent ----
    def _work(self, text: str, message_id: str, chat_id: str) -> None:
        try:
            res = self.runner(text, mode=self.cfg.zentao.mode, push=bool(self.cfg.bot.push))
        except Exception as exc:            # 任何异常都要回话，不能静默吞掉
            log.exception("机器人内生成异常")
            self._say(message_id, chat_id, f"❌ 生成失败：{exc}")
            return
        self._say(message_id, chat_id, format_result(res))

    # ---- 发送：优先挂在原消息下，退化到发群消息；发送失败只记日志不抛 ----
    def _say(self, message_id: str, chat_id: str, text: str) -> None:
        content = {"text": text}
        try:
            if message_id:
                self.client.reply_message(message_id, "text", content)
            elif chat_id:
                self.client.send_message(chat_id, "text", content)
        except Exception as exc:
            log.warning("回消息失败（不影响已生成的报告）：%s", exc)

    def shutdown(self) -> None:
        """排空线程池（测试与退出时用）。"""
        self._pool.shutdown(wait=True)


def serve(cfg: Optional[Config] = None) -> int:
    """建立长连接并阻塞收消息。缺凭据 / 缺白名单直接退出，不做"先连上再说"。"""
    cfg = cfg or load_config()
    if not (cfg.feishu.app_id and cfg.feishu.app_secret):
        print("[机器人] 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，未启动")
        return 2
    if not cfg.bot.allowed_chats and not cfg.bot.allowed_users:
        print("[机器人] 白名单为空，启动即全部拒绝；请先在 config/.env 填 FEISHU_BOT_ALLOWED_CHATS（群会话 ID）")
        return 2
    try:
        import lark_oapi as lark
        from lark_oapi import ws
    except ImportError:
        print("[机器人] 缺少依赖 lark-oapi，请先 pip install lark-oapi")
        return 2

    service = BotService(cfg)
    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(service.on_message)
               .build())
    client = ws.Client(cfg.feishu.app_id, cfg.feishu.app_secret,
                       event_handler=handler, log_level=lark.LogLevel.INFO)
    print(f"[机器人] 长连接启动中…（白名单群 {len(cfg.bot.allowed_chats)} 个，Ctrl+C 退出）")
    try:
        client.start()
    finally:
        service.shutdown()
    return 0
