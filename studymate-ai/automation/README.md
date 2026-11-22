# 자동화 워크플로우 (n8n) — 피드백 수집 파이프라인

```
StudyMate 앱 (Python)                         n8n (노코드)
 피드백 제출 ─► DB 저장 ─► Webhook POST ─►  [Webhook] ─► [피드백 정리(Code)] ─┬─► [Google Sheets 행 추가]
                         (실패해도 서비스 영향 없음)                          └─► [Discord 팀 채널 알림]
```

- 앱 쪽 코드: `studymate/analytics.py` → `save_feedback()`(DB 저장, 익명 ID 생성) → `send_webhook()`(5초 타임아웃)
- 외부로는 **닉네임 대신 8자리 익명 ID**만 전송한다.
- 피드백은 DB에 먼저 저장되므로, n8n을 설정하지 않아도 관리자 화면에서 CSV로 받을 수 있다. n8n은 "실시간 팀 알림 + 공유 시트"를 위한 확장이다.

## 설정 순서

### 1. 준비
1. **Google Sheets**: 새 스프레드시트를 만들고 시트(탭) 이름을 `feedback`으로 바꾼다. 1행(머리글)에 아래 값을 한 칸씩 입력한다.
   `received_at | created_at | app_version | tester_id | tester_group | satisfaction | usefulness | ease | recommend | liked | improve`
2. **Discord**: 팀 서버 → 알림 받을 채널의 ⚙️ 채널 편집 → 연동(Integrations) → 웹후크 → 새 웹후크 → **웹후크 URL 복사**
   (Slack을 쓰면 Slack의 Incoming Webhook 주소를 써도 같은 방식으로 동작한다. 메시지 키가 `content` 대신 `text`인 점만 바꾼다.)

### 2. n8n 준비
- 가장 쉬운 방법: n8n Cloud 무료 체험 가입 (체험 기간이 끝나기 전에 테스트를 마칠 것)
- 또는 자체 실행: Node.js가 있으면 `npx n8n` → 브라우저에서 `http://localhost:5678`
  (자체 실행 시 배포된 앱이 접근하려면 외부에서 접속 가능한 주소가 필요하므로, 사용자 테스트 기간에는 Cloud를 권장)

### 3. 워크플로우 가져오기
1. n8n 화면 → **Workflows → Add workflow(새 워크플로우)** → 오른쪽 위 `⋯` 메뉴 → **Import from File**
2. 이 폴더의 `n8n_feedback_workflow.json` 선택
3. **Google Sheets - 기록** 노드를 더블클릭
   - Credential: `Create New` → Google 계정으로 로그인(OAuth) 연결
   - Document: 1번에서 만든 시트 URL 붙여넣기, Sheet: `feedback`
4. **Discord - 팀 알림** 노드를 더블클릭 → URL 칸에 1번의 Discord 웹후크 URL 붙여넣기
5. 저장(Save)

> n8n 버전에 따라 가져온 노드에 경고 표시가 생기면, 노드를 열어 위 값이 들어가 있는지만 확인하고 다시 저장하면 된다.
> 가져오기가 안 되면 같은 구성을 직접 만든다: `Webhook`(POST, Path `studymate-feedback`) → `Code`(JSON 파일 안의 jsCode 복사) → `Google Sheets`(Append Row, 자동 매핑) + `HTTP Request`(POST, JSON 본문).

### 4. 테스트
1. **Webhook - 피드백 수신** 노드를 열고 **Test URL** 복사 → 화면의 `Listen for test event` 클릭
2. 터미널에서 테스트 요청 보내기:
   ```bash
   curl -X POST "<Test URL>" -H "Content-Type: application/json" \
     -d '{"type":"feedback","app_version":"1.0.0","tester_id":"test0001","tester_group":"대학생","satisfaction":5,"usefulness":4,"ease":5,"recommend":9,"liked":"테스트","improve":"테스트"}'
   ```
3. 시트에 행이 추가되고 Discord에 알림이 오면 성공

### 5. 운영 연결
1. 워크플로우 오른쪽 위 스위치를 **Active**로 켠다.
2. Webhook 노드의 **Production URL**을 복사한다. (Test URL과 다르다)
3. Streamlit Cloud → 앱 → Settings → Secrets 에 추가: `N8N_WEBHOOK_URL = "<Production URL>"` → 저장(앱 자동 재시작)
4. 배포된 앱에서 피드백을 하나 제출해 시트·Discord에 들어오는지 확인한다.

## 발표에서 설명할 포인트
- **역할 분리**: 앱(Python)은 "이벤트를 보낸다"까지만, 저장·알림 로직은 n8n(노코드)에서 코드 수정 없이 바꿀 수 있다.
- **장애 격리**: Webhook 실패는 무시되고 피드백은 DB에 이미 저장되어 있다.
- **개인정보**: 외부 도구에는 익명 ID와 점수·의견만 전달한다.
