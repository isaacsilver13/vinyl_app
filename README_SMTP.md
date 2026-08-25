SMTP via Gmail — Vinyl

Overview
- This folder contains a tiny Node-based test harness using `nodemailer` to validate Gmail SMTP settings.

Files
- `package.json` — declares `nodemailer` + `dotenv` and a `test-send` script.
- `test_send.js` — loads env from `vinyl/.env` (via `dotenv`) and sends a test email.

Prerequisites
- Node.js installed (>=14 recommended)
- You already created a Gmail account and generated an App Password (example: `APP_PASSWORD`).
- Place SMTP values in `vinyl/.env` or export them into your shell. See example below.

Recommended `vinyl/.env` values (do NOT commit secrets):

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=vinylupdates67@gmail.com
SMTP_PASSWORD=APP_PASSWORD
SMTP_USE_TLS=1
ALERT_FROM_EMAIL=vinylupdates67@gmail.com
ALERT_FROM_NAME=Vinyl Alerts
SMTP_TEST_TO=your_receive_address@example.com
SMTP_DEBUG=0
```

Install and run (PowerShell):

```powershell
cd vinyl
npm install

# Temporary env for a single command (PowerShell)
$env:SMTP_HOST='smtp.gmail.com'; $env:SMTP_PORT='587'; $env:SMTP_USERNAME='vinylupdates67@gmail.com'; $env:SMTP_PASSWORD='APP_PASSWORD'; $env:ALERT_FROM_EMAIL='vinylupdates67@gmail.com'; $env:SMTP_TEST_TO='vinylupdates67@gmail.com'; node test_send.js

# Or use the npm script after install
npm run test-send
```

Troubleshooting
- Authentication errors: confirm the App Password is for `vinylupdates67@gmail.com` and 2FA is enabled on the account.
- If using port 465 set `SMTP_PORT=465` and `SMTP_USE_TLS=1`.
- For CI / deploy, set secrets with your host's secret manager (Fly, GitHub Actions, etc.).
