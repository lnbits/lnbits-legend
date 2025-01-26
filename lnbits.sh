#!/bin/bash

# Check install has not already run
if [ ! -d ~/lnbits_data ]; then

  # Update package list and install prerequisites non-interactively
  sudo apt update -y
  sudo apt install -y software-properties-common
  
  # Add the deadsnakes PPA repository non-interactively
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  
  # Install Python 3.9 and distutils non-interactively
  sudo apt install -y python3.9 python3.9-distutils

  # Install Poetry
  curl -sSL https://install.python-poetry.org | python3.9 -

  # Add Poetry to PATH for the current session
  export PATH="/home/$USER/.local/bin:$PATH"

  if [ ! -d lnbits/wallets ]; then
    # Clone the LNbits repository
    git clone https://github.com/lnbits/lnbits.git
    if [ $? -ne 0 ]; then
      echo "Failed to clone the repository ... FAIL"
      exit 1
    fi
    # Ensure we are in the lnbits directory
    cd lnbits || { echo "Failed to cd into lnbits ... FAIL"; exit 1; }
  fi

  git checkout main
  # Make data folder in the home directory
  mkdir -p ~/lnbits_data

  # Copy the .env.example to .env
  cp .env.example .env

elif [ ! -d lnbits/wallets ]; then
  # cd into lnbits
  cd lnbits || { echo "Failed to cd into lnbits ... FAIL"; exit 1; }
fi

# Set path for running after install
export PATH="/home/$USER/.local/bin:$PATH"

# Install the dependencies using Poetry
poetry env use python3.9
poetry install --only main

# Set environment variables for LNbits
export LNBITS_ADMIN_UI=true
export HOST=0.0.0.0

# Run LNbits
poetry run lnbits