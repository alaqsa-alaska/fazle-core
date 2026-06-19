# VPS Full Audit Report

**Report rewritten:** 2026-06-17  
**Original audit baseline:** 2026-06-10  
**System:** `iamazim.com` production VPS  
**Primary owner rule:** business correctness over technical convenience  
**Scope:** production repositories, LocationWhere SMS onboarding, deployment paths, known risks, and next actions  

---

## 1. Executive Summary

This report has been rewritten after the latest LocationWhere implementation work on 2026-06-17.

The most important correction is that the previous report's LocationWhere SMS recommendation was no longer aligned with the owner's business requirement. The approved SMS architecture is not SSL Wireless, Twilio, or any external provider. The only approved gateway is the Android `smsgateway` app installed on the administrator phone.

The LocationWhere backend has now been changed so the VPS does not send SMS directly. It receives inbound SMS webhook payloads from the Android gateway, creates or detects the employee, generates the employee code using the existing server-side generator, and returns a reply payload for the Android gateway to send back to the employee.

### Current readiness

| App | Status | Notes |
|---|---|---|
| fazle-core | Likely healthy | No new code changes in this rewrite; previous findings still need verification before schema work |
| locationwhere | Improved | SMS onboarding now matches Android gateway architecture; build passed |
| locationwhere frontend | Verified connected to git build | Active nginx root is `/home/azim/location_where/admin-dashboard/dist` |
| locationwhere recordings | Improved failure mode | S3 remains config-dependent, but backend now fails clearly when storage is not configured |
| nginx | Verified for LocationWhere | Active LocationWhere config points to tracked dashboard build |

---

## 2. Mandatory Business Workflow

### VERIFIED

Employees must send SMS to the administrator mobile:

```text
01958122300
```

Accepted SMS formats are intentionally flexible. All of these are valid:

```text
ID: 017XXXXXXXX Employee Name
ID: Employee Name 017XXXXXXXX
ID : 017XXXXXXXX Employee Name
I D : Employee Name 017XXXXXXXX
```

The SMS must contain the employee's own mobile number.

Approved flow:

```text
Employee
Admin mobile
smsgateway Android app
LocationWhere/fazle-core API
Employee creation logic
Generated employee code
Response payload
smsgateway Android app
Employee receives SMS
```

### VERIFIED

The VPS must not send SMS directly. The VPS only returns the response that the Android gateway should send.

### BUSINESS CONFLICT RESOLVED

The previous report recommended configuring SSL Wireless credentials. That recommendation is now removed because it conflicts with owner instructions.

---

## 3. LocationWhere Repository and Architecture

### VERIFIED

Production source repository:

```text
/home/azim/location_where/
```

Do not confuse this with removed or obsolete deployments:

```text
/home/azim/locationwhere-backend
/home/azim/locationwhere-frontend
```

### VERIFIED

Repository layout:

| Component | Path | Purpose |
|---|---|---|
| Backend | `/home/azim/location_where/backend/` | Node.js, Express, Prisma, PostgreSQL, Redis |
| Employee Android app | `/home/azim/location_where/app/` | Kotlin employee monitoring app |
| Admin dashboard | `/home/azim/location_where/admin-dashboard/` | React/Vite dashboard |

### VERIFIED

Android employee app API base URL is configured from Gradle and defaults to:

```text
https://locationwhere.iamazim.com/
```

Backend API paths used by the Android app include:

```text
api/v1/auth/mobile/login
api/v1/location/update
api/v1/calls/log
api/v1/calls/upload-recording
api/v1/device/register
```

---

## 4. Latest Implementation Changes

### VERIFIED

The following files were changed in `/home/azim/location_where/backend/`:

| File | Change |
|---|---|
| `src/modules/employee/employee.utils.ts` | SMS parser now accepts flexible `ID` / `I D` formats with phone before or after employee name |
| `src/modules/employee/employee.service.ts` | Removed direct SMS sending; returns gateway reply payload |
| `src/modules/auth/auth.service.ts` | Disabled direct OTP SMS delivery path |
| `src/modules/call/call.service.ts` | Added S3 configuration guard before upload/download |
| `src/utils/sms.ts` | Deleted obsolete SSL Wireless sender helper |
| `.env.example` | Removed external SMS provider example variables |
| `.env.locationwhere.example` | Added Android gateway-only configuration keys |
| `dist/*` | Rebuilt backend artifacts with `npm run build` |

### VERIFIED

Backend build command completed successfully:

```bash
cd /home/azim/location_where/backend
npm run build
```

### VERIFIED

Backend source, compiled dist files, and env examples were scanned after the change. No remaining references were found for:

```text
SSL Wireless
SMS_API_TOKEN
SMS_SID
SMS_DOMAIN
sendSMS
ssl_wireless
```

---

## 5. SMS Onboarding API

### VERIFIED

Endpoint:

```text
POST https://locationwhere.iamazim.com/api/v1/employees/onboarding/sms
```

Required header:

```text
x-gateway-secret: <SMS_GATEWAY_SECRET>
```

Suggested request body from Android `smsgateway`:

```json
{
  "sender": "017XXXXXXXX",
  "recipient": "01958122300",
  "body": "ID: 017XXXXXXXX Employee Name"
}
```

Accepted alternate field names already supported by the controller:

| Meaning | Supported body fields |
|---|---|
| Sender | `sender`, `from`, `msisdn` |
| Recipient | `recipient`, `to`, `destination` |
| Message | `body`, `message`, `text`, `sms` |

### VERIFIED

The SMS parser accepts all of these examples:

```text
ID: 01712345678 Karim Uddin
ID: Karim Uddin 01712345678
ID : 01712345678 Karim Uddin
I D : Karim Uddin 01712345678
```

The backend extracts the Bangladesh mobile number from anywhere after the `ID` prefix and uses the remaining text as the employee name.

### VERIFIED

Successful backend response includes data for the gateway to send:

```json
{
  "success": true,
  "data": {
    "status": "created",
    "employeeCode": "EMP###",
    "replyTo": "017XXXXXXXX",
    "replyMessage": "Welcome ...\nID: EMP###\nPass: ...\nAPK: https://locationwhere.iamazim.com/downloads/app-debug.apk"
  }
}
```

### VERIFIED

Duplicate registration returns the existing employee code instead of creating another employee:

```json
{
  "status": "duplicate",
  "employeeCode": "EMP###",
  "replyMessage": "This number is already registered as EMP###."
}
```

### VERIFIED

If the sender number does not match the mobile number inside the `ID` SMS, the API returns:

```text
The mobile number in the ID SMS must match the sender number.
```

### LIKELY

Because the database requires `Employee.fullName`, SMS-created records use the phone number as the initial placeholder name when no name is supplied. Admins can update the real employee name later from the dashboard.

---

## 6. Android smsgateway App Configuration

### RECOMMENDATION

Configure the administrator phone's Android `smsgateway` app as follows:

| Setting | Value |
|---|---|
| Webhook URL | `https://locationwhere.iamazim.com/api/v1/employees/onboarding/sms` |
| HTTP method | `POST` |
| Header | `x-gateway-secret: <same value as SMS_GATEWAY_SECRET>` |
| Content type | `application/json` |

Suggested JSON template:

```json
{
  "sender": "{{from}}",
  "recipient": "01958122300",
  "body": "{{message}}"
}
```

The Android gateway should send the SMS reply using:

```text
To: data.replyTo
Message: data.replyMessage
```

### RECOMMENDATION

Set or verify these backend environment values before production use:

```text
ADMIN_ONBOARDING_PHONE=01958122300
APK_DOWNLOAD_URL=https://locationwhere.iamazim.com/downloads/app-debug.apk
SMS_GATEWAY_SECRET=<strong random secret>
```

Do not add:

```text
SMS_API_TOKEN
SMS_SID
SMS_DOMAIN
TWILIO_*
```

unless the owner explicitly approves an external SMS provider later.

---

## 7. LocationWhere nginx and Frontend Deployment

### VERIFIED

The previous P0 finding said active nginx served:

```text
/home/azim/locationwhere-frontend/
```

That is no longer true for the active LocationWhere site config.

Active nginx file:

```text
/etc/nginx/sites-available/locationwhere.iamazim.com.conf
```

Active root:

```text
/home/azim/location_where/admin-dashboard/dist
```

Enabled symlink:

```text
/etc/nginx/sites-enabled/locationwhere.iamazim.com.conf -> /etc/nginx/sites-available/locationwhere.iamazim.com.conf
```

### VERIFIED

Tracked dashboard build output exists:

```text
/home/azim/location_where/admin-dashboard/dist/index.html
/home/azim/location_where/admin-dashboard/dist/assets/
```

### LIKELY

The old directory still exists:

```text
/home/azim/locationwhere-frontend/
```

It appears obsolete for active nginx serving. Do not delete it until systemd, PM2, cron, deployment scripts, and backup references are checked.

---

## 8. APK Distribution

### VERIFIED

nginx serves downloads from:

```text
/var/www/locationwhere.iamazim.com/downloads/
```

Files currently present:

```text
gateway.apk
app-debug.apk
```

### VERIFIED

The backend default APK URL is:

```text
https://locationwhere.iamazim.com/downloads/app-debug.apk
```

### RECOMMENDATION

Keep `gateway.apk` for the administrator phone and `app-debug.apk` for employees unless release artifacts are renamed. If a signed release APK is introduced, update `APK_DOWNLOAD_URL` and the file in `/var/www/locationwhere.iamazim.com/downloads/` together.

---

## 9. LocationWhere Authentication

### VERIFIED

The employee Android app uses employee code and password login:

```text
POST /api/v1/auth/mobile/login
```

The login request includes:

```text
employeeCode
password
deviceId
optional fcmToken
```

### BUSINESS CONFLICT RESOLVED

The old direct OTP SMS delivery function was disabled because it depended on server-side SMS delivery.

Current behavior:

```text
Direct OTP SMS delivery is disabled; use employee-code/password login or smsgateway-mediated delivery.
```

### RECOMMENDATION

Keep the current employee-code/password login unless the owner explicitly chooses a future smsgateway-mediated OTP design. Do not reintroduce server-side SMS providers.

---

## 10. LocationWhere S3 / Call Recording

### VERIFIED

The backend still supports call log creation independently of recording storage.

### VERIFIED

Recording upload and signed URL retrieval now check required S3 environment values before calling AWS SDK:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
AWS_S3_BUCKET
```

If these are missing, the backend returns a clear error:

```text
Call recording storage is not configured
```

### RECOMMENDATION

Decide whether call recording is required in production:

1. If required, configure real S3 credentials and bucket.
2. If not required, keep the graceful failure and hide recording upload/download UI where appropriate.
3. Do not let missing S3 credentials crash the application.

---

## 11. Remaining fazle-core Risks

### LIKELY

The earlier audit identified possible fazle-core schema mismatches:

```text
wbom_escort_programs.shift_type
wbom_employees.contact_id
```

These were not changed during the LocationWhere SMS work.

### BLOCKER

Do not run schema changes until the live database, code references, migrations, and backup plan are verified.

### RECOMMENDATION

Investigate with read-only evidence first:

```bash
rg -n "shift_type|contact_id" /home/azim/core
```

Then inspect PostgreSQL columns and migration history before deciding whether to add columns or remove stale code references.

---

## 12. Internal Notification Risk

### LIKELY

The previous audit reported that some internal/admin notifications may be incorrectly suppressed when:

```text
AUTO_REPLY_ENABLED=false
```

This was not investigated or patched during the LocationWhere work.

### RECOMMENDATION

Validate whether operational notifications depend on customer-facing safe mode. Internal administrative messages should not be blocked by customer auto-reply settings.

---

## 13. PM2 Restart Count

### RECOMMENDATION

Treat PM2 restart counts as historical operational data. Do not recreate or reset PM2 processes just for cosmetic cleanup.

Only restart or recreate `locationwhere-backend` if operationally justified, and preserve uptime where possible.

---

## 14. Node.js Lifecycle

### RECOMMENDATION

Plan Node.js runtime upgrades separately from this SMS onboarding fix. Do not perform a major runtime upgrade without rollback steps, dependency rebuild planning, and service restart approval.

---

## 15. Resource Monitoring

### RECOMMENDATION

Continue monitoring:

```text
RAM
swap
CPU
ollama consumption
media processing services
```

Escalate only when evidence shows actual resource pressure.

---

## 16. Key File Paths

| Purpose | Path |
|---|---|
| fazle-core app root | `/home/azim/core/` |
| LocationWhere repo root | `/home/azim/location_where/` |
| LocationWhere backend | `/home/azim/location_where/backend/` |
| LocationWhere backend env | `/home/azim/location_where/backend/.env` |
| LocationWhere SMS onboarding service | `/home/azim/location_where/backend/src/modules/employee/employee.service.ts` |
| LocationWhere SMS parser | `/home/azim/location_where/backend/src/modules/employee/employee.utils.ts` |
| LocationWhere auth service | `/home/azim/location_where/backend/src/modules/auth/auth.service.ts` |
| LocationWhere call service | `/home/azim/location_where/backend/src/modules/call/call.service.ts` |
| LocationWhere dashboard build | `/home/azim/location_where/admin-dashboard/dist/` |
| LocationWhere employee Android app | `/home/azim/location_where/app/` |
| LocationWhere downloads | `/var/www/locationwhere.iamazim.com/downloads/` |
| LocationWhere nginx config | `/etc/nginx/sites-available/locationwhere.iamazim.com.conf` |
| LocationWhere PM2 config | `/home/azim/location_where/backend/ecosystem.locationwhere.config.cjs` |
| vps-config repo | `/home/azim/vps-config-git/` |

---

## 17. Current Action Order

| Priority | Action | Status |
|---|---|---|
| P0 | Align LocationWhere SMS onboarding with Android smsgateway architecture | Done in code; needs live env/restart if deploying |
| P0 | Remove external SMS provider assumptions from LocationWhere backend | Done in source/dist/env examples |
| P0 | Configure Android smsgateway app webhook and secret | Pending device configuration |
| P1 | Set `SMS_GATEWAY_SECRET`, `ADMIN_ONBOARDING_PHONE`, and `APK_DOWNLOAD_URL` in live backend env | Pending approval/change window |
| P1 | Decide whether S3 call recording is required | Pending owner decision |
| P1 | Investigate fazle-core escort schema references | Pending read-only audit |
| P1 | Validate internal notification safe-mode behavior | Pending read-only audit |
| P2 | Archive old draft backlog only after retention decision | Pending policy decision |
| P2 | Plan Node.js lifecycle upgrade | Pending maintenance plan |

---

## 18. Final Classification

### VERIFIED

- LocationWhere active source is `/home/azim/location_where/`.
- Active LocationWhere nginx root points to `/home/azim/location_where/admin-dashboard/dist`.
- SMS onboarding route exists at `/api/v1/employees/onboarding/sms`.
- The backend now parses flexible `ID` / `I D` employee SMS formats with the mobile number before or after the employee name.
- The backend no longer sends onboarding SMS directly.
- External SMS provider code and env examples were removed from LocationWhere backend source/dist/examples.
- Backend build passed with `npm run build`.
- APK download files exist under `/var/www/locationwhere.iamazim.com/downloads/`.

### LIKELY

- `/home/azim/locationwhere-frontend/` is obsolete but should not be deleted until deployment references are fully checked.
- Employee-code/password login is the safest current employee authentication design under the no-server-SMS rule.

### BLOCKER

- Live deployment still requires explicit approval before editing `.env`, restarting PM2, changing nginx, or running migrations.

### RECOMMENDATION

- Configure the Android `smsgateway` app first.
- Then set the live gateway environment values and restart `locationwhere-backend` during an approved window.
- Do not configure SSL Wireless, Twilio, or any other external SMS provider unless the owner explicitly changes the business rule.

---

*Report rewritten 2026-06-17 after LocationWhere SMS onboarding implementation and flexible `ID` SMS parser update. Production-destructive actions were not performed during this rewrite.*
