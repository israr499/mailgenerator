import os
import logging
import base64
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
import pyrebase
import firebase_admin
from firebase_admin import credentials, firestore
import PyPDF2
import docx
from typing import Tuple, Dict, Any, List

# =======================
# Setup & Config
# =======================
load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# -----------------------------
# Firebase Config (from .env)
# -----------------------------
firebaseConfig = {
    "apiKey": os.getenv("FIREBASE_API_KEY"),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
    "projectId": os.getenv("FIREBASE_PROJECT_ID"),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
    "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
    "appId": os.getenv("FIREBASE_APP_ID"),
    "databaseURL": os.getenv("FIREBASE_DATABASE_URL"),
}

if not firebaseConfig["apiKey"]:
    st.error("❌ FIREBASE_API_KEY not found! Check your .env file.")
    st.stop()

# -----------------------------
# Firebase Initialization
# -----------------------------
service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT")

if not service_account_path or not os.path.exists(service_account_path):
    raise FileNotFoundError(
        f"❌ Service account file not found at: {service_account_path}. "
        "Check your .env and file location."
    )

cred = credentials.Certificate(service_account_path)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {"databaseURL": os.getenv("FIREBASE_DATABASE_URL")})

db = firestore.client()
firebase = pyrebase.initialize_app(firebaseConfig)
auth = firebase.auth()

# -----------------------------
# Gemini API
# -----------------------------
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    st.error("❌ No Gemini API key found! Please set GEMINI_API_KEY in your .env file")
    st.stop()

genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-2.5-flash")

# =======================
# Helpers
# =======================
def fallback_generation(prompt: str) -> str:
    return f"(Fallback response)\n\nCould not connect to Gemini. Prompt was:\n{prompt}"


def generate_with_gemini(prompt: str, system_instruction: str | None = None) -> str:
    try:
        if system_instruction:
            response = model.generate_content([system_instruction, prompt])
        else:
            response = model.generate_content(prompt)
        return response.text or "(⚠ Empty response from Gemini)"
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        return fallback_generation(prompt + f"\n\n(Note: Fallback used due to: {e})")


def extract_text_from_pdf(file) -> str:
    try:
        reader = PyPDF2.PdfReader(file)
        texts = []
        for page in reader.pages:
            try:
                t = page.extract_text()
                if t:
                    texts.append(t)
            except Exception:
                pass
        return " ".join(texts)
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""


def extract_text_from_docx(file) -> str:
    try:
        doc = docx.Document(file)
        return " ".join([para.text for para in doc.paragraphs])
    except Exception as e:
        logger.error(f"DOCX extraction failed: {e}")
        return ""


def analyze_cv(file, file_type: str) -> str:
    if file_type == "pdf":
        return extract_text_from_pdf(file)
    elif file_type in ["doc", "docx"]:
        return extract_text_from_docx(file)
    return ""


def download_txt(subject: str, body: str) -> str:
    content = f"Subject: {subject}\n\n{body}"
    b64 = base64.b64encode(content.encode()).decode()
    return f'<a href="data:file/txt;base64,{b64}" download="email.txt">💾 Download as TXT</a>'


def parse_subject_body(text: str) -> Tuple[str, str]:
    """Robust parsing for Subject/Body sections from model output."""
    if "Subject:" in text and "Body:" in text:
        try:
            before_body, after_subject = text.split("Body:", 1)
            subject = before_body.replace("Subject:", "").strip()
            body = after_subject.strip()
            if subject and body:
                return subject, body
        except Exception:
            pass
    # Fallback
    return "Generated Email", text.strip()


def ensure_user_doc(user_email: str) -> firestore.DocumentReference:
    ref = db.collection("users").document(user_email)
    snap = ref.get()
    if not snap.exists:
        ref.set({"emails": []})
    return ref


def save_email(user_email: str, email_obj: Dict[str, Any]) -> None:
    """Save a single email object using ArrayUnion to avoid duplicates."""
    ref = ensure_user_doc(user_email)
    try:
        ref.update({"emails": firestore.ArrayUnion([email_obj])})
    except Exception as e:
        logger.error(f"Firestore save failed: {e}")
        st.sidebar.error("❌ Failed to save email to Firestore.")


def remove_email(user_email: str, email_obj: Dict[str, Any]) -> None:
    """Remove an exact email object using ArrayRemove (works if object matches exactly)."""
    ref = ensure_user_doc(user_email)
    try:
        ref.update({"emails": firestore.ArrayRemove([email_obj])})
    except Exception as e:
        logger.error(f"Firestore remove failed: {e}")
        st.error("❌ Failed to delete email.")


# =======================
# UI
# =======================
st.title("📧 AI Email Generator")

if "user" not in st.session_state:
    st.session_state["user"] = None

menu = ["Generate Email", "Logout"] if st.session_state["user"] else ["Login", "Signup"]
choice = st.sidebar.selectbox("Menu", menu)

# ============== Signup ==============
if choice == "Signup" and not st.session_state["user"]:
    st.subheader("Create a New Account")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Signup"):
        try:
            auth.create_user_with_email_and_password(email, password)
            st.success("✅ Account created successfully")
        except Exception as e:
            st.error(f"❌ {e}")

# ============== Login ==============
elif choice == "Login" and not st.session_state["user"]:
    st.subheader("Login")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        try:
            auth.sign_in_with_email_and_password(email, password)
            st.session_state["user"] = email  # Using email as document ID; consider switching to UID for prod
            st.success(f"✅ Logged in as {email}")
            st.rerun()
        except Exception as e:
            st.error(f"❌ {e}")

# ============== Logout ==============
elif choice == "Logout" and st.session_state["user"]:
    st.session_state["user"] = None
    st.success("👋 You have been logged out.")
    st.rerun()

# =======================
# Email Generator (only if logged in)
# =======================
if st.session_state["user"]:
    st.sidebar.header("✍ Email Settings")

    # Category & Purpose
    purpose_category = st.sidebar.selectbox("Select Category", ["Academic", "Career", "General"])
    if purpose_category == "Academic":
        purposes = [
            "Assignment Extension",
            "Leave Application",
            "Transcript Request",
            "Exam Absence Explanation",
            "Scholarship Request",
        ]
    elif purpose_category == "Career":
        purposes = [
            "Job Application",
            "Internship Application",
            "Recommendation Letter Request",
            "Project Collaboration Request",
            "Fee Waiver / Financial Aid Request",
        ]
    else:
        purposes = [
            "Meeting Request",
            "Research Assistance / Guidance",
            "Event Invitation",
            "Follow-up Email",
            "General Query",
        ]

    purpose = st.sidebar.selectbox("Purpose", purposes)

    # Inputs
    student_name = st.sidebar.text_input("Your Name")
    recipient = st.sidebar.text_input("Recipient")
    details = st.sidebar.text_area("Additional Details")

    tone = st.sidebar.radio("Tone", ["Formal", "Polite", "Professional"], index=1)
    style = st.sidebar.radio("Writing Style", ["Short & Direct", "Detailed & Elaborate", "Creative"])
    formality_level = st.sidebar.slider("Formality Level", 0, 100, 70)
    language = st.sidebar.selectbox("Language", ["English", "Urdu", "French", "German", "Spanish"])

    # CV Upload
    cv_text = ""
    if purpose in ["Job Application", "Internship Application"]:
        cv_file = st.sidebar.file_uploader("📂 Upload Your CV (PDF/DOCX)", type=["pdf", "doc", "docx"])
        if cv_file:
            file_type = cv_file.name.split(".")[-1].lower()
            cv_text = analyze_cv(cv_file, file_type)
            if cv_text:
                st.sidebar.success("✅ CV Uploaded & Processed!")
            else:
                st.sidebar.warning("⚠ CV could not be processed properly.")

    # =======================
    # Generate Email
    # =======================
    if st.sidebar.button("🚀 Generate Email"):
        with st.spinner("✨ Generating your email..."):
            prompt = f"""
            You are an AI assistant that generates professional emails for students and early-career professionals.

            Generate an email with:
            - Subject line
            - Body text

            Student Name: {student_name}
            Recipient: {recipient}
            Purpose: {purpose}
            Additional Context: {details}
            Tone: {tone}
            Writing Style: {style}
            Formality Level: {formality_level}/100
            Language: {language}
            {"The student has attached their CV. Relevant CV details: " + cv_text[:1000] if cv_text else ""}

            Format output as:
            Subject: ...
            Body: ...
            """
            raw = generate_with_gemini(prompt)

        st.success("✅ Email Generated Successfully!")

        subject, body_text = parse_subject_body(raw)

        # Display email
        st.markdown("### 📌 Final Email")
        st.markdown(f"*To:* {recipient}")
        st.markdown(f"*Subject:* {subject}")
        st.text_area("📩 Final Email Body", body_text, height=300)

        st.markdown(download_txt(subject, body_text), unsafe_allow_html=True)

        # Save email
        email_obj = {"subject": subject, "body": body_text}
        save_email(st.session_state["user"], email_obj)
        st.sidebar.success("✅ Email saved automatically to Firestore!")

        # Subject line suggestions (only after generation)
        subj_prompt = f"""
        Generate 5 professional subject line suggestions for this email purpose:
        {purpose}, Context: {details}, Tone: {tone}, Language: {language}
        """
        suggestions_raw = generate_with_gemini(subj_prompt)
        st.markdown("### 📝 Subject Line Suggestions")
        for line in suggestions_raw.splitlines():
            line = line.strip("-• ")
            if line:
                st.markdown(f"- {line}")

    # =======================
    # View / Manage Persistent History (Firestore)
    # =======================
    st.divider()
    st.subheader("📜 Saved Emails (Persistent)")

    user_email = st.session_state["user"]
    user_doc_ref = ensure_user_doc(user_email)
    user_snapshot = user_doc_ref.get()
    user_data = user_snapshot.to_dict() or {}
    emails: List[Dict[str, Any]] = user_data.get("emails", [])

    if not emails:
        st.info("No emails saved yet.")
    else:
        # Render each saved email with a delete button
        for idx, email in enumerate(emails):
            if isinstance(email, dict):
                with st.expander(f"{idx+1}. {email.get('subject', '(No Subject)')}"):
                    st.text_area("Body", email.get("body", ""), height=200, key=f"hist_body_{idx}")
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("🗑 Delete this email", key=f"del_{idx}"):
                            remove_email(user_email, email)
                            st.success("Deleted.")
                            st.rerun()
                    with col2:
                        st.markdown(download_txt(email.get("subject", "(No Subject)"), email.get("body", "")), unsafe_allow_html=True)
            else:
                with st.expander(f"{idx+1}. (Old entry)"):
                    st.text(str(email))
                    if st.button("🗑 Delete", key=f"del_old_{idx}"):
                        remove_email(user_email, email)
                        st.success("Deleted.")
                        st.rerun()

        # Clear all history
        if st.button("⚠ Clear All History"):
            try:
                user_doc_ref.update({"emails": []})
                st.success("History cleared.")
                st.rerun()
            except Exception as e:
                logger.error(e)
                st.error("Failed to clear history.")
