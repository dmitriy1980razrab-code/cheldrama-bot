"""Create named staff accounts without placing passwords in command history."""
from pathlib import Path
import argparse
import getpass
import sys
import sqlite3
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from theatre_bot.admin_access import ROLES,create_staff,password_hash,audit
from theatre_bot.subscribers import connect_subscribers,initialize_subscribers

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['create','reset-password'])
    parser.add_argument('--username',required=True)
    parser.add_argument('--role',choices=list(ROLES),default='advertising')
    args=parser.parse_args()
    password=getpass.getpass('Пароль (минимум 12 символов): ')
    if password!=getpass.getpass('Повтор пароля: '):raise SystemExit('Пароли не совпадают')
    db=connect_subscribers(Path(__file__).parents[1]/'data/subscribers.sqlite3')
    try:
        initialize_subscribers(db)
        if args.action=='create':create_staff(db,args.username,password,args.role)
        else:
            value=password_hash(password)
            with db:
                if not db.execute('UPDATE admin_users SET password_hash=? WHERE username=?',(value,args.username)).rowcount:
                    raise ValueError('Учётная запись не найдена')
                audit(db,'setup','password_reset',args.username)
        print('Учётная запись сохранена. Пароль не выводится.')
    except (ValueError, sqlite3.IntegrityError) as error:
        raise SystemExit('Не удалось сохранить: проверьте логин, роль и требования к паролю.')
    finally:db.close()
