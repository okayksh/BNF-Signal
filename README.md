# BNF-Signal · BNF 매매법 자동 알림

BNF(코테가와 타카시)의 **25일선 이격도 역추세 매매법**을 한국·미국 주식에 매일 적용해
텔레그램으로 알림을 보내고, 대시보드(GitHub Pages)에 현황을 표시합니다.

- 대시보드: https://okayksh.github.io/BNF-Signal/
- 매매법 정리 노트: [BNF-trading-notes](https://github.com/okayksh/BNF-trading-notes) (비공개)

## 신호 규칙

| 단계 | 조건 |
|---|---|
| 🔵 관찰 | 종가가 25EMA 대비 기준 이격도 이하 |
| 🟠 대기 | 최근 10일 안에 이격도 기준 충족 + RSI(14) ≤ 30 |
| 🟢 **매수** | 위 조건 + **오늘 MACD 히스토그램이 0선 아래 → 위로 전환** |

이격도 기준(25EMA 대비): 대형주·일반 ETF **상승장 −20% / 하락장 −25%**, 코스닥·레버리지 ETF·고변동 **−30% / −35%**
(상승장/하락장 = 코스피 지수 / QQQ가 200일선 위/아래)

**청산 추적** (매수 신호 이후 자동 추적): 손절 = 최근 10일 최저가 · 익절① = 25EMA 도달 · 익절② = 손익비 2R · 최대 20거래일

## 감시 종목
- 🇰🇷 코스피 시가총액 상위 200 + 코스닥 상위 150
- 🇺🇸 나스닥100 + 주요 ETF(QQQ, SPY, SMH…) + 레버리지/고변동(TQQQ, QLD, SOXL, MSTR, COIN…)
- `config.json`에서 종목·기준값 수정 가능

## 실행 시각 (GitHub Actions, 평일)
- 한국: 16:40 KST (장 마감 후)
- 미국: 06:40 KST (미국장 마감 후)
- 수동 실행: Actions → BNF Signal → Run workflow

## 텔레그램 연결 (1회)
1. 텔레그램에서 **@BotFather** → `/newbot` → 봇 이름 정하기 → **토큰** 복사
2. 만든 봇에게 아무 메시지나 보내기
3. 브라우저로 `https://api.telegram.org/bot<토큰>/getUpdates` 열기 → `"chat":{"id": 숫자` 의 숫자가 **채팅 ID**
4. 이 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - `TELEGRAM_TOKEN` = 봇 토큰
   - `TELEGRAM_CHAT_ID` = 채팅 ID

## 간이 백테스트 (참고)
`python backtest.py US 5y` / `python backtest.py KR 5y` — 현재 구성종목 기준(생존편향 있음), 수수료 미반영.
2021~2026 기준 결과: 미국 51건 승률 63%·평균 +3.3%, 한국 105건 승률 56%·평균 +1.1% (평균 보유 5~7일).

> ⚠️ 학습·참고용 신호입니다. 투자 판단과 책임은 본인에게 있습니다.
