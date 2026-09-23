# 🌟 Магазин Telegram Stars & Premium (Бот + Сайт + Mini App)

Полностью готовый коммерческий проект для продажи **Telegram Stars** (от 50 до 1 000 000 ⭐) и **Telegram Premium** (3, 6, 12 месяцев) с автоматической доставкой через блокчейн TON или API шлюзы.

Все интерфейсы (Telegram-бот, Web-сайт, Telegram Mini App и Админка) работают **в одном процессе Python** на общей базе SQLite (WAL-mode).

---

## 🚀 Быстрый старт за 5 минут

### 1. Требования к серверу
* **ОС:** Ubuntu 22.04 / 24.04 LTS или Debian 12
* **Характеристики:** от 1 vCPU / 1 GB RAM / 5 GB SSD
* **Домен:** Любой домен с настроенным SSL (HTTPS) — необходим для работы Telegram Mini App.

### 2. Клонирование и установка зависимостей
```bash
git clone https://github.com/your-username/telegram-stars-store.git
cd telegram-stars-store

# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate

# Установка пакетов
pip install -r requirements.txt
```

### 3. Настройка конфигурации
Скопируйте пример конфига:
```bash
cp .env.example .env
nano .env
```
Заполните обязательные поля:
* `BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather)
* `ADMIN_TELEGRAM_IDS` — ваш Telegram ID (узнать в [@userinfobot](https://t.me/userinfobot))
* `WEB_APP_URL` — ссылка на ваш домен (`https://stars.example.com`)
* `MYSTARS_FAAS_KEY` или `GREENGAME_API_KEY` — ключ провайдера выдачи

### 4. Запуск через Docker (рекомендуется)
```bash
docker compose up -d --build
```

Или локально через Python:
```bash
python main.py
```

---

## 🌐 Настройка Nginx и SSL (Let's Encrypt)

Создайте конфигурационный файл Nginx: `/etc/nginx/sites-available/stars-store`

```nginx
server {
    server_name stars.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активируйте и выпустите сертификат:
```bash
sudo ln -s /etc/nginx/sites-available/stars-store /etc/nginx/sites-enabled/
sudo certbot --nginx -d stars.yourdomain.com
sudo systemctl reload nginx
```

---

## 🤖 Настройка кнопки Mini App в Telegram

1. Откройте [@BotFather](https://t.me/BotFather)
2. Введите команду `/mybots` и выберите вашего бота.
3. Перейдите в **Bot Settings** -> **Menu Button** -> **Configure menu button**.
4. Отправьте URL: `https://stars.yourdomain.com` и название кнопки: `🌟 Магазин Stars`.

---

## ⚙️ Архитектура и функционал

### 1. Автоматическая выдача
- **MyStars FaaS (TON Smart Contract):** Прямой выкуп звёзд через блокчейн с оплатой комиссий в TON. Минимальные комиссии и моментальная отправка на любой `@username`.
- **GreenGamePay:** Альтернативный API-шлюз для автоматического зачисления подарков.
- **Переключение:** В 1 клик через веб-админку или команду `/admin` в боте без перезагрузки сервера.

### 2. Платёжные шлюзы
- **CryptoBot:** Автоматическое выставление счетов в USDT, TON, BTC через `@CryptoBot`.
- **ЮKassa:** Банковские карты МИР/Visa/Mastercard, СБП.
- **ЮMoney:** P2P переводы с автосверкой по уникальной метке заказа.
- **Внутренний баланс:** Мгновенное списание и поддержка депозитов/возвратов.

### 3. Маркетинг и удержание
- **Реферальная программа:** До 5-20% от заказов приглашённых пользователей с мгновенным зачислением на баланс.
- **Промокоды:**
  - Тип `balance` — прямое пополнение баланса клиента (например, `START100` на 100 ₽).
  - Тип `discount` — процентная скидка на заказ (например, `STARS5` на 5%).
- **Партнёрские промокоды:** Владелец кода получает реферальный процент, покупатель — скидку.

### 4. Безопасность и надёжность
- **SQLite WAL-mode:** Обеспечивает высокую скорость и защиту от блокировок при одновременных запросах.
- **Атомарные операции:** Исключена возможность двойного списания или начисления средств.
- **Проверка подписи initData:** Валидация HMAC-SHA256 для защиты Telegram WebApp.
- **Фоновый реконсилятор:** Каждые 60 секунд проверяет и синхронизирует зависшие транзакции.
- **Автовозврат:** При ошибке поставщика сумма мгновенно возвращается на баланс покупателя.

---

## 🛠 Админ-панель (/admin и Веб-интерфейс)
- **Финансовая аналитика:** Выручка, чистая маржа, проданные звёзды за сегодня/неделю/всё время.
- **Управление наценкой:** Регулировка процента маржи на Stars и Premium в реальном времени.
- **Менеджер заказов:** Поиск по ID/юзернейму, ручная смена статуса, принудительный повтор доставки, возврат средств.
- **Управление пользователями:** Ручное изменение баланса, блокировка (бан), просмотр истории.
- **Рассылка:** Массовая отправка уведомлений всем клиентам бота.
- **Управление витриной (/site):** Включение режима техработ, смена баннера объявлений.
