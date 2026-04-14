import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import config


class EmailSender:
    def send_confirmation(self, to_email: str, post_url: str, post_title: str):
        """Send an HTML confirmation email with the live Substack post link."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Your Substack post is live: {post_title}"
        msg["From"] = config.EMAIL_SENDER
        msg["To"] = to_email

        text_body = (
            f"Your Substack post is live!\n\n"
            f"Title: {post_title}\n"
            f"Link: {post_url}\n\n"
            f"Published by the LinkedIn → Substack Agent."
        )

        html_body = f"""
<html>
<body style="font-family: Georgia, serif; max-width: 600px; margin: 0 auto; padding: 24px; color: #333;">
  <div style="border-bottom: 3px solid #FF6719; padding-bottom: 12px; margin-bottom: 24px;">
    <h2 style="color: #FF6719; margin: 0; font-family: sans-serif;">
      Your post is live on Substack
    </h2>
  </div>

  <p style="font-size: 1rem; line-height: 1.6;">
    Your LinkedIn post has been successfully converted and published as a Substack article.
  </p>

  <p style="font-size: 1rem;">
    <strong>Title:</strong> {post_title}
  </p>

  <div style="text-align: center; margin: 32px 0;">
    <a href="{post_url}"
       style="background-color: #FF6719; color: white; padding: 14px 32px;
              text-decoration: none; border-radius: 4px; font-weight: bold;
              font-family: sans-serif; font-size: 1rem;">
      Read on Substack
    </a>
  </div>

  <p style="color: #888; font-size: 0.85rem; font-family: sans-serif;">
    Or copy this link:
    <a href="{post_url}" style="color: #FF6719;">{post_url}</a>
  </p>

  <hr style="border: none; border-top: 1px solid #eee; margin-top: 32px;" />
  <p style="color: #bbb; font-size: 0.8rem; font-family: sans-serif; text-align: center;">
    Sent by LinkedIn → Substack Agent
  </p>
</body>
</html>
"""

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        print(f"[email] connecting to Gmail SMTP...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config.EMAIL_SENDER, config.EMAIL_APP_PASSWORD)
            server.sendmail(config.EMAIL_SENDER, to_email, msg.as_string())
        print(f"[email] sent OK → {to_email} | subject='{msg['Subject'][:60]}'")

