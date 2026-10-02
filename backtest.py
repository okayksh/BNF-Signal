"""
간이 백테스트: bnf_signal.py와 같은 규칙을 과거 데이터에 적용
사용법: python backtest.py KR 5y   /   python backtest.py US 5y
(국면은 단순화를 위해 각 시점의 지수 200일선 기준으로 판단, 수수료·슬리피지 미반영)
"""
import sys
import pandas as pd
import bnf_signal as b

market = (sys.argv[1] if len(sys.argv) > 1 else "US").upper()
period = sys.argv[2] if len(sys.argv) > 2 else "5y"
R, CFG = b.R, b.CFG
uni = b.kr_universe() if market == "KR" else b.us_universe()
idx_t = CFG[market]["regime_index"]
data = b.download(list(uni) + [idx_t], period=period)
ic = data[idx_t]["Close"]
bull = ic >= ic.rolling(R["regime_ma_len"]).mean()

trades = []
lb = R["lookback_days"]
for t, info in uni.items():
    if t not in data:
        continue
    ind = b.indicators(data[t])
    reg = bull.reindex(ind.index).ffill().fillna(True)
    th = reg.map(lambda x: CFG["thresholds"][info["group"]]["bull" if x else "bear"])
    dev_ok = (ind["dev"] - th).rolling(lb).min() <= 0
    rsi_ok = ind["rsi"].rolling(lb).min() <= R["rsi_oversold"]
    cross = (ind["hist"].shift(1) <= 0) & (ind["hist"] > 0)
    sig = dev_ok & rsi_ok & cross
    busy_until = -1
    for i in [k for k, v in enumerate(sig.values) if v]:
        if i <= busy_until or i < 220:
            continue
        entry = ind["close"].iloc[i]
        stop = ind["low"].iloc[max(0, i - R["stop_lookback_days"] + 1):i + 1].min()
        risk = max(entry - stop, entry * 0.01)
        t2r = entry + 2 * risk
        res, px, j = "만료", None, i
        for j in range(i + 1, min(i + 1 + R["max_hold_days"], len(ind))):
            r = ind.iloc[j]
            if r["low"] <= stop:
                res, px = "손절", stop; break
            if r["high"] >= r["ema"]:
                res, px = "25EMA", r["ema"]; break
            if r["high"] >= t2r:
                res, px = "2R", t2r; break
        if px is None:
            px = ind["close"].iloc[j]
        busy_until = j
        trades.append({"ticker": t, "name": info["name"], "date": ind.index[i].date(),
                       "result": res, "ret": (px / entry - 1) * 100, "days": j - i})

df = pd.DataFrame(trades)
if df.empty:
    print("신호 없음")
else:
    df = df.sort_values("date")
    print(df.tail(25).to_string(index=False))
    print(f"\n[{market} {period}] 거래 {len(df)}건 | 승률 {(df.ret > 0).mean()*100:.1f}% | "
          f"평균 {df.ret.mean():+.2f}% | 중앙값 {df.ret.median():+.2f}% | 평균보유 {df.days.mean():.1f}일")
    print(df.groupby("result")["ret"].agg(["count", "mean"]).round(2))
