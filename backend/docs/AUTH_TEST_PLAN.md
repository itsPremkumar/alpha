# Auth Module Test Plan

## Test Matrix

| Mode | Launch Command | Auth Layer | Port |
|------|---------|---------|------|
| Standard Mode | `make dev` | Gateway AuthMiddleware (Full) | 2026 (nginx) |
| Direct Gateway | `cd backend && make gateway` | Gateway AuthMiddleware | 8001 |
| Direct LangGraph Compatibility | Used when manually running LangGraph toolchain | LangGraph auth | 2024 |

`make dev`, Docker dev, and production deployments all run Gateway embedded runtime by default.
`app.gateway.langgraph_auth` is only used for preserved direct LangGraph toolchain / Studio compatibility tests, not standard service startup.

The following tests must be executed under each mode.

---

## 1. Environment Preparation

### 1.1 First Boot (Clean Database)

```bash
# Clear existing data
rm -f backend/.agent-workspace/data/agent_workspace.db

# Start standard mode (Gateway embedded runtime)
make dev
```

**Verification Points:**
- [ ] Console does not print admin email or plaintext password
- [ ] Console displays `First boot detected — no admin account exists.`
- [ ] Console prompts to visit `/setup` to complete admin creation
- [ ] `GET /api/v1/auth/setup-status` returns `{"needs_setup": true}`
- [ ] Visiting `/login` redirects to `/setup`

### 1.2 Non-First Boot

```bash
# Start directly without clearing database
make dev
```

**Verification Points:**
- [ ] Console does not print password
- [ ] `GET /api/v1/auth/setup-status` returns `{"needs_setup": false}`
- [ ] Logged-in users with `needs_setup=True` visiting workspace are redirected to `/setup` to complete email/password setup

### 1.3 Environment Variable Configuration

| Variable | Verification |
|------|------|
| `AUTH_JWT_SECRET` unconfigured | Startup warning, automatically generates ephemeral key |
| `AUTH_JWT_SECRET` configured | No warning, sessions persist across restart |

---

## 2. API Flow Tests

> Below uses `BASE=http://localhost:2026` as an example. Standard mode exposes this address through nginx.
> Direct connection tests should substitute the corresponding port.
>
> **CSRF Token Extraction**: Multiple places require extracting the CSRF token from the cookie jar; uniformly use:
> ```bash
> CSRF=$(python3 -c "
> import http.cookiejar
> cj = http.cookiejar.MozillaCookieJar('cookies.txt'); cj.load()
> print(next(c.value for c in cj if c.name == 'csrf_token'))
> ")
> ```
> Or shorthand (sufficient for most scenarios): `CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')`

### 2.1 Registration + Login + Session

#### TC-API-01: Setup Status Query

```bash
curl -s $BASE/api/v1/auth/setup-status | jq .
```

**Expected:**
- Clean database with no admin initialized: returns `{"needs_setup": true}`
- Admin already exists: returns `{"needs_setup": false}`

#### TC-API-02: Initial Admin Account Creation

```bash
curl -s -X POST $BASE/api/v1/auth/initialize \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"AdminPass1!"}' \
  -c cookies.txt | jq .
```

**Expected:**
- Status code 201
- Body: `{"id": "...", "email": "admin@example.com", "system_role": "admin", "needs_setup": false}`
- `cookies.txt` contains `access_token` (HttpOnly) and `csrf_token` (non-HttpOnly)

#### TC-API-03: Get Current User Profile

```bash
curl -s $BASE/api/v1/auth/me -b cookies.txt | jq .
```

**Expected:** `{"id": "...", "email": "admin@example.com", "system_role": "admin", "needs_setup": false}`

#### TC-API-04: Password Change Flow

```bash
CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/v1/auth/change-password \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"current_password":"AdminPass1!","new_password":"NewPass123!"}' | jq .
```

**Expected:**
- Status code 200
- `{"message": "Password changed successfully"}`
- Calling `/auth/me` again still returns `admin@example.com`, `needs_setup` is still `false`

#### TC-API-04a: Setup Flow After reset_admin (Change Email + Password)

```bash
cd backend
python -m app.gateway.auth.reset_admin --email admin@example.com
# Read reset password from .agent-workspace/admin_initial_credentials.txt

curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=<credential_file_password>" \
  -c cookies.txt | jq .

CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/v1/auth/change-password \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"current_password":"<credential_file_password>","new_password":"AdminPass2!","new_email":"admin2@example.com"}' | jq .
```

**Expected:**
- Login returns `{"expires_in": 604800, "needs_setup": true}`
- After `change-password`, `/auth/me` email changes to `admin2@example.com`, `needs_setup` becomes `false`

#### TC-API-05: Standard User Registration

```bash
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user1@example.com","password":"UserPass1!"}' \
  -c user_cookies.txt | jq .
```

**Expected:** Status code 201, `system_role` is `"user"`, automatically logged in (cookies set)

#### TC-API-06: Logout

```bash
curl -s -X POST $BASE/api/v1/auth/logout -b cookies.txt | jq .
```

**Expected:** `{"message": "Successfully logged out"}`, subsequent access to `/auth/me` with cookies.txt returns 401

### 2.2 Multi-Tenant Isolation

#### TC-API-07: User A Creates Thread

```bash
# Login as user1
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=user1@example.com&password=UserPass1!" \
  -c user1.txt

CSRF1=$(grep csrf_token user1.txt | awk '{print $NF}')

# Create thread
curl -s -X POST $BASE/api/threads \
  -b user1.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF1" \
  -d '{"metadata":{}}' | jq .thread_id
# Record THREAD_ID
```

#### TC-API-08: User B Cannot Access User A's Thread

```bash
# Register and login user2
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user2@example.com","password":"UserPass2!"}' \
  -c user2.txt

# Attempt to access user1's thread
curl -s $BASE/api/threads/$THREAD_ID -b user2.txt
```

**Expected:** Status code 404 (not 403, preventing leakage of thread existence)

#### TC-API-09: User B Searching Threads Cannot See User A's

```bash
CSRF2=$(grep csrf_token user2.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/threads/search \
  -b user2.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF2" \
  -d '{}' | jq length
```

**Expected:** Returns 0 or only contains user2's own threads

### 2.3 LangGraph-Compatible Gateway Route Isolation

#### TC-API-10: LangGraph-Compatible Endpoints Require Cookie

```bash
# Access LangGraph-compatible endpoint without cookie
curl -s -w "%{http_code}" $BASE/api/langgraph/threads
```

**Expected:** 401

#### TC-API-11: LangGraph-Compatible Routes Accessible With Cookie

```bash
curl -s $BASE/api/langgraph/threads -b user1.txt | jq length
```

**Expected:** 200, returns user1's thread list

#### TC-API-12: LangGraph-Compatible Route Isolation — Users Only See Their Own

```bash
# user2 queries threads
curl -s $BASE/api/langgraph/threads -b user2.txt | jq length
```

**Expected:** Does not contain user1's threads

### 2.4 Token Invalidation

#### TC-API-13: Old Token Immediately Invalidated After Password Change

```bash
# Save current cookie
cp user1.txt user1_old.txt

# Change password
CSRF1=$(grep csrf_token user1.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/v1/auth/change-password \
  -b user1.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF1" \
  -d '{"current_password":"UserPass1!","new_password":"NewUserPass1!"}' \
  -c user1.txt

# Access using old cookie
curl -s -w "%{http_code}" $BASE/api/v1/auth/me -b user1_old.txt
```

**Expected:** 401 (token_version mismatch)

#### TC-API-14: New Cookie Valid After Password Change

```bash
curl -s $BASE/api/v1/auth/me -b user1.txt | jq .email
```

**Expected:** 200, returns user info

### 2.5 Error Response Formatting

#### TC-API-15: Structured Error Response Format

```bash
# Login with wrong password
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=wrong" | jq .detail
```

**Expected:**
```json
{"code": "invalid_credentials", "message": "Incorrect email or password"}
```

#### TC-API-16: Duplicate Email Registration

```bash
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user1@example.com","password":"AnyPass123"}' -w "\n%{http_code}"
```

**Expected:** 400, `{"code": "email_already_exists", ...}`

---

## 3. Attack & Penetration Tests

### 3.1 Brute-Force Protection

#### TC-ATK-01: IP Rate Limiting

```bash
# 6 consecutive wrong password attempts
for i in $(seq 1 6); do
  echo "Attempt $i:"
  curl -s -X POST $BASE/api/v1/auth/login/local \
    -d "username=admin@example.com&password=wrong$i" -w " HTTP %{http_code}\n"
done
```

**Expected:** First 5 attempts return 401, 6th attempt returns 429 `"Too many login attempts. Try again later."`

#### TC-ATK-02: Correct Password Rejected During Lockout

```bash
# Follow up from previous step
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" -w " HTTP %{http_code}\n"
```

**Expected:** 429 (locked out for 5 minutes)

#### TC-ATK-03: Successful Login Resets Rate Limit Counter

```bash
# After lockout expires (or restart), login with correct password
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" -w " HTTP %{http_code}\n"
```

**Expected:** 200, counter reset

### 3.2 CSRF Protection

#### TC-ATK-04: POST Request Without CSRF Token

```bash
curl -s -X POST $BASE/api/threads \
  -b user1.txt \
  -H "Content-Type: application/json" \
  -d '{"metadata":{}}' -w "\nHTTP %{http_code}"
```

**Expected:** 403 `"CSRF token missing"`

#### TC-ATK-05: Mismatched CSRF Token

```bash
curl -s -X POST $BASE/api/threads \
  -b user1.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: fake-token" \
  -d '{"metadata":{}}' -w "\nHTTP %{http_code}"
```

**Expected:** 403 `"CSRF token mismatch"`

### 3.3 Cookie Security

> HTTP vs HTTPS behavioral differences are simulated via `X-Forwarded-Proto: https`.
> **Note:** When proxied through nginx, nginx's `proxy_set_header X-Forwarded-Proto $scheme` will overwrite
> the client-sent value (`$scheme` = nginx listener scheme), so HTTPS simulation must **directly connect to Gateway (Port 8001)**.
> Each case needs to be validated once on both the **login** and **register** endpoints.

#### TC-ATK-06: HTTP Mode Cookie Attributes

```bash
# Login
curl -s -D - -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" 2>/dev/null | grep -i set-cookie
```

**Expected:**
- `access_token`: `HttpOnly; Path=/; SameSite=lax`, no `Secure`, no `Max-Age`
- `csrf_token`: `Path=/; SameSite=strict`, no `HttpOnly` (JS needs to read it), no `Secure`

```bash
# Register
curl -s -D - -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"cookie-http@example.com","password":"CookieTest1!"}' 2>/dev/null | grep -i set-cookie
```

**Expected:** Same as above

#### TC-ATK-07: HTTPS Mode Cookie Attributes

> **Must directly connect to Gateway** (`GW=http://localhost:8001`); going through nginx will be overwritten by `$scheme`.

```bash
GW=http://localhost:8001

# Login (simulate HTTPS)
curl -s -D - -X POST $GW/api/v1/auth/login/local \
  -H "X-Forwarded-Proto: https" \
  -d "username=admin@example.com&password=CorrectPassword" 2>/dev/null | grep -i set-cookie
```

**Expected:**
- `access_token`: `HttpOnly; Secure; Path=/; SameSite=lax; Max-Age=604800`
- `csrf_token`: `Secure; Path=/; SameSite=strict`, no `HttpOnly`

```bash
# Register (simulate HTTPS)
curl -s -D - -X POST $GW/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -H "X-Forwarded-Proto: https" \
  -d '{"email":"cookie-https@example.com","password":"CookieTest1!"}' 2>/dev/null | grep -i set-cookie
```

**Expected:** Same as above

#### TC-ATK-07a: HTTP vs HTTPS Cookie Differences

> Execute directly against Gateway to prevent nginx from overwriting `X-Forwarded-Proto`.

```bash
GW=http://localhost:8001

for proto in "" "https"; do
  HEADER=""
  LABEL="HTTP"
  if [ -n "$proto" ]; then
    HEADER="-H X-Forwarded-Proto:$proto"
    LABEL="HTTPS"
  fi
  echo "=== $LABEL ==="
  EMAIL="compare-${LABEL,,}-$(date +%s)@example.com"
  curl -s -D - -X POST $GW/api/v1/auth/register \
    -H "Content-Type: application/json" $HEADER \
    -d "{\"email\":\"$EMAIL\",\"password\":\"Compare1!\"}" 2>/dev/null | grep -i set-cookie | while read line; do
    if echo "$line" | grep -q "access_token="; then
      echo "  access_token:"
      echo "    HttpOnly: $(echo "$line" | grep -qi httponly && echo YES || echo NO)"
      echo "    Secure:   $(echo "$line" | grep -qi "secure" && echo "$line" | grep -v samesite | grep -qi secure && echo YES || echo NO)"
      echo "    Max-Age:  $(echo "$line" | grep -oi "max-age=[0-9]*" || echo NONE)"
      echo "    SameSite: $(echo "$line" | grep -oi "samesite=[a-z]*")"
    fi
    if echo "$line" | grep -q "csrf_token="; then
      echo "  csrf_token:"
      echo "    HttpOnly: $(echo "$line" | grep -qi httponly && echo YES || echo NO)"
      echo "    Secure:   $(echo "$line" | grep -qi "secure" && echo "$line" | grep -v samesite | grep -qi secure && echo YES || echo NO)"
      echo "    SameSite: $(echo "$line" | grep -oi "samesite=[a-z]*")"
    fi
  done
done
```

**Expected Comparison Table:**

| Attribute | HTTP access_token | HTTPS access_token | HTTP csrf_token | HTTPS csrf_token |
|------|------|------|------|------|
| HttpOnly | Yes | Yes | No | No |
| Secure | No | **Yes** | No | **Yes** |
| SameSite | Lax | Lax | Strict | Strict |
| Max-Age | None (session cookie) | **604800** (7 days) | None | None |

### 3.4 Unauthorized Access Prevention

#### TC-ATK-08: Access Protected Endpoints Without Cookie

```bash
for path in /api/models /api/mcp/config /api/memory /api/skills \
            /api/agents /api/channels; do
  echo "$path: $(curl -s -w '%{http_code}' -o /dev/null $BASE$path)"
done
```

**Expected:** All return 401

#### TC-ATK-09: Forged JWT Signature

```bash
# Token signed with different secret
FAKE_TOKEN=$(python3 -c "
import jwt
print(jwt.encode({'sub':'admin-id','ver':0,'exp':9999999999}, 'wrong-secret', algorithm='HS256'))
")

curl -s -w "%{http_code}" $BASE/api/v1/auth/me \
  --cookie "access_token=$FAKE_TOKEN"
```

**Expected:** 401 (signature verification failed)

#### TC-ATK-10: Expired JWT

```bash
# Standalone test with expired token signed by random secret
# Expired tokens are rejected regardless of secret matching
EXPIRED_TOKEN=$(python3 -c "
import jwt, time
print(jwt.encode({'sub':'x','ver':0,'exp':int(time.time())-100}, 'any-secret-32chars-placeholder!!', algorithm='HS256'))
")

curl -s -w "%{http_code}" -o /dev/null $BASE/api/v1/auth/me \
  --cookie "access_token=$EXPIRED_TOKEN"
```

**Expected:** 401 (expired or signature mismatch, both rejected)

### 3.5 Password Security

#### TC-ATK-11: Password Too Short

```bash
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"short@example.com","password":"1234567"}' -w "\nHTTP %{http_code}"
```

**Expected:** 422 (Pydantic validation: min_length=8)

#### TC-ATK-12: Passwords Not Stored In Plaintext

```bash
# Inspect database
sqlite3 backend/.agent-workspace/data/agent_workspace.db "SELECT email, password_hash FROM users LIMIT 3;"
```

**Expected:** `password_hash` starts with `$2b$` (bcrypt format)

---

## 4. UI Operation Tests

> Operated in the browser to verify frontend-backend linkage.

### 4.1 Initial Login Flow

#### TC-UI-01: Visiting Workspace Without Admin Redirects to /setup

1. Navigate to `http://localhost:2026/workspace`
2. **Expected:** Automatically redirects to `/setup`

#### TC-UI-02: Create Admin on Setup Page

1. Enter admin email, password, and password confirmation
2. Click Create Admin Account
3. **Expected:** Redirects to `/workspace`
4. Refresh page does not redirect back to `/setup`

#### TC-UI-03: Login Page When Already Initialized

1. Log out and visit `/login`
2. Enter admin email and password
3. Click Login
4. **Expected:** Redirects to `/workspace`

#### TC-UI-04: Password Mismatch on Setup Page

1. New password and confirmation do not match
2. Click Complete Setup
3. **Expected:** Displays "Passwords do not match" error

### 4.2 Routine Usage

#### TC-UI-05: Create Conversation

1. Send a message in workspace
2. **Expected:** New thread appears in left sidebar

#### TC-UI-06: Conversation Persistence

1. Create conversation and refresh page
2. **Expected:** Conversation list and message history persist

#### TC-UI-07: Logout

1. Click avatar → Logout
2. **Expected:** Redirects to landing page `/`
3. Directly visit `/workspace` → redirects to `/login`

### 4.3 Multi-User Isolation

#### TC-UI-08: User A Cannot See User B's Conversations

1. User A logs in on Browser 1, creates a thread, and sends a message
2. User B registers and logs in on Browser 2 (or incognito)
3. **Expected:** User B's left sidebar is empty; User A's conversations are not visible

#### TC-UI-09: Direct URL Access to Another User's Thread

1. Copy User A's thread URL
2. Visit in User B's browser
3. **Expected:** 404 or empty page; conversation content not displayed

### 4.4 Session Management

#### TC-UI-10: Tab Switching Session Validation

1. Log in to workspace
2. Switch to another tab and wait 60+ seconds
3. Switch back to workspace tab
4. **Expected:** Silently validates session; page behaves normally (no 401 console spam)

#### TC-UI-11: Switch Back to Tab After Session Expiration

1. Log in to workspace
2. Change password in another tab (invalidates current session)
3. Switch back to workspace tab
4. **Expected:** Automatically redirects to `/login`

#### TC-UI-12: Settings Page After Password Change

1. Navigate to Settings → Account
2. Change password
3. **Expected:** Success notification, no re-login required (cookies updated automatically)

### 4.5 Registration Flow

#### TC-UI-13: Navigate to Registration from Login Page

1. Click register link on `/login` page
2. Enter email and password
3. **Expected:** Automatically redirects to `/workspace` upon successful registration

#### TC-UI-14: Duplicate Email Registration

1. Attempt registration using an already-registered email
2. **Expected:** Displays "Email already registered" error

### 4.6 Password Reset (CLI)

#### TC-UI-15: Re-login After reset_admin

1. Execute `cd backend && python -m app.gateway.auth.reset_admin`
2. Read new password from `.agent-workspace/admin_initial_credentials.txt` and log in
3. **Expected:** Redirects to `/setup` page (`needs_setup` reset to true)
4. Old session is invalidated

---

## 5. Upgrade Tests

> Simulates upgrade from non-auth version (main branch) to auth version (feat/rfc-001-auth-module).

### 5.1 Prepare Legacy Data

```bash
# 1. Switch to main branch and start service
git stash && git checkout main
make dev

# 2. Create conversation data (no auth, direct access)
curl -s -X POST http://localhost:2026/api/langgraph/threads \
  -H "Content-Type: application/json" \
  -d '{"metadata":{"title":"old-thread-1"}}' | jq .thread_id

curl -s -X POST http://localhost:2026/api/langgraph/threads \
  -H "Content-Type: application/json" \
  -d '{"metadata":{"title":"old-thread-2"}}' | jq .thread_id

# 3. Record thread count
curl -s http://localhost:2026/api/langgraph/threads | jq length
# Expected: 2+

# 4. Stop service
make stop
```

### 5.2 Upgrade and Start

```bash
# 5. Switch to auth branch
git checkout feat/rfc-001-auth-module && git stash pop
make install
make dev
```

#### TC-UPG-01: First Boot Awaiting Admin Setup

**Expected:**
- [ ] Console does not print admin email or random password
- [ ] Visiting `/setup` allows creating initial admin
- [ ] Normal startup without errors

#### TC-UPG-02: Legacy Threads Migrated to Admin

```bash
# Create initial admin
curl -s -X POST http://localhost:2026/api/v1/auth/initialize \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"AdminPass1!"}' \
  -c cookies.txt

# Restart once: startup migration only runs when admin exists
make stop && make dev

# Login as admin
curl -s -X POST http://localhost:2026/api/v1/auth/login/local \
  -d "username=admin@example.com&password=AdminPass1!" \
  -c cookies.txt

# View thread list
CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')
curl -s -X POST http://localhost:2026/api/threads/search \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{}' | jq length
```

**Expected:**
- [ ] Returned thread count >= pre-upgrade created count
- [ ] Console logs `Migrated N orphan LangGraph thread(s) to admin`
- [ ] Legacy threads are visible only to admin

#### TC-UPG-03: Legacy Thread Content Integrity

```bash
# Verify content of legacy thread
curl -s http://localhost:2026/api/threads/<old-thread-id> \
  -b cookies.txt | jq .metadata
```

**Expected:**
- [ ] `metadata.title` preserves original value (e.g. `old-thread-1`)
- [ ] Response does not echo server-reserved `user_id` / `owner_id`

#### TC-UPG-04: New Users Cannot See Legacy Threads

```bash
# Register new user
curl -s -X POST http://localhost:2026/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"newuser@example.com","password":"NewPass123!"}' \
  -c newuser.txt

CSRF2=$(grep csrf_token newuser.txt | awk '{print $NF}')
curl -s -X POST http://localhost:2026/api/threads/search \
  -b newuser.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF2" \
  -d '{}' | jq length
```

**Expected:** Returns 0 (old threads belong to admin, invisible to new users)

### 5.3 Database Schema Compatibility

#### TC-UPG-05: Empty agent_workspace.db Initializes Schema Without Default Users

```bash
ls -la backend/.agent-workspace/data/agent_workspace.db
sqlite3 backend/.agent-workspace/data/agent_workspace.db "SELECT COUNT(*) FROM users;"
```

**Expected:** File exists, `sqlite3` shows `users` table with `needs_setup` and `token_version` columns; before calling `/initialize`, user count is 0

#### TC-UPG-06: agent_workspace.db WAL Mode

```bash
sqlite3 backend/.agent-workspace/data/agent_workspace.db "PRAGMA journal_mode;"
```

**Expected:** Returns `wal`

### 5.4 Configuration Compatibility

#### TC-UPG-07: Legacy .env Without AUTH_JWT_SECRET

```bash
# Verify AUTH_JWT_SECRET is unset in .env
grep AUTH_JWT_SECRET backend/.env || echo "NOT SET"
```

**Expected:**
- [ ] Startup warning: `AUTH_JWT_SECRET is not set — using auto-generated ephemeral secret`
- [ ] Service operates normally
- [ ] Stale sessions invalidated after restart (ephemeral secret changed)

#### TC-UPG-08: Legacy config.yaml Without Auth Section

```bash
# Verify config.yaml has no auth section
grep -c "auth" config.yaml || echo "0"
```

**Expected:** auth module does not depend on config.yaml (configured via environment variables), old config.yaml does not affect startup

### 5.5 Frontend Compatibility

#### TC-UPG-09: Legacy Frontend Cache

1. Access upgraded service with cached legacy frontend assets
2. **Expected:** Intercepted by AuthMiddleware returning 401 (no cookie), page refreshes and loads new frontend

#### TC-UPG-10: Bookmarked URLs

1. Directly visit a pre-upgrade bookmarked workspace URL (e.g. `localhost:2026/workspace/chats/xxx`)
2. **Expected:** Redirects to `/login`, redirects back to original URL after login (`?next=` parameter)

### 5.6 Downgrade and Rollback

#### TC-UPG-11: Rollback to main Branch

```bash
make stop
git checkout main
make dev
```

**Expected:**
- [ ] Service starts up normally (ignores `agent_workspace.db`, unauthenticated code does not error)
- [ ] Legacy conversation data remains accessible
- [ ] Existing `agent_workspace.db` file does not impact operation

#### TC-UPG-12: Upgrade to Auth Branch Again

```bash
make stop
git checkout feat/rfc-001-auth-module
make dev
```

**Expected:**
- [ ] Recognizes existing `agent_workspace.db`, does not duplicate admin
- [ ] Legacy admin account can still log in (if `agent_workspace.db` was preserved)

### 5.7 Admin Initialization & reset_admin

> First startup does not generate a default admin or log passwords. For forgotten passwords, run `reset_admin`; the new password is saved to a 0600 credential file.

#### TC-UPG-13: Restart Without Initialized Admin Does Not Create Defaults

```bash
rm -f backend/.agent-workspace/data/agent_workspace.db
make dev
make stop

make dev
curl -s $BASE/api/v1/auth/setup-status | jq .
```

**Expected:**
- [ ] Console does not print password
- [ ] `setup-status` remains `{"needs_setup": true}`
- [ ] Visiting `/setup` still allows creating initial admin

#### TC-UPG-14: Lost Password — reset_admin Writes to Credentials File

```bash
python -m app.gateway.auth.reset_admin --email admin@example.com
ls -la backend/.agent-workspace/admin_initial_credentials.txt
cat backend/.agent-workspace/admin_initial_credentials.txt
```

**Expected:**
- [ ] CLI only outputs credentials file path, not plaintext password
- [ ] Credentials file permissions are `0600`
- [ ] Credentials file contains email + password lines
- [ ] Subsequent login for this user returns `needs_setup=true`

#### TC-UPG-15: Registration Policy Boundary Before Admin Initialization

```bash
# Admin does not yet exist; standard user attempts registration
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"earlybird@example.com","password":"EarlyPass1!"}' \
  -c early.txt -w "\nHTTP %{http_code}"
```

**Expected:**
- [ ] Code allows standard user registration and auto-login (201, role `user`)
- [ ] But `setup-status` remains `{"needs_setup": true}` because admin does not exist
- [ ] Product policy boundary: if admin is required first, add admin-exists gate to `/register`

#### TC-UPG-16: User Data Isolated From Subsequent Admin

```bash
# Standard user creates thread and sends messages normally
CSRF=$(grep csrf_token early.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/threads \
  -b early.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"metadata":{}}' | jq .thread_id
```

**Expected:** Regular users create threads normally; subsequently created admin cannot search or access that regular user's thread

#### TC-UPG-17: Complete Setup After reset_admin

```bash
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=<credential_file_password>" \
  -c admin.txt | jq .needs_setup
# Expected: true

# Complete setup
CSRF=$(grep csrf_token admin.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/v1/auth/change-password \
  -b admin.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"current_password":"<credential_file_password>","new_password":"AdminFinal1!","new_email":"admin@real.com"}' \
  -c admin.txt

# Verification
curl -s $BASE/api/v1/auth/me -b admin.txt | jq '{email, needs_setup}'
```

**Expected:**
- [ ] `email` updated to `admin@real.com`
- [ ] `needs_setup` becomes `false`
- [ ] Subsequent logins use the new password

#### TC-UPG-18: JWT Secret Rotation After Inactivity

```bash
# Scenario: Operator rotated AUTH_JWT_SECRET while admin was inactive
# 1. Initial boot uses auto-generated ephemeral secret
# 2. Operator sets persistent secret in .env
echo "AUTH_JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
make stop && make dev
```

**Expected:**
- [ ] Service starts up normally
- [ ] Email/password login still works (passwords stored in DB independent of JWT secret)
- [ ] Stale JWT tokens invalidated (signature mismatch due to changed secret)

---

## 6. Reentrancy & Idempotency Tests

> Verifies that the auth module behaves correctly without race conditions under repeated operations, concurrency, and interrupted recovery.

### 6.1 Startup Reentrancy

#### TC-REENT-01: Consecutive Restarts Do Not Duplicate Admin

```bash
# Start 3 consecutive times (daemon mode)
for i in 1 2 3; do
  make dev-daemon && sleep 10 && make stop
done

# Check admin count
sqlite3 backend/.agent-workspace/data/agent_workspace.db \
  "SELECT COUNT(*) FROM users WHERE system_role='admin';"
```

**Expected:** Always 1. Multiple admins will not be created due to restart.

#### TC-REENT-02: Multi-Process Concurrent Startup

```bash
# Simulate concurrent startup of two gateway processes
cd backend
PYTHONPATH=. uv run python -c "
import asyncio
from app.gateway.app import create_app, _ensure_admin_user

async def boot():
    app = create_app()
    # Simulate concurrent ensure_admin calls
    await asyncio.gather(
        _ensure_admin_user(app),
        _ensure_admin_user(app),
    )

asyncio.run(boot())
" 2>&1 | grep -i "admin\|error\|duplicate"
```

**Expected:**
- [ ] No error raised (SQLite UNIQUE constraint catches race; second skips silently)
- [ ] Exactly 1 admin exists in the end

#### TC-REENT-03: Thread Migration Idempotency

```bash
# Call _migrate_orphaned_threads twice consecutively
# Second run should migrate 0 threads (already have user_id)
```

**Expected:** Second run yields `migrated = 0`, no side effects

### 6.2 Login Reentrancy

#### TC-REENT-04: Repeat Login Obtains New Cookie

```bash
# Same user logs in 3 consecutive times
for i in 1 2 3; do
  curl -s -X POST $BASE/api/v1/auth/login/local \
    -d "username=admin@example.com&password=CorrectPassword" \
    -c "cookies_$i.txt" -o /dev/null
done

# All three cookies remain valid
for i in 1 2 3; do
  echo "Cookie $i: $(curl -s -w '%{http_code}' -o /dev/null $BASE/api/v1/auth/me -b cookies_$i.txt)"
done
```

**Expected:** All three cookies return 200 (password unchanged, token_version identical, multi-session coexistence)

#### TC-REENT-05: Login-Logout-Login Cycle

```bash
# Login
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" \
  -c cookies.txt -o /dev/null

# Logout
curl -s -X POST $BASE/api/v1/auth/logout -b cookies.txt -o /dev/null

# Login again
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" \
  -c cookies.txt

curl -s -w "%{http_code}" $BASE/api/v1/auth/me -b cookies.txt
```

**Expected:** 200. Logout -> re-login flow leaves no residual state.

### 6.3 Password Change Reentrancy

#### TC-REENT-06: Consecutive Password Changes

```bash
CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')

# First password change
curl -s -X POST $BASE/api/v1/auth/change-password \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"current_password":"Pass1","new_password":"Pass2"}' \
  -c cookies.txt

# Update again using CSRF token from new cookie
CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/v1/auth/change-password \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"current_password":"Pass2","new_password":"Pass3"}' \
  -c cookies.txt

curl -s -w "%{http_code}" $BASE/api/v1/auth/me -b cookies.txt
```

**Expected:**
- [ ] Both password changes succeed
- [ ] Final password is Pass3
- [ ] `token_version` incremented twice (+2)
- [ ] Latest cookie is valid

#### TC-REENT-07: Old Cookies Expire After Password Change

```bash
# Save cookies across three points in time
# t1: Initial login → cookies_t1.txt
# t2: After first password change → cookies_t2.txt
# t3: After second password change → cookies_t3.txt

# Access using cookies from t1 and t2
curl -s -w "%{http_code}" $BASE/api/v1/auth/me -b cookies_t1.txt  # Expected 401
curl -s -w "%{http_code}" $BASE/api/v1/auth/me -b cookies_t2.txt  # Expected 401
curl -s -w "%{http_code}" $BASE/api/v1/auth/me -b cookies_t3.txt  # Expected 200
```

**Expected:** Only the latest cookie is valid; older cookies all return 401 due to token_version mismatch

### 6.4 Registration Reentrancy

#### TC-REENT-08: Concurrent Registration with Same Email

```bash
# Send concurrent registration requests with identical email
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"race@example.com","password":"RacePass1!"}' &
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"race@example.com","password":"RacePass1!"}' &
wait

# Verify user count
sqlite3 backend/.agent-workspace/data/agent_workspace.db \
  "SELECT COUNT(*) FROM users WHERE email='race@example.com';"
```

**Expected:**
- [ ] One succeeds (201), one fails (400 `email_already_exists`)
- [ ] Database has only 1 record (protected by UNIQUE constraint)

### 6.5 Rate Limiter Reentrancy

#### TC-REENT-09: Rate Limiter Resets After Lockout Expiry

```bash
# Trigger lockout (5 consecutive errors)
for i in $(seq 1 5); do
  curl -s -o /dev/null -X POST $BASE/api/v1/auth/login/local \
    -d "username=admin@example.com&password=wrong"
done

# Verify account lockout
curl -s -w "%{http_code}" -o /dev/null -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=wrong"
# Expected: 429

# Wait for lockout to expire (5 min) or restart service to clear counters
make stop && make dev

# Retry — counter should be reset
curl -s -w "%{http_code}" -o /dev/null -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=wrong"
# Expected: 401 (not 429)
```

**Expected:** After lockout expires, normal rate limiting resumes (counting restarts from 0) rather than accumulating

#### TC-REENT-10: Success Resets Counter Followed by New Failures

```bash
# 3 failed attempts
for i in $(seq 1 3); do
  curl -s -o /dev/null -X POST $BASE/api/v1/auth/login/local \
    -d "username=admin@example.com&password=wrong"
done

# 1 successful attempt (resets counter)
curl -s -o /dev/null -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword"

# Another 4 failures (recounting from 0, threshold 5 not reached)
for i in $(seq 1 4); do
  curl -s -w "attempt $i: %{http_code}\n" -o /dev/null -X POST $BASE/api/v1/auth/login/local \
    -d "username=admin@example.com&password=wrong"
done
```

**Expected:** All 4 attempts return 401 (not locked out), because successful login reset the counter

### 6.6 CSRF Token Reentrancy

#### TC-REENT-11: Reusable CSRF Token for Multiple POSTs

```bash
CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')

# Multiple requests using the same CSRF token
for i in 1 2 3; do
  echo "Request $i: $(curl -s -w '%{http_code}' -o /dev/null \
    -X POST $BASE/api/threads \
    -b cookies.txt \
    -H 'Content-Type: application/json' \
    -H "X-CSRF-Token: $CSRF" \
    -d '{"metadata":{}}')"
done
```

**Expected:** All three attempts succeed (CSRF token is a Double Submit Cookie, not a one-time nonce)

### 6.7 Thread Operations Reentrancy

#### TC-REENT-12: Repeat Deletion of Same Thread

```bash
CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')

# Create thread
TID=$(curl -s -X POST $BASE/api/threads \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"metadata":{}}' | jq -r .thread_id)

# First deletion
curl -s -w "%{http_code}" -X DELETE "$BASE/api/threads/$TID" \
  -b cookies.txt -H "X-CSRF-Token: $CSRF"
# Expected: 200

# Second deletion (idempotent)
curl -s -w "%{http_code}" -X DELETE "$BASE/api/threads/$TID" \
  -b cookies.txt -H "X-CSRF-Token: $CSRF"
```

**Expected:** Second call returns 200 or 404, does not report 500

### 6.8 reset_admin Reentrancy

#### TC-REENT-13: Consecutive reset_admin Invocations

```bash
cd backend
python -m app.gateway.auth.reset_admin
cp .agent-workspace/admin_initial_credentials.txt /tmp/agent_workspace-reset-p1.txt
P1=$(awk -F': ' '/^password:/ {print $2}' /tmp/agent_workspace-reset-p1.txt)

python -m app.gateway.auth.reset_admin
cp .agent-workspace/admin_initial_credentials.txt /tmp/agent_workspace-reset-p2.txt
P2=$(awk -F': ' '/^password:/ {print $2}' /tmp/agent_workspace-reset-p2.txt)
```

**Expected:**
- [ ] `.agent-workspace/admin_initial_credentials.txt` is overwritten each time with mode `0600`
- [ ] P1 != P2 (new random password generated each time)
- [ ] P1 is invalid; only P2 is valid
- [ ] `token_version` incremented by 2
- [ ] `needs_setup` is True

### 6.9 Setup Flow Reentrancy

#### TC-REENT-14: Revisit /setup Page After Setup Completed

1. Complete admin setup (change email + password)
2. Directly visit `/setup`
3. **Expected:** Redirects to `/workspace` (`needs_setup` is false, SSR guard does not return `needs_setup` tag)

#### TC-REENT-15: Refresh Page Mid-Setup

1. Fill form halfway on `/setup` page
2. Refresh page
3. **Expected:** Remains on `/setup` (`needs_setup` remains true), form cleared with no errors

---

## 7. Mode Differences Tests

> Below uses `GW=http://localhost:8001` for direct Gateway connection, and `BASE=http://localhost:2026` for proxied via nginx.
> Standard launch command: `make dev` (or `./scripts/serve.sh --dev`).

### 7.1 Standard Startup Mode

#### TC-MODE-01: Gateway AuthMiddleware token_version Check

```bash
# Login and capture cookie
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" -c cookies.txt

# Change password (bumps token_version)
CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/v1/auth/change-password \
  -b cookies.txt -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" \
  -d '{"current_password":"CorrectPassword","new_password":"NewPass1!"}' -c new_cookies.txt

# Access LangGraph-compatible route with stale cookie
curl -s -w "%{http_code}" $BASE/api/langgraph/threads/search -b cookies.txt
# Expected: 401 (token_version mismatch)

# Access using new cookie
CSRF2=$(grep csrf_token new_cookies.txt | awk '{print $NF}')
curl -s -w "%{http_code}" -X POST $BASE/api/langgraph/threads/search \
  -b new_cookies.txt -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF2" -d '{}'
# Expected: 200
```

#### TC-MODE-02: Gateway Owner Filter Isolation

```bash
# user1 creates thread
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=user1@example.com&password=UserPass1!" -c u1.txt
CSRF1=$(grep csrf_token u1.txt | awk '{print $NF}')
TID=$(curl -s -X POST $BASE/api/langgraph/threads \
  -b u1.txt -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF1" \
  -d '{"metadata":{}}' | python3 -c "import sys,json; print(json.load(sys.stdin)['thread_id'])")

# user2 searches — must not see user1's thread
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=user2@example.com&password=UserPass2!" -c u2.txt
CSRF2=$(grep csrf_token u2.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/langgraph/threads/search \
  -b u2.txt -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF2" -d '{}' | python3 -c "
import sys,json
threads = json.load(sys.stdin)
ids = [t['thread_id'] for t in threads]
assert '$TID' not in ids, 'LEAK: user2 can see user1 thread'
print('OK: user2 sees', len(threads), 'threads, none belong to user1')
"
```

#### TC-MODE-03: All Requests Pass Through AuthMiddleware

```bash
# Gateway API protected
curl -s -w "%{http_code}" -o /dev/null $BASE/api/models
# Expected: 401

# LangGraph-compatible routes (rewritten to Gateway) are also protected
curl -s -w "%{http_code}" -o /dev/null -X POST $BASE/api/langgraph/threads/search \
  -H "Content-Type: application/json" -d '{}'
# Expected: 401
```

#### TC-MODE-04: Full Auth Flow in Standard Mode

```bash
# Login
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" -c cookies.txt

CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')

# Create thread (via Gateway embedded runtime)
curl -s -X POST $BASE/api/langgraph/threads \
  -b cookies.txt -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" \
  -d '{"metadata":{}}' | python3 -c "import sys,json; print(json.load(sys.stdin)['thread_id'])"
# Expected: returns thread_id

# CSRF protection (CSRFMiddleware covers all Gateway routes)
curl -s -w "%{http_code}" -o /dev/null -X POST $BASE/api/langgraph/threads \
  -b cookies.txt -H "Content-Type: application/json" -d '{"metadata":{}}'
# Expected: 403 (CSRF token missing)
```

### 7.3 Direct Gateway (No Nginx)

> Launch command: `cd backend && make gateway` (Port 8001)
> Directly test the Gateway auth layer without passing through nginx.

#### TC-GW-01: AuthMiddleware Protects All Non-Public Routes

```bash
GW=http://localhost:8001

for path in /api/models /api/mcp/config /api/memory /api/skills \
            /api/v1/auth/me /api/v1/auth/change-password; do
  echo "$path: $(curl -s -w '%{http_code}' -o /dev/null $GW$path)"
done
# Expected: All 401
```

#### TC-GW-02: Public Routes Do Not Require Cookie

```bash
GW=http://localhost:8001

for path in /health /api/v1/auth/setup-status /api/v1/auth/login/local \
            /api/v1/auth/register /api/v1/auth/initialize /api/v1/auth/logout; do
  echo "$path: $(curl -s -w '%{http_code}' -o /dev/null $GW$path)"
done
# Expected: 200 or 405/422 (method mismatch, but not 401)
```

#### TC-GW-03: Direct Gateway Register + Login + CSRF Flow

```bash
GW=http://localhost:8001

# Register
curl -s -X POST $GW/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"gwtest@example.com","password":"GwTest123!"}' \
  -c gw_cookies.txt -w "\nHTTP %{http_code}"
# Expected: 201

# Login
curl -s -X POST $GW/api/v1/auth/login/local \
  -d "username=gwtest@example.com&password=GwTest123!" \
  -c gw_cookies.txt -w "\nHTTP %{http_code}"
# Expected: 200

# GET (no CSRF required)
curl -s -w "%{http_code}" $GW/api/models -b gw_cookies.txt
# Expected: 200

# POST without CSRF
curl -s -w "%{http_code}" -o /dev/null -X POST $GW/api/memory/reload -b gw_cookies.txt
# Expected: 403 (CSRF token missing)

# POST with CSRF
CSRF=$(grep csrf_token gw_cookies.txt | awk '{print $NF}')
curl -s -w "%{http_code}" -o /dev/null -X POST $GW/api/memory/reload \
  -b gw_cookies.txt -H "X-CSRF-Token: $CSRF"
# Expected: 200
```

#### TC-GW-04: Direct Gateway Rate Limiter

```bash
GW=http://localhost:8001

# On direct connect, client.host is the real TCP peer IP; ignores X-Real-IP
for i in $(seq 1 6); do
  echo -n "attempt $i: "
  curl -s -w "%{http_code}\n" -o /dev/null -X POST $GW/api/v1/auth/login/local \
    -d "username=admin@example.com&password=wrong"
done
# Expected: First 5 return 401, 6th returns 429
```

#### TC-GW-05: Direct Gateway Not Deceived by X-Real-IP Spoofing

```bash
GW=http://localhost:8001

# On direct connect, client.host is not a trusted proxy; X-Real-IP ignored
for i in $(seq 1 6); do
  echo -n "attempt $i (X-Real-IP spoofed): "
  curl -s -w "%{http_code}\n" -o /dev/null -X POST $GW/api/v1/auth/login/local \
    -H "X-Real-IP: 10.0.0.$i" \
    -d "username=admin@example.com&password=wrong"
done
# Expected: First 5 return 401, 6th returns 429 (spoofed IP ignored, all share real bucket)
```

### 7.4 Docker Deployment

> Launch command: `./scripts/deploy.sh`
> Docker Compose file: `docker/docker-compose.yaml`
>
> Prerequisites:
> - Set `AUTH_JWT_SECRET` in `.env` (otherwise sessions are invalidated on every container restart)
> - Mount `AGENT_WORKSPACE_HOME` to a host directory (persisting `agent_workspace.db`)

#### TC-DOCKER-01: agent_workspace.db Volume Persistence

```bash
# Start container
./scripts/deploy.sh

# Wait for startup completion
sleep 15
BASE=http://localhost:2026

# Register user
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"docker-test@example.com","password":"DockerTest1!"}' -w "\nHTTP %{http_code}"

# Verify agent_workspace.db on host filesystem
ls -la ${AGENT_WORKSPACE_HOME:-backend/.agent-workspace}/data/agent_workspace.db
sqlite3 ${AGENT_WORKSPACE_HOME:-backend/.agent-workspace}/data/agent_workspace.db \
  "SELECT email FROM users WHERE email='docker-test@example.com';"
```

**Expected:** agent_workspace.db resides in the host `AGENT_WORKSPACE_HOME` directory, query shows newly registered users.

#### TC-DOCKER-02: Session Persistence Across Container Restarts

```bash
# Login and capture cookie
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=docker-test@example.com&password=DockerTest1!" \
  -c docker_cookies.txt -o /dev/null

# Verify cookie is valid
curl -s -w "%{http_code}" -o /dev/null $BASE/api/v1/auth/me -b docker_cookies.txt
# Expected: 200

# Restart container (preserving volume)
./scripts/deploy.sh down && ./scripts/deploy.sh
sleep 15

# Access using old cookie
curl -s -w "%{http_code}" -o /dev/null $BASE/api/v1/auth/me -b docker_cookies.txt
```

**Expected:**
- With `AUTH_JWT_SECRET` -> 200 (session persists)
- Without `AUTH_JWT_SECRET` -> 401 (new ephemeral secret generated on each startup, old JWT signature invalid)

#### TC-DOCKER-03: Independent Rate Limiters Across Workers

```bash
# gateway defaults to 4 workers in docker-compose.yaml
# Each worker maintains an independent _login_attempts dict
# Rate limit may be approximate across workers but still enforces bounds

for i in $(seq 1 20); do
  echo -n "attempt $i: "
  curl -s -w "%{http_code}\n" -o /dev/null -X POST $BASE/api/v1/auth/login/local \
    -d "username=docker-test@example.com&password=wrong"
done
```

**Expected:** Starts returning 429 at some point (each worker counts independently; threshold may trigger between 5~20 depending on load balancer distribution).

**Known Limitation:** In-process rate limiter is not shared across workers. In production, precise rate limiting requires external storage such as Redis.

#### TC-DOCKER-04: IM Channels Use Internal Auth

```bash
# IM channels (Feishu/Slack/Telegram) invoke Gateway via LangGraph SDK inside gateway container
# Request attaches process-local internal auth header and CSRF token

# Verification: inspect gateway logs to verify channel manager requests contain no auth errors
docker logs agent-workspace-gateway 2>&1 | grep -E "ChannelManager|channel" | head -10
```

**Expected:** No auth-related errors. Channels do not depend on browser cookies; the server routes requests into the `default` user bucket via internal auth headers.

#### TC-DOCKER-05: reset_admin Credentials Written to 0600 File (Not Logged)

```bash
# First boot does not generate admin password automatically. Reset admin to write credentials file.
docker exec agent-workspace-gateway python -m app.gateway.auth.reset_admin --email docker-test@example.com

ls -la ${AGENT_WORKSPACE_HOME:-backend/.agent-workspace}/admin_initial_credentials.txt
# Expected file permissions: -rw------- (0600)

cat ${AGENT_WORKSPACE_HOME:-backend/.agent-workspace}/admin_initial_credentials.txt
# Expected content: email + password lines

# Container log prints credentials file path, not plaintext password
docker logs agent-workspace-gateway 2>&1 | grep -E "Credentials written to|Admin account"
# Expected output: "Credentials written to: /...../admin_initial_credentials.txt (mode 0600)"

# Negative check: Plaintext password NEVER appears in logs
docker logs agent-workspace-gateway 2>&1 | grep -iE "Password: .{15,}" && echo "FAIL: leaked" || echo "OK: not leaked"
```

**Expected:**
- Credential file exists under `AGENT_WORKSPACE_HOME`, permissions `0600`
- Container logs output the **path** (not the password itself), conforming to CodeQL `py/clear-text-logging-sensitive-data` rule
- `grep "Password:"` in logs **should have no match** (legacy behavior deprecated; simplify pass removed log leakage paths)

#### TC-DOCKER-06: Docker Deployment

```bash
# Standard Docker mode: runtime embedded inside gateway container
./scripts/deploy.sh
sleep 15

# Verify gateway container is running
docker ps --filter name=agent-workspace-gateway --format '{{.Names}}'
# Expected: agent-workspace-gateway

# Normal auth flow: unauthenticated protected endpoints return 401
curl -s -w "%{http_code}" -o /dev/null $BASE/api/models
# Expected: 401

curl -s -X POST $BASE/api/v1/auth/initialize \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"AdminPass1!"}' \
  -c cookies.txt -w "\nHTTP %{http_code}"
# Expected: 201
```

### 7.4 Additional Edge Cases

#### TC-EDGE-01: Well-Formed But Random JWT

```bash
RANDOM_JWT=$(python3 -c "
import jwt, time, uuid
print(jwt.encode({'sub':str(uuid.uuid4()),'ver':0,'exp':int(time.time())+3600}, 'wrong-secret-32chars-placeholder!!', algorithm='HS256'))
")
curl -s --cookie "access_token=$RANDOM_JWT" $BASE/api/v1/auth/me | jq .detail
```

**Expected:** `{"code": "token_invalid", "message": "Token error: invalid_signature"}`

#### TC-EDGE-02: Pass system_role=admin During Registration

```bash
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"hacker@example.com","password":"HackPass1!","system_role":"admin"}' | jq .system_role
```

**Expected:** `"user"` (`system_role` field is ignored)

#### TC-EDGE-03: Concurrent Password Changes

```bash
# Register user and establish two sessions
curl -s -X POST $BASE/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"edge03@example.com","password":"EdgePass3!"}' -o /dev/null
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=edge03@example.com&password=EdgePass3!" -c s1.txt -o /dev/null
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=edge03@example.com&password=EdgePass3!" -c s2.txt -o /dev/null

CSRF1=$(grep csrf_token s1.txt | awk '{print $NF}')
CSRF2=$(grep csrf_token s2.txt | awk '{print $NF}')

# Concurrent password changes
curl -s -w "S1: %{http_code}\n" -o /dev/null -X POST $BASE/api/v1/auth/change-password \
  -b s1.txt -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF1" \
  -d '{"current_password":"EdgePass3!","new_password":"NewEdge3a!"}' &
curl -s -w "S2: %{http_code}\n" -o /dev/null -X POST $BASE/api/v1/auth/change-password \
  -b s2.txt -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF2" \
  -d '{"current_password":"EdgePass3!","new_password":"NewEdge3b!"}' &
wait
```

**Expected:** One 200, one 400 (current_password already changed causing verification failure). Under extreme concurrency both might return 200 (SQLite serialized writes), but only one password ultimately takes effect.

#### TC-EDGE-04: Cookie SameSite Verification

> See §3.3 TC-ATK-06/07/07a for the full HTTP/HTTPS cookie attribute comparison.

```bash
curl -s -D - -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" 2>/dev/null | grep -i set-cookie
```

**Expected:** `access_token` -> `SameSite=lax`, `csrf_token` -> `SameSite=strict`

#### TC-EDGE-05: HTTP Has No max_age / HTTPS Has max_age

```bash
GW=http://localhost:8001

# HTTP
curl -s -D - -X POST $GW/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" 2>/dev/null \
  | grep "access_token=" | grep -oi "max-age=[0-9]*" || echo "NO max-age (HTTP session cookie)"

# HTTPS: Direct Gateway access required to simulate HTTPS via X-Forwarded-Proto; nginx overwrites header
curl -s -D - -X POST $GW/api/v1/auth/login/local \
  -H "X-Forwarded-Proto: https" \
  -d "username=admin@example.com&password=CorrectPassword" 2>/dev/null \
  | grep "access_token=" | grep -oi "max-age=[0-9]*"
```

**Expected:** HTTP has no `Max-Age` (session cookie, expires when browser closes), HTTPS has `Max-Age=604800` (7 days)

#### TC-EDGE-06: Public Path Trailing Slash

```bash
for path in /api/v1/auth/login/local/ /api/v1/auth/register/ \
            /api/v1/auth/logout/ /api/v1/auth/setup-status/; do
  echo "$path: $(curl -s -w '%{http_code}' -o /dev/null $BASE$path)"
done
```

**Expected:** All return 307 (redirect stripping trailing slash) or 200/405, not 401

### 7.5 Red Team Adversarial Tests

> Simulates an attacker perspective to verify defenses have no exploitable gaps.

#### 7.5.1 Path Obfuscation Bypass

```bash
# Attempt bypass via encoding, double slashes, and traversal
for path in \
  "//api/v1/auth/me" \
  "/api/v1/auth/login/local/../me" \
  "/api/v1/auth/login/local%2f..%2fme" \
  "/api/v1/auth/login/local/..%2Fme" \
  "/API/V1/AUTH/ME"; do
  echo "$path: $(curl -s -w '%{http_code}' -o /dev/null $BASE$path)"
done
```

**Expected:** All return 401 or 404. Path confusion must not bypass auth checks.

#### 7.5.2 CSRF Adversarial Matrix

```bash
# Login and capture cookie
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" -c cookies.txt

CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')

# Case 1: Cookie present, header missing → 403
curl -s -w "%{http_code}" -o /dev/null \
  -X POST $BASE/api/threads -b cookies.txt \
  -H "Content-Type: application/json" -d '{"metadata":{}}'

# Case 2: Header present, cookie missing → 403 (delete csrf_token from cookie)
curl -s -w "%{http_code}" -o /dev/null \
  -X POST $BASE/api/threads \
  -b cookies.txt \
  -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" -d '{"metadata":{}}'

# Case 3: Mismatched header and cookie → 403
curl -s -w "%{http_code}" -o /dev/null \
  -X POST $BASE/api/threads -b cookies.txt \
  -H "X-CSRF-Token: wrong-token" \
  -H "Content-Type: application/json" -d '{"metadata":{}}'

# Case 4: Stale CSRF token (after logout and re-login) → stale token invalidated
curl -s -X POST $BASE/api/v1/auth/logout -b cookies.txt
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" -c cookies.txt
# Send request with stale CSRF token
curl -s -w "%{http_code}" -o /dev/null \
  -X POST $BASE/api/threads -b cookies.txt \
  -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" -d '{"metadata":{}}'
```

**Expected:** Cases 1-3 all return 403. Case 4 should return 403 (old CSRF does not match new cookie).

#### 7.5.3 Token Replay (Replaying Old Token After Logout)

```bash
# Login and save cookies
curl -s -X POST $BASE/api/v1/auth/login/local \
  -d "username=admin@example.com&password=CorrectPassword" -c cookies.txt

# Extract access_token value
TOKEN=$(grep access_token cookies.txt | awk '{print $NF}')

# Logout
curl -s -X POST $BASE/api/v1/auth/logout -b cookies.txt

# Manually inject stale token (simulating stolen token)
curl -s -w "%{http_code}" -o /dev/null \
  $BASE/api/v1/auth/me --cookie "access_token=$TOKEN"
```

**Expected:** 200 (Known limitation: logout only clears client cookies without bumping `token_version`. Old tokens remain valid until expiration).
**Security Note:** For strict replay prevention, `token_version += 1` would be needed on logout. The current design chooses not to do this because the cost is invalidating sessions across all devices.

#### 7.5.4 Cross-Site Forced Logout

```bash
# Attacker posts to /logout from third-party site (public + CSRF-exempt)
curl -s -X POST $BASE/api/v1/auth/logout -w "%{http_code}"
```

**Expected:** 200 (logout is public + CSRF exempt).
**Risk Assessment:** Low — only impacts availability (forced logout), does not leak data. Browser `SameSite=Lax` restricts cookies from being attached in actual cross-site scenarios, so third-party POSTs will not actually clear user cookies.

#### 7.5.5 Metadata Injection Attack (Ownership Forgery)

```bash
# Attempt to inject another user's user_id during thread creation
CSRF=$(grep csrf_token cookies.txt | awk '{print $NF}')
curl -s -X POST $BASE/api/threads \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"metadata":{"owner_id":"victim-user-id","user_id":"victim-user-id"}}' | jq .metadata
```

**Expected:** Returned `metadata` does not contain `owner_id` or `user_id`. True ownership is stored in `threads_meta.user_id`, neither accepted from client metadata nor echoed back in metadata.

#### 7.5.6 HTTP Method Probing

```bash
# HEAD/OPTIONS must not leak protected resource info
for method in HEAD OPTIONS TRACE; do
  echo "$method /api/models: $(curl -s -w '%{http_code}' -o /dev/null -X $method $BASE/api/models)"
done
```

**Expected:** HEAD/OPTIONS return 401 or 405. TRACE should return 405.

#### 7.5.7 Rate Limiter IP Dimension Defect Verification

```bash
# Attempt rate limit bypass via different X-Forwarded-For headers
for i in $(seq 1 6); do
  curl -s -w "attempt $i: %{http_code}\n" -o /dev/null \
    -X POST $BASE/api/v1/auth/login/local \
    -H "X-Forwarded-For: 10.0.0.$i" \
    -d "username=admin@example.com&password=wrong"
done
```

**Expected:** If rate limiter is based on `request.client.host` (actual TCP connection IP), all requests come from the same IP, so the 6th should return 429. X-Forwarded-For must not influence rate limit evaluation.

#### 7.5.8 Junk Cookie Penetration Verification

```bash
# Middleware checks cookie presence; downstream validates JWT
# Confirm junk cookie passes middleware but is rejected by downstream @require_auth
curl -s -w "%{http_code}" $BASE/api/v1/auth/me \
  --cookie "access_token=not-a-jwt"
```

**Expected:** 401 (middleware allows request through, `get_current_user_from_request` decode fails and returns 401).
**Security Note:** Middleware performs a presence-only check by intentional design. Full validation is deferred to `@require_auth`.

#### 7.5.9 Route Coverage Audit

```bash
# List all registered routes to verify @require_auth coverage
cd backend && PYTHONPATH=. python3 -c "
from app.gateway.app import create_app
app = create_app()
public_prefixes = ['/health', '/docs', '/redoc', '/openapi.json',
                   '/api/v1/auth/login', '/api/v1/auth/register',
                   '/api/v1/auth/logout', '/api/v1/auth/setup-status']
for route in app.routes:
    path = getattr(route, 'path', '')
    if not path or not path.startswith('/api'):
        continue
    is_public = any(path.startswith(p) for p in public_prefixes)
    if not is_public:
        print(f'  {path}')
" 2>/dev/null
```

**Expected:** All listed routes should have dual-layer protection from AuthMiddleware (cookie presence) + `@require_auth`/`@require_permission` (JWT validation). Verify no routes are missed.

---

## 8. Regression Checklist

Must pass after every auth-related code change:

```bash
# Unit tests
cd backend && PYTHONPATH=. uv run pytest \
  tests/test_auth.py \
  tests/test_auth_config.py \
  tests/test_auth_errors.py \
  tests/test_auth_type_system.py \
  tests/test_auth_middleware.py \
  tests/test_langgraph_auth.py \
  -v

# Core endpoint smoke tests
curl -s $BASE/health                              # 200
curl -s $BASE/api/models                          # 401 (no cookie)
curl -s $BASE/api/v1/auth/setup-status            # 200
curl -s $BASE/api/v1/auth/me -b cookies.txt       # 200 (with cookie)
```
