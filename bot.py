
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html import escape
from datetime import datetime, timezone

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ["TELEGRAM_CHAT_ID"]

FEEDS = [
    "https://news.google.com/rss/search?q=Nha+Trang+Vietnam&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Khanh+Hoa+Vietnam&hl=en-US&gl=US&ceid=US:en",
]

KEYWORDS = [
    "nha trang", "khanh hoa", "cam ranh",
    "nhatrang", "khánh hòa", "nha trang"
]

def get_url(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "NhaTrangLifeNewsBot/1.0"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()

def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode()
    with urllib.request.urlopen(url, data=data, timeout=20) as response:
        result = response.read().decode()
        if '"ok":true' not in result:
            raise RuntimeError(result)

def main():
    seen = set()
    items = []

    for feed in FEEDS:
        try:
            root = ET.fromstring(get_url(feed))
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                description = (item.findtext("description") or "").strip()
                if not title or not link or link in seen:
                    continue
                seen.add(link)
                combined = (title + " " + description).lower()
                if any(word in combined for word in KEYWORDS):
                    items.append((title, link))
        except Exception as exc:
            print(f"Feed error: {exc}")

    # Avoid flooding the channel during initial setup.
    for title, link in items[:3]:
        post = (
            f"📰 <b>{escape(title)}</b>\n\n"
            f"Источник: <a href=\"{escape(link, quote=True)}\">прочитать новость</a>\n\n"
            "🇻🇳 Нячанг Лайф | Новости\n\n"
            '<a href="https://t.me/NhaTrangLifeNews">ПОДПИСАТЬСЯ ↗</a>'
        )
        send_message(post)
        print(f"Published: {title}")

    print(f"Finished at {datetime.now(timezone.utc).isoformat()}; "
          f"found {len(items)} matching items.")

if __name__ == "__main__":
    main()
