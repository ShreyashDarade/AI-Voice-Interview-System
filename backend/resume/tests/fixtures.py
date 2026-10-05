"""Realistic resume fixtures (generated on the fly)."""
from __future__ import annotations

from datetime import date

from .helpers import PdfBuilder, docx_bytes

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def months_ago(n: int) -> str:
    t = date.today()
    idx = t.year * 12 + t.month - 1 - n
    return f"{MONTHS[idx % 12]} {idx // 12}"


def ym_months_ago(n: int) -> str:
    t = date.today()
    idx = t.year * 12 + t.month - 1 - n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


# ------------------------------------------------------------------ 1. fresher (DOCX)

def fresher_docx() -> bytes:
    def build(d):
        d.add_heading("Rahul Verma", level=0)
        d.add_paragraph("rahul.verma@university.edu | +91 91234 56780 | Pune, Maharashtra")
        d.add_paragraph("github.com/rahulv | linkedin.com/in/rahul-verma-8a1b2c")
        d.add_paragraph("Date of Birth: 12 March 2002 | Gender: Male | Nationality: Indian")
        d.add_heading("OBJECTIVE", level=1)
        d.add_paragraph("Final year computer engineering student seeking a backend development role.")
        d.add_heading("EDUCATION", level=1)
        d.add_paragraph("B.E. in Computer Engineering, Savitribai Phule Pune University, 2020 - 2024")
        d.add_paragraph("CGPA: 8.9/10")
        d.add_paragraph("Higher Secondary (HSC), Fergusson College, 2018 - 2020, 91.2%")
        d.add_heading("INTERNSHIPS", level=1)
        d.add_paragraph("Software Engineering Intern, Zoho Corporation Pvt Ltd")
        d.add_paragraph("Jun 2023 - Aug 2023")
        d.add_paragraph("Built REST endpoints with Java and Spring Boot backed by MySQL", style="List Bullet")
        d.add_paragraph("Wrote JUnit tests and fixed 25 bugs across the billing module", style="List Bullet")
        d.add_paragraph("Data Analyst Intern, Mu Sigma")
        d.add_paragraph("Dec 2022 - Jan 2023")
        d.add_paragraph("Cleaned and analysed sales data using Python and Pandas", style="List Bullet")
        d.add_heading("PROJECTS", level=1)
        d.add_paragraph("Smart Attendance System | Python, OpenCV, Flask")
        d.add_paragraph("Face-recognition attendance system used by 3 departments", style="List Bullet")
        d.add_paragraph("Deployed with Docker on a college server", style="List Bullet")
        d.add_paragraph("Library Management App (Java, MySQL)")
        d.add_paragraph("Desktop application for issuing and returning books", style="List Bullet")
        d.add_heading("SKILLS", level=1)
        d.add_paragraph("Languages: Java, Python, C, SQL")
        d.add_paragraph("Tools: Git, Docker, Linux, Postman")
        d.add_heading("CERTIFICATIONS", level=1)
        d.add_paragraph("AWS Certified Cloud Practitioner - Amazon Web Services, 2023")
        d.add_heading("LANGUAGES", level=1)
        d.add_paragraph("English (Fluent), Hindi (Native), Marathi")
    return docx_bytes(build)


# ------------------------------------------------------------------ 2. mid-level with overlapping roles (PDF)

def midlevel_pdf():
    """Returns (bytes, expectations dict)."""
    a_start, a_end = months_ago(54), months_ago(24)
    b_start = months_ago(30)
    b = PdfBuilder()
    y = b.flow(50, 60, [
        ("Marcus Chen", 22, True),
        "marcus.chen@proton.me | (415) 555-0132 | San Francisco, CA",
        "github.com/mchen-dev",
        "",
        ("PROFESSIONAL SUMMARY", 12, True),
        "Backend developer with 4+ years of experience designing APIs and data pipelines.",
        "",
        ("WORK EXPERIENCE", 12, True),
        (f"Software Developer | Pied Piper Inc. | Palo Alto, CA", 10.5, True),
        f"{a_start} - {a_end}",
        "• Developed microservices in Java and Spring Boot with PostgreSQL and Kafka",
        "• Mentored 3 junior engineers and reduced build times by 35% using Jenkins pipelines",
        (f"Backend Developer | Initech LLC | San Francisco, CA", 10.5, True),
        f"{b_start} - Present",
        "• Designed GraphQL and REST APIs in Python (FastAPI) serving 2M requests per day",
        "• Migrated services to AWS (ECS, S3, SQS) and Terraform",
        "",
        ("EDUCATION", 12, True),
        "B.S. in Computer Science, University of Illinois Urbana-Champaign, 2015 - 2019",
        "",
        ("SKILLS", 12, True),
        "Java, Python, SQL, Go, Docker, Kubernetes, Terraform, AWS, Kafka, PostgreSQL, Redis, Git",
    ])
    return b.bytes(), {"a": (ym_months_ago(54), ym_months_ago(24)), "b_start": ym_months_ago(30)}


# ------------------------------------------------------------------ 3. senior two-column PDF with promotions

def senior_two_column_pdf() -> bytes:
    b = PdfBuilder()
    W = 595
    # full-width header band
    b.text(40, 55, "ELENA PETROVA", 24, True)
    b.text(40, 76, "Engineering Leader | Distributed Systems", 11)
    # sidebar (left, dark text on light box)
    b.rect(0, 95, 190, 842, fill=(0.93, 0.93, 0.95))
    b.flow(20, 120, [
        ("CONTACT", 11, True),
        "elena.petrova@mail.com",
        "+44 20 7946 0958",
        "London, United Kingdom",
        "linkedin.com/in/elena-petrova",
        "",
        ("SKILLS", 11, True),
        "Java, Kotlin, Go, Python",
        "Kafka, Kubernetes, AWS",
        "PostgreSQL, Cassandra",
        "System Design, Mentoring",
        "",
        ("EDUCATION", 11, True),
        "M.Sc. Computer Science",
        "Imperial College London",
        "2008 - 2010",
        "B.Sc. Mathematics",
        "University of Sofia",
        "2004 - 2008",
        "",
        ("LANGUAGES", 11, True),
        "English (Fluent)",
        "Bulgarian (Native)",
    ], width=150)
    # main column (right)
    y = b.flow(215, 120, [
        ("SUMMARY", 11, True),
        "Principal engineer and manager with 14 years of experience building payment platforms at scale.",
        "",
        ("EXPERIENCE", 11, True),
        ("Globex Corporation, London", 10.5, True),
        ("Principal Engineer                          Mar 2021 - Present", 10.5, True),
        "• Led a team of 12 engineers across 3 squads delivering a real-time payments platform on Kafka and Kubernetes",
        "• Reduced payment failures by 45% through idempotent retries",
        ("Senior Software Engineer                 Jun 2017 - Feb 2021", 10.5, True),
        "• Built event-driven services in Java and Kotlin on AWS handling 4M transactions daily",
        ("Software Engineer                             Aug 2014 - May 2017", 10.5, True),
        "• Developed PostgreSQL-backed settlement services in Java",
        "",
        ("Initech Ltd, Sofia", 10.5, True),
        ("Software Developer                            Sep 2010 - Jul 2014", 10.5, True),
        "• Implemented trading back-office tooling in Java and Python",
    ], width=345)
    return b.bytes()


# ------------------------------------------------------------------ extras

def fresher_txt() -> str:
    return """Ananya Iyer
ananya.iyer@gmail.com | 98450 12345 | Bengaluru, Karnataka

SUMMARY
Recent graduate who loves data and backend systems.

EDUCATION
B.Tech in Information Technology - PES University, 2019 - 2023
CGPA: 9.1/10

EXPERIENCE
Python Developer Intern - Fynd, Mumbai
Jan 2023 - Jun 2023
- Built ETL jobs with Python and Airflow
- Experience with Docker and PostgreSQL

SKILLS
Python, SQL, Docker, R, Git
"""
