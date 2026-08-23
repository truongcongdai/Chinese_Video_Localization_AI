# Hướng dẫn triển khai User Management Server

## Tổng quan

User Management Server chạy cùng với License Server trên server 192.168.6.10:
- **License Server**: Port 8000 - Quản lý licenses
- **User Management Server**: Port 8001 - Quản lý user accounts
- **Database**: SQLite (users.db)

## Bước 1: Upload files lên server

```bash
# Upload user management server
scp license_server/user_management_server.py root@192.168.6.10:/opt/license-server/

# Upload updated requirements.txt
scp license_server/requirements.txt root@192.168.6.10:/opt/license-server/

# Upload systemd service file
scp license_server/user_management_server.service root@192.168.6.10:/tmp/
```

## Bước 2: Cài đặt dependencies trên server

```bash
ssh root@192.168.6.10
cd /opt/license-server
pip install -r requirements.txt
```

## Bước 3: Cài đặt systemd service

```bash
# Copy service file
sudo cp /tmp/user_management_server.service /etc/systemd/system/

# Enable và start service
sudo systemctl daemon-reload
sudo systemctl enable user-management-server
sudo systemctl restart user-management-server

# Check status
sudo systemctl status user-management-server
```

## Bước 4: Kiểm tra server

```bash
# Test từ server
curl http://localhost:8001/

# Test từ máy local
curl http://192.168.6.10:8001/
```

## API Endpoints

### User Registration
```http
POST /api/users/register
Content-Type: application/json

{
  "username": "testuser",
  "email": "test@example.com",
  "password": "password123",
  "referral_code": "ABCDEFG"
}
```

`referral_code` là tùy chọn. Khi hợp lệ, server cộng mặc định 5 credit cho cả
người mời và người đăng ký trong cùng một transaction. Có thể cấu hình bằng
`REFERRAL_BONUS_CREDITS`; nên giữ giá trị này là `5` trên cả server và EXE.

### User Login
```http
POST /api/users/login
Content-Type: application/json

{
  "username": "testuser",
  "password": "password123"
}
```

### Request Password Reset
```http
POST /api/users/reset-password
Content-Type: application/json

{
  "email": "test@example.com"
}
```

### Confirm Password Reset
```http
POST /api/users/reset-password/confirm
Content-Type: application/json

{
  "token": "reset_token_here",
  "new_password": "newpassword123"
}
```

### List All Users (Admin)
```http
GET /api/users
```

### Get User by ID
```http
GET /api/users/{user_id}
```

### Update User (Admin)
```http
PUT /api/users/{user_id}
Content-Type: application/json

{
  "credits": 100,
  "is_admin": false
}
```

### Delete User (Admin)
```http
DELETE /api/users/{user_id}
```

## Bước 5: Cấu hình Web UI để sync với User Management Server

Cần sửa code web UI để:
1. Gọi API `/api/users/register` khi user đăng ký
2. Gọi API `/api/users/login` khi user đăng nhập
3. Gọi API `/api/users/reset-password` khi quên mật khẩu
4. Sync local data với server định kỳ

## Troubleshooting

### Service không chạy
```bash
sudo systemctl status user-management-server
sudo journalctl -u user-management-server -f
```

### Port conflict
```bash
sudo lsof -i :8001
```

### Database issues
```bash
cd /opt/license-server
ls -la users.db
sqlite3 users.db "SELECT * FROM users;"
```

## Security Notes

- Server hiện chạy HTTP, nên cân nhắc upgrade sang HTTPS
- Password được hash với bcrypt
- Reset token có expiry 1 giờ
- Cần thêm authentication cho admin endpoints
