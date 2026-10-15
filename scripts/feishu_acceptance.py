"""Opt-in live card rendering check. Only replies to exact acceptance phrases.

No bank binding, account query or payment. Human confirmation is recorded
separately from successful delivery. Never stores chat IDs or message content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=900)
    args = parser.parse_args()
    folder = args.output_dir.resolve()
    if not folder.is_relative_to(ROOT / "build"):
        raise ValueError("Private acceptance records must stay in build/")
    folder.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import lark_oapi as lark
    from feishu_access import probe_access
    access = probe_access()
    if not access["ok"]:
        (folder / "access-failed.json").write_text(json.dumps(access, indent=2), encoding="utf-8")
        print(json.dumps(access), flush=True)
        return
    from feishu_cards import FeishuReply
    from feishu_im import _send_reply
    app_id, app_secret = os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"]
    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
    salt, seen, delivered = secrets.token_hex(32), set(), set()
    lock = threading.Lock()
    records = []

    def on_message(event):
        header, body = event.header, event.event
        message, sender = body.message, body.sender
        if header.app_id != app_id or message.chat_type != "p2p" or sender.sender_type != "user" or message.message_type != "text":
            return
        if abs(time.time() - int(message.create_time) / 1000) > 120:
            return
        text = json.loads(message.content).get("text", "").strip()
        if text not in {"本轮验收", "验收通过", "验收失败"}:
            return
        identity = hashlib.sha256((salt + sender.sender_id.open_id).encode()).hexdigest()[:16]
        with lock:
            if header.event_id in seen:
                return
            seen.add(header.event_id)
            if text != "本轮验收" and identity not in delivered:
                return
            reply = FeishuReply("interactive", {"config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": "安言 · 本轮原生卡片验收"}, "template": "purple"},
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content":
                    "**这是一张验收卡片，无账户信息和交易按钮。**\n请检查标题、正文及中文换行是否正常。正常请回复“验收通过”，异常请回复“验收失败”。\n余额、账单分类、解绑及撤权仍需按验收清单逐项操作。"}}]})
            success = _send_reply(client, message.chat_id, reply, header.event_id)
            if text == "本轮验收" and success:
                delivered.add(identity)
            records.append({"participant": identity, "at": int(time.time()), "phase": "delivery" if text == "本轮验收" else "human_confirmation",
                            "transport_success": success, "human_rendering_passed": text == "验收通过" if text != "本轮验收" else None})
            (folder / "acceptance.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
            print(json.dumps(records[-1]), flush=True)

    dispatcher = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
    ws = lark.ws.Client(app_id, app_secret, event_handler=dispatcher, log_level=lark.LogLevel.WARNING)
    threading.Thread(target=ws.start, daemon=True).start()
    print("Acceptance listener started; this alone does not prove connection", flush=True)
    time.sleep(max(30, min(args.duration_seconds, 3600)))


if __name__ == "__main__":
    main()
