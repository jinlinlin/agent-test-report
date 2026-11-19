"""飞书机器人通道自检：验证「应用能不能往白名单群发消息」。

用途：接机器人前先把发送通道验通——报 `99991672` 说明 im:message 类权限没随版本发布生效，
报 `230002`/「机器人不在群里」说明应用还没被加进那个群，正常返回 message_id 即通道可用。
长连接收消息那一半没法离线验（要有人在群里说话），跑起来后看日志里的 chat= 是否与白名单一致。

用法：
    python scripts/feishu_bot_probe.py                # 往 .env 白名单里每个群发一条自检消息
    python scripts/feishu_bot_probe.py --dry-run      # 只打印将要发送的目标与文案，不发网络
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_agent.config import load_config                      # noqa: E402
from report_agent.tools.feishu_client import FeishuClient, FeishuError  # noqa: E402

TEXT = "【自检】测试报告机器人发送通道连通（这条由 scripts/feishu_bot_probe.py 发出，可忽略）"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="飞书机器人发送通道自检")
    parser.add_argument("--dry-run", action="store_true", help="只打印目标，不实际发消息")
    args = parser.parse_args(argv)

    cfg = load_config()
    chats = tuple(cfg.bot.allowed_chats)
    if not (cfg.feishu.app_id and cfg.feishu.app_secret):
        print("[失败] 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        return 2
    if not chats:
        print("[失败] FEISHU_BOT_ALLOWED_CHATS 为空，没有可发的目标群")
        return 2

    print(f"[目标] 白名单群 {len(chats)} 个：{', '.join(chats)}")
    if args.dry_run:
        print("[dry-run] 未发送。文案：" + TEXT)
        return 0

    client = FeishuClient(cfg.feishu)
    try:
        token = client.get_tenant_token()
        print(f"[认证] tenant_access_token 获取成功（长度 {len(token)}）")
    except FeishuError as exc:
        print(f"[失败] 认证阶段：({exc.kind}) {exc}")
        return 2

    ok = 0
    for chat in chats:
        try:
            mid = client.send_message(chat, "text", {"text": TEXT})
            print(f"[成功] → {chat}  message_id={mid}")
            ok += 1
        except FeishuError as exc:
            hint = ""
            msg = str(exc)
            if "99991672" in msg or "Access denied" in msg:
                hint = "  ← im:message 类权限未随版本发布生效，去后台「创建版本 → 发布」"
            elif "230002" in msg or "not found" in msg.lower() or "chat" in msg.lower():
                hint = "  ← 应用还没被加进这个群（群设置 → 群机器人 → 添加机器人）"
            print(f"[失败] → {chat}：({exc.kind}) {exc}{hint}")
    print(f"\n[结果] 成功 {ok}/{len(chats)}")
    return 0 if ok == len(chats) else 1


if __name__ == "__main__":
    sys.exit(main())
