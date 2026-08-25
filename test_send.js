// test_send.js - lightweight nodemailer test for Vinyl
// Usage: set env vars or create vinyl/.env, then: npm install && node test_send.js

const dotenv = require('dotenv');
dotenv.config();

const nodemailer = require('nodemailer');

const host = process.env.SMTP_HOST || 'smtp.gmail.com';
const port = parseInt(process.env.SMTP_PORT || '587', 10);
const user = process.env.SMTP_USERNAME;
const pass = process.env.SMTP_PASSWORD;
const from = process.env.ALERT_FROM_EMAIL || user;
const fromName = process.env.ALERT_FROM_NAME || 'Vinyl Alerts';
const to = process.env.SMTP_TEST_TO || user;
const useTls = (process.env.SMTP_USE_TLS || '1') === '1';
const debug = (process.env.SMTP_DEBUG || '0') === '1';

if (!user || !pass) {
  console.error('Missing SMTP_USERNAME or SMTP_PASSWORD in environment.');
  process.exit(1);
}

async function sendTest() {
  const transporter = nodemailer.createTransport({
    host,
    port,
    secure: port === 465, // true for 465, false for 587
    requireTLS: useTls,
    auth: { user, pass },
    logger: debug,
    debug: debug,
  });

  await transporter.verify();
  console.log('SMTP connection verified. Sending test message...');

  const info = await transporter.sendMail({
    from: `"${fromName}" <${from}>`,
    to,
    subject: 'Vinyl SMTP test',
    text: 'This is a test message from Vinyl (nodemailer).',
    html: '<p>This is a test message from <b>Vinyl</b> (nodemailer).</p>'
  });

  console.log('Message sent: %s', info.messageId);
}

sendTest().catch(err => {
  console.error('Error sending test email:');
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
