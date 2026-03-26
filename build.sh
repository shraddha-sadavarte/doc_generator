#!/bin/bash
# build.sh - Install system dependencies for WeasyPrint on Render

echo "🚀 Installing system dependencies for WeasyPrint..."

# Update package list
apt-get update

# Install WeasyPrint dependencies
apt-get install -y \
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

# Install Python dependencies
pip install -r requirements.txt

echo "✅ Build completed!"