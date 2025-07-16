# Web Archive Tool - Cloud Run Edition

A scalable web archiving solution designed for Google Cloud Run with persistent storage using Cloud Storage and Firestore.

## 🚀 Features

- ✅ **Cloud Native**: Designed for Google Cloud Run
- ✅ **Persistent Storage**: Archives stored in Google Cloud Storage
- ✅ **Scalable Jobs**: Job tracking with Firestore
- ✅ **Real-time Progress**: Server-sent events for live updates
- ✅ **Auto-scaling**: Scales to zero when not in use
- ✅ **Direct Downloads**: Public URLs for archived content
- ✅ **Playback Integration**: Works with replayweb.page

## 📋 Prerequisites

1. **Google Cloud Account** with billing enabled
2. **gcloud CLI** installed and authenticated
3. **Docker** installed locally (for local development)
4. **Project with required APIs** enabled:
   - Cloud Build API
   - Cloud Run API
   - Cloud Storage API
   - Firestore API

## 🔧 Quick Deployment

### One-Click Deployment

```bash
# Make deployment script executable
chmod +x deploy.sh

# Deploy to Cloud Run
./deploy.sh your-project-id us-central1
```

### Manual Deployment

1. **Set up Google Cloud Project**:
```bash
# Set project
gcloud config set project YOUR_PROJECT_ID

# Enable APIs
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable firestore.googleapis.com
```

2. **Create Cloud Storage Bucket**:
```bash
gsutil mb -p YOUR_PROJECT_ID -l us-central1 gs://web-archive-storage-YOUR_PROJECT_ID
```

3. **Set up Firestore**:
```bash
gcloud firestore databases create --location=us-central1 --type=firestore-native
```

4. **Deploy with Cloud Build**:
```bash
gcloud builds submit --config cloudbuild.yaml \
  --substitutions _STORAGE_BUCKET=web-archive-storage-YOUR_PROJECT_ID
```

## 🏗️ Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Cloud Run     │    │  Cloud Storage  │    │   Firestore     │
│   (main app)    │────▶│   (archives)    │    │   (jobs)        │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │
         ▼
┌─────────────────┐
│ browsertrix-    │
│ crawler         │
│ (temporary)     │
└─────────────────┘
```

### Key Components

- **Cloud Run Service**: Hosts the FastAPI application
- **Cloud Storage**: Persistent storage for WACZ archives
- **Firestore**: NoSQL database for job tracking and metadata
- **Cloud Build**: Automated build and deployment pipeline
- **browsertrix-crawler**: Temporary containers for web crawling

## 🔐 Security & Permissions

The Cloud Run service needs the following IAM roles:

```bash
# Service account with required permissions
gcloud iam service-accounts create web-archive-sa

# Grant necessary roles
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:web-archive-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:web-archive-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/datastore.user"
```

## 💰 Cost Optimization

### Resource Configuration
- **CPU**: 2 vCPU (adjustable in cloudbuild.yaml)
- **Memory**: 2 GB (adjustable in cloudbuild.yaml)
- **Timeout**: 1 hour (for long crawls)
- **Concurrency**: 10 concurrent requests
- **Max Instances**: 5 (auto-scales to zero)

### Cost Estimates (per month)
- **Cloud Run**: ~$0.10 per 100,000 requests
- **Cloud Storage**: ~$0.026 per GB stored
- **Firestore**: ~$0.18 per 100,000 reads
- **Cloud Build**: ~$0.003 per build minute

## 🛠️ Development

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export GOOGLE_CLOUD_PROJECT=your-project-id
export STORAGE_BUCKET=web-archive-storage-your-project-id

# Run locally
python main.py
```

### Testing
```bash
# Test with local Docker
docker build -t web-archive .
docker run -p 8080:8080 -e GOOGLE_CLOUD_PROJECT=your-project-id web-archive
```

## 📝 Configuration

### Environment Variables
- `GOOGLE_CLOUD_PROJECT`: Your GCP project ID
- `STORAGE_BUCKET`: Cloud Storage bucket name
- `PORT`: Server port (default: 8080)

### Firestore Collections
- `archive_jobs`: Job metadata and status

### Cloud Storage Structure
```
gs://your-bucket/
├── archives/
│   ├── job-uuid-1/
│   │   └── archive-job-uuid-1.wacz
│   ├── job-uuid-2/
│   │   └── archive-job-uuid-2.wacz
│   └── ...
```

## 🔍 Monitoring

### Cloud Logging
```bash
# View logs
gcloud logs tail /projects/YOUR_PROJECT_ID/logs/run.googleapis.com%2Fstdout

# Filter by service
gcloud logs read "resource.type=cloud_run_revision AND resource.labels.service_name=web-archive"
```

### Cloud Monitoring
- **Request Count**: Number of archive requests
- **Response Time**: API response latency
- **Error Rate**: Failed requests percentage
- **Storage Usage**: Archive storage consumption

## 🚨 Troubleshooting

### Common Issues

1. **Permission Errors**:
```bash
# Check service account permissions
gcloud iam service-accounts get-iam-policy web-archive-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

2. **Storage Access Issues**:
```bash
# Test bucket access
gsutil ls gs://web-archive-storage-YOUR_PROJECT_ID
```

3. **Firestore Connection Issues**:
```bash
# Check Firestore status
gcloud firestore databases describe --database="(default)"
```

### Performance Tuning

1. **Increase Memory**: For large sites
2. **Adjust Timeout**: For long crawls
3. **Scale Instances**: For high traffic
4. **Optimize Crawl Settings**: Limit pages/depth

## 📊 Usage Analytics

Track usage with Cloud Monitoring:
- Archive requests per day
- Storage growth over time
- Popular domains archived
- Error rates by URL type

## 🔄 Updates & Maintenance

### Automated Deployments
Set up Cloud Build triggers for automatic deployments:

```yaml
# cloudbuild-trigger.yaml
name: web-archive-deploy
github:
  owner: your-username
  name: your-repo
  push:
    branch: main
filename: cloudbuild.yaml
```

### Database Maintenance
- Monitor Firestore usage
- Clean up old job records
- Archive completed jobs

## 🌐 Custom Domain

```bash
# Map custom domain
gcloud run domain-mappings create \
  --service=web-archive \
  --domain=archive.yourdomain.com \
  --region=us-central1
```

## 📞 Support

For issues and questions:
1. Check Cloud Run logs
2. Review Firestore collections
3. Verify IAM permissions
4. Test bucket access

## 🔗 Related Resources

- [Cloud Run Documentation](https://cloud.google.com/run/docs)
- [Cloud Storage Documentation](https://cloud.google.com/storage/docs)
- [Firestore Documentation](https://cloud.google.com/firestore/docs)
- [browsertrix-crawler](https://github.com/webrecorder/browsertrix-crawler)