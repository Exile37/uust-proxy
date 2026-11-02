from flask import Flask, jsonify, request, session
from flask_compress import Compress
import re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
app.secret_key = "super_secret_key_123"

# 1. ОПТИМИЗАЦИЯ: Включаем автоматическое Gzip-сжатие ответов ответа прокси
Compress(app)

BASE_URL = "https://account.str.uust.ru"
EDU_URL = "https://edu.str.uust.ru"
EDU_PHP = f"{EDU_URL}/php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": EDU_URL,
    "Referer": EDU_URL + "/",
    "X-Requested-With": "XMLHttpRequest",
}

AUTH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# 2. ОПТИМИЗАЦИЯ: Пулинг соединений через глобальную сессию для открытых эндпоинтов расписания
http_session = requests.Session()
http_session.headers.update(HEADERS)


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/ping")
def ping():
    return jsonify({"status": "ok"})

@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "uust-proxy"})

@app.route("/api/debug/edu")
def debug_edu():
    try:
        r = http_session.get(f"{EDU_PHP}/getList.php?faculty=26", timeout=10)
        return jsonify({"status": r.status_code, "url": r.url, "html": r.text[:1000]})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/debug/timetable")
def debug_timetable():
    try:
        r = http_session.post(
            f"{EDU_PHP}/getShedule.php",
            data={"type": "2", "id": "10155", "week": "0"},
            timeout=10
        )
        return jsonify({"status": r.status_code, "html": r.text[:3000]})
    except Exception as e:
        return jsonify({"error": str(e)})

# ─── AUTH ─────────────────────────────────────────────────────────────────────
@app.route("/api/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.json or {}
    client = requests.Session()
    client.headers.update(AUTH_HEADERS)
    try:
        login_url = f"{BASE_URL}/Account/Login"
        response = client.get(login_url, timeout=10)
        
        # 3. ОПТИМИЗАЦИЯ: Использование lxml парсера (значительно быстрее html.parser)
        soup = BeautifulSoup(response.text, "lxml")
        form = soup.find("form")
        if not form:
            return jsonify({"success": False, "error": "Форма не найдена"})
        
        payload = {
            field.get("name"): field.get("value", "")
            for field in form.find_all("input")
            if field.get("name")
        }
        
        login_field = "Email"
        password_field = "Password"
        for key in payload:
            key_lower = key.lower()
            if any(word in key_lower for word in ["login", "email", "user"]):
                login_field = key
            if "pass" in key_lower:
                password_field = key
                
        payload[login_field] = data.get("username")
        payload[password_field] = data.get("password")
        
        response = client.post(
            login_url, data=payload,
            headers={**AUTH_HEADERS, "Referer": login_url},
            allow_redirects=True, timeout=10,
        )
        
        if any(".AspNet" in cookie.name for cookie in client.cookies) or "Выйти" in response.text:
            session["cookies"] = requests.utils.dict_from_cookiejar(client.cookies)
            group_name = None
            try:
                profile_soup = BeautifulSoup(response.text, "lxml")
                for tag in profile_soup.find_all(string=re.compile(r"Группа")):
                    parent = tag.parent
                    text = parent.get_text(strip=True) if parent else str(tag)
                    match = re.search(r"Группа[:\s]+([А-ЯЁа-яёA-Za-z0-9\-]+)", text)
                    if match:
                        group_name = match.group(1).strip()
                        break
                if not group_name:
                    profile_r = client.get(f"{BASE_URL}/", timeout=10)
                    profile_soup2 = BeautifulSoup(profile_r.text, "lxml")
                    for tag in profile_soup2.find_all(string=re.compile(r"Группа")):
                        parent = tag.parent
                        text = parent.get_text(strip=True) if parent else str(tag)
                        match = re.search(r"Группа[:\s]+([А-ЯЁа-яёA-Za-z0-9\-]+)", text)
                        if match:
                            group_name = match.group(1).strip()
                            break
            except Exception:
                pass
            return jsonify({"success": True, "group_name": group_name})
        return jsonify({"success": False, "error": "Неверный логин или пароль"})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)})


@app.route("/api/logout", methods=["POST", "OPTIONS"])
def logout():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    session.clear()
    return jsonify({"success": True})


@app.route("/api/subjects", methods=["GET", "OPTIONS"])
def subjects():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    if "cookies" not in session:
        return jsonify({"error": "auth"}), 401
    
    client = requests.Session()
    client.cookies.update(requests.utils.cookiejar_from_dict(session["cookies"]))
    client.headers.update(AUTH_HEADERS)
    
    try:
        response = client.get(f"{BASE_URL}/Journals/DisciplinesStudent", timeout=10)
        soup = BeautifulSoup(response.text, "lxml")
        result = []
        for link in soup.find_all("a", href=True):
            if "/Journals/DisciplineGrades" not in link["href"]:
                continue
            row = link.find_parent("tr")
            if not row:
                continue
            cells = row.find_all("td")
            result.append({
                "name": cells[1].get_text(strip=True) if len(cells) > 1 else "—",
                "semestr": cells[2].get_text(strip=True) if len(cells) > 2 else "—",
                "teacher": cells[3].get_text(strip=True) if len(cells) > 3 else "—",
                "url": link["href"],
            })
        return jsonify({"subjects": list({item["url"]: item for item in result}.values())})
    except Exception as exc:
        return jsonify({"error": str(exc)})


@app.route("/api/grades", methods=["GET", "OPTIONS"])
def grades():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    if "cookies" not in session:
        return jsonify({"error": "auth"}), 401
    
    url = request.args.get("url", "")
    if not url.startswith("/Journals/"):
        return jsonify({"error": "неверный URL"})
        
    client = requests.Session()
    client.cookies.update(requests.utils.cookiejar_from_dict(session["cookies"]))
    client.headers.update(AUTH_HEADERS)
    
    try:
        response = client.get(urljoin(BASE_URL, url), timeout=10)
        soup = BeautifulSoup(response.text, "lxml")
        lessons = []
        table = soup.find("table")
        if table:
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 3:
                    continue
                date = cells[1].get_text(strip=True)
                if not date or "Дата" in date:
                    continue
                content = cells[2].get_text(separator=" ", strip=True)
                theme = content.split("Домашнее задание:")[0].replace("Тема:", "").strip()
                lessons.append({
                    "date": date,
                    "theme": theme or "Занятие",
                    "grade": cells[-1].get_text(strip=True) or "-",
                })
        return jsonify({"lessons": lessons})
    except Exception as exc:
        return jsonify({"error": str(exc)})


# ─── ЗАЧЁТНАЯ КНИЖКА ──────────────────────────────────────────────────────────
STATEMENT_URL = "/StatementStudents/Index"

# Формы контроля и оценки, которые встречаются в ведомости
GRADE_RE = re.compile(
    r"^(?:[2-5](?:[.,]\d+)?|зач[её]т|зачтено|не\s*зач[её]т|не\s*зачтено|н/я|н\.?я\.?|неявка)$",
    re.IGNORECASE,
)
NEGATIVE_RE = re.compile(r"^(?:2|не\s*зач[её]т|не\s*зачтено|неуд\w*|н/я|н\.?я\.?|неявка)$", re.IGNORECASE)
CONTROL_RE = re.compile(r"(экзамен|зач[её]т|диф\.?\s*зач|курсов\w*|практик|контрольн)", re.IGNORECASE)
DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")
TEACHER_RE = re.compile(r"^[А-ЯЁ][а-яё\-]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]?\.?$")
SKIP_NAMES = {
    "итого", "всего", "дисциплина", "наименование", "предмет", "семестр", "курс",
    "главная", "расписание", "оценки", "настройки", "профиль", "журнал", "выход",
    "выйти", "назад", "далее", "подробнее", "печать",
}


def _norm(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _lower(value):
    return _norm(value).lower().replace("ё", "е")


def _cell_text(cell):
    return _norm(cell.get_text(" ", strip=True))


def _labeled(text, labels, max_len=90):
    """Достаёт значение из строки вида «Группа: ИС-301»."""
    for label in labels:
        match = re.search(rf"{label}\s*[:\-—]?\s*([^\n]{{2,{max_len}}})", text, re.IGNORECASE)
        if match:
            value = _norm(match.group(1))
            value = re.split(r"\s{2,}|\s+[А-ЯЁA-Z][а-яёa-z]+\s*:", value)[0]
            if value:
                return value[:max_len]
    return ""


def _parse_student(soup):
    text = soup.get_text("\n", strip=True)
    info = {
        "name": _labeled(text, [r"ФИО", r"Студент(?:ка)?", r"Обучающийся"]),
        "group": _labeled(text, [r"Группа"], 40),
        "faculty": _labeled(text, [r"Факультет", r"Направление подготовки", r"Специальность"], 90),
        "recordBook": "",
    }
    book = re.search(
        r"(?:№|номер)?\s*зач[её]тн\w*\s+книжк\w*[^\d]{0,20}(\d{4,}(?:[-/]\d{1,8})?)",
        text,
        re.IGNORECASE,
    )
    if not book:
        book = re.search(r"(?:№|номер)\s*зач[её]тки[^\d]{0,20}(\d{4,}(?:[-/]\d{1,8})?)", text, re.IGNORECASE)
    if book:
        info["recordBook"] = _norm(book.group(1))

    # ФИО часто лежит в шапке страницы отдельной строкой без подписи
    if not info["name"]:
        heading = soup.find(["h1", "h2", "h3"])
        if heading:
            value = _norm(heading.get_text(" ", strip=True))
            value = re.sub(r"(?i)зач[её]тная\s+книжка\s*(студента)?", "", value).strip(" :—-")
            if 5 <= len(value) <= 70:
                info["name"] = value
    return info


def _columns(headers):
    def find(*keys):
        for index, header in enumerate(headers):
            lowered = _lower(header)
            if any(key in lowered for key in keys):
                return index
        return -1

    return {
        "number": find("№", "номер", "п/п"),
        "name": find("дисциплин", "предмет", "наименован"),
        "semester": find("семестр", "курс"),
        "control": find("форма контрол", "вид контрол", "контрол", "отчетност"),
        "teacher": find("преподават", "фио"),
        "date": find("дата"),
        "grade": find("оценка", "результат", "итог"),
        "credits": find("з.е", "зачетн", "кредит"),
        "hours": find("час"),
    }


def _semester_title(value):
    value = _norm(value)
    if not value:
        return ""
    match = re.search(r"(\d+)\s*(семестр|сем\.)", value, re.IGNORECASE)
    if match:
        return f"{match.group(1)} семестр"
    match = re.search(r"(\d+)\s*курс", value, re.IGNORECASE)
    if match:
        return f"{match.group(1)} курс"
    return value


def _row_grade(texts, idx):
    if idx["grade"] >= 0 and idx["grade"] < len(texts):
        value = _norm(texts[idx["grade"]])
        return value if value and GRADE_RE.match(value) else ""
    for value in reversed(texts[1:]):
        value = _norm(value)
        if value and GRADE_RE.match(value):
            return value
    return ""


def _row_control(texts, idx):
    if idx["control"] >= 0 and idx["control"] < len(texts):
        value = _norm(texts[idx["control"]])
        if value:
            return value
    for value in texts:
        if CONTROL_RE.search(value) and len(value) <= 60:
            return _norm(value)
    return ""


def _table_context(table):
    for node in table.find_all_previous(limit=6):
        name = getattr(node, "name", "") or ""
        classes = " ".join(node.get("class", [])) if hasattr(node, "get") else ""
        if name in ("h1", "h2", "h3", "h4", "h5", "h6", "caption", "legend") or "heading" in classes or "title" in classes:
            value = _norm(node.get_text(" ", strip=True))
            if re.search(r"(семестр|курс)", value, re.IGNORECASE) and len(value) <= 90:
                return value
    return ""


def _parse_statement(soup):
    student = _parse_student(soup)
    semesters = []
    seen = set()

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        header_cells = rows[0].find_all(["th", "td"])
        headers = [_cell_text(cell) for cell in header_cells]
        idx = _columns(headers)
        has_header = idx["name"] >= 0 or any(_lower(h) in ("№", "n") for h in headers)
        data_rows = rows[1:] if has_header else rows
        context = _semester_title(_table_context(table))

        for row in data_rows:
            cells = row.find_all(["td", "th"])
            texts = [_cell_text(cell) for cell in cells]
            if not any(texts):
                continue

            if idx["name"] >= 0 and idx["name"] < len(texts):
                name = texts[idx["name"]]
            else:
                meaningful = [
                    text for index, text in enumerate(texts)
                    if index > 0
                    and len(text) > 2
                    and not GRADE_RE.match(text)
                    and not DATE_RE.search(text)
                    and not CONTROL_RE.search(text)
                    and not TEACHER_RE.match(text)
                    and not re.fullmatch(r"[\d\s.,]+", text)
                ]
                name = meaningful[0] if meaningful else ""

            name = _norm(name)
            if not name or _lower(name) in SKIP_NAMES:
                continue
            if len(name) < 3 or len(name) > 200:
                continue

            grade = _row_grade(texts, idx)
            semester_value = ""
            if idx["semester"] >= 0 and idx["semester"] < len(texts):
                semester_value = _semester_title(texts[idx["semester"]])
            semester_value = semester_value or context or "Зачётная книжка"

            teacher = texts[idx["teacher"]] if 0 <= idx["teacher"] < len(texts) else ""
            date = ""
            if 0 <= idx["date"] < len(texts) and DATE_RE.search(texts[idx["date"]]):
                date = DATE_RE.search(texts[idx["date"]]).group(0)
            else:
                for value in texts:
                    found = DATE_RE.search(value)
                    if found:
                        date = found.group(0)
                        break

            credits = ""
            if 0 <= idx["credits"] < len(texts):
                credits = re.sub(r"[^\d.,]", "", texts[idx["credits"]]).replace(",", ".")
            hours = ""
            if 0 <= idx["hours"] < len(texts):
                hours = re.sub(r"[^\d]", "", texts[idx["hours"]])

            key = (_lower(name), _lower(semester_value), grade)
            if key in seen:
                continue
            seen.add(key)

            item = {
                "name": name,
                "semester": semester_value,
                "control": _row_control(texts, idx),
                "teacher": _norm(teacher),
                "date": date,
                "grade": grade,
                "credits": credits,
                "hours": hours,
            }

            target = next((group for group in semesters if group["title"] == semester_value), None)
            if target is None:
                target = {"title": semester_value, "disciplines": []}
                semesters.append(target)
            target["disciplines"].append(item)

    def sort_key(group):
        match = re.search(r"\d+", group["title"])
        return (int(match.group(0)) if match else 99, group["title"])

    semesters.sort(key=sort_key)
    return student, semesters


def _statement_summary(semesters):
    grades = []
    credits = 0.0
    has_credits = False
    debts = 0
    total = 0
    for group in semesters:
        for item in group["disciplines"]:
            total += 1
            grade = _norm(item.get("grade"))
            if grade:
                if re.fullmatch(r"[2-5](?:[.,]\d+)?", grade):
                    grades.append(float(grade.replace(",", ".")))
                elif NEGATIVE_RE.match(grade):
                    debts += 1
            raw_credits = item.get("credits") or ""
            if raw_credits:
                try:
                    credits += float(raw_credits)
                    has_credits = True
                except ValueError:
                    pass
    return {
        "average": round(sum(grades) / len(grades), 2) if grades else None,
        "credits": round(credits, 1) if has_credits else None,
        "debts": debts,
        "total": total,
    }


def _statement_client():
    client = requests.Session()
    client.cookies.update(requests.utils.cookiejar_from_dict(session["cookies"]))
    client.headers.update(AUTH_HEADERS)
    return client


@app.route("/api/recordbook", methods=["GET", "OPTIONS"])
def recordbook():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    if "cookies" not in session:
        return jsonify({"error": "auth"}), 401

    url = request.args.get("url", STATEMENT_URL)
    if not (url.startswith("/StatementStudents") or url.startswith("/Journals")):
        return jsonify({"error": "неверный URL"})

    client = _statement_client()
    try:
        response = client.get(urljoin(BASE_URL, url), timeout=15)
        if response.status_code >= 400:
            return jsonify({"error": f"Сайт ответил {response.status_code}"})
        soup = BeautifulSoup(response.text, "lxml")
        student, semesters = _parse_statement(soup)
        if not semesters:
            return jsonify({
                "error": "Не удалось разобрать зачётную книжку",
                "hint": "Проверьте страницу через /api/debug/statement",
            })
        return jsonify({
            "student": student,
            "semesters": semesters,
            "summary": _statement_summary(semesters),
            "sourceUrl": url,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)})


@app.route("/api/debug/statement", methods=["GET", "OPTIONS"])
def debug_statement():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    if "cookies" not in session:
        return jsonify({"error": "auth"}), 401

    client = _statement_client()
    try:
        response = client.get(f"{BASE_URL}{STATEMENT_URL}", timeout=15)
        soup = BeautifulSoup(response.text, "lxml")
        tables = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            tables.append({
                "headers": [_cell_text(c) for c in rows[0].find_all(["th", "td"])] if rows else [],
                "rows": [[_cell_text(c) for c in row.find_all(["td", "th"])] for row in rows[1:6]],
            })
        payload = {
            "status": response.status_code,
            "url": response.url,
            "title": _norm(soup.title.get_text()) if soup.title else "",
            "tables": tables,
            "text": soup.get_text("\n", strip=True)[:5000],
        }
        if request.args.get("html"):
            payload["html"] = response.text[:60000]
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)})


# ─── РАСПИСАНИЕ ───────────────────────────────────────────────────────────────
@app.route("/api/schedule/groups", methods=["GET", "OPTIONS"])
def schedule_groups():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    faculty = request.args.get("faculty", "26")
    try:
        # Используем переиспользуемую HTTP-сессию
        response = http_session.get(
            f"{EDU_PHP}/getList.php?faculty={faculty}", timeout=10
        )
        html = response.text
        groups = []
        pattern = re.compile(
            r"<div[^>]*display\s*:\s*none[^>]*>\s*(\d+)\s*</div>\s*<a[^>]*>([^<]+)</a>",
            re.IGNORECASE,
        )
        for match in pattern.finditer(html):
            groups.append({
                "id": match.group(1).strip(),
                "name": match.group(2).strip(),
            })
        return jsonify({"groups": groups})
    except Exception as exc:
        return jsonify({"error": str(exc)})


@app.route("/api/schedule/week_header", methods=["GET", "OPTIONS"])
def schedule_week_header():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    group_id = request.args.get("id")
    week = request.args.get("week", "0")
    if not group_id:
        return jsonify({"error": "id is required"}), 400
    try:
        response = http_session.post(
            f"{EDU_PHP}/getSheduleHeader.php",
            data={"type": "2", "id": group_id, "week": week},
            timeout=10,
        )
        soup = BeautifulSoup(response.text, "lxml")
        return jsonify({"header": soup.get_text(separator=" ", strip=True)})
    except Exception as exc:
        return jsonify({"error": str(exc)})


@app.route("/api/schedule/timetable", methods=["GET", "OPTIONS"])
def schedule_timetable():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    group_id = request.args.get("id")
    week = request.args.get("week", "0")
    if not group_id:
        return jsonify({"error": "id is required"}), 400
    try:
        response = http_session.post(
            f"{EDU_PHP}/getShedule.php",
            data={"type": "2", "id": group_id, "week": week},
            timeout=10,
        )
        soup = BeautifulSoup(response.text, "lxml")
        days = []
        day_names_order = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]

        for day_div in soup.find_all("div", class_="day"):
            h2 = day_div.find("h2", class_="date")
            day_header = h2.get_text(strip=True) if h2 else ""

            day_name = ""
            for d in day_names_order:
                if d in day_header:
                    day_name = d
                    break
            if not day_name:
                day_name = day_header.split()[0] if day_header else "День"

            lessons = []
            for li in day_div.find_all("li", class_="lesson"):
                num_div = li.find("div", class_="number")
                num_text = num_div.get_text(strip=True) if num_div else ""
                num = num_text.replace(".", "").strip()
                if not num:
                    continue

                time_div = li.find("div", class_="time")
                time_str = time_div.get_text(strip=True) if time_div else ""

                name_div = li.find("div", class_="name")
                subject = name_div.get_text(strip=True) if name_div else ""

                cab_div = li.find("div", class_="cab")
                room = cab_div.get_text(strip=True) if cab_div else ""

                type_div = li.find("div", class_="type")
                lesson_type = ""
                if type_div:
                    hidden = type_div.find("div", style=lambda s: s and "display" in s)
                    if hidden:
                        hidden.decompose()
                    lesson_type = type_div.get_text(strip=True)

                prep_div = li.find("div", class_="prep")
                teacher = prep_div.get_text(strip=True) if prep_div else ""

                if subject:
                    lessons.append({
                        "num": num,
                        "time": time_str,
                        "subject": subject,
                        "teacher": teacher,
                        "room": room,
                        "type": lesson_type,
                    })

            days.append({
                "name": day_name,
                "header": day_header,
                "lessons": lessons,
            })

        existing = {d["name"] for d in days}
        for dn in day_names_order:
            if dn not in existing:
                days.append({"name": dn, "header": "", "lessons": []})

        days.sort(key=lambda d: day_names_order.index(d["name"]) if d["name"] in day_names_order else 99)

        return jsonify({"days": days})
    except Exception as exc:
        return jsonify({"error": str(exc)})


if __name__ == "__main__":
    # В продакшене рекомендуется запуск через Waitress: waitress-serve --port=5000 proxy:app
    app.run(host="0.0.0.0", port=5000, debug=True)
