from dotenv import load_dotenv
import os
import json
import traceback
import re

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
from flask import Flask, flash, jsonify, render_template, request, redirect, url_for, session, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
import io
import zipfile
import subprocess
from flask import jsonify
import tempfile
from flask import send_file, make_response, request
from googleapiclient.http import MediaIoBaseDownload

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
from googleapiclient.http import MediaIoBaseDownload

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
    __tablename__ = 'increment_history'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id', ondelete='CASCADE'), nullable=False)
    
    old_ctc = db.Column(db.Float, nullable=False)
    increment_amount = db.Column(db.Float, nullable=False)
    new_ctc = db.Column(db.Float, nullable=False)
    
    effective_date = db.Column(db.Date)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    generated_by = db.Column(db.String(100))
    
    # Relationship with cascade delete
    employee = db.relationship('Employee', backref=db.backref('increment_history', cascade='all, delete-orphan'))

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
    drive_folder_id = db.Column(db.String(100), nullable=True)
    base_ctc = db.Column(db.Float, default=0)
    joining_date = db.Column(db.Date, nullable=True)
    resignation_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default='active')
    profile_image = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    resignation_email_content = db.Column(db.Text, nullable=True)
    resignation_datetime = db.Column(db.DateTime, nullable=True)
    relieving_date = db.Column(db.Date, nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=True)
    company = db.relationship('Company', backref='employees')
    resignation_acceptance_date = db.Column(db.Date, nullable=True)
    
    # Bank Details
    account_holder = db.Column(db.String(100))
    account_number = db.Column(db.String(50))
    bank_name = db.Column(db.String(100))
    branch = db.Column(db.String(100))
    ifsc_code = db.Column(db.String(20))
    
    # Relationships
    documents = db.relationship('Document', backref='employee', lazy=True, cascade='all, delete-orphan')
    # increment_history relationship is defined in IncrementHistory model

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
    document_type = db.Column(db.String(100), nullable=True)
    amount = db.Column(db.Float, default=0)          # total amount due
    paid_amt = db.Column(db.Float, default=0)        # amount actually paid
    due_amount = db.Column(db.Float, default=0)      # amount still due
    overdue_amount = db.Column(db.Float, default=0)  # amount overdue (if any)
    status = db.Column(db.String(50), default='Pending')  # Pending, Partial, Paid, Overdue
    payment_date = db.Column(db.Date, nullable=True)     # when payment was made
    due_date = db.Column(db.Date, nullable=True)         # due date for payment
    notes = db.Column(db.Text, nullable=True)            # payment notes
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
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
    logo_with_name = db.Column(db.String(200), nullable=True)  # For header
    signature = db.Column(db.String(200))   # filename in static/images/signatures
    hr_name = db.Column(db.String(100))
    hr_designation = db.Column(db.String(100))
    hr_email = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)
    notice_period = db.Column(db.String(50), nullable=True)
    email_domain = db.Column(db.String(100), nullable=True)
    accepts_interns = db.Column(db.Boolean, default=True)

#intern model
class Intern(db.Model):
    __tablename__ = 'interns'
    
    id = db.Column(db.Integer, primary_key=True)
    intern_id = db.Column(db.String(20), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    gender = db.Column(db.String(20), nullable=True)
    address = db.Column(db.Text, nullable=True)
    aadhar_no = db.Column(db.String(20), unique=True)
    pan_no = db.Column(db.String(20), unique=True)
    qualification = db.Column(db.String(100), nullable=True)
    college_name = db.Column(db.String(200), nullable=True)
    course = db.Column(db.String(100), nullable=True)
    specialization = db.Column(db.String(100), nullable=True)
    internship_duration = db.Column(db.Integer, default=3)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    stipend = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default='active')
    profile_image = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=True)
    company = db.relationship('Company', backref='interns')
    mentor_name = db.Column(db.String(100), nullable=True)
    mentor_designation = db.Column(db.String(100), nullable=True)
    mentor_email = db.Column(db.String(100), nullable=True)
    
    # ========== ADD BANK DETAILS FIELDS ==========
    account_holder = db.Column(db.String(100), nullable=True)
    account_number = db.Column(db.String(50), nullable=True)
    bank_name = db.Column(db.String(100), nullable=True)
    branch = db.Column(db.String(100), nullable=True)
    ifsc_code = db.Column(db.String(20), nullable=True)
    
    # Relationships
    documents = db.relationship('InternDocument', backref='intern', lazy=True)

#intern documents model
class InternDocument(db.Model):
    __tablename__ = 'intern_documents'
    
    id = db.Column(db.Integer, primary_key=True)
    intern_id = db.Column(db.Integer, db.ForeignKey('interns.id'), nullable=False)
    document_type = db.Column(db.String(50))  # intern_offer_letter, certificate_of_internship
    filename = db.Column(db.String(200))
    file_path = db.Column(db.String(500))
    generated_at = db.Column(db.DateTime, default=datetime.now)
    generated_by = db.Column(db.String(80))
    drive_file_id = db.Column(db.String(100), nullable=True)

#==================helper functions========================
def get_company_domain(company):
    """Get company domain from company object"""
    if not company:
        print("⚠️ No company provided")
        return "company.com"  # Default fallback
    
    # Check if company has an email_domain field (this is what your model uses)
    if hasattr(company, 'email_domain') and company.email_domain:
        print(f"✅ Using company.email_domain: {company.email_domain}")
        return company.email_domain
    
    # Check if company has a domain field (for backward compatibility)
    if hasattr(company, 'domain') and company.domain:
        print(f"✅ Using company.domain: {company.domain}")
        return company.domain
    
    # Check if company has an email field
    if hasattr(company, 'email') and company.email and '@' in company.email:
        domain = company.email.split('@')[1]
        print(f"✅ Extracted domain from company email: {domain}")
        return domain
    
    # Check if company has a website field
    if hasattr(company, 'website') and company.website:
        # Extract domain from website (e.g., https://litecode.com -> litecode.com)
        website = company.website
        website = website.replace('https://', '').replace('http://', '').replace('www.', '')
        domain = website.split('/')[0]
        print(f"✅ Extracted domain from website: {domain}")
        return domain
    
    # Fallback: use company name to generate domain
    if company.name:
        # Convert company name to domain format
        domain = company.name.lower().replace(' ', '').replace('pvt', '').replace('ltd', '').replace('.', '')
        domain = domain + '.com'
        print(f"⚠️ Generated domain from company name: {domain}")
        return domain
    
    print("⚠️ No domain found, using default")
    return "company.com"  # Default fallback

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
    """Get employee email - generate company email if only personal email exists"""
    # Generate company email format
    name_parts = employee.full_name.split()
    first_name = name_parts[0].lower() if name_parts else ''
    last_name = name_parts[-1].lower() if len(name_parts) > 1 else ''
    company_domain = get_company_domain(company)
    
    # Create company email
    generated_email = f"{first_name}.{last_name}@{company_domain}"
    
    # If employee has a stored email and it matches the company domain, use it
    if employee.email and '@' in employee.email:
        # Check if it's a company email (contains company domain)
        if company_domain in employee.email:
            return employee.email
    
    # Otherwise return the generated company email
    return generated_email

def get_google_flow(state=None):
    """Create and return Google OAuth2 flow object"""
    credentials_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'credentials.json')
    
    if not os.path.exists(credentials_path):
        raise Exception(f'credentials.json not found at {credentials_path}')
    
    # Create flow with all required parameters
    flow = Flow.from_client_secrets_file(
        credentials_path,
        scopes=['https://www.googleapis.com/auth/drive.file'],
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    
    if state:
        flow.state = state
    
    return flow

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

#==========payment helper functions===========
def calculate_due_amount(self):
        """Calculate due amount"""
        self.due_amount = self.amount - self.paid_amt
        return self.due_amount

def update_status(self):
        """Update payment status based on paid amount"""
        if self.paid_amt >= self.amount:
            self.status = 'Paid'
        elif self.paid_amt > 0:
            self.status = 'Partial'
        else:
            self.status = 'Pending'
        
        # Check for overdue
        if self.due_date and datetime.now().date() > self.due_date and self.status != 'Paid':
            self.status = 'Overdue'
            self.overdue_amount = self.due_amount

@app.template_filter('get')
def get_filter(dictionary, key, default=''):
    """Safely get value from dictionary"""
    if dictionary is None:
        return default
    if isinstance(dictionary, dict):
        return dictionary.get(key, default)
    return default

#production function
def embed_images_as_base64(html_content):
    """Convert only small images to base64, skip large ones"""
    static_folder = app.static_folder
    images_folder = os.path.join(static_folder, 'images')
    signatures_folder = os.path.join(images_folder, 'signatures')
    
    image_cache = {}
    MAX_IMAGE_SIZE = 500 * 1024  # 500KB - skip images larger than this
    
    def replace_image(match):
        img_tag = match.group(0)
        src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
        if not src_match:
            return img_tag
        
        src = src_match.group(1)
        
        # Skip external URLs and data URIs
        if src.startswith('data:') or src.startswith('http://') or src.startswith('https://'):
            return img_tag
        
        if src in image_cache:
            return img_tag.replace(src, image_cache[src])
        
        abs_path = None
        
        if src.startswith('/static/'):
            relative_path = src.replace('/static/', '')
            abs_path = os.path.join(static_folder, relative_path)
        elif 'signatures' in src:
            filename = src.split('signatures/')[-1]
            abs_path = os.path.join(signatures_folder, filename)
        elif 'images' in src:
            filename = src.split('images/')[-1]
            abs_path = os.path.join(images_folder, filename)
        else:
            return img_tag
        
        if abs_path and os.path.exists(abs_path):
            # Check file size - skip if too large
            file_size = os.path.getsize(abs_path)
            if file_size > MAX_IMAGE_SIZE:
                print(f"⚠️ Skipping large image ({file_size/1024:.1f}KB): {src}")
                return img_tag
            
            try:
                with open(abs_path, 'rb') as f:
                    image_data = base64.b64encode(f.read()).decode('utf-8')
                
                ext = abs_path.split('.')[-1].lower()
                if ext in ['jpg', 'jpeg']:
                    mime = 'image/jpeg'
                elif ext == 'png':
                    mime = 'image/png'
                else:
                    mime = f'image/{ext}'
                
                new_src = f'data:{mime};base64,{image_data}'
                image_cache[src] = new_src
                print(f"✅ Embedded image: {src} ({file_size/1024:.1f}KB)")
                return img_tag.replace(src, new_src)
            except Exception as e:
                print(f"⚠️ Failed to embed {src}: {e}")
                return img_tag
        
        return img_tag
    
    html_content = re.sub(r'<img[^>]+>', replace_image, html_content)
    return html_content

import traceback
def html_to_pdf(html_content, output_path):
    """Convert HTML to PDF with detailed logging"""
    print("="*60)
    print("🔴 HTML_TO_PDF CALLED")
    print(f"📁 Output path: {output_path}")
    print(f"📄 HTML length: {len(html_content)}")
    print("="*60)
    
    try:
        from weasyprint import HTML
        import tempfile
        import os
        
        # Create temp HTML file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
            f.write(html_content)
            temp_html = f.name
        
        print(f"📄 Temp HTML created: {temp_html}")
        
        # SIMPLE CONVERSION - NO EXTRA PARAMETERS
        HTML(filename=temp_html).write_pdf(output_path)
        
        # Clean up
        if os.path.exists(temp_html):
            os.unlink(temp_html)
            print(f"🧹 Temp file cleaned: {temp_html}")
        
        print(f"✅ PDF GENERATED SUCCESSFULLY: {output_path}")
        if os.path.exists(output_path):
            print(f"📁 PDF size: {os.path.getsize(output_path)} bytes")
        
        return True
        
    except ImportError as e:
        print(f"❌ WeasyPrint not installed: {e}")
        traceback.print_exc()
        return False
        
    except Exception as e:
        print(f"❌ PDF generation error: {e}")
        traceback.print_exc()
        return False

#test route
@app.route('/test-pdf-generation')
def test_pdf_generation():
    """Test PDF generation with debugging"""
    try:
        test_html = """
        <html>
        <head>
            <style>
                body {{ font-family: Arial; padding: 50px; }}
                h1 {{ color: blue; }}
            </style>
        </head>
        <body>
            <h1>Test PDF</h1>
            <p>This is a test PDF document generated on Render.</p>
            <p>Timestamp: {}</p>
        </body>
        </html>
        """.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'test_debug.pdf')
        
        print(f"📁 Output path: {output_path}")
        
        result = html_to_pdf(test_html, output_path)
        
        if result and os.path.exists(output_path):
            return send_file(output_path, as_attachment=True, download_name='test_debug.pdf')
        else:
            return f"PDF generation failed. Result: {result}", 500
            
    except Exception as e:
        import traceback
        return f"Error: {str(e)}<br><pre>{traceback.format_exc()}</pre>", 500

# #local function
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
    member_type = session.get('member_type', 'employee')

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
        
        # Format resignation_date
        formatted_resignation_date = ""
        if employee.resignation_date:
            if isinstance(employee.resignation_date, datetime):
                formatted_resignation_date = employee.resignation_date.strftime('%d %B %Y')
            else:
                formatted_resignation_date = employee.resignation_date.strftime('%d %B %Y')
        
        # Format acceptance_date
        formatted_acceptance_date = ""
        if employee.resignation_acceptance_date:
            if isinstance(employee.resignation_acceptance_date, datetime):
                formatted_acceptance_date = employee.resignation_acceptance_date.strftime('%d %B %Y')
            else:
                formatted_acceptance_date = employee.resignation_acceptance_date.strftime('%d %B %Y')
        
        # If acceptance_date is not set, calculate it
        if not formatted_acceptance_date and employee.resignation_date:
            if isinstance(employee.resignation_date, datetime):
                calc_date = employee.resignation_date.date() + timedelta(days=3)
            else:
                calc_date = employee.resignation_date + timedelta(days=3)
            formatted_acceptance_date = calc_date.strftime('%d %B %Y')

        data = {
            'timestamp': datetime.now().strftime('%d %B %Y'),
            'full_name': employee.full_name,
            'first_name': first_name,
            'employee_email': employee_email,
            'company_name': company.name,
            'company_domain': company_domain,
            'hr_email': hr_email,
            'acceptance_date': formatted_acceptance_date,  
            'resignation_date': formatted_resignation_date,  
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
    
    # ========== INTERN DOCUMENTS HANDLER ==========
    if form_data.get('document_type') in ['intern_offer_letter', 'certificate_of_internship']:
        # Check if we have preview data in session (from generate_intern_document)
        data = session.get('intern_preview_data')
        
        # If no preview data, get from database
        if not data:
            intern_id = form_data.get('intern_id')
            intern = None
            if intern_id:
                intern = Intern.query.get(intern_id)
            
            # Get company
            company_id = form_data.get('company')
            company = None
            if company_id:
                try:
                    company = Company.query.get(int(company_id))
                except (ValueError, TypeError):
                    company = None
            
            if not intern:
                flash('Intern not found', 'danger')
                return redirect(url_for('admin_dashboard'))
            
            if not company:
                flash('Company not found', 'danger')
                return redirect(url_for('admin_dashboard'))
            
            # Prepare data for template
            name_parts = intern.full_name.split() if intern.full_name else ['']
            first_name = name_parts[0] if name_parts else ''
            
            end_date = intern.end_date
            if not end_date and intern.start_date:
                end_date = intern.start_date + timedelta(days=intern.internship_duration * 30)
            
            company_domain = get_company_domain(company)
            
            # ========== DATE CALCULATIONS ==========
            if intern.start_date:
                if isinstance(intern.start_date, datetime):
                    start_date = intern.start_date.date()
                else:
                    start_date = intern.start_date
                offer_date = start_date - timedelta(days=5)
                formatted_offer_date = offer_date.strftime('%d %B %Y')
                formatted_joining_date = start_date.strftime('%d %B %Y')
                acceptance_deadline = start_date + timedelta(days=5)
                formatted_acceptance_deadline = acceptance_deadline.strftime('%d %B %Y')
            else:
                formatted_joining_date = 'To be confirmed'
                formatted_offer_date = datetime.now().strftime('%d %B %Y')
                formatted_acceptance_deadline = (datetime.now() + timedelta(days=5)).strftime('%d %B %Y')
            
            data = {
                'timestamp': datetime.now().strftime('%d %B %Y'),
                'full_name': intern.full_name,
                'first_name': first_name,
                'email': intern.email,
                'address': intern.address,
                'qualification': intern.qualification,
                'college_name': intern.college_name,
                'course': intern.course,
                'specialization': intern.specialization,
                'internship_duration': intern.internship_duration,
                'start_date': formatted_joining_date,
                'end_date': end_date.strftime('%d %B %Y') if end_date else 'TBD',
                'stipend': intern.stipend,
                'mentor_name': intern.mentor_name or company.hr_name,
                'mentor_designation': intern.mentor_designation or company.hr_designation,
                'hr_name': company.hr_name or 'HR Department',
                'hr_designation': company.hr_designation or 'HR Manager',
                'company_name': company.name,
                'company_domain': company_domain,
                'intern_id': intern.intern_id,
                'certificate_no': f"CERT-{intern.intern_id}-{datetime.now().year}",
                'offer_date': formatted_offer_date,
                'acceptance_deadline': formatted_acceptance_deadline,
                'joining_date': formatted_joining_date
            }
            
            # Store in session for future use
            session['intern_preview_data'] = data
        
        # Get company for watermark
        company = Company.query.get(form_data.get('company')) if form_data.get('company') else None
        
        return render_template(
            f'documents/{form_data.get("document_type")}.html',
            data=data,
            company=company,
            watermark_logo=company.logo if company else None,
            now=datetime.now()
        )
    
    # ========== OFFER AND SALARY DOCUMENTS ==========
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
    member_type = session.get('member_type', 'employee')
    
    if not form_data:
        flash('No document data found. Please try again.', 'danger')
        return redirect(url_for('index'))

    # ========== RESIGNATION ACCEPTANCE HANDLER ==========
    if doc_type == 'resignation_acceptance':
        employee_id = form_data.get('employee_id')
        employee = None
        if employee_id:
            employee = Employee.query.filter_by(employee_id=employee_id).first()
        
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
        
        if not employee.resignation_datetime:
            flash('Resignation date and time not found.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))
        
        if not employee.relieving_date:
            flash('Relieving date not found.', 'danger')
            return redirect(url_for('view_employee', emp_id=employee.id))
        
        name_parts = employee.full_name.split() if employee.full_name else ['']
        first_name = name_parts[0] if name_parts else ''
        
        company_domain = get_company_domain(company)
        hr_email = get_hr_email(company)
        employee_email = get_employee_email(employee, company)
        
        formatted_resignation_date = ""
        if employee.resignation_date:
            if isinstance(employee.resignation_date, datetime):
                formatted_resignation_date = employee.resignation_date.strftime('%d %B %Y')
            else:
                formatted_resignation_date = employee.resignation_date.strftime('%d %B %Y')
        
        formatted_acceptance_date = ""
        if employee.resignation_acceptance_date:
            if isinstance(employee.resignation_acceptance_date, datetime):
                formatted_acceptance_date = employee.resignation_acceptance_date.strftime('%d %B %Y')
            else:
                formatted_acceptance_date = employee.resignation_acceptance_date.strftime('%d %B %Y')
        
        if not formatted_acceptance_date and employee.resignation_date:
            if isinstance(employee.resignation_date, datetime):
                calc_date = employee.resignation_date.date() + timedelta(days=3)
            else:
                calc_date = employee.resignation_date + timedelta(days=3)
            formatted_acceptance_date = calc_date.strftime('%d %B %Y')

        data = {
            'timestamp': datetime.now().strftime('%d %B %Y'),
            'full_name': employee.full_name,
            'first_name': first_name,
            'employee_email': employee_email,
            'company_name': company.name,
            'company_domain': company_domain,
            'hr_email': hr_email,
            'acceptance_date': formatted_acceptance_date,  
            'resignation_date': formatted_resignation_date,  
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

    # ========== INTERN DOCUMENTS HANDLER ==========
    if doc_type in ['intern_offer_letter', 'certificate_of_internship']:
        print(f"\n{'='*60}")
        print(f"🔍 PREVIEW INTERN DOCUMENT: {doc_type}")
        print(f"{'='*60}")
        
        # Check if we have preview data in session
        data = session.get('intern_preview_data')
        
        # If no preview data, get from database
        if not data:
            print("⚠️ No preview data in session, fetching from database...")
            intern_id = form_data.get('intern_id')
            intern = None
            if intern_id:
                intern = Intern.query.get(intern_id)
            
            company_id = form_data.get('company')
            company = None
            if company_id:
                try:
                    company = Company.query.get(int(company_id))
                except (ValueError, TypeError):
                    company = None
            
            if not intern:
                flash('Intern not found', 'danger')
                return redirect(url_for('admin_dashboard'))
            
            if not company:
                flash('Company not found', 'danger')
                return redirect(url_for('admin_dashboard'))
            
            # Calculate data
            name_parts = intern.full_name.split() if intern.full_name else ['']
            first_name = name_parts[0] if name_parts else ''
            
            end_date = intern.end_date
            if not end_date and intern.start_date:
                end_date = intern.start_date + timedelta(days=intern.internship_duration * 30)
            
            company_domain = get_company_domain(company)
            if not company_domain:
                company_domain = company.name.lower().replace(' ', '').replace('pvt', '').replace('ltd', '').replace('.', '') + '.com'
            
            hr_email = company.hr_email or f"hr@{company_domain}"
            hr_name = company.hr_name or 'HR Department'
            hr_designation = company.hr_designation or 'HR Manager'

            if intern.start_date:
                if isinstance(intern.start_date, datetime):
                    start_date = intern.start_date.date()
                else:
                    start_date = intern.start_date
                offer_date = start_date - timedelta(days=5)
                formatted_offer_date = offer_date.strftime('%d %B %Y')
                formatted_joining_date = start_date.strftime('%d %B %Y')
                acceptance_deadline = start_date + timedelta(days=5)
                formatted_acceptance_deadline = acceptance_deadline.strftime('%d %B %Y')
            else:
                formatted_joining_date = 'To be confirmed'
                formatted_offer_date = datetime.now().strftime('%d %B %Y')
                formatted_acceptance_deadline = (datetime.now() + timedelta(days=5)).strftime('%d %B %Y')
            
            data = {
                'timestamp': datetime.now().strftime('%d %B %Y'),
                'full_name': intern.full_name,
                'first_name': first_name,
                'email': intern.email or '',
                'address': intern.address or '',
                'qualification': intern.qualification or '',
                'college_name': intern.college_name or '',
                'course': intern.course or '',
                'specialization': intern.specialization or '',
                'internship_duration': intern.internship_duration or 3,
                'start_date': formatted_joining_date,
                'end_date': end_date.strftime('%d %B %Y') if end_date else 'TBD',
                'stipend': intern.stipend or 0,
                'mentor_name': intern.mentor_name or hr_name,
                'mentor_designation': intern.mentor_designation or hr_designation,
                'hr_name': hr_name,
                'hr_designation': hr_designation,
                'company_name': company.name,
                'company_domain': company_domain,
                'intern_id': intern.intern_id,
                'certificate_no': f"CERT-{intern.intern_id}-{datetime.now().year}",
                'offer_date': formatted_offer_date,
                'acceptance_deadline': formatted_acceptance_deadline,
                'joining_date': formatted_joining_date
            }
            session['intern_preview_data'] = data
            print(f"✅ Data fetched from database for: {intern.full_name}")
        else:
            print(f"✅ Using preview data from session")
        
        # Get company for watermark
        company = None
        company_id = form_data.get('company')
        if company_id:
            try:
                company = Company.query.get(int(company_id))
            except (ValueError, TypeError):
                company = None
        
        print(f"📄 Rendering template: documents/{doc_type}.html")
        print(f"{'='*60}\n")
        
        return render_template(
            f'documents/{doc_type}.html',
            data=data,
            company=company,
            watermark_logo=company.logo if company else None,
            now=datetime.now()
        )

    # ========== OTHER DOCUMENTS ==========
    form_data = convert_dates(form_data)

    if form_data.get('joining_date'):
        date_before = get_previous_workday(form_data['joining_date'], 8)
        form_data['date_before'] = date_before

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

    ctc = float(form_data.get('ctc') or 0)
    increment_per_month = float(form_data.get('increment_per_month') or 0)
    
    components = calculate_salary_components(
        ctc=ctc,
        increment_per_month=increment_per_month,
        paid_days=30,
        month_days=30
    )
    
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
    member_type = session.get('member_type', 'employee')

    if not form_data:
        return redirect(url_for('index'))

    upload_to_drive_flag = request.form.get('upload_to_drive') == 'true'
    doc_type = form_data.get('document_type')

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

        # Format resignation_date
        formatted_resignation_date = ""
        if employee.resignation_date:
            if isinstance(employee.resignation_date, datetime):
                formatted_resignation_date = employee.resignation_date.strftime('%d %B %Y')
            else:
                formatted_resignation_date = employee.resignation_date.strftime('%d %B %Y')
        
        # Format acceptance_date
        formatted_acceptance_date = ""
        if employee.resignation_acceptance_date:
            if isinstance(employee.resignation_acceptance_date, datetime):
                formatted_acceptance_date = employee.resignation_acceptance_date.strftime('%d %B %Y')
            else:
                formatted_acceptance_date = employee.resignation_acceptance_date.strftime('%d %B %Y')
        
        # If acceptance_date is not set, calculate it
        if not formatted_acceptance_date and employee.resignation_date:
            if isinstance(employee.resignation_date, datetime):
                calc_date = employee.resignation_date.date() + timedelta(days=3)
            else:
                calc_date = employee.resignation_date + timedelta(days=3)
            formatted_acceptance_date = calc_date.strftime('%d %B %Y')

        data = {
            'timestamp': datetime.now().strftime('%d %B %Y'),
            'full_name': employee.full_name,
            'first_name': first_name,
            'employee_email': employee_email,
            'company_name': company.name,
            'company_domain': company_domain,
            'hr_email': hr_email,
            'acceptance_date': formatted_acceptance_date,  
            'resignation_date': formatted_resignation_date,
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
    
    # ========== INTERN DOCUMENTS HANDLER ==========
    if doc_type in ['intern_offer_letter', 'certificate_of_internship']:
        # Get intern from session
        intern_id = form_data.get('intern_id')
        intern = None
        if intern_id:
            intern = Intern.query.get(intern_id)
        
        if not intern:
            flash('Intern not found', 'danger')
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
        
        # Get preview data from session or calculate
        data = session.get('intern_preview_data')
        
        if not data:
            # Calculate fresh data with fallbacks
            name_parts = intern.full_name.split() if intern.full_name else ['']
            first_name = name_parts[0] if name_parts else ''
            
            end_date = intern.end_date
            if not end_date and intern.start_date:
                end_date = intern.start_date + timedelta(days=intern.internship_duration * 30)
            
            # Ensure company_domain has a value
            company_domain = get_company_domain(company)
            if not company_domain:
                company_domain = company.name.lower().replace(' ', '') + '.com'
            
            # Ensure hr_email has a value
            hr_email = company.hr_email or f"hr@{company_domain}"
            
            # Ensure hr_name has a value
            hr_name = company.hr_name or 'HR Department'
            
            # Ensure hr_designation has a value
            hr_designation = company.hr_designation or 'HR Manager'
            
            if intern.start_date:
                if isinstance(intern.start_date, datetime):
                    start_date = intern.start_date.date()
                else:
                    start_date = intern.start_date
                offer_date = start_date - timedelta(days=5)
                formatted_offer_date = offer_date.strftime('%d %B %Y')
                formatted_joining_date = start_date.strftime('%d %B %Y')
                acceptance_deadline = start_date + timedelta(days=5)
                formatted_acceptance_deadline = acceptance_deadline.strftime('%d %B %Y')
            else:
                formatted_joining_date = 'To be confirmed'
                formatted_offer_date = datetime.now().strftime('%d %B %Y')
                formatted_acceptance_deadline = (datetime.now() + timedelta(days=5)).strftime('%d %B %Y')
            
            data = {
                'timestamp': datetime.now().strftime('%d %B %Y'),
                'full_name': intern.full_name,
                'first_name': first_name,
                'email': intern.email or '',
                'address': intern.address or '',
                'qualification': intern.qualification or '',
                'college_name': intern.college_name or '',
                'course': intern.course or '',
                'specialization': intern.specialization or '',
                'internship_duration': intern.internship_duration or 3,
                'start_date': formatted_joining_date,
                'end_date': end_date.strftime('%d %B %Y') if end_date else 'TBD',
                'stipend': intern.stipend or 0,
                'mentor_name': intern.mentor_name or hr_name,
                'mentor_designation': intern.mentor_designation or hr_designation,
                'hr_name': hr_name,
                'hr_designation': hr_designation,
                'company_name': company.name,
                'company_domain': company_domain,
                'intern_id': intern.intern_id,
                'certificate_no': f"CERT-{intern.intern_id}-{datetime.now().year}",
                'offer_date': formatted_offer_date,
                'acceptance_deadline': formatted_acceptance_deadline,
                'joining_date': formatted_joining_date
            }
        
        # Generate HTML
        try:
            html_content = render_template(f'documents/{doc_type}.html', data=data, company=company)
            
            # Create PDF filename
            filename = f"{doc_type}_{intern.intern_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            print(f"📄 Generating PDF for intern: {intern.full_name}")
            print(f"📁 Output path: {file_path}")
            
            # Convert to PDF
            if not html_to_pdf(html_content, file_path):
                flash('Failed to generate PDF document. Check server logs for details.', 'danger')
                return redirect(url_for('admin_dashboard', tab='document_generator'))
            
            # Save document record
            document = InternDocument(
                intern_id=intern.id,
                document_type=doc_type,
                filename=filename,
                file_path=file_path,
                generated_by=session.get('admin_username', 'admin'),
                generated_at=datetime.now()
            )
            db.session.add(document)
            db.session.commit()
            
            #Upload to Drive if requested
            if upload_to_drive_flag:
                try:
                    # Create drive folder structure for interns
                    service, error = get_drive_service()
                    if not error:
                        intern_folder_name = f"{intern.intern_id}_{intern.full_name.replace(' ', '_')}"
                        folder_response = service.files().list(
                            q=f"name='{intern_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                            spaces='drive', fields='files(id)'
                        ).execute()
                        folders = folder_response.get('files', [])
                        if folders:
                            parent_folder_id = folders[0]['id']
                        else:
                            file_metadata = {'name': intern_folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
                            folder = service.files().create(body=file_metadata, fields='id').execute()
                            parent_folder_id = folder.get('id')
                        
                        doc_folder_map = {
                            'intern_offer_letter': 'Offer Letters',
                            'certificate_of_internship': 'Certificates'
                        }
                        folder_name = doc_folder_map.get(doc_type, 'Documents')
                        
                        folder_response = service.files().list(
                            q=f"name='{folder_name}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                            spaces='drive', fields='files(id)'
                        ).execute()
                        subfolders = folder_response.get('files', [])
                        if subfolders:
                            target_folder_id = subfolders[0]['id']
                        else:
                            file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_folder_id]}
                            subfolder = service.files().create(body=file_metadata, fields='id').execute()
                            target_folder_id = subfolder.get('id')
                        
                        media = MediaFileUpload(file_path, mimetype='application/pdf', resumable=True)
                        file = service.files().create(
                            body={'name': filename, 'parents': [target_folder_id]},
                            media_body=media,
                            fields='id'
                        ).execute()
                        document.drive_file_id = file.get('id')
                        db.session.commit()
                except Exception as e:
                    print(f"Drive upload failed: {e}")
            
            # Clear session data
            session.pop('form_data', None)
            session.pop('intern_preview_data', None)
            session.pop('selected_months', None)
            
            flash(f'✅ {doc_type.replace("_", " ").title()} generated successfully for {intern.full_name}!', 'success')
            return redirect(url_for('admin_dashboard', tab='document_generator'))
            
        except Exception as e:
            print(f"❌ Error generating intern document: {e}")
            import traceback
            traceback.print_exc()
            flash(f'Error generating document: {str(e)}', 'danger')
            return redirect(url_for('admin_dashboard', tab='document_generator'))
    
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
        # Add document date to form_data for template
        if pending and 'document_date' in pending:
            form_data['document_date'] = pending['document_date']
        if pending and 'effective_date_formatted' in pending:
            form_data['increment_effective_date_formatted'] = pending['effective_date_formatted']

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

            #always save local file first
            local_file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if html_to_pdf(html, local_file_path):
                files_generated.append(month)

                #save document record with local path
                doc = Document(
                    employee_id=employee.id,
                    document_type=doc_type,
                    filename=filename,
                    file_path=local_file_path,
                    month=month,
                    year=session.get('selected_year', datetime.now().year),
                    generated_by=session.get('admin_username', 'system'),
                    drive_file_id=None
                )

                # Upload to Drive if requested
                if upload_to_drive_flag:
                    try:
                        drive_file_id = upload_file_to_drive(local_file_path, filename, f"Salary Slips/{month}", employee)
                        if drive_file_id:
                            doc.drive_file_id = drive_file_id
                    except Exception as e:
                        print(f"Drive upload error: {e}")
                
                db.session.add(doc)
            else:
                failed_months.append(month)

            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                temp_path = tmp_file.name

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
        'paid_days': 30,
        # Add document date for increment letter
        'document_date': form_data.get('document_date'),
        'increment_effective_date_formatted': form_data.get('increment_effective_date_formatted')
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
    # ========== Save local file first ==========
    local_file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    if not html_to_pdf(html, local_file_path):
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
            drive_file_id = upload_file_to_drive(local_file_path, filename, folder_name, employee)
        except Exception as e:
            print("Drive Upload Error:", e)

    if employee:
        doc = Document(
            employee_id=employee.id,
            document_type=doc_type,
            filename=filename,
            file_path=local_file_path,
            generated_by=session.get('admin_username', 'system'),
            drive_file_id=drive_file_id
        )
        db.session.add(doc)

    db.session.commit()

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
    selected_member_id = request.args.get('member_id', type=int)
    selected_member_type = request.args.get('member_type', 'employee')

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

    # Get all interns (needed for members dashboard)
    interns = Intern.query.order_by(Intern.created_at.desc()).all()

    total_employees = len(employees)
    total_interns = len(interns)
    active_employees = sum(1 for emp in employees if emp.status == 'active')
    active_interns = sum(1 for intern in interns if intern.status == 'active')

    # ========== GET ALL EMPLOYEE PAYMENTS (NO INTERN PAYMENTS) ==========
    all_payments = []
    
    # Get employee payments only
    emp_payments = db.session.query(
        Payment.id,
        Employee.full_name.label('employee_name'),
        Employee.employee_id.label('employee_id'),
        Payment.document_type,
        Payment.amount,
        Payment.paid_amt,
        Payment.status,
        Payment.payment_date,
        Payment.due_date,
        Payment.created_at
    ).join(Employee, Payment.employee_id == Employee.id).all()
    
    for p in emp_payments:
        due_amount = p.amount - p.paid_amt
        if p.paid_amt >= p.amount:
            status = 'Paid'
            status_class = 'success'
        elif p.paid_amt > 0:
            status = 'Partial'
            status_class = 'warning'
        else:
            status = 'Pending'
            status_class = 'secondary'
        
        all_payments.append({
            'id': p.id,
            'employee_name': p.employee_name,
            'employee_id': p.employee_id,
            'document_type': p.document_type or 'N/A',
            'amount': p.amount,
            'paid_amt': p.paid_amt,
            'due_amount': due_amount,
            'status': status,
            'status_class': status_class,
            'payment_date': p.payment_date.strftime('%d %b %Y') if p.payment_date else 'N/A',
            'due_date': p.due_date.strftime('%d %b %Y') if p.due_date else 'N/A',
            'created_at': p.created_at.strftime('%d %b %Y') if p.created_at else 'N/A',
        })
    
    # Sort all payments by created date (newest first)
    all_payments.sort(key=lambda x: x['created_at'], reverse=True)
    
    # Calculate totals
    total_amount = sum(p['amount'] for p in all_payments)
    total_paid = sum(p['paid_amt'] for p in all_payments)
    total_due = sum(p['due_amount'] for p in all_payments)
    
    # Count by status
    paid_count = sum(1 for p in all_payments if p['status'] == 'Paid')
    pending_count = sum(1 for p in all_payments if p['status'] == 'Pending')
    partial_count = sum(1 for p in all_payments if p['status'] == 'Partial')
    overdue_count = sum(1 for p in all_payments if p['status'] == 'Overdue')
    
    # Get companies for add member form
    companies = Company.query.all()

    return render_template('admin_dashboard.html',
                         employees=employee_data,
                         interns=interns,  # ← ADD THIS BACK
                         companies=companies,
                         active_tab=active_tab,
                         selected_emp_id=selected_emp_id,
                         selected_member_id=selected_member_id,
                         selected_member_type=selected_member_type,
                         now=datetime.now(),
                         total_employees=total_employees,
                         total_interns=total_interns,  # ← ADD THIS BACK
                         active_employees=active_employees,
                         active_interns=active_interns,  # ← ADD THIS BACK
                         total_documents=total_documents,
                         pending_payments=pending_count,
                         paid_count=paid_count,
                         pending_count=pending_count,
                         partial_count=partial_count,
                         overdue_count=overdue_count,
                         paid_amount=total_paid,
                         pending_amount=total_due,
                         overdue_amount=0,
                         payments=all_payments)

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
    drive_folder_id = employee.drive_folder_id

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

        # Delete increment history records first (they will be cascaded but handle explicitly)
        for history in employee.increment_history:
            db.session.delete(history)

        # After deleting all documents and history, delete the main employee folder (if exists and empty)
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
        print(f"Error: {str(e)}")

    return redirect(url_for('admin_dashboard', tab='members'))

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
            
            # ========== CALCULATE DOCUMENT DATE (30 days before effective date) ==========
            if effective_date:
                try:
                    effective_date_obj = datetime.strptime(effective_date, '%Y-%m-%d').date()
                    # Calculate document date (30 days before effective date)
                    document_date_obj = effective_date_obj - timedelta(days=7)
                    document_date = document_date_obj.strftime('%d %B %Y')
                    # Store both dates
                    effective_date_formatted = effective_date_obj.strftime('%d %B %Y')
                    effective_date_str = effective_date
                except Exception as e:
                    print(f"Date parsing error: {e}")
                    effective_date_obj = datetime.now().date()
                    document_date_obj = effective_date_obj - timedelta(days=7)
                    document_date = document_date_obj.strftime('%d %B %Y')
                    effective_date_formatted = effective_date_obj.strftime('%d %B %Y')
                    effective_date_str = effective_date_obj.strftime('%Y-%m-%d')
            else:
                effective_date_obj = datetime.now().date()
                document_date_obj = effective_date_obj - timedelta(days=7)
                document_date = document_date_obj.strftime('%d %B %Y')
                effective_date_formatted = effective_date_obj.strftime('%d %B %Y')
                effective_date_str = effective_date_obj.strftime('%Y-%m-%d')

            if increment_amount <= 0:
                flash('Increment amount must be greater than zero.', 'danger')
                return render_template('increment_form.html', employee=employee, companies=Company.query.all(), now=datetime.now)

            session['pending_increment'] = {
                'amount': increment_amount,
                'effective_date': effective_date_str,
                'effective_date_obj': effective_date_obj,
                'effective_date_formatted': effective_date_formatted,
                'document_date': document_date,
                'document_date_obj': document_date_obj,
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
                'basic': basic, 
                'hra': hra, 
                'conveyance': conveyance,
                'medical': medical, 
                'telephone': telephone,
                'special_allowance': special_allowance, 
                'professional_tax': professional_tax,
                'gross_salary': gross_salary, 
                'net_salary': net_salary,
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
                'increment_effective_date': effective_date_str,
                'increment_effective_date_formatted': effective_date_formatted,
                'document_date': document_date,
                'document_date_obj': document_date_obj,
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
        month_days_values = {}

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
        session['month_days_values'] = month_days_values

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
            month_days=first_month_days
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
            'month_days': first_month_days,
            
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

# ========== INTERN DOCUMENT GENERATION ROUTES ==========
@app.route('/set-intern-preview/<int:intern_id>/<doc_type>', methods=['POST'])
def set_intern_preview(intern_id, doc_type):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    try:
        intern = Intern.query.get_or_404(intern_id)
        
        # Set session data
        session['form_data'] = {
            'document_type': doc_type,
            'intern_id': intern.id,
            'company': intern.company_id,
            'member_type': 'intern'
        }
        
        # Redirect to preview_document with the doc_type
        return redirect(url_for('preview_document', doc_type=doc_type))
        
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard', tab='document_generator'))

@app.route('/set-intern-session', methods=['POST'])
def set_intern_session():
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    intern_id = data.get('intern_id')
    doc_type = data.get('doc_type')
    
    try:
        intern = Intern.query.get(intern_id)
        if not intern:
            return jsonify({'error': 'Intern not found'}), 404
        
        session['form_data'] = {
            'document_type': doc_type,
            'intern_id': intern.id,
            'company': intern.company_id,
            'member_type': 'intern'
        }
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/add-intern', methods=['POST'])
def add_intern():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    try:
        # Get the next intern ID number
        last_intern = Intern.query.order_by(Intern.id.desc()).first()
        next_id = (last_intern.id + 1) if last_intern else 1
        intern_id = f"LMSI{str(next_id).zfill(4)}"
        
        # Handle profile image upload
        profile_image = None
        if 'profile_image' in request.files:
            file = request.files['profile_image']
            if file and file.filename:
                filename = secure_filename(f"{intern_id}_{file.filename}")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'profiles', filename)
                file.save(filepath)
                profile_image = filename
        
        # Calculate end date
        start_date = None
        end_date = None
        if request.form.get('start_date'):
            start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date()
            duration = int(request.form.get('internship_duration', 3))
            end_date = start_date + timedelta(days=duration * 30)
        
        # Create new intern
        intern = Intern(
            intern_id=intern_id,
            full_name=request.form.get('full_name'),
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            gender=request.form.get('gender'),
            address=request.form.get('address'),
            aadhar_no=request.form.get('aadhar_no'),
            pan_no=request.form.get('pan_no'),
            qualification=request.form.get('qualification'),
            college_name=request.form.get('college_name'),
            course=request.form.get('course'),
            specialization=request.form.get('specialization'),
            internship_duration=int(request.form.get('internship_duration', 3)),
            start_date=start_date,
            end_date=end_date,
            stipend=float(request.form.get('stipend', 0)),
            status=request.form.get('status', 'active'),
            profile_image=profile_image,
            mentor_name=request.form.get('mentor_name'),
            mentor_designation=request.form.get('mentor_designation'),
            company_id=request.form.get('company_id', type=int),
            # ========== ADD BANK DETAILS ==========
            account_holder=request.form.get('account_holder'),
            account_number=request.form.get('account_number'),
            bank_name=request.form.get('bank_name'),
            branch=request.form.get('branch'),
            ifsc_code=request.form.get('ifsc_code')
        )
        
        db.session.add(intern)
        db.session.commit()
        
        flash(f'Intern {intern.full_name} added successfully! (ID: {intern.intern_id})', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding intern: {str(e)}', 'danger')
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
    
    return redirect(url_for('admin_dashboard', tab='members'))

@app.route('/admin/intern/<int:intern_id>')
def view_intern(intern_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    intern = Intern.query.get_or_404(intern_id)
    documents = InternDocument.query.filter_by(intern_id=intern.id).all()
    company = Company.query.get(intern.company_id) if intern.company_id else None
    
    return render_template('view_intern.html', intern=intern, documents=documents, company=company)

@app.route('/admin/delete-intern/<int:intern_id>', methods=['POST'])
def delete_intern(intern_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    intern = Intern.query.get_or_404(intern_id)
    
    try:
        # Delete associated documents first
        InternDocument.query.filter_by(intern_id=intern.id).delete()
        db.session.delete(intern)
        db.session.commit()
        flash(f'Intern {intern.full_name} deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting intern: {str(e)}', 'danger')
    
    return redirect(url_for('admin_dashboard', tab='members'))

@app.route('/admin/generate-intern-document/<int:intern_id>/<doc_type>')
def generate_intern_document(intern_id, doc_type):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    try:
        intern = Intern.query.get_or_404(intern_id)
        
        if not intern.company:
            flash('Company not found for this intern.', 'danger')
            return redirect(url_for('admin_dashboard', tab='document_generator'))
        
        company = intern.company
        
        # Prepare data for template
        name_parts = intern.full_name.split() if intern.full_name else ['']
        first_name = name_parts[0] if name_parts else ''
        
        end_date = intern.end_date
        if not end_date and intern.start_date:
            end_date = intern.start_date + timedelta(days=intern.internship_duration * 30)
        
        # Ensure company_domain has a value
        company_domain = get_company_domain(company)
        if not company_domain:
            company_domain = company.name.lower().replace(' ', '').replace('pvt', '').replace('ltd', '').replace('.', '') + '.com'
        
        # Ensure hr_email has a value
        hr_email = company.hr_email or f"hr@{company_domain}"
        
        # Ensure hr_name has a value
        hr_name = company.hr_name or 'HR Department'
        
        # Ensure hr_designation has a value
        hr_designation = company.hr_designation or 'HR Manager'
        
        # ========== DATE CALCULATIONS ==========
        if intern.start_date:
            if isinstance(intern.start_date, datetime):
                start_date = intern.start_date.date()
            else:
                start_date = intern.start_date
            offer_date = start_date - timedelta(days=5)
            formatted_offer_date = offer_date.strftime('%d %B %Y')
            formatted_joining_date = start_date.strftime('%d %B %Y')
            acceptance_deadline = start_date + timedelta(days=5)
            formatted_acceptance_deadline = acceptance_deadline.strftime('%d %B %Y')
        else:
            formatted_joining_date = 'To be confirmed'
            formatted_offer_date = datetime.now().strftime('%d %B %Y')
            formatted_acceptance_deadline = (datetime.now() + timedelta(days=5)).strftime('%d %B %Y')
        
        data = {
            'timestamp': datetime.now().strftime('%d %B %Y'),
            'full_name': intern.full_name,
            'first_name': first_name,
            'email': intern.email or '',
            'address': intern.address or '',
            'qualification': intern.qualification or '',
            'college_name': intern.college_name or '',
            'course': intern.course or '',
            'specialization': intern.specialization or '',
            'internship_duration': intern.internship_duration or 3,
            'start_date': formatted_joining_date,
            'end_date': end_date.strftime('%d %B %Y') if end_date else 'TBD',
            'stipend': intern.stipend or 0,
            'mentor_name': intern.mentor_name or hr_name,
            'mentor_designation': intern.mentor_designation or hr_designation,
            'hr_name': hr_name,
            'hr_designation': hr_designation,
            'company_name': company.name,
            'company_domain': company_domain,
            'intern_id': intern.intern_id,
            'certificate_no': f"CERT-{intern.intern_id}-{datetime.now().year}",
            'offer_date': formatted_offer_date,
            'acceptance_deadline': formatted_acceptance_deadline,
            'joining_date': formatted_joining_date
        }
        
        # ========== Store in session and redirect to preview ==========
        session['form_data'] = {
            'document_type': doc_type,
            'intern_id': intern.id,
            'company': intern.company_id,
            'member_type': 'intern'
        }
        
        # Store preview data in session
        session['intern_preview_data'] = data
        
        print(f"✅ Intern document preview data stored for: {intern.full_name}")
        print(f"   Document type: {doc_type}")
        print(f"   Redirecting to preview_document...")
        
        # Redirect to preview_document with doc_type
        return redirect(url_for('preview_document', doc_type=doc_type))
        
    except Exception as e:
        print(f"❌ Error in generate_intern_document: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard', tab='document_generator'))

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
        
        # ========== FORMAT ALL DATES PROPERLY ==========
        
        # CRITICAL FIX: Convert all dates to string format properly
        formatted_resignation_date = ""
        formatted_acceptance_date = ""
        formatted_relieving_date = ""
        formatted_resignation_datetime = ""
        
        # 1. Resignation Date - FIXED
        if employee.resignation_date:
            try:
                if isinstance(employee.resignation_date, datetime):
                    resignation_date_obj = employee.resignation_date.date()
                else:
                    resignation_date_obj = employee.resignation_date
                formatted_resignation_date = resignation_date_obj.strftime('%d %B %Y')
                print(f"✅ Formatted resignation_date: '{formatted_resignation_date}'")
            except Exception as e:
                print(f"Error formatting resignation_date: {e}")
                formatted_resignation_date = str(employee.resignation_date)
        else:
            print("⚠️ No resignation_date found in database")
            formatted_resignation_date = ""
        
        # 2. Acceptance Date - FIXED
        if employee.resignation_acceptance_date:
            try:
                if isinstance(employee.resignation_acceptance_date, datetime):
                    acceptance_date_obj = employee.resignation_acceptance_date.date()
                else:
                    acceptance_date_obj = employee.resignation_acceptance_date
                formatted_acceptance_date = acceptance_date_obj.strftime('%d %B %Y')
                print(f"✅ Formatted acceptance_date: '{formatted_acceptance_date}'")
            except Exception as e:
                print(f"Error formatting acceptance_date: {e}")
                formatted_acceptance_date = str(employee.resignation_acceptance_date)
        else:
            print("⚠️ No acceptance_date found in database")
            # Calculate from resignation date if available
            if employee.resignation_date:
                try:
                    if isinstance(employee.resignation_date, datetime):
                        resignation_for_calc = employee.resignation_date.date()
                    else:
                        resignation_for_calc = employee.resignation_date
                    calculated_acceptance = resignation_for_calc + timedelta(days=3)
                    formatted_acceptance_date = calculated_acceptance.strftime('%d %B %Y')
                    print(f"⚠️ Calculated acceptance_date: '{formatted_acceptance_date}'")
                except Exception as e:
                    print(f"Error calculating acceptance_date: {e}")
                    formatted_acceptance_date = datetime.now().strftime('%d %B %Y')
            else:
                formatted_acceptance_date = datetime.now().strftime('%d %B %Y')
        
        # 3. Relieving Date - FIXED
        if employee.relieving_date:
            try:
                if isinstance(employee.relieving_date, datetime):
                    relieving_date_obj = employee.relieving_date.date()
                else:
                    relieving_date_obj = employee.relieving_date
                formatted_relieving_date = relieving_date_obj.strftime('%d %B %Y')
                print(f"✅ Formatted relieving_date: '{formatted_relieving_date}'")
            except Exception as e:
                print(f"Error formatting relieving_date: {e}")
                formatted_relieving_date = str(employee.relieving_date)
        else:
            print("⚠️ No relieving_date found")
            formatted_relieving_date = ""
        
        # 4. Resignation Email DateTime - FIXED
        if employee.resignation_datetime:
            try:
                if isinstance(employee.resignation_datetime, datetime):
                    formatted_resignation_datetime = employee.resignation_datetime.strftime('%d %B %Y at %I:%M %p')
                else:
                    formatted_resignation_datetime = str(employee.resignation_datetime)
                print(f"✅ Formatted resignation_datetime: '{formatted_resignation_datetime}'")
            except Exception as e:
                print(f"Error formatting resignation_datetime: {e}")
                formatted_resignation_datetime = str(employee.resignation_datetime)
        else:
            formatted_resignation_datetime = ""
        
        # 5. Employee Email - Always generate company email for official documents
        name_parts = employee.full_name.split()
        first_name_part = name_parts[0].lower() if name_parts else ''
        last_name_part = name_parts[-1].lower() if len(name_parts) > 1 else ''
        company_domain = get_company_domain(company)
        employee_email = f"{first_name_part}.{last_name_part}@{company_domain}"

        # Store personal email separately if needed for other purposes
        personal_email = employee.email  # Keep this for reference
        
        # 6. First Name
        name_parts = employee.full_name.split()
        first_name = name_parts[0] if name_parts else ''
        
        # 7. HR Email
        hr_email = get_hr_email(company)
        
        # 8. Resignation Email Content
        resignation_email_content = employee.resignation_email_content or ''
        
        # Build data dictionary - MAKE SURE ALL STRINGS ARE PROPERLY SET
        data = {
            'timestamp': formatted_acceptance_date,
            'full_name': employee.full_name,
            'first_name': first_name,
            'employee_email': employee_email,
            'company_name': company.name,
            'company_domain': get_company_domain(company),
            'hr_email': hr_email,
            'relieving_date': formatted_relieving_date,
            'acceptance_date': formatted_acceptance_date,
            'hr_name': company.hr_name or 'HR Department',
            'hr_designation': company.hr_designation or 'HR Manager',
            'resignation_email': resignation_email_content,
            'resignation_email_datetime': formatted_resignation_datetime,
            'resignation_date': formatted_resignation_date  # CRITICAL: This must match template
        }
        
        print("\n" + "="*60)
        print("📦 FINAL DATA DICTIONARY BEING SENT TO TEMPLATE:")
        print("="*60)
        for key, value in data.items():
            print(f"   {key}: '{value}'")
        print("="*60 + "\n")
        
        # Check specifically for empty values
        if not data['acceptance_date']:
            print("⚠️ WARNING: acceptance_date is empty!")
            data['acceptance_date'] = datetime.now().strftime('%d %B %Y')
            print(f"   Set to current date: {data['acceptance_date']}")
        
        if not data['resignation_date']:
            print("⚠️ WARNING: resignation_date is empty!")
            data['resignation_date'] = "Not specified"
        
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
    if not resignation_date_str:
        flash('Resignation date is required.', 'danger')
        return redirect(url_for('view_employee', emp_id=emp_id))

    try:
        # =========================
        # DATE CALCULATIONS
        # =========================
        resignation_date = datetime.strptime(resignation_date_str, '%Y-%m-%d').date()
        employee.resignation_date = resignation_date

        acceptance_date = resignation_date + timedelta(days=3)
        employee.resignation_acceptance_date = acceptance_date

        relieving_date = acceptance_date + timedelta(days=15)
        employee.relieving_date = relieving_date

        employee.status = 'resigned'

        # Resignation email sent 3 days before
        email_sent_date = resignation_date - timedelta(days=3)
        resignation_datetime = datetime.combine(email_sent_date, datetime.min.time())
        resignation_datetime = resignation_datetime.replace(hour=10, minute=0)
        employee.resignation_datetime = resignation_datetime

        # Assign company
        employee.company_id = company.id

        # =========================
        # EMAIL GENERATION (FIXED)
        # =========================
        company_domain = get_company_domain(company)

        if not company_domain:
            raise ValueError("Company domain is missing")

        # Clean name parsing
        name_parts = employee.full_name.strip().split() if employee.full_name else []

        first_name = name_parts[0].lower() if len(name_parts) >= 1 else "employee"
        last_name = name_parts[-1].lower() if len(name_parts) >= 2 else ""

        # Generate company email safely
        if last_name:
            company_email = f"{first_name}.{last_name}@{company_domain}"
        else:
            company_email = f"{first_name}@{company_domain}"

        # Personal email (if exists)
        personal_email = employee.email if employee.email else ""

        # Use personal email in resignation signature (real-world behavior)
        email_for_signature = personal_email if personal_email else company_email

        # =========================
        # EMAIL CONTENT
        # =========================
        formatted_resignation_date = resignation_date.strftime('%d %B %Y')

        resignation_email_content = f"""Dear HR,

I am writing to formally resign from my position as {employee.designation or 'Employee'} at {company.name}, effective from {formatted_resignation_date}.

I have decided to pursue other opportunities and would like to thank you for the support and opportunities provided during my tenure.

I will ensure a smooth handover of my responsibilities before my departure. Please let me know the next steps regarding the notice period and exit formalities.

Thank you for the guidance and support.

Thanks and Regards,
{employee.full_name}
{email_for_signature}"""

        employee.resignation_email_content = resignation_email_content

        # =========================
        # DEBUG (optional but useful)
        # =========================
        print("\n====== DEBUG EMAIL GENERATION ======")
        print("Full Name:", employee.full_name)
        print("Company Domain:", company_domain)
        print("Generated Company Email:", company_email)
        print("Personal Email:", personal_email)
        print("Final Signature Email:", email_for_signature)
        print("===================================\n")

        db.session.commit()

        # Clear session cache
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
        request = Request()
        credentials.refresh(request)
        
        # Save refreshed credentials
        with open(token_path, 'wb') as token:
            pickle.dump(credentials, token)
    elif credentials.expired:
        return None, "Token expired. Please reconnect Google Drive."
    
    service = build('drive', 'v3', credentials=credentials)
    return service, None

def upload_file_to_drive(file_path, filename, folder_name=None, employee=None):
    """Upload file to Google Drive"""
    service, error = get_drive_service()
    if error:
        raise Exception(f"Google Drive not connected: {error}")
    
    try:
        # Use employee details for folder name
        emp_id = employee.employee_id if employee else "unknown"
        emp_name = employee.full_name if employee else "Unknown"
        main_folder_name = f"{emp_id}_{emp_name.replace(' ', '_')}" if employee else "Documents"
        
        # Create or get main employee folder
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
        
        # Create subfolder if specified
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
        
        # Upload file
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

# ==================== GOOGLE DRIVE AUTHENTICATION ROUTES ====================

@app.route('/authorize')
def authorize():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    try:
        # Create flow with redirect URI
        credentials_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'credentials.json')
        
        if not os.path.exists(credentials_path):
            flash('credentials.json file not found. Please add it to the project root.', 'danger')
            return redirect(url_for('admin_dashboard'))
        
        # Create flow
        flow = Flow.from_client_secrets_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/drive.file'],
            redirect_uri=url_for('oauth2callback', _external=True)
        )
        
        # Generate authorization URL
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        
        # Store state in session
        session['oauth_state'] = state
        
        return redirect(authorization_url)
        
    except Exception as e:
        print(f"Authorization error: {e}")
        import traceback
        traceback.print_exc()
        flash(f'Error connecting to Google Drive: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/oauth2callback')
def oauth2callback():
    if 'oauth_state' not in session:
        flash('OAuth session expired. Please try again.', 'danger')
        return redirect(url_for('authorize'))
    
    try:
        # Create flow with the stored state
        credentials_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'credentials.json')
        
        flow = Flow.from_client_secrets_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/drive.file'],
            redirect_uri=url_for('oauth2callback', _external=True),
            state=session['oauth_state']
        )
        
        # Exchange authorization code for credentials
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials
        
        # Save credentials
        token_path = os.path.join(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], 'token.pickle')
        
        # Create token folder if it doesn't exist
        os.makedirs(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], exist_ok=True)
        
        with open(token_path, 'wb') as token:
            pickle.dump(credentials, token)
        
        # Clear session state
        session.pop('oauth_state', None)
        
        flash('✅ Successfully connected to Google Drive!', 'success')
        return redirect(url_for('admin_dashboard'))
        
    except Exception as e:
        print(f"OAuth2 callback error: {e}")
        import traceback
        traceback.print_exc()
        flash(f'Error connecting to Google Drive: {str(e)}', 'danger')
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
    """Make functions available to all templates"""
    
    def check_drive_connection():
        """Check if Google Drive is connected"""
        token_path = os.path.join(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], 'token.pickle')
        return os.path.exists(token_path)
    
    def format_date(date, format='%d %B %Y'):
        """Format date for templates"""
        if not date:
            return ''
        if isinstance(date, str):
            try:
                from datetime import datetime
                date = datetime.strptime(date, '%Y-%m-%d')
            except:
                return date
        return date.strftime(format)
    
    return dict(
        check_drive_connection=check_drive_connection,
        format_date=format_date,
        now=datetime.now()
    )

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
        logo_with_name_file = request.files.get('logo_with_name')  # NEW
        signature_file = request.files.get('signature')
        
        logo_filename = None
        logo_with_name_filename = None
        signature_filename = None

        # Save logo (for watermark)
        if logo_file and logo_file.filename:
            logo_filename = secure_filename(logo_file.filename)
            logo_file.save(os.path.join(app.root_path, 'static', 'images', logo_filename))
        
        # Save logo with name (for header)
        if logo_with_name_file and logo_with_name_file.filename:
            logo_with_name_filename = secure_filename(logo_with_name_file.filename)
            logo_with_name_file.save(os.path.join(app.root_path, 'static', 'images', logo_with_name_filename))
        
        # Save signature
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
            logo_with_name=logo_with_name_filename,  # NEW
            signature=signature_filename,
            hr_name=request.form.get('hr_name'),
            hr_designation=request.form.get('hr_designation'),
            hr_email=request.form.get('hr_email'),
            notice_period=request.form.get('notice_period'),  # NEW
            email_domain=request.form.get('email_domain')     # NEW
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
        company.notice_period = request.form.get('notice_period')
        company.email_domain = request.form.get('email_domain')

        # Handle logo upload (for watermark)
        logo_file = request.files.get('logo')
        if logo_file and logo_file.filename:
            logo_filename = secure_filename(logo_file.filename)
            logo_file.save(os.path.join(app.root_path, 'static', 'images', logo_filename))
            company.logo = logo_filename

        # Handle logo with name upload (NEW)
        logo_with_name_file = request.files.get('logo_with_name')
        if logo_with_name_file and logo_with_name_file.filename:
            logo_with_name_filename = secure_filename(logo_with_name_file.filename)
            logo_with_name_file.save(os.path.join(app.root_path, 'static', 'images', logo_with_name_filename))
            company.logo_with_name = logo_with_name_filename

        # Handle signature upload
        signature_file = request.files.get('signature')
        if signature_file and signature_file.filename:
            signature_filename = secure_filename(signature_file.filename)
            signature_file.save(os.path.join(app.root_path, 'static', 'images', 'signatures', signature_filename))
            company.signature = signature_filename

        db.session.commit()
        flash('Company updated successfully', 'success')
        return redirect(url_for('admin_companies'))
    
    return render_template('company_form.html', company=company)

@app.route('/admin/companies/<int:company_id>/delete', methods=['POST'])
def delete_company(company_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    company = Company.query.get_or_404(company_id)
    
    try:
        # Optionally delete associated image files
        if company.logo:
            logo_path = os.path.join(app.root_path, 'static', 'images', company.logo)
            if os.path.exists(logo_path):
                os.remove(logo_path)
        
        if company.logo_with_name:
            logo_with_name_path = os.path.join(app.root_path, 'static', 'images', company.logo_with_name)
            if os.path.exists(logo_with_name_path):
                os.remove(logo_with_name_path)
        
        if company.signature:
            signature_path = os.path.join(app.root_path, 'static', 'images', 'signatures', company.signature)
            if os.path.exists(signature_path):
                os.remove(signature_path)
        
        db.session.delete(company)
        db.session.commit()
        flash('Company deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting company: {str(e)}', 'danger')
    
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
        # Get company_id from form
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

        elif doc_type in ['offer_letter', 'experience_letter', 'increment_letter', 'relieving_letter']:
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
        session['member_type'] = 'employee'
        
        # Redirect to preview
        return redirect(url_for('preview'))

    # GET request – show company selection form
    return render_template('select_company.html',
                           employee=employee,
                           doc_type=doc_type,
                           companies=companies)

@app.route('/download-document/<int:doc_id>')
def download_document(doc_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    # First try to find document in employee documents
    document = Document.query.get(doc_id)
    doc_type = 'employee'
    
    # If not found, try intern documents
    if not document:
        document = InternDocument.query.get(doc_id)
        doc_type = 'intern'
    
    if not document:
        flash('Document not found', 'danger')
        return redirect(request.referrer or url_for('admin_dashboard'))
    
    # ========== METHOD 1: CHECK LOCAL FILE ==========
    # Try multiple possible local paths
    local_paths = [
        document.file_path,
        os.path.join(app.config['UPLOAD_FOLDER'], document.filename),
        os.path.join(app.root_path, 'uploads', document.filename),
    ]
    
    for path in local_paths:
        if path and os.path.exists(path):
            try:
                return send_file(
                    path,
                    as_attachment=True,
                    download_name=document.filename,
                    mimetype='application/pdf'
                )
            except Exception as e:
                print(f"Error sending local file {path}: {e}")
                continue
    
    # ========== METHOD 2: DOWNLOAD FROM GOOGLE DRIVE ==========
    if document.drive_file_id:
        try:
            service, error = get_drive_service()
            if error:
                flash('Failed to connect to Google Drive', 'danger')
                return redirect(request.referrer or url_for('admin_dashboard'))
            
            # Download file from Drive
            drive_request = service.files().get_media(fileId=document.drive_file_id)
            file_data = io.BytesIO()
            downloader = MediaIoBaseDownload(file_data, drive_request)
            
            done = False
            while not done:
                status, done = downloader.next_chunk()
            
            file_data.seek(0)
            
            # Save a local copy for future downloads
            try:
                local_path = os.path.join(app.config['UPLOAD_FOLDER'], document.filename)
                with open(local_path, 'wb') as f:
                    f.write(file_data.getvalue())
                # Update database with local path
                file_data.seek(0)
                document.file_path = local_path
                db.session.commit()
                print(f"✅ Saved local copy: {local_path}")
            except Exception as e:
                print(f"Could not save local copy: {e}")
                file_data.seek(0)
            
            # Send file as download
            response = make_response(file_data.read())
            response.headers['Content-Type'] = 'application/pdf'
            response.headers['Content-Disposition'] = f'attachment; filename={document.filename}'
            return response
            
        except Exception as e:
            print(f"Error downloading from Drive: {e}")
            flash(f'Error downloading from Drive: {str(e)}', 'danger')
            return redirect(request.referrer or url_for('admin_dashboard'))
    
    # ========== FILE NOT FOUND ANYWHERE ==========
    flash(f'File not found: {document.filename}. The file may have been deleted.', 'danger')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/delete-document/<int:doc_id>', methods=['POST'])
def delete_document(doc_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    try:
        # Try to find document in employee documents
        document = Document.query.get(doc_id)
        doc_type = 'employee'
        
        # If not found, try intern documents
        if not document:
            document = InternDocument.query.get(doc_id)
            doc_type = 'intern'
        
        if not document:
            flash('Document not found', 'danger')
            return redirect(request.referrer or url_for('admin_dashboard'))
        
        # Store document info for flash message and redirect
        doc_filename = document.filename
        doc_type_name = document.document_type.replace('_', ' ').title()
        
        # Get employee/intern ID for redirect
        if doc_type == 'employee':
            employee_id = document.employee_id
            redirect_url = url_for('view_employee', emp_id=employee_id)
        else:
            intern_id = document.intern_id
            redirect_url = url_for('view_intern', intern_id=intern_id)
        
        # ========== HANDLE INCREMENT LETTER DELETION ==========
        # Check if this is an increment letter
        if document.document_type == 'increment_letter' and doc_type == 'employee':
            # Get the employee
            employee = Employee.query.get(employee_id)
            
            if employee:
                # Find the increment history record for this employee
                # Get the most recent increment history (assuming the document corresponds to the latest)
                increment_history = IncrementHistory.query.filter_by(
                    employee_id=employee.id
                ).order_by(IncrementHistory.generated_at.desc()).first()
                
                if increment_history:
                    # Log the revert operation
                    print(f"\n{'='*60}")
                    print(f"🔄 REVERTING INCREMENT FOR: {employee.full_name}")
                    print(f"{'='*60}")
                    print(f"   Current CTC: ₹{employee.ctc:,.2f}")
                    print(f"   Reverting to old CTC: ₹{increment_history.old_ctc:,.2f}")
                    print(f"   Monthly increment being removed: ₹{increment_history.increment_amount:,.2f}")
                    print(f"   Annual increment being removed: ₹{increment_history.increment_amount * 12:,.2f}")
                    print(f"   Document: {doc_filename}")
                    print(f"   Generated on: {document.generated_at.strftime('%d %B %Y %H:%M')}")
                    
                    # Update employee's base_ctc back to old value
                    # Since CTC is calculated as base_ctc + (total_increment * 12)
                    # Removing this increment means setting base_ctc to old_ctc
                    employee.base_ctc = increment_history.old_ctc
                    
                    # Delete the increment history record
                    db.session.delete(increment_history)
                    print(f"   ✅ Increment history record deleted")
                    print(f"   ✅ Employee CTC reverted to: ₹{employee.ctc:,.2f}")
                    print(f"{'='*60}\n")
                    
                    # Add a note about the deletion in flash message
                    flash(f'⚠️ Increment letter deleted. CTC has been reverted to ₹{increment_history.old_ctc:,.2f}', 'warning')
                else:
                    print(f"⚠️ No increment history found for employee {employee.full_name}")
        
        # ========== DELETE FROM GOOGLE DRIVE ==========
        if document.drive_file_id:
            try:
                # Delete the file from Drive
                delete_drive_file(document.drive_file_id)
                print(f"✅ Deleted from Drive: {document.drive_file_id}")
                
                # Check if parent folder is empty and delete it
                parent_id = get_parent_folder_id(document.drive_file_id)
                if parent_id and is_folder_empty(parent_id):
                    delete_drive_folder(parent_id)
                    print(f"✅ Deleted empty parent folder: {parent_id}")
                    
            except Exception as e:
                print(f"Drive deletion error (continuing anyway): {e}")
        
        # ========== DELETE LOCAL FILE ==========
        if document.file_path and os.path.exists(document.file_path):
            try:
                os.remove(document.file_path)
                print(f"✅ Deleted local file: {document.file_path}")
            except Exception as e:
                print(f"Error deleting local file: {e}")
        
        # ========== DELETE DATABASE RECORD ==========
        db.session.delete(document)
        db.session.commit()
        
        flash(f'✅ {doc_type_name} document "{doc_filename}" deleted successfully!', 'success')
        
        return redirect(redirect_url)
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting document: {str(e)}', 'danger')
        print(f"Error deleting document: {str(e)}")
        import traceback
        traceback.print_exc()
        return redirect(request.referrer or url_for('admin_dashboard'))

# ==================== PAYMENT ROUTES ====================
@app.route('/admin/payments')
def view_payments():
    """View all employee payments with filters"""
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    # Get filter parameters
    status_filter = request.args.get('status', 'all')
    employee_filter = request.args.get('employee_id', type=int)
    month_filter = request.args.get('month', '')
    year_filter = request.args.get('year', datetime.now().year)
    
    # Build query for employee payments - ADD phone number
    query = db.session.query(
        Payment.id,
        Payment.amount,
        Payment.paid_amt,
        Payment.document_type,
        Payment.status,
        Payment.payment_date,
        Payment.due_date,
        Payment.created_at,
        Employee.full_name.label('employee_name'),
        Employee.employee_id.label('employee_id'),
        Employee.email.label('employee_email'),
        Employee.phone.label('employee_phone')  # ← ADD PHONE NUMBER
    ).join(Employee, Payment.employee_id == Employee.id)
    
    # Apply filters
    if status_filter != 'all':
        query = query.filter(Payment.status == status_filter)
    
    if employee_filter:
        query = query.filter(Payment.employee_id == employee_filter)
    
    # Include NULL payment_date in filters
    if month_filter:
        query = query.filter(
            db.or_(
                Payment.payment_date.is_(None),
                db.extract('month', Payment.payment_date) == int(month_filter)
            )
        )
    
    if year_filter:
        query = query.filter(
            db.or_(
                Payment.payment_date.is_(None),
                db.extract('year', Payment.payment_date) == int(year_filter)
            )
        )
    
    results = query.order_by(Payment.created_at.desc()).all()
    
    # Process results
    payments = []
    total_amount = 0
    total_paid = 0
    total_due = 0
    
    for row in results:
        payment_id = row[0]
        amount = row[1]
        paid_amt = row[2]
        document_type = row[3]
        status = row[4]
        payment_date = row[5]
        due_date = row[6]
        created_at = row[7]
        employee_name = row[8]
        employee_id = row[9]
        employee_email = row[10]
        employee_phone = row[11]  # ← GET PHONE NUMBER
        
        due_amount = amount - paid_amt
        total_amount += amount
        total_paid += paid_amt
        total_due += due_amount
        
        # Determine status class for badge
        if paid_amt >= amount:
            status_class = 'success'
        elif paid_amt > 0:
            status_class = 'warning'
        else:
            status_class = 'secondary'
        
        payments.append({
            'id': payment_id,
            'employee_name': employee_name,
            'employee_id': employee_id,
            'employee_email': employee_email,
            'employee_phone': employee_phone or 'N/A',  # ← ADD PHONE NUMBER
            'amount': amount,
            'paid_amt': paid_amt,
            'due_amount': due_amount,
            'status': status,
            'status_class': status_class,
            'payment_date': payment_date.strftime('%d %b %Y') if payment_date else 'Not Paid Yet',
            'due_date': due_date.strftime('%d %b %Y') if due_date else 'N/A',
            'created_at': created_at.strftime('%d %b %Y') if created_at else 'N/A',
        })
    
    # Get all employees for the create payment dropdown
    employees = Employee.query.all()
    
    # Calculate counts for summary cards
    paid_count = sum(1 for p in payments if p['status'] == 'Paid')
    pending_count = sum(1 for p in payments if p['status'] == 'Pending')
    partial_count = sum(1 for p in payments if p['status'] == 'Partial')
    overdue_count = sum(1 for p in payments if p['status'] == 'Overdue')
    
    return render_template('payments.html',
                         payments=payments,
                         employees=employees,
                         total_amount=total_amount,
                         total_paid=total_paid,
                         total_due=total_due,
                         paid_count=paid_count,
                         pending_count=pending_count,
                         partial_count=partial_count,
                         overdue_count=overdue_count,
                         status_filter=status_filter,
                         employee_filter=employee_filter,
                         month_filter=month_filter,
                         year_filter=year_filter,
                         now=datetime.now())

@app.route('/admin/payment/<int:payment_id>')
def view_payment(payment_id):
    """View single payment details"""
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    payment = Payment.query.get_or_404(payment_id)
    employee = Employee.query.get(payment.employee_id)
    
    return render_template('payment_details.html',
                         payment=payment,
                         employee=employee,
                         now=datetime.now())

@app.route('/admin/payment/<int:payment_id>/add-payment', methods=['POST'])
def add_payment(payment_id):
    """Add payment to an existing payment record"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        payment = Payment.query.get_or_404(payment_id)
        
        # Get payment amount from form
        payment_amount = float(request.form.get('payment_amount', 0))
        
        if payment_amount <= 0:
            flash('Payment amount must be greater than zero.', 'danger')
            return redirect(request.referrer)
        
        # Update payment
        payment.paid_amt += payment_amount
        payment.due_amount = payment.amount - payment.paid_amt
        payment.payment_date = datetime.now().date()
        payment.updated_at = datetime.now()
        
        # Update status
        if payment.paid_amt >= payment.amount:
            payment.status = 'Paid'
        elif payment.paid_amt > 0:
            payment.status = 'Partial'
        
        db.session.commit()
        
        flash(f'✅ Payment of ₹{payment_amount:,.2f} added successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding payment: {str(e)}', 'danger')
        print(f"Error: {str(e)}")
    
    return redirect(request.referrer)

@app.route('/admin/payment/<int:payment_id>/mark-paid', methods=['POST'])
def mark_payment_paid(payment_id):
    """Mark entire payment as paid"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        payment = Payment.query.get_or_404(payment_id)
        
        payment.paid_amt = payment.amount
        payment.due_amount = 0
        payment.status = 'Paid'
        payment.payment_date = datetime.now().date()
        payment.updated_at = datetime.now()
        
        db.session.commit()
        
        flash(f'✅ Payment marked as paid successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error marking payment: {str(e)}', 'danger')
    
    return redirect(request.referrer)

@app.route('/admin/payment/<int:payment_id>/update-amount', methods=['POST'])
def update_payment_amount(payment_id):
    """Update total amount of payment"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        payment = Payment.query.get_or_404(payment_id)
        
        new_amount = float(request.form.get('amount', 0))
        
        if new_amount <= 0:
            flash('Amount must be greater than zero.', 'danger')
            return redirect(request.referrer)
        
        old_amount = payment.amount
        payment.amount = new_amount
        payment.due_amount = new_amount - payment.paid_amt
        payment.updated_at = datetime.now()
        
        # Update status
        if payment.paid_amt >= new_amount:
            payment.status = 'Paid'
        elif payment.paid_amt > 0:
            payment.status = 'Partial'
        else:
            payment.status = 'Pending'
        
        db.session.commit()
        
        flash(f'✅ Amount updated from ₹{old_amount:,.2f} to ₹{new_amount:,.2f}', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating amount: {str(e)}', 'danger')
    
    return redirect(request.referrer)

@app.route('/admin/payment/<int:payment_id>/delete', methods=['POST'])
def delete_payment(payment_id):
    """Delete a payment record"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        payment = Payment.query.get_or_404(payment_id)
        db.session.delete(payment)
        db.session.commit()
        
        flash('✅ Payment record deleted successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting payment: {str(e)}', 'danger')
    
    return redirect(url_for('view_payments'))

@app.route('/admin/create-payment', methods=['POST'])
def create_payment():
    """Create a new payment record for employee"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        employee_id = request.form.get('employee_id', type=int)
        amount = float(request.form.get('amount', 0))
        document_type = request.form.get('document_type', '')
        due_date = request.form.get('due_date')
        status = request.form.get('status', 'Pending')
        notes = request.form.get('notes', '')
        
        if not employee_id or amount <= 0:
            flash('Please provide valid employee and amount.', 'danger')
            return redirect(request.referrer)
        
        employee = Employee.query.get(employee_id)
        if not employee:
            flash('Employee not found.', 'danger')
            return redirect(request.referrer)
        
        # Create payment - payment_date can be NULL initially
        payment = Payment(
            employee_id=employee_id,
            amount=amount,
            paid_amt=0,
            due_amount=amount,
            document_type=document_type,
            status=status,
            due_date=datetime.strptime(due_date, '%Y-%m-%d').date() if due_date else None,
            notes=notes,
            created_at=datetime.now(),
            updated_at=datetime.now()
            # payment_date is NOT set here - will be NULL until first payment is made
        )
        
        db.session.add(payment)
        db.session.commit()
        
        flash(f'✅ Payment record created for {employee.full_name} - ₹{amount:,.2f}', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating payment: {str(e)}', 'danger')
    
    return redirect(request.referrer or url_for('view_payments'))

@app.route('/admin/process-payment/<int:payment_id>', methods=['POST'])
def process_payment(payment_id):
    """Process payment and mark as paid (AJAX endpoint for dashboard)"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        payment = Payment.query.get_or_404(payment_id)
        
        # Mark as fully paid
        payment.paid_amt = payment.amount
        payment.due_amount = 0
        payment.status = 'Paid'
        payment.payment_date = datetime.now().date()  # Changed from paid_date to payment_date
        payment.updated_at = datetime.now()
        
        db.session.commit()
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Error processing payment: {e}")
        return jsonify({'error': str(e)}), 500
 
 #update employee information and bank details

@app.route('/admin/employee/<int:emp_id>/update', methods=['POST'])
def update_employee(emp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    employee = Employee.query.get_or_404(emp_id)
    
    try:
        # Update personal information
        employee.full_name = request.form.get('full_name')
        employee.email = request.form.get('email')
        employee.phone = request.form.get('phone')
        employee.gender = request.form.get('gender')
        employee.designation = request.form.get('designation')
        employee.department = request.form.get('department')
        employee.address = request.form.get('address')
        employee.aadhar_no = request.form.get('aadhar_no')
        employee.pan_no = request.form.get('pan_no')
        
        # Update dates
        if request.form.get('joining_date'):
            employee.joining_date = datetime.strptime(request.form.get('joining_date'), '%Y-%m-%d').date()
        if request.form.get('resignation_date'):
            employee.resignation_date = datetime.strptime(request.form.get('resignation_date'), '%Y-%m-%d').date()
        
        db.session.commit()
        flash('Employee details updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating employee: {str(e)}', 'danger')
        print(f"Error: {str(e)}")
    
    return redirect(url_for('view_employee', emp_id=emp_id))

@app.route('/admin/employee/<int:emp_id>/update-bank', methods=['POST'])
def update_employee_bank(emp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    employee = Employee.query.get_or_404(emp_id)
    
    try:
        # Update bank details
        employee.account_holder = request.form.get('account_holder')
        employee.account_number = request.form.get('account_number')
        employee.bank_name = request.form.get('bank_name')
        employee.branch = request.form.get('branch')
        employee.ifsc_code = request.form.get('ifsc_code')
        
        db.session.commit()
        flash('Bank details updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating bank details: {str(e)}', 'danger')
    
    return redirect(url_for('view_employee', emp_id=emp_id))

#update intern
@app.route('/admin/intern/<int:intern_id>/update', methods=['POST'])
def update_intern(intern_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    intern = Intern.query.get_or_404(intern_id)
    
    try:
        # Update personal information
        intern.full_name = request.form.get('full_name')
        intern.email = request.form.get('email')
        intern.phone = request.form.get('phone')
        intern.gender = request.form.get('gender')
        intern.address = request.form.get('address')
        
        # Update internship details
        intern.qualification = request.form.get('qualification')
        intern.college_name = request.form.get('college_name')
        intern.course = request.form.get('course')
        intern.specialization = request.form.get('specialization')
        intern.internship_duration = int(request.form.get('internship_duration')) if request.form.get('internship_duration') else 3
        intern.stipend = float(request.form.get('stipend')) if request.form.get('stipend') else 0
        intern.status = request.form.get('status')
        
        # Update mentor details
        intern.mentor_name = request.form.get('mentor_name')
        intern.mentor_designation = request.form.get('mentor_designation')
        
        # Update dates
        if request.form.get('start_date'):
            intern.start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date()
        if request.form.get('end_date'):
            intern.end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%d').date()
        
        # Update company
        if request.form.get('company_id'):
            intern.company_id = int(request.form.get('company_id'))
        
        db.session.commit()
        flash('Intern details updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating intern: {str(e)}', 'danger')
        print(f"Error: {str(e)}")
    
    return redirect(url_for('view_intern', intern_id=intern_id))

@app.route('/admin/intern/<int:intern_id>/update-bank', methods=['POST'])
def update_intern_bank(intern_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    intern = Intern.query.get_or_404(intern_id)
    
    try:
        intern.account_holder = request.form.get('account_holder')
        intern.account_number = request.form.get('account_number')
        intern.bank_name = request.form.get('bank_name')
        intern.ifsc_code = request.form.get('ifsc_code')
        
        db.session.commit()
        flash('Bank details updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating bank details: {str(e)}', 'danger')
    
    return redirect(url_for('view_intern', intern_id=intern_id))

# ========== APP INITIALIZATION ==========
if __name__ == '__main__':
    with app.app_context():
        try:
            # Create all tables
            print("🔄 Creating database tables...")
            db.create_all()
            print("✅ Database tables created successfully!")
            
            # Create default admin if none exists
            if Admin.query.first() is None:
                default_admin = Admin(username='admin')
                default_admin.set_password('admin123')
                db.session.add(default_admin)
                db.session.commit()
                print("✅ Default admin created: username='admin', password='admin123'")
            
            # Seed companies if none exist
            try:
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
                            logo_filename = comp.get('logo')
                        
                        # Build company with all possible fields
                        company = Company(
                            name=comp['name'],
                            address=comp.get('address'),
                            phone=comp.get('phone'),
                            email=comp.get('email'),
                            website=comp.get('website'),
                            logo=logo_filename,
                            signature=comp.get('signature'),
                            hr_name=comp.get('hr_name'),
                            hr_designation=comp.get('hr_designation'),
                            hr_email=comp.get('hr_email'),
                            notice_period=comp.get('notice_period', '30'),
                            email_domain=comp.get('email_domain', ''),
                            accepts_interns=comp.get('accepts_interns', True)
                        )
                        db.session.add(company)
                    db.session.commit()
                    print("✅ Static companies imported successfully")
                else:
                    print("ℹ️ Companies already exist, skipping seed")
            except Exception as e:
                print(f"⚠️ Could not seed companies: {e}")
                db.session.rollback()
                
        except Exception as e:
            print(f"❌ Database initialization error: {e}")
            import traceback
            traceback.print_exc()
    
    # Run the app
    app.run(debug=True)