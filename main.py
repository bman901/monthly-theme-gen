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
from fuzzywuzzy import fuzz

now = datetime.datetime.now(pytz.timezone("Australia/Brisbane"))
if now.day != 1:
    print("⏳ Not the 1st of the month – skipping theme generation.")
    exit()

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

def is_similar(new_subject, existing_subjects, threshold=85):
    return any(fuzz.partial_ratio(new_subject.lower(), old.lower()) >= threshold for old in existing_subjects)

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


def fetch_recent_subjects(segment, months_back=6):
    now = datetime.datetime.now(pytz.timezone("Australia/Brisbane"))
    headers = {"Authorization": f"Bearer {AIRTABLE_PAT}"}
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"

    recent_months = []
    for i in range(months_back):
        dt = now - datetime.timedelta(days=30 * i)
        recent_months.append(dt.strftime("%B %Y"))

    formula = f"AND(Segment = '{segment}', OR({','.join([f\"Month = '{m}'\" for m in recent_months])}))"
    params = {"filterByFormula": formula}

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print("⚠️ Error fetching recent subjects:", response.text)
        return []

    records = response.json().get("records", [])
    return [
        record["fields"].get("Subject", "").strip()
        for record in records
        if "Subject" in record["fields"]
    ]



def build_prompt(segment, extra_prompt=""):
    month_year = get_month_year()

    if segment == "Pre-Retiree":
        persona_context = (
            f"""Tailor these suggestions to pre-retirees like Paul and Lisa Harrington—smart, capable Australians in their late 50s juggling work, family, and finances. They're time-poor but financially comfortable. They have ~$1.3m in super, savings, and a mortgage nearly paid off. Their goal is lifestyle flexibility—travel, time with family, scaling back work—but they want to feel confident they’re making smart decisions with their resources.
            Themes should help this group:
            - Simplify complex financial lives (e.g. multiple super accounts, offset loans, investments, business equity)
            - Know when and how to reduce work without risking future security
            - Understand what “getting it right” looks like for people who’ve done well but aren’t sure they’re on track
            - Avoid procrastination and get clarity before it’s too late
            - Frame advice as a strategic edge, not a remedial fix"""
        )
    elif segment == "Retiree":
        persona_context = (
            f"""Tailor these suggestions to recent retirees like Alan and Margaret Rowe—calm, organised Australians in their mid-to-late 60s who have done well financially and are now focused on drawing down wisely. Alan is systems-focused and likes logic and models. Margaret is reflective and values connection and clarity. They have ~$1.7m in super (in pension phase), term deposits, and shares, and no mortgage. They value independence, simplicity, and knowing they’re doing it right.
            Themes should help this group:
            - Draw confidently from super without fear of running out
            - Understand the ripple effects of gifting, downsizing, or market volatility
            - Simplify estate planning and administrative complexity
            - Balance being careful with actually enjoying their money
            - Reduce decision fatigue through visual models or steady plans
            - Frame advice as a safeguard for the future, not a disruption to the present"""
        )
    else:
        persona_context = ""

    prompt = (
        persona_context +
        f"""You are helping develop monthly marketing email themes for Hatch Financial Planning, based in Logan, Queensland. These are not full emails—just high-quality topic ideas that could later be expanded into full emails.

        Audience: Financially successful Australians with $1M+ in investable assets, excluding their home. They’re thoughtful, time-poor, and value clarity and confidence in their financial decision-making. They’re not asking “Can we retire?” but “Can we afford to say yes to the life we want?”

        Your task: Generate 3–5 email themes tailored to this audience. Each theme must include:
        - Subject: A short, clear subject line (no clever wordplay, clickbait, or vague headlines)
        - Description: One concise sentence explaining what the email would help the reader understand, solve, or reflect on. It must clearly address a specific belief, pain point, or opportunity.

        Tone: Professional, plainspoken, and reassuring. Use clear, conversational Australian English. Avoid sales language, fluff, financial jargon, or technical complexity. These readers want clarity, not overwhelm.

        Focus areas you can explore:
        - Timing and decision-making
        - Superannuation use and drawdown
        - Complexity vs. clarity in financial life
        - Confidence vs. second-guessing
        - Trade-offs around retirement lifestyle (e.g. travel, gifting, scaling back work)
        - Regret avoidance and peace of mind
        - Missed opportunities or hidden risks
        - Managing market or health uncertainty
        - Balancing logic with personal values

        Guidelines:
        - Include at least one theme tied to common financial planning questions or concerns specific to the current month: {month_year}.
        - Use only standard hyphens (-), not em or en dashes.
        - Format your output exactly like this for each theme:
        Subject: [subject line]
        Description: [one sentence explanation]
        - Return only the themes—no commentary, lists, or explanations before or after.

        {extra_prompt}"""
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
            <p style='margin-top: 30px;'>Visit the Streamlit app to choose your campaign topics or add your own: <a href='https://hfp-monthly-theme-selector.streamlit.app/' target='_blank'>Open Theme Selector</a>.</p>
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
        recent_subjects = fetch_recent_subjects(segment)
        print(f"Found {len(old_themes)} reusable themes")
        new_themes = generate_new_themes(segment)
        deduped_themes = [(s, d) for s, d in new_themes
                          if not is_similar(s, recent_subjects)]

        # If less than 3 new usable themes, ask GPT to try again with a more creative prompt
        max_retries = 3
        retries = 0
        while len(deduped) < 3 and retries < 3:
            print("🔁 Not enough themes – retrying...")
            extra = "Add more unconventional or creative themes this time."
            retry = generate_new_themes(segment)
            for s, d in retry:
                if not is_similar(s, recent_subjects):
                    deduped.append((s, d))
                    recent_subjects.append(s)
            retries += 1

        import random
        reusable = random.sample(old_themes, min(2, len(old_themes)))
        final_set = reusable + deduped[:3]

        store_themes_in_airtable(segment, final_set)
        summary_lines.append(f"{segment} themes:\n" + "\n".join([f"{s} – {d}" for s, d in final_set]))

    notify_editor("\n\n".join(summary_lines))


if __name__ == "__main__":
    run_monthly_theme_generation()
