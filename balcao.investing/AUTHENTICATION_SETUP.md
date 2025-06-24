# Google Authentication Setup Guide

This guide will help you set up Google OAuth authentication for your Streamlit application.

## Prerequisites

1. A Google account
2. Your Streamlit application deployed on Streamlit Cloud

## Step 1: Google Cloud Console Setup

### 1.1 Create/Select a Project
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Make sure the project is selected in the top navigation

### 1.2 Enable Required APIs
1. Go to "APIs & Services" > "Library"
2. Search for and enable these APIs:
   - Google+ API
   - Google OAuth2 API

### 1.3 Create OAuth 2.0 Credentials
1. Go to "APIs & Services" > "Credentials"
2. Click "Create Credentials" > "OAuth 2.0 Client IDs"
3. Choose "Web application" as the application type
4. Fill in the details:
   - **Name**: Your app name (e.g., "Financial Planning Suite")
   - **Authorized JavaScript origins**: 
     - `https://your-app-name.streamlit.app`
     - `http://localhost:8501` (for local development)
   - **Authorized redirect URIs**:
     - `https://your-app-name.streamlit.app/`
     - `http://localhost:8501/` (for local development)
5. Click "Create"
6. **Save the Client ID and Client Secret** - you'll need these for the next step

## Step 2: Configure Streamlit Secrets

### 2.1 For Local Development
1. Create a `.streamlit/secrets.toml` file in your project root
2. Add your Google OAuth credentials:

```toml
GOOGLE_CLIENT_ID = "your_actual_client_id_here"
GOOGLE_CLIENT_SECRET = "your_actual_client_secret_here"
GOOGLE_REDIRECT_URI = "http://localhost:8501/"
```

### 2.2 For Streamlit Cloud Deployment
1. Go to your app on [share.streamlit.io](https://share.streamlit.io)
2. Click on your app
3. Go to "Settings" > "Secrets"
4. Add the following configuration:

```toml
GOOGLE_CLIENT_ID = "your_actual_client_id_here"
GOOGLE_CLIENT_SECRET = "your_actual_client_secret_here"
GOOGLE_REDIRECT_URI = "https://your-app-name.streamlit.app/"
```

## Step 3: Test the Authentication

1. Deploy your application to Streamlit Cloud
2. Visit your app URL
3. You should see a "Sign in with Google" button
4. Click the button and complete the OAuth flow
5. After successful authentication, you should see your name and email displayed

## Step 4: Customize Authentication (Optional)

### 4.1 Restrict Access by Domain
Edit `config.py` to restrict access to specific email domains:

```python
ALLOWED_DOMAINS = ["gmail.com", "yourcompany.com"]
```

### 4.2 Change Session Timeout
Edit `config.py` to modify session duration:

```python
SESSION_CONFIG = {
    "session_timeout": 7200,  # 2 hours in seconds
    "max_login_attempts": 3
}
```

### 4.3 Add More Users
Edit `config.py` to add more users with local authentication:

```python
AUTH_CONFIG = {
    "credentials": {
        "usernames": {
            "admin": {
                "email": "admin@example.com",
                "name": "Admin User",
                "password": "$2b$12$..."  # Use streamlit-authenticator to hash passwords
            },
            "user2": {
                "email": "user2@example.com",
                "name": "User 2",
                "password": "$2b$12$..."
            }
        }
    }
}
```

## Troubleshooting

### Common Issues:

1. **"Redirect URI mismatch" error**
   - Make sure the redirect URI in Google Cloud Console exactly matches your app URL
   - Include the trailing slash in the redirect URI

2. **"Client ID not found" error**
   - Verify that the Client ID in your secrets matches the one from Google Cloud Console
   - Check that you've enabled the required APIs

3. **"Domain not allowed" error**
   - Check the `ALLOWED_DOMAINS` list in `config.py`
   - Add your email domain if it's not in the list

4. **Authentication not working on Streamlit Cloud**
   - Make sure you've added the secrets in Streamlit Cloud settings
   - Verify that the redirect URI uses HTTPS for production

### Security Best Practices:

1. **Never commit secrets to version control**
   - Keep `.streamlit/secrets.toml` in your `.gitignore`
   - Use environment variables for sensitive data

2. **Use HTTPS in production**
   - Always use HTTPS URLs for redirect URIs in production
   - Google OAuth requires HTTPS for security

3. **Regularly rotate credentials**
   - Periodically update your OAuth credentials
   - Monitor for any suspicious activity

## Support

If you encounter issues:
1. Check the Streamlit Cloud logs for error messages
2. Verify all configuration steps were completed correctly
3. Test with a simple OAuth flow first before adding domain restrictions

## Files Modified/Created:

- `requirements.txt` - Added authentication packages
- `config.py` - Authentication configuration
- `utils/auth_utils.py` - Authentication utilities
- `Index.py` - Updated main page with authentication
- `.streamlit/secrets.toml` - Secrets template
- `AUTHENTICATION_SETUP.md` - This setup guide 