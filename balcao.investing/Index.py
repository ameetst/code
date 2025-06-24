import streamlit as st
import utils.auth_utils as auth_utils

st.set_page_config(
    page_title="Financial Planning Suite",
    page_icon="💰",
    layout="centered"
)

# Custom CSS for background and card style
st.markdown("""
    <style>
    .main {
        background: linear-gradient(135deg, #e0eafc 0%, #cfdef3 100%);
    }
    .card {
        background: white;
        border-radius: 1rem;
        box-shadow: 0 4px 24px 0 rgba(0,0,0,0.10);
        padding: 2rem;
        margin-bottom: 2rem;
    }
    .big-title {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1a237e;
        margin-bottom: 0.5rem;
    }
    .subtitle {
        font-size: 1.2rem;
        color: #3949ab;
        margin-bottom: 2rem;
    }
    .user-info {
        background: rgba(255, 255, 255, 0.9);
        border-radius: 0.5rem;
        padding: 1rem;
        margin-bottom: 1rem;
        border-left: 4px solid #4285f4;
    }
    </style>
""", unsafe_allow_html=True)

# AUTHENTICATION DISABLED - Uncomment the lines below to re-enable authentication
# # Handle OAuth callback
# auth_utils.handle_oauth_callback()

# # Check authentication status
# if auth_utils.is_authenticated():
#     # User is logged in
#     user_info = auth_utils.get_user_info()
    
#     if user_info:
#         # Display user info
#         st.markdown(f"""
#         <div class="user-info">
#             <strong>Welcome, {user_info['name']}!</strong><br>
#             <small>Email: {user_info['email']}</small>
#         </div>
#         """, unsafe_allow_html=True)
    
#     # Logout button
#     if st.button("🚪 Logout"):
#         auth_utils.logout()
    
#     st.markdown('<div class="big-title">💰 Financial Planning Suite</div>', unsafe_allow_html=True)
#     st.markdown('<div class="subtitle">Your one-stop solution for financial goal planning, retirement, and smart investing.</div>', unsafe_allow_html=True)

#     st.markdown("""
#     ### 📊 Available Tools

#     - **Retirement Corpus Calculator**  
#       _Calculate and visualize how long your retirement corpus will last._

#     - **Goal Planning**  
#       _Find out the annual SIP needed to achieve your future financial goals._

#     - **Dual Momentum Strategy**  
#       _Explore a classical dual momentum trading strategy with Indian ETFs._

#     - **Mutual Fund Analyzer**  
#       _Compare mutual funds across categories, analyze rolling and year-on-year returns, and identify consistent top performers._

#     ---
#     """)

#     st.markdown('<span style="color: #616161;">Select a tool from the sidebar to get started!</span>', unsafe_allow_html=True)
#     st.markdown('</div>', unsafe_allow_html=True)

# else:
#     # User is not logged in - show login page
#     st.markdown('<div class="big-title">💰 Financial Planning Suite</div>', unsafe_allow_html=True)
#     st.markdown('<div class="subtitle">Please sign in to access your financial planning tools.</div>', unsafe_allow_html=True)
    
#     st.markdown("""
#     ### 🔐 Secure Access
    
#     This application requires authentication to access sensitive financial planning tools.
#     Please sign in with your Google account to continue.
    
#     ---
#     """)
    
#     # Show Google login button
#     auth_utils.login_with_google()
    
#     st.markdown("""
#     ### 📊 Available Tools (After Login)
    
#     - **Retirement Corpus Calculator**  
#       _Calculate and visualize how long your retirement corpus will last._
    
#     - **Goal Planning**  
#       _Find out the annual SIP needed to achieve your future financial goals._
    
#     - **Dual Momentum Strategy**  
#       _Explore a classical dual momentum trading strategy with Indian ETFs._
    
#     - **Mutual Fund Analyzer**  
#       _Compare mutual funds across categories, analyze rolling and year-on-year returns, and identify consistent top performers._
#     """)

# AUTHENTICATION DISABLED - Showing main content directly
st.markdown('<div class="big-title">💰 Financial Planning Suite</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Your one-stop solution for financial goal planning, retirement, and smart investing.</div>', unsafe_allow_html=True)

st.markdown("""
### 📊 Available Tools

- **Retirement Corpus Calculator**  
  _Calculate and visualize how long your retirement corpus will last._

- **Goal Planning**  
  _Find out the annual SIP needed to achieve your future financial goals._

- **Dual Momentum Strategy**  
  _Explore a classical dual momentum trading strategy with Indian ETFs._

- **Mutual Fund Analyzer**  
  _Compare mutual funds across categories, analyze rolling and year-on-year returns, and identify consistent top performers._

---
""")

st.markdown('<span style="color: #616161;">Select a tool from the sidebar to get started!</span>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# Authentication status indicator (for development purposes)
st.sidebar.markdown("---")
st.sidebar.markdown("**🔧 Development Mode**")
st.sidebar.markdown("*Authentication disabled*")
st.sidebar.markdown("*All files preserved*")