#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, time, json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple

import requests
import pandas as pd
import numpy as np

# ====== Robust .env loader ======
def _read_env_file(path: Path) -> dict:
    data = {}
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line=line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k,v=line.split("=",1)
                data[k.strip()] = v.strip()
    except Exception as e:
        print(f"[ENV] read error {path}: {e}")
    return data

def _normalize(v: Optional[str]) -> Optional[str]:
    if v is None: return None
    v = v.strip().strip('"').strip("'")
    return v or None

def load_env_robust():
    script_env = Path(__file__).with_name(".env")
    cwd_env = Path.cwd()/".env"
    loaded = False
    try:
        from dotenv import load_dotenv, find_dotenv
        if script_env.exists():
            loaded = load_dotenv(dotenv_path=script_env, override=False) or loaded
        fd = find_dotenv(filename=".env", usecwd=True)
        if fd:
            p = Path(fd)
            if p.exists() and p != script_env:
                loaded = load_dotenv(dotenv_path=p, override=False) or loaded
        print(f"[ENV] dotenv loaded={loaded} | script_env={script_env.exists()} | cwd_env={cwd_env.exists()}")
    except Exception as e:
        print(f"[ENV] python-dotenv unavailable ({e}); using manual parse…")
    for p in [script_env, cwd_env]:
        for k, v in _read_env_file(p).items():
            os.environ.setdefault(k, v)
    print("[ENVCHK]", "TG_TOKEN:", "SET" if os.environ.get("TG_TOKEN") else "MISSING",
          "| TG_CHAT:", "SET" if os.environ.get("TG_CHAT") else "MISSING")

load_env_robust()

@dataclass
class Config:
    timeframe: str = "30m"
    lookback: int = 400
    top_n: int = 20
    exchange_id: str = "binanceusdm"
    adjust_time_diff: bool = True
    telegram_token: Optional[str] = _normalize(os.environ.get("TG_TOKEN"))
    telegram_chat_id: Optional[str] = _normalize(os.environ.get("TG_CHAT"))
    strategy: str = ""   # set per bot
    poll_secs: int = 60  # scan every ~60s

# ====== Telegram helpers ======
def tg_api(cfg: Config, method: str, **params) -> Tuple[bool, dict]:
    if not cfg.telegram_token:
        print("[TG_FAIL] TG_TOKEN مفقود")
        return False, {}
    url = f"https://api.telegram.org/bot{cfg.telegram_token}/{method}"
    try:
        r = requests.get(url, params=params, timeout=12)
        try:
            data = r.json()
        except Exception:
            data = {"ok": False, "description": f"Non-JSON response status={r.status_code}", "text": r.text[:200]}
        ok = (r.status_code == 200) and bool(data.get("ok", False))
        if not ok:
            print(f"[TG_FAIL] {method} status={r.status_code} resp={json.dumps(data)[:300]}")
        return ok, data
    except Exception as e:
        print(f"[TG_ERROR] {method}: {type(e).__name__}: {e}")
        return False, {}

def tg_self_test(cfg: Config) -> None:
    print("[TG_SELFTEST] getMe …")
    ok, data = tg_api(cfg, "getMe")
    if not ok:
        print("  → فشل getMe — تحقق من صحة TG_TOKEN")
        return
    print(f"  → bot = @{data['result'].get('username','?')} (id {data['result'].get('id','?')})")

    if not cfg.telegram_chat_id:
        print("  → TG_CHAT غير موجود في .env")
        return

    print("[TG_SELFTEST] getChat …")
    ok, data = tg_api(cfg, "getChat", chat_id=cfg.telegram_chat_id)
    if not ok:
        print("  → فشل getChat — غالبًا البوت ليس عضوًا في هذه القناة/الجروب أو Chat ID غير صحيح.")
        print("    • لو قناة/جروب: أضف البوت كـ Admin ثم استخدم رقم Chat ID (غالبًا يبدأ بـ -100).")
        print("    • لو محادثة خاصة: افتح البوت واضغط Start.")
    else:
        title = data['result'].get('title') or data['result'].get('username') or data['result'].get('first_name')
        print(f"  → chat ok: {title} (id {data['result'].get('id')})")

    print("[TG_SELFTEST] sendMessage(ONLINE) …")
    ok, data = tg_api(cfg, "sendMessage", chat_id=cfg.telegram_chat_id, text=f"🟢 ONLINE — {cfg.strategy} | {cfg.timeframe}")
    if ok:
        print(f"  → sent message_id={data['result'].get('message_id')}")
    else:
        print("  → فشل الإرسال — راجع الصلاحيات/Chat ID")

def tg_send(cfg: Config, text: str) -> None:
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        print("[TG_MISSING] TG_TOKEN/TG_CHAT غير موجودين — تأكد من .env بجانب السكربت")
        return
    ok, _ = tg_api(cfg, "sendMessage", chat_id=cfg.telegram_chat_id, text=text)
    if ok:
        print("[TG_OK] sent")
    else:
        print("[TG_FAIL] sendMessage failed")

# ====== Exchange ======
class Ex:
    """Minimal Binance futures API wrapper for fetching market data."""

    def __init__(self, cfg: Config):
        self.base = "https://fapi.binance.com"

    def top_usdt_perps(self, n: int) -> List[str]:
        try:
            r = requests.get(f"{self.base}/fapi/v1/ticker/24hr", timeout=10)
            r.raise_for_status()
            tickers = r.json()
        except Exception as e:
            print(f"[EX_ERROR] ticker24hr: {e}")
            return ["BTC/USDT", "ETH/USDT"]
        rows: List[Tuple[str, float]] = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if t.get("contractType") and t.get("contractType") != "PERPETUAL":
                continue
            vol = float(t.get("quoteVolume") or 0)
            rows.append((sym[:-4] + "/USDT", vol))
        rows.sort(key=lambda r: r[1], reverse=True)
        return [s for s, _ in rows[:n]]

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        sym = symbol.replace("/", "")
        params = {"symbol": sym, "interval": timeframe, "limit": limit}
        r = requests.get(f"{self.base}/fapi/v1/klines", params=params, timeout=10)
        r.raise_for_status()
        raw = r.json()
        df = pd.DataFrame(
            [[k[0], k[1], k[2], k[3], k[4], k[5]] for k in raw],
            columns=["ts", "open", "high", "low", "close", "vol"],
        )
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        return df.astype({"open": float, "high": float, "low": float, "close": float, "vol": float})

# ====== Indicator utils ======
def atr(df: pd.DataFrame, n: int=14) -> pd.Series:
    h,l,c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def fmt_price(x: float) -> str:
    try:
        if x >= 1:
            return f"{x:.4f}"
        elif x >= 0.01:
            return f"{x:.6f}"
        else:
            return f"{x:.8f}"
    except Exception:
        return str(x)

def open_msg(cfg: Config, sym: str, side: str, px: float, sl: float, tp: float, reason: str) -> str:
    arrow = "🟢 LONG" if side == "buy" else "🔴 SHORT"
    return (
        f"{arrow} — {sym}  [{cfg.strategy} | {cfg.timeframe}]\n"
        f"Entry: {fmt_price(px)}\n"
        f"SL:    {fmt_price(sl)}\n"
        f"TP:    {fmt_price(tp)}\n"
        f"Reason: {reason}\n"
        f"Time: {pd.Timestamp.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

def close_msg(cfg: Config, sym: str, side: str, entry: float, exit_price: float, outcome: str, pnl_pct: float) -> str:
    tick = "✅ TP hit" if outcome == "TP" else "🛑 SL hit"
    lr = "LONG" if side == "buy" else "SHORT"
    sign = "+" if pnl_pct >= 0 else ""
    return (
        f"{tick} — {sym}  [{cfg.strategy} | {cfg.timeframe}]\n"
        f"{lr}: {fmt_price(entry)} → {fmt_price(exit_price)}\n"
        f"PnL: {sign}{pnl_pct:.2f}%\n"
        f"Time: {pd.Timestamp.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

STRAT_NAME='SCALP'

# ====== Strategy: SCALP (BB + RSI) ======
def bbands(close: pd.Series, n: int=20, k: float=2.0) -> Tuple[pd.Series,pd.Series,pd.Series]:
    ma = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    upper = ma + k*sd
    lower = ma - k*sd
    return lower, ma, upper

def rsi(close: pd.Series, n: int=14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    dn = -delta.clip(upper=0.0)
    ma_up = up.ewm(alpha=1/n, adjust=False).mean()
    ma_dn = dn.ewm(alpha=1/n, adjust=False).mean()
    rs = ma_up / (ma_dn.replace(0, np.nan))
    return 100 - (100/(1+rs))

def strat(df: pd.DataFrame):
    if len(df) < 40: return None
    a = atr(df,14).iloc[-1]
    if np.isnan(a) or a <= 0: return None
    lo = df["close"].rolling(20).mean() - 2.0*df["close"].rolling(20).std(ddof=0)
    hi = df["close"].rolling(20).mean() + 2.0*df["close"].rolling(20).std(ddof=0)
    bb_lo = float(lo.iloc[-1]); bb_hi = float(hi.iloc[-1])
    # RSI
    delta = df["close"].diff()
    up = delta.clip(lower=0.0); dn = -delta.clip(upper=0.0)
    r = float(100 - (100/(1 + (up.ewm(alpha=1/14, adjust=False).mean() / (dn.replace(0, np.nan).ewm(alpha=1/14, adjust=False).mean())))).iloc[-1])
    px = float(df["close"].iloc[-1])
    tp, sl = 1.2*a, 0.8*a
    if px <= bb_lo and r <= 40:  return ("buy",  px - sl, px + tp, f"SCALP: px<=BBlo & RSI={r:.1f}")
    if px >= bb_hi and r >= 60:  return ("sell", px + sl, px - tp, f"SCALP: px>=BBhi & RSI={r:.1f}")
    return None

# ====== Runner (shared) ======
def run_forever(cfg: Config) -> None:
    ex = Ex(cfg)
    open_pos: Dict[str, Dict[str, object]] = {}
    closed: List[Dict[str, object]] = []
    totals = {"trades": 0, "wins": 0, "losses": 0, "net": 0.0}
    last_summary = time.time()

    # Telegram self-test + start ping
    tg_self_test(cfg)

    while True:
        cycle_start = time.time()
        try:
            syms = ex.top_usdt_perps(cfg.top_n)
            open_count = len(open_pos)
            for sym in syms:
                df = ex.fetch_ohlcv(sym, cfg.timeframe, cfg.lookback)
                last = df.iloc[-1]
                px = float(last['close']); hi=float(last['high']); lo=float(last['low'])

                # 1) Exit logic
                if sym in open_pos:
                    pos = open_pos[sym]
                    side = pos['side']
                    entry=float(pos['price']); sl=float(pos['sl']); tp=float(pos['tp'])
                    outcome=None; exit_price=None
                    if side == "buy":
                        if lo <= sl and hi >= tp: outcome="SL"; exit_price=sl
                        elif hi >= tp: outcome="TP"; exit_price=tp
                        elif lo <= sl: outcome="SL"; exit_price=sl
                    else:
                        if hi >= sl and lo <= tp: outcome="SL"; exit_price=sl
                        elif lo <= tp: outcome="TP"; exit_price=tp
                        elif hi >= sl: outcome="SL"; exit_price=sl

                    if outcome:
                        pnl = (exit_price - entry)/entry*100.0 if side=="buy" else (entry - exit_price)/entry*100.0
                        closed.append({"symbol":sym,"side":side,"entry":entry,"exit":exit_price,
                                       "pnl_pct":pnl,"outcome":outcome,"closed_at":pd.Timestamp.utcnow()})
                        totals["trades"] += 1
                        if pnl >= 0: totals["wins"] += 1
                        else: totals["losses"] += 1
                        totals["net"] += pnl
                        tg_send(cfg, close_msg(cfg, sym, side, entry, exit_price, outcome, pnl))
                        del open_pos[sym]
                        open_count = len(open_pos)
                        continue

                # 2) Skip if already open
                if sym in open_pos:
                    continue

                # 3) New signal
                sig = strat(df)
                if sig is None:
                    continue
                side, sl, tp, reason = sig
                open_pos[sym] = {"side":side,"price":px,"sl":sl,"tp":tp,"reason":reason,"opened_at":pd.Timestamp.utcnow()}
                tg_send(cfg, open_msg(cfg, sym, side, px, sl, tp, reason))
                open_count = len(open_pos)
                time.sleep(0.05)

            # Hourly summary
            if time.time() - last_summary >= 3600:
                last_summary = time.time()
                cutoff = pd.Timestamp.utcnow() - pd.Timedelta(hours=1)
                recent = [t for t in closed if t["closed_at"] >= cutoff]
                r_trades = len(recent)
                r_wins = sum(1 for t in recent if t["pnl_pct"] >= 0)
                r_losses = sum(1 for t in recent if t["pnl_pct"] < 0)
                r_net = sum(t["pnl_pct"] for t in recent)
                wr = (r_wins / r_trades * 100.0) if r_trades else 0.0
                lr = (r_losses / r_trades * 100.0) if r_trades else 0.0
                t_trades = totals["trades"]
                t_wr = (totals["wins"] / t_trades * 100.0) if t_trades else 0.0
                summary = (
                    f"📊 تقرير الساعة — {cfg.strategy} | {cfg.timeframe}\n"
                    f"الساعة الماضية: صفقات مُغلقة: {r_trades} | فوز: {r_wins} ({wr:.1f}%) | خسارة: {r_losses} ({lr:.1f}%) | الصافي: {r_net:+.2f}%\n"
                    f"الإجمالي منذ البدء: صفقات: {t_trades} | نسبة الفوز: {t_wr:.1f}% | الصافي: {totals['net']:+.2f}%"
                )
                tg_send(cfg, summary)

            # Heartbeat to console
            print(f"[HB] scanned={len(syms)} | open_positions={len(open_pos)}")

        except Exception as e:
            print(f"[ERR] loop: {e}")

        time.sleep(max(0.0, cfg.poll_secs - (time.time() - cycle_start)))

if __name__ == "__main__":
    cfg = Config(strategy=STRAT_NAME)
    print(f"[START] {cfg.strategy} | TOP {cfg.top_n} | TF {cfg.timeframe}")
    run_forever(cfg)
