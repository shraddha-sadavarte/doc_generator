from flask import Flask, render_template, request, redirect, url_for, session, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from xhtml2pdf import pisa
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import io
import zipfile

from config import COMPANIES

app = Flask(__name__)
app.secret_key = "super-secret-key"

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///documents.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = "generated_docs"

db = SQLAlchemy(app)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)  # Auto Increment
    full_name = db.Column(db.String(100))
    aadhar_no = db.Column(db.String(20))
    designation = db.Column(db.String(100))
    ctc = db.Column(db.Float)

def html_to_pdf(html_content, output_path):
    with open(output_path, "w+b") as pdf_file:
        result = pisa.CreatePDF(html_content, dest=pdf_file)
    return not result.err

@app.template_filter('humanize')
def humanize_filter(value):
    try:
        num = float(value)
        return intword(num)
    except (ValueError, TypeError):
        return str(value)

@app.context_processor
def inject_now():
    return {'now': datetime.now()}

def get_previous_workday(target_date, days_before):
    count = 0
    while count < days_before:
        target_date -= timedelta(days=1)
        if target_date.weekday() < 5:
            count += 1
    return target_date

def convert_dates(form_data):
    date_fields = ['joining_date', 'resignation_date']
    for field in date_fields:
        if field in form_data and form_data[field]:
            try:
                form_data[field] = datetime.strptime(form_data[field], '%Y-%m-%d')
            except (ValueError, TypeError):
                form_data[field] = None
    return form_data

def generate_pdf_file(form_data, company, doc_type):
    template = f"templates/documents/{doc_type}.html"
    html_content = render_template(template.replace('templates/', ''), data=form_data, company=company)
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

        #check if employee already exists in DB based on full name and aadhar number
        existing_employee = Employee.query.filter_by(full_name=full_name, aadhar_no=aadhar_no).first()
        if existing_employee:
            #use existing employee ID for document generation
            employee = existing_employee
        else:
            #create new employee record in DB
            employee = Employee(
                full_name=full_name,
                aadhar_no=aadhar_no,
                designation=request.form.get('designation'),
                ctc=float(request.form.get('ctc', '0'))
            )
            db.session.add(employee)
            db.session.commit()

        # Generate employee ID from DB auto increment ID
        employee_id = f"EMP{employee.id:04d}"

        # Prepare form data
        form_data = {
            'employee_id': employee_id,
            'company': request.form.get('company'),
            'document_type': request.form.get('document_type'),
            'full_name': request.form.get('full_name'),
            'address': request.form.get('address'),
            'aadhar_no': request.form.get('aadhar_no'),
            'joining_date': request.form.get('joining_date'),
            'resignation_date': request.form.get('resignation_date'),
            'designation': request.form.get('designation'),
            'ctc': request.form.get('ctc', '0'),
            'bank_details': {
                'account_holder': request.form.get('account_holder'),
                'account_number': request.form.get('account_number'),
                'bank_name': request.form.get('bank_name'),
                'branch': request.form.get('branch'),
                'ifsc_code': request.form.get('ifsc_code')
            },
            'pan_no': request.form.get('pan_no'),
            'salary_breakdown': {
                'basic': request.form.get('basic', '0'),
                'hra': request.form.get('hra', '0'),
                'da': request.form.get('da', '0'),
                'conveyance': request.form.get('conveyance', '0'),
                'medical': request.form.get('medical', '0'),
                'special_allowance': request.form.get('special_allowance', '0'),
                'pf': request.form.get('pf', '0'),
                'professional_tax': request.form.get('professional_tax', '0')
            }
        }

        selected_months = request.form.getlist('months')
        selected_year = request.form.get('year')

        session['selected_months'] = selected_months
        session['selected_year'] = selected_year
        session['form_data'] = form_data
        session['documents_to_process'] = [form_data['document_type']]

        if form_data['document_type'] == 'offer_and_salary':
            session['documents_to_process'] = ['offer_letter']
            session['current_doc_index'] = 0
            return redirect(url_for('preview_document', doc_type='offer_letter'))

        return redirect(url_for('preview'))

    return render_template('index.html', companies=COMPANIES)

@app.route('/preview')
def preview():
    form_data = session.get('form_data', {})
    
    selected_months = session.get('selected_months', [])
    if not form_data:
        return redirect(url_for('index'))

    form_data = convert_dates(form_data)

    if form_data.get('joining_date'):
        date_before = get_previous_workday(form_data['joining_date'], 8)
        form_data['date_before'] = date_before

    company = next((c for c in COMPANIES if c['id'] == form_data['company']), None)
    if not company:
        return "Company not found", 404

    # Compute monthly components
    ctc = float(form_data.get('ctc', 0))
    basic = round(ctc * 0.5 / 12)
    hra = round(basic * 0.5)
    conveyance = round(ctc * 0.05 / 12)
    medical = round(ctc * 0.014 / 12)
    telephone = round(ctc * 0.02 / 12)
    monthly_ctc = round(ctc / 12)
    special_allowance = monthly_ctc - (basic + hra + conveyance + medical + telephone)
    professional_tax = 200
    gross_salary = basic + hra + conveyance + medical + telephone + special_allowance
    total_deductions = professional_tax
    net_salary = gross_salary - total_deductions

    form_data['salary_breakdown'] = {
        'basic': basic,
        'hra': hra,
        'conveyance': conveyance,
        'medical': medical,
        'telephone': telephone,
        'special_allowance': special_allowance,
        'professional_tax': professional_tax,
        'gross_salary': gross_salary,
        'net_salary': net_salary
    }

    # Build a month label for preview when applicable (use first selected month)
    month_label = []
    if form_data.get('document_type') in ['salary_slip', 'offer_and_salary'] and selected_months:
        current_year = session.get('selected_year', datetime.now().year)
        for m in selected_months:
            m = m.strip()
            m = m[:1].upper() + m[1:].lower()
            month_label.append(f"{m} {current_year}")

    if form_data.get('document_type') == 'offer_and_salary':
        return render_template(
            'documents/offer_letter.html',
            data=form_data,
            company=company,
            months=selected_months,
            month=month_label,
            lc_logo="your_watermark_logo.png"
        )

    template = f"documents/{form_data['document_type']}.html"
    return render_template(
        template,
        data=form_data,
        company=company,
        months=selected_months,
        month=month_label,
        lc_logo="your_watermark_logo.png"
    )

@app.route('/preview_document/<doc_type>')
def preview_document(doc_type):
    form_data = session.get('form_data', {})
    
    selected_months = session.get('selected_months', [])
    if not form_data:
        return redirect(url_for('index'))

    form_data = convert_dates(form_data)

    if form_data.get('joining_date'):
        date_before = get_previous_workday(form_data['joining_date'], 8)
        form_data['date_before'] = date_before

    company = next((c for c in COMPANIES if c['id'] == form_data['company']), None)
    if not company:
        return "Company not found", 404

    # Compute monthly components
    ctc = float(form_data.get('ctc', 0))
    basic = round(ctc * 0.5 / 12)
    hra = round(basic * 0.5)
    conveyance = round(ctc * 0.05 / 12)
    medical = round(ctc * 0.014 / 12)
    telephone = round(ctc * 0.02 / 12)
    monthly_ctc = round(ctc / 12)
    special_allowance = monthly_ctc - (basic + hra + conveyance + medical + telephone)
    professional_tax = 2500
    gross_salary = basic + hra + conveyance + medical + telephone + special_allowance
    total_deductions = professional_tax
    net_salary = gross_salary - total_deductions

    form_data['salary_breakdown'] = {
        'basic': basic,
        'hra': hra,
        'conveyance': conveyance,
        'medical': medical,
        'telephone': telephone,
        'special_allowance': special_allowance,
        'professional_tax': professional_tax,
        'gross_salary': gross_salary,
        'net_salary': net_salary
    }

    # month label for preview route
    month_label = None
    if doc_type in ['salary_slip'] and selected_months:
        m = selected_months[0].strip()
        m = m[:1].upper() + m[1:].lower()
        current_year = datetime.now().year
        month_label = f"{m} {current_year}"

    if form_data.get('document_type') == 'offer_and_salary' and doc_type == 'offer_letter':
        return render_template(
            'documents/offer_letter.html',
            data=form_data,
            company=company,
            months=selected_months,
            month=month_label,
            lc_logo="your_watermark_logo.png"
        )

    template = f"documents/{doc_type}.html"
    return render_template(
        template,
        data=form_data,
        company=company,
        months=selected_months,
        month=month_label,
        lc_logo="your_watermark_logo.png"
    )

@app.route('/generate', methods=['POST'])
def generate():

    form_data = session.get('form_data')
    selected_months = session.get('selected_months', [])

    if not form_data:
        return redirect(url_for('index'))

    employee_id = secure_filename(form_data['employee_id'])
    base_folder = os.path.join(app.config['UPLOAD_FOLDER'], "employee_documents")
    employee_folder = os.path.join(base_folder, employee_id)

    os.makedirs(employee_folder, exist_ok=True)
    doc_type = form_data['document_type']

    # 🔹 SALARY SLIP (MULTIPLE MONTHS)
    if doc_type == "salary_slip" and selected_months:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
            for month in selected_months:
                form_data_copy = form_data.copy()
                form_data_copy['month'] = month
                html = render_template(
                    "documents/salary_slip.html",
                    data=form_data_copy
                )

                filename = f"Salary_Slip_{month}.pdf"
                filepath = os.path.join(employee_folder, filename)
                html_to_pdf(html, filepath)
                zip_file.write(filepath, filename)

        zip_buffer.seek(0)
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f"{employee_id}_Salary_Slips.zip",
            mimetype="application/zip"
        )
    # 🔹 OTHER DOCUMENTS
    else:
        company = next((c for c in COMPANIES if c['id'] == form_data['company']), None) 
        html = render_template(
            f"documents/{doc_type}.html",
            data=form_data
        )

        filename = f"{doc_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        filepath = os.path.join(employee_folder, filename)

        html_to_pdf(html, filepath)

        return send_from_directory(employee_folder, filename, as_attachment=True)

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

if __name__ == '__main__':
    app.run(debug=True)
