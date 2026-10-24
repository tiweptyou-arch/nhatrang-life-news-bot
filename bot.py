
import os
import re
import html
import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ["TELEGRAM_CHAT_ID"]

SUBSCRIBE_URL = "https://t.me/NhaTrangLifeNews"

FEEDS = [
    "https://news.google.com/rss/search?q=Nha+Trang&hl=vi&gl=VN&ceid=VN:vi",
    "https://news.google.com/rss/search?q=Kh%C3%A1nh+H%C3%B2a&hl=vi&gl=VN&ceid=VN:vi",
]

KEYWORDS = [
    "nha trang", "khánh hòa", "khanh hoa",
    "cam ranh", "diên khánh", "dien khanh",
    "vạn ninh", "van ninh", "trường sa", "truong sa"
]

MAX_POSTS_PER_RUN = 3


def get_url(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def clean_text(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def translate(text):
    text = clean_text(text)
    if not text:
        return ""

    params = urllib.parse.urlencode({
        "client": "gtx",
        "sl": "auto",
        "tl": "ru",
        "dt": "t",
        "q": text[:4000]
    })

    url = (
        "https://translate.googleapis.com/translate_a/single?"
        + params
    )

    try:
        data = json.loads(get_url(url).decode("utf-8"))
        translated = "".join(
            part[0] for part in data[0] if part[0]
        )
        return translated.strip()
    except Exception as exc:
        print(f"Translation error: {exc}")
        return ""


def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode()

    request = urllib.request.Request(url, data=data)

    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


def make_summary(description, title):
    # Use the RSS description as the news text.
    text = translate(description)

    if not text:
        text = translate(title)

    if not text:
        return ""

    # Remove the translated title if the description repeats it.
    translated_title = translate(title)

    if translated_title:
        normalized_text = text.casefold()
        normalized_title = translated_title.casefold()

        if normalized_text.startswith(normalized_title):
            text = text[len(translated_title):].lstrip(
                " :-—–.,;!?"
            )

    # Keep only a short text, without a separate headline.
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(sentences[:3]).strip()

    if len(text) > 650:
        text = text[:647].rsplit(" ", 1)[0] + "..."

    return text


def main():
    items = []
    seen_links = set()

    for feed in FEEDS:
        try:
            root = ET.fromstring(get_url(feed))

            for item in root.findall(".//item"):
                title = clean_text(item.findtext("title"))
                description = clean_text(
                    item.findtext("description")
                )
                link = (item.findtext("link") or "").strip()

                if not title or not link:
                    continue

                if link in seen_links:
                    continue
                seen_links.add(link)

                combined = (title + " " + description).casefold()

                if not any(word in combined for word in KEYWORDS):
                    continue

                items.append((title, description, link))

        except Exception as exc:
            print(f"Feed error: {exc}")

    published = 0

    for title, description, link in items[:MAX_POSTS_PER_RUN]:
        summary = make_summary(description, title)

        if not summary:
            print(f"Skipped: translation unavailable for {title}")
            continue

        post = (
            f"{html.escape(summary)}\n\n"
            f'🇻🇳 <a href="{SUBSCRIBE_URL}">'
            f"Нячанг Лайф | Подписаться</a>"
        )

        try:
            send_message(post)
            published += 1
            print(f"Published: {summary[:100]}")
            time.sleep(2)
        except Exception as exc:
            print(f"Publish error: {exc}")

    print(
        f"Finished at {datetime.now(timezone.utc).isoformat()}; "
        f"found {len(items)} matching items; published {published}."
    )


if __name__ == "__main__":
    main()
