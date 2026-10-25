"""Named staff accounts and server-side permissions for the private panel."""
import hashlib
import secrets
from datetime import datetime, timezone

ROLES = {'advertising': 'Реклама / администратор', 'analytics': 'Аналитика',
         'observer': 'Наблюдатель', 'support': 'Техподдержка'}
DEPARTMENTS = {'advertising': 'Реклама, акции', 'social': 'Соц.сети', 'technical': 'Техподдержка'}
ROLE_DEPARTMENTS = {'advertising': 'advertising', 'analytics': 'social', 'support': 'technical'}

def stamp(now=None):
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec='seconds')

def audit(db, actor, action, key=None, now=None):
    db.execute('INSERT INTO admin_audit(actor,action,object_key,recorded_at) VALUES (?,?,?,?)',
               (actor, action, str(key) if key is not None else None, stamp(now)))

def require(user, permission):
    allowed = {'reports': {'advertising','analytics','observer'},
               'campaigns': {'advertising'}, 'accounts': {'advertising'},
               'audience': {'advertising'},
               'tickets': set(ROLE_DEPARTMENTS)}
    if not user or not user['active'] or user['role'] not in allowed.get(permission,set()):
        raise PermissionError('Недостаточно прав')

def password_hash(password):
    if not 12 <= len(password) <= 256:
        raise ValueError('Пароль: от 12 до 256 символов')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
    return f'scrypt${salt.hex()}${digest.hex()}'

def password_matches(password, stored):
    try:
        if len(password)>256: return False
        kind,salt,digest=stored.split('$')
        if kind!='scrypt': return False
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32)
        return secrets.compare_digest(actual,bytes.fromhex(digest))
    except (ValueError,TypeError):
        return False

def create_staff(db, username, password, role, actor='setup', now=None):
    username=username.strip()
    if not username or len(username)>64 or role not in ROLES:
        raise ValueError('Проверьте логин и роль')
    hashed=password_hash(password)
    with db:
        cur=db.execute('INSERT INTO admin_users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
                       (username,hashed,role,stamp(now)))
        audit(db,actor,'account_created',cur.lastrowid,now)
    return cur.lastrowid

def set_staff_active(db,user,target_id,active,now=None):
    require(user,'accounts')
    with db:
        if target_id==user['id']:
            raise ValueError('Нельзя отключить собственную учётную запись')
        if not db.execute('UPDATE admin_users SET active=? WHERE id=?',(int(active),target_id)).rowcount:
            raise LookupError('Учётная запись не найдена')
        audit(db,user['username'],'account_enabled' if active else 'account_disabled',target_id,now)

def set_staff_role(db,user,target_id,role,now=None):
    require(user,'accounts')
    if role not in ROLES or target_id==user['id']:
        raise ValueError('Нельзя менять собственную роль; проверьте выбранную роль')
    with db:
        if not db.execute('UPDATE admin_users SET role=? WHERE id=?',(role,target_id)).rowcount:
            raise LookupError('Учётная запись не найдена')
        audit(db,user['username'],'account_role_changed',target_id,now)
