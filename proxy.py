from flask import Flask, request, jsonify, session
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

app = Flask(__name__)
app.secret_key = 'super_secret_key_123'

BASE_URL = 'https://account.str.uust.ru'
EDU_URL = 'https://edu.str.uust.ru'

# Имитируем реальный браузер на полную мощность
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
}

EDU_POST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': EDU_URL,
    'Referer': f'{EDU_URL}/index.php',
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
    data = request.json or {}
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


# ========================================================
#   НОВЫЙ НЕУБИВАЕМЫЙ ПАРСЕР СТРАНИЦЫ РАСПИСАНИЯ (SCRAPER)
# ========================================================

def parse_html_schedule_direct(html_text, group_name):
    soup = BeautifulSoup(html_text, 'html.parser')
    
    # Пытаемся вытащить заголовок (неделю)
    header_text = f"Расписание группы {group_name}"
    rasp_head = soup.find(class_='rasp_head')
    if rasp_head:
        header_text = rasp_head.get_text(separator=' ', strip=True)
    elif soup.find('h1'):
        header_text = soup.find('h1').get_text(strip=True)

    days = [
        {'name': 'Понедельник', 'header': '', 'lessons': []},
        {'name': 'Вторник', 'header': '', 'lessons': []},
        {'name': 'Среда', 'header': '', 'lessons': []},
        {'name': 'Четверг', 'header': '', 'lessons': []},
        {'name': 'Пятница', 'header': '', 'lessons': []},
        {'name': 'Суббота', 'header': '', 'lessons': []},
    ]

    # Если на странице есть таблицы
    tables = soup.find_all('table')
    if not tables:
        return header_text, days, False

    has_data = False
    
    # Перебираем все таблицы на странице (иногда расписание завернуто в несколько таблиц)
    for table in tables:
        rows = table.find_all('tr')
        if not rows:
            continue
            
        # Попытка вытащить даты дней из первой строки таблицы
        first_row_cells = rows[0].find_all(['th', 'td'])
        for idx, cell in enumerate(first_row_cells):
            if idx < len(days):
                txt = cell.get_text(strip=True)
                if any(d in txt for d in ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Понедельник']):
                    days[idx]['header'] = txt

        # Парсим строки с парами
        for row in rows[1:]:
            cells = row.find_all('td')
            if not cells:
                continue
                
            for idx, cell in enumerate(cells):
                if idx >= len(days):
                    break
                
                # Дробим содержимое ячейки на отдельные текстовые строки
                lines = [l.strip() for l in cell.get_text(separator='\n').split('\n') if l.strip()]
                if not lines:
                    continue

                # Проверяем, нет ли здесь заглушки типа "отсутствует"
                cell_text_lower = cell.get_text().lower()
                if 'отсутствует' in cell_text_lower or 'пар нет' in cell_text_lower:
                    continue

                num = ""
                time_str = ""
                subject = ""
                teacher = ""
                room = ""

                # Извлекаем время и номер пары
                clean_lines = []
                for line in lines:
                    if re.search(r'\d{2}[:\.]\d{2}', line):
                        time_str = line
                    elif re.match(r'^\d+\s*\.$', line) or (line.isdigit() and len(line) == 1):
                        num = line.replace('.', '').strip()
                    else:
                        clean_lines.append(line)

                if not clean_lines:
                    continue

                # Извлекаем кабинет/аудиторию
                final_lines = []
                for line in clean_lines:
                    if any(x in line.lower() for x in ['каб', 'ауд', 'гк', 'лк', 'пр', 'лаб', 'стадион']):
                        room = line
                    else:
                        final_lines.append(line)

                # Выделяем предмет и преподавателя
                if final_lines:
                    has_data = True
                    bold_item = cell.find(['b', 'strong'])
                    if bold_item:
                        subject = bold_item.get_text(strip=True)
                        teachers_list = [l for l in final_lines if l.lower() != subject.lower()]
                        teacher = ", ".join(teachers_list) if teachers_list else ""
                    else:
                        subject = final_lines[0]
                        if len(final_lines) > 1:
                            teacher = ", ".join(final_lines[1:])

                if subject or time_str:
                    days[idx]['lessons'].append({
                        'num': num if num else str(len(days[idx]['lessons']) + 1),
                        'time': time_str if time_str else "08:30 - 10:00",
                        'subject': subject,
                        'teacher': teacher,
                        'room': room
                    })

    # Сортируем пары внутри дней
    for d in days:
        d['lessons'].sort(key=lambda x: x['num'])

    return header_text, days, has_data


@app.route('/api/schedule/by_name', methods=['POST', 'OPTIONS'])
def schedule_by_name():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
        
    data = request.json or {}
    group_name = data.get('group_name', '').strip()
    week = str(data.get('week', '0'))
    
    if not group_name:
        return jsonify({'error': 'Не указано имя группы'})
        
    # Формируем список вариантов написания группы
    variants = [group_name]
    if not group_name.upper().startswith('К-'):
        variants.append(f"К-{group_name}")
    if 'М' in group_name.upper():
        variants.append(group_name.upper().replace('М', 'M')) # латинская М
        
    for g_variant in variants:
        try:
            print(f"[HTML_SCRAPE] Пробуем прямую отправку формы для группы: {g_variant}")
            
            # Делаем классический POST запроса формы прямо на index.php
            form_payload = {
                'group_name': g_variant,
                'week': week,
                'type': '2'  # Поиск по группе
            }
            
            r = requests.post(f'{EDU_URL}/index.php', data=form_payload, headers=EDU_HEADERS, timeout=12)
            
            header, days, success = parse_html_schedule_direct(r.text, g_variant)
            
            if success:
                print(f"[HTML_SCRAPE] Успешно спарсили расписание со страницы для {g_variant}!")
                return jsonify({'header': header, 'days': days})
                
        except Exception as e:
            print(f"[HTML_SCRAPE] Ошибка при обработке варианта {g_variant}: {str(e)}")
            continue
            
    return jsonify({
        'header': f'Не удалось найти расписание группы "{group_name}" на сайте',
        'days': []
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
