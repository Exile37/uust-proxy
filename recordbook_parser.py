"""Parsing the *official* record book, with no guesses from journal marks.

Only named columns are used: a course number or hours must never become a grade.
Raw cell values are kept for display; derived statistics are explicitly labelled.
"""
import re


def _norm(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _lower(value):
    return _norm(value).lower().replace("ё", "е")


def _cell_text(cell):
    return _norm(cell.get_text(" ", strip=True))


def _columns(headers):
    def find(*keys):
        return next((i for i, h in enumerate(headers)
                     if any(k in _lower(h) for k in keys)), -1)
    return {
        "name": find("дисциплин", "предмет", "наименован"),
        "course": find("курс"),
        "semester": find("семестр"),
        "control": find("контрол", "отчетност"),
        "teacher": find("преподават", "фио"),
        "date": find("дата"),
        "grade": find("оценка", "результат"),
        "credits": find("з.е", "з. е", "зачетн", "кредит"),
        "hours": find("час"),
    }


def _positive_int(value):
    match = re.fullmatch(r"\s*(\d+)\s*(?:курс|семестр)?\s*", value, re.I)
    return int(match[1]) if match and int(match[1]) > 0 else None


def _period(value):
    course = re.search(r"(\d+)\s*курс", value, re.I)
    semester = re.search(r"(\d+)\s*семестр", value, re.I)
    return (int(course[1]) if course else None,
            int(semester[1]) if semester else None)


def _table_context(table):
    """Search siblings/ancestors, not just six preceding *cells*."""
    for node in table.previous_elements:
        if getattr(node, "name", None) == "table":
            break
        if getattr(node, "name", None) not in (
                "h1", "h2", "h3", "h4", "h5", "h6", "caption", "legend", "div", "p", "b", "strong"):
            continue
        value = _cell_text(node)
        if len(value) <= 90 and _period(value)[1] is not None:
            return _period(value)
    caption = table.find("caption")
    return _period(_cell_text(caption)) if caption else (None, None)


def _statement_scope(soup):
    # Bootstrap tabs: archive and debts must not be mixed into the record book.
    for link in soup.select('a[href^="#"]'):
        label = _lower(_cell_text(link))
        if "электронная" in label and "зачетная" in label:
            pane = soup.find(id=link.get("href", "")[1:])
            if pane is not None:
                return pane
    return soup


def _parse_student(soup):
    # Use explicit labels only. A heading like "1 курс 1 семестр" is not a name.
    text = soup.get_text("\n", strip=True)
    result = {"name": "", "group": "", "faculty": "", "recordBook": ""}
    labels = {"name": r"ФИО|Студент(?:ка)?|Обучающийся",
              "group": r"Группа", "faculty": r"Факультет|Специальность",
              "recordBook": r"(?:Номер|№)\s*зач[её]тн\w*\s+книжк\w*"}
    for key, label in labels.items():
        match = re.search(rf"(?:^|\n)(?:{label})\s*:\s*([^\n]+)", text, re.I)
        if match:
            result[key] = _norm(match[1])[:120]
    return result


def _parse_statement(soup):
    scope = _statement_scope(soup)
    groups = {}
    for table in scope.find_all("table"):
        rows = [r for r in table.find_all("tr") if r.find_parent("table") is table]
        idx = None
        context_course, context_semester = _table_context(table)
        for row in rows:
            cells = row.find_all(["th", "td"], recursive=False)
            texts = [_cell_text(c) for c in cells]
            candidate = _columns(texts)
            if candidate["name"] >= 0 and candidate["grade"] >= 0:
                idx = candidate
                continue
            if len(texts) == 1 and _period(texts[0])[1] is not None:
                context_course, context_semester = _period(texts[0])
                continue
            if idx is None or len(texts) <= max(idx["name"], idx["grade"]):
                continue
            def get(key):
                i = idx[key]
                return texts[i] if 0 <= i < len(texts) else ""
            name = get("name")
            if not name or _lower(name) in {"итого", "всего", "дисциплина"}:
                continue
            course = _positive_int(get("course")) or context_course
            semester = _positive_int(get("semester")) or context_semester
            title = (f"{course} курс · " if course else "") + (
                f"{semester} семестр" if semester else "Семестр не указан")
            key = (course, semester)
            if key not in groups:
                groups[key] = {"title": title, "course": course, "semester": semester, "disciplines": []}
            # Do not deduplicate by name/grade: exam and project can legitimately match.
            item = {field: get(field) for field in (
                "name", "control", "teacher", "date", "grade", "credits", "hours")}
            item.update(course=course, semester=semester,
                        numericGrade=_numeric_grade(item["grade"]))
            groups[key]["disciplines"].append(item)
    semesters = sorted(groups.values(), key=lambda g: (
        g["semester"] if g["semester"] is not None else 999,
        g["course"] if g["course"] is not None else 999))
    return _parse_student(soup), semesters


def _numeric_grade(value):
    value = _lower(value)
    words = {"отлично": 5, "хорошо": 4, "удовлетворительно": 3,
             "неудовлетворительно": 2, "неуд": 2, "неуд.": 2}
    if value in words:
        return words[value]
    if re.fullmatch(r"[2-5](?:[.,]0+)?", value):
        return int(float(value.replace(",", ".")))
    return None


def _site_number(soup, label):
    # Handles text nodes separated by <br>, <span> or <td>.
    text = soup.get_text(" ", strip=True)
    match = re.search(rf"{label}\s*:?\s*(\d+(?:[.,]\d+)?)(?![\d.,])", text, re.I)
    return float(match[1].replace(",", ".")) if match else None


def _statement_summary(semesters, soup=None):
    items = [item for group in semesters for item in group["disciplines"]]
    grades = [_numeric_grade(item["grade"]) for item in items]
    grades = [g for g in grades if g is not None]
    calculated = round(sum(grades) / len(grades), 2) if grades else None
    average = _site_number(_statement_scope(soup), r"Средний\s+балл") if soup is not None else None
    rating = _site_number(_statement_scope(soup), r"Рейтинг\s+студента") if soup is not None else None
    failed = sum(_numeric_grade(i["grade"]) == 2 or _lower(i["grade"]) in {
        "не зачтено", "незачтено", "не зачет", "незачет", "н/я", "н.я.", "неявка"} for i in items)
    return {
        "average": average if average is not None else calculated,
        "averageSource": "site" if average is not None else "calculated",
        "rating": rating,
        "total": len(items),
        "failed": failed,
        # A missing mark or failed attempt is not proof of a current debt.
        "debts": None,
    }
