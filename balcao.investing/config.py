import streamlit as st
import os

# Google OAuth Configuration
GOOGLE_CLIENT_ID = st.secrets.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = st.secrets.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = st.secrets.get("GOOGLE_REDIRECT_URI", "")

# Authentication settings
AUTH_CONFIG = {
    "credentials": {
        "usernames": {
            "admin": {
                "email": "admin@example.com",
                "name": "Admin User",
                "password": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj4J/HS.iK8."  # "admin123"
            }
        }
    },
    "cookie": {
        "expiry_days": 30,
        "key": "some_signature_key",
        "name": "some_cookie_name"
    }
}

# Allowed email domains (optional - for restricting access)
ALLOWED_DOMAINS = ["gmail.com", "outlook.com", "yahoo.com"]

# Session configuration
SESSION_CONFIG = {
    "session_timeout": 3600,  # 1 hour in seconds
    "max_login_attempts": 3
} 