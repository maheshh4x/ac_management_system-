import psycopg2

conn = psycopg2.connect(
    host="localhost", port="5433", dbname="postgres",
    user="postgres", password="postgres",
)
conn.autocommit = True
cur = conn.cursor()
cur.execute(
    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conrelid = 'acs'::regclass AND contype = 'c'"
)
for row in cur.fetchall():
    print(row)
cur.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 3")
print(cur.fetchall())
cur.close()
conn.close()
