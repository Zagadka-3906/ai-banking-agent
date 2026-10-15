"""Opt-in live text/card check with bounded waiting for access recovery.

Only exact acceptance phrases trigger replies; no bank queries or mutations.
Delivery and human rendering confirmation are recorded separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from feishu_cards import FeishuReply


class AcceptanceSession:
    def __init__(self, app_id, send, record, *, clock=time.time):
        self.app_id, self.send, self.record, self.clock = app_id, send, record, clock
        self.salt, self.seen, self.delivered = secrets.token_hex(32), set(), {}
        self.confirmed = {}
        self.started_at = {}

    def handle(self, event):
        try:
            header, body = event.header, event.event
            message, sender = body.message, body.sender
            if (header.app_id != self.app_id or message.chat_type != "p2p" or
                    sender.sender_type != "user" or message.message_type != "text"):
                return
            if abs(self.clock() - int(message.create_time) / 1000) > 120:
                return
            content = json.loads(message.content)
            text = content.get("text", "").strip()
            if text not in {"本轮验收", "验收通过", "验收失败"}:
                return
            event_id, chat_id, open_id = header.event_id, message.chat_id, sender.sender_id.open_id
            if not all(isinstance(value, str) and value for value in (event_id, chat_id, open_id)):
                return
        except (AttributeError, TypeError, ValueError):
            return
        if event_id in self.seen:
            return
        self.seen.add(event_id)
        identity = hashlib.sha256((self.salt + open_id).encode()).hexdigest()[:16]
        if text != "本轮验收":
            if identity not in self.delivered or identity in self.confirmed:
                return
            if int(message.create_time) < self.started_at[identity]:
                return
            passed = text == "验收通过"
            self.confirmed[identity] = passed
            self.record({"participant": identity, "at": int(self.clock()), "phase": "human_confirmation",
                         "transport_success": None, "human_rendering_passed": passed})
            self._send(chat_id, FeishuReply.text("已记录本轮人工验收结果。账户绑定、分类查询和撤权仍需单独验收。"),
                       event_id, "ack")
            return
        # A fresh trial cannot inherit the preceding trial's delivery or human result.
        self.delivered.pop(identity, None)
        self.confirmed.pop(identity, None)
        self.started_at[identity] = int(message.create_time)
        text_ok = self._send(chat_id, FeishuReply.text(
            "安言 · 文本消息验收\n第一行：中文正常显示。\n第二行：请同时检查随后收到的原生卡片。\n"
            "只有两条消息均收到且显示正常，才回复“验收通过”；否则回复“验收失败”。"), event_id, "text")
        self.record({"participant": identity, "at": int(self.clock()), "phase": "delivery_text",
                     "transport_success": text_ok, "human_rendering_passed": None})
        card = FeishuReply("interactive", {"config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "安言 · 本轮原生卡片验收"}, "template": "purple"},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content":
                "**这是一张验收卡片，无账户信息和交易按钮。**\n请检查标题、正文及中文换行。\n"
                "文本消息和卡片均正常请回复“验收通过”；否则回复“验收失败”。\n"
                "余额、账单分类、解绑及撤权仍需逐项验收。"}}]})
        card_ok = self._send(chat_id, card, event_id, "card")
        self.record({"participant": identity, "at": int(self.clock()), "phase": "delivery_card",
                     "transport_success": card_ok, "human_rendering_passed": None})
        self.delivered[identity] = text_ok and card_ok

    def _send(self, chat_id, payload, event_id, phase):
        message_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"feishu-acceptance/{event_id}/{phase}"))
        try:
            return bool(self.send(chat_id, payload, message_uuid))
        except Exception:
            # SDK exceptions may contain tokens, URLs or message content.
            return False

    def summary(self):
        return {"participants": len(self.delivered), "both_delivered": sum(self.delivered.values()),
                "human_passed": sum(self.confirmed.values()),
                "human_failed": sum(not passed for passed in self.confirmed.values()),
                "complete": bool(self.delivered) and all(self.delivered.values()) and
                            len(self.confirmed) == len(self.delivered) and all(self.confirmed.values())}


def wait_for_access(probe, report, *, wait_seconds=0, poll_seconds=30, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + max(0, min(wait_seconds, 3600))
    while True:
        access = probe()
        report(access)
        if access["ok"] or clock() >= deadline or access["kind"] == "not_configured":
            return access
        sleep(min(max(15, min(poll_seconds, 300)), max(0, deadline - clock())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=900)
    parser.add_argument("--wait-for-access-seconds", type=int, default=0)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    folder = args.output_dir.resolve()
    if not folder.is_relative_to(ROOT / "build"):
        parser.error("Private acceptance records must stay in build/")
    if folder.exists() and any(folder.iterdir()):
        parser.error("Use a new, empty output directory for each run")
    folder.mkdir(parents=True, exist_ok=True)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from feishu_access import probe_access

    def save(name, value):
        temporary = folder / f"{name}.tmp"
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        temporary.replace(folder / name)

    def access_report(access):
        save("access.json", access)
        print(json.dumps({"state": "access_available" if access["ok"] else "awaiting_access", **access}), flush=True)

    access = wait_for_access(probe_access, access_report, wait_seconds=args.wait_for_access_seconds, poll_seconds=args.poll_seconds)
    if not access["ok"]:
        save("summary.json", {"complete": False, "stage": "tenant_token", "access": access})
        return 2

    import lark_oapi as lark
    from feishu_im import _send_reply
    # This dedicated process must not print signed WebSocket URLs from SDK logs.
    for logger_name in ("Lark", "lark_oapi"):
        logging.getLogger(logger_name).disabled = True
    app_id, app_secret = os.environ["FEISHU_APP_ID"].strip(), os.environ["FEISHU_APP_SECRET"].strip()
    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).timeout(8).build()
    inbox, records = queue.Queue(maxsize=1000), []
    finished, ws_failed, worker_failed, completed = (threading.Event() for _ in range(4))

    def record(value):
        records.append(value)
        save("acceptance.json", records)
        print(json.dumps(value), flush=True)

    session = AcceptanceSession(app_id, lambda chat, reply, event: _send_reply(client, chat, reply, event), record)

    def replies():
        while not finished.is_set():
            try:
                event = inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                session.handle(event)
                save("summary.json", session.summary())
                if session.summary()["complete"]:
                    completed.set()
            except Exception:
                worker_failed.set()
                print(json.dumps({"state": "worker_failed"}), flush=True)
                return

    def on_message(event):
        # A worker performs API calls so the event callback can finish promptly.
        inbox.put_nowait(event)

    dispatcher = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
    ws = lark.ws.Client(app_id, app_secret, event_handler=dispatcher, log_level=lark.LogLevel.ERROR)

    def connect():
        try:
            ws.start()
        except Exception:
            ws_failed.set()
            print(json.dumps({"state": "connection_failed"}), flush=True)

    worker = threading.Thread(target=replies, daemon=True)
    worker.start()
    threading.Thread(target=connect, daemon=True).start()
    status = {"state": "listener_started", "connection_proven": False,
              "instruction": "Send 本轮验收, check both text and card, then reply 验收通过 or 验收失败"}
    save("status.json", status)
    print(json.dumps(status), flush=True)
    deadline = time.monotonic() + max(30, min(args.duration_seconds, 3600))
    while time.monotonic() < deadline and not ws_failed.wait(0.5):
        if worker_failed.is_set() or completed.is_set():
            break
    finished.set()
    worker.join(timeout=45)
    if worker.is_alive():
        # Do not race a stalled worker writing the same summary file.
        print(json.dumps({"state": "worker_timeout", "complete": False}), flush=True)
        return 3
    summary = session.summary()
    summary["connection_failed"] = ws_failed.is_set()
    summary["worker_failed"] = worker_failed.is_set()
    summary["worker_drained"] = not worker.is_alive() and inbox.empty()
    summary["complete"] = (summary["complete"] and not summary["connection_failed"] and
                           not summary["worker_failed"] and summary["worker_drained"])
    save("summary.json", summary)
    print(json.dumps(summary), flush=True)
    return 0 if summary["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
