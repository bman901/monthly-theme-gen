# Theme Suggestion Engine (Phase 1 of Build - Normalised + Reuse)
"""
This module will:
- Run on the 1st of each month
- Generate 3–5 campaign themes for each segment (pre-retirees, retirees)
- Reuse themes from the last 6 months that are still unselected/unsent
- Avoid inserting duplicate subject lines
- Store each theme as its own row in Airtable (with subject + description)
- Send a notification email with a summary of the themes
"""

from openai import OpenAI
import requests
import datetime
import os
import pytz
from smtplib import SMTP
from email.mime.text import MIMEText

# now = datetime.datetime.now(pytz.timezone("Australia/Brisbane"))
# if now.day != 1:
#     print("⏳ Not the 1st of the month – skipping theme generation.")
#     exit()

# --- CONFIGURATION ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = "MonthlyThemes"
EMAIL_RECIPIENT = os.getenv("NOTIFY_EMAIL")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


# --- FUNCTIONS ---
def get_month_year():
    now = datetime.datetime.now(pytz.timezone("Australia/Brisbane"))
    return now.strftime("%B %Y")


def fetch_old_themes(segment):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_PAT}"}
    params = {
        "filterByFormula":
        f"AND(Segment = '{segment}', Status != 'selected', Status != 'sent')"
    }
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print("⚠️ Error fetching old themes:", response.text)
        return []
    records = response.json().get("records", [])
    return [(record["fields"].get("Subject"),
             record["fields"].get("Description")) for record in records
            if "Subject" in record["fields"]]


def fetch_all_subjects(segment):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_PAT}"}
    params = {"filterByFormula": f"Segment = '{segment}'"}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print("⚠️ Error fetching all subjects:", response.text)
        return set()
    records = response.json().get("records", [])
    return set(record["fields"].get("Subject") for record in records
               if "Subject" in record["fields"])


def build_prompt(segment, extra_prompt=""):
    month_year = get_month_year()

    if segment == "Pre-Retiree":
        persona_context = (
            "Tailor your suggestions to Paul and Lisa Harrington, a married couple in their late 50s based in South East QLD. "
            "They’re financially comfortable but time-poor, running a homewares business and working part-time as an engineer. "
            "They value family, travel, simplicity, and confidence in their future. "
            "Themes should help them navigate complexity (super, offset, business sale) and feel ready for the next chapter, "
            "without overwhelming them with jargon. "
        )
    elif segment == "Retiree":
        persona_context = (
            "Tailor your suggestions to Alan and Margaret Rowe, a retired couple in their mid to late 60s living in South East QLD. "
            "They are organised savers who now seek peace of mind, simplicity, and clarity in their financial drawdown phase. "
            "Alan is detail-focused and likes logic and models, while Margaret values clarity and emotional connection. "
            "Themes should speak to issues like confident drawdown, managing market risk, gifting to family, staying independent, and simplifying estate planning. "
            "Avoid complexity or jargon—they value advice that’s calm, clear, and deeply aligned with their lifestyle. "
        )
    else:
        persona_context = ""

    prompt = (
        persona_context +
        f"""You are helping develop marketing email themes for Hatch Financial Planning in Logan, Queensland. These are not full emails—just compelling topic ideas that could later be developed into full emails.
        Audience: People aged 50–65 with $1M+ in investable assets, approaching retirement in the next 3–10 years. They’re experienced professionals and business owners who value clarity and confidence about their financial future. They’re asking not “Can we retire?” but “Can we afford to say yes to the life we want?”
        Your job: Generate 3-5 high-quality email themes for {segment.lower()} clients. Each theme should include:
        Subject: A clear, honest subject line for the email (not clever or cryptic)
        Description: 1-2 sentences explaining what the email would help the reader understand, solve, or reflect on. It should address a specific belief, pain point, or insight.
        Tone: Professional, plainspoken, and useful. No sales language, no fluff. Each theme should feel relevant, reassuring, and practical for someone with complex finances who’s short on time.
        Focus areas to explore:
        Timing and decision-making
        Superannuation use and drawdown
        Business exits and liquidity
        Financial clarity vs. complexity
        Confidence and regret
        Personal goals like travel, family, and flexibility
        Invisible risks or missed opportunities"""
        f"Please do not include any references to Centrelink. "
        f"Use Australian language i.e. not American or British "
        f"Ensure at least one theme is tied to financial planning issues relevant to the month of {month_year}. "
        f"Each theme should include a short subject line followed by a one-sentence description. "
        f"Don't be specific about the persona's situation or names, they're intended to be general in nature "
        f"Please do not use en dashes (–) or em dashes (—); use standard hyphens (-) instead. "
        f"The output should be in the format: 'Subject: ...\\nDescription: ...' for each theme. "
        f"Return only the themes. Do not include explanations or introductory text."
        f"{extra_prompt}"
    )
    return prompt


def generate_new_themes(segment):
    prompt = build_prompt(segment)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": prompt
        }],
        temperature=0.7,
    )
    raw_output = response.choices[0].message.content.strip()
    blocks = [b.strip() for b in raw_output.split("\n\n") if b.strip()]

    themes = []
    seen_subjects = set()
    for block in blocks:
        subject_line = ""
        description_line = ""
        for line in block.split("\n"):
            if line.lower().startswith("subject:"):
                subject_line = line.split(":", 1)[1].strip()
            elif line.lower().startswith("description:"):
                description_line = line.split(":", 1)[1].strip()
        if subject_line and description_line and subject_line not in seen_subjects:
            themes.append((subject_line, description_line))
            seen_subjects.add(subject_line)
    return themes


def store_themes_in_airtable(segment, themes):
    month = get_month_year()
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_PAT}",
        "Content-Type": "application/json"
    }
    for subject, description in themes:
        data = {
            "fields": {
                "Month": month,
                "Segment": segment,
                "Subject": subject,
                "Description": description,
                "Status": "pending"
            }
        }
        response = requests.post(url,
                                 json={"records": [data]},
                                 headers=headers)
        if response.status_code != 200:
            print("⚠️ Error writing to Airtable:", response.text)
        else:
            print(f"✅ Stored theme: {subject}")


def notify_editor(theme_summary):
    subject = f"New Email Themes Ready for {get_month_year()}"

    # Build HTML email body
    body_html = """
    <html>
        <body style='font-family: Arial, sans-serif; color: #333;'>
            <h2>Monthly Email Themes</h2>
            <p>Your monthly email themes have been generated and stored in Airtable.</p>
            <div style='margin-top: 20px;'>
    """
    for block in theme_summary.split("\n\n"):
        if block.strip():
            lines = block.split("\n")
            segment_title = lines[0]
            themes = lines[1:]
            body_html += f"<h3 style='color: #005580;'>{segment_title}</h3><ul>"
            for theme in themes:
                body_html += f"<li>{theme}</li>"
            body_html += "</ul>"

    body_html += """
            </div>
            <p style='margin-top: 30px;'>Visit the Streamlit app to choose your campaign topics: <a href='https://hfp-monthly-theme-selector.streamlit.app/' target='_blank'>Open Theme Selector</a>.</p>
            <p>– Automated Assistant</p>
        </body>
    </html>
    """

    msg = MIMEText(body_html, "html")
    msg["Subject"] = subject
    msg["From"] = SMTP_USERNAME
    recipient_list = [email.strip() for email in EMAIL_RECIPIENT.split(",")]
    msg["To"] = ", ".join(recipient_list)
    with SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.sendmail(SMTP_USERNAME, recipient_list, msg.as_string())
        print(f"📬 Notification email sent to {', '.join(recipient_list)}")


# --- RUNNING THE TASK ---
def run_monthly_theme_generation():
    summary_lines = []
    for segment in ["Pre-Retiree", "Retiree"]:
        print(f"Processing segment: {segment}")
        old_themes = fetch_old_themes(segment)
        all_subjects = fetch_all_subjects(segment)
        print(f"Found {len(old_themes)} reusable themes")
        new_themes = generate_new_themes(segment)
        deduped_themes = [(s, d) for s, d in new_themes
                          if s not in all_subjects]

        # If less than 3 new usable themes, ask GPT to try again with a more creative prompt
        max_retries = 3
        retries = 0
        while len(deduped_themes) < 3 and retries < max_retries:
            print(
                "🔁 Not enough new themes, regenerating with broader creativity..."
            )
            extra_prompt = f" Think outside the box, include more creative or unconventional themes that might still be valuable."
            raw_retry = client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": build_prompt(segment, extra_prompt)
                }],
                temperature=0.8,
            )
            retry_output = raw_retry.choices[0].message.content.strip()
            retry_blocks = [
                b.strip() for b in retry_output.split("\n\n") if b.strip()
            ]
            for block in retry_blocks:
                subject_line = ""
                description_line = ""
                for line in block.split("\n"):
                    if line.lower().startswith("subject:"):
                        subject_line = line.split(":", 1)[1].strip()
                    elif line.lower().startswith("description:"):
                        description_line = line.split(":", 1)[1].strip()
                if subject_line and description_line and subject_line not in all_subjects:
                    deduped_themes.append((subject_line, description_line))
                    all_subjects.add(subject_line)
                if len(deduped_themes) >= 3:
                    break
            retries += 1

        import random
        reusable_sample = random.sample(old_themes, min(2, len(old_themes)))
        combined = reusable_sample + deduped_themes[:3]
        store_themes_in_airtable(segment, combined)
        summary_lines.append(f"{segment} themes:\n" +
                             "\n".join([f"{s} - {d}" for s, d in combined]))
    notify_editor("\n\n".join(summary_lines))


if __name__ == "__main__":
    run_monthly_theme_generation()
