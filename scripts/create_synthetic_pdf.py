from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

PDF_PATH = "data/loan_eligibility.pdf"

rules = [
    "Banking Loan Eligibility Policy - Synthetic Demo Document",
    "",
    "1. Salaried applicants must have a minimum monthly income of Rs. 30,000.",
    "2. Self-employed applicants must have a minimum monthly income of Rs. 50,000.",
    "3. The minimum credit score required for loan eligibility is 700.",
    "4. Applicants must be between 21 and 60 years old.",
    "5. The maximum debt-to-income ratio allowed is 40 percent.",
    "6. The maximum loan tenure is 20 years for home loans.",
    "7. Applicants must have at least 12 months of stable employment or business history.",
    "8. Missing income proof, identity proof, or address proof can delay approval.",
    "9. Final approval depends on document verification and risk assessment.",
]

pdf = canvas.Canvas(PDF_PATH, pagesize=letter)
width, height = letter
y = height - 72

for rule in rules:
    pdf.drawString(72, y, rule)
    y -= 24

pdf.save()
print(f"Created {PDF_PATH}")