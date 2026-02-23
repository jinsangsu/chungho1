import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai


def get_working_gemini_model():
    genai.configure(api_key=st.secrets["gemini_api_key"])

    # 1) 우선 흔히 쓰는 후보들부터 시도
    candidates = [
        "gemini-1.5-flash",
        "gemini-1.5-flash-latest",
        "gemini-1.5-pro",
        "gemini-1.5-pro-latest",
        "gemini-pro",  # 구버전 호환용
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

#@st.cache_resource
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

# --- 2. 로그인 화면 ---
def login():
    st.set_page_config(page_title="충호본부 AI 비서 로그인", layout="centered")
    st.title("🛡️ 충호본부 스마트 AI 비서")
    st.write("사번으로 로그인하여 서비스를 시작하세요.")

    emp_id = st.text_input("사번(ID) 입력", placeholder="사번을 입력하세요", type="password")
    
    if st.button("로그인", use_container_width=True):
        member_list = fetch_data("사원명부")
        # 사번 매칭 (사번이 숫자로 인식될 수 있어 str로 변환 대조)
        user = next((row for row in member_list if str(row.get('사번')) == emp_id), None)
        
        if user:
            st.session_state["logged_in"] = True
            st.session_state["user_name"] = user.get("이름", "사용자")
            st.rerun()
        else:
            st.error("일치하는 사번이 없습니다. 시트의 사번을 확인해 주세요.")

# --- 3. AI 답변 생성 ---
def get_ai_response(user_query):
    # 1. 시트에서 질의응답 데이터 가져오기
    qa_data = fetch_data("질의응답시트")
    
    # 2. 참고할 데이터 구성 (에러 방지용 처리)
    context = ""
    if qa_data:
        context_list = []
        for item in qa_data:
            q = str(item.get("질문", "")).strip()
            a = str(item.get("답변", "")).strip()
            if q and a:
                context_list.append(f"Q: {q}\nA: {a}")
        
        # ⚠️ 중요: 데이터가 너무 많으면 404/400 오류가 날 수 있으므로 최신 50개로 제한
        context = "\n\n".join(context_list[-50:]) 
    else:
        context = "현재 등록된 지침 데이터가 없습니다."

    # 3. AI 프롬프트 작성 (인사 및 일상 대화 허용 버전)
    prompt = f"""
    당신은 KB손해보험 충청호남본부의 '충호 Assistant'입니다. 
    설계사님들에게 친절하고 든든한 파트너가 되어주세요.

    [답변 원칙]
    1. 인삿말(안녕, 반가워 등)이나 일상적인 대화에는 보험 전문가답게 따뜻하고 위트 있게 응답하세요.
    2. 업무 관련 질문인 경우, 아래 제공된 [지침 데이터]를 최우선으로 참고하여 정확하게 답하세요.
    3. [지침 데이터]에 없는 전문적인 업무 지침은 "현재 제 지침서에는 등록되지 않은 내용입니다. 정확한 확인을 위해 지점 매니저님께 문의 부탁드립니다!"라고 안내하세요.
    4. 모든 답변은 모바일에서 읽기 편하게 짧은 문장과 불렛 포인트(•)를 사용하세요.
    
    [지침 데이터]:
    {context}
    
    질문: {user_query}
    
    [답변 가이드]:
    1. 반드시 제공된 데이터에 기반하여 답변하세요.
    2. 데이터에 없는 내용은 "현재 등록되지 않은 지침입니다. 지점 매니저에게 확인 부탁드립니다."라고 안내하세요.
    3. 답변은 스마트폰에서 보기 편하게 핵심만 요약하고 불렛 포인트(•)를 사용하세요.
    """
    
    try:
        # ⚠️ API 설정 및 모델 선언 (404 해결 포인트)
        genai.configure(api_key=st.secrets["gemini_api_key"])
        
        # 모델명을 명확히 지정 (이름이 틀리면 404가 발생함)
        model = get_working_gemini_model()
        
        # 답변 생성
        response = model.generate_content(prompt)
        
        if response and response.text:
            return response.text
        else:
            return "AI가 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요."
            
    except Exception as e:
        # 에러 발생 시 상세 원인 출력 (디버깅용)
        return f"⚠️ 서비스 일시 오류 (관리자 문의): {str(e)}"

#메인채팅화면
def main_page():
    st.set_page_config(page_title="충호본부 AI Assistant", layout="wide")
    st.write(f"### 👋 안녕하세요, {st.session_state['user_name']}님!")
    
    if st.sidebar.button("로그아웃"):
        del st.session_state["logged_in"]
        st.rerun()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("업무 지침이나 궁금한 점을 물어보세요..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("지침을 분석 중입니다..."):
                answer = get_ai_response(prompt)
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})

# --- 5. 앱 실행 ---
if __name__ == "__main__":
    if "logged_in" not in st.session_state:
        login()
    else:
        main_page()