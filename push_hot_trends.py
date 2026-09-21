#!/usr/bin/env python3
"""
每日热搜看板 + 新闻联播 - 飞书推送（零依赖，可上云）

用法:
  python3 push_hot_trends.py                       # 默认：全量热搜
  python3 push_hot_trends.py --hot-trends          # 全量推送 6 大平台热搜（早 7 点）
  python3 push_hot_trends.py --hot-trends-incremental  # 增量推送热搜（午 2 点）
  python3 push_hot_trends.py --xinwenlianbo        # 推送新闻联播全部标题+链接（晚 8 点）

环境变量（推送方式二选一）:
  方式1（推荐）自定义机器人 Webhook:
    FEISHU_WEBHOOK_URL   飞书群自定义机器人 Webhook 地址
  方式2 飞书开放平台应用:
    FEISHU_APP_ID        应用 App ID
    FEISHU_APP_SECRET    应用 App Secret
    FEISHU_CHAT_ID       目标群 chat_id

其他:
  HOT_TRENDS_LIMIT     每平台热搜抓取条数，默认 10
  STATE_FILE           已推送标题状态文件，默认 hot_trends_state.json

推送时间:
  07:00  6大平台热搜TOP10 全量
  14:00  6大平台热搜TOP10 增量（仅新上榜）
  20:00  新闻联播全部标题+内容链接
"""

import argparse
import json
import os
import ssl
import sys
import re
import time
import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError
from html.parser import HTMLParser

# ============ 配置 ============

WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "")
LIMIT = int(os.environ.get("HOT_TRENDS_LIMIT", "10"))
STATE_FILE = os.environ.get("STATE_FILE", "hot_trends_state.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_SSL_CTX = ssl.create_default_context()
_TOKEN_CACHE = {"token": "", "expire_at": 0}


def _urlopen(req, timeout=10):
    try:
        return urlopen(req, timeout=timeout, context=_SSL_CTX)
    except URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, (ssl.SSLError, ssl.SSLCertVerificationError)):
            return urlopen(req, timeout=timeout, context=ssl.create_default_context())
        raise


# ============ 热搜抓取 ============

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
        results.append({
            "title": title,
            "platform": "知乎",
            "heat_display": item.get("detail_text", ""),
            "url": "https://www.zhihu.com/question/{}".format(target.get("id", "")),
        })
    return results


def fetch_weibo(limit=10):
    url = "https://weibo.com/ajax/side/hotSearch"
    try:
        req = Request(url, headers={**HEADERS, "Referer": "https://weibo.com/"})
        with _urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ 微博抓取失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    for item in data.get("data", {}).get("realtime", [])[:limit]:
        note = item.get("note", "").strip()
        if not note:
            continue
        if item.get("label_name"):
            note = "[{}] {}".format(item["label_name"], note)
        results.append({
            "title": note,
            "platform": "微博",
            "heat_display": str(item.get("num", 0)),
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
        for item in cards[0].get("content", [])[:limit]:
            query = item.get("query", "").strip()
            if not query:
                continue
            results.append({
                "title": query,
                "platform": "百度",
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
    for item in data.get("data", {}).get("list", [])[:limit]:
        title = item.get("title", "").strip()
        if not title:
            continue
        results.append({
            "title": title,
            "platform": "B站",
            "heat_display": "{}播放".format(_fmt_num(item.get("stat", {}).get("view", 0))),
            "url": item.get("short_link_v2", "https://www.bilibili.com/video/{}".format(item.get("bvid", ""))),
        })
    return results


def fetch_douyin(limit=10):
    url = "https://www.douyin.com/aweme/v1/web/hot/search/list/?device_platform=webapp&aid=6383"
    try:
        req = Request(url, headers={**HEADERS, "Referer": "https://www.douyin.com/"})
        with _urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ 抖音抓取失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    for item in data.get("data", {}).get("word_list", [])[:limit]:
        title = (item.get("word", "") or "").strip()
        if not title:
            continue
        results.append({
            "title": title,
            "platform": "抖音",
            "heat_display": _fmt_num(item.get("hot_value", 0)),
            "url": "https://www.douyin.com/hot/{}".format(item.get("sentence_id", "")),
        })
    return results


def fetch_toutiao(limit=10):
    url = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
    try:
        req = Request(url, headers={**HEADERS, "Referer": "https://www.toutiao.com/"})
        with _urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ⚠️ 头条抓取失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    for item in data.get("data", [])[:limit]:
        title = (item.get("Title", "") or "").strip()
        if not title:
            continue
        results.append({
            "title": title,
            "platform": "头条",
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


def _fmt_num(n):
    n = int(n)
    if n >= 100000000:
        return "{:.1f}亿".format(n / 100000000)
    elif n >= 10000:
        return "{:.1f}万".format(n / 10000)
    elif n >= 1000:
        return "{:.1f}k".format(n / 1000)
    return str(n)


# ============ 新闻联播抓取 ============

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            text = data.strip()
            if text:
                self.parts.append(text)


def _html_to_text(html):
    p = _TextExtractor()
    p.feed(html)
    return "\n".join(p.parts)


def fetch_xinwenlianbo(max_items=None):
    main_url = "https://tv.cctv.com/lm/xwlb/"
    try:
        req = Request(main_url, headers=HEADERS)
        with _urlopen(req, timeout=15) as resp:
            main_html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print("  ⚠️ 新闻联播主页抓取失败: {}".format(e), file=sys.stderr)
        return []

    pattern = re.compile(
        r'<a[^>]*href="(https://tv\.cctv\.com/\d{4}/\d{2}/\d{2}/VIDE[\w]+\.shtml)"[^>]*>',
        re.DOTALL,
    )
    seen = set()
    items = []
    for m in pattern.finditer(main_html):
        url = m.group(1)
        tag = m.group(0)
        title_m = re.search(r'title="([^"]+)"', tag) or re.search(r'alt="([^"]+)"', tag)
        if not title_m:
            continue
        title = re.sub(r"\[视频\]", "", title_m.group(1)).strip()
        title = re.sub(r"\s+", " ", title).strip()
        if not title or "完整版" in title or "新闻联播》" in title or url in seen:
            continue
        seen.add(url)
        items.append({"title": title, "url": url})

    if not items:
        return []

    target = items if max_items is None else items[:max_items]
    results = []
    for item in target:
        try:
            req = Request(item["url"], headers=HEADERS)
            with _urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            text = _html_to_text(html)
            idx = text.find("主要内容")
            if idx != -1:
                body = text[idx + 4:]
            else:
                for marker in ["央视网消息", "新闻联播"]:
                    idx = text.find(marker)
                    if idx != -1:
                        body = text[idx + len(marker):]
                        break
                else:
                    body = text
            body = re.sub(r"\s+", " ", body).strip()
            for footer in ["编辑：", "央视网首页", "京ICP备", "中央广播电视总台",
                           "责任编辑：", "版权所有"]:
                fidx = body.find(footer)
                if fidx != -1:
                    body = body[:fidx].strip()
            summary = body[:150] + ("…" if len(body) > 150 else "")
            results.append({"title": item["title"], "url": item["url"], "summary": summary})
        except Exception:
            results.append({"title": item["title"], "url": item["url"], "summary": ""})

    print("  ✅ 新闻联播 {} 条".format(len(results)), file=sys.stderr)
    return results


# ============ 格式化 ============

def format_hot_trends_md(data, today_str):
    lines = ["# 🔥 每日全平台热搜看板", "",
             "**{}** | 来源：知乎 · 微博 · 百度 · B站 · 抖音 · 头条".format(today_str), "",
             "---", ""]
    total = sum(len(v) for v in data.values())
    lines.append("**共 {} 个平台，{} 条热点**".format(len(data), total))
    lines.append("")
    for platform, items in data.items():
        lines.append("## {} (TOP {})".format(platform, len(items)))
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
    lines.append("_由 GitHub Actions 自动推送_")
    return "\n".join(lines)


def format_xinwenlianbo_md(items, today_str):
    if not items:
        return ""
    lines = ["# 📺 新闻联播 · 今日要目", "",
             "**{}** | 共 {} 条 | 来源：央视网".format(today_str, len(items)), "",
             "---", ""]
    for i, item in enumerate(items, 1):
        title = item.get("title", "")
        url = item.get("url", "")
        summary = item.get("summary", "")
        if url:
            lines.append("{}. [{}]({})".format(i, title, url))
        else:
            lines.append("{}. **{}**".format(i, title))
        if summary:
            lines.append("   > {}".format(summary))
        lines.append("")
    lines.append("---")
    lines.append("_点击标题跳转央视网查看完整视频与文稿_")
    return "\n".join(lines)


# ============ 飞书推送（Webhook 或 OpenAPI） ============

def _get_tenant_token():
    """获取并缓存飞书 tenant_access_token"""
    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expire_at"]:
        return _TOKEN_CACHE["token"]
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    body = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with _urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != 0:
        raise RuntimeError("获取 tenant_access_token 失败: {}".format(data))
    _TOKEN_CACHE["token"] = data["tenant_access_token"]
    _TOKEN_CACHE["expire_at"] = now + data.get("expire", 7200) - 60
    return _TOKEN_CACHE["token"]


def _send_via_webhook(text):
    payload = {"msg_type": "text", "content": {"text": text}}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(WEBHOOK_URL, data=body,
                  headers={"Content-Type": "application/json"}, method="POST")
    with _urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result.get("code") == 0 or result.get("StatusCode") == 0


def _send_via_openapi(text):
    token = _get_tenant_token()
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    payload = {
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body,
                  headers={"Content-Type": "application/json",
                           "Authorization": "Bearer {}".format(token)},
                  method="POST")
    with _urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result.get("code") == 0


def send_to_feishu(text):
    """发送消息到飞书，优先 Webhook，其次 OpenAPI"""
    try:
        if WEBHOOK_URL:
            ok = _send_via_webhook(text)
            print("✅ 飞书推送(Webhook): {}".format("成功" if ok else "失败"), file=sys.stderr)
            return ok
        elif FEISHU_APP_ID and FEISHU_APP_SECRET and FEISHU_CHAT_ID:
            ok = _send_via_openapi(text)
            print("✅ 飞书推送(OpenAPI): {}".format("成功" if ok else "失败"), file=sys.stderr)
            return ok
        else:
            print("❌ 未配置推送方式：设置 FEISHU_WEBHOOK_URL 或 FEISHU_APP_ID+FEISHU_APP_SECRET+FEISHU_CHAT_ID",
                  file=sys.stderr)
            return False
    except Exception as e:
        print("❌ 飞书推送异常: {}".format(e), file=sys.stderr)
        return False


# ============ 状态持久化 ============

def load_pushed_titles():
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("titles", []))
    except Exception:
        return set()


def save_pushed_titles(titles):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"titles": sorted(titles)}, f, ensure_ascii=False)
    except Exception as e:
        print("⚠️ 状态保存失败: {}".format(e), file=sys.stderr)


# ============ 主流程 ============

def run_hot_trends(today, incremental=False):
    mode = "增量" if incremental else "全量"
    print("📡 {} 抓取热搜（{}模式）...".format(today, mode), file=sys.stderr)

    pushed = set()
    if incremental:
        pushed = load_pushed_titles()
        print("  📋 已推送 {} 条".format(len(pushed)), file=sys.stderr)

    data = {}
    new_titles = set()
    for platform, fetcher in FETCHERS:
        print("  抓取 {}...".format(platform), file=sys.stderr)
        try:
            items = fetcher(LIMIT)
            if incremental:
                items = [it for it in items if it.get("title") not in pushed]
            if items:
                data[platform] = items
                for it in items:
                    new_titles.add(it.get("title", ""))
                print("    ✅ {} 条".format(len(items)), file=sys.stderr)
            else:
                print("    ⏭️ 无新增" if incremental else "    ⚠️ 无数据", file=sys.stderr)
        except Exception as e:
            print("    ❌ 失败: {}".format(e), file=sys.stderr)

    if not data:
        if incremental:
            print("ℹ️ 暂无新增热搜", file=sys.stderr)
            return True
        send_to_feishu("⚠️ 今日热搜抓取失败。")
        return False

    md = format_hot_trends_md(data, today)
    if incremental:
        md = "🔔 **热搜增量更新**\n\n" + md
    ok = send_to_feishu(md)
    save_pushed_titles(pushed | new_titles)
    total = sum(len(v) for v in data.values())
    print("✅ 热搜{}完成 | {}平台 {}条".format(mode, len(data), total), file=sys.stderr)
    return ok


def run_xinwenlianbo(today):
    print("📡 抓取新闻联播...", file=sys.stderr)
    items = fetch_xinwenlianbo(max_items=None)
    if not items:
        send_to_feishu("⚠️ 今日新闻联播抓取失败。")
        return False
    md = format_xinwenlianbo_md(items, today)
    ok = send_to_feishu(md)
    print("✅ 新闻联播完成 | {} 条".format(len(items)), file=sys.stderr)
    return ok


def main():
    parser = argparse.ArgumentParser(description="每日热搜 + 新闻联播飞书推送")
    parser.add_argument("--hot-trends", action="store_true", help="全量推送热搜（早7点）")
    parser.add_argument("--hot-trends-incremental", action="store_true", help="增量推送热搜（午2点）")
    parser.add_argument("--xinwenlianbo", action="store_true", help="推送新闻联播（晚8点）")
    args = parser.parse_args()

    if not WEBHOOK_URL and not (FEISHU_APP_ID and FEISHU_APP_SECRET and FEISHU_CHAT_ID):
        print("❌ 请设置 FEISHU_WEBHOOK_URL 或 FEISHU_APP_ID+FEISHU_APP_SECRET+FEISHU_CHAT_ID",
              file=sys.stderr)
        sys.exit(1)

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    any_mode = args.hot_trends or args.hot_trends_incremental or args.xinwenlianbo
    results = []
    if args.hot_trends or not any_mode:
        results.append(run_hot_trends(today, incremental=False))
    if args.hot_trends_incremental:
        results.append(run_hot_trends(today, incremental=True))
    if args.xinwenlianbo:
        results.append(run_xinwenlianbo(today))

    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
