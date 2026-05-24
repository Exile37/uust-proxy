from flask import Flask, jsonify, request, session
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


app = Flask(__name__)
app.secret_key = "super_secret_key_123"

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
        r = requests.get(f"{EDU_PHP}/getList.php?faculty=26", headers=HEADERS, timeout=15)
        return jsonify({"status": r.status_code, "url": r.url, "html": r.text[:1000]})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/debug/timetable")
def debug_timetable():
    try:
        r = requests.post(
            f"{EDU_PHP}/getShedule.php",
            data={"type": "2", "id": "10155", "week": "0"},
            headers=HEADERS, timeout=15
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
        response = client.get(login_url, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
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
            allow_redirects=True, timeout=15,
        )
        if any(".AspNet" in cookie.name for cookie in client.cookies) or "Выйти" in response.text:
            session["cookies"] = requests.utils.dict_from_cookiejar(client.cookies)
            group_name = None
            try:
                profile_soup = BeautifulSoup(response.text, "html.parser")
                for tag in profile_soup.find_all(string=re.compile(r"Группа")):
                    parent = tag.parent
                    text = parent.get_text(strip=True) if parent else str(tag)
                    match = re.search(r"Группа[:\s]+([А-ЯЁа-яёA-Za-z0-9\-]+)", text)
                    if match:
                        group_name = match.group(1).strip()
                        break
                if not group_name:
                    profile_r = client.get(f"{BASE_URL}/", timeout=10)
                    profile_soup2 = BeautifulSoup(profile_r.text, "html.parser")
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
        response = client.get(f"{BASE_URL}/Journals/DisciplinesStudent", timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
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
        response = client.get(urljoin(BASE_URL, url), timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
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


# ─── РАСПИСАНИЕ ───────────────────────────────────────────────────────────────
@app.route("/api/schedule/groups", methods=["GET", "OPTIONS"])
def schedule_groups():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    faculty = request.args.get("faculty", "26")
    try:
        response = requests.get(
            f"{EDU_PHP}/getList.php?faculty={faculty}",
            headers=HEADERS, timeout=15,
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
        response = requests.post(
            f"{EDU_PHP}/getSheduleHeader.php",
            data={"type": "2", "id": group_id, "week": week},
            headers=HEADERS, timeout=15,
        )
        soup = BeautifulSoup(response.text, "html.parser")
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
        response = requests.post(
            f"{EDU_PHP}/getShedule.php",
            data={"type": "2", "id": group_id, "week": week},
            headers=HEADERS, timeout=15,
        )
        soup = BeautifulSoup(response.text, "html.parser")
        days = []

        for day_div in soup.find_all("div", class_="day"):
            # Заголовок дня — <h2 class='date ...'>
            h2 = day_div.find("h2", class_="date")
            day_header = h2.get_text(strip=True) if h2 else ""

            # Определяем название дня
            day_name = ""
            for d in ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота"]:
                if d in day_header:
                    day_name = d
                    break
            if not day_name:
                day_name = day_header.split()[0] if day_header else "День"

            lessons = []
            for li in day_div.find_all("li", class_="lesson"):
                # Номер пары
                num_div = li.find("div", class_="number")
                num_text = num_div.get_text(strip=True) if num_div else ""
                num = num_text.replace(".", "").strip()
                if not num:
                    continue  # пустая пара

                # Время
                time_div = li.find("div", class_="time")
                time_str = time_div.get_text(strip=True) if time_div else ""

                # Название предмета
                name_div = li.find("div", class_="name")
                subject = name_div.get_text(strip=True) if name_div else ""

                # Аудитория
                cab_div = li.find("div", class_="cab")
                room = cab_div.get_text(strip=True) if cab_div else ""

                # Преподаватель
                prep_div = li.find("div", class_="prep")
                teacher = prep_div.get_text(strip=True) if prep_div else ""

                if subject:
                    lessons.append({
                        "num": num,
                        "time": time_str,
                        "subject": subject,
                        "teacher": teacher,
                        "room": room,
                    })

            days.append({
                "name": day_name,
                "header": day_header,
                "lessons": lessons,
            })

        # Если дней меньше 6 — дополняем
        day_names = ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота"]
        existing = [d["name"] for d in days]
        for dn in day_names:
            if dn not in existing:
                days.append({"name": dn, "header": "", "lessons": []})

        # Сортируем по порядку дней недели
        days.sort(key=lambda d: day_names.index(d["name"]) if d["name"] in day_names else 99)

        return jsonify({"days": days})
    except Exception as exc:
        return jsonify({"error": str(exc)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
