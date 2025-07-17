import sqlite3

conn = sqlite3.connect('data/archives.db')
print('All jobs:')
for row in conn.execute('SELECT job_id, status, progress, url FROM archive_jobs ORDER BY created_at DESC').fetchall():
    print(row)
conn.close()