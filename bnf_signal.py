"""
BNF 25일선 이격도 역추세 신호 알림 시스템
- 단계1 관찰: 종가가 25EMA 대비 기준 이격도 이하
- 단계2 대기: + 최근 RSI(14) 과매도(<=30)
- 매수 신호: 최근 N일 내 이격도·RSI 조건 충족 + 오늘 MACD 히스토그램이 0선 아래→위 전환
- 청산 추적: 손절(직전 저점) / 익절(25EMA 도달) / 2R 도달 / 보유기간 만료
사용법: python bnf_signal.py KR   또는   python bnf_signal.py US
"""
import json
import os
import sys
import time
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
STATE_DIR = ROOT / "state"
DATA_DIR = ROOT / "docs" / "data"
STATE_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
R = CFG["rules"]
KST = dt.timezone(dt.timedelta(hours=9))


# ---------------------------------------------------------------- 유니버스
def kr_universe():
    """코스피 시총 상위 N(large) + 코스닥 시총 상위 N(volatile). 실패 시 캐시 사용."""
    cache = STATE_DIR / "kr_universe.json"
    try:
        import FinanceDataReader as fdr
        out = {}
        for mkt, n, grp, sfx in (("KOSPI", CFG["KR"]["kospi_top_n"], "large", ".KS"),
                                 ("KOSDAQ", CFG["KR"]["kosdaq_top_n"], "volatile", ".KQ")):
            df = fdr.StockListing(mkt)
            df = df[df["Code"].astype(str).str.fullmatch(r"\d{6}")]
            df = df.sort_values("Marcap", ascending=False).head(n)
            for _, r in df.iterrows():
                out[r["Code"] + sfx] = {"name": r["Name"], "group": grp}
        for t, v in CFG["KR"].get("extra", {}).items():
            out[t] = v
        if len(out) > 50:
            cache.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
            return out
    except Exception as e:  # noqa
        print("KR 종목목록 조회 실패, 캐시 사용:", e)
    return json.loads(cache.read_text(encoding="utf-8"))


def us_universe():
    u = CFG["US"]
    out = {}
    for t in u["nasdaq100"] + u["large"]:
        out[t] = {"name": t, "group": "large"}
    for t in u["volatile"]:
        out[t] = {"name": t, "group": "volatile"}
    return out


# ---------------------------------------------------------------- 지표
def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def indicators(df):
    c = df["Close"]
    ema = c.ewm(span=R["ema_len"], adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    out = pd.DataFrame({
        "close": c, "high": df["High"], "low": df["Low"], "volume": df["Volume"],
        "ema": ema, "dev": (c / ema - 1) * 100, "rsi": rsi(c, R["rsi_len"]), "hist": hist,
    })
    out["vol_ratio"] = out["volume"] / out["volume"].rolling(20).mean()
    return out.dropna(subset=["close", "ema"])


def download(tickers, period="2y"):
    frames = {}
    tickers = list(tickers)
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        for attempt in range(3):
            try:
                d = yf.download(chunk, period=period, auto_adjust=True, progress=False,
                                group_by="ticker", threads=True)
                break
            except Exception as e:  # noqa
                print("다운로드 재시도", attempt, e)
                time.sleep(5)
        else:
            continue
        for t in chunk:
            try:
                sub = d[t] if len(chunk) > 1 else d
                sub = sub.dropna(subset=["Close"])
                if len(sub) > 60:
                    frames[t] = sub
            except Exception:  # noqa
                pass
    return frames


# ---------------------------------------------------------------- 텔레그램
def telegram(text):
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[텔레그램 미설정] 메시지:\n" + text)
        return False
    for part in [text[i:i + 3800] for i in range(0, len(text), 3800)]:
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data={"chat_id": chat, "text": part, "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"}, timeout=20)
            if not r.ok:
                print("텔레그램 전송 실패:", r.status_code, r.text[:200])
                return False
        except Exception as e:  # noqa
            print("텔레그램 전송 실패:", e)
            return False
    return True


# ---------------------------------------------------------------- 메인
def fmt_px(x, market):
    return f"{x:,.0f}" if market == "KR" else f"{x:,.2f}"


def run(market):
    universe = kr_universe() if market == "KR" else us_universe()
    idx_t = CFG[market]["regime_index"]
    print(f"[{market}] 종목 {len(universe)}개 다운로드")
    data = download(list(universe) + [idx_t])

    # 시장 국면
    regime = "bull"
    idx_info = {}
    if idx_t in data:
        ic = data[idx_t]["Close"]
        ma = ic.rolling(R["regime_ma_len"]).mean()
        regime = "bull" if ic.iloc[-1] >= ma.iloc[-1] else "bear"
        idx_info = {"ticker": idx_t, "close": round(float(ic.iloc[-1]), 2),
                    "ma": round(float(ma.iloc[-1]), 2)}

    pos_file = STATE_DIR / f"positions_{market}.json"
    hist_file = STATE_DIR / f"history_{market}.json"
    meta_file = STATE_DIR / f"meta_{market}.json"
    positions = json.loads(pos_file.read_text("utf-8")) if pos_file.exists() else []
    history = json.loads(hist_file.read_text("utf-8")) if hist_file.exists() else []
    meta = json.loads(meta_file.read_text("utf-8")) if meta_file.exists() else {}

    rows, buys = [], []
    asof = None
    lb = R["lookback_days"]
    for t, info in universe.items():
        if t not in data:
            continue
        ind = indicators(data[t])
        if len(ind) < 40:
            continue
        last = ind.iloc[-1]
        d_last = ind.index[-1].date()
        asof = max(asof, d_last) if asof else d_last
        th = CFG["thresholds"][info["group"]][regime]
        win = ind.iloc[-lb:]
        dev_ok_now = last["dev"] <= th
        dev_ok_recent = win["dev"].min() <= th
        rsi_ok_recent = win["rsi"].min() <= R["rsi_oversold"]
        cross = ind["hist"].iloc[-2] <= 0 < ind["hist"].iloc[-1]

        stage = 0
        if dev_ok_now:
            stage = 1
        if dev_ok_recent and rsi_ok_recent:
            stage = max(stage, 2)
        if dev_ok_recent and rsi_ok_recent and cross:
            stage = 3

        row = {
            "ticker": t, "name": info["name"], "group": info["group"], "th": th,
            "close": round(float(last["close"]), 4), "ema": round(float(last["ema"]), 4),
            "dev": round(float(last["dev"]), 2), "dev_min": round(float(win["dev"].min()), 2),
            "rsi": round(float(last["rsi"]), 1), "rsi_min": round(float(win["rsi"].min()), 1),
            "hist": round(float(last["hist"]), 4), "hist_prev": round(float(ind["hist"].iloc[-2]), 4),
            "vol_ratio": round(float(last["vol_ratio"]), 2) if pd.notna(last["vol_ratio"]) else None,
            "stage": stage, "date": str(d_last),
        }
        if stage == 3:
            stop = float(ind["low"].iloc[-R["stop_lookback_days"]:].min())
            entry = float(last["close"])
            risk = max(entry - stop, entry * 0.01)
            row.update({"stop": round(stop, 4), "target_ema": round(float(last["ema"]), 4),
                        "target_2r": round(entry + 2 * risk, 4)})
            buys.append(row)
        rows.append(row)

    if asof is None:
        print("데이터 없음")
        return
    asof_s = str(asof)
    already = meta.get("last_alert_date") == asof_s
    msgs = []

    # 보유(가상) 포지션 청산 체크
    still_open = []
    for p in positions:
        t = p["ticker"]
        if t not in data or p.get("entry_date") == asof_s:
            still_open.append(p)
            continue
        ind = indicators(data[t])
        after = ind[ind.index.date > dt.date.fromisoformat(p["last_checked"])]
        closed = None
        for d_i, r in after.iterrows():
            p["days"] = p.get("days", 0) + 1
            if r["low"] <= p["stop"]:
                closed = ("🔴 손절", p["stop"], d_i)
            elif r["high"] >= r["ema"]:
                closed = ("🟢 익절(25EMA 도달)", float(r["ema"]), d_i)
            elif r["high"] >= p["target_2r"]:
                closed = ("🟢 익절(2R 도달)", p["target_2r"], d_i)
            elif p["days"] >= R["max_hold_days"]:
                closed = ("⏱ 기간만료", float(r["close"]), d_i)
            if closed:
                break
        p["last_checked"] = asof_s
        p["last_close"] = round(float(ind["close"].iloc[-1]), 4)
        if closed:
            label, px, d_i = closed
            ret = (px / p["entry"] - 1) * 100
            p.update({"exit": round(px, 4), "exit_date": str(d_i.date()), "result": label,
                      "ret": round(ret, 2)})
            history.insert(0, p)
            msgs.append(f"{label} <b>{p['name']}</b> ({t})\n"
                        f"  진입 {fmt_px(p['entry'], market)} → {fmt_px(px, market)} ({ret:+.1f}%)")
        else:
            p["ret_now"] = round((p["last_close"] / p["entry"] - 1) * 100, 2)
            still_open.append(p)
    positions = still_open

    # 신규 매수 신호
    open_t = {p["ticker"] for p in positions}
    for b in buys:
        if b["ticker"] in open_t or already:
            continue
        positions.append({"ticker": b["ticker"], "name": b["name"], "entry": b["close"],
                          "entry_date": asof_s, "last_checked": asof_s, "stop": b["stop"],
                          "target_ema": b["target_ema"], "target_2r": b["target_2r"], "days": 0})
        msgs.insert(0,
            f"🟢 <b>매수 신호</b> <b>{b['name']}</b> ({b['ticker']})\n"
            f"  종가 {fmt_px(b['close'], market)} | 이격 최저 {b['dev_min']}% (기준 {b['th']}%)\n"
            f"  RSI 최저 {b['rsi_min']} | MACD히스토 {b['hist_prev']:+.3g}→{b['hist']:+.3g}\n"
            f"  손절 {fmt_px(b['stop'], market)} | 익절① 25EMA {fmt_px(b['target_ema'], market)}"
            f" | 익절② 2R {fmt_px(b['target_2r'], market)}")

    watch = sorted([r for r in rows if r["stage"] == 2], key=lambda r: r["dev_min"])
    flag = "🇰🇷 한국" if market == "KR" else "🇺🇸 미국"
    reg_txt = "상승장(지수>200일선)" if regime == "bull" else "하락장(지수<200일선)"

    if not already:
        head = f"<b>BNF 신호 {flag} · {asof_s}</b>\n시장: {reg_txt}\n"
        if msgs:
            body = "\n\n".join(msgs)
        else:
            body = "오늘은 매수/청산 신호가 없습니다."
        if watch:
            body += "\n\n🟠 <b>대기 종목</b> (이격·RSI 충족, MACD 전환 대기)\n" + "\n".join(
                f"  · {w['name']} ({w['ticker']}) 이격 {w['dev']}% RSI {w['rsi']}" for w in watch[:15])
        if positions:
            body += f"\n\n📌 추적 중 {len(positions)}건"
        if telegram(head + "\n" + body):
            meta["last_alert_date"] = asof_s

    # 저장
    rows.sort(key=lambda r: r["dev"])
    out = {"market": market, "asof": asof_s, "regime": regime, "index": idx_info,
           "updated": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
           "thresholds": {g: v[regime] for g, v in CFG["thresholds"].items() if not g.startswith("_")},
           "buys": buys, "watch": watch, "positions": positions, "history": history[:100],
           "candidates": [r for r in rows if r["stage"] >= 1 or r["dev"] <= -10][:80],
           "count": len(rows)}
    (DATA_DIR / f"{market.lower()}.json").write_text(json.dumps(out, ensure_ascii=False), "utf-8")
    pos_file.write_text(json.dumps(positions, ensure_ascii=False, indent=1), "utf-8")
    hist_file.write_text(json.dumps(history[:300], ensure_ascii=False, indent=1), "utf-8")
    meta_file.write_text(json.dumps(meta), "utf-8")
    print(f"[{market}] 완료 asof={asof_s} 매수={len(buys)} 대기={len(watch)} 추적={len(positions)}")


if __name__ == "__main__":
    for m in (sys.argv[1:] or ["KR", "US"]):
        run(m.upper())
