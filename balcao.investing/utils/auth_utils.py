import streamlit as st
import streamlit_authenticator as stauth
from google.oauth2 import id_token
from google.auth.transport import requests
import requests as http_requests
import json
import time
from datetime import datetime, timedelta
import config

def init_authentication():
    """Initialize the authentication system"""
    try:
        authenticator = stauth.Authenticate(
            config.AUTH_CONFIG['credentials'],
            config.AUTH_CONFIG['cookie']['name'],
            config.AUTH_CONFIG['cookie']['key'],
            config.AUTH_CONFIG['cookie']['expiry_days']
        )
        return authenticator
    except Exception as e:
        st.error(f"Authentication initialization failed: {e}")
        return None

def verify_google_token(token):
    """Verify Google ID token"""
    try:
        idinfo = id_token.verify_oauth2_token(
            token, 
            requests.Request(), 
            config.GOOGLE_CLIENT_ID
        )
        
        # Check if the token is expired
        if idinfo['exp'] < time.time():
            return None, "Token expired"
        
        # Check if the email domain is allowed
        email = idinfo.get('email', '')
        domain = email.split('@')[-1] if '@' in email else ''
        
        if domain not in config.ALLOWED_DOMAINS:
            return None, f"Domain {domain} not allowed"
        
        return idinfo, None
    except Exception as e:
        return None, f"Token verification failed: {e}"

def check_session_timeout():
    """Check if the current session has timed out"""
    if 'login_time' not in st.session_state:
        return True
    
    login_time = st.session_state.login_time
    current_time = datetime.now()
    timeout_delta = timedelta(seconds=config.SESSION_CONFIG['session_timeout'])
    
    if current_time - login_time > timeout_delta:
        # Clear session state
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        return True
    
    return False

def login_with_google():
    """Handle Google OAuth login"""
    if not config.GOOGLE_CLIENT_ID:
        st.error("Google OAuth not configured. Please set up Google Client ID in secrets.")
        return False
    
    # Create Google OAuth URL
    google_oauth_url = f"https://accounts.google.com/o/oauth2/v2/auth"
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline"
    }
    
    # Build the authorization URL
    auth_url = f"{google_oauth_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
    
    st.markdown(f"""
    <div style="text-align: center; margin: 20px 0;">
        <a href="{auth_url}" target="_self">
            <button style="
                background-color: #4285f4;
                color: white;
                padding: 12px 24px;
                border: none;
                border-radius: 4px;
                font-size: 16px;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                gap: 8px;
            ">
                <svg width="20" height="20" viewBox="0 0 24 24">
                    <path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                    <path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                    <path fill="currentColor" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                    <path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                </svg>
                Sign in with Google
            </button>
        </a>
    </div>
    """, unsafe_allow_html=True)
    
    return True

def handle_oauth_callback():
    """Handle OAuth callback from Google"""
    # Get the authorization code from URL parameters
    code = st.query_params.get("code", None)
    
    if code:
        try:
            # Exchange authorization code for tokens
            token_url = "https://oauth2.googleapis.com/token"
            token_data = {
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": config.GOOGLE_REDIRECT_URI
            }
            
            response = http_requests.post(token_url, data=token_data)
            tokens = response.json()
            
            if "id_token" in tokens:
                # Verify the ID token
                user_info, error = verify_google_token(tokens["id_token"])
                
                if user_info and not error:
                    # Store user information in session state
                    st.session_state.authenticated = True
                    st.session_state.user_email = user_info.get('email', '')
                    st.session_state.user_name = user_info.get('name', '')
                    st.session_state.user_picture = user_info.get('picture', '')
                    st.session_state.login_time = datetime.now()
                    
                    # Clear the URL parameters
                    st.query_params.clear()
                    
                    st.success(f"Welcome, {user_info.get('name', 'User')}!")
                    st.rerun()
                else:
                    st.error(f"Authentication failed: {error}")
            else:
                st.error("Failed to get ID token from Google")
                
        except Exception as e:
            st.error(f"OAuth callback error: {e}")

def logout():
    """Handle user logout"""
    # Clear all session state
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    
    st.success("Successfully logged out!")
    st.rerun()

def require_auth():
    """Decorator to require authentication for pages"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            if not is_authenticated():
                st.error("Please log in to access this page.")
                st.stop()
            return func(*args, **kwargs)
        return wrapper
    return decorator

def is_authenticated():
    """Check if user is authenticated and session is valid"""
    if 'authenticated' not in st.session_state or not st.session_state.authenticated:
        return False
    
    if check_session_timeout():
        return False
    
    return True

def get_user_info():
    """Get current user information"""
    if is_authenticated():
        return {
            'email': st.session_state.get('user_email', ''),
            'name': st.session_state.get('user_name', ''),
            'picture': st.session_state.get('user_picture', '')
        }
    return None 