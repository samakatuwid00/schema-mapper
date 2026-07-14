import os
import mysql.connector

conn = mysql.connector.connect(
    host=os.environ.get("LRMIS_STAGING_HOST", "localhost"),
    port=int(os.environ.get("LRMIS_STAGING_PORT", 3307)),
    user=os.environ.get("LRMIS_ROOT_USER", "root"),
    password=os.environ.get("LRMIS_ROOT_PASSWORD", "root"),
)
cur = conn.cursor()
cur.execute("SHOW DATABASES LIKE 'lrmis_staging%'")
print(cur.fetchall())
