/**
 * AI Resume Generator - Frontend JavaScript
 * Handles file upload, form submission, and UI interactions
 */

// DOM Elements
const uploadZone = document.getElementById('uploadZone');
const resumeFile = document.getElementById('resumeFile');
const fileSelected = document.getElementById('fileSelected');
const fileName = document.getElementById('fileName');
const removeFile = document.getElementById('removeFile');
const resumeText = document.getElementById('resumeText');
const jobDescription = document.getElementById('jobDescription');
const instructions = document.getElementById('instructions');
const generateBtn = document.getElementById('generateBtn');
const progressSection = document.getElementById('progressSection');
const resultSection = document.getElementById('resultSection');
const errorSection = document.getElementById('errorSection');
const loadingProgress = document.getElementById('loadingProgress');
const aiScore = document.getElementById('aiScore');
const iterations = document.getElementById('iterations');
const resumeOutput = document.getElementById('resumeOutput');
const copyBtn = document.getElementById('copyBtn');
const regenerateBtn = document.getElementById('regenerateBtn');
const retryBtn = document.getElementById('retryBtn');
const errorMessage = document.getElementById('errorMessage');

// State
let selectedFile = null;
let currentJobId = null;
let userName = localStorage.getItem('resumeUserName') || '';  // Persist name across sessions

// ================================
// File Upload Handling
// ================================

uploadZone.addEventListener('click', () => {
    resumeFile.click();
});

uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('dragover');
});

uploadZone.addEventListener('dragleave', () => {
    uploadZone.classList.remove('dragover');
});

uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        handleFileSelect(files[0]);
    }
});

resumeFile.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFileSelect(e.target.files[0]);
    }
});

removeFile.addEventListener('click', (e) => {
    e.stopPropagation();
    clearSelectedFile();
});

function handleFileSelect(file) {
    const allowedTypes = ['.pdf', '.docx', '.txt'];
    const extension = '.' + file.name.split('.').pop().toLowerCase();

    if (!allowedTypes.includes(extension)) {
        showToast('Please upload a PDF, DOCX, or TXT file.', 'error');
        return;
    }

    selectedFile = file;
    fileName.textContent = file.name;
    document.querySelector('.upload-content').style.display = 'none';
    fileSelected.style.display = 'flex';

    // Clear text area when file is selected
    resumeText.value = '';
    resumeText.placeholder = 'File selected - text area disabled';
    resumeText.disabled = true;
}

function clearSelectedFile() {
    selectedFile = null;
    resumeFile.value = '';
    document.querySelector('.upload-content').style.display = 'flex';
    fileSelected.style.display = 'none';
    resumeText.disabled = false;
    resumeText.placeholder = 'Paste your current resume text here...';
}

// ================================
// Form Submission
// ================================

generateBtn.addEventListener('click', async () => {
    // Validation
    if (!selectedFile && !resumeText.value.trim()) {
        showToast('Please upload a file or paste your resume text.', 'error');
        return;
    }

    if (!jobDescription.value.trim()) {
        showToast('Please enter the job description.', 'error');
        return;
    }

    // Hide previous results/errors
    resultSection.style.display = 'none';
    errorSection.style.display = 'none';

    // Show progress
    progressSection.style.display = 'block';
    generateBtn.disabled = true;
    generateBtn.querySelector('.btn-text').textContent = 'Generating...';

    // Reset progress UI
    resetProgressSteps();

    try {
        // Run progress simulation and API request in parallel
        const progressPromise = simulateProgress();

        // Build form data
        const formData = new FormData();
        if (selectedFile) {
            formData.append('resume_file', selectedFile);
        } else {
            formData.append('resume_text', resumeText.value);
        }

        formData.append('job_description', jobDescription.value);
        formData.append('instructions', instructions.value);

        // Make API request without waiting for progress to finish first
        const apiPromise = fetch('/api/generate', {
            method: 'POST',
            body: formData
        });

        // Wait for both to complete
        const [_, response] = await Promise.all([progressPromise, apiPromise]);

        // check content type before parsing json
        const contentType = response.headers.get("content-type");
        if (!contentType || !contentType.includes("application/json")) {
            throw new Error("Server response was not JSON. The server might be down or returned an error page.");
        }

        const data = await response.json();

        if (response.ok && data.status === 'completed') {
            // Success
            currentJobId = data.job_id;
            displayResult(data);
        } else {
            // Error
            throw new Error(data.error || 'Failed to generate resume');
        }

    } catch (error) {
        console.error('Generation error:', error);

        // generic error message for users as requested
        let userMessage = "Server down, or process timed out. Please try again later.";

        // If it's a known handled error from the backend, show that instead? 
        // User asked: "only show relevant , like server down"
        // I will stick to the generic one for technical errors, but maybe keep backend validation errors?
        // User said: "on any error" -> "server down, try again later"
        // But if form validation failed, we shouldn't say server down.
        // The form validation happens before this try block (lines 105-112).
        // So any error caught HERE is a server/network/parsing error.

        displayError(userMessage);
    } finally {
        generateBtn.disabled = false;
        generateBtn.querySelector('.btn-text').textContent = 'Generate ATS-Optimized Resume';
    }
});

// ================================
// Progress Animation
// ================================

function resetProgressSteps() {
    const steps = document.querySelectorAll('.step');
    steps.forEach(step => {
        step.classList.remove('active', 'completed');
    });
    loadingProgress.style.width = '0%';
}

async function simulateProgress() {
    const steps = ['step1', 'step2', 'step3', 'step4'];

    for (let i = 0; i < steps.length; i++) {
        const step = document.getElementById(steps[i]);

        // Mark current step as active
        step.classList.add('active');

        // Update progress bar
        loadingProgress.style.width = `${((i + 1) / steps.length) * 100}%`;

        // Wait (the actual API call is happening in parallel)
        await sleep(800);

        // Mark as completed
        step.classList.remove('active');
        step.classList.add('completed');
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ================================
// Result Display
// ================================

function displayResult(data) {
    progressSection.style.display = 'none';
    resultSection.style.display = 'block';

    // Update stats
    aiScore.textContent = `${data.ai_score}%`;
    iterations.textContent = data.iterations;

    // Display resume
    resumeOutput.textContent = data.resume;

    // Scroll to result
    resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

    showToast('Resume generated successfully!', 'success');
}

function displayError(message) {
    progressSection.style.display = 'none';
    errorSection.style.display = 'block';
    errorMessage.textContent = message;

    errorSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ================================
// Result Actions
// ================================

copyBtn.addEventListener('click', async () => {
    try {
        await navigator.clipboard.writeText(resumeOutput.textContent);
        showToast('Resume copied to clipboard!', 'success');
        copyBtn.innerHTML = '<span>✅</span> Copied!';
        setTimeout(() => {
            copyBtn.innerHTML = '<span>📋</span> Copy to Clipboard';
        }, 2000);
    } catch (err) {
        showToast('Failed to copy. Please select and copy manually.', 'error');
    }
});

// Download handlers for each format
function downloadResume(format) {
    if (!currentJobId) {
        showToast('No resume to download.', 'error');
        return;
    }

    // Generate filename with user name
    let filename;
    if (userName) {
        // Format name: "John Doe" -> "John-Doe"
        const formattedName = userName.trim().replace(/\s+/g, '-');
        const randomDigit = Math.floor(Math.random() * 10);
        filename = `${formattedName}_Resume${randomDigit}.${format}`;
    } else {
        filename = `optimized_resume_${currentJobId}.${format}`;
    }

    const link = document.createElement('a');
    link.href = `/api/download/${currentJobId}/${format}`;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    showToast(`Downloading ${format.toUpperCase()} resume!`, 'success');
}

// Attach download handlers (check if elements exist first)
const downloadPdf = document.getElementById('downloadPdf');
const downloadDocx = document.getElementById('downloadDocx');
const downloadMd = document.getElementById('downloadMd');

if (downloadPdf) {
    downloadPdf.addEventListener('click', () => downloadResume('pdf'));
}
if (downloadDocx) {
    downloadDocx.addEventListener('click', () => downloadResume('docx'));
}
if (downloadMd) {
    downloadMd.addEventListener('click', () => downloadResume('md'));
}

regenerateBtn.addEventListener('click', () => {
    resultSection.style.display = 'none';
    generateBtn.click();
});

retryBtn.addEventListener('click', () => {
    errorSection.style.display = 'none';
});

// ================================
// Toast Notifications
// ================================

function showToast(message, type = 'info') {
    // Remove existing toasts
    const existingToast = document.querySelector('.toast');
    if (existingToast) {
        existingToast.remove();
    }

    // Create new toast
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    // Remove after 3 seconds
    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ================================
// Health Check on Load
// ================================

async function checkHealth() {
    try {
        const response = await fetch('/api/health');
        const data = await response.json();

        if (!data.api_configured) {
            showToast('⚠️ API key not configured. Please set GEMINI_API_KEY in .env file.', 'error');
        }
    } catch (error) {
        console.error('Health check failed:', error);
    }
}

// ================================
// Name Modal Handling
// ================================

const nameModal = document.getElementById('nameModal');
const userNameInput = document.getElementById('userName');
const saveNameBtn = document.getElementById('saveNameBtn');

function showNameModal() {
    if (nameModal) {
        nameModal.classList.remove('hidden');
        if (userNameInput) {
            userNameInput.focus();
        }
    }
}

function hideNameModal() {
    if (nameModal) {
        nameModal.classList.add('hidden');
    }
}

function saveName() {
    const name = userNameInput ? userNameInput.value.trim() : '';
    if (name) {
        userName = name;
        localStorage.setItem('resumeUserName', name);
        hideNameModal();
        showToast(`Welcome, ${name.split(' ')[0]}! 👋`, 'success');
    } else {
        showToast('Please enter your name to continue.', 'error');
        if (userNameInput) {
            userNameInput.focus();
        }
    }
}

// Event listeners for modal
if (saveNameBtn) {
    saveNameBtn.addEventListener('click', saveName);
}

if (userNameInput) {
    userNameInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            saveName();
        }
    });
}

// ================================
// Initialize on Page Load
// ================================

document.addEventListener('DOMContentLoaded', () => {
    // Run health check
    checkHealth();

    // Show name modal if name not already saved
    if (!userName) {
        showNameModal();
    } else {
        hideNameModal();
    }
});
