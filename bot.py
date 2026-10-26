import os, re, json, html, time, hashlib, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHANNEL = os.environ.get('TELEGRAM_CHAT_ID', '@NhaTrangLifeNews')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID', '').strip()
# Allowed private-chat managers. The environment ID remains supported for compatibility.
ADMIN_USER_IDS = {x.strip() for x in os.environ.get('ADMIN_USER_IDS', '1335496436,8563451956').split(',') if x.strip()}
if ADMIN_USER_ID: ADMIN_USER_IDS.add(ADMIN_USER_ID)
TZ = ZoneInfo('Asia/Ho_Chi_Minh')
STATE_FILE = 'bot_state.json'
SUBSCRIBE_URL = 'https://t.me/NhaTrangLifeNews'
SUGGEST_URL = 'https://t.me/fsfinesed'
FOOTER = f'🇻🇳 <a href="{SUBSCRIBE_URL}">ПОДПИСАТЬСЯ</a> | <a href="{SUGGEST_URL}">ПРЕДЛОЖИТЬ НОВОСТЬ</a>'
# Official local sources are searched first. Google News RSS is used only as a lightweight
# discovery layer; the published link remains the original publisher's URL.
FEEDS = [
 'https://news.google.com/rss/search?q=site%3Akhanhhoa.gov.vn+Nha+Trang&hl=vi&gl=VN&ceid=VN:vi',
 'https://news.google.com/rss/search?q=site%3Anhatrang.khanhhoa.gov.vn+Nha+Trang&hl=vi&gl=VN&ceid=VN:vi',
 'https://news.google.com/rss/search?q=site%3Anews.baokhanhhoa.vn+Nha+Trang&hl=vi&gl=VN&ceid=VN:vi',
 'https://news.google.com/rss/search?q=site%3Acongan.khanhhoa.gov.vn+Kh%C3%A1nh+H%C3%B2a&hl=vi&gl=VN&ceid=VN:vi',
 'https://news.google.com/rss/search?q=site%3Anchmf.gov.vn+Kh%C3%A1nh+H%C3%B2a&hl=vi&gl=VN&ceid=VN:vi',
 'https://news.google.com/rss/search?q=site%3Avndms.gov.vn+Kh%C3%A1nh+H%C3%B2a+ng%E1%BA%ADp&hl=vi&gl=VN&ceid=VN:vi',
 'https://news.google.com/rss/search?q=Nha+Trang&hl=vi&gl=VN&ceid=VN:vi',
 'https://news.google.com/rss/search?q=Kh%C3%A1nh+H%C3%B2a&hl=vi&gl=VN&ceid=VN:vi',
 'https://news.google.com/rss/search?q=Nha+Trang+Kh%C3%A1nh+H%C3%B2a&hl=en&gl=US&ceid=US:en',
]
KEYWORDS = ['nha trang','khánh hòa','khanh hoa','cam ranh','diên khánh','dien khanh','vạn ninh','van ninh','trường sa','truong sa']


def now(): return datetime.now(TZ)
def iso(dt): return dt.astimezone(TZ).isoformat()
def parse_dt(value):
    try: return datetime.fromisoformat(value).astimezone(TZ)
    except Exception: return now()

def parse_dt_strict(value):
    """Parse a schedule/expiry timestamp safely; invalid dates must never trigger an action."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(TZ)
    except (TypeError, ValueError, OverflowError):
        return None

def parse_source_date(value):
    """Parse an RSS publication date and return local time, or None if unknown."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    try:
        return parsed.astimezone(TZ)
    except (ValueError, OverflowError):
        return None


def is_recent_source_date(published_at, current_time=None, urgent=False):
    """Reject unknown, future-dated, and stale stories before publication."""
    if published_at is None:
        return False
    current_time = current_time or now()
    age = current_time - published_at
    if age < -timedelta(minutes=15):
        return False
    maximum_age = timedelta(hours=24 if urgent else 72)
    return age <= maximum_age


def get_url(url):
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 NhaTrangLifeNewsBot/2.0'})
    with urllib.request.urlopen(req, timeout=12) as r: return r.read()


def get_url_with_final_url(url):
    """Fetch a URL and expose the final redirect destination for Google News RSS wrappers."""
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 NhaTrangLifeNewsBot/2.0'})
    with urllib.request.urlopen(req, timeout=12) as response:
        return response.geturl(), response.read()


def api(method, data=None):
    """Call Telegram without putting the secret bot token in exception messages or logs."""
    url = f'https://api.telegram.org/bot{TOKEN}/{method}'
    body = urllib.parse.urlencode(data or {}).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            result = json.loads(response.read().decode())
    except Exception as e:
        # urllib exceptions can include the full request URL, which contains the bot token.
        raise RuntimeError(f'Telegram API {method} transport error ({type(e).__name__})') from None
    if not result.get('ok'):
        description = result.get('description') or 'unknown Telegram API error'
        raise RuntimeError(f'Telegram API {method} failed: {description}')
    return result['result']


def main_menu():
    return {'keyboard': [[{'text':'📰 Новости'}, {'text':'📥 Предложить пост'}], [{'text':'📣 Добавить рекламу'}, {'text':'📋 Активная реклама'}], [{'text':'📊 Статус'}, {'text':'⏸ Пауза / ▶️ Продолжить'}]], 'resize_keyboard': True, 'is_persistent': True}

def ad_actions(ad_id):
    return {'inline_keyboard': [[
        {'text': '📌 Закрепить', 'callback_data': f'ad_pin:{ad_id}'},
        {'text': '📍 Открепить', 'callback_data': f'ad_unpin:{ad_id}'},
        {'text': '🗑 Удалить сейчас', 'callback_data': f'ad_delete:{ad_id}'}
    ]]}

def send_dm(user_id, text, markup=None):
    data = {'chat_id': user_id, 'text': text, 'parse_mode':'HTML', 'disable_web_page_preview':'true'}
    if markup: data['reply_markup'] = json.dumps(markup, ensure_ascii=False)
    return api('sendMessage', data)


def load_state():
    """Load existing state without silently replacing damaged data with defaults."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding='utf-8') as f:
                s = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f'Не удалось прочитать {STATE_FILE}; файл не будет перезаписан, чтобы не потерять данные.'
            ) from e
        if not isinstance(s, dict):
            raise RuntimeError(f'{STATE_FILE} должен содержать JSON-объект; сохранение остановлено.')
    else:
        s = {}
    defaults = {
      'seen_links': [], 'seen_titles': [], 'seen_fingerprints': [], 'last_update_id': 0, 'pending_ad': {},
      'ads': [], 'paused': False, 'pause_until': '', 'daily_counts': {}, 'last_slots': {},
      'game_history': [], 'last_news_hour': '', 'manual_posts': [], 'pending_manual_post': {}, 'published_news': [],
      'pending_news_review': {}, 'review_editing': {}, 'rejected_news_links': [], 'rejected_news_fingerprints': [], 'rejected_news_titles': []
    }
    for k, v in defaults.items():
        s.setdefault(k, v)
    for key in ('seen_links', 'seen_titles', 'seen_fingerprints', 'ads', 'published_news', 'game_history', 'manual_posts', 'rejected_news_links', 'rejected_news_fingerprints', 'rejected_news_titles'):
        if not isinstance(s.get(key), list):
            raise RuntimeError(
                f'Поле {key} в {STATE_FILE} имеет неверный формат; файл не будет сохранён, чтобы не потерять данные.'
            )
    for key in ('daily_counts', 'last_slots', 'pending_ad', 'pending_manual_post', 'pending_news_review', 'review_editing'):
        if not isinstance(s.get(key), dict):
            raise RuntimeError(
                f'Поле {key} в {STATE_FILE} имеет неверный формат; файл не будет сохранён, чтобы не потерять данные.'
            )
    if not isinstance(s.get('last_update_id'), int):
        raise RuntimeError(f'Поле last_update_id в {STATE_FILE} имеет неверный формат; сохранение остановлено.')
    if not isinstance(s.get('paused'), bool):
        raise RuntimeError(f'Поле paused в {STATE_FILE} имеет неверный формат; сохранение остановлено.')
    return s


def save_state(s):
    """Atomic write: a failed process cannot leave half-written JSON."""
    temp_path = STATE_FILE + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, STATE_FILE)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass



def clean(value):
    value = re.sub(r'<[^>]+>', ' ', value or '')
    return re.sub(r'\s+', ' ', html.unescape(value)).strip()


def clean_multiline(value):
    """Remove markup while preserving paragraph boundaries in long articles."""
    value = re.sub(r'<(script|style|noscript|svg)\b[^>]*>.*?</\1>', ' ', value or '', flags=re.I | re.S)
    value = re.sub(r'<br\s*/?>|</(p|div|li|h[1-6])\s*>', '\n\n', value, flags=re.I)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = html.unescape(value)
    value = re.sub(r'[ \t\r\f\v]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def normalized_title(value):
    value = clean(value).casefold()
    value = re.sub(r'https?://\S+', ' ', value)
    # \w respects Unicode letters, including Vietnamese diacritics.
    value = re.sub(r'[^\w\s]', ' ', value, flags=re.UNICODE)
    value = re.sub(r'_+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def title_fingerprint(value):
    """Stable title fingerprint catches exact duplicates after normalizing punctuation."""
    words = [w for w in normalized_title(value).split() if len(w) > 2]
    return hashlib.sha256(' '.join(words).encode('utf-8')).hexdigest()


PRACTICAL_NEWS_TERMS = (
    'cảnh báo', 'mưa', 'bão', 'ngập', 'lũ', 'sạt lở', 'thời tiết', 'gió mạnh', 'sóng lớn',
    'tai nạn', 'cháy', 'nổ', 'cứu hộ', 'sơ tán', 'an toàn', 'nguy hiểm', 'khẩn cấp',
    'giao thông', 'ùn tắc', 'kẹt xe', 'cấm đường', 'phân luồng', 'đường phố', 'cầu',
    'sân bay', 'xe buýt', 'tàu', 'cảng', 'bến xe', 'bệnh viện', 'bác sĩ', 'phòng khám',
    'nhà thuốc', 'dịch bệnh', 'y tế', 'trường học', 'học sinh', 'giáo dục', 'đại học',
    'visa', 'thị thực', 'xuất nhập cảnh', 'lưu trú', 'người nước ngoài', 'cư trú',
    'giá nhà', 'tiền thuê', 'giá thuê', 'giá điện', 'giá nước', 'mất điện', 'cúp điện',
    'mất nước', 'internet', 'rác thải', 'nước biển', 'chất lượng nước', 'ô nhiễm',
    'môi trường', 'quy định', 'chính quyền', 'ủy ban', 'xử phạt', 'phạt tiền',
    'hạ tầng', 'công trình', 'sửa chữa', 'đóng cửa', 'khai trương', 'mở cửa',
    'miễn phí', 'dịch vụ công', 'giờ hoạt động', 'bãi biển', 'cấm tắm biển',
    'thực phẩm', 'giá xăng', 'giá cả', 'chi phí sinh hoạt', 'bất động sản',
    'động đất', 'lễ hội', 'đình chỉ', 'thu hồi',
    'thủ tục', 'cải cách hành chính', 'quyết định',
    'lệ phí', 'đường dây nóng', 'phân phối nước', 'lịch cúp điện', 'xét nghiệm',
    # English-language sources need the same practical-life filter.
    'weather', 'heavy rain', 'flood warning', 'typhoon', 'storm warning', 'road closure',
    'traffic', 'public transport', 'bus route', 'airport', 'hospital', 'clinic', 'pharmacy',
    'school', 'students', 'education', 'visa', 'immigration', 'residency', 'foreign residents',
    'electricity outage', 'power outage', 'water outage', 'internet outage', 'rental prices',
    'cost of living', 'water quality', 'pollution', 'road repairs', 'government announces',
    'official warning', 'emergency services', 'beach closure', 'food safety',
    # Russian terms help if an RSS provider already returns a Russian-language headline.
    'погода', 'ливень', 'шторм', 'наводнение', 'предупреждение', 'авария', 'пожар',
    'перекрытие дороги', 'транспорт', 'аэропорт', 'больница', 'аптека', 'школа',
    'виза', 'миграция', 'электричество', 'отключение воды', 'интернет', 'аренда',
    'цены', 'качество воды', 'загрязнение', 'правила пребывания'
)
SPORTS_NOISE_TERMS = (
    'bóng đá', 'bóng chuyền', 'trận đấu', 'giải đấu', 'cầu thủ', 'huấn luyện viên',
    'thể thao', 'vô địch', 'tỷ số', 'bàn thắng', 'football match', 'sports match',
    'soccer', 'basketball game'
)
URGENT_NEWS_TERMS = (
    'cảnh báo', 'khẩn cấp', 'bão', 'ngập', 'lũ', 'sạt lở', 'sơ tán', 'cháy', 'nổ',
    'tai nạn nghiêm trọng', 'mất tích', 'nguy hiểm', 'cấm tắm biển', 'sóng lớn',
    'dịch bệnh', 'đóng đường khẩn cấp', 'emergency', 'flood', 'storm', 'evacuation'
)
ALLOWED_REACTIONS = ('🤣', '😱', '👍', '❤️', '🙏')


def contains_term(text, term):
    """Match a whole word or phrase so short syllables don't create false alerts."""
    words = [re.escape(part) for part in term.split()]
    if not words:
        return False
    pattern = r'(?<!\w)' + r'\s+'.join(words) + r'(?!\w)'
    return re.search(pattern, text, flags=re.UNICODE) is not None


def is_relevant_news(title, description):
    """Only accept local stories with a practical-life signal."""
    text = clean((title or '') + ' ' + (description or '')).casefold()
    if not text or not any(contains_term(text, k) for k in KEYWORDS):
        return False
    practical = any(contains_term(text, term) for term in PRACTICAL_NEWS_TERMS)
    sports = any(contains_term(text, term) for term in SPORTS_NOISE_TERMS)
    urgent_or_safety = any(contains_term(text, term) for term in URGENT_NEWS_TERMS)
    if sports and not urgent_or_safety:
        return False
    return practical or urgent_or_safety


def is_urgent_news(text):
    value = clean(text).casefold()
    return any(contains_term(value, term) for term in URGENT_NEWS_TERMS)



def remove_repeated_lead(headline, body):
    """Remove a repeated first sentence while preserving remaining facts and paragraphs."""
    body = clean_multiline(body)
    if not body:
        return ''
    paragraphs = body.split('\n\n')
    first_paragraph = paragraphs[0].strip()
    sentences = re.split(r'(?<=[.!?…])\s+', first_paragraph, maxsplit=1)
    first = sentences[0].strip()
    h_words = set(normalized_title(headline).split())
    f_words = set(normalized_title(first).split())
    if h_words and f_words:
        jaccard = len(h_words & f_words) / max(1, len(h_words | f_words))
        same_start = normalized_title(first) == normalized_title(headline)
        similar_length = len(f_words) <= max(8, int(len(h_words) * 1.35))
        if same_start or (jaccard >= 0.78 and similar_length):
            remainder = sentences[1].strip() if len(sentences) > 1 else ''
            if remainder:
                paragraphs[0] = remainder
            else:
                paragraphs = paragraphs[1:]
            return '\n\n'.join(p.strip() for p in paragraphs if p.strip())
    return body



def limit_news_body(value, max_chars=1450):
    """Keep a useful, readable body while avoiding an excessively long post."""
    value = clean_multiline(re.sub(r'https?://\S+', '', value or ''))
    if len(value) <= max_chars:
        return value

    paragraphs = [part.strip() for part in value.split('\n\n') if part.strip()]
    kept = []
    for paragraph in paragraphs:
        current = '\n\n'.join(kept)
        candidate = paragraph if not current else current + '\n\n' + paragraph
        if len(candidate) <= max_chars:
            kept.append(paragraph)
            continue

        room = max_chars - len(current) - (2 if current else 0)
        if room > 0:
            fragment = paragraph[:room]
            if len(paragraph) > room and ' ' in fragment:
                fragment = fragment.rsplit(' ', 1)[0]
            fragment = fragment.rstrip(' ,;:-')
            if fragment:
                if not fragment.endswith(('…', '.', '!', '?')):
                    fragment += '…'
                kept.append(fragment)
        break

    result = '\n\n'.join(kept).strip()
    if not result:
        result = value[:max_chars].rsplit(' ', 1)[0].rstrip(' ,;:-') + '…'
    return result


def reaction_line(text):
    """Use topic-relevant reactions; serious incidents deliberately get no playful prompt."""
    value = clean(text).casefold()
    if is_urgent_news(value) or any(contains_term(value, x) for x in ('thiệt mạng', 'tử vong', 'người chết', 'bị thương nặng')):
        return ''
    if any(contains_term(value, x) for x in ('tình nguyện', 'giúp đỡ', 'hỗ trợ người dân', 'cứu trợ', 'quyên góp', 'cảm ơn')):
        return '🙏 — спасибо всем, кто помогает.'
    if any(contains_term(value, x) for x in ('câu chuyện cảm động', 'đoàn tụ', 'giúp đỡ trẻ em', 'lan tỏa yêu thương')):
        return '❤️ — такая история заслуживает поддержки.'
    if any(contains_term(value, x) for x in ('hài hước', 'buồn cười', 'dở khóc dở cười', 'tình huống hài')):
        return '🤣 — редкий повод улыбнуться.'
    if any(contains_term(value, x) for x in ('giao thông', 'xe buýt', 'bệnh viện', 'trường học', 'quy định', 'giá điện', 'giá nước', 'dịch vụ', 'công trình', 'khai trương', 'mở cửa', 'транспорт', 'больница', 'школа', 'правила', 'цены', 'услуги', 'открытие', 'ремонт дороги')):
        return '👍 — полезно; ❤️ — хочу больше такой информации.'
    return '👍 — полезно; ❤️ — хочу больше таких новостей.'

def news_image_query(title, body=''):
    """Choose a topical illustration query using the translated Russian story as well as source terms."""
    value = clean(title + ' ' + body).casefold()
    local = 'Nha Trang Khanh Hoa Vietnam'
    if any(x in value for x in (
        'bão', 'mưa', 'ngập', 'lũ', 'thời tiết', 'storm', 'flood',
        'погода', 'дожд', 'ливень', 'шторм', 'наводнен', 'затоплен', 'тайфун',
        'гроза', 'осадк', 'сильный ветер', 'штормовое предупреждение'
    )):
        return 'Vietnam tropical storm heavy rain flooded street landscape'
    if any(x in value for x in (
        'trường học', 'học sinh', 'giáo dục', 'đại học',
        'школ', 'учен', 'образован', 'студент', 'университет', 'обучен'
    )):
        return 'Vietnamese school students classroom education'
    if any(x in value for x in (
        'bệnh viện', 'bác sĩ', 'phòng khám', 'nhà thuốc', 'y tế',
        'больниц', 'врач', 'клиник', 'аптек', 'медицин', 'здравоохран'
    )):
        return 'Vietnam hospital doctor healthcare clinic'
    if any(x in value for x in (
        'giao thông', 'đường', 'cầu', 'xe buýt', 'sân bay', 'tai nạn', 'cấm đường',
        'дорог', 'транспорт', 'пробк', 'автобус', 'аэропорт', 'авари', 'перекрыт',
        'ремонт дороги', 'мост', 'железнодорож'
    )):
        return 'Nha Trang Vietnam road traffic public transport'
    if any(x in value for x in (
        'nước biển', 'bãi biển', 'ô nhiễm', 'môi trường', 'rác thải',
        'пляж', 'море', 'загрязнен', 'окружающей сред', 'мусор', 'качество воды'
    )):
        return 'Nha Trang Vietnam beach coastline environment'
    if any(x in value for x in (
        'nhà ở', 'giá thuê', 'giá nhà', 'bất động sản', 'căn hộ',
        'аренд', 'жиль', 'недвижим', 'квартир', 'стоимость жилья'
    )):
        return 'Nha Trang Vietnam apartment buildings housing'
    if any(x in value for x in (
        'visa', 'thị thực', 'xuất nhập cảnh', 'lưu trú', 'người nước ngoài',
        'виза', 'иммиграц', 'миграц', 'иностранц', 'пребыван', 'регистрац'
    )):
        return 'Vietnam immigration passport paperwork government office'
    if any(x in value for x in (
        'điện', 'mất nước', 'internet', 'dịch vụ công', 'hạ tầng',
        'электрич', 'отключение воды', 'интернет', 'коммуналь', 'инфраструктур'
    )):
        return 'Nha Trang Vietnam urban infrastructure utilities'
    return local + ' city street public services'



def review_keyboard(item_id):
    """Inline controls for the private review preview."""
    return {'inline_keyboard': [
        [
            {'text': '✅ Опубликовать сейчас', 'callback_data': f'review:publish:{item_id}'},
            {'text': '✏️ Изменить текст', 'callback_data': f'review:edit:{item_id}'}
        ],
        [
            {'text': '⏰ Изменить время', 'callback_data': f'review:time:{item_id}'},
            {'text': '🗑 Отклонить', 'callback_data': f'review:reject:{item_id}'}
        ]
    ]}

def review_post_text(item):
    """Build the exact public post from plain text; keep article links out of the message."""
    body = clean_multiline(item.get('body', ''))
    prefix = '⚠️ ' if item.get('urgent') and item.get('kind') == 'news' else ''
    parts = [html.escape(prefix + body)]
    reaction = clean_multiline(item.get('reaction_line', ''))
    if reaction:
        parts.append(html.escape(reaction))
    credit = item.get('image_credit_html') or ''
    if credit:
        parts.append(credit)
    parts.append(footer().strip())
    return '\n\n'.join(part for part in parts if part)


def delete_review_previews(item):
    """Remove the private draft previews after publication or rejection."""
    for message in item.get('preview_messages', []):
        try:
            api('deleteMessage', {
                'chat_id': message.get('chat_id'),
                'message_id': message.get('message_id')
            })
        except Exception as e:
            print('Review preview cleanup skipped:', type(e).__name__)



def review_status_text(item):
    planned = parse_dt_strict(item.get('planned_at')) or (now() + timedelta(minutes=15))
    return (
        f'<b>Черновик: {html.escape(item.get("kind_label", "новость"))}</b>\n'
        f'План публикации: <b>{planned.strftime("%d.%m.%Y в %H:%M")}</b> по времени Нячанга.\n\n'
        'Если ничего не менять, бот опубликует материал в указанное время. '
        'Можно изменить текст, изменить время, опубликовать сразу или отклонить. '
        'Не удаляй предпросмотр вручную вместо отклонения: Telegram не сообщает боту об удалении сообщения.'
    )



def send_review_preview(item):
    """Send a private photo preview and review controls to every configured admin."""
    admins = sorted(ADMIN_USER_IDS)
    if not admins:
        print('No configured admins; review item was not queued.')
        return False

    item.setdefault('preview_messages', [])
    item.setdefault('review_status_messages', [])
    item['image_file_id'] = item.get('image_file_id') or ''
    image_fallback_attempted = False

    for admin_id in admins:
        if item.get('image_file_id'):
            photo = item['image_file_id']
        else:
            photo = normalize_image_url(item.get('image_url') or '')
            if not photo:
                photo = ''
        while True:
            if not photo:
                if image_fallback_attempted:
                    break
                image_fallback_attempted = True
                fallback = commons_image(item.get('image_query') or news_image_query(item.get('title', ''), item.get('body', '')))
                if not fallback:
                    break
                if isinstance(fallback, dict):
                    item['image_url'] = fallback.get('url') or ''
                    item['image_credit_html'] = fallback.get('credit_html') or ''
                else:
                    item['image_url'] = fallback
                    item['image_credit_html'] = ''
                item['image_file_id'] = ''
                photo = normalize_image_url(item.get('image_url') or '')
                continue

            full_text = review_post_text(item)
            caption_limit = 800 if item.get('image_credit_html') else 950
            caption, continuation = split_photo_caption(full_text, limit=caption_limit)
            if not caption:
                caption = '🇻🇳 Новости Нячанга'
            try:
                sent_photo = api('sendPhoto', {
                    'chat_id': admin_id,
                    'photo': photo,
                    'caption': caption,
                    'parse_mode': 'HTML'
                })
                item['image_file_id'] = (
                    ((sent_photo.get('photo') or [])[-1]).get('file_id', '')
                    if isinstance(sent_photo, dict) else ''
                )
                item['preview_messages'].append({
                    'chat_id': admin_id, 'message_id': sent_photo.get('message_id')
                })
                if continuation:
                    continued = send_dm(admin_id, 'Продолжение текста публикации:\n\n' + continuation)
                    if continued and continued.get('message_id'):
                        item['preview_messages'].append({
                            'chat_id': admin_id, 'message_id': continued['message_id']
                        })
                status = send_dm(admin_id, review_status_text(item), review_keyboard(item['id']))
                if status and status.get('message_id'):
                    message_ref = {'chat_id': admin_id, 'message_id': status['message_id']}
                    item['preview_messages'].append(message_ref)
                    item['review_status_messages'].append(message_ref)
                break
            except Exception as e:
                print('Review photo preview failed:', type(e).__name__)
                item['image_file_id'] = ''
                photo = ''
                continue

    return bool(item.get('preview_messages')) and bool(item.get('image_file_id'))

def queue_review_item(s, item, planned_at=None, slot_key=None):
    """Persist one private review candidate; publication waits until its planned time."""
    if s.get('pending_news_review'):
        return False
    item = dict(item)
    stamp = str(int(time.time()))
    fingerprint = hashlib.md5(
        ((item.get('link') or '') + stamp + (item.get('title') or '')).encode('utf-8')
    ).hexdigest()[:7]
    item_id = 'rv' + stamp + fingerprint
    item['id'] = item_id
    item['created_at'] = iso(now())
    item['status'] = 'pending_review'
    item['review_count'] = 0
    minimum_time = now() + timedelta(minutes=15)
    requested_time = planned_at or minimum_time
    item['planned_at'] = iso(max(requested_time, minimum_time))
    item['slot_key'] = slot_key or ''
    item['preview_messages'] = []
    item['image_url'] = normalize_image_url(item.get('image_url') or '') or ''
    item['image_file_id'] = item.get('image_file_id') or ''

    # Find a Vietnam/topic-specific fallback before sending the preview if the article has no usable image.
    if not item['image_url'] and not item['image_file_id']:
        fallback = commons_image(item.get('image_query') or news_image_query(item.get('title', ''), item.get('body', '')))
        if not fallback:
            print('No suitable image found; candidate was not queued:', item.get('title', item_id))
            return False
        if isinstance(fallback, dict):
            item['image_url'] = normalize_image_url(fallback.get('url') or '') or ''
            item['image_credit_html'] = fallback.get('credit_html') or ''
        else:
            item['image_url'] = normalize_image_url(fallback) or ''
            item['image_credit_html'] = ''
        if not item['image_url']:
            print('Fallback image was not a usable web image; candidate was not queued:', item_id)
            return False

    if not send_review_preview(item):
        delete_review_previews(item)
        print('Private photo preview could not be delivered; candidate not queued:', item_id)
        return False

    s['pending_news_review'] = item
    s['review_editing'] = {}
    print('Queued for review:', item_id, 'planned_at=', item['planned_at'], 'kind=', item.get('kind'))
    return True


def publish_review_item(s, item):
    """Publish an approved/due candidate once, then persist it to the normal deduplication history."""
    text = review_post_text(item)
    result = post_with_image(
        text,
        image_url=item.get('image_url') or None,
        image_file_id=item.get('image_file_id') or None,
        image_query=item.get('image_query') or None
    )
    if not result:
        raise RuntimeError('Публикация не вернула подтверждение Telegram.')
    timestamp = iso(now())
    if item.get('kind') == 'news':
        source_link = item.get('link') or ''
        original_title = item.get('source_title') or item.get('title') or ''
        if source_link:
            s['seen_links'] = (s.get('seen_links', []) + [source_link])[-1000:]
        s['seen_titles'] = (s.get('seen_titles', []) + [original_title])[-500:]
        s['seen_fingerprints'] = (s.get('seen_fingerprints', []) + [title_fingerprint(original_title)])[-1000:]
        s['published_news'] = (s.get('published_news', []) + [{
            'title': item.get('title') or original_title,
            'summary': clean_multiline(item.get('body', '')),
            'link': source_link,
            'published_at': timestamp,
            'source_published_at': item.get('source_published_at') or timestamp,
            'urgent': bool(item.get('urgent'))
        }])[-100:]
        s['last_news_hour'] = now().strftime('%Y-%m-%d-%H')
    bump_today(s)
    delete_review_previews(item)
    s['pending_news_review'] = {}
    s['review_editing'] = {}
    for admin_id in sorted(ADMIN_USER_IDS):
        try:
            send_dm(admin_id, f'✅ Материал <code>{html.escape(item.get("id", ""))}</code> опубликован в канале.')
        except Exception as e:
            print('Review publication notice failed:', type(e).__name__)
    return result



def process_news_review(s):
    """Publish a queued item at its planned time, tolerating sub-minute cron jitter for fixed rubrics."""
    item = s.get('pending_news_review') or {}
    if not item or item.get('status') != 'pending_review':
        return
    planned = parse_dt_strict(item.get('planned_at'))
    if not planned:
        print('Review item has an invalid planned timestamp; leaving it for admin review:', item.get('id'))
        return

    # Fixed daily rubrics are checked only every five minutes by GitHub Actions.
    # Permit at most 60 seconds of schedule jitter so a :00 post does not slip to :05
    # merely because the preview was created a few seconds after the previous cron tick.
    scheduled_slots = {'review_morning', 'weather_morning', 'weather_evening', 'review_night'}
    tolerance = timedelta(seconds=60) if item.get('slot_key') in scheduled_slots else timedelta(0)
    if now() + tolerance < planned:
        return

    if is_paused(s):
        later = top_pause_until(s)
        planned = later if later > now() else now() + timedelta(minutes=5)
        item['planned_at'] = iso(planned)
        for admin_id in sorted(ADMIN_USER_IDS):
            try:
                send_dm(admin_id, f'⏸ Материал <code>{html.escape(item.get("id", ""))}</code> сдвинут на {planned.strftime("%d.%m %H:%M")}: сейчас действует пауза.')
            except Exception as e:
                print('Review pause notice failed:', type(e).__name__)
        return
    try:
        publish_review_item(s, item)
        if item.get('slot_key'):
            mark_slot(s, item['slot_key'])
    except Exception as e:
        print('Scheduled review publication failed:', type(e).__name__)
        item['last_publish_error_at'] = iso(now())
        retry_at = now() + timedelta(minutes=5)
        item['planned_at'] = iso(retry_at)
        if not item.get('publish_error_notified'):
            item['publish_error_notified'] = True
            for admin_id in sorted(ADMIN_USER_IDS):
                try:
                    send_dm(admin_id, f'⚠️ Не удалось опубликовать материал <code>{html.escape(item.get("id", ""))}</code>. Черновик сохранён, повторная попытка — через 5 минут.')
                except Exception as notice_error:
                    print('Review error notice failed:', type(notice_error).__name__)

def is_duplicate_news(s, title, link):
    norm = normalized_title(title)
    fp = title_fingerprint(title)
    if link in s.get('seen_links', []) or fp in s.get('seen_fingerprints', []):
        return True
    if link in s.get('rejected_news_links', []) or fp in s.get('rejected_news_fingerprints', []):
        return True
    # Catch near-identical titles from different RSS feeds.
    words = set(norm.split())
    if len(words) >= 5:
        rejected_words = [set(normalized_title(x).split()) for x in s.get('rejected_news_titles', [])[-100:]]
        for old_words in rejected_words:
            if old_words and len(words & old_words) / max(1, min(len(words), len(old_words))) >= 0.82:
                return True
        for old in s.get('seen_titles', [])[-300:]:
            old_words = set(normalized_title(old).split())
            if not old_words:
                continue
            overlap = len(words & old_words) / max(1, min(len(words), len(old_words)))
            if overlap >= 0.82:
                return True
    return False

def normalize_image_url(candidate, base_url=''):
    """Normalize an article photo URL and reject Google-served search/preview images."""
    if not isinstance(candidate, str):
        return None
    candidate = html.unescape(candidate.strip()).replace('\\/', '/')
    if not candidate:
        return None
    if candidate.startswith('//'):
        candidate = 'https:' + candidate
    candidate = urllib.parse.urljoin(base_url, candidate)
    try:
        parsed = urllib.parse.urlparse(candidate)
    except ValueError:
        return None
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    host = (parsed.hostname or '').casefold().rstrip('.')
    google_hosts = ('google.com', 'googleusercontent.com', 'gstatic.com', 'googlesyndication.com', 'googleapis.com')
    if any(host == domain or host.endswith('.' + domain) for domain in google_hosts):
        return None
    path = parsed.path.casefold()
    if path.endswith('.svg') or any(x in path for x in (
        'favicon', 'sprite', 'tracking', 'pixel.', 'placeholder', 'default-avatar',
        'site-logo', 'logo-', '/logo.', 'avatar', 'blank.gif', 'google-logo'
    )):
        return None
    return candidate

def extract_rss_image(item, article_url=''):
    """Prefer image media embedded in the source RSS item over stock illustrations."""
    candidates = []
    for node in list(item):
        tag = node.tag.rsplit('}', 1)[-1].casefold()
        raw = node.attrib.get('url') or node.attrib.get('href') or node.attrib.get('src')
        media_type = (node.attrib.get('type') or '').casefold()
        if not raw:
            continue
        is_image_type = media_type.startswith('image/')
        image_extension = bool(re.search(r'\.(?:jpe?g|png|webp|gif)(?:[?#]|$)', raw, re.I))
        if tag in ('content', 'thumbnail', 'image') and (is_image_type or image_extension):
            candidates.append(raw)
        elif tag == 'enclosure' and (is_image_type or image_extension):
            candidates.append(raw)

    raw_description = item.findtext('description') or ''
    decoded_description = html.unescape(raw_description)
    for match in re.finditer(r'<img\b[^>]*(?:src|data-src)\s*=\s*(["\'])(.*?)\1', decoded_description, re.I | re.S):
        candidates.append(match.group(2))
    for candidate in candidates:
        normalized = normalize_image_url(candidate, article_url)
        if normalized:
            return normalized
    return None


def extract_article_image(page_html, article_url=''):
    """Prefer Open Graph image, then Twitter metadata, then a substantial article image."""
    if not page_html:
        return None
    meta_candidates = {'og': [], 'twitter': [], 'other': []}
    for tag in re.findall(r'<meta\b[^>]*>', page_html, re.I | re.S):
        attrs = {
            key.casefold(): html.unescape(value)
            for key, _, value in re.findall(r'([\w:-]+)\s*=\s*(["\'])(.*?)\2', tag, re.I | re.S)
        }
        key = (attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or '').casefold()
        value = attrs.get('content') or attrs.get('value')
        if not value:
            continue
        if key in ('og:image', 'og:image:url'):
            meta_candidates['og'].append(value)
        elif key in ('twitter:image', 'twitter:image:src'):
            meta_candidates['twitter'].append(value)
        elif key == 'image':
            meta_candidates['other'].append(value)

    for priority in ('og', 'twitter', 'other'):
        for candidate in meta_candidates[priority]:
            normalized = normalize_image_url(candidate, article_url)
            if normalized:
                return normalized

    article_match = re.search(r'<article\b[^>]*>(.*?)</article>', page_html, re.I | re.S)
    area = article_match.group(1) if article_match else page_html
    for tag in re.findall(r'<img\b[^>]*>', area, re.I | re.S)[:20]:
        attrs = {
            key.casefold(): html.unescape(value)
            for key, _, value in re.findall(r'([\w:-]+)\s*=\s*(["\'])(.*?)\2', tag, re.I | re.S)
        }
        width = re.sub(r'\D', '', attrs.get('width', ''))
        height = re.sub(r'\D', '', attrs.get('height', ''))
        if width and int(width) < 250:
            continue
        if height and int(height) < 150:
            continue
        candidate = attrs.get('src') or attrs.get('data-src') or attrs.get('data-lazy-src')
        normalized = normalize_image_url(candidate, article_url)
        if normalized:
            return normalized
    return None

def split_photo_caption(text, limit=900):
    """Split text into a Telegram photo caption and a continuation without cutting markup."""
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n', (text or '').strip()) if part.strip()]
    caption_parts, remaining = [], []
    for index, paragraph in enumerate(paragraphs):
        candidate = paragraph if not caption_parts else '\n\n'.join(caption_parts + [paragraph])
        if len(candidate) <= limit:
            caption_parts.append(paragraph)
            continue
        if not caption_parts:
            words = paragraph.split()
            head, tail = '', []
            for word in words:
                trial = word if not head else head + ' ' + word
                if len(trial) <= limit:
                    head = trial
                else:
                    tail.append(word)
            if not head and words:
                head, tail = words[0][:limit], words[1:]
            if head:
                caption_parts.append(head)
            if tail:
                remaining.append(' '.join(tail))
            remaining.extend(paragraphs[index + 1:])
        else:
            remaining = paragraphs[index:]
        break
    caption = '\n\n'.join(caption_parts).strip()
    continuation = '\n\n'.join(remaining).strip()
    return caption, continuation


def commons_image(query):
    """Find a topical reusable Wikimedia Commons illustration and build attribution."""
    try:
        params = urllib.parse.urlencode({
            'action': 'query',
            'generator': 'search',
            'gsrsearch': query + ' filetype:bitmap',
            'gsrnamespace': 6,
            'gsrlimit': 8,
            'prop': 'imageinfo|info',
            'inprop': 'url',
            'iiprop': 'url|extmetadata',
            'iiurlwidth': 1000,
            'format': 'json'
        })
        data = json.loads(get_url('https://commons.wikimedia.org/w/api.php?' + params).decode('utf-8'))
        pages = (data.get('query') or {}).get('pages') or {}
        for page in pages.values():
            info = (page.get('imageinfo') or [{}])[0]
            url = info.get('thumburl') or info.get('url')
            if not url or not url.lower().split('?')[0].endswith(('.jpg', '.jpeg', '.png', '.webp')):
                continue
            metadata = info.get('extmetadata') or {}
            artist_raw = (metadata.get('Artist') or {}).get('value') or ''
            artist = clean(artist_raw)
            license_name = clean((metadata.get('LicenseShortName') or {}).get('value') or '')
            license_url = clean((metadata.get('LicenseUrl') or {}).get('value') or '')
            page_url = page.get('fullurl') or info.get('descriptionurl') or info.get('descriptionshorturl')
            # Skip files whose licence cannot be identified. CC BY variants also need an author credit.
            if not license_name or not page_url:
                continue
            license_normalized = license_name.casefold().replace('_', '-')
            license_reference = (license_url or '').casefold()
            # The channel may be monetized: reject non-commercial licences.
            if re.search(r'\bnon[ -]?commercial\b|\bnc\b|by-nc', license_normalized + ' ' + license_reference):
                continue
            if ('cc by' in license_normalized or 'cc-by' in license_normalized) and not artist:
                continue
            credits = ['📷 Иллюстрация: <a href="' + html.escape(page_url, quote=True) + '">Wikimedia Commons</a>']
            if artist:
                credits.append('автор: ' + html.escape(artist))
            if license_name:
                if license_url and license_url.startswith('https://'):
                    credits.append('лицензия: <a href="' + html.escape(license_url, quote=True) + '">' + html.escape(license_name) + '</a>')
                else:
                    credits.append('лицензия: ' + html.escape(license_name))
            return {'url': url, 'credit_html': ' · '.join(credits)}
    except Exception as e:
        print('Commons image search error:', e)
    return None


def split_html_text(text, limit=3900):
    """Split HTML-mode Telegram messages by paragraph then words, never silently truncate."""
    blocks = [p.strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]
    chunks, current = [], ''
    for block in blocks:
        pieces = [block]
        if len(block) > limit:
            words = block.split()
            pieces, piece = [], ''
            for word in words:
                candidate = word if not piece else piece + ' ' + word
                if len(candidate) > limit and piece:
                    pieces.append(piece)
                    piece = word
                else:
                    piece = candidate
            if piece:
                pieces.append(piece)
        for piece in pieces:
            candidate = piece if not current else current + '\n\n' + piece
            if len(candidate) > limit and current:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks








def post_with_image(text, image_url=None, illustration=False, image_query=None, image_file_id=None):
    """Publish a photo-led post, preferring the reviewed photo before trying a topical Vietnam image."""
    if not text or not text.strip():
        return None

    query = image_query or news_image_query('', clean(text))
    # Reuse the exact image file reviewed by an admin when possible.
    if image_file_id:
        caption_limit = 800 if 'Wikimedia Commons' in text else 950
        caption, continuation = split_photo_caption(text, limit=caption_limit)
        try:
            result = api('sendPhoto', {
                'chat_id': CHANNEL, 'photo': image_file_id,
                'caption': caption or '🇻🇳 Новости Нячанга',
                'parse_mode': 'HTML'
            })
            if continuation:
                for part in split_html_text(continuation, limit=3900):
                    post_text(part)
                    time.sleep(0.2)
            return result
        except Exception as e:
            print('Reviewed image could not be reused; trying article image/fallback:', type(e).__name__)

    candidates = []
    normalized = normalize_image_url(image_url or '')
    if normalized:
        candidates.append(normalized)
    fallback_loaded = False

    while True:
        if not candidates and not fallback_loaded:
            fallback_loaded = True
            fallback = commons_image(query)
            if fallback:
                candidates.append(fallback)
        if not candidates:
            break

        candidate = candidates.pop(0)
        photo_url = candidate.get('url') if isinstance(candidate, dict) else candidate
        photo_url = normalize_image_url(photo_url)
        if not photo_url:
            continue

        content = text
        credit = candidate.get('credit_html') if isinstance(candidate, dict) else None
        if credit and 'Wikimedia Commons' not in content:
            if FOOTER in content:
                content = content.replace(FOOTER, '\n\n' + credit + FOOTER, 1)
            else:
                content = content.rstrip() + '\n\n' + credit
        caption, continuation = split_photo_caption(content, limit=800 if credit else 950)
        if not caption:
            caption = '🇻🇳 Новости Нячанга'
        try:
            result = api('sendPhoto', {
                'chat_id': CHANNEL, 'photo': photo_url,
                'caption': caption, 'parse_mode': 'HTML'
            })
            if continuation:
                for part in split_html_text(continuation, limit=3900):
                    post_text(part)
                    time.sleep(0.2)
            return result
        except Exception as e:
            print('Photo send failed; trying topical fallback:', type(e).__name__)
            if not fallback_loaded:
                fallback_loaded = True
                fallback = commons_image(query)
                if fallback:
                    candidates.append(fallback)

    # Last-resort protection: do not drop important information if Telegram cannot fetch any image.
    print('No usable image could be sent; publishing text fallback.')
    result = None
    for part in split_html_text(text, limit=3900):
        result = post_text(part)
        time.sleep(0.2)
    return result

def translate(text):
    """Translate the whole text in bounded chunks instead of silently truncating at 4,000 chars."""
    text = clean(text)
    if not text:
        return ''
    # Keep requests modest; prefer paragraph/sentence boundaries and carry all chunks forward.
    chunks, current = [], ''
    for paragraph in re.split(r'\n+', text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentences = re.split(r'(?<=[.!?。！？])\s+', paragraph)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > 3400:
                if current:
                    chunks.append(current)
                    current = ''
                for offset in range(0, len(sentence), 3200):
                    chunks.append(sentence[offset:offset + 3200])
                continue
            candidate = sentence if not current else current + ' ' + sentence
            if len(candidate) > 3400 and current:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
    if current:
        chunks.append(current)

    translated = []
    for chunk in chunks:
        q = urllib.parse.urlencode({'client': 'gtx', 'sl': 'auto', 'tl': 'ru', 'dt': 't', 'q': chunk})
        try:
            data = json.loads(get_url('https://translate.googleapis.com/translate_a/single?' + q).decode())
            translated.append(''.join(x[0] for x in data[0] if x[0]).strip())
        except Exception as e:
            print('Translation error:', e)
            # Do not pretend that an untranslated source passage was successfully translated.
            return ''
    return '\n\n'.join(part for part in translated if part).strip()


def footer(): return '\n\n' + FOOTER

def post_text(text, disable_preview=True):
    return api('sendMessage', {'chat_id':CHANNEL,'text':text,'parse_mode':'HTML','disable_web_page_preview':str(disable_preview).lower()})


def validate_ad_draft(ad):
    """Validate Telegram caption and media-group constraints before scheduled or immediate publishing."""
    caption = ((ad.get('text') or '') + footer()).strip()
    kind = ad.get('type')
    if kind == 'album':
        count = len(ad.get('media_items', []))
        if not 2 <= count <= 10:
            raise ValueError('Альбом должен содержать от 2 до 10 фото или видео.')
        if len(caption) > 1024:
            raise ValueError('Подпись к альбому длиннее лимита Telegram 1024 символа. Сократи текст.')
    elif kind in ('photo', 'video', 'document') and len(caption) > 1024:
        raise ValueError('Подпись к медиа длиннее лимита Telegram 1024 символа. Сократи текст.')
    elif not (ad.get('text') or ad.get('file_id') or ad.get('media_items')):
        raise ValueError('Рекламный черновик пуст.')


def send_ad_content(ad):
    validate_ad_draft(ad)
    caption = (ad.get('text') or '').strip()
    if ad.get('type') == 'album':
        full_caption = (caption + footer()).strip()
        if len(full_caption) > 1024:
            raise ValueError('Подпись к альбому длиннее лимита Telegram 1024 символа. Сократи текст.')
        media = []
        for i, item in enumerate(ad.get('media_items', [])):
            element = {'type': item['type'], 'media': item['file_id']}
            item_caption = item.get('caption') or ''
            if i == 0:
                element['caption'] = full_caption
                element['parse_mode'] = 'HTML'
            elif item_caption:
                element['caption'] = html.escape(item_caption)
                element['parse_mode'] = 'HTML'
            media.append(element)
        if not 2 <= len(media) <= 10:
            raise ValueError('Альбом должен содержать от 2 до 10 фото или видео.')
        messages = api('sendMediaGroup', {'chat_id': CHANNEL, 'media': json.dumps(media, ensure_ascii=False)})
        if not messages:
            raise RuntimeError('Telegram не вернул сообщения для рекламного альбома.')
        return {'message_id': messages[0]['message_id'], 'chat': messages[0]['chat'],
                '_message_ids': [m['message_id'] for m in messages]}

    if ad.get('type') in ('photo', 'video', 'document'):
        full_caption = (caption + footer()).strip()
        if len(full_caption) > 1024:
            raise ValueError('Подпись к фото/видео/документу длиннее лимита Telegram 1024 символа. Сократи текст.')
        method = {'photo':'sendPhoto','video':'sendVideo','document':'sendDocument'}[ad['type']]
        field = {'photo':'photo','video':'video','document':'document'}[ad['type']]
        return api(method, {'chat_id':CHANNEL,field:ad['file_id'],'caption':full_caption,'parse_mode':'HTML'})
    full_text = (caption + footer()).strip()
    sent = []
    for part in split_html_text(full_text):
        sent.append(post_text(part))
        time.sleep(0.2)
    if not sent:
        raise RuntimeError('Рекламный текст оказался пустым.')
    return {
        'message_id': sent[0]['message_id'],
        'chat': sent[0]['chat'],
        '_message_ids': [message['message_id'] for message in sent]
    }


def count_today(s):
    key = now().date().isoformat()
    return int(s['daily_counts'].get(key,0))

def bump_today(s):
    key = now().date().isoformat(); s['daily_counts'][key] = int(s['daily_counts'].get(key,0))+1
    s['daily_counts'] = {k:v for k,v in s['daily_counts'].items() if k >= (now().date()-timedelta(days=3)).isoformat()}

def top_pause_until(s):
    latest = now()
    for ad in s['ads']:
        if ad.get('deleted'): continue
        try: until = parse_dt(ad['top_until'])
        except Exception: continue
        if until > latest: latest = until
    if s.get('paused') and s.get('pause_until'):
        try:
            until = parse_dt(s['pause_until'])
            if until > latest: latest = until
        except Exception: pass
    return latest

def is_paused(s): return top_pause_until(s) > now()



def capture_manual_post(msg, s, uid):
    """Save an admin-submitted news tip for review; never publish it automatically."""
    content = (msg.get('text') or msg.get('caption') or '').strip()
    post_type, file_id = 'text', None
    if msg.get('photo'):
        post_type, file_id = 'photo', msg['photo'][-1]['file_id']
    elif msg.get('video'):
        post_type, file_id = 'video', msg['video']['file_id']
    if not content:
        send_dm(uid, 'Добавь заголовок или короткое описание и по возможности ссылку. Фото/видео без пояснения не попадёт в очередь. Для отмены: /cancel')
        return
    message_id = str((msg.get('message_id') or int(time.time() * 1000)))
    item_id = f'{uid}-{message_id}'
    s.setdefault('manual_posts', []).append({
        'id': item_id,
        'status': 'pending',
        'content': content,
        'type': post_type,
        'file_id': file_id,
        'submitted_by': uid,
        'created_at': iso(now())
    })
    s['pending_manual_post'] = {}
    send_dm(
        uid,
        f'Материал добавлен в очередь проверки. ID: <code>{html.escape(item_id)}</code>.\n'
        'Посмотреть очередь: /newsqueue\n'
        'Опубликовать после проверки: /approve ID\n'
        'Отклонить: /reject ID'
    )


def split_manual_content(content):
    """Extract a separate first-line headline without repeating it in the body."""
    content = clean_multiline(content)
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
    if not paragraphs:
        return 'Предложенная новость', ''
    if len(paragraphs) > 1:
        first = paragraphs[0]
        title = first[:180].strip() or 'Предложенная новость'
        overflow = first[180:].strip()
        body_parts = ([overflow] if overflow else []) + paragraphs[1:]
        return title, '\n\n'.join(body_parts).strip()
    only = paragraphs[0]
    sentences = re.split(r'(?<=[.!?…])\s+', only, maxsplit=1)
    if len(sentences) > 1 and len(sentences[0]) <= 180:
        return sentences[0].strip(), only[len(sentences[0]):].strip()
    return 'Предложенная новость', only




def publish_manual_post(item, s, uid=None):
    """Publish an explicitly approved tip without generated headline or source URL."""
    if is_paused(s):
        raise ValueError('Публикации на паузе из-за ручной или рекламной паузы.')
    if count_today(s) >= 10:
        raise ValueError('Достигнут дневной лимит в 10 публикаций.')

    raw = clean_multiline((item.get('content') or '').strip())
    title, body = split_manual_content(raw)
    source_match = re.search(r'https?://[^\s<>]+', raw)
    source_link = source_match.group(0).rstrip('.,);]') if source_match else ''
    body = re.sub(r'(?i)(?:источник|source|ссылка на источник)\s*:\s*https?://\S+', '', body)
    body = re.sub(r'https?://\S+', '', body)
    body = clean_multiline(body)
    if not body:
        raise ValueError('В материале не осталось текста новости после удаления заголовка и ссылок. Добавь описание и повтори публикацию.')
    body_html = html.escape(body)
    result = None
    post_type = item.get('type') or 'text'
    file_id = item.get('file_id')

    if post_type == 'album' and item.get('media_items'):
        media_items = item.get('media_items') or []
        if len(media_items) < 2:
            raise ValueError('В альбоме должно быть минимум два фото или видео.')
        body_excerpt = body[:420].strip()
        caption = ((html.escape(body_excerpt) + footer()) if body_excerpt else footer()).strip()
        if len(caption) > 1024:
            raise ValueError('Подпись альбома превышает лимит Telegram 1024 символа.')
        media = []
        for media_item in media_items[:10]:
            if media_item.get('type') not in ('photo', 'video') or not media_item.get('file_id'):
                continue
            element = {'type': media_item['type'], 'media': media_item['file_id']}
            if not media:
                element['caption'] = caption
                element['parse_mode'] = 'HTML'
            media.append(element)
        if len(media) < 2:
            raise ValueError('В альбоме должно быть минимум два допустимых медиафайла.')
        album_result = api('sendMediaGroup', {'chat_id': CHANNEL, 'media': json.dumps(media, ensure_ascii=False)})
        item['published_message_ids'] = [message.get('message_id') for message in (album_result or []) if message.get('message_id')]
        result = {'message_id': item['published_message_ids'][0]} if item.get('published_message_ids') else None
        if len(body) > 420:
            for part in split_html_text(html.escape(body[420:].strip())):
                post_text(part)
                time.sleep(0.2)
    elif post_type in ('photo', 'video') and file_id:
        body_excerpt, remainder = body, ''
        if len(body) > 420:
            cut = body.rfind(' ', 0, 420)
            if cut < 250:
                cut = 420
            body_excerpt = body[:cut].strip()
            remainder = body[cut:].strip()
        caption = (html.escape(body_excerpt).strip() + footer()).strip()
        if len(caption) > 1024:
            raise ValueError('Подпись к медиа превышает лимит Telegram 1024 символа.')
        method, field = ('sendPhoto', 'photo') if post_type == 'photo' else ('sendVideo', 'video')
        result = api(method, {'chat_id': CHANNEL, field: file_id, 'caption': caption, 'parse_mode': 'HTML'})
        if remainder:
            for part in split_html_text(html.escape(remainder)):
                post_text(part)
                time.sleep(0.2)
    else:
        result = post_with_image(
            (body_html + footer()).strip(),
            image_query=news_image_query(title, body)
        )

    item['status'] = 'published'
    item['published_at'] = iso(now())
    item['published_message_id'] = (result or {}).get('message_id')
    item['title'] = title
    s['published_news'] = (s.get('published_news', []) + [{
        'title': title, 'summary': body, 'link': source_link,
        'published_at': iso(now()), 'source_published_at': iso(now()),
        'urgent': False, 'manual': True
    }])[-100:]
    if source_link:
        s['seen_links'] = (s.get('seen_links', []) + [source_link])[-1000:]
    s['seen_titles'] = (s.get('seen_titles', []) + [title])[-500:]
    s['seen_fingerprints'] = (s.get('seen_fingerprints', []) + [title_fingerprint(title)])[-1000:]
    bump_today(s)
    if uid:
        send_dm(uid, f'Материал <code>{html.escape(item["id"])}</code> опубликован в канале.')
    return result

def handle_admin_album(messages, s):
    """Capture a Telegram media group as one manual tip or one ad draft."""
    if not messages:
        return
    messages = sorted(messages, key=lambda m: int(m.get('message_id', 0)))
    first = messages[0]
    uid = str(((first.get('from') or {}).get('id', '')))
    if uid not in ADMIN_USER_IDS or str((first.get('chat') or {}).get('id', '')) != uid:
        return

    manual_pending = s.get('pending_manual_post') or {}
    if manual_pending.get('awaiting_content'):
        media_items = []
        for msg in messages:
            if msg.get('photo'):
                media_items.append({'type': 'photo', 'file_id': msg['photo'][-1]['file_id']})
            elif msg.get('video'):
                media_items.append({'type': 'video', 'file_id': msg['video']['file_id']})
        content = (first.get('caption') or '').strip()
        if not content:
            content = next(((m.get('caption') or '').strip() for m in messages if (m.get('caption') or '').strip()), '')
        if not content:
            send_dm(uid, 'Добавь заголовок или описание к альбому и отправь его снова. Для отмены: /cancel')
            return
        item_id = f'{uid}-{first.get("message_id") or int(time.time() * 1000)}'
        s.setdefault('manual_posts', []).append({
            'id': item_id, 'status': 'pending', 'content': content,
            'type': 'album', 'media_items': media_items,
            'submitted_by': uid, 'created_at': iso(now())
        })
        s['pending_manual_post'] = {}
        send_dm(uid, f'Альбом добавлен в очередь проверки. ID: <code>{html.escape(item_id)}</code>.\n'
                     'Посмотреть очередь: /newsqueue\nОпубликовать: /approve ID\nОтклонить: /reject ID')
        return

    pending = s.get('pending_ad') or {}
    if not pending.get('awaiting_content'):
        return
    media_items = []
    for msg in messages:
        if msg.get('photo'):
            media_items.append({'type': 'photo', 'file_id': msg['photo'][-1]['file_id'],
                                'caption': (msg.get('caption') or '').strip()})
        elif msg.get('video'):
            media_items.append({'type': 'video', 'file_id': msg['video']['file_id'],
                                'caption': html.escape((msg.get('caption') or '').strip())})
    if len(media_items) < 2:
        handle_admin_message(first, s)
        return
    first_caption = (first.get('caption') or '').strip()
    pending.update({
        'type': 'album',
        'media_items': media_items,
        'text': html.escape(first_caption),
        'awaiting_content': False
    })
    s['pending_ad'] = pending
    send_dm(uid, f'Черновик альбома готов: {len(media_items)} медиафайла.\n'
                 f'Проверь содержимое через /preview.\nПубликуем: /publish\n'
                 f'Отложить: /schedule ГГГГ-ММ-ДД ЧЧ:ММ\nОтменить: /cancel')



def handle_admin_message(msg, s):
    user = msg.get('from') or {}
    uid = str(user.get('id', ''))
    if uid not in ADMIN_USER_IDS:
        return
    chat_id = str((msg.get('chat') or {}).get('id', ''))
    if chat_id != uid:
        return  # Management remains private-chat only.

    text = (msg.get('text') or msg.get('caption') or '').strip()

    # Review editing is isolated from the existing advertising/manual-post draft flows.
    editing = s.get('review_editing') or {}
    if editing and editing.get('user_id') == uid:
        if editing.get('mode') == 'time':
            if text.casefold() in ('/cancelreview', '/cancel'):
                s['review_editing'] = {}
                send_dm(uid, 'Изменение времени отменено. Черновик остаётся в очереди без изменений.')
                return
            item = s.get('pending_news_review') or {}
            if not item or str(item.get('id')) != str(editing.get('id')):
                s['review_editing'] = {}
                send_dm(uid, 'Этот черновик уже опубликован или отклонён.')
                return
            try:
                if re.fullmatch(r'\d{1,2}:\d{2}', text):
                    hh, mm = map(int, text.split(':'))
                    planned = now().replace(hour=hh, minute=mm, second=0, microsecond=0)
                    if planned <= now():
                        planned += timedelta(days=1)
                else:
                    planned = datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
            except ValueError:
                send_dm(uid, 'Формат времени: <code>HH:MM</code> или <code>YYYY-MM-DD HH:MM</code>. Для отмены: /cancelreview')
                return
            if planned < now() + timedelta(minutes=5):
                send_dm(uid, 'Новое время должно быть минимум через 5 минут, чтобы бот успел подготовить публикацию.')
                return
            item['planned_at'] = iso(planned)
            item['time_edited_at'] = iso(now())
            item['time_edited_by'] = uid
            s['review_editing'] = {}
            updated = False
            for ref in item.get('review_status_messages', []):
                try:
                    api('editMessageText', {
                        'chat_id': ref['chat_id'],
                        'message_id': ref['message_id'],
                        'text': review_status_text(item),
                        'parse_mode': 'HTML',
                        'reply_markup': json.dumps(review_keyboard(item['id']), ensure_ascii=False)
                    })
                    updated = True
                except Exception as e:
                    print('Could not update review status message:', type(e).__name__)
            if not updated:
                status = send_dm(uid, review_status_text(item), review_keyboard(item['id']))
                if status and status.get('message_id'):
                    ref = {'chat_id': uid, 'message_id': status['message_id']}
                    item.setdefault('preview_messages', []).append(ref)
                    item.setdefault('review_status_messages', []).append(ref)
            send_dm(uid, f'⏰ Время публикации изменено на <b>{planned.strftime("%d.%m.%Y %H:%M")}</b> (Нячанг).')
            return
        if text.casefold() in ('/cancelreview', '/cancel'):
            s['review_editing'] = {}
            send_dm(uid, 'Редактирование отменено. Текущий черновик остаётся в очереди без изменений.')
            return
        item = s.get('pending_news_review') or {}
        if not item or str(item.get('id')) != str(editing.get('id')):
            s['review_editing'] = {}
            send_dm(uid, 'Этот черновик уже опубликован или отклонён. Новая очередь доступна в следующем предпросмотре.')
            return

        if msg.get('photo'):
            # A photo with a caption replaces both the body and the image; without a caption, keep the existing body.
            body = text or item.get('body', '')
            item['image_file_id'] = msg['photo'][-1]['file_id']
            item['image_url'] = ''
            item['image_credit_html'] = ''
        else:
            if not text or text.startswith('/'):
                send_dm(uid, 'Отправь новый текст публикации одним сообщением. Для отмены: /cancelreview')
                return
            body = text

        body = clean_multiline(re.sub(r'https?://\S+', '', body))
        if not body:
            send_dm(uid, 'После удаления ссылок текст пуст. Отправь текст публикации ещё раз или /cancelreview.')
            return

        item['body'] = body
        item['reaction_line'] = ''
        item['edited_by'] = uid
        item['edited_at'] = iso(now())
        item['review_count'] = int(item.get('review_count', 0)) + 1
        item['planned_at'] = iso(now() + timedelta(minutes=15))
        delete_review_previews(item)
        item['preview_messages'] = []
        item['review_status_messages'] = []
        s['review_editing'] = {}
        if send_review_preview(item):
            s['pending_news_review'] = item
            send_dm(uid, 'Изменения сохранены. Таймер проверки обновлён: публикация состоится через 15 минут, если материал не отклонить.')
        else:
            item['status'] = 'pending_review'
            item['planned_at'] = iso(now() + timedelta(minutes=15))
            s['pending_news_review'] = item
            send_dm(uid, 'Не удалось обновить предпросмотр с фотографией. Черновик сохранён; попробую повторно показать его при следующем запуске.')
        return

    manual_pending = s.get('pending_manual_post') or {}
    if manual_pending.get('awaiting_content') and not text.startswith('/'):
        capture_manual_post(msg, s, uid)
        return
    pending = s.get('pending_ad') or {}
    # Complete ad draft by sending the next message after /ad FORMAT.
    if pending.get('awaiting_content') and not text.startswith('/'):
        content = {'text': html.escape(text)}
        if msg.get('photo'):
            content['type']='photo'; content['file_id']=msg['photo'][-1]['file_id']; content['text']=html.escape(msg.get('caption','').strip())
        elif msg.get('video'):
            content['type']='video'; content['file_id']=msg['video']['file_id']; content['text']=html.escape(msg.get('caption','').strip())
        elif msg.get('document'):
            content['type']='document'; content['file_id']=msg['document']['file_id']; content['text']=html.escape(msg.get('caption','').strip())
        elif not text:
            send_dm(uid,'Пришли текст, фото, видео или документ с подписью. Для отмены: /cancel'); return
        pending.update(content); pending['awaiting_content']=False; s['pending_ad']=pending
        send_dm(uid, f'Черновик готов. Формат <b>{pending["format"]}</b>.\\n\\nПубликуем сейчас: /publish\\nОтложить: /schedule ГГГГ-ММ-ДД ЧЧ:ММ\\nОтменить: /cancel')
        return
    aliases = {'📰 Новости':'/newsqueue', '📥 Предложить пост':'/suggest', '📣 Добавить рекламу':'/ad', '📋 Активная реклама':'/ads', '📊 Статус':'/status', '⏸ Пауза / ▶️ Продолжить':'/togglepause'}
    if text in aliases:
        text = aliases[text]
    parts = text.split(maxsplit=1)
    cmd = parts[0].split('@')[0].lower() if parts else ''
    arg = parts[1].strip() if len(parts)>1 else ''
    if cmd in ('/start','/menu','/help'):
        send_dm(uid, '<b>Панель Нячанг Лайф</b>\\nВыбери действие кнопками ниже.\\n\\nРеклама: /ad 1/24 → отправь содержимое → /preview → /publish.\\nДругие команды: /ads, /status, /pause, /resume, /cancel.', main_menu())
    elif cmd=='/ad':
        if (s.get('pending_manual_post') or {}).get('awaiting_content'):
            send_dm(uid, 'Сначала отправь материал для очереди новостей или отмени этот черновик командой /cancel.')
            return
        if not arg:
            arg='1/24'
        m=re.fullmatch(r'(\d{1,2})\s*/\s*(\d{1,3})',arg)
        if not m:
            send_dm(uid,'Формат: <code>/ad 1/24</code> — 1 час без новостей, удаление через 24 часа.', main_menu()); return
        top_hours, life_hours=map(int,m.groups())
        if not (1<=top_hours<=72 and top_hours<=life_hours<=720):
            send_dm(uid,'Допустимо: время в топе 1–72 часа, срок жизни не меньше времени в топе и не больше 720 часов.'); return
        s['pending_ad']={'format':f'{top_hours}/{life_hours}','top_hours':top_hours,'life_hours':life_hours,'created_by':uid,'awaiting_content':True}
        send_dm(uid,f'Пришли следующим сообщением рекламный текст, фото или видео.\\nФормат: <b>{top_hours}/{life_hours}</b>.\\nПервые {top_hours} ч. бот не будет публиковать обычные новости и рубрики; реклама удалится через {life_hours} ч.\\n/cancel — отмена.', main_menu())

    elif cmd=='/preview':
        ad = s.get('pending_ad') or {}
        if not ad or ad.get('awaiting_content') or not (ad.get('text') or ad.get('file_id') or ad.get('media_items')):
            send_dm(uid, 'Сначала создай черновик через /ad 1/24 и отправь содержимое.')
            return
        preview = ((ad.get('text') or '') + footer()).strip()
        kind = ad.get('type')
        if kind == 'album':
            if len(preview) > 1024:
                send_dm(uid, 'Подпись альбома длиннее лимита Telegram 1024 символа. Сократи подпись перед предпросмотром.')
                return
            media = []
            for i, item in enumerate(ad.get('media_items', [])):
                element = {'type': item['type'], 'media': item['file_id']}
                item_caption = html.escape(item.get('caption', '') or '')
                if i == 0:
                    element['caption'] = preview
                    element['parse_mode'] = 'HTML'
                elif item_caption:
                    element['caption'] = item_caption
                    element['parse_mode'] = 'HTML'
                media.append(element)
            if len(media) >= 2:
                api('sendMediaGroup', {'chat_id': uid, 'media': json.dumps(media, ensure_ascii=False)})
            else:
                send_dm(uid, 'В альбоме должно быть минимум два фото или видео.')
        elif kind in ('photo', 'video', 'document'):
            if len(preview) > 1024:
                send_dm(uid, 'Подпись к медиа длиннее лимита Telegram 1024 символа. Сократи текст перед предпросмотром.')
                return
            method, field = {'photo': ('sendPhoto', 'photo'), 'video': ('sendVideo', 'video'), 'document': ('sendDocument', 'document')}[kind]
            api(method, {'chat_id': uid, field: ad['file_id'], 'caption': preview, 'parse_mode': 'HTML'})
        else:
            for part in split_html_text(preview):
                send_dm(uid, part)
                time.sleep(0.2)

    elif cmd=='/publish':
        ad=s.get('pending_ad') or {}
        if not ad or ad.get('awaiting_content') or not (ad.get('text') or ad.get('file_id') or ad.get('media_items')):
            send_dm(uid,'Сначала создай черновик: /ad 1/24, затем пришли содержимое.'); return
        try:
            publish_ad(ad, s, uid)
        except Exception as e:
            print('Manual ad publication failed:', type(e).__name__, e)
            send_dm(uid, f'Не удалось опубликовать рекламу: {html.escape(str(e)[:400])}. Черновик сохранён; исправь текст или попробуй ещё раз.')
    elif cmd=='/schedule':
        ad=s.get('pending_ad') or {}
        if not ad or ad.get('awaiting_content') or not (ad.get('text') or ad.get('file_id') or ad.get('media_items')):
            send_dm(uid,'Сначала подготовь черновик через /ad 1/24.'); return
        try:
            validate_ad_draft(ad)
        except ValueError as e:
            send_dm(uid, html.escape(str(e)))
            return
        try:
            when=datetime.strptime(arg,'%Y-%m-%d %H:%M').replace(tzinfo=TZ)
        except ValueError:
            send_dm(uid,'Формат: /schedule 2026-10-25 14:30'); return
        if when<=now():
            send_dm(uid,'Время должно быть в будущем.'); return
        ad['scheduled_at']=iso(when); ad['awaiting_content']=False; ad['created_by']=uid; ad['id']=str(int(time.time()))
        s['ads'].append(ad); s['pending_ad']={}
        send_dm(uid,f'Реклама запланирована на {when.strftime("%d.%m.%Y %H:%M")} по времени Нячанга. ID: <code>{ad["id"]}</code>.')
    elif cmd=='/togglepause':
        s['paused']=not s.get('paused',False); s['pause_until']=iso(now()+timedelta(days=3650)) if s['paused'] else ''
        send_dm(uid, 'Пауза включена: обычные посты остановлены.' if s['paused'] else 'Ручная пауза снята: продолжение по расписанию, если нет рекламного окна.', main_menu())
    elif cmd=='/suggest':
        if (s.get('pending_ad') or {}).get('awaiting_content'):
            send_dm(uid, 'Сначала закончи или отмени рекламный черновик командой /cancel, затем предложи новость.')
            return
        s['pending_manual_post']={'user_id':uid,'awaiting_content':True}
        send_dm(uid,'Пришли следующим сообщением текст новости, ссылку или фото/видео с подписью.\\nМатериал попадёт в очередь и не опубликуется без твоего подтверждения.\\n/cancel — отмена.', main_menu())
    elif cmd=='/newsqueue':
        pending_items = [p for p in s.get('manual_posts', []) if p.get('status', 'pending') == 'pending']
        if not pending_items:
            send_dm(uid, 'Очередь новостей пуста. Добавить материал: /suggest', main_menu())
            return
        send_dm(uid, f'<b>Очередь новостей</b> · на проверке: {len(pending_items)}', main_menu())
        for item in pending_items[-10:]:
            preview = html.escape(clean_multiline(item.get('content', ''))[:700])
            media_note = ' · есть фото/видео' if item.get('file_id') else ''
            item_id = html.escape(str(item.get('id', '?')))
            send_dm(uid, f'ID <code>{item_id}</code>{media_note}\\n\\n{preview}\\n\\n'
                         f'Опубликовать: <code>/approve {item_id}</code>\\n'
                         f'Отклонить: <code>/reject {item_id}</code>')
    elif cmd=='/approve':
        target = arg.strip()
        item = next((p for p in s.get('manual_posts', [])
                     if str(p.get('id')) == target and p.get('status', 'pending') == 'pending'), None)
        if not item:
            send_dm(uid, 'Не нашёл материал на проверке с таким ID. Посмотри /newsqueue.')
            return
        try:
            publish_manual_post(item, s, uid)
        except Exception as e:
            print('Manual post publication failed:', type(e).__name__, e)
            send_dm(uid, f'Не удалось опубликовать материал <code>{html.escape(target)}</code>: {html.escape(str(e)[:400])}')
    elif cmd=='/reject':
        target = arg.strip()
        item = next((p for p in s.get('manual_posts', [])
                     if str(p.get('id')) == target and p.get('status', 'pending') == 'pending'), None)
        if not item:
            send_dm(uid, 'Не нашёл материал на проверке с таким ID. Посмотри /newsqueue.')
            return
        item['status'] = 'rejected'
        item['reviewed_at'] = iso(now())
        item['reviewed_by'] = uid
        send_dm(uid, f'Материал <code>{html.escape(target)}</code> отклонён и не опубликован.')
    elif cmd=='/ads':
        rows=[]
        for a in s['ads'][-30:]:
            status='удалена' if a.get('deleted') else ('запланирована на '+parse_dt(a['scheduled_at']).strftime('%d.%m %H:%M') if a.get('scheduled_at') and not a.get('message_id') else 'активна')
            rows.append(f'ID <code>{a.get("id","?")}</code> · {a.get("format","?")} · {status}')
        send_dm(uid,'<b>Реклама</b>\\n'+'\\n'.join(rows) if rows else 'Реклам пока нет.', main_menu())
        for a in s['ads'][-10:]:
            if a.get('message_id') and not a.get('deleted'):
                send_dm(uid, f'Реклама ID <code>{a.get("id","?")}</code> · удаление: {parse_dt(a["expires_at"]).strftime("%d.%m %H:%M") if a.get("expires_at") else "не задано"} · закрепление можно проверить кнопкой ниже.', ad_actions(a.get('id','?')))
    elif cmd=='/editad':
        if '|' not in arg:
            send_dm(uid,'Формат: <code>/editad ID | новый текст</code>'); return
        target,new_text=arg.split('|',1); target=target.strip(); new_text=new_text.strip()
        ad=next((a for a in s['ads'] if a.get('id')==target and not a.get('deleted') and a.get('message_id')),None)
        if not ad:
            send_dm(uid,'Не нашёл активный рекламный пост с таким ID. Посмотри /ads.'); return
        new_text=html.escape(new_text)
        if ad.get('type') not in ('photo', 'video', 'document', 'album') and (
            len(ad.get('message_ids') or [ad.get('message_id')]) > 1 or len((new_text + footer()).strip()) > 3900
        ):
            old_ids = list(ad.get('message_ids') or [ad.get('message_id')])
            old_ids.extend(ad.get('stale_message_ids') or [])
            old_ids = list(dict.fromkeys(mid for mid in old_ids if mid))
            replacement = dict(ad); replacement['text'] = new_text
            try:
                result = send_ad_content(replacement)
            except Exception as e:
                send_dm(uid, f'Не удалось заменить рекламный текст: {html.escape(str(e)[:350])}')
                return
            try:
                delete_ad_message({'chat_id': ad.get('chat_id', CHANNEL), 'message_ids': old_ids})
                ad['stale_message_ids'] = []
            except Exception as e:
                ad['stale_message_ids'] = old_ids
                print('Old ad text cleanup will retry:', ad.get('id'), type(e).__name__)
            ad['message_id'] = result['message_id']; ad['message_ids'] = result.get('_message_ids') or [result['message_id']]
            ad['chat_id'] = result['chat']['id']; ad['text'] = new_text
            send_dm(uid, f'Текст рекламы {target} обновлён. Если запись была закреплена, проверь закрепление через /ads.')
            return
        if ad.get('type') in ('photo','video','document','album'):
            caption=(new_text+footer()).strip()
            if len(caption)>1024:
                send_dm(uid,'Подпись слишком длинная: максимум 1024 символа вместе с футером.'); return
            api('editMessageCaption',{'chat_id':ad.get('chat_id',CHANNEL),'message_id':ad['message_id'],'caption':caption,'parse_mode':'HTML'})
        else:
            api('editMessageText',{'chat_id':ad.get('chat_id',CHANNEL),'message_id':ad['message_id'],'text':(new_text+footer()).strip(),'parse_mode':'HTML','disable_web_page_preview':'true'})
        ad['text']=new_text; send_dm(uid,f'Текст рекламного поста {target} обновлён.')
    elif cmd in ('/cancelad','/delete'):
        target=arg.strip()
        ad=next((a for a in s['ads'] if a.get('id')==target and not a.get('deleted')),None)
        if not ad:
            send_dm(uid,'Не нашёл активную рекламу с таким ID. Посмотри /ads.'); return
        if ad.get('message_id') or ad.get('message_ids'):
            delete_ad_message(ad)
        ad['deleted']=True; ad['deleted_at']=iso(now())
        send_dm(uid,f'Реклама {target} удалена/отменена.')
    elif cmd=='/pause':
        s['paused']=True; s['pause_until']=iso(now()+timedelta(days=3650)); send_dm(uid,'Обычные публикации остановлены. Реклама и проверка сроков продолжат обрабатываться.')
    elif cmd=='/resume':
        s['paused']=False; s['pause_until']=''; send_dm(uid,'Ручная пауза снята. Бот продолжит расписание, если сейчас нет активного рекламного окна.')
    elif cmd=='/cancel':
        s['pending_ad']={}
        s['pending_manual_post']={}
        send_dm(uid,'Текущий черновик отменён.')
    elif cmd=='/status':
        until=top_pause_until(s)
        pending = s.get('pending_news_review') or {}
        review_status = f'\nЧерновик новости: {pending.get("planned_at", "на проверке")}' if pending else '\nЧерновик новости: нет'
        send_dm(uid, f'<b>Статус</b>\nВремя Нячанга: {now().strftime("%d.%m.%Y %H:%M:%S")}\nПостов сегодня: {count_today(s)}/10\nНовости: {"на паузе" if is_paused(s) else "разрешены"}\nОкно без новостей до: {until.strftime("%d.%m %H:%M") if until>now() else "нет"}\nАктивных реклам: {sum(1 for a in s["ads"] if not a.get("deleted") and a.get("message_id"))}{review_status}')
    else:
        if text.startswith('/'):
            send_dm(uid,'Неизвестная команда. Напиши /menu.')

def publish_ad(ad,s,uid=None):
    top=int(ad['top_hours']); life=int(ad['life_hours']); t=now()
    result=send_ad_content(ad)
    ad['id']=ad.get('id') or str(result['message_id'])
    ad['message_id']=result['message_id']; ad['message_ids']=result.get('_message_ids') or [result['message_id']]; ad['chat_id']=result['chat']['id']
    ad['published_at']=iso(t); ad['top_until']=iso(t+timedelta(hours=top)); ad['expires_at']=iso(t+timedelta(hours=life)); ad.pop('scheduled_at',None); ad['awaiting_content']=False
    if not any(existing is ad for existing in s['ads']):
        s['ads'].append(ad)
    s['pending_ad']={}; bump_today(s)
    ad.pop('_publish_error_notified', None)
    notify_uid = uid or ad.get('created_by')
    if notify_uid:
        try:
            send_dm(notify_uid, f'Опубликовано! ID: <code>{ad["id"]}</code>. Окно без новостей до {parse_dt(ad["top_until"]).strftime("%d.%m %H:%M")}; автоматическое удаление {parse_dt(ad["expires_at"]).strftime("%d.%m %H:%M")} (Нячанг).')
        except Exception as notify_error:
            # The ad is already published; a failed confirmation must not make us treat it as unpublished.
            print('Ad published, but owner notification failed:', type(notify_error).__name__)

def delete_ad_message(ad):
    chat_id = ad.get('chat_id', CHANNEL)
    message_ids = list(ad.get('message_ids') or ([ad['message_id']] if ad.get('message_id') else []))
    message_ids.extend(ad.get('stale_message_ids') or [])
    message_ids = list(dict.fromkeys(message_ids))
    errors = []
    for mid in message_ids:
        try: api('unpinChatMessage', {'chat_id': chat_id, 'message_id': mid})
        except Exception as e: print('Unpin skipped/failed for', mid, type(e).__name__)
        try:
            api('deleteMessage', {'chat_id': chat_id, 'message_id': mid})
        except Exception as e:
            error_text = str(e).casefold()
            # Deletion is idempotent: a media-group item that is already gone is not a failure.
            if 'message to delete not found' in error_text or 'message not found' in error_text:
                print('Ad message already absent:', mid)
                continue
            errors.append(f'{mid}: {type(e).__name__}: {e}')
    if errors:
        raise RuntimeError('; '.join(errors))

def process_ad_timers(s):
    for ad in s['ads']:
        if ad.get('deleted'):
            continue
        if ad.get('stale_message_ids'):
            try:
                delete_ad_message({
                    'chat_id': ad.get('chat_id', CHANNEL),
                    'message_ids': ad.get('stale_message_ids')
                })
                ad['stale_message_ids'] = []
            except Exception as e:
                print('Stale ad-message cleanup will retry:', ad.get('id'), type(e).__name__)
        if ad.get('scheduled_at') and not ad.get('message_id'):
            scheduled_at = parse_dt_strict(ad.get('scheduled_at'))
            if scheduled_at is None:
                if not ad.get('_invalid_schedule_logged'):
                    print('Scheduled ad has an invalid timestamp; not publishing:', ad.get('id'))
                    ad['_invalid_schedule_logged'] = True
            elif scheduled_at <= now():
                try:
                    publish_ad(ad, s)
                except Exception as e:
                    print('Scheduled ad error', ad.get('id'), type(e).__name__, e)
                    creator = ad.get('created_by')
                    if creator and not ad.get('_publish_error_notified'):
                        try:
                            send_dm(creator, f'Не удалось опубликовать запланированную рекламу <code>{html.escape(str(ad.get("id", "?")))}</code>. Бот повторит попытку: {html.escape(str(e)[:300])}')
                        except Exception as notify_error:
                            print('Could not notify ad owner:', type(notify_error).__name__)
                        ad['_publish_error_notified'] = True
        if ad.get('message_id') and ad.get('expires_at'):
            expires_at = parse_dt_strict(ad.get('expires_at'))
            if expires_at is None:
                if not ad.get('_invalid_expiry_logged'):
                    print('Ad has an invalid expiry timestamp; not deleting automatically:', ad.get('id'))
                    ad['_invalid_expiry_logged'] = True
            elif expires_at <= now():
                try:
                    delete_ad_message(ad)
                    ad['deleted'] = True
                    ad['deleted_at'] = iso(now())
                    print('Deleted expired ad', ad.get('id'))
                except Exception as e:
                    print('Ad deletion error', ad.get('id'), type(e).__name__, e)
                    creator = ad.get('created_by')
                    if creator and not ad.get('_expiry_error_notified'):
                        try:
                            send_dm(creator, f'Не удалось автоматически удалить рекламу <code>{html.escape(str(ad.get("id", "?")))}</code>. Бот продолжит повторять попытки: {html.escape(str(e)[:300])}')
                        except Exception as notify_error:
                            print('Could not notify ad owner about expiry:', type(notify_error).__name__)
                        ad['_expiry_error_notified'] = True


def handle_callback(q, s):
    user = q.get('from') or {}
    uid = str(user.get('id', ''))
    if uid not in ADMIN_USER_IDS:
        return
    data = q.get('data', '')
    msg = q.get('message') or {}
    if str((msg.get('chat') or {}).get('id', '')) != uid:
        return
    try:
        if data.startswith('review:'):
            parts = data.split(':', 2)
            action = parts[1] if len(parts) > 1 else ''
            target = parts[2] if len(parts) > 2 else ''
            item = s.get('pending_news_review') or {}
            if not item or str(item.get('id')) != target:
                send_dm(uid, 'Этот черновик уже опубликован, отклонён или заменён.')
            elif action == 'edit':
                s['review_editing'] = {'id': target, 'user_id': uid, 'started_at': iso(now()), 'mode': 'text'}
                send_dm(uid, 'Отправь следующим сообщением новый текст публикации. Если нужно заменить изображение — отправь фото с новой подписью.\n\nТаймер публикации перезапустится на 15 минут после изменения. Для отмены: /cancelreview')
            elif action == 'time':
                s['review_editing'] = {'id': target, 'user_id': uid, 'started_at': iso(now()), 'mode': 'time'}
                send_dm(uid, 'Отправь новое время в формате <code>HH:MM</code> или <code>YYYY-MM-DD HH:MM</code>. До публикации должно оставаться минимум 5 минут. Для отмены: /cancelreview')
            elif action == 'reject':
                item['status'] = 'rejected'
                item['rejected_at'] = iso(now())
                item['rejected_by'] = uid
                link = item.get('link') or ''
                original_title = item.get('source_title') or item.get('title') or ''
                if link:
                    s['rejected_news_links'] = (s.get('rejected_news_links', []) + [link])[-1000:]
                if original_title:
                    fingerprint = title_fingerprint(original_title)
                    s['rejected_news_fingerprints'] = (s.get('rejected_news_fingerprints', []) + [fingerprint])[-1000:]
                    s['rejected_news_titles'] = (s.get('rejected_news_titles', []) + [original_title])[-500:]
                delete_review_previews(item)
                s['pending_news_review'] = {}
                s['review_editing'] = {}
                send_dm(uid, '🗑 Черновик отклонён. Он не будет опубликован; бот продолжит поиск следующей новости.')
            elif action == 'publish':
                try:
                    publish_review_item(s, item)
                except Exception as e:
                    print('Immediate review publication failed:', type(e).__name__)
                    send_dm(uid, f'Не удалось опубликовать черновик. Он сохранён в очереди: {html.escape(str(e)[:250])}')
        elif data.startswith('ad_delete:'):
            target=data.split(':',1)[1]
            ad=next((a for a in s['ads'] if str(a.get('id'))==target and not a.get('deleted')),None)
            if not ad:
                send_dm(uid,'Реклама не найдена или уже удалена.',main_menu()); return
            delete_ad_message(ad); ad['deleted']=True; ad['deleted_at']=iso(now()); send_dm(uid,f'Реклама {target} удалена и откреплена, если была закреплена.',main_menu())
        elif data.startswith('ad_pin:'):
            target = data.split(':', 1)[1]
            ad = next((a for a in s['ads'] if str(a.get('id')) == target and not a.get('deleted') and a.get('message_id')), None)
            if not ad:
                send_dm(uid, 'Реклама не найдена или ещё не опубликована.', main_menu())
                return
            mid = (ad.get('message_ids') or [ad.get('message_id')])[0]
            try:
                api('pinChatMessage', {'chat_id': ad.get('chat_id', CHANNEL), 'message_id': mid, 'disable_notification': 'true'})
                send_dm(uid, f'Реклама {target} закреплена. Проверь, что у бота есть право закреплять сообщения.', main_menu())
            except Exception as e:
                print('Pin action failed:', type(e).__name__)
                send_dm(uid, 'Не удалось закрепить рекламу. Проверь права администратора бота в канале.', main_menu())
        elif data.startswith('ad_unpin:'):
            target=data.split(':',1)[1]
            ad=next((a for a in s['ads'] if str(a.get('id'))==target and not a.get('deleted')),None)
            if not ad:
                send_dm(uid,'Реклама не найдена или уже удалена.',main_menu()); return
            for mid in (ad.get('message_ids') or [ad.get('message_id')]):
                if mid:
                    try:
                        api('unpinChatMessage',{'chat_id':ad.get('chat_id',CHANNEL),'message_id':mid})
                    except Exception as e:
                        print('Unpin action:', type(e).__name__)
            send_dm(uid,f'Команда открепления выполнена для рекламы {target}.',main_menu())
    except Exception as e:
        send_dm(uid,'Не удалось выполнить действие. Проверь права бота и журнал GitHub Actions.')
        print('Callback error:', type(e).__name__)
    try:
        api('answerCallbackQuery',{'callback_query_id':q['id']})
    except Exception:
        pass

def ensure_polling_no_webhook():
    """Return whether polling is safe. Never disable a webhook or stop scheduled posts."""
    try:
        info = api('getWebhookInfo')
    except Exception as e:
        print('Could not verify webhook status; polling skipped this run:', type(e).__name__)
        return False
    webhook_url = (info or {}).get('url') or ''
    if webhook_url.strip():
        print('Webhook is configured; polling skipped and webhook left unchanged.')
        return False
    return True


def get_updates(s):
    try:
        updates = api('getUpdates', {
            'offset': int(s.get('last_update_id', 0)) + 1,
            'timeout': 0,
            'allowed_updates': json.dumps(['message', 'callback_query'])
        })
    except Exception as e:
        print('getUpdates error:', e)
        return

    albums = {}
    for update in updates:
        msg = update.get('message')
        if msg and msg.get('media_group_id'):
            albums.setdefault(str(msg['media_group_id']), []).append(msg)
    processed_albums = set()
    for update in updates:
        s['last_update_id'] = max(int(s.get('last_update_id', 0)), int(update['update_id']))
        msg = update.get('message')
        if msg:
            group_id = str(msg.get('media_group_id') or '')
            try:
                if group_id:
                    if group_id in processed_albums:
                        continue
                    processed_albums.add(group_id)
                    group = albums.get(group_id, [])
                    if len(group) >= 2:
                        handle_admin_album(group, s)
                    else:
                        handle_admin_message(msg, s)
                else:
                    handle_admin_message(msg, s)
            except Exception as e:
                print('Admin command error:', e)
        callback = update.get('callback_query')
        if callback:
            try:
                handle_callback(callback, s)
            except Exception as e:
                print('Admin callback error:', e)


def slot_once(s,key):
    day=now().date().isoformat(); return s['last_slots'].get(key)==day

def mark_slot(s,key): s['last_slots'][key]=now().date().isoformat()







def next_routine_target(current_time=None):
    """Return the next fixed daily rubric time in Nha Trang timezone."""
    current_time = current_time or now()
    targets = [(6, 0), (8, 0), (18, 0), (22, 30)]
    candidates = []
    for day_offset in (0, 1):
        day = current_time.date() + timedelta(days=day_offset)
        for hour, minute in targets:
            target = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
            if target >= current_time:
                candidates.append(target)
    return min(candidates) if candidates else None


def routine_target_within(minutes=25, current_time=None):
    """Keep new general-news previews away from fixed daily routine slots."""
    current_time = current_time or now()
    target = next_routine_target(current_time)
    return bool(target and timedelta(0) <= target - current_time <= timedelta(minutes=minutes))



def routine_posts(s):
    """Queue the 06:00 greeting and the 22:30 recap for private review on the same day."""
    if is_paused(s) or count_today(s) >= 10 or s.get('pending_news_review'):
        return
    n = now()
    slots = [
        {
            'key': 'review_morning',
            'hour': 6,
            'minute': 0,
            'kind': 'morning',
            'kind_label': 'Доброе утро',
            'image_query': 'Nha Trang Vietnam sunrise beach promenade morning light'
        },
        {
            'key': 'review_night',
            'hour': 22,
            'minute': 30,
            'kind': 'night',
            'kind_label': 'Доброй ночи',
            'image_query': 'Nha Trang Vietnam night city lights beach coastline'
        }
    ]
    for slot in slots:
        key = slot['key']
        if slot_once(s, key):
            continue
        target = n.replace(hour=slot['hour'], minute=slot['minute'], second=0, microsecond=0)
        start = target - timedelta(minutes=15)
        end = target + timedelta(minutes=10)
        if not (start <= n <= end):
            continue

        if slot['kind'] == 'morning':
            messages = [
                '☀️ Доброе утро, Нячанг! Уже проснулись?\\n\\n👍 — уже на ногах\\n❤️ — проснулся, но ещё не включился\\n🙏 — пусть день будет хорошим.',
                '☀️ Доброе утро! Какой план на сегодня?\\n\\n👍 — работа и дела\\n❤️ — море и прогулка\\n🙏 — пока хочу ещё немного поспать.',
                '☀️ Доброе утро, Нячанг! Как настроение?\\n\\n❤️ — всё отлично\\n👍 — постепенно просыпаюсь\\n🙏 — нужен спокойный день.'
            ]
            body = messages[int(n.strftime('%j')) % len(messages)]
        else:
            stories = []
            today_items = [
                item for item in s.get('published_news', [])
                if str(item.get('published_at', '')).startswith(n.date().isoformat())
                and item.get('summary')
            ]
            for entry in today_items[-3:]:
                summary = clean_multiline(entry.get('summary', ''))
                if not summary:
                    continue
                sentence = re.split(r'(?<=[.!?])\\s+', summary.replace('\\n', ' '), maxsplit=1)[0]
                if len(sentence) > 240:
                    sentence = sentence[:237].rsplit(' ', 1)[0] + '…'
                if sentence and sentence not in stories:
                    stories.append(sentence)
            parts = ['🌙 Доброй ночи, Нячанг!']
            if stories:
                parts.append('Что важного произошло сегодня: ' + ' '.join(stories))
            tomorrow_warning = ''
            try:
                url = ('https://api.open-meteo.com/v1/forecast?latitude=12.2388&longitude=109.1967'
                       '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code'
                       '&timezone=Asia%2FHo_Chi_Minh&forecast_days=2')
                forecast = json.loads(get_url(url).decode('utf-8'))
                daily = forecast['daily']
                code = int(daily['weather_code'][1])
                rain_values = daily.get('precipitation_probability_max', [None, None])
                rain = rain_values[1] if len(rain_values) > 1 else None
                names = {
                    0:'ясно',1:'преимущественно ясно',2:'переменная облачность',3:'облачно',
                    45:'туман',48:'туман',51:'морось',53:'морось',55:'сильная морось',
                    61:'небольшой дождь',63:'дождь',65:'сильный дождь',80:'кратковременный дождь',
                    81:'ливни',82:'сильные ливни',95:'гроза',96:'гроза с градом',99:'сильная гроза с градом'
                }
                if (rain is not None and rain >= 60) or code in (65, 81, 82, 95, 96, 99):
                    rain_text = f'вероятность осадков — {rain}%' if rain is not None else 'вероятность осадков не указана'
                    tomorrow_warning = (
                        f'На завтра прогнозируют {names.get(code, "переменную погоду")}; {rain_text}. '
                        'Учитывай это при планировании поездок.'
                    )
            except Exception as e:
                print('Night forecast unavailable; no warning inferred:', type(e).__name__)
            if tomorrow_warning:
                parts.append(tomorrow_warning)
            parts.append('Перед сном выбери реакцию: ❤️ — день удался; 🙏 — завтра хочется спокойствия; 🤣 — сегодня было над чем посмеяться.')
            body = '\\n\\n'.join(parts)

        queued = queue_review_item(s, {
            'kind': slot['kind'],
            'kind_label': slot['kind_label'],
            'title': slot['kind_label'],
            'body': body,
            'reaction_line': '',
            'image_query': slot['image_query'],
            'urgent': False
        }, planned_at=target, slot_key=key)
        if queued:
            mark_slot(s, key)
        return



def weather_post(s, period):
    """Prepare the current or next-day forecast for private review 15 minutes before the slot."""
    key = 'weather_' + period
    if slot_once(s, key) or count_today(s) >= 10 or is_paused(s) or s.get('pending_news_review'):
        return
    n = now()
    if period == 'morning':
        target = n.replace(hour=8, minute=0, second=0, microsecond=0)
        image_query = 'Nha Trang Vietnam morning weather coastal city daylight'
    elif period == 'evening':
        target = n.replace(hour=18, minute=0, second=0, microsecond=0)
        image_query = 'Nha Trang Vietnam coastal city evening weather seaside'
    else:
        return
    if not (target - timedelta(minutes=15) <= n <= target + timedelta(minutes=10)):
        return

    url = ('https://api.open-meteo.com/v1/forecast?latitude=12.2388&longitude=109.1967'
           '&current=temperature_2m,apparent_temperature,weather_code'
           '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code'
           '&timezone=Asia%2FHo_Chi_Minh&forecast_days=2')
    try:
        data = json.loads(get_url(url).decode('utf-8'))
        cur, daily = data['current'], data['daily']
        index = 0 if period == 'morning' else 1
        icons = {0:'☀️',1:'🌤️',2:'⛅',3:'☁️',45:'🌫️',48:'🌫️',51:'🌦️',53:'🌦️',55:'🌧️',
                 61:'🌧️',63:'🌧️',65:'🌧️',80:'🌦️',81:'🌧️',82:'⛈️',95:'⛈️',96:'⛈️',99:'⛈️'}
        names = {0:'ясно',1:'преимущественно ясно',2:'переменная облачность',3:'облачно',
                 45:'туман',48:'туман',51:'морось',53:'морось',55:'сильная морось',
                 61:'небольшой дождь',63:'дождь',65:'сильный дождь',80:'кратковременный дождь',
                 81:'ливни',82:'сильные ливни',95:'гроза',96:'гроза с градом',99:'сильная гроза с градом'}
        code, current_code = int(daily['weather_code'][index]), int(cur['weather_code'])
        rain_values = daily.get('precipitation_probability_max', [None, None])
        rain = rain_values[index] if len(rain_values) > index else None

        current_text = (
            f'Сейчас в Нячанге {round(cur["temperature_2m"])}°C, '
            f'{names.get(current_code, "переменная погода")} {icons.get(current_code, "🌤️")}; '
            f'по ощущениям {round(cur["apparent_temperature"])}°C.'
        )
        if period == 'morning':
            forecast_text = (
                f'Сегодня ожидается {names.get(code, "переменная погода")} {icons.get(code, "🌤️")}; '
                f'температура {round(daily["temperature_2m_min"][index])}–{round(daily["temperature_2m_max"][index])}°C.'
            )
        else:
            forecast_text = (
                f'Завтра ожидается {names.get(code, "переменная погода")} {icons.get(code, "🌤️")}; '
                f'температура {round(daily["temperature_2m_min"][index])}–{round(daily["temperature_2m_max"][index])}°C.'
            )
        rain_text = f'Вероятность осадков — {rain}%.' if rain is not None else 'Вероятность осадков сейчас недоступна.'
        if rain is not None and rain >= 60:
            advice = 'Вероятность дождя высокая — учитывай это при поездках и планах на улице.'
            image_query = 'Nha Trang Vietnam heavy rain wet streets tropical downpour'
        elif rain is not None and rain >= 35:
            advice = 'Возможен дождь, поэтому проверь прогноз перед выходом.'
            image_query = 'Vietnam Nha Trang rainy weather wet street tropical rain'
        elif code in (82, 95, 96, 99):
            advice = 'В прогнозе есть гроза. Следи за обновлениями погоды перед выходом.'
            image_query = 'Vietnam Nha Trang dark storm clouds coastal city rain'
        else:
            advice = 'Перед длительной поездкой проверь прогноз ещё раз.'

        parts = [current_text, forecast_text, rain_text, advice]
        if period == 'morning':
            cutoff = n - timedelta(hours=24)
            recent = []
            for entry in s.get('published_news', []):
                try:
                    published_at = datetime.fromisoformat(entry.get('published_at', '')).astimezone(TZ)
                except (TypeError, ValueError):
                    continue
                if cutoff <= published_at <= n and entry.get('summary'):
                    recent.append(entry)
            snippets = []
            for entry in recent[-3:]:
                summary = clean_multiline(entry.get('summary', ''))
                first_sentence = re.split(r'(?<=[.!?])\s+', summary.replace('\n', ' '), maxsplit=1)[0]
                if len(first_sentence) > 220:
                    first_sentence = first_sentence[:217].rsplit(' ', 1)[0] + '…'
                if first_sentence and first_sentence not in snippets:
                    snippets.append(first_sentence)
            if snippets:
                parts.append('За последние сутки: ' + ' '.join(snippets))
        parts.append('👍 — прогноз полезен; ❤️ — следим за погодой вместе.')
        body = '\n\n'.join(parts)

        queued = queue_review_item(s, {
            'kind': 'weather_' + period,
            'kind_label': 'Погода на сегодня' if period == 'morning' else 'Погода на завтра',
            'title': 'Погода в Нячанге',
            'body': body,
            'reaction_line': '',
            'image_query': image_query,
            'urgent': False
        }, planned_at=target, slot_key=key)
        if queued:
            mark_slot(s, key)
    except Exception as e:
        print('Weather error; no unverified weather message was queued:', type(e).__name__)

def publish_news(s):
    """Find the next useful local story and queue it for a 15-minute private review."""
    if s.get('pending_news_review'):
        return
    n = now()
    hour_key = n.strftime('%Y-%m-%d-%H')
    if routine_target_within(25, n):
        # Leave space for the fixed morning greeting, weather and night routines.
        return

    items, links = [], set()
    for feed in FEEDS:
        try:
            root = ET.fromstring(get_url(feed))
            for item in root.findall('.//item'):
                title = clean(item.findtext('title'))
                desc = clean(item.findtext('description'))
                link = (item.findtext('link') or '').strip()
                source_published_at = parse_source_date(item.findtext('pubDate'))
                if not title or not link or not re.match(r'^https?://', link, re.I) or link in links:
                    continue
                links.add(link)
                if not is_relevant_news(title, desc):
                    continue
                urgent_candidate = is_urgent_news(title + ' ' + desc)
                if not is_recent_source_date(source_published_at, n, urgent=urgent_candidate):
                    print('Skipping missing, future-dated, or stale RSS story:', title)
                    continue
                items.append((
                    title, desc, link, urgent_candidate, source_published_at,
                    extract_rss_image(item, link)
                ))
        except Exception as e:
            print('Feed error:', type(e).__name__)

    items.sort(key=lambda item: not item[3])
    for title, desc, link, urgent_candidate, source_published_at, rss_image in items:
        if is_duplicate_news(s, title, link):
            continue
        if is_paused(s) and not urgent_candidate:
            continue
        if count_today(s) >= 10 and not urgent_candidate:
            return
        if s.get('last_news_hour') == hour_key and not urgent_candidate:
            return
        if n.hour in (6, 7, 8, 11, 12, 17, 18, 22, 23) and not urgent_candidate:
            continue
        if routine_target_within(25, n) and not urgent_candidate:
            continue

        translated_title = translate(re.split(r'\s[-–|]\s', title, maxsplit=1)[0])
        if not translated_title:
            continue
        internal_title = clean(translated_title)
        translated_body = translate(desc) if desc else ''

        article_page = ''
        article_page_url = link
        article_image = None
        try:
            # Google News links often wrap a publisher URL. Follow redirects and inspect the final article page.
            article_page_url, page_bytes = get_url_with_final_url(link)
            article_page = page_bytes.decode('utf-8', 'ignore')
            article_image = extract_article_image(article_page, article_page_url)
        except Exception as e:
            print('Source page unavailable; try RSS image/fallback:', type(e).__name__)

        # Never use Google-hosted search/result thumbnails as the public news photo.
        image_url = normalize_image_url(article_image or rss_image or '', article_page_url) or ''

        if len(translated_body) < 180 or len(translated_body.split()) < 24:
            try:
                article_match = re.search(r'<article\b[^>]*>(.*?)</article>', article_page, re.I | re.S) if article_page else None
                if article_match:
                    raw_article = article_match.group(1)
                else:
                    paragraphs = re.findall(r'<p\b[^>]*>(.*?)</p>', article_page, re.I | re.S) if article_page else []
                    raw_article = '\n'.join(paragraphs[:20])
                raw_article = re.sub(r'<(script|style|noscript|svg)\b[^>]*>.*?</\1>', ' ', raw_article, flags=re.I | re.S)
                raw_article = re.sub(r'</?(p|div|br|li|h[1-6])\b[^>]*>', '\n', raw_article, flags=re.I)
                raw_article = clean_multiline(raw_article)
                if len(raw_article) > len(desc) + 100:
                    translated_body = translate(raw_article[:12000])
            except Exception as e:
                print('Full article unavailable; relying on RSS description only:', type(e).__name__)

        body = remove_repeated_lead(internal_title, translated_body)
        body = clean_multiline(re.sub(r'https?://\S+', '', body))
        body = limit_news_body(body, max_chars=1450)
        if len(body) < 100 or len(body.split()) < 18:
            print('Skipping thin or repetitive story:', internal_title)
            continue

        urgent = is_urgent_news(title + ' ' + desc + ' ' + internal_title + ' ' + body)
        selected_query = news_image_query(internal_title, body)
        item = {
            'kind': 'news',
            'kind_label': 'Важная новость' if urgent else 'Новость Нячанга',
            'title': internal_title,
            'source_title': title,
            'body': body,
            'reaction_line': reaction_line(title + ' ' + internal_title + ' ' + body),
            'link': link,
            'source_published_at': iso(source_published_at),
            'urgent': bool(urgent),
            'image_url': image_url,
            'image_query': selected_query
        }

        if urgent:
            # Do not delay immediate safety warnings behind an editorial timer.
            try:
                result = post_with_image(
                    review_post_text(item),
                    image_url=item.get('image_url') or None,
                    image_query=selected_query
                )
                if not result:
                    raise RuntimeError('Telegram did not confirm the safety alert post')
                s['seen_links'] = (s.get('seen_links', []) + [link])[-1000:]
                s['seen_titles'] = (s.get('seen_titles', []) + [title])[-500:]
                s['seen_fingerprints'] = (s.get('seen_fingerprints', []) + [title_fingerprint(title)])[-1000:]
                s['published_news'] = (s.get('published_news', []) + [{
                    'title': internal_title, 'summary': body, 'link': link,
                    'published_at': iso(now()), 'source_published_at': iso(source_published_at),
                    'urgent': True
                }])[-100:]
                bump_today(s)
                s['last_news_hour'] = hour_key
                print('Published urgent safety news immediately with image.')
                return
            except Exception as e:
                print('Urgent safety news failed; keep candidate available for retry:', type(e).__name__)
                continue

        queued = queue_review_item(s, item)
        if queued:
            return
        # No preview/photo means this item is not treated as reviewed or published. Try the next source story.


def main():
    s = load_state()
    if ensure_polling_no_webhook():
        get_updates(s)
    else:
        print('Admin polling skipped; scheduled content and review timers will still run.')
    process_ad_timers(s)
    process_news_review(s)
    if not is_paused(s):
        routine_posts(s)
        weather_post(s, 'morning')
        weather_post(s, 'evening')
    # Screen stories even during quiet windows so real safety alerts can still bypass review/pause.
    publish_news(s)
    save_state(s)
    pending = s.get('pending_news_review') or {}
    print(
        'Finished:', now().isoformat(),
        'posts_today=', count_today(s),
        'paused=', is_paused(s),
        'review_pending=', bool(pending),
        'review_due=', pending.get('planned_at', '')
    )


if __name__=='__main__':
    main()
