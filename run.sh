#!/bin/bash

# Web Archive Tool - Docker Management Script

# Function to stop the service
stop_service() {
    echo "🛑 Stopping Web Archive Tool..."
    
    # Stop docker-compose deployment
    echo "Stopping docker-compose..."
    docker-compose down 2>/dev/null || true
    
    # Stop standalone container
    echo "Stopping standalone container..."
    docker stop web-archive-tool 2>/dev/null || true
    docker rm web-archive-tool 2>/dev/null || true
    
    # Stop Python server
    echo "Stopping Python server..."
    if [ -f "server.pid" ]; then
        SERVER_PID=$(cat server.pid)
        if kill -0 $SERVER_PID 2>/dev/null; then
            kill $SERVER_PID
            echo "Stopped server with PID: $SERVER_PID"
        fi
        rm -f server.pid
    fi
    
    # Kill any remaining Python processes
    pkill -f "python.*main.py" 2>/dev/null || true
    
    # Check if anything is still running on port 8080
    if lsof -i :8080 > /dev/null 2>&1; then
        echo "⚠️  Something is still running on port 8080"
        echo "Use: sudo lsof -i :8080 to check what's running"
    else
        echo "✅ Port 8080 is now free"
    fi
    
    echo "🧹 Cleanup complete!"
    exit 0
}

# Function to start the service
start_service() {
    echo "🚀 Starting Web Archive Tool..."

    # Check if Docker is installed and running
    if ! command -v docker &> /dev/null; then
        echo "❌ Docker is not installed. Please install Docker first."
        exit 1
    fi

    if ! docker info &> /dev/null; then
        echo "❌ Docker is not running. Please start Docker daemon."
        exit 1
    fi

    # Check if Python is installed
    if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
        echo "❌ Python is not installed. Please install Python 3.11+ first."
        exit 1
    fi

    # Stop any existing instances
    echo "🛑 Stopping previous instances..."
    docker-compose down 2>/dev/null || true
    docker stop web-archive-tool 2>/dev/null || true
    docker rm web-archive-tool 2>/dev/null || true
    
    # Stop any existing Python processes on port 8080
    pkill -f "python.*main.py" 2>/dev/null || true

    # Pull the latest browsertrix-crawler image
    echo "📦 Pulling browsertrix-crawler image..."
    docker pull webrecorder/browsertrix-crawler:latest

    # Create necessary directories
    echo "📁 Creating directories..."
    mkdir -p ./archives ./data

    # Set proper permissions
    echo "🔒 Setting permissions..."
    chmod 755 ./archives ./data

    # Check if virtual environment exists
    if [ ! -d "venv" ]; then
        echo "🐍 Creating Python virtual environment..."
        python3 -m venv venv 2>/dev/null || python -m venv venv
    fi

    # Activate virtual environment
    echo "🔧 Activating virtual environment..."
    source venv/bin/activate

    # Install dependencies
    echo "📦 Installing Python dependencies..."
    pip install -r requirements.txt

    # Set environment variables
    export ARCHIVE_DIR="$(pwd)/archives"
    export DB_PATH="$(pwd)/data/archives.db"
    export PORT=8080

    # Start the FastAPI server
    echo "🚀 Starting FastAPI server on host..."
    nohup python main.py > server.log 2>&1 &
    SERVER_PID=$!
    
    # Wait a moment for server to start
    sleep 3
    
    # Check if server started successfully
    if kill -0 $SERVER_PID 2>/dev/null; then
        echo "✅ Web Archive Tool is running at http://localhost:8080"
        echo "📖 Check logs with: tail -f server.log"
        echo "🛑 Stop with: ./run.sh stop"
        echo "🔍 Server PID: $SERVER_PID"
        echo "$SERVER_PID" > server.pid
    else
        echo "❌ Failed to start server. Check server.log for errors."
        exit 1
    fi

    echo ""
    echo "🌐 Access the web interface at: http://localhost:8080"
    echo "📋 The tool now supports:"
    echo "   • Deep crawling (3 levels, up to 50 pages)"
    echo "   • Professional WACZ archives via Docker"
    echo "   • replayweb.page integration"
    echo "   • Real-time progress monitoring"
}

# Main script logic
if [ "$1" = "stop" ]; then
    stop_service
else
    # Default to start (stops previous instances first)
    start_service
fi