# Deployment Guide

## Prerequisites
- Ubuntu 20.04+ or Debian 11+
- Docker & Docker Compose installed
- 4GB+ RAM, 20GB+ disk space
- Python 3.11+ (if not using Docker)

## Option 1: Docker Deployment (Recommended)

### Setup

```bash
# Clone repository
git clone <repo-url>
cd Chinese_Video_Localization_AI

# Copy environment file
cp .env.example .env

# Edit .env with production values
nano .env

# Build image
docker build -t chinese-video-ai:latest .

# Start containers
docker-compose -f docker-compose.prod.yml up -d