# Stock Chart Monitor

This application monitors StockCharts.com for color changes in the $NYSI chart line (red/black transitions). It analyzes a specific region of the chart to detect when the line changes from red to black or black to red, logs these transitions, and sends email notifications.

## Docker Usage

### Build the Docker image

```bash
docker-compose build
```

### Running the container

#### Test mode (single capture)

```bash
docker-compose run --rm stock-monitor python stock_monitor.py test
```

To analyze a specific region:

```bash
docker-compose run --rm stock-monitor python stock_monitor.py test 600 640
```

#### Monitor mode

Run with default settings (checks every 30 seconds during 9:30-10:00 AM AEST):

```bash
docker-compose up -d
```

Run with custom interval (60 seconds):

```bash
docker-compose run -d --name stock-monitor stock-monitor python stock_monitor.py monitor 60
```

Run in continuous mode (24/7 monitoring):

```bash
docker-compose run -d --name stock-monitor stock-monitor python stock_monitor.py monitor 60 continuous
```

### Email Notifications

To enable email notifications:

1. Copy the template file to the real configuration:
   ```bash
   cp email_details.template.py email_details.py
   ```

2. Edit `email_details.py` with your email credentials:
   ```python
   sender_email = "your.email@gmail.com"
   sender_password_password = "your-app-password"  # For Gmail, use an App Password
   recipients = ["recipient1@example.com", "recipient2@example.com"]
   ```

### Viewing logs

```bash
# View container logs
docker-compose logs -f

# Access the log file directly
cat nysi_changes.txt
```

## Non-Docker Usage

### Requirements

- Python 3.6+
- Required packages: see requirements.txt

### Installation

```bash
pip install -r requirements.txt
```

### Running

Same commands as above, but without docker-compose:

```bash
python stock_monitor.py test
python stock_monitor.py monitor 60
python stock_monitor.py monitor 60 continuous
```
