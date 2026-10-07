# 📈 주식 포트폴리오 실시간 비중 감시 & 텔레그램 알림 봇

컴퓨터를 켜두지 않아도 **GitHub Actions**가 무료 클라우드 서버에서 매일 파이썬 스크립트를 실행하여, **핸드폰 텔레그램(Telegram)**으로 실시간 주가, 포트폴리오 비중, 리밸런싱 알림을 전송해 줍니다.

---

## 📁 구성 파일

1. **`portfolio_config.json`**: 보유 종목, 보유 수량, 목표 비중(%) 설정 파일
2. **`portfolio_checker.py`**: 한국/미국 주식 실시간 시세 조회 및 비중 계산, 텔레그램 알림 스크립트
3. **`.github/workflows/check_portfolio.yml`**: 컴퓨터 없이 서버에서 자동 실행되도록 하는 깃허브 액션 설정 파일

---

## 🚀 5분 세팅 가이드 (초보자용 클릭 단계)

### 1단계: 텔레그램 봇 만들기 & ID 얻기 (3분)

1. **텔레그램 앱**에서 검색창에 `@BotFather` 입력 후 선택
2. `/newbot` 입력 후 봇 이름 및 봇 아이디 입력 (예: `my_stock_bot`)
3. 출력되는 `HTTP API Token` 복사 
   *(예: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`)*
4. 검색창에 `@userinfobot` 입력 후 `/start` 누르면 나오는 **Id 번호** 복사
   *(예: `12345678`)*
5. 생성한 봇에게 메시지 아무거나 하나 보내놓기 (첫 알림 수신 준비)

---

### 2단계: 깃허브(GitHub) 웹사이트에 올리기 (1분)

1. [GitHub.com](https://github.com) 로그인
2. 우측 상단 **`+`** 버튼 클릭 -> **`New repository`** 클릭
3. Repository name에 `my-stock-portfolio` 입력 -> **`Create repository`** 클릭
4. 화면 중앙의 **`uploading an existing file`** 링크 클릭
5. 내 바탕화면의 `주식` 폴더에 있는 파일들을 드래그해서 모두 업로드:
   - `portfolio_config.json`
   - `portfolio_checker.py`
   - `.github/workflows/check_portfolio.yml` (폴더째 업로드)
6. 하단 녹색 **`Commit changes`** 버튼 클릭

---

### 3단계: 깃허브에 텔레그램 토큰 등록하기 (1분)

1. 깃허브 저장소 상단 메뉴에서 **`Settings`** 클릭
2. 좌측 메뉴에서 **`Secrets and variables`** -> **`Actions`** 클릭
3. **`New repository secret`** 버튼 클릭
   - Name: `TELEGRAM_TOKEN`
   - Secret: 1단계에서 복사한 봇 토큰 입력
   - `Add secret` 클릭
4. **`New repository secret`** 버튼 한 번 더 클릭
   - Name: `TELEGRAM_CHAT_ID`
   - Secret: 1단계에서 복사한 Id 번호 입력
   - `Add secret` 클릭

---

## ✅ 모바일/웹에서 테스트 실행하는 법

1. 깃허브 저장소 상단 **`Actions`** 탭 클릭
2. 좌측에서 **`주식 포트폴리오 비중 실시간 감시`** 클릭
3. 우측의 **`Run workflow`** -> **`Run workflow`** 버튼 클릭!
4. 10~20초 후 핸드폰 텔레그램으로 포트폴리오 실시간 알림이 도착합니다! 📱

이제 매일 장 마감 시간마다 컴퓨터가 꺼져 있어도 텔레그램으로 알림이 자동 전송됩니다.
