# Stock Chart Monitor

This application monitors StockCharts.com for color changes in the $NYSI chart line (red/black transitions). It analyzes a specific region of the chart to detect when the line changes from red to black or black to red, logs these transitions, and sends email notifications.

## Requirements

- Python 3.6+
- Required packages: see requirements.txt

## Installation

```bash
pip install -r requirements.txt
```

## Running

### Test mode (single capture)

```bash
python stock_monitor.py test
```

To analyze a specific region:

```bash
python stock_monitor.py test --x-region 600 640
```

To specify both x and y regions:

```bash
python stock_monitor.py test --x-region 600 640 --y-region 200 300
```

To specify a different symbol:
*(Note: this will probably break. This whole thing is mad brittle)*

```bash
python stock_monitor.py test --symbol "$NYMO"
```



### Monitor mode

Run with default settings (checks every 30 seconds during 9:30-10:00 AM AEST):

```bash
python stock_monitor.py monitor
```

Run with custom interval (60 seconds):

```bash
python stock_monitor.py monitor --interval 60
```

Run in continuous mode (24/7 monitoring):

```bash
python stock_monitor.py monitor --interval 60 --continuous
```

Monitor a different symbol:

```bash
python stock_monitor.py monitor --symbol "$NYMO" --interval 60 --continuous
```

## Email Notifications

To enable email notifications:

1. Copy the template file to the real configuration:

   ```bash
   cp email_details.template.py email_details.py
   ```

2. Edit `email_details.py` with your email credentials:

   ```python
   sender_email = "your.email@gmail.com"
   sender_password = "your-app-password"  # For Gmail, use an App Password
   recipients = ["recipient1@example.com", "recipient2@example.com"]
   ```

## Viewing logs

```bash
# Access the log file
cat logs/nysi_changes.log
```
