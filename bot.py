import os, re, json, html, time, hashlib, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
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
 'https://news.google.com/rss/search?q=site%3Akhituongvietnam.gov.vn+Kh%C3%A1nh+H%C3%B2a&hl=vi&gl=VN&ceid=VN:vi',
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

def get_url(url):
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 NhaTrangLifeNewsBot/2.0'})
    with urllib.request.urlopen(req, timeout=12) as r: return r.read()

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
      'game_history': [], 'last_news_hour': '', 'manual_posts': [], 'published_news': []
    }
    for k, v in defaults.items():
        s.setdefault(k, v)
    for key in ('seen_links', 'seen_titles', 'seen_fingerprints', 'ads', 'published_news', 'game_history', 'manual_posts'):
        if not isinstance(s.get(key), list):
            raise RuntimeError(
                f'Поле {key} в {STATE_FILE} имеет неверный формат; файл не будет сохранён, чтобы не потерять данные.'
            )
    for key in ('daily_counts', 'last_slots', 'pending_ad'):
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


def reaction_line(text):
    """Optional topic-specific reaction line; serious stories deliberately get none."""
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
        return '👍 — полезная информация для жизни в Нячанге.'
    return ''


def news_image_query(title, body=''):
    """Choose a topic-matched illustration query instead of defaulting to a beach."""
    value = clean(title + ' ' + body).casefold()
    local = 'Nha Trang Khanh Hoa Vietnam'
    if any(x in value for x in ('bão', 'mưa', 'ngập', 'lũ', 'thời tiết', 'storm', 'flood')):
        return 'Vietnam tropical storm heavy rain flooded street landscape'
    if any(x in value for x in ('trường học', 'học sinh', 'giáo dục', 'đại học')):
        return 'Vietnamese school students classroom education'
    if any(x in value for x in ('bệnh viện', 'bác sĩ', 'phòng khám', 'nhà thuốc', 'y tế')):
        return 'Vietnam hospital doctor healthcare clinic'
    if any(x in value for x in ('giao thông', 'đường', 'cầu', 'xe buýt', 'sân bay', 'tai nạn', 'cấm đường')):
        return 'Nha Trang Vietnam road traffic public transport'
    if any(x in value for x in ('nước biển', 'bãi biển', 'ô nhiễm', 'môi trường', 'rác thải')):
        return 'Nha Trang Vietnam beach coastline environment'
    if any(x in value for x in ('nhà ở', 'giá thuê', 'giá nhà', 'bất động sản', 'căn hộ')):
        return 'Nha Trang Vietnam apartment buildings housing'
    if any(x in value for x in ('visa', 'thị thực', 'xuất nhập cảnh', 'lưu trú', 'người nước ngoài')):
        return 'Vietnam immigration passport paperwork government office'
    if any(x in value for x in ('điện', 'mất nước', 'internet', 'dịch vụ công', 'hạ tầng')):
        return 'Nha Trang Vietnam urban infrastructure utilities'
    return local + ' city street public services'


def is_duplicate_news(s, title, link):
    norm = normalized_title(title)
    fp = title_fingerprint(title)
    if link in s.get('seen_links', []) or fp in s.get('seen_fingerprints', []):
        return True
    # Catch near-identical titles from different RSS feeds.
    words = set(norm.split())
    if len(words) >= 5:
        for old in s.get('seen_titles', [])[-300:]:
            old_words = set(normalized_title(old).split())
            if not old_words: continue
            overlap = len(words & old_words) / max(1, min(len(words), len(old_words)))
            if overlap >= 0.82:
                return True
    return False


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
            if ('cc by' in license_name.casefold() or 'cc-by' in license_name.casefold()) and not artist:
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



def post_with_image(text, image_url=None, illustration=False):
    """Always pair a topical image with a short caption and preserve the complete article text."""
    if not text.strip():
        return None
    if isinstance(image_url, dict):
        image_info = image_url
        image_url = image_info.get('url')
        attribution = image_info.get('credit_html')
        if attribution:
            if FOOTER in text:
                text = text.replace(FOOTER, '\n\n' + attribution + FOOTER, 1)
            else:
                text += '\n\n' + attribution
        illustration = not bool(attribution)

    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]
    caption_parts = []
    remaining = []
    caption_limit = 850

    for index, paragraph in enumerate(paragraphs):
        candidate = paragraph if not caption_parts else '\n\n'.join(caption_parts + [paragraph])
        if len(candidate) <= caption_limit:
            caption_parts.append(paragraph)
        else:
            remaining = paragraphs[index:]
            break

    if not caption_parts and paragraphs:
        # Headlines are expected to fit; safeguard unusual long first blocks without dropping text.
        words = paragraphs[0].split()
        caption, tail = '', []
        for word in words:
            candidate = word if not caption else caption + ' ' + word
            if len(candidate) <= caption_limit:
                caption = candidate
            else:
                tail.append(word)
        caption_parts = [caption] if caption else []
        remaining = ([' '.join(tail)] if tail else []) + paragraphs[1:]

    caption = '\n\n'.join(caption_parts)
    full_body = '\n\n'.join(remaining)
    if illustration:
        label = '📷 Изображение — иллюстрация'
        if len(caption) + len(label) + 2 <= 1024:
            caption = (caption + '\n\n' + label).strip()
        else:
            full_body = (label + ('\n\n' + full_body if full_body else '')).strip()

    result = None
    photo_sent = False
    if image_url and caption and len(caption) <= 1024:
        try:
            result = api('sendPhoto', {
                'chat_id': CHANNEL, 'photo': image_url,
                'caption': caption, 'parse_mode': 'HTML'
            })
            photo_sent = True
        except Exception as e:
            print('Photo send failed; falling back to full text:', e)

    # If a picture cannot be used, include the title in the text fallback instead.
    text_parts = split_html_text(full_body) if photo_sent else split_html_text(text)
    for part in text_parts:
        result = post_text(part)
        time.sleep(0.25)
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


def send_ad_content(ad):
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
                element['caption'] = item_caption
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
    return post_text((caption + footer()).strip())


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


def handle_admin_album(messages, s):
    """Treat a Telegram media group as one ad draft, retaining every media file ID."""
    if not messages:
        return
    messages = sorted(messages, key=lambda m: int(m.get('message_id', 0)))
    first = messages[0]
    uid = str(((first.get('from') or {}).get('id', '')))
    if uid not in ADMIN_USER_IDS or str((first.get('chat') or {}).get('id', '')) != uid:
        return
    pending = s.get('pending_ad') or {}
    if not pending.get('awaiting_content'):
        return
    media_items = []
    for msg in messages:
        if msg.get('photo'):
            media_items.append({'type': 'photo', 'file_id': msg['photo'][-1]['file_id'],
                                'caption': html.escape((msg.get('caption') or '').strip())})
        elif msg.get('video'):
            media_items.append({'type': 'video', 'file_id': msg['video']['file_id'],
                                'caption': html.escape((msg.get('caption') or '').strip())})
    if len(media_items) < 2:
        # A one-item media group is handled by the normal message path.
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


def handle_admin_message(msg,s):
    user = msg.get('from') or {}; uid = str(user.get('id',''))
    if uid not in ADMIN_USER_IDS: return
    chat_id = str((msg.get('chat') or {}).get('id',''))
    if chat_id != uid: return  # management is private-chat only
    text = (msg.get('text') or msg.get('caption') or '').strip()
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
        send_dm(uid, f'Черновик готов. Формат <b>{pending["format"]}</b>.\n\nПубликуем сейчас: /publish\nОтложить: /schedule ГГГГ-ММ-ДД ЧЧ:ММ\nОтменить: /cancel')
        return
    aliases = {'📰 Новости':'/newsqueue', '📥 Предложить пост':'/suggest', '📣 Добавить рекламу':'/ad', '📋 Активная реклама':'/ads', '📊 Статус':'/status', '⏸ Пауза / ▶️ Продолжить':'/togglepause'}
    if text in aliases: text = aliases[text]
    parts=text.split(maxsplit=1); cmd=parts[0].split('@')[0].lower() if parts else ''
    arg=parts[1].strip() if len(parts)>1 else ''
    if cmd in ('/start','/menu','/help'):
        send_dm(uid, '<b>Панель Нячанг Лайф</b>\nВыбери действие кнопками ниже.\n\nРеклама: /ad 1/24 → отправь содержимое → /preview → /publish.\nДругие команды: /ads, /status, /pause, /resume, /cancel.', main_menu())
    elif cmd=='/ad':
        if not arg: arg='1/24'
        m=re.fullmatch(r'(\d{1,2})\s*/\s*(\d{1,3})',arg)
        if not m: send_dm(uid,'Формат: <code>/ad 1/24</code> — 1 час без новостей, удаление через 24 часа.', main_menu()); return
        top_hours, life_hours=map(int,m.groups())
        if not (1<=top_hours<=72 and top_hours<=life_hours<=720): send_dm(uid,'Допустимо: время в топе 1–72 часа, срок жизни не меньше времени в топе и не больше 720 часов.'); return
        s['pending_ad']={'format':f'{top_hours}/{life_hours}','top_hours':top_hours,'life_hours':life_hours,'awaiting_content':True}
        send_dm(uid,f'Пришли следующим сообщением рекламный текст, фото или видео.\nФормат: <b>{top_hours}/{life_hours}</b>.\nПервые {top_hours} ч. бот не будет публиковать обычные новости и рубрики; реклама удалится через {life_hours} ч.\n/cancel — отмена.', main_menu())

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
            method, field = {
                'photo': ('sendPhoto', 'photo'),
                'video': ('sendVideo', 'video'),
                'document': ('sendDocument', 'document')
            }[kind]
            api(method, {'chat_id': uid, field: ad['file_id'], 'caption': preview, 'parse_mode': 'HTML'})
        else:
            for part in split_html_text(preview):
                send_dm(uid, part)
                time.sleep(0.2)

    elif cmd=='/publish':
        ad=s.get('pending_ad') or {}
        if not ad or ad.get('awaiting_content') or not (ad.get('text') or ad.get('file_id') or ad.get('media_items')):
            send_dm(uid,'Сначала создай черновик: /ad 1/24, затем пришли содержимое.'); return
        publish_ad(ad,s,uid)
    elif cmd=='/schedule':
        ad=s.get('pending_ad') or {}
        if not ad or ad.get('awaiting_content') or not (ad.get('text') or ad.get('file_id') or ad.get('media_items')):
            send_dm(uid,'Сначала подготовь черновик через /ad 1/24.'); return
        try: when=datetime.strptime(arg,'%Y-%m-%d %H:%M').replace(tzinfo=TZ)
        except ValueError: send_dm(uid,'Формат: /schedule 2026-10-25 14:30'); return
        if when<=now(): send_dm(uid,'Время должно быть в будущем.'); return
        ad['scheduled_at']=iso(when); ad['awaiting_content']=False; ad['id']=str(int(time.time()))
        s['ads'].append(ad); s['pending_ad']={}
        send_dm(uid,f'Реклама запланирована на {when.strftime("%d.%m.%Y %H:%M")} по времени Нячанга. ID: <code>{ad["id"]}</code>.')
    elif cmd=='/togglepause':
        s['paused']=not s.get('paused',False); s['pause_until']=iso(now()+timedelta(days=3650)) if s['paused'] else ''
        send_dm(uid, 'Пауза включена: обычные посты остановлены.' if s['paused'] else 'Ручная пауза снята. Рекламная пауза, если действует, сохранится.', main_menu())
    elif cmd=='/suggest':
        s['pending_manual_post']={'user_id':uid,'awaiting_content':True}
        send_dm(uid,'Пришли текст новости, ссылку и при необходимости фото/видео. Это пока только черновик; публикация появится после отдельного этапа проверки.', main_menu())
    elif cmd=='/newsqueue':
        send_dm(uid,'Очередь проверки новостей пока будет добавлена следующим этапом. Сейчас можно управлять рекламой и статусом кнопками.', main_menu())
    elif cmd=='/ads':
        rows=[]
        for a in s['ads'][-30:]:
            status='удалена' if a.get('deleted') else ('запланирована на '+parse_dt(a['scheduled_at']).strftime('%d.%m %H:%M') if a.get('scheduled_at') and not a.get('message_id') else 'активна')
            rows.append(f'ID <code>{a.get("id","?")}</code> · {a.get("format","?")} · {status}')
        send_dm(uid,'<b>Реклама</b>\n'+'\n'.join(rows) if rows else 'Реклам пока нет.', main_menu())
        for a in s['ads'][-10:]:
            if a.get('message_id') and not a.get('deleted'):
                send_dm(uid, f'Реклама ID <code>{a.get("id","?")}</code> · удаление: {parse_dt(a["expires_at"]).strftime("%d.%m %H:%M") if a.get("expires_at") else "не задано"} · закрепление можно проверить кнопкой ниже.', ad_actions(a.get('id','?')))
    elif cmd=='/editad':
        if '|' not in arg: send_dm(uid,'Формат: <code>/editad ID | новый текст</code>'); return
        target,new_text=arg.split('|',1); target=target.strip(); new_text=new_text.strip()
        ad=next((a for a in s['ads'] if a.get('id')==target and not a.get('deleted') and a.get('message_id')),None)
        if not ad: send_dm(uid,'Не нашёл активный рекламный пост с таким ID. Посмотри /ads.'); return
        new_text=html.escape(new_text)
        if ad.get('type') in ('photo','video','document','album'):
            caption=(new_text+footer()).strip()
            if len(caption)>1024: send_dm(uid,'Подпись слишком длинная: максимум 1024 символа вместе с футером.'); return
            api('editMessageCaption',{'chat_id':ad.get('chat_id',CHANNEL),'message_id':ad['message_id'],'caption':caption,'parse_mode':'HTML'})
        else:
            api('editMessageText',{'chat_id':ad.get('chat_id',CHANNEL),'message_id':ad['message_id'],'text':(new_text+footer()).strip(),'parse_mode':'HTML','disable_web_page_preview':'true'})
        ad['text']=new_text; send_dm(uid,f'Текст рекламного поста {target} обновлён.')
    elif cmd in ('/cancelad','/delete'):
        target=arg.strip()
        ad=next((a for a in s['ads'] if a.get('id')==target and not a.get('deleted')),None)
        if not ad: send_dm(uid,'Не нашёл активную рекламу с таким ID. Посмотри /ads.'); return
        if ad.get('message_id') or ad.get('message_ids'):
            delete_ad_message(ad)
        ad['deleted']=True; ad['deleted_at']=iso(now())
        send_dm(uid,f'Реклама {target} удалена/отменена.')
    elif cmd=='/pause':
        s['paused']=True; s['pause_until']=iso(now()+timedelta(days=3650)); send_dm(uid,'Обычные публикации остановлены. Реклама и проверка сроков продолжат обрабатываться.')
    elif cmd=='/resume':
        s['paused']=False; s['pause_until']=''; send_dm(uid,'Ручная пауза снята. Бот продолжит расписание, если сейчас нет активного рекламного окна.')
    elif cmd=='/cancel':
        s['pending_ad']={}; send_dm(uid,'Черновик отменён.')
    elif cmd=='/status':
        until=top_pause_until(s)
        send_dm(uid, f'<b>Статус</b>\nВремя Нячанга: {now().strftime("%d.%m.%Y %H:%M:%S")}\nПостов сегодня: {count_today(s)}/10\nНовости: {"на паузе" if is_paused(s) else "разрешены"}\nОкно без новостей до: {until.strftime("%d.%m %H:%M") if until>now() else "нет"}\nАктивных реклам: {sum(1 for a in s["ads"] if not a.get("deleted") and a.get("message_id"))}')
    else:
        # Other admin messages are only treated as ad content when draft is awaiting it.
        if text.startswith('/'): send_dm(uid,'Неизвестная команда. Напиши /menu.')

def publish_ad(ad,s,uid=None):
    top=int(ad['top_hours']); life=int(ad['life_hours']); t=now()
    result=send_ad_content(ad)
    ad['id']=ad.get('id') or str(result['message_id'])
    ad['message_id']=result['message_id']; ad['message_ids']=result.get('_message_ids') or [result['message_id']]; ad['chat_id']=result['chat']['id']
    ad['published_at']=iso(t); ad['top_until']=iso(t+timedelta(hours=top)); ad['expires_at']=iso(t+timedelta(hours=life)); ad.pop('scheduled_at',None); ad['awaiting_content']=False
    if not any(existing is ad for existing in s['ads']):
        s['ads'].append(ad)
    s['pending_ad']={}; bump_today(s)
    if uid: send_dm(uid,f'Опубликовано! ID: <code>{ad["id"]}</code>. Окно без новостей до {parse_dt(ad["top_until"]).strftime("%d.%m %H:%M")}; автоматическое удаление {parse_dt(ad["expires_at"]).strftime("%d.%m %H:%M")} (Нячанг).')

def delete_ad_message(ad):
    chat_id = ad.get('chat_id', CHANNEL)
    message_ids = ad.get('message_ids') or ([ad['message_id']] if ad.get('message_id') else [])
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

def handle_callback(q,s):
    user=q.get('from') or {}; uid=str(user.get('id',''))
    if uid not in ADMIN_USER_IDS: return
    data=q.get('data',''); msg=q.get('message') or {}
    if str((msg.get('chat') or {}).get('id','')) != uid: return
    try:
        if data.startswith('ad_delete:'):
            target=data.split(':',1)[1]; ad=next((a for a in s['ads'] if str(a.get('id'))==target and not a.get('deleted')),None)
            if not ad: send_dm(uid,'Реклама не найдена или уже удалена.',main_menu()); return
            delete_ad_message(ad); ad['deleted']=True; ad['deleted_at']=iso(now()); send_dm(uid,f'Реклама {target} удалена и откреплена, если была закреплена.',main_menu())
        elif data.startswith('ad_pin:'):
            target = data.split(':', 1)[1]
            ad = next((a for a in s['ads'] if str(a.get('id')) == target and not a.get('deleted') and a.get('message_id')), None)
            if not ad:
                send_dm(uid, 'Реклама не найдена или ещё не опубликована.', main_menu())
                return
            # For an album, pin the first message; Telegram groups the album visually.
            mid = (ad.get('message_ids') or [ad.get('message_id')])[0]
            try:
                api('pinChatMessage', {
                    'chat_id': ad.get('chat_id', CHANNEL),
                    'message_id': mid,
                    'disable_notification': 'true'
                })
                send_dm(uid, f'Реклама {target} закреплена. Проверь, что у бота есть право закреплять сообщения.', main_menu())
            except Exception as e:
                print('Pin action failed:', type(e).__name__)
                send_dm(uid, 'Не удалось закрепить рекламу. Проверь права администратора бота в канале.', main_menu())
        elif data.startswith('ad_unpin:'):
            target=data.split(':',1)[1]; ad=next((a for a in s['ads'] if str(a.get('id'))==target and not a.get('deleted')),None)
            if not ad: send_dm(uid,'Реклама не найдена или уже удалена.',main_menu()); return
            for mid in (ad.get('message_ids') or [ad.get('message_id')]):
                if mid:
                    try: api('unpinChatMessage',{'chat_id':ad.get('chat_id',CHANNEL),'message_id':mid})
                    except Exception as e: print('Unpin action:',e)
            send_dm(uid,f'Команда открепления выполнена для рекламы {target}.',main_menu())
    except Exception as e: send_dm(uid,'Не удалось выполнить действие. Проверь права бота и журнал GitHub Actions.'); print('Callback error:',e)
    try: api('answerCallbackQuery',{'callback_query_id':q['id']})
    except Exception: pass


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


def routine_posts(s):
    """Publish an end-of-day digest only when there is useful news or a real forecast warning."""
    n = now()
    if is_paused(s) or count_today(s) >= 10 or n.hour != 22 or not (30 <= n.minute < 35) or slot_once(s, 'night'):
        return

    todays = [item for item in s.get('published_news', [])
              if str(item.get('published_at', '')).startswith(n.date().isoformat())]
    tomorrow_warning = ''
    try:
        url = ('https://api.open-meteo.com/v1/forecast?latitude=12.2388&longitude=109.1967'
               '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code'
               '&timezone=Asia%2FHo_Chi_Minh&forecast_days=2')
        forecast = json.loads(get_url(url).decode('utf-8'))
        daily = forecast['daily']
        code = int(daily['weather_code'][1])
        rain = daily.get('precipitation_probability_max', [None, None])[1]
        names = {
          0:'ясно',1:'преимущественно ясно',2:'переменная облачность',3:'облачно',
          45:'туман',48:'туман',51:'морось',53:'морось',55:'сильная морось',
          61:'небольшой дождь',63:'дождь',65:'сильный дождь',80:'кратковременный дождь',
          81:'ливни',82:'сильные ливни',95:'гроза',96:'гроза с градом',99:'сильная гроза с градом'
        }
        if (rain is not None and rain >= 60) or code in (65, 81, 82, 95, 96, 99):
            tomorrow_warning = (
                f'По прогнозу на завтра: {names.get(code, "переменная погода")}, вероятность осадков '
                f'{rain if rain is not None else "не указана"}%. Учти это при планировании поездок.'
            )
    except Exception as e:
        print('Night forecast unavailable; no weather warning inferred:', e)

    # No fabricated "goodnight" filler when there is nothing important to recap.
    if not todays and not tomorrow_warning:
        mark_slot(s, 'night')
        return

    sections = ['🌙 <b>Доброй ночи, Нячанг!</b>']
    if todays:
        lines = ['<b>Что важного было сегодня</b>']
        for item in todays[-4:]:
            title = clean(item.get('title', ''))
            summary = clean(item.get('summary', ''))
            if not title:
                continue
            sentences = re.split(r'(?<=[.!?])\s+', summary)
            brief = ' '.join(sentences[:2]).strip()
            if len(brief) > 320:
                brief = brief[:317].rsplit(' ', 1)[0] + '…'
            lines.append('• <b>' + html.escape(title) + '</b>' +
                         (('\n' + html.escape(brief)) if brief else ''))
        if len(lines) > 1:
            sections.append('\n'.join(lines))
    if tomorrow_warning:
        sections.append('<b>На завтра</b>\n' + html.escape(tomorrow_warning))
    sections.append('Источник прогноза: <a href="https://open-meteo.com/">Open-Meteo</a>' + footer())
    text = '\n\n'.join(sections)
    image = commons_image('Nha Trang Vietnam city at night street')
    post_with_image(text, image, illustration=bool(image))
    bump_today(s)
    mark_slot(s, 'night')



def weather_post(s, period):
    key = 'weather_' + period
    if slot_once(s, key) or count_today(s) >= 10 or is_paused(s):
        return
    n = now()
    if period == 'morning' and not (n.hour == 6 and 30 <= n.minute < 40):
        return
    if period == 'evening' and not (n.hour == 18 and 0 <= n.minute < 10):
        return

    url = ('https://api.open-meteo.com/v1/forecast?latitude=12.2388&longitude=109.1967'
           '&current=temperature_2m,apparent_temperature,weather_code'
           '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code'
           '&timezone=Asia%2FHo_Chi_Minh&forecast_days=2')
    try:
        d = json.loads(get_url(url).decode('utf-8'))
        cur, daily = d['current'], d['daily']
        i = 0 if period == 'morning' else 1
        icons = {0:'☀️',1:'🌤️',2:'⛅',3:'☁️',45:'🌫️',48:'🌫️',51:'🌦️',53:'🌦️',55:'🌧️',
                 61:'🌧️',63:'🌧️',65:'🌧️',80:'🌦️',81:'🌧️',82:'⛈️',95:'⛈️',96:'⛈️',99:'⛈️'}
        names = {0:'ясно',1:'преимущественно ясно',2:'переменная облачность',3:'облачно',
                 45:'туман',48:'туман',51:'морось',53:'морось',55:'сильная морось',
                 61:'небольшой дождь',63:'дождь',65:'сильный дождь',80:'кратковременный дождь',
                 81:'ливни',82:'сильные ливни',95:'гроза',96:'гроза с градом',99:'сильная гроза с градом'}
        code, current_code = int(daily['weather_code'][i]), int(cur['weather_code'])
        rain = daily.get('precipitation_probability_max', [None, None])[i]
        if period == 'morning':
            title = 'Доброе утро, Нячанг! Погода на сегодня'
            extra = (f'Сейчас: {round(cur["temperature_2m"])}°C, {names.get(current_code, "переменная погода")} '
                     f'{icons.get(current_code, "🌤️")} (ощущается как {round(cur["apparent_temperature"])}°C).\n')
        else:
            title = 'Погода в Нячанге: вечер и завтра'
            extra = (f'Сейчас: {round(cur["temperature_2m"])}°C. Завтра: '
                     f'{names.get(code, "переменная погода")} {icons.get(code, "🌤️")}.\n')
        if rain is not None and rain >= 60:
            advice = 'Вероятность осадков высокая — учитывай это при поездках и планах на улице.'
        elif rain is not None and rain >= 35:
            advice = 'Возможен дождь — проверь погоду перед выходом.'
        elif code in (82, 95, 96, 99):
            advice = 'В прогнозе есть гроза. Следи за обновлениями погоды перед выходом.'
        else:
            advice = 'Проверь прогноз повторно, если собираешься надолго выходить на улицу.'
        text = (
            ('☀️' if period == 'morning' else '🌆') + ' <b>' + title + '</b>\n\n' +
            extra + f'Температура: {round(daily["temperature_2m_min"][i])}–{round(daily["temperature_2m_max"][i])}°C.\n' +
            'Вероятность осадков: ' + (f'{rain}%' if rain is not None else 'нет данных') + '.\n\n' +
            advice
        )
        if period == 'morning':
            cutoff = n - timedelta(hours=24)
            recent = []
            for entry in s.get('published_news', []):
                try:
                    published_at = datetime.fromisoformat(entry.get('published_at', '')).astimezone(TZ)
                except (TypeError, ValueError):
                    continue
                if cutoff <= published_at <= n and entry.get('title'):
                    recent.append(entry)
            if recent:
                digest_lines = ['<b>Что важно знать за последние сутки</b>']
                for entry in recent[-3:]:
                    summary = clean(entry.get('summary', ''))
                    first_sentence = re.split(r'(?<=[.!?])\s+', summary, maxsplit=1)[0]
                    if len(first_sentence) > 220:
                        first_sentence = first_sentence[:217].rsplit(' ', 1)[0] + '…'
                    link = entry.get('link', '')
                    item = '• <b>' + html.escape(clean(entry.get('title', ''))) + '</b>'
                    if first_sentence:
                        item += '\n' + html.escape(first_sentence)
                    if link.startswith('https://') or link.startswith('http://'):
                        item += ' <a href="' + html.escape(link, quote=True) + '">Источник</a>'
                    digest_lines.append(item)
                text += '\n\n' + '\n\n'.join(digest_lines)
        text += '\n\nИсточник прогноза: <a href="https://open-meteo.com/">Open-Meteo</a>' + footer()
        img = commons_image('Nha Trang Vietnam weather rainy season coastline' if (rain is not None and rain >= 35)
                            else 'Nha Trang Vietnam city coastal view')
        post_with_image(text, img, illustration=bool(img))
        bump_today(s)
        mark_slot(s, key)
    except Exception as e:
        print('Weather error; no unverified weather message was posted:', e)



def publish_news(s):
    """Publish screened local stories; urgent safety information can bypass quiet windows."""
    n = now()
    hour_key = n.strftime('%Y-%m-%d-%H')
    items, links = [], set()
    for feed in FEEDS:
        try:
            root = ET.fromstring(get_url(feed))
            for item in root.findall('.//item'):
                title = clean(item.findtext('title'))
                desc = clean(item.findtext('description'))
                link = (item.findtext('link') or '').strip()
                if not title or not link or not re.match(r'^https?://', link, re.I) or link in links:
                    continue
                links.add(link)
                if not is_relevant_news(title, desc):
                    continue
                image_url = None
                for node in list(item):
                    tag = node.tag.lower()
                    if tag.endswith('}content') or tag.endswith('}thumbnail') or tag.endswith('enclosure'):
                        candidate = node.attrib.get('url') or node.attrib.get('href')
                        media_type = node.attrib.get('type', '')
                        if candidate and (media_type.startswith('image/') or
                                          re.search(r'\.(jpg|jpeg|png|webp)(\?|$)', candidate, re.I)):
                            image_url = candidate
                            break
                items.append((title, desc, link, image_url, is_urgent_news(title + ' ' + desc)))
        except Exception as e:
            print('Feed error:', e)

    items.sort(key=lambda item: not item[4])
    for title, desc, link, image_url, urgent in items:
        if is_duplicate_news(s, title, link):
            continue
        if is_paused(s) and not urgent:
            continue
        if count_today(s) >= 10 and not urgent:
            return
        if s.get('last_news_hour') == hour_key and not urgent:
            return
        if n.hour in (6, 7, 8, 11, 12, 17, 18, 22, 23) and not urgent:
            continue

        translated_title = translate(re.split(r'\s[-–|]\s', title, maxsplit=1)[0])
        if not translated_title:
            continue
        headline = clean(translated_title)
        translated_body = translate(desc) if desc else ''

        if len(translated_body) < 180 or len(translated_body.split()) < 24:
            # Only fetch a source page when RSS lacks usable detail; don't let one slow page stall a run.
            try:
                page = get_url(link).decode('utf-8', 'ignore')
                article_match = re.search(r'<article\b[^>]*>(.*?)</article>', page, re.I | re.S)
                if article_match:
                    raw_article = article_match.group(1)
                else:
                    paragraphs = re.findall(r'<p\b[^>]*>(.*?)</p>', page, re.I | re.S)
                    raw_article = '\n'.join(paragraphs[:35])
                raw_article = re.sub(r'<(script|style|noscript|svg)\b[^>]*>.*?</\1>', ' ', raw_article, flags=re.I | re.S)
                raw_article = re.sub(r'</?(p|div|br|li|h[1-6])\b[^>]*>', '\n', raw_article, flags=re.I)
                raw_article = clean_multiline(raw_article)
                if len(raw_article) > len(desc) + 100:
                    translated_body = translate(raw_article)
            except Exception as e:
                print('Full article unavailable; relying on RSS description only:', e)

        body = remove_repeated_lead(headline, translated_body)
        if len(body) < 100 or len(body.split()) < 18:
            print('Skipping thin or repetitive story:', headline)
            continue
        urgent = is_urgent_news(title + ' ' + desc + ' ' + headline + ' ' + body)

        # Do not republish an RSS publisher's image without knowing its licence.
        # Prefer a Wikimedia Commons image and label it as an illustration with attribution.
        image_url = commons_image(news_image_query(headline, body))
        illustration = bool(image_url)
        if not image_url and not urgent:
            print('Skipping non-urgent news without a suitable licensed illustration:', headline)
            continue

        emoji = '⚠️' if urgent else '📰'
        sections = [f'{emoji} <b>{html.escape(headline[:180])}</b>', html.escape(body)]
        line = reaction_line(title + ' ' + headline + ' ' + body)
        if line:
            sections.append(line)
        sections.append(f'🔗 <a href="{html.escape(link, quote=True)}">Источник</a>' + footer())
        news_text = '\n\n'.join(sections)
        post_with_image(news_text, image_url, illustration=illustration)
        s['seen_links'] = (s.get('seen_links', []) + [link])[-1000:]
        s['seen_titles'] = (s.get('seen_titles', []) + [title])[-500:]
        s['seen_fingerprints'] = (s.get('seen_fingerprints', []) + [title_fingerprint(title)])[-1000:]
        s['published_news'] = (s.get('published_news', []) + [{
            'title': headline, 'summary': body, 'link': link, 'published_at': iso(now()), 'urgent': bool(urgent)
        }])[-100:]
        bump_today(s)
        s['last_news_hour'] = hour_key
        print('Published news:', headline, 'urgent=', urgent)
        return



def main():
    s = load_state()
    get_updates(s)
    process_ad_timers(s)
    if not is_paused(s):
        routine_posts(s)
        weather_post(s, 'morning')
        weather_post(s, 'evening')
    # Screen news even during a quiet window; publish_news itself allows only urgent alerts through.
    publish_news(s)
    save_state(s)
    print('Finished:', now().isoformat(), 'posts_today=', count_today(s), 'paused=', is_paused(s))


if __name__=='__main__': main()
