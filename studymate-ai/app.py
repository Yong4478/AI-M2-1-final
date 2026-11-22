"""StudyMate AI — Streamlit 앱 진입점.

로컬 실행:  streamlit run app.py
"""
from __future__ import annotations

import hmac
from pathlib import Path

import pandas as pd
import streamlit as st

from studymate import APP_NAME, APP_VERSION, analytics, memory, rag
from studymate import quiz as quiz_mod
from studymate.agent import StudyAgent
from studymate.auth import AuthError, User, login, register
from studymate.config import load_settings
from studymate.db import Database, new_id, to_kst_display
from studymate.documents import DocumentError, mask_pii
from studymate.llm import LLMClient, LLMError
from studymate.texts import AI_NOTICE_LONG, AI_NOTICE_SHORT, CONSENT_TEXT, SUGGESTED_PROMPTS, TOOL_LABELS

st.set_page_config(page_title=APP_NAME, page_icon="📚", layout="wide")

ROOT = Path(__file__).parent
SAMPLE_FILE = ROOT / "sample_data" / "sample_ai_basics.md"
SAMPLE_NAME = "[샘플] AI 기초 개념 노트.md"

PAGE_CHAT = "💬 AI 튜터"
PAGE_DOCS = "📚 내 자료"
PAGE_QUIZ = "📝 퀴즈"
PAGE_MEMORY = "🧠 학습 기록"
PAGE_FEEDBACK = "🗣️ 피드백"
PAGE_ADMIN = "🔧 관리자"


# ---------------------------------------------------------------- 공통 리소스
@st.cache_resource
def get_services() -> tuple:
    settings = load_settings()
    db = Database(settings.database_url)
    db.init_schema()
    return settings, db, LLMClient(settings)


try:
    settings, db, llm = get_services()
except Exception as exc:  # DB 연결 실패 등
    st.error(f"서비스 초기화에 실패했어요. DATABASE_URL 설정을 확인해 주세요. ({exc.__class__.__name__})")
    st.stop()

ss = st.session_state
for key, default in {
    "user": None,
    "chat": None,
    "quiz": None,
    "is_admin": False,
    "login_failures": 0,
    "pending_prompt": None,
    "flash": [],
}.items():
    if key not in ss:
        ss[key] = default


def goto(page: str) -> None:
    ss.page = page


def flash(message: str) -> None:
    ss.flash = [*ss.flash, message]


def ask_tutor(prompt: str) -> None:
    ss.pending_prompt = prompt
    ss.page = PAGE_CHAT


def on_login(user: User, kind: str) -> None:
    ss.user = user
    ss.chat = None
    ss.quiz = None
    ss.login_failures = 0
    analytics.log_event(db, user.id, kind)
    st.rerun()


def logout() -> None:
    for key in ("user", "chat", "quiz", "pending_prompt"):
        ss[key] = None
    ss.is_admin = False


# ---------------------------------------------------------------- 로그인
def render_login() -> None:
    st.title(f"📚 {APP_NAME}")
    st.subheader("내 공부 자료로 질문하고, 퀴즈 풀고, 약점까지 기억하는 AI 튜터")
    c1, c2, c3 = st.columns(3)
    c1.markdown("**① 자료 올리기**  \n강의 PDF, 필기 노트(TXT·MD)를 올리면 AI가 읽어 둬요.")
    c2.markdown("**② 질문·퀴즈**  \n자료 근거와 출처를 붙여 답하고, 객관식 퀴즈를 만들어 줘요.")
    c3.markdown("**③ 약점 기억**  \n틀린 개념과 학습 목표를 기억했다가 다음 대화에서 챙겨 줘요.")
    st.info(AI_NOTICE_LONG)

    tab_new, tab_back = st.tabs(["처음이에요", "다시 왔어요"])
    with tab_new:
        with st.form("register_form"):
            nickname = st.text_input("닉네임 (실명 대신 별명, 2~20자)", max_chars=20)
            pin = st.text_input("PIN (숫자 4~6자리 — 다음에 들어올 때 필요해요)", type="password", max_chars=6)
            with st.expander("개인정보 수집·이용 안내 (필수) — 펼쳐서 읽어 주세요"):
                st.markdown(CONSENT_TEXT.format(retention=settings.data_retention_until))
            consent = st.checkbox("위 내용을 확인했으며 개인정보 수집·이용 및 국외 이전에 동의합니다. (필수)")
            if st.form_submit_button("시작하기", type="primary"):
                try:
                    on_login(register(db, nickname, pin, consent), "signup")
                except AuthError as exc:
                    st.error(str(exc))
    with tab_back:
        if ss.login_failures >= 5:
            st.error("로그인 시도가 너무 많아요. 브라우저를 새로고침한 뒤 다시 시도해 주세요.")
            return
        with st.form("login_form"):
            nickname = st.text_input("닉네임", max_chars=20)
            pin = st.text_input("PIN", type="password", max_chars=6)
            if st.form_submit_button("들어가기", type="primary"):
                try:
                    on_login(login(db, nickname, pin), "login")
                except AuthError as exc:
                    ss.login_failures += 1
                    st.error(str(exc))


# ---------------------------------------------------------------- 사이드바
def render_sidebar() -> str:
    pages = [PAGE_CHAT, PAGE_DOCS, PAGE_QUIZ, PAGE_MEMORY, PAGE_FEEDBACK]
    if ss.is_admin:
        pages.append(PAGE_ADMIN)
    if ss.get("page") not in pages:
        ss.page = PAGE_CHAT
    with st.sidebar:
        st.markdown(f"## 📚 {APP_NAME}")
        st.caption(f"v{APP_VERSION} · **{ss.user.nickname}**님")
        page = st.radio("메뉴", pages, key="page", label_visibility="collapsed")
        st.divider()
        st.button("로그아웃", on_click=logout)
        with st.expander("운영자 메뉴"):
            if ss.is_admin:
                st.caption("관리자 모드 켜짐")
            else:
                password = st.text_input("관리자 비밀번호", type="password", key="admin_pw")
                if st.button("확인", key="admin_btn"):
                    if settings.admin_password and hmac.compare_digest(password, settings.admin_password):
                        ss.is_admin = True
                        st.rerun()
                    else:
                        st.error("비밀번호가 맞지 않거나 ADMIN_PASSWORD가 설정되지 않았어요.")
        st.caption(AI_NOTICE_SHORT)
    return page


# ---------------------------------------------------------------- 💬 AI 튜터
def render_message(m: dict) -> None:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] != "assistant":
            return
        sources = m.get("sources") or []
        if sources:
            cited = sum(1 for s in sources if s.get("cited"))
            with st.expander(f"📎 근거 자료 {len(sources)}개 (답변에 인용 {cited}개)"):
                for s in sources:
                    loc = f"{s['filename']} p.{s['page']}" if s.get("page") else s["filename"]
                    mark = "✅ 인용" if s.get("cited") else "참고"
                    st.markdown(f"**[{s['ref']}] {loc}** · 유사도 {s['score']} · {mark}")
                    st.caption(s["excerpt"] + " …")
        tools = list(dict.fromkeys(m.get("tools_used") or []))
        caption = AI_NOTICE_SHORT
        if tools:
            caption += "  ·  사용한 도구: " + ", ".join(TOOL_LABELS.get(t, t) for t in tools)
        st.caption(caption)
        if m.get("rating") is None:
            rating = st.feedback("thumbs", key=f"fb_{m['id']}")
            if rating is not None:
                memory.rate_message(db, ss.user.id, m["id"], int(rating))
                m["rating"] = int(rating)
                analytics.log_event(db, ss.user.id, "rate", {"rating": int(rating)})
        else:
            st.caption("👍 도움이 됐다고 평가함" if m["rating"] == 1 else "👎 아쉽다고 평가함")


def handle_prompt(prompt: str) -> None:
    user: User = ss.user
    text = mask_pii(prompt.strip())[:2000]
    if not text:
        return
    if analytics.count_today(db, user.id, "chat") >= settings.daily_message_limit:
        st.warning(f"오늘 질문 한도({settings.daily_message_limit}회)를 모두 썼어요. 내일 다시 만나요!")
        return
    with st.chat_message("user"):
        st.markdown(text)
    history = [{"role": m["role"], "content": m["content"]} for m in ss.chat]
    with st.chat_message("assistant"):
        with st.spinner("자료를 찾아보고 생각하는 중이에요..."):
            try:
                agent = StudyAgent(
                    db, llm, user.id, user.nickname,
                    top_k=settings.retrieval_top_k, min_score=settings.retrieval_min_score,
                )
                result = agent.run(text, history)
            except (LLMError, DocumentError) as exc:
                st.error(str(exc))
                analytics.log_event(db, user.id, "error", {"where": "chat", "message": str(exc)})
                return
    user_id_msg = memory.save_message(db, user.id, "user", text)
    ss.chat.append({"id": user_id_msg, "role": "user", "content": text})
    msg_id = memory.save_message(db, user.id, "assistant", result.answer, result.sources, result.tools_used)
    ss.chat.append(
        {
            "id": msg_id, "role": "assistant", "content": result.answer,
            "sources": result.sources, "tools_used": result.tools_used, "rating": None,
        }
    )
    for saved in result.memories_saved:
        flash(f"🧠 기억했어요 — {saved}")
    analytics.log_event(
        db, user.id, "chat",
        {"tools": result.tools_used, "sources": len(result.sources), "memories": len(result.memories_saved)},
    )
    st.rerun()


def render_chat() -> None:
    user: User = ss.user
    st.header(PAGE_CHAT)
    if not rag.list_documents(db, user.id):
        st.info("아직 올린 자료가 없어요. 자료를 올리면 **자료 근거와 출처**를 붙여 답해 드려요.")
        st.button("📚 자료 올리러 가기", on_click=goto, args=(PAGE_DOCS,))
    if ss.chat is None:
        ss.chat = memory.load_messages(db, user.id, limit=50)
    for m in ss.chat:
        render_message(m)
    if not ss.chat:
        st.markdown("**이렇게 물어보세요 👇**")
        for col, text in zip(st.columns(len(SUGGESTED_PROMPTS)), SUGGESTED_PROMPTS):
            col.button(text, key=f"suggest_{text}", on_click=ask_tutor, args=(text,))
    prompt = st.chat_input("공부하다 궁금한 걸 물어보세요 (예: 임베딩이 뭐야?)")
    if ss.pending_prompt:
        prompt, ss.pending_prompt = ss.pending_prompt, None
    if prompt:
        handle_prompt(prompt)


# ---------------------------------------------------------------- 📚 내 자료
def ingest(filename: str, data: bytes) -> None:
    user: User = ss.user
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        st.error(f"{filename}: 파일이 {settings.max_upload_mb}MB보다 커요.")
        return
    with st.spinner(f"'{filename}'을(를) 읽고 AI가 검색할 수 있게 정리하는 중..."):
        try:
            info = rag.ingest_document(
                db, llm, user.id, filename, data,
                max_documents=settings.max_documents_per_user,
                max_chunks=settings.max_chunks_per_document,
            )
        except (DocumentError, LLMError) as exc:
            st.error(f"{filename}: {exc}")
            analytics.log_event(db, user.id, "error", {"where": "upload", "message": str(exc)})
            return
    st.success(f"✅ {filename} 등록 완료 — {info.num_chunks}개 조각, {info.num_chars:,}자")
    analytics.log_event(
        db, user.id, "upload",
        {"chunks": info.num_chunks, "chars": info.num_chars, "ext": Path(filename).suffix.lower()},
    )


def render_documents() -> None:
    user: User = ss.user
    st.header(PAGE_DOCS)
    st.caption(
        f"PDF · TXT · MD / 파일당 최대 {settings.max_upload_mb}MB / 최대 {settings.max_documents_per_user}개. "
        "스캔한 이미지 PDF는 글자를 읽을 수 없어요."
    )
    st.warning(
        "실명·연락처 등 개인정보가 담긴 자료는 올리지 마세요. "
        "주민등록번호·전화번호·이메일 형식은 자동으로 가려서 저장해요."
    )
    with st.form("upload_form", clear_on_submit=True):
        files = st.file_uploader("공부 자료 선택", type=["pdf", "txt", "md"], accept_multiple_files=True)
        submitted = st.form_submit_button("자료 등록", type="primary")
    if submitted:
        if not files:
            st.warning("파일을 먼저 선택해 주세요.")
        for f in files or []:
            ingest(f.name, f.getvalue())

    docs = rag.list_documents(db, user.id)
    if SAMPLE_FILE.exists() and not any(d.filename == SAMPLE_NAME for d in docs):
        with st.container(border=True):
            st.markdown("**자료가 없나요? 샘플로 체험해 보세요** — 'AI 기초 개념 노트'(LLM, 토큰, 임베딩, RAG, 에이전트)")
            if st.button("샘플 자료 넣기"):
                ingest(SAMPLE_NAME, SAMPLE_FILE.read_bytes())
                docs = rag.list_documents(db, user.id)

    st.subheader(f"등록된 자료 ({len(docs)}개)")
    if not docs:
        st.caption("아직 없어요.")
    for d in docs:
        c1, c2, c3 = st.columns([6, 3, 1])
        c1.markdown(f"📄 **{d.filename}**")
        c2.caption(f"{d.num_chunks}개 조각 · {d.num_chars:,}자 · {to_kst_display(d.created_at)}")
        if c3.button("삭제", key=f"del_{d.id}"):
            rag.delete_document(db, user.id, d.id)
            analytics.log_event(db, user.id, "delete_document")
            st.rerun()


# ---------------------------------------------------------------- 📝 퀴즈
def render_quiz() -> None:
    user: User = ss.user
    st.header(PAGE_QUIZ)
    st.caption("🤖 문제는 AI가 내 자료를 바탕으로 만들어요. 틀린 개념은 '약점'으로 기억했다가 다음에 다시 챙겨요.")
    docs = rag.list_documents(db, user.id)
    if not docs:
        st.info("퀴즈를 내려면 먼저 자료가 필요해요.")
        st.button("📚 자료 올리러 가기", on_click=goto, args=(PAGE_DOCS,))
        return

    weak = memory.weak_topics(db, user.id, 5)
    with st.form("quiz_setup"):
        c1, c2 = st.columns(2)
        scope = c1.selectbox("출제 범위", ["전체 자료"] + [d.filename for d in docs])
        topic = c2.text_input("주제 키워드 (선택)", placeholder="예: 임베딩, 재귀 함수")
        c3, c4 = st.columns(2)
        num = c3.slider("문항 수", 3, 10, 5)
        difficulty = c4.select_slider("난이도", options=["쉬움", "보통", "어려움"], value="보통")
        focus = st.checkbox(
            f"내 약점 위주로 내기 ({', '.join(weak)})" if weak else "내 약점 위주로 내기 (아직 기록된 약점 없음)",
            disabled=not weak,
        )
        make = st.form_submit_button("퀴즈 만들기", type="primary")
    if make:
        document_id = None if scope == "전체 자료" else next(d.id for d in docs if d.filename == scope)
        with st.spinner("자료를 읽고 문제를 만드는 중..."):
            try:
                questions, hits, scope_name = quiz_mod.generate_quiz(
                    db, llm, user.id, topic=topic, num_questions=num, difficulty=difficulty,
                    focus_weak=focus, document_id=document_id,
                )
            except (quiz_mod.QuizError, LLMError, DocumentError) as exc:
                st.error(str(exc))
                analytics.log_event(db, user.id, "error", {"where": "quiz", "message": str(exc)})
                questions = None
        if questions:
            ss.quiz = {
                "nonce": new_id(),
                "scope": scope_name if scope == "전체 자료" else f"{scope} · {scope_name}",
                "questions": [q.to_dict() for q in questions],
                "sources": {f"S{i + 1}": h.location() for i, h in enumerate(hits)},
                "graded": None,
            }
            analytics.log_event(db, user.id, "quiz_generate", {"n": len(questions), "difficulty": difficulty, "focus_weak": focus})

    if ss.quiz:
        render_quiz_body()


def render_quiz_body() -> None:
    user: User = ss.user
    quiz = ss.quiz
    questions = [quiz_mod.QuizQuestion(**d) for d in quiz["questions"]]
    st.subheader(f"범위: {quiz['scope']}")

    if quiz["graded"] is None:
        with st.form(f"quiz_{quiz['nonce']}"):
            answers = []
            for i, q in enumerate(questions):
                st.markdown(f"**Q{i + 1}. {q.question}**")
                answers.append(
                    st.radio(
                        f"Q{i + 1}", options=list(range(len(q.options))), index=None,
                        format_func=lambda k, q=q: f"{'①②③④'[k]} {q.options[k]}",
                        key=f"answer_{quiz['nonce']}_{i}", label_visibility="collapsed",
                    )
                )
            submitted = st.form_submit_button("채점하기", type="primary")
        if submitted:
            if any(a is None for a in answers):
                st.warning("모든 문제에 답을 골라 주세요.")
                return
            results = quiz_mod.grade(questions, answers)
            summary = memory.record_quiz_attempt(db, user.id, quiz["scope"], results)
            quiz["graded"] = {"answers": answers, "summary": summary}
            analytics.log_event(db, user.id, "quiz_submit", {"n": summary["num_questions"], "correct": summary["num_correct"]})
            st.rerun()
        return

    summary = quiz["graded"]["summary"]
    answers = quiz["graded"]["answers"]
    n, c = summary["num_questions"], summary["num_correct"]
    st.metric("점수", f"{c} / {n}", f"정답률 {round(100 * c / n)}%")
    if summary["new_weak"]:
        st.warning("⚠️ 약점으로 기억했어요: " + ", ".join(summary["new_weak"]) + " — AI 튜터가 다음 대화에서 챙겨 줄게요.")
    if summary["recovered"]:
        st.success("💪 약점을 극복했어요: " + ", ".join(summary["recovered"]))

    wrong_topics = []
    for i, q in enumerate(questions):
        ok = answers[i] == q.answer_index
        if not ok:
            wrong_topics.append(q.topic)
        with st.container(border=True):
            st.markdown(f"{'✅' if ok else '❌'} **Q{i + 1}. {q.question}**  `{q.topic}`")
            if not ok:
                st.markdown(f"- 내가 고른 답: {q.options[answers[i]]}")
            st.markdown(f"- 정답: **{q.options[q.answer_index]}**")
            if q.explanation:
                st.caption(f"해설: {q.explanation}")
            if q.source in quiz["sources"]:
                st.caption(f"근거: [{q.source}] {quiz['sources'][q.source]}")
    st.caption(AI_NOTICE_SHORT)

    c1, c2 = st.columns(2)
    if c1.button("새 퀴즈 만들기"):
        ss.quiz = None
        st.rerun()
    if wrong_topics:
        topics = ", ".join(dict.fromkeys(wrong_topics))
        c2.button(
            "틀린 개념 AI 튜터에게 다시 배우기",
            on_click=ask_tutor,
            args=(f"퀴즈에서 '{topics}' 개념을 틀렸어. 내 자료를 근거로 쉽게 다시 설명하고 확인 문제 하나 내줘.",),
        )


# ---------------------------------------------------------------- 🧠 학습 기록 (장기 기억)
def render_memory() -> None:
    user: User = ss.user
    st.header(PAGE_MEMORY)
    stats = analytics.user_stats(db, user.id)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("올린 자료", stats["documents"])
    c2.metric("질문 수", stats["questions"])
    c3.metric("퀴즈 응시", stats["quizzes"])
    c4.metric("평균 정답률", f"{stats['accuracy']}%" if stats["accuracy"] is not None else "-")

    st.subheader("AI가 기억하고 있는 나")
    st.caption("AI 튜터는 대화를 시작할 때마다 이 내용을 자동으로 불러와요. 틀리거나 원하지 않는 기억은 지울 수 있어요.")
    memories = memory.list_memories(db, user.id)
    if not memories:
        st.info("아직 기억이 없어요. 대화에서 목표·시험 일정·선호하는 설명 방식을 말하거나 퀴즈를 풀면 쌓여요.")
    for category, label in memory.CATEGORIES.items():
        items = [m for m in memories if m["category"] == category]
        if not items:
            continue
        st.markdown(f"**{label}**")
        for m in items:
            c1, c2 = st.columns([9, 1])
            extra = f"  · 오답 {m['weight']}회" if category == "weak_topic" else ""
            origin = {"agent": "대화", "quiz": "퀴즈", "user": "직접 입력"}.get(m["source"], m["source"])
            c1.markdown(f"{m['content']}{extra}  <span style='color:gray;font-size:0.8em'>({origin})</span>", unsafe_allow_html=True)
            if c2.button("🗑", key=f"mem_{m['id']}", help="이 기억 지우기"):
                memory.delete_memory(db, user.id, m["id"])
                st.rerun()
    with st.expander("➕ 기억 직접 추가하기"):
        with st.form("add_memory", clear_on_submit=True):
            category = st.selectbox("종류", list(memory.CATEGORIES), format_func=lambda k: memory.CATEGORIES[k])
            content = st.text_input("내용", max_chars=100, placeholder="예: 12월 14일 정보처리기사 필기 시험")
            if st.form_submit_button("저장") and content.strip():
                memory.add_memory(db, user.id, category, mask_pii(content), source="user")
                st.rerun()

    st.subheader("퀴즈 기록")
    quizzes = list(reversed(memory.recent_quizzes(db, user.id, 50)))
    if quizzes:
        df = pd.DataFrame(
            [
                {
                    "회차": i + 1,
                    "일시": to_kst_display(q["created_at"]),
                    "범위": q["topic"],
                    "점수": f"{q['num_correct']}/{q['num_questions']}",
                    "정답률": round(100 * q["num_correct"] / q["num_questions"]) if q["num_questions"] else 0,
                }
                for i, q in enumerate(quizzes)
            ]
        )
        st.line_chart(df, x="회차", y="정답률")
        st.dataframe(df, hide_index=True)
    else:
        st.caption("아직 푼 퀴즈가 없어요.")

    st.subheader("내 데이터 관리")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("대화 기록 지우기"):
            memory.clear_messages(db, user.id)
            ss.chat = None
            flash("대화 기록을 지웠어요. (장기 기억은 남아 있어요)")
            st.rerun()
    with c2:
        confirm = st.checkbox("계정과 모든 데이터(자료·대화·기억·퀴즈)를 삭제합니다. 되돌릴 수 없어요.")
        if st.button("내 데이터 전체 삭제", disabled=not confirm, type="primary"):
            db.delete_user_data(user.id)
            rag.invalidate(user.id)
            logout()
            flash("모든 데이터를 삭제했어요. 이용해 주셔서 감사합니다.")
            st.rerun()


# ---------------------------------------------------------------- 🗣️ 피드백
def _scale(label: str, key: str, low: str, high: str, start: int = 1, end: int = 5) -> int | None:
    return st.radio(
        label, options=list(range(start, end + 1)), index=None, horizontal=True, key=key,
        help=f"{start} = {low}, {end} = {high}",
    )


def render_feedback() -> None:
    user: User = ss.user
    st.header(PAGE_FEEDBACK)
    st.markdown("StudyMate AI는 **실제 사용자 의견으로 개선**되고 있어요. 3분이면 충분해요!")
    with st.form("feedback_form", clear_on_submit=True):
        group = st.selectbox("어떤 분이신가요?", ["대학생", "취업 준비생", "직장인", "부트캠프·교육과정 수강생", "기타"])
        satisfaction = _scale("1. 전반적으로 만족하시나요?", "fb_sat", "매우 불만족", "매우 만족")
        usefulness = _scale("2. AI 답변·퀴즈가 공부에 도움이 됐나요?", "fb_use", "전혀 아니다", "매우 그렇다")
        ease = _scale("3. 사용하기 쉬웠나요?", "fb_ease", "매우 어렵다", "매우 쉽다")
        recommend = _scale("4. 친구에게 추천할 의향은? (0~10)", "fb_rec", "전혀 없음", "매우 높음", 0, 10)
        liked = st.text_area("5. 좋았던 점")
        improve = st.text_area("6. 불편했던 점, 있었으면 하는 기능 (가장 중요해요!)")
        submitted = st.form_submit_button("피드백 보내기", type="primary")
    if submitted:
        if None in (satisfaction, usefulness, ease, recommend):
            st.warning("1~4번 점수를 모두 골라 주세요. (작성한 글은 다시 입력해야 할 수 있어요)")
        else:
            payload = analytics.save_feedback(
                db, user.id, APP_VERSION, satisfaction=satisfaction, usefulness=usefulness, ease=ease,
                recommend=recommend, liked=mask_pii(liked), improve=mask_pii(improve), tester_group=group,
            )
            analytics.send_webhook(settings.n8n_webhook_url, payload)
            analytics.log_event(db, user.id, "feedback")
            st.success("소중한 의견 감사합니다! 다음 버전에 반영할게요 🙌")
            st.balloons()
    if settings.feedback_form_url:
        st.link_button("📋 더 자세한 설문 참여하기 (Google Form)", settings.feedback_form_url)
    st.caption("피드백은 서비스 개선에만 쓰이며, 계정을 삭제해도 누구인지 알 수 없는 익명 통계로만 남아요.")


# ---------------------------------------------------------------- 🔧 관리자
def render_admin() -> None:
    st.header("🔧 관리자 — 사용자 테스트 현황")
    s = analytics.global_stats(db)
    row1 = st.columns(4)
    row1[0].metric("가입자", s["users"])
    row1[1].metric("실사용자(질문 1회+)", s["active_users"])
    row1[2].metric("질문 수", s["questions"])
    row1[3].metric("퀴즈 응시", s["quizzes"])
    row2 = st.columns(4)
    row2[0].metric("업로드 자료", s["documents"])
    row2[1].metric("👍 비율", f"{s['thumbs_up_rate']}%" if s["thumbs_up_rate"] is not None else "-", f"평가 {s['thumbs_rated']}건")
    row2[2].metric("피드백 응답", s["feedback_count"])
    row2[3].metric("평균 만족도", s["avg_satisfaction"] if s["avg_satisfaction"] is not None else "-")

    st.subheader("버전별 피드백 평균 (개선 전후 비교)")
    versions = analytics.feedback_by_version(db)
    if versions:
        st.dataframe(pd.DataFrame(versions), hide_index=True)
    else:
        st.caption("아직 피드백이 없어요.")

    st.subheader("일별 활동")
    activity = analytics.daily_activity(db)
    if activity:
        pivot = pd.DataFrame(activity).pivot_table(index="day", columns="event_type", values="n", fill_value=0)
        st.bar_chart(pivot)

    st.subheader("피드백 원문")
    feedback = analytics.list_feedback(db)
    if feedback:
        df = pd.DataFrame(feedback)
        df["created_at"] = df["created_at"].map(to_kst_display)
        st.dataframe(df, hide_index=True)
        st.download_button(
            "CSV 다운로드 (엑셀용)", df.to_csv(index=False).encode("utf-8-sig"),
            file_name="studymate_feedback.csv", mime="text/csv",
        )

    st.subheader("👎 평가를 받은 답변 (개선 대상)")
    low = analytics.low_rated_answers(db)
    if not low:
        st.caption("아직 없어요.")
    for item in low:
        with st.container(border=True):
            st.markdown(f"**Q.** {item['question'] or '(질문 없음)'}")
            st.markdown(item["answer"][:800])
            st.caption(to_kst_display(item["created_at"]))
    if st.button("관리자 모드 끄기"):
        ss.is_admin = False
        st.rerun()


# ---------------------------------------------------------------- 라우팅
def main() -> None:
    for message in ss.flash:
        st.toast(message)
    ss.flash = []
    if ss.user is None:
        render_login()
        return
    page = render_sidebar()
    {
        PAGE_CHAT: render_chat,
        PAGE_DOCS: render_documents,
        PAGE_QUIZ: render_quiz,
        PAGE_MEMORY: render_memory,
        PAGE_FEEDBACK: render_feedback,
        PAGE_ADMIN: render_admin,
    }.get(page, render_chat)()


main()
