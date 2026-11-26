FROM python:3.12-slim

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements-monitor.txt .
RUN pip install --no-cache-dir -r requirements-monitor.txt

# Copy the application code
COPY crap_stock_monitor.py .

# Create directory for persistent storage
RUN mkdir -p /app/data

# Set default environment variables
ENV TZ=Australia/Sydney
ENV DATA_DIR=/app/data
ENV CHECK_INTERVAL=120
ENV FAST_WINDOW_HOURS=4
ENV SLOW_CHECK_INTERVAL=3600
ENV SYMBOL=NYSI
ENV EMAIL_FREQUENCY=colour_change
ENV SIGNAL_FREQUENCY=colour_change

# Run the script in continuous monitoring mode when the container starts
ENTRYPOINT ["python", "-u", "crap_stock_monitor.py"]
