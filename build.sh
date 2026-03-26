#!/bin/bash
echo "🚀 Installing system dependencies for WeasyPrint..."

# Use Python 3.11
export PYTHON_VERSION=3.11

# Install system dependencies
apt-get update
apt-get install -y \
    python3.11 \
    python3-pip \
    python3-dev \
    build-essential \
    libffi-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libshared-mime-info \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    libjpeg-dev \
    zlib1g-dev \
    libopenjp2-7 \
    libtiff5

# Install pip
python3.11 -m ensurepip --upgrade

# Install requirements
python3.11 -m pip install --upgrade pip
python3.11 -m pip install -r requirements.txt

echo "✅ Build completed!"