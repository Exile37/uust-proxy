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
# УЛЬТИМАТИВНЫЙ ПАРСЕР РАСПИСАНИЯ ПО ИМЕНИ
# ==========================================

def parse_schedule_html(html_text, group_name):
    soup = BeautifulSoup(html_text, 'html.parser')
    
    header_text = f"Группа {group_name}"
    rasp_head = soup.find(class_='rasp_head')
    if rasp_head:
        header_text = rasp_head.get_text(separator=' ', strip=True)
        
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
        return header_text, days, False
        
    rows = table.find_all('tr')
    if not rows:
        return header_text, days, False

    # Сбор дат из шапки таблицы
    headers = rows[0].find_all(['th', 'td'])
    for i, th in enumerate(headers):
        if i < len(days):
            days[i]['header'] = th.get_text(strip=True)

    has_data = False
    for row in rows[1:]:
        cells = row.find_all('td')
        for i, cell in enumerate(cells):
            if i >= len(days):
                break
            
            # Собираем все строки текста внутри ячейки
            lines = [line.strip() for line in cell.get_text(separator='\n').split('\n') if line.strip()]
            if not lines:
                continue

            num = ""
            time_str = ""
            subject = ""
            teacher = ""
            room = ""

            # Извлекаем служебную информацию (время, номер пары)
            clean_lines = []
            for line in lines:
                if re.search(r'\d{2}:\d{2}', line):
                    time_str = line
                elif re.match(r'^\d+\s*\.$', line) or (line.isdigit() and len(line) == 1):
                    num = line.replace('.', '').strip()
                else:
                    clean_lines.append(line)

            if not clean_lines:
                continue

            # Определяем кабинет
            final_lines = []
            for line in clean_lines:
                if any(x in line.lower() for x in ['пр', 'каб', 'ауд', 'лр', 'лек']):
                    room = line
                else:
                    final_lines.append(line)

            # Распределяем оставшиеся строки на Предмет и Преподавателя
            if final_lines:
                has_data = True
                bold_tag = cell.find(['b', 'strong'])
                if bold_tag:
                    subject = bold_tag.get_text(strip=True)
                    # Всё, что не является предметом — это преподаватель
                    t_parts = [l for l in final_lines if l.lower() != subject.lower()]
                    if t_parts:
                        teacher = " ".join(t_parts)
                else:
                    # Если жирного текста нет, первая строка — предмет, остальное — преподаватель
                    subject = final_lines[0]
                    if len(final_lines) > 1:
                        teacher = " ".join(final_lines[1:])

            if subject or time_str:
                days[i]['lessons'].append({
                    'num': num if num else str(len(days[i]['lessons']) + 1),
                    'time': time_str if time_str else "—",
                    'subject': subject if subject else "Занятие",
                    'teacher': teacher if teacher else "",
                    'room': room if room else "",
                })

    return header_text, days, has_data

@app.route('/api/schedule/by_name', methods=['GET', 'OPTIONS'])
def schedule_by_name():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
        
    group_name = request.args.get('group_name', '').strip()
    week = request.args.get('week', '0')
    
    if not group_name:
        return jsonify({'error': 'Не указано имя группы'})
        
    try:
        # Стратегия 1: Ищем имя в оригинальном виде (например, К-4М21)
        url1 = f'{EDU_URL}/index.php?group_name={group_name}&week={week}'
        r = requests.get(url1, headers=EDU_HEADERS, timeout=12)
        header, days, success = parse_schedule_html(r.text, group_name)
        
        # Стратегия 2: Если парсер пуст, пробуем подменить К (русское) на K (английское) или наоборот
        if not success:
            alt_name = group_name
            if 'К' in group_name:
                alt_name = group_name.replace('К', 'K') # Рус в Англ
            elif 'K' in group_name:
                alt_name = group_name.replace('K', 'К') # Англ в Рус
                
            if alt_name != group_name:
                url2 = f'{EDU_URL}/index.php?group_name={alt_name}&week={week}'
                r2 = requests.get(url2, headers=EDU_HEADERS, timeout=12)
                h2, d2, s2 = parse_schedule_html(r2.text, group_name)
                if s2:
                    header, days = h2, d2

        return jsonify({'header': header, 'days': days})
        
    except Exception as e:
        return jsonify({'error': str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
