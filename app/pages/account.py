import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st

from auth import classify_auth_error, request_resend_verification, sign_in, sign_out, sign_up

st.title("Account")

if st.session_state.get("user"):
    user = st.session_state["user"]
    st.success(f"Signed in as {user.email}")
    if st.button("Sign out"):
        sign_out()
        st.rerun()
else:
    tab_login, tab_register = st.tabs(["Log In", "Create Account"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log In")
        if submitted:
            try:
                sign_in(email, password)
                st.rerun()
            except Exception as e:  # noqa: BLE001 - classified below, never shown raw
                st.error(classify_auth_error(e))

    with tab_register:
        st.caption(
            "New accounts start in Learning / Paper Mode. Live execution stays locked until the "
            "graduation criteria in TRADING_OS_DESIGN.md §30 are met and manually approved."
        )
        with st.form("register_form"):
            name = st.text_input("Display name")
            email2 = st.text_input("Email", key="reg_email")
            password2 = st.text_input("Password", type="password", key="reg_pw")
            confirm = st.text_input("Confirm password", type="password")
            agree = st.checkbox(
                "I acknowledge trading involves risk of loss and this platform provides no "
                "guarantee of profit."
            )
            submitted2 = st.form_submit_button("Create Account")
        if submitted2:
            if password2 != confirm:
                st.error("Passwords do not match.")
            elif not agree:
                st.error("You must acknowledge the risk disclosure to continue.")
            else:
                try:
                    sign_up(email2, password2, name)
                    st.success("Account created. Check your email to verify, then log in.")
                except Exception as e:  # noqa: BLE001 - classified below, never shown raw
                    code = getattr(e, "code", None)
                    if code in ("user_already_exists", "email_exists"):
                        st.error(
                            "An account with this email may already exist. If you haven't verified "
                            'it yet, use "Resend verification email" below instead of creating a new '
                            "account."
                        )
                    else:
                        st.error(classify_auth_error(e))

    with st.expander("Resend verification email"):
        with st.form("resend_verification_form"):
            resend_email = st.text_input("Email")
            resend_submitted = st.form_submit_button("Resend verification email")
        if resend_submitted:
            if not resend_email.strip():
                st.error("Enter your email first.")
            else:
                ok, message = request_resend_verification(resend_email.strip())
                if ok:
                    st.success(message)
                else:
                    st.error(message)
