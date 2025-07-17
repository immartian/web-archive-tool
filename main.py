from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
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
import sqlite3
import docker
import aiofiles
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = FastAPI(title="Web Archive API - Local Docker")

# Add CORS middleware for replayweb.page integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Local configuration
ARCHIVE_DIR = os.getenv("ARCHIVE_DIR", "./archives")
DB_PATH = os.getenv("DB_PATH", "./data/archives.db")
PORT = int(os.getenv("PORT", 8080))

def cleanup_orphaned_containers():
    """Clean up any browsertrix containers that may be running from previous sessions"""
    if not docker_client:
        return
        
    try:
        # Find all running browsertrix containers
        containers = docker_client.containers.list(
            filters={"ancestor": "webrecorder/browsertrix-crawler:latest"}
        )
        
        for container in containers:
            try:
                # Stop and remove the container
                container.stop(timeout=10)
                container.remove()
            except Exception as e:
                # Container might already be stopped/removed
                pass
                
    except Exception as e:
        pass

async def cleanup_orphaned_jobs():
    """Update job status for jobs that were running when the app restarted"""
    try:
        # Initialize a temporary job manager to update orphaned jobs
        temp_job_manager = SQLiteJobManager(DB_PATH)
        
        # Find all jobs that were in active states
        all_jobs = await temp_job_manager.get_all_jobs()
        active_statuses = ["started", "crawling", "preparing", "uploading_gcs"]
        
        for job in all_jobs:
            if job.get("status") in active_statuses:
                # Mark as stopped since containers were cleaned up
                await temp_job_manager.update_job(job["job_id"], {
                    "status": "stopped",
                    "completed_at": datetime.now().isoformat()
                })
                
    except Exception as e:
        pass

# Initialize Docker client
docker_client = None
try:
    # Simply try to create a Docker client with the socket path
    docker_client = docker.DockerClient(base_url='unix:///var/run/docker.sock')
    # Test the connection
    docker_client.ping()
    
    # Clean up any orphaned browsertrix containers on startup - but only if they've been running for a very long time
    # cleanup_orphaned_containers()  # Disabled for now - too aggressive
    
    # Update any jobs that were running when the app restarted
    # asyncio.run(cleanup_orphaned_jobs())  # Disabled for now
    
except Exception as e:
    pass
    docker_client = None

# Ensure archive directory exists
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS archive_jobs (
            job_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            archive_path TEXT,
            local_path TEXT,
            crawler_type TEXT,
            crawler_reason TEXT,
            complexity_score INTEGER DEFAULT 0,
            gcs_url TEXT
        )
    ''')
    
    # Add gcs_url column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE archive_jobs ADD COLUMN gcs_url TEXT')
    except sqlite3.OperationalError:
        # Column already exists
        pass
    
    # Add gcs_error column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE archive_jobs ADD COLUMN gcs_error TEXT')
    except sqlite3.OperationalError:
        # Column already exists
        pass
    
    # Add pages_archived column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE archive_jobs ADD COLUMN pages_archived INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        # Column already exists
        pass
    
    # Add current_depth column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE archive_jobs ADD COLUMN current_depth INTEGER DEFAULT 1')
    except sqlite3.OperationalError:
        # Column already exists
        pass
    
    conn.commit()
    conn.close()

init_db()

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
    local_path: Optional[str] = None

class SQLiteJobManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    async def create_job(self, job_data: dict) -> str:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO archive_jobs 
            (job_id, url, status, progress, created_at, completed_at, archive_path, local_path, crawler_type, crawler_reason, complexity_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            job_data['job_id'], job_data['url'], job_data['status'], job_data['progress'],
            job_data['created_at'], job_data.get('completed_at'), job_data.get('archive_path'),
            job_data.get('local_path'), job_data.get('crawler_type'), job_data.get('crawler_reason'),
            job_data.get('complexity_score', 0)
        ))
        conn.commit()
        conn.close()
        return job_data['job_id']
    
    async def update_job(self, job_id: str, updates: dict):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        set_clause = ', '.join([f'{key} = ?' for key in updates.keys()])
        values = list(updates.values()) + [job_id]
        
        cursor.execute(f'UPDATE archive_jobs SET {set_clause} WHERE job_id = ?', values)
        conn.commit()
        conn.close()
    
    async def get_job(self, job_id: str) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM archive_jobs WHERE job_id = ?', (job_id,))
        row = cursor.fetchone()
        
        if row:
            columns = [desc[0] for desc in cursor.description]
            conn.close()
            return dict(zip(columns, row))
        conn.close()
        return None
    
    async def get_all_jobs(self) -> List[dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM archive_jobs ORDER BY created_at DESC')
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        conn.close()
        
        return [dict(zip(columns, row)) for row in rows]
    
    async def get_completed_jobs(self) -> List[dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM archive_jobs WHERE status = ? ORDER BY created_at DESC', ('completed',))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        conn.close()
        
        return [dict(zip(columns, row)) for row in rows]
    
    async def delete_job(self, job_id: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM archive_jobs WHERE job_id = ?', (job_id,))
        conn.commit()
        conn.close()

# Initialize job manager
job_manager = SQLiteJobManager(DB_PATH)

class LocalStorageManager:
    def __init__(self, archive_dir: str):
        self.archive_dir = archive_dir
        os.makedirs(archive_dir, exist_ok=True)
    
    async def save_archive(self, content: str, job_id: str, filename: str) -> str:
        """Save archive to local storage and return path"""
        job_dir = os.path.join(self.archive_dir, job_id)
        os.makedirs(job_dir, exist_ok=True)
        
        file_path = os.path.join(job_dir, filename)
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(content)
        
        return file_path
    
    async def save_binary_archive(self, file_path: str, job_id: str, filename: str) -> str:
        """Copy binary archive to local storage"""
        job_dir = os.path.join(self.archive_dir, job_id)
        os.makedirs(job_dir, exist_ok=True)
        
        dest_path = os.path.join(job_dir, filename)
        shutil.copy2(file_path, dest_path)
        
        return dest_path
    
    async def list_archives(self, job_id: str) -> List[str]:
        """List all archives for a job"""
        job_dir = os.path.join(self.archive_dir, job_id)
        if not os.path.exists(job_dir):
            return []
        
        return [f for f in os.listdir(job_dir) if os.path.isfile(os.path.join(job_dir, f))]

# Initialize storage manager
storage_manager = LocalStorageManager(ARCHIVE_DIR)

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
            .job a { 
                color: #007bff; 
                text-decoration: none; 
                border-bottom: 1px dotted #007bff;
            }
            .job a:hover { 
                color: #0056b3; 
                text-decoration: underline;
                border-bottom: 1px solid #0056b3;
            }
            .error { color: red; }
            .success { color: green; }
            .status-stopped { color: #ff9500; font-weight: bold; }
            .status-failed { color: #dc3545; font-weight: bold; }
            .status-completed { color: #28a745; font-weight: bold; }
            .status-active { color: #007bff; font-weight: bold; }
            .cloud-badge { 
                background: #4285f4; 
                color: white; 
                padding: 2px 8px; 
                border-radius: 12px; 
                font-size: 12px; 
                margin-left: 10px; 
            }
            .gcs-upload-btn { 
                background: #4285f4; 
                color: white; 
                margin-left: 10px; 
            }
            .gcs-upload-btn:hover { 
                background: #3367d6; 
            }
        </style>
    </head>
    <body>
        <h1>Web Archive Tool <span class="cloud-badge">Local Docker</span></h1>
        
        <div class="container">
            <h2>Archive a Website</h2>
            <input type="url" id="urlInput" placeholder="Enter website URL (e.g., https://example.com)" />
            <button onclick="startArchive()">Archive</button>
            <div id="message"></div>
        </div>

        <div class="container">
            <h2>Jobs</h2>
            <div id="allJobs"></div>
        </div>

        <script>
            let eventSource = null;
            let jobs = {};

            function showMessage(text, type = 'info') {
                const messageDiv = document.getElementById('message');
                messageDiv.innerHTML = `<div class="${type}">${text}</div>`;
                
                // Auto-hide success messages after different timeouts
                if (type === 'success') {
                    if (text.includes('What happens next:')) {
                        // Hide detailed GCS upload message after 15 seconds
                        setTimeout(() => messageDiv.innerHTML = '', 15000);
                    } else {
                        // Hide brief success messages after 5 seconds
                        setTimeout(() => messageDiv.innerHTML = '', 5000);
                    }
                }
                // Keep error messages visible (no auto-hide for errors)
            }

            async function startArchive() {
                const url = document.getElementById('urlInput').value;
                if (!url) {
                    showMessage('Please enter a URL', 'error');
                    return;
                }

                // Show loading state
                const archiveButton = document.querySelector('button[onclick="startArchive()"]');
                const originalText = archiveButton.textContent;
                archiveButton.textContent = '⏳ Creating...';
                archiveButton.disabled = true;

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
                    showMessage(`📋 Archive job created! Check "Active Jobs" below for progress. Job ID: ${result.job_id}`, 'success');
                    document.getElementById('urlInput').value = '';
                    
                    // Immediately load and display the new job
                    await loadExistingArchives();
                    startProgressMonitoring();
                } catch (error) {
                    showMessage(`Error: ${error.message}`, 'error');
                } finally {
                    // Restore button state
                    archiveButton.textContent = originalText;
                    archiveButton.disabled = false;
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
                
                const allJobsDiv = document.getElementById('allJobs');
                allJobsDiv.innerHTML = '';
                
                // Sort jobs by created_at (newest first)
                const sortedJobs = Object.values(jobs).sort((a, b) => {
                    return new Date(b.created_at) - new Date(a.created_at);
                });
                
                sortedJobs.forEach(job => {
                    const jobHTML = createJobHTML(job);
                    allJobsDiv.innerHTML += jobHTML;
                });
                // Job list updated
            }

            function getStatusClass(status) {
                if (status === 'completed') return 'completed';
                if (status === 'failed') return 'failed';
                if (status === 'stopped') return 'stopped';
                return 'active';
            }

            function createJobHTML(job) {
                // Ensure all required fields exist
                if (!job || !job.job_id || !job.url || !job.status) {
                    return '';
                }
                
                const progressWidth = job.progress || 0;
                
                // Only show play button when GCS URL is available
                let playbackButton = '';
                if (job.status === 'completed' && job.gcs_url) {
                    playbackButton = `<button class="playback-btn" onclick="playArchiveGCS('${job.gcs_url}')">📺 Play Online</button>`;
                }
                
                const downloadButton = job.status === 'completed' && job.local_path ? 
                    `<button class="download-btn" onclick="downloadArchive('${job.local_path}')">⬇️ Download</button>` : '';
                
                // GCS upload button for completed jobs without GCS URL
                let gcsUploadButton = '';
                if (job.status === 'completed' && job.local_path && !job.gcs_url) {
                    gcsUploadButton = `<button class="gcs-upload-btn" onclick="uploadToGCS('${job.job_id}')">☁️ Upload to Cloud</button>`;
                } else if (job.status === 'uploading_gcs') {
                    gcsUploadButton = `<button class="gcs-upload-btn" disabled>☁️ Uploading...</button>`;
                } else if (job.status === 'gcs_upload_failed') {
                    gcsUploadButton = `<button class="retry-btn" onclick="uploadToGCS('${job.job_id}')">☁️ Retry Upload</button>`;
                }
                
                // Show GCS status for uploaded archives
                let gcsStatus = '';
                if (job.gcs_url) {
                    gcsStatus = `<div><strong>Cloud:</strong> ✅ Available for online viewing</div>`;
                } else if (job.status === 'uploading_gcs') {
                    gcsStatus = `<div><strong>Cloud:</strong> 🔄 Uploading to cloud storage...</div>`;
                } else if (job.status === 'gcs_upload_failed') {
                    const errorMsg = job.gcs_error || 'Upload failed';
                    gcsStatus = `<div><strong>Cloud:</strong> ❌ Upload failed - ${errorMsg}</div>`;
                }
                
                // Action buttons based on job status
                let actionButtons = '';
                if (job.status === 'failed') {
                    actionButtons = `<button class="retry-btn" onclick="retryJob('${job.job_id}')">🔄 Retry</button>`;
                } else if (['started', 'crawling', 'preparing', 'uploading_gcs'].includes(job.status)) {
                    actionButtons = `<button class="delete-btn" onclick="stopJob('${job.job_id}')">⏹️ Stop</button>`;
                }
                
                // Delete button for non-active jobs
                const deleteButton = ['failed', 'completed', 'gcs_upload_failed', 'stopped'].includes(job.status) ? 
                    `<button class="delete-btn" onclick="deleteArchive('${job.job_id}')">🗑️ Delete</button>` : '';
                
                const startedDate = job.created_at ? new Date(job.created_at).toLocaleString() : 'Unknown';
                const completedDate = job.completed_at ? new Date(job.completed_at).toLocaleString() : null;
                
                // Crawler type badge
                const crawlerBadge = job.crawler_type ? 
                    `<span class="crawler-badge crawler-${job.crawler_type}">${job.crawler_type.toUpperCase()}</span>` : '';
                
                const crawlerReason = job.crawler_reason ? 
                    `<div class="crawler-reason"><small>📋 ${job.crawler_reason}</small></div>` : '';
                
                // Create clickable URL
                const clickableUrl = `<a href="${job.url}" target="_blank" rel="noopener noreferrer" style="color: #007bff; text-decoration: none;">${job.url}</a>`;
                
                // Add page count and depth information
                let crawlInfo = '';
                if (job.pages_archived > 0 || job.current_depth > 0) {
                    const pages = job.pages_archived || 0;
                    const depth = job.current_depth || 1;
                    const maxDepth = 4; // Our configured max depth
                    
                    crawlInfo = `<div><strong>Crawling:</strong> ${pages} pages • Level ${depth}/${maxDepth}</div>`;
                }

                return `
                    <div class="job">
                        <div><strong>URL:</strong> ${clickableUrl} ${crawlerBadge}</div>
                        <div class="status"><strong>Status:</strong> <span class="status-${getStatusClass(job.status)}">${job.status}</span></div>
                        <div class="progress">
                            <div class="progress-bar" style="width: ${progressWidth}%"></div>
                        </div>
                        <div>Progress: ${progressWidth}%</div>
                        ${crawlInfo}
                        <div><strong>Started:</strong> ${startedDate}</div>
                        ${completedDate ? `<div><strong>Completed:</strong> ${completedDate}</div>` : ''}
                        ${job.local_path ? `<div><strong>Storage:</strong> Local Storage</div>` : ''}
                        ${gcsStatus}
                        ${crawlerReason}
                        ${playbackButton}
                        ${gcsUploadButton}
                        ${downloadButton}
                        ${actionButtons}
                        ${deleteButton}
                    </div>
                `;
            }

            async function playArchive(localPath) {
                try {
                    // For local development, download and use local replayweb.page
                    // Check if we're on localhost or any non-public domain
                    const isLocalhost = window.location.hostname === 'localhost' || 
                                      window.location.hostname === '127.0.0.1' || 
                                      window.location.hostname === '0.0.0.0' ||
                                      window.location.hostname.startsWith('192.168.') ||
                                      window.location.hostname.startsWith('10.') ||
                                      window.location.hostname.startsWith('172.');
                    
                    if (isLocalhost) {
                        // Show instructions for local viewing
                        const archiveUrl = `${window.location.origin}/api/serve/${localPath}`;
                        const message = `
                            <div style="margin: 10px 0;">
                                <p><strong>To view this archive locally:</strong></p>
                                <ol style="text-align: left; max-width: 500px; margin: 0 auto;">
                                    <li>Download the WACZ file: <button onclick="downloadArchive('${localPath}')" style="margin-left: 5px;">Download</button></li>
                                    <li>Go to <a href="https://replayweb.page/" target="_blank">replayweb.page</a></li>
                                    <li>Click "Choose File" and select your downloaded WACZ file</li>
                                    <li>Click "Start Exploring!" to view the archive</li>
                                </ol>
                                <p style="font-size: 12px; color: #666; margin-top: 10px;">
                                    <strong>Why download?</strong> replayweb.page (HTTPS) cannot directly access localhost URLs (HTTP) for security reasons.<br>
                                    <strong>Alternative:</strong> Use the "Upload to Cloud" button to make archives accessible online.
                                </p>
                            </div>
                        `;
                        showMessage(message, 'info');
                    } else {
                        // For production URLs, use direct replayweb.page integration
                        const archiveUrl = `${window.location.origin}/api/serve/${localPath}`;
                        const replayUrl = `https://replayweb.page/?source=${encodeURIComponent(archiveUrl)}`;
                        window.open(replayUrl, '_blank');
                    }
                } catch (error) {
                    showMessage(`Failed to open archive: ${error.message}`, 'error');
                }
            }

            async function downloadArchive(localPath) {
                window.open(`/api/download/${localPath}`, '_blank');
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

            async function stopJob(jobId) {
                if (!confirm('Are you sure you want to stop this job?')) {
                    return;
                }
                
                try {
                    const response = await fetch(`/api/stop/${jobId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    });

                    if (!response.ok) {
                        throw new Error('Failed to stop job');
                    }

                    showMessage('Job stopped successfully', 'success');
                    // Remove from local jobs object
                    delete jobs[jobId];
                    // Refresh display
                    updateJobList(Object.values(jobs));
                } catch (error) {
                    showMessage(`Stop error: ${error.message}`, 'error');
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

            async function deleteArchive(jobId) {
                if (!confirm('Are you sure you want to permanently delete this archive? This will remove it from the database, local storage, and cloud storage.')) {
                    return;
                }
                
                try {
                    const response = await fetch(`/api/delete-archive/${jobId}`, {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' }
                    });

                    if (!response.ok) {
                        const errorData = await response.json();
                        throw new Error(errorData.detail || 'Failed to delete archive');
                    }

                    showMessage('Archive deleted successfully from all locations', 'success');
                    // Remove from local jobs object
                    delete jobs[jobId];
                    // Refresh display
                    updateJobList(Object.values(jobs));
                } catch (error) {
                    showMessage(`Delete error: ${error.message}`, 'error');
                }
            }

            async function uploadToGCS(jobId) {
                try {
                    showMessage('Starting cloud upload...', 'info');
                    
                    const response = await fetch(`/api/upload-gcs/${jobId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    });

                    if (!response.ok) {
                        const errorData = await response.json();
                        throw new Error(errorData.detail || 'Failed to upload to cloud');
                    }

                    const result = await response.json();
                    showMessage('☁️ Uploading to cloud storage...', 'success');
                } catch (error) {
                    showMessage(`Upload error: ${error.message}`, 'error');
                }
            }

            async function playArchiveGCS(gcsUrl) {
                try {
                    // Use proxy URL instead of direct GCS URL for better compatibility
                    // Extract job_id from the gcs_url to build proxy URL
                    // GCS URL format: .../archives/JOB_ID/archive-JOB_ID.wacz
                    const jobIdMatch = gcsUrl.match(/\/archives\/([^\/]+)\/archive-/);
                    if (jobIdMatch) {
                        const jobId = jobIdMatch[1];
                        // Try direct GCS URL first since proxy has issues
                        const replayUrl = `https://replayweb.page/?source=${encodeURIComponent(gcsUrl)}`;
                        window.open(replayUrl, '_blank');
                    } else {
                        // Fallback to direct GCS URL
                        const replayUrl = `https://replayweb.page/?source=${encodeURIComponent(gcsUrl)}`;
                        window.open(replayUrl, '_blank');
                    }
                } catch (error) {
                    showMessage(`Failed to open archive: ${error.message}`, 'error');
                }
            }

            window.onload = function() {
                startProgressMonitoring();
                loadExistingArchives();
            };

            async function loadExistingArchives() {
                try {
                    const response = await fetch('/api/jobs');
                    if (response.ok) {
                        const jobs = await response.json();
                        updateJobList(jobs);
                    }
                } catch (error) {
                    // Failed to load existing jobs
                }
            }
        </script>
    </body>
    </html>
    """

def analyze_url_for_crawler_type(url: str) -> dict:
    """Always use browsertrix-crawler for professional web archiving"""
    from urllib.parse import urlparse
    
    parsed = urlparse(url)
    analysis = {
        "url": url,
        "domain": parsed.netloc.lower(),
        "path": parsed.path.lower(),
        "recommended_crawler": "browsertrix",
        "reason": "High-quality web archiving",
        "complexity_score": 1
    }
    
    return analysis

@app.post("/api/archive")
async def create_archive(request: ArchiveRequest, background_tasks: BackgroundTasks):
    
    # Check if Docker is available
    if not docker_client:
        raise HTTPException(
            status_code=503, 
            detail="Docker is not available. Please ensure Docker is running and accessible."
        )
    
    # Always use browsertrix-crawler
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
        "local_path": None,
        "crawler_type": "browsertrix",
        "crawler_reason": analysis["reason"],
        "complexity_score": analysis["complexity_score"],
        "pages_archived": 0,
        "current_depth": 1
    }
    
    await job_manager.create_job(job_data)
    
    # Give the EventSource a moment to catch the "started" status
    await asyncio.sleep(0.5)
    
    # Always use browsertrix-crawler
    background_tasks.add_task(run_browsertrix_crawler, job_id, url)
    
    return {
        "job_id": job_id, 
        "status": "started",
        "crawler_type": "browsertrix",
        "reason": analysis["reason"]
    }

@app.post("/api/retry/{job_id}")
async def retry_archive(job_id: str, background_tasks: BackgroundTasks):
    
    # Check if Docker is available
    if not docker_client:
        raise HTTPException(
            status_code=503, 
            detail="Docker is not available. Please ensure Docker is running and accessible."
        )
    
    # Get the existing job
    existing_job = await job_manager.get_job(job_id)
    if not existing_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Reset the job to restart state
    await job_manager.update_job(job_id, {
        "status": "started",
        "progress": 0,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "archive_path": None,
        "local_path": None,
        "crawler_type": "browsertrix",
        "crawler_reason": "Professional web archiving with browsertrix-crawler"
    })
    
    # Always use browsertrix-crawler
    background_tasks.add_task(run_browsertrix_crawler, job_id, existing_job["url"])
    
    return {"job_id": job_id, "status": "restarted"}

@app.post("/api/stop/{job_id}")
async def stop_job(job_id: str):
    """Stop an active job"""
    
    # Get the job to check if it exists
    existing_job = await job_manager.get_job(job_id)
    if not existing_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Only allow stopping of active jobs
    if existing_job["status"] not in ["started", "crawling", "preparing", "uploading_gcs"]:
        raise HTTPException(status_code=400, detail="Only active jobs can be stopped")
    
    # Update job status to stopped
    await job_manager.update_job(job_id, {
        "status": "stopped",
        "completed_at": datetime.now().isoformat()
    })
    
    return {"message": "Job stopped successfully", "job_id": job_id}

@app.delete("/api/delete/{job_id}")
async def delete_job(job_id: str):
    
    # Get the job to check if it exists
    existing_job = await job_manager.get_job(job_id)
    if not existing_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Only allow deletion of failed jobs for safety
    if existing_job["status"] != "failed":
        raise HTTPException(status_code=400, detail="Only failed jobs can be deleted")
    
    # Delete the job from database
    await job_manager.delete_job(job_id)
    
    return {"message": "Job deleted successfully", "job_id": job_id}

@app.delete("/api/delete-archive/{job_id}")
async def delete_archive(job_id: str):
    """Delete archive from database, local storage, and cloud storage"""
    
    # Get the job to check if it exists
    existing_job = await job_manager.get_job(job_id)
    if not existing_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Only allow deletion of completed, failed, or stopped jobs for safety
    if existing_job["status"] not in ["completed", "failed", "gcs_upload_failed", "stopped"]:
        raise HTTPException(status_code=400, detail="Only completed, failed, or stopped jobs can be deleted")
    
    results = {
        "database": False,
        "local_file": False,
        "gcs_file": False,
        "errors": []
    }
    
    # 1. Delete from local file system
    if existing_job.get("local_path"):
        try:
            import os
            import shutil
            
            # Build full path to the file
            full_file_path = os.path.join("archives", existing_job["local_path"])
            
            # Get the job directory (parent directory of the file)
            job_directory = os.path.dirname(full_file_path)
            
            if os.path.exists(job_directory):
                # Remove the entire job directory and all its contents
                shutil.rmtree(job_directory)
                results["local_file"] = True
                pass
            else:
                results["local_file"] = True  # Directory doesn't exist, consider it deleted
        except Exception as e:
            results["errors"].append(f"Failed to delete local directory: {str(e)}")
    
    # 2. Delete from Google Cloud Storage
    if existing_job.get("gcs_url"):
        try:
            import google.cloud.storage
            import os
            from urllib.parse import urlparse
            
            # Parse GCS URL to get bucket and object name
            # Format: https://storage.googleapis.com/bucket/path/to/file
            parsed_url = urlparse(existing_job["gcs_url"])
            path_parts = parsed_url.path.strip('/').split('/')
            bucket_name = path_parts[0]
            object_name = '/'.join(path_parts[1:])
            
            client = google.cloud.storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            
            if blob.exists():
                blob.delete()
                results["gcs_file"] = True
            else:
                results["gcs_file"] = True  # File doesn't exist, consider it deleted
                
        except Exception as e:
            results["errors"].append(f"Failed to delete GCS file: {str(e)}")
    else:
        results["gcs_file"] = True  # No GCS file to delete
    
    # 3. Delete from database (do this last in case of errors above)
    try:
        await job_manager.delete_job(job_id)
        results["database"] = True
    except Exception as e:
        results["errors"].append(f"Failed to delete from database: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to delete from database: {str(e)}")
    
    # Check if all deletions were successful
    success = results["database"] and results["local_file"] and results["gcs_file"]
    
    response = {
        "message": "Archive deletion completed",
        "job_id": job_id,
        "success": success,
        "results": results
    }
    
    if not success:
        response["message"] = "Archive deletion partially completed with errors"
    
    return response

@app.post("/api/upload-gcs/{job_id}")
async def upload_to_gcs(job_id: str, background_tasks: BackgroundTasks):
    """Upload WACZ archive to Google Cloud Storage for replayweb.page access"""
    
    # Get the job to check if it exists and is completed
    existing_job = await job_manager.get_job(job_id)
    if not existing_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if existing_job["status"] not in ["completed", "gcs_upload_failed"]:
        raise HTTPException(status_code=400, detail="Only completed jobs can be uploaded to GCS")
    
    if existing_job.get("gcs_url"):
        raise HTTPException(status_code=400, detail="Archive already uploaded to GCS")
    
    if not existing_job.get("local_path"):
        raise HTTPException(status_code=400, detail="No local archive file found")
    
    # Check if GCS is configured before starting
    try:
        import google.cloud.storage
        import os
        if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS") and not os.getenv("GCS_BUCKET"):
            raise HTTPException(
                status_code=503, 
                detail="Google Cloud Storage is not configured. Please set GOOGLE_APPLICATION_CREDENTIALS and GCS_BUCKET environment variables."
            )
    except ImportError:
        raise HTTPException(
            status_code=503, 
            detail="Google Cloud Storage library not installed. Run: pip install google-cloud-storage"
        )
    
    # Start GCS upload in background
    background_tasks.add_task(upload_archive_to_gcs, job_id, existing_job["local_path"])
    
    return {"message": "GCS upload started", "job_id": job_id}

@app.get("/api/progress")
async def get_progress():
    async def event_stream():
        while True:
            jobs = await job_manager.get_all_jobs()
            # Filter out invalid jobs and send the complete job list
            valid_jobs = [job for job in jobs if job and job.get('job_id') and job.get('url') and job.get('status')]
            yield f"data: {json.dumps({'jobs': valid_jobs})}\n\n"
            await asyncio.sleep(1)
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.get("/api/archives")
async def get_archives():
    return await job_manager.get_completed_jobs()

@app.get("/api/jobs")
async def get_all_jobs():
    return await job_manager.get_all_jobs()

@app.get("/api/playback/{job_id}")
async def playback_archive(job_id: str):
    job = await job_manager.get_job(job_id)
    if not job or not job.get('local_path'):
        raise HTTPException(status_code=404, detail="Archive not found")
    
    # Create full URL for replayweb.page
    serve_url = f"/api/serve/{job['local_path']}"
    
    return {
        "playback_url": f"https://replayweb.page/?source={serve_url}",
        "download_url": f"/api/download/{job['local_path']}"
    }


async def run_browsertrix_crawler(job_id: str, url: str):
    """Background task to run browsertrix-crawler in Docker"""
    try:
        await job_manager.update_job(job_id, {"status": "crawling", "progress": 10})
        
        pass
        
        if not docker_client:
            raise Exception("Docker client not available")
        
        # Create temporary directory for crawler output
        temp_dir = tempfile.mkdtemp(prefix=f"crawl_{job_id}_")
        pass
        
        try:
            await job_manager.update_job(job_id, {"progress": 20})
            
            # Configure browsertrix-crawler parameters for comprehensive crawling
            crawler_config = {
                "url": url,
                "collection": f"archive-{job_id}",
                "depth": 4,  # Increased depth for more comprehensive crawling
                "limit": 100,  # Increased page limit
                "timeout": 900,  # Increased timeout to 15 minutes
                "workers": 2,
                "screenshot": "view",
                "screencastTimeout": 10,
                "behaviors": "autoscroll,autoplay,autofetch,siteSpecific",
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "scopeType": "prefix", 
                "include": "same-domain"
                # Removed extraHops to focus on the target domain only
            }
            
            # Build crawler command
            crawler_cmd = [
                "crawl",
                "--url", url,
                "--collection", crawler_config["collection"],
                "--depth", str(crawler_config["depth"]),
                "--limit", str(crawler_config["limit"]),
                "--timeout", str(crawler_config["timeout"]),
                "--workers", str(crawler_config["workers"]),
                "--screenshot", crawler_config["screenshot"],
                "--screencastTimeout", str(crawler_config["screencastTimeout"]),
                "--behaviors", crawler_config["behaviors"],
                "--userAgent", crawler_config["userAgent"],
                "--scopeType", crawler_config["scopeType"],
                "--include", crawler_config["include"],
                "--generateWACZ",
                "--text",
                "--logging", "info"
            ]
            
            pass
            
            await job_manager.update_job(job_id, {"status": "preparing", "progress": 30})
            
            # Run browsertrix-crawler in Docker with error handling
            try:
                container = docker_client.containers.run(
                    "webrecorder/browsertrix-crawler:latest",
                    command=crawler_cmd,
                    volumes={
                        temp_dir: {"bind": "/crawls", "mode": "rw"}
                    },
                    environment={
                        "CRAWL_ID": job_id,
                        "STORE_USER": "1000",
                        "STORE_GROUP": "1000"
                    },
                    remove=False,  # Don't remove so we can debug
                    detach=True,
                    stdout=True,
                    stderr=True,
                    user="1000:1000"
                )
            except Exception as e:
                # Container creation failed
                await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
                raise Exception(f"Failed to start container: {e}")
            
            # Update status to show container is running
            await job_manager.update_job(job_id, {"status": "crawling", "progress": 10})
            
            # Don't block the main thread - let the container run and monitor progress separately
            # The container will run independently and we'll check its status periodically
            
            # Start a background task to monitor progress without blocking
            async def monitor_container_progress():
                progress = 10
                pages_archived = 0
                current_depth = 1
                container_id = container.id
                
                try:
                    # Give container time to start and initialize
                    await asyncio.sleep(5)
                    
                    # Check if container was created and is running using fresh reference
                    try:
                        current_container = docker_client.containers.get(container_id)
                        print(f"DEBUG: Container {container_id} status: {current_container.status}")
                        if current_container.status == 'exited':
                            # Container completed successfully, handle completion
                            print(f"DEBUG: Container completed successfully")
                            await handle_container_completion(pages_archived, current_depth)
                            return
                        elif current_container.status != 'running':
                            print(f"DEBUG: Container failed, status: {current_container.status}")
                            await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
                            return
                    except Exception as e:
                        # Container creation failed or not found
                        print(f"DEBUG: Failed to get container {container_id}: {e}")
                        await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
                        return
                    
                    # Monitor container progress
                    while True:
                        try:
                            # Check if container is still running using fresh reference
                            current_container = docker_client.containers.get(container_id)
                            if current_container.status != 'running':
                                # Container finished - handle completion
                                await handle_container_completion(pages_archived, current_depth)
                                break
                                
                            # Get recent logs (last 50 lines) to check progress
                            logs = current_container.logs(tail=50).decode('utf-8')
                            
                            # Count "Page Finished" events in recent logs
                            import json
                            log_lines = logs.strip().split('\n')
                            recent_pages = 0
                            
                            for line in log_lines:
                                try:
                                    log_data = json.loads(line)
                                    if log_data.get("context") == "pageStatus" and log_data.get("message") == "Page Finished":
                                        recent_pages += 1
                                except json.JSONDecodeError:
                                    continue
                            
                            # Update progress if we found new pages
                            if recent_pages > 0:
                                # This is a rough estimate - we're counting recent pages
                                pages_archived = max(pages_archived, recent_pages)
                                progress = min(10 + int(pages_archived * 2), 80)
                                
                                await job_manager.update_job(job_id, {
                                    "progress": progress, 
                                    "pages_archived": pages_archived, 
                                    "current_depth": current_depth
                                })
                                
                        except Exception as e:
                            # Container might have stopped or failed
                            try:
                                current_container = docker_client.containers.get(container_id)
                                if current_container.status != 'running':
                                    await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
                                    break
                            except:
                                await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
                                break
                                
                        # Wait before checking again
                        await asyncio.sleep(5)
                        
                except Exception as e:
                    # Any unhandled exception should mark job as failed
                    print(f"DEBUG: Monitoring task failed with exception: {e}")
                    import traceback
                    traceback.print_exc()
                    await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
                    
            async def handle_container_completion(pages_archived, current_depth):
                """Handle container completion and file processing"""
                try:
                    # Check exit code
                    result = container.wait()
                    exit_code = result['StatusCode']
                    
                    if exit_code != 0:
                        await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
                        return
                    
                    await job_manager.update_job(job_id, {"progress": 95})
                    
                    # Find the generated WACZ file
                    wacz_files = []
                    for root, _, files in os.walk(temp_dir):
                        for file in files:
                            if file.endswith('.wacz'):
                                wacz_files.append(os.path.join(root, file))
                    
                    if not wacz_files:
                        await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
                        return
                    
                    # Use the first WACZ file found
                    wacz_file = wacz_files[0]
                    
                    # Save to local storage with simple filename for replayweb.page compatibility
                    filename = f"{job_id[:8]}.wacz"
                    await storage_manager.save_binary_archive(wacz_file, job_id, filename)
                    
                    await job_manager.update_job(job_id, {
                        "status": "completed",
                        "progress": 100,
                        "completed_at": datetime.now().isoformat(),
                        "archive_path": filename,
                        "local_path": f"{job_id}/{filename}",
                        "pages_archived": pages_archived,
                        "current_depth": current_depth
                    })
                    
                except Exception:
                    await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
                finally:
                    # Clean up temp directory after container completes
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception:
                        pass
            
            # Start the monitoring task and let it run independently
            asyncio.create_task(monitor_container_progress())
            
            # Don't wait for container here - let it run independently
            # The background task will monitor it and update the job status when done
            return  # Exit the background task immediately, don't block the server
            
        finally:
            # Don't clean up temp directory immediately - let container finish first
            # The monitoring task will clean it up when container completes
            pass
                
    except Exception as e:
        await job_manager.update_job(job_id, {"status": "failed", "progress": 0})
        pass
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

async def upload_archive_to_gcs(job_id: str, local_path: str):
    """Background task to upload WACZ archive to Google Cloud Storage"""
    try:
        pass
        
        # Update job status to indicate upload in progress
        await job_manager.update_job(job_id, {"status": "uploading_gcs"})
        
        # Check if GCS credentials are available
        try:
            from google.cloud import storage
            import os
            
            # Check for GCS credentials
            if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS") and not os.getenv("GCS_BUCKET"):
                raise Exception("GCS credentials or bucket not configured")
            
            bucket_name = os.getenv("GCS_BUCKET", "web-archives-bucket")
            
            # Initialize GCS client
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            
            # Create simple blob name for replayweb.page compatibility
            file_path = os.path.join(ARCHIVE_DIR, local_path)
            simple_filename = f"{job_id[:8]}.wacz"
            blob_name = f"archives/{simple_filename}"
            blob = bucket.blob(blob_name)
            
            pass
            
            # Update progress
            await job_manager.update_job(job_id, {"progress": 50})
            
            # Upload file
            blob.upload_from_filename(file_path)
            
            # Update progress after upload
            await job_manager.update_job(job_id, {"progress": 90})
            
            # Make blob publicly readable
            blob.make_public()
            
            # Get public URL
            gcs_url = blob.public_url
            
            pass
            
            # Update job with GCS URL and restore completed status
            await job_manager.update_job(job_id, {
                "status": "completed",
                "gcs_url": gcs_url
            })
            
        except ImportError:
            raise Exception("Google Cloud Storage library not installed. Run: pip install google-cloud-storage")
        except Exception as gcs_error:
            raise Exception(f"GCS upload failed: {gcs_error}")
            
    except Exception as e:
        error_msg = str(e)
        pass
        
        # Update job with error status and message
        await job_manager.update_job(job_id, {
            "status": "gcs_upload_failed",
            "gcs_error": error_msg
        })
        import traceback
        traceback.print_exc()

@app.get("/api/download/{job_id}/{filename}")
async def download_archive(job_id: str, filename: str):
    file_path = os.path.join(ARCHIVE_DIR, job_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Archive file not found")
    
    from fastapi.responses import FileResponse
    return FileResponse(file_path, filename=filename)

@app.get("/api/download/{local_path:path}")
async def download_archive_by_path(local_path: str):
    """Download archive using the full local path (job_id/filename format)"""
    file_path = os.path.join(ARCHIVE_DIR, local_path)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Archive file not found")
    
    from fastapi.responses import FileResponse
    filename = os.path.basename(file_path)
    return FileResponse(file_path, filename=filename)

@app.options("/api/serve/{job_id}/{filename}")
async def serve_archive_options(job_id: str, filename: str):
    """Handle CORS preflight requests for archive serving"""
    from fastapi.responses import Response
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400"
    }
    return Response(headers=headers)

@app.get("/api/serve/{job_id}/{filename}")
@app.head("/api/serve/{job_id}/{filename}")
async def serve_archive(job_id: str, filename: str, request: Request):
    """Serve archive files with range request support for replayweb.page"""
    from fastapi.responses import Response, StreamingResponse
    import mimetypes
    
    file_path = os.path.join(ARCHIVE_DIR, job_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Archive file not found")
    
    file_size = os.path.getsize(file_path)
    
    # Set content type - WACZ files should be served as application/wacz
    if filename.endswith('.wacz'):
        content_type = "application/wacz"
    else:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    
    # Common headers
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Expose-Headers": "Accept-Ranges, Content-Length, Content-Range",
        "Cross-Origin-Embedder-Policy": "require-corp",
        "Cross-Origin-Opener-Policy": "same-origin"
    }
    
    # Handle HEAD requests
    if request.method == "HEAD":
        headers["Content-Length"] = str(file_size)
        return Response(headers=headers)
    
    # Handle range requests
    range_header = request.headers.get("range")
    if range_header:
        try:
            # Parse range header (e.g., "bytes=0-1023")
            range_match = range_header.replace("bytes=", "").split("-")
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if range_match[1] else file_size - 1
            
            # Validate range
            if start >= file_size or end >= file_size or start > end:
                headers["Content-Range"] = f"bytes */{file_size}"
                return Response(status_code=416, headers=headers)
            
            # Set range response headers
            content_length = end - start + 1
            headers.update({
                "Content-Length": str(content_length),
                "Content-Range": f"bytes {start}-{end}/{file_size}"
            })
            
            # Stream the requested range
            async def stream_range():
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            
            return StreamingResponse(stream_range(), status_code=206, headers=headers)
        
        except (ValueError, IndexError):
            # Invalid range header, fall back to full file
            pass
    
    # Serve full file
    headers["Content-Length"] = str(file_size)
    
    async def stream_file():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk
    
    return StreamingResponse(stream_file(), headers=headers)

@app.get("/api/gcs-proxy/{job_id}")
@app.head("/api/gcs-proxy/{job_id}")
async def gcs_proxy(job_id: str, request: Request):
    """Proxy GCS WACZ files with proper headers for replayweb.page"""
    from fastapi.responses import StreamingResponse, Response
    import aiohttp
    
    # Get job to find GCS URL
    job = await job_manager.get_job(job_id)
    if not job or not job.get('gcs_url'):
        raise HTTPException(status_code=404, detail="GCS archive not found")
    
    gcs_url = job['gcs_url']
    
    # Forward the request to GCS with proper headers
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": "application/octet-stream",  # Use octet-stream like official examples
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Expose-Headers": "Accept-Ranges, Content-Length, Content-Range",
        "Cross-Origin-Embedder-Policy": "require-corp",
        "Cross-Origin-Opener-Policy": "same-origin"
    }
    
    # Handle HEAD requests
    if request.method == "HEAD":
        async with aiohttp.ClientSession() as session:
            async with session.head(gcs_url) as response:
                headers["Content-Length"] = response.headers.get("Content-Length", "0")
                return Response(headers=headers)
    
    # Handle range requests
    range_header = request.headers.get("range")
    request_headers = {}
    if range_header:
        request_headers["Range"] = range_header
    
    # Stream from GCS and let aiohttp handle the headers properly
    async def stream_gcs():
        async with aiohttp.ClientSession() as session:
            async with session.get(gcs_url, headers=request_headers) as response:
                # Forward the exact response headers from GCS
                for header_name, header_value in response.headers.items():
                    if header_name.lower() in ['content-length', 'content-range', 'content-type']:
                        headers[header_name] = header_value
                
                # Override content-type to match replayweb.page expectations
                headers["Content-Type"] = "application/octet-stream"
                
                # Stream the content
                async for chunk in response.content.iter_chunked(8192):
                    yield chunk
    
    status_code = 206 if range_header else 200
    return StreamingResponse(stream_gcs(), status_code=status_code, headers=headers)

@app.options("/api/gcs-proxy/{job_id}")
async def gcs_proxy_options(job_id: str):
    """Handle CORS preflight for GCS proxy"""
    from fastapi.responses import Response
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400"
    }
    return Response(headers=headers)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)