# uust-proxy

Прокси к электронному журналу УУСТ (`account.str.uust.ru`). Нужен приложению
[УУСТ Журнал](https://github.com/Exile37/uust-jornal): браузерные запросы к журналу
блокирует CORS, а cookies сессии должны жить на сервере, а не в приложении.

## Маршруты

| Маршрут | Что делает |
|---|---|
| `POST /api/login` | Вход, cookies журнала сохраняются в сессии |
| `POST /api/logout` | Сброс сессии |
| `GET /api/subjects` | Предметы студента |
| `GET /api/grades?url=…` | Оценки по предмету |
| `GET /api/schedule/groups?faculty=26` | Список групп |
| `GET /api/schedule/timetable?id=…&week=…` | Расписание |
| `GET /api/schedule/week_header?id=…&week=…` | Шапка недели |
| `GET /api/recordbook` | Официальная зачётная книжка (`schemaVersion: 2`) |
| `GET /api/debug/statement` | Диагностика страницы зачётки |

`/api/recordbook` разбирает `StatementStudents/Index` модулем `recordbook_parser.py`:
берутся только именованные колонки, вкладки «Электронная зачётная книжка» и архив
не смешиваются, оценки из журнала не подставляются вместо официальных.

## Переменные окружения

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `PORT` | `5000` | Порт, который слушает gunicorn |
| `SECRET_KEY` | встроенный | Ключ подписи сессии. Если задать новый, все пользователи один раз перевойдут |

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python proxy.py                # http://127.0.0.1:5000
```

Тесты парсера:

```bash
python -m unittest discover -s tests -v
```

## Деплой

### Render

Репозиторий уже подключён к сервису `uust-proxy`. Любой пуш в `main` запускает
пересборку; если авто-деплой выключен — **Manual Deploy → Deploy latest commit**.
Первый запрос после простоя идёт 30–60 секунд: сервис на бесплатном тарифе
засыпает.

### Docker / VPS

```bash
docker build -t uust-proxy .
docker run -d --name uust-proxy -p 5000:5000 \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  --restart unless-stopped uust-proxy
```

Дальше — reverse-proxy (nginx/Caddy) и HTTPS. После переезда поменяйте `PROXY_URL`
в приложении на новый адрес.

## Проверка после деплоя

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<адрес>/api/recordbook
```

`401` — маршрут жив, нужен вход. `404` — задеплоена старая версия.
`502/503/520–522` — сервис не поднялся, смотрите логи.
