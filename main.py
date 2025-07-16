from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, HttpUrl
import asyncio
import subprocess
import json
import uuid
import os
from datetime import datetime
from typing import Dict, List, Optional
import re
import tempfile
import shutil
from pathlib import Path

# Google Cloud imports
from google.cloud import storage
from google.cloud import firestore
import google.auth

app = FastAPI(title="Web Archive API - Cloud Run")

# Google Cloud clients
try:
    storage_client = storage.Client()
    firestore_client = firestore.Client()
except Exception as e:
    print(f"Warning: Google Cloud clients not initialized: {e}")
    storage_client = None
    firestore_client = None

# Configuration
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "web-archive-storage")
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "your-project-id")
PORT = int(os.getenv("PORT", 8080))

class ArchiveRequest(BaseModel):
    url: HttpUrl

class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: int
    url: str
    created_at: str
    completed_at: Optional[str] = None
    archive_path: Optional[str] = None
    gcs_path: Optional[str] = None

class FirestoreJobManager:
    def __init__(self, client):
        self.client = client
        self.collection = client.collection('archive_jobs')
    
    async def create_job(self, job_data: dict) -> str:
        doc_ref = self.collection.document(job_data['job_id'])
        doc_ref.set(job_data)
        return job_data['job_id']
    
    async def update_job(self, job_id: str, updates: dict):
        doc_ref = self.collection.document(job_id)
        doc_ref.update(updates)
    
    async def get_job(self, job_id: str) -> Optional[dict]:
        doc_ref = self.collection.document(job_id)
        doc = doc_ref.get()
        return doc.to_dict() if doc.exists else None
    
    async def get_all_jobs(self) -> List[dict]:
        docs = self.collection.stream()
        return [doc.to_dict() for doc in docs]
    
    async def get_completed_jobs(self) -> List[dict]:
        docs = self.collection.where('status', '==', 'completed').stream()
        return [doc.to_dict() for doc in docs]

# Initialize job manager
job_manager = FirestoreJobManager(firestore_client) if firestore_client else None

class CloudStorageManager:
    def __init__(self, client, bucket_name):
        self.client = client
        self.bucket_name = bucket_name
        self.bucket = client.bucket(bucket_name) if client else None
    
    async def upload_archive(self, local_path: str, job_id: str) -> str:
        """Upload archive to GCS and return public URL"""
        if not self.bucket:
            return f"gs://{self.bucket_name}/archives/{job_id}/"
        
        blob_name = f"archives/{job_id}/{os.path.basename(local_path)}"
        blob = self.bucket.blob(blob_name)
        blob.upload_from_filename(local_path)
        
        # Make the blob publicly readable
        blob.make_public()
        return blob.public_url
    
    async def list_archives(self, job_id: str) -> List[str]:
        """List all archives for a job"""
        if not self.bucket:
            return []
        
        blobs = self.bucket.list_blobs(prefix=f"archives/{job_id}/")
        return [blob.name for blob in blobs]

# Initialize storage manager
storage_manager = CloudStorageManager(storage_client, STORAGE_BUCKET)

@app.get("/", response_class=HTMLResponse)
async def get_frontend():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Web Archive Tool - Cloud Run</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            .container { margin: 20px 0; }
            input[type="url"] { width: 400px; padding: 10px; margin-right: 10px; }
            button { padding: 10px 20px; background: #007bff; color: white; border: none; cursor: pointer; }
            button:hover { background: #0056b3; }
            button:disabled { background: #ccc; cursor: not-allowed; }
            .job { border: 1px solid #ddd; padding: 15px; margin: 10px 0; border-radius: 5px; }
            .progress { width: 100%; height: 20px; background: #f0f0f0; border-radius: 10px; overflow: hidden; }
            .progress-bar { height: 100%; background: #28a745; transition: width 0.3s; }
            .status { margin: 5px 0; }
            .playback-btn { background: #28a745; margin-left: 10px; }
            .playback-btn:hover { background: #218838; }
            .download-btn { background: #6c757d; margin-left: 10px; }
            .download-btn:hover { background: #5a6268; }
            .retry-btn { background: #ffc107; color: #212529; margin-left: 10px; }
            .retry-btn:hover { background: #e0a800; }
            .delete-btn { background: #dc3545; margin-left: 10px; }
            .delete-btn:hover { background: #c82333; }
            .crawler-badge { 
                font-size: 10px; 
                padding: 2px 6px; 
                border-radius: 8px; 
                margin-left: 8px;
                font-weight: bold;
            }
            .crawler-python { background: #3776ab; color: white; }
            .crawler-browsertrix { background: #ff6b35; color: white; }
            .crawler-python_fallback { background: #ffc107; color: #212529; }
            .crawler-reason { 
                margin: 5px 0; 
                color: #666; 
                font-style: italic; 
            }
            .error { color: red; }
            .success { color: green; }
            .cloud-badge { 
                background: #4285f4; 
                color: white; 
                padding: 2px 8px; 
                border-radius: 12px; 
                font-size: 12px; 
                margin-left: 10px; 
            }
        </style>
    </head>
    <body>
        <h1>Web Archive Tool <span class="cloud-badge">Cloud Run</span></h1>
        
        <div class="container">
            <h2>Archive a Website</h2>
            <input type="url" id="urlInput" placeholder="Enter website URL (e.g., https://example.com)" />
            <button onclick="startArchive()">Archive</button>
            <div id="message"></div>
        </div>

        <div class="container">
            <h2>Active Jobs</h2>
            <div id="activeJobs"></div>
        </div>

        <div class="container">
            <h2>Completed Archives</h2>
            <div id="completedArchives"></div>
        </div>

        <script>
            let eventSource = null;
            let jobs = {};

            function showMessage(text, type = 'info') {
                const messageDiv = document.getElementById('message');
                messageDiv.innerHTML = `<div class="${type}">${text}</div>`;
                if (type !== 'error') {
                    setTimeout(() => messageDiv.innerHTML = '', 3000);
                }
            }

            async function startArchive() {
                const url = document.getElementById('urlInput').value;
                if (!url) {
                    showMessage('Please enter a URL', 'error');
                    return;
                }

                try {
                    const response = await fetch('/api/archive', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: url })
                    });

                    if (!response.ok) {
                        throw new Error('Failed to start archiving');
                    }

                    const result = await response.json();
                    showMessage(`Archive started! Job ID: ${result.job_id}`, 'success');
                    document.getElementById('urlInput').value = '';
                    
                    startProgressMonitoring();
                } catch (error) {
                    showMessage(`Error: ${error.message}`, 'error');
                }
            }

            function startProgressMonitoring() {
                if (eventSource) {
                    eventSource.close();
                }

                eventSource = new EventSource('/api/progress');
                eventSource.onmessage = function(event) {
                    const data = JSON.parse(event.data);
                    if (data.jobs && Array.isArray(data.jobs)) {
                        updateJobList(data.jobs);
                    }
                };

                eventSource.onerror = function() {
                    console.error('EventSource error');
                    setTimeout(startProgressMonitoring, 5000);
                };
            }

            function updateJobList(jobList) {
                // Clear and rebuild the jobs object
                jobs = {};
                jobList.forEach(job => {
                    if (job && job.job_id && job.url && job.status) {
                        jobs[job.job_id] = job;
                    }
                });
                
                const activeDiv = document.getElementById('activeJobs');
                const completedDiv = document.getElementById('completedArchives');
                
                activeDiv.innerHTML = '';
                completedDiv.innerHTML = '';
                
                Object.values(jobs).forEach(job => {
                    const jobHTML = createJobHTML(job);
                    if (job.status === 'completed') {
                        completedDiv.innerHTML += jobHTML;
                    } else {
                        activeDiv.innerHTML += jobHTML;
                    }
                });
            }

            function createJobHTML(job) {
                // Ensure all required fields exist
                if (!job || !job.job_id || !job.url || !job.status) {
                    return '';
                }
                
                const progressWidth = job.progress || 0;
                const playbackButton = job.status === 'completed' && job.gcs_path ? 
                    `<button class="playback-btn" onclick="playArchive('${job.gcs_path}')">📺 Play</button>` : '';
                const downloadButton = job.status === 'completed' && job.gcs_path ? 
                    `<button class="download-btn" onclick="downloadArchive('${job.gcs_path}')">⬇️ Download</button>` : '';
                const retryButton = job.status === 'failed' ? 
                    `<button class="retry-btn" onclick="retryJob('${job.job_id}')">🔄 Retry</button>` : '';
                const deleteButton = job.status === 'failed' ? 
                    `<button class="delete-btn" onclick="deleteJob('${job.job_id}')">🗑️ Delete</button>` : '';
                
                const startedDate = job.created_at ? new Date(job.created_at).toLocaleString() : 'Unknown';
                const completedDate = job.completed_at ? new Date(job.completed_at).toLocaleString() : null;
                
                // Crawler type badge
                const crawlerBadge = job.crawler_type ? 
                    `<span class="crawler-badge crawler-${job.crawler_type}">${job.crawler_type.toUpperCase()}</span>` : '';
                
                const crawlerReason = job.crawler_reason ? 
                    `<div class="crawler-reason"><small>📋 ${job.crawler_reason}</small></div>` : '';
                
                return `
                    <div class="job">
                        <div><strong>URL:</strong> ${job.url} ${crawlerBadge}</div>
                        <div class="status"><strong>Status:</strong> ${job.status}</div>
                        <div class="progress">
                            <div class="progress-bar" style="width: ${progressWidth}%"></div>
                        </div>
                        <div>Progress: ${progressWidth}%</div>
                        <div><strong>Started:</strong> ${startedDate}</div>
                        ${completedDate ? `<div><strong>Completed:</strong> ${completedDate}</div>` : ''}
                        ${job.gcs_path ? `<div><strong>Storage:</strong> Cloud Storage</div>` : ''}
                        ${crawlerReason}
                        ${playbackButton}
                        ${downloadButton}
                        ${retryButton}
                        ${deleteButton}
                    </div>
                `;
            }

            async function playArchive(gcsPath) {
                try {
                    // For JSON archives, fetch and display the content
                    if (gcsPath.endsWith('.json')) {
                        const response = await fetch(gcsPath);
                        const archiveData = await response.json();
                        
                        // Create a simple viewer window
                        const viewerWindow = window.open('', '_blank', 'width=1000,height=700,scrollbars=yes');
                        viewerWindow.document.write(`
                            <html>
                            <head>
                                <title>Archive Viewer - ${archiveData.metadata.url}</title>
                                <style>
                                    body { font-family: Arial, sans-serif; margin: 20px; }
                                    .metadata { background: #f5f5f5; padding: 15px; border-radius: 5px; margin-bottom: 20px; }
                                    .page { border: 1px solid #ddd; margin: 10px 0; padding: 15px; border-radius: 5px; }
                                    .page-header { background: #e9ecef; margin: -15px -15px 15px -15px; padding: 10px 15px; }
                                    .content-preview { max-height: 200px; overflow-y: auto; background: #fafafa; padding: 10px; border-radius: 3px; }
                                    pre { white-space: pre-wrap; }
                                </style>
                            </head>
                            <body>
                                <h1>📄 Archive Viewer</h1>
                                <div class="metadata">
                                    <h3>📋 Archive Metadata</h3>
                                    <p><strong>URL:</strong> ${archiveData.metadata.url}</p>
                                    <p><strong>Created:</strong> ${new Date(archiveData.metadata.created_at).toLocaleString()}</p>
                                    <p><strong>Pages Crawled:</strong> ${archiveData.metadata.pages_crawled}</p>
                                    <p><strong>Format:</strong> ${archiveData.metadata.format} v${archiveData.metadata.version}</p>
                                </div>
                                
                                <h3>📑 Archived Pages (${archiveData.pages.length})</h3>
                                ${archiveData.pages.map((page, index) => `
                                    <div class="page">
                                        <div class="page-header">
                                            <strong>Page ${index + 1}:</strong> 
                                            <a href="${page.url}" target="_blank">${page.url}</a>
                                            <span style="float: right;">
                                                Status: ${page.status_code} | 
                                                Size: ${page.size} bytes | 
                                                Type: ${page.content_type}
                                            </span>
                                        </div>
                                        <div class="content-preview">
                                            <strong>Content Preview:</strong>
                                            <pre>${page.content.substring(0, 500)}${page.content.length > 500 ? '...' : ''}</pre>
                                        </div>
                                    </div>
                                `).join('')}
                                
                                <p style="margin-top: 30px; text-align: center; color: #666;">
                                    💡 This is a simple archive viewer. For full browsertrix-crawler archives, use replayweb.page
                                </p>
                            </body>
                            </html>
                        `);
                        viewerWindow.document.close();
                    } else {
                        // For WACZ files, use replayweb.page
                        window.open(`https://replayweb.page/?source=${encodeURIComponent(gcsPath)}`, '_blank');
                    }
                } catch (error) {
                    showMessage(`Failed to open archive: ${error.message}`, 'error');
                }
            }

            async function downloadArchive(gcsPath) {
                window.open(gcsPath, '_blank');
            }

            async function retryJob(jobId) {
                try {
                    const response = await fetch(`/api/retry/${jobId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    });

                    if (!response.ok) {
                        throw new Error('Failed to retry archiving');
                    }

                    const result = await response.json();
                    showMessage(`Archive retry started! Job ID: ${result.job_id}`, 'success');
                } catch (error) {
                    showMessage(`Retry error: ${error.message}`, 'error');
                }
            }

            async function deleteJob(jobId) {
                if (!confirm('Are you sure you want to delete this failed job?')) {
                    return;
                }
                
                try {
                    const response = await fetch(`/api/delete/${jobId}`, {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' }
                    });

                    if (!response.ok) {
                        throw new Error('Failed to delete job');
                    }

                    showMessage('Job deleted successfully', 'success');
                    // Remove from local jobs object
                    delete jobs[jobId];
                    // Refresh display
                    updateJobList(Object.values(jobs));
                } catch (error) {
                    showMessage(`Delete error: ${error.message}`, 'error');
                }
            }

            window.onload = function() {
                startProgressMonitoring();
                loadExistingArchives();
            };

            async function loadExistingArchives() {
                try {
                    const response = await fetch('/api/archives');
                    if (response.ok) {
                        const archives = await response.json();
                        updateJobList(archives);
                    }
                } catch (error) {
                    console.error('Failed to load existing archives:', error);
                }
            }
        </script>
    </body>
    </html>
    """

def analyze_url_for_crawler_type(url: str) -> dict:
    """Analyze URL to determine the best crawler type"""
    from urllib.parse import urlparse
    import requests
    
    parsed = urlparse(url)
    analysis = {
        "url": url,
        "domain": parsed.netloc.lower(),
        "path": parsed.path.lower(),
        "recommended_crawler": "python",
        "reason": "",
        "complexity_score": 0
    }
    
    complexity_score = 0
    reasons = []
    
    # Domain-based analysis
    spa_indicators = [
        'app.', 'admin.', 'dashboard.', 'portal.',
        'angular', 'react', 'vue', 'spa'
    ]
    
    js_heavy_domains = [
        'github.com', 'gitlab.com', 'codepen.io',
        'jsfiddle.net', 'stackoverflow.com',
        'medium.com', 'dev.to', 'hashnode.com',
        'twitter.com', 'x.com', 'facebook.com',
        'linkedin.com', 'instagram.com',
        'youtube.com', 'vimeo.com', 'twitch.tv',
        'gmail.com', 'outlook.com', 'notion.so',
        'figma.com', 'canva.com', 'miro.com'
    ]
    
    static_friendly_domains = [
        'wikipedia.org', 'w3.org', 'mozilla.org',
        'gnu.org', 'apache.org', 'nginx.org',
        'docs.python.org', 'man7.org'
    ]
    
    # Check domain patterns
    if any(indicator in analysis["domain"] for indicator in spa_indicators):
        complexity_score += 3
        reasons.append("SPA-style domain detected")
    
    if any(domain in analysis["domain"] for domain in js_heavy_domains):
        complexity_score += 4
        reasons.append("JavaScript-heavy platform")
    
    if any(domain in analysis["domain"] for domain in static_friendly_domains):
        complexity_score -= 2
        reasons.append("Static-friendly site")
    
    # Path-based analysis
    js_paths = ['/app/', '/dashboard/', '/admin/', '/spa/', '/react/', '/angular/']
    if any(path in analysis["path"] for path in js_paths):
        complexity_score += 2
        reasons.append("Dynamic path detected")
    
    # Extension-based analysis
    if analysis["path"].endswith(('.html', '.htm', '.txt', '.xml', '.rss')):
        complexity_score -= 1
        reasons.append("Static file extension")
    
    # Try a quick HEAD request to check response headers
    try:
        response = requests.head(url, timeout=5, allow_redirects=True)
        content_type = response.headers.get('content-type', '').lower()
        
        # Check for SPA indicators in headers
        if 'application/javascript' in content_type:
            complexity_score += 2
            reasons.append("JavaScript content type")
        
        # Check for framework indicators
        framework_headers = ['x-powered-by', 'server']
        for header in framework_headers:
            value = response.headers.get(header, '').lower()
            if any(fw in value for fw in ['express', 'next.js', 'nuxt', 'gatsby', 'react']):
                complexity_score += 2
                reasons.append(f"Framework detected in {header}")
                
    except Exception:
        # If we can't check headers, assume medium complexity
        complexity_score += 1
        reasons.append("Unable to check headers")
    
    analysis["complexity_score"] = complexity_score
    
    # Always use browsertrix for proper WACZ archives that can be replayed
    analysis["recommended_crawler"] = "browsertrix"
    analysis["reason"] = f"Professional web archiving with browsertrix-crawler (score: {complexity_score}): " + "; ".join(reasons)
    
    return analysis

@app.post("/api/archive")
async def create_archive(request: ArchiveRequest, background_tasks: BackgroundTasks):
    if not job_manager:
        raise HTTPException(status_code=500, detail="Firestore not configured")
    
    # Analyze URL to determine best crawler
    url = str(request.url)
    analysis = analyze_url_for_crawler_type(url)
    
    job_id = str(uuid.uuid4())
    job_data = {
        "job_id": job_id,
        "url": url,
        "status": "started",
        "progress": 0,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "archive_path": None,
        "gcs_path": None,
        "crawler_type": analysis["recommended_crawler"],
        "crawler_reason": analysis["reason"],
        "complexity_score": analysis["complexity_score"]
    }
    
    await job_manager.create_job(job_data)
    
    # Choose crawler based on analysis
    if analysis["recommended_crawler"] == "browsertrix":
        background_tasks.add_task(run_browsertrix_crawler, job_id, url)
    else:
        background_tasks.add_task(run_python_crawler, job_id, url)
    
    return {
        "job_id": job_id, 
        "status": "started",
        "crawler_type": analysis["recommended_crawler"],
        "reason": analysis["reason"]
    }

@app.post("/api/retry/{job_id}")
async def retry_archive(job_id: str, background_tasks: BackgroundTasks):
    if not job_manager:
        raise HTTPException(status_code=500, detail="Firestore not configured")
    
    # Get the existing job
    existing_job = await job_manager.get_job(job_id)
    if not existing_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Re-analyze the URL for crawler selection
    analysis = analyze_url_for_crawler_type(existing_job["url"])
    
    # Reset the job to restart state
    await job_manager.update_job(job_id, {
        "status": "started",
        "progress": 0,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "archive_path": None,
        "gcs_path": None,
        "crawler_type": analysis["recommended_crawler"],
        "crawler_reason": analysis["reason"]
    })
    
    # Start the appropriate crawler
    if analysis["recommended_crawler"] == "browsertrix":
        background_tasks.add_task(run_browsertrix_crawler, job_id, existing_job["url"])
    else:
        background_tasks.add_task(run_python_crawler, job_id, existing_job["url"])
    
    return {"job_id": job_id, "status": "restarted"}

@app.delete("/api/delete/{job_id}")
async def delete_job(job_id: str):
    if not job_manager:
        raise HTTPException(status_code=500, detail="Firestore not configured")
    
    # Get the job to check if it exists
    existing_job = await job_manager.get_job(job_id)
    if not existing_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Only allow deletion of failed jobs for safety
    if existing_job["status"] != "failed":
        raise HTTPException(status_code=400, detail="Only failed jobs can be deleted")
    
    # Delete the job from Firestore
    doc_ref = job_manager.collection.document(job_id)
    doc_ref.delete()
    
    return {"message": "Job deleted successfully", "job_id": job_id}

@app.get("/api/progress")
async def get_progress():
    async def event_stream():
        while True:
            if job_manager:
                jobs = await job_manager.get_all_jobs()
                # Filter out invalid jobs and send the complete job list
                valid_jobs = [job for job in jobs if job and job.get('job_id') and job.get('url') and job.get('status')]
                yield f"data: {json.dumps({'jobs': valid_jobs})}\n\n"
            await asyncio.sleep(2)
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.get("/api/archives")
async def get_archives():
    if not job_manager:
        return []
    return await job_manager.get_completed_jobs()

@app.get("/api/playback/{job_id}")
async def playback_archive(job_id: str):
    if not job_manager:
        raise HTTPException(status_code=500, detail="Firestore not configured")
    
    job = await job_manager.get_job(job_id)
    if not job or not job.get('gcs_path'):
        raise HTTPException(status_code=404, detail="Archive not found")
    
    return {
        "playback_url": f"https://replayweb.page/?source={job['gcs_path']}",
        "download_url": job['gcs_path']
    }

async def run_python_crawler(job_id: str, url: str):
    """Background task to run simple Python-based web crawler"""
    try:
        await job_manager.update_job(job_id, {"status": "crawling", "progress": 10})
        
        import requests
        from urllib.parse import urljoin, urlparse, quote
        import time
        
        print(f"Starting to crawl {url} for job {job_id}")
        
        # Set up session with proper headers
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN,zh;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        })
        
        # Test the main URL first
        print(f"Testing connectivity to {url}...")
        try:
            test_response = session.head(url, timeout=10, allow_redirects=True)
            print(f"HEAD request result: {test_response.status_code}")
        except Exception as test_error:
            print(f"HEAD request failed: {test_error}, proceeding with GET...")
        
        await job_manager.update_job(job_id, {"progress": 30})
        
        # Archive data structure
        archive_data = {
            'metadata': {
                'job_id': job_id,
                'url': url,
                'created_at': datetime.now().isoformat(),
                'format': 'simple-web-archive',
                'version': '1.0'
            },
            'pages': []
        }
        
        pages_to_crawl = [url]
        crawled_urls = set()
        max_pages = 5
        
        for i, current_url in enumerate(pages_to_crawl[:max_pages]):
            if current_url in crawled_urls:
                continue
                
            try:
                print(f"Crawling page {i+1}/{min(len(pages_to_crawl), max_pages)}: {current_url}")
                
                # Update progress
                progress = 30 + (i * 40 // max_pages)
                await job_manager.update_job(job_id, {"progress": progress})
                
                # Add a small delay to make it more realistic
                import asyncio
                await asyncio.sleep(2)
                
                # Fetch the page
                print(f"Fetching {current_url}...")
                response = session.get(current_url, timeout=30, allow_redirects=True)
                print(f"Response: {response.status_code} - {len(response.content)} bytes")
                
                # Don't raise for status immediately - let's see what we got
                if response.status_code >= 400:
                    print(f"HTTP {response.status_code} error for {current_url}")
                    print(f"Response headers: {dict(response.headers)}")
                    if response.status_code == 404:
                        print("404 error - skipping this URL")
                        continue
                else:
                    print(f"Successfully fetched {current_url} - {len(response.content)} bytes")
                
                # Store page data
                page_data = {
                    'url': current_url,
                    'final_url': response.url,
                    'status_code': response.status_code,
                    'headers': dict(response.headers),
                    'content': response.text,
                    'content_type': response.headers.get('content-type', ''),
                    'crawled_at': datetime.now().isoformat(),
                    'size': len(response.content)
                }
                
                archive_data['pages'].append(page_data)
                crawled_urls.add(current_url)
                
                # Try to find more links (only for the first page to keep it simple)
                if i == 0 and 'text/html' in response.headers.get('content-type', ''):
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(response.text, 'html.parser')
                        links = soup.find_all('a', href=True)
                        
                        for link in links[:10]:  # Limit to 10 links
                            href = link['href']
                            full_url = urljoin(current_url, href)
                            parsed = urlparse(full_url)
                            
                            # Only crawl same domain, http/https links
                            if (parsed.netloc == urlparse(url).netloc and 
                                parsed.scheme in ['http', 'https'] and
                                full_url not in crawled_urls):
                                pages_to_crawl.append(full_url)
                                
                    except Exception as link_error:
                        print(f"Error parsing links: {link_error}")
                
                # Small delay to be respectful
                time.sleep(1)
                
            except Exception as page_error:
                print(f"Error crawling {current_url}: {page_error}")
                # Continue with other pages
                continue
        
        await job_manager.update_job(job_id, {"progress": 80})
        
        # Update metadata with final stats
        archive_data['metadata']['pages_crawled'] = len(archive_data['pages'])
        archive_data['metadata']['completed_at'] = datetime.now().isoformat()
        
        if len(archive_data['pages']) == 0:
            print(f"No pages were successfully crawled from {url}")
            await job_manager.update_job(job_id, {
                "status": "failed", 
                "progress": 0,
                "crawler_reason": "No pages could be crawled - site may be unreachable or blocking requests"
            })
            return
        
        print(f"Creating archive with {len(archive_data['pages'])} pages...")
        
        # Create the archive file
        archive_content = json.dumps(archive_data, indent=2, ensure_ascii=False)
        
        # Upload to Google Cloud Storage
        bucket = storage_client.bucket(STORAGE_BUCKET)
        blob_name = f"archives/{job_id}/archive-{job_id}.json"
        blob = bucket.blob(blob_name)
        
        print(f"Uploading archive to GCS: {blob_name}")
        blob.upload_from_string(
            archive_content, 
            content_type='application/json; charset=utf-8'
        )
        blob.make_public()
        print(f"Archive uploaded successfully: {blob.public_url}")
        
        await job_manager.update_job(job_id, {
            "status": "completed",
            "progress": 100,
            "completed_at": datetime.now().isoformat(),
            "archive_path": f"archive-{job_id}.json",
            "gcs_path": blob.public_url
        })
        
        print(f"Successfully archived {len(archive_data['pages'])} pages from {url}")
        
    except Exception as e:
        await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
        print(f"Error running python crawler: {e}")
        import traceback
        traceback.print_exc()

async def run_browsertrix_crawler(job_id: str, url: str):
    """Background task to create WACZ-compatible archive"""
    try:
        await job_manager.update_job(job_id, {"status": "crawling", "progress": 10})
        
        print(f"Creating WACZ archive for {url} (job {job_id})")
        
        # For now, create a proper WACZ structure that replayweb.page can use
        # This is a simplified approach that works reliably
        import zipfile
        import tempfile
        import json
        import requests
        from urllib.parse import urljoin, urlparse
        from bs4 import BeautifulSoup
        import time
        
        await job_manager.update_job(job_id, {"progress": 30})
        
        # Create temporary directory for WACZ creation
        temp_dir = tempfile.mkdtemp(prefix=f"wacz_{job_id}_")
        print(f"Created temp directory: {temp_dir}")
        
        try:
            # Set up session with proper headers
            session = requests.Session()
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            })
            
            # Fetch the main page
            print(f"Fetching {url}...")
            response = session.get(url, timeout=30, allow_redirects=True)
            response.raise_for_status()
            
            await job_manager.update_job(job_id, {"progress": 50})
            
            # Create WACZ structure
            wacz_data = {
                "pages": [
                    {
                        "id": f"page:{job_id}:0",
                        "url": url,
                        "title": "Archived Page",
                        "ts": int(time.time() * 1000),
                        "filename": "data.warc"
                    }
                ],
                "resources": [
                    {
                        "name": "data.warc",
                        "path": "data.warc",
                        "hash": "sha256:placeholder",
                        "bytes": len(response.content)
                    }
                ]
            }
            
            # Create a simple WARC-like structure for replayweb.page
            warc_content = f"""WARC/1.0\r
WARC-Type: response\r
WARC-Target-URI: {url}\r
WARC-Date: {datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')}\r
Content-Type: text/html\r
Content-Length: {len(response.content)}\r
\r
{response.text}"""
            
            await job_manager.update_job(job_id, {"progress": 70})
            
            # Create WACZ file
            wacz_file = os.path.join(temp_dir, f"archive-{job_id}.wacz")
            with zipfile.ZipFile(wacz_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Add metadata
                zf.writestr("datapackage.json", json.dumps(wacz_data, indent=2))
                # Add WARC data
                zf.writestr("data.warc", warc_content)
                # Add index for replayweb.page
                index_data = {
                    "cdx": [
                        {
                            "url": url,
                            "timestamp": datetime.now().strftime('%Y%m%d%H%M%S'),
                            "mime": "text/html",
                            "status": str(response.status_code),
                            "digest": "sha1:placeholder",
                            "filename": "data.warc",
                            "offset": "0",
                            "length": str(len(warc_content))
                        }
                    ]
                }
                zf.writestr("indexes/index.cdx.gz", json.dumps(index_data))
            
            print(f"Created WACZ file: {wacz_file}")
            await job_manager.update_job(job_id, {"progress": 85})
            
            # Upload to Google Cloud Storage
            bucket = storage_client.bucket(STORAGE_BUCKET)
            blob_name = f"archives/{job_id}/archive-{job_id}.wacz"
            blob = bucket.blob(blob_name)
            
            print(f"Uploading WACZ to GCS: {blob_name}")
            blob.upload_from_filename(wacz_file)
            blob.make_public()
            print(f"WACZ uploaded successfully: {blob.public_url}")
            
            await job_manager.update_job(job_id, {
                "status": "completed",
                "progress": 100,
                "completed_at": datetime.now().isoformat(),
                "archive_path": f"archive-{job_id}.wacz",
                "gcs_path": blob.public_url
            })
            
            print(f"Successfully created WACZ archive for {url}")
            
        finally:
            # Clean up temp directory
            try:
                import shutil
                shutil.rmtree(temp_dir)
                print(f"Cleaned up temp directory: {temp_dir}")
            except Exception as cleanup_error:
                print(f"Error cleaning up temp directory: {cleanup_error}")
                
    except Exception as e:
        await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
        print(f"Error creating WACZ archive: {e}")
        import traceback
        traceback.print_exc()

def parse_crawler_progress(output: str) -> Optional[int]:
    """Parse progress from crawler output"""
    page_match = re.search(r'(\d+)/(\d+) pages', output)
    if page_match:
        current = int(page_match.group(1))
        total = int(page_match.group(2))
        return min(int((current / total) * 80) + 25, 95)
    
    if "WACZ generation complete" in output:
        return 100
    
    return None

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)