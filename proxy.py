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
    'Accept': '*/*',
    'Accept-Language': 'ru-RU,ru;q=0.9',
    'Origin':  EDU_URL,
    'Referer': EDU_URL + '/',
    'X-Requested-With': 'XMLHttpRequest',
}

AUTH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
}

# ─── CORS ─────────────────────────────────────────────────────────────────────
@app.after_request
def add_cors(r):
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return r

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok'})

# ─── AUTH ─────────────────────────────────────────────────────────────────────
@app.route('/api/login', methods=['POST','OPTIONS'])
def login():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.json or {}
    s = requests.Session()
    s.headers.update(AUTH_HEADERS)
    try:
        login_url = f'{BASE_URL}/Account/Login'
        r = s.get(login_url, timeout=15)
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
        resp = s.post(login_url, data=payload, headers={**AUTH_HEADERS,'Referer':login_url},
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

# ─── ПРЕДМЕТЫ И ОЦЕНКИ ────────────────────────────────────────────────────────
@app.route('/api/subjects', methods=['GET','OPTIONS'])
def subjects():
    if request.method == 'OPTIONS': return jsonify({}), 200
    if 'cookies' not in session: return jsonify({'error': 'auth'}), 401
    s = requests.Session()
    s.cookies.update(requests.utils.cookiejar_from_dict(session['cookies']))
    s.headers.update(AUTH_HEADERS)
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
    s.headers.update(AUTH_HEADERS)
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
                lessons.append({
                    'date': date,
                    'theme': theme or 'Занятие',
                    'grade': cells[-1].get_text(strip=True) or '-'
                })
        return jsonify({'lessons': lessons})
    except Exception as e:
        return jsonify({'error': str(e)})

# ─── РАСПИСАНИЕ ───────────────────────────────────────────────────────────────

@app.route('/api/schedule/groups', methods=['GET','OPTIONS'])
def schedule_groups():
    if request.method == 'OPTIONS': return jsonify({}), 200
    faculty = request.args.get('faculty', '26')
    try:
        r = requests.get(
            f'{EDU_URL}/getList.php?faculty={faculty}',
            headers=HEADERS, timeout=15
        )
        html = r.text
        groups = []
        # Формат: <div style = 'display: none;'>ID</div><a ...>NAME</a>
        # (с пробелами вокруг =)
        pattern = re.compile(
            r"<div[^>]*display\s*:\s*none[^>]*>\s*(\d+)\s*</div>\s*<a[^>]*>([^<]+)</a>",
            re.IGNORECASE
        )
        for m in pattern.finditer(html):
            groups.append({'id': m.group(1).strip(), 'name': m.group(2).strip()})
        return jsonify({'groups': groups})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/schedule/week_header', methods=['GET','OPTIONS'])
def schedule_week_header():
    if request.method == 'OPTIONS': return jsonify({}), 200
    group_id = request.args.get('id')
    week = request.args.get('week', '0')
    try:
        r = requests.get(
            f'{EDU_URL}/getSheduleHeader.php?type=2&id={group_id}&week={week}',
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        return jsonify({'header': text})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/schedule/timetable', methods=['GET','OPTIONS'])
def schedule_timetable():
    if request.method == 'OPTIONS': return jsonify({}), 200
    group_id = request.args.get('id')
    week = request.args.get('week', '0')
    try:
        r = requests.get(
            f'{EDU_URL}/getShedule.php?type=2&id={group_id}&week={week}',
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, 'html.parser')
        days = [
            {'name':'Понедельник','header':'','lessons':[]},
            {'name':'Вторник',    'header':'','lessons':[]},
            {'name':'Среда',      'header':'','lessons':[]},
            {'name':'Четверг',    'header':'','lessons':[]},
            {'name':'Пятница',    'header':'','lessons':[]},
            {'name':'Суббота',    'header':'','lessons':[]},
        ]
        table = soup.find('table')
        if not table:
            return jsonify({'days': days})
        rows = table.find_all('tr')

        # Заголовки дней
        if rows:
            headers = rows[0].find_all('th')
            for i, th in enumerate(headers):
                if i < len(days):
                    days[i]['header'] = th.get_text(strip=True)

        # Пары
        for row in rows[1:]:
            cells = row.find_all('td')
            for i, cell in enumerate(cells):
                if i >= len(days): break
                text = cell.get_text(separator='\n', strip=True)
                if not text: continue

                cell_html = str(cell)
                time_match = re.search(r'(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})', cell_html)
                time_str = f"{time_match.group(1)} – {time_match.group(2)}" if time_match else ''

                num_match = re.match(r'^(\d+)\.', text)
                num = num_match.group(1) if num_match else ''

                room_match = re.search(r'(?:Пр|пр)\s*([^\s<,\n]+)', cell_html)
                room = room_match.group(1) if room_match else ''

                bold = cell.find('b') or cell.find('strong')
                subject = bold.get_text(strip=True) if bold else ''

                teacher_match = re.search(r'([А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.[А-ЯЁ]\.(?:\(\d+\))?)', text)
                teacher = teacher_match.group(1) if teacher_match else ''

                if subject or time_str:
                    days[i]['lessons'].append({
                        'num': num,
                        'time': time_str,
                        'subject': subject,
                        'teacher': teacher,
                        'room': room,
                    })

        return jsonify({'days': days})
    except Exception as e:
        return jsonify({'error': str(e)})
