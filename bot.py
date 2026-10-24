
import os
import re
import time
import html
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timezone

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ["TELEGRAM_CHAT_ID"]

FEEDS = [
    "https://news.google.com/rss/search?q=Nha+Trang&hl=vi&gl=VN&ceid=VN:vi",
    "https://news.google.com/rss/search?q=Kh%C3%A1nh+H%C3%B2a&hl=vi&gl=VN&ceid=VN:vi",
]

SUBSCRIBE_URL = "https://t.me/NhaTrangLifeNews"

KEYWORDS = [
    "nha trang", "khánh hòa", "khanh hoa",
    "cam ranh", "diên khánh", "dien khanh",
    "vạn ninh", "van ninh", "trường sa", "truong sa"
]

def get_url(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read()

def translate(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text).strip()
    if not text:
        return ""

    params = urllib.parse.urlencode({
        "client": "gtx",
        "sl": "auto",
        "tl": "ru",
        "dt": "t",
        "q": text[:4000]
    })
    url = "https://translate.googleapis.com/translate_a/single?" + params

    try:
        data = json.loads(get_url(url).decode("utf-8"))
        return "".join(part[0] for part in data[0] if part[0]).strip()
    except Exception as exc:
        print(f"Translation error: {exc}")
        return text

def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode()

    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=25) as response:
        result = json.loads(response.read().decode())
        if not result.get("ok"):
            raise RuntimeError(f"Telegram error: {result}")

def clean_text(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

def main():
    items = []
    seen = set()

    for feed in FEEDS:
        try:
            root = ET.fromstring(get_url(feed))
            for item in root.findall(".//item"):
                title = clean_text(item.findtext("title"))
                description = clean_text(item.findtext("description"))
                link = (item.findtext("link") or "").strip()

                if not title or not link or link in seen:
                    continue
                seen.add(link)

                combined = (title + " " + description).lower()
                if not any(word in combined for word in KEYWORDS):
                    continue

                items.append((title, description, link))

        except Exception as exc:
            print(f"Feed error: {exc}")

    published = 0

    for title, description, link in items[:3]:
        ru_title = translate(title)
        ru_description = translate(description) if description else ""

        # Keep the summary short.
        sentences = re.split(r"(?<=[.!?])\s+", ru_description)
        summary = " ".join(sentences[:3]).strip()
        if len(summary) > 650:
            summary = summary[:647].rsplit(" ", 1)[0] + "..."

        post = (
            f"📰 <b>{html.escape(ru_title)}</b>\n\n"
            f"{html.escape(summary)}\n\n"
            f'🇻🇳 <a href="{SUBSCRIBE_URL}">'
            f"Нячанг Лайф | Подписаться</a>"
        )

        try:
            send_message(post)
            published += 1
            print(f"Published: {ru_title}")
            time.sleep(2)
        except Exception as exc:
            print(f"Publish error: {exc}")

    print(
        f"Finished at {datetime.now(timezone.utc).isoformat()}; "
        f"found {len(items)} matching items; published {published}."
    )

if __name__ == "__main__":
    main()
