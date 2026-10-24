import os, re, json, html, time, hashlib, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHANNEL = os.environ.get('TELEGRAM_CHAT_ID', '@NhaTrangLifeNews')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID', '').strip()
TZ = ZoneInfo('Asia/Ho_Chi_Minh')
STATE_FILE = 'bot_state.json'
SUBSCRIBE_URL = 'https://t.me/NhaTrangLifeNews'
SUGGEST_URL = 'https://t.me/fsfinesed'
FOOTER = f'🇻🇳 <a href="{SUBSCRIBE_URL}">ПОДПИСАТЬСЯ</a> | <a href="{SUGGEST_URL}">ПРЕДЛОЖИТЬ НОВОСТЬ</a>'
FEEDS = [
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

def get_url(url):
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 NhaTrangLifeNewsBot/2.0'})
    with urllib.request.urlopen(req, timeout=20) as r: return r.read()

def api(method, data=None):
    url = f'https://api.telegram.org/bot{TOKEN}/{method}'
    body = urllib.parse.urlencode(data or {}).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=25) as r: result = json.loads(r.read().decode())
    if not result.get('ok'): raise RuntimeError(f'Telegram API {method}: {result}')
    return result['result']

def send_dm(user_id, text, markup=None):
    data = {'chat_id': user_id, 'text': text, 'parse_mode':'HTML', 'disable_web_page_preview':'true'}
    if markup: data['reply_markup'] = json.dumps(markup, ensure_ascii=False)
    return api('sendMessage', data)

def load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f: s = json.load(f)
    except (OSError, json.JSONDecodeError): s = {}
    defaults = {
      'seen_links': [], 'seen_titles': [], 'last_update_id': 0, 'pending_ad': {},
      'ads': [], 'paused': False, 'pause_until': '', 'daily_counts': {}, 'last_slots': {},
      'game_history': [], 'last_news_hour': '', 'manual_posts': []
    }
    for k,v in defaults.items(): s.setdefault(k,v)
    return s

def save_state(s):
    with open(STATE_FILE,'w',encoding='utf-8') as f: json.dump(s,f,ensure_ascii=False,indent=2)

def clean(value):
    value = re.sub(r'<[^>]+>',' ',value or '')
    return re.sub(r'\s+',' ',html.unescape(value)).strip()

def translate(text):
    text = clean(text)
    if not text: return ''
    q = urllib.parse.urlencode({'client':'gtx','sl':'auto','tl':'ru','dt':'t','q':text[:4000]})
    try:
        data = json.loads(get_url('https://translate.googleapis.com/translate_a/single?'+q).decode())
        return ''.join(x[0] for x in data[0] if x[0]).strip()
    except Exception as e:
        print('Translation error:',e); return ''

def footer(): return '\n\n' + FOOTER

def post_text(text, disable_preview=True):
    return api('sendMessage', {'chat_id':CHANNEL,'text':text,'parse_mode':'HTML','disable_web_page_preview':str(disable_preview).lower()})

def send_ad_content(ad):
    caption = (ad.get('text') or '').strip()
    # Always include the standard footer. Telegram captions are limited to 1024 chars.
    if ad.get('type') in ('photo','video','document'):
        full_caption = (caption + footer()).strip()
        if len(full_caption) > 1024: raise ValueError('Подпись к фото/видео длиннее лимита Telegram 1024 символа. Сократи текст.')
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

def handle_admin_message(msg,s):
    user = msg.get('from') or {}; uid = str(user.get('id',''))
    if not ADMIN_USER_ID or uid != ADMIN_USER_ID: return
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
    parts=text.split(maxsplit=1); cmd=parts[0].split('@')[0].lower() if parts else ''
    arg=parts[1].strip() if len(parts)>1 else ''
    if cmd in ('/start','/menu','/help'):
        send_dm(uid, '<b>Панель Нячанг Лайф</b>\n/ad 1/24 — создать рекламу\n/publish — опубликовать черновик\n/schedule 2026-10-25 14:30 — отложить черновик\n/ads — активные и запланированные рекламы\n/cancelad ID — удалить рекламу досрочно\n/editad ID | новый текст — изменить подпись рекламы\n/pause — остановить новости вручную\n/resume — снять ручную паузу\n/status — статус бота\n/cancel — отменить черновик\n/delete ID — удалить пост по ID из списка реклам')
    elif cmd=='/ad':
        m=re.fullmatch(r'(\d{1,2})\s*/\s*(\d{1,3})',arg)
        if not m: send_dm(uid,'Формат: <code>/ad 1/24</code> — 1 час без новостей, удаление через 24 часа.'); return
        top_hours, life_hours=map(int,m.groups())
        if not (1<=top_hours<=72 and top_hours<=life_hours<=720): send_dm(uid,'Допустимо: время в топе 1–72 часа, срок жизни не меньше времени в топе и не больше 720 часов.'); return
        s['pending_ad']={'format':f'{top_hours}/{life_hours}','top_hours':top_hours,'life_hours':life_hours,'awaiting_content':True}
        send_dm(uid,f'Пришли следующим сообщением рекламный текст, фото или видео.\nФормат: <b>{top_hours}/{life_hours}</b>.\nПервые {top_hours} ч. бот не будет публиковать обычные новости; реклама удалится через {life_hours} ч.\n/cancel — отмена.')
    elif cmd=='/publish':
        ad=s.get('pending_ad') or {}
        if not ad or ad.get('awaiting_content') or not ad.get('text','') and not ad.get('file_id'):
            send_dm(uid,'Сначала создай черновик: /ad 1/24, затем пришли содержимое.'); return
        publish_ad(ad,s,uid)
    elif cmd=='/schedule':
        ad=s.get('pending_ad') or {}
        if not ad or ad.get('awaiting_content') or not (ad.get('text') or ad.get('file_id')):
            send_dm(uid,'Сначала подготовь черновик через /ad 1/24.'); return
        try: when=datetime.strptime(arg,'%Y-%m-%d %H:%M').replace(tzinfo=TZ)
        except ValueError: send_dm(uid,'Формат: /schedule 2026-10-25 14:30'); return
        if when<=now(): send_dm(uid,'Время должно быть в будущем.'); return
        ad['scheduled_at']=iso(when); ad['awaiting_content']=False; ad['id']=str(int(time.time()))
        s['ads'].append(ad); s['pending_ad']={}
        send_dm(uid,f'Реклама запланирована на {when.strftime("%d.%m.%Y %H:%M")} по времени Нячанга. ID: <code>{ad["id"]}</code>.')
    elif cmd=='/ads':
        rows=[]
        for a in s['ads'][-30:]:
            status='удалена' if a.get('deleted') else ('запланирована на '+parse_dt(a['scheduled_at']).strftime('%d.%m %H:%M') if a.get('scheduled_at') and not a.get('message_id') else 'активна')
            rows.append(f'ID <code>{a.get("id","?")}</code> · {a.get("format","?")} · {status}')
        send_dm(uid,'<b>Реклама</b>\n'+'\n'.join(rows) if rows else 'Реклам пока нет.')
    elif cmd=='/editad':
        if '|' not in arg: send_dm(uid,'Формат: <code>/editad ID | новый текст</code>'); return
        target,new_text=arg.split('|',1); target=target.strip(); new_text=new_text.strip()
        ad=next((a for a in s['ads'] if a.get('id')==target and not a.get('deleted') and a.get('message_id')),None)
        if not ad: send_dm(uid,'Не нашёл активный рекламный пост с таким ID. Посмотри /ads.'); return
        new_text=html.escape(new_text)
        if ad.get('type') in ('photo','video','document'):
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
        if ad.get('message_id'):
            api('deleteMessage',{'chat_id':CHANNEL,'message_id':ad['message_id']})
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
    ad['message_id']=result['message_id']; ad['chat_id']=result['chat']['id']
    ad['published_at']=iso(t); ad['top_until']=iso(t+timedelta(hours=top)); ad['expires_at']=iso(t+timedelta(hours=life)); ad.pop('scheduled_at',None); ad['awaiting_content']=False
    if not any(existing is ad for existing in s['ads']):
        s['ads'].append(ad)
    s['pending_ad']={}; bump_today(s)
    if uid: send_dm(uid,f'Опубликовано! ID: <code>{ad["id"]}</code>. Окно без новостей до {parse_dt(ad["top_until"]).strftime("%d.%m %H:%M")}; автоматическое удаление {parse_dt(ad["expires_at"]).strftime("%d.%m %H:%M")} (Нячанг).')

def process_ad_timers(s):
    for ad in s['ads']:
        if ad.get('deleted'): continue
        if ad.get('scheduled_at') and not ad.get('message_id') and parse_dt(ad['scheduled_at'])<=now():
            try: publish_ad(ad,s)
            except Exception as e: print('Scheduled ad error',ad.get('id'),e)
        if ad.get('message_id') and ad.get('expires_at') and parse_dt(ad['expires_at'])<=now():
            try:
                api('deleteMessage',{'chat_id':ad.get('chat_id',CHANNEL),'message_id':ad['message_id']})
                ad['deleted']=True; ad['deleted_at']=iso(now()); print('Deleted expired ad',ad.get('id'))
            except Exception as e:
                # Retry on next workflow run; Telegram may reject old/already deleted messages.
                print('Ad deletion error',ad.get('id'),e)

def get_updates(s):
    try:
        updates=api('getUpdates',{'offset':int(s.get('last_update_id',0))+1,'timeout':0,'allowed_updates':json.dumps(['message'])})
    except Exception as e:
        print('getUpdates error:',e); return
    for u in updates:
        s['last_update_id']=max(int(s.get('last_update_id',0)),int(u['update_id']))
        msg=u.get('message')
        if msg: 
            try: handle_admin_message(msg,s)
            except Exception as e: print('Admin command error:',e)

def slot_once(s,key):
    day=now().date().isoformat(); return s['last_slots'].get(key)==day

def mark_slot(s,key): s['last_slots'][key]=now().date().isoformat()

def routine_posts(s):
    n=now(); hour=n.hour; minute=n.minute
    if is_paused(s): return
    # The fixed routine posts are counted against the daily 10-post cap.
    if hour==6 and minute>=30 and not slot_once(s,'morning') and count_today(s)<10:
        greetings=[
          '🌅 <b>Нячанг просыпается</b>\n\nПусть утро будет спокойным, кофе — вкусным, а планы — приятными. Как настроение? ☕\n\n👍 — уже на ногах\n😴 — ещё просыпаюсь',
          '🌤️ <b>Новое утро у моря</b>\n\nЖелаем лёгкого старта и побольше хороших новостей сегодня. Что выбираешь?\n\n☕ — сначала кофе\n🌊 — хочу к морю',
          '☀️ <b>Доброе утро, Нячанг!</b>\n\nГород начинает новый день — пусть у вас он сложится удачно. Какие планы?\n\n🚶 — прогуляться\n💻 — рабочий день'
        ]
        idx=int(n.strftime('%j'))%len(greetings)
        post_text(greetings[idx]+footer()); bump_today(s); mark_slot(s,'morning')
    # Weather handled separately at 08:00 and 18:00.
    if hour>=22 and minute>=30 and not slot_once(s,'night') and count_today(s)<10:
        words=['Сериал','Кофе','Море','Закат','Монтаж','Пальма','Волна','Нячанг','Дождь','Кокос','Билет','Путешествие','Музыка','Ветер','Лагуна']
        idx=(int(n.strftime('%j'))*7)%len(words); word=words[idx]
        hist=s.get('game_history',[])
        for shift in range(len(words)):
            candidate=words[(idx+shift)%len(words)]
            if candidate.casefold() not in [x.casefold() for x in hist[-14:]]: word=candidate; break
        next_letter=word[-1].upper()
        if next_letter in 'ЬЪЫ': next_letter=word[-2].upper()
        post_text(f'🌙 <b>Доброй ночи!</b>\n\nПеред сном давайте поиграем в слова! Мы начинаем:\n\n«{html.escape(word)}» — вам на «{html.escape(next_letter)}»!'+footer())
        s['game_history']=(hist+[word])[-100:]; bump_today(s); mark_slot(s,'night')

def weather_post(s,period):
    key='weather_'+period
    if slot_once(s,key) or count_today(s)>=10 or is_paused(s): return
    n=now()
    url=('https://api.open-meteo.com/v1/forecast?latitude=12.2388&longitude=109.1967'
         '&current=temperature_2m,apparent_temperature,weather_code'
         '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code'
         '&timezone=Asia%2FHo_Chi_Minh&forecast_days=2')
    try:
        d=json.loads(get_url(url).decode()); cur=d['current']; daily=d['daily']; i=0 if period=='morning' else 1
        icons={0:'☀️',1:'🌤️',2:'⛅',3:'☁️',45:'🌫️',48:'🌫️',51:'🌦️',53:'🌦️',55:'🌧️',61:'🌧️',63:'🌧️',65:'🌧️',80:'🌦️',81:'🌧️',82:'⛈️',95:'⛈️',96:'⛈️',99:'⛈️'}
        names={0:'ясно',1:'преимущественно ясно',2:'переменная облачность',3:'облачно',45:'туман',48:'туман',51:'морось',53:'морось',55:'сильная морось',61:'небольшой дождь',63:'дождь',65:'сильный дождь',80:'кратковременный дождь',81:'ливни',82:'сильные ливни',95:'гроза',96:'гроза с градом',99:'сильная гроза с градом'}
        code=int(daily['weather_code'][i]); cc=int(cur['weather_code']); rain=daily.get('precipitation_probability_max',[None,None])[i]
        title='Погода в Нячанге на сегодня' if period=='morning' else 'Погода в Нячанге: вечер и завтра'
        extra=(f'Сейчас: {round(cur["temperature_2m"])}°C, {names.get(cc,"переменная погода")} {icons.get(cc,"🌤️")} (ощущается как {round(cur["apparent_temperature"])}°C).\n' if period=='morning' else f'Сейчас: {round(cur["temperature_2m"])}°C. Завтра: {names.get(code,"переменная погода")} {icons.get(code,"🌤️")}.\n')
        advice='Возьми зонт или дождевик, если собираешься надолго выходить.' if rain is not None and rain>=40 else 'Не забудь воду и защиту от солнца в дневные часы.'
        post_text(f'{"☀️" if period=="morning" else "🌆"} <b>{title}</b>\n\n{extra}Температура: {round(daily["temperature_2m_min"][i])}–{round(daily["temperature_2m_max"][i])}°C.\nВероятность осадков: {str(rain)+"%" if rain is not None else "нет данных"}.\n\n{advice}'+footer())
        bump_today(s); mark_slot(s,key)
    except Exception as e: print('Weather error:',e)

def publish_news(s):
    if is_paused(s) or count_today(s)>=10: return
    hour_key=now().strftime('%Y-%m-%d-%H')
    if s.get('last_news_hour')==hour_key: return
    # Routine posts take priority around their scheduled times.
    if now().hour in (6,7,8,11,12,17,18,22,23): return
    items=[]; links=set()
    for feed in FEEDS:
        try:
            root=ET.fromstring(get_url(feed))
            for item in root.findall('.//item'):
                title=clean(item.findtext('title')); desc=clean(item.findtext('description')); link=(item.findtext('link') or '').strip()
                if not title or not desc or not link or link in links: continue
                links.add(link); combined=(title+' '+desc).casefold()
                if any(k in combined for k in KEYWORDS): items.append((title,desc,link))
        except Exception as e: print('Feed error:',e)
    for title,desc,link in items:
        if link in s['seen_links']: continue
        headline=translate(re.split(r'\s[-–|]\s',title,maxsplit=1)[0])
        summary=translate(desc)
        if not headline or not summary: continue
        headline=clean(headline); summary=clean(summary)
        if len(summary)<60: continue
        if len(summary)>600: summary=summary[:597].rsplit(' ',1)[0]+'…'
        emoji='⚠️' if any(x in (headline+' '+summary).casefold() for x in ['шторм','наводнен','пожар','авари','опасност','предупрежден']) else '📰'
        post_text(f'{emoji} <b>{html.escape(headline[:180])}</b>\n\n{html.escape(summary)}'+footer())
        s['seen_links']=(s['seen_links']+[link])[-500:]; bump_today(s); s['last_news_hour']=hour_key
        print('Published news:',headline); return

def main():
    s=load_state()
    get_updates(s)
    process_ad_timers(s)
    # Publish scheduled content, except while an ad top window or manual pause is active.
    if not is_paused(s):
        n=now()
        routine_posts(s)
        if n.hour>=8: weather_post(s,'morning')
        if n.hour>=18: weather_post(s,'evening')
        publish_news(s)
    save_state(s)
    print('Finished:',now().isoformat(),'posts_today=',count_today(s),'paused=',is_paused(s))

if __name__=='__main__': main()
