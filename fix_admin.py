"""One-time script to ensure admin user exists with correct password hash."""
import psycopg2
from urllib.parse import unquote
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
admin_hash = pwd_context.hash("Admin123!")

conn = psycopg2.connect(
    host="aws-0-eu-west-1.pooler.supabase.com",
    port=6543,
    dbname="postgres",
    user="postgres.pochhuolpjgzjjbgrlia",
    password="?@*85PP5xam+%",
)
cur = conn.cursor()

cur.execute("SELECT id, email, role, password_hash FROM users WHERE email = 'admin@cremacuadrado.com'")
row = cur.fetchone()

if row:
    print(f"Admin existe: id={row[0]}, role={row[2]}")
    cur.execute("UPDATE users SET password_hash = %s, email_verified = true, is_active = true WHERE email = 'admin@cremacuadrado.com'", (admin_hash,))
    conn.commit()
    print("Password actualizada a Admin123!")
else:
    print("Admin NO existe, insertando...")
    cur.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role, is_active, email_verified) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        ("admin@cremacuadrado.com", admin_hash, "Admin", "Cremacuadrado", "admin", True, True),
    )
    conn.commit()
    print("Admin creado con Admin123!")

cur.close()
conn.close()
print("Done.")
