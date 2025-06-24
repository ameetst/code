# Local Development Guide

This guide will help you run the Financial Planning Suite locally with Google authentication.

## Prerequisites

1. Python 3.8 or higher
2. pip (Python package installer)
3. A Google account for OAuth setup

## Step 1: Install Dependencies

1. Navigate to your project directory:
```bash
cd balcao.investing
```

2. Install the required packages:
```bash
pip install -r requirements.txt
```

## Step 2: Set Up Google OAuth for Local Development

### 2.1 Google Cloud Console Setup
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the required APIs:
   - Go to "APIs & Services" > "Library"
   - Search for and enable "Google+ API" and "Google OAuth2 API"

### 2.2 Create OAuth Credentials
1. Go to "APIs & Services" > "Credentials"
2. Click "Create Credentials" > "OAuth 2.0 Client IDs"
3. Choose "Web application"
4. Fill in the details:
   - **Name**: "Financial Planning Suite (Local)"
   - **Authorized JavaScript origins**: 
     - `http://localhost:8501`
   - **Authorized redirect URIs**:
     - `http://localhost:8501/`
5. Click "Create"
6. **Save the Client ID and Client Secret**

### 2.3 Configure Local Secrets
1. Edit `.streamlit/secrets.toml`
2. Replace the placeholder values with your actual credentials:

```toml
GOOGLE_CLIENT_ID = "your_actual_client_id_here"
GOOGLE_CLIENT_SECRET = "your_actual_client_secret_here"
GOOGLE_REDIRECT_URI = "http://localhost:8501/"
```

## Step 3: Run the Application

1. Start the Streamlit application:
```bash
streamlit run Index.py
```

2. Open your browser and go to: `http://localhost:8501`

3. You should see the login page with a "Sign in with Google" button

## Step 4: Test Authentication

1. Click the "Sign in with Google" button
2. Complete the OAuth flow with your Google account
3. After successful authentication, you should see:
   - Your name and email displayed
   - Access to all the financial planning tools
   - A logout button

## Troubleshooting Local Development

### Common Issues:

1. **"Module not found" errors**
   ```bash
   pip install -r requirements.txt
   ```

2. **"Redirect URI mismatch" error**
   - Make sure `http://localhost:8501/` is added to authorized redirect URIs in Google Cloud Console
   - Include the trailing slash

3. **"Client ID not found" error**
   - Verify your Client ID in `.streamlit/secrets.toml`
   - Make sure the file is in the correct location

4. **Port already in use**
   ```bash
   streamlit run Index.py --server.port 8502
   ```

5. **Authentication not working**
   - Check that all required APIs are enabled in Google Cloud Console
   - Verify your OAuth credentials are correct
   - Check the browser console for any JavaScript errors

### Development Tips:

1. **Hot Reload**: Streamlit automatically reloads when you save changes to your code

2. **Debug Mode**: Add this to see more detailed error messages:
   ```bash
   streamlit run Index.py --logger.level debug
   ```

3. **Custom Port**: If port 8501 is busy:
   ```bash
   streamlit run Index.py --server.port 8502
   ```

4. **Clear Cache**: If you encounter caching issues:
   ```bash
   streamlit cache clear
   ```

## File Structure for Local Development

```
balcao.investing/
├── Index.py                    # Main application entry point
├── config.py                   # Authentication configuration
├── requirements.txt            # Python dependencies
├── .streamlit/
│   └── secrets.toml           # Local OAuth credentials
├── utils/
│   ├── __init__.py
│   ├── auth_utils.py          # Authentication utilities
│   ├── _mfapi_utils.py        # Mutual fund API utilities
│   └── _mutual_fund_analysis_utils.py
├── pages/
│   ├── 1_Retirement Corpus Calculator.py
│   ├── 2_Goal Planning.py
│   ├── 3_Dual Momentum Strategy.py
│   ├── 4_Mutual Fund Analyzer.py
│   ├── FC.txt
│   ├── FX.txt
│   ├── MC.txt
│   └── SC.txt
└── LOCAL_DEVELOPMENT.md        # This file
```

## Security Notes for Local Development

1. **Never commit secrets**: Make sure `.streamlit/secrets.toml` is in your `.gitignore`
2. **Use HTTP for local development**: Google OAuth allows HTTP for localhost
3. **Test with different accounts**: Try logging in with different Google accounts to test the flow

## Next Steps

Once local development is working:
1. Test all the financial planning tools
2. Customize the authentication settings in `config.py`
3. Deploy to Streamlit Cloud following the `AUTHENTICATION_SETUP.md` guide

## Support

If you encounter issues:
1. Check the terminal output for error messages
2. Verify all configuration steps were completed
3. Test with a simple OAuth flow first
4. Check the browser's developer console for any JavaScript errors 