import mysql.connector

conn = mysql.connector.connect(
    host="localhost",
    user="callpilot_user",
    password="theoptimizers",
    database="callpilot_db"
)

print("Connected:", conn.is_connected())
conn.close()
