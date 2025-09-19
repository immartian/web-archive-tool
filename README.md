# Web Archive Tool - Local Docker Edition

A dedicated web service for running Docker-based web archiving tools with a user-friendly web interface. This tool manages web archiving jobs using local Docker containers and provides persistent storage for archived content.
## Features

- **Local Docker Integration**: Runs browsertrix-crawler in Docker containers
- **Persistent Local Storage**: Archives stored in local filesystem
- **SQLite Database**: Lightweight job tracking and metadata storage
- **Real-time Progress**: Server-sent events for live updates
- **Web UI**: User-friendly interface for managing archives
- **WACZ Format**: Compatible with replayweb.page for playback
- **Multi-format Support**: JSON archives and WACZ files
 
## Prerequisites

1. **Docker** installed and running
2. **Python 3.11+** for local development
3. **Docker permissions** for the application user

## Quick Start

### Using Docker Compose (Recommended)

```bash
# Clone the repository
git clone https://github.com/immartian/web-archive-tool.git
cd web-archive-tool

# Run with Docker Compose
docker-compose up -d

# Access the web interface
open http://localhost:8080
```

### Manual Docker Setup

```bash
# Build the Docker image
docker build -t web-archive-tool .

# Run the container
docker run -d \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $(pwd)/archives:/app/archives \
  -v $(pwd)/data:/app/data \
  --name web-archive-tool \
  web-archive-tool

# Access the web interface
open http://localhost:8080
```

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export ARCHIVE_DIR=./archives
export DB_PATH=./archives.db

# Run the application
python main.py
```

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────────┐
│   FastAPI App   │    │  Local Storage  │    │   SQLite DB     │    │ Google Cloud Storage    │
│   (main.py)     │────▶│   (archives/)   │    │   (jobs)        │    │   (GCS, WACZ files)     │
└─────────────────┘    └─────────────────┘    └─────────────────┘    └─────────────────────────┘
         │
         ▼
┌─────────────────┐
│ Docker Containers│
│ browsertrix-    │
│ crawler         │
└─────────────────┘
```

### Key Components

- **FastAPI Application**: Web API and user interface
- **Local Storage**: Persistent storage for WACZ and JSON archives
- **SQLite Database**: Lightweight database for job tracking
- **Docker Integration**: Runs browsertrix-crawler in containers
- **Web Interface**: Real-time job monitoring and archive management

## Project Structure

```
web-archive-tool/
├── main.py              # FastAPI application
├── requirements.txt     # Python dependencies
├── Dockerfile          # Container configuration
├── docker-compose.yml  # Multi-container setup
├── README.md           # This file
├── archives/           # Archive storage directory
│   ├── job-uuid-1/
│   │   └── archive.wacz
│   └── job-uuid-2/
│       └── archive.json
└── data/
    └── archives.db     # SQLite database
```

## Configuration

### Environment Variables

- `ARCHIVE_DIR`: Directory for storing archives (default: `./archives`)
- `DB_PATH`: SQLite database path (default: `./archives.db`)
- `PORT`: Server port (default: `8080`)

### Docker Compose Configuration

```yaml
version: '3.8'
services:
  web-archive:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./archives:/app/archives
      - ./data:/app/data
    environment:
      - ARCHIVE_DIR=/app/archives
      - DB_PATH=/app/data/archives.db
```

## Usage

### Web Interface

1. **Access the UI**: Open `http://localhost:8080` in your browser
2. **Submit URL**: Enter a website URL to archive
3. **Monitor Progress**: Watch real-time progress updates
4. **Download Archives**: Access completed archives via download links
5. **Playback**: Use replayweb.page for WACZ files
6. **IPFS Integration**: After uploading your archive to IPFS, use the `ipfs://<CID>/archive.wacz` URL in ReplayWeb.Page for decentralized playback.

### API Endpoints

- `POST /api/archive` - Start new archive job
- `GET /api/progress` - Server-sent events for progress updates
- `GET /api/archives` - List completed archives
- `GET /api/download/{job_id}/{filename}` - Download archive files
- `POST /api/retry/{job_id}` - Retry failed jobs
- `DELETE /api/delete/{job_id}` - Delete failed jobs

## Development

### Local Development Setup

```bash
# Install development dependencies
pip install -r requirements.txt

# Run in development mode
python main.py

# Run with auto-reload
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

### Adding New Archive Formats

1. Create a new crawler function in `main.py`
2. Register the crawler in the `analyze_url_for_crawler_type` function
3. Update the storage manager to handle the new format
4. Add frontend support for the new format

## Docker Integration

The application uses Docker to run browsertrix-crawler instances:

```python
# Example: Running browsertrix-crawler in Docker
docker_client.containers.run(
    "webrecorder/browsertrix-crawler",
    command=["crawl", "--url", url, "--output", "/crawls"],
    volumes={"/tmp/crawls": {"bind": "/crawls", "mode": "rw"}},
    remove=True
)
```

## Storage Management
#### Cache-Control Header

For optimal streaming and playback in replayweb.page, set the `Cache-Control` header on your WACZ files in Google Cloud Storage. This allows browsers and replayweb.page to cache the archive efficiently, improving performance and reducing repeated requests.

Recommended setting:
```bash
gsutil setmeta -h "Cache-Control:public,max-age=3600" gs://your-bucket/path/to/archive.wacz
```

You can adjust `max-age` as needed for your use case. Ensure that `Cache-Control` is included in your bucket's CORS `responseHeader` list.


### Local & Decentralized Storage Structure

```
archives/
├── job-uuid-1/
│   ├── archive-job-uuid-1.wacz
│   └── metadata.json
├── job-uuid-2/
│   ├── archive-job-uuid-2.json
│   └── metadata.json
└── ...
```

#### IPFS Support

You can now store and access web archives using IPFS for decentralized, peer-to-peer storage. ReplayWeb.Page supports loading archives via the `ipfs://` URL scheme.

- **Upload to IPFS**: Use an IPFS node or pinning service to add your WACZ files. Example:
  ```bash
  ipfs add /path/to/archive.wacz
  ```
  This returns a CID (Content Identifier) for your file.
- **Access via ReplayWeb.Page**: Use the following URL format:
  ```
  ipfs://<CID>/archive.wacz
  ```
- **Migration**: If you previously used Google Cloud Storage, you can migrate by uploading your archives to IPFS and updating your links to use the `ipfs://` scheme.

#### Supported Storage Schemes

ReplayWeb.Page supports loading archives from:
- `https://` (HTTP/S)
- `file://` (local files)
- `s3://` (Amazon S3)
- `googledrive://` (Google Drive)
- `ipfs://` (IPFS)

See [ReplayWeb.Page Supported URLs](https://replayweb.page/docs/url-schemes/) for details.

### Database Schema

```sql
CREATE TABLE archive_jobs (
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
    complexity_score INTEGER DEFAULT 0
);
```

## Security Considerations

- Docker socket access is required for container management
- Archive files are stored locally without external access
- No sensitive data is logged or exposed
- User input is validated and sanitized

## Troubleshooting

### Common Issues

1. **Docker Permission Errors**:
   ```bash
   # Add user to docker group
   sudo usermod -a -G docker $USER
   # Restart session
   ```

2. **Storage Permission Issues**:
   ```bash
   # Fix archive directory permissions
   chmod 755 ./archives
   chown -R $USER:$USER ./archives
   ```

3. **Database Lock Issues**:
   ```bash
   # Remove database lock
   rm -f ./data/archives.db-wal ./data/archives.db-shm
   ```

### Logs and Debugging

```bash
# View application logs
docker logs web-archive-tool

# Debug mode
export DEBUG=1
python main.py

# Check Docker connectivity
docker ps
docker info
```

## Performance Tuning

### Resource Limits

```yaml
# docker-compose.yml
services:
  web-archive:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
        reservations:
          cpus: '1.0'
          memory: 2G
```

### Concurrent Jobs

The application supports multiple concurrent archiving jobs with automatic resource management.

## Backup and Recovery

### Database Backup

```bash
# Backup SQLite database
cp ./data/archives.db ./data/archives.db.backup

# Restore from backup
cp ./data/archives.db.backup ./data/archives.db
```

### Archive Backup

```bash
# Backup all archives
tar -czf archives-backup.tar.gz ./archives/

# Restore archives
tar -xzf archives-backup.tar.gz
```

## Support

For issues and questions:
1. Check application logs for errors
2. Verify Docker daemon is running
3. Check file permissions for storage directories
4. Review SQLite database status

## Related Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Docker Documentation](https://docs.docker.com/)
- [browsertrix-crawler](https://github.com/webrecorder/browsertrix-crawler)
- [replayweb.page](https://replayweb.page/)
- [SQLite Documentation](https://sqlite.org/docs.html)

## License

This project is open source and available under the MIT License.