"""Persistencia: SQLite (local) o PostgreSQL (DATABASE_URL).
Misma interfaz Store para ambos motores.
"""
import json
import os
import time


class Store:
    def __init__(self, path=None, database_url=None):
        self._url = database_url or os.environ.get("DATABASE_URL", "")
        if self._url:
            import psycopg2
            import psycopg2.extras
            self._pg = True
            self.db = psycopg2.connect(self._url, sslmode="require")
            self.db.autocommit = False
        else:
            import sqlite3
            self._pg = False
            self.db = sqlite3.connect(path or "bot/bot.db", check_same_thread=False)
            self.db.row_factory = sqlite3.Row
        self._schema()

    def _ensure_conn(self):
        if not self._pg:
            return
        try:
            if self.db.closed:
                raise Exception("closed")
            cur = self.db.cursor()
            cur.execute("SELECT 1")
            cur.close()
        except Exception:
            import psycopg2
            self.db = psycopg2.connect(self._url, sslmode="require")
            self.db.autocommit = False

    def _execute(self, sql, params=None):
        if self._pg:
            self._ensure_conn()
            import psycopg2.extras
            cur = self.db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cur = self.db.cursor()
        cur.execute(sql, params or ())
        return cur

    def _commit(self):
        self.db.commit()

    def _scalar(self, cur, default=0):
        """fetchone scalar → value"""
        r = cur.fetchone()
        if r is None:
            return default
        if self._pg:
            return list(r.values())[0]
        return r[0]

    def _row(self, cur):
        """fetchone → dict or None"""
        r = cur.fetchone()
        if r is None:
            return None
        if self._pg:
            return dict(r)
        return {k: r[k] for k in r.keys()}

    def _rows(self, cur):
        """fetchall → list[dict]"""
        rows = cur.fetchall()
        if self._pg:
            return [dict(r) for r in rows]
        return [{k: r[k] for k in r.keys()} for r in rows]

    def _schema(self):
        c = self.db.cursor()
        if self._pg:
            c.execute("""CREATE TABLE IF NOT EXISTS kv(
                k TEXT PRIMARY KEY, v TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS markets(
                symbol TEXT PRIMARY KEY, onoff INTEGER DEFAULT 1, fav INTEGER DEFAULT 0)""")
            c.execute("""CREATE TABLE IF NOT EXISTS assignments(
                id TEXT PRIMARY KEY, strategy TEXT, name TEXT,
                params TEXT, symbol TEXT, timeframe TEXT, onoff INTEGER DEFAULT 1)""")
            c.execute("""CREATE TABLE IF NOT EXISTS trades(
                id SERIAL PRIMARY KEY, tkey TEXT,
                symbol TEXT, strategy TEXT, timeframe TEXT, direction TEXT,
                entry REAL, exit REAL,
                open_epoch BIGINT, close_epoch BIGINT,
                reason_open TEXT, reason_close TEXT,
                pnl REAL, status TEXT DEFAULT 'open',
                bot_id TEXT DEFAULT '', lot REAL DEFAULT 0,
                spread REAL DEFAULT 0, commission REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0, duration REAL DEFAULT 0,
                params TEXT DEFAULT '{}', provider TEXT DEFAULT '',
                costs_assumed INTEGER DEFAULT 1)""")
            c.execute("""CREATE TABLE IF NOT EXISTS signals(
                id SERIAL PRIMARY KEY, epoch BIGINT,
                strategy TEXT, symbol TEXT, timeframe TEXT,
                direction TEXT, price REAL, reason TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS processed(
                uid TEXT PRIMARY KEY)""")
            c.execute("""CREATE TABLE IF NOT EXISTS events(
                id SERIAL PRIMARY KEY, epoch BIGINT,
                kind TEXT, data TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS bots(
                bot_id TEXT PRIMARY KEY, name TEXT, strategy_key TEXT,
                symbol TEXT, provider TEXT, timeframe TEXT,
                capital REAL, lot REAL, max_positions INTEGER,
                status TEXT DEFAULT 'running', created BIGINT,
                spread REAL, commission REAL,
                costs_assumed INTEGER DEFAULT 1,
                config TEXT DEFAULT '{}',
                provider_ref TEXT DEFAULT '')""")
            c.execute("""CREATE TABLE IF NOT EXISTS cmc_assets(
                cmc_id INTEGER PRIMARY KEY, symbol TEXT, name TEXT,
                slug TEXT, rank INTEGER, price REAL, volume_24h REAL,
                market_cap REAL, pairs INTEGER, updated TEXT,
                synced_at BIGINT)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_cmc_symbol ON cmc_assets(symbol)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_cmc_name ON cmc_assets(name)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_cmc_rank ON cmc_assets(rank)")
        else:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS markets(symbol TEXT PRIMARY KEY, onoff INTEGER DEFAULT 1, fav INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS assignments(id TEXT PRIMARY KEY, strategy TEXT, name TEXT,
                params TEXT, symbol TEXT, timeframe TEXT, onoff INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, tkey TEXT,
                symbol TEXT, strategy TEXT, timeframe TEXT, direction TEXT, entry REAL, exit REAL,
                open_epoch INTEGER, close_epoch INTEGER, reason_open TEXT, reason_close TEXT,
                pnl REAL, status TEXT DEFAULT 'open');
            CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY AUTOINCREMENT, epoch INTEGER,
                strategy TEXT, symbol TEXT, timeframe TEXT, direction TEXT, price REAL, reason TEXT);
            CREATE TABLE IF NOT EXISTS processed(uid TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, epoch INTEGER,
                kind TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS bots(bot_id TEXT PRIMARY KEY, name TEXT, strategy_key TEXT,
                symbol TEXT, provider TEXT, timeframe TEXT, capital REAL, lot REAL, max_positions INTEGER,
                status TEXT DEFAULT 'running', created INTEGER, spread REAL, commission REAL,
                costs_assumed INTEGER DEFAULT 1, config TEXT);
            CREATE TABLE IF NOT EXISTS cmc_assets(cmc_id INTEGER PRIMARY KEY, symbol TEXT, name TEXT,
                slug TEXT, rank INTEGER, price REAL, volume_24h REAL, market_cap REAL,
                pairs INTEGER, updated TEXT, synced_at INTEGER);
            CREATE INDEX IF NOT EXISTS idx_cmc_symbol ON cmc_assets(symbol);
            CREATE INDEX IF NOT EXISTS idx_cmc_name ON cmc_assets(name);
            CREATE INDEX IF NOT EXISTS idx_cmc_rank ON cmc_assets(rank);
            """)
            for col in ("bot_id TEXT", "lot REAL", "spread REAL", "commission REAL",
                        "pnl_pct REAL", "duration REAL", "params TEXT", "provider TEXT",
                        "costs_assumed INTEGER DEFAULT 1"):
                try:
                    c.execute(f"ALTER TABLE trades ADD COLUMN {col}")
                except Exception:
                    pass
            try:
                c.execute("ALTER TABLE bots ADD COLUMN provider_ref TEXT DEFAULT ''")
            except Exception:
                pass
        self._commit()

    def get(self, k, default=None):
        cur = self._execute("SELECT v FROM kv WHERE k=%s" if self._pg else "SELECT v FROM kv WHERE k=?", (k,))
        row = cur.fetchone()
        if row is None:
            return default
        if self._pg:
            return row.get("v", default)
        return row[0]

    def set(self, k, v):
        if self._pg:
            self._execute("INSERT INTO kv(k,v) VALUES(%s,%s) ON CONFLICT(k) DO UPDATE SET v=EXCLUDED.v", (k, v))
        else:
            self._execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, v))
        self._commit()

    def is_processed(self, uid):
        cur = self._execute("SELECT 1 FROM processed WHERE uid=%s" if self._pg else "SELECT 1 FROM processed WHERE uid=?", (uid,))
        return cur.fetchone() is not None

    def mark_processed(self, uid):
        if self._pg:
            self._execute("INSERT INTO processed(uid) VALUES(%s) ON CONFLICT DO NOTHING", (uid,))
        else:
            self._execute("INSERT OR IGNORE INTO processed(uid) VALUES(?)", (uid,))
        self._commit()

    def upsert_market(self, symbol, onoff=1, fav=0):
        if self._pg:
            self._execute("INSERT INTO markets(symbol,onoff,fav) VALUES(%s,%s,%s) "
                          "ON CONFLICT(symbol) DO UPDATE SET onoff=EXCLUDED.onoff, fav=EXCLUDED.fav",
                          (symbol, onoff, fav))
        else:
            self._execute("INSERT INTO markets(symbol,onoff,fav) VALUES(?,?,?) "
                          "ON CONFLICT(symbol) DO UPDATE SET onoff=excluded.onoff, fav=excluded.fav",
                          (symbol, onoff, fav))
        self._commit()

    def markets(self):
        return self._rows(self._execute("SELECT * FROM markets ORDER BY symbol"))

    def save_assignment(self, a):
        vals = (a.id, a.strategy_key, a.strategy_name, json.dumps(a.params),
                a.symbol, a.timeframe, 1 if a.on else 0)
        if self._pg:
            self._execute("INSERT INTO assignments(id,strategy,name,params,symbol,timeframe,onoff) "
                          "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET "
                          "strategy=EXCLUDED.strategy, name=EXCLUDED.name, params=EXCLUDED.params",
                          vals)
        else:
            self._execute("INSERT OR REPLACE INTO assignments VALUES(?,?,?,?,?,?,?)", vals)
        self._commit()

    def assignments(self):
        return self._rows(self._execute("SELECT * FROM assignments"))

    def open_trade(self, pos, price, extra=None):
        e = extra or {}
        cols = "tkey,symbol,strategy,timeframe,direction,entry,open_epoch,status,bot_id,lot,spread,commission,params,provider,costs_assumed"
        vals = (pos.key, pos.symbol,
                e.get("strategy") or pos.strategy_id,
                e.get("timeframe") or pos.timeframe,
                pos.direction, price, pos.epoch, "open",
                e.get("bot_id", ""), e.get("lot", 0), e.get("spread", 0),
                e.get("commission", 0), json.dumps(e.get("params", {})),
                e.get("provider", ""), 1 if e.get("costs_assumed", True) else 0)
        if self._pg:
            self._execute(f"INSERT INTO trades({cols}) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", vals)
        else:
            ph = ",".join(["?"] * 15)
            self._execute(f"INSERT INTO trades({cols}) VALUES({ph})", vals)
        self._commit()

    def close_trade(self, pos, price, epoch, reason, pnl, extra=None):
        e = extra or {}
        qty = getattr(pos, "qty", 0) or 0
        entry = getattr(pos, "entry", price) or price
        pnl_pct = (pnl / (abs(entry) * qty) * 100) if entry and qty else 0.0
        duration = max(0, (epoch or 0) - (getattr(pos, "epoch", 0) or 0))
        if self._pg:
            self._execute("UPDATE trades SET exit=%s,close_epoch=%s,reason_close=%s,pnl=%s,"
                          "pnl_pct=%s,duration=%s,status='closed'"
                          " WHERE tkey=%s AND status='open'",
                          (price, epoch, reason, pnl, pnl_pct, duration, pos.key))
        else:
            self._execute("UPDATE trades SET exit=?,close_epoch=?,reason_close=?,pnl=?,"
                          "pnl_pct=?,duration=?,status='closed'"
                          " WHERE tkey=? AND status='open'",
                          (price, epoch, reason, pnl, pnl_pct, duration, pos.key))
        self._commit()

    def open_trades(self, bot_id=None):
        if bot_id:
            cur = self._execute(
                "SELECT * FROM trades WHERE status='open' AND bot_id=%s ORDER BY id DESC" if self._pg else
                "SELECT * FROM trades WHERE status='open' AND bot_id=? ORDER BY id DESC", (bot_id,))
        else:
            cur = self._execute("SELECT * FROM trades WHERE status='open' ORDER BY id DESC")
        return self._rows(cur)

    def trade_history(self, limit=200, bot_id=None):
        if bot_id:
            cur = self._execute(
                "SELECT * FROM trades WHERE status='closed' AND bot_id=%s ORDER BY id DESC LIMIT %s" if self._pg else
                "SELECT * FROM trades WHERE status='closed' AND bot_id=? ORDER BY id DESC LIMIT ?", (bot_id, limit))
        else:
            cur = self._execute(
                "SELECT * FROM trades WHERE status='closed' ORDER BY id DESC LIMIT %s" if self._pg else
                "SELECT * FROM trades WHERE status='closed' ORDER BY id DESC LIMIT ?", (limit,))
        return self._rows(cur)

    def save_bot(self, b):
        provider_ref = getattr(b, "provider_ref", "")
        costs = 1 if getattr(b, "costs_assumed", True) else 0
        vals = (b.bot_id, b.display_name, b.strategy_key, b.symbol, b.provider,
                b.timeframe, float(b.capital), float(b.lot), int(b.max_positions),
                b.status, int(b.created), float(b.spread), float(b.commission),
                costs, "{}", provider_ref)
        if self._pg:
            self._execute(
                "INSERT INTO bots(bot_id,name,strategy_key,symbol,provider,timeframe,"
                "capital,lot,max_positions,status,created,spread,commission,costs_assumed,config,provider_ref) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(bot_id) DO UPDATE SET name=EXCLUDED.name,strategy_key=EXCLUDED.strategy_key,"
                "symbol=EXCLUDED.symbol,provider=EXCLUDED.provider,timeframe=EXCLUDED.timeframe,"
                "capital=EXCLUDED.capital,lot=EXCLUDED.lot,max_positions=EXCLUDED.max_positions,"
                "status=EXCLUDED.status,spread=EXCLUDED.spread,commission=EXCLUDED.commission,"
                "costs_assumed=EXCLUDED.costs_assumed,provider_ref=EXCLUDED.provider_ref", vals)
        else:
            self._execute("INSERT OR REPLACE INTO bots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals)
        self._commit()

    def delete_bot(self, bot_id):
        if self._pg:
            self._execute("DELETE FROM bots WHERE bot_id=%s", (bot_id,))
        else:
            self._execute("DELETE FROM bots WHERE bot_id=?", (bot_id,))
        self._commit()

    def bots(self):
        return self._rows(self._execute("SELECT * FROM bots ORDER BY created"))

    def get_bot(self, bot_id):
        cur = self._execute(
            "SELECT * FROM bots WHERE bot_id=%s" if self._pg else
            "SELECT * FROM bots WHERE bot_id=?", (bot_id,))
        return self._row(cur)

    def save_asset(self, a):
        vals = (a.get("cmc_id"), a.get("symbol"), a.get("name"), a.get("slug"),
                a.get("rank"), a.get("price"), a.get("volume_24h"),
                a.get("market_cap"), a.get("pairs"), a.get("updated"), int(time.time()))
        if self._pg:
            self._execute(
                "INSERT INTO cmc_assets(cmc_id,symbol,name,slug,rank,price,volume_24h,"
                "market_cap,pairs,updated,synced_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(cmc_id) DO UPDATE SET symbol=EXCLUDED.symbol,name=EXCLUDED.name,"
                "slug=EXCLUDED.slug,rank=EXCLUDED.rank,price=EXCLUDED.price,volume_24h=EXCLUDED.volume_24h,"
                "market_cap=EXCLUDED.market_cap,pairs=EXCLUDED.pairs,updated=EXCLUDED.updated,"
                "synced_at=EXCLUDED.synced_at", vals)
        else:
            self._execute("INSERT OR REPLACE INTO cmc_assets VALUES(?,?,?,?,?,?,?,?,?,?,?)", vals)
        self._commit()
        return 1

    def asset_count(self):
        return self._scalar(self._execute("SELECT COUNT(*) FROM cmc_assets"))

    def search_assets(self, q, limit=10):
        like = f"%{q.strip().upper()}%"
        if self._pg:
            cur = self._execute(
                "SELECT * FROM cmc_assets WHERE UPPER(symbol)=%s OR UPPER(symbol) LIKE %s "
                "OR UPPER(name) LIKE %s OR UPPER(slug)=%s OR cmc_id::text=%s "
                "ORDER BY COALESCE(rank, 999999) LIMIT %s",
                (q.strip().upper(), like, like, q.strip().lower(), q.strip(), limit))
        else:
            cur = self._execute(
                "SELECT * FROM cmc_assets WHERE UPPER(symbol)=? OR UPPER(symbol) LIKE ? "
                "OR UPPER(name) LIKE ? OR UPPER(slug)=? OR CAST(cmc_id AS TEXT)=? "
                "ORDER BY COALESCE(rank, 999999) LIMIT ?",
                (q.strip().upper(), like, like, q.strip().lower(), q.strip(), limit))
        return self._rows(cur)

    def get_asset_by_id(self, cid):
        cur = self._execute(
            "SELECT * FROM cmc_assets WHERE cmc_id=%s" if self._pg else
            "SELECT * FROM cmc_assets WHERE cmc_id=?", (cid,))
        return self._row(cur)

    def signal(self, s):
        if self._pg:
            self._execute(
                "INSERT INTO signals(epoch,strategy,symbol,timeframe,direction,price,reason) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (s.candle_open_epoch, s.strategy_id, s.symbol, s.timeframe,
                 s.direction, s.price, s.reason))
        else:
            self._execute(
                "INSERT INTO signals(epoch,strategy,symbol,timeframe,direction,price,reason) "
                "VALUES(?,?,?,?,?,?,?)",
                (s.candle_open_epoch, s.strategy_id, s.symbol, s.timeframe,
                 s.direction, s.price, s.reason))
        self._commit()

    def signals(self, limit=100):
        return self._rows(self._execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT %s" if self._pg else
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)))

    def event(self, kind, data):
        try:
            if self._pg:
                self._execute("INSERT INTO events(epoch,kind,data) VALUES(%s,%s,%s)",
                              (int(time.time()), kind, json.dumps(data, default=str)))
            else:
                self._execute("INSERT INTO events(epoch,kind,data) VALUES(?,?,?)",
                              (int(time.time()), kind, json.dumps(data, default=str)))
            self._commit()
        except Exception:
            pass
