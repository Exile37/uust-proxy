from flask import Flask, request, jsonify, session
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

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
# ЧИСТЫЙ РАБОЧИЙ ЭНДПОИНТ ЧЕРЕЗ ОФИЦИАЛЬНОЕ API ВУЗА
# ==========================================

@app.route('/api/schedule/by_name', methods=['POST', 'OPTIONS'])
def schedule_by_name():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
        
    data = request.json or {}
    group_name = data.get('group_name', '').strip()
    week = str(data.get('week', '0'))
    
    if not group_name:
        return jsonify({'error': 'Не указано имя группы'})
        
    try:
        # Шаг 1. Получаем ID группы через внутренний поиск API
        search_url = f'{EDU_URL}/api/search?query={group_name}'
        search_res = requests.get(search_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10).json()
        
        # Если по исходному имени ничего нет, пробуем заменить раскладку К/K
        if not search_res or not search_res.get('groups'):
            alt_name = group_name.replace('К', 'K') if 'К' in group_name else group_name.replace('K', 'К')
            search_url = f'{EDU_URL}/api/search?query={alt_name}'
            search_res = requests.get(search_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10).json()

        if not search_res or not search_res.get('groups'):
            return jsonify({'header': f'Группа "{group_name}" не найдена', 'days': []})
            
        # Берем ID первой совпавшей группы
        group_id = search_res['groups'][0]['id']
        real_group_title = search_res['groups'][0]['title']
        
        # Шаг 2. Запрашиваем чистое JSON-расписание по этому ID
        schedule_url = f'{EDU_URL}/api/schedule?id={group_id}&week={week}'
        api_data = requests.get(schedule_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10).json()
        
        # Шаг 3. Преобразуем формат API вуза в формат нашего мобильного приложения
        # API возвращает 'week_range' (например "25.05.2026 - 31.05.2026")
        week_range = api_data.get('week_range', '')
        header_text = f"Группа {real_group_title} ({week_range})" if week_range else f"Группа {real_group_title}"
        
        # Массив соответствия индексов дней недели (сервер присылает 1=Понедельник, 2=Вторник...)
        day_names = {
            1: 'Понедельник',
            2: 'Вторник',
            3: 'Среда',
            4: 'Четверг',
            5: 'Пятница',
            6: 'Суббота'
        }
        
        days_dict = {i: {'name': name, 'header': '', 'lessons': []} for i, name in day_names.items()}
        
        # Наполняем пары
        for lesson in api_data.get('lessons', []):
            d_num = lesson.get('day') # Число от 1 до 6
            if d_num in days_dict:
                # Добавляем дату дня, если она пришла из API
                if lesson.get('date') and not days_dict[d_num]['header']:
                    days_dict[d_num]['header'] = lesson.get('date')
                    
                # Формируем красивую строку кабинета
                room_info = lesson.get('room', '')
                if lesson.get('building'):
                    room_info += f"-{lesson.get('building')}"
                
                # Тип занятия (Практика, Лекция и т.д.)
                l_type = lesson.get('type', '')
                subject_full = lesson.get('subject', 'Занятие')
                if l_type:
                    subject_full += f" ({l_type})"

                days_dict[d_num]['lessons'].append({
                    'num': str(lesson.get('number', '')),
                    'time': f"{lesson.get('time_start', '')} - {lesson.get('time_end', '')}",
                    'subject': subject_full,
                    'teacher': lesson.get('teacher', ''),
                    'room': room_info
                })
                
        # Сортируем уроки внутри дней по их номеру
        for d in days_dict.values():
            d['lessons'].sort(key=lambda x: x['num'])
            
        return jsonify({
            'header': header_text,
            'days': list(days_dict.values())
        })
        
    except Exception as e:
        return jsonify({'error': f'Ошибка API вуза: {str(e)}'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
