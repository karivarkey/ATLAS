import sqlite3

c = sqlite3.connect("data/atlas.db")
for (content,) in c.execute(
    "SELECT content FROM messages WHERE role='assistant' ORDER BY created_at DESC LIMIT 2"
):
    print((content or "")[:800])
    print("=" * 60)
