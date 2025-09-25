FROM python:3.12-slim

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements-monitor.txt .
RUN pip install --no-cache-dir -r requirements-monitor.txt

# Copy the application code
COPY crap_stock_monitor.py .
COPY email_details.py .

# Create directory for persistent storage
RUN mkdir -p /app/data

# Set environment variable for timezone
ENV TZ=Australia/Sydney

# Run the script in continuous monitoring mode when the container starts
ENTRYPOINT ["python", "-u", "crap_stock_monitor.py"]
CMD ["--interval", "60"]
