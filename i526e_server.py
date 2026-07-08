#!/usr/bin/env python3
"""
SMS Law — I-526E AutoFill Server
Immigrant Petition by Regional Center Investor
Same architecture as I-485 / G-28 tools: Flask + Claude vision OCR + PIL overlay.
Field IDs extracted directly from the real i-526e.pdf (USCIS Edition 01/20/25).
Run with: python i526e_server.py
Or: gunicorn i526e_server:app
"""

import os, json, threading, time, uuid, base64, logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, send_file, render_template_string

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

VERSION   = "1.0.1"
MODEL     = "claude-sonnet-4-6"   # centralized — change only here on deprecation
BASE_DIR  = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
I526E_PDF = BASE_DIR / "i-526e.pdf"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

jobs = {}

# ── Firm / Attorney static data ───────────────────────────────────────────────

ATTORNEY_PROFILES = {
    "Kevin Qi": {
        "attorney_last_name":  "QI",
        "attorney_first_name": "KEVIN",
        "attorney_org_name":   "SMS LAW FIRM",
        "attorney_phone":      "6193427887",
        "attorney_email":      "INFO@SMSLAWFIRM.US",
        "attorney_bar_number": "284314",
        "attorney_uscis_acct": "051538377214",
    },
    "James Shih": {
        "attorney_last_name":  "SHIH",
        "attorney_first_name": "JAMES",
        "attorney_org_name":   "SMS LAW FIRM",
        "attorney_phone":      "6193427887",
        "attorney_email":      "INFO@SMSLAWFIRM.US",
        "attorney_bar_number": "279789",
        "attorney_uscis_acct": "070074997106",
    },
}

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SMS Law — I-526E AutoFill</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  :root { --cream:#F8F4EE; --dark:#1A1A1A; --red:#C0392B; --red-light:#E74C3C; --border:#D4C9BB; --gray:#7A7169; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--cream); color:var(--dark); font-family:'DM Sans',sans-serif; min-height:100vh; }
  header { background:var(--dark); padding:0 48px; display:flex; align-items:center; justify-content:space-between; height:72px; border-bottom:3px solid var(--red); }
  .logo { font-family:'Playfair Display',serif; color:white; font-size:22px; }
  .logo span { color:var(--red); }
  .badge { background:var(--red); color:white; font-size:11px; font-weight:500; letter-spacing:1.5px; text-transform:uppercase; padding:6px 14px; border-radius:2px; }
  main { max-width:860px; margin:0 auto; padding:64px 32px; }
  .eyebrow { font-size:11px; font-weight:500; letter-spacing:2px; text-transform:uppercase; color:var(--red); margin-bottom:16px; }
  h1 { font-family:'Playfair Display',serif; font-size:42px; line-height:1.15; margin-bottom:16px; }
  .subtitle { font-size:16px; color:var(--gray); line-height:1.6; margin-bottom:52px; max-width:600px; }
  .card { background:white; border:1px solid var(--border); border-radius:4px; padding:48px; margin-bottom:24px; box-shadow:0 2px 12px rgba(0,0,0,0.04); }
  .card-title { font-family:'Playfair Display',serif; font-size:20px; margin-bottom:8px; }
  .card-desc { font-size:14px; color:var(--gray); margin-bottom:32px; line-height:1.5; }
  .drop-zone { border:2px dashed var(--border); border-radius:4px; padding:48px 32px; text-align:center; cursor:pointer; transition:all 0.2s; background:var(--cream); }
  .drop-zone:hover, .drop-zone.drag-over { border-color:var(--red); background:#FDF8F5; }
  .drop-icon { font-size:40px; margin-bottom:16px; display:block; }
  .drop-zone h3 { font-size:16px; font-weight:500; margin-bottom:6px; }
  .drop-zone p { font-size:13px; color:var(--gray); }
  .file-selected { display:none; align-items:center; gap:12px; background:#F0FAF0; border:1px solid #7BC47B; border-radius:4px; padding:14px 18px; margin-top:16px; font-size:14px; }
  .file-selected.show { display:flex; }
  .btn { display:inline-flex; align-items:center; gap:10px; background:var(--red); color:white; border:none; padding:16px 32px; font-family:'DM Sans',sans-serif; font-size:15px; font-weight:500; cursor:pointer; border-radius:2px; transition:background 0.2s; margin-top:28px; width:100%; justify-content:center; }
  .btn:hover { background:var(--red-light); }
  .btn:disabled { background:var(--border); cursor:not-allowed; }
  #progress-section { display:none; }
  .progress-card { background:white; border:1px solid var(--border); border-radius:4px; padding:40px 48px; box-shadow:0 2px 12px rgba(0,0,0,0.04); }
  .step-list { list-style:none; margin-top:28px; }
  .step { display:flex; align-items:center; gap:16px; padding:14px 0; border-bottom:1px solid var(--border); font-size:15px; color:var(--gray); transition:color 0.3s; }
  .step:last-child { border-bottom:none; }
  .step.active { color:var(--dark); font-weight:500; }
  .step.done { color:#2E7D32; }
  .step.error { color:var(--red); }
  .step-dot { width:28px; height:28px; border-radius:50%; background:var(--border); display:flex; align-items:center; justify-content:center; font-size:13px; flex-shrink:0; transition:all 0.3s; }
  .step.active .step-dot { background:var(--dark); color:white; }
  .step.done .step-dot { background:#2E7D32; color:white; }
  .step.error .step-dot { background:var(--red); color:white; }
  .spinner { width:14px; height:14px; border:2px solid rgba(255,255,255,0.3); border-top-color:white; border-radius:50%; animation:spin 0.8s linear infinite; display:inline-block; }
  @keyframes spin { to { transform:rotate(360deg); } }
  #result-section { display:none; }
  .result-card { background:white; border:1px solid var(--border); border-radius:4px; padding:40px 48px; box-shadow:0 2px 12px rgba(0,0,0,0.04); }
  .result-header { display:flex; align-items:center; gap:16px; margin-bottom:28px; }
  .result-icon { width:52px; height:52px; background:#E8F5E9; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:24px; flex-shrink:0; }
  .result-title { font-family:'Playfair Display',serif; font-size:22px; }
  .result-sub { font-size:14px; color:var(--gray); margin-top:4px; }
  .stats { display:grid; grid-template-columns:repeat(2,1fr); gap:16px; margin-bottom:32px; }
  .stat { background:var(--cream); border:1px solid var(--border); border-radius:4px; padding:20px; text-align:center; }
  .stat-num { font-family:'Playfair Display',serif; font-size:32px; color:var(--dark); display:block; }
  .stat-label { font-size:12px; color:var(--gray); margin-top:4px; }
  .download-btn { display:flex; align-items:center; justify-content:center; gap:10px; background:var(--dark); color:white; padding:16px 28px; border-radius:2px; text-decoration:none; font-size:15px; font-weight:500; transition:background 0.2s; margin-bottom:12px; }
  .download-btn:hover { background:#333; }
  .try-again { text-align:center; margin-top:20px; }
  .try-again a { color:var(--red); font-size:14px; cursor:pointer; text-decoration:underline; }
  #review-section { display:none; }
  .review-card { background:white; border:1px solid var(--border); border-radius:4px; padding:40px 48px; box-shadow:0 2px 12px rgba(0,0,0,0.04); }
  .review-grid { display:grid; grid-template-columns:1fr 1fr; gap:0; margin:20px 0; border:1px solid var(--border); border-radius:4px; overflow:hidden; }
  .review-group { grid-column:1/-1; background:var(--dark); color:white; padding:8px 16px; font-size:11px; font-weight:500; letter-spacing:1.5px; text-transform:uppercase; }
  .review-label { padding:10px 16px; font-size:13px; color:var(--gray); border-bottom:1px solid var(--border); border-right:1px solid var(--border); background:var(--cream); display:flex; align-items:center; }
  .review-value { padding:6px 12px; border-bottom:1px solid var(--border); display:flex; align-items:center; }
  .review-input { width:100%; border:1px solid transparent; border-radius:3px; padding:4px 8px; font-size:13px; font-family:'DM Sans',sans-serif; background:transparent; color:var(--dark); transition:all 0.15s; }
  .review-input:hover { border-color:var(--border); background:white; }
  .review-input:focus { border-color:var(--red); background:white; outline:none; }
  .review-select { width:100%; border:1px solid var(--border); border-radius:3px; padding:4px 8px; font-size:13px; font-family:'DM Sans',sans-serif; background:white; color:var(--dark); }
  .review-select:focus { border-color:var(--red); outline:none; }
  .review-textarea { width:100%; border:1px solid var(--border); border-radius:3px; padding:6px 8px; font-size:13px; font-family:'DM Sans',sans-serif; background:white; color:var(--dark); height:70px; resize:vertical; }
  .review-textarea:focus { border-color:var(--red); outline:none; }
  .generate-btn { display:flex; align-items:center; justify-content:center; gap:10px; background:var(--red); color:white; border:none; padding:16px 32px; font-family:'DM Sans',sans-serif; font-size:15px; font-weight:500; cursor:pointer; border-radius:2px; width:100%; transition:background 0.2s; margin-top:8px; }
  .generate-btn:hover { background:var(--red-light); }
  .note { background:#FFF8E1; border:1px solid #FFD54F; border-radius:4px; padding:12px 16px; font-size:13px; color:#5D4037; margin-bottom:20px; }
</style>
</head>
<body>
<header>
  <div class="logo">SMS <span>law</span></div>
  <span class="badge">I-526E AutoFill · v{{ version }}</span>
</header>
<main>
  <!-- Upload -->
  <div id="upload-section">
    <p class="eyebrow">Immigrant Petition by Regional Center Investor</p>
    <h1>I-526E<br>AutoFill</h1>
    <p class="subtitle">Upload the client's biographical intake form. Then upload the project documents from the Regional Center (subscription agreement, wire confirmation, fee waiver, etc.). AI will extract and cross-reference both sources.</p>

    <div class="card">
      <div class="card-title">Step 1 — Biographical Intake Form</div>
      <div class="card-desc">Upload the completed SMS Law intake form (same one used for I-485).</div>
      <div class="drop-zone" id="dropZone">
        <span class="drop-icon">📄</span>
        <h3>Click to browse · PDF only</h3>
        <p>or drag and drop here</p>
      </div>
      <input type="file" id="fileInput" accept=".pdf" style="display:none;">
      <div class="file-selected" id="fileSelected"><span>✅</span><span id="fileName"></span></div>
    </div>

    <div class="card" style="padding:32px 48px;">
      <div class="card-title" style="font-size:16px;">Step 2 — Project Documents</div>
      <div class="card-desc">Upload subscription agreement, wire confirmation, admin fee waiver, declarations, and any other project documents from the Regional Center. AI will extract NCE info, investment amounts, and capital sources from these.</div>
      <div class="drop-zone" id="docsDropZone" style="padding:24px;">
        <span class="drop-icon" style="font-size:28px;">📎</span>
        <h3 style="font-size:14px;">Click to browse</h3>
        <p>PDF, JPG, PNG, DOCX · Multiple files OK</p>
      </div>
      <input type="file" id="docsInput" accept=".pdf,.jpg,.jpeg,.png,.docx" multiple style="display:none;">
      <div id="docsList" style="margin-top:12px;"></div>
    </div>

    <button class="btn" id="submitBtn" onclick="startProcessing()" disabled>
      Extract &amp; Review &#x2192;
    </button>
  </div>

  <!-- Review -->
  <div id="review-section">
    <div class="review-card">
      <p class="eyebrow">Step 3 — Review &amp; Complete</p>
      <h2 style="font-family:'Playfair Display',serif;font-size:26px;margin-bottom:8px;">Review &amp; Complete</h2>
      <p style="font-size:14px;color:var(--gray);margin-bottom:16px;">Personal info and project data were extracted by AI — verify everything. Part 4 receipt numbers must be entered manually from the Regional Center's USCIS approval notices.</p>
      <div class="note">⚠ <strong>Part 4 (Regional Center identifiers)</strong> — the I-956F receipt number, Regional Center receipt number, and NCE Identification Number are NOT in your uploaded documents. Enter these manually from Maruti NCE's USCIS approval notices.</div>
      <div id="review-form"></div>
      <button class="generate-btn" onclick="generatePdf()">Generate Filled I-526E →</button>
    </div>
  </div>

  <!-- Progress -->
  <div id="progress-section">
    <div class="progress-card">
      <p class="eyebrow">Processing</p>
      <div class="card-title" style="font-family:'Playfair Display',serif;font-size:22px;">Generating Form I-526E...</div>
      <ul class="step-list">
        <li class="step" id="step1"><div class="step-dot">1</div>Reading intake form &amp; project documents</li>
        <li class="step" id="step2"><div class="step-dot">2</div>Extracting data with AI</li>
        <li class="step" id="step3"><div class="step-dot">3</div>Filling Form I-526E</li>
        <li class="step" id="step4"><div class="step-dot">4</div>Complete</li>
      </ul>
    </div>
  </div>

  <!-- Result -->
  <div id="result-section">
    <div class="result-card">
      <div class="result-header">
        <div class="result-icon">✅</div>
        <div>
          <div class="result-title">Form I-526E Ready</div>
          <div class="result-sub" id="clientNameDisplay"></div>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><span class="stat-num" id="statFilled">—</span><div class="stat-label">Fields Filled</div></div>
        <div class="stat"><span class="stat-num" id="statFlagged">—</span><div class="stat-label">Need Review</div></div>
      </div>
      <a class="download-btn" id="downloadPdf" href="#" download>⬇ Download Filled I-526E (PDF)</a>
      <div class="try-again"><a onclick="resetForm()">Process another client →</a></div>
    </div>
  </div>
</main>

<script>
let currentJobId = null;
let pollInterval = null;
let allDocs = [];

const fileInput = document.getElementById('fileInput');
const docsInput = document.getElementById('docsInput');
const dropZone = document.getElementById('dropZone');
const docsDropZone = document.getElementById('docsDropZone');

dropZone.addEventListener('click', () => fileInput.click());
docsDropZone.addEventListener('click', () => docsInput.click());
fileInput.addEventListener('change', e => { if (e.target.files[0]) showIntakeFile(e.target.files[0]); });
docsInput.addEventListener('change', e => addDocFiles(e.target.files));

['dragover','dragleave','drop'].forEach(ev => {
  [dropZone, docsDropZone].forEach(el => {
    el.addEventListener(ev, e => {
      e.preventDefault();
      if (ev === 'dragover') { el.classList.add('drag-over'); }
      else { el.classList.remove('drag-over'); }
      if (ev === 'drop') {
        const files = e.dataTransfer.files;
        if (el === dropZone) { if (files[0]) showIntakeFile(files[0]); }
        else { addDocFiles(files); }
      }
    });
  });
});

function showIntakeFile(file) {
  document.getElementById('fileName').textContent = file.name;
  document.getElementById('fileSelected').classList.add('show');
  document.getElementById('submitBtn').disabled = false;
}

function addDocFiles(files) {
  for (const f of files) { if (!allDocs.find(d => d.name === f.name)) allDocs.push(f); }
  renderDocList();
}

function renderDocList() {
  const list = document.getElementById('docsList');
  list.innerHTML = '';
  for (const f of allDocs) {
    const div = document.createElement('div');
    div.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:#f0faf0;border:1px solid #7bc47b;border-radius:4px;margin-bottom:6px;font-size:13px;';
    div.innerHTML = '<span>✅ ' + f.name + '</span><span onclick="removeDoc(this)" data-name="' + f.name + '" style="cursor:pointer;color:#c0392b;font-weight:bold;padding:0 6px;">✕</span>';
    list.appendChild(div);
  }
}

function removeDoc(el) {
  allDocs = allDocs.filter(d => d.name !== el.getAttribute('data-name'));
  renderDocList();
}

async function startProcessing() {
  const file = fileInput.files[0];
  if (!file) return;
  document.getElementById('upload-section').style.display = 'none';
  document.getElementById('progress-section').style.display = 'block';
  setStep(1, 'active');
  const formData = new FormData();
  formData.append('intake', file);
  for (const doc of allDocs) formData.append('docs', doc);
  try {
    const res = await fetch('/process', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.error) { showError(data.error); return; }
    currentJobId = data.job_id;
    pollInterval = setInterval(pollStatus, 2000);
  } catch (err) { showError('Failed to start: ' + err.message); }
}

async function pollStatus() {
  if (!currentJobId) return;
  try {
    const res = await fetch('/status/' + currentJobId);
    const data = await res.json();
    if (data.status === 'review_ready') {
      clearInterval(pollInterval);
      setStep(2, 'done');
      showReview(data.client_data);
      return;
    }
    if (data.step >= 1) setStep(1, data.step === 1 ? 'active' : 'done');
    if (data.step >= 2) setStep(2, data.step === 2 ? 'active' : 'done');
    if (data.step >= 3) setStep(3, data.step === 3 ? 'active' : 'done');
    if (data.step >= 4) setStep(4, data.step === 4 ? 'active' : 'done');
    if (data.status === 'done') { clearInterval(pollInterval); setStep(4, 'done'); showResult(data); }
    else if (data.status === 'error') { clearInterval(pollInterval); showError(data.message); }
  } catch(e) {}
}

const ATTORNEY_PROFILES = {{ attorney_profiles_json | safe }};

const REVIEW_SECTIONS = [
  { label: "Preparing Attorney", fields: [
    { key: "_attorney_select", label: "Quick Select Attorney", type: "attorney_select" },
    { key: "attorney_first_name", label: "First Name" },
    { key: "attorney_last_name", label: "Last Name" },
    { key: "attorney_org_name", label: "Organization" },
    { key: "attorney_phone", label: "Phone" },
    { key: "attorney_email", label: "Email" },
    { key: "attorney_uscis_acct", label: "USCIS Online Account Number" },
  ]},
  { label: "Part 1 — Petition Type", fields: [
    { key: "petition_type", label: "Petition Type", type: "select", options: ["Initial Petition","Amendment to Previously Filed Petition"] },
  ]},
  { label: "Part 2 — Personal Information (AI-extracted)", fields: [
    { key: "last_name", label: "Last Name" },
    { key: "first_name", label: "First Name" },
    { key: "middle_name", label: "Middle Name" },
    { key: "a_number", label: "A-Number (if any)" },
    { key: "uscis_account_number", label: "USCIS Online Account Number (if any)" },
    { key: "ssn", label: "SSN (if any)" },
    { key: "date_of_birth", label: "Date of Birth (MM/DD/YYYY)" },
    { key: "sex", label: "Sex", type: "select", options: ["","Male","Female"] },
    { key: "city_of_birth", label: "City of Birth" },
    { key: "country_of_birth", label: "Country of Birth" },
    { key: "country_of_citizenship_current", label: "Country(ies) of Citizenship (current)" },
    { key: "country_of_citizenship_relinquished", label: "Country(ies) of Citizenship (relinquished)" },
    { key: "country_of_last_residence", label: "Country of Last Foreign Residence" },
    { key: "address_street", label: "Mailing Address — Street" },
    { key: "address_apt", label: "Apt/Ste/Flr" },
    { key: "address_city", label: "City" },
    { key: "address_state", label: "State" },
    { key: "address_zip", label: "ZIP Code" },
    { key: "address_country", label: "Country" },
    { key: "employer_name", label: "Current Employer Name" },
    { key: "employer_job_title", label: "Job Title" },
    { key: "employer_from", label: "Employment From (MM/DD/YYYY)" },
    { key: "date_of_arrival", label: "Date of Arrival in US (MM/DD/YYYY)" },
    { key: "i94_number", label: "I-94 Number" },
    { key: "passport_number", label: "Passport Number" },
    { key: "passport_country", label: "Passport Issuing Country" },
    { key: "passport_expiration", label: "Passport Expiration (MM/DD/YYYY)" },
    { key: "current_nonimmigrant_status", label: "Current Nonimmigrant Status" },
    { key: "status_expiration", label: "Status Expiration (MM/DD/YYYY)" },
    { key: "phone_number", label: "Daytime Phone" },
    { key: "email", label: "Email" },
  ]},
  { label: "Part 3 — Spouse / Children (AI-extracted)", fields: [
    { key: "spouse_last_name", label: "Spouse Last Name" },
    { key: "spouse_first_name", label: "Spouse First Name" },
    { key: "spouse_middle_name", label: "Spouse Middle Name" },
    { key: "spouse_dob", label: "Spouse DOB (MM/DD/YYYY)" },
    { key: "spouse_country_of_birth", label: "Spouse Country of Birth" },
    { key: "spouse_citizenship_current", label: "Spouse Citizenship (current)" },
    { key: "spouse_applying_aos_yn", label: "Spouse Applying for Adjustment of Status?", type: "select", options: ["No","Yes"] },
    { key: "spouse_applying_visa_yn", label: "Spouse Applying for Visa Abroad?", type: "select", options: ["No","Yes"] },
    { key: "child1_last_name", label: "Child 1 Last Name" },
    { key: "child1_first_name", label: "Child 1 First Name" },
    { key: "child1_dob", label: "Child 1 DOB (MM/DD/YYYY)" },
    { key: "child1_country_of_birth", label: "Child 1 Country of Birth" },
    { key: "child1_applying_aos_yn", label: "Child 1 Applying for AOS?", type: "select", options: ["No","Yes"] },
    { key: "child1_applying_visa_yn", label: "Child 1 Applying for Visa Abroad?", type: "select", options: ["No","Yes"] },
    { key: "child2_last_name", label: "Child 2 Last Name" },
    { key: "child2_first_name", label: "Child 2 First Name" },
    { key: "child2_dob", label: "Child 2 DOB (MM/DD/YYYY)" },
    { key: "child2_country_of_birth", label: "Child 2 Country of Birth" },
    { key: "child2_applying_aos_yn", label: "Child 2 Applying for AOS?", type: "select", options: ["No","Yes"] },
    { key: "child2_applying_visa_yn", label: "Child 2 Applying for Visa Abroad?", type: "select", options: ["No","Yes"] },
  ]},
  { label: "Part 4 — Regional Center Identifiers (manual — from USCIS approval notices)", fields: [
    { key: "i956f_receipt_number", label: "I-956F Receipt Number (Item 1)" },
    { key: "rc_receipt_number", label: "Regional Center Receipt Number (Item 2)" },
    { key: "nce_id_number", label: "USCIS NCE Identification Number (Item 3)" },
    { key: "investment_area_type", label: "Investment Area Type", type: "select",
      options: ["","Rural Area","High Unemployment Area","High Employment Area","Infrastructure Project","None of the Above"] },
  ]},
  { label: "Part 5 — Investment Details (AI-extracted from project docs)", fields: [
    { key: "investment_date_1", label: "Investment Date 1 (MM/DD/YYYY)" },
    { key: "investment_amount_1", label: "Investment Amount 1 ($)" },
    { key: "investment_date_2", label: "Investment Date 2 (MM/DD/YYYY, if any)" },
    { key: "investment_amount_2", label: "Investment Amount 2 ($, if any)" },
    { key: "investment_total", label: "Total Investment ($)" },
    { key: "capital_cash", label: "Total Cash Deposited into NCE ($)" },
    { key: "net_worth", label: "Your Current Net Worth ($)" },
    { key: "capital_source_income", label: "Capital Source: Income?", type: "select", options: ["No","Yes"] },
    { key: "capital_source_loan", label: "Capital Source: Loan Proceeds?", type: "select", options: ["No","Yes"] },
    { key: "capital_source_gift", label: "Capital Source: Gift/Inheritance?", type: "select", options: ["No","Yes"] },
    { key: "capital_source_real_estate", label: "Capital Source: Sale of Real Estate?", type: "select", options: ["No","Yes"] },
    { key: "capital_source_assets", label: "Capital Source: Tangible Assets?", type: "select", options: ["No","Yes"] },
    { key: "capital_source_insurance", label: "Capital Source: Insurance Proceeds?", type: "select", options: ["No","Yes"] },
    { key: "capital_source_securities", label: "Capital Source: Sale of Securities?", type: "select", options: ["No","Yes"] },
    { key: "lawful_source_explanation", label: "Lawful Source of Funds — Explanation", type: "textarea" },
  ]},
  { label: "Part 6 — Visa Processing", fields: [
    { key: "visa_processing_type", label: "Seeking LPR Status Via", type: "select",
      options: ["","Immigrant Visa Processing","Application for Adjustment of Status"] },
    { key: "country_of_current_residence", label: "Country of Current Residence" },
    { key: "country_of_last_lpr_abroad", label: "Country of Last Permanent Residence Abroad" },
    { key: "foreign_address_street", label: "Foreign Address — Street" },
    { key: "foreign_address_city", label: "City" },
    { key: "foreign_address_country", label: "Country" },
    { key: "filing_other_petitions_yn", label: "Filing Other Petitions With This I-526E?", type: "select", options: ["No","Yes"] },
    { key: "immigration_proceedings_yn", label: "Ever in Immigration Proceedings?", type: "select", options: ["No","Yes"] },
    { key: "final_order_yn", label: "Ever Subject to Final Order of Exclusion/Deportation?", type: "select", options: ["No","Yes"] },
    { key: "worked_without_permission_yn", label: "Ever Worked in US Without Permission?", type: "select", options: ["No","Yes"] },
  ]},
];

let reviewData = {};

function showReview(clientData) {
  reviewData = Object.assign({}, clientData);
  document.getElementById('progress-section').style.display = 'none';
  document.getElementById('review-section').style.display = 'block';
  buildReviewForm();
}

function buildReviewForm() {
  const container = document.getElementById('review-form');
  container.innerHTML = '';
  for (const section of REVIEW_SECTIONS) {
    const grid = document.createElement('div');
    grid.className = 'review-grid';
    const header = document.createElement('div');
    header.className = 'review-group';
    header.textContent = section.label;
    grid.appendChild(header);
    for (const field of section.fields) {
      const val = reviewData[field.key] || '';
      const labelEl = document.createElement('div');
      labelEl.className = 'review-label';
      labelEl.textContent = field.label;
      const valueEl = document.createElement('div');
      valueEl.className = 'review-value';
      if (field.type === 'attorney_select') {
        const sel = document.createElement('select');
        sel.className = 'review-select';
        const blank = document.createElement('option');
        blank.value = ''; blank.textContent = '— Select to auto-fill attorney —';
        sel.appendChild(blank);
        for (const name of Object.keys(ATTORNEY_PROFILES)) {
          const o = document.createElement('option'); o.value = name; o.textContent = name; sel.appendChild(o);
        }
        sel.addEventListener('change', e => { if (e.target.value) applyAttorneyProfile(e.target.value); });
        valueEl.appendChild(sel);
      } else if (field.type === 'select') {
        const sel = document.createElement('select');
        sel.className = 'review-select'; sel.dataset.key = field.key;
        for (const opt of field.options) {
          const o = document.createElement('option'); o.value = opt; o.textContent = opt || '(none)';
          if (opt === val) o.selected = true; sel.appendChild(o);
        }
        sel.addEventListener('change', e => { reviewData[field.key] = e.target.value; });
        valueEl.appendChild(sel);
      } else if (field.type === 'textarea') {
        const ta = document.createElement('textarea');
        ta.className = 'review-textarea'; ta.dataset.key = field.key; ta.value = val;
        ta.addEventListener('input', e => { reviewData[field.key] = e.target.value; });
        valueEl.style.gridColumn = '1 / -1';
        valueEl.appendChild(ta);
      } else {
        const inp = document.createElement('input');
        inp.className = 'review-input'; inp.type = 'text'; inp.value = val;
        inp.dataset.key = field.key; inp.placeholder = val ? '' : '(enter value)';
        inp.addEventListener('input', e => { reviewData[field.key] = e.target.value; });
        valueEl.appendChild(inp);
      }
      grid.appendChild(labelEl);
      grid.appendChild(valueEl);
    }
    container.appendChild(grid);
  }
}

function applyAttorneyProfile(name) {
  const profile = ATTORNEY_PROFILES[name];
  if (!profile) return;
  Object.assign(reviewData, profile);
  for (const [key, val] of Object.entries(profile)) {
    const inp = document.querySelector('[data-key="' + key + '"]');
    if (inp) inp.value = val;
  }
}

async function generatePdf() {
  const btn = document.querySelector('.generate-btn');
  btn.disabled = true; btn.textContent = 'Generating...';
  try {
    const res = await fetch('/approve/' + currentJobId, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_data: reviewData })
    });
    const data = await res.json();
    if (data.error) { showError(data.error); return; }
    document.getElementById('review-section').style.display = 'none';
    document.getElementById('progress-section').style.display = 'block';
    setStep(1, 'done'); setStep(2, 'done'); setStep(3, 'active');
    pollInterval = setInterval(pollStatus, 2000);
  } catch (err) {
    showError('Failed to submit: ' + err.message);
    btn.disabled = false; btn.textContent = 'Generate Filled I-526E →';
  }
}

function setStep(num, state) {
  const el = document.getElementById('step' + num);
  el.className = 'step ' + state;
  const dot = el.querySelector('.step-dot');
  if (state === 'active') dot.innerHTML = '<div class="spinner"></div>';
  else if (state === 'done') dot.innerHTML = '✓';
  else if (state === 'error') dot.innerHTML = '✕';
  else dot.innerHTML = num;
}

function showResult(data) {
  document.getElementById('progress-section').style.display = 'none';
  document.getElementById('result-section').style.display = 'block';
  document.getElementById('statFilled').textContent = data.fields_filled || '—';
  document.getElementById('statFlagged').textContent = data.fields_flagged || '0';
  document.getElementById('clientNameDisplay').textContent = data.client_name || '';
  document.getElementById('downloadPdf').href = '/download/' + currentJobId + '/pdf';
}

function showError(msg) {
  document.getElementById('progress-section').style.display = 'none';
  document.getElementById('upload-section').style.display = 'block';
  alert('Error: ' + msg + '\\n\\nPlease try again.');
}

function resetForm() {
  ['result','review','progress'].forEach(s => document.getElementById(s+'-section').style.display = 'none');
  document.getElementById('upload-section').style.display = 'block';
  document.getElementById('fileSelected').classList.remove('show');
  document.getElementById('submitBtn').disabled = true;
  fileInput.value = ''; allDocs = []; document.getElementById('docsList').innerHTML = '';
  currentJobId = null; reviewData = {};
}
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML, version=VERSION,
        attorney_profiles_json=json.dumps(ATTORNEY_PROFILES))

@app.route("/debug/version")
def debug_version():
    return jsonify({"version": VERSION})

@app.route("/debug/fields")
def debug_fields():
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(I526E_PDF))
        fields = {}
        for pg_num, pg in enumerate(reader.pages):
            if "/Annots" not in pg: continue
            for annot in pg["/Annots"]:
                try:
                    obj = annot.get_object()
                    if "/T" not in obj: continue
                    name = str(obj["/T"])
                    ft = str(obj.get("/FT","")).replace("/","")
                    rect = [float(r) for r in obj.get("/Rect",[])]
                    fields[name] = {"page": pg_num, "type": ft, "rect": rect}
                except Exception: pass
        q = request.args.get("q","")
        if q: fields = {k:v for k,v in fields.items() if q.lower() in k.lower()}
        return jsonify({"count": len(fields), "fields": fields})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/process", methods=["POST"])
def process():
    if "intake" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["intake"]
    job_id = str(uuid.uuid4())[:8]
    intake_path = UPLOAD_DIR / f"{job_id}_intake.pdf"
    file.save(intake_path)
    doc_paths = []
    for doc in request.files.getlist("docs"):
        if doc.filename:
            ext = Path(doc.filename).suffix.lower()
            dp = UPLOAD_DIR / f"{job_id}_doc{len(doc_paths)}{ext}"
            doc.save(dp); doc_paths.append(dp)
    jobs[job_id] = {"status": "running", "step": 1, "message": "Starting..."}
    t = threading.Thread(target=run_job, args=(job_id, intake_path, doc_paths))
    t.daemon = True; t.start()
    return jsonify({"job_id": job_id})

@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id, {"status": "unknown"})
    if job.get("status") == "review_ready":
        return jsonify({"status": "review_ready", "step": job.get("step", 2),
            "client_data": job.get("client_data", {})})
    return jsonify({k: v for k, v in job.items() if k not in ("client_data",)})

@app.route("/approve/<job_id>", methods=["POST"])
def approve(job_id):
    job = jobs.get(job_id)
    if not job: return jsonify({"error": "Job not found"}), 404
    if job.get("status") != "review_ready":
        return jsonify({"error": f"Job not in review_ready state (is: {job.get('status')})"}), 400
    body = request.get_json(force=True)
    jobs[job_id]["corrected_data"] = body.get("client_data", {})
    jobs[job_id]["status"] = "approved"
    return jsonify({"ok": True})

@app.route("/download/<job_id>/pdf")
def download_pdf(job_id):
    p = OUTPUT_DIR / f"{job_id}_filled.pdf"
    if not p.exists(): return jsonify({"error": "Not found"}), 404
    return send_file(str(p), as_attachment=True, download_name="I-526E_filled.pdf")


# ── Background job ────────────────────────────────────────────────────────────

def run_job(job_id, intake_path, doc_paths):
    try:
        import anthropic as _anthropic
        import fitz as _fitz, io

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        client = _anthropic.Anthropic(api_key=api_key)

        # ── Step 1: OCR all documents ─────────────────────────────────────────
        jobs[job_id]["step"] = 1
        jobs[job_id]["message"] = "Reading documents..."

        def ocr_file(fp):
            fp = Path(fp); suffix = fp.suffix.lower()
            try:
                if suffix in (".jpg", ".jpeg", ".png"):
                    with open(fp, "rb") as f: b64 = base64.b64encode(f.read()).decode()
                    mt = "image/png" if suffix == ".png" else "image/jpeg"
                    r = client.messages.create(model=MODEL, max_tokens=2000,
                        messages=[{"role":"user","content":[
                            {"type":"image","source":{"type":"base64","media_type":mt,"data":b64}},
                            {"type":"text","text":"Extract every piece of text, name, number, date, and field label visible. Format as 'FieldName: Value' on separate lines."}
                        ]}])
                    return fp.name, r.content[0].text
                elif suffix == ".docx":
                    import subprocess
                    result = subprocess.run(["python3", "-c",
                        f"import sys; sys.path.insert(0,'.'); "
                        f"from docx import Document; d=Document('{fp}'); "
                        f"print('\\n'.join(p.text for p in d.paragraphs if p.text.strip()))"],
                        capture_output=True, text=True, timeout=30)
                    return fp.name, result.stdout or "[Could not read docx]"
                else:  # PDF
                    doc = _fitz.open(str(fp))
                    mat = _fitz.Matrix(100/72, 100/72)
                    def ocr_page(pg):
                        pix = pg.get_pixmap(matrix=mat, alpha=False)
                        b64 = base64.b64encode(io.BytesIO(pix.tobytes("png")).getvalue()).decode()
                        r = client.messages.create(model=MODEL, max_tokens=2000,
                            messages=[{"role":"user","content":[
                                {"type":"image","source":{"type":"base64","media_type":"image/png","data":b64}},
                                {"type":"text","text":f"Page {pg.number+1} of '{fp.name}'. Extract all text, names, numbers, dates, amounts, field labels. Format as 'FieldName: Value' on separate lines."}
                            ]}])
                        return r.content[0].text
                    with ThreadPoolExecutor(max_workers=4) as ex:
                        futs = [ex.submit(ocr_page, pg) for pg in doc]
                        texts = [f.result() for f in futs]
                    doc.close()
                    return fp.name, "\n".join(texts)
            except Exception as e:
                return fp.name, f"[Could not read {fp.name}: {e}]"

        all_files = [intake_path] + list(doc_paths)
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(ocr_file, fp): fp for fp in all_files}
            ordered = {fp: None for fp in all_files}
            for fut in as_completed(futs):
                ordered[futs[fut]] = fut.result()

        _, intake_text = ordered[intake_path]
        full_context = f"=== BIOGRAPHICAL INTAKE FORM ===\n{intake_text}"
        for fp in doc_paths:
            if ordered[fp]:
                label, text = ordered[fp]
                full_context += f"\n\n=== PROJECT DOCUMENT: {label} ===\n{text}"

        logging.info(f"[I526E STEP1] Total context: {len(full_context)} chars")

        # ── Step 2: Extract all data ──────────────────────────────────────────
        jobs[job_id]["step"] = 2
        jobs[job_id]["message"] = "Extracting data..."

        EXTRACTION_SYSTEM = """You are an expert EB-5 immigration paralegal reading two types of documents:
1. A biographical intake form (personal info about the investor)
2. Project documents from a Regional Center (subscription agreement, wire confirmations, fee waiver letters, source of funds memos, declarations)

Extract ALL available data and return a flat JSON object. Return ONLY valid JSON, no prose, no markdown.

CRITICAL RULES:
- Every value must come from the actual documents. Do NOT invent or assume.
- Dates in MM/DD/YYYY format.
- State fields: 2-letter abbreviations only.
- A-Number: digits only, no leading 'A'.
- Sex: exactly "Male" or "Female".
- Dollar amounts: digits only, no $ sign, no commas (e.g. "800000" not "$800,000").
- If a field is not found, omit it entirely.

From the BIOGRAPHICAL INTAKE FORM, extract:
last_name, first_name, middle_name,
a_number, uscis_account_number, ssn,
date_of_birth, sex,
city_of_birth, country_of_birth,
country_of_citizenship_current, country_of_citizenship_relinquished,
country_of_last_residence,
address_street, address_apt, address_city, address_state, address_zip, address_country,
employer_name, employer_job_title, employer_from,
date_of_arrival, i94_number,
passport_number, passport_country, passport_expiration,
current_nonimmigrant_status, status_expiration,
phone_number, email,
spouse_last_name, spouse_first_name, spouse_middle_name, spouse_dob, spouse_country_of_birth,
spouse_citizenship_current, spouse_applying_aos_yn, spouse_applying_visa_yn,
child1_last_name, child1_first_name, child1_dob, child1_country_of_birth,
child1_applying_aos_yn, child1_applying_visa_yn,
child2_last_name, child2_first_name, child2_dob, child2_country_of_birth,
child2_applying_aos_yn, child2_applying_visa_yn

From the PROJECT DOCUMENTS, extract:
investment_date_1, investment_amount_1,
investment_date_2, investment_amount_2,
investment_total,
capital_cash,
net_worth,
lawful_source_explanation (brief description of sources, e.g. "Accumulated savings, stock sale proceeds, 401k loans, real estate sale, gifted funds"),
capital_source_income (Yes/No — based on whether income is mentioned as a source),
capital_source_loan (Yes/No — 401k loans, mortgage proceeds, etc.),
capital_source_gift (Yes/No — gifts or inheritance),
capital_source_real_estate (Yes/No — sale of real estate),
capital_source_assets (Yes/No — tangible assets),
capital_source_insurance (Yes/No — insurance proceeds),
capital_source_securities (Yes/No — sale of stocks/securities)

Also extract if present in project docs:
country_of_current_residence, country_of_last_lpr_abroad"""

        r = client.messages.create(
            model=MODEL, max_tokens=4000, system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": f"Extract all data from these documents:\n\n{full_context}"}]
        )
        raw = r.content[0].text.strip().replace("```json","").replace("```","").strip()
        try:
            client_data = json.loads(raw)
        except Exception:
            client_data = {}

        # Apply attorney defaults
        client_data = {**ATTORNEY_PROFILES["Kevin Qi"], **client_data}
        # Defaults
        client_data.setdefault("petition_type", "Initial Petition")
        client_data.setdefault("spouse_applying_aos_yn", "No")
        client_data.setdefault("spouse_applying_visa_yn", "No")
        client_data.setdefault("child1_applying_aos_yn", "No")
        client_data.setdefault("child1_applying_visa_yn", "No")
        client_data.setdefault("child2_applying_aos_yn", "No")
        client_data.setdefault("child2_applying_visa_yn", "No")
        client_data.setdefault("filing_other_petitions_yn", "No")
        client_data.setdefault("immigration_proceedings_yn", "No")
        client_data.setdefault("final_order_yn", "No")
        client_data.setdefault("worked_without_permission_yn", "No")

        jobs[job_id]["status"] = "review_ready"
        jobs[job_id]["step"] = 2
        jobs[job_id]["client_data"] = client_data
        logging.info(f"[I526E STEP2] Extracted {len(client_data)} keys — waiting for review")

        # ── Wait for paralegal approval — no timeout ──────────────────────────
        while True:
            time.sleep(2)
            job = jobs.get(job_id, {})
            if job.get("status") == "approved":
                client_data = job.get("corrected_data", client_data)
                jobs[job_id]["step"] = 3
                break
            elif job.get("status") == "cancelled":
                jobs[job_id] = {"status": "error", "message": "Cancelled by user"}
                return

        # ── Step 3: PIL overlay ───────────────────────────────────────────────
        jobs[job_id]["step"] = 3
        jobs[job_id]["message"] = "Filling Form I-526E..."

        from PIL import Image as PILImage, ImageDraw, ImageFont
        import img2pdf as i2p
        from pypdf import PdfReader as PR

        # Full field ID → data key map (all field IDs verified against real PDF)
        FIELD_MAP = [
            # Attorney (Part 0 header + Part 11 preparer)
            ("form1[0].#subform[0].#area[0].USCISELISAcctNumber[0]",   "attorney_uscis_acct",           "text"),
            ("form1[0].#subform[13].P10_Line1a_FamilyName[0]",          "attorney_last_name",            "text"),
            ("form1[0].#subform[13].P10_Line1b_PreparersGivenName[0]",  "attorney_first_name",           "text"),
            ("form1[0].#subform[13].P10_Line2_NameofBusinessor[0]",     "attorney_org_name",             "text"),
            ("form1[0].#subform[14].P9_Line4_DaytimePhoneNumber[2]",    "attorney_phone",                "text"),
            ("form1[0].#subform[14].P9_Line6_EmailAddress[2]",          "attorney_email",                "text"),
            # Part 4 — Regional Center identifiers
            ("form1[0].#subform[6].P4_Line1_956F[0]",                   "i956f_receipt_number",          "text"),
            ("form1[0].#subform[6].P4_Line2_RC[0]",                     "rc_receipt_number",             "text"),
            ("form1[0].#subform[6].P4_Line3_NCE[0]",                    "nce_id_number",                 "text"),
            # Part 2 — Personal info
            ("form1[0].#subform[0].#area[3].P2_Line1_AlienNumber[0]",   "a_number",                      "text"),
            ("form1[0].#subform[0].#area[2].P2_Line2_AcctIdentifier[0]","uscis_account_number",          "text"),
            ("form1[0].#subform[0].#area[4].P2_Line3_SSN[0]",           "ssn",                           "text"),
            ("form1[0].#subform[1].P2_Line4_FamilyName[0]",             "last_name",                     "text"),
            ("form1[0].#subform[1].P2_Line4_GivenName[0]",              "first_name",                    "text"),
            ("form1[0].#subform[1].P2_Line4_MiddleName[0]",             "middle_name",                   "text"),
            ("form1[0].#subform[1].P1_Line7_DateOfBirth[0]",            "date_of_birth",                 "text"),
            ("form1[0].#subform[1].P1_Line21_CityTownOfBirth[0]",       "city_of_birth",                 "text"),
            ("form1[0].#subform[1].P1_Line22_CountryofBirth[0]",        "country_of_birth",              "text"),
            ("form1[0].#subform[1].P1_Line23CountryofBirth[0]",         "country_of_citizenship_current","text"),
            ("form1[0].#subform[1].P1_Line24_CountryofBirth[0]",        "country_of_citizenship_relinquished","text"),
            ("form1[0].#subform[1].P1_Line25_CountryofLast[0]",         "country_of_last_residence",     "text"),
            ("form1[0].#subform[1].P1_Line7_StreetNumberName[0]",       "address_street",                "text"),
            ("form1[0].#subform[1].P1_Line7_AptSteFlrNumber[0]",        "address_apt",                   "text"),
            ("form1[0].#subform[1].P1_Line7_CityOrTown[0]",             "address_city",                  "text"),
            ("form1[0].#subform[1].P1_Line7_ZipCode[0]",                "address_zip",                   "text"),
            ("form1[0].#subform[1].P1_Line7_Country[0]",                "address_country",               "text"),
            ("form1[0].#subform[3].P1_14a_EmployerName[0]",             "employer_name",                 "text"),
            ("form1[0].#subform[3].P1_14j_jobTitle[0]",                 "employer_job_title",            "text"),
            ("form1[0].#subform[3].P1_14k_from[0]",                     "employer_from",                 "text"),
            ("form1[0].#subform[4].P2_Line24_DateOfarrival[0]",         "date_of_arrival",               "text"),
            ("form1[0].#subform[4].Line27_ArrivalDeparture[0]",         "i94_number",                    "text"),
            ("form1[0].#subform[4].Line29_Passport[0]",                 "passport_number",               "text"),
            ("form1[0].#subform[4].P2_Line30_Country[0]",               "passport_country",              "text"),
            ("form1[0].#subform[4].P2_Line31_DateOfPeriod[0]",          "passport_expiration",           "text"),
            ("form1[0].#subform[4].Line32_CurrentNon[0]",               "current_nonimmigrant_status",   "text"),
            ("form1[0].#subform[4].P2_Line33_DateOfPeriod[0]",          "status_expiration",             "text"),
            ("form1[0].#subform[11].P9_Line4_DaytimePhoneNumber[0]",    "phone_number",                  "text"),
            ("form1[0].#subform[11].P9_Line6_EmailAddress[0]",          "email",                         "text"),
            # Part 3 — Spouse (Family Member 1)
            ("form1[0].#subform[4].P3_Line1a_FamilyName[0]",            "spouse_last_name",              "text"),
            ("form1[0].#subform[4].P3_Line1b_GivenName[0]",             "spouse_first_name",             "text"),
            ("form1[0].#subform[4].P3_Line1c_MiddleName[0]",            "spouse_middle_name",            "text"),
            ("form1[0].#subform[4].P3_Line2_DateOfBirth2[0]",           "spouse_dob",                    "text"),
            ("form1[0].#subform[4].P3_Line3_Country[0]",                "spouse_country_of_birth",       "text"),
            ("form1[0].#subform[4].P3_Line4_SpouseCountry[0]",          "spouse_citizenship_current",    "text"),
            # Part 3 — Child 1 (Family Member 2)
            ("form1[0].#subform[5].P7_Line7a_FamilyName[0]",            "child1_last_name",              "text"),
            ("form1[0].#subform[5].P7_Line7b_GivenName[0]",             "child1_first_name",             "text"),
            ("form1[0].#subform[5].P7_Line8_DateOfBirth2[0]",           "child1_dob",                    "text"),
            ("form1[0].#subform[5].P7_Line10_Country[0]",               "child1_country_of_birth",       "text"),
            # Part 3 — Child 2 (Family Member 3)
            ("form1[0].#subform[5].P7_Line13a_FamilyName[0]",           "child2_last_name",              "text"),
            ("form1[0].#subform[5].P7_Line13b_GivenName[0]",            "child2_first_name",             "text"),
            ("form1[0].#subform[5].P7_Line14_DateOfBirth2[0]",          "child2_dob",                    "text"),
            ("form1[0].#subform[5].P7_Line15_Country[0]",               "child2_country_of_birth",       "text"),
            # Part 5 — Investment
            ("form1[0].#subform[6].P5_line1a[0]",                       "investment_date_1",             "text"),
            ("form1[0].#subform[6].P5_line1b[0]",                       "investment_amount_1",           "text"),
            ("form1[0].#subform[6].P5_line1c[0]",                       "investment_date_2",             "text"),
            ("form1[0].#subform[6].P5_line1d[0]",                       "investment_amount_2",           "text"),
            ("form1[0].#subform[6].P5_line1Total[0]",                   "investment_total",              "text"),
            ("form1[0].#subform[7].P5_line2[0]",                        "capital_cash",                  "text"),
            ("form1[0].#subform[7].P3_line12[0]",                       "net_worth",                     "text"),
            ("form1[0].#subform[8].P2_line21f_AdditionalInfo[0]",       "lawful_source_explanation",     "text"),
            # Part 6 — Visa processing
            ("form1[0].#subform[8].Pt6Line1c_CountryofResidence[0]",    "country_of_current_residence",  "text"),
            ("form1[0].#subform[8].Pt6Line2b_CountryofResidence[0]",    "country_of_last_lpr_abroad",    "text"),
            ("form1[0].#subform[8].P6_Line3a_StreetNumberName[0]",      "foreign_address_street",        "text"),
            ("form1[0].#subform[8].P6_Line3c_CityOrTown[0]",            "foreign_address_city",          "text"),
            ("form1[0].#subform[8].P6_Line3f_Country[0]",               "foreign_address_country",       "text"),
        ]

        # Checkbox map: (field_id, condition_key, condition_value)
        # All on-values confirmed from PDF field states
        def _yn(key, val): return str(client_data.get(key, "")).lower().strip() == val.lower()

        CHECKBOX_MAP = []
        # Petition type — initial=index 0
        if client_data.get("petition_type","").lower().startswith("initial"):
            CHECKBOX_MAP.append("form1[0].#subform[0].prt1PetitionType[0]")
        else:
            CHECKBOX_MAP.append("form1[0].#subform[0].prt1PetitionTypeNCE[0]")

        # G-28 checkbox — always check
        CHECKBOX_MAP.append("form1[0].#subform[0].CheckBox1[0]")

        # Sex
        sex = str(client_data.get("sex","")).lower()
        if sex == "male":   CHECKBOX_MAP.append("form1[0].#subform[1].P1_Line8_Sex[0]")
        elif sex == "female": CHECKBOX_MAP.append("form1[0].#subform[1].P1_Line8_Sex[1]")

        # Same mailing = physical address — default Yes
        CHECKBOX_MAP.append("form1[0].#subform[2].P2_Line1[0]")

        # Employment — has employer → Yes
        if client_data.get("employer_name"):
            CHECKBOX_MAP.append("form1[0].#subform[2].pt1Line8_YesNo[0]")
        else:
            CHECKBOX_MAP.append("form1[0].#subform[2].pt1Line8_YesNo[1]")

        # Spouse applying — Family Member 1 checkboxes
        if _yn("spouse_applying_aos_yn","yes"):  CHECKBOX_MAP.append("form1[0].#subform[4].P3_Line7[0]")
        else:                                     CHECKBOX_MAP.append("form1[0].#subform[4].P3_Line7[1]")
        if _yn("spouse_applying_visa_yn","yes"): CHECKBOX_MAP.append("form1[0].#subform[4].P3_Line8[0]")
        else:                                     CHECKBOX_MAP.append("form1[0].#subform[4].P3_Line8[1]")
        # Spouse relationship
        CHECKBOX_MAP.append("form1[0].#subform[4].P3_Line6[0]")  # Spouse

        # Child 1 applying (Family Member 2)
        if _yn("child1_applying_aos_yn","yes"): CHECKBOX_MAP.append("form1[0].#subform[5].P7_Line11[0]")
        else:                                    CHECKBOX_MAP.append("form1[0].#subform[5].P7_Line11[1]")
        if _yn("child1_applying_visa_yn","yes"): CHECKBOX_MAP.append("form1[0].#subform[5].P7_Line12[0]")
        else:                                     CHECKBOX_MAP.append("form1[0].#subform[5].P7_Line12[1]")

        # Child 2 applying (Family Member 3)
        if _yn("child2_applying_aos_yn","yes"): CHECKBOX_MAP.append("form1[0].#subform[5].P7_Line18[0]")
        else:                                    CHECKBOX_MAP.append("form1[0].#subform[5].P7_Line18[1]")
        if _yn("child2_applying_visa_yn","yes"): CHECKBOX_MAP.append("form1[0].#subform[5].P7_Line17[0]")
        else:                                     CHECKBOX_MAP.append("form1[0].#subform[5].P7_Line17[1]")

        # Investment area type (Part 4) — indices: Rural=0, HighUnemployment=1, HighEmployment=2, Infrastructure=3, None=4
        area_map = {"rural area":0, "high unemployment area":1, "high employment area":2,
                    "infrastructure project":3, "none of the above":4}
        area_val = str(client_data.get("investment_area_type","none of the above")).lower().strip()
        area_idx = area_map.get(area_val, 4)
        CHECKBOX_MAP.append(f"form1[0].#subform[6].P3_typeofNCE[{area_idx}]")

        # Capital sources
        src_map = [
            ("capital_source_income",       "form1[0].#subform[7].P2_line21a_sourcesInvestment[0]"),
            ("capital_source_loan",         "form1[0].#subform[7].P2_line21b_sourcesInvestment[0]"),
            ("capital_source_gift",         "form1[0].#subform[7].P2_line21c_sourcesInvestment[0]"),
            ("capital_source_real_estate",  "form1[0].#subform[7].P2_line21d_sourcesInvestment[0]"),
            ("capital_source_assets",       "form1[0].#subform[7].P2_line21d_sourcesInvestment[1]"),
            ("capital_source_insurance",    "form1[0].#subform[7].P2_line21d_sourcesInvestment[2]"),
            ("capital_source_securities",   "form1[0].#subform[7].P2_line21d_sourcesInvestment[3]"),
        ]
        for key, fid in src_map:
            if _yn(key, "yes"): CHECKBOX_MAP.append(fid)

        # Visa processing type
        vtype = str(client_data.get("visa_processing_type","")).lower()
        if "adjustment" in vtype: CHECKBOX_MAP.append("form1[0].#subform[8].Pt6Line2a_AFAOS[0]")
        elif "immigrant visa" in vtype: CHECKBOX_MAP.append("form1[0].#subform[8].Pt6Line1a_IVP[0]")

        # Part 6 Yes/No defaults — all No unless specified
        yn_defaults = [
            ("filing_other_petitions_yn",    "form1[0].#subform[9].P6_Line6[0]",   "form1[0].#subform[9].P6_Line6[1]"),
            ("immigration_proceedings_yn",   "form1[0].#subform[9].P6_Line9[0]",   "form1[0].#subform[9].P6_Line9[1]"),
            ("final_order_yn",               "form1[0].#subform[10].P6_Line10[0]", "form1[0].#subform[10].P6_Line10[1]"),
            ("worked_without_permission_yn", "form1[0].#subform[9].P6_Line11[0]",  "form1[0].#subform[9].P6_Line11[1]"),
        ]
        for key, yes_fid, no_fid in yn_defaults:
            if _yn(key, "yes"): CHECKBOX_MAP.append(yes_fid)
            else:               CHECKBOX_MAP.append(no_fid)

        # Part 7 Bona Fides — all No by default
        for fid in ["P7_Line1_YesNo","P7_Line2_YesNo","P7_Line3_YesNo",
                    "P7_Line5_YesNo","P7_Line6_YesNo","P7_Line7_YesNo",
                    "P7_Line8_YesNo","P7_Line9_YesNo","P7_Line10_YesNo"]:
            CHECKBOX_MAP.append(f"form1[0].#subform[10].{fid}[1]")
        for fid in ["P7_Line11_YesNo","P7_Line12_YesNo","P7_Line13_YesNo"]:
            CHECKBOX_MAP.append(f"form1[0].#subform[11].{fid}[1]")
        # Part 7 Item 4 sub-questions — all No
        for fid in ["P7_Line4b_YesNo","P7_Line4c_YesNo","P7_Line4d_YesNo","P7_Line4e_YesNo","P7_Line4f_YesNo"]:
            CHECKBOX_MAP.append(f"form1[0].#subform[10].{fid}[1]")

        # Part 8 Foreign involvement — all No by default
        for fid in ["P8_Line1_YesNo","P8_Line2_YesNo","P8_Line3_YesNo"]:
            CHECKBOX_MAP.append(f"form1[0].#subform[11].{fid}[1]")

        # Preparer statement — attorney whose representation extends
        CHECKBOX_MAP.append("form1[0].#subform[14].P12_Line7b_extends[0]")

        # Build field positions via full parent-chain walk
        def _full_field_name(obj):
            parts = []
            node = obj
            seen = 0
            while node is not None and seen < 12:
                t = node.get("/T")
                if t: parts.append(str(t))
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
                seen += 1
            return ".".join(reversed(parts))

        reader = PR(str(I526E_PDF))
        field_positions = {}
        for pg_num, pg in enumerate(reader.pages):
            if "/Annots" not in pg: continue
            for annot in pg["/Annots"]:
                try:
                    obj = annot.get_object()
                    if "/T" not in obj or "/Rect" not in obj: continue
                    full_name = _full_field_name(obj)
                    rect = obj["/Rect"]
                    field_positions[full_name] = {
                        "page": pg_num,
                        "rect": [float(r) for r in rect],
                        "page_height": float(pg.mediabox.height),
                        "page_width":  float(pg.mediabox.width),
                    }
                except Exception: pass

        logging.info(f"[I526E OVERLAY] {len(field_positions)} field positions found")

        text_values = []
        for (field_id, data_key, _) in FIELD_MAP:
            val = client_data.get(data_key, "")
            if val: text_values.append((field_id, str(val)))

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except Exception:
            try: font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 18)
            except Exception: font = ImageFont.load_default()

        doc = _fitz.open(str(I526E_PDF))
        mat = _fitz.Matrix(150/72, 150/72)
        pngs = []

        for pg in doc:
            pg_idx = pg.number
            pix = pg.get_pixmap(matrix=mat, alpha=False)
            img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_w, img_h = img.size
            draw = ImageDraw.Draw(img)

            for (field_id, val) in text_values:
                pos = field_positions.get(field_id)
                if not pos or pos["page"] != pg_idx: continue
                pdf_w, pdf_h = pos["page_width"], pos["page_height"]
                rect = pos["rect"]
                x0 = rect[0] / pdf_w * img_w; y0 = (pdf_h - rect[3]) / pdf_h * img_h
                x1 = rect[2] / pdf_w * img_w; y1 = (pdf_h - rect[1]) / pdf_h * img_h
                draw.rectangle([x0+1, y0+1, x1-1, y1-1], fill="white")
                draw.text((x0+3, y0+2), str(val)[:70], fill="black", font=font)

            for cb_id in CHECKBOX_MAP:
                pos = field_positions.get(cb_id)
                if not pos or pos["page"] != pg_idx: continue
                pdf_w, pdf_h = pos["page_width"], pos["page_height"]
                rect = pos["rect"]
                x0 = rect[0] / pdf_w * img_w; y0 = (pdf_h - rect[3]) / pdf_h * img_h
                x1 = rect[2] / pdf_w * img_w; y1 = (pdf_h - rect[1]) / pdf_h * img_h
                w, h = x1 - x0, y1 - y0
                draw.rectangle([x0+w*0.2, y0+h*0.2, x1-w*0.2, y1-h*0.2], fill="#1A1A1A")

            png_path = str(OUTPUT_DIR / f"{job_id}_pg{pg_idx}.png")
            img.save(png_path); pngs.append(png_path); img.close()

        doc.close()

        filled_pdf = OUTPUT_DIR / f"{job_id}_filled.pdf"
        with open(str(filled_pdf), "wb") as f:
            f.write(i2p.convert(pngs))
        for p in pngs:
            Path(p).unlink(missing_ok=True)

        logging.info(f"[I526E OVERLAY] Done — {len(pngs)} pages")

        # ── Step 4: Done ──────────────────────────────────────────────────────
        client_name = f"{client_data.get('first_name','')} {client_data.get('last_name','')}".strip()
        fields_filled = len([v for _, v in text_values if v]) + len(CHECKBOX_MAP)

        jobs[job_id] = {
            "status": "done", "step": 4,
            "client_name": client_name,
            "fields_filled": fields_filled,
            "fields_flagged": 0,
            "message": "Complete",
        }

    except Exception as e:
        import traceback
        jobs[job_id] = {"status": "error", "message": str(e), "trace": traceback.format_exc()}
        logging.error(traceback.format_exc())


# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not I526E_PDF.exists():
        print(f"⚠️  i-526e.pdf not found at {I526E_PDF}")
    print(f"\n✅ SMS Law I-526E AutoFill Server v{VERSION}")
    print("   Open in browser: http://localhost:5003\n")
    port = int(os.environ.get("PORT", 5003))
    app.run(debug=False, port=port, host="0.0.0.0")
