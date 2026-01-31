"""
Flask Web Application - Resume Generator API and UI
"""
import os
import uuid
import json
import threading
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from crew.resume_crew import ResumeCrew
from utils.pdf_parser import extract_text_from_file
from utils.exporter import export_resume, export_to_markdown, export_to_docx, export_to_pdf

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

# Configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'output')
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'txt'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Ensure output folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# In-memory job storage (use Redis/DB in production)
jobs = {}

# Initialize ThreadPoolExecutor
# M4 Air (16GB): 8 workers is a safe balance for memory vs speed.
# With 2 Gunicorn workers, total concurrency = 16 jobs.
# Extra jobs will queue in memory automatically.
executor = ThreadPoolExecutor(max_workers=8)


def allowed_file(filename):
    """Check if file extension is allowed."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS



@app.route('/sitemap.xml')
def sitemap():
    """Generate sitemap.xml dynamically."""
    base_url = "https://humanizeresumeai.com"
    curr_date = datetime.now().strftime("%Y-%m-%d")
    
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base_url}/</loc>
    <lastmod>{curr_date}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return app.response_class(xml, mimetype='application/xml')


@app.route('/robots.txt')
def robots():
    """Serve robots.txt."""
    txt = """User-agent: *
Allow: /
Sitemap: https://humanizeresumeai.com/sitemap.xml
"""
    return app.response_class(txt, mimetype='text/plain')


@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')



def process_resume_job(job_id, resume_text, job_description, instructions):
    """
    Background worker function for resume generation.
    Run this in a separate thread/executor.
    """
    print(f"🧵 Starting background job {job_id}")
    try:
        # Update status to processing (already set, but good to confirm)
        if job_id in jobs:
            jobs[job_id]["status"] = "processing"
            
        # Initialize Crew
        crew = ResumeCrew(max_iterations=2)  # Reduced iterations for speed
        
        # Run generation pipeline
        result = crew.generate_resume(
            original_resume=resume_text,
            job_description=job_description,
            user_instructions=instructions
        )
        
        if job_id not in jobs:
            print(f"❌ Job {job_id} not found immediately after generation")
            return

        # Export to all formats
        export_paths = export_resume(
            result["final_resume"], 
            job_id, 
            app.config['UPLOAD_FOLDER']
        )
        
        # Update job with results
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = result
        jobs[job_id]["export_paths"] = export_paths
        
        print(f"✅ Job {job_id} completed successfully")
        
    except Exception as e:
        print(f"❌ Job {job_id} failed: {e}")
        traceback.print_exc()
        if job_id in jobs:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = str(e)


@app.route('/api/generate', methods=['POST'])
def generate_resume():
    """
    Async API endpoint for resume generation.
    Returns a job_id immediately. Client polls /api/status/<job_id>.
    """
    try:
        # Get resume content (from file or text)
        resume_text = ""
        
        if 'resume_file' in request.files:
            file = request.files['resume_file']
            if file and file.filename and allowed_file(file.filename):
                resume_text = extract_text_from_file(file, file.filename)
        
        if not resume_text and 'resume_text' in request.form:
            resume_text = request.form['resume_text']
        
        if not resume_text:
            return jsonify({
                "error": "No resume content provided. Upload a file or paste resume text."
            }), 400
        
        # Get job description
        job_description = request.form.get('job_description', '')
        if not job_description:
            return jsonify({
                "error": "Job description is required."
            }), 400
            
        # Verify Access Code
        access_code = request.form.get('access_code', '').strip()
        required_code = os.getenv("ACCESS_CODE", "UTKARSH2026")
        
        if access_code != required_code:
            return jsonify({
                "error": "Invalid Access Code. Please check the LinkedIn post for the correct code."
            }), 403
        
        # Get user instructions
        instructions = request.form.get('instructions', '')
        
        # Generate job ID
        job_id = str(uuid.uuid4())[:8]
        
        # Store initial job info
        jobs[job_id] = {
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "resume_text": resume_text[:100] + "...", # Store snippet only to save memory
            "job_description": job_description[:100] + "...",
            "result": None,
            "error": None
        }
        
        # Submit to ThreadPoolExecutor (Managed Queue)
        executor.submit(
            process_resume_job,
            job_id, resume_text, job_description, instructions
        )
        
        # Return immediately with job ID
        return jsonify({
            "job_id": job_id,
            "status": "pending",
            "message": "Resume generation started in background."
        })
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Check the status of a generation job."""
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    
    job = jobs[job_id]
    response = {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job["created_at"]
    }
    
    if job["status"] == "completed" and job["result"]:
        response["ai_score"] = job["result"]["ai_score"]
        response["iterations"] = job["result"]["iterations"]
        response["resume"] = job["result"]["final_resume"]
        response["downloads"] = {
            "markdown": f"/api/download/{job_id}/md",
            "docx": f"/api/download/{job_id}/docx",
            "pdf": f"/api/download/{job_id}/pdf"
        }
    elif job["status"] == "failed":
        response["error"] = job["error"]
    
    return jsonify(response)


@app.route('/api/download/<job_id>/<format>', methods=['GET'])
def download_resume(job_id, format='md'):
    """Download the generated resume in specified format (md, docx, pdf)."""
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    
    job = jobs[job_id]
    if job["status"] != "completed":
        return jsonify({"error": "Resume not ready yet"}), 400
    
    # Map format to file extension and mimetype
    format_map = {
        'md': ('md', 'text/markdown', 'markdown'),
        'markdown': ('md', 'text/markdown', 'markdown'),
        'docx': ('docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx'),
        'pdf': ('pdf', 'application/pdf', 'pdf')
    }
    
    if format.lower() not in format_map:
        return jsonify({"error": f"Invalid format. Use: md, docx, or pdf"}), 400
    
    ext, mimetype, key = format_map[format.lower()]
    output_path = os.path.join(UPLOAD_FOLDER, f"resume_{job_id}.{ext}")
    
    if os.path.exists(output_path):
        return send_file(
            output_path,
            as_attachment=True,
            download_name=f"optimized_resume_{job_id}.{ext}",
            mimetype=mimetype
        )
    else:
        return jsonify({"error": f"Resume file ({ext}) not found"}), 404


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    # Check if API key is configured
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    groq_key = os.getenv("GROQ_API_KEY", "")
    
    api_configured = (
        (deepseek_key and deepseek_key != "your_deepseek_api_key_here") or
        (gemini_key and gemini_key != "your_gemini_api_key_here") or
        (groq_key and groq_key != "your_groq_api_key_here")
    )
    
    return jsonify({
        "status": "healthy",
        "api_configured": api_configured,
        "timestamp": datetime.now().isoformat()
    })


if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 AI Resume Generator - Starting Server")
    print("="*60)
    print("\n📍 Open http://localhost:5000 in your browser")
    print("\n⚠️  Make sure to set your DEEPSEEK_API_KEY in .env file!")
    print("   Get a key at: https://platform.deepseek.com/")
    print("\n" + "="*60 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
