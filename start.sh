#!/bin/bash

# Install system dependencies
apt-get update && apt-get install -y unrar

# Install Python dependencies
pip install -r requirements.txt

# Start the bot
python3 bot.py
