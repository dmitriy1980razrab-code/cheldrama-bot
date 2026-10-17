from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from theatre_bot.notification_jobs import run_service_job
from theatre_bot.subscribers import IdentityProtector
from theatre_bot.technical_log import create_technical_logger, record_error
from theatre_bot.update_lock import UpdateAlreadyRunning

ROOT = Path(__file__).parents[1]


def main():
    parser = argparse.ArgumentParser(description='Проверки подписанных показов по расписанию.')
    parser.add_argument('--check', action='store_true', help='Только план: без сети и изменений баз.')
    args = parser.parse_args()
    try:
        result = run_service_job(ROOT / 'data/theatre.sqlite3', ROOT / 'data/subscribers.sqlite3',
            protector=None if args.check else IdentityProtector.from_environment(), check_only=args.check)
    except UpdateAlreadyRunning:
        print('Обновление уже выполняется; проверка отложена до следующего запуска.')
        return 0
    except Exception as error:
        if not args.check:
            record_error(create_technical_logger(ROOT / 'data/technical.log'), 'notifications.job', error)
        print(f'Задание остановлено ({type(error).__name__}).')
        return 1
    print('Режим: ' + ('проверка плана без сети и изменений баз' if args.check else 'рабочий'))
    print(f'Показов с подписками: {result.subscriptions}; проверок сейчас: {result.due_checks}; страниц месяцев: {result.fetched_months}')
    print(f'Изменений: {result.changes}; напоминаний подготовлено: {result.reminders}; новых сообщений в очереди: {result.queued}')
    print('Рекламные рассылки этим заданием не запускаются.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
