#!/bin/bash

set -e

# Define variables
APP_NAME="LNbits"
APP_VERSION="1.0.0"
APP_DIR="${APP_NAME}.AppDir"
APPIMAGE_TOOL="appimagetool-x86_64.AppImage"

# Install dependencies
sudo apt update -y
sudo apt install -y software-properties-common curl git fuse

# Add the deadsnakes PPA repository and install Python 3.9
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt install -y python3.9 python3.9-distutils

# Install Poetry
curl -sSL https://install.python-poetry.org | python3.9 -

# Add Poetry to PATH
export PATH="/home/$USER/.local/bin:$PATH"

# Clone the LNbits repository
if [ ! -d lnbits ]; then
  git clone https://github.com/lnbits/lnbits.git
fi

cd lnbits

# Check out the main branch and install dependencies
git checkout main
poetry env use python3.9
poetry install --only main

# Create the AppDir structure
mkdir -p ${APP_DIR}/usr/bin
mkdir -p ${APP_DIR}/usr/share/applications
mkdir -p ${APP_DIR}/usr/share/icons/hicolor/256x256/apps

# Copy the LNbits files to the AppDir, excluding the AppDir itself
rsync -av --progress . ${APP_DIR}/usr/bin --exclude ${APP_DIR}

# Create a desktop entry
cat > ${APP_DIR}/usr/share/applications/${APP_NAME}.desktop <<EOF
[Desktop Entry]
Name=${APP_NAME}
Exec=${APP_NAME}
Icon=${APP_NAME}
Type=Application
Categories=Utility;
EOF

# Create an icon (replace with your actual icon)
cp path/to/icon.png ${APP_DIR}/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png

# Download the AppImage tool
if [ ! -f ${APPIMAGE_TOOL} ]; then
  wget https://github.com/AppImage/AppImageKit/releases/download/continuous/${APPIMAGE_TOOL}
  chmod +x ${APPIMAGE_TOOL}
fi

# Build the AppImage
./${APPIMAGE_TOOL} ${APP_DIR}

# Move the AppImage to the output directory
mv ${APP_NAME}*.AppImage ../

echo "AppImage created successfully!"