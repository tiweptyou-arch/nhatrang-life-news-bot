"""Small provider adapter for editorial images using the OpenAI Images API."""

import base64
import json
import os
import urllib.error
import urllib.request
from io import BytesIO


def generate(prompt, api_key=None, model=None, quality=None):
    """Return (image_bytes, observed_usd_cost) using the documented Images API."""
    api_key = (api_key or os.environ.get('OPENAI_API_KEY', '')).strip()
    if not api_key:
        raise RuntimeError('OPENAI_API_KEY is required for generated editorial images.')
    provider = os.environ.get('IMAGE_PROVIDER', 'openai').strip().casefold()
    if provider != 'openai':
        raise RuntimeError(f'Unsupported IMAGE_PROVIDER: {provider}')
    model = model or os.environ.get('OPENAI_IMAGE_MODEL', 'gpt-image-2.5-flare').strip()
    quality = quality or os.environ.get('OPENAI_IMAGE_QUALITY', 'low').strip()
    payload = json.dumps({
        'model': model,
        'prompt': prompt,
        'n': 1,
        'size': '1536x864',
        'quality': quality,
        'output_format': 'jpeg',
        'output_compression': 85,
        'background': 'opaque',
        'moderation': 'auto',
    }).encode('utf-8')
    request = urllib.request.Request(
        'https://api.openai.com/v1/images/generations',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'User-Agent': 'NhaTrangLifeNewsBot/3.0',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=150) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'OpenAI image API returned HTTP {exc.code}.') from None
    except Exception as exc:
        raise RuntimeError(f'OpenAI image API transport error ({type(exc).__name__}).') from None
    try:
        image_bytes = base64.b64decode(result['data'][0]['b64_json'], validate=True)
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError('OpenAI image API returned no usable image.') from None
    usage = result.get('usage') or {}
    detail = usage.get('input_tokens_details') or {}
    output_detail = usage.get('output_tokens_details') or {}
    text_tokens = detail.get('text_tokens', usage.get('input_tokens', 0)) or 0
    input_image_tokens = detail.get('image_tokens', 0) or 0
    output_image_tokens = output_detail.get('image_tokens', usage.get('output_tokens', 0)) or 0
    # Current GPT Image 2.5 standard token rates: text input $5/M, image input $8/M, output $30/M.
    cost = (text_tokens * 5 + input_image_tokens * 8 + output_image_tokens * 30) / 1_000_000
    return image_bytes, cost


def render_card(image_bytes, title, weather_hours=None):
    """Crop to 16:9 and add Russian text locally, including a dark weather strip."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError as exc:
        raise RuntimeError('Pillow is required to add Russian text to generated images.') from exc
    source = Image.open(BytesIO(image_bytes)).convert('RGB')
    image = ImageOps.fit(source, (1536, 864), method=Image.Resampling.LANCZOS)
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    rows = list(weather_hours or [])[:6]
    if rows:
        draw.rounded_rectangle((48, 535, 1488, 824), radius=26, fill=(3, 14, 25, 220))
    else:
        draw.rounded_rectangle((64, 670, 1472, 808), radius=22, fill=(0, 0, 0, 150))
    candidates = (
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
    )
    font = None
    font_path = None
    for path in candidates:
        try:
            font = ImageFont.truetype(path, 48)
            font_path = path
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    text = (title or '').strip()
    if len(text) > 76:
        text = text[:73].rsplit(' ', 1)[0].rstrip('.,:;!?') + '…'
    if font_path:
        while draw.textbbox((0, 0), text, font=font)[2] > 1320 and font.size > 30:
            font = ImageFont.truetype(font_path, font.size - 2)
    if rows:
        title_font = ImageFont.truetype(font_path, 38) if font_path else font
        row_font = ImageFont.truetype(font_path, 27) if font_path else font
        draw.text((82, 558), text, font=title_font, fill=(255, 255, 255, 255))
        for index, row in enumerate(rows):
            column, line = index % 2, index // 2
            x = 82 + column * 715
            y = 630 + line * 58
            value = f"{row.get('time', '—')}   {row.get('temp', '—')}°C   дождь {row.get('rain', '—')}%"
            draw.text((x, y), value, font=row_font, fill=(240, 246, 250, 255))
    else:
        draw.text((96, 708), text, font=font, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 110))
    return Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')


def prompt_for(item):
    kind = item.get('kind') or ''
    if kind == 'morning_greeting':
        scene = 'sunrise on Nha Trang beach, palm trees, calm sea and warm morning light'
    elif kind == 'night':
        scene = 'quiet Nha Trang coastline at night, city lights reflected on the sea, calm atmosphere'
    elif kind.startswith('weather'):
        category = item.get('weather_image_category') or 'coastal clouds'
        scene = f'Nha Trang Vietnam coast showing {category}, realistic local tropical weather'
    else:
        query = item.get('image_query') or item.get('headline') or 'Nha Trang Vietnam'
        scene = f'editorial documentary-style illustration of {query} in Vietnam'
    return (
        'Create a realistic, tasteful 16:9 editorial photograph for a Russian-language local news channel. '
        f'Scene: {scene}. Show no readable text, letters, logos, charts, watermarks, or fabricated news details. '
        'Natural photographic light, uncluttered composition, suitable as a Telegram news cover.'
    )


def title_for(item):
    if item.get('kind') == 'morning_greeting':
        return 'Доброе утро!'
    if item.get('kind') == 'night':
        return 'Доброй ночи!'
    if str(item.get('kind') or '').startswith('weather'):
        return 'Погода в Нячанге'
    return item.get('headline') or item.get('title') or ''
