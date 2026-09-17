# FANUC 매뉴얼 챗봇

포트 **8787 하나**만 사용합니다. 화면과 API를 같은 서버가 제공합니다. DB는 SQLite 파일이라 포트가 없습니다.

## 쓰지 않는 포트

8005, 8010, 8011, 8020, 8080, 5173, 3306, 3308, 11434

이 값으로 실행하면 서버가 바로 종료됩니다.

## 구조

```text
frontend/          화면 (HTML/CSS/JS)
backend/server.py API + 정적 파일 서빙
data/manuals/      FANUC 매뉴얼 텍스트
data/chatbot.db    대화 저장 (실행 시 생성)
```

## 실행

```bash
python3 -m pip install -r requirements.txt
python3 backend/server.py
```

가상환경이 안 되면 프로젝트 폴더에 패키지를 설치하고 실행합니다.

```bash
python3 -m pip install --target .pip -r requirements.txt
PYTHONPATH=.pip python3 backend/server.py
```

브라우저에서 http://127.0.0.1:8787 을 엽니다.

채팅은 `POST /api/chat` 으로 처리합니다.
