import sqlite3

c = sqlite3.connect("data/atlas.db")
for row in c.execute(
    "SELECT id, status, current_step, model FROM executions ORDER BY updated_at DESC LIMIT 5"
):
    print(row)
