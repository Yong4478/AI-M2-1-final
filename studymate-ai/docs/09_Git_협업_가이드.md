# 09. Git 협업 가이드

> 미션 요건: "Git을 활용하여 체계적으로 버전 관리", "GitHub를 통한 버전 관리 기록이 존재", "모든 팀원이 실질적으로 기여".
> → **모든 작업을 이슈 → 브랜치 → PR → 리뷰 → merge로 진행**하면 기록이 저절로 남습니다.

## 1. 브랜치 전략 (GitHub Flow)

| 브랜치 | 용도 | 규칙 |
|---|---|---|
| `main` | 배포 브랜치 (push 시 Streamlit Cloud 자동 재배포) | 직접 push 금지, PR + CI 통과 + 리뷰 1명 |
| `feat/<기능>` | 기능 개발 | 예: `feat/quiz-difficulty` |
| `fix/<내용>` | 버그 수정 | 예: `fix/pdf-empty-page` |
| `feedback/<내용>` | 사용자 피드백 반영 | 예: `feedback/answer-length` |
| `docs/<내용>` | 문서 | 예: `docs/test-result` |

## 2. 커밋 메시지 규칙

```
<타입>: <무엇을 했는지 한 줄> (#이슈번호)
```

| 타입 | 의미 | 예 |
|---|---|---|
| `feat` | 새 기능 | `feat: 약점 집중 퀴즈 옵션 추가 (#8)` |
| `fix` | 버그 수정 | `fix: 빈 페이지 PDF 업로드 오류 수정 (#15)` |
| `docs` | 문서 | `docs: 1차 사용자 테스트 결과 정리` |
| `test` | 테스트 | `test: Agent 최대 단계 테스트 추가` |
| `refactor` | 동작 변화 없는 구조 개선 | `refactor: 검색 결과 포맷 함수 분리` |
| `chore` | 설정·빌드 | `chore: Python 3.12 CI 설정` |
| `prompt` | 프롬프트 변경 (AI 프로젝트 특화) | `prompt: 답변 길이 원칙 수정 (#12 피드백)` |

- 피드백 반영 커밋에는 **근거를 함께** 적는다: `(#12 테스터 4명 "답변이 길다")` → 발표에서 개선 과정의 증거가 된다.

## 3. 작업 흐름

```bash
git switch main && git pull                 # 1. 최신 main
git switch -c feat/quiz-difficulty          # 2. 브랜치 생성
# ... 작업 ...
pytest -q                                   # 3. 테스트
git add . && git commit -m "feat: 퀴즈 난이도 3단계 추가 (#7)"
git push -u origin feat/quiz-difficulty     # 4. push → GitHub에서 PR 생성 (템플릿 자동 적용)
# 5. CI 통과 + 팀원 1명 리뷰 승인 → Squash and merge → 브랜치 삭제
```

## 4. 이슈·마일스톤 구성 (예시)

| 마일스톤 | 이슈 (담당) |
|---|---|
| **M0 기획** (1주차) | 문제 정의·인터뷰 (PM) · 요구사항 명세 (PM) · AI 설계서 (AI) · 기술스택 ADR (DevOps) · 화면 설계 (Frontend) |
| **M1 MVP v1.0.0** (2주차) | DB 계층·스키마 (DevOps) · 자료 추출·청크 (RAG) · 임베딩·검색 (RAG) · Agent 루프·도구 (AI) · 장기 기억 (AI) · 퀴즈 생성·채점 (RAG) · 채팅·자료 화면 (Frontend) · 퀴즈·기록 화면 (Frontend) · 피드백·관리자 (DevOps) · 테스트·CI (DevOps) · 배포 (DevOps) |
| **M2 1차 테스트** (3주차) | 테스터 모집·진행 (PM) · 결과 분석 (PM) · 피드백 반영 이슈 N개 (전원) |
| **M3 v1.1.0·발표** (4주차) | 2차 테스트 (PM) · RAG 평가 수치 (RAG) · 발표 자료 (PM) · 시연 영상 (Frontend) · README 정리 (전원) |

## 5. 기여 기록을 남기는 팁
- 각자 **자기 계정으로** 커밋·PR (다른 사람 컴퓨터에서 대신 커밋하지 않기)
- 처음 받은 코드를 한 번에 올린 뒤에는, 테스트로 발견한 개선·피드백 반영을 **작은 PR 여러 개**로 나눈다
- 리뷰 코멘트를 남기는 것도 기여 기록이다 (모든 PR에 최소 1개 리뷰)
- 회의록·결정은 `docs/07_개발_진행_기록.md`에 커밋
- 확인: 저장소 **Insights → Contributors**에서 팀원별 기여 그래프

## 6. 버전·태그
| 태그 | 시점 | 내용 |
|---|---|---|
| `v1.0.0` | MVP 배포 | 1차 사용자 테스트 버전 |
| `v1.1.0` | 1차 피드백 반영 | 2차 테스트 버전 |
| `v1.2.0` | (선택) 2차 피드백 반영 | 발표 버전 |

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## 7. 절대 커밋하면 안 되는 것
- `.env`, `.streamlit/secrets.toml`, API 키(`sk-...`), DB 비밀번호, `data/` 폴더의 DB 파일
- 실수로 올렸다면: **즉시 OpenAI에서 해당 키 삭제(Revoke) 후 새 키 발급**, Supabase 비밀번호 재설정. (커밋을 지워도 기록에 남을 수 있으므로 키 교체가 먼저)
