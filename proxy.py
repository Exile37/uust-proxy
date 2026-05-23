from flask import Flask, request, jsonify, session
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
import json

app = Flask(__name__)
app.secret_key = 'super_secret_key_123'

BASE_URL = 'https://account.str.uust.ru'
EDU_URL = 'https://edu.str.uust.ru'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'Connection': 'keep-alive',
}


# ─────────────────────────────────────────────
#  CORS
# ─────────────────────────────────────────────
@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response


# ─────────────────────────────────────────────
#  ВСПОМОГАЛКИ
# ─────────────────────────────────────────────
def make_edu_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def normalize_group(name: str) -> list[str]:
    """Возвращает список вариантов написания группы для перебора."""
    name = name.strip()
    variants = [name]

    # Варианты с К- префиксом
    if not name.upper().startswith('К-') and not name.upper().startswith('K-'):
        variants.append(f'К-{name}')
        variants.append(f'K-{name}')

    # Заменяем кириллическую М на латинскую и наоборот
    for v in list(variants):
        swapped_cyr = v.replace('M', 'М').replace('m', 'м')
        swapped_lat = v.replace('М', 'M').replace('м', 'm')
        variants += [swapped_cyr, swapped_lat]

    # Убираем дубли, сохраняем порядок
    seen = []
    for v in variants:
        if v not in seen:
            seen.append(v)
    return seen


# ─────────────────────────────────────────────
#  ШАБЛОНЫ ЗАПРОСОВ К САЙТУ РАСПИСАНИЯ
#  (перебираем пока не найдём рабочий)
# ─────────────────────────────────────────────
def build_request_strategies(group: str, week: str) -> list[dict]:
    """
    Возвращает список стратегий: каждая — словарь с методом, URL и параметрами.
    Перебираем по очереди, останавливаемся на первой успешной.
    """
    strategies = []

    # ── Стратегия 1: читаем реальную форму со страницы и отправляем её
    strategies.append({'type': 'auto_form', 'group': group, 'week': week})

    # ── Стратегия 2: POST на index.php с разными вариантами полей
    post_payloads = [
        {'type': '2', 'grp':        group, 'week': week},
        {'type': '2', 'group':      group, 'week': week},
        {'type': '2', 'group_name': group, 'week': week},
        {'type': '2', 'name':       group, 'week': week},
        {'grp':        group, 'week': week},
        {'group':      group, 'week': week},
        {'group_name': group, 'week': week},
        {'search': group, 'week': week},
        {'gr': group, 'week': week},
    ]
    for p in post_payloads:
        strategies.append({'type': 'post', 'url': f'{EDU_URL}/', 'data': p})
        strategies.append({'type': 'post', 'url': f'{EDU_URL}/index.php', 'data': p})

    # ── Стратегия 3: GET с параметрами
    get_params = [
        {'type': '2', 'grp':   group, 'week': week},
        {'type': '2', 'group': group, 'week': week},
        {'grp':   group, 'week': week},
        {'group': group, 'week': week},
        {'search': group},
        {'q': group},
    ]
    for p in get_params:
        strategies.append({'type': 'get', 'url': f'{EDU_URL}/',         'params': p})
        strategies.append({'type': 'get', 'url': f'{EDU_URL}/index.php', 'params': p})

    return strategies


def try_auto_form(s: requests.Session, group: str, week: str):
    """
    Получаем главную страницу, читаем форму, подставляем наши значения, отправляем.
    Возвращает Response или None.
    """
    try:
        r = s.get(f'{EDU_URL}/', timeout=12)
        soup = BeautifulSoup(r.text, 'html.parser')
        forms = soup.find_all('form')
        if not forms:
            print('[AUTO_FORM] Форм на главной нет — скорее всего всё через JS')
            # Сохраним HTML для отладки
            with open('/tmp/edu_main_debug.html', 'w', encoding='utf-8') as f:
                f.write(r.text)
            return None

        form = forms[0]
        action = form.get('action') or '/'
        method = form.get('method', 'get').lower()
        full_action = urljoin(EDU_URL, action)

        payload = {}
        for inp in form.find_all(['input', 'select']):
            name = inp.get('name')
            if not name:
                continue
            payload[name] = inp.get('value', '')

        # Пробуем угадать поля для группы и недели
        group_field = None
        week_field = None
        for key in payload:
            low = key.lower()
            if any(w in low for w in ['grp', 'group', 'grup', 'gr']):
                group_field = key
            if 'week' in low or 'нед' in low:
                week_field = key

        if not group_field:
            # Берём первое незащищённое текстовое поле
            for inp in form.find_all('input'):
                if inp.get('type', 'text') in ('text', '') and inp.get('name'):
                    group_field = inp['name']
                    break

        if group_field:
            payload[group_field] = group
        if week_field:
            payload[week_field] = week

        print(f'[AUTO_FORM] action={full_action} method={method} payload={payload}')

        if method == 'post':
            resp = s.post(full_action, data=payload, timeout=12,
                          headers={**HEADERS, 'Referer': f'{EDU_URL}/'})
        else:
            resp = s.get(full_action, params=payload, timeout=12,
                         headers={**HEADERS, 'Referer': f'{EDU_URL}/'})
        return resp
    except Exception as e:
        print(f'[AUTO_FORM] Ошибка: {e}')
        return None


# ─────────────────────────────────────────────
#  ПАРСЕР HTML → РАСПИСАНИЕ
# ─────────────────────────────────────────────
def response_has_schedule(html: str) -> bool:
    """Быстрая проверка: есть ли в HTML что-то похожее на расписание."""
    lower = html.lower()
    # Признаки расписания: таблица + что-то из ключевых слов
    has_table = '<table' in lower
    has_keywords = any(w in lower for w in [
        'пара', 'лекци', 'практ', 'семин', 'лабора',
        'каб', 'ауд', '08:30', '10:10', '11:50', '13:30',
    ])
    return has_table and has_keywords


def parse_html_schedule(html_text: str, group_name: str) -> dict:
    """
    Главный парсер. Пробует несколько подходов к структуре таблицы.
    Возвращает {'header': str, 'days': list, 'success': bool}
    """
    soup = BeautifulSoup(html_text, 'html.parser')

    # Заголовок (неделя / название расписания)
    header_text = f'Расписание группы {group_name}'
    for selector in ['.rasp_head', 'h1', 'h2', '.header']:
        el = soup.find(class_=selector.lstrip('.')) if selector.startswith('.') else soup.find(selector)
        if el:
            txt = el.get_text(separator=' ', strip=True)
            if txt:
                header_text = txt
                break

    days = [
        {'name': 'Понедельник', 'header': '', 'lessons': []},
        {'name': 'Вторник',     'header': '', 'lessons': []},
        {'name': 'Среда',       'header': '', 'lessons': []},
        {'name': 'Четверг',     'header': '', 'lessons': []},
        {'name': 'Пятница',     'header': '', 'lessons': []},
        {'name': 'Суббота',     'header': '', 'lessons': []},
    ]

    tables = soup.find_all('table')
    if not tables:
        return {'header': header_text, 'days': days, 'success': False}

    has_data = False

    for table in tables:
        rows = table.find_all('tr')
        if len(rows) < 2:
            continue

        # ── Пытаемся прочитать заголовки дней из первой строки
        header_cells = rows[0].find_all(['th', 'td'])
        day_map = {}  # col_index -> day_index

        for col_idx, cell in enumerate(header_cells):
            text = cell.get_text(strip=True)
            for day_idx, day in enumerate(days):
                short = day['name'][:2]  # Пн, Вт и т.д.
                if short in text or day['name'] in text:
                    day_map[col_idx] = day_idx
                    days[day_idx]['header'] = text

        # ── Разбираем строки с парами
        for row in rows[1:]:
            cells = row.find_all('td')
            if not cells:
                continue

            for col_idx, cell in enumerate(cells):
                day_idx = day_map.get(col_idx, col_idx if col_idx < 6 else None)
                if day_idx is None or day_idx >= len(days):
                    continue

                raw_text = cell.get_text(separator='\n')
                lines = [l.strip() for l in raw_text.split('\n') if l.strip()]

                if not lines:
                    continue
                if any(w in raw_text.lower() for w in ['отсутствует', 'пар нет', 'нет занятий']):
                    continue

                num = ''
                time_str = ''
                subject = ''
                teacher = ''
                room = ''

                rest = []
                for line in lines:
                    if re.search(r'\d{1,2}[:\.]\d{2}', line):
                        time_str = line
                    elif re.match(r'^\d+[\.\)]\s*$', line) or (line.isdigit() and len(line) <= 2):
                        num = line.strip('.)').strip()
                    else:
                        rest.append(line)

                if not rest and not time_str:
                    continue

                # Аудитория — строки со словами каб/ауд/гк/лк/пр/лаб и т.д.
                subject_lines = []
                for line in rest:
                    if re.search(r'\b(каб|ауд|гк|лк|пр|лаб|стад|спорт|онлайн)\b', line, re.I):
                        room = line
                    else:
                        subject_lines.append(line)

                if subject_lines:
                    has_data = True
                    bold = cell.find(['b', 'strong'])
                    if bold:
                        subject = bold.get_text(strip=True)
                        teacher = ', '.join(
                            l for l in subject_lines if l.lower() != subject.lower()
                        )
                    else:
                        subject = subject_lines[0]
                        if len(subject_lines) > 1:
                            teacher = ', '.join(subject_lines[1:])

                if subject or time_str:
                    days[day_idx]['lessons'].append({
                        'num':     num or str(len(days[day_idx]['lessons']) + 1),
                        'time':    time_str or '',
                        'subject': subject,
                        'teacher': teacher,
                        'room':    room,
                    })

    # Сортируем пары по номеру
    for d in days:
        d['lessons'].sort(key=lambda x: (x['num'].zfill(2), x['time']))

    return {'header': header_text, 'days': days, 'success': has_data}


# ─────────────────────────────────────────────
#  ЭНДПОИНТЫ АВТОРИЗАЦИИ
# ─────────────────────────────────────────────
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
            return jsonify({'success': False, 'error': 'Форма входа не найдена'})
        payload = {}
        for inp in form.find_all('input'):
            name = inp.get('name')
            if name:
                payload[name] = inp.get('value', '')
        login_field = 'Email'
        password_field = 'Password'
        for key in payload:
            low = key.lower()
            if 'login' in low or 'email' in low or 'user' in low:
                login_field = key
            if 'pass' in low:
                password_field = key
        payload[login_field] = username
        payload[password_field] = password
        resp = s.post(login_url, data=payload,
                      headers={**HEADERS, 'Referer': login_url},
                      allow_redirects=True, timeout=15)
        auth = any('.AspNet' in c.name for c in s.cookies) or 'Выйти' in resp.text
        if auth:
            session['cookies'] = requests.utils.dict_from_cookiejar(s.cookies)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Неверный логин или пароль'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/logout', methods=['POST', 'OPTIONS'])
def logout():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    session.clear()
    return jsonify({'success': True})


# ─────────────────────────────────────────────
#  ПРЕДМЕТЫ И ОЦЕНКИ
# ─────────────────────────────────────────────
@app.route('/api/subjects', methods=['GET', 'OPTIONS'])
def subjects():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    if 'cookies' not in session:
        return jsonify({'error': 'auth'}), 401
    s = requests.Session()
    s.cookies.update(requests.utils.cookiejar_from_dict(session['cookies']))
    s.headers.update(HEADERS)
    try:
        r = s.get(f'{BASE_URL}/Journals/DisciplinesStudent', timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        result = []
        for link in soup.find_all('a', href=True):
            if '/Journals/DisciplineGrades' not in link['href']:
                continue
            row = link.find_parent('tr')
            if not row:
                continue
            cells = row.find_all('td')
            result.append({
                'name':     cells[1].get_text(strip=True) if len(cells) > 1 else 'Без названия',
                'semestr':  cells[2].get_text(strip=True) if len(cells) > 2 else '—',
                'teacher':  cells[3].get_text(strip=True) if len(cells) > 3 else '—',
                'url':      link['href'],
            })
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
    url = request.args.get('url', '')
    if not url.startswith('/Journals/'):
        return jsonify({'error': 'Неверный URL'})
    s = requests.Session()
    s.cookies.update(requests.utils.cookiejar_from_dict(session['cookies']))
    s.headers.update(HEADERS)
    try:
        r = s.get(urljoin(BASE_URL, url), timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        lessons = []
        table = soup.find('table')
        if table:
            for row in table.find_all('tr'):
                cells = row.find_all(['td', 'th'])
                if len(cells) < 3:
                    continue
                date = cells[1].get_text(strip=True)
                if not date or date in ('№', 'Дата') or 'Дата' in date:
                    continue
                content = cells[2].get_text(separator=' ', strip=True)
                theme = content.split('Домашнее задание:')[0].replace('Тема:', '').strip()
                grade = cells[-1].get_text(strip=True)
                lessons.append({
                    'date':  date,
                    'theme': theme or 'Занятие',
                    'grade': grade or '-',
                })
        return jsonify({'lessons': lessons})
    except Exception as e:
        return jsonify({'error': str(e)})


# ─────────────────────────────────────────────
#  РАСПИСАНИЕ — ГЛАВНЫЙ ЭНДПОИНТ
# ─────────────────────────────────────────────
@app.route('/api/schedule/by_name', methods=['POST', 'OPTIONS'])
def schedule_by_name():
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    data = request.json or {}
    group_name = data.get('group_name', '').strip()
    week = str(data.get('week', '0'))

    if not group_name:
        return jsonify({'error': 'Не указано имя группы'})

    group_variants = normalize_group(group_name)
    s = make_edu_session()

    for group in group_variants:
        strategies = build_request_strategies(group, week)

        for strategy in strategies:
            try:
                resp = None

                if strategy['type'] == 'auto_form':
                    resp = try_auto_form(s, group, week)

                elif strategy['type'] == 'post':
                    resp = s.post(
                        strategy['url'],
                        data=strategy['data'],
                        timeout=12,
                        headers={**HEADERS,
                                 'Content-Type': 'application/x-www-form-urlencoded',
                                 'Referer': f'{EDU_URL}/'},
                    )
                    print(f"[POST] {strategy['url']} data={strategy['data']} -> {resp.status_code} len={len(resp.text)}")

                elif strategy['type'] == 'get':
                    resp = s.get(
                        strategy['url'],
                        params=strategy['params'],
                        timeout=12,
                        headers={**HEADERS, 'Referer': f'{EDU_URL}/'},
                    )
                    print(f"[GET] {strategy['url']} params={strategy['params']} -> {resp.status_code} len={len(resp.text)}")

                if resp is None:
                    continue

                if resp.status_code != 200:
                    continue

                if not response_has_schedule(resp.text):
                    continue

                # Есть что-то похожее на расписание!
                result = parse_html_schedule(resp.text, group)
                if result['success']:
                    print(f'[SUCCESS] Нашли расписание для {group} стратегией {strategy["type"]}')
                    return jsonify({
                        'header': result['header'],
                        'days':   result['days'],
                    })

            except Exception as e:
                print(f'[ERROR] Стратегия {strategy}: {e}')
                continue

    # Всё провалилось — возвращаем отладочную информацию
    print(f'[FAIL] Не нашли расписание для группы "{group_name}"')

    # Сохраняем последний ответ для отладки
    debug_info = {'variants_tried': group_variants, 'strategies_count': 0}
    try:
        r_debug = s.get(f'{EDU_URL}/', timeout=10)
        debug_info['main_page_status'] = r_debug.status_code
        debug_info['main_page_length'] = len(r_debug.text)
        debug_info['has_forms'] = bool(BeautifulSoup(r_debug.text, 'html.parser').find_all('form'))
        debug_info['main_page_preview'] = r_debug.text[:500]
    except Exception as e:
        debug_info['main_page_error'] = str(e)

    return jsonify({
        'header': f'Расписание группы "{group_name}" не найдено',
        'days':   [],
        'debug':  debug_info,
    })


# ─────────────────────────────────────────────
#  ДИАГНОСТИКА (только для разработки!)
# ─────────────────────────────────────────────
@app.route('/api/debug/site', methods=['GET'])
def debug_site():
    """
    Вызови этот эндпоинт и посмотри что возвращает сайт расписания.
    GET /api/debug/site
    """
    s = make_edu_session()
    result = {}
    try:
        r = s.get(f'{EDU_URL}/', timeout=12)
        soup = BeautifulSoup(r.text, 'html.parser')
        result['status'] = r.status_code
        result['url'] = r.url
        result['html_length'] = len(r.text)
        result['html_preview'] = r.text[:2000]

        forms = soup.find_all('form')
        result['forms'] = []
        for form in forms:
            fields = []
            for el in form.find_all(['input', 'select', 'textarea']):
                fields.append({
                    'tag':   el.name,
                    'name':  el.get('name'),
                    'type':  el.get('type'),
                    'value': el.get('value', '')[:50],
                })
                if el.name == 'select':
                    opts = [
                        {'value': o.get('value'), 'text': o.get_text(strip=True)}
                        for o in el.find_all('option')[:10]
                    ]
                    fields[-1]['options'] = opts
            result['forms'].append({
                'action': form.get('action'),
                'method': form.get('method'),
                'fields': fields,
            })

        links = [
            {'text': a.get_text(strip=True)[:40], 'href': a['href']}
            for a in soup.find_all('a', href=True)
            if a['href'] != '#'
        ]
        result['links'] = links[:30]

    except Exception as e:
        result['error'] = str(e)

    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
