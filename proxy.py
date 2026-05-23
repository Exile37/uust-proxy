from flask import Flask, request, jsonify, session
import requests
from bs4 import BeautifulSoup
import re

app = Flask(__name__)
app.secret_key = 'stable_key_for_session_persistence'

BASE_URL = 'https://account.str.uust.ru'
EDU_URL  = 'https://edu.str.uust.ru'
PHP_URL  = f'{EDU_URL}/php'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
    'X-Requested-With': 'XMLHttpRequest',
}

@app.after_request
def add_cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return r

# ─── АВТОРИЗАЦИЯ ─────────────────────────────────────────────────────────────
@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS': return jsonify({}), 200
    data = request.json or {}
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        r = s.get(f'{BASE_URL}/Account/Login', timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        form = soup.find('form')
        if not form: return jsonify({'success': False, 'error': 'Нет формы'})
        payload = {i.get('name'): i.get('value', '') for i in form.find_all('input') if i.get('name')}
        for k in payload:
            if 'user' in k.lower() or 'email' in k.lower(): payload[k] = data.get('username')
            if 'pass' in k.lower(): payload[k] = data.get('password')
        resp = s.post(f'{BASE_URL}/Account/Login', data=payload, allow_redirects=True, timeout=10)
        if any('.AspNet' in c.name for c in s.cookies) or 'Выйти' in resp.text:
            session['cookies'] = requests.utils.dict_from_cookiejar(s.cookies)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Неверные данные'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─── ПРЕДМЕТЫ ───────────────────────────────────────────────────────────────
@app.route('/api/subjects', methods=['GET', 'OPTIONS'])
def subjects():
    if 'cookies' not in session: return jsonify({'error': 'auth'}), 401
    s = requests.Session()
    s.cookies.update(requests.utils.cookiejar_from_dict(session['cookies']))
    try:
        r = s.get(f'{BASE_URL}/Journals/DisciplinesStudent', timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        result = []
        for a in soup.find_all('a', href=re.compile(r'/Journals/DisciplineGrades')):
            row = a.find_parent('tr')
            cells = row.find_all('td') if row else []
            result.append({'name': cells[1].text.strip() if len(cells)>1 else 'Дисциплина', 'url': a['href']})
        return jsonify({'subjects': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─── ПОИСК И РАСПИСАНИЕ ─────────────────────────────────────────────────────
def extract_id(text, name):
    soup = BeautifulSoup(text, 'html.parser')
    for li in soup.find_all('li'):
        a = li.find('a')
        if a and (name.lower() in a.text.lower()):
            div = li.find('div', style=lambda x: x and 'display: none' in x)
            return div.text.strip() if div else None
    return None

def parse_schedule(html, group):
    soup = BeautifulSoup(html, 'html.parser')
    days = []
    for table in soup.find_all('table'):
        day_info = {'name': (table.find('caption') or table.find('th')).text.strip() if table.find('caption') else "День", 'lessons': []}
        for row in table.find_all('tr')[1:]:
            cells = row.find_all('td')
            if len(cells) >= 2:
                day_info['lessons'].append({'time': cells[0].text.strip(), 'subject': cells[1].text.strip()})
        days.append(day_info)
    return {'header': f'Расписание {group}', 'days': days, 'success': len(days) > 0}

@app.route('/api/schedule/by_name', methods=['POST', 'OPTIONS'])
def schedule():
    data = request.json or {}
    group_name = data.get('group_name', '')
    s = requests.Session()
    
    # 1. Поиск ID
    group_id = None
    for fid in range(1, 40):
        try:
            r = s.get(f'{PHP_URL}/getList.php', params={'faculty': fid}, timeout=1)
            group_id = extract_id(r.text, group_name)
            if group_id: break
        except: continue
        
    if not group_id: return jsonify({'header': 'Группа не найдена', 'days': []})

    # 2. Получение расписания
    try:
        r = s.post(f'{PHP_URL}/getShedule.php', data={'id': group_id, 'week': 0}, timeout=3)
        return jsonify(parse_schedule(r.text, group_name))
    except:
        return jsonify({'header': 'Ошибка загрузки расписания', 'days': []})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
