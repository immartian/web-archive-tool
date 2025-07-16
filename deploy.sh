#!/bin/bash

# Web Archive Tool - Cloud Run Deployment Script

set -e

PROJECT_ID=${1:-"sage-striker-294302"}
REGION=${2:-"us-central1"}
STORAGE_BUCKET=${3:-"web-archive-storage-${PROJECT_ID}"}

echo "🚀 Deploying Web Archive Tool to Cloud Run"
echo "Project ID: $PROJECT_ID"
echo "Region: $REGION"
echo "Storage Bucket: $STORAGE_BUCKET"

# Set the project
gcloud config set project $PROJECT_ID

# Enable required APIs
echo "📋 Enabling required APIs..."
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable firestore.googleapis.com

# Create storage bucket
echo "🪣 Creating storage bucket..."
gsutil mb -p $PROJECT_ID -l $REGION gs://$STORAGE_BUCKET || echo "Bucket might already exist"

# Create Firestore database
echo "🔥 Setting up Firestore..."
gcloud firestore databases create --location=$REGION --type=firestore-native || echo "Firestore might already exist"

# Build and deploy using Cloud Build
echo "🔨 Building and deploying with Cloud Build..."
gcloud builds submit --config cloudbuild.yaml \
  --substitutions _STORAGE_BUCKET=$STORAGE_BUCKET

# Get the service URL
SERVICE_URL=$(gcloud run services describe web-archive \
  --platform=managed \
  --region=$REGION \
  --format='value(status.url)')

echo "✅ Deployment complete!"
echo "📱 Service URL: $SERVICE_URL"
echo "🪣 Storage Bucket: gs://$STORAGE_BUCKET"
echo "🔥 Firestore Database: $PROJECT_ID"

# Optional: Set up domain mapping
echo ""
echo "💡 Next steps:"
echo "1. Visit: $SERVICE_URL"
echo "2. To map a custom domain: gcloud run domain-mappings create --service=web-archive --domain=yourdomain.com"
echo "3. To view logs: gcloud logs tail /projects/$PROJECT_ID/logs/run.googleapis.com%2Fstdout"