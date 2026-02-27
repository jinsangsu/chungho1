import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import re
st.set_page_config(page_title="충호본부 AI Assistant", layout="wide") 

def get_working_gemini_model():
    genai.configure(api_key=st.secrets["gemini_api_key"])

    # 1) 우선 흔히 쓰는 후보들부터 시도
    candidates = [
        "models/gemini-2.0-flash-lite",
        "models/gemini-2.0-flash-lite-001",
        "models/gemini-2.0-flash",
        "models/gemini-2.0-flash-001",
        "models/gemini-2.5-flash",
        "models/gemini-2.5-pro",
    ]

    for name in candidates:
        try:
            m = genai.GenerativeModel(name)
            # 아주 짧게 호출해 모델 유효성 확인
            _ = m.generate_content("ping")
            return m
        except Exception:
            pass

    # 2) 그래도 안 되면 list_models로 generateContent 가능한 모델 자동 선택
    try:
        models = list(genai.list_models())
        # 사이드바에 모델 목록 일부 표시(디버깅)
        st.sidebar.caption("✅ Available models:")
        for mm in models[:15]:
            st.sidebar.caption(f"- {mm.name} / {getattr(mm, 'supported_generation_methods', [])}")

        for mm in models:
            methods = getattr(mm, "supported_generation_methods", [])
            if "generateContent" in methods:
                return genai.GenerativeModel(mm.name)  # mm.name은 보통 "models/..." 형태
    except Exception as e:
        st.sidebar.error(f"list_models 실패: {e}")

    raise RuntimeError("generateContent 지원 모델을 찾지 못했습니다.")

# --- 1. 설정 및 권한 ---
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource
def get_gs_client():
    # Streamlit Cloud의 Secrets 설정을 그대로 사용
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], 
        scopes=GOOGLE_SCOPES
    )
    return gspread.authorize(creds)

def fetch_data(sheet_name):
    # 1. URL이 비어있지 않은지 다시 한번 하드코딩 확인
    target_url = "https://docs.google.com/spreadsheets/d/1C2tEZ1tGgbhfLw5LsUWrzttByD-zt_CZobg-FVTKyWo/edit"
    
    try:
        if not target_url:
            st.error("구글 시트 URL 주소를 확인해주세요.")
            return []
            
        client = get_gs_client()
        sh = client.open_by_url(target_url)
        
        # 2. 탭 이름을 유연하게 처리 (시트에 '사원명부'가 있는지 확인)
        worksheet_list = [w.title for w in sh.worksheets()]
        if sheet_name not in worksheet_list:
            st.error(f"'{sheet_name}' 탭을 찾을 수 없습니다. 현재 탭 목록: {worksheet_list}")
            return []
            
        return sh.worksheet(sheet_name).get_all_records()
    except Exception as e:
        st.error(f"연동 오류 발생: {e}")
        return []

@st.cache_data(ttl=120)
def fetch_data_cached(sheet_name):
    return fetch_data(sheet_name)


# --- 2. 로그인 화면 ---
def login():
    st.title("🛡️ 충호본부 스마트 AI 비서")
    st.write("사번으로 로그인하여 서비스를 시작하세요.")

    # st.form을 사용하면 엔터키로 제출(Submit)이 가능합니다.
    with st.form("login_form", clear_on_submit=False):
        emp_id = st.text_input("사번(ID) 입력", placeholder="사번을 입력하세요", type="password")
        submit_button = st.form_submit_button("로그인", use_container_width=True)
        
        if submit_button:
            if not emp_id:
                st.error("사번을 입력해 주세요.")
                return

            member_list = fetch_data_cached("사원명부")
            user = next((row for row in member_list if str(row.get('사번')) == emp_id), None)
            
            if user:
                # 로그인 상태 저장
                st.session_state["logged_in"] = True
                st.session_state["user_name"] = user.get("이름", "사용자")
                st.rerun()
            else:
                st.error("일치하는 사번이 없습니다. 시트의 사번을 확인해 주세요.")

#Top-k 뽑는 함수 2개 추가
def normalize_tokens(text: str) -> set:
    text = re.sub(r"\s+", " ", str(text)).strip().lower()
    tokens = re.findall(r"[0-9a-zA-Z가-힣]+", text)
    return set(tokens)

def pick_top_k_qa(user_query: str, qa_data: list, k: int = 5):
    q_tokens = normalize_tokens(user_query)
    scored = []
    uq_norm = str(user_query).strip().lower()

    for idx, item in enumerate(qa_data):
        q = str(item.get("질문", "")).strip()
        a = str(item.get("답변", "")).strip()
        if not q or not a:
            continue

        q_norm = q.lower()

        # 기본 토큰 교집합 점수
        score = len(q_tokens & normalize_tokens(q))

        # 한 단어/짧은 질의 보너스
        if uq_norm and uq_norm in q_norm:
            score += 3
        elif q_norm and q_norm in uq_norm:
            score += 2

        scored.append((score, idx, q, a))

    scored.sort(reverse=True, key=lambda x: x[0])
    return scored[:k]


# --- 3. AI 답변 생성 ---
def get_ai_response(user_query):
    user_name = st.session_state.get("user_name", "사용자")

    # 1) 시트 데이터 조회
    qa_data = fetch_data_cached("질의응답시트")

    if not qa_data:
        return f"{user_name}님, 현재 등록된 지침 데이터가 없습니다."

    # 2) Top-k 검색
    top = pick_top_k_qa(user_query, qa_data, k=5)

    LOW = 1
    HIGH = 5
    top_score = top[0][0] if top else 0

    # 3) LOW: 차단 (LLM 호출 X)
    if top_score < LOW:
        return f"{user_name}님, 현재 등록되지 않은 지침입니다. 지점 매니저에게 확인 부탁드립니다."

    # 4) HIGH: 직반환 (LLM 호출 X)
    if top_score >= HIGH:
        best_score, best_idx, best_q, best_a = top[0]
        return (
            f"{user_name}님, 아래 지침을 안내드립니다.\n\n"
            f"• {best_a}\n\n"
            f"(근거: 질의응답시트 #{best_idx+2})"
        )

    # 5) MID: LLM 호출용 context 구성
    context_list = []
    for score, idx, q, a in top:
        context_list.append(
            f"[근거#{idx+2} / score={score}]\n"
            f"Q: {q}\n"
            f"A: {a}"
        )
    context = "\n\n".join(context_list)

    # 6) 프롬프트 작성
    prompt = f"""
당신은 KB손해보험 충청호남본부의 '충호 Assistant'입니다.
{user_name}님에게 친절하고 든든한 파트너가 되어주세요.

[답변 원칙]
0. 답변의 첫 문장은 반드시 "{user_name}님," 으로 시작하세요.
1. 업무 관련 질문은 아래 [지침 데이터]를 기반으로 답하세요.
2. 데이터에 없는 경우 "{user_name}님, 현재 등록되지 않은 지침입니다. 지점 매니저에게 확인 부탁드립니다."라고 안내하세요.
3. 답변은 모바일에서 읽기 쉽게 불렛(•)으로 정리하세요.

[지침 데이터]
{context}

질문: {user_query}
"""

    try:
        genai.configure(api_key=st.secrets["gemini_api_key"])
        model = get_working_gemini_model()
        response = model.generate_content(prompt)

        if response and response.text:
            return response.text
        return "AI가 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요."

    except Exception as e:
        msg = str(e)
        if "429" in msg or "Quota exceeded" in msg:
            return (
                f"{user_name}님, 현재 AI 처리량이 많아 잠시 자동응답이 제한되었습니다.\n\n"
                "• 지금은 등록된 지침 기반으로만 안내됩니다.\n"
                "• 지침에 없는 경우: 지점 매니저 확인 부탁드립니다."
            )
        return f"⚠️ 서비스 일시 오류 (관리자 문의): {msg}"


#메인채팅화면
def main_page():
    # 1. 사이드바 및 레이아웃 설정
    st.markdown("""
        <style>
        /* 상단 헤더 제거에 따른 여백 조정 */
        .block-container { padding-top: 2rem; }
        
        /* 사이드바 스타일 */
        [data-testid="stSidebar"] { background-color: #F8F9FA; width: 300px !important; }
        
        /* 새 채팅 버튼 스타일 */
        .stButton > button {
            width: 100%; border-radius: 10px; border: 1px solid #ddd;
            background-color: white; color: #333; height: 45px;
            font-weight: bold; margin-bottom: 20px;
        }
        
        /* 업무공지 섹션 스타일 */
        .notice-box {
            background-color: #ffffff; padding: 10px;
            border-radius: 8px; border: 1px solid #eee; margin-bottom: 10px;
        }
        </style>
    """, unsafe_allow_html=True)

    # 2. 사이드바 구성
    with st.sidebar:
        # (1) 새 채팅 버튼
        if st.button("➕ 새 채팅"):
            st.session_state.messages = []
            st.rerun()

        st.divider()

        # (2) 지역단 업무공지 (아코디언 활용)
        st.markdown("### 📢 지역단 정보")
        with st.expander("📌 최신 지역단 업무공지", expanded=False):
            # 클릭 시 세부 항목 표시
            tab_choice = st.radio(
                "항목 선택",
                ["주요 업무공지", "시상안", "지역단 주요 사항"],
                key="notice_tab"
            )
            
            # 선택한 항목에 따른 시트 데이터 필터링 또는 고정 안내
            if tab_choice == "주요 업무공지":
                st.info("💡 업무공지")
            elif tab_choice == "시상안":
                st.success("🏆 시상안")
            else:
                st.warning("📅 지역단 조회 일정 안내")
            
            # '적용' 버튼 클릭 시 채팅창에 해당 내용 요약 요청 자동 입력
            if st.button(f"{tab_choice} 내용 요약보기"):
                st.session_state.temp_prompt = f"현재 등록된 '{tab_choice}'의 핵심 내용을 요약해서 알려줘."

        st.divider()

        # (3) 최근 질문 목록
        st.markdown("### 📜 최근 질문")
        if "messages" in st.session_state and len(st.session_state.messages) > 0:
            user_questions = [m["content"] for m in st.session_state.messages if m["role"] == "user"]
            for q in reversed(user_questions[-10:]): # 최근 5개
                if st.button(f"🔍 {q[:15]}...", key=f"hist_{q}"):
                    st.session_state.temp_prompt = q
        else:
            st.caption("질문 내역이 없습니다.")

    # 3. 메인 채팅 영역
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # 채팅 메시지 출력
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # 4. 입력 처리 (자동 입력 또는 직접 입력)
    auto_p = st.session_state.get("temp_prompt", None)
    user_p = st.chat_input("궁금한 점을 입력하세요...")
    
    final_prompt = auto_p if auto_p else user_p

    if final_prompt:
        if "temp_prompt" in st.session_state:
            del st.session_state.temp_prompt
            
        st.session_state.messages.append({"role": "user", "content": final_prompt})
        with st.chat_message("user"):
            st.markdown(final_prompt)

        with st.chat_message("assistant"):
            with st.spinner("데이터 분석 중..."):
                answer = get_ai_response(final_prompt)
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
        st.rerun()
# --- 5. 앱 실행 ---
if __name__ == "__main__":
    if "logged_in" not in st.session_state:
        login()
    else:
        main_page()