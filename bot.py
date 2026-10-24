
import os
import re
import json
import html
import time
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ["TELEGRAM_CHAT_ID"]
SUBSCRIBE_URL = "https://t.me/NhaTrangLifeNews"
STATE_FILE = "bot_state.json"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")

FEEDS = [
    "https://news.google.com/rss/search?q=Nha+Trang&hl=vi&gl=VN&ceid=VN:vi",
    "https://news.google.com/rss/search?q=Kh%C3%A1nh+H%C3%B2a&hl=vi&gl=VN&ceid=VN:vi",
]

KEYWORDS = [
    "nha trang", "khánh hòa", "khanh hoa", "cam ranh",
    "diên khánh", "dien khanh", "vạn ninh", "van ninh",
    "trường sa", "truong sa"
]

STOP_WORDS = {
    "это", "как", "что", "для", "при", "после", "перед",
    "этот", "эта", "эти", "его", "ее", "их", "они", "она",
    "оно", "или", "если", "уже", "также", "который",
    "которая", "которые", "быть", "был", "была", "будет",
    "the", "and", "for", "with", "from", "that", "this",
    "nha", "trang", "khanh", "hoa"
}


def request_json(url, data=None, timeout=25):
    body = None
    headers = {"User-Agent": "Mozilla/5.0"}

    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_url(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read()


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    state.setdefault("seen_links", [])
    state.setdefault("seen_titles", [])
    state.setdefault("manual_posts", [])
    state.setdefault("last_update_id", 0)
    state.setdefault("last_weather_date", "")
    return state


def save_state(state):
    # GitHub Actions commits this file after the script finishes.
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


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
        return "".join(
            part[0] for part in data[0] if part[0]
        ).strip()
    except Exception as exc:
        print(f"Translation error: {exc}")
        return ""


def telegram_api(method, data=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    return request_json(url, data=data)


def send_message(text):
    result = telegram_api("sendMessage", {
        "chat_id": CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    })
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


def get_bot_id():
    result = telegram_api("getMe")
    return result["result"]["id"]


def collect_manual_posts(state, bot_id):
    # Read channel posts since the last saved update.
    offset = state["last_update_id"] + 1
    result = telegram_api("getUpdates", {
        "offset": offset,
        "timeout": 0,
        "allowed_updates": json.dumps(
            ["channel_post", "edited_channel_post"]
        )
    })

    if not result.get("ok"):
        print(f"getUpdates error: {result}")
        return

    for update in result.get("result", []):
        state["last_update_id"] = max(
            state["last_update_id"], update["update_id"]
        )

        post = (
            update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not post:
            continue

        chat = post.get("chat", {})
        if str(chat.get("id")) != str(CHANNEL) and (
            chat.get("username", "").lower()
            != str(CHANNEL).lstrip("@").lower()
        ):
            continue

        sender = post.get("from", {})
        if sender.get("id") == bot_id or sender.get("is_bot"):
            continue

        text = clean_text(post.get("text") or post.get("caption") or "")
        if text:
            state["manual_posts"].append(text)

    state["manual_posts"] = state["manual_posts"][-100:]


def short_headline(title):
    title = clean_text(title)

    # Remove the news outlet suffix often appended to RSS titles.
    title = re.split(r"\s[-–|]\s", title, maxsplit=1)[0].strip()
    translated = translate(title)
    if not translated:
        return ""

    translated = re.sub(r"\s+", " ", translated).strip()
    if len(translated) > 100:
        translated = translated[:97].rsplit(" ", 1)[0] + "…"

    return translated


def make_summary(description, headline):
    description = clean_text(description)
    if not description:
        return ""

    text = translate(description)
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text).strip()

    # Remove a repeated headline at the beginning of the description.
    if headline and text.casefold().startswith(headline.casefold()):
        text = text[len(headline):].lstrip(" :-—–.,;!?")

    # If the description contains the headline as its first sentence,
    # remove that sentence rather than publishing it twice.
    parts = re.split(r"(?<=[.!?])\s+", text)
    if len(parts) > 1 and headline:
        first_words = set(words(parts[0]))
        title_words = set(words(headline))
        if title_words and len(first_words & title_words) / len(title_words) >= 0.65:
            parts = parts[1:]

    text = " ".join(part for part in parts if part).strip()

    if not text or len(words(text)) < 5:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(sentences[:3]).strip()

    if len(text) > 650:
        text = text[:647].rsplit(" ", 1)[0] + "..."

    return text


def words(text):
    return [
        word for word in re.findall(r"[a-zа-яё0-9]+", text.casefold())
        if len(word) > 2 and word not in STOP_WORDS
    ]


def title_key(title):
    normalized = " ".join(words(title))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def already_posted(headline, link, state):
    if link in state["seen_links"]:
        return True

    if title_key(headline) in state["seen_titles"]:
        return True

    title_words = set(words(headline))
    if len(title_words) >= 4:
        for post in state["manual_posts"]:
            post_words = set(words(post))
            overlap = len(title_words & post_words) / len(title_words)
            if overlap >= 0.65:
                print(f"Skipped likely manual duplicate: {headline}")
                return True

    return False


def remember_news(headline, link, state):
    state["seen_links"].append(link)
    state["seen_titles"].append(title_key(headline))
    state["seen_links"] = state["seen_links"][-500:]
    state["seen_titles"] = state["seen_titles"][-500:]


def emoji_for(headline, summary):
    text = (headline + " " + summary).casefold()

    rules = [
        (["дожд", "ливен", "шторм", "наводнен", "ветер",
          "rain", "storm", "flood"], "🌧️"),
        (["пожар", "авари", "опасн", "предупрежден",
          "штраф", "запрет", "происшеств"], "⚠️"),
        (["турист", "туризм", "отел", "гостиниц", "пляж",
          "путешеств"], "🏖️"),
        (["самолет", "аэропорт", "рейс", "авиакомпан",
          "камрань", "пассажир"], "✈️"),
        (["дорог", "движен", "транспорт", "автобус",
          "пробк", "машин"], "🚗"),
        (["цена", "стоимост", "рынок", "эконом",
          "бизнес", "торгов"], "💰"),
        (["власт", "мэр", "городск", "решени",
          "проект", "строительств"], "🏛️"),
        (["медицин", "больниц", "здоров", "врач"], "🏥"),
        (["школ", "университет", "студент", "образован"], "🎓"),
        (["море", "рыб", "рыбак", "побереж"], "🌊"),
    ]

    for keywords, emoji in rules:
        if any(word in text for word in keywords):
            return emoji

    return "📰"


def publish_news(headline, summary, state, link):
    emoji = emoji_for(headline, summary)

    post = (
        f"{emoji} <b>{html.escape(headline)}</b>\n\n"
        f"{html.escape(summary)}\n\n"
        f'🇻🇳 <a href="{SUBSCRIBE_URL}">'
        f"Нячанг Лайф | Подписаться</a>"
    )

    send_message(post)
    remember_news(headline, link, state)
    print(f"Published: {headline}")


def weather_description(code):
    mapping = {
        0: ("☀️", "ясно"),
        1: ("🌤️", "преимущественно ясно"),
        2: ("⛅", "переменная облачность"),
        3: ("☁️", "облачно"),
        45: ("🌫️", "туман"),
        48: ("🌫️", "туман"),
        51: ("🌦️", "морось"),
        53: ("🌦️", "морось"),
        55: ("🌧️", "сильная морось"),
        61: ("🌧️", "небольшой дождь"),
        63: ("🌧️", "дождь"),
        65: ("🌧️", "сильный дождь"),
        80: ("🌦️", "кратковременный дождь"),
        81: ("🌧️", "ливни"),
        82: ("⛈️", "сильные ливни"),
        95: ("⛈️", "гроза"),
        96: ("⛈️", "гроза с градом"),
        99: ("⛈️", "сильная гроза с градом"),
    }
    return mapping.get(code, ("🌤️", "переменная погода"))


def publish_weather(state):
    now = datetime.now(TZ)
    today = now.date().isoformat()

    if state["last_weather_date"] == today:
        return

    url = (
        "https://api.open-meteo.com/v1/forecast?"
        "latitude=12.2388&longitude=109.1967"
        "&current=temperature_2m,apparent_temperature,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,weather_code"
        "&timezone=Asia%2FHo_Chi_Minh&forecast_days=1"
    )

    try:
        data = json.loads(get_url(url).decode("utf-8"))
        current = data["current"]
        daily = data["daily"]

        icon, condition = weather_description(
            int(current["weather_code"])
        )
        low = round(daily["temperature_2m_min"][0])
        high = round(daily["temperature_2m_max"][0])
        rain = daily.get("precipitation_probability_max", [None])[0]
        rain_text = (
            f"{rain}%" if rain is not None
            else "нет данных"
        )

        post = (
            f"{icon} <b>Погода в Нячанге на сегодня</b>\n\n"
            f"Сейчас: {round(current['temperature_2m'])}°C, "
            f"{condition}.\n"
            f"Ощущается как: "
            f"{round(current['apparent_temperature'])}°C.\n"
            f"За день: от {low}°C до {high}°C.\n"
            f"Вероятность осадков: {rain_text}.\n\n"
            f'🇻🇳 <a href="{SUBSCRIBE_URL}">'
            f"Нячанг Лайф | Подписаться</a>"
        )

        send_message(post)
        state["last_weather_date"] = today
        print("Weather published.")

    except Exception as exc:
        print(f"Weather error: {exc}")


def main():
    state = load_state()

    try:
        bot_id = get_bot_id()
        collect_manual_posts(state, bot_id)
    except Exception as exc:
        print(f"Channel update check error: {exc}")

    items = []
    links_in_feed = set()

    for feed in FEEDS:
        try:
            root = ET.fromstring(get_url(feed))

            for item in root.findall(".//item"):
                title = clean_text(item.findtext("title"))
                description = clean_text(item.findtext("description"))
                link = (item.findtext("link") or "").strip()

                if not title or not description or not link:
                    continue
                if link in links_in_feed:
                    continue
                links_in_feed.add(link)

                combined = (title + " " + description).casefold()
                if not any(word in combined for word in KEYWORDS):
                    continue

                items.append((title, description, link))

        except Exception as exc:
            print(f"Feed error: {exc}")

    published = 0

    for title, description, link in items:
        if published >= 3:
            break

        headline = short_headline(title)
        if not headline:
            print(f"Skipped: translation failed for {title}")
            continue

        if already_posted(headline, link, state):
            continue

        summary = make_summary(description, headline)
        if not summary:
            print(f"Skipped: no distinct summary for {headline}")
            continue

        try:
            publish_news(headline, summary, state, link)
            published += 1
            time.sleep(2)
        except Exception as exc:
            print(f"Publish error: {exc}")

    # Publish the weather once daily, on the first run after 08:00 local time.
    if datetime.now(TZ).hour >= 8:
        publish_weather(state)

    save_state(state)

    print(
        f"Finished at {datetime.now(TZ).isoformat()}; "
        f"RSS matches: {len(items)}; news published: {published}."
    )


if __name__ == "__main__":
    main()
