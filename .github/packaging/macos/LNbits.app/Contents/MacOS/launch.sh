#!/bin/sh

# Get the script's directory
cd "$(dirname "$0")"/lnbits || exit 1

# Activate the virtual environment
if [ -f "lnbits_env/bin/activate" ]; then
    source lnbits_env/bin/activate
else
    echo "Error: Virtual environment not found!"
    exit 1
fi

# Define persistent storage for LNbits
PERSISTENT_DIR="$HOME/Library/Application Support/LNbits"

# Ensure the persistent directory exists
mkdir -p "$PERSISTENT_DIR/database"
mkdir -p "$PERSISTENT_DIR/extensions"

# Set environment variables
export LNBITS_DATA_FOLDER="$PERSISTENT_DIR/database"
export LNBITS_EXTENSIONS_PATH="$PERSISTENT_DIR/extensions"
export LNBITS_ADMIN_UI=true

# Default host and port
DEFAULT_HOST="0.0.0.0"
DEFAULT_PORT="5000"

# Check if --host or --port are provided in the arguments
HOST_SET=false
PORT_SET=false

for arg in "$@"; do
    case $arg in
        --host)
            HOST_SET=true
            ;;
        --port)
            PORT_SET=true
            ;;
    esac
done

# Append default values if not provided
EXTRA_ARGS=""
if [ "$HOST_SET" = false ]; then
    EXTRA_ARGS="$EXTRA_ARGS --host $DEFAULT_HOST"
fi
if [ "$PORT_SET" = false ]; then
    EXTRA_ARGS="$EXTRA_ARGS --port $DEFAULT_PORT"
fi

# Start LNbits using the virtual environment
./lnbits_env/bin/python -c "from lnbits.server import main; main()" $EXTRA_ARGS "$@" &
LNBITS_PID=$!

# Wait for LNbits to start
sleep 3

# Check if running in GUI mode and `osascript` is available
if [ -n "$DISPLAY" ] || [[ "$(uname -s)" == "Darwin" ]]; then
    if command -v osascript >/dev/null 2>&1; then
        osascript -e 'display dialog "LNbits is running.\n\n http://0.0.0.0:5000" buttons {"Close Server"} default button "Close Server"'
    fi
fi

# Function to stop LNbits gracefully
kill_lnbits() {
    LN_PIDS=$(lsof -t -i:5000 2>/dev/null)
    if [ -n "$LN_PIDS" ]; then
        echo "Stopping LNbits (PIDs: $LN_PIDS)..."
        kill -2 $LN_PIDS  # Send SIGINT
    fi
}

# Stop LNbits when the dialog is closed
kill_lnbits

# Ensure LNbits fully stops
if ps -p $LNBITS_PID >/dev/null 2>&1; then
    wait $LNBITS_PID 2>/dev/null || true
fi