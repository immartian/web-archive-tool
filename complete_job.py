import sqlite3
from datetime import datetime

job_id = 'e60af519-eee2-4f2b-9952-1253908002d1'
filename = f'archive-{job_id}.wacz'
local_path = f'{job_id}/{filename}'

conn = sqlite3.connect('data/archives.db')
cursor = conn.cursor()

cursor.execute('''
    UPDATE archive_jobs 
    SET status = ?, progress = ?, completed_at = ?, archive_path = ?, local_path = ?
    WHERE job_id = ?
''', ('completed', 100, datetime.now().isoformat(), filename, local_path, job_id))

conn.commit()
conn.close()

print(f"Job {job_id} marked as completed!")
print(f"Archive path: {local_path}")