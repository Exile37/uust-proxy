from flask import Flask, request, jsonify, session
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

app = Flask(__name__)
app.secret_key = 'super_secret_key_123'

BASE_URL = 'https://account.str.uust.ru'
EDU_URL  = 'https://edu.str.uust.ru'
PHP_URL  = f'{EDU_URL}/php'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'ru-RU,ru;q=0.9',
    'Origin':  EDU_URL,
    'Referer': EDU_URL + '/',
    'X-Requested-With': 'XMLHttpRequest',
}

# ─── CORS ────────────────────────────────────────────────────────────────────
@app.after_request
def add_cors(r):
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return r

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok'})

# ─── AUTH (ЛИЧНЫЙ КАБИНЕТ) ───────────────────────────────────────────────────
@app.route('/api/login', methods=['POST','OPTIONS'])
def login():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.json or {}
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        login_url = f'{BASE_URL}/Account/Login'
        r = s.get(login_url, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        form = soup.find('form')
        if not form: return jsonify({'success': False, 'error': 'Форма не найдена'})
        payload = {i.get('name'): i.get('value','') for i in form.find_all('input') if i.get('name')}
        lf, pf = 'Email', 'Password'
        for k in payload:
            if any(w in k.lower() for w in ['login','email','user']): lf = k
            if 'pass' in k.lower(): pf = k
        payload[lf] = data.get('username')
        payload[pf] = data.get('password')
        resp = s.post(login_url, data=payload, headers={**HEADERS,'Referer':login_url},
                      allow_redirects=True, timeout=10)
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
        soup = BeautifulSoup(s.get(f'{BASE_URL}/Journals/DisciplinesStudent', timeout=10).text, 'html.parser')
        result = []
        for link in soup.find_all('a', href=True):
            if '/Journals/DisciplineGrades' not in link['href']: continue
            row = link.find_parent('tr')
            if not row: continue
            cells = row.find_all('td')
            result.append({
                'name':   cells[1].get_text(strip=True) if len(cells)>1 else '—',
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
        soup = BeautifulSoup(s.get(urljoin(BASE_URL, url), timeout=10).text, 'html.parser')
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


# ─── ОТЛАДОЧНЫЙ ПОИСК ID ГРУППЫ ──────────────────────────────────────────────
def find_group_id(group_name: str) -> tuple[str|None, str|None, dict]:
    name_lower = group_name.lower().strip()
    clean_name = name_lower.replace('к-', '').strip()
    logs = {'attempts': []}
    
    s = requests.Session()
    s.headers.update(HEADERS)

    # Принудительно проверяем факультет 26, затем остальные (1-40)
    faculty_ids = [26] + [i for i in range(1, 41) if i != 26]

    for fid in faculty_ids:
        try:
            r = s.get(f'{PHP_URL}/getList.php', params={'faculty': str(fid)}, timeout=3)
            
            # ОТЛАДКА: выводим ответ от сервера в логи Render
            if fid == 26:
                print("======== [DEBUG] ОТВЕТ ОТ GETLIST (FACULTY 26) ========")
                print(r.text[:1000])
                print("=======================================================")
            
            if r.status_code == 200 and "cannot select db" not in r.text.lower():
                gid = extract_group_id_from_response(r.text, name_lower)
                if not gid and clean_name != name_lower:
                    gid = extract_group_id_from_response(r.text, clean_name)
                    
                if gid:
                    print(f'[FIND_ID] Найдено на факультете {fid}! ID: {gid}')
                    return gid, group_name, logs
        except Exception as e:
            continue

    return None, None, logs


def extract_group_id_from_response(text: str, name_lower: str) -> str | None:
    try:
        import json
        data = json.loads(text)
        items = data if isinstance(data, list) else data.get('groups', data.get('data', []))
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    item_name = str(item.get('name','') or item.get('group','') or item.get('title','')).lower().strip()
                    if name_lower in item_name or item_name in name_lower:
                        return str(item.get('id') or item.get('group_id') or item.get('ID',''))
        elif isinstance(items, dict):
            for key, val in items.items():
                if name_lower in str(val).lower():
                    return str(key)
    except Exception:
        pass

    try:
        soup = BeautifulSoup(text, 'html.parser')
        for opt in soup.find_all(['option', 'li', 'a']):
            opt_text = opt.get_text(strip=True).lower()
            if name_lower in opt_text:
                val = opt.get('value') or opt.get('data-id') or opt.get('id')
                if val and val.isdigit():
                    return val
    except Exception:
        pass

    return None


# ─── ПАРСЕР РАСПИСАНИЯ ────────────────────────────────────────────────────────
def parse_schedule_html(html: str, group: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    header = f'Расписание группы {group}'
    for sel in ['.rasp_head','h1','h2','.title','.schedule-title','caption']:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(' ', strip=True)
            if t and len(t) > 3: header = t; break

    days = [
        {'name':'Понедельник','header':'','lessons':[]},
        {'name':'Вторник',    'header':'','lessons':[]},
        {'name':'Среда',      'header':'','lessons':[]},
        {'name':'Четверг',    'header':'','lessons':[]},
        {'name':'Пятница',    'header':'','lessons':[]},
        {'name':'Суббота',    'header':'','lessons':[]},
    ]
    DAY_NAMES = ['понедельник','вторник','среда','четверг','пятница','суббота']
    DAY_SHORT  = ['пн','вт','ср','чт','пт','сб']
    has_data = False

    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 2: continue

        hdr = rows[0].find_all(['th','td'])
        day_map = {}
        for ci, cell in enumerate(hdr):
            txt = cell.get_text(strip=True).lower()
            for di, (full, short) in enumerate(zip(DAY_NAMES, DAY_SHORT)):
                if full in txt or txt.startswith(short):
                    day_map[ci] = di
                    days[di]['header'] = cell.get_text(strip=True)
        if not day_map:
            for ci in range(min(len(hdr), 6)):
                day_map[ci] = ci

        for row in rows[1:]:
            cells = row.find_all('td')
            for ci, cell in enumerate(cells):
                di = day_map.get(ci)
                if di is None or di >= 6: continue
                lines = [l.strip() for l in cell.get_text('\n').split('\n') if l.strip()]
                if not lines: continue
                if any(w in cell.get_text().lower() for w in ['отсутствует','пар нет']): continue

                num = time_str = subject = teacher = room = ''
                rest = []
                for ln in lines:
                    if re.search(r'\d{1,2}[:\.]\d{2}', ln): time_str = ln
                    elif re.match(r'^\d+[\.)]?\s*$', ln): num = ln.strip('.)').strip()
                    else: rest.append(ln)
                if not rest and not time_str: continue

                final = []
                for ln in rest:
                    if re.search(r'\b(каб|ауд|гк|лк|пр|лаб|стад|зал)\b', ln, re.I): room = ln
                    else: final.append(ln)

                if final:
                    has_data = True
                    bold = cell.find(['b','strong'])
                    if bold:
                        subject = bold.get_text(strip=True)
                        teacher = ', '.join(l for l in final if l.lower() != subject.lower())
                    else:
                        subject = final[0]
                        if len(final) > 1:
                            teacher = ', '.join(final[1:])

                if subject or time_str:
                    days[di]['lessons'].append({
                        'num': num or str(len(days[di]['lessons'])+1),
                        'time': time_str, 'subject': subject,
                        'teacher': teacher, 'room': room,
                    })

    for d in days:
        d['lessons'].sort(key=lambda x: x['num'].zfill(2))

    return {'header': header, 'days': days, 'success': has_data}


def call_schedule_by_id(group_id: str, week: str, group_name: str) -> dict | None:
    payload = {'id': group_id, 'week': week}
    endpoints = ['getShedule.php', 'getSheduleBody.php', 'getScheduleBody.php']
    
    for ep in endpoints:
        try:
            r = requests.post(f'{PHP_URL}/{ep}', data=payload, headers=HEADERS, timeout=4)
            if r.status_code == 200 and len(r.text) > 200 and "cannot select db" not in r.text.lower():
                parsed = parse_schedule_html(r.text, group_name)
                if parsed['success']: return parsed
        except Exception:
            continue
    return None


# ─── ГЛАВНЫЙ ЭНДПОИНТ ────────────────────────────────────────────────────────
@app.route('/api/schedule/by_name', methods=['POST','OPTIONS'])
def schedule_by_name():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.json or {}
    group = data.get('group_name','').strip()
    week  = str(data.get('week','0'))
    if not group: return jsonify({'error': 'Не указана группа'})

    group_id, _, diagnostic_logs = find_group_id(group)

    if not group_id:
        return jsonify({
            'header': f'Группа "{group}" не найдена',
            'days': [],
            'debug_server_logs': diagnostic_logs
        })

    result = call_schedule_by_id(group_id, week, group)

    if result and result['success']:
        return jsonify({'header': result['header'], 'days': result['days']})

    return jsonify({
        'header': f'Расписание для группы {group} временно недоступно',
        'days': [],
        'debug_server_logs': diagnostic_logs
    })


@app.route('/api/schedule/by_id', methods=['POST','OPTIONS'])
def schedule_by_id():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.json or {}
    group_id   = str(data.get('group_id','')).strip()
    group_name = data.get('group_name', f'группа {group_id}')
    week       = str(data.get('week','0'))
    if not group_id: return jsonify({'error': 'Не указан group_id'})

    result = call_schedule_by_id(group_id, week, group_name)

    if result and result['success']:
        return jsonify({'header': result['header'], 'days': result['days']})

    return jsonify({'header': f'Расписание не найдено (id={group_id})', 'days': []})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
