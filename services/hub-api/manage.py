"""Admin CLI for Elite Hub.

  python manage.py init
  python manage.py add-user alice "Alice" --tier elite [--admin] [--minutes 240]
  python manage.py set-key alice sk-xxxx         # personal New API key, issued in New API admin
  python manage.py set-minutes alice 300
  python manage.py list
Passwords are prompted interactively, never passed on the command line.
"""
import argparse
import getpass

from app import db


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init")
    a = sp.add_parser("add-user")
    a.add_argument("username"); a.add_argument("display_name")
    a.add_argument("--tier", choices=["campus", "elite"], default="campus")
    a.add_argument("--admin", action="store_true")
    a.add_argument("--minutes", type=int)
    k = sp.add_parser("set-key"); k.add_argument("username"); k.add_argument("key")
    m = sp.add_parser("set-minutes"); m.add_argument("username"); m.add_argument("minutes", type=int)
    sp.add_parser("passwd").add_argument("username")
    sp.add_parser("list")
    args = p.parse_args()
    db.init()

    with db.tx() as c:
        if args.cmd == "add-user":
            pw = getpass.getpass("密码: ")
            minutes = args.minutes if args.minutes is not None else (240 if args.tier == "elite" else 120)
            c.execute("INSERT INTO users(username,display_name,pw_hash,is_admin,tier,compute_minutes) VALUES(?,?,?,?,?,?)",
                      (args.username, args.display_name, db.hash_pw(pw), int(args.admin), args.tier, minutes))
        elif args.cmd == "set-key":
            c.execute("UPDATE users SET gateway_key=? WHERE username=?", (args.key, args.username))
        elif args.cmd == "set-minutes":
            u = c.execute("SELECT id,compute_minutes FROM users WHERE username=?", (args.username,)).fetchone()
            c.execute("UPDATE users SET compute_minutes=? WHERE id=?", (args.minutes, u["id"]))
            c.execute("INSERT INTO compute_ledger(user_id,delta_minutes,reason) VALUES(?,?,?)",
                      (u["id"], args.minutes - u["compute_minutes"], "管理员调整"))
        elif args.cmd == "passwd":
            c.execute("UPDATE users SET pw_hash=? WHERE username=?", (db.hash_pw(getpass.getpass("新密码: ")), args.username))
        elif args.cmd == "list":
            for r in c.execute("SELECT username,display_name,tier,is_admin,compute_minutes,gateway_key IS NOT NULL k FROM users"):
                print(dict(r))
    print("ok")


if __name__ == "__main__":
    main()
