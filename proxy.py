from flask import Flask, request, jsonify, session
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

app = Flask(__name__)
app.secret_key = 'super_secret_key_123'

BASE_URL = 'https://account.str.uust.ru'
EDU_URL = 'https://edu.str.uust.ru'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0 Safari/537.36'
    )
}

EDU_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': f'{EDU_URL}/',
}

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok'})

@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    data = request.json
    username = data.get('username')
    password = data.get('password')
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        login_url = f'{BASE_URL}/Account/Login'
        r = s.get(login_url, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        form = soup.find('form')
        if not form:
            return jsonify({'success': False, 'error': 'Форма не найдена'})
        payload = {}
        for inp in form.find_all('input'):
            name = inp.get('name')
            if not name:
                continue
            payload[name] = inp.get('value', '')
        login_field = None
        password_field = None
        for key in payload.keys():
            low = key.lower()
            if 'login' in low or 'email' in low or 'username' in low:
                login_field = key
            if 'password' in low:
                password_field = key
        if not login_field:
            login_field = 'Email'
        if not password_field:
            password_field = 'Password'
        payload[login_field] = username
        payload[password_field] = password
        headers = HEADERS.copy()
        headers['Referer'] = login_url
        resp = s.post(login_url, data=payload, headers=headers,
                      allow_redirects=True, timeout=15)
        auth = any('.AspNet' in cookie.name for cookie in s.cookies)
        if auth or 'Выйти' in resp.text:
            session['cookies'] = requests.utils.dict_from_cookiejar(s.cookies)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Неверный логин или пароль'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/subjects', methods=['GET', 'OPTIONS'])
def subjects():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    if 'cookies' not in session:
        return jsonify({'error': 'auth'}), 401
    s = requests.Session()
    cookiejar = requests.utils.cookiejar_from_dict(session['cookies'])
    s.cookies.update(cookiejar)
    s.headers.update(HEADERS)
    try:
        r = s.get(f'{BASE_URL}/Journals/DisciplinesStudent', timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        result = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            if '/Journals/DisciplineGrades' not in href:
                continue
            row = link.find_parent('tr')
            if not row:
                continue
            cells = row.find_all('td')
            name = cells[1].get_text(strip=True) if len(cells) > 1 else 'Без названия'
            semester = cells[2].get_text(strip=True) if len(cells) > 2 else '—'
            teacher = cells[3].get_text(strip=True) if len(cells) > 3 else '—'
            result.append({'name': name, 'semestr': semester, 'teacher': teacher, 'url': href})
        unique = {item['url']: item for item in result}
        return jsonify({'subjects': list(unique.values())})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/grades', methods=['GET', 'OPTIONS'])
def grades():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    if 'cookies' not in session:
        return jsonify({'error': 'auth'}), 401
    url = request.args.get('url')
    if not url.startswith('/Journals/'):
        return jsonify({'error': 'неверный URL'})
    s = requests.Session()
    cookiejar = requests.utils.cookiejar_from_dict(session['cookies'])
    s.cookies.update(cookiejar)
    s.headers.update(HEADERS)
    try:
        full_url = urljoin(BASE_URL, url)
        r = s.get(full_url, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        lessons = []
        table = soup.find('table')
        if table:
            for row in table.find_all('tr'):
                cells = row.find_all(['td', 'th'])
                if len(cells) < 3:
                    continue
                date = cells[1].get_text(strip=True)
                if not date or date == '№' or 'Дата' in date:
                    continue
                content = cells[2].get_text(separator=' ', strip=True)
                theme = content.split('Домашнее задание:')[0].replace('Тема:', '').strip()
                grade = cells[-1].get_text(strip=True)
                lessons.append({
                    'date': date,
                    'theme': theme if theme else 'Занятие',
                    'grade': grade if grade else '-'
                })
        return jsonify({'lessons': lessons})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/logout', methods=['POST', 'OPTIONS'])
def logout():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    session.clear()
    return jsonify({'success': True})

# ==========================================
# НОВОЕ РАСПИСАНИЕ ПО ПРЯМОЙ ССЫЛКЕ ГРУППЫ
# ==========================================

@app.route('/api/schedule/by_name', methods=['GET', 'OPTIONS'])
def schedule_by_name():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
        
    group_name = request.args.get('group_name', '').strip()
    week = request.args.get('week', '0')
    
    if not group_name:
        return jsonify({'error': 'Не указано имя группы'})
        
    try:
        # Стучимся на прямую ссылку расписания, которую обрабатывает сервер вуза
        direct_url = f'{EDU_URL}/index.php?group_name={group_name}&week={week}'
        r = requests.get(direct_url, headers=EDU_HEADERS, timeout=15)
        
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # 1. Извлекаем текстовый заголовок (Даты недели)
        header_text = f"Группа {group_name}"
        rasp_head = soup.find(class_='rasp_head')
        if rasp_head:
            header_text = rasp_head.get_text(separator=' ', strip=True)
            
        # 2. Парсим сетку расписания
        days = [
            {'name': 'Понедельник', 'lessons': []},
            {'name': 'Вторник', 'lessons': []},
            {'name': 'Среда', 'lessons': []},
            {'name': 'Четверг', 'lessons': []},
            {'name': 'Пятница', 'lessons': []},
            {'name': 'Суббота', 'lessons': []},
        ]
        
        table = soup.find('table')
        if not table:
            return jsonify({'header': header_text, 'days': days})
            
        rows = table.find_all('tr')
        if rows:
            # Обновляем даты в заголовках дней, если они есть в <th>
            headers = rows[0].find_all('th')
            for i, th in enumerate(headers):
                if i < len(days):
                    days[i]['header'] = th.get_text(strip=True)

        for row in rows[1:]:
            cells = row.find_all('td')
            for i, cell in enumerate(cells):
                if i >= len(days):
                    break
                text = cell.get_text(separator='\n', strip=True)
                if not text:
                    continue

                # Регулярные выражения для вытаскивания деталей пары
                time_match = re.search(r'(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})', cell.decode_contents())
                time_str = f"{time_match.group(1)} – {time_match.group(2)}" if time_match else ''

                num_match = re.match(r'^(\d+)\.', text)
                num = num_match.group(1) if num_match else ''

                room_match = re.search(r'(?:Пр|пр)\s*([^\s<,\n]+)', cell.decode_contents())
                room = room_match.group(1) if room_match else ''

                bold = cell.find('b') or cell.find('strong')
                subject = bold.get_text(strip=True) if bold else ''

                teacher_match = re.search(r'([А-ЯЁ][а-яё]+\s+[А-Я.]{3,5})', text)
                teacher = teacher_match.group(1) if teacher_match else ''

                if subject or time_str:
                    days[i]['lessons'].append({
                        'num': num,
                        'time': time_str,
                        'subject': subject,
                        'teacher': teacher,
                        'room': room,
                    })

        return jsonify({
            'header': header_text,
            'days': days
        })
        
    except Exception as e:
        return jsonify({'error': str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
