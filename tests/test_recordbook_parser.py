import unittest
from bs4 import BeautifulSoup
from recordbook_parser import _columns, _parse_statement, _statement_summary


PAGE = '''<!doctype html><html lang="ru"><body>
<nav><a href="#grades">Электронная зачетная книжка</a>
<a href="#archive">Архив оценок</a></nav>
<div id="grades">
<p>Рейтинг студента: 0</p><p>Средний балл: 4,82</p>
<h3>1 курс 1 семестр</h3>
<table><tr><th>Дисциплина</th><th>Вид контроля</th><th>Кол-во часов</th>
<th>Преподаватель</th><th>Курс</th><th>Семестр</th><th>Дата</th><th>Оценка</th></tr>
<tr><td>Физическая культура (ОК 01, ОК 02)</td><td>Зачет с оценкой</td><td>/36</td>
<td>Саламагареева Г.М.</td><td>1</td><td>1</td><td>20.12.2024</td><td>Зачтено</td></tr>
<tr><td>Математика</td><td>Экзамен</td><td>/108</td><td>Сагадеева Э.А.</td>
<td>1</td><td>1</td><td>27.12.2024</td><td>Отлично</td></tr>
<tr><td>История</td><td>Экзамен</td><td>/72</td><td>Сагадеева Э.А.</td>
<td>1</td><td>1</td><td>27.12.2024</td><td>4</td></tr></table>
<h3>2 курс 3 семестр</h3>
<table><tr><th>Дисциплина</th><th>Вид контроля</th><th>Кол-во часов</th>
<th>Преподаватель</th><th>Курс</th><th>Семестр</th><th>Дата</th><th>Оценка</th></tr>
<tr><td>Физика</td><td>Экзамен</td><td>/104</td><td>Касимов Р.А.</td>
<td>2</td><td>3</td><td>30.06.2025</td><td>Удовлетворительно</td></tr></table>
</div>
<div id="archive"><h3>Архив</h3><table><tr><th>Дисциплина</th><th>Оценка</th></tr>
<tr><td>Архивная оценка</td><td>5</td></tr></table></div>
</body></html>'''


class RecordBookParserTests(unittest.TestCase):
    def setUp(self):
        self.soup = BeautifulSoup(PAGE, 'lxml')

    def test_distinguishes_course_from_semester_and_groups(self):
        student, groups = _parse_statement(self.soup)
        self.assertEqual([(g['course'], g['semester']) for g in groups], [(1, 1), (2, 3)])
        self.assertEqual(groups[0]['title'], '1 курс · 1 семестр')
        self.assertEqual(groups[1]['disciplines'][0]['name'], 'Физика')

    def test_keeps_every_official_field_and_grade_type(self):
        item = _parse_statement(self.soup)[1][1]['disciplines'][0]
        self.assertEqual(item['control'], 'Экзамен')
        self.assertEqual(item['hours'], '/104')
        self.assertEqual(item['teacher'], 'Касимов Р.А.')
        self.assertEqual(item['date'], '30.06.2025')
        self.assertEqual(item['grade'], 'Удовлетворительно')
        self.assertEqual(item['numericGrade'], 3)

    def test_does_not_mix_archive_and_preserves_repeated_names(self):
        _, groups = _parse_statement(self.soup)
        all_names = [d['name'] for g in groups for d in g['disciplines']]
        self.assertNotIn('Архивная оценка', all_names)
        self.assertEqual(len(all_names), 4)

    def test_site_average_and_rating_are_authoritative(self):
        _, groups = _parse_statement(self.soup)
        summary = _statement_summary(groups, self.soup)
        self.assertEqual(summary['average'], 4.82)
        self.assertEqual(summary['averageSource'], 'site')
        self.assertEqual(summary['rating'], 0)
        self.assertEqual(summary['total'], 4)
        self.assertIsNone(summary['debts'])

    def test_header_wrapping_still_finds_named_columns(self):
        indexes = _columns(['Дисциплина', 'Вид контроля', 'Кол-во\nчасов',
                            'Преподаватель', 'Курс', 'Семестр', 'Дата', 'Оценка'])
        self.assertEqual(indexes['course'], 4)
        self.assertEqual(indexes['semester'], 5)
        self.assertEqual(indexes['grade'], 7)


if __name__ == '__main__':
    unittest.main()
