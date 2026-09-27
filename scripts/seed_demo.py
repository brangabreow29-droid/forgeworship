#!/usr/bin/env python3
"""Seed ForgeWorship with demo content: 6 songs, Isaiah 42, sample media,
and a complete Sunday Service playlist. Public-domain hymns + original demo texts."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))
import main  # noqa: E402

conn = main.db()
main.init_db()

# ---------------------------------------------------------------- songs
SONGS = [
    {
        "title": "Amazing Grace",
        "author": "John Newton (1779)",
        "ccli": "Public Domain",
        "lyrics": """[Verse 1]
Amazing grace, how sweet the sound
That saved a wretch like me
I once was lost, but now am found
Was blind, but now I see

[Verse 2]
'Twas grace that taught my heart to fear
And grace my fears relieved
How precious did that grace appear
The hour I first believed

[Verse 3]
Through many dangers, toils and snares
I have already come
'Tis grace has brought me safe thus far
And grace will lead me home

[Chorus]
My chains are gone, I've been set free
My God, my Saviour has ransomed me
And like a flood His mercy rains
Unending love, amazing grace""",
    },
    {
        "title": "How Great Thou Art",
        "author": "Carl Boberg / Stuart K. Hine",
        "ccli": "Public Domain",
        "lyrics": """[Verse 1]
O Lord my God, when I in awesome wonder
Consider all the worlds Thy hands have made
I see the stars, I hear the rolling thunder
Thy power throughout the universe displayed

[Chorus]
Then sings my soul, my Saviour God, to Thee
How great Thou art, how great Thou art
Then sings my soul, my Saviour God, to Thee
How great Thou art, how great Thou art

[Verse 2]
When through the woods and forest glades I wander
And hear the birds sing sweetly in the trees
When I look down from lofty mountain grandeur
And hear the brook and feel the gentle breeze

[Verse 3]
And when I think that God, His Son not sparing
Sent Him to die, I scarce can take it in
That on the cross, my burden gladly bearing
He bled and died to take away my sin""",
    },
    {
        "title": "Great Is Thy Faithfulness",
        "author": "Thomas O. Chisholm (1923)",
        "ccli": "Public Domain",
        "lyrics": """[Verse 1]
Great is Thy faithfulness, O God my Father
There is no shadow of turning with Thee
Thou changest not, Thy compassions they fail not
As Thou hast been Thou forever wilt be

[Chorus]
Great is Thy faithfulness
Great is Thy faithfulness
Morning by morning new mercies I see
All I have needed Thy hand hath provided
Great is Thy faithfulness, Lord, unto me

[Verse 2]
Summer and winter, and springtime and harvest
Sun, moon and stars in their courses above
Join with all nature in manifold witness
To Thy great faithfulness, mercy and love""",
    },
    {
        "title": "Blessed Assurance",
        "author": "Fanny J. Crosby (1873)",
        "ccli": "Public Domain",
        "lyrics": """[Verse 1]
Blessed assurance, Jesus is mine
O what a foretaste of glory divine
Heir of salvation, purchase of God
Born of His Spirit, washed in His blood

[Chorus]
This is my story, this is my song
Praising my Saviour all the day long
This is my story, this is my song
Praising my Saviour all the day long

[Verse 2]
Perfect submission, perfect delight
Visions of rapture now burst on my sight
Angels descending, bring from above
Echoes of mercy, whispers of love""",
    },
    {
        "title": "Be Thou My Vision",
        "author": "Ancient Irish hymn, tr. Mary Byrne",
        "ccli": "Public Domain",
        "lyrics": """[Verse 1]
Be Thou my Vision, O Lord of my heart
Naught be all else to me, save that Thou art
Thou my best Thought, by day or by night
Waking or sleeping, Thy presence my light

[Verse 2]
Be Thou my Wisdom, and Thou my true Word
I ever with Thee and Thou with me, Lord
Thou my great Father, I Thy true son
Thou in me dwelling, and I with Thee one

[Verse 3]
Riches I heed not, nor man's empty praise
Thou mine Inheritance, now and always
Thou and Thou only, first in my heart
High King of Heaven, my Treasure Thou art""",
    },
    {
        "title": "10,000 Reasons (Bless the Lord)",
        "author": "Jonas Myrin & Matt Redman",
        "ccli": "6016351",
        "lyrics": """[Verse 1]
The sun comes up, it's a new day dawning
It's time to sing Your song again
Whatever may pass, and whatever lies before me
Let me be singing when the evening comes

[Chorus]
Bless the Lord, O my soul
O my soul, worship His holy name
Sing like never before, O my soul
I'll worship Your holy name

[Verse 2]
You're rich in love, and You're slow to anger
Your name is great, and Your heart is kind
For all Your goodness I will keep on singing
Ten thousand reasons for my heart to find

[Bridge]
And on that day when my strength is failing
The end draws near and my time has come
Still my soul will sing Your praise unending
Ten thousand years and then forevermore""",
    },
]

song_ids = {}
for s in SONGS:
    cur = conn.execute(
        "INSERT INTO songs (title,author,ccli,lyrics,theme_id,notes,updated_at) VALUES (?,?,?,?,(SELECT id FROM themes WHERE is_default=1),'',?)",
        (s["title"], s["author"], s["ccli"], s["lyrics"], time.time()),
    )
    song_ids[s["title"]] = cur.lastrowid

# ---------------------------------------------------------------- scriptures
ISAIAH = [
    (1, "Behold my servant, whom I uphold, my chosen, in whom my soul delights; I have put my Spirit upon him; he will bring forth justice to the nations."),
    (2, "He will not cry aloud or lift up his voice, or make it heard in the street."),
    (3, "A bruised reed he will not break, and a smouldering wick he will not quench; he will faithfully bring forth justice."),
    (4, "He will not grow faint or be discouraged till he has established justice in the earth; and the coastlands wait for his law."),
    (5, "Thus says God, the LORD, who created the heavens and stretched them out, who spread out the earth and what comes from it, who gives breath to the people on it and spirit to those who walk in it:"),
    (6, "I am the LORD; I have called you in righteousness; I will take you by the hand and keep you; I will give you as a covenant for the people, a light for the nations."),
    (7, "To open the eyes that are blind, to bring out the prisoners from the prison, those who sit in darkness from the prison house."),
    (8, "I am the LORD; that is my name; my glory I give to no other, nor my praise to carved idols."),
    (9, "Behold, the former things have come to pass, and new things I now declare; before they spring forth I tell you of them."),
]
# Isaiah 42 in three calm slide groups (1-3, 4-6, 7-9)
chunks = [(1, 3), (4, 6), (7, 9)]
scripture_ids = []
cur = conn.execute(
    "INSERT INTO scriptures (reference,version,verses,notes,updated_at) VALUES (?,?,?,?,?)",
    ("Isaiah 42:1-9", "ESV", json.dumps([{"v": v, "text": t} for v, t in ISAIAH]), "Demo - servant song", time.time()),
)
scripture_ids.append(cur.lastrowid)

# ---------------------------------------------------------------- media
media_ids = {}
cur = conn.execute(
    "INSERT INTO media (name,kind,path,theme_id,notes,updated_at) VALUES (?,?,?,?,?,?)",
    ("Calm Gradient", "color", "#0b1218", None, "Default resting backdrop", time.time()),
)
media_ids["Calm Gradient"] = cur.lastrowid

# ---------------------------------------------------------------- playlist
pl_name = "Sunday Service (Demo)"
row = conn.execute("SELECT id FROM playlists WHERE name=?", (pl_name,)).fetchone()
if row:
    conn.execute("DELETE FROM playlist_items WHERE playlist_id=?", (row["id"],))
    conn.execute("DELETE FROM playlists WHERE id=?", (row["id"],))
cur = conn.execute("INSERT INTO playlists (name,service_date,notes,created_at,updated_at) VALUES (?,?,?,datetime('now'),datetime('now'))",
                   (pl_name, time.strftime("%Y-%m-%d"), "Demo service seeded by scripts/seed_demo.py"))
pid = cur.lastrowid

def verse_chunk(verses, a, b):
    return [{"v": v, "text": t} for v, t in verses if a <= v <= b]

items = [
    ("song", song_ids["Amazing Grace"]),
    ("scripture", scripture_ids[0]),
    ("song", song_ids["How Great Thou Art"]),
    ("song", song_ids["Great Is Thy Faithfulness"]),
    ("song", song_ids["Blessed Assurance"]),
    ("song", song_ids["Be Thou My Vision"]),
    ("song", song_ids["10,000 Reasons (Bless the Lord)"]),
]
pos = 0
for kind, ref in items:
    if kind == "song":
        label = next(s["title"] for s in SONGS if song_ids[s["title"]] == ref)
    else:
        label = "Isaiah 42:1-9"
    conn.execute(
        "INSERT INTO playlist_items (playlist_id,position,kind,ref_id,label,payload) VALUES (?,?,?,?,?,?)",
        (pid, pos, kind, ref, label, "{}"),
    )
    pos += 1

# custom welcome + benediction slides
conn.execute(
    "INSERT INTO playlist_items (playlist_id,position,kind,ref_id,label,payload) VALUES (?,?,?,?,?,?)",
    (pid, 0, "custom", None, "Welcome", json.dumps({"lines": ["Welcome to worship", "Let everything that has breath praise the LORD"]})),
)
conn.execute(
    "INSERT INTO playlist_items (playlist_id,position,kind,ref_id,label,payload) VALUES (?,?,?,?,?,?)",
    (pid, pos, "custom", None, "Benediction", json.dumps({"lines": ["The LORD bless you and keep you,", "the LORD make his face to shine upon you", "and be gracious to you.  (Numbers 6:24-25)"]})),
)

conn.commit()
print("Seeded:")
print("  songs:", conn.execute("SELECT COUNT(*) c FROM songs").fetchone()["c"])
print("  scriptures:", conn.execute("SELECT COUNT(*) c FROM scriptures").fetchone()["c"])
print("  media:", conn.execute("SELECT COUNT(*) c FROM media").fetchone()["c"])
print("  playlist:", pl_name, "->", conn.execute("SELECT COUNT(*) c FROM playlist_items WHERE playlist_id=?", (pid,)).fetchone()["c"], "items")
conn.close()