import requests
import json

# Test the archives endpoint
try:
    response = requests.get('http://localhost:8080/api/archives')
    print(f"Archives API status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Archives response: {json.dumps(data, indent=2)}")
    else:
        print(f"Archives error: {response.text}")
except Exception as e:
    print(f"Archives error: {e}")

print("\n" + "="*50 + "\n")

# Test direct job manager
import sys
sys.path.append('.')
import asyncio
from main import job_manager

async def test_job_manager():
    jobs = await job_manager.get_all_jobs()
    print(f"Job manager all jobs: {jobs}")
    
    completed = await job_manager.get_completed_jobs()
    print(f"Job manager completed jobs: {completed}")

asyncio.run(test_job_manager())