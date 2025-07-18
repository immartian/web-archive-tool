#!/bin/bash

# Web Archive Tool - Local Development Script

# Function to stop the service
stop_service() {
    echo "🛑 Stopping Web Archive Tool..."
    
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
    
    # Kill any remaining Python/uvicorn processes
    pkill -f "python.*main.py" 2>/dev/null || true
    pkill -f "uvicorn.*main:app" 2>/dev/null || true
    pkill -f "uvicorn" 2>/dev/null || true
    
    # Force kill any processes still using port 8080
    if lsof -i :8080 -sTCP:LISTEN > /dev/null 2>&1; then
        echo "Force killing processes on port 8080..."
        lsof -ti :8080 -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
    fi
    
    # Clean up any remaining Docker containers
    echo "🐳 Cleaning up Docker containers..."
    docker container prune -f >/dev/null 2>&1 || true
    
    # Check if anything is still listening on port 8080
    if lsof -i :8080 -sTCP:LISTEN > /dev/null 2>&1; then
        echo "⚠️  Something is still listening on port 8080"
        echo "Use: sudo lsof -i :8080 -sTCP:LISTEN to check what's listening"
    else
        echo "✅ Port 8080 is now free"
    fi
    
    echo "🧹 Cleanup complete!"
    exit 0
}

# Function to get Yggdrasil IPv6 address (optional)
get_yggdrasil_ipv6() {
    # Try to get Yggdrasil IPv6 address from common interfaces
    local ygg_addr=""
    
    # Try to find Yggdrasil IPv6 address
    for interface in tun0 utun0 ygg0; do
        if ip addr show $interface 2>/dev/null | grep -q "inet6.*200::/7"; then
            ygg_addr=$(ip addr show $interface 2>/dev/null | grep "inet6.*200::/7" | head -1 | awk '{print $2}' | cut -d'/' -f1)
            break
        fi
    done
    
    # Fallback: try ifconfig for macOS
    if [ -z "$ygg_addr" ]; then
        for interface in utun0 utun1 utun2; do
            if ifconfig $interface 2>/dev/null | grep -q "inet6.*200:"; then
                ygg_addr=$(ifconfig $interface 2>/dev/null | grep "inet6.*200:" | head -1 | awk '{print $2}')
                break
            fi
        done
    fi
    
    echo "$ygg_addr"  # Return empty string if not found
}

# Function to start the service
start_service() {
    local bind_host="0.0.0.0"
    local ipv6_mode=false
    
    # Check for IPv6 flag
    if [ "$1" = "--ipv6" ]; then
        ipv6_mode=true
        echo "🌐 Starting Web Archive Tool with IPv6 support..."
        ygg_addr=$(get_yggdrasil_ipv6)
        
        if [ -n "$ygg_addr" ]; then
            bind_host="$ygg_addr"
            echo "📍 Yggdrasil IPv6 address found: $ygg_addr"
        else
            bind_host="::"
            echo "📍 Using standard IPv6 binding (::) - Yggdrasil not detected"
        fi
    else
        echo "🚀 Starting Web Archive Tool..."
    fi

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
    
    # Stop any existing Python/uvicorn processes on port 8080
    pkill -f "python.*main.py" 2>/dev/null || true
    pkill -f "uvicorn.*main:app" 2>/dev/null || true

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

    # Start the FastAPI server with uvicorn
    if [ "$ipv6_mode" = true ]; then
        echo "🚀 Starting FastAPI server with uvicorn on IPv6..."
        nohup uvicorn main:app --host "$bind_host" --port 8080 --reload > server.log 2>&1 &
    else
        echo "🚀 Starting FastAPI server with uvicorn..."
        nohup uvicorn main:app --host 0.0.0.0 --port 8080 --reload > server.log 2>&1 &
    fi
    SERVER_PID=$!
    
    # Wait a moment for server to start
    sleep 3
    
    # Check if server started successfully
    if kill -0 $SERVER_PID 2>/dev/null; then
        if [ "$ipv6_mode" = true ]; then
            if [ -n "$ygg_addr" ]; then
                echo "✅ Web Archive Tool is running on IPv6 at http://[$ygg_addr]:8080"
                echo "🌐 Yggdrasil network address: [$ygg_addr]:8080"
            else
                echo "✅ Web Archive Tool is running on IPv6 at http://[::]:8080"
                echo "🌐 IPv6 address: [::]:8080 (all interfaces)"
            fi
            echo "📡 Also accessible via localhost: http://localhost:8080"
        else
            echo "✅ Web Archive Tool is running at http://localhost:8080"
        fi
        echo "📖 Check logs with: tail -f server.log"
        echo "🛑 Stop with: ./run.sh stop"
        echo "🔍 Server PID: $SERVER_PID"
        echo "$SERVER_PID" > server.pid
    else
        echo "❌ Failed to start server. Check server.log for errors."
        exit 1
    fi

    echo ""
    if [ "$ipv6_mode" = true ]; then
        if [ -n "$ygg_addr" ]; then
            echo "🌐 IPv6 (Yggdrasil): http://[$ygg_addr]:8080"
        else
            echo "🌐 IPv6 (Standard): http://[::]:8080"
        fi
        echo "🏠 Local access: http://localhost:8080"
    else
        echo "🌐 Access the web interface at: http://localhost:8080"
    fi
    echo "📋 The tool now supports:"
    echo "   • Deep crawling (4 levels, up to 100 pages)"
    echo "   • Professional WACZ archives via browsertrix-crawler"
    echo "   • Async non-blocking crawling with real-time progress"
    echo "   • Cloud storage integration (Google Cloud Storage)"
    echo "   • replayweb.page integration for online playback"
    echo "   • Unified job management with stop/retry/delete controls"
    if [ "$ipv6_mode" = true ]; then
        echo "   • Yggdrasil IPv6 network accessibility"
    fi
}

# Main script logic
if [ "$1" = "stop" ]; then
    stop_service
elif [ "$1" = "--ipv6" ]; then
    # Start with IPv6 support
    start_service --ipv6
else
    # Default to start (stops previous instances first)
    start_service
fi