from dotenv import load_dotenv
import os
import json

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
from flask import Flask, flash, render_template, request, redirect, url_for, session, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
import io
import zipfile
import subprocess
import tempfile

try:
    from weasyprint import HTML
except:
    HTML = None
from flask import redirect, url_for, flash
from config import COMPANIES
from werkzeug.security import generate_password_hash, check_password_hash
from humanize import intword
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import pickle
import uuid
from num2words import num2words

app = Flask(__name__)
app.secret_key = "super-secret-key"

if DATABASE_URL:
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    # Local development – use MySQL
    app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://LiteCode:LiteCode%400804@localhost/lc_lms'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, "generated_docs")
# Google Drive Configuration
app.config['GOOGLE_DRIVE_TOKEN_FOLDER'] = os.path.join(app.root_path, "tokens")
os.makedirs(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], exist_ok=True)

# Google OAuth Configuration - Use environment variables directly
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
REDIRECT_URI = os.getenv('OAUTH_REDIRECT_URI', 
    'https://doc-generator-z2b2.onrender.com/oauth2callback' if os.getenv('RENDER') else 'http://localhost:5000/oauth2callback')

if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    print("Warning: Google OAuth credentials not found in environment variables")
    # You can add fallback logic here if needed
else:
    # Create client config from environment variables
    client_config = {
        "client_id": GOOGLE_CLIENT_ID,
        "project_id": os.getenv('GOOGLE_PROJECT_ID', ''),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uris": [REDIRECT_URI]
    }

SCOPES = ['https://www.googleapis.com/auth/drive.file']
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], "employee_documents"), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], "profiles"), exist_ok=True)


db = SQLAlchemy(app)
migrate = Migrate(app, db)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

#Admin model
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)   
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class IncrementHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    
    old_ctc = db.Column(db.Float, nullable=False)
    increment_amount = db.Column(db.Float, nullable=False)
    new_ctc = db.Column(db.Float, nullable=False)
    
    effective_date = db.Column(db.Date)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    generated_by = db.Column(db.String(100))
    
    employee = db.relationship('Employee', backref='increment_history')

#employee model
class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(20), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    gender = db.Column(db.String(20), nullable=True)
    address = db.Column(db.Text, nullable=True)
    aadhar_no = db.Column(db.String(20), unique=True)
    pan_no = db.Column(db.String(20), unique=True)
    designation = db.Column(db.String(100))
    department = db.Column(db.String(100))
    drive_folder_id = db.Column(db.String(100), nullable=True)  # To store Google Drive folder ID for employee documents
    # ctc is now a computed property; base_ctc stores the starting value
    base_ctc = db.Column(db.Float, default=0)
    joining_date = db.Column(db.Date, nullable=True)
    resignation_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default='active')  # active, resigned, terminated
    profile_image = db.Column(db.String(200), nullable=True)  # For employee photo
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    resignation_email_content = db.Column(db.Text, nullable=True)  # To store resignation email content for resignation acceptance letter
    resignation_datetime = db.Column(db.DateTime, nullable=True)  # To store resignation email date and time for resignation acceptance letter
    relieving_date = db.Column(db.Date, nullable=True)  # To store calculated relieving date for resignation acceptance letter
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=True)
    company = db.relationship('Company', backref='employees')
    
    # Bank Details
    account_holder = db.Column(db.String(100))
    account_number = db.Column(db.String(50))
    bank_name = db.Column(db.String(100))
    branch = db.Column(db.String(100))
    ifsc_code = db.Column(db.String(20))
    
    # Relationships
    documents = db.relationship('Document', backref='employee', lazy=True)
    #increment_history = db.relationship('IncrementHistory', backref='employee', lazy=True)

    @property
    def ctc(self):
        """Calculate current CTC based on base_ctc and increments"""
        total_increment = sum([inc.increment_amount for inc in self.increment_history])
        base = self.base_ctc if self.base_ctc is not None else 0
        return base + (total_increment * 12)
    
# Document Model to Track Generated Documents
class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    document_type = db.Column(db.String(50))
    filename = db.Column(db.String(200))
    file_path = db.Column(db.String(500))
    month = db.Column(db.String(20), nullable=True)
    year = db.Column(db.Integer, nullable=True)
    generated_at = db.Column(db.DateTime, default=datetime.now)
    generated_by = db.Column(db.String(80))
    drive_file_id = db.Column(db.String(100), nullable=True)  # To store Google Drive file ID

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=True)
    amount = db.Column(db.Float, default=0)          # total amount due
    paid_amt = db.Column(db.Float, default=0)        # amount actually paid
    overdue_amount = db.Column(db.Float, default=0)  # amount overdue (if any)
    paid_date = db.Column(db.Date, nullable=True)    # when payment was made
    
    employee = db.relationship('Employee', backref='payments')
    document = db.relationship('Document', backref='payment')

#company module
class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.Text)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(100))
    website = db.Column(db.String(200))
    logo = db.Column(db.String(200))        # filename in static/images
    signature = db.Column(db.String(200))   # filename in static/images/signatures
    hr_name = db.Column(db.String(100))
    hr_designation = db.Column(db.String(100))
    hr_email = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)
    notice_period = db.Column(db.String(50), nullable=True)
    email_domain = db.Column(db.String(100), nullable=True)

#function for production
def html_to_pdf(html_content, output_path):
    try:
        HTML(string=html_content).write_pdf(output_path)
        return True
    except Exception as e:
        print("WeasyPrint error:", e)
        return False

def calculate_salary_components(ctc, increment_per_month=0, paid_days=30, month_days=30):
    """
    Calculate all salary components correctly with no rounding errors
    """
    print(f"\n🔍 DEBUG - calculate_salary_components called with:")
    print(f"  ctc: {ctc}")
    print(f"  paid_days: {paid_days}")
    print(f"  month_days: {month_days}")
    
    # Monthly CTC with increment
    monthly_ctc = round(ctc / 12)
    monthly_ctc_after_increment = monthly_ctc + increment_per_month
    
    # IMPORTANT FIX: Ensure pro-ration factor is NEVER > 1
    if paid_days > month_days:
        print(f"  ⚠️ WARNING: paid_days ({paid_days}) > month_days ({month_days}), capping at {month_days}")
        paid_days = month_days
    
    pro_ration_factor = paid_days / month_days if month_days > 0 else 1
    print(f"  pro_ration_factor: {pro_ration_factor}")
    
    # Calculate FULL month values with consistent rounding
    basic_full = round(monthly_ctc_after_increment * 0.5)
    hra_full = round(basic_full * 0.5)  # 50% of basic
    conveyance_full = round(monthly_ctc_after_increment * 0.05)
    medical_full = round(monthly_ctc_after_increment * 0.014)
    telephone_full = round(monthly_ctc_after_increment * 0.02)
    
    # Sum of all fixed components
    sum_fixed = basic_full + hra_full + conveyance_full + medical_full + telephone_full
    
    # Special allowance is the balancing figure to reach monthly CTC
    special_allowance_full = monthly_ctc_after_increment - sum_fixed
    
    # Apply pro-ration to ALL components including special allowance
    basic = round(basic_full * pro_ration_factor)
    hra = round(hra_full * pro_ration_factor)
    conveyance = round(conveyance_full * pro_ration_factor)
    medical = round(medical_full * pro_ration_factor)
    telephone = round(telephone_full * pro_ration_factor)
    special_allowance = round(special_allowance_full * pro_ration_factor)
    
    # RECALCULATE special allowance to ensure exact total
    # This is the key fix - recalculate special allowance to balance the pro-rated total
    sum_pro_rated = basic + hra + conveyance + medical + telephone
    expected_total = round(monthly_ctc_after_increment * pro_ration_factor)
    special_allowance = expected_total - sum_pro_rated
    
    # GROSS EARNINGS - now should equal expected_total exactly
    gross_earnings = basic + hra + conveyance + medical + telephone + special_allowance
    
    # DEDUCTIONS
    professional_tax = 200
    pf_amount = 0  # PF set to 0 as requested
    income_tax = 0
    
    # GROSS DEDUCTIONS
    gross_deductions = professional_tax + pf_amount + income_tax
    
    # NET SALARY
    net_salary = gross_earnings - gross_deductions
    
    print(f"  Expected total: {expected_total}")
    print(f"  Sum of pro-rated components: {sum_pro_rated}")
    print(f"  Recalculated Special Allowance: {special_allowance}")
    print(f"  GROSS EARNINGS: {gross_earnings}")
    print(f"  Professional Tax: {professional_tax}")
    print(f"  PF: {pf_amount}")
    print(f"  GROSS DEDUCTIONS: {gross_deductions}")
    print(f"  NET SALARY: {net_salary}")
    print(f"  VERIFICATION: {gross_earnings} == {expected_total} ? {gross_earnings == expected_total}")
    
    return {
        # Earnings
        'basic': basic,
        'hra': hra,
        'conveyance': conveyance,
        'medical': medical,
        'telephone': telephone,
        'special_allowance': special_allowance,
        'gross_earnings': gross_earnings,
        
        # Deductions
        'professional_tax': professional_tax,
        'pf_amount': pf_amount,
        'income_tax': income_tax,
        'gross_deductions': gross_deductions,
        
        # Net
        'net_salary': net_salary,
        
        # Full month values
        'basic_full': basic_full,
        'hra_full': hra_full,
        'monthly_ctc': monthly_ctc_after_increment,
        'expected_total': expected_total
    }

def calculate_annual_income_tax(annual_ctc):
    """
    Calculate annual income tax based on old tax regime
    (You may want to update this based on current financial year)
    """
    # Standard deduction
    taxable_income = max(0, annual_ctc - 50000)
    
    # Tax slabs (old regime)
    if taxable_income <= 250000:
        return 0
    elif taxable_income <= 500000:
        return (taxable_income - 250000) * 0.05
    elif taxable_income <= 1000000:
        return 12500 + (taxable_income - 500000) * 0.2
    else:
        return 112500 + (taxable_income - 1000000) * 0.3

#local function
# def html_to_pdf(html_content, output_path):
#     # Path to the standalone WeasyPrint executable (for local Windows)
#     weasyprint_path = os.path.join(app.root_path, 'weasyprint', 'weasyprint.exe')
    
#     with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
#         f.write(html_content)
#         temp_html_path = f.name

#     try:
#         result = subprocess.run(
#             [weasyprint_path, temp_html_path, output_path],
#             capture_output=True,
#             text=True,
#             timeout=30
#         )
#         if result.returncode == 0:
#             return True
#         else:
#             print("WeasyPrint error:", result.stderr)
#             return False
#     except Exception as e:
#         print("WeasyPrint exception:", e)
#         return False
#     finally:
#         if os.path.exists(temp_html_path):
#             os.unlink(temp_html_path)

def get_company_domain(company):
    """Safely get company domain from company object"""
    if not company:
        return 'company.com'
    
    # Check if email_domain field exists and has value
    if hasattr(company, 'email_domain') and company.email_domain:
        return company.email_domain
    
    # Try to extract from company email
    if company.email and '@' in company.email:
        return company.email.split('@')[-1]
    
    # Try to extract from company website
    if company.website:
        website = company.website.replace('http://', '').replace('https://', '').replace('www.', '')
        return website.split('/')[0]
    
    # Default fallback
    return 'company.com'

def get_hr_email(company):
    """Get HR email from company or generate from domain"""
    if not company:
        return 'hr@company.com'
    
    # Use company's HR email if available
    if company.hr_email:
        return company.hr_email
    
    # Otherwise generate from domain
    domain = get_company_domain(company)
    return f"hr@{domain}"

def get_employee_email(employee, company):
    """Generate employee email from name and company domain"""
    if not employee:
        return ''
    
    # Use employee's email if available and it matches company domain
    if employee.email:
        # If employee has an email, check if we should use it or generate new one
        company_domain = get_company_domain(company)
        if company_domain in employee.email:
            return employee.email
        # If email doesn't match company domain, generate new one
        # (optional - you can remove this if you want to keep existing email)
    
    # Generate from name and company domain
    if employee.full_name:
        name_parts = employee.full_name.split()
        if len(name_parts) >= 2:
            first_name = name_parts[0].lower()
            last_name = name_parts[-1].lower()
            domain = get_company_domain(company)
            return f"{first_name}.{last_name}@{domain}"
        elif len(name_parts) == 1:
            domain = get_company_domain(company)
            return f"{name_parts[0].lower()}@{domain}"
    
    return ''

def get_google_flow(state=None):
    """Create and return a Google OAuth flow object"""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return None
    
    # Create flow using client configuration
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI]
            }
        },
        scopes=SCOPES,
        state=state  # Add this line
    )
    flow.redirect_uri = REDIRECT_URI
    return flow

@app.template_filter('humanize')
def humanize_filter(value):
    try:
        num = float(value)
        return intword(num)
    except (ValueError, TypeError):
        return str(value)

@app.context_processor
def inject_now():
    return {
        'now': datetime.now(),
        'timedelta': timedelta  # Add timedelta to template context
    }

def get_days_in_month(month_name, year):
    """Return the number of days in a given month"""
    month_days = {
        'January': 31, 'February': 28, 'March': 31, 'April': 30,
        'May': 31, 'June': 30, 'July': 31, 'August': 31,
        'September': 30, 'October': 31, 'November': 30, 'December': 31
    }
    
    # Handle leap year for February
    if month_name == 'February':
        # Check if it's a leap year
        if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
            return 29
    
    return month_days.get(month_name, 30)

def get_previous_workday(target_date, days_before):
    """Get previous working day (Monday-Friday)"""
    count = 0
    current_date = target_date
    while count < days_before:
        current_date -= timedelta(days=1)
        if current_date.weekday() < 5:  # Monday=0, Friday=4
            count += 1
    return current_date

def format_date(date_value, format_string="%d %B %Y"):
    """Safely format a date, handling both string and datetime objects"""
    if date_value is None:
        return None
    
    if isinstance(date_value, str):
        try:
            date_obj = datetime.strptime(date_value, "%Y-%m-%d").date()
            return date_obj.strftime(format_string)
        except (ValueError, TypeError):
            return None
    elif hasattr(date_value, 'strftime'):  # datetime or date object
        return date_value.strftime(format_string)
    else:
        return None

def convert_dates(form_data):
    """Convert date strings to datetime objects"""
    date_fields = ['joining_date', 'resignation_date']
    for field in date_fields:
        if field in form_data and form_data[field]:
            try:
                # Store as date object
                form_data[field] = datetime.strptime(form_data[field], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                form_data[field] = None
    return form_data

def get_watermark_logo(company_id):
    """Get watermark logo filename for a company"""
    company = Company.query.get(company_id)
    if not company or not company.logo:
        return None
    return company.logo  # Just return the filename

def generate_pdf_file(form_data, company, doc_type):
    watermark_logo = get_watermark_logo(company['id'])
    template = f"templates/documents/{doc_type}.html"
    html_content = render_template(
        template.replace('templates/', ''), 
        data=form_data, 
        company=company,
        watermark_logo=watermark_logo
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{doc_type}_{form_data['full_name']}_{timestamp}.pdf"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    success = html_to_pdf(html_content, filepath)
    if success:
        return filename
    else:
        raise Exception("Failed to generate PDF")

@app.route('/')
def index():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    else:
        return redirect(url_for('admin_login'))

@app.route('/preview')
def preview():
    form_data = session.get('form_data', {})
    selected_months = session.get('selected_months', [])
    per_month_values = session.get('per_month_values', {})
    month_days_values = session.get('month_days_values', {})

    if 'document_type' not in form_data:
        flash('Document type is missing!', 'danger')
        return redirect(url_for('admin_dashboard'))

    if not form_data:
        return redirect(url_for('index'))

    form_data = convert_dates(form_data)

    # ========== RESIGNATION ACCEPTANCE HANDLER ==========
    if form_data.get('document_type') == 'resignation_acceptance':
        # Get employee from database
        employee_id = form_data.get('employee_id')
        employee = None
        if employee_id:
            employee = Employee.query.filter_by(employee_id=employee_id).first()
        
        # Get company
        company_id = form_data.get('company')
        company = None
        if company_id:
            try:
                company = Company.query.get(int(company_id))
            except (ValueError, TypeError):
                company = None
        
        if not employee:
            flash('Employee not found', 'danger')
            return redirect(url_for('admin_dashboard'))
        
        if not company:
            flash('Company not found', 'danger')
            return redirect(url_for('admin_dashboard'))
        
        # Check required data
        if not employee.resignation_datetime:
            flash('Resignation date and time not found. Please save resignation details first.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))
        
        if not employee.relieving_date:
            flash('Relieving date not found. Please save resignation details first.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))
        
        # Prepare data for template
        name_parts = employee.full_name.split() if employee.full_name else ['']
        first_name = name_parts[0] if name_parts else ''
        last_name = name_parts[-1] if len(name_parts) > 1 else ''
        
        # Get company domain, hr email, employee email
        company_domain = get_company_domain(company)
        hr_email = get_hr_email(company)
        employee_email = get_employee_email(employee, company)
        
        data = {
            'timestamp': datetime.now().strftime('%d %B %Y'),
            'full_name': employee.full_name,
            'first_name': first_name,
            'employee_email': employee_email,
            'company_name': company.name,
            'company_domain': company_domain,
            'hr_email': hr_email,
            'relieving_date': employee.relieving_date.strftime('%d %B %Y'),
            'hr_name': company.hr_name or 'HR Department',
            'hr_designation': company.hr_designation or 'HR Manager',
            'resignation_email': employee.resignation_email_content or '',
            'resignation_email_datetime': employee.resignation_datetime.strftime('%d %B %Y at %I:%M %p') if employee.resignation_datetime else ''
        }
        
        return render_template(
            'resignation_acceptance.html',
            data=data,
            company=company,
            watermark_logo=company.logo,
            now=datetime.now()
        )
    # ========== END RESIGNATION ACCEPTANCE HANDLER ==========

    # ... rest of your preview code remains the same ...
    if form_data.get('joining_date'):
        date_before = get_previous_workday(form_data['joining_date'], 8)
        form_data['date_before'] = date_before

    # Get company ID from form data
    company_id = form_data.get('company')
    if company_id:
        try:
            company = Company.query.get(int(company_id))
        except (ValueError, TypeError):
            company = None
    else:
        company = None

    if not company:
        flash('Company not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Determine which month to preview
    preview_month = request.args.get('month')
    if preview_month and preview_month in selected_months:
        pass
    elif selected_months:
        preview_month = selected_months[0]
    else:
        preview_month = None

    # Get base values
    ctc = float(form_data.get('ctc') or 0)
    increment_per_month = float(form_data.get('increment_per_month', 0))
    
    # Apply per‑month values for the selected preview month
    if form_data.get('document_type') == 'salary_slip' and preview_month:
        form_data['month'] = preview_month
        pm = per_month_values.get(preview_month, {})
        paid_days = pm.get('paid', 30)
        
        # Get actual days in this month
        month_days = month_days_values.get(preview_month, 30)
        
        # Calculate components with CORRECT month_days
        components = calculate_salary_components(
            ctc=ctc,
            increment_per_month=increment_per_month,
            paid_days=paid_days,
            month_days=month_days
        )
        
        # Update form_data with ALL components
        updated_data = {
            'basic': components['basic'],
            'hra': components['hra'],
            'conveyance': components['conveyance'],
            'medical': components['medical'],
            'telephone': components['telephone'],
            'special_allowance': components['special_allowance'],
            'gross_earnings': components['gross_earnings'],
            'professional_tax': components['professional_tax'],
            'pf_amount': components['pf_amount'],
            'income_tax': components['income_tax'],
            'gross_deductions': components['gross_deductions'],
            'net_salary': components['net_salary'],
            'worked_days': pm.get('worked', 30),
            'lop': pm.get('lop', 0),
            'paid_days': paid_days,
            'month_days': month_days,
            'preview_month': preview_month,
            'month': preview_month
        }
        form_data.update(updated_data)
        
        words = num2words(int(components['net_salary']), lang='en_IN').title() + ' Rupees'
        form_data['words'] = words
        
    else:
        components = calculate_salary_components(
            ctc=ctc,
            increment_per_month=increment_per_month,
            paid_days=30,
            month_days=30
        )
        
        updated_data = {
            'basic': components['basic'],
            'hra': components['hra'],
            'conveyance': components['conveyance'],
            'medical': components['medical'],
            'telephone': components['telephone'],
            'special_allowance': components['special_allowance'],
            'gross_earnings': components['gross_earnings'],
            'professional_tax': components['professional_tax'],
            'pf_amount': components['pf_amount'],
            'income_tax': components['income_tax'],
            'gross_deductions': components['gross_deductions'],
            'net_salary': components['net_salary'],
            'month': None,
            'words': num2words(int(components['net_salary']), lang='en_IN').title() + ' Rupees',
            'worked_days': 30,
            'lop': 0,
            'paid_days': 30,
            'month_days': 30
        }
        form_data.update(updated_data)

    # Salary breakdown dictionary
    form_data['salary_breakdown'] = {
        'basic': form_data['basic'],
        'hra': form_data['hra'],
        'conveyance': form_data['conveyance'],
        'medical': form_data['medical'],
        'telephone': form_data['telephone'],
        'special_allowance': form_data['special_allowance'],
        'professional_tax': form_data['professional_tax'],
        'pf_amount': form_data.get('pf_amount', 0),
        'income_tax': form_data.get('income_tax', 0),
        'gross_salary': form_data['gross_earnings'],
        'gross_deductions': form_data['gross_deductions'],
        'net_salary': form_data['net_salary'],
        'increment_per_month': increment_per_month
    }
    
    form_data['monthly_ctc_after_increment'] = round((ctc / 12) + increment_per_month)
    form_data['formatted_joining_date'] = format_date(form_data.get('joining_date'))

    # --- RELIEVING DATE AND CERTIFICATE DATE LOGIC ---
    relieving_date_raw = form_data.get('relieving_date')
    
    if relieving_date_raw and isinstance(relieving_date_raw, str):
        try:
            form_data['relieving_date'] = datetime.strptime(relieving_date_raw, '%Y-%m-%d').date()
        except ValueError:
            try:
                form_data['relieving_date'] = datetime.strptime(relieving_date_raw, '%d/%m/%Y').date()
            except ValueError:
                form_data['relieving_date'] = None
    elif relieving_date_raw and isinstance(relieving_date_raw, datetime):
        form_data['relieving_date'] = relieving_date_raw.date()
    elif relieving_date_raw and isinstance(relieving_date_raw, date):
        form_data['relieving_date'] = relieving_date_raw

    if not form_data.get('relieving_date'):
        emp = Employee.query.filter_by(employee_id=form_data.get('employee_id')).first()
        if emp and emp.relieving_date:
            form_data['relieving_date'] = emp.relieving_date
        elif form_data.get('resignation_date'):
            res_date = form_data['resignation_date']
            if isinstance(res_date, str):
                res_date = datetime.strptime(res_date, '%Y-%m-%d').date()
            elif isinstance(res_date, datetime):
                res_date = res_date.date()
            form_data['relieving_date'] = res_date + timedelta(days=30)

    base_relieving = form_data.get('relieving_date')
    
    if base_relieving and isinstance(base_relieving, (date, datetime)):
        form_data['top_date'] = base_relieving + timedelta(days=1)
    elif base_relieving and isinstance(base_relieving, str):
        try:
            base_relieving_date = datetime.strptime(base_relieving, '%Y-%m-%d').date()
            form_data['top_date'] = base_relieving_date + timedelta(days=1)
        except ValueError:
            form_data['top_date'] = datetime.now().date()
    else:
        form_data['top_date'] = datetime.now().date()

    base_date_for_cert = form_data.get('relieving_date')
    
    if base_date_for_cert and isinstance(base_date_for_cert, (date, datetime)):
        base_date = base_date_for_cert
    elif base_date_for_cert and isinstance(base_date_for_cert, str):
        try:
            base_date = datetime.strptime(base_date_for_cert, '%Y-%m-%d').date()
        except ValueError:
            base_date = datetime.now().date()
    else:
        base_date = datetime.now().date()
    
    target_date = base_date + timedelta(days=15)
    if target_date.weekday() == 5:
        target_date += timedelta(days=2)
    elif target_date.weekday() == 6:
        target_date += timedelta(days=1)
    
    form_data['certificate_issue_date'] = target_date

    emp = Employee.query.filter_by(employee_id=form_data.get('employee_id')).first()
    if emp:
        form_data['formatted_resignation_date'] = format_date(emp.resignation_date)
        if not form_data.get('relieving_date'):
            form_data['relieving_date'] = emp.relieving_date
    else:
        form_data['formatted_resignation_date'] = None

    month_label = []
    if form_data.get('document_type') in ['salary_slip', 'offer_and_salary'] and selected_months:
        current_year = session.get('selected_year', datetime.now().year)
        for m in selected_months:
            m = m.strip()
            m = m[:1].upper() + m[1:].lower()
            month_label.append(f"{m} {current_year}")

    watermark_logo = company.logo
    template = f"documents/{form_data['document_type']}.html"
    
    return render_template(
        template,
        data=form_data,
        company=company,
        months=selected_months,
        month=month_label,
        watermark_logo=watermark_logo,
        now=datetime.now()
    )

@app.route('/preview_document/<doc_type>')
def preview_document(doc_type):
    form_data = session.get('form_data', {})
    selected_months = session.get('selected_months', [])
    
    if not form_data:
        return redirect(url_for('index'))

    # ========== RESIGNATION ACCEPTANCE HANDLER ==========
    if doc_type == 'resignation_acceptance':
        # Get employee from database
        employee_id = form_data.get('employee_id')
        employee = None
        if employee_id:
            employee = Employee.query.filter_by(employee_id=employee_id).first()
        
        # Get company
        company_id = form_data.get('company')
        company = None
        if company_id:
            try:
                company = Company.query.get(int(company_id))
            except (ValueError, TypeError):
                company = None
        
        if not employee:
            flash('Employee not found', 'danger')
            return redirect(url_for('admin_dashboard'))
        
        if not company:
            flash('Company not found', 'danger')
            return redirect(url_for('admin_dashboard'))
        
        # Check required data
        if not employee.resignation_datetime:
            flash('Resignation date and time not found. Please save resignation details first.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))
        
        if not employee.relieving_date:
            flash('Relieving date not found. Please save resignation details first.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))
        
        # Prepare data for template
        name_parts = employee.full_name.split() if employee.full_name else ['']
        first_name = name_parts[0] if name_parts else ''
        last_name = name_parts[-1] if len(name_parts) > 1 else ''
        
        # Get company domain
        company_domain = get_company_domain(company)
        hr_email = get_hr_email(company)
        employee_email = get_employee_email(employee, company)
        
        data = {
            'timestamp': datetime.now().strftime('%d %B %Y'),
            'full_name': employee.full_name,
            'first_name': first_name,
            'employee_email': employee_email,
            'company_name': company.name,
            'company_domain': company_domain,
            'hr_email': hr_email,
            'relieving_date': employee.relieving_date.strftime('%d %B %Y'),
            'hr_name': company.hr_name or 'HR Department',
            'hr_designation': company.hr_designation or 'HR Manager',
            'resignation_email': employee.resignation_email_content or '',
            'resignation_email_datetime': employee.resignation_datetime.strftime('%d %B %Y at %I:%M %p') if employee.resignation_datetime else ''
        }
        
        return render_template(
            'resignation_acceptance.html',
            data=data,
            company=company,
            watermark_logo=company.logo,
            now=datetime.now()
        )
    # ========== END RESIGNATION ACCEPTANCE HANDLER ==========

    form_data = convert_dates(form_data)

    if form_data.get('joining_date'):
        date_before = get_previous_workday(form_data['joining_date'], 8)
        form_data['date_before'] = date_before

    # Get company ID from form data
    company_id = form_data.get('company')
    if company_id:
        try:
            company = Company.query.get(int(company_id))
        except (ValueError, TypeError):
            company = None
    else:
        company = None

    if not company:
        flash('Company not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Get base values
    ctc = float(form_data.get('ctc') or 0)
    increment_per_month = float(form_data.get('increment_per_month') or 0)
    
    # Calculate components
    components = calculate_salary_components(
        ctc=ctc,
        increment_per_month=increment_per_month,
        paid_days=30,  # Default for non-salary docs
        month_days=30
    )
    
    # Update form_data with calculated values
    form_data.update({
        'basic': components['basic'],
        'hra': components['hra'],
        'conveyance': components['conveyance'],
        'medical': components['medical'],
        'telephone': components['telephone'],
        'special_allowance': components['special_allowance'],
        'gross_earnings': components['gross_earnings'],
        'professional_tax': components['professional_tax'],
        'pf_amount': components['pf_amount'],
        'income_tax': components['income_tax'],
        'gross_deductions': components['gross_deductions'],
        'net_salary': components['net_salary'],
        'words': num2words(int(components['net_salary']), lang='en_IN').title() + ' Rupees'
    })

    form_data['salary_breakdown'] = {
        'basic': components['basic'],
        'hra': components['hra'],
        'conveyance': components['conveyance'],
        'medical': components['medical'],
        'telephone': components['telephone'],
        'special_allowance': components['special_allowance'],
        'professional_tax': components['professional_tax'],
        'pf_amount': components['pf_amount'],
        'income_tax': components['income_tax'],
        'gross_salary': components['gross_earnings'],
        'gross_deductions': components['gross_deductions'],
        'net_salary': components['net_salary'],
        'increment_per_month': increment_per_month
    }
    
    form_data['monthly_ctc_after_increment'] = round((ctc / 12) + increment_per_month)
    form_data['formatted_joining_date'] = format_date(form_data.get('joining_date'))

    # Resignation and relieving dates
    resignation_date = form_data.get('resignation_date')
    if resignation_date:
        form_data['formatted_resignation_date'] = format_date(resignation_date)
        if isinstance(resignation_date, str):
            relieving_date = datetime.strptime(resignation_date, "%Y-%m-%d").date() + timedelta(days=30)
        else:
            relieving_date = resignation_date + timedelta(days=30)
        form_data['relieving_date'] = format_date(relieving_date)
    else:
        form_data['formatted_resignation_date'] = None
        form_data['relieving_date'] = None

    # Month label for preview
    month_label = None
    if doc_type in ['salary_slip'] and selected_months:
        m = selected_months[0].strip()
        m = m[:1].upper() + m[1:].lower()
        current_year = datetime.now().year
        month_label = f"{m} {current_year}"

    watermark_logo = company.logo

    if form_data.get('document_type') == 'offer_and_salary' and doc_type == 'offer_letter':
        return render_template(
            'documents/offer_letter.html',
            data=form_data,
            company=company,
            months=selected_months,
            month=month_label,
            watermark_logo=watermark_logo
        )

    template = f"documents/{doc_type}.html"
    return render_template(
        template,
        data=form_data,
        company=company,
        months=selected_months,
        month=month_label,
        watermark_logo=watermark_logo
    )

@app.route('/generate', methods=['POST'])
def generate():
    form_data = session.get('form_data')
    selected_months = session.get('selected_months', [])

    if not form_data:
        return redirect(url_for('index'))

    upload_to_drive_flag = request.form.get('upload_to_drive') == 'true'
    doc_type = form_data.get('document_type')

    print(f"\n{'='*50}")
    print(f"GENERATE ROUTE STARTED for {doc_type}")
    print(f"{'='*50}")

    form_data = convert_dates(form_data)

    # ========== RESIGNATION ACCEPTANCE HANDLER ==========
    if doc_type == 'resignation_acceptance':
        # Get employee
        employee = None
        if 'employee_id' in form_data:
            employee = Employee.query.filter_by(employee_id=form_data.get('employee_id')).first()
        
        if not employee:
            flash('Employee not found', 'danger')
            return redirect(url_for('admin_dashboard'))
        
        # Get company
        company_id = form_data.get('company')
        company = None
        if company_id:
            try:
                company = Company.query.get(int(company_id))
            except (ValueError, TypeError):
                company = None
        
        if not company:
            flash('Company not found', 'danger')
            return redirect(url_for('admin_dashboard'))
        
        # Check required data
        if not employee.resignation_datetime:
            flash('Resignation date and time not found. Please save resignation details first.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))
        
        if not employee.relieving_date:
            flash('Relieving date not found. Please save resignation details first.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))
        
        # Prepare data for template
        name_parts = employee.full_name.split() if employee.full_name else ['']
        first_name = name_parts[0] if name_parts else ''
        last_name = name_parts[-1] if len(name_parts) > 1 else ''
        
        # Get company domain
        company_domain = get_company_domain(company)
        hr_email = get_hr_email(company)
        employee_email = get_employee_email(employee, company)
                    
        data = {
            'timestamp': datetime.now().strftime('%d %B %Y'),
            'full_name': employee.full_name,
            'first_name': first_name,
            'employee_email': employee_email,
            'company_name': company.name,
            'company_domain': company_domain,
            'hr_email':hr_email,
            'relieving_date': employee.relieving_date.strftime('%d %B %Y'),
            'hr_name': company.hr_name or 'HR Department',
            'hr_designation': company.hr_designation or 'HR Manager',
            'resignation_email': employee.resignation_email_content or '',
            'resignation_email_datetime': employee.resignation_datetime.strftime('%d %B %Y at %I:%M %p') if employee.resignation_datetime else ''
        }
        
        # Generate HTML
        html_content = render_template('resignation_acceptance.html', data=data)
        
        # Create PDF filename
        filename = f"Resignation_Acceptance_{employee.employee_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Convert to PDF
        if not html_to_pdf(html_content, file_path):
            flash('Failed to generate PDF document', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))
        
        # Save document record
        document = Document(
            employee_id=employee.id,
            document_type='resignation_acceptance',
            filename=filename,
            file_path=file_path,
            month=datetime.now().strftime('%B'),
            year=datetime.now().year,
            generated_by=session.get('admin_username', 'admin'),
            generated_at=datetime.now()
        )
        db.session.add(document)
        
        # Upload to Drive if requested
        if upload_to_drive_flag:
            try:
                drive_file_id = upload_file_to_drive(file_path, filename, 'Resignation Letters', employee)
                if drive_file_id:
                    document.drive_file_id = drive_file_id
            except Exception as e:
                print(f"Drive upload failed: {e}")
        
        db.session.commit()
        
        # Clear session data
        session.pop('form_data', None)
        session.pop('selected_months', None)
        
        flash(f'✅ Resignation acceptance letter generated successfully for {employee.full_name}!', 'success')
        return redirect(url_for('admin_dashboard'))
    # ========== END RESIGNATION ACCEPTANCE HANDLER ==========

    # ------------------------- FIND EMPLOYEE -------------------------
    employee = None
    if 'employee_id' in form_data:
        employee = Employee.query.filter_by(employee_id=form_data.get('employee_id')).first()

    if not employee:
        flash('Employee not found', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Get base values
    ctc = float(form_data.get('ctc') or 0)
    increment_per_month = float(form_data.get('increment_per_month') or 0)

    # Get company
    company_id = form_data.get('company')
    if company_id:
        try:
            company = Company.query.get(int(company_id))
        except (ValueError, TypeError):
            company = None
    else:
        company = None

    if not company:
        flash('Company not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    watermark_logo = company.logo

    # ------------------------- PENDING INCREMENT -------------------------
    should_update_increment = False
    pending = None
    if doc_type == 'increment_letter' and 'pending_increment' in session:
        should_update_increment = True
        pending = session['pending_increment']

    # Retrieve per‑month values from session
    per_month_values = session.get('per_month_values', {})
    
    # ==================== SALARY SLIP (multiple months) ====================
    if doc_type == "salary_slip" and selected_months:
        uploaded_files = []
        files_generated = []
        failed_months = []
        
        month_days_values = session.get('month_days_values', {})

        for month in selected_months:
            print(f"\n--- Processing month: {month} ---")
            
            pm = per_month_values.get(month, {})
            paid_days = pm.get('paid', 30)
            month_days = month_days_values.get(month, 30)
            
            components = calculate_salary_components(
                ctc=ctc,
                increment_per_month=increment_per_month,
                paid_days=paid_days,
                month_days=month_days
            )
            
            month_form_data = {
                'employee_id': form_data.get('employee_id'),
                'company': company.id,
                'document_type': doc_type,
                'full_name': form_data.get('full_name'),
                'address': form_data.get('address'),
                'aadhar_no': form_data.get('aadhar_no'),
                'pan_no': form_data.get('pan_no'),
                'designation': form_data.get('designation'),
                'gender': form_data.get('gender'),
                'department': form_data.get('department'),
                'ctc': ctc,
                'increment_per_month': increment_per_month,
                'month': month,
                'basic': components['basic'],
                'hra': components['hra'],
                'conveyance': components['conveyance'],
                'medical': components['medical'],
                'telephone': components['telephone'],
                'special_allowance': components['special_allowance'],
                'gross_earnings': components['gross_earnings'],
                'professional_tax': components['professional_tax'],
                'pf_amount': components['pf_amount'],
                'income_tax': components['income_tax'],
                'gross_deductions': components['gross_deductions'],
                'net_salary': components['net_salary'],
                'words': num2words(int(components['net_salary']), lang='en_IN').title() + ' Rupees',
                'worked_days': pm.get('worked', 30),
                'lop': pm.get('lop', 0),
                'paid_days': paid_days,
                'month_days': month_days,
                'joining_date': form_data.get('joining_date'),
                'resignation_date': form_data.get('resignation_date'),
                'bank_details': form_data.get('bank_details', {})
            }

            html = render_template(
                "documents/salary_slip.html",
                data=month_form_data,
                company=company,
                watermark_logo=watermark_logo,
                now=datetime.now()
            )

            filename = f"Salary_Slip_{month}_{datetime.now().strftime('%Y%m%d')}.pdf"

            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                temp_path = tmp_file.name

            if html_to_pdf(html, temp_path):
                files_generated.append(month)

                if upload_to_drive_flag and employee:
                    try:
                        drive_file_id = upload_file_to_drive(temp_path, filename, f"Salary Slips/{month}", employee)
                        doc = Document(
                            employee_id=employee.id,
                            document_type=doc_type,
                            filename=filename,
                            file_path=None,
                            month=month,
                            year=session.get('selected_year', datetime.now().year),
                            generated_by=session.get('admin_username', 'system'),
                            drive_file_id=drive_file_id
                        )
                        db.session.add(doc)
                    except Exception as e:
                        print(f"Drive upload error: {e}")
                else:
                    doc = Document(
                        employee_id=employee.id,
                        document_type=doc_type,
                        filename=filename,
                        file_path=None,
                        month=month,
                        year=session.get('selected_year', datetime.now().year),
                        generated_by=session.get('admin_username', 'system'),
                        drive_file_id=None
                    )
                    db.session.add(doc)

                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            else:
                failed_months.append(month)

        if files_generated:
            db.session.commit()
            session.pop('form_data', None)
            session.pop('selected_months', None)
            session.pop('selected_year', None)
            session.pop('per_month_values', None)
            session.pop('month_days_values', None)
            session.pop('pending_increment', None)

            if upload_to_drive_flag:
                flash(f'{len(files_generated)} salary slips uploaded to Drive!', 'success')
            else:
                flash(f'{len(files_generated)} salary slips generated successfully!', 'success')

            return redirect(url_for('admin_dashboard'))

        flash('Failed to generate any salary slips', 'danger')
        return redirect(url_for('admin_dashboard'))

    # ==================== OTHER DOCUMENTS ====================
    components = calculate_salary_components(
        ctc=ctc,
        increment_per_month=increment_per_month,
        paid_days=30,
        month_days=30
    )
    
    clean_form_data = {
        'employee_id': form_data.get('employee_id'),
        'company': company.id,
        'document_type': doc_type,
        'full_name': form_data.get('full_name'),
        'address': form_data.get('address'),
        'aadhar_no': form_data.get('aadhar_no'),
        'pan_no': form_data.get('pan_no'),
        'designation': form_data.get('designation'),
        'gender': form_data.get('gender'),
        'department': form_data.get('department'),
        'ctc': ctc,
        'increment_per_month': increment_per_month,
        'basic': components['basic'],
        'hra': components['hra'],
        'conveyance': components['conveyance'],
        'medical': components['medical'],
        'telephone': components['telephone'],
        'special_allowance': components['special_allowance'],
        'gross_earnings': components['gross_earnings'],
        'professional_tax': components['professional_tax'],
        'pf_amount': components['pf_amount'],
        'income_tax': components['income_tax'],
        'gross_deductions': components['gross_deductions'],
        'net_salary': components['net_salary'],
        'words': num2words(int(components['net_salary']), lang='en_IN').title() + ' Rupees',
        'joining_date': form_data.get('joining_date'),
        'resignation_date': form_data.get('resignation_date'),
        'bank_details': form_data.get('bank_details', {}),
        'worked_days': 30,
        'lop': 0,
        'paid_days': 30
    }

    emp = Employee.query.filter_by(employee_id=form_data.get('employee_id')).first()
    if emp:
        clean_form_data['formatted_resignation_date'] = format_date(emp.resignation_date)
        clean_form_data['relieving_date'] = format_date(emp.relieving_date)

    html = render_template(
        f"documents/{doc_type}.html",
        data=clean_form_data,
        company=company,
        watermark_logo=watermark_logo,
        now=datetime.now()
    )

    filename = f"{doc_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
        temp_path = tmp_file.name

    if not html_to_pdf(html, temp_path):
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        flash('Failed to generate PDF', 'danger')
        return redirect(url_for('admin_dashboard'))

    if should_update_increment and employee and pending:
        try:
            old_ctc = employee.ctc
            increment_amount = pending['amount']
            new_ctc = old_ctc + (increment_amount * 12)

            history = IncrementHistory(
                employee_id=employee.id,
                old_ctc=old_ctc,
                increment_amount=increment_amount,
                new_ctc=new_ctc,
                effective_date=datetime.strptime(pending['effective_date'], '%Y-%m-%d').date() if pending['effective_date'] else None,
                generated_by=session.get('admin_username', 'system')
            )
            db.session.add(history)
            session.pop('pending_increment', None)
        except Exception as e:
            print("Increment Update Error:", e)
            db.session.rollback()

    drive_file_id = None
    if upload_to_drive_flag and employee:
        try:
            folder_map = {
                'offer_letter': 'Offer Letters',
                'experience_letter': 'Experience Letters',
                'increment_letter': 'Increment Letters',
                'relieving_letter': 'Relieving Letters'
            }
            folder_name = folder_map.get(doc_type, 'Other Documents')
            drive_file_id = upload_file_to_drive(temp_path, filename, folder_name, employee)
        except Exception as e:
            print("Drive Upload Error:", e)

    if employee:
        doc = Document(
            employee_id=employee.id,
            document_type=doc_type,
            filename=filename,
            file_path=None,
            generated_by=session.get('admin_username', 'system'),
            drive_file_id=drive_file_id
        )
        db.session.add(doc)

    db.session.commit()

    if os.path.exists(temp_path):
        os.unlink(temp_path)

    session.pop('form_data', None)
    session.pop('selected_months', None)
    session.pop('selected_year', None)
    session.pop('per_month_values', None)

    flash(f'{doc_type.replace("_", " ").title()} generated successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/generated_docs/<filename>')
def serve_generated_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/admin/documents')
def admin_documents():
    if not session.get('is_admin'):
        return "Unauthorized", 403

    base_folder = os.path.join(app.config['UPLOAD_FOLDER'], "employee_documents")

    if not os.path.exists(base_folder):
        os.makedirs(base_folder)

    data = {}

    for emp in os.listdir(base_folder):
        emp_path = os.path.join(base_folder, emp)
        if os.path.isdir(emp_path):
            data[emp] = os.listdir(emp_path)

    return render_template("admin_documents.html", data=data)

#admin login
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        admin = Admin.query.filter_by(username=username).first()

        if admin and admin.check_password(password):
            session['admin_id'] = admin.id
            session['admin_username'] = admin.username
            session['is_admin'] = True
            flash('Login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password', 'danger')

    return render_template('admin_login.html')

# Admin Logout
@app.route('/admin/logout')
def admin_logout():
    session.clear()
    flash('Logged out successfully', 'success')
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    # Get tab from query parameters
    active_tab = request.args.get('tab', 'dashboard')
    selected_emp_id = request.args.get('emp_id', type=int)

    # Get all employees with their document counts
    employees = Employee.query.order_by(Employee.created_at.desc()).all()

    employee_data = []
    total_documents = 0
    for emp in employees:
        doc_count = Document.query.filter_by(employee_id=emp.id).count()
        total_documents += doc_count
        latest_inc = IncrementHistory.query.filter_by(employee_id=emp.id).order_by(IncrementHistory.generated_at.desc()).first()
        increment_amount = latest_inc.increment_amount if latest_inc else 0
        employee_data.append({
            'employee': emp,
            'document_count': doc_count,
            'increment_amount': increment_amount
        })

    total_employees = len(employees)
    active_employees = sum(1 for emp in employees if emp.status == 'active')

    # ---------- Payment calculations using simplified model ----------
    # All payments
    all_payments = Payment.query.all()

    paid_payments = [p for p in all_payments if p.paid_amt >= p.amount]
    pending_payments = [p for p in all_payments if p.paid_amt < p.amount]

    paid_count = len(paid_payments)
    pending_count = len(pending_payments)
    overdue_count = 0  # define your overdue logic if needed

    paid_amount = sum(p.amount for p in paid_payments)
    pending_amount = sum(p.amount - p.paid_amt for p in pending_payments)
    overdue_amount = 0  # define your logic

    # Get all payments for the table
    payments_result = db.session.query(
        Payment.id,
        Employee.full_name.label('employee_name'),
        Employee.employee_id,
        Document.document_type,
        Payment.amount,
        Payment.paid_amt,
        Payment.paid_date
    ).select_from(Payment).join(Employee).outerjoin(
        Document, Payment.document_id == Document.id
    ).all()

    payments = []
    for p in payments_result:
        if p.paid_amt >= p.amount:
            status = 'Paid'
            status_class = 'paid'
        else:
            status = 'Pending'
            status_class = 'pending'
        payments.append({
            'id': p.id,
            'employee_name': p.employee_name,
            'employee_id': p.employee_id,
            'document_type': p.document_type or 'N/A',
            'amount': p.amount,
            'paid_amt': p.paid_amt,
            'due_amount': p.amount - p.paid_amt,
            'status': status,
            'status_class': status_class,
            'paid_date': p.paid_date.strftime('%d %b %Y') if p.paid_date else 'N/A',
        })

    return render_template('admin_dashboard.html',
                         employees=employee_data,
                         active_tab=active_tab,
                         selected_emp_id=selected_emp_id,
                         now=datetime.now(),
                         total_employees=total_employees,
                         active_employees=active_employees,
                         total_documents=total_documents,
                         pending_payments=pending_count,
                         paid_count=paid_count,
                         pending_count=pending_count,
                         overdue_count=overdue_count,
                         paid_amount=paid_amount,
                         pending_amount=pending_amount,
                         overdue_amount=overdue_amount,
                         payments=payments)

@app.route('/admin/profile', methods=['GET', 'POST'])
def admin_profile():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    admin = Admin.query.get(session['admin_id'])
    if request.method == 'POST':
        current = request.form.get('current_password')
        new = request.form.get('new_password')
        confirm = request.form.get('confirm_password')
        if not admin.check_password(current):
            flash('Current password is incorrect', 'danger')
        elif new != confirm:
            flash('New passwords do not match', 'danger')
        elif len(new) < 6:
            flash('Password must be at least 6 characters', 'danger')
        else:
            admin.set_password(new)
            db.session.commit()
            flash('Password updated successfully', 'success')
            return redirect(url_for('admin_profile'))
    return render_template('admin_profile.html', admin=admin)

@app.route('/admin/employee/<int:emp_id>/delete', methods=['POST'])
def delete_employee(emp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    employee = Employee.query.get_or_404(emp_id)
    drive_folder_id = employee.drive_folder_id  # main employee Drive folder

    try:
        # Delete all related documents (and their Drive files)
        for doc in employee.documents:
            if doc.drive_file_id:
                # Delete the file from Drive
                parent_id = get_parent_folder_id(doc.drive_file_id)
                delete_drive_file(doc.drive_file_id)
                # Optionally delete parent folder if empty (might be redundant)
                if parent_id and is_folder_empty(parent_id):
                    delete_drive_folder(parent_id)

            # Delete local file if exists
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
                except Exception as e:
                    print(f"Error deleting local file: {e}")

        # After deleting all documents, delete the main employee folder (if exists and empty)
        if drive_folder_id:
            # Check if folder still has any files (documents might have been deleted above)
            if is_folder_empty(drive_folder_id):
                delete_drive_folder(drive_folder_id)
            else:
                print(f"Main folder {drive_folder_id} not empty, skipping deletion.")

        # Now delete the employee (cascades to documents via DB)
        db.session.delete(employee)
        db.session.commit()

        flash(f'Employee {employee.full_name} and all data deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting employee: {str(e)}', 'danger')

    return redirect(url_for('admin_dashboard', tab='employees'))

@app.route('/admin/employee/<int:emp_id>/generate/<doc_type>', methods=['GET', 'POST'])
def admin_generate_document(emp_id, doc_type):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    employee = Employee.query.get_or_404(emp_id)

    # Special handling for increment letter
    if doc_type == 'increment_letter':
        if request.method == 'POST':
            # Get company_id from form – now it's an integer ID
            company_id = request.form.get('company', type=int)
            if not company_id:
                flash('Please select a company.', 'danger')
                return render_template('increment_form.html', employee=employee, companies=Company.query.all(), now=datetime.now)

            company = Company.query.get(company_id)
            if not company:
                flash('Company not found.', 'danger')
                return redirect(url_for('view_employee', emp_id=employee.id))

            increment_amount = float(request.form.get('increment_amount', 0))
            effective_date = request.form.get('effective_date')

            if increment_amount <= 0:
                flash('Increment amount must be greater than zero.', 'danger')
                return render_template('increment_form.html', employee=employee, companies=Company.query.all(), now=datetime.now)

            session['pending_increment'] = {
                'amount': increment_amount,
                'effective_date': effective_date,
                'employee_id': employee.id,
                'old_ctc': employee.ctc
            }

            # Salary breakdown calculations
            ctc = float(employee.ctc)
            monthly_ctc = round(ctc / 12)
            monthly_ctc_after_increment = monthly_ctc + increment_amount

            basic = round(monthly_ctc_after_increment * 0.5)
            hra = round(basic * 0.5)
            conveyance = round(monthly_ctc_after_increment * 0.05)
            medical = round(monthly_ctc_after_increment * 0.014)
            telephone = round(monthly_ctc_after_increment * 0.02)
            special_allowance = monthly_ctc_after_increment - (basic + hra + conveyance + medical + telephone)
            professional_tax = 200
            gross_salary = basic + hra + conveyance + medical + telephone + special_allowance
            net_salary = gross_salary - professional_tax

            salary_breakdown = {
                'basic': basic, 'hra': hra, 'conveyance': conveyance,
                'medical': medical, 'telephone': telephone,
                'special_allowance': special_allowance, 'professional_tax': professional_tax,
                'gross_salary': gross_salary, 'net_salary': net_salary,
                'increment_per_month': increment_amount
            }

            form_data = {
                'employee_id': employee.employee_id,
                'company': company.id,
                'document_type': doc_type,
                'full_name': employee.full_name,
                'address': employee.address,
                'aadhar_no': employee.aadhar_no,
                'pan_no': employee.pan_no,
                'designation': employee.designation,
                'base_ctc': employee.base_ctc,
                'ctc': employee.ctc,
                'increment_per_month': increment_amount,
                'salary_breakdown': salary_breakdown,
                'increment_effective_date': effective_date,
                'joining_date': employee.joining_date.strftime('%Y-%m-%d') if employee.joining_date else None,
                'resignation_date': employee.resignation_date.strftime('%Y-%m-%d') if employee.resignation_date else None,
                'bank_details': {
                    'account_holder': employee.account_holder,
                    'account_number': employee.account_number,
                    'bank_name': employee.bank_name,
                    'branch': employee.branch,
                    'ifsc_code': employee.ifsc_code
                }
            }
            session['form_data'] = form_data
            return redirect(url_for('preview'))

        # GET request – show increment form with all companies
        return render_template('increment_form.html', employee=employee, companies=Company.query.all(), now=datetime.now)

    # ===== Resignation Acceptance =====
    elif doc_type == 'resignation_acceptance':
        company_id = request.args.get('company', type=int)
        if not company_id:
            flash('No company selected.', 'danger')
            return redirect(url_for('select_company_for_doc', emp_id=employee.id, doc_type=doc_type))

        company = Company.query.get(company_id)
        if not company:
            flash('Company not found.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))

        if not employee.resignation_date or not employee.resignation_email_content:
            flash('Resignation details not found. Please mark employee as resigned first.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))

        relieving_date = employee.resignation_date + timedelta(days=30)
        formatted_relieving_date = relieving_date.strftime('%d %B %Y')
        formatted_email_datetime = employee.resignation_datetime.strftime('%d %B %Y %I:%M %p') if employee.resignation_datetime else None

        form_data = {
            'employee_id': employee.employee_id,
            'company': company.id,
            'document_type': doc_type,
            'full_name': employee.full_name,
            'employee_email': employee.email,
            'designation': employee.designation,
            'relieving_date': formatted_relieving_date,
            'resignation_email': employee.resignation_email_content,
            'resignation_email_datetime': formatted_email_datetime,
            'hr_name': company.hr_name,
            'hr_designation': company.hr_designation,
            'hr_email': company.hr_email,
            'timestamp': datetime.now().strftime('%d/%m/%Y %I:%M %p')
        }
        session['form_data'] = form_data
        return redirect(url_for('preview'))

    # ===== Salary Slip =====
    if doc_type == 'salary_slip' and request.method == 'POST':
        company_id = request.form.get('company', type=int)
        if not company_id:
            flash('Please select a company.', 'danger')
            return render_template('select_months.html', employee=employee, companies=Company.query.all())

        company = Company.query.get(company_id)
        if not company:
            flash('Company not found.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))

        selected_months = request.form.getlist('months')
        year = int(request.form.get('year', datetime.now().year))

        # Collect per‑month values and store actual days in month
        per_month_values = {}
        month_days_values = {}  # New dict to store actual days in each month

        for month in selected_months:
            month_key = month.lower()
            
            # Get the values from form
            if f'worked_days_{month_key}' in request.form:
                worked_days = int(request.form.get(f'worked_days_{month_key}'))
                lop = int(request.form.get(f'lop_{month_key}'))
                paid_days = int(request.form.get(f'paid_days_{month_key}'))
            else:
                worked_days = int(request.form.get('worked_days', 30))
                lop = int(request.form.get('lop', 0))
                paid_days = int(request.form.get('paid_days', 30))
            
            # Calculate actual days in this month
            actual_days_in_month = get_days_in_month(month, year)
            
            per_month_values[month] = {
                'worked': worked_days,
                'lop': lop,
                'paid': paid_days
            }
            
            # Store actual days in month for this month
            month_days_values[month] = actual_days_in_month

        session['per_month_values'] = per_month_values
        session['month_days_values'] = month_days_values  # Store in session

        if not selected_months:
            flash('Please select at least one month.', 'danger')
            return render_template('select_months.html', employee=employee, companies=Company.query.all())

        session['selected_months'] = selected_months
        session['selected_year'] = year

        # Use the first month's values for preview
        first_month = selected_months[0]
        first_month_values = per_month_values[first_month]
        first_month_days = month_days_values[first_month]
        
        # Calculate salary components with CORRECT month_days
        components = calculate_salary_components(
            ctc=float(employee.ctc),
            increment_per_month=0,
            paid_days=first_month_values['paid'],
            month_days=first_month_days  # Use actual days in month!
        )
        
        # Generate amount in words
        words = num2words(int(components['net_salary']), lang='en_IN').title() + ' Rupees'

        form_data = {
            'employee_id': employee.employee_id,
            'company': company.id,
            'document_type': doc_type,
            'full_name': employee.full_name,
            'address': employee.address,
            'aadhar_no': employee.aadhar_no,
            'pan_no': employee.pan_no,
            'designation': employee.designation,
            'gender': employee.gender,
            'department': employee.department,
            'base_ctc': employee.base_ctc,
            'ctc': employee.ctc,
            'increment_per_month': 0,
            
            # Earnings
            'basic': components['basic'],
            'hra': components['hra'],
            'conveyance': components['conveyance'],
            'medical': components['medical'],
            'telephone': components['telephone'],
            'special_allowance': components['special_allowance'],
            'gross_earnings': components['gross_earnings'],
            
            # Deductions
            'professional_tax': components['professional_tax'],
            'pf_amount': components['pf_amount'],
            'income_tax': components['income_tax'],
            'gross_deductions': components['gross_deductions'],
            
            # Net
            'net_salary': components['net_salary'],
            'words': words,
            
            # Day details
            'worked_days': first_month_values['worked'],
            'lop': first_month_values['lop'],
            'paid_days': first_month_values['paid'],
            'month_days': first_month_days,  # Add actual month days
            
            # Dates
            'joining_date': employee.joining_date.strftime('%Y-%m-%d') if employee.joining_date else None,
            'resignation_date': employee.resignation_date.strftime('%Y-%m-%d') if employee.resignation_date else None,
            
            # Bank details
            'bank_details': {
                'account_holder': employee.account_holder,
                'account_number': employee.account_number,
                'bank_name': employee.bank_name,
                'branch': employee.branch,
                'ifsc_code': employee.ifsc_code
            }
        }
        session['form_data'] = form_data
        return redirect(url_for('preview'))

    # GET request – show options for salary slip
    if doc_type == 'salary_slip':
        return render_template('select_months.html', employee=employee, companies=Company.query.all())

    # ===== All other document types (offer letter, experience letter, etc.) =====
    # Redirect to company selection page so the admin can choose a company
    return redirect(url_for('select_company_for_doc', emp_id=emp_id, doc_type=doc_type))

#view employee details and documents
@app.route('/admin/employee/<int:emp_id>')
def view_employee(emp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    # Force a fresh query without caching
    db.session.expire_all()
    
    employee = Employee.query.get_or_404(emp_id)
    
    # Explicitly query documents to ensure fresh data
    documents = Document.query.filter_by(employee_id=emp_id).order_by(Document.generated_at.desc()).all()
    
    # Explicitly query increment history to ensure fresh data
    increment_history = IncrementHistory.query.filter_by(employee_id=emp_id).order_by(IncrementHistory.generated_at.desc()).all()
    
    # Generate folder name for employee documents
    employee_folder = get_employee_folder_name(employee)

    latest_increment = IncrementHistory.query.filter_by(
        employee_id=emp_id
    ).order_by(IncrementHistory.generated_at.desc()).first()
        
    return render_template('view_employee.html', 
                         employee=employee, 
                         documents=documents,
                         increment_history=increment_history,
                         latest_increment=latest_increment,
                         employee_folder=employee_folder)

def get_employee_folder_name(employee):
    """Generate folder name for employee documents"""
    return f"{employee.employee_id}_{employee.full_name.replace(' ', '_')}"

# Add employee route
@app.route('/admin/employee/add', methods=['GET', 'POST'])
def add_employee():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        # Generate employee ID
        last_employee = Employee.query.order_by(Employee.id.desc()).first()
        new_id = f"EMP{(last_employee.id + 1) if last_employee else 1:04d}"
        
        # Handle profile image upload
        profile_image = None
        if 'profile_image' in request.files:
            file = request.files['profile_image']
            if file and file.filename:
                #create profiles directory if it doesn't exist
                profiles_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'profiles')
                os.makedirs(profiles_dir, exist_ok=True)
                #secure the filename and save
                filename = secure_filename(f"{new_id}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'profiles', filename))
                profile_image = filename
        
        # Parse dates
        joining_date = None
        if request.form.get('joining_date'):
            joining_date = datetime.strptime(request.form['joining_date'], '%Y-%m-%d').date()
        
        resignation_date = None
        if request.form.get('resignation_date'):
            resignation_date = datetime.strptime(request.form['resignation_date'], '%Y-%m-%d').date()
        
        employee = Employee(
            employee_id="",  # Will be set after flush
            full_name=request.form['full_name'],
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            gender=request.form.get('gender'),
            address=request.form.get('address'),
            aadhar_no=request.form.get('aadhar_no'),
            pan_no=request.form.get('pan_no'),
            designation=request.form['designation'],
            department=request.form.get('department'),
            base_ctc=float(request.form.get('ctc') or 0),
            # increment_per_month removed
            joining_date=joining_date,
            resignation_date=resignation_date,
            status=request.form.get('status', 'active'),
            profile_image=profile_image,
            account_holder=request.form.get('account_holder'),
            account_number=request.form.get('account_number'),
            bank_name=request.form.get('bank_name'),
            branch=request.form.get('branch'),
            ifsc_code=request.form.get('ifsc_code')
        )
        
        db.session.add(employee)
        db.session.flush()  # Flush to get employee.id for employee_id generation
        #set employee id
        employee.employee_id = f"LC{1003+employee.id}"
        db.session.commit()
        
        flash('Employee added successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('add_employee.html')

@app.route('/profiles/<filename>')
def serve_profile_image(filename):
    """Serve employee profile images"""
    if not session.get('is_admin'):
        return "Unauthorized", 403
    
    profiles_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'profiles')
    return send_from_directory(profiles_dir, filename)

@app.route('/admin/process-payment/<int:payment_id>', methods=['POST'])
def process_payment(payment_id):
    if not session.get('is_admin'):
        return "Unauthorized", 403

    payment = Payment.query.get_or_404(payment_id)
    data = request.get_json()

    try:
        # In a real scenario, you might receive the paid amount from the form.
        # For now, we'll mark the full amount as paid.
        payment.paid_amt = payment.amount
        payment.paid_date = datetime.now().date()
        db.session.commit()
        return {'success': True, 'message': 'Payment marked as paid'}
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'message': str(e)}, 500

#increment route
@app.route('/admin/give-increment/<int:emp_id>', methods=['POST'])
def give_increment(emp_id):
    employee = Employee.query.get_or_404(emp_id)

    increment_per_month = float(request.form['increment_per_month'])

    old_ctc = employee.ctc
    annual_increment = increment_per_month * 12
    new_ctc = old_ctc + annual_increment

    # 2️⃣ Store in history
    history = IncrementHistory(
        employee_id=emp_id,
        old_ctc=old_ctc,
        increment_amount=increment_per_month,
        new_ctc=new_ctc,
        effective_date=datetime.today(),
        generated_by=session.get('admin_username')
    )

    db.session.add(history)
    db.session.commit()

    return redirect(url_for('view_employee', emp_id=emp_id))

#update employee status
@app.route('/employee/<int:emp_id>/update-status/<string:status>')
def update_employee_status(emp_id, status):
    employee = Employee.query.get_or_404(emp_id)

    # Only allow valid statuses
    if status not in ['active', 'resigned', 'terminated']:
        flash("Invalid status", "danger")
        return redirect(url_for('employee_details', emp_id=emp_id))

    employee.status = status

    # If resigned → set resignation date
    if status == 'resigned':
        employee.resignation_date = datetime.today().date()

    # If active again → clear resignation date
    if status == 'active':
        employee.resignation_date = None

    db.session.commit()

    flash("Employee status updated successfully!", "success")
    return redirect(url_for('view_employee', emp_id=emp_id))

#============resignation acceptace routes================
@app.route('/admin/employee/<int:emp_id>/generate-resignation-acceptance')
def generate_resignation_acceptance(emp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    try:
        employee = Employee.query.get_or_404(emp_id)
        
        # Check if all required data exists
        if not employee.resignation_datetime:
            flash('Resignation date and time not found. Please save resignation details first.', 'danger')
            return redirect(url_for('view_employee', emp_id=emp_id))
        
        if not employee.relieving_date:
            flash('Relieving date not found. Please save resignation details first.', 'danger')
            return redirect(url_for('view_employee', emp_id=emp_id))
        
        if not employee.company:
            flash('Company details not found. Please select a company for this employee.', 'danger')
            return redirect(url_for('view_employee', emp_id=emp_id))
        
        company = employee.company
        
        # Prepare data for template with safe defaults
        name_parts = employee.full_name.split() if employee.full_name else ['']
        first_name = name_parts[0] if name_parts else ''
        last_name = name_parts[-1] if len(name_parts) > 1 else ''
        
        # Get company domain using helper function
        company_domain = get_company_domain(company)
        hr_email = get_hr_email(company)
        employee_email = get_employee_email(employee, company)
        
        data = {
            'timestamp': datetime.now().strftime('%d %B %Y'),
            'full_name': employee.full_name,
            'first_name': first_name,
            'employee_email': employee_email,
            'company_name': company.name,
            'company_domain': company_domain,
            'hr_email': hr_email,
            'relieving_date': employee.relieving_date.strftime('%d %B %Y'),
            'hr_name': company.hr_name or 'HR Department',
            'hr_designation': company.hr_designation or 'HR Manager',
            'resignation_email': employee.resignation_email_content or '',
            'resignation_email_datetime': employee.resignation_datetime.strftime('%d %B %Y at %I:%M %p') if employee.resignation_datetime else ''
        }
        
        # Store in session for preview
        session['form_data'] = {
            'document_type': 'resignation_acceptance',
            'employee_id': employee.employee_id,
            'company': company.id
        }
        
        # Generate HTML content
        html_content = render_template('resignation_acceptance.html', data=data)
        
        # Create PDF filename
        filename = f"Resignation_Acceptance_{employee.employee_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Convert HTML to PDF
        if not html_to_pdf(html_content, file_path):
            flash('Failed to generate PDF document', 'danger')
            return redirect(url_for('view_employee', emp_id=emp_id))
        
        # Save document record
        document = Document(
            employee_id=employee.id,
            document_type='resignation_acceptance',
            filename=filename,
            file_path=file_path,
            month=datetime.now().strftime('%B'),
            year=datetime.now().year,
            generated_by=session.get('admin_username', 'admin'),
            generated_at=datetime.now()
        )
        db.session.add(document)
        db.session.commit()
        
        # Upload to Google Drive if connected
        token_path = os.path.join(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], 'token.pickle')
        if os.path.exists(token_path):
            try:
                drive_file_id = upload_file_to_drive(file_path, filename, 'Resignation Letters', employee)
                if drive_file_id:
                    document.drive_file_id = drive_file_id
                    db.session.commit()
            except Exception as e:
                print(f"Drive upload failed: {e}")
        
        flash(f'✅ Resignation acceptance letter generated successfully for {employee.full_name}!', 'success')
        return redirect(url_for('view_employee', emp_id=emp_id))
        
    except Exception as e:
        print(f"Error generating resignation acceptance: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'Error generating document: {str(e)}', 'danger')
        return redirect(url_for('view_employee', emp_id=emp_id))

@app.route('/admin/employee/<int:emp_id>/save-resignation', methods=['POST'])
def save_resignation_details(emp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    employee = Employee.query.get_or_404(emp_id)

    # Get company from form
    company_id = request.form.get('company_id', type=int)
    if not company_id:
        flash('Please select a company for the resignation letter.', 'danger')
        return redirect(url_for('view_employee', emp_id=emp_id))

    company = Company.query.get(company_id)
    if not company:
        flash('Selected company does not exist.', 'danger')
        return redirect(url_for('view_employee', emp_id=emp_id))

    resignation_date_str = request.form.get('resignation_date')
    relieving_date_str = request.form.get('relieving_date')

    if not resignation_date_str:
        flash('Resignation date is required.', 'danger')
        return redirect(url_for('view_employee', emp_id=emp_id))

    try:
        # Convert resignation date
        resignation_date = datetime.strptime(resignation_date_str, '%Y-%m-%d').date()
        employee.resignation_date = resignation_date

        # Determine relieving date
        if relieving_date_str and relieving_date_str.strip():
            relieving_date = datetime.strptime(relieving_date_str, '%Y-%m-%d').date()
        else:
            # Get notice period from company or default to 30 days
            notice_period = int(company.notice_period) if company.notice_period else 30
            relieving_date = resignation_date + timedelta(days=notice_period)
        employee.relieving_date = relieving_date

        # Mark status and timestamp
        employee.status = 'resigned'
        employee.resignation_datetime = datetime.now()

        # Assign the selected company to the employee
        employee.company_id = company.id

        # 🔥 ALWAYS REGENERATE resignation email content with the selected company
        employee.resignation_email_content = f"""Dear HR,

I am writing to formally resign from my position as {employee.designation or 'Employee'} at {company.name}, effective from {resignation_date.strftime('%d %B %Y')}.

I have decided to pursue other opportunities and would like to thank you for the support and opportunities provided during my tenure.

I will ensure a smooth handover of my responsibilities before my departure. Please let me know the next steps regarding the notice period and exit formalities.

Thank you for the guidance and support.

Thanks and Regards,
{employee.full_name}"""

        db.session.commit()
        
        # Clear session data to force refresh
        if 'form_data' in session:
            session.pop('form_data')
        
        flash('Resignation details saved successfully.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()

    return redirect(url_for('view_employee', emp_id=emp_id))

@app.route('/admin/employee/<int:emp_id>/resignation-form')
def resignation_input_form(emp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    employee = Employee.query.get_or_404(emp_id)
    companies = Company.query.all()
    return render_template('resignation_input_form.html', employee=employee, companies=companies)

def get_drive_service():
    """Get authenticated Google Drive service"""
    token_path = os.path.join(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], 'token.pickle')
    
    if not os.path.exists(token_path):
        return None, "Not authenticated"
    
    with open(token_path, 'rb') as token:
        credentials = pickle.load(token)
    
    # Refresh token if expired
    if credentials.expired and credentials.refresh_token:
        import google.auth.transport.requests
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        
        # Save refreshed credentials
        with open(token_path, 'wb') as token:
            pickle.dump(credentials, token)
    
    service = build('drive', 'v3', credentials=credentials)
    return service, None

def upload_file_to_drive(file_path, filename, folder_name=None, employee=None):

    service, error = get_drive_service()
    if error:
        raise Exception("Google Drive not connected. Please connect first.")
    try:
        # Use employee details for folder name
        emp_id = employee.employee_id if employee else "unknown"
        emp_name = employee.full_name if employee else "Unknown"
        main_folder_name = f"{emp_id}_{emp_name.replace(' ', '_')}" if employee else "Documents"

        response = service.files().list(
            q=f"name='{main_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        folders = response.get('files', [])
        if folders:
            parent_folder_id = folders[0]['id']
        else:
            file_metadata = {
                'name': main_folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = service.files().create(body=file_metadata, fields='id').execute()
            parent_folder_id = folder.get('id')
            # Save folder ID to employee record
            if employee:
                employee.drive_folder_id = parent_folder_id
                db.session.commit()

        if folder_name:
            response = service.files().list(
                q=f"name='{folder_name}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='files(id, name)'
            ).execute()
            subfolders = response.get('files', [])
            if subfolders:
                target_folder_id = subfolders[0]['id']
            else:
                file_metadata = {
                    'name': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [parent_folder_id]
                }
                subfolder = service.files().create(body=file_metadata, fields='id').execute()
                target_folder_id = subfolder.get('id')
        else:
            target_folder_id = parent_folder_id

        file_metadata = {
            'name': filename,
            'parents': [target_folder_id]
        }
        media = MediaFileUpload(file_path, mimetype='application/pdf', resumable=True)
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        file_id = file.get('id')
        return file_id

    except Exception as e:
        import traceback
        print("❌ Exception in upload_file_to_drive:")
        traceback.print_exc()
        raise Exception(f"Drive upload failed: {str(e)}")
    
def delete_drive_file(file_id):
    """Delete a file from Google Drive by its ID."""
    service, error = get_drive_service()
    if error:
        print(f"Drive service error: {error}")
        return False
    try:
        service.files().delete(fileId=file_id).execute()
        print(f"Deleted Drive file {file_id}")
        return True
    except Exception as e:
        print(f"Error deleting Drive file {file_id}: {e}")
        return False

def get_parent_folder_id(file_id):
    """Return the ID of the parent folder of a given file."""
    service, error = get_drive_service()
    if error:
        return None
    try:
        file = service.files().get(fileId=file_id, fields='parents').execute()
        parents = file.get('parents', [])
        return parents[0] if parents else None
    except Exception as e:
        print(f"Error getting parent for {file_id}: {e}")
        return None

def is_folder_empty(folder_id):
    """Check if a Drive folder has any files/folders inside (excluding trashed)."""
    service, error = get_drive_service()
    if error:
        return False
    try:
        # List items inside the folder
        query = f"'{folder_id}' in parents and trashed=false"
        response = service.files().list(q=query, fields='files(id)').execute()
        files = response.get('files', [])
        return len(files) == 0
    except Exception as e:
        print(f"Error checking folder {folder_id}: {e}")
        return False

def delete_drive_folder(folder_id):
    """Delete a Drive folder by its ID (folder must be empty)."""
    service, error = get_drive_service()
    if error:
        return False
    try:
        service.files().delete(fileId=folder_id).execute()
        print(f"Deleted Drive folder {folder_id}")
        return True
    except Exception as e:
        print(f"Error deleting folder {folder_id}: {e}")
        return False

@app.route('/admin/document/<int:doc_id>/delete', methods=['POST'])
def delete_document(doc_id):
    if not session.get('is_admin'):
        return "Unauthorized", 403

    doc = Document.query.get_or_404(doc_id)
    employee = doc.employee
    drive_file_id = doc.drive_file_id

    # Delete from Drive if we have a file ID
    if drive_file_id:
        parent_id = get_parent_folder_id(drive_file_id)
        delete_drive_file(drive_file_id)
        if parent_id and is_folder_empty(parent_id):
            delete_drive_folder(parent_id)

    # Remove database record
    db.session.delete(doc)
    db.session.commit()

    flash('Document deleted successfully!', 'success')
    return redirect(url_for('view_employee', emp_id=employee.id))

# ==================== GOOGLE DRIVE AUTHENTICATION ROUTES ====================

@app.route('/authorize')
def authorize():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    try:
        flow = get_google_flow()
    except Exception as e:
        flash(f'Failed to load Google credentials: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard'))

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    session['oauth_state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    if 'oauth_state' not in session:
        flash('OAuth session expired. Please try again.', 'danger')
        return redirect(url_for('authorize'))

    try:
        # FIXED: Don't pass redirect_uri parameter
        flow = get_google_flow(state=session['oauth_state'])
        
        # The redirect_uri is already set inside get_google_flow()
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials
    except Exception as e:
        flash(f'OAuth callback failed: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard'))

    token_path = os.path.join(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], 'token.pickle')
    with open(token_path, 'wb') as token:
        pickle.dump(credentials, token)
    
    session.pop('oauth_state', None)
    flash('✅ Successfully connected to Google Drive!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/disconnect-drive')
def disconnect_drive():
    """Disconnect Google Drive"""
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    token_path = os.path.join(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], 'token.pickle')
    if os.path.exists(token_path):
        os.remove(token_path)
        flash('Disconnected from Google Drive', 'success')
    else:
        flash('No Google Drive connection found', 'info')
    
    return redirect(url_for('admin_dashboard'))

@app.context_processor
def utility_processor():
    def check_drive_connection():
        token_path = os.path.join(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], 'token.pickle')
        return os.path.exists(token_path)
    
    return dict(check_drive_connection=check_drive_connection)

#============company routes====================
@app.route('/admin/companies')
def admin_companies():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    companies = Company.query.all()
    return render_template('admin_companies.html', companies=companies)

@app.route('/admin/companies/add', methods=['GET', 'POST'])
def add_company():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    if request.method == 'POST':
        # Handle file uploads
        logo_file = request.files.get('logo')
        signature_file = request.files.get('signature')
        logo_filename = None
        signature_filename = None

        if logo_file and logo_file.filename:
            logo_filename = secure_filename(logo_file.filename)
            logo_file.save(os.path.join(app.root_path, 'static', 'images', logo_filename))
        if signature_file and signature_file.filename:
            signature_filename = secure_filename(signature_file.filename)
            signature_file.save(os.path.join(app.root_path, 'static', 'images', 'signatures', signature_filename))

        company = Company(
            name=request.form['name'],
            address=request.form.get('address'),
            phone=request.form.get('phone'),
            email=request.form.get('email'),
            website=request.form.get('website'),
            logo=logo_filename,
            signature=signature_filename,
            hr_name=request.form.get('hr_name'),
            hr_designation=request.form.get('hr_designation'),
            hr_email=request.form.get('hr_email')
        )
        db.session.add(company)
        db.session.commit()
        flash('Company added successfully', 'success')
        return redirect(url_for('admin_companies'))
    return render_template('company_form.html', company=None)

@app.route('/admin/companies/<int:company_id>/edit', methods=['GET', 'POST'])
def edit_company(company_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    company = Company.query.get_or_404(company_id)
    if request.method == 'POST':
        company.name = request.form['name']
        company.address = request.form.get('address')
        company.phone = request.form.get('phone')
        company.email = request.form.get('email')
        company.website = request.form.get('website')
        company.hr_name = request.form.get('hr_name')
        company.hr_designation = request.form.get('hr_designation')
        company.hr_email = request.form.get('hr_email')

        # Handle file uploads (replace if new file uploaded)
        logo_file = request.files.get('logo')
        if logo_file and logo_file.filename:
            logo_filename = secure_filename(logo_file.filename)
            logo_file.save(os.path.join(app.root_path, 'static', 'images', logo_filename))
            company.logo = logo_filename

        signature_file = request.files.get('signature')
        if signature_file and signature_file.filename:
            signature_filename = secure_filename(signature_file.filename)
            signature_file.save(os.path.join(app.root_path, 'static', 'images', 'signatures', signature_filename))
            company.signature = signature_filename

        db.session.commit()
        flash('Company updated', 'success')
        return redirect(url_for('admin_companies'))
    return render_template('company_form.html', company=company)

@app.route('/admin/companies/<int:company_id>/delete', methods=['POST'])
def delete_company(company_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    company = Company.query.get_or_404(company_id)
    db.session.delete(company)
    db.session.commit()
    flash('Company deleted', 'success')
    return redirect(url_for('admin_companies'))

@app.route('/admin/select-company-for-doc/<int:emp_id>/<doc_type>', methods=['GET', 'POST'])
def select_company_for_doc(emp_id, doc_type):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    employee = Employee.query.get_or_404(emp_id)
    companies = Company.query.all()

    if not companies:
        flash('No companies found. Please add a company first.', 'danger')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        company_id = request.form.get('company_id', type=int)
        if not company_id:
            flash('Please select a company.', 'danger')
            return redirect(request.url)

        company = Company.query.get(company_id)
        if not company:
            flash('Selected company does not exist.', 'danger')
            return redirect(request.url)

        # --- Build form_data based on document type ---
        if doc_type == 'resignation_acceptance':
            # Resignation acceptance logic
            if not employee.resignation_date or not employee.resignation_email_content:
                flash('Resignation details not found. Please mark employee as resigned first.', 'danger')
                return redirect(url_for('view_employee', emp_id=employee.id))

            relieving_date = employee.resignation_date + timedelta(days=30)
            formatted_relieving_date = relieving_date.strftime('%d %B %Y')
            formatted_email_datetime = employee.resignation_datetime.strftime('%d %B %Y %I:%M %p') if employee.resignation_datetime else None

            form_data = {
                'employee_id': employee.employee_id,
                'company': company.id,
                'document_type': doc_type,
                'full_name': employee.full_name,
                'employee_email': employee.email,
                'designation': employee.designation,
                'relieving_date': formatted_relieving_date,
                'resignation_email': employee.resignation_email_content,
                'resignation_email_datetime': formatted_email_datetime,
                'hr_name': company.hr_name,
                'hr_designation': company.hr_designation,
                'hr_email': company.hr_email,
                'timestamp': datetime.now().strftime('%d/%m/%Y %I:%M %p')
            }

        # FIXED: Added 'relieving_letter' to the list
        elif doc_type in ['offer_letter', 'appointment_letter', 'experience_letter', 
                         'relieving_letter', 'other_standard_doc']:
            # Generic salary‑based document logic
            ctc = float(employee.ctc)
            monthly_ctc = round(ctc / 12)

            basic = round(monthly_ctc * 0.5)
            hra = round(basic * 0.5)
            conveyance = round(monthly_ctc * 0.05)
            medical = round(monthly_ctc * 0.014)
            telephone = round(monthly_ctc * 0.02)
            special_allowance = monthly_ctc - (basic + hra + conveyance + medical + telephone)
            professional_tax = 200
            gross_salary = basic + hra + conveyance + medical + telephone + special_allowance
            net_salary = gross_salary - professional_tax

            salary_breakdown = {
                'basic': basic, 'hra': hra, 'conveyance': conveyance,
                'medical': medical, 'telephone': telephone,
                'special_allowance': special_allowance, 'professional_tax': professional_tax,
                'gross_salary': gross_salary, 'net_salary': net_salary,
                'increment_per_month': 0
            }

            form_data = {
                'employee_id': employee.employee_id,
                'company': company.id,
                'document_type': doc_type,
                'full_name': employee.full_name,
                'address': employee.address,
                'aadhar_no': employee.aadhar_no,
                'pan_no': employee.pan_no,
                'designation': employee.designation,
                'base_ctc': employee.base_ctc,
                'ctc': employee.ctc,
                'increment_per_month': 0,
                'salary_breakdown': salary_breakdown,
                'joining_date': employee.joining_date.strftime('%Y-%m-%d') if employee.joining_date else None,
                'resignation_date': employee.resignation_date.strftime('%Y-%m-%d') if employee.resignation_date else None,
                'bank_details': {
                    'account_holder': employee.account_holder,
                    'account_number': employee.account_number,
                    'bank_name': employee.bank_name,
                    'branch': employee.branch,
                    'ifsc_code': employee.ifsc_code
                }
            }

        else:
            flash(f'Document type "{doc_type}" is not supported.', 'danger')
            return redirect(url_for('admin_dashboard'))

        # Save to session and go to preview
        session['form_data'] = form_data
        return redirect(url_for('preview'))

    # GET request – show company selection form
    return render_template('select_company.html',
                           employee=employee,
                           doc_type=doc_type,
                           companies=companies)
                 
# Create default admin if none exists
with app.app_context():
    try:
        # Create all tables first
        print("🔄 Creating database tables...")
        db.create_all()
        print("✅ Database tables created successfully!")
        
        # Then check/create admin
        if Admin.query.first() is None:
            default_admin = Admin(username='admin')
            default_admin.set_password('admin123')
            db.session.add(default_admin)
            db.session.commit()
            print("✅ Default admin created: username='admin', password='admin123'")
    except Exception as e:
        print(f"❌ Database setup error: {e}")

with app.app_context():
    db.create_all()
    # Seed companies if none exist
    if Company.query.count() == 0:
        from config import COMPANIES as static_companies
        for comp in static_companies:
            # Map company names to logo files
            logo_filename = None
            if 'LiteCode' in comp['name']:
                logo_filename = 'lc_logo.png'
            elif 'Arraycon' in comp['name']:
                logo_filename = 'arr_logo.png'
            elif 'Web Minds' in comp['name']:
                logo_filename = 'saraswati-yantra-saraswati-symbol-vector-happy-dussehra-vijayadashmi-sacred-symbol-2JDKHCY.jpg'
            else:
                logo_filename = comp.get('logo')  # fallback to config value
            
            company = Company(
                name=comp['name'],
                address=comp.get('address'),
                phone=comp.get('phone'),
                email=comp.get('email'),
                website=comp.get('website'),
                logo=logo_filename,  # Use the mapped filename
                signature=comp.get('signature'),
                hr_name=comp.get('hr_name'),
                hr_designation=comp.get('hr_designation'),
                hr_email=comp.get('hr_email')
            )
            db.session.add(company)
        db.session.commit()
        print("✅ Static companies imported into database with correct logos.")

if __name__ == '__main__':
    app.run(debug=True)

    