import os, tempfile, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(tempfile.mkdtemp())
open("54088_inst_history.db", "wb").write(b"garbage not a db" * 300)
import dashangdao_helpers as H
c = H.init_sqlite_db()
assert c.execute("select count(*) from inst_holding").fetchone()[0] == 0
print("ok test_init_sqlite_recover", H.SQLITE_DB_FILE)
