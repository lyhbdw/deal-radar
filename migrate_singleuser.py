#!/usr/bin/env python3
"""Convert the former multi-user database to the single-user schema."""
import shutil, sqlite3, sys
from pathlib import Path
from singleuser import connect, now

old_path=Path(sys.argv[1] if len(sys.argv)>1 else Path(__file__).with_name('feedsentinel.db'))
new_path=old_path.with_suffix('.singleuser.db')
if new_path.exists(): new_path.unlink()
old=sqlite3.connect(old_path)
new=connect(str(new_path))
try:
    owner=old.execute('SELECT user_id FROM users ORDER BY user_id LIMIT 1').fetchone()
    owner_id=owner[0] if owner else None
    if owner_id is not None:
        for _,keyword in old.execute('SELECT id,keyword FROM user_keywords WHERE user_id=? ORDER BY id',(owner_id,)):
            new.execute('INSERT OR IGNORE INTO keywords(keyword,created_at) VALUES(?,?)',(keyword,now()))
        for source,enabled in old.execute('SELECT source,enabled FROM user_sources WHERE user_id=?',(owner_id,)):
            new.execute('INSERT OR REPLACE INTO source_config(source,enabled,interval,updated_at) VALUES(?,?,?,?)',(source,enabled,1,now()))
    for row in old.execute('SELECT source,guid,title,link,author,category,pub_date,matched_keywords,first_seen FROM seen_posts'):
        new.execute('INSERT OR IGNORE INTO seen_posts VALUES(?,?,?,?,?,?,?,?,?)',row)
    for row in old.execute('SELECT source,guid,message,markup,status,attempts,next_attempt,claimed_at,last_error,sent_at,created_at FROM user_notifications'):
        new.execute('INSERT OR IGNORE INTO notifications(source,guid,message,markup,status,attempts,next_attempt,claimed_at,last_error,sent_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',row)
    for row in old.execute('SELECT source,guid,title,category,matched_keywords,pushed_at FROM user_notification_history'):
        new.execute('INSERT OR IGNORE INTO notification_history(source,guid,title,matched_keywords,pushed_at) VALUES(?,?,?,?,?)',(row[0],row[1],row[2],row[4],row[5]))
    for key,value in old.execute('SELECT key,value FROM monitor_state'):
        new.execute('INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)',(key,value))
    new.commit()
finally:
    old.close(); new.close()
backup=old_path.with_suffix('.pre-singleuser.db')
if not backup.exists(): shutil.copy2(old_path,backup)
shutil.move(new_path,old_path)
print(f'migrated {old_path}')
