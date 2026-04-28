import sqlite3
from datetime import datetime

conn = sqlite3.connect('hospital_workflow.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('''
    SELECT p.patient_id, p.priority, p.created_at,
           MIN(a.scheduled_time) AS diag_start
    FROM patients p
    JOIN appointments a ON p.patient_id = a.patient_id
    GROUP BY p.patient_id, p.priority, p.created_at
''').fetchall()
conn.close()

waits = []
for r in rows:
    try:
        arrival = datetime.fromisoformat(str(r['created_at']))
        diag_start = datetime.fromisoformat(str(r['diag_start']))
        wait = max(0.0, (diag_start - arrival).total_seconds() / 60)
        waits.append(wait)
        print(f"{r['patient_id']}: arrival={arrival}, diag_start={diag_start}, wait={wait:.4f} min")
    except Exception as e:
        print(f"Error for {r['patient_id']}: {e}")

print(f"Total waits: {len(waits)}, values: {waits}")
import numpy as np
if waits:
    print(f"Mean: {np.mean(waits):.4f}, Median: {np.median(waits):.4f}, Min: {np.min(waits):.4f}, Max: {np.max(waits):.4f}, Std: {np.std(waits):.4f}")