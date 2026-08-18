# Client Deployment Guide

## Deploy to New Windows Machine

### Prerequisites
- Windows 10/11
- Network access to license server at `http://192.168.6.10:8000`
- Administrator privileges (required for Registry configuration)

### Deployment Steps

#### 1. Copy Application Files
Copy the entire `ChineseVideoLocalizationAI` folder from `dist\ChineseVideoLocalizationAI\` to the target machine.

#### 2. Configure License Server URL
Run as Administrator:
```cmd
cd ChineseVideoLocalizationAI
set_license_server.bat http://192.168.6.10:8000
```

This sets `LICENSE_SERVER_URL` in Windows Registry:
- Registry Key: `HKLM\SOFTWARE\ChineseVideoLocalizationAI\LICENSE_SERVER_URL`
- Value: `http://192.168.6.10:8000`

**Important:** The application only reads LICENSE_SERVER_URL from Windows Registry or system environment variable. It does NOT read from .env file to prevent user modification.

#### 3. Setup Environment
```cmd
setup_env.bat
```

This creates the `.env` file with required configuration (excluding LICENSE_SERVER_URL).

#### 4. Run Application
```cmd
ChineseVideoLocalizationAI.exe
```

#### 5. Activate License
1. Open the web interface (usually opens automatically in browser)
2. Find the license input field
3. Enter the license key obtained from the license server admin panel
4. Click "Kích hoạt License" (Activate License)

### License Server Admin Panel
Access the admin panel at: `http://192.168.6.10:8000/static/admin.html`

From here you can:
- Create new licenses
- View existing licenses
- Edit license details
- Revoke licenses
- View usage statistics

### Troubleshooting

#### Application not connecting to license server
1. Verify network connectivity: `ping 192.168.6.10`
2. Check LICENSE_SERVER_URL in Registry:
   ```cmd
   reg query "HKLM\SOFTWARE\ChineseVideoLocalizationAI" /v LICENSE_SERVER_URL
   ```
3. Verify license server is running:
   ```cmd
   curl http://192.168.6.10:8000/
   ```

#### License validation fails
1. Verify license key is correct
2. Check license status in admin panel (active, not expired, not revoked)
3. Verify machine ID matches (if license is machine-bound)

#### Registry configuration failed
- Ensure running Command Prompt as Administrator
- Check if Registry key exists: `reg query "HKLM\SOFTWARE\ChineseVideoLocalizationAI"`
- If missing, run set_license_server.bat again as Administrator

### Security Notes

- LICENSE_SERVER_URL is stored in Windows Registry (HKLM) - requires admin to modify
- License keys are stored in local SQLite database, not in configuration files
- Application does NOT read LICENSE_SERVER_URL from .env file
- License server uses HTTP - consider upgrading to HTTPS for production

### Alternative Configuration Methods

If Windows Registry is not preferred, you can also set LICENSE_SERVER_URL as a system environment variable:

1. Right-click "This PC" → Properties → Advanced system settings
2. Environment Variables → System variables → New
3. Variable name: `LICENSE_SERVER_URL`
4. Variable value: `http://192.168.6.10:8000`
5. Restart the application

Priority order: Windows Registry > System Environment Variable
