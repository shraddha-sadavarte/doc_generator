import html
import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
from flask import Flask, flash, render_template, request, redirect, url_for, session, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from xhtml2pdf import pisa
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import io
import zipfile
import subprocess
import tempfile

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
from flask import send_file

app = Flask(__name__)
app.secret_key = "super-secret-key"

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///documents.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, "generated_docs")
# Google Drive Configuration
app.config['GOOGLE_DRIVE_TOKEN_FOLDER'] = os.path.join(app.root_path, "tokens")
os.makedirs(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], exist_ok=True)

CLIENT_SECRETS_FILE = "credentials.json"  # Download this from Google Cloud Console
SCOPES = ['https://www.googleapis.com/auth/drive.file']
REDIRECT_URI = 'http://localhost:5000/oauth2callback'
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
    amount = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default='pending')  # pending, paid, overdue
    due_date = db.Column(db.Date, nullable=True)
    paid_date = db.Column(db.Date, nullable=True)
    payment_method = db.Column(db.String(50), nullable=True)
    transaction_id = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    employee = db.relationship('Employee', backref='payments')
    document = db.relationship('Document', backref='payment')

def html_to_pdf(html_content, output_path):
    weasyprint_path = os.path.join(app.root_path, 'weasyprint', 'weasyprint.exe')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        temp_html_path = f.name

    try:
        result = subprocess.run(
            [weasyprint_path, temp_html_path, output_path],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print("WeasyPrint error:", e)
        return False
    finally:
        if os.path.exists(temp_html_path):
            os.unlink(temp_html_path)

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
    """Return watermark logo filename based on company ID"""
    # Debug print to see what company_id is being passed
    print(f"Company ID received: {company_id}")
    
    # Map your actual company IDs from config.py to logo filenames
    watermarks = {
        'company1': 'lc_logo.png',      # Map company1 to lc_logo.png
        'company2': 'arr_logo.png',     # Map company2 to arr_logo.png
    }
    
    watermark = watermarks.get(company_id, 'lc_logo.png')  # Default to lc_logo.png
    print(f"Watermark logo selected: {watermark}")
    return watermark

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

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':

        full_name = request.form.get('full_name')
        aadhar_no = request.form.get('aadhar_no')

        existing_employee = Employee.query.filter_by(
            full_name=full_name,
            aadhar_no=aadhar_no
        ).first()

        if existing_employee:
            employee = existing_employee
            # No increment_per_month to update
        else:
            employee = Employee(
                employee_id="",  # Temporary, will set after flush
                full_name=full_name,
                aadhar_no=aadhar_no,
                designation=request.form.get('designation'),
                base_ctc=float(request.form.get('ctc') or 0)
            )
            db.session.add(employee)
            db.session.flush()  # Get the employee ID before commit

            employee.employee_id = f"LC{100 + employee.id}"
            db.session.commit()
    
        # Store dates as strings initially
        form_data = {
            'employee_id': employee.employee_id,
            'company': request.form.get('company'),
            'document_type': request.form.get('document_type'),
            'full_name': full_name,
            'address': request.form.get('address'),
            'aadhar_no': aadhar_no,
            'joining_date': request.form.get('joining_date'),  # Keep as string
            'resignation_date': request.form.get('resignation_date'),  # Keep as string
            'designation': request.form.get('designation'),
            'ctc': request.form.get('ctc') or 0,
            # increment_per_month removed
            'bank_details': {
                'account_holder': request.form.get('account_holder'),
                'account_number': request.form.get('account_number'),
                'bank_name': request.form.get('bank_name'),
                'branch': request.form.get('branch'),
                'ifsc_code': request.form.get('ifsc_code')
            },
            'pan_no': request.form.get('pan_no')
        }

        selected_months = request.form.getlist('months')
        selected_year = request.form.get('year')

        session['selected_months'] = selected_months
        session['selected_year'] = selected_year
        session['form_data'] = form_data

        return redirect(url_for('preview'))

    return render_template('index.html', companies=COMPANIES)

@app.route('/preview')
def preview():
    form_data = session.get('form_data', {})

     # Debug print
    print("=" * 50)
    print("PREVIEW ROUTE - form_data keys:", form_data.keys())
    print("document_type:", form_data.get('document_type'))
    print("=" * 50)
    
    # Set defaults if missing
    if 'document_type' not in form_data:
        flash('Document type is missing!', 'danger')
        return redirect(url_for('admin_dashboard'))

    if 'company' not in form_data or not form_data['company']:
        form_data['company'] = 'company1'  # Default to company1 if not provided
    
    selected_months = session.get('selected_months', [])
    if not form_data:
        return redirect(url_for('index'))

    # Convert string dates to date objects for calculations
    form_data = convert_dates(form_data)

    # Calculate date_before if joining_date exists
    if form_data.get('joining_date'):
        date_before = get_previous_workday(form_data['joining_date'], 8)
        form_data['date_before'] = date_before

    company = next((c for c in COMPANIES if c['id'] == form_data['company']), None)
    if not company:
        return "Company not found", 404

    ctc = float(form_data.get('ctc') or 0)
    # increment_per_month no longer used in salary breakdown for preview? 
    # For increment letter, we will have a separate amount in form_data
    increment_per_month = float(form_data.get('increment_per_month', 0))

    monthly_ctc = round(ctc / 12)
    monthly_ctc_after_increment = monthly_ctc + increment_per_month

    basic = round(monthly_ctc_after_increment * 0.5)
    hra = round(basic * 0.5)
    conveyance = round(monthly_ctc_after_increment * 0.05)
    medical = round(monthly_ctc_after_increment * 0.014)
    telephone = round(monthly_ctc_after_increment * 0.02)

    special_allowance = monthly_ctc_after_increment - (
        basic + hra + conveyance + medical + telephone
    )

    professional_tax = 200
    gross_salary = basic + hra + conveyance + medical + telephone + special_allowance
    net_salary = gross_salary - professional_tax

    form_data['salary_breakdown'] = {
        'basic': basic,
        'hra': hra,
        'conveyance': conveyance,
        'medical': medical,
        'telephone': telephone,
        'special_allowance': special_allowance,
        'professional_tax': professional_tax,
        'gross_salary': gross_salary,
        'net_salary': net_salary,
        'increment_per_month': increment_per_month
    }

    form_data['monthly_ctc_after_increment'] = monthly_ctc_after_increment

    # Format dates for display using the safe format_date function
    form_data['formatted_joining_date'] = format_date(form_data.get('joining_date'))
    
    resignation_date = form_data.get('resignation_date')
    if resignation_date:
        form_data['formatted_resignation_date'] = format_date(resignation_date)
        # Calculate relieving date (30 days after resignation)
        if isinstance(resignation_date, str):
            relieving_date = datetime.strptime(resignation_date, "%Y-%m-%d").date() + timedelta(days=30)
        else:
            relieving_date = resignation_date + timedelta(days=30)
        form_data['relieving_date'] = format_date(relieving_date)
    else:
        form_data['formatted_resignation_date'] = None
        form_data['relieving_date'] = None

    # Build month label for preview when applicable
    month_label = []
    if form_data.get('document_type') in ['salary_slip', 'offer_and_salary'] and selected_months:
        current_year = session.get('selected_year', datetime.now().year)
        for m in selected_months:
            m = m.strip()
            m = m[:1].upper() + m[1:].lower()
            month_label.append(f"{m} {current_year}")

    # Determine watermark logo based on company
    watermark_logo = get_watermark_logo(company['id'])

    if form_data.get('document_type') == 'offer_and_salary':
        return render_template(
            'documents/offer_letter.html',
            data=form_data,
            company=company,
            months=selected_months,
            month=month_label,
            watermark_logo=watermark_logo
        )

    template = f"documents/{form_data['document_type']}.html"
    return render_template(
        template,
        data=form_data,
        company=company,
        months=selected_months,
        month=month_label,
        watermark_logo=watermark_logo
    )

@app.route('/preview_document/<doc_type>')
def preview_document(doc_type):
    form_data = session.get('form_data', {})
    
    selected_months = session.get('selected_months', [])
    if not form_data:
        return redirect(url_for('index'))

    # Convert string dates to date objects for calculations
    form_data = convert_dates(form_data)

    # Calculate date_before if joining_date exists
    if form_data.get('joining_date'):
        date_before = get_previous_workday(form_data['joining_date'], 8)
        form_data['date_before'] = date_before

    company = next((c for c in COMPANIES if c['id'] == form_data['company']), None)
    if not company:
        return "Company not found", 404

    ctc = float(form_data.get('ctc') or 0)
    increment_per_month = float(form_data.get('increment_per_month') or 0)

    monthly_ctc = round(ctc / 12)
    monthly_ctc_after_increment = monthly_ctc + increment_per_month

    basic = round(monthly_ctc_after_increment * 0.5)
    hra = round(basic * 0.5)
    conveyance = round(monthly_ctc_after_increment * 0.05)
    medical = round(monthly_ctc_after_increment * 0.014)
    telephone = round(monthly_ctc_after_increment * 0.02)

    special_allowance = monthly_ctc_after_increment - (
        basic + hra + conveyance + medical + telephone
    )

    professional_tax = 200
    gross_salary = basic + hra + conveyance + medical + telephone + special_allowance
    net_salary = gross_salary - professional_tax

    form_data['salary_breakdown'] = {
        'basic': basic,
        'hra': hra,
        'conveyance': conveyance,
        'medical': medical,
        'telephone': telephone,
        'special_allowance': special_allowance,
        'professional_tax': professional_tax,
        'gross_salary': gross_salary,
        'net_salary': net_salary,
        'increment_per_month': increment_per_month
    }

    form_data['monthly_ctc_after_increment'] = monthly_ctc_after_increment

    # Format dates for display using the safe format_date function
    form_data['formatted_joining_date'] = format_date(form_data.get('joining_date'))
    
    resignation_date = form_data.get('resignation_date')
    if resignation_date:
        form_data['formatted_resignation_date'] = format_date(resignation_date)
        # Calculate relieving date (30 days after resignation)
        if isinstance(resignation_date, str):
            relieving_date = datetime.strptime(resignation_date, "%Y-%m-%d").date() + timedelta(days=30)
        else:
            relieving_date = resignation_date + timedelta(days=30)
        form_data['relieving_date'] = format_date(relieving_date)
    else:
        form_data['formatted_resignation_date'] = None
        form_data['relieving_date'] = None

    # month label for preview route
    month_label = None
    if doc_type in ['salary_slip'] and selected_months:
        m = selected_months[0].strip()
        m = m[:1].upper() + m[1:].lower()
        current_year = datetime.now().year
        month_label = f"{m} {current_year}"

    # Determine watermark logo based on company
    watermark_logo = get_watermark_logo(company['id'])

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

    # ✅ Safe upload flag (NO name conflict)
    upload_to_drive_flag = request.form.get('upload_to_drive') == 'true'

    doc_type = form_data.get('document_type')

    print("=" * 50)
    print("GENERATE ROUTE STARTED")
    print(f"Document type: {doc_type}")
    print(f"Upload to Drive: {upload_to_drive_flag}")
    print("=" * 50)

    form_data = convert_dates(form_data)

    # -------------------------
    # FIND EMPLOYEE
    # -------------------------
    employee = None
    if 'employee_id' in form_data:
        employee = Employee.query.filter_by(
            employee_id=form_data.get('employee_id')
        ).first()

    employee_id = secure_filename(form_data.get('employee_id', 'unknown'))
    base_folder = os.path.join(app.config['UPLOAD_FOLDER'], "employee_documents")
    employee_folder = os.path.join(base_folder, employee_id)
    os.makedirs(employee_folder, exist_ok=True)

    # -------------------------
    # SALARY CALCULATION
    # -------------------------
    ctc = float(form_data.get('ctc') or 0)
    increment_per_month = float(form_data.get('increment_per_month') or 0)

    monthly_ctc = round(ctc / 12)
    monthly_ctc_after_increment = monthly_ctc + increment_per_month

    basic = round(monthly_ctc_after_increment * 0.5)
    hra = round(basic * 0.5)
    conveyance = round(monthly_ctc_after_increment * 0.05)
    medical = round(monthly_ctc_after_increment * 0.014)
    telephone = round(monthly_ctc_after_increment * 0.02)

    special_allowance = monthly_ctc_after_increment - (
        basic + hra + conveyance + medical + telephone
    )

    professional_tax = 200

    gross_salary = basic + hra + conveyance + medical + telephone + special_allowance
    net_salary = gross_salary - professional_tax

    form_data['salary_breakdown'] = {
        'basic': basic,
        'hra': hra,
        'conveyance': conveyance,
        'medical': medical,
        'telephone': telephone,
        'special_allowance': special_allowance,
        'professional_tax': professional_tax,
        'gross_salary': gross_salary,
        'net_salary': net_salary,
        'increment_per_month': increment_per_month
    }

    form_data['net_salary'] = net_salary
    form_data['monthly_ctc_after_increment'] = monthly_ctc_after_increment

    # -------------------------
    # DATE FORMATTING
    # -------------------------
    form_data['formatted_joining_date'] = format_date(
        form_data.get('joining_date')
    )

    resignation_date = form_data.get('resignation_date')

    if resignation_date:
        form_data['formatted_resignation_date'] = format_date(resignation_date)

        if isinstance(resignation_date, str):
            relieving_date = datetime.strptime(
                resignation_date, "%Y-%m-%d"
            ).date() + timedelta(days=30)
        else:
            relieving_date = resignation_date + timedelta(days=30)

        form_data['relieving_date'] = format_date(relieving_date)
    else:
        form_data['formatted_resignation_date'] = None
        form_data['relieving_date'] = None

    company = next(
        (c for c in COMPANIES if c['id'] == form_data.get('company')),
        None
    )

    watermark_logo = get_watermark_logo(company['id']) if company else 'lc_logo.png'

    # -------------------------
    # CHECK PENDING INCREMENT
    # -------------------------
    should_update_increment = False
    pending = None

    if doc_type == 'increment_letter' and 'pending_increment' in session:
        should_update_increment = True
        pending = session['pending_increment']

    # ======================================================
    # ✅ SALARY SLIP (MULTIPLE MONTHS) – UPLOAD ONLY, NO ZIP
    # ======================================================
    if doc_type == "salary_slip" and selected_months:

        uploaded_files = []
        files_generated = False

        for month in selected_months:
            form_data_copy = form_data.copy()
            form_data_copy['month'] = month

            html = render_template(
                "documents/salary_slip.html",
                data=form_data_copy,
                company=company,
                watermark_logo=watermark_logo,
                months = selected_months,
            )

            filename = f"Salary_Slip_{month}.pdf"
            filepath = os.path.join(employee_folder, filename)

            if html_to_pdf(html, filepath):
                files_generated = True

                if employee:
                    doc = Document(
                        employee_id=employee.id,
                        document_type=doc_type,
                        filename=filename,
                        file_path=filepath,
                        month=month,
                        year=session.get('selected_year', datetime.now().year),
                        generated_by=session.get('admin_username', 'system')
                    )
                    db.session.add(doc)
                    db.session.flush()

                    # ✅ Upload to Drive
                    if upload_to_drive_flag and employee:
                        try:
                            drive_file_id = upload_file_to_drive(
                                file_path=filepath,
                                filename=filename,
                                folder_name=f"Salary Slips/{month}",
                                employee=employee
                            )
                            doc.drive_file_id = drive_file_id
                            uploaded_files.append(filename)
                        except Exception as e:
                            print("Drive Upload Error:", e)
                            flash(f'{filename} upload failed', 'warning')

        if files_generated:
            db.session.commit()

            # Clear session data
            session.pop('form_data', None)
            session.pop('selected_months', None)
            session.pop('selected_year', None)

            if upload_to_drive_flag and uploaded_files:
                flash(f'{len(uploaded_files)} salary slips uploaded to Drive!', 'success')
            else:
                flash('Salary slips generated successfully!', 'success')

            return redirect(url_for('admin_dashboard'))   # <-- No file download, just redirect

        flash('Failed to generate salary slips', 'danger')
        return redirect(url_for('admin_dashboard'))

    # ======================================================
    # ✅ OTHER DOCUMENTS
    # ======================================================

    html = render_template(
        f"documents/{doc_type}.html",
        data=form_data,
        company=company,
        watermark_logo=watermark_logo
    )

    filename = f"{doc_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    filepath = os.path.join(employee_folder, filename)

    if not html_to_pdf(html, filepath):
        flash('Failed to generate PDF', 'danger')
        return redirect(url_for('admin_dashboard'))

    # -------------------------
    # UPDATE INCREMENT
    # -------------------------
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
                effective_date=datetime.strptime(
                    pending['effective_date'], '%Y-%m-%d'
                ).date() if pending['effective_date'] else None,
                generated_by=session.get('admin_username', 'system')
            )

            db.session.add(history)
            session.pop('pending_increment', None)

        except Exception as e:
            print("Increment Update Error:", e)
            db.session.rollback()

    # -------------------------
    # SAVE DOCUMENT RECORD
    # -------------------------
    if employee:
        doc = Document(
            employee_id=employee.id,
            document_type=doc_type,
            filename=filename,
            file_path=filepath,
            generated_by=session.get('admin_username', 'system')
        )
        db.session.add(doc)
        db.session.flush()

        # ✅ SAFE DRIVE UPLOAD (updated to pass employee object)
        if upload_to_drive_flag and employee:
            try:
                folder_map = {
                    'offer_letter': 'Offer Letters',
                    'experience_letter': 'Experience Letters',
                    'increment_letter': 'Increment Letters',
                    'relieving_letter': 'Relieving Letters'
                }

                folder_name = folder_map.get(doc_type, 'Other Documents')

                drive_file_id = upload_file_to_drive(
                    file_path=filepath,
                    filename=filename,
                    folder_name=folder_name,
                    employee=employee   # pass the employee object
                )

                doc.drive_file_id = drive_file_id
                flash('Document uploaded to Drive successfully!', 'success')

            except Exception as e:
                print("Drive Upload Error:", e)
                flash('Drive upload failed', 'warning')

    db.session.commit()

    session.pop('form_data', None)
    session.pop('selected_months', None)
    session.pop('selected_year', None)

    flash(f'{doc_type.replace("_", " ").title()} generated successfully!', 'success')

    # Use send_file with absolute path
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True, download_name=filename, mimetype='application/pdf')
    else:
        flash('File not found!', 'danger')
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

# Admin Dashboard with Employee Cards
@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    # Get tab from query parameters
    active_tab = request.args.get('tab', 'dashboard')
    selected_emp_id = request.args.get('emp_id', type=int)
    
    # Get all employees with their document counts
    employees = Employee.query.order_by(Employee.created_at.desc()).all()
    
    # Get document counts for each employee
    employee_data = []
    total_documents = 0
    for emp in employees:
        doc_count = Document.query.filter_by(employee_id=emp.id).count()
        total_documents += doc_count
        # Get latest increment amount for display (optional)
        latest_inc = IncrementHistory.query.filter_by(employee_id=emp.id).order_by(IncrementHistory.generated_at.desc()).first()
        increment_amount = latest_inc.increment_amount if latest_inc else 0
        employee_data.append({
            'employee': emp,
            'document_count': doc_count,
            'increment_amount': increment_amount
        })
    
    # Calculate statistics
    total_employees = len(employees)
    active_employees = sum(1 for emp in employees if emp.status == 'active')
    
    # 🔴 FIXED: Payment calculations using actual database queries
    pending_payments = Payment.query.filter_by(status='pending').count()
    paid_count = Payment.query.filter_by(status='paid').count()
    pending_count = Payment.query.filter_by(status='pending').count()
    overdue_count = Payment.query.filter_by(status='overdue').count()

    paid_amount = db.session.query(db.func.sum(Payment.amount)).filter_by(status='paid').scalar() or 0
    pending_amount = db.session.query(db.func.sum(Payment.amount)).filter_by(status='pending').scalar() or 0
    overdue_amount = db.session.query(db.func.sum(Payment.amount)).filter_by(status='overdue').scalar() or 0

    # 🔴 FIXED: Get all payments for the table - Fixed the join ambiguity
    payments_result = db.session.query(
        Payment.id,
        Employee.full_name.label('employee_name'),
        Employee.employee_id,
        Document.document_type,
        Payment.amount,
        Payment.status,
        Payment.due_date,
        Payment.paid_date
    ).select_from(Payment).join(Employee).outerjoin(
        Document, Payment.document_id == Document.id
    ).all()
    
    # Format payments for template
    payments = []
    for p in payments_result:
        status_class = 'paid' if p.status == 'paid' else 'pending' if p.status == 'pending' else 'overdue'
        payments.append({
            'id': p.id,
            'employee_name': p.employee_name,
            'employee_id': p.employee_id,
            'document_type': p.document_type or 'N/A',
            'amount': p.amount,
            'status': p.status.title() if p.status else 'Pending',
            'status_class': status_class,
            'due_date': p.due_date.strftime('%d %b %Y') if p.due_date else 'N/A',
            'paid_date': p.paid_date.strftime('%d %b %Y') if p.paid_date else 'N/A'
        })
    
    return render_template('admin_dashboard.html', 
                         employees=employee_data,
                         active_tab=active_tab,
                         selected_emp_id=selected_emp_id,
                         now=datetime.now(),
                         total_employees=total_employees,
                         active_employees=active_employees,
                         total_documents=total_documents,
                         pending_payments=pending_payments,
                         paid_count=paid_count,
                         pending_count=pending_count,
                         overdue_count=overdue_count,
                         paid_amount=paid_amount,
                         pending_amount=pending_amount,
                         overdue_amount=overdue_amount,
                         payments=payments)

# Setup first admin (run once)
@app.route('/admin/setup')
def setup_admin():
    # Check if any admin exists
    if Admin.query.first() is None:
        admin = Admin(username='admin')
        admin.set_password('admin123')  # Change this in production!
        db.session.add(admin)
        db.session.commit()
        return "Admin created! Username: admin, Password: admin123"
    return "Admin already exists"

@app.route('/admin/employee/<int:emp_id>/delete', methods=['POST'])
def delete_employee(emp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    employee = Employee.query.get_or_404(emp_id)
    
    try:
        # Delete related documents first
        Document.query.filter_by(employee_id=emp_id).delete()
        
        # Delete the employee
        db.session.delete(employee)
        db.session.commit()
        
        flash(f'Employee {employee.full_name} deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting employee: {str(e)}', 'danger')
    
    return redirect(url_for('admin_dashboard', tab='employees'))

#generate document for specific employee
@app.route('/admin/employee/<int:emp_id>/generate/<doc_type>', methods=['GET', 'POST'])
def admin_generate_document(emp_id, doc_type):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    employee = Employee.query.get_or_404(emp_id)

    # Special handling for increment letter
    if doc_type == 'increment_letter':
        if request.method == 'POST':
            # Get company_id from form
            company_id = request.form.get('company', 'company1')
            increment_amount = float(request.form.get('increment_amount', 0))
            effective_date = request.form.get('effective_date')
            
            if increment_amount <= 0:
                flash('Increment amount must be greater than zero.', 'danger')
                return render_template('increment_form.html', employee=employee, companies=COMPANIES, now=datetime.now)
            
            # Store increment data in session (NOT in database yet)
            session['pending_increment'] = {
                'amount': increment_amount,
                'effective_date': effective_date,
                'employee_id': employee.id,
                'old_ctc': employee.ctc   # store old CTC instead of old increment
            }

            # 🔴 ADD SALARY BREAKDOWN CALCULATIONS HERE
            ctc = float(employee.ctc)
            monthly_ctc = round(ctc / 12)
            monthly_ctc_after_increment = monthly_ctc + increment_amount

            basic = round(monthly_ctc_after_increment * 0.5)
            hra = round(basic * 0.5)
            conveyance = round(monthly_ctc_after_increment * 0.05)
            medical = round(monthly_ctc_after_increment * 0.014)
            telephone = round(monthly_ctc_after_increment * 0.02)

            special_allowance = monthly_ctc_after_increment - (
                basic + hra + conveyance + medical + telephone
            )

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

            # Prepare form data with increment amount and salary breakdown
            form_data = {
                'employee_id': employee.employee_id,
                'company': company_id,
                'document_type': doc_type,
                'full_name': employee.full_name,
                'address': employee.address,
                'aadhar_no': employee.aadhar_no,
                'pan_no': employee.pan_no,
                'designation': employee.designation,
                'base_ctc': employee.base_ctc,
                'ctc': employee.ctc,                      # ADD current CTC for display
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
        
        # GET request - show increment form
        return render_template('increment_form.html', employee=employee, companies=COMPANIES, now=datetime.now)

    # Handle POST requests for other document types
    if request.method == 'POST':
        company_id = request.form.get('company', 'company1')

        # Handle months selection for salary slip
        if doc_type == 'salary_slip':
            selected_months = request.form.getlist('months')
            if not selected_months:
                flash('Please select at least one month.', 'danger')
                return render_template('select_months.html', employee=employee, companies=COMPANIES)
            session['selected_months'] = selected_months
            session['selected_year'] = request.form.get('year', datetime.now().year)

        # 🔴 ADD SALARY BREAKDOWN FOR OTHER DOCUMENTS – use computed ctc
        ctc = float(employee.ctc)                         # FIXED: use ctc property, not base_ctc
        monthly_ctc = round(ctc / 12)
        monthly_ctc_after_increment = monthly_ctc          # no increment

        basic = round(monthly_ctc_after_increment * 0.5)
        hra = round(basic * 0.5)
        conveyance = round(monthly_ctc_after_increment * 0.05)
        medical = round(monthly_ctc_after_increment * 0.014)
        telephone = round(monthly_ctc_after_increment * 0.02)

        special_allowance = monthly_ctc_after_increment - (
            basic + hra + conveyance + medical + telephone
        )

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
            'increment_per_month': 0
        }

        # Prepare form data from employee records
        form_data = {
            'employee_id': employee.employee_id,
            'company': company_id,
            'document_type': doc_type,
            'full_name': employee.full_name,
            'address': employee.address,
            'aadhar_no': employee.aadhar_no,
            'pan_no': employee.pan_no,
            'designation': employee.designation,
            'base_ctc': employee.base_ctc,
            'ctc': employee.ctc,                           # include current CTC
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
        session['form_data'] = form_data
        return redirect(url_for('preview'))
    
    # GET request - show options for salary slip
    if doc_type == 'salary_slip':
        return render_template('select_months.html', employee=employee, companies=COMPANIES)
    
    # For other documents (GET request), directly generate with default company
    ctc = float(employee.ctc)                               # FIXED: use ctc property
    monthly_ctc = round(ctc / 12)
    monthly_ctc_after_increment = monthly_ctc  # no increment

    basic = round(monthly_ctc_after_increment * 0.5)
    hra = round(basic * 0.5)
    conveyance = round(monthly_ctc_after_increment * 0.05)
    medical = round(monthly_ctc_after_increment * 0.014)
    telephone = round(monthly_ctc_after_increment * 0.02)

    special_allowance = monthly_ctc_after_increment - (
        basic + hra + conveyance + medical + telephone
    )

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
        'increment_per_month': 0
    }

    form_data = {
        'employee_id': employee.employee_id,
        'company': 'company1',
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
    session['form_data'] = form_data
    return redirect(url_for('preview'))

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

# Serve employee documents
@app.route('/employee_docs/<emp_folder>/<doc_type>/<filename>')
def serve_employee_document(emp_folder, doc_type, filename):
    if not session.get('is_admin'):
        return "Unauthorized", 403
    
    # Construct the path to the document
    # emp_folder format: EMP0001_John_Doe
    base_storage = os.path.join(app.root_path, 'generated_docs', 'employee_documents')
    
    # Try both possible structures
    possible_paths = [
        os.path.join(base_storage, emp_folder, doc_type, filename),  # With subfolder
        os.path.join(base_storage, emp_folder, filename)  # Without subfolder
    ]
    
    for folder_path in possible_paths:
        if os.path.exists(folder_path):
            directory = os.path.dirname(folder_path)
            return send_from_directory(directory, filename)
    
    # If file not found, try to find it in the database
    document = Document.query.filter_by(filename=filename).first()
    if document and os.path.exists(document.file_path):
        directory = os.path.dirname(document.file_path)
        return send_from_directory(directory, filename)
    
    return "File not found", 404

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
        employee.employee_id = f"LC{100+employee.id}"
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

# Add route to process payment
@app.route('/admin/process-payment/<int:payment_id>', methods=['POST'])
def process_payment(payment_id):
    if not session.get('is_admin'):
        return "Unauthorized", 403
    
    payment = Payment.query.get_or_404(payment_id)
    
    try:
        payment.status = 'paid'
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
        print(f"🔍 Step 1: main_folder_name = {main_folder_name}")

        print("🔍 Step 2: Checking if employee folder exists...")
        response = service.files().list(
            q=f"name='{main_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        folders = response.get('files', [])
        if folders:
            parent_folder_id = folders[0]['id']
            print(f"   Existing folder ID: {parent_folder_id}")
        else:
            print("   Creating new employee folder...")
            file_metadata = {
                'name': main_folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = service.files().create(body=file_metadata, fields='id').execute()
            parent_folder_id = folder.get('id')
            print(f"   New folder ID: {parent_folder_id}")
            # Save folder ID to employee record
            if employee:
                employee.drive_folder_id = parent_folder_id
                db.session.commit()
                print("   Saved folder ID to employee record.")

        if folder_name:
            print(f"🔍 Step 3: Checking if subfolder '{folder_name}' exists...")
            response = service.files().list(
                q=f"name='{folder_name}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='files(id, name)'
            ).execute()
            subfolders = response.get('files', [])
            if subfolders:
                target_folder_id = subfolders[0]['id']
                print(f"   Existing subfolder ID: {target_folder_id}")
            else:
                print("   Creating new subfolder...")
                file_metadata = {
                    'name': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [parent_folder_id]
                }
                subfolder = service.files().create(body=file_metadata, fields='id').execute()
                target_folder_id = subfolder.get('id')
                print(f"   New subfolder ID: {target_folder_id}")
        else:
            target_folder_id = parent_folder_id

        print(f"🔍 Step 4: Uploading file to folder {target_folder_id}...")
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
        print(f"✅ Upload successful! File ID: {file_id}")
        return file_id

    except Exception as e:
        import traceback
        print("❌ Exception in upload_file_to_drive:")
        traceback.print_exc()
        raise Exception(f"Drive upload failed: {str(e)}")
# ==================== GOOGLE DRIVE AUTHENTICATION ROUTES ====================

@app.route('/authorize')
def authorize():
    """Start OAuth flow for Google Drive"""
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    
    # Check if credentials.json exists
    if not os.path.exists(CLIENT_SECRETS_FILE):
        flash('Google Drive credentials file not found. Please add credentials.json to your project.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Create flow instance
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    
    # Generate authorization URL
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    # Store state in session for callback
    session['oauth_state'] = state
    
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    """Handle OAuth callback from Google"""
    # Verify state
    if 'oauth_state' not in session:
        flash('OAuth session expired. Please try again.', 'danger')
        return redirect(url_for('authorize'))
    
    # Create flow instance
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=session['oauth_state'],
        redirect_uri=REDIRECT_URI
    )
    
    # Fetch token
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    
    # Save credentials for future use
    token_path = os.path.join(app.config['GOOGLE_DRIVE_TOKEN_FOLDER'], 'token.pickle')
    with open(token_path, 'wb') as token:
        pickle.dump(credentials, token)
    
    # Clear session state
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)