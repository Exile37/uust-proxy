from flask import Flask, request, jsonify, session
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

app = Flask(__name__)
app.secret_key = 'super_secret_key_123'

BASE_URL = 'https://account.str.uust.ru'
EDU_URL  = 'https://edu.str.uust.ru'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9',
}

# ─── CORS ───────────────────────────────────────────────────────────────────
@app.after_request
def add_cors(r):
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return r

# ─── ВСПОМОГАЛКИ ────────────────────────────────────────────────────────────
def edu_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok'})

# ─── AUTH ────────────────────────────────────────────────────────────────────
@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    data = request.json or {}
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        login_url = f'{BASE_URL}/Account/Login'
        r = s.get(login_url, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        form = soup.find('form')
        if not form:
            return jsonify({'success': False, 'error': 'Форма не найдена'})
        payload = {i.get('name'): i.get('value','') for i in form.find_all('input') if i.get('name')}
        lf, pf = 'Email', 'Password'
        for k in payload:
            if any(w in k.lower() for w in ['login','email','user']): lf = k
            if 'pass' in k.lower(): pf = k
        payload[lf] = data.get('username')
        payload[pf] = data.get('password')
        resp = s.post(login_url, data=payload, headers={**HEADERS,'Referer':login_url},
                      allow_redirects=True, timeout=15)
        if any('.AspNet' in c.name for c in s.cookies) or 'Выйти' in resp.text:
            session['cookies'] = requests.utils.dict_from_cookiejar(s.cookies)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Неверный логин или пароль'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/logout', methods=['POST','OPTIONS'])
def logout():
    if request.method == 'OPTIONS': return jsonify({}), 200
    session.clear()
    return jsonify({'success': True})

# ─── ПРЕДМЕТЫ И ОЦЕНКИ ───────────────────────────────────────────────────────
@app.route('/api/subjects', methods=['GET','OPTIONS'])
def subjects():
    if request.method == 'OPTIONS': return jsonify({}), 200
    if 'cookies' not in session: return jsonify({'error': 'auth'}), 401
    s = requests.Session()
    s.cookies.update(requests.utils.cookiejar_from_dict(session['cookies']))
    s.headers.update(HEADERS)
    try:
        soup = BeautifulSoup(s.get(f'{BASE_URL}/Journals/DisciplinesStudent', timeout=15).text, 'html.parser')
        result = []
        for link in soup.find_all('a', href=True):
            if '/Journals/DisciplineGrades' not in link['href']: continue
            row = link.find_parent('tr')
            if not row: continue
            cells = row.find_all('td')
            result.append({
                'name':    cells[1].get_text(strip=True) if len(cells)>1 else '—',
                'semestr': cells[2].get_text(strip=True) if len(cells)>2 else '—',
                'teacher': cells[3].get_text(strip=True) if len(cells)>3 else '—',
                'url':     link['href'],
            })
        return jsonify({'subjects': list({i['url']:i for i in result}.values())})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/grades', methods=['GET','OPTIONS'])
def grades():
    if request.method == 'OPTIONS': return jsonify({}), 200
    if 'cookies' not in session: return jsonify({'error': 'auth'}), 401
    url = request.args.get('url','')
    if not url.startswith('/Journals/'): return jsonify({'error': 'неверный URL'})
    s = requests.Session()
    s.cookies.update(requests.utils.cookiejar_from_dict(session['cookies']))
    s.headers.update(HEADERS)
    try:
        soup = BeautifulSoup(s.get(urljoin(BASE_URL, url), timeout=15).text, 'html.parser')
        lessons = []
        table = soup.find('table')
        if table:
            for row in table.find_all('tr'):
                cells = row.find_all(['td','th'])
                if len(cells) < 3: continue
                date = cells[1].get_text(strip=True)
                if not date or 'Дата' in date: continue
                content = cells[2].get_text(separator=' ', strip=True)
                theme = content.split('Домашнее задание:')[0].replace('Тема:','').strip()
                lessons.append({'date':date,'theme':theme or 'Занятие','grade':cells[-1].get_text(strip=True) or '-'})
        return jsonify({'lessons': lessons})
    except Exception as e:
        return jsonify({'error': str(e)})

# ─── ДИАГНОСТИКА — ПОКАЗЫВАЕМ СЫРОЙ HTML И JS-ЗАПРОСЫ САЙТА ─────────────────
@app.route('/api/debug/site', methods=['GET'])
def debug_site():
    """
    Вызови: GET /api/debug/site
    Показывает сырой HTML главной страницы и все найденные JS/AJAX ссылки.
    """
    s = edu_session()
    try:
        r = s.get(f'{EDU_URL}/', timeout=12)
        html = r.text
        soup = BeautifulSoup(html, 'html.parser')

        # Ищем все src скриптов
        scripts_src = [sc.get('src','') for sc in soup.find_all('script', src=True)]

        # Ищем упоминания URL внутри inline-скриптов
        ajax_hints = []
        for sc in soup.find_all('script'):
            txt = sc.string or ''
            # Любые строки с .php или fetch( или XMLHttp или ajax
            urls = re.findall(r'["\']([^"\']*(?:\.php|ajax|api|schedule|rasp|grp)[^"\']*)["\']', txt, re.I)
            ajax_hints.extend(urls)

        return jsonify({
            'status':      r.status_code,
            'html_length': len(html),
            'raw_html':    html,            # <── главное! вставь это в ответе
            'scripts_src': scripts_src,
            'ajax_hints':  list(set(ajax_hints)),
        })
    except Exception as e:
        return jsonify({'error': str(e)})

# ─── ВСПОМОГАЛКИ ДЛЯ ПАРСЕРА РАСПИСАНИЯ ─────────────────────────────────────
def parse_schedule_html(html: str, group: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    header = f'Расписание группы {group}'
    for sel in ['.rasp_head','h1','h2']:
        el = soup.select_one(sel)
        if el:
            header = el.get_text(' ', strip=True)
            break

    days = [
        {'name':'Понедельник','header':'','lessons':[]},
        {'name':'Вторник',    'header':'','lessons':[]},
        {'name':'Среда',      'header':'','lessons':[]},
        {'name':'Четверг',    'header':'','lessons':[]},
        {'name':'Пятница',    'header':'','lessons':[]},
        {'name':'Суббота',    'header':'','lessons':[]},
    ]
    has_data = False
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 2: continue
        hdr = rows[0].find_all(['th','td'])
        day_map = {}
        for ci, cell in enumerate(hdr):
            txt = cell.get_text(strip=True)
            for di, d in enumerate(days):
                if d['name'][:2] in txt or d['name'] in txt:
                    day_map[ci] = di
                    days[di]['header'] = txt

        for row in rows[1:]:
            cells = row.find_all('td')
            for ci, cell in enumerate(cells):
                di = day_map.get(ci, ci if ci < 6 else None)
                if di is None: continue
                lines = [l.strip() for l in cell.get_text('\n').split('\n') if l.strip()]
                if not lines: continue
                if any(w in cell.get_text().lower() for w in ['отсутствует','пар нет']): continue

                num=time_str=subject=teacher=room=''
                rest=[]
                for ln in lines:
                    if re.search(r'\d{1,2}[:\.]\d{2}', ln): time_str = ln
                    elif re.match(r'^\d+[\.)]?\s*$', ln): num = ln.strip('.)').strip()
                    else: rest.append(ln)

                if not rest and not time_str: continue
                final=[]
                for ln in rest:
                    if re.search(r'\b(каб|ауд|гк|лк|пр|лаб|стад)\b', ln, re.I): room=ln
                    else: final.append(ln)

                if final:
                    has_data = True
                    b = cell.find(['b','strong'])
                    if b:
                        subject = b.get_text(strip=True)
                        teacher = ', '.join(l for l in final if l.lower()!=subject.lower())
                    else:
                        subject = final[0]
                        teacher = ', '.join(final[1:])

                if subject or time_str:
                    days[di]['lessons'].append({
                        'num': num or str(len(days[di]['lessons'])+1),
                        'time': time_str,
                        'subject': subject,
                        'teacher': teacher,
                        'room': room,
                    })

    for d in days:
        d['lessons'].sort(key=lambda x: x['num'].zfill(2))

    return {'header': header, 'days': days, 'success': has_data}


# ─── РАСПИСАНИЕ — РЕАЛЬНЫЙ ЭНДПОИНТ ─────────────────────────────────────────
# СЕЙЧАС ЗАГЛУШКА — нужно знать реальный AJAX-эндпоинт сайта
# Вызови /api/debug/site и скинь html_preview + ajax_hints
# Тогда я подставлю правильный URL и параметры
@app.route('/api/schedule/by_name', methods=['POST','OPTIONS'])
def schedule_by_name():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.json or {}
    group = data.get('group_name','').strip()
    week  = str(data.get('week', '0'))
    if not group:
        return jsonify({'error': 'Не указана группа'})

    s = edu_session()

    # ── Попытка 1: прямой AJAX-запрос (заполни AJAX_URL когда узнаем из debug)
    AJAX_URL = None  # ← СЮДА впишем реальный URL после debug
    if AJAX_URL:
        try:
            r = s.post(AJAX_URL, data={'group': group, 'week': week}, timeout=12)
            result = parse_schedule_html(r.text, group)
            if result['success']:
                return jsonify({'header': result['header'], 'days': result['days']})
        except Exception as e:
            print(f'[AJAX] Ошибка: {e}')

    # ── Попытка 2: пробуем несколько самых вероятных URL (без перебора сотен)
    candidates = [
        f'{EDU_URL}/rasp/',
        f'{EDU_URL}/schedule/',
        f'{EDU_URL}/get_schedule.php',
        f'{EDU_URL}/api/schedule',
        f'{EDU_URL}/ajax.php',
        f'{EDU_URL}/rasp.php',
    ]
    for url in candidates:
        try:
            r = s.post(url, data={'group': group, 'week': week}, timeout=8,
                       headers={**HEADERS,'Referer':f'{EDU_URL}/'})
            if r.status_code == 200 and len(r.text) > 5500:
                result = parse_schedule_html(r.text, group)
                if result['success']:
                    print(f'[FOUND] Работает URL: {url}')
                    return jsonify({'header': result['header'], 'days': result['days']})
        except Exception:
            continue

    # ── Ничего не нашли
    return jsonify({
        'header': f'Расписание группы "{group}" не найдено',
        'days': [],
        'hint': 'Нужно вызвать /api/debug/site чтобы найти реальный AJAX эндпоинт'
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
