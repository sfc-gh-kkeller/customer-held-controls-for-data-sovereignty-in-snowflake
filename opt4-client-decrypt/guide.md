# Streamlit Native Authentication Setup Guide

This guide will help you set up your Streamlit app with native OIDC authentication.

## 🔄 Changes Made

The app has been refactored to use Streamlit's native authentication capabilities:

### ✅ **Removed**
- Custom `AuthManager` class
- `streamlit_authenticator` dependency
- Local user management with hardcoded passwords
- Custom JWT handling
- Manual session state management for auth

### ✅ **Added**
- Streamlit native authentication integration
- Role-based access control using email mapping
- Permission system for different user roles
- Environment variable support for OIDC config
- Secure authentication flow handling

### ✅ **Enhanced**
- Better security with role-based permissions
- Cleaner code without custom auth logic
- Production-ready OIDC integration
- Fallback test authentication for development

## 📋 Requirements

Create a `requirements.txt` file:

```txt
streamlit>=1.28.0
pandas>=1.5.0
duckdb>=0.8.0
psycopg2-binary>=2.9.0
adbc-driver-snowflake>=0.5.0
plotly>=5.15.0
sqlalchemy>=1.4.0
PyYAML>=6.0
cryptography>=3.4.0
pyarrow>=12.0.0
requests>=2.28.0
numpy>=1.21.0
```

## 🔧 Setup Steps

### 1. **Choose Your Authentication Provider**

Popular OIDC providers:
- **Okta** - Enterprise identity management
- **Auth0** - Developer-friendly auth platform
- **Azure AD** - Microsoft's identity platform
- **Google** - Google Workspace/Cloud Identity
- **Keycloak** - Open-source identity management

### 2. **Configure Your OIDC Provider**

#### For Okta:
1. Log into your Okta Admin Console
2. Go to Applications → Create App Integration
3. Choose "OIDC - OpenID Connect" and "Web Application"
4. Set redirect URI: `https://your-app.com/auth/callback`
5. Note down Client ID and Client Secret

#### For Auth0:
1. Log into Auth0 Dashboard
2. Go to Applications → Create Application
3. Choose "Regular Web Applications"
4. Set Allowed Callback URLs: `https://your-app.com/auth/callback`
5. Note down Client ID and Client Secret

#### For Azure AD:
1. Go to Azure Portal → Azure Active Directory
2. App registrations → New registration
3. Set redirect URI: `https://your-app.com/auth/callback`
4. Go to Certificates & secrets → New client secret
5. Note down Application (client) ID and client secret

### 3. **Configure Streamlit Authentication**

Create `.streamlit/config.toml`:

```toml
[server]
enableCORS = false

[auth]
providers = ["oidc"]

[auth.oidc]
issuer = "https://your-provider.com"
clientId = "your-client-id"
clientSecret = "your-client-secret"
redirectUri = "https://your-app.com/auth/callback"
scopes = ["openid", "profile", "email"]
```

**Or use environment variables:**

```bash
export STREAMLIT_AUTH_PROVIDERS="oidc"
export STREAMLIT_AUTH_OIDC_ISSUER="https://your-provider.com"
export STREAMLIT_AUTH_OIDC_CLIENT_ID="your-client-id"
export STREAMLIT_AUTH_OIDC_CLIENT_SECRET="your-client-secret"
export STREAMLIT_AUTH_OIDC_REDIRECT_URI="https://your-app.com/auth/callback"
export STREAMLIT_AUTH_OIDC_SCOPES="openid,profile,email"
```

### 4. **Customize User Role Mapping**

Edit the `get_user_role()` function in your app:

```python
def get_user_role(email: str, config: CacheConfig) -> str:
    """Map user email to role based on your organization's needs"""
    
    # Option 1: Email-based mapping
    admin_emails = ["admin@yourcompany.com", "cto@yourcompany.com"]
    analyst_emails = ["analyst@yourcompany.com", "data@yourcompany.com"]
    
    if email in admin_emails:
        return "admin"
    elif email in analyst_emails:
        return "analyst"
    
    # Option 2: Domain-based mapping
    domain = email.split('@')[1] if '@' in email else ''
    if domain == "yourcompany.com":
        if "admin" in email or "manager" in email:
            return "admin"
        elif "analyst" in email or "data" in email:
            return "analyst"
        else:
            return "viewer"
    
    # Option 3: OIDC groups/claims (if available in token)
    # This would require parsing additional claims from the OIDC token
    
    return "viewer"  # Default role
```

### 5. **Configure Role Permissions**

Edit `cache_config.yaml`:

```yaml
roles:
  admin:
    permissions: ["read", "write", "admin", "purge_cache", "manage_config"]
    description: "Full administrative access"
  analyst:
    permissions: ["read", "write"]
    description: "Data analysis and querying"
  viewer:
    permissions: ["read"]
    description: "Read-only access"
  
# Custom roles for your organization
manager:
  permissions: ["read", "write", "purge_cache"]
  description: "Team manager with cache management"

contractor:
  permissions: ["read"]
  description: "External contractor with limited access"
```

## 🚀 Deployment Options

### **Streamlit Cloud**
1. Push your app to GitHub
2. Connect to Streamlit Cloud
3. Add environment variables in the app settings
4. Set redirect URI to: `https://your-app.streamlit.app/auth/callback`

### **Docker Deployment**
```dockerfile
FROM python:3.9-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Set environment variables
ENV STREAMLIT_AUTH_PROVIDERS=oidc
ENV STREAMLIT_AUTH_OIDC_ISSUER=https://your-provider.com
ENV STREAMLIT_AUTH_OIDC_CLIENT_ID=your-client-id
ENV STREAMLIT_AUTH_OIDC_CLIENT_SECRET=your-client-secret

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

### **Kubernetes Deployment**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: snowflake-analytics
spec:
  replicas: 2
  selector:
    matchLabels:
      app: snowflake-analytics
  template:
    metadata:
      labels:
        app: snowflake-analytics
    spec:
      containers:
      - name: app
        image: your-registry/snowflake-analytics:latest
        ports:
        - containerPort: 8501
        env:
        - name: STREAMLIT_AUTH_PROVIDERS
          value: "oidc"
        - name: STREAMLIT_AUTH_OIDC_ISSUER
          valueFrom:
            secretKeyRef:
              name: auth-secrets
              key: issuer
        - name: STREAMLIT_AUTH_OIDC_CLIENT_ID
          valueFrom:
            secretKeyRef:
              name: auth-secrets
              key: client-id
        - name: STREAMLIT_AUTH_OIDC_CLIENT_SECRET
          valueFrom:
            secretKeyRef:
              name: auth-secrets
              key: client-secret
```

## 🧪 Testing

### **Development Mode**
The app includes test authentication for development:

```python
# Test users available in development mode
if st.button("🔓 Test Login (Admin)"):
    st.session_state["authentication_status"] = True
    st.session_state["name"] = "Test Admin"
    st.session_state["email"] = "admin@yourcompany.com"
    st.rerun()
```

### **Production Testing**
1. Set up a test user in your OIDC provider
2. Configure the test user's email in your role mapping
3. Test the full authentication flow
4. Verify role-based access controls work correctly

## 🔐 Security Considerations

### **Environment Variables**
- Never commit secrets to version control
- Use proper secret management in production
- Rotate client secrets regularly

### **HTTPS Requirements**
- OIDC requires HTTPS in production
- Configure proper SSL certificates
- Use secure redirect URIs

### **Role Security**
- Implement principle of least privilege
- Regularly audit user roles and permissions
- Log authentication and authorization events

### **Data Protection**
- Encryption keys are hidden from regular users
- Cache expiration prevents data accumulation
- Role-based access to sensitive operations

## 🐛 Troubleshooting

### **Common Issues**

1. **"Authentication not configured"**
   - Check your OIDC provider configuration
   - Verify client ID and secret are correct
   - Ensure redirect URI matches exactly

2. **"Access denied" for legitimate users**
   - Check the `get_user_role()` function
   - Verify user email is mapped to correct role
   - Check role permissions in config

3. **CORS errors**
   - Set `enableCORS = false` in config.toml
   - Check your OIDC provider's CORS settings

4. **Redirect URI mismatch**
   - Ensure redirect URI in config matches provider
   - Check for trailing slashes and protocol (http/https)

### **Debug Mode**
Add logging to debug authentication issues:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Add to your app
st.write("Debug Info:")
st.write("Authentication Status:", st.session_state.get("authentication_status"))
st.write("User Email:", st.session_state.get("email"))
st.write("User Role:", get_user_role(st.session_state.get("email", ""), config))
```

## 📚 Resources

- [Streamlit Authentication Documentation](https://docs.streamlit.io/library/advanced-features/authentication)
- [OIDC Specification](https://openid.net/connect/)
- [OAuth 2.0 Guide](https://auth0.com/intro-to-iam/what-is-oauth-2)
- [Okta OIDC Setup](https://developer.okta.com/docs/guides/sign-into-web-app-overview/)
- [Auth0 Integration Guide](https://auth0.com/docs/quickstart/webapp)

## 🆘 Support

If you encounter issues:

1. Check Streamlit's authentication documentation
2. Review your OIDC provider's integration guides
3. Test with development mode first
4. Verify environment variables and configuration
5. Check application logs for detailed error messages

The refactored app provides enterprise-grade authentication with role-based access control while maintaining all the existing Snowflake analytics functionality.
