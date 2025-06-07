FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for OpenCV
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .s
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY stock_monitor.py .
#COPY email_details.py .

# Create directories for persistent storage
RUN mkdir -p /app/downloaded_charts /app/logs

# Set environment variable for timezone
ENV TZ=Australia/Sydney

# Run the script in monitor mode when the container starts
ENTRYPOINT ["python", "-u", "stock_monitor.py"]
CMD ["monitor", "--interval", "60"]
