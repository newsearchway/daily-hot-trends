#!/usr/bin/env python3
"""
每日热搜看板 - 飞书 Webhook 推送版（零依赖，可上云）

用法:
  python3 push_hot_trends.py

环境变量:
  FEISHU_WEBHOOK_URL  飞书群自定义机器人 Webhook 地址（必填）
  HOT_TRENDS_LIMIT    每平台抓取条数，默认 10

特点:
  - 仅使用 Python 标准库（urllib/json），无需 pip 安装
  - 不依赖 lark-cli，直接调用飞书 Webhook
  - 可在 GitHub Actions / 云函数 / VPS 等任何环境运行
  - 电脑关机也能按时推送
"""

import json
import os
import ssl
import sys
import re
import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError

# ============ 配置 ============

WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
LIMIT = int(os.environ.get("HOT_TRENDS_LIMIT", "10"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_SSL_CTX = ssl.create_default_context()


def _urlopen(req, timeout=10):
    try:
        return urlopen(req, timeout=timeout, context=_SSL_CTX)
    except URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, (ssl.SSLError, ssl.SSLCertVerificationError)):
            return urlopen(req, timeout=timeout, context=ssl.create_default_context())
        raise


# ============ 抓取函数 ============

def fetch_zhihu(limit=10):
    url = "https://api.zhihu.com/topstory/hot-lists/total?limit={}".format(limit)
    try:
        req = Request(url, headers=HEADERS)
        with _urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ 知乎抓取失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    for item in data.get("data", []):
        target = item.get("target", {})
        title = target.get("title", "").strip()
        if not title:
            continue
        detail = item.get("detail_text", "")
        results.append({
            "title": title,
            "platform": "知乎",
            "emoji": "💬",
            "heat_display": detail,
            "url": "https://www.zhihu.com/question/{}".format(target.get("id", "")),
        })
    return results


def fetch_weibo(limit=10):
    url = "https://weibo.com/ajax/side/hotSearch"
    headers = {**HEADERS, "Referer": "https://weibo.com/"}
    try:
        req = Request(url, headers=headers)
        with _urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ 微博抓取失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    realtime = data.get("data", {}).get("realtime", [])
    for item in realtime[:limit]:
        note = item.get("note", "").strip()
        if not note:
            continue
        label = item.get("label_name", "")
        if label:
            note = "[{}] {}".format(label, note)
        num = item.get("num", 0)
        results.append({
            "title": note,
            "platform": "微博",
            "emoji": "🌐",
            "heat_display": str(num),
            "url": "https://s.weibo.com/weibo?q=%23{}%23".format(item.get("word", note)),
        })
    return results


def fetch_baidu(limit=10):
    url = "https://top.baidu.com/api/board?platform=pc&tab=realtime"
    try:
        req = Request(url, headers=HEADERS)
        with _urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ 百度抓取失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    cards = data.get("data", {}).get("cards", [])
    if cards:
        content = cards[0].get("content", [])
        for item in content[:limit]:
            query = item.get("query", "").strip()
            if not query:
                continue
            results.append({
                "title": query,
                "platform": "百度",
                "emoji": "🔍",
                "heat_display": item.get("desc", "")[:30],
                "url": "https://www.baidu.com/s?wd={}".format(query),
            })
    return results


def fetch_bilibili(limit=10):
    url = "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all"
    try:
        req = Request(url, headers=HEADERS)
        with _urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ B站抓取失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    items = data.get("data", {}).get("list", [])
    for item in items[:limit]:
        title = item.get("title", "").strip()
        if not title:
            continue
        stat = item.get("stat", {})
        results.append({
            "title": title,
            "platform": "B站",
            "emoji": "📺",
            "heat_display": "{}播放".format(_format_num(stat.get("view", 0))),
            "url": item.get("short_link_v2", "https://www.bilibili.com/video/{}".format(item.get("bvid", ""))),
        })
    return results


def fetch_douyin(limit=10):
    url = "https://www.douyin.com/aweme/v1/web/hot/search/list/?device_platform=webapp&aid=6383"
    headers = {**HEADERS, "Referer": "https://www.douyin.com/"}
    try:
        req = Request(url, headers=headers)
        with _urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ 抖音抓取失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    word_list = data.get("data", {}).get("word_list", [])
    for item in word_list[:limit]:
        title = (item.get("word", "") or "").strip()
        if not title:
            continue
        results.append({
            "title": title,
            "platform": "抖音",
            "emoji": "🎵",
            "heat_display": _format_num(item.get("hot_value", 0)),
            "url": "https://www.douyin.com/hot/{}".format(item.get("sentence_id", "")),
        })
    return results


def fetch_toutiao(limit=10):
    url = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
    headers = {**HEADERS, "Referer": "https://www.toutiao.com/"}
    try:
        req = Request(url, headers=headers)
        with _urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ 头条抓取失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    items = data.get("data", [])
    for item in items[:limit]:
        title = (item.get("Title", "") or "").strip()
        if not title:
            continue
        results.append({
            "title": title,
            "platform": "头条",
            "emoji": "📰",
            "heat_display": str(item.get("HotValue", "")),
            "url": item.get("Url", "") or "https://www.toutiao.com/",
        })
    return results


FETCHERS = [
    ("知乎", fetch_zhihu),
    ("微博", fetch_weibo),
    ("百度", fetch_baidu),
    ("B站", fetch_bilibili),
    ("抖音", fetch_douyin),
    ("头条", fetch_toutiao),
]


def _format_num(n):
    n = int(n)
    if n >= 100000000:
        return "{:.1f}亿".format(n / 100000000)
    elif n >= 10000:
        return "{:.1f}万".format(n / 10000)
    elif n >= 1000:
        return "{:.1f}k".format(n / 1000)
    return str(n)


# ============ 格式化与推送 ============

def format_markdown(data, today_str):
    lines = []
    lines.append("# 🔥 每日全平台热搜看板")
    lines.append("")
    lines.append("**{}** | 来源：知乎 · 微博 · 百度 · B站 · 抖音 · 头条".format(today_str))
    lines.append("")
    lines.append("---")
    lines.append("")
    total = sum(len(v) for v in data.values())
    lines.append("**共 {} 个平台，{} 条热点**".format(len(data), total))
    lines.append("")

    for platform, items in data.items():
        emoji = next((p[1] for p in [("知乎","💬"),("微博","🌐"),("百度","🔍"),("B站","📺"),("抖音","🎵"),("头条","📰")] if p[0]==platform), "📌")
        lines.append("## {} {} (TOP {})".format(emoji, platform, len(items)))
        lines.append("")
        for i, item in enumerate(items, 1):
            title = item.get("title", "")[:40]
            heat = item.get("heat_display", "")
            url = item.get("url", "")
            heat_str = " 🔥{}".format(heat) if heat else ""
            if url:
                lines.append("{}. [{}]({}){}".format(i, title, url, heat_str))
            else:
                lines.append("{}. {}{}".format(i, title, heat_str))
        lines.append("")

    lines.append("---")
    lines.append("_由 GitHub Actions 自动推送，电脑关机也能准时送达_")
    return "\n".join(lines)


def send_to_feishu(markdown):
    """通过飞书自定义机器人 Webhook 发送消息"""
    if not WEBHOOK_URL:
        print("❌ 未设置 FEISHU_WEBHOOK_URL 环境变量", file=sys.stderr)
        return False

    payload = {
        "msg_type": "text",
        "content": {"text": markdown},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            print("✅ 飞书推送成功", file=sys.stderr)
            return True
        else:
            print("❌ 飞书推送失败: {}".format(result), file=sys.stderr)
            return False
    except Exception as e:
        print("❌ 飞书推送异常: {}".format(e), file=sys.stderr)
        return False


# ============ 主流程 ============

def main():
    if not WEBHOOK_URL:
        print("❌ 请先设置环境变量 FEISHU_WEBHOOK_URL", file=sys.stderr)
        sys.exit(1)

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    print("📡 {} 抓取全平台热搜...".format(today), file=sys.stderr)

    data = {}
    success = 0
    for platform, fetcher in FETCHERS:
        print("  抓取 {}...".format(platform), file=sys.stderr)
        try:
            items = fetcher(LIMIT)
            if items:
                data[platform] = items
                success += 1
                print("    ✅ {} 条".format(len(items)), file=sys.stderr)
            else:
                print("    ⚠️ 无数据", file=sys.stderr)
        except Exception as e:
            print("    ❌ 失败: {}".format(e), file=sys.stderr)

    if not data:
        print("⚠️ 未抓取到任何数据，发送告警", file=sys.stderr)
        send_to_feishu("⚠️ 今日热搜抓取失败，未获取到任何平台数据。")
        sys.exit(1)

    print("📤 推送到飞书群...", file=sys.stderr)
    markdown = format_markdown(data, today)
    ok = send_to_feishu(markdown)

    total = sum(len(v) for v in data.values())
    print("\n✅ 完成 | {}平台 {}条 | 推送: {}".format(
        success, total, "成功" if ok else "失败"), file=sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
