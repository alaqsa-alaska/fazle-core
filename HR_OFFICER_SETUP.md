# HR Officer Mobile Payroll Setup Guide

## Overview
This document provides setup instructions for adding two HR Officer users (OfficeAssistant01 and OfficeAssistant02) to the payroll dashboard with mobile-friendly access.

## User Credentials

### OfficeAssistant01
- **API Key**: `fk_oHYBRq0koo2EOuoWCQLNvCmDSuNgdz3oUg8jB2-SPQs`
- **Password/Note**: Office1234 (reference only; API key is the actual authentication)
- **Role**: Operator (can view all, create entries, no edit/delete)
- **Phone (for DB)**: 01700000001

### OfficeAssistant02
- **API Key**: `fk_LVvuQciEYTq2JwfQ9lgcuhB29xErPtIf_ipdQpPvAWE`
- **Password/Note**: Assistant1234 (reference only; API key is the actual authentication)
- **Role**: Operator (can view all, create entries, no edit/delete)
- **Phone (for DB)**: 01700000002

## Setup Steps

### 1. Apply Database Migration

Execute the SQL migration to create the user accounts in the database:

```bash
# From the database host, run:
psql -d postgres -f /path/to/core/migrations/002_add_hr_officers.sql

# Or using Docker:
docker exec <db_container> psql -U postgres -d postgres -f /migrations/002_add_hr_officers.sql
```

Verify successful creation:
```sql
SELECT a.id, a.phone, a.name, a.status, 
       string_agg(ar.role_name, ',') as roles
FROM fazle_admins a
LEFT JOIN fazle_admin_roles ar ON ar.admin_id = a.id
WHERE a.phone IN ('01700000001', '01700000002')
GROUP BY a.id;
```

Expected output:
```
 id |    phone    |      name      | status | roles
----+-------------+----------------+--------+-------
 XX | 01700000001 | OfficeAssistant01 | active | operator
 XX | 01700000002 | OfficeAssistant02 | active | operator
```

### 2. Environment Configuration

The API keys are already configured in `.env` under the "HR Officer Users" section:

```env
HROFFICER_01_NAME=OfficeAssistant01
HROFFICER_01_APIKEY=fk_oHYBRq0koo2EOuoWCQLNvCmDSuNgdz3oUg8jB2-SPQs
HROFFICER_01_PASSWORD=Office1234

HROFFICER_02_NAME=OfficeAssistant02
HROFFICER_02_APIKEY=fk_LVvuQciEYTq2JwfQ9lgcuhB29xErPtIf_ipdQpPvAWE
HROFFICER_02_PASSWORD=Assistant1234
```

### 3. Mobile Payroll Dashboard Access

**URL**: `/app/static/payroll.html`

**Login Process**:
1. Open the payroll dashboard in mobile browser
2. At the login prompt, paste the API Key (not the password)
3. The key will be saved in browser localStorage
4. Dashboard loads with officer's permissions

**Permissions**:
- ✅ View all payroll data, employees, transactions
- ✅ Create income entries (📥 Income tab)
- ✅ Create cash transactions (💵 Cash tab)
- ✅ View reports and statistics
- ❌ Edit existing entries
- ❌ Delete entries
- ❌ Approve/reject items (requires higher roles)

## Mobile UI Improvements

The payroll dashboard has been optimized for mobile devices:

### Responsive Design Features
- **Touch-friendly buttons**: 44px minimum height for easy tapping
- **Adaptive navigation**: Horizontally scrollable tabs that fit all screens
- **Responsive grids**: Stats cards arrange 2 columns on mobile, 4 on desktop
- **Full-width forms**: Income/cash entry forms stack vertically on mobile
- **Touch-optimized input**: Larger font sizes to trigger mobile keyboard properly
- **Scrollable tables**: Tables scroll horizontally on mobile while maintaining usability
- **Safe bottom padding**: Modals and panels account for on-screen keyboards

### Breakpoints
- **768px and below**: Tablet optimizations (smaller padding, adjusted fonts)
- **600px and below**: Mobile optimizations (vertical layouts, large touch targets)
- **480px and below**: Small phone optimizations (further size reduction)
- **Landscape mode**: Special handling for landscape orientation

### Testing
To test mobile responsiveness:

1. **Chrome DevTools**: F12 → Toggle Device Toolbar (Ctrl+Shift+M)
2. **Actual Device**: Open URL on phone/tablet
3. **Landscape Test**: Rotate device to landscape and verify layout

## Use Cases

### Income Entry (📥 Income Tab)
HR Officers can record income received by the company:
- Received from whom (name)
- Income category (Form Fee, Training Fee, etc.)
- Amount received (৳)
- Money receipt number (for reference)
- Who received the money

**Mobile Experience**: Large touch targets, single-column form layout, easy number entry

### Cash Entry (💵 Cash Tab)
Record cash transactions:
- Date and method
- Employee or recipient
- Amount (৳)
- Transaction reference

**Mobile Experience**: Simplified search filters, scrollable transaction table

## Troubleshooting

### Login Issues
- **"Invalid API Key"**: Verify the full key is copied without extra spaces
- **Key not saved**: Check browser localStorage is enabled (Storage → Cookies)
- **Mobile keyboard blocking input**: Try landscape orientation for more space

### Mobile Performance
- **Slow loading**: Clear browser cache (Settings → History → Clear Browsing Data)
- **Touch not responsive**: Ensure minimum 44px button sizes (browser DevTools can verify)
- **Table scrolling issues**: Enable `-webkit-overflow-scrolling: touch` (already done)

### Permissions Issues
- **"Permission denied"**: Verify user has "operator" role in database
- **Can't create entries**: Check RBAC roles assigned in `fazle_admin_roles` table
- **Can't view data**: Ensure user's role has required command permissions

## Maintenance

### Resetting Credentials
If an API key is compromised, issue a new one:

```python
import asyncio
from modules.rbac import issue_api_key
from app.database import init_db, close_db

async def reset_key(phone):
    await init_db()
    try:
        result = await issue_api_key(phone)
        print(f"New API Key: {result['api_key']}")
    finally:
        await close_db()

# Run: asyncio.run(reset_key("01700000001"))
```

### Disabling an Officer
```sql
UPDATE fazle_admins SET status='disabled' WHERE phone='01700000001';
```

### Monitoring Usage
```sql
SELECT * FROM fazle_admin_audit 
WHERE actor_user_id IN (
  SELECT id FROM fazle_admins 
  WHERE phone IN ('01700000001', '01700000002')
)
ORDER BY created_at DESC
LIMIT 50;
```

## Security Notes

1. **API Keys are credentials**: Treat them like passwords
2. **Browser storage**: Keys are stored in localStorage (XSS vulnerable); only use on trusted networks
3. **HTTPS only**: Always use HTTPS in production to prevent key interception
4. **Audit logging**: All officer actions are logged in `fazle_admin_audit` table
5. **Role-based access**: Officer role prevents dangerous operations (edit, delete, approve)

## Configuration Files Modified

- `.env` - Added HR Officer credentials section
- `migrations/002_add_hr_officers.sql` - Database migration for user creation
- `app/static/payroll.html` - Enhanced mobile responsiveness

## Next Steps

1. Run the database migration: `psql -f migrations/002_add_hr_officers.sql`
2. Restart the application to pick up .env changes
3. Test on a mobile device by opening `/app/static/payroll.html`
4. Share the API keys with the HR Officers (securely, not via email)
5. Monitor audit logs for usage patterns
