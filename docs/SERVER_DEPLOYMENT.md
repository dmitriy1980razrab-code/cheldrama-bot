# Серверное размещение

Тестовый контур развёрнут в Yandex Cloud на российской виртуальной машине Ubuntu
24.04. Проект остаётся переносимым: Docker-конфигурация сохранена, а текущий сервер
работает через Python virtual environment и systemd.

## Текущий тестовый контур

- ВМ: `cheldrama-test-vm`, зона `ru-central1-b`;
- приложение: `/opt/cheldrama-bot`;
- HTTP-сервер: `127.0.0.1:8080`, наружу публикуется только через Nginx;
- публичный статический IPv4: `84.201.153.110`;
- тестовый домен: `vash-kapeldiner.ru` и `www.vash-kapeldiner.ru`;
- HTTPS: сертификат Let's Encrypt, автоматическое продление Certbot;
- служба бота: `cheldrama-bot.service`;
- обновление данных: `cheldrama-sync.timer`, четыре запуска в сутки;
- резервное копирование: `cheldrama-backup.timer`, один запуск в сутки;
- внешнее хранилище: закрытый Yandex Object Storage.

Тестовый HTTPS-контур работает. Официальные Webhook VK/MAX остаются выключенными до
получения доступов театра.

## Домен, Nginx и HTTPS

DNS-записи `A` для основного домена и `www` указывают на статический IPv4 ВМ.
Приложение не публикуется напрямую: Nginx пересылает запросы на `127.0.0.1:8080`.
Исходная HTTP-конфигурация для восстановления хранится в
`deploy/nginx/cheldrama-bot.conf`.

При восстановлении нового сервера сначала устанавливается HTTP-конфигурация:

```bash
sudo cp deploy/nginx/cheldrama-bot.conf /etc/nginx/sites-available/cheldrama-bot
sudo ln -s /etc/nginx/sites-available/cheldrama-bot /etc/nginx/sites-enabled/cheldrama-bot
sudo nginx -t
sudo systemctl reload nginx
```

После обновления DNS Certbot выпускает и устанавливает сертификат:

```bash
sudo certbot --nginx -d vash-kapeldiner.ru -d www.vash-kapeldiner.ru
sudo certbot renew --dry-run
```

Секретный ключ сертификата находится только в `/etc/letsencrypt` на сервере и не
копируется в GitHub или архив проекта. При переходе на официальный домен меняются
DNS, `server_name` и сертификат; приложение и базы данных переносить не требуется.

## Где хранятся данные

- исходный код и история изменений — в GitHub и в рабочей копии на ноутбуке;
- театральная база и будущая база подписчиков — на сервере;
- ежедневные копии — локально на сервере и во внешнем Object Storage;
- ноутбук можно использовать как третью периодическую копию;
- ключи и токены не добавляются в GitHub и архивы проекта.

Поля подписчиков, идентификаторы и содержимое очередей защищаются шифрованием на
уровне приложения. Архив `.gz` является сжатой копией, но не шифрует весь файл базы.
Перед появлением реальных подписчиков требуется включить шифрование резервных копий
целиком либо подтверждённое серверное шифрование хранилища с управлением ключами.

## Установка служб

Версии установленных файлов находятся в `deploy/systemd/`. После копирования изменений:

```bash
sudo cp deploy/systemd/cheldrama-bot.service /etc/systemd/system/
sudo cp deploy/systemd/cheldrama-sync.service /etc/systemd/system/
sudo cp deploy/systemd/cheldrama-sync.timer /etc/systemd/system/
sudo cp deploy/systemd/cheldrama-backup.service /etc/systemd/system/
sudo cp deploy/systemd/cheldrama-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cheldrama-bot.service cheldrama-sync.timer cheldrama-backup.timer
```

Проверка:

```bash
systemctl is-active cheldrama-bot.service
systemctl list-timers cheldrama-sync.timer cheldrama-backup.timer --no-pager
python scripts/check_readiness.py
```

## Автоматическое обновление

`cheldrama-sync.timer` запускает `scripts/sync_all.py` в 00:15, 06:15, 12:15 и
18:15 UTC с небольшой случайной задержкой. `Persistent=true` выполняет пропущенный
запуск после включения сервера. Успешная автоматическая загрузка уже проверена журналом.

## Резервные копии

`scripts/backup_database.py` создаёт согласованные сжатые копии SQLite и файлы
контрольных сумм SHA-256. Скрипт `deploy/scripts/cheldrama-backup-to-cloud.sh`
загружает театральную базу, существующую базу подписчиков и обе контрольные суммы
в закрытый bucket `cheldrama-bot-backups-20260929`.

Доступ выполняется от сервисного аккаунта ВМ без статических access keys. Для объектов
с префиксом `database/` действует автоматическое удаление через 90 дней. Восстановление
копии и проверка SHA-256 выполнены успешно.

До появления реальных подписчиков нужно проверить полный цикл восстановления обеих
баз и защитить архивы целиком в соответствии с утверждённой политикой театра.

## Доступ с другого компьютера

Код восстанавливается из GitHub. Сервер управляется по SSH с закрытым ключом владельца;
порт базы данных наружу не публикуется. В дальнейшем персональные карточки, статистика
и кампании должны быть доступны через отдельный HTTPS-кабинет с учётной записью,
двухфакторной авторизацией и журналированием действий.

Ноутбук не является единственным местом хранения и не нужен для круглосуточной работы
бота, но его локальная копия проекта остаётся полезной дополнительной страховкой.
