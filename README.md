# PulseAI Lite (WebRTC Hub Sample)

이 프로젝트는 편의점 POS 단말의 실시간 모니터링 및 이상 탐지를 위한 WebRTC 기반 데이터 허브입니다.

<img width="1536" height="1024" alt="Architecture" src="https://github.com/user-attachments/assets/3c0b8891-35f9-4b33-9359-4445576f56dd" />

파이썬(Python) 기반의 핵심 허브를 중심으로 여러 클라이언트들이 **WebRTC DataChannel**을 통해 데이터를 주고받습니다.
- **React Client** ↔ **Python Hub** (대시보드 실시간 모니터링)
- **.NET (C#) Agent** ↔ **Python Hub** (실제 POS 단말 데이터 전송)

---

## 🚀 시작하기 전에

본 프로젝트는 두 가지 모드를 지원합니다.
1. **Sample 모드**: 실제 POS 환경이 없어도 제공된 `sample/data_pos.txt` (JSON 로그 데이터)를 활용해 시스템을 구동해보고 차트 등을 테스트해 볼 수 있는 모드입니다. 개발 및 테스팅 목적으로 주로 사용됩니다.
2. **Live 모드**: C# 클라이언트를 실행하여 직접 실시간으로 POS 시스템 환경과 연동하는 프로덕션 모드입니다.

---

## 🛠 실행 가이드

### 1) Python 허브 서버 및 샘플 데이터 사용법

가장 먼저 중앙 통신을 담당하는 파이썬 서버를 실행해야 합니다. 패키지 관리는 `uv`를 사용합니다.

```bash
cd server
uv venv
uv sync
```

**[Sample 모드 실행 (테스트/개발용)]**
저장소에 포함된 약 6.8MB 크기의 샘플 데이터(`sample/data_pos.txt`)를 읽어와 가상의 POS 데이터를 스트리밍합니다.
```bash
uv run webrtc-hub --mode sample --file ../sample/data_pos.txt --speed 1.0 --loop
```
* **옵션 설명**
  * `--speed 1.0` : 실시간 속도로 데이터 전송 (2.0을 입력하면 2배속, 0을 입력하면 최대 속도 배속)
  * `--loop` : 파일의 끝에 도달하면 처음부터 데이터를 무한 반복해서 보냄

**[Live 모드 실행 (운영용)]**
WebRTC를 통해 실제 클라이언트(C# .NET 애플리케이션 등)로부터 실시간 데이터를 수신하려면 모드를 변경하여 실행합니다.
```bash
uv run webrtc-hub --mode live
```

### 2) React 클라이언트 대시보드 (Web)

수신된 데이터를 시각화하고 제어하는 화면입니다.
Vite 기반의 React 환경으로 구성되어 있습니다.

```bash
cd client
npm install
npm run dev
```
명령어 실행 후 브라우저에서 `http://localhost:5173` 에 접속하여 모니터링 화면을 확인할 수 있습니다.

### 3) C# 클라이언트 에이전트 (Live 모드용)

실전 POS 역할을 수행하거나 C# 관련 개발을 테스트할 때 사용합니다. Python 서버가 반드시 **Live 모드**로 실행 중이어야 합니다.

```bash
cd webrtc_csharp_client
dotnet restore
dotnet run -- http://127.0.0.1:8080 client1 csharp
```

---

## 🧪 브라우저 간 간단 통신 테스트 방법

Sample 모드 데이터 연동 수신과는 별개로 브라우저 탭들 사이의 WebRTC DataChannel 송수신 여부를 직접 확인하려면 다음 절차를 사용할 수 있습니다.

1. 웹 브라우저에서 `http://localhost:5173` 탭을 **두 개** 엽니다.
2. 각각 다른 `client_id` (예시: `user-1`, `user-2`)값을 입력하여 서버에 **Connect** 합니다.
3. 양쪽 탭 모두 연결 성공 후,
   - **Send to** 기능을 통해 특정 대상에게 메세지를 전송
   - 혹은 **Broadcast room** 기능을 통해 현재 Room 내 전체 클라이언트 대상 메시지 발송
4. 메세지 전송 후 상대 탭의 로그 화면을 통해 데이터가 정상 도달하는지 확인합니다.
5. 파이썬 서버의 연결 상세 정보는 브라우저에서 `http://localhost:8080/who` 주소를 통해 접속된 클라이언트 JSON 스냅샷으로 확인할 수 있습니다.

---

## 🔗 Hub Server API 엔드포인트

- `POST /offer?client_id=...&role=...` : WebRTC SDP 연결(Offer) 수신 처리 및 DataChannel 생성 연결고리 역할
- `GET /who` : 현재 서버에 정상적으로 연결된 클라이언트 정보 확인 목록 포트
- `GET /health` : 서버 상태 정상 여부 확인 (health-check)
