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

@app.after_request
def add_cors(r):
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return r

# ─── АВТОРИЗАЦИЯ И ДАННЫЕ ────────────────────────────────────────────────────
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
        resp = s.post(login_url, data=payload, headers={**HEADERS,'Referer':login_url}, allow_redirects=True, timeout=10)
        if any('.AspNet' in c.name for c in s.cookies) or 'Выйти' in resp.text:
            session['cookies'] = requests.utils.dict_from_cookiejar(s.cookies)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Неверный логин или пароль'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─── ПОИСК ID ГРУППЫ (HTML ПАРСЕР) ──────────────────────────────────────────
def extract_group_id_from_response(text: str, name_lower: str) -> str | None:
    clean_name = name_lower.replace('к-', '').strip()
    soup = BeautifulSoup(text, 'html.parser')
    for item in soup.find_all('li'):
        a_tag = item.find('a')
        if not a_tag: continue
        group_text = a_tag.get_text(strip=True).lower()
        if name_lower == group_text or clean_name == group_text:
            div_tag = item.find('div', style=lambda x: x and 'display: none' in x)
            if div_tag: return div_tag.get_text(strip=True)
    return None

def find_group_id(group_name: str) -> tuple[str|None, str|None, dict]:
    name_lower = group_name.lower().strip()
    logs = {'attempts': []}
    s = requests.Session()
    s.headers.update(HEADERS)
    # Перебор факультетов (26 - приоритетный)
    faculty_ids = [26] + [i for i in range(1, 41) if i != 26]
    for fid in faculty_ids:
        try:
            r = s.get(f'{PHP_URL}/getList.php', params={'faculty': str(fid)}, timeout=2)
            if r.status_code == 200:
                gid = extract_group_id_from_response(r.text, name_lower)
                if gid: return gid, group_name, logs
        except Exception: continue
    return None, None, logs

# ─── ПАРСЕР РАСПИСАНИЯ ────────────────────────────────────────────────────────
def parse_schedule_html(html: str, group: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    days = [{'name': d, 'lessons': []} for d in ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота']]
    has_data = False
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 2: continue
        for row in rows[1:]:
            cells = row.find_all('td')
            for ci, cell in enumerate(cells):
                if ci >= 6: continue
                text = cell.get_text('\n', strip=True)
                if text and 'пар нет' not in text.lower():
                    has_data = True
                    days[ci]['lessons'].append({'subject': text})
    return {'header': f'Расписание {group}', 'days': days, 'success': has_data}

def call_schedule_by_id(group_id: str, week: str, group_name: str) -> dict | None:
    payload = {'id': group_id, 'week': week}
    for ep in ['getShedule.php', 'getSheduleBody.php']:
        try:
            r = requests.post(f'{PHP_URL}/{ep}', data=payload, headers=HEADERS, timeout=4)
            if r.status_code == 200 and len(r.text) > 100:
                parsed = parse_schedule_html(r.text, group_name)
                if parsed['success']: return parsed
        except Exception: continue
    return None

# ─── ЭНДПОИНТЫ ───────────────────────────────────────────────────────────────
@app.route('/api/schedule/by_name', methods=['POST','OPTIONS'])
def schedule_by_name():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.json or {}
    group = data.get('group_name','').strip()
    week  = str(data.get('week','0'))
    group_id, _, _ = find_group_id(group)
    if not group_id: return jsonify({'header': 'Группа не найдена', 'days': []})
    result = call_schedule_by_id(group_id, week, group)
    return jsonify(result if result else {'header': 'Ошибка загрузки', 'days': []})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
