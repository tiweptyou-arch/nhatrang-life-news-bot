
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
