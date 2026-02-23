import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

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
    # '질의응답시트' 탭에서 데이터를 가져옴
    qa_data = fetch_data("질의응답시트")
    
    context = f"[충호본부 질의응답 지침서]\n{str(qa_data)}"
    
    prompt = f"""
    당신은 충호본부 설계사들을 돕는 전문 AI 비서입니다.
    사용자 이름: {st.session_state['user_name']}님
    
    [규칙]
    1. 인삿말이나 일상 대화는 밝고 친절하게 응답하세요.
    2. 업무 질문은 반드시 제공된 [충호본부 질의응답 지침서]를 바탕으로 정확하게 답변하세요.
    3. 지침서에 없는 업무 내용은 "죄송합니다. 해당 지침은 등록되지 않았습니다."라고 정중히 안내하세요.
    4. 답변은 모바일에서 보기 편하게 요약하여 전달하세요.
    
    참고 데이터:
    {context}
    
    질문: {user_query}
    """
    
    genai.configure(api_key=st.secrets["gemini_api_key"])
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(prompt)
    return response.text

# --- 4. 메인 채팅 화면 ---
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